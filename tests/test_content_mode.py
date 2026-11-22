import os
import tempfile
import unittest
from unittest.mock import patch

os.environ['MANAGER_KECIL_START_WORKERS'] = '0'

from core import asset_pool


def touch(*parts):
    path = os.path.join(*parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, 'w').close()


class ContentModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app
        import shorts.queue_worker as sq
        cls.module, cls.sq = app, sq

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(patch.stopall)
        self.root = self.tmp.name
        self.channels = [{'id': 1, 'name': 'Ads', 'yt_id': 'ADS', 'thumbnail': '', 'status': 'Connected', 'content_mode': 'adsense'},
                         {'id': 2, 'name': 'Cid', 'yt_id': 'CID', 'thumbnail': '', 'status': 'Connected'}]
        app = self.module
        patch.multiple(app, database_channel=self.channels, active_tasks=[], BASE_UPLOAD=self.root, BASE_DIR=self.root).start()
        patch.object(app, 'save_channels').start()
        patch.object(self.sq, 'UPLOADS_ROOT', self.root).start()
        patch.object(self.sq, '_get_channel', side_effect=lambda cid: next((c for c in self.channels if c['yt_id'] == cid), None)).start()
        self.shorts_jobs = []
        patch.object(self.sq, 'read_db', side_effect=lambda: self.shorts_jobs).start()
        for i in range(3):
            touch(self.root, 'ADS', 'backgrounds', f'bg{i}.jpg')
            touch(self.root, 'ADS', 'shorts_bg', f's{i}.jpg')
        touch(self.root, 'ADS', 'audio_non_cid', 'a.mp3')
        touch(self.root, 'CID', 'backgrounds', 'bg.jpg')
        touch(self.root, 'CID', 'audio_cid', 'c.mp3')
        self.client = app.app.test_client()
        with self.client.session_transaction() as s:
            s['logged_in'] = True

    # ── endpoint mode ──
    def test_set_mode_and_listed_in_channels(self):
        r = self.client.post('/api/channel_mode', json={'yt_id': 'CID', 'mode': 'adsense'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.channels[1]['content_mode'], 'adsense')
        self.assertEqual(self.client.post('/api/channel_mode', json={'yt_id': 'CID', 'mode': 'x'}).status_code, 400)
        self.assertEqual(self.client.post('/api/channel_mode', json={'yt_id': 'NOPE', 'mode': 'cid'}).status_code, 404)
        modes = {c['yt_id']: c['content_mode'] for c in self.client.get('/api/get_channels').get_json()}
        self.assertEqual(modes, {'ADS': 'adsense', 'CID': 'adsense'})

    # ── endpoint stok & folder terpakai ──
    def test_asset_pool_info_restore_delete(self):
        ch = os.path.join(self.root, 'ADS')
        asset_pool.pick(ch, 'backgrounds', 2, asset_pool.BG_EXTS, 'adsense', owner='factory:1')
        asset_pool.commit(ch, 'factory:1')
        d = self.client.get('/api/asset_pool?yt_id=ADS').get_json()
        self.assertEqual(d['mode'], 'adsense')
        self.assertEqual(d['audio_folder'], 'audio_non_cid')
        self.assertEqual((d['folders']['backgrounds']['available'], d['folders']['backgrounds']['used']), (1, 2))
        name = d['folders']['backgrounds']['used_files'][0]['name']
        r = self.client.post('/api/asset_pool/restore', json={'yt_id': 'ADS', 'folder': 'backgrounds', 'name': name}).get_json()
        self.assertEqual(r['status'], 'success')
        r = self.client.post('/api/asset_pool/delete_used', json={'yt_id': 'ADS', 'folder': 'backgrounds'}).get_json()
        self.assertIn('1 file', r['message'])
        d = self.client.get('/api/asset_pool?yt_id=ADS').get_json()
        self.assertEqual((d['folders']['backgrounds']['available'], d['folders']['backgrounds']['used']), (2, 0))
        self.assertEqual(self.client.post('/api/asset_pool/restore', json={'yt_id': 'ADS', 'folder': 'backgrounds', 'name': '../x'}).status_code, 400)
        self.assertEqual(self.client.post('/api/asset_pool/hapus', json={}).status_code, 404)
        self.assertEqual(self.client.get('/api/asset_pool?yt_id=NOPE').status_code, 404)

    # ── validasi stok Factory ──
    def test_factory_stock_check_adsense(self):
        err = self.module.factory_stock_error
        self.assertIsNone(err('ADS', {'bg_count': 1}, 3))
        self.assertIn('butuh 4', err('ADS', {'bg_count': 1}, 4))
        self.assertIn('butuh 4', err('ADS', {'bg_count': 2}, 2))
        # antrean yang belum jalan ikut dihitung
        self.module.active_tasks.append({'id': 7, 'yt_id': 'ADS', 'status': 'Menunggu Giliran (Queue) ⏳', 'blueprint': {'bg_count': 2}})
        self.assertIn('antrean lain butuh 2', err('ADS', {'bg_count': 1}, 2))
        # task yang sudah memesan footage tidak dihitung dua kali
        asset_pool.pick(os.path.join(self.root, 'ADS'), 'backgrounds', 2, asset_pool.BG_EXTS, 'adsense', owner='factory:7')
        self.assertIsNone(err('ADS', {'bg_count': 1}, 1))
        # mode video utuh memakai folder base_videos
        self.assertIn('video sumber', err('ADS', {'source_mode': 'single_video', 'vid_per_video': 1}, 1))

    def test_factory_stock_check_cid_and_audio(self):
        err = self.module.factory_stock_error
        self.assertIsNone(err('CID', {'bg_count': 5}, 100))  # Content ID: footage boleh berulang
        os.remove(os.path.join(self.root, 'CID', 'audio_cid', 'c.mp3'))
        self.assertIn('audio CID', err('CID', {}, 1))
        self.assertEqual(err('NOPE', {}, 1), 'Channel tidak ditemukan.')

    def test_batch_create_rejected_when_stock_short(self):
        r = self.client.post('/api/batch_create', json={'yt_id': 'ADS', 'count': 5, 'bg_count': 1, 'start_date': '2026-12-01T12:00'})
        self.assertEqual(r.status_code, 400)
        self.assertIn('Stok background 16:9 tidak cukup', r.get_json()['message'])
        self.assertEqual(self.module.active_tasks, [])

    # ── worker Factory: footage kembali ke stok bila render gagal ──
    def test_factory_worker_releases_footage_on_failure(self):
        import queue
        from unittest.mock import Mock
        app = self.module

        class EndLoop(BaseException):
            pass

        q = queue.PriorityQueue()
        task = {'id': 5, 'yt_id': 'ADS', 'title': 'T', 'bg_count': 2, 'mp3_non_cid_count': 1, 'mp3_cid_count': 0}
        app.active_tasks.append({'id': 5, 'yt_id': 'ADS', 'status': 'Queue', 'title': 'T'})
        q.put((1, 0, task))
        picked = []
        real_pick = asset_pool.pick

        def spy_pick(*a, **kw):
            res = real_pick(*a, **kw)
            picked.extend(res)
            return res

        probe = Mock(stdout='12.0', returncode=0)
        history = Mock()
        with patch.object(app, 'render_queue', q), patch.object(app, 'save_tasks_db'), \
             patch.object(app, 'acquire_resource'), patch.object(app, 'release_resource'), \
             patch.object(app.subprocess, 'run', return_value=probe), \
             patch.object(app.asset_pool, 'pick', side_effect=spy_pick), \
             patch.object(app, 'render_video_core', side_effect=RuntimeError('render gagal')), \
             patch.object(app, 'move_to_history', history), patch.object(app, 'notify_event'), \
             patch.object(app.time, 'sleep', side_effect=EndLoop):
            with self.assertRaises(EndLoop):
                app.background_worker()
        self.assertEqual(len(picked), 2)
        self.assertTrue(all('/_dipakai/' in p for p in picked))
        self.assertIn('render gagal', history.call_args.args[1])
        ch = os.path.join(self.root, 'ADS')
        info = asset_pool.stock_info(ch, 'backgrounds')
        self.assertEqual((info['available'], info['in_use'], info['used']), (3, 0, 0))
        self.assertFalse(asset_pool.has_lease(ch, 'factory:5'))

    # ── validasi stok Shorts ──
    def test_shorts_stock_check(self):
        err = self.sq.shorts_stock_error
        self.assertIsNone(err('ADS', 'shorts_bg', 3))
        self.assertIn('butuh 4', err('ADS', 'shorts_bg', 4))
        self.shorts_jobs.append({'id': 'j1', 'yt_id': 'ADS', 'status': 'Menunggu Giliran (Queue) ⏳'})
        self.assertIn('antrean lain butuh 1', err('ADS', 'shorts_bg', 3))
        self.assertIsNone(err('CID', 'shorts_bg', 50))  # CID: shorts_bg kosong -> pakai backgrounds, boleh berulang
        os.remove(os.path.join(self.root, 'ADS', 'audio_non_cid', 'a.mp3'))
        self.assertIn('audio non-CID', err('ADS', 'shorts_bg', 1))

    def test_shorts_fallback_only_when_primary_never_filled(self):
        folder = self.sq._shorts_bg_folder
        self.assertEqual(folder('ADS', 'shorts_bg'), 'shorts_bg')
        self.assertEqual(folder('CID', 'shorts_bg'), 'backgrounds')
        ch = os.path.join(self.root, 'ADS')
        asset_pool.pick(ch, 'shorts_bg', 3, asset_pool.BG_EXTS, 'adsense', owner='shorts:x')
        asset_pool.commit(ch, 'shorts:x')
        # stok shorts_bg habis, tapi folder pernah diisi -> tidak diam-diam mengambil background Factory
        self.assertEqual(folder('ADS', 'shorts_bg'), 'shorts_bg')


if __name__ == '__main__':
    unittest.main()
