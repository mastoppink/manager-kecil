import json
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ['MANAGER_KECIL_START_WORKERS'] = '0'


class ChangePinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app
        cls.module = app

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(patch.stopall)
        self.cfg = os.path.join(self.tmp.name, 'config.json')
        with open(self.cfg, 'w') as f:
            json.dump({'admin_pin': '1234', 'secret_key': 'lama', 'tg_token': 'TG', 'tg_chat_id': '99'}, f)
        app = self.module
        patch.object(app, 'CONFIG_FILE', self.cfg).start()
        old_secret = app.app.secret_key
        app.app.secret_key = 'lama'
        self.addCleanup(setattr, app.app, 'secret_key', old_secret)
        self.me = app.app.test_client()
        self.other = app.app.test_client()
        for c in (self.me, self.other):
            with c.session_transaction() as s:
                s['logged_in'] = True

    def change(self, current, new, confirm=None):
        return self.me.post('/api/change_pin', json={'current_pin': current, 'new_pin': new,
                                                     'confirm_pin': new if confirm is None else confirm})

    def config(self):
        with open(self.cfg) as f:
            return json.load(f)

    def test_rejects_wrong_current_pin(self):
        r = self.change('0000', '5678')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.config()['admin_pin'], '1234')

    def test_validates_new_pin(self):
        self.assertEqual(self.change('1234', '12').status_code, 400)
        self.assertEqual(self.change('1234', '5678', '5679').status_code, 400)
        self.assertEqual(self.change('1234', '1234').status_code, 400)
        self.assertEqual(self.config()['admin_pin'], '1234')

    def test_changes_pin_keeps_other_settings_and_logs_out_other_devices(self):
        r = self.change('1234', '5678')
        self.assertEqual(r.status_code, 200, r.get_json())
        cfg = self.config()
        self.assertEqual(cfg['admin_pin'], '5678')
        self.assertNotEqual(cfg['secret_key'], 'lama')
        self.assertEqual((cfg['tg_token'], cfg['tg_chat_id']), ('TG', '99'))
        # Perangkat ini tetap masuk
        self.assertEqual(self.me.get('/api/update/state').status_code, 200)
        # Perangkat lain diarahkan ke halaman login
        r = self.other.get('/api/update/state')
        self.assertEqual(r.status_code, 302)
        self.assertIn('/login', r.headers['Location'])

    def test_login_with_new_pin(self):
        self.change('1234', '5678')
        fresh = self.module.app.test_client()
        self.assertIn('/login', fresh.get('/hub').headers['Location'])
        self.assertEqual(fresh.post('/login', data={'password': '1234'}).status_code, 200)  # PIN lama ditolak (form tampil lagi)
        r = fresh.post('/login', data={'password': '5678'})
        self.assertEqual(r.status_code, 302)
        self.assertIn('/hub', r.headers['Location'])

    def test_requires_login(self):
        r = self.module.app.test_client().post('/api/change_pin', json={})
        self.assertEqual(r.status_code, 302)


if __name__ == '__main__':
    unittest.main()
