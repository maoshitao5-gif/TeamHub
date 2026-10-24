"""
M3 行为测试:文件夹整体快照(§3.3/V-03) + 收藏(§2.5/§5.1)

文件夹快照:
- 清单摘要(content_hash)与归档摘要(manifest_hash)分离;
- 空目录、隐藏项纳入;应用管理目录 .teamhub 排除;导出完整复原;
- 符号链接拒绝;解包拒绝路径穿越/绝对路径/符号链接。
收藏:local 引用本地资料,幂等,列表附带资料引用,取消,资料不存在拒绝。
"""
import zipfile

import pytest

from fastapi.testclient import TestClient

import backend.app.database as database
from backend.app.config import settings
from backend.app.services import snapshots
from backend.app.services.snapshots import SnapshotError
from main import app

client = TestClient(app)


@pytest.fixture
def library(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    settings.library_path = str(lib)
    database.rebind_engine()
    database.init_db()
    yield lib
    settings.library_path = str(tmp_path / "lib-rest")
    database.rebind_engine()
    database.init_db()


def _make_source(tmp_path, name="报告.txt", content="第一版内容"):
    src = tmp_path / name
    src.write_text(content, encoding="utf-8")
    return src


def _ingest(source, name=None):
    return client.post("/api/documents", json={
        "name": name or source.stem,
        "source_path": str(source),
        "tags": ["测试"],
    })


# ── 文件夹整体快照(§3.3) ──────────────────────────────────────

def test_folder_snapshot_separates_content_and_manifest_hash(tmp_path):
    """契约 §3.3.6:清单摘要(内容身份)与归档字节摘要(传输校验)分开"""
    folder = tmp_path / "文件夹"
    folder.mkdir()
    (folder / "a.txt").write_text("内容", encoding="utf-8")
    lib = tmp_path / "lib"

    storage_key, content_hash, manifest_hash, size = snapshots.store_folder_snapshot(lib, folder)
    assert content_hash and manifest_hash
    assert content_hash != manifest_hash


def test_folder_snapshot_roundtrip_includes_empty_dirs_and_hidden(library, tmp_path):
    """V-03:中文名/空目录/隐藏项可完整复原,.teamhub 排除"""
    folder = tmp_path / "文件夹"
    (folder / "子目录").mkdir(parents=True)
    (folder / "空目录").mkdir()
    (folder / "子目录" / "中文文件.txt").write_text("中文内容", encoding="utf-8")
    (folder / ".hidden").write_text("隐藏", encoding="utf-8")
    (folder / ".teamhub").mkdir()
    (folder / ".teamhub" / "x.txt").write_text("排除", encoding="utf-8")

    lib = tmp_path / "lib"
    storage_key, content_hash, manifest_hash, size = snapshots.store_folder_snapshot(lib, folder)

    out = tmp_path / "out"
    snapshots.export_object(lib, storage_key, "文件夹", out, is_folder=True)
    restored = out / "文件夹"
    assert (restored / "子目录" / "中文文件.txt").read_text(encoding="utf-8") == "中文内容"
    assert (restored / "空目录").is_dir()  # 空目录复原
    assert (restored / ".hidden").read_text(encoding="utf-8") == "隐藏"  # 隐藏项不忽略
    assert not (restored / ".teamhub").exists()  # 应用管理目录排除


def test_folder_snapshot_rejects_symlink(tmp_path):
    """§3.3.4:符号链接拒绝,不跟随取出目录外资料"""
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "real.txt").write_text("x", encoding="utf-8")
    (folder / "link.txt").symlink_to(folder / "real.txt")
    lib = tmp_path / "lib"

    with pytest.raises(SnapshotError):
        snapshots.store_folder_snapshot(lib, folder)


def test_folder_snapshot_rejects_symlink_dir(tmp_path):
    """§3.3.4:符号链接目录同样拒绝"""
    outside = tmp_path / "outside"
    outside.mkdir()
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "linkdir").symlink_to(outside, target_is_directory=True)
    lib = tmp_path / "lib"

    with pytest.raises(SnapshotError):
        snapshots.store_folder_snapshot(lib, folder)


