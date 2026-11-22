import os
import queue
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ['MANAGER_KECIL_START_WORKERS'] = '0'


def touch(path, data=b'x'):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data)


class DirectUploadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app
        cls.module = app

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(patch.stopall)
        self.root = self.tmp.name
        app = self.module
        self.channels = [{'id': 1, 'name': 'Ch', 'yt_id': 'UC1', 'thumbnail': '', 'status': 'ok', 'client_id': 'c.apps', 'creds_list': ['{}']}]
        self.q = queue.PriorityQueue()
        patch.multiple(app, database_channel=self.channels, active_tasks=[], history_tasks=[],
                       render_queue=self.q, BASE_UPLOAD=self.root, BASE_DIR=self.root).start()
        patch.object(app, 'save_tasks_db').start()
        self.ready = os.path.join(self.root, 'UC1', 'ready_videos')
        touch(os.path.join(self.ready, '01. Lofi Hujan.mp4'))
        touch(os.path.join(self.ready, 'Jazz Pagi.mov'))
        touch(os.path.join(self.ready, 'catatan.txt'))
        self.client = app.app.test_client()
        with self.client.session_transaction() as s:
            s['logged_in'] = True

    def create(self, items, **extra):
        body = {'yt_id': 'UC1', 'items': items, 'tags': 'a,b', 'privacy': 'public', **extra}
        return self.client.post('/api/direct_upload', json=body)

    def item(self, name, title='Judul', date='2026-12-01 10:00'):
        return {'file': name, 'title': title, 'publish_date': date}

    # ── API ──
    def test_lists_only_video_files_with_queue_flag(self):
        d = self.client.get('/api/direct_upload/files?yt_id=UC1').get_json()
        self.assertEqual([f['name'] for f in d['files']], ['01. Lofi Hujan.mp4', 'Jazz Pagi.mov'])
        self.assertFalse(any(f['queued'] for f in d['files']))
        self.assertEqual(self.create([self.item('Jazz Pagi.mov')]).status_code, 200)
        d = self.client.get('/api/direct_upload/files?yt_id=UC1').get_json()
        self.assertEqual({f['name']: f['queued'] for f in d['files']}, {'01. Lofi Hujan.mp4': False, 'Jazz Pagi.mov': True})

    def test_create_queues_tasks_without_render_settings(self):
        r = self.create([self.item('01. Lofi Hujan.mp4', 'Lofi Hujan', '2026-12-01 10:00'),
                         self.item('Jazz Pagi.mov', '', '2026-12-02 10:00')], playlist_id='PL1', description='Desk')
        self.assertEqual(r.status_code, 200, r.get_json())
        tasks = self.module.active_tasks
        self.assertEqual(len(tasks), 2)
        bp = tasks[1]['blueprint']
        self.assertEqual((bp['source_mode'], bp['ready_file'], bp['title']), ('ready_video', 'Jazz Pagi.mov', 'Jazz Pagi'))
        self.assertEqual((bp['playlist_id'], bp['description'], bp['publish_date']), ('PL1', 'Desk', '2026-12-02 10:00'))
        self.assertEqual(self.q.qsize(), 2)

    def test_create_validation(self):
        self.assertEqual(self.create([]).status_code, 400)
        self.assertEqual(self.create([self.item('tidak-ada.mp4')]).status_code, 400)
        self.assertEqual(self.create([self.item('../../etc/passwd')]).status_code, 400)
        self.assertEqual(self.create([self.item('Jazz Pagi.mov'), self.item('Jazz Pagi.mov')]).status_code, 400)
        self.assertEqual(self.create([self.item('Jazz Pagi.mov', date='besok')]).status_code, 400)
        self.assertEqual(self.client.post('/api/direct_upload', json={'yt_id': 'X', 'items': [self.item('a')]}).status_code, 404)
        self.assertEqual(self.create([self.item('Jazz Pagi.mov')]).status_code, 200)
        self.assertEqual(self.create([self.item('Jazz Pagi.mov')]).status_code, 409)  # sudah di antrean
        self.assertEqual(len(self.module.active_tasks), 1)

    def test_ready_tasks_do_not_count_as_background_demand(self):
        self.channels[0]['content_mode'] = 'adsense'
        touch(os.path.join(self.root, 'UC1', 'backgrounds', 'bg.jpg'))
        touch(os.path.join(self.root, 'UC1', 'audio_non_cid', 'a.mp3'))
        self.create([self.item('Jazz Pagi.mov'), self.item('01. Lofi Hujan.mp4')])
        self.assertIsNone(self.module.factory_stock_error('UC1', {'bg_count': 1}, 1))

    # ── Worker ──
    def run_worker(self, upload_side_effect=None):
        app = self.module

        class EndLoop(BaseException):
            pass

        self.create([self.item('01. Lofi Hujan.mp4', 'Lofi Hujan')])
        durable = Mock()
        durable.next_chunk.side_effect = upload_side_effect or [(None, {'id': 'VID123'})]
        history, thumb = Mock(), Mock(return_value=None)
        with patch.object(app, 'acquire_resource'), patch.object(app, 'release_resource'), \
             patch.object(app.channel_auth, 'channel_creds', return_value=[(0, '{}')]), \
             patch.object(app.Credentials, 'from_authorized_user_info', return_value=Mock(expired=False)), \
             patch.object(app, 'build', return_value=Mock()), patch.object(app, 'MediaFileUpload'), \
             patch.object(app, 'DurableUpload', return_value=durable), \
             patch.object(app, 'get_and_consume_thumbnail', thumb), \
             patch.object(app, 'render_video_core') as render, patch.object(app.subprocess, 'run') as run, \
             patch.object(app, 'move_to_history', history), patch.object(app, 'notify_event'), \
             patch.object(app.time, 'sleep', side_effect=EndLoop):
            with self.assertRaises(EndLoop):
                app.background_worker()
        render.assert_not_called()
        run.assert_not_called()
        return history, thumb

    def test_worker_uploads_without_render_and_deletes_file(self):
        history, thumb = self.run_worker()
        self.assertIn('Tayang', history.call_args.args[1])
        self.assertEqual(thumb.call_args.args, ('UC1', '01. Lofi Hujan'))
        self.assertFalse(os.path.exists(os.path.join(self.ready, '01. Lofi Hujan.mp4')))
        self.assertTrue(os.path.exists(os.path.join(self.ready, 'Jazz Pagi.mov')))

    def test_worker_keeps_file_when_upload_fails(self):
        from googleapiclient.errors import HttpError
        rejected = HttpError(Mock(status=403, reason='Forbidden'), b'{"error":{"errors":[{"reason":"forbidden"}]}}')
        history, _ = self.run_worker(upload_side_effect=rejected)
        self.assertIn('Gagal', history.call_args.args[1])
        self.assertTrue(os.path.exists(os.path.join(self.ready, '01. Lofi Hujan.mp4')))

    # ── Thumbnail bernama sama ──
    def test_thumbnail_prefers_matching_name(self):
        from PIL import Image
        tdir = os.path.join(self.root, 'UC1', 'thumbnails')
        os.makedirs(tdir)
        for name in ('lain.jpg', 'Lofi Hujan.jpg', 'zzz.jpg'):
            Image.new('RGB', (1280, 720)).save(os.path.join(tdir, name))
        for _ in range(5):
            self.assertTrue(self.module.get_and_consume_thumbnail('UC1', 'lofi hujan').endswith('Lofi Hujan.jpg'))
        self.assertIsNotNone(self.module.get_and_consume_thumbnail('UC1', 'tidak ada'))


if __name__ == '__main__':
    unittest.main()
