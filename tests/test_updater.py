import os
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from core import updater


def git(cwd, *args):
    return subprocess.run(['git', '-C', cwd, *args], check=True, capture_output=True, text=True).stdout.strip()


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = self.tmp.name
        self.origin = os.path.join(root, 'origin.git')
        self.dev = os.path.join(root, 'dev')
        self.server = os.path.join(root, 'server')
        subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', self.origin], check=True)
        subprocess.run(['git', 'init', '-q', '-b', 'main', self.dev], check=True)
        for repo in (self.dev,):
            git(repo, 'config', 'user.email', 't@example.com')
            git(repo, 'config', 'user.name', 'T')
        self.commit('app.py', 'v1', 'versi 1')
        git(self.dev, 'remote', 'add', 'origin', self.origin)
        git(self.dev, 'push', '-q', 'origin', 'main')
        subprocess.run(['git', 'clone', '-q', self.origin, self.server], check=True)
        updater._state.update(running=False, phase='idle', log=[], error=None, restarting=False)

    def commit(self, name, content, msg):
        with open(os.path.join(self.dev, name), 'w') as f:
            f.write(content)
        git(self.dev, 'add', name)
        git(self.dev, 'commit', '-q', '-m', msg)

    def publish(self, name, content, msg):
        self.commit(name, content, msg)
        git(self.dev, 'push', '-q', 'origin', 'main')

    def wait_done(self):
        for _ in range(100):
            if not updater._state['running']:
                return
            time.sleep(0.05)
        self.fail('pembaruan tidak selesai')

    def test_up_to_date(self):
        info = updater.check(self.server)
        self.assertTrue(info['ok'])
        self.assertEqual(info['behind'], 0)
        self.assertFalse(info['can_update'])
        self.assertEqual(updater.start(self.server)[0], False)

    def test_detects_and_applies_update(self):
        self.publish('app.py', 'v2', 'versi 2')
        info = updater.check(self.server)
        self.assertEqual(info['behind'], 1)
        self.assertTrue(info['can_update'])
        self.assertEqual(info['changes'][0]['subject'], 'versi 2')

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('INVOCATION_ID', None)
            ok, _ = updater.start(self.server)
            self.assertTrue(ok)
            self.wait_done()
        self.assertEqual(updater._state['phase'], 'done')
        with open(os.path.join(self.server, 'app.py')) as f:
            self.assertEqual(f.read(), 'v2')
        self.assertEqual(updater.check(self.server)['behind'], 0)

    def test_requirements_change_runs_pip(self):
        self.publish('requirements.txt', 'flask\n', 'tambah library')
        calls = []

        class Done:
            returncode, stdout, stderr = 0, '', ''

        real_run = subprocess.run

        def fake_run(cmd, *a, **kw):
            if cmd[:3] == [updater.sys.executable, '-m', 'pip']:
                calls.append(cmd)
                return Done()
            return real_run(cmd, *a, **kw)

        os.environ.pop('INVOCATION_ID', None)
        with patch.object(updater.subprocess, 'run', side_effect=fake_run):
            self.assertTrue(updater.start(self.server)[0])
            self.wait_done()
        self.assertEqual(updater._state['phase'], 'done')
        self.assertEqual(len(calls), 1)

    def test_blocks_when_server_code_modified(self):
        self.publish('app.py', 'v2', 'versi 2')
        with open(os.path.join(self.server, 'app.py'), 'w') as f:
            f.write('diedit di server')
        info = updater.check(self.server)
        self.assertEqual(info['dirty'], ['app.py'])
        self.assertFalse(info['can_update'])
        ok, msg = updater.start(self.server)
        self.assertFalse(ok)
        self.assertIn('app.py', msg)

    def test_untracked_data_files_do_not_block(self):
        self.publish('app.py', 'v2', 'versi 2')
        with open(os.path.join(self.server, 'channels_db.json'), 'w') as f:
            f.write('[]')
        self.assertTrue(updater.check(self.server)['can_update'])

    def test_restart_under_systemd(self):
        self.publish('app.py', 'v2', 'versi 2')
        with patch.dict(os.environ, {'INVOCATION_ID': 'x'}), patch.object(updater, '_exit_soon') as exit_soon:
            self.assertTrue(updater.start(self.server)[0])
            self.wait_done()
            time.sleep(0.1)
        self.assertEqual(updater._state['phase'], 'restart')
        self.assertTrue(updater._state['restarting'])
        exit_soon.assert_called_once()

    def test_not_a_git_repo(self):
        info = updater.check(self.tmp.name)
        self.assertFalse(info['ok'])


if __name__ == '__main__':
    unittest.main()