def test_export_rejects_zip_slip(tmp_path):
    """§3.3.5:解包拒绝路径穿越条目"""
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../escape.txt", "x")
    target = tmp_path / "out"
    target.mkdir()

    with pytest.raises(SnapshotError):
        snapshots._extract_zip_safely(evil, target)
    assert not (tmp_path / "escape.txt").exists()


# ── 收藏(§2.5 / §5.1) ─────────────────────────────────────────

def test_favorite_lifecycle(library, tmp_path):
    """local 收藏:创建、幂等、列表附带资料引用、取消"""
    src = _make_source(tmp_path, "资料.txt", "内容")
    doc = _ingest(src).json()

    resp = client.post("/api/favorites", json={
        "target_type": "local", "local_document_id": doc["id"],
    })
    assert resp.status_code == 201, resp.text
    fav = resp.json()
    assert fav["target_type"] == "local"
    assert fav["local_document_id"] == doc["id"]
    assert fav["document"]["id"] == doc["id"]

    # 幂等:重复收藏返回同一收藏
    resp2 = client.post("/api/favorites", json={
        "target_type": "local", "local_document_id": doc["id"],
    })
    assert resp2.status_code == 201
    assert resp2.json()["id"] == fav["id"]

    # 列表
    resp = client.get("/api/favorites")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["document"]["name"] == "资料"

    # 取消
    assert client.delete(f"/api/favorites/{fav['id']}").status_code == 200
    assert client.get("/api/favorites").json() == []


def test_favorite_rejects_missing_document(library):
    """收藏不存在的资料 → 404"""
    resp = client.post("/api/favorites", json={
        "target_type": "local", "local_document_id": "nonexistent",
    })
    assert resp.status_code == 404


def test_favorite_missing_local_doc_id_rejected(library):
    """local 收藏缺少 local_document_id → 400"""
    resp = client.post("/api/favorites", json={"target_type": "local"})
    assert resp.status_code == 400


@pytest.mark.parametrize('quick', [False, True])
def test_ingest_rejects_nested_symlink_before_copy(library, tmp_path, quick):
    folder = tmp_path / '源目录'
    folder.mkdir()
    outside = _make_source(tmp_path, '不可收录.txt', '目录外内容')
    (folder / '链接.txt').symlink_to(outside)
    response = (client.post('/api/documents/quick-add', json={'source_path': str(folder)})
                if quick else _ingest(folder))
    assert response.status_code == 500, response.text
    assert client.post('/api/documents/search', json={}).json()['total'] == 0
    assert not (library / folder.name).exists()
    assert not (library / settings.pending_folder_name / folder.name).exists()
    assert outside.read_text(encoding='utf-8') == '目录外内容'


def test_walk_failure_is_not_empty_success(tmp_path, monkeypatch):
    folder = tmp_path / 'folder'
    folder.mkdir()
    def failed_walk(*args, **kwargs):
        if kwargs.get('onerror'):
            kwargs['onerror'](PermissionError('unreadable directory'))
        return iter([])
    monkeypatch.setattr(snapshots.os, 'walk', failed_walk)
    with pytest.raises(SnapshotError):
        snapshots.store_folder_snapshot(tmp_path / 'lib', folder)


def test_folder_change_during_archive_fails(tmp_path, monkeypatch):
    folder = tmp_path / 'folder'
    folder.mkdir()
    source = folder / 'a.txt'
    source.write_text('before')
    original = zipfile.ZipFile.open
    def changed_open(self, name, mode='r', *args, **kwargs):
        if mode == 'w':
            source.write_text('after-content')
        return original(self, name, mode, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, 'open', changed_open)
    with pytest.raises(SnapshotError):
        snapshots.store_folder_snapshot(tmp_path / 'lib', folder)
    assert not list((tmp_path / 'lib/.teamhub/objects/tmp').glob('*'))


