"""
M2 行为测试:单文件可靠个人生命周期

对应蓝图验证清单:
- V-01 收录后源文件不动、无需登录;
- V-02 同内容再收录返回既有入口,不新建;
- V-04 存两版→恢复初版,各版摘要逐版不变,恢复直接切换当前版本;
- V-10(本地部分) 删除本地工作副本不影响快照对象与导出;
另覆盖:重复建版(CONTENT_UNCHANGED)、导出策略(不覆写)、
回收站同名资料按 ID 精确恢复(附录 B 缺陷)、versions 唯一索引迁移。
"""
import sqlite3

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.app.database as database
from backend.app.config import settings
from main import app

client = TestClient(app)


@pytest.fixture
def library(tmp_path):
    """每个用例一个独立文件库,结束后恢复到隔离默认库"""
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
    """默认参数收录(不显式传 storage_mode,验证默认复制)"""
    return client.post("/api/documents", json={
        "name": name or source.stem,
        "source_path": str(source),
        "tags": ["测试"],
    })


def _versions(doc_id):
    resp = client.get(f"/api/documents/{doc_id}/versions")
    assert resp.status_code == 200
    return resp.json()


def test_ingest_copies_source_and_creates_snapshot(library, tmp_path):
    """V-01:默认复制收录,源文件不动,v1 为对象库不可变快照"""
    src = _make_source(tmp_path)
    resp = _ingest(src)
    assert resp.status_code == 200, resp.text
    doc = resp.json()

    # 源文件不动
    assert src.read_text(encoding="utf-8") == "第一版内容"
    # 工作副本落在库内
    working = library / doc["storage_path"]
    assert working.is_file()

    # v1 快照在对象库,内容一致,哈希已写入
    versions = _versions(doc["id"])
    assert len(versions) == 1
    v1 = versions[0]
    assert v1["version_number"] == 1
    assert v1["sha256_hash"]
    snapshot = library / ".teamhub" / "objects" / v1["sha256_hash"][:2] / v1["sha256_hash"]
    assert snapshot.is_file()
    assert snapshot.read_text(encoding="utf-8") == "第一版内容"
    # 版本指向快照对象,而非工作副本
    assert v1["file_path"] == str(snapshot)


def test_duplicate_ingest_returns_existing_entry(library, tmp_path):
    """V-02:同内容再收录 → 200 DUPLICATE_CONTENT,不复制不建档"""
    src_a = _make_source(tmp_path, "甲.txt", "相同内容")
    doc_a = _ingest(src_a).json()

    src_b = _make_source(tmp_path, "乙.txt", "相同内容")
    resp = _ingest(src_b)
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"]["code"] == "DUPLICATE_CONTENT"
    assert body["document"]["id"] == doc_a["id"]

    # 库内没有第二个工作副本,文档仍只有一条
    assert not (library / "乙.txt").exists()
    db = database.SessionLocal()
    try:
        from backend.app.models import Document
        assert db.query(Document).count() == 1
    finally:
        db.close()


def test_restore_switches_working_file_without_rewriting_history(library, tmp_path):
    """V-04:两版→恢复初版;历史版本逐版不变,恢复切换工作文件与当前版本"""
    src = _make_source(tmp_path)
    doc = _ingest(src).json()
    working = library / doc["storage_path"]
    v1 = _versions(doc["id"])[0]

    # 修改工作副本 → 建 v2
    working.write_text("第二版内容", encoding="utf-8")
    resp = client.post(f"/api/documents/{doc['id']}/versions", json={"note": "第二版"})
    assert resp.status_code == 200, resp.text
    v2 = resp.json()
    assert v2["version_number"] == 2
    assert v2["sha256_hash"] != v1["sha256_hash"]

    # 未修改再建版 → CONTENT_UNCHANGED,不产生新版本
    resp = client.post(f"/api/documents/{doc['id']}/versions", json={})
    assert resp.status_code == 200
    assert resp.json()["error"]["code"] == "CONTENT_UNCHANGED"
    assert len(_versions(doc["id"])) == 2

    # 恢复 v1 → 工作内容恢复，版本数量不增加
    resp = client.post(f"/api/documents/{doc['id']}/versions/{v1['id']}/restore")
    assert resp.status_code == 200, resp.text
    v3 = resp.json()
    assert v3["version_number"] == 1
    assert working.read_text(encoding="utf-8") == "第一版内容"
    assert len(_versions(doc["id"])) == 2
    assert v3["sha256_hash"] == v1["sha256_hash"]
    assert v3["file_path"] == v1["file_path"]

    # 历史版本逐版不变
    versions = {v["version_number"]: v for v in _versions(doc["id"])}
    assert versions[1]["sha256_hash"] == v1["sha256_hash"]
    assert versions[2]["sha256_hash"] == v2["sha256_hash"]

    # 当前版本唯一事实来源指向 v1
    db = database.SessionLocal()
    try:
        from backend.app.models import Document
        assert db.get(Document, doc["id"]).current_version_id == v3["id"]
    finally:
        db.close()

    # 导出 v1 得到初版内容;重复导出不覆写
    export_dir = tmp_path / "export"
    resp = client.post(
        f"/api/documents/{doc['id']}/versions/{v1['id']}/export",
        json={"target_dir": str(export_dir)},
    )
    assert resp.status_code == 200, resp.text
    exported = export_dir / v1["original_filename"]
    assert exported.read_text(encoding="utf-8") == "第一版内容"

    resp = client.post(
        f"/api/documents/{doc['id']}/versions/{v1['id']}/export",
        json={"target_dir": str(export_dir)},
    )
    assert resp.status_code == 500  # STORAGE_ERROR:不覆写已有文件
    assert exported.read_text(encoding="utf-8") == "第一版内容"


