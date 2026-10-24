"""Smoke-test the extracted cloud artifact without local backend or desktop files."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    temp = Path(tempfile.mkdtemp(prefix='teamhub-cloud-package-'))
    artifact = ROOT/'dist/teamhub-cloud.zip'
    with zipfile.ZipFile(artifact) as package:
        package.extractall(temp)
    manifest = json.loads((temp/'manifest.json').read_text())
    with zipfile.ZipFile(artifact) as package:
        assert set(package.namelist()) == set(manifest) | {'manifest.json'}
    for name, digest in manifest.items():
        assert hashlib.sha256((temp/name).read_bytes()).hexdigest() == digest
    assert not (temp/'backend').exists() and not (temp/'electron').exists()
    assert not list(temp.rglob('*.db')) and not (temp/'.env').exists()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port = sock.getsockname()[1]
    env = {**os.environ,'DATABASE_URL':f'sqlite:///{temp}/test.db','STORAGE_DIR':str(temp/'storage'),
           'CLOUD_WEB_DIST':str(temp/'web'),'TEAMHUB_SANDBOX':'1','JWT_SECRET_KEY':secrets.token_hex(40)}
    python = str(ROOT/'cloud_backend/venv/bin/python')
    initialized = subprocess.run([python,'manage.py','init'],cwd=temp,env=env,capture_output=True,text=True)
    assert initialized.returncode == 0, initialized.stderr
    identity = json.loads(initialized.stdout.strip().splitlines()[-1])['server_id']
    with (temp/'service.log').open('w') as log:
        process = subprocess.Popen([python,'-m','uvicorn','main:app','--host','127.0.0.1','--port',str(port)],cwd=temp,env=env,stdout=log,stderr=log)
        try:
            base=f'http://127.0.0.1:{port}'
            for _ in range(100):
                try:
                    with urllib.request.urlopen(base+'/api/health',timeout=1) as response:
                        if response.status==200:break
                except OSError:time.sleep(.1)
            else:raise RuntimeError((temp/'service.log').read_text())
            for page in ('cloud.html','admin.html'):
                with urllib.request.urlopen(base+'/'+page) as response:
                    assert response.status==200 and '<script' in response.read().decode()
                    assert "connect-src 'self'" in response.headers['Content-Security-Policy']
            with urllib.request.urlopen(base+'/api/server-info') as response:
                assert json.load(response)['server_id']==identity
            print(f'PASS independent cloud package: {temp}')
        finally:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()

if __name__=='__main__':main()
