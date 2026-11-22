"""Impor aset dari link Google Drive publik ("siapa saja yang memiliki link").

Alur: baca isi link (file atau folder) -> pengguna memilih -> diunduh di background satu per satu.
File diunduh ke nama sementara tersembunyi (.nama.drive-part) lalu diganti nama setelah selesai,
sehingga file setengah jadi tidak pernah terbaca oleh render.
"""
import os
import re
import shutil
import threading
import time
import uuid

import gdown

MIN_FREE_BYTES = 2 * 1024 ** 3  # sisakan minimal 2 GB di disk
MAX_JOBS_KEPT = 20
PART_SUFFIX = '.drive-part'

_FOLDER_RE = re.compile(r'/folders/([A-Za-z0-9_-]{10,})')
_FILE_RES = (re.compile(r'/file/d/([A-Za-z0-9_-]{10,})'), re.compile(r'[?&]id=([A-Za-z0-9_-]{10,})'))
_ID_RE = re.compile(r'^[A-Za-z0-9_-]{10,}$')

_lock = threading.Lock()
_jobs = {}


class DriveError(Exception):
    pass


def parse_url(url):
    """('folder'|'file', id) dari link Google Drive."""
    url = (url or '').strip()
    if 'drive.google.com' not in url and 'docs.google.com' not in url:
        raise DriveError('Bukan link Google Drive.')
    m = _FOLDER_RE.search(url)
    if m:
        return 'folder', m.group(1)
    for rx in _FILE_RES:
        m = rx.search(url)
        if m:
            return 'file', m.group(1)
    raise DriveError('Link Google Drive tidak dikenali. Pakai link file atau folder dari tombol "Bagikan".')


def _friendly(err):
    text = str(err)
    if 'Only the owner and editors' in text:
        return 'Pemilik file mematikan izin download.'
    if 'Too many users' in text or 'quota' in text.lower():
        return 'Kuota download Google untuk file ini habis, coba lagi nanti.'
    if 'Cannot retrieve the public link' in text or 'permission' in text.lower() or 'Failed to retrieve' in text:
        return 'File tidak bisa diakses. Pastikan aksesnya "Siapa saja yang memiliki link".'
    return text.strip().splitlines()[0][:200] if text.strip() else 'Gagal mengunduh.'


def list_link(url):
    """Isi link: [{'id', 'name', 'path'}]. Folder dibaca beserta subfoldernya."""
    kind, drive_id = parse_url(url)
    try:
        if kind == 'folder':
            found = gdown.download_folder(id=drive_id, skip_download=True, quiet=True,
                                          use_cookies=False, timeout=30, retries=1) or []
            items = [{'id': it.id, 'name': os.path.basename(it.path), 'path': it.path} for it in found]
        else:
            info = gdown.download(id=drive_id, skip_download=True, quiet=True, use_cookies=False, timeout=30)
            name = os.path.basename(info.path)
            items = [{'id': drive_id, 'name': name, 'path': name}]
    except DriveError:
        raise
    except Exception as e:  # noqa: BLE001 — pesan dirapikan untuk pengguna
        raise DriveError(_friendly(e))
    return kind, items


def _safe_name(name):
    name = os.path.basename(str(name or '')).strip().replace('\x00', '')
    if not name or name in ('.', '..') or name.startswith('.'):
        return ''
    return name


def start_job(yt_id, folder_path, label, items, allowed_exts):
    """Mulai impor di background. items: [{'id', 'name'}]. Mengembalikan job_id."""
    entries = []
    for it in items:
        name = _safe_name(it.get('name'))
        fid = str(it.get('id') or '')
        if not name or not _ID_RE.match(fid):
            raise DriveError(f"Item tidak valid: {it.get('name')}")
        if not name.lower().endswith(tuple(allowed_exts)):
            raise DriveError(f"Jenis file tidak sesuai folder ini: {name}")
        entries.append({'id': fid, 'name': name, 'status': 'menunggu', 'bytes': 0, 'total': None, 'error': ''})
    if not entries:
        raise DriveError('Pilih minimal 1 file.')
    job_id = uuid.uuid4().hex[:12]
    job = {'id': job_id, 'yt_id': yt_id, 'label': label, 'created': time.time(), 'status': 'berjalan',
           'items': entries, '_cancel': threading.Event(), '_folder': folder_path}
    with _lock:
        _jobs[job_id] = job
        for old in sorted(_jobs.values(), key=lambda j: j['created'])[:-MAX_JOBS_KEPT]:
            if old['status'] != 'berjalan':
                _jobs.pop(old['id'], None)
    threading.Thread(target=_run, args=(job,), daemon=True).start()
    return job_id


def _run(job):
    folder, cancel = job['_folder'], job['_cancel']
    os.makedirs(folder, exist_ok=True)
    for entry in job['items']:
        if cancel.is_set():
            entry['status'] = 'dibatalkan'
            continue
        final = os.path.join(folder, entry['name'])
        if os.path.exists(final):
            entry['status'] = 'dilewati'
            entry['error'] = 'File dengan nama sama sudah ada.'
            continue
        if shutil.disk_usage(folder).free < MIN_FREE_BYTES:
            entry['status'] = 'gagal'
            entry['error'] = 'Ruang disk VPS tinggal kurang dari 2 GB.'
            continue
        part = os.path.join(folder, '.' + entry['name'] + PART_SUFFIX)
        entry['status'] = 'mengunduh'

        def progress(done, total, e=entry):
            e['bytes'], e['total'] = done, total

        try:
            gdown.download(id=entry['id'], output=part, quiet=True, use_cookies=False, progress=progress,
                           timeout=60, retries=2, resume=True, cancel=cancel)
            if cancel.is_set() and not os.path.exists(part):
                raise InterruptedError()
            os.replace(part, final)
            entry['status'] = 'selesai'
        except Exception as e:  # noqa: BLE001
            entry['status'] = 'dibatalkan' if cancel.is_set() else 'gagal'
            entry['error'] = '' if cancel.is_set() else _friendly(e)
            try:
                os.remove(part)
            except OSError:
                pass
    job['status'] = 'dibatalkan' if cancel.is_set() else 'selesai'


def jobs_for(yt_id):
    with _lock:
        jobs = [j for j in _jobs.values() if j['yt_id'] == yt_id]
    jobs.sort(key=lambda j: j['created'], reverse=True)
    return [{k: v for k, v in j.items() if not k.startswith('_')} for j in jobs]


def cancel_job(job_id):
    job = _jobs.get(job_id)
    if not job:
        return False
    job['_cancel'].set()
    return True