def test_deleted_working_copy_keeps_snapshot(library, tmp_path):
    """V-10 本地部分:删掉工作副本,快照对象与导出不受影响"""
    src = _make_source(tmp_path)
    doc = _ingest(src).json()
    working = library / doc["storage_path"]
    working.unlink()

    v1 = _versions(doc["id"])[0]
    snapshot = library / ".teamhub" / "objects" / v1["sha256_hash"][:2] / v1["sha256_hash"]
    assert snapshot.is_file()

    export_dir = tmp_path / "export"
    resp = client.post(
        f"/api/documents/{doc['id']}/versions/{v1['id']}/export",
        json={"target_dir": str(export_dir)},
    )
    assert resp.status_code == 200, resp.text
    assert (export_dir / v1["original_filename"]).read_text(encoding="utf-8") == "第一版内容"

    # 工作副本缺失时建新版 → 存储错误,不产生成功记录
    resp = client.post(f"/api/documents/{doc['id']}/versions", json={})
    assert resp.status_code == 500
    assert len(_versions(doc["id"])) == 1


def test_trash_restore_distinguishes_same_name_documents(library, tmp_path):
    """附录 B:同名资料先后进回收站,恢复时各回各的内容(按 ID 定位)"""
    dir_a = tmp_path / "a"
    dir_a.mkdir()
    src_a = _make_source(dir_a, "同名.txt", "甲的内容")
    doc_a = _ingest(src_a, name="同名").json()
    assert client.delete(f"/api/documents/{doc_a['id']}").status_code == 200

    dir_b = tmp_path / "b"
    dir_b.mkdir()
    src_b = _make_source(dir_b, "同名.txt", "乙的内容")
    doc_b = _ingest(src_b, name="同名").json()
    assert client.delete(f"/api/documents/{doc_b['id']}").status_code == 200

    # 回收站条目以文档 ID 命名
    trash = library / ".teamhub" / "trash"
    assert (trash / doc_a["id"]).exists()
    assert (trash / doc_b["id"]).exists()

    # 恢复乙 → 必须取回乙的内容
    assert client.post(f"/api/documents/{doc_b['id']}/restore").status_code == 200
    restored = library / doc_b["storage_path"]
    assert restored.read_text(encoding="utf-8") == "乙的内容"

    # 原位置被乙占用时,恢复甲 → 400 拒绝,不静默覆盖(冲突需人工处理)
    resp = client.post(f"/api/documents/{doc_a['id']}/restore")
    assert resp.status_code == 400
    assert restored.read_text(encoding="utf-8") == "乙的内容"

    # 乙再次进回收站后,甲可独立恢复且取回甲的内容
    assert client.delete(f"/api/documents/{doc_b['id']}").status_code == 200
    assert client.post(f"/api/documents/{doc_a['id']}/restore").status_code == 200
    restored_a = library / doc_a["storage_path"]
    assert restored_a.read_text(encoding="utf-8") == "甲的内容"