def test_extract_limits_before_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'snapshot_max_total_bytes', 3, raising=False)
    archive = tmp_path / 'large.zip'
    with zipfile.ZipFile(archive, 'w') as zf:
        zf.writestr('a.txt', '1234')
    target = tmp_path / 'out'
    target.mkdir()
    with pytest.raises(SnapshotError, match='上限'):
        snapshots._extract_zip_safely(archive, target)
    assert list(target.iterdir()) == []


@pytest.mark.parametrize('name', ['C:/escape.txt', 'a/../escape.txt', 'a\\escape.txt'])
def test_extract_rejects_nonportable_paths(tmp_path, name):
    archive = tmp_path / 'bad.zip'
    with zipfile.ZipFile(archive, 'w') as zf:
        zf.writestr(name, 'x')
    with pytest.raises(SnapshotError):
        snapshots._extract_zip_safely(archive, tmp_path / 'out')


def test_folder_versions_and_readonly_working_state(library, tmp_path):
    source = tmp_path / '工程资料'
    (source / '空目录').mkdir(parents=True)
    (source / '.hidden').write_text('hidden')
    (source / 'a.txt').write_text('v1')
    response = _ingest(source)
    assert response.status_code == 200, response.text
    doc = response.json()
    working = library / doc['storage_path']
    base = f"/api/documents/{doc['id']}/versions"
    v1 = client.get(base).json()[0]
    objects = sorted((library / '.teamhub' / 'objects').rglob('*'))
    assert client.get(base + '/working-state').json()['state'] == 'unchanged'
    assert sorted((library / '.teamhub' / 'objects').rglob('*')) == objects
    (working / 'a.txt').write_text('v2-longer')
    assert client.get(base + '/working-state').json()['state'] == 'changed'
    v2 = client.post(base, json={}).json()
    assert v2['version_number'] == 2
    assert client.get(base + '/working-state').json()['state'] == 'unchanged'
    v3 = client.post(base + '/' + v1['id'] + '/restore').json()
    assert v3['id'] == v1['id'] and v3['sha256_hash'] == v1['sha256_hash']
    assert (working / 'a.txt').read_text() == 'v1'
    assert client.get(base + '/working-state').json()['state'] == 'unchanged'
    for version, expected in [(v1, 'v1'), (v2, 'v2-longer')]:
        out = tmp_path / str(version['version_number'])
        result = client.post(base + '/' + version['id'] + '/export', json={'target_dir': str(out)})
        assert result.status_code == 200, result.text
        assert (out / source.name / 'a.txt').read_text() == expected
        assert (out / source.name / '空目录').is_dir()
        assert (out / source.name / '.hidden').read_text() == 'hidden'
    duplicate = _ingest(source).json()
    assert duplicate['error']['code'] == 'DUPLICATE_CONTENT'
    assert duplicate['document']['id'] == doc['id']
    import shutil
    shutil.rmtree(working)
    assert client.get(base + '/working-state').json()['state'] == 'missing'
    assert len(client.get(base).json()) == 2


def test_favorites_survive_rename_and_library_isolation(library, tmp_path):
    from backend.app.models import Document
    doc = _ingest(_make_source(tmp_path)).json()
    fav = client.post('/api/favorites', json={'target_type': 'local', 'local_document_id': doc['id']}).json()
    with database.SessionLocal() as db:
        db.get(Document, doc['id']).name = '新名称'
        db.commit()
    assert client.get('/api/favorites?target_type=local').json()[0]['document']['name'] == '新名称'
    assert client.get('/api/favorites?target_type=shared').json() == []
    other = tmp_path / '另一资料库'
    other.mkdir()
    settings.library_path = str(other)
    database.rebind_engine()
    database.init_db()
    assert client.get('/api/favorites?target_type=local').json() == []
    settings.library_path = str(library)
    database.rebind_engine()
    assert client.get('/api/favorites?target_type=local').json()[0]['id'] == fav['id']
    assert client.delete('/api/favorites/' + fav['id']).status_code == 200


