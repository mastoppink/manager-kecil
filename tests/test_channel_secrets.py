import json
import os
import tempfile
import unittest
from unittest.mock import patch, Mock

os.environ['MANAGER_KECIL_START_WORKERS'] = '0'

from core import channel_auth


def secret_json(cid, sec='s3cret'):
    return json.dumps({'installed': {'client_id': cid, 'client_secret': sec}})


def cred_json(cid, refresh='r'):
    return json.dumps({'client_id': cid, 'client_secret': 'x', 'refresh_token': refresh, 'token': 't'})


CID_A = 'aaaa-1111.apps.googleusercontent.com'
CID_B = 'bbbb-2222.apps.googleusercontent.com'


class ChannelAuthHelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_save_and_load_secret(self):
        cid = channel_auth.save_secret(self.tmp.name, secret_json(CID_A))
        self.assertEqual(cid, CID_A)
        self.assertEqual(channel_auth.load_secret(self.tmp.name, CID_A), (CID_A, 's3cret'))
        self.assertIsNone(channel_auth.load_secret(self.tmp.name, CID_B))
        self.assertIsNone(channel_auth.load_secret(self.tmp.name, '../../etc/passwd'))

    def test_rejects_invalid_secret(self):
        for raw in ('[]', '{}', json.dumps({'installed': {'client_id': '../x'}}), 'not json'):
            with self.assertRaises(ValueError):
                channel_auth.save_secret(self.tmp.name, raw)

    def test_channel_creds_filters_by_bound_client_and_keeps_index(self):
        ch = {'client_id': CID_B, 'creds_list': [cred_json(CID_A), cred_json(CID_B)]}
        self.assertEqual([i for i, _ in channel_auth.channel_creds(ch)], [1])
        self.assertFalse(channel_auth.needs_relink(ch))
        ch['client_id'] = 'cccc-3333.apps.googleusercontent.com'
        self.assertEqual(channel_auth.channel_creds(ch), [])
        self.assertTrue(channel_auth.needs_relink(ch))

    def test_legacy_channel_without_client_id_is_unfiltered(self):
        ch = {'creds_json': cred_json(CID_A)}
        self.assertEqual(channel_auth.first_channel_cred(ch), cred_json(CID_A))
        self.assertFalse(channel_auth.needs_relink(ch))

    def test_migration_binds_client_and_moves_legacy_secret(self):
        legacy = os.path.join(self.tmp.name, 'client_secret.json')
        with open(legacy, 'w') as f:
            f.write(secret_json(CID_A))
        sdir = os.path.join(self.tmp.name, 'secrets')
        chans = [{'yt_id': 'X', 'creds_list': [cred_json(CID_A)]}, {'yt_id': 'Y', 'client_id': CID_B}]
        self.assertTrue(channel_auth.migrate_channels(chans, sdir, legacy))
        self.assertEqual(chans[0]['client_id'], CID_A)
        self.assertEqual(chans[1]['client_id'], CID_B)
        self.assertIsNotNone(channel_auth.load_secret(sdir, CID_A))
        self.assertFalse(channel_auth.migrate_channels(chans, sdir, legacy))


class ChannelSecretRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app
        cls.module = app

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(patch.stopall)
        self.app = self.module
        self.sdir = os.path.join(self.tmp.name, 'secrets')
        self.channels = [{'id': 1, 'name': 'Lama', 'yt_id': 'UC_OLD', 'thumbnail': '', 'status': 'Connected 🟢',
                          'client_id': CID_A, 'creds_list': [cred_json(CID_A)]}]
        patch.multiple(self.app, database_channel=self.channels, SECRETS_DIR=self.sdir).start()
        self.saved = []
        patch.object(self.app, 'save_channels', side_effect=lambda c: self.saved.append(json.loads(json.dumps(c)))).start()
        self.client = self.app.app.test_client()
        with self.client.session_transaction() as s:
            s['logged_in'] = True

    def upload(self, cid, yt_id=None):
        from io import BytesIO
        data = {'secret_file': (BytesIO(secret_json(cid).encode()), 'client_secret.json')}
        if yt_id:
            data['yt_id'] = yt_id
        return self.client.post('/api/upload_secret', data=data, content_type='multipart/form-data').get_json()

    def google_ok(self, channel_id, title='Ch'):
        tok = Mock(); tok.json.return_value = {'access_token': 'AT', 'refresh_token': 'RT'}
        patch.object(self.app.requests, 'post', return_value=tok).start()
        yt = Mock()
        yt.channels.return_value.list.return_value.execute.return_value = {
            'items': [{'id': channel_id, 'snippet': {'title': title, 'thumbnails': {'default': {'url': 'u'}}}}]}
        patch.object(self.app, 'build', return_value=yt).start()

    def poll(self, cid, target=''):
        return self.client.post('/api/poll_device_token', json={'device_code': 'DC', 'secret': cid, 'target': target}).get_json()

    def test_upload_secret_to_existing_channel_marks_relink(self):
        r = self.upload(CID_B, 'UC_OLD')
        self.assertEqual(r['status'], 'success')
        self.assertIn('secret=' + CID_B, r['auth_url'])
        self.assertIn('yt_id=UC_OLD', r['auth_url'])
        self.assertEqual(self.channels[0]['client_id'], CID_B)
        self.assertTrue(self.app.channel_auth.needs_relink(self.channels[0]))
        ch = self.client.get('/api/get_channels').get_json()[0]
        self.assertTrue(ch['needs_relink'])
        self.assertTrue(ch['has_secret'])

    def test_same_secret_can_be_used_by_several_channels(self):
        self.upload(CID_A)
        self.google_ok('UC_NEW', 'Baru')
        self.assertEqual(self.poll(CID_A)['status'], 'success')
        self.assertEqual([c['client_id'] for c in self.channels], [CID_A, CID_A])
        self.assertEqual(self.channels[1]['id'], 2)
        self.assertEqual(self.channels[1]['content_mode'], 'adsense')  # channel baru otomatis AdSense

    def test_relink_rejects_wrong_account(self):
        self.upload(CID_B, 'UC_OLD')
        self.google_ok('UC_SOMEONE_ELSE')
        r = self.poll(CID_B, 'UC_OLD')
        self.assertEqual(r['status'], 'error')
        self.assertTrue(self.app.channel_auth.needs_relink(self.channels[0]))

    def test_relink_replaces_old_tokens(self):
        self.upload(CID_B, 'UC_OLD')
        self.google_ok('UC_OLD')
        self.assertEqual(self.poll(CID_B, 'UC_OLD')['status'], 'success')
        ch = self.channels[0]
        self.assertEqual(len(ch['creds_list']), 1)
        self.assertEqual(json.loads(ch['creds_list'][0])['client_id'], CID_B)
        self.assertFalse(self.app.channel_auth.needs_relink(ch))

    def test_poll_without_known_secret_fails(self):
        r = self.poll('zzzz-9999.apps.googleusercontent.com')
        self.assertEqual(r['status'], 'error')

    def test_list_secrets_with_usage(self):
        self.upload(CID_A)
        self.upload(CID_B)
        items = {s['client_id']: s for s in self.client.get('/api/secrets').get_json()}
        self.assertEqual(set(items), {CID_A, CID_B})
        self.assertEqual([u['yt_id'] for u in items[CID_A]['used_by']], ['UC_OLD'])
        self.assertEqual(items[CID_B]['used_by'], [])

    def test_use_secret_for_new_and_existing_channel(self):
        self.upload(CID_A)
        self.upload(CID_B)
        r = self.client.post('/api/secrets/use', json={'client_id': CID_B}).get_json()
        self.assertEqual(r['status'], 'success')
        self.assertIn('secret=' + CID_B, r['auth_url'])
        self.assertNotIn('yt_id=', r['auth_url'])
        self.assertEqual(self.channels[0]['client_id'], CID_A)  # channel lama tidak berubah

        r = self.client.post('/api/secrets/use', json={'client_id': CID_B, 'yt_id': 'UC_OLD'}).get_json()
        self.assertIn('yt_id=UC_OLD', r['auth_url'])
        self.assertEqual(self.channels[0]['client_id'], CID_B)
        self.assertTrue(self.app.channel_auth.needs_relink(self.channels[0]))

        self.assertEqual(self.client.post('/api/secrets/use', json={'client_id': 'zzzz-0000.apps.googleusercontent.com'}).status_code, 404)
        self.assertEqual(self.client.post('/api/secrets/use', json={'client_id': CID_A, 'yt_id': 'NOPE'}).status_code, 404)

    def test_delete_secret_only_when_unused(self):
        self.upload(CID_A)
        self.upload(CID_B)
        r = self.client.post('/api/secrets/delete', json={'client_id': CID_A})
        self.assertEqual(r.status_code, 409)
        self.assertIn('Lama', r.get_json()['message'])
        self.assertEqual(self.client.post('/api/secrets/delete', json={'client_id': CID_B}).status_code, 200)
        self.assertEqual([s['client_id'] for s in self.client.get('/api/secrets').get_json()], [CID_A])
        self.assertEqual(self.client.post('/api/secrets/delete', json={'client_id': CID_B}).status_code, 404)
        self.assertEqual(self.client.post('/api/secrets/delete', json={'client_id': '../x'}).status_code, 400)

    def test_generate_link_needs_channel_secret(self):
        self.assertIn('error', self.client.get('/api/generate_tv_link').get_json())
        self.assertIn('error', self.client.get('/api/generate_tv_link?yt_id=UC_OLD').get_json())
        self.upload(CID_A, 'UC_OLD')
        self.assertIn('secret=' + CID_A, self.client.get('/api/generate_tv_link?yt_id=UC_OLD').get_json()['auth_url'])


if __name__ == '__main__':
    unittest.main()