def test_versions_unique_index_created(library):
    """契约 §2.2:uq_versions_doc_num 唯一索引随迁移建立"""
    conn = sqlite3.connect(str(library / ".teamhub" / "db.sqlite"))
    try:
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(versions)").fetchall()}
    finally:
        conn.close()
    assert "uq_versions_doc_num" in indexes


def test_clean_trash_removes_id_named_entries(library, tmp_path):
    """M2 任务4:清空回收站按文档 ID 定位并删除物理文件 + 数据库记录"""
    src = _make_source(tmp_path, "待清理.txt", "内容甲")
    doc = _ingest(src).json()
    assert client.delete(f"/api/documents/{doc['id']}").status_code == 200

    trash_dir = library / ".teamhub" / "trash"
    assert (trash_dir / doc["id"]).exists()  # 条目按文档 ID 命名

    resp = client.post("/api/settings/trash/clean")
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] == 1

    assert not (trash_dir / doc["id"]).exists()
    db = database.SessionLocal()
    try:
        from backend.app.models import Document
        assert db.query(Document).filter(Document.id == doc["id"]).count() == 0
    finally:
        db.close()


def test_auto_cleanup_removes_id_named_trash(library, tmp_path):
    """M2 任务4:启动自动清理同样按文档 ID 定位过期回收站条目"""
    from datetime import datetime, timezone, timedelta
    from backend.app.core.trash_cleaner import cleanup_old_trash

    src = _make_source(tmp_path, "过期.txt", "内容乙")
    doc = _ingest(src).json()
    assert client.delete(f"/api/documents/{doc['id']}").status_code == 200

    trash_dir = library / ".teamhub" / "trash"
    assert (trash_dir / doc["id"]).exists()

    # 把 trashed_at 设到 31 天前,超过 30 天清理阈值
    db = database.SessionLocal()
    try:
        from backend.app.models import Document
        d = db.get(Document, doc["id"])
        d.trashed_at = datetime.now(timezone.utc) - timedelta(days=31)
        db.commit()
    finally:
        db.close()

    cleaned = cleanup_old_trash(30, str(library))
    assert cleaned == 1
    assert not (trash_dir / doc["id"]).exists()
    db = database.SessionLocal()
    try:
        from backend.app.models import Document
        assert db.query(Document).filter(Document.id == doc["id"]).count() == 0
    finally:
        db.close()


def test_quick_add_moves_and_deduplicates_history(library, tmp_path):
    src = _make_source(tmp_path)
    response = client.post('/api/documents/quick-add', json={'source_path': str(src)})
    assert response.status_code == 200, response.text
    doc = response.json()
    assert not src.exists()
    assert doc['storage_mode'] == 'move'
    v1 = _versions(doc['id'])[0]
    assert doc['current_version']['id'] == v1['id']
    working = library / doc['storage_path']
    working.write_text('第二版', encoding='utf-8')
    assert client.post(f"/api/documents/{doc['id']}/versions", json={}).status_code == 200
    src.write_text('第一版内容', encoding='utf-8')
    retry = client.post('/api/documents/quick-add', json={'source_path': str(src)}).json()
    assert retry['error']['code'] == 'DUPLICATE_CONTENT'
    assert retry['document']['id'] == doc['id']
    assert retry['version']['id'] == v1['id']
    assert src.read_text(encoding='utf-8') == '第一版内容'


@pytest.mark.parametrize('batch', [False, True])
def test_organizing_never_rewrites_snapshot(library, tmp_path, batch):
    from pathlib import Path
    doc = _ingest(_make_source(tmp_path)).json()
    v1 = _versions(doc['id'])[0]
    moved = client.post(f"/api/documents/{doc['id']}/move-to-pending").json()
    (library / moved['storage_path']).write_text('尚未保存的修改', encoding='utf-8')
    if batch:
        result = client.post('/api/documents/batch/organize', json={
            'document_ids': [doc['id']], 'target_dir': '分类', 'tags': ['设备']})
    else:
        result = client.post(f"/api/documents/{doc['id']}/organize", json={
            'target_dir': '分类', 'tags': ['设备']})
    assert result.status_code == 200, result.text
    current = _versions(doc['id'])[0]
    assert current['file_path'] == v1['file_path']
    assert current['sha256_hash'] == v1['sha256_hash']
    assert Path(current['file_path']).read_text(encoding='utf-8') == '第一版内容'


