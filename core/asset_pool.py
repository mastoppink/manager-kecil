"""Pemilihan aset per channel berdasarkan mode konten.

Mode "cid" (Content ID): footage boleh dipakai berulang, diacak dengan sistem kocok kartu
(semua file dipakai dulu sebelum ada yang terulang).
Mode "adsense": footage (backgrounds, shorts_bg, base_videos) hanya sekali pakai.
  stok      -> folder/<nama>             file yang belum dipakai
  dipesan   -> folder/_dipakai/<nama>    sedang dipakai task yang berjalan
  terpakai  -> folder/_terpakai/<nama>   sudah masuk ke video yang berhasil dibuat
Audio di kedua mode hanya diacak urutannya (kocok kartu), tidak pernah habis.

Status disimpan di uploads/<channel>/.asset_state.json:
  bags:   {folder: [sisa kartu]}
  in_use: {owner: {"boot": id_proses, "files": [[folder, nama], ...]}}
"""
import os
import random
import shutil
import threading
import uuid

from core.json_store import lock_for, read_json, write_json

MODE_CID = 'cid'
MODE_ADSENSE = 'adsense'
MODES = (MODE_CID, MODE_ADSENSE)
IN_USE_DIR = '_dipakai'
USED_DIR = '_terpakai'
STATE_FILE = '.asset_state.json'
CONSUMABLE = ('backgrounds', 'shorts_bg', 'base_videos')

BG_EXTS = ('.mp4', '.jpg', '.jpeg', '.png', '.gif', '.webp', '.mov', '.webm', '.mkv')
VIDEO_EXTS = ('.mp4', '.mov', '.webm', '.mkv')
AUDIO_EXTS = ('.mp3', '.wav')

BOOT_ID = uuid.uuid4().hex  # beda di setiap proses server
_lock = threading.RLock()
_recovered = set()
_keep_predicate = None


class StockError(Exception):
    def __init__(self, folder, needed, available):
        self.folder, self.needed, self.available = folder, needed, available
        super().__init__(f"Stok {folder_label(folder)} habis (mode AdSense): butuh {needed}, tersisa {available}. "
                         f"Upload footage baru atau kembalikan file dari folder Terpakai di Settings > Galeri Aset.")


def folder_label(folder):
    return {'backgrounds': 'background 16:9', 'shorts_bg': 'background Shorts 9:16',
            'base_videos': 'video sumber', 'audio_cid': 'audio CID', 'audio_non_cid': 'audio non-CID'}.get(folder, folder)


DEFAULT_MODE = MODE_ADSENSE  # channel baru otomatis AdSense


def channel_mode(channel):
    """Mode channel. Channel tanpa field content_mode dianggap mode default (AdSense);
    channel lama sudah diberi 'cid' eksplisit oleh migrasi saat start agar perilakunya tidak berubah."""
    if not channel:
        return MODE_CID
    mode = channel.get('content_mode')
    return mode if mode in MODES else DEFAULT_MODE


def pin_legacy_modes(channels):
    """Channel yang belum punya content_mode (dibuat sebelum mode default AdSense) ditandai 'cid'.
    Mengembalikan True bila ada perubahan."""
    changed = False
    for c in channels:
        if isinstance(c, dict) and c.get('content_mode') not in MODES:
            c['content_mode'] = MODE_CID
            changed = True
    return changed


def audio_folder(mode):
    return 'audio_non_cid' if mode == MODE_ADSENSE else 'audio_cid'


def set_keep_predicate(fn):
    """fn(chan_dir, owner) -> True bila lease milik proses lama harus dipertahankan."""
    global _keep_predicate
    _keep_predicate = fn


# ── helper file ────────────────────────────────────────────
def _valid_name(name):
    return bool(name) and os.path.basename(name) == name and name not in ('.', '..') and not name.startswith('.')


def _files(path, exts):
    if not os.path.isdir(path):
        return []
    return sorted(f for f in os.listdir(path)
                  if not f.startswith('.') and f.lower().endswith(exts)
                  and not f.endswith('_compressed.jpg') and os.path.isfile(os.path.join(path, f)))


