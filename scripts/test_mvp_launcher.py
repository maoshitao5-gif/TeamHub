"""M0 launcher regression checks; disposable services, directories and processes only.
Run: python3 -m unittest discover -s scripts -p 'test_mvp_launcher.py' -v
Needs permission to inspect processes and bind isolated loopback ports (only the temporary launcher copy is adapted).
"""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest

LAUNCHER = Path(__file__).with_name('mvp-dev.sh')
FAKE_SERVER = '''
import json, os, sys, time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
root = Path(sys.argv[sys.argv.index('--app-dir') + 1])
mode = (root / 'mode').read_text() if (root / 'mode').exists() else 'healthy'
Path('observed-env.json').write_text(json.dumps(dict(os.environ)))
if mode == 'exit': sys.exit(42)
if mode == 'hang': time.sleep(120)
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'{"status":"ok"}')
    def log_message(self, *args): pass
HTTPServer(('127.0.0.1', int(sys.argv[sys.argv.index('--port') + 1])), Handler).serve_forever()
'''


class LauncherTests(unittest.TestCase):
    def setUp(self):
        # 为脚本临时副本分配独立端口，不因用户 demo 正在运行而跳过验收。
        with socket.socket() as local, socket.socket() as cloud, socket.socket() as frontend:
            local.bind(('127.0.0.1', 0)); cloud.bind(('127.0.0.1', 0)); frontend.bind(('127.0.0.1', 0))
            self.frontend_port = frontend.getsockname()[1]
            self.local_port, self.cloud_port = local.getsockname()[1], cloud.getsockname()[1]
        self.tmp = tempfile.TemporaryDirectory(prefix='teamhub-m0-中文 路径-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'scripts').mkdir()
        source = LAUNCHER.read_text().replace('8001', str(self.local_port)).replace('9000', str(self.cloud_port)).replace('5173', str(self.frontend_port))
        (self.root / 'scripts/mvp-dev.sh').write_text(source)
        secret_helper = LAUNCHER.with_name('mvp-cloud-secret.py')
        if secret_helper.exists():
            shutil.copy2(secret_helper, self.root / 'scripts/mvp-cloud-secret.py')
        for prefix in ('venv', 'cloud_backend/venv'):
            executable = self.root / prefix / 'bin/python'
            executable.parent.mkdir(parents=True)
            executable.symlink_to(sys.executable)
        for relative in ('.mvp-sandbox/personal-a', '.mvp-sandbox/shared-server'):
            directory = self.root / relative
            directory.mkdir(parents=True)
            (directory / 'uvicorn.py').write_text(FAKE_SERVER)
        fake_node = self.root / 'bin/node'
        fake_node.parent.mkdir()
        fake_node.symlink_to(sys.executable)
        vite = self.root / 'frontend/node_modules/vite/bin/vite.js'
        vite.parent.mkdir(parents=True)
        vite.write_text(FAKE_SERVER.replace("root = Path(sys.argv[sys.argv.index('--app-dir') + 1])", "root = Path.cwd()"))
        self.addCleanup(lambda: self.run_launcher('stop'))

    def run_launcher(self, *args, env=None):
        return subprocess.run(['bash', str(self.root / 'scripts/mvp-dev.sh'), *args],
                              capture_output=True, text=True, timeout=55,
                              env=dict(env or os.environ, TEAMHUB_NO_OPEN='1',
                                       PATH=str(self.root / 'bin') + os.pathsep + os.environ['PATH']))

    def assert_stopped(self):
        self.assertFalse(list((self.root / '.mvp-sandbox/pids').glob('*.pid')))
        for port in (self.local_port, self.cloud_port, self.frontend_port):
            with socket.socket() as sock:
                self.assertNotEqual(sock.connect_ex(('127.0.0.1', port)), 0)

    def test_success_isolated_environment_and_repeat_start(self):
        env = dict(os.environ, LIBRARY_PATH='/outside-canary', OSS_ENDPOINT='https://invalid.test')
        result = self.run_launcher('start', 'a', env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        observed = json.loads((self.root / '.mvp-sandbox/personal-a/observed-env.json').read_text())
        self.assertEqual(observed['LIBRARY_PATH'], str(self.root / '.mvp-sandbox/personal-a/library'))
        self.assertNotIn('OSS_ENDPOINT', observed)
        self.assertEqual(self.run_launcher('status').returncode, 0)
        self.assertNotEqual(self.run_launcher('start').returncode, 0)
        self.assertEqual(self.run_launcher('status').returncode, 0)
        self.assertEqual(self.run_launcher('stop').returncode, 0)
        self.assert_stopped()
        self.assertNotEqual(self.run_launcher('status').returncode, 0)

    def test_cloud_signing_secret_is_private_persistent_and_not_in_output(self):
        first = self.run_launcher('cloud')
        self.assertEqual(first.returncode, 0, first.stderr)
        env_path = self.root / '.mvp-sandbox/shared-server/observed-env.json'
        secret = json.loads(env_path.read_text()).get('JWT_SECRET_KEY')
        self.assertIsInstance(secret, str)
        self.assertGreaterEqual(len(secret), 64)
        self.assertNotIn(secret, first.stdout + first.stderr)
        key_path = self.root / '.mvp-sandbox/shared-server/.jwt-secret'
        self.assertEqual(key_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.run_launcher('stop').returncode, 0)
        second = self.run_launcher('cloud')
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(json.loads(env_path.read_text())['JWT_SECRET_KEY'], secret)

    def test_immediate_exit_cleans_other_service(self):
        (self.root / 'cloud_backend/mode').write_text('exit')
        result = self.run_launcher('start')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('已就绪', result.stdout)
        self.assert_stopped()

    def test_unbound_startup_timeout_is_cleaned(self):
        (self.root / 'cloud_backend/mode').write_text('hang')
        result = self.run_launcher('start')
        self.assertNotEqual(result.returncode, 0)
        self.assert_stopped()
        # The hanging process never listened; verify no matching process remains.
        commands = subprocess.check_output(['ps', '-axo', 'command='], text=True)
        self.assertNotIn(f'-m uvicorn main:app --app-dir {self.root}/cloud_backend', commands)

    def test_unrelated_python_with_correct_start_time_survives(self):
        process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
        def cleanup():
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
        self.addCleanup(cleanup)
        stamp = subprocess.check_output(['ps', '-p', str(process.pid), '-o', 'lstart='],
                                        text=True, env=dict(os.environ, LC_ALL='C')).strip()
        pid_dir = self.root / '.mvp-sandbox/pids'
        pid_dir.mkdir()
        (pid_dir / 'local-backend.pid').write_text(f'{process.pid}|{stamp}|{self.local_port}\n')
        self.assertEqual(self.run_launcher('stop').returncode, 0)
        self.assertIsNone(process.poll())

    def test_occupied_port_untouched(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(('127.0.0.1', self.local_port)); listener.listen()
            self.assertNotEqual(self.run_launcher('start').returncode, 0)
            self.assertEqual(listener.getsockname()[1], self.local_port)

    def test_one_click_repeat_and_stop(self):
        result = self.run_launcher('start-all')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        records = {p.name: p.read_text() for p in (self.root / '.mvp-sandbox/pids').glob('*.pid')}
        self.assertEqual(len(records), 3)
        self.assertEqual(self.run_launcher('start-all').returncode, 0)
        self.assertEqual(records, {p.name: p.read_text() for p in (self.root / '.mvp-sandbox/pids').glob('*.pid')})
        self.assertEqual(self.run_launcher('status').returncode, 0)
        self.assertEqual(self.run_launcher('stop').returncode, 0)
        self.assert_stopped()

    def test_frontend_failure_rolls_back_new_backends(self):
        (self.root / 'frontend/mode').write_text('exit')
        self.assertNotEqual(self.run_launcher('start-all').returncode, 0)
        self.assert_stopped()

    def test_frontend_failure_preserves_existing_backends(self):
        self.assertEqual(self.run_launcher('start').returncode, 0)
        (self.root / 'frontend/mode').write_text('exit')
        self.assertNotEqual(self.run_launcher('start-all').returncode, 0)
        self.assertEqual(self.run_launcher('status').returncode, 0)

    def test_one_click_occupied_frontend_untouched(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', self.frontend_port)); listener.listen()
            self.assertNotEqual(self.run_launcher('start-all').returncode, 0)
            self.assertEqual(listener.getsockname()[1], self.frontend_port)
            self.assertFalse(list((self.root / '.mvp-sandbox/pids').glob('*.pid')))

    def test_invalid_personal_name(self):
        self.assertEqual(self.run_launcher('start', '../../escape').returncode, 2)
        self.assertFalse((self.root / 'escape').exists())

    def test_local_does_not_require_cloud_environment(self):
        (self.root / 'cloud_backend/venv/bin/python').unlink()
        result = self.run_launcher('local')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        records = [p.name for p in (self.root / '.mvp-sandbox/pids').glob('*.pid')]
        self.assertEqual(records, ['local-backend.pid'])
        with socket.socket() as sock:
            self.assertEqual(sock.connect_ex(('127.0.0.1', self.local_port)), 0)

    def test_cloud_does_not_require_local_environment(self):
        (self.root / 'venv/bin/python').unlink()
        result = self.run_launcher('cloud')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        records = [p.name for p in (self.root / '.mvp-sandbox/pids').glob('*.pid')]
        self.assertEqual(records, ['cloud-backend.pid'])
        with socket.socket() as sock:
            self.assertEqual(sock.connect_ex(('127.0.0.1', self.cloud_port)), 0)


if __name__ == '__main__':
    unittest.main()
