"""C5 behaviour tests use isolated libraries and synthetic contents only."""
import json
from pathlib import Path

import pytest

import backend.app.database as database
from backend.app.config import settings
from backend.app.models import Document, RemoteOperation, ShareLink, Version, VersionSource
from backend.app.services import remote_update, snapshots, versions


@pytest.fixture
def local(tmp_path, monkeypatch):
    library = tmp_path / 'library'
    library.mkdir()
    monkeypatch.setattr(settings, 'library_path', str(library))
    monkeypatch.setattr(settings, 'pending_path', '')
    database.rebind_engine()
    database.init_db()
    db = database.SessionLocal()
    yield library, db
    db.close()


def _write(path, text, folder=False):
    if folder:
        path.mkdir(exist_ok=True)
        (path / 'content.txt').write_text(text)
    else:
        path.write_text(text)


def _document(local, content='a', folder=False):
    library, db = local
    path = library / ('folder' if folder else 'report.txt')
    _write(path, content, folder)
    doc = Document(name='Original name', storage_path=path.name, status='pending',
                   storage_area='library', storage_mode='copy', is_folder=folder,
                   doc_type='folder' if folder else 'file')
    db.add(doc)
    db.flush()
    versions.snapshot_working_copy(db, doc)
    link = ShareLink(local_document_id=doc.id, server_url='http://example.test',
                     server_id='server', account_id='actor', workspace_id='workspace',
                     shared_document_id='remote-doc', base_version_id='remote-1',
                     observed_version_id='remote-2')
    db.add(link)
    db.commit()
    return doc, link, path


def _target(local, content, folder=False):
    library, _ = local
    source = library.parent / ('remote-folder' if folder else 'remote.txt')
    _write(source, content, folder)
    if folder:
        prepared = snapshots.store_folder_snapshot(library, source)
    else:
        key, digest, size = snapshots.store_file_snapshot(library, source)
        prepared = key, digest, None, size
    remote = dict(id='remote-2', content_hash=prepared[1], manifest_hash=prepared[2],
                  version_number=2, file_size=prepared[3])
    return prepared, remote


def _operation(db, doc, remote, request_id='update-1'):
    return remote_update.freeze_operation(db, server_id='server', account_id='actor',
        workspace_id='workspace', request_id=request_id, kind='update_local',
        local_document_id=doc.id, payload={'remote_version': remote})


@pytest.mark.parametrize('folder', [False, True])
@pytest.mark.parametrize('working,target,count,current_number,protected', [
    ('a', 'a', 1, 1, False),
    ('b', 'a', 2, 1, True),
    ('b', 'b', 2, 2, False),
    ('b', 'c', 3, 3, True),
    ('a', 'c', 2, 2, False),
])
def test_three_way_content_matrix(local, folder, working, target, count, current_number, protected):
    _, db = local
    doc, link, work = _document(local, folder=folder)
    original_id = doc.id
    _write(work, working, folder)
    work_inode = work.stat().st_ino
    prepared, remote = _target(local, target, folder)
    operation = _operation(db, doc, remote)
    result = remote_update.apply_received_snapshot(db, doc, link, operation, prepared, remote)
    assert db.query(Version).filter_by(document_id=doc.id).count() == count
    assert db.get(Version, doc.current_version_id).version_number == current_number
    assert bool(result['protected_version_id']) is protected
    assert remote_update.content_hash(work, folder) == prepared[1]
    assert doc.id == original_id and doc.name == 'Original name' and doc.status == 'pending'
    assert link.base_version_id == 'remote-2'
    assert db.query(VersionSource).one().shared_version_id == 'remote-2'
    if target == working:
        assert work.stat().st_ino == work_inode
    # A response-lost retry must never overwrite subsequent external edits.
    _write(work, 'later edit', folder)
    assert remote_update.apply_received_snapshot(db, doc, link, operation, prepared, remote) == result
    assert db.query(Version).filter_by(document_id=doc.id).count() == count
    assert remote_update.content_hash(work, folder) != prepared[1]


def test_historical_match_creates_receipt_and_does_not_reuse_numbers(local):
    _, db = local
    doc, link, work = _document(local)
    first = db.get(Version, doc.current_version_id)
    _write(work, 'b')
    versions.snapshot_working_copy(db, doc)
    doc.version_sequence = 8
    db.commit()
    prepared, remote = _target(local, 'a')
    operation = _operation(db, doc, remote)
    result = remote_update.apply_received_snapshot(db, doc, link, operation, prepared, remote)
    assert result['version_number'] == 9
    assert result['local_version_id'] != first.id
    assert db.get(Version, result['local_version_id']).file_path == first.file_path