def _move_unique(src, dst_dir):
    os.makedirs(dst_dir, exist_ok=True)
    name = os.path.basename(src)
    stem, ext = os.path.splitext(name)
    dst, n = os.path.join(dst_dir, name), 1
    while os.path.exists(dst):
        dst = os.path.join(dst_dir, f"{stem}_{n}{ext}")
        n += 1
    shutil.move(src, dst)
    return os.path.basename(dst)


def _state_path(chan_dir):
    return os.path.join(chan_dir, STATE_FILE)


def _load(chan_dir):
    st = read_json(_state_path(chan_dir), {})
    if not isinstance(st, dict):
        st = {}
    st.setdefault('bags', {})
    st.setdefault('in_use', {})
    return st


def _save(chan_dir, st):
    os.makedirs(chan_dir, exist_ok=True)
    write_json(_state_path(chan_dir), st)


# ── pemulihan setelah restart ──────────────────────────────
def _recover_locked(chan_dir, st):
    """Kembalikan ke stok file yang dipesan proses lama (kecuali yang perlu dipertahankan)."""
    changed = False
    for owner, lease in list(st['in_use'].items()):
        if lease.get('boot') == BOOT_ID:
            continue
        if _keep_predicate and _keep_predicate(chan_dir, owner):
            continue
        _release_files(chan_dir, lease.get('files', []))
        del st['in_use'][owner]
        changed = True
    # File di _dipakai yang tidak tercatat (crash di antara pindah file dan simpan status)
    tracked = {(f, n) for lease in st['in_use'].values() for f, n in lease.get('files', [])}
    for folder in CONSUMABLE:
        busy = os.path.join(chan_dir, folder, IN_USE_DIR)
        for name in _files(busy, BG_EXTS):
            if (folder, name) not in tracked:
                _move_unique(os.path.join(busy, name), os.path.join(chan_dir, folder))
    return changed


def _ensure_recovered(chan_dir, st):
    if chan_dir in _recovered or _keep_predicate is None:
        return False
    _recovered.add(chan_dir)
    return _recover_locked(chan_dir, st)


def _release_files(chan_dir, files):
    for folder, name in files:
        src = os.path.join(chan_dir, folder, IN_USE_DIR, name)
        if os.path.exists(src):
            _move_unique(src, os.path.join(chan_dir, folder))


# ── kocok kartu (mode CID & audio) ─────────────────────────
def _draw(st, key, pool, count, unique=True):
    """Ambil `count` kartu dari tumpukan acak; tumpukan dikocok ulang setelah habis."""
    if not pool:
        return []
    pool_set = set(pool)
    bag = [x for x in st['bags'].get(key, []) if x in pool_set]
    last = st.setdefault('last', {}).get(key)
    picked = []
    target = min(count, len(pool)) if unique else count
    guard = 0
    while len(picked) < target and guard < target * 4 + 8:
        guard += 1
        if not bag:
            bag = list(pool)
            random.shuffle(bag)
            # hindari kartu yang baru saja keluar (juga dari pengambilan sebelumnya) langsung muncul lagi
            prev = picked[-1] if picked else last
            if prev is not None and len(bag) > 1 and bag[-1] == prev:
                bag[0], bag[-1] = bag[-1], bag[0]
        card = bag.pop()
        if unique and card in picked:
            continue
        picked.append(card)
    st['bags'][key] = bag
    if picked:
        st['last'][key] = picked[-1]
    return picked


# ── API utama ──────────────────────────────────────────────
def pick(chan_dir, folder, count, exts, mode, owner=None, unique=False):
    """Pilih footage. Mengembalikan daftar path absolut.

    AdSense: file dipindah ke _dipakai atas nama `owner`; StockError bila stok kurang.
    CID: kocok kartu dari stok + _terpakai, file tidak dipindah.
    """
    count = max(1, int(count))
    with _lock, lock_for(_state_path(chan_dir)):
        st = _load(chan_dir)
        changed = _ensure_recovered(chan_dir, st)
        base = os.path.join(chan_dir, folder)
        if mode == MODE_ADSENSE and folder in CONSUMABLE:
            if not owner:
                raise ValueError('owner wajib untuk mode AdSense')
            stock = _files(base, exts)
            if len(stock) < count:
                if changed:
                    _save(chan_dir, st)
                raise StockError(folder, count, len(stock))
            chosen = random.sample(stock, count)
            lease = st['in_use'].setdefault(owner, {'boot': BOOT_ID, 'files': []})
            lease['boot'] = BOOT_ID
            paths = []
            for name in chosen:
                new_name = _move_unique(os.path.join(base, name), os.path.join(base, IN_USE_DIR))
                lease['files'].append([folder, new_name])
                paths.append(os.path.join(base, IN_USE_DIR, new_name))
            _save(chan_dir, st)
            return paths
        pool = _files(base, exts) + [f"{USED_DIR}/{n}" for n in _files(os.path.join(base, USED_DIR), exts)]
        cards = _draw(st, folder, pool, count, unique=unique)
        _save(chan_dir, st)
        return [os.path.join(base, c) for c in cards]


