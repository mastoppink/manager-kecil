"""Client secret per channel: setiap channel mengikuti client_secret yang diupload ke channel itu."""
import json
import os
import re

_CLIENT_ID_RE = re.compile(r'^[A-Za-z0-9._-]{8,200}$')


def parse_client_secret(raw):
    """Validasi isi client_secret.json. Mengembalikan (client_id, data)."""
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError('Format client_secret.json tidak valid.')
    conf = data.get('installed') or data.get('web')
    if not isinstance(conf, dict) or not conf.get('client_id') or not conf.get('client_secret'):
        raise ValueError('client_secret.json tidak berisi client_id/client_secret.')
    client_id = str(conf['client_id'])
    if not _CLIENT_ID_RE.match(client_id):
        raise ValueError('client_id tidak valid.')
    return client_id, data


def valid_client_id(client_id):
    return isinstance(client_id, str) and bool(_CLIENT_ID_RE.match(client_id))


def secret_path(secrets_dir, client_id):
    if not valid_client_id(client_id):
        raise ValueError('client_id tidak valid.')
    return os.path.join(secrets_dir, client_id + '.json')


def save_secret(secrets_dir, raw):
    client_id, data = parse_client_secret(raw)
    os.makedirs(secrets_dir, exist_ok=True)
    path = secret_path(secrets_dir, client_id)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f)
    os.replace(tmp, path)
    return client_id


def load_secret(secrets_dir, client_id):
    """Mengembalikan (client_id, client_secret) atau None kalau file tidak ada."""
    try:
        path = secret_path(secrets_dir, client_id)
        with open(path, 'r', encoding='utf-8') as f:
            _, data = parse_client_secret(f.read())
    except (OSError, ValueError):
        return None
    conf = data.get('installed') or data.get('web')
    return conf['client_id'], conf['client_secret']


def cred_client_id(cred_str):
    try:
        return json.loads(cred_str).get('client_id')
    except Exception:
        return None


def channel_creds(channel):
    """List (index, cred_str) milik channel yang sesuai client_id channel.

    Index mengikuti posisi asli di creds_list agar state upload lama tetap cocok.
    Channel lama tanpa field client_id tidak difilter.
    """
    creds_list = channel.get('creds_list') or [channel.get('creds_json')]
    bound = channel.get('client_id')
    out = []
    for idx, cred_str in enumerate(creds_list):
        if not cred_str:
            continue
        if bound and cred_client_id(cred_str) != bound:
            continue
        out.append((idx, cred_str))
    return out


def first_channel_cred(channel):
    found = channel_creds(channel)
    return found[0][1] if found else None


def needs_relink(channel):
    return bool(channel.get('client_id')) and not channel_creds(channel)


def migrate_channels(channels, secrets_dir, legacy_secret_file=None):
    """Isi client_id channel lama dari token pertamanya, dan pindahkan secret global ke secrets/.

    Mengembalikan True kalau ada perubahan data channel.
    """
    if legacy_secret_file and os.path.exists(legacy_secret_file):
        try:
            with open(legacy_secret_file, 'r', encoding='utf-8') as f:
                save_secret(secrets_dir, f.read())
        except (OSError, ValueError):
            pass
    changed = False
    for c in channels:
        if not isinstance(c, dict) or c.get('client_id'):
            continue
        creds_list = c.get('creds_list') or [c.get('creds_json')]
        for cred_str in creds_list:
            cid = cred_client_id(cred_str) if cred_str else None
            if valid_client_id(cid):
                c['client_id'] = cid
                changed = True
                break
    return changed