def test_purge_keeps_other_document_working_copy_and_snapshot(library, tmp_path):
    from pathlib import Path
    source = _make_source(tmp_path)
    a = _ingest(source).json()
    v1 = _versions(a['id'])[0]
    assert client.delete(f"/api/documents/{a['id']}").status_code == 200
    # 回收站资料不阻止重新收录同名同内容资料，两条记录可引用相同对象。
    b = _ingest(source).json()
    assert b['id'] != a['id']
    assert client.delete(f"/api/documents/{a['id']}?permanent=true").status_code == 200
    assert (library / b['storage_path']).read_text(encoding='utf-8') == '第一版内容'
    assert Path(v1['file_path']).read_text(encoding='utf-8') == '第一版内容'
    result = client.post(f"/api/documents/{b['id']}/versions/{b['current_version']['id']}/export",
                         json={'target_dir': str(tmp_path / 'exports')})
    assert result.status_code == 200, result.text


def test_ingest_commit_failure_cleans_working_copy(library, tmp_path, monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError
    from backend.app.models import Document
    src = _make_source(tmp_path)
    def fail_commit(self):
        raise SQLAlchemyError('injected commit failure')
    with monkeypatch.context() as patch:
        patch.setattr(database.TrackedSession, 'commit', fail_commit)
        response = TestClient(app, raise_server_exceptions=False).post('/api/documents', json={
            'name': '报告', 'source_path': str(src), 'tags': ['测试']})
        assert response.status_code == 500
    assert src.exists()
    assert not (library / src.name).exists()
    with database.SessionLocal() as db:
        assert db.query(Document).count() == 0
    assert _ingest(src).status_code == 200


def test_snapshot_placement_failure_cleans_temp(library, tmp_path, monkeypatch):
    from backend.app.services import snapshots
    src = _make_source(tmp_path)
    def fail_place(*args, **kwargs):
        raise OSError('injected disk error')
    monkeypatch.setattr(snapshots.os, 'replace', fail_place)
    with pytest.raises(snapshots.SnapshotError):
        snapshots.store_file_snapshot(library, src)
    assert list((library / '.teamhub/objects/tmp').iterdir()) == []
    assert src.exists()


def test_undo_move_restores_source(library, tmp_path):
    src = _make_source(tmp_path)
    doc = client.post('/api/documents/quick-add', json={'source_path': str(src)}).json()
    result = client.post(f"/api/documents/{doc['id']}/undo-pending")
    assert result.status_code == 200, result.text
    assert src.read_text(encoding='utf-8') == '第一版内容'
    assert not (library / doc['storage_path']).exists()
    assert client.get(f"/api/documents/{doc['id']}").status_code == 404


@pytest.mark.parametrize('operation', ['pending', 'trash', 'purge', 'organize'])
def test_working_moves_rollback_on_commit_failure(library, tmp_path, monkeypatch, operation):
    from sqlalchemy.exc import SQLAlchemyError
    doc = _ingest(_make_source(tmp_path)).json()
    if operation == 'organize':
        doc = client.post(f"/api/documents/{doc['id']}/move-to-pending").json()
    before = (library / doc['storage_path']).read_bytes()
    def fail_commit(self):
        raise SQLAlchemyError('injected commit failure')
    with monkeypatch.context() as patch:
        patch.setattr(database.TrackedSession, 'commit', fail_commit)
        if operation == 'pending':
            result = client.post(f"/api/documents/{doc['id']}/move-to-pending")
        elif operation in {'trash', 'purge'}:
            result = client.delete(f"/api/documents/{doc['id']}", params={'permanent': operation == 'purge'})
        else:
            result = client.post(f"/api/documents/{doc['id']}/organize", json={'target_dir': '分类', 'tags': ['设备']})
        assert result.status_code == 500, result.text
    assert (library / doc['storage_path']).read_bytes() == before
    actual = client.get(f"/api/documents/{doc['id']}").json()
    assert actual['status'] == doc['status']
    assert actual['storage_path'] == doc['storage_path']
    assert len(_versions(doc['id'])) == 1


def test_repeated_trash_and_batch_restore_collision(library, tmp_path):
    doc = _ingest(_make_source(tmp_path)).json()
    for _ in range(2):
        assert client.delete(f"/api/documents/{doc['id']}").status_code == 200
    assert client.get(f"/api/documents/{doc['id']}").json()['status'] == 'trashed'
    working = library / doc['storage_path']
    working.write_text('另一个文件', encoding='utf-8')
    result = client.post('/api/documents/batch/restore', json={'document_ids': [doc['id']]})
    assert result.status_code == 400, result.text
    assert working.read_text(encoding='utf-8') == '另一个文件'
    assert client.get(f"/api/documents/{doc['id']}").json()['status'] == 'trashed'
    assert (library / '.teamhub/trash' / doc['id']).exists()


def test_scan_keeps_root_level_snapshot_paths(library, tmp_path):
    doc = _ingest(_make_source(tmp_path)).json()
    original = _versions(doc['id'])[0]
    destination = library / '新目录'
    destination.mkdir()
    (library / doc['storage_path']).rename(destination / '报告.txt')
    assert client.post('/api/settings/scan').status_code == 200
    assert _versions(doc['id'])[0]['file_path'] == original['file_path']
    assert client.get(f"/api/documents/{doc['id']}").json()['storage_path'] == '新目录/报告.txt'


def test_legacy_trash_names_never_selected_by_filename(library, tmp_path):
    doc = _ingest(_make_source(tmp_path)).json()
    assert client.delete(f"/api/documents/{doc['id']}").status_code == 200
    trash = library / '.teamhub/trash'
    unrelated = trash / '123456_报告.txt'
    unrelated.write_text('归属未知，必须保留', encoding='utf-8')
    assert client.delete(f"/api/documents/{doc['id']}?permanent=true").status_code == 200
    assert unrelated.read_text(encoding='utf-8') == '归属未知，必须保留'


def test_snapshot_rejects_same_size_corrupted_object(library, tmp_path):
    from backend.app.services import snapshots
    source = _make_source(tmp_path, content='first')
    key, _, _ = snapshots.store_file_snapshot(library, source)
    snapshots.resolve_object(library, key).write_text('other')
    with pytest.raises(snapshots.SnapshotError, match='校验失败'):
        snapshots.store_file_snapshot(library, source)
    assert list((library / '.teamhub/objects/tmp').iterdir()) == []


def test_export_failure_leaves_no_partial_target(library, tmp_path, monkeypatch):
    from backend.app.services import snapshots
    doc = _ingest(_make_source(tmp_path)).json()
    target = tmp_path / '导出'
    def partial_copy(source, destination, *args):
        destination.write(b'partial')
        raise OSError('disk full')
    with monkeypatch.context() as patch:
        patch.setattr(snapshots.shutil, 'copyfileobj', partial_copy)
        result = client.post(f"/api/documents/{doc['id']}/versions/{doc['current_version']['id']}/export",
                             json={'target_dir': str(target)})
        assert result.status_code == 500
    assert list(target.iterdir()) == []
    assert client.post(f"/api/documents/{doc['id']}/versions/{doc['current_version']['id']}/export",
                       json={'target_dir': str(target)}).status_code == 200


def test_snapshot_rejects_source_changed_during_read(library, tmp_path, monkeypatch):
    import builtins
    from backend.app.services import snapshots
    source = _make_source(tmp_path, content='first')
    real_open = builtins.open
    class ChangingReader:
        def __init__(self, file):
            self.file = file
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.file.close()
        def fileno(self):
            return self.file.fileno()
        def read(self, size):
            data = self.file.read(size)
            if data:
                with real_open(source, 'wb') as writer:
                    writer.write(b'other')
            return data
    def open_with_change(path, mode='r', *args, **kwargs):
        file = real_open(path, mode, *args, **kwargs)
        return ChangingReader(file) if path == source and mode == 'rb' else file
    monkeypatch.setattr(builtins, 'open', open_with_change)
    with pytest.raises(snapshots.SnapshotError, match='发生变化'):
        snapshots.store_file_snapshot(library, source)
    assert list((library / '.teamhub/objects/tmp').iterdir()) == []


def test_quick_move_commit_failure_restores_source(library, tmp_path, monkeypatch):
    from sqlalchemy.orm import Session
    src = _make_source(tmp_path)
    def fail_commit(self):
        raise RuntimeError('injected commit failure')
    monkeypatch.setattr(Session, 'commit', fail_commit)
    response = client.post('/api/documents/quick-add', json={'source_path': str(src)})
    assert response.status_code == 500
    assert src.read_text(encoding='utf-8') == '第一版内容'
    assert not (library / settings.pending_folder_name / src.name).exists()


def test_undo_move_does_not_overwrite_new_source(library, tmp_path):
    src = _make_source(tmp_path)
    doc = client.post('/api/documents/quick-add', json={'source_path': str(src)}).json()
    src.write_text('新文件', encoding='utf-8')
    response = client.post(f"/api/documents/{doc['id']}/undo-pending")
    assert response.status_code == 400
    assert src.read_text(encoding='utf-8') == '新文件'
    assert (library / doc['storage_path']).read_text(encoding='utf-8') == '第一版内容'
    assert client.get(f"/api/documents/{doc['id']}").status_code == 200


def test_quick_move_folder_and_undo(library, tmp_path):
    folder = tmp_path / '移动文件夹'
    folder.mkdir()
    (folder / '.hidden').write_text('隐藏内容')
    (folder / 'empty').mkdir()
    response = client.post('/api/documents/quick-add', json={'source_path': str(folder)})
    assert response.status_code == 200, response.text
    doc = response.json()
    assert not folder.exists()
    assert (library / doc['storage_path'] / '.hidden').read_text() == '隐藏内容'
    assert client.post(f"/api/documents/{doc['id']}/undo-pending").status_code == 200
    assert (folder / '.hidden').read_text() == '隐藏内容'
    assert (folder / 'empty').is_dir()


@pytest.mark.parametrize('pending', [False, True])
@pytest.mark.parametrize('folder', [False, True])
def test_restore_protects_unsaved_content_and_keeps_document_relations(library, tmp_path, pending, folder):
    source = tmp_path / ('source-folder' if folder else 'source.txt')
    if folder:
        source.mkdir()
        (source / 'empty').mkdir()
        (source / '.hidden').write_text('keep-hidden')
        (source / 'content.txt').write_text('v1')
    else:
        source.write_text('v1')
    response = (client.post('/api/documents/quick-add', json={'source_path': str(source)})
                if pending else _ingest(source))
    assert response.status_code == 200, response.text
    doc = response.json()
    work = Path(doc['working_path'])
    content = work / 'content.txt' if folder else work
    base = f"/api/documents/{doc['id']}/versions"
    v1 = client.get(base).json()[0]
    content.write_text('v2-saved')
    v2 = client.post(base, json={}).json()
    content.write_text('v3-unsaved')
    if not pending:
        assert client.post('/api/favorites', json={'target_type': 'local', 'local_document_id': doc['id']}).status_code in (200, 201)
    result = client.post(base + '/' + v1['id'] + '/restore')
    assert result.status_code == 200, result.text
    assert result.json()['id'] == v1['id']
    assert content.read_text() == 'v1'
    versions = client.get(base).json()
    assert len(versions) == 3
    protection = next(v for v in versions if v['version_number'] == 3)
    assert '自动保存' in protection['note']
    assert [v['id'] for v in versions if v['is_current']] == [v1['id']]
    assert next(v for v in versions if v['id'] == v2['id'])['sha256_hash'] == v2['sha256_hash']
    exported = tmp_path / 'protected-export'
    export = client.post(base + '/' + protection['id'] + '/export', json={'target_dir': str(exported)})
    assert export.status_code == 200, export.text
    protected_path = Path(export.json()['path'])
    assert (protected_path / 'content.txt' if folder else protected_path).read_text() == 'v3-unsaved'
    after = client.get(f"/api/documents/{doc['id']}").json()
    for field in ('id', 'status', 'storage_path', 'storage_area', 'tags'):
        assert after[field] == doc[field]
    assert client.get(base + '/working-state').json()['state'] == 'unchanged'
    if folder:
        assert (work / 'empty').is_dir()
        assert (work / '.hidden').read_text() == 'keep-hidden'
    if not pending:
        assert any(f['local_document_id'] == doc['id'] for f in client.get('/api/favorites').json())


@pytest.mark.parametrize('failure', ['commit', 'replace'])
def test_restore_failure_preserves_unsaved_work_and_current_version(library, tmp_path, monkeypatch, failure):
    from sqlalchemy.orm import Session
    from backend.app.services import versions as service
    doc = _ingest(_make_source(tmp_path)).json()
    work = library / doc['storage_path']
    v1 = _versions(doc['id'])[0]
    work.write_text('unsaved-important')
    if failure == 'commit':
        def fail_commit(self):
            raise RuntimeError('synthetic commit error')
        monkeypatch.setattr(Session, 'commit', fail_commit)
    else:
        rename = service.os.rename
        def fail_replace(src, dest):
            if 'ready' in Path(src).parts:
                raise OSError('synthetic replace error')
            return rename(src, dest)
        monkeypatch.setattr(service.os, 'rename', fail_replace)
    result = client.post(f"/api/documents/{doc['id']}/versions/{v1['id']}/restore")
    assert result.status_code == 500
    assert work.read_text() == 'unsaved-important'
    assert len(_versions(doc['id'])) == 1
    assert _versions(doc['id'])[0]['is_current']
    assert not list(work.parent.glob('.teamhub-restore-*'))


def test_restore_corrupt_snapshot_does_not_touch_work(library, tmp_path):
    doc = _ingest(_make_source(tmp_path)).json()
    work = library / doc['storage_path']
    v1 = _versions(doc['id'])[0]
    work.write_text('unsaved-important')
    Path(v1['file_path']).write_text('damaged')
    result = client.post(f"/api/documents/{doc['id']}/versions/{v1['id']}/restore")
    assert result.status_code == 500
    assert work.read_text() == 'unsaved-important'
    assert len(_versions(doc['id'])) == 1


def test_restore_recreates_missing_work_file_without_new_version(library, tmp_path):
    doc = _ingest(_make_source(tmp_path)).json()
    work = library / doc['storage_path']
    v1 = _versions(doc['id'])[0]
    work.unlink()
    result = client.post(f"/api/documents/{doc['id']}/versions/{v1['id']}/restore")
    assert result.status_code == 200, result.text
    assert work.read_text() == '第一版内容'
    assert len(_versions(doc['id'])) == 1


def test_delete_history_preserves_current_work_and_other_versions(library, tmp_path):
    doc = _ingest(_make_source(tmp_path)).json()
    work = library / doc['storage_path']
    base = f"/api/documents/{doc['id']}/versions"
    v1 = _versions(doc['id'])[0]
    work.write_text('v2')
    v2 = client.post(base, json={}).json()
    assert client.delete(base + '/' + v2['id']).status_code == 409
    deleted = client.delete(base + '/' + v1['id'])
    assert deleted.status_code == 200, deleted.text
    assert work.read_text() == 'v2'
    assert [v['id'] for v in _versions(doc['id'])] == [v2['id']]
    assert _versions(doc['id'])[0]['is_current']
    assert Path(v1['file_path']).exists()
    assert client.delete(base + '/' + v1['id']).status_code == 404
    assert client.post(base + '/' + v1['id'] + '/restore').status_code == 404


def test_delete_latest_history_never_reuses_version_number(library, tmp_path):
    doc = _ingest(_make_source(tmp_path)).json()
    work = library / doc['storage_path']
    base = f"/api/documents/{doc['id']}/versions"
    v1 = _versions(doc['id'])[0]
    work.write_text('v2')
    v2 = client.post(base, json={}).json()
    assert client.post(base + '/' + v1['id'] + '/restore').status_code == 200
    assert client.delete(base + '/' + v2['id']).status_code == 200
    database.init_db()  # 重启迁移也不能把序号计数回退。
    work.write_text('v3')
    v3 = client.post(base, json={}).json()
    assert v3['version_number'] == 3


def test_delete_version_rejects_wrong_document(library, tmp_path):
    first = _ingest(_make_source(tmp_path, 'first.txt', 'one')).json()
    second = _ingest(_make_source(tmp_path, 'second.txt', 'two')).json()
    version = _versions(first['id'])[0]
    result = client.delete(f"/api/documents/{second['id']}/versions/{version['id']}")
    assert result.status_code == 404
    assert len(_versions(first['id'])) == 1


def test_delete_version_commit_failure_keeps_history(library, tmp_path, monkeypatch):
    from sqlalchemy.orm import Session
    doc = _ingest(_make_source(tmp_path)).json()
    work = library / doc['storage_path']
    base = f"/api/documents/{doc['id']}/versions"
    v1 = _versions(doc['id'])[0]
    work.write_text('v2')
    client.post(base, json={})
    def fail_commit(self):
        raise RuntimeError('synthetic delete commit failure')
    monkeypatch.setattr(Session, 'commit', fail_commit)
    response = TestClient(app, raise_server_exceptions=False).delete(base + '/' + v1['id'])
    assert response.status_code == 500
    assert len(_versions(doc['id'])) == 2
    assert work.read_text() == 'v2'
    assert Path(v1['file_path']).exists()