def pick_audio(chan_dir, folder, count, exts=AUDIO_EXTS):
    """Audio: kocok kartu, unik dalam satu pengambilan, tidak pernah habis."""
    with _lock, lock_for(_state_path(chan_dir)):
        st = _load(chan_dir)
        pool = _files(os.path.join(chan_dir, folder), exts)
        cards = _draw(st, folder, pool, max(1, int(count)), unique=True)
        _save(chan_dir, st)
        return [os.path.join(chan_dir, folder, c) for c in cards]


def commit(chan_dir, owner):
    """Task berhasil: footage yang dipesan dipindah ke _terpakai."""
    with _lock, lock_for(_state_path(chan_dir)):
        st = _load(chan_dir)
        lease = st['in_use'].pop(owner, None)
        if not lease:
            return 0
        n = 0
        for folder, name in lease.get('files', []):
            src = os.path.join(chan_dir, folder, IN_USE_DIR, name)
            if os.path.exists(src):
                _move_unique(src, os.path.join(chan_dir, folder, USED_DIR))
                n += 1
        _save(chan_dir, st)
        return n


def release(chan_dir, owner):
    """Task gagal/dibatalkan: footage yang dipesan kembali ke stok."""
    with _lock, lock_for(_state_path(chan_dir)):
        st = _load(chan_dir)
        lease = st['in_use'].pop(owner, None)
        if not lease:
            return 0
        _release_files(chan_dir, lease.get('files', []))
        _save(chan_dir, st)
        return len(lease.get('files', []))


def has_lease(chan_dir, owner):
    st = read_json(_state_path(chan_dir), {}) or {}
    return owner in (st.get('in_use') or {})


def stock_info(chan_dir, folder, exts=BG_EXTS):
    base = os.path.join(chan_dir, folder)
    used = []
    for name in _files(os.path.join(base, USED_DIR), exts):
        fp = os.path.join(base, USED_DIR, name)
        used.append({'name': name, 'size': f"{round(os.path.getsize(fp) / (1024 * 1024), 2)} MB"})
    return {
        'available': len(_files(base, exts)),
        'in_use': len(_files(os.path.join(base, IN_USE_DIR), exts)),
        'used': len(used),
        'used_files': used,
    }


def restore_used(chan_dir, folder, name=None):
    """Kembalikan file terpakai ke stok (satu file atau semua)."""
    if folder not in CONSUMABLE or (name is not None and not _valid_name(name)):
        raise ValueError('Permintaan tidak valid.')
    with _lock:
        src_dir = os.path.join(chan_dir, folder, USED_DIR)
        names = [name] if name else _files(src_dir, BG_EXTS)
        n = 0
        for nm in names:
            src = os.path.join(src_dir, nm)
            if os.path.isfile(src):
                _move_unique(src, os.path.join(chan_dir, folder))
                n += 1
        return n


def delete_used(chan_dir, folder, name=None):
    """Hapus permanen file terpakai (satu file atau semua)."""
    if folder not in CONSUMABLE or (name is not None and not _valid_name(name)):
        raise ValueError('Permintaan tidak valid.')
    with _lock:
        src_dir = os.path.join(chan_dir, folder, USED_DIR)
        names = [name] if name else _files(src_dir, BG_EXTS)
        n = 0
        for nm in names:
            fp = os.path.join(src_dir, nm)
            if os.path.isfile(fp):
                os.remove(fp)
                n += 1
        return n
