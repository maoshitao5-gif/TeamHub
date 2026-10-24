"""
M4 共享发布通路行为测试(契约 §5.2/§4;蓝图 §3.5)

覆盖验证清单云端侧:
- V-05 发布全链路 + 同 request_id 重放结果一致,不重复建档;
- V-07 同 request_id 换负载 → 409 REQUEST_MISMATCH;过期基准/并发败者 → 409 STALE_BASE_VERSION;
- V-08 跨空间:上传/发布/提交/下载/查重一律 404;
- V-09 声明摘要 ≠ 上传字节 → 422,不产生任何成功记录;
- 下载三级归属校验;查重不溢出当前空间。
"""
import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import SessionLocal
from app.models import (
    User, Team, TeamMember, Workspace, CloudDocument, CloudVersion, PublicationRequest,
)
from app.core.deps import get_current_user
from main import app

client = TestClient(app)


def _uid():
    return str(uuid.uuid4())


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def space(tmp_path):
    """成员 u1 ∈ team1/ws1;另有 ws2(u1 无权)。存储根目录隔离到 tmp_path。"""
    old_storage = settings.storage_dir
    settings.storage_dir = str(tmp_path / "objects-root")

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
        db.commit()
        yield {"u1": u1, "ws1": ws1, "ws2": ws2}
    finally:
        db.close()
        settings.storage_dir = old_storage
        app.dependency_overrides.clear()


def _as(user):
    app.dependency_overrides[get_current_user] = lambda: user