def test_personal_search_intersection_and_related_scope(library, tmp_path):
    def add(name, tags):
        src = _make_source(tmp_path, name + '.txt', name)
        result = client.post('/api/documents', json={'name': name, 'source_path': str(src), 'tags': tags})
        assert result.status_code == 200, result.text
        return result.json()
    a = add('变压器报告', ['设备', '检修', '甲项目'])
    add('断路器报告', ['设备', '乙项目'])
    b = add('已删除报告', ['设备', '检修', '失效标签'])
    assert client.delete('/api/documents/' + b['id']).status_code == 200
    tags = {t['name']: t['id'] for t in a['tags']}
    response = client.post('/api/documents/search', json={'keyword': '报告', 'tags': ['设备', '检修'], 'status': 'organized'})
    assert response.status_code == 200, response.text
    assert [d['id'] for d in response.json()['documents']] == [a['id']]
    related = client.get('/api/tags/related', params={'selected': ','.join([tags['设备'], tags['检修'], tags['设备']]), 'limit': 1})
    assert related.status_code == 200, related.text
    assert [t['name'] for t in related.json()] == ['甲项目']
    assert client.post('/api/documents/search', json={'status': 'organized'}).json()['total'] == 2


@pytest.mark.parametrize('setting,value', [
    ('snapshot_max_entries', 1),
    ('snapshot_max_file_bytes', 2),
    ('snapshot_max_total_bytes', 4),
])
def test_folder_capture_limits_leave_no_partial_object(tmp_path, monkeypatch, setting, value):
    folder = tmp_path / 'source'
    folder.mkdir()
    (folder / 'a').write_bytes(b'abc')
    (folder / 'b').write_bytes(b'def')
    monkeypatch.setattr(settings, setting, value)
    lib = tmp_path / 'lib'
    with pytest.raises(SnapshotError, match='上限'):
        snapshots.store_folder_snapshot(lib, folder)
    assert not [p for p in (lib / '.teamhub' / 'objects').rglob('*') if p.is_file()]


def test_first_folder_import_recovers_from_one_transient_change(library, tmp_path, monkeypatch):
    """源目录首次读取时有一次变化，单次请求重新捕获后完成，且只产生一版。"""
    folder = tmp_path / 'first-import'
    folder.mkdir()
    source = folder / 'sample.txt'
    source.write_text('before')
    inventory = snapshots._folder_inventory
    changed = False

    def changing_inventory(path):
        nonlocal changed
        result = inventory(path)
        if not changed:
            changed = True
            source.write_text('stable-content')
        return result

    monkeypatch.setattr(snapshots, '_folder_inventory', changing_inventory)
    response = client.post('/api/documents/quick-add', json={'source_path': str(folder)})
    assert response.status_code == 200, response.text
    doc = response.json()
    assert doc['status'] == 'pending'
    assert not folder.exists()
    assert (library / doc['storage_path'] / 'sample.txt').read_text() == 'stable-content'
    versions = client.get(f"/api/documents/{doc['id']}/versions").json()
    assert len(versions) == 1
    assert not list((library / '.teamhub/objects/tmp').glob('*'))


def test_continuously_changing_folder_stops_without_moving_source(library, tmp_path, monkeypatch):
    folder = tmp_path / 'changing'
    folder.mkdir()
    source = folder / 'sample.txt'
    source.write_text('initial')
    inventory = snapshots._folder_inventory
    attempts = 0

    def changing_inventory(path):
        nonlocal attempts
        result = inventory(path)
        attempts += 1
        source.write_text('changed-' * attempts)
        return result

    monkeypatch.setattr(snapshots, '_folder_inventory', changing_inventory)
    response = client.post('/api/documents/quick-add', json={'source_path': str(folder)})
    assert response.status_code == 409
    assert attempts == 3
    assert source.exists()
    assert not (library / settings.pending_folder_name / folder.name).exists()
    assert not list((library / '.teamhub/objects/tmp').glob('*'))


