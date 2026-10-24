"""
旧共享路径停用测试(M1,契约 §7/§4)

验证每条已停用路径返回 410 与统一错误信封 GONE_DECOMMISSIONED,
而不是静默 404 或继续产生写入。
"""
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)

GONE_ROUTES = [
    ("PUT", "/api/share/documents/any-doc/sync-enabled?enabled=true"),
    ("POST", "/api/share/documents/any-doc/quick-share"),
    ("POST", "/api/sync/sync-token"),
    ("POST", "/api/sync/push-doc"),
    ("POST", "/api/sync/pull-doc"),
    ("POST", "/api/sync/check-doc"),
    ("GET", "/api/sync/cloud-docs/any-doc/versions/any-version/download-url"),
]


def test_decommissioned_routes_return_410():
    for method, path in GONE_ROUTES:
        resp = client.request(method, path)
        assert resp.status_code == 410, f"{method} {path} 返回 {resp.status_code}"
        body = resp.json()
        assert body["error"]["code"] == "GONE_DECOMMISSIONED", f"{method} {path} 信封不符: {body}"
        assert "M4" in body["error"]["message"] or "M2" in body["error"]["message"]


def test_decommissioned_routes_do_not_write(tmp_path):
    """410 响应不得伴随任何写入:调用后当前库中不应出现新文档"""
    import backend.app.database as database
    database.init_db()  # 确保当前隔离库已建表
    db = database.SessionLocal()
    try:
        from backend.app.models import Document
        before = db.query(Document).count()
    finally:
        db.close()

    for method, path in GONE_ROUTES:
        client.request(method, path)

    db = database.SessionLocal()
    try:
        from backend.app.models import Document
        after = db.query(Document).count()
    finally:
        db.close()
    assert before == after
