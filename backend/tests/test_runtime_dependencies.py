"""生产服务不应加载已退役的模型 SDK；隔离进程不会使用用户配置。"""
import os
from pathlib import Path
import subprocess
import sys


def test_host_starts_without_retired_ai_sdk(tmp_path):
    source = """
import importlib.abc
import sys
class DenyRetiredSDK(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'pydantic_ai', 'pydantic_graph', 'openai', 'genai_prices'}:
            raise ImportError('retired SDK required by production: ' + fullname)
sys.meta_path.insert(0, DenyRetiredSDK())
from main import app
paths = app.openapi()['paths']
assert '/api/ai/preorganize/config' in paths
assert '/api/ai/partner/sessions' in paths
"""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "TEAMHUB_SANDBOX": "1",
        "LIBRARY_PATH": str(tmp_path / "library"),
        "USER_DATA_DIR": str(tmp_path / "profile"),
        "ELECTRON_APP": "true",
        "TEAMHUB_AI_CONFIG_DIR": str(tmp_path / "ai"),
    }
    result = subprocess.run(
        [sys.executable, "-c", source],
        cwd=Path(__file__).resolve().parents[2],
        env=env, text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
