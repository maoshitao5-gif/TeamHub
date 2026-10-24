"""
共享端权限骨架测试(M1,契约 §5.2/§4)

- 跨空间读取版本列表被拒(修复前:list_versions 只按 doc_id 查,可跨空间泄漏)
- 跨空间确认上传被拒(修复前:confirm_upload 无任何空间校验)
- 对无权限者返回 404(不区分不存在与不可见,防探测)
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import User, Team, TeamMember, Workspace, CloudDocument, CloudVersion
from app.core.deps import get_current_user
from main import app

client = TestClient(app)


def _uid():
    return str(uuid.uuid4())


@pytest.fixture
def two_spaces():
    """用户 u1 属于 team1/ws1;另有 team2/ws2 及其文档(u1 无权访问)"""
    db = SessionLocal()
    try:
        u1 = User(id=_uid(), email=f"{_uid()[:8]}@t.local", hashed_password="x", display_name="U1")
        t1 = Team(id=_uid(), name="团队1", slug=f"t1-{_uid()[:8]}")
        t2 = Team(id=_uid(), name="团队2", slug=f"t2-{_uid()[:8]}")
        db.add_all([u1, t1, t2])
        db.flush()
        db.add(TeamMember(team_id=t1.id, user_id=u1.id, role="member"))
        ws1 = Workspace(id=_uid(), team_id=t1.id, name="空间1")
        ws2 = Workspace(id=_uid(), team_id=t2.id, name="空间2")
        db.add_all([ws1, ws2])
        db.flush()
        doc2 = CloudDocument(id=_uid(), workspace_id=ws2.id, name="机密资料")
        db.add(doc2)
        db.flush()
        ver2 = CloudVersion(id=_uid(), document_id=doc2.id, version_number=1,
                            original_filename="secret.txt", sha256_hash="ff" * 32)
        db.add(ver2)
        db.commit()
        yield {"u1": u1, "ws1": ws1, "ws2": ws2, "doc2": doc2, "ver2": ver2}
    finally:
        db.close()
        app.dependency_overrides.clear()


def _as_user(user):
    app.dependency_overrides[get_current_user] = lambda: user


def test_cross_workspace_version_list_denied(two_spaces):
    """用自己空间的 ws_id + 其他空间的 doc_id 读版本:必须 404"""
    _as_user(two_spaces["u1"])
    resp = client.get(
        f"/api/workspaces/{two_spaces['ws1'].id}/documents/{two_spaces['doc2'].id}/versions"
    )
    assert resp.status_code == 404


def test_cross_workspace_confirm_upload_denied(two_spaces):
    """M4:confirm-upload 旧旁路已停用,任何调用一律 410 且不产生写入"""
    _as_user(two_spaces["u1"])
    resp = client.post("/api/storage/confirm-upload", json={
        "version_id": two_spaces["ver2"].id,
        "sha256_hash": "00" * 32,
        "oss_key": "attacker/key",
        "file_size": 1,
    })
    assert resp.status_code == 410
    assert resp.json()["error"]["code"] == "GONE_DECOMMISSIONED"

    db = SessionLocal()
    try:
        v = db.query(CloudVersion).filter(CloudVersion.id == two_spaces["ver2"].id).first()
        assert v.sha256_hash == "ff" * 32  # 未被改写
        assert v.oss_key is None
    finally:
        db.close()


def test_same_workspace_version_list_allowed(two_spaces):
    """对照:本空间文档的版本列表可正常访问"""
    db = SessionLocal()
    try:
        doc1 = CloudDocument(id=_uid(), workspace_id=two_spaces["ws1"].id, name="公开资料")
        db.add(doc1)
        db.flush()
        db.add(CloudVersion(id=_uid(), document_id=doc1.id, version_number=1,
                            original_filename="a.txt"))
        db.commit()
        doc1_id = doc1.id
    finally:
        db.close()

    _as_user(two_spaces["u1"])
    resp = client.get(f"/api/workspaces/{two_spaces['ws1'].id}/documents/{doc1_id}/versions")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
