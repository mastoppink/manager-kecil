import os
import tempfile
import threading
import time
import unittest
from collections import namedtuple
from unittest.mock import patch

os.environ['MANAGER_KECIL_START_WORKERS'] = '0'

from core import drive_import as di

Item = namedtuple('Item', 'id path local_path')
FOLDER = 'https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp?usp=sharing'
FILE = 'https://drive.google.com/file/d/1FileIdAbcdefghij/view?usp=drive_link'


def fake_download(fail=None, slow=None):
    """Tiruan gdown.download: tulis isi file & panggil progress."""
    def _dl(id=None, output=None, progress=None, cancel=None, skip_download=False, **kw):
        if fail and id in fail:
            raise Exception(fail[id])
        if slow and id in slow:
            while not cancel.is_set():
                time.sleep(0.01)
            raise Exception('cancelled')
        data = b'x' * 1000
        if progress:
            progress(500, 1000)
            progress(1000, 1000)
        with open(output, 'wb') as f:
            f.write(data)
        return output
    return _dl


class DriveImportUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = os.path.join(self.tmp.name, 'backgrounds')
        di._jobs.clear()

    def wait(self, job_id):
        for _ in range(300):
            if di._jobs[job_id]['status'] != 'berjalan':
                return di._jobs[job_id]
            time.sleep(0.01)
        self.fail('impor tidak selesai')

    def test_parse_url(self):
        self.assertEqual(di.parse_url(FOLDER), ('folder', '1AbCdEfGhIjKlMnOp'))
        self.assertEqual(di.parse_url('https://drive.google.com/drive/u/0/folders/1AbCdEfGhIjKlMnOp'), ('folder', '1AbCdEfGhIjKlMnOp'))
        self.assertEqual(di.parse_url(FILE), ('file', '1FileIdAbcdefghij'))
        self.assertEqual(di.parse_url('https://drive.google.com/open?id=1FileIdAbcdefghij'), ('file', '1FileIdAbcdefghij'))
        self.assertEqual(di.parse_url('https://drive.google.com/uc?export=download&id=1FileIdAbcdefghij'), ('file', '1FileIdAbcdefghij'))
        for bad in ('', 'https://example.com/x', 'https://drive.google.com/drive/my-drive'):
            with self.assertRaises(di.DriveError):
                di.parse_url(bad)

    def test_list_folder_and_file(self):
        found = [Item('1aaaaaaaaaaa', 'Root/sub/bg1.jpg', ''), Item('1bbbbbbbbbbb', 'Root/clip.mp4', '')]
        with patch.object(di.gdown, 'download_folder', return_value=found):
            kind, items = di.list_link(FOLDER)
        self.assertEqual(kind, 'folder')
        self.assertEqual([i['name'] for i in items], ['bg1.jpg', 'clip.mp4'])
        with patch.object(di.gdown, 'download', return_value=Item('1FileIdAbcdefghij', 'Lagu.mp3', '')):
            kind, items = di.list_link(FILE)
        self.assertEqual((kind, items[0]['name']), ('file', 'Lagu.mp3'))

    def test_list_error_is_friendly(self):
        with patch.object(di.gdown, 'download_folder', side_effect=Exception('Failed to retrieve folder contents')):
            with self.assertRaises(di.DriveError) as cm:
                di.list_link(FOLDER)
        self.assertIn('Siapa saja yang memiliki link', str(cm.exception))

    def test_download_job_progress_skip_and_errors(self):
        os.makedirs(self.folder)
        open(os.path.join(self.folder, 'ada.jpg'), 'w').close()
        items = [{'id': '1aaaaaaaaaaa', 'name': 'baru.jpg'}, {'id': '1bbbbbbbbbbb', 'name': 'ada.jpg'},
                 {'id': '1ccccccccccc', 'name': 'terkunci.jpg'}]
        fail = {'1ccccccccccc': 'Only the owner and editors can download this file.'}
        with patch.object(di.gdown, 'download', side_effect=fake_download(fail)):
            job = self.wait(di.start_job('UC1', self.folder, 'Background', items, ('.jpg',)))
        st = {i['name']: i for i in job['items']}
        self.assertEqual(st['baru.jpg']['status'], 'selesai')
        self.assertEqual((st['baru.jpg']['bytes'], st['baru.jpg']['total']), (1000, 1000))
        self.assertEqual(st['ada.jpg']['status'], 'dilewati')
        self.assertEqual(st['terkunci.jpg']['status'], 'gagal')
        self.assertIn('mematikan izin download', st['terkunci.jpg']['error'])
        self.assertEqual(sorted(os.listdir(self.folder)), ['ada.jpg', 'baru.jpg'])  # tidak ada sisa file .drive-part
        self.assertEqual(job['status'], 'selesai')

    def test_rejects_bad_items(self):
        for bad in ([{'id': '1aaaaaaaaaaa', 'name': 'video.mp4'}],            # ekstensi salah
                    [{'id': '1aaaaaaaaaaa', 'name': '.tersembunyi.jpg'}],
                    [{'id': '../../x', 'name': 'a.jpg'}],
                    []):
            with self.assertRaises(di.DriveError):
                di.start_job('UC1', self.folder, 'Background', bad, ('.jpg',))
        job_id = None
        with patch.object(di.gdown, 'download', side_effect=fake_download()):
            job_id = di.start_job('UC1', self.folder, 'B', [{'id': '1aaaaaaaaaaa', 'name': '../../keluar.jpg'}], ('.jpg',))
            self.wait(job_id)
        self.assertTrue(os.path.exists(os.path.join(self.folder, 'keluar.jpg')))  # path dipotong ke nama file saja

    def test_cancel(self):
        items = [{'id': '1aaaaaaaaaaa', 'name': 'lama.mp4'}, {'id': '1bbbbbbbbbbb', 'name': 'berikut.mp4'}]
        with patch.object(di.gdown, 'download', side_effect=fake_download(slow={'1aaaaaaaaaaa'})):
            job_id = di.start_job('UC1', self.folder, 'V', items, ('.mp4',))
            time.sleep(0.05)
            self.assertTrue(di.cancel_job(job_id))
            job = self.wait(job_id)
        self.assertEqual(job['status'], 'dibatalkan')
        self.assertEqual([i['status'] for i in job['items']], ['dibatalkan', 'dibatalkan'])
        self.assertEqual(os.listdir(self.folder), [])
        self.assertFalse(di.cancel_job('tidak-ada'))

    def test_low_disk_fails_item(self):
        Usage = namedtuple('Usage', 'total used free')
        with patch.object(di.shutil, 'disk_usage', return_value=Usage(10, 9, 1024)), \
             patch.object(di.gdown, 'download', side_effect=fake_download()) as dl:
            job = self.wait(di.start_job('UC1', self.folder, 'B', [{'id': '1aaaaaaaaaaa', 'name': 'a.jpg'}], ('.jpg',)))
        self.assertEqual(job['items'][0]['status'], 'gagal')
        self.assertIn('2 GB', job['items'][0]['error'])
        dl.assert_not_called()

    def test_jobs_for_hides_internal_fields(self):
        with patch.object(di.gdown, 'download', side_effect=fake_download()):
            self.wait(di.start_job('UC1', self.folder, 'B', [{'id': '1aaaaaaaaaaa', 'name': 'a.jpg'}], ('.jpg',)))
        jobs = di.jobs_for('UC1')
        self.assertEqual(len(jobs), 1)
        self.assertFalse(any(k.startswith('_') for k in jobs[0]))
        self.assertEqual(di.jobs_for('LAIN'), [])


class DriveImportRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app
        cls.module = app

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(patch.stopall)
        di._jobs.clear()
        patch.multiple(self.module, database_channel=[{'yt_id': 'UC1', 'name': 'C'}], BASE_UPLOAD=self.tmp.name).start()
        self.client = self.module.app.test_client()
        with self.client.session_transaction() as s:
            s['logged_in'] = True

    def test_list_marks_allowed_and_existing(self):
        os.makedirs(os.path.join(self.tmp.name, 'UC1', 'backgrounds'))
        open(os.path.join(self.tmp.name, 'UC1', 'backgrounds', 'ada.jpg'), 'w').close()
        found = [Item('1aaaaaaaaaaa', 'R/ada.jpg', ''), Item('1bbbbbbbbbbb', 'R/baru.png', ''), Item('1ccccccccccc', 'R/lagu.mp3', '')]
        with patch.object(di.gdown, 'download_folder', return_value=found):
            d = self.client.post('/api/drive/list', json={'yt_id': 'UC1', 'type': 'background', 'url': FOLDER}).get_json()
        st = {i['name']: (i['allowed'], i['exists']) for i in d['items']}
        self.assertEqual(st, {'ada.jpg': (True, True), 'baru.png': (True, False), 'lagu.mp3': (False, False)})

    def test_validation_errors(self):
        post = lambda body: self.client.post('/api/drive/list', json=body)
        self.assertEqual(post({'yt_id': 'UC1', 'type': 'apa', 'url': FOLDER}).status_code, 400)
        self.assertEqual(post({'yt_id': 'X', 'type': 'background', 'url': FOLDER}).status_code, 400)
        self.assertEqual(post({'yt_id': 'UC1', 'type': 'background', 'url': 'https://x.com'}).status_code, 400)
        r = self.client.post('/api/drive/import', json={'yt_id': 'UC1', 'type': 'audio_cid', 'items': [{'id': '1aaaaaaaaaaa', 'name': 'a.jpg'}]})
        self.assertEqual(r.status_code, 400)

    def test_import_and_jobs(self):
        with patch.object(di.gdown, 'download', side_effect=fake_download()):
            r = self.client.post('/api/drive/import', json={'yt_id': 'UC1', 'type': 'audio_cid', 'items': [{'id': '1aaaaaaaaaaa', 'name': 'lagu.mp3'}]})
            self.assertEqual(r.status_code, 200)
            for _ in range(300):
                jobs = self.client.get('/api/drive/jobs?yt_id=UC1').get_json()
                if jobs and jobs[0]['status'] != 'berjalan':
                    break
                time.sleep(0.01)
        self.assertEqual(jobs[0]['items'][0]['status'], 'selesai')
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, 'UC1', 'audio_cid', 'lagu.mp3')))
        self.assertEqual(self.client.post('/api/drive/cancel', json={'job_id': 'x'}).get_json()['status'], 'error')


if __name__ == '__main__':
    unittest.main()
