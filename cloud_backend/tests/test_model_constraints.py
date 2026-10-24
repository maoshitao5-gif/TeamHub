"""
共享端模型约束测试(M1,契约 §3)

- (document_id, version_number) 唯一
- publication_requests 的 (actor_id, workspace_id, request_id) 唯一
- 同一 content_hash 可出现在不同资料(查重不等于身份合并)
"""
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.models import (
    User, Team, TeamMember, Workspace, CloudDocument, CloudVersion,
    PublicationRequest,
)


def _uid():
    return str(uuid.uuid4())


@pytest.fixture
def workspace():
    db = SessionLocal()
    try:
        user = User(id=_uid(), email=f"{_uid()[:8]}@t.local",
                    hashed_password="x", display_name="测试")
        team = Team(id=_uid(), name="测试团队", slug=f"t-{_uid()[:8]}")
        db.add_all([user, team])
        db.flush()
        db.add(TeamMember(team_id=team.id, user_id=user.id, role="member"))
        ws = Workspace(id=_uid(), team_id=team.id, name="测试空间")
        db.add(ws)
        db.commit()
        yield {"user_id": user.id, "team_id": team.id, "ws_id": ws.id}
    finally:
        db.close()


def _add_doc(db, ws_id, user_id):
    doc = CloudDocument(id=_uid(), workspace_id=ws_id, name="资料X", created_by=user_id)
    db.add(doc)
    db.flush()
    return doc


def test_version_number_unique_per_document(workspace):
    db = SessionLocal()
    try:
        doc = _add_doc(db, workspace["ws_id"], workspace["user_id"])
        db.add(CloudVersion(id=_uid(), document_id=doc.id, version_number=1,
                            original_filename="a.txt", author_id=workspace["user_id"]))
        db.commit()
        db.add(CloudVersion(id=_uid(), document_id=doc.id, version_number=1,
                            original_filename="b.txt", author_id=workspace["user_id"]))
        with pytest.raises(IntegrityError):
            db.commit()
    finally:
        db.rollback()
        db.close()


def test_same_content_hash_allowed_across_documents(workspace):
    """相同内容哈希可存在于不同资料/版本:内容身份不代替资料身份"""
    db = SessionLocal()
    try:
        d1 = _add_doc(db, workspace["ws_id"], workspace["user_id"])
        d2 = _add_doc(db, workspace["ws_id"], workspace["user_id"])
        same_hash = "ab" * 32
        db.add(CloudVersion(id=_uid(), document_id=d1.id, version_number=1,
                            content_hash=same_hash, original_filename="a.txt"))
        db.add(CloudVersion(id=_uid(), document_id=d2.id, version_number=1,
                            content_hash=same_hash, original_filename="b.txt"))
        db.commit()  # 不应触发唯一约束
    finally:
        db.close()


def test_publication_request_idempotent_key_unique(workspace):
    db = SessionLocal()
    try:
        req_id = _uid()
        db.add(PublicationRequest(
            id=_uid(), request_id=req_id, actor_id=workspace["user_id"],
            workspace_id=workspace["ws_id"], request_hash="h1", action="publish",
        ))
        db.commit()
        db.add(PublicationRequest(
            id=_uid(), request_id=req_id, actor_id=workspace["user_id"],
            workspace_id=workspace["ws_id"], request_hash="h1", action="publish",
        ))
        with pytest.raises(IntegrityError):
            db.commit()
    finally:
        db.rollback()
        db.close()
