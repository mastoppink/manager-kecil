import os
import tempfile
import threading
import unittest
from collections import Counter
from unittest.mock import patch

from core import asset_pool as ap


class AssetPoolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ch = os.path.join(self.tmp.name, 'UC1')
        self.bg = os.path.join(self.ch, 'backgrounds')
        os.makedirs(self.bg)
        for i in range(5):
            open(os.path.join(self.bg, f'bg{i}.jpg'), 'w').close()
        ap._recovered.clear()
        self.addCleanup(ap.set_keep_predicate, None)
        ap.set_keep_predicate(lambda chan_dir, owner: False)

    def ls(self, sub=''):
        p = os.path.join(self.bg, sub)
        return sorted(f for f in os.listdir(p) if os.path.isfile(os.path.join(p, f))) if os.path.isdir(p) else []

    # ── AdSense ──
    def test_adsense_pick_reserves_commit_moves_to_used(self):
        paths = ap.pick(self.ch, 'backgrounds', 2, ap.BG_EXTS, ap.MODE_ADSENSE, owner='factory:1')
        self.assertEqual(len(paths), 2)
        self.assertTrue(all(os.path.exists(p) and f'/{ap.IN_USE_DIR}/' in p for p in paths))
        self.assertEqual(len(self.ls()), 3)
        self.assertEqual(ap.commit(self.ch, 'factory:1'), 2)
        self.assertEqual(len(self.ls(ap.USED_DIR)), 2)
        self.assertEqual(self.ls(ap.IN_USE_DIR), [])
        info = ap.stock_info(self.ch, 'backgrounds')
        self.assertEqual((info['available'], info['in_use'], info['used']), (3, 0, 2))

    def test_adsense_release_returns_to_stock(self):
        ap.pick(self.ch, 'backgrounds', 3, ap.BG_EXTS, ap.MODE_ADSENSE, owner='shorts:a')
        self.assertEqual(ap.release(self.ch, 'shorts:a'), 3)
        self.assertEqual(len(self.ls()), 5)

    def test_adsense_never_reuses_and_raises_when_empty(self):
        seen = []
        for i in range(5):
            seen += [os.path.basename(p) for p in ap.pick(self.ch, 'backgrounds', 1, ap.BG_EXTS, ap.MODE_ADSENSE, owner=f'o{i}')]
            ap.commit(self.ch, f'o{i}')
        self.assertEqual(len(set(seen)), 5)
        with self.assertRaises(ap.StockError) as cm:
            ap.pick(self.ch, 'backgrounds', 1, ap.BG_EXTS, ap.MODE_ADSENSE, owner='o9')
        self.assertEqual((cm.exception.needed, cm.exception.available), (1, 0))

    def test_adsense_stock_error_does_not_move_files(self):
        with self.assertRaises(ap.StockError):
            ap.pick(self.ch, 'backgrounds', 6, ap.BG_EXTS, ap.MODE_ADSENSE, owner='o')
        self.assertEqual(len(self.ls()), 5)

    def test_parallel_owners_get_distinct_files(self):
        got, errors = [], []

        def worker(i):
            try:
                got.extend(ap.pick(self.ch, 'backgrounds', 1, ap.BG_EXTS, ap.MODE_ADSENSE, owner=f'w{i}'))
            except ap.StockError as e:
                errors.append(e)
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len(got), 5)
        self.assertEqual(len(errors), 3)
        self.assertEqual(len({os.path.basename(p) for p in got}), 5)

    # ── Content ID ──
    def test_cid_shuffle_bag_uses_all_before_repeat_and_keeps_files(self):
        draws = [os.path.basename(ap.pick(self.ch, 'backgrounds', 1, ap.BG_EXTS, ap.MODE_CID)[0]) for _ in range(10)]
        self.assertEqual(len(set(draws[:5])), 5)
        self.assertEqual(len(set(draws[5:])), 5)
        self.assertNotEqual(draws[4], draws[5])
        self.assertEqual(len(self.ls()), 5)

    def test_cid_includes_used_folder(self):
        ap.pick(self.ch, 'backgrounds', 5, ap.BG_EXTS, ap.MODE_ADSENSE, owner='x')
        ap.commit(self.ch, 'x')
        paths = ap.pick(self.ch, 'backgrounds', 3, ap.BG_EXTS, ap.MODE_CID)
        self.assertEqual(len(paths), 3)
        self.assertTrue(all(os.path.exists(p) for p in paths))

    def test_cid_count_larger_than_pool_cycles(self):
        paths = ap.pick(self.ch, 'backgrounds', 8, ap.BG_EXTS, ap.MODE_CID)
        self.assertEqual(len(paths), 8)
        self.assertLessEqual(max(Counter(paths).values()), 2)

    def test_audio_unique_per_draw_and_rotates(self):
        aud = os.path.join(self.ch, 'audio_cid')
        os.makedirs(aud)
        for i in range(4):
            open(os.path.join(aud, f's{i}.mp3'), 'w').close()
        first = ap.pick_audio(self.ch, 'audio_cid', 3)
        self.assertEqual(len(set(first)), 3)
        second = ap.pick_audio(self.ch, 'audio_cid', 10)  # minta lebih dari jumlah file
        self.assertEqual(len(second), 4)
        self.assertEqual(len(set(second)), 4)
        self.assertEqual(ap.pick_audio(self.ch, 'audio_non_cid', 2), [])

    # ── Pemulihan setelah restart ──
    def test_recover_releases_old_process_leases_except_kept(self):
        ap.pick(self.ch, 'backgrounds', 1, ap.BG_EXTS, ap.MODE_ADSENSE, owner='factory:keep')
        ap.pick(self.ch, 'backgrounds', 1, ap.BG_EXTS, ap.MODE_ADSENSE, owner='shorts:drop')
        # file yatim di _dipakai (crash sebelum status tersimpan)
        open(os.path.join(self.bg, ap.IN_USE_DIR, 'yatim.jpg'), 'w').close()
        ap._recovered.clear()
        ap.set_keep_predicate(lambda chan_dir, owner: owner == 'factory:keep')
        with patch.object(ap, 'BOOT_ID', 'proses-baru'):
            ap.pick(self.ch, 'backgrounds', 1, ap.BG_EXTS, ap.MODE_CID)  # akses pertama memicu pemulihan
        self.assertTrue(ap.has_lease(self.ch, 'factory:keep'))
        self.assertFalse(ap.has_lease(self.ch, 'shorts:drop'))
        self.assertEqual(len(self.ls(ap.IN_USE_DIR)), 1)
        self.assertIn('yatim.jpg', self.ls())

    def test_no_recovery_before_predicate_registered(self):
        ap.pick(self.ch, 'backgrounds', 1, ap.BG_EXTS, ap.MODE_ADSENSE, owner='factory:x')
        ap._recovered.clear()
        ap.set_keep_predicate(None)
        with patch.object(ap, 'BOOT_ID', 'proses-baru'):
            ap.pick(self.ch, 'backgrounds', 1, ap.BG_EXTS, ap.MODE_CID)
        self.assertTrue(ap.has_lease(self.ch, 'factory:x'))

    # ── Kelola folder terpakai ──
    def test_restore_and_delete_used(self):
        ap.pick(self.ch, 'backgrounds', 3, ap.BG_EXTS, ap.MODE_ADSENSE, owner='x')
        ap.commit(self.ch, 'x')
        one = self.ls(ap.USED_DIR)[0]
        self.assertEqual(ap.restore_used(self.ch, 'backgrounds', one), 1)
        self.assertEqual(len(self.ls()), 3)
        self.assertEqual(ap.delete_used(self.ch, 'backgrounds'), 2)
        self.assertEqual(self.ls(ap.USED_DIR), [])
        for bad in ('../x', '.hidden', ''):
            with self.assertRaises(ValueError):
                ap.restore_used(self.ch, 'backgrounds', bad or '..')
        with self.assertRaises(ValueError):
            ap.delete_used(self.ch, 'audio_cid')

    def test_name_collision_on_restore(self):
        ap.pick(self.ch, 'backgrounds', 1, ap.BG_EXTS, ap.MODE_ADSENSE, owner='x')
        ap.commit(self.ch, 'x')
        used = self.ls(ap.USED_DIR)[0]
        open(os.path.join(self.bg, used), 'w').close()  # file baru dengan nama sama diupload
        ap.restore_used(self.ch, 'backgrounds', used)
        self.assertEqual(len(self.ls()), 6)

    def test_channel_mode_and_audio_folder(self):
        self.assertEqual(ap.channel_mode({'yt_id': 'baru'}), ap.MODE_ADSENSE)  # default channel baru
        self.assertEqual(ap.channel_mode({}), ap.MODE_CID)              # channel tidak ditemukan
        self.assertEqual(ap.channel_mode({'content_mode': 'aneh'}), ap.MODE_ADSENSE)
        self.assertEqual(ap.channel_mode({'content_mode': 'cid'}), ap.MODE_CID)
        self.assertEqual(ap.channel_mode(None), ap.MODE_CID)            # tanpa channel: tidak ada stok sekali pakai
        legacy = [{'yt_id': 'a'}, {'yt_id': 'b', 'content_mode': 'adsense'}, {'yt_id': 'c', 'content_mode': 'cid'}]
        self.assertTrue(ap.pin_legacy_modes(legacy))
        self.assertEqual([c['content_mode'] for c in legacy], ['cid', 'adsense', 'cid'])
        self.assertFalse(ap.pin_legacy_modes(legacy))
        self.assertEqual(ap.audio_folder(ap.MODE_ADSENSE), 'audio_non_cid')
        self.assertEqual(ap.audio_folder(ap.MODE_CID), 'audio_cid')


if __name__ == '__main__':
    unittest.main()
