"""
共享端旧路径停用测试(M1,契约 §7/§4)

每条已停用路径返回 410 与统一错误信封 GONE_DECOMMISSIONED;
保留的认证与成员管理路径仍可正常响应(不因收敛误伤)。
"""
import uuid

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)

GONE_ROUTES = [
    ("POST", "/api/sharing/presign-upload"),
    ("POST", "/api/sharing/publish"),
    ("GET", "/api/sharing/inbox"),
    ("POST", "/api/workspaces/any-ws/sync/push"),
    ("GET", "/api/workspaces/any-ws/sync/pull"),
    ("POST", "/api/devices/register"),
    ("GET", "/api/devices"),
    ("POST", "/api/backup/presign-upload"),
    ("GET", "/api/backup/list"),
    ("POST", "/api/auth/register"),
    ("GET", "/api/teams/discover"),
    ("POST", "/api/teams/join-requests"),
    ("GET", "/api/teams/join-requests/my"),
    ("GET", "/api/teams/any-team/join-requests"),
    ("POST", "/api/teams/any-team/join-requests/any-req/review"),
    # M1-R02:旧文档写入入口停用
    ("POST", "/api/workspaces/any-ws/documents/any-doc/push"),
    ("POST", "/api/workspaces/any-ws/documents/any-doc/versions/snapshot"),
    ("POST", "/api/workspaces/any-ws/documents"),
    ("PUT", "/api/workspaces/any-ws/documents/any-doc"),
    ("DELETE", "/api/workspaces/any-ws/documents/any-doc"),
    ("POST", "/api/workspaces/any-ws/tags"),
    ("DELETE", "/api/workspaces/any-ws/tags/any-tag"),
    # M4:storage 预签名旧旁路停用
    ("POST", "/api/storage/presign-upload"),
    ("POST", "/api/storage/presign-download"),
    ("POST", "/api/storage/confirm-upload"),
]


def test_decommissioned_routes_return_410():
    for method, path in GONE_ROUTES:
        resp = client.request(method, path)
        assert resp.status_code == 410, f"{method} {path} 返回 {resp.status_code}"
        body = resp.json()
        assert body["error"]["code"] == "GONE_DECOMMISSIONED", f"{method} {path} 信封不符: {body}"


def test_kept_routes_still_mounted():
    """login/health 等保留路由不应返回 410"""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    # login 缺少凭据返回 401/422,但绝不是 410
    resp = client.post("/api/auth/login", json={"email": "a@b.c", "password": "x"})
    assert resp.status_code in (401, 422)


def test_document_write_routes_do_not_write():
    """M1-R02:旧文档写入入口返回 410 且不产生任何组织数据"""
    from app.database import SessionLocal
    from app.models import CloudDocument, CloudVersion, CloudTag, CloudChangeLog

    def _counts():
        db = SessionLocal()
        try:
            return {
                "docs": db.query(CloudDocument).count(),
                "vers": db.query(CloudVersion).count(),
                "tags": db.query(CloudTag).count(),
                "logs": db.query(CloudChangeLog).count(),
            }
        finally:
            db.close()

    write_routes = [
        ("POST", "/api/workspaces/any-ws/documents/any-doc/push"),
        ("POST", "/api/workspaces/any-ws/documents/any-doc/versions/snapshot"),
        ("POST", "/api/workspaces/any-ws/documents"),
        ("PUT", "/api/workspaces/any-ws/documents/any-doc"),
        ("DELETE", "/api/workspaces/any-ws/documents/any-doc"),
        ("POST", "/api/workspaces/any-ws/tags"),
        ("DELETE", "/api/workspaces/any-ws/tags/any-tag"),
    ]
    before = _counts()
    for method, path in write_routes:
        resp = client.request(method, path)
        assert resp.status_code == 410, f"{method} {path} 返回 {resp.status_code}"
    after = _counts()
    assert before == after, f"写入入口产生了数据变更: before={before} after={after}"