def _upload(ws_id, data: bytes, filename="资料.txt"):
    resp = client.post(
        f"/api/workspaces/{ws_id}/uploads?filename={filename}",
        content=data,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _publish(ws_id, upload, *, request_id=None, name="共享资料", content_hash=None, tags=None):
    body = {
        "request_id": request_id or _uid(), "confirmed": True,
        "name": name, "visibility": "workspace",
        "doc_type": "file",
        "tags": tags or ["共享"],
        "content_hash": content_hash or upload["sha256"],
        "upload_id": upload["upload_id"],
    }
    return client.post(f"/api/workspaces/{ws_id}/publications", json=body)


def _counts(ws_id):
    """按空间过滤计数:测试模块共享一个数据库,用各用例独立的空间 ID 隔离断言"""
    db = SessionLocal()
    try:
        docs = db.query(CloudDocument).filter(CloudDocument.workspace_id == ws_id).count()
        versions = (
            db.query(CloudVersion)
            .join(CloudDocument, CloudVersion.document_id == CloudDocument.id)
            .filter(CloudDocument.workspace_id == ws_id)
            .count()
        )
        reqs = db.query(PublicationRequest).filter(
            PublicationRequest.workspace_id == ws_id,
            PublicationRequest.status == "succeeded",
        ).count()
        return docs, versions, reqs
    finally:
        db.close()


def test_corrupt_folder_upload_returns_validation_error_without_promotion(space):
    import io
    import struct
    import zipfile
    from app.core import object_store

    _as(space["u1"])
    ws_id = space["ws1"].id
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr("file.txt", b"synthetic data")
    broken = bytearray(archive.getvalue())
    name_length, extra_length = struct.unpack_from("<HH", broken, 26)
    broken[30 + name_length + extra_length] = 255
    upload = _upload(ws_id, bytes(broken), "folder.zip")
    response = client.post(f"/api/workspaces/{ws_id}/publications", json={
        "request_id": _uid(), "confirmed": True, "name": "合成文件夹", "visibility": "workspace",
        "doc_type": "folder", "upload_id": upload["upload_id"],
        "manifest_hash": upload["sha256"], "content_hash": "a" * 64,
    })
    assert response.status_code == 422
    assert _counts(ws_id) == (0, 0, 0)
    assert not list((object_store._root() / "objects").glob("*/*"))


def test_upload_io_error_does_not_expose_server_paths(space, monkeypatch):
    from app.core import object_store

    async def broken_storage(*args, **kwargs):
        raise OSError("synthetic-private-server-path")

    _as(space["u1"])
    monkeypatch.setattr(object_store, "save_upload_stream", broken_storage)
    response = client.post(f"/api/workspaces/{space['ws1'].id}/uploads?filename=file.txt", content=b"synthetic")
    assert response.status_code == 507
    assert "synthetic-private-server-path" not in response.text


# ========== V-05 发布与幂等重放 ==========

def test_upload_publish_replay_and_query(space):
    _as(space["u1"])
    ws_id = space["ws1"].id
    payload = "第一版内容".encode()

    up = _upload(ws_id, payload)
    assert up["sha256"] == _sha(payload)
    assert up["file_size"] == len(payload)

    req_id = _uid()
    resp = _publish(ws_id, up, request_id=req_id)
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["request_id"] == req_id

    # GET 本人发布请求 → succeeded
    resp = client.get(f"/api/workspaces/{ws_id}/publications/{req_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "succeeded"
    assert resp.json()["result_document_id"] == result["document_id"]

    # 同 request_id 同负载重放 → 同一结果,不重复建档(staging 已消费也不影响)
    replay = _publish(ws_id, {"upload_id": "consumed", "sha256": up["sha256"]}, request_id=req_id)
    assert replay.status_code == 200
    assert replay.json()["document_id"] == result["document_id"]
    docs, versions, reqs = _counts(ws_id)
    assert (docs, versions, reqs) == (1, 1, 1)

    # 当前版本事实来源已指向 v1
    db = SessionLocal()
    try:
        doc = db.get(CloudDocument, result["document_id"])
        v1 = db.get(CloudVersion, result["version_id"])
        assert doc.current_version_id == v1.id
        assert doc.created_by == space["u1"].id
        assert v1.author_id == space["u1"].id
        assert v1.storage_key == f"objects/{up['sha256'][:2]}/{up['sha256']}"
    finally:
        db.close()


# ========== V-07 幂等键与基准冲突 ==========

def test_request_id_with_different_payload_rejected(space):
    _as(space["u1"])
    ws_id = space["ws1"].id
    up = _upload(ws_id, b"content-a")
    req_id = _uid()
    assert _publish(ws_id, up, request_id=req_id).status_code == 200

    # 同 request_id 换名称 → 409 REQUEST_MISMATCH,不产生新资料
    resp = _publish(ws_id, up, request_id=req_id, name="换掉的名称")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "REQUEST_MISMATCH"
    assert _counts(ws_id) == (1, 1, 1)


def test_submit_version_and_stale_base(space):
    _as(space["u1"])
    ws_id = space["ws1"].id

    up1 = _upload(ws_id, "第一版".encode())
    doc_id = _publish(ws_id, up1).json()["document_id"]
    db = SessionLocal()
    try:
        v1_id = db.query(CloudDocument).filter(CloudDocument.id == doc_id).first().current_version_id
    finally:
        db.close()

    # 正常提交 v2 → 当前版本更新
    up2 = _upload(ws_id, "第二版".encode())
    resp = client.post(f"/api/workspaces/{ws_id}/documents/{doc_id}/versions", json={
        "request_id": _uid(), "confirmed": True, "base_version_id": v1_id,
        "content_hash": up2["sha256"], "upload_id": up2["upload_id"], "note": "v2",
    })
    assert resp.status_code == 200, resp.text
    v2_id = resp.json()["version_id"]

    # 基于同一旧版再提交(并发败者语义)→ 409 STALE_BASE_VERSION,details 带当前入口
    up3 = _upload(ws_id, "第三版".encode())
    resp = client.post(f"/api/workspaces/{ws_id}/documents/{doc_id}/versions", json={
        "request_id": _uid(), "confirmed": True, "base_version_id": v1_id,
        "content_hash": up3["sha256"], "upload_id": up3["upload_id"],
    })
    assert resp.status_code == 409
    body = resp.json()["error"]
    assert body["code"] == "STALE_BASE_VERSION"
    assert body["details"]["current_version_id"] == v2_id

    db = SessionLocal()
    try:
        doc = db.query(CloudDocument).filter(CloudDocument.id == doc_id).first()
        assert doc.current_version_id == v2_id
        assert db.query(CloudVersion).filter(CloudVersion.document_id == doc_id).count() == 2
    finally:
        db.close()


# ========== V-09 伪造摘要 ==========

def test_forged_hash_creates_nothing(space):
    _as(space["u1"])
    ws_id = space["ws1"].id
    up = _upload(ws_id, b"real-bytes")

    # 声明另一个摘要 → 422,不产生任何成功记录
    resp = _publish(ws_id, up, content_hash=_sha(b"forged-claim"))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert _counts(space["ws1"].id) == (0, 0, 0)

    # 未知 upload_id → 404(防探测)
    resp = _publish(ws_id, {"upload_id": _uid(), "sha256": _sha(b"x")})
    assert resp.status_code == 404
    assert _counts(ws_id) == (0, 0, 0)


# ========== V-08 跨空间防探测 ==========

def test_cross_workspace_all_write_paths_denied(space):
    _as(space["u1"])
    ws1, ws2 = space["ws1"].id, space["ws2"].id

    # 直接向他人空间上传/发布/查重 → 404
    assert client.post(f"/api/workspaces/{ws2}/uploads?filename=x", content=b"x").status_code == 404
    assert client.post(f"/api/workspaces/{ws2}/publications", json={}).status_code == 404
    assert client.post(f"/api/workspaces/{ws2}/duplicates", json={"content_hash": "0" * 64}).status_code == 404

    # 在自己空间上传后,拿着暂存到他人空间发布 → 404(归属不符)
    up = _upload(ws1, b"my-bytes")
    resp = client.post(f"/api/workspaces/{ws2}/publications", json={
        "request_id": _uid(), "confirmed": True, "name": "越权", "doc_type": "file",
        "tags": [], "content_hash": up["sha256"], "upload_id": up["upload_id"],
    })
    assert resp.status_code == 404
    assert _counts(ws1) == (0, 0, 0)


def test_download_ownership_chain(space):
    _as(space["u1"])
    ws1, ws2 = space["ws1"].id, space["ws2"].id
    payload = "下载内容".encode()

    up = _upload(ws1, payload)
    doc_id = _publish(ws1, up).json()["document_id"]
    db = SessionLocal()
    try:
        v1_id = db.query(CloudDocument).filter(CloudDocument.id == doc_id).first().current_version_id
        # 他人空间的资料与版本
        doc2 = CloudDocument(id=_uid(), workspace_id=ws2, name="机密")
        db.add(doc2)
        db.flush()
        ver2 = CloudVersion(id=_uid(), document_id=doc2.id, version_number=1,
                            original_filename="s.txt", content_hash=up["sha256"],
                            storage_key=f"objects/{up['sha256'][:2]}/{up['sha256']}")
        db.add(ver2)
        db.commit()
        doc2_id, ver2_id = doc2.id, ver2.id
    finally:
        db.close()

    # 正确链 → 200 且字节一致
    resp = client.get(f"/api/workspaces/{ws1}/documents/{doc_id}/versions/{v1_id}/download")
    assert resp.status_code == 200
    assert resp.content == payload

    # 版本不属于资料 / 资料不属于空间 / 无权限空间 → 一律 404
    assert client.get(f"/api/workspaces/{ws1}/documents/{doc2_id}/versions/{v1_id}/download").status_code == 404
    assert client.get(f"/api/workspaces/{ws1}/documents/{doc_id}/versions/{ver2_id}/download").status_code == 404
    assert client.get(f"/api/workspaces/{ws2}/documents/{doc2_id}/versions/{ver2_id}/download").status_code == 404


# ========== 查重不溢出当前空间 ==========

def test_duplicates_scoped_to_workspace(space):
    _as(space["u1"])
    ws1, ws2 = space["ws1"].id, space["ws2"].id
    payload = b"duplicate-content"

    up = _upload(ws1, payload)
    doc_id = _publish(ws1, up).json()["document_id"]

    # 他空间放一条同摘要资料(直接建库)
    db = SessionLocal()
    try:
        doc2 = CloudDocument(id=_uid(), workspace_id=ws2, name="同名内容")
        db.add(doc2)
        db.flush()
        db.add(CloudVersion(id=_uid(), document_id=doc2.id, version_number=1,
                            original_filename="d.txt", content_hash=_sha(payload)))
        db.commit()
    finally:
        db.close()

    resp = client.post(f"/api/workspaces/{ws1}/duplicates", json={"content_hash": _sha(payload)})
    assert resp.status_code == 200
    hits = resp.json()
    assert len(hits) == 1
    assert hits[0]["document_id"] == doc_id


def test_description_is_part_of_idempotency_payload(space):
    _as(space['u1'])
    ws = space['ws1'].id
    up = _upload(ws, b'description-check')
    body = dict(request_id=_uid(), name='资料', visibility='workspace', content_hash=up['sha256'], upload_id=up['upload_id'], confirmed=True)
    assert client.post(f'/api/workspaces/{ws}/publications', json=body).status_code == 200
    body['description'] = 'different'
    assert client.post(f'/api/workspaces/{ws}/publications', json=body).status_code == 409


def test_unconfirmed_publication_rejected(space):
    _as(space['u1'])
    ws = space['ws1'].id
    up = _upload(ws, b'private')
    response = client.post(f'/api/workspaces/{ws}/publications', json=dict(request_id=_uid(), name='私密', content_hash=up['sha256'], upload_id=up['upload_id']))
    assert response.status_code == 400
    assert _counts(ws) == (0, 0, 0)


def test_other_member_cannot_consume_upload(space):
    _as(space['u1'])
    ws = space['ws1'].id
    up = _upload(ws, b'owner-only')
    with SessionLocal() as db:
        other = User(id=_uid(), email=_uid()+'@t.local', hashed_password='x', display_name='Other')
        db.add(other)
        db.flush()
        db.add(TeamMember(team_id=space['ws1'].team_id, user_id=other.id, role='member'))
        db.commit()
        _as(other)
        assert _publish(ws, up).status_code == 404
    assert _counts(ws) == (0, 0, 0)


def test_simultaneous_submissions_have_one_winner(space):
    from concurrent.futures import ThreadPoolExecutor
    _as(space['u1'])
    ws = space['ws1'].id
    first = _publish(ws, _upload(ws, b'first')).json()
    uploads = [_upload(ws, b'next-a'), _upload(ws, b'next-b')]
    def submit(up):
        return client.post(f"/api/workspaces/{ws}/documents/{first['document_id']}/versions", json=dict(
            request_id=_uid(), base_version_id=first['version_id'], content_hash=up['sha256'], upload_id=up['upload_id'], confirmed=True))
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(submit, uploads))
    assert sorted(r.status_code for r in results) == [200, 409]
    assert _counts(ws) == (1, 2, 2)


def test_folder_validation_hashes_and_unsafe_archive(space):
    import io
    import json
    import zipfile
    _as(space['u1'])
    ws = space['ws1'].id
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('空目录/', b'')
        archive.writestr('a.txt', b'abc')
    entries = [dict(path='a.txt', type='file', size=3, sha256=_sha(b'abc')),
               dict(path='空目录', type='dir', size=0, sha256=None)]
    digest = _sha(json.dumps(dict(version='1', entries=entries), sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode())
    up = _upload(ws, data.getvalue(), '文件夹')
    body = dict(request_id=_uid(), name='文件夹', visibility='workspace', doc_type='folder', upload_id=up['upload_id'],
                content_hash=digest, manifest_hash=up['sha256'], confirmed=True)
    result = client.post(f'/api/workspaces/{ws}/publications', json=body)
    assert result.status_code == 200, result.text
    document = client.get(f"/api/workspaces/{ws}/documents/{result.json()['document_id']}").json()
    assert document['is_folder'] and document['file_count'] == 1 and document['total_size'] == 3
    version = document['versions'][0]
    assert version['content_hash'] == digest and version['manifest_hash'] == up['sha256']
    assert version['is_current']
    bad = io.BytesIO()
    with zipfile.ZipFile(bad, 'w') as archive:
        archive.writestr('../escape', b'bad')
    upload = _upload(ws, bad.getvalue(), 'bad')
    body.update(request_id=_uid(), upload_id=upload['upload_id'], manifest_hash=upload['sha256'])
    assert client.post(f'/api/workspaces/{ws}/publications', json=body).status_code == 422


def test_retry_after_database_commit_failure(space, monkeypatch):
    _as(space['u1'])
    ws = space['ws1'].id
    up = _upload(ws, b'commit-retry')
    request_id = _uid()
    from sqlalchemy.orm import Session
    real_commit = Session.commit
    def fail_commit(self):
        raise OSError('injected commit failure')
    with monkeypatch.context() as patch:
        patch.setattr(Session, 'commit', fail_commit)
        with pytest.raises(OSError):
            _publish(ws, up, request_id=request_id)
    assert _counts(ws) == (0, 0, 0)
    assert _publish(ws, up, request_id=request_id).json()['status'] == 'succeeded'
    assert _counts(ws) == (1, 1, 1)


def test_upload_abort_and_limit_cleanup(space, monkeypatch):
    import asyncio
    from app.core import object_store
    async def broken():
        yield b'partial'
        raise OSError('disconnected')
    with pytest.raises(OSError):
        asyncio.run(object_store.save_upload_stream(broken(), workspace_id=space['ws1'].id, uploader_id=space['u1'].id, filename='x'))
    assert list(object_store._staging_dir().iterdir()) == []
    monkeypatch.setattr(settings, 'upload_max_bytes', 2)
    _as(space['u1'])
    resp = client.post(f"/api/workspaces/{space['ws1'].id}/uploads?filename=x", content=b'abc')
    assert resp.status_code == 422
    assert list(object_store._staging_dir().iterdir()) == []


def test_force_new_and_single_file_type_do_not_bypass_content_deduplication(space):
    _as(space['u1'])
    ws = space['ws1'].id
    up = _upload(ws, b'identical')
    first = _publish(ws, up).json()
    duplicate = _publish(ws, up).json()
    assert duplicate['status'] == 'failed'
    assert duplicate['document_id'] == first['document_id']
    body = dict(request_id=_uid(), name='另建资料', visibility='workspace', doc_type='file', upload_id=up['upload_id'], content_hash=up['sha256'], confirmed=True, force_new=True)
    other = client.post(f'/api/workspaces/{ws}/publications', json=body).json()
    assert other['status'] == 'failed' and other['document_id'] == first['document_id']
    assert other['code'] == 'DUPLICATE_CONTENT'
    body.update(request_id=_uid(), doc_type='archive', force_new=False)
    assert client.post(f'/api/workspaces/{ws}/publications', json=body).json()['code'] == 'DUPLICATE_CONTENT'
    assert _counts(ws) == (1, 1, 1)


def test_file_cannot_override_download_object_hash(space):
    _as(space['u1'])
    ws = space['ws1'].id
    up = _upload(ws, b'valid-file')
    response = client.post(f'/api/workspaces/{ws}/publications', json=dict(
        request_id=_uid(), name='资料', visibility='workspace', upload_id=up['upload_id'], content_hash=up['sha256'],
        manifest_hash='0'*64, confirmed=True))
    assert response.status_code == 422
    assert _counts(ws) == (0, 0, 0)


def test_unchanged_shared_content_does_not_create_version(space):
    _as(space['u1'])
    ws = space['ws1'].id
    up = _upload(ws, b'unchanged')
    first = _publish(ws, up).json()
    body = dict(request_id=_uid(), base_version_id=first['version_id'], upload_id=up['upload_id'], content_hash=up['sha256'], confirmed=True)
    url = f"/api/workspaces/{ws}/documents/{first['document_id']}/versions"
    response = client.post(url, json=body)
    assert response.status_code == 200 and response.json()['code'] == 'CONTENT_UNCHANGED'
    assert response.json()['version_id'] == first['version_id']
    assert client.post(url, json=body).json() == response.json()
    assert _counts(ws) == (1, 1, 2)
