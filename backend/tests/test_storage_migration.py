from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from main import app
from backend.app import database
from backend.app.config import settings
from backend.app.services import storage_migration as migration
from backend.app.services import versions
from backend.app.models import Document, Version

client = TestClient(app)

@pytest.fixture
def library(tmp_path, monkeypatch):
    old = settings.library_path, settings.pending_path
    settings.library_path, settings.pending_path = str(tmp_path / 'library'), None
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(migration, 'bootstrap_path', lambda: tmp_path / 'bootstrap.json')
    database.rebind_engine()
    database.init_db()
    yield Path(settings.library_path)
    settings.library_path, settings.pending_path = old
    database.rebind_engine()


def ingest(tmp_path, name, organized=False, folder=False):
    source = tmp_path / name
    if folder:
        (source / '子目录').mkdir(parents=True)
        (source / '子目录/图纸.txt').write_text(name)
        (source / '.hidden').write_text('隐藏资料')
        (source / '空目录').mkdir()
    else:
        source.write_text(name)
    doc = client.post('/api/documents/quick-add', json={'source_path': str(source)}).json()
    assert 'id' in doc, doc
    if organized:
        response = client.post(f"/api/documents/{doc['id']}/organize", json={'tags': ['设备', '检修']})
        assert response.status_code == 200, response.text
        doc = response.json()
    return doc


def run(area, path):
    response = client.post('/api/settings/storage/migrate', json={'area': area, 'path': str(path)})
    assert response.status_code == 200, response.text
    assert response.json()['phase'] == 'complete', response.text
    return response.json()


def test_pending_independent_then_organize_and_restore(library, tmp_path):
    doc = ingest(tmp_path, '待整理.txt')
    other = ingest(tmp_path, '已整理.txt', True)
    old = Path(doc['working_path'])
    unknown = old.parent / '未收录.txt'
    unknown.write_text('不可移动')
    destination = tmp_path / '独立待整理'
    run('pending', destination)
    current = client.get(f"/api/documents/{doc['id']}").json()
    assert Path(current['working_path']) == destination / '待整理.txt'
    assert Path(current['working_path']).exists() and not old.exists()
    assert unknown.read_text() == '不可移动'
    assert Path(other['working_path']).exists()
    saved = client.post(f"/api/documents/{doc['id']}/versions", json={})
    assert saved.status_code == 200, saved.text
    response = client.post(f"/api/documents/{doc['id']}/organize", json={'tags': ['自由标签']})
    assert response.status_code == 200, response.text
    assert Path(response.json()['working_path']) == library / '待整理.txt'
    response = client.post(f"/api/documents/{doc['id']}/move-to-pending")
    assert response.status_code == 200, response.text
    client.delete(f"/api/documents/{doc['id']}")
    assert client.post(f"/api/documents/{doc['id']}/restore").status_code == 200
    assert (destination / '待整理.txt').exists()


@pytest.mark.parametrize('layout', ['separate', 'library_in_pending', 'pending_in_library'])
def test_library_migration_keeps_identity_versions_and_other_region(library, tmp_path, layout):
    pending = ingest(tmp_path, '待整理.txt')
    doc = ingest(tmp_path, '成套资料', True, True)
    unknown = library / '不属于系统.txt'
    unknown.write_text('不可移动')
    old_pending = Path(pending['working_path'])
    if layout == 'separate':
        destination = tmp_path / '新库'
    elif layout == 'library_in_pending':
        destination = old_pending.parent / '新库'
    else:
        destination = tmp_path
    before = client.get(f"/api/documents/{doc['id']}/versions").json()
    run('library', destination)
    after = client.get(f"/api/documents/{doc['id']}").json()
    assert after['id'] == doc['id'] and after['current_version']['id'] == doc['current_version']['id']
    assert {t['name'] for t in after['tags']} == {'设备', '检修'}
    assert (destination / '成套资料/.hidden').read_text() == '隐藏资料'
    assert old_pending.exists() and unknown.exists()
    assert not (library / '成套资料').exists()
    assert not (library / '.teamhub/db.sqlite').exists()
    with database.SessionLocal() as db:
        v = db.get(Version, before[0]['id'])
        assert Path(v.file_path).exists()
        assert Path(v.file_path).is_relative_to(destination / '.teamhub')
    exported = client.post(f"/api/documents/{doc['id']}/versions/{before[0]['id']}/export", json={'target_dir': str(tmp_path / '导出')})
    assert exported.status_code == 200, exported.text
    config = database.load_library_config()
    assert config['pending_path'] == str(old_pending.parent)


