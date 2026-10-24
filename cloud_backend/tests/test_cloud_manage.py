"""Maintenance commands use explicit isolated targets; check never initializes a database."""
import json
import os
from pathlib import Path
import subprocess
import sys


def run_manage(target, *args):
    environment = {**os.environ, 'TEAMHUB_SANDBOX': '1', 'DATABASE_URL': f'sqlite:///{target}'}
    return subprocess.run([sys.executable, 'manage.py', *args], cwd=Path(__file__).resolve().parents[1],
                          env=environment, capture_output=True, text=True, timeout=15)


def test_manage_check_does_not_create_or_modify_database(tmp_path):
    target = tmp_path / 'database with spaces.db'
    assert run_manage(target, 'check').returncode != 0
    assert not target.exists()
    initialized = run_manage(target, 'init')
    assert initialized.returncode == 0, initialized.stderr
    before = target.read_bytes()
    checked = run_manage(target, 'check')
    assert checked.returncode == 0, checked.stderr
    assert target.read_bytes() == before
    assert json.loads(initialized.stdout) == json.loads(checked.stdout)


def test_clone_identity_requires_stopped_services_and_rotates_identity(tmp_path):
    target = tmp_path / 'clone.db'
    assert run_manage(target, 'clone-identity', '--services-stopped').returncode != 0
    assert not target.exists()
    original = json.loads(run_manage(target, 'init').stdout)
    assert run_manage(target, 'clone-identity').returncode != 0
    cloned = run_manage(target, 'clone-identity', '--services-stopped')
    assert cloned.returncode == 0, cloned.stderr
    identity = json.loads(cloned.stdout.splitlines()[0])
    assert identity['server_id'] != original['server_id']
