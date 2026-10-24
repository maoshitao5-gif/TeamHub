"""
M5 共享标签协作 / 回收站 / 成员停用 行为测试(契约 §3.4/§5.2)

- V-06: B 补标签后 C 用该标签搜索到同一共享资料;
- V-11: A/B 同时加不同标签均保留;B 移除 A 的关联被拒(403);
- V-10: 普通成员回收被拒;管理员 trash/restore/purge,purge 前检查内容引用;
- V-14: 停用上传者后其资料与贡献保留,历史仍可下载。
"""
import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import SessionLocal
from app.models import (
    User, Team, TeamMember, Workspace, CloudDocument, CloudVersion,
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
    old_storage = settings.storage_dir
    settings.storage_dir = str(tmp_path / "objects-root")

    db = SessionLocal()
    try:
        admin = User(id=_uid(), email=f"{_uid()[:8]}@t.local", hashed_password="x",
                     display_name="管理员", is_superuser=True)
        a = User(id=_uid(), email=f"{_uid()[:8]}@t.local", hashed_password="x", display_name="A")
        b = User(id=_uid(), email=f"{_uid()[:8]}@t.local", hashed_password="x", display_name="B")
        c = User(id=_uid(), email=f"{_uid()[:8]}@t.local", hashed_password="x", display_name="C")
        team = Team(id=_uid(), name="团队", slug=f"t-{_uid()[:8]}")
        ws = Workspace(id=_uid(), team_id=team.id, name="空间")
        db.add_all([admin, a, b, c, team, ws])
        db.flush()
        db.add(TeamMember(team_id=team.id, user_id=admin.id, role="admin"))
        db.add(TeamMember(team_id=team.id, user_id=a.id, role="member"))
        db.add(TeamMember(team_id=team.id, user_id=b.id, role="member"))
        db.add(TeamMember(team_id=team.id, user_id=c.id, role="member"))
        db.commit()
        yield {"admin": admin, "a": a, "b": b, "c": c, "ws": ws}
    finally:
        db.close()
        settings.storage_dir = old_storage
        app.dependency_overrides.clear()


def _as(user):
    app.dependency_overrides[get_current_user] = lambda: user


def _upload(ws_id, data: bytes):
    resp = client.post(f"/api/workspaces/{ws_id}/uploads?filename=资料.txt",
                       content=data, headers={"Content-Type": "application/octet-stream"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _publish(ws_id, upload, name="共享资料"):
    resp = client.post(f"/api/workspaces/{ws_id}/publications", json={
        "request_id": _uid(), "confirmed": True, "name": name, "visibility": "workspace", "doc_type": "file",
        "tags": [], "content_hash": upload["sha256"], "upload_id": upload["upload_id"],
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


def _add_tags(ws_id, doc_id, names):
    return client.post(f"/api/workspaces/{ws_id}/documents/{doc_id}/tags",
                       json={"add": names})


# ========== V-06 / V-11 标签协作 ==========

def test_tag_collaboration_and_search(space):
    ws = space["ws"].id

    # A 发布资料
    _as(space["a"])
    up = _upload(ws, "共享内容".encode())
    doc_id = _publish(ws, up)["document_id"]

    # B 补标签
    _as(space["b"])
    resp = _add_tags(ws, doc_id, ["乙标签"])
    assert resp.status_code == 200, resp.text
    links = resp.json()
    assert any(l["name"] == "乙标签" and l["added_by"] == space["b"].id for l in links)

    # C 用该标签搜索 → 找到同一资料
    _as(space["c"])
    resp = client.get(f"/api/workspaces/{ws}/documents/search", params={"tags": "乙标签"})
    assert resp.status_code == 200
    ids = [item["id"] for item in resp.json()["items"]]
    assert doc_id in ids

    # A/B 同时加不同标签 → 都保留
    _as(space["a"])
    resp = _add_tags(ws, doc_id, ["甲标签"])
    assert resp.status_code == 200
    a_tag_id = next(l["tag_id"] for l in resp.json() if l["name"] == "甲标签")
    _as(space["b"])
    _add_tags(ws, doc_id, ["丙标签"])

    # 直接读当前关联集合(空 add 返回最新关联)
    _as(space["a"])
    resp = _add_tags(ws, doc_id, [])
    names = {l["name"] for l in resp.json()}
    assert {"甲标签", "乙标签", "丙标签"} <= names


def test_member_cannot_remove_others_link(space):
    ws = space["ws"].id
    _as(space["a"])
    up = _upload(ws, "内容".encode())
    doc_id = _publish(ws, up)["document_id"]
    resp = _add_tags(ws, doc_id, ["甲的标签"])
    a_tag_id = next(l["tag_id"] for l in resp.json() if l["name"] == "甲的标签")

    # B 尝试移除 A 的关联 → 403
    _as(space["b"])
    resp = client.post(f"/api/workspaces/{ws}/documents/{doc_id}/tags",
                       json={"remove": [a_tag_id]})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"

    # A 移除自己的关联 → 成功
    _as(space["a"])
    resp = client.post(f"/api/workspaces/{ws}/documents/{doc_id}/tags",
                       json={"remove": [a_tag_id]})
    assert resp.status_code == 200
    assert all(l["tag_id"] != a_tag_id for l in resp.json())


def test_admin_can_remove_any_link(space):
    ws = space["ws"].id
    _as(space["a"])
    up = _upload(ws, "内容".encode())
    doc_id = _publish(ws, up)["document_id"]
    resp = _add_tags(ws, doc_id, ["甲的标签"])
    a_tag_id = next(l["tag_id"] for l in resp.json())

    # 管理员可移除任意关联
    _as(space["admin"])
    resp = client.post(f"/api/workspaces/{ws}/documents/{doc_id}/tags",
                       json={"remove": [a_tag_id]})
    assert resp.status_code == 200


# ========== V-10 回收站 ==========

def test_trash_restore_purge_permissions(space):
    ws = space["ws"].id
    _as(space["a"])
    up = _upload(ws, "回收站内容".encode())
    doc_id = _publish(ws, up)["document_id"]

    # 普通成员回收 → 403
    _as(space["b"])
    resp = client.post(f"/api/workspaces/{ws}/documents/{doc_id}/trash")
    assert resp.status_code == 403

    # 管理员回收 → 资料从列表消失
    _as(space["admin"])
    assert client.post(f"/api/workspaces/{ws}/documents/{doc_id}/trash").status_code == 200
    resp = client.get(f"/api/workspaces/{ws}/documents/search", params={"q": ""})
    ids = [item["id"] for item in resp.json()["items"]]
    assert doc_id not in ids

    # 管理员恢复 → 重新出现
    assert client.post(f"/api/workspaces/{ws}/documents/{doc_id}/restore").status_code == 200
    resp = client.get(f"/api/workspaces/{ws}/documents/search", params={"q": ""})
    assert doc_id in [item["id"] for item in resp.json()["items"]]

    # 管理员明确从回收站永久删除
    assert client.post(f"/api/workspaces/{ws}/documents/{doc_id}/trash").status_code == 200
    assert client.post(f"/api/workspaces/{ws}/documents/{doc_id}/purge").status_code == 200
    db = SessionLocal()
    try:
        assert db.query(CloudDocument).filter(CloudDocument.id == doc_id).count() == 0
        assert db.query(CloudVersion).filter(CloudVersion.document_id == doc_id).count() == 0
    finally:
        db.close()


# ========== V-14 成员停用 ==========

def test_deactivate_keeps_content_and_history(space):
    ws = space["ws"].id
    _as(space["a"])
    up = _upload(ws, "停用前的内容".encode())
    result = _publish(ws, up)
    doc_id, v1_id = result["document_id"], result["version_id"]

    # 管理员停用 A
    _as(space["admin"])
    resp = client.post(f"/api/admin/users/{space['a'].id}/deactivate")
    assert resp.status_code == 200, resp.text
    db = SessionLocal()
    try:
        a = db.query(User).filter(User.id == space["a"].id).first()
        assert a.is_active is False
        # 资料与版本记录保留
        assert db.query(CloudDocument).filter(CloudDocument.id == doc_id).count() == 1
        assert db.query(CloudVersion).filter(CloudVersion.id == v1_id).count() == 1
    finally:
        db.close()

    # 停用后历史仍可下载(管理员)
    resp = client.get(f"/api/workspaces/{ws}/documents/{doc_id}/versions/{v1_id}/download")
    assert resp.status_code == 200
    assert resp.content == "停用前的内容".encode()


def test_trashed_resources_are_hidden_everywhere(space):
    ws = space['ws'].id
    _as(space['a'])
    result = _publish(ws, _upload(ws, b'trashed'), '隐藏资料')
    doc, version = result['document_id'], result['version_id']
    _add_tags(ws, doc, ['仅回收站标签'])
    _as(space['admin'])
    assert client.post(f'/api/workspaces/{ws}/documents/{doc}/trash').status_code == 200
    _as(space['b'])
    for tail in [f'documents/{doc}', f'documents/{doc}/versions', f'documents/{doc}/versions/{version}/download', f'documents/{doc}/tags']:
        assert client.get(f'/api/workspaces/{ws}/{tail}').status_code == 404
    assert _add_tags(ws, doc, ['不能补充']).status_code == 404
    tags = client.get(f'/api/workspaces/{ws}/documents/suggestions', params={'type': 'tag'}).json()
    assert tags == []
    assert client.get(f'/api/workspaces/{ws}/tags').json() == []


def test_real_concurrent_tag_contributions(space):
    from concurrent.futures import ThreadPoolExecutor
    from app.core.security import create_access_token
    ws = space['ws'].id
    _as(space['a'])
    doc = _publish(ws, _upload(ws, b'parallel'))['document_id']
    app.dependency_overrides.clear()
    def contribute(pair):
        user, name = pair
        token = create_access_token({'sub': user.id})
        return client.post(f'/api/workspaces/{ws}/documents/{doc}/tags', headers={'Authorization': f'Bearer {token}'}, json={'add': [name, '共同标签']})
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(contribute, [(space['a'], '甲标签'), (space['b'], '乙标签')]))
    assert all(r.status_code == 200 for r in results)
    _as(space['c'])
    links = client.get(f'/api/workspaces/{ws}/documents/{doc}/tags').json()
    assert {r['name'] for r in links} == {'甲标签', '乙标签', '共同标签'}
    assert len(links) == 3
    assert next(r for r in links if r['name'] == '甲标签')['added_by'] == space['a'].id
    assert next(r for r in links if r['name'] == '乙标签')['added_by'] == space['b'].id


def test_rejected_tag_batch_is_atomic(space):
    ws = space['ws'].id
    _as(space['a'])
    doc = _publish(ws, _upload(ws, b'atomic'))['document_id']
    tag = _add_tags(ws, doc, ['甲']).json()[0]['tag_id']
    _as(space['b'])
    resp = client.post(f'/api/workspaces/{ws}/documents/{doc}/tags', json={'add': ['不应留下'], 'remove': [tag]})
    assert resp.status_code == 403
    _as(space['a'])
    assert {t['name'] for t in client.get(f'/api/workspaces/{ws}/documents/{doc}/tags').json()} == {'甲'}


def test_purge_requires_trash_and_preserves_bytes_on_commit_failure(space, monkeypatch):
    from app.core import object_store
    from sqlalchemy.orm import Session
    ws = space['ws'].id
    _as(space['a'])
    upload = _upload(ws, b'keep-on-failure')
    doc = _publish(ws, upload)['document_id']
    _as(space['admin'])
    url = f'/api/workspaces/{ws}/documents/{doc}'
    assert client.post(url + '/purge').status_code == 409
    assert client.post(url + '/trash').status_code == 200
    def fail(self):
        raise OSError('commit failure')
    monkeypatch.setattr(Session, 'commit', fail)
    with pytest.raises(OSError):
        client.post(url + '/purge')
    assert object_store.object_path(upload['sha256']).read_bytes() == b'keep-on-failure'
    with SessionLocal() as db:
        assert db.get(CloudDocument, doc) is not None


def test_purge_keeps_objects_referenced_by_another_document(space):
    from app.core import object_store
    ws = space['ws'].id
    _as(space['a'])
    up = _upload(ws, b'shared-object')
    one = _publish(ws, up)
    # Independent workspaces can legitimately share the same physical blob.
    with SessionLocal() as db:
        second_ws = Workspace(team_id=space['ws'].team_id, name='另一空间')
        db.add(second_ws)
        db.commit()
        second_ws_id = second_ws.id
    second_upload = _upload(second_ws_id, b'shared-object')
    two = _publish(second_ws_id, second_upload, name='另一资料')
    assert two['status'] == 'succeeded'
    _as(space['admin'])
    url = f"/api/workspaces/{ws}/documents/{one['document_id']}"
    assert client.post(url + '/trash').status_code == 200
    assert client.post(url + '/purge').status_code == 200
    assert object_store.object_path(up['sha256']).read_bytes() == b'shared-object'
    response = client.get(f"/api/workspaces/{second_ws_id}/documents/{two['document_id']}/versions/{two['version_id']}/download")
    assert response.status_code == 200


def test_duplicate_selected_tags_do_not_break_suggestions(space):
    ws = space['ws'].id
    _as(space['a'])
    doc = _publish(ws, _upload(ws, b'suggestions'))['document_id']
    tags = _add_tags(ws, doc, ['设备', '检修']).json()
    selected = next(t['tag_id'] for t in tags if t['name'] == '设备')
    result = client.get(f'/api/workspaces/{ws}/documents/suggestions', params={'type': 'cooccurrence', 'selected_tag_ids': f'{selected},{selected}'}).json()
    assert [t['name'] for t in result] == ['检修']


def test_user_delete_is_disabled_and_deactivation_revokes_tokens(space):
    from datetime import datetime, timedelta, timezone
    from app.models import RefreshToken
    from app.core.security import create_access_token, hash_refresh_token
    a = space['a']
    token = create_access_token({'sub': a.id})
    refresh = 'test-' + _uid()
    with SessionLocal() as db:
        db.add(RefreshToken(user_id=a.id, token_hash=hash_refresh_token(refresh), expires_at=datetime.now(timezone.utc)+timedelta(days=1)))
        db.commit()
    _as(space['admin'])
    assert client.delete(f'/api/admin/users/{a.id}').status_code == 410
    assert client.post(f'/api/admin/users/{a.id}/deactivate').status_code == 200
    app.dependency_overrides.clear()
    assert client.get('/api/users/me', headers={'Authorization': f'Bearer {token}'}).status_code == 401
    assert client.post('/api/auth/refresh', json={'refresh_token': refresh}).status_code == 401
    _as(space['admin'])
    assert client.put(f'/api/admin/users/{a.id}', json={'is_active': True}).status_code == 200
    app.dependency_overrides.clear()
    assert client.post('/api/auth/refresh', json={'refresh_token': refresh}).status_code == 401


def test_folder_purge_uses_archive_hash(space):
    import io, json, zipfile
    from app.core import object_store
    ws = space['ws'].id
    _as(space['a'])
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('a.txt', b'folder')
    up = _upload(ws, stream.getvalue())
    manifest = dict(version='1', entries=[dict(path='a.txt', type='file', size=6, sha256=_sha(b'folder'))])
    content_hash = _sha(json.dumps(manifest, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode())
    result = client.post(f'/api/workspaces/{ws}/publications', json=dict(request_id=_uid(), name='文件夹', visibility='workspace', doc_type='folder', confirmed=True,
                          content_hash=content_hash, manifest_hash=up['sha256'], upload_id=up['upload_id'])).json()
    _as(space['admin'])
    url = f"/api/workspaces/{ws}/documents/{result['document_id']}"
    assert client.post(url+'/trash').status_code == 200
    response = client.post(url+'/purge')
    assert response.status_code == 200 and response.json()['objects_deleted'] == 1
    assert not object_store.object_path(up['sha256']).exists()


def test_cleanup_failure_does_not_report_metadata_failure(space, monkeypatch):
    from pathlib import Path
    ws = space['ws'].id
    _as(space['a'])
    up = _upload(ws, b'cleanup-failure')
    doc = _publish(ws, up)['document_id']
    _as(space['admin'])
    url = f'/api/workspaces/{ws}/documents/{doc}'
    assert client.post(url+'/trash').status_code == 200
    original = Path.unlink
    def fail_object(self, *args, **kwargs):
        if self.name == up['sha256']:
            raise OSError('read-only object')
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', fail_object)
    response = client.post(url+'/purge')
    assert response.status_code == 200 and response.json()['objects_retained'] == 1
    with SessionLocal() as db:
        assert db.get(CloudDocument, doc) is None


def test_visibility_context_and_foreign_workspace(space):
    ws = space['ws'].id
    _as(space['b'])
    response = client.get(f'/api/workspaces/{ws}/collaboration-context').json()
    assert response['can_tag'] and not response['can_manage'] and not response['can_deactivate_users']
    assert client.get(f'/api/workspaces/{ws}/trash').status_code == 403
    assert client.get(f'/api/workspaces/{ws}/members-for-deactivation').status_code == 403
    with SessionLocal() as db:
        team = Team(id=_uid(), name='其他组织', slug=_uid())
        db.add(team); db.flush()
        other = Workspace(id=_uid(), team_id=team.id, name='其他空间')
        db.add(other); db.commit()
        other_id = other.id
    for path in ['documents/search', 'documents/suggestions', 'tags', 'trash', 'collaboration-context']:
        assert client.get(f'/api/workspaces/{other_id}/{path}').status_code == 404


def test_tag_ids_support_free_names_with_commas(space):
    ws = space['ws'].id
    _as(space['a'])
    doc = _publish(ws, _upload(ws, b'comma'))['document_id']
    tags = _add_tags(ws, doc, ['设备,一次', '项目']).json()
    ids = ','.join(t['tag_id'] for t in tags)
    response = client.get(f'/api/workspaces/{ws}/documents/search', params={'tag_ids': ids}).json()
    assert [d['id'] for d in response['items']] == [doc]
    assert _add_tags(ws, doc, [' '*3]).status_code == 422
    assert _add_tags(ws, doc, ['x'*101]).status_code == 422


def test_administrator_cannot_disable_self_via_alternate_update(space):
    _as(space['admin'])
    user = space['admin'].id
    assert client.put(f'/api/admin/users/{user}', json={'is_active': False}).status_code == 400
    assert client.put(f'/api/admin/users/{user}', json={'is_superuser': False}).status_code == 400