def test_conflict_missing_and_folder_root_are_rejected(library, tmp_path):
    doc = ingest(tmp_path, '文件.txt')
    destination = tmp_path / 'new'
    destination.mkdir()
    (destination / '文件.txt').write_text('目标不能覆盖')
    assert client.post('/api/settings/storage/migrate', json={'area': 'pending', 'path': str(destination)}).status_code == 409
    assert Path(doc['working_path']).exists()
    assert (destination / '文件.txt').read_text() == '目标不能覆盖'
    folder = ingest(tmp_path, '完整项目', True, True)
    assert client.post('/api/settings/storage/preview', json={'area': 'pending', 'path': str(Path(folder['working_path']) / '收件箱')}).status_code == 409


def test_copy_failure_retains_sources_and_recovery_unblocks(library, tmp_path, monkeypatch):
    doc = ingest(tmp_path, '重要资料.txt')
    original = migration._copy
    def fail(entry, journal):
        original(entry, journal)
        raise OSError('模拟断电前中断')
    monkeypatch.setattr(migration, '_copy', fail)
    response = client.post('/api/settings/storage/migrate', json={'area': 'pending', 'path': str(tmp_path / 'new')})
    assert response.status_code == 500
    assert Path(doc['working_path']).exists()
    assert settings.pending_path is None
    assert client.post(f"/api/documents/{doc['id']}/organize", json={'tags': ['设备']}).status_code == 409
    response = client.post('/api/settings/storage/recover')
    assert response.status_code == 200, response.text
    assert response.json()['phase'] == 'cancelled'
    assert Path(doc['working_path']).exists()
    assert (tmp_path / 'new/重要资料.txt').exists()


def test_cleanup_failure_recovers_after_switch(library, tmp_path, monkeypatch):
    doc = ingest(tmp_path, '资料.txt')
    original = migration._remove_recorded
    def fail(*args):
        raise OSError('清理中断')
    monkeypatch.setattr(migration, '_remove_recorded', fail)
    response = client.post('/api/settings/storage/migrate', json={'area': 'pending', 'path': str(tmp_path / 'new')})
    assert response.status_code == 200 and response.json()['phase'] == 'cleanup'
    assert settings.pending_path == str(tmp_path / 'new')
    monkeypatch.setattr(migration, '_remove_recorded', original)
    response = client.post('/api/settings/storage/recover')
    assert response.status_code == 200 and response.json()['phase'] == 'complete', response.text
    assert not Path(doc['working_path']).exists()
    assert (tmp_path / 'new/资料.txt').exists()


def test_library_switch_failure_keeps_old_database_and_bootstrap(library, tmp_path, monkeypatch):
    doc = ingest(tmp_path, '主变.txt', True)
    original = database.write_json_config
    def fail_bootstrap(path, data):
        if Path(path).name == 'bootstrap.json':
            raise OSError('配置写入失败')
        return original(path, data)
    monkeypatch.setattr(database, 'write_json_config', fail_bootstrap)
    response = client.post('/api/settings/storage/migrate', json={'area': 'library', 'path': str(tmp_path / 'new')})
    assert response.status_code == 500
    assert settings.library_path == str(library)
    assert Path(doc['working_path']).exists()
    assert client.get(f"/api/documents/{doc['id']}").json()['id'] == doc['id']
    assert client.post('/api/settings/storage/recover').json()['phase'] == 'cancelled'


def test_new_destination_change_keeps_old_source(library, tmp_path, monkeypatch):
    doc = ingest(tmp_path, '资料.txt')
    original = migration._finish
    def change_before_cleanup(journal):
        Path(journal['entries'][0]['target']).write_text('迁移中被外部修改')
        return original(journal)
    monkeypatch.setattr(migration, '_finish', change_before_cleanup)
    response = client.post('/api/settings/storage/migrate', json={'area': 'pending', 'path': str(tmp_path / 'new')})
    assert response.status_code == 200 and response.json()['phase'] == 'cleanup'
    assert Path(doc['working_path']).read_text() == '资料.txt'


def test_old_entry_redirect_after_two_moves(library, tmp_path):
    doc = ingest(tmp_path, '文件.txt', True)
    run('library', tmp_path / 'second')
    run('library', tmp_path / 'third')
    assert migration.follow_library_redirect(library) == str(tmp_path / 'third')
    assert client.get(f"/api/documents/{doc['id']}").json()['id'] == doc['id']
