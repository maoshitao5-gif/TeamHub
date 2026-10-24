"""可重复的检索基准只使用合成资料，独立子进程不继承用户资料库。"""
import json
from pathlib import Path
import subprocess
import sys


def test_synthetic_evaluation_outputs_reproducible_metrics(tmp_path):
    root = Path(__file__).resolve().parents[2]
    report = tmp_path / 'report.json'
    result = subprocess.run([sys.executable, str(root / 'scripts/evaluate_retrieval.py'),
                             '--output', str(report)], cwd=tmp_path,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(report.read_text())
    assert data['scope'] == 'synthetic_lexical_only'
    assert data['model_requests'] == 0
    assert data['query_count'] == len(data['cases']) == 12
    assert data['ranked']['recall_at_5'] == 1
    assert data['ranked']['recall_at_10'] == 1
    assert data['ranked']['negative_false_positives'] == 0
    assert data['legacy']['recall_at_5'] < data['ranked']['recall_at_5']
    assert not list(tmp_path.glob('**/db.sqlite')), '临时合成库应随评测结束清理'
