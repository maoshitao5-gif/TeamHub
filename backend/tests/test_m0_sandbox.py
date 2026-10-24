"""M0 startup isolation: exercise actual lifespan in a separate process."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "legacy_config",
    [None, {"max_versions": 10, "max_version_age_days": 30}],
    ids=["empty", "retired-version-settings"],
)
def test_sandbox_ignores_dotenv_and_bootstrap(tmp_path, legacy_config):
    repo = Path(__file__).resolve().parents[2]
    library = tmp_path / 'sandbox-library'
    canary = tmp_path / 'outside-canary'
    canary.mkdir()
    (tmp_path / '.env').write_text(f'LIBRARY_PATH={canary}\n')
    (tmp_path / 'config.json').write_text(
        json.dumps({'library_path': str(canary)})
    )
    if legacy_config is not None:
        (library / ".teamhub").mkdir(parents=True)
        (library / ".teamhub/config.json").write_text(json.dumps(legacy_config))
    code = '''
from fastapi.testclient import TestClient
from main import app
from backend.app.config import settings
import os
with TestClient(app) as client:
    assert settings.library_path == os.environ['LIBRARY_PATH']
    r = client.get('/api/health')
    assert r.status_code == 200
    assert r.json()['library_path'] == os.environ['LIBRARY_PATH']
'''
    result = subprocess.run(
        [sys.executable, '-c', code], cwd=tmp_path, capture_output=True, text=True,
        env={'PATH': os.environ['PATH'], 'HOME': str(tmp_path),
             'PYTHONPATH': str(repo), 'TEAMHUB_SANDBOX': '1',
             'LIBRARY_PATH': str(library), 'TRASH_AUTO_CLEAN_DAYS': '0'}, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (library / '.teamhub/db.sqlite').is_file()
    assert list(canary.iterdir()) == []
    if legacy_config is not None:
        assert json.loads((library / ".teamhub/config.json").read_text()) == legacy_config
