import os
import queue
import subprocess
import tempfile
import unittest
from unittest.mock import patch

os.environ['MANAGER_KECIL_START_WORKERS'] = '0'

import cv2
import numpy as np
from PIL import Image


class ResolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app
        cls.module = app

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(patch.stopall)

    def image(self, name, w, h):
        path = os.path.join(self.tmp.name, name)
        Image.new('RGB', (w, h)).save(path)
        return path

    def video(self, name, w, h):
        path = os.path.join(self.tmp.name, name)
        out = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*'mp4v'), 10, (w, h))
        for _ in range(3):
            out.write(np.zeros((h, w, 3), dtype=np.uint8))
        out.release()
        return path

    def test_presets(self):
        r = self.module.resolve_render_size
        self.assertEqual(r('720p', []), (1280, 720))
        self.assertEqual(r('1080p', []), (1920, 1080))
        self.assertEqual(r('2k', []), (2560, 1440))
        self.assertEqual(r('4k', []), (3840, 2160))
        self.assertEqual(r('aneh', []), (1280, 720))
        self.assertEqual(r(None, []), (1280, 720))

    def test_source_follows_first_readable_media(self):
        r = self.module.resolve_render_size
        self.assertEqual(r('source', [self.image('uw.jpg', 2560, 1080)]), (2560, 1080))
        self.assertEqual(r('source', [self.video('v.mp4', 1920, 1080)]), (1920, 1080))
        self.assertEqual(r('source', [self.image('tegak.png', 1080, 1920)]), (1080, 1920))
        # file pertama rusak -> pakai file berikutnya
        bad = os.path.join(self.tmp.name, 'rusak.mp4')
        open(bad, 'wb').close()
        self.assertEqual(r('source', [bad, self.image('ok.jpg', 1600, 900)]), (1600, 900))
        self.assertEqual(r('source', [bad]), (1280, 720))

    def test_source_is_capped_even_and_not_tiny(self):
        r = self.module.resolve_render_size
        self.assertEqual(r('source', [self.image('8k.jpg', 7680, 4320)]), (3840, 2160))
        w, h = r('source', [self.image('ganjil.jpg', 1281, 721)])
        self.assertEqual((w % 2, h % 2), (0, 0))
        w, h = r('source', [self.image('kecil.jpg', 320, 180)])
        self.assertGreaterEqual(min(w, h), 360)
        self.assertAlmostEqual(w / h, 16 / 9, places=1)

    def test_render_output_size(self):
        app = self.module
        patch.object(app, 'save_tasks_db').start()
        audio = os.path.join(self.tmp.name, 'a.mp3')
        subprocess.run([app.get_ffmpeg_path(), '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=220:duration=1', audio], check=True)
        bg = self.image('bg.jpg', 2560, 1080)
        cfg = {'use_floating_card': True, 'floating_card_mode': 'full',
               'track_schedule': [{'title': 'Tes', 'start': 0, 'end': 1, 'duration': 1}]}
        for res, size in (('1080p', '1920,1080'), ('source', '2560,1080')):
            out = os.path.join(self.tmp.name, f'{res}.mp4')
            app.render_video_core(f't-{res}', audio, [bg], out, 1.0, dict(cfg, resolution=res))
            got = subprocess.run([app.get_ffprobe_path(), '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                                  'stream=width,height', '-of', 'csv=p=0', out], capture_output=True, text=True).stdout.strip()
            self.assertEqual(got, size)

    def test_batch_stores_resolution(self):
        app = self.module
        patch.multiple(app, active_tasks=[], history_tasks=[], render_queue=queue.PriorityQueue(),
                       database_channel=[{'yt_id': 'ch'}]).start()
        for name in ('save_tasks_db', 'save_channels'):
            patch.object(app, name).start()
        patch.object(app, 'factory_stock_error', return_value=None).start()
        for res, expected in (('4k', '4k'), ('source', 'source'), ('8k', '720p'), (None, '720p')):
            app.active_tasks.clear()
            with app.app.test_request_context('/api/batch_create', method='POST', json={
                    'yt_id': 'ch', 'count': 1, 'start_date': '2026-12-01T12:00', 'resolution': res}):
                app.batch_create()
            self.assertEqual(app.active_tasks[0]['blueprint']['resolution'], expected)


if __name__ == '__main__':
    unittest.main()
