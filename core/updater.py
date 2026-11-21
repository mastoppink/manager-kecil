"""Pembaruan aplikasi dari GitHub: cek commit baru, tarik (fast-forward saja), pasang library, restart."""
import os
import subprocess
import sys
import threading
import time

BRANCH = 'main'
_lock = threading.Lock()
_state = {'running': False, 'phase': 'idle', 'log': [], 'error': None, 'restarting': False}


def _git(repo, *args, timeout=60, strip=True):
    res = subprocess.run(['git', '-C', repo, *args], capture_output=True, text=True, timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError((res.stderr or res.stdout).strip() or f'git {args[0]} gagal')
    return res.stdout.strip() if strip else res.stdout


def under_systemd():
    """systemd mengisi INVOCATION_ID; dengan Restart=always, proses yang keluar akan dinyalakan lagi."""
    return bool(os.environ.get('INVOCATION_ID'))


def check(repo, fetch=True):
    """Bandingkan versi lokal dengan origin/main."""
    if not os.path.isdir(os.path.join(repo, '.git')):
        return {'ok': False, 'message': 'Folder aplikasi bukan repo Git. Pasang ulang memakai install.sh.'}
    try:
        if fetch:
            _git(repo, 'fetch', '--quiet', 'origin', BRANCH, timeout=60)
        head = _git(repo, 'rev-parse', 'HEAD')
        remote = _git(repo, 'rev-parse', f'origin/{BRANCH}')
        behind = int(_git(repo, 'rev-list', '--count', f'HEAD..origin/{BRANCH}') or 0)
        ahead = int(_git(repo, 'rev-list', '--count', f'origin/{BRANCH}..HEAD') or 0)
        log = _git(repo, 'log', f'HEAD..origin/{BRANCH}', '--format=%h\x1f%s\x1f%cr', '-n', '30')
        changes = [dict(zip(('hash', 'subject', 'when'), line.split('\x1f'))) for line in log.splitlines() if line]
        current = _git(repo, 'log', '-1', '--format=%h\x1f%s\x1f%cr').split('\x1f')
        dirty = [l[3:] for l in _git(repo, 'status', '--porcelain', '--untracked-files=no', strip=False).splitlines() if l.strip()]
    except (RuntimeError, subprocess.TimeoutExpired, ValueError) as e:
        return {'ok': False, 'message': f'Gagal mengecek pembaruan: {e}'}
    return {
        'ok': True,
        'current': {'hash': current[0], 'subject': current[1] if len(current) > 1 else '', 'when': current[2] if len(current) > 2 else ''},
        'head': head, 'remote': remote, 'behind': behind, 'ahead': ahead,
        'changes': changes, 'dirty': dirty,
        'can_update': behind > 0 and ahead == 0 and not dirty,
        'auto_restart': under_systemd(),
    }


def state():
    return {k: (list(v) if isinstance(v, list) else v) for k, v in _state.items()}


def _log(msg):
    _state['log'].append(msg)


def _run(repo):
    try:
        _state['phase'] = 'pull'
        old = _git(repo, 'rev-parse', 'HEAD')
        _log('Mengambil kode terbaru…')
        _git(repo, 'merge', '--ff-only', f'origin/{BRANCH}', timeout=120)
        new = _git(repo, 'rev-parse', 'HEAD')
        _log(f'Kode diperbarui {old[:7]} → {new[:7]}.')

        changed = _git(repo, 'diff', '--name-only', old, new).splitlines()
        if 'requirements.txt' in changed:
            _state['phase'] = 'pip'
            _log('requirements.txt berubah, memasang library…')
            res = subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '-r', os.path.join(repo, 'requirements.txt')],
                                 capture_output=True, text=True, timeout=1800)
            if res.returncode != 0:
                tail = (res.stderr or res.stdout).strip().splitlines()[-5:]
                raise RuntimeError('pip install gagal: ' + ' | '.join(tail))
            _log('Library terpasang.')

        if under_systemd():
            _state['phase'] = 'restart'
            _state['restarting'] = True
            _log('Me-restart aplikasi…')
            threading.Thread(target=_exit_soon, daemon=True).start()
        else:
            _state['phase'] = 'done'
            _log('Selesai. Jalankan ulang aplikasi secara manual agar perubahan aktif.')
    except Exception as e:  # noqa: BLE001 — semua error dilaporkan ke UI
        _state['phase'] = 'error'
        _state['error'] = str(e)
        _log(f'Gagal: {e}')
    finally:
        _state['running'] = False


def _exit_soon():
    time.sleep(1.5)  # beri waktu respons terakhir terkirim ke browser
    os._exit(0)      # systemd (Restart=always) menyalakan ulang service


def start(repo):
    """Mulai pembaruan di background. Mengembalikan (berhasil, pesan)."""
    with _lock:
        if _state['running']:
            return False, 'Pembaruan sedang berjalan.'
        info = check(repo, fetch=True)
        if not info.get('ok'):
            return False, info.get('message', 'Gagal mengecek pembaruan.')
        if info['dirty']:
            return False, 'Ada file kode yang diubah langsung di server: ' + ', '.join(info['dirty'][:5])
        if info['ahead']:
            return False, 'Versi di server punya commit yang tidak ada di GitHub. Pembaruan otomatis dibatalkan.'
        if not info['behind']:
            return False, 'Aplikasi sudah versi terbaru.'
        _state.update(running=True, phase='start', log=[], error=None, restarting=False)
    threading.Thread(target=_run, args=(repo,), daemon=True).start()
    return True, 'Pembaruan dimulai.'