@pytest.mark.parametrize('after_install', [False, True])
def test_restart_compensates_interrupted_install(local, monkeypatch, after_install):
    _, db = local
    doc, link, work = _document(local)
    old_id, doc_id = doc.current_version_id, doc.id
    _write(work, 'unsaved')
    prepared, remote = _target(local, 'remote')
    operation = _operation(db, doc, remote)
    operation_id = operation.id
    if after_install:
        monkeypatch.setattr(remote_update, 'record_source', lambda *a, **k: (_ for _ in ()).throw(SystemExit()))
    else:
        monkeypatch.setattr(snapshots, 'export_object', lambda *a, **k: (_ for _ in ()).throw(SystemExit()))
    with pytest.raises(SystemExit):
        remote_update.apply_received_snapshot(db, doc, link, operation, prepared, remote)
    db.rollback()
    monkeypatch.undo()
    # monkeypatch.undo also restores library_path: pin the isolated library again.
    settings.library_path = str(local[0])
    results = remote_update.recover_pending_updates(db)
    assert results[0]['status'] == 'interrupted'
    assert work.read_text() == 'unsaved'
    assert db.get(Document, doc_id).current_version_id == old_id
    assert link.base_version_id == 'remote-1'
    operation = db.get(RemoteOperation, operation_id)
    result = remote_update.apply_received_snapshot(db, doc, link, operation, prepared, remote)
    assert result['version_number'] == 3
    assert db.query(Version).count() == 3  # no duplicate protection on restart


def test_external_edit_after_install_preserves_both_and_does_not_advance_base(local, monkeypatch):
    _, db = local
    doc, link, work = _document(local)
    old_id = doc.current_version_id
    prepared, remote = _target(local, 'remote')
    operation = _operation(db, doc, remote)
    original = snapshots.export_object

    def external_edit(*args, **kwargs):
        result = original(*args, **kwargs)
        work.write_text('external edit')
        return result

    monkeypatch.setattr(snapshots, 'export_object', external_edit)
    with pytest.raises(remote_update.RemoteUpdateError):
        remote_update.apply_received_snapshot(db, doc, link, operation, prepared, remote)
    assert work.read_text() == 'external edit'
    assert Path(json.loads(operation.recovery_json)['backup']).read_text() == 'a'
    assert operation.status == 'needs_review'
    assert doc.current_version_id == old_id and link.base_version_id == 'remote-1'
    assert db.query(VersionSource).count() == 0


def test_corrupt_target_does_not_touch_working_content(local):
    _, db = local
    doc, link, work = _document(local)
    prepared, remote = _target(local, 'remote')
    operation = _operation(db, doc, remote)
    snapshots.resolve_object(local[0], prepared[0]).write_text('corrupt')
    with pytest.raises(remote_update.RemoteUpdateError):
        remote_update.apply_received_snapshot(db, doc, link, operation, prepared, remote)
    assert work.read_text() == 'a' and link.base_version_id == 'remote-1'
    assert db.query(Version).count() == 1


def test_recovery_invalid_document_path_does_not_abort_startup(local):
    _, db = local
    doc, _, work = _document(local)
    prepared, remote = _target(local, 'remote')
    operation = _operation(db, doc, remote)
    operation.status = 'installing'
    operation.recovery_json = json.dumps({'work': str(work), 'backup': str(work.parent / '.teamhub-update-x' / work.name)})
    doc.storage_path = '../outside.txt'
    db.commit()
    result = remote_update.recover_pending_updates(db)
    assert result[0]['status'] == 'needs_review'
    assert work.read_text() == 'a'


def test_recovery_rejects_symlinked_backup_parent(local, tmp_path):
    _, db = local
    doc, _, work = _document(local)
    _, remote = _target(local, 'remote')
    operation = _operation(db, doc, remote)
    outside = tmp_path / 'outside'
    outside.mkdir()
    sentinel = outside / work.name
    sentinel.write_text('unrelated')
    container = work.parent / '.teamhub-update-symlink'
    container.symlink_to(outside, target_is_directory=True)
    operation.status = 'installing'
    operation.recovery_json = json.dumps({'work': str(work), 'backup': str(container / work.name),
        'working_hash': 'irrelevant', 'target_hash': 'irrelevant'})
    db.commit()
    assert remote_update.recover_pending_updates(db)[0]['status'] == 'needs_review'
    assert sentinel.read_text() == 'unrelated' and work.read_text() == 'a'
