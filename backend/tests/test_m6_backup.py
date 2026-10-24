"""
M6 备份/恢复行为测试(V-15,契约 §3.7)

- 备份涵盖 SQLite 一致性备份 + 快照对象 + 脱敏配置 + 清单;
- 恢复至全新空目录,数据库摘要与清单一致;
- 历史版本可取回(导出得到历史内容);
- 配置备份排除凭据(cloud_access_token / cloud_refresh_token)。
"""
import pytest
from fastapi.testclient import TestClient

import backend.app.database as database
from backend.app.config import settings
from backend.app.core import backup
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


def _make_source(tmp_path, name, content):
    src = tmp_path / name
    src.write_text(content, encoding="utf-8")
    return src


def _ingest(source):
    resp = client.post("/api/documents", json={
        "name": source.stem, "source_path": str(source), "tags": ["备份"],
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


def _versions(doc_id):
    resp = client.get(f"/api/documents/{doc_id}/versions")
    assert resp.status_code == 200
    return resp.json()


def test_backup_restore_roundtrip(library, tmp_path):
    # 建库:一份资料,两个版本 + 标签
    src = _make_source(tmp_path, "报告.txt", "第一版内容")
    doc = _ingest(src)
    working = library / doc["storage_path"]
    working.write_text("第二版内容", encoding="utf-8")
    client.post(f"/api/documents/{doc['id']}/versions", json={"note": "v2"})
    v1 = _versions(doc["id"])[1]  # 版本号倒序,v1 在末尾

    # 写入凭据(模拟登录)
    config = database.load_library_config()
    config["cloud_access_token"] = "secret-access"
    config["cloud_refresh_token"] = "secret-refresh"
    database.save_library_config(config)

    # 备份
    backup_dir = tmp_path / "backup"
    manifest = backup.backup_library(str(library), str(backup_dir))
    assert manifest["object_count"] >= 2  # v1 + v2 两个快照对象
    assert backup.backup_is_redacted(str(backup_dir))  # 凭据已排除

    # 恢复至全新空目录
    target = tmp_path / "restored"
    result = backup.restore_library(str(backup_dir), str(target))
    assert result["verify"]["documents"] == 1
    assert result["verify"]["versions"] == 2

    # 恢复后的库可重新挂载,历史版本可取回
    settings.library_path = str(target)
    database.rebind_engine()
    database.init_db()

    versions = _versions(doc["id"])
    assert len(versions) == 2
    v1_restored = versions[1]

    export_dir = tmp_path / "export"
    resp = client.post(
        f"/api/documents/{doc['id']}/versions/{v1_restored['id']}/export",
        json={"target_dir": str(export_dir)},
    )
    assert resp.status_code == 200, resp.text
    assert (export_dir / v1_restored["original_filename"]).read_text(encoding="utf-8") == "第一版内容"

    # 恢复后的 config 不含凭据
    restored_config = database.load_library_config()
    assert "cloud_access_token" not in restored_config
    assert "cloud_refresh_token" not in restored_config


def test_restore_rejects_non_backup_dir(tmp_path):
    """非备份目录(无清单)应明确失败"""
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        backup.restore_library(str(empty), str(tmp_path / "target"))


def test_backup_preserves_working_changes_and_rejects_overwrite(library, tmp_path):
    doc = _ingest(_make_source(tmp_path, 'working.txt', '已保存'))
    (library / doc['storage_path']).write_text('尚未保存的工作修改')
    dest = tmp_path / 'backup'
    backup.backup_library(library, dest)
    target = tmp_path / 'restored'
    backup.restore_library(dest, target)
    assert (target / doc['storage_path']).read_text() == '尚未保存的工作修改'
    sentinel = target / '不可覆盖.txt'
    sentinel.write_text('原有资料')
    with pytest.raises((ValueError, FileExistsError)):
        backup.restore_library(dest, target)
    assert sentinel.read_text() == '原有资料'


def test_restore_rejects_corrupt_object_before_target_write(library, tmp_path):
    _ingest(_make_source(tmp_path, 'corrupt.txt', 'content'))
    dest = tmp_path / 'backup'
    backup.backup_library(library, dest)
    obj = next(p for p in (dest / 'objects').rglob('*') if p.is_file())
    obj.write_bytes(b'corrupted')
    target = tmp_path / 'restored'
    with pytest.raises((ValueError, RuntimeError)):
        backup.restore_library(dest, target)
    assert not target.exists()


@pytest.mark.parametrize('damage', ['missing', 'extra', 'config', 'kind', 'symlink'])
def test_backup_damage_is_rejected_without_partial_restore(library, tmp_path, damage):
    import json
    _ingest(_make_source(tmp_path, 'data.txt', '完整内容'))
    dest = tmp_path / 'backup'
    backup.backup_library(library, dest)
    if damage == 'missing':
        next(p for p in (dest / 'objects').rglob('*') if p.is_file()).unlink()
    elif damage == 'extra':
        (dest / 'unexpected.txt').write_text('额外内容')
    elif damage == 'config':
        (dest / 'config.json').write_text('{} ')
    elif damage == 'kind':
        path = dest / 'manifest.json'
        data = json.loads(path.read_text()); data['kind'] = 'cloud'
        path.write_text(json.dumps(data))
    else:
        (dest / 'escape').symlink_to(tmp_path)
    target = tmp_path / 'restored'
    with pytest.raises((ValueError, RuntimeError)):
        backup.restore_library(dest, target)
    assert not target.exists()


def test_backup_rejects_overlap_reuse_and_source_symlink(library, tmp_path):
    _ingest(_make_source(tmp_path, 'data.txt', '资料'))
    with pytest.raises(ValueError):
        backup.backup_library(library, library / 'backup')
    dest = tmp_path / 'backup'
    backup.backup_library(library, dest)
    original = (dest / 'manifest.json').read_bytes()
    with pytest.raises(FileExistsError):
        backup.backup_library(library, dest)
    assert (dest / 'manifest.json').read_bytes() == original
    (library / '外部链接').symlink_to(tmp_path)
    with pytest.raises(ValueError):
        backup.backup_library(library, tmp_path / 'invalid-backup')
    assert not (tmp_path / 'invalid-backup').exists()


def test_backup_retains_folders_trash_favorites_and_omits_secrets(library, tmp_path):
    import json
    source = tmp_path / 'folder'; source.mkdir()
    (source / 'empty').mkdir(); (source / '说明.txt').write_text('第一版')
    doc = _ingest(source)
    favorite = client.post('/api/favorites', json={'target_type': 'local', 'local_document_id': doc['id']}).json()
    assert client.delete('/api/documents/' + doc['id']).status_code == 200
    config = dict(cloud_access_token='access-secret', cloud_refresh_token='refresh-secret',
                  nested={'password': 'secret'}, future_token='secret', cloud_api_url='http://127.0.0.1:9000')
    database.save_library_config(config)
    dest = tmp_path / 'backup'
    backup.backup_library(library, dest)
    assert json.loads((dest / 'config.json').read_text()) == {'cloud_api_url': 'http://127.0.0.1:9000'}
    target = tmp_path / 'restored'
    backup.restore_library(dest, target)
    settings.library_path = str(target); database.rebind_engine(); database.init_db()
    # 收藏引用随备份保留，但回收站资料不属于已整理集合，恢复前隐藏。
    assert client.get('/api/favorites').json() == []
    restored = client.post('/api/documents/' + doc['id'] + '/restore')
    assert restored.status_code == 200, restored.text
    assert client.get('/api/favorites').json()[0]['id'] == favorite['id']
    working = target / doc['storage_path']
    assert (working / '说明.txt').read_text() == '第一版' and (working / 'empty').is_dir()


def test_copy_failure_leaves_no_published_backup(library, tmp_path, monkeypatch):
    _ingest(_make_source(tmp_path, 'data.txt', 'content'))
    def fail(*args, **kwargs):
        raise OSError('模拟磁盘写入失败')
    monkeypatch.setattr(backup.shutil, 'copytree', fail)
    with pytest.raises(OSError):
        backup.backup_library(library, tmp_path / 'backup')
    assert not (tmp_path / 'backup').exists()
    assert not list(tmp_path.glob('.backup-partial-*'))


def _cloud_source(tmp_path):
    import sqlite3
    import hashlib
    storage = tmp_path / 'storage'; storage.mkdir()
    content = b'shared content'
    digest = hashlib.sha256(content).hexdigest()
    obj = storage / 'objects' / digest[:2] / digest
    obj.parent.mkdir(parents=True); obj.write_bytes(content)
    db = tmp_path / 'cloud.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE cloud_versions(id TEXT, storage_key TEXT, content_hash TEXT, manifest_hash TEXT)')
        conn.execute('INSERT INTO cloud_versions VALUES (?, ?, ?, NULL)', ('v1', f'objects/{digest[:2]}/{digest}', digest))
        conn.execute('CREATE TABLE refresh_tokens(token_hash TEXT)')
        conn.execute("INSERT INTO refresh_tokens VALUES ('session-secret')")
    return db, storage


def test_cloud_backup_restore_verifies_content_and_preserves_source(tmp_path):
    import sqlite3
    db, storage = _cloud_source(tmp_path)
    dest = tmp_path / 'backup'
    backup.backup_cloud(db, storage, dest)
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM refresh_tokens').fetchone()[0] == 1
    target_db = tmp_path / 'new' / 'cloud.db'; target_storage = tmp_path / 'new' / 'storage'
    result = backup.restore_cloud(dest, target_db, target_storage)
    assert result['restored'] and result['object_count'] == 1
    with sqlite3.connect(target_db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM refresh_tokens').fetchone()[0] == 0
        assert conn.execute('SELECT id FROM cloud_versions').fetchone()[0] == 'v1'
    with pytest.raises(FileExistsError):
        backup.restore_cloud(dest, target_db, target_storage)
    next(p for p in (dest / 'objects').rglob('*') if p.is_file()).write_bytes(b'bad')
    with pytest.raises(ValueError):
        backup.restore_cloud(dest, tmp_path / 'bad.db', tmp_path / 'bad-storage')
    assert not (tmp_path / 'bad.db').exists() and not (tmp_path / 'bad-storage').exists()


def test_cloud_restore_publish_failure_cleans_own_output(tmp_path, monkeypatch):
    db, storage = _cloud_source(tmp_path)
    dest = tmp_path / 'backup'; backup.backup_cloud(db, storage, dest)
    def fail(*args):
        raise OSError('模拟排他数据库发布失败')
    monkeypatch.setattr(backup.os, 'link', fail)
    with pytest.raises(OSError):
        backup.restore_cloud(dest, tmp_path / 'new.db', tmp_path / 'new-storage')
    assert not (tmp_path / 'new.db').exists() and not (tmp_path / 'new-storage').exists()


def test_cloud_backup_preserves_delivery_acl_and_identity(tmp_path):
    import sqlite3
    db, storage = _cloud_source(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE delivery_items(id TEXT, storage_key TEXT, content_hash TEXT, manifest_hash TEXT)')
        conn.execute('INSERT INTO delivery_items SELECT * FROM cloud_versions')
        conn.execute('DELETE FROM cloud_versions')
        conn.execute('CREATE TABLE server_metadata(server_id TEXT)')
        conn.execute("INSERT INTO server_metadata VALUES ('original-service')")
        conn.execute('CREATE TABLE document_access(document_id TEXT, user_id TEXT, role TEXT)')
        conn.execute("INSERT INTO document_access VALUES ('doc', 'B', 'reader')")
    dest = tmp_path / 'delivery-backup'
    backup.backup_cloud(db, storage, dest)
    target_db = tmp_path / 'restored.db'
    backup.restore_cloud(dest, target_db, tmp_path/'restored-objects')
    with sqlite3.connect(target_db) as conn:
        assert conn.execute('SELECT server_id FROM server_metadata').fetchone()[0] == 'original-service'
        assert conn.execute('SELECT user_id FROM document_access').fetchone()[0] == 'B'
        assert conn.execute('SELECT COUNT(*) FROM delivery_items').fetchone()[0] == 1
    next(p for p in (storage/'objects').rglob('*') if p.is_file()).unlink()
    with pytest.raises(ValueError, match='对象缺失'):
        backup.backup_cloud(db, storage, tmp_path/'incomplete')


def test_wal_rows_and_missing_referenced_objects_are_not_silently_lost(library, tmp_path):
    import sqlite3
    doc = _ingest(_make_source(tmp_path, 'wal.txt', '原始内容'))
    db_path = library / '.teamhub/db.sqlite'
    with sqlite3.connect(db_path) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute('UPDATE documents SET description=? WHERE id=?', ('WAL 中的已提交修改', doc['id']))
        writer.commit()
        backup.backup_library(library, tmp_path / 'wal-backup')
    target = tmp_path / 'wal-restored'
    backup.restore_library(tmp_path / 'wal-backup', target)
    with sqlite3.connect(target / '.teamhub/db.sqlite') as conn:
        assert conn.execute('SELECT description FROM documents WHERE id=?', (doc['id'],)).fetchone()[0] == 'WAL 中的已提交修改'
    next(p for p in (library / '.teamhub/objects').rglob('*') if p.is_file()).unlink()
    with pytest.raises(ValueError, match='对象缺失'):
        backup.backup_library(library, tmp_path / 'missing-backup')
    assert not (tmp_path / 'missing-backup').exists()