def test_folder_permission_error_is_not_retried(tmp_path, monkeypatch):
    attempts = 0

    def denied(path):
        nonlocal attempts
        attempts += 1
        raise PermissionError('synthetic permission failure')

    monkeypatch.setattr(snapshots, '_folder_inventory', denied)
    with pytest.raises(SnapshotError, match='synthetic permission failure'):
        snapshots.store_folder_snapshot(tmp_path / 'lib', tmp_path / 'source')
    assert attempts == 1
    assert not list((tmp_path / 'lib/.teamhub/objects/tmp').glob('*'))


@pytest.mark.parametrize('status', ['organized', 'pending', 'trashed', 'missing'])
def test_used_tag_cannot_be_deleted_in_any_document_state(library, tmp_path, status):
    from backend.app.models import Document, Tag, ChangeLog
    response = _ingest(_make_source(tmp_path))
    assert response.status_code == 200
    doc = response.json()
    tag_id = doc['tags'][0]['id']
    with database.SessionLocal() as db:
        db.get(Document, doc['id']).status = status
        db.commit()
    tag = next(tag for tag in client.get('/api/tags').json() if tag['id'] == tag_id)
    assert tag['usage_count'] == 1
    assert tag['usage_by_status'] == {status: 1}
    assert tag['document_count'] == (1 if status == 'organized' else 0)
    stats = client.get('/api/tags?include_stats=true').json()
    assert stats['total_associated_documents'] == 1
    result = client.delete(f'/api/tags/{tag_id}')
    assert result.status_code == 409
    with database.SessionLocal() as db:
        assert db.get(Tag, tag_id) is not None
        assert [tag.id for tag in db.get(Document, doc['id']).tags] == [tag_id]
        assert db.query(ChangeLog).filter_by(entity_id=tag_id, operation='delete').count() == 0


def test_unused_tag_can_be_deleted(library):
    tag = client.post('/api/tags', json={'name': 'unused'}).json()
    assert tag['usage_count'] == 0
    assert client.delete(f"/api/tags/{tag['id']}").status_code == 200
    assert client.delete(f"/api/tags/{tag['id']}").status_code == 404


def test_tag_can_be_deleted_after_last_association_removed(library, tmp_path):
    doc = _ingest(_make_source(tmp_path)).json()
    tag_id = doc['tags'][0]['id']
    assert client.put(f"/api/documents/{doc['id']}/tags", json=[]).status_code == 200
    tag = next(tag for tag in client.get('/api/tags').json() if tag['id'] == tag_id)
    assert tag['usage_count'] == 0
    assert tag['usage_by_status'] == {}
    assert client.delete(f'/api/tags/{tag_id}').status_code == 200


def test_tag_used_just_before_delete_is_preserved(library, tmp_path):
    from sqlalchemy import event
    from backend.app.models import document_tag
    doc = _ingest(_make_source(tmp_path)).json()
    tag = client.post('/api/tags', json={'name': 'concurrent-use'}).json()
    inserted = False

    def attach_before_delete(conn, cursor, statement, parameters, context, executemany):
        nonlocal inserted
        if not inserted and statement.lstrip().upper().startswith('DELETE FROM TAGS'):
            inserted = True
            with database.engine.begin() as writer:
                writer.execute(document_tag.insert().values(document_id=doc['id'], tag_id=tag['id']))

    event.listen(database.engine, 'before_cursor_execute', attach_before_delete)
    try:
        result = client.delete(f"/api/tags/{tag['id']}")
        assert inserted
        assert result.status_code == 409, result.text
    finally:
        event.remove(database.engine, 'before_cursor_execute', attach_before_delete)
    assert tag['id'] in [t['id'] for t in client.get(f"/api/documents/{doc['id']}").json()['tags']]
