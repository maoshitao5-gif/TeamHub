"""Remote intake compares current managed entities, never names or old snapshots."""
import hashlib
import os
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import database
from backend.app.api import sharing, deliveries
from backend.app.config import settings
from backend.app.core import cloud_client
from backend.app.models import Document, Version, ShareLink, RemoteOperation, VersionSource, Tag
from backend.app.services import snapshots, versions
from main import app

client = TestClient(app)
SERVER_ID = '96aefce7-5cd3-4e3b-8b4f-b396a84e34f7'
CONTENT = b'entity content'
HASH = hashlib.sha256(CONTENT).hexdigest()


@pytest.fixture
def dedup_library(tmp_path, monkeypatch):
    library = tmp_path / 'library'
    library.mkdir()
    monkeypatch.setattr(settings, 'library_path', str(library))
    monkeypatch.setattr(settings, 'pending_path', str(tmp_path / 'pending'))
    database.rebind_engine()
    database.init_db()
    config = database.load_library_config()
    config.update(cloud_api_url='http://dedup.invalid', cloud_access_token='access',
                  cloud_server_id=SERVER_ID, active_workspace_id='workspace')
    database.save_library_config(config)
    yield library
    database.engine.dispose()


def local_file(library, *, content=CONTENT, status='organized', doc_type='file', name='renamed.txt'):
    root = Path(settings.pending_path) if status == 'pending' else library
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_bytes(content)
    with database.SessionLocal() as db:
        doc = Document(name=name, status=status, doc_type=doc_type, is_folder=False,
                       storage_mode='move', storage_area='pending' if status == 'pending' else 'library',
                       storage_path=str(Path(settings.pending_folder_name) / name) if status == 'pending' else name)
        db.add(doc)
        db.flush()
        versions.snapshot_working_copy(db, doc)
        db.commit()
        return doc.id, path


def counts():
    with database.SessionLocal() as db:
        return tuple(db.query(model).count() for model in (Document, Version, ShareLink, VersionSource, RemoteOperation, Tag))


@pytest.fixture
def upstream(monkeypatch):
    calls = []
    def install(handler=None, *, content=CONTENT, doc_type='file', content_hash=HASH, manifest_hash=None):
        def respond(request):
            calls.append(request)
            path = request.url.path
            if path == '/api/server-info':
                return httpx.Response(200, json=dict(server_id=SERVER_ID, protocol_major=1, capabilities=['explicit_publication_scope']))
            if path == '/api/users/me':
                return httpx.Response(200, json={'id': 'actor'})
            if handler:
                value = handler(request)
                if value is not None:
                    return value
            if path.endswith('/documents/remote'):
                return httpx.Response(200, json=dict(id='remote', name='different name', doc_type=doc_type, current_version_id='remote-version'))
            if path.endswith('/documents/remote/versions'):
                return httpx.Response(200, json=[dict(id='remote-version', original_filename='download.txt', version_number=1,
                    content_hash=content_hash, manifest_hash=manifest_hash, file_size=len(content))])
            if path.endswith('/download'):
                return httpx.Response(200, content=content)
            if path.endswith('/duplicates'):
                return httpx.Response(200, json=[])
            if path.endswith('/uploads'):
                return httpx.Response(200, json=dict(upload_id='uploaded', sha256=hashlib.sha256(request.read()).hexdigest(), file_size=len(content)))
            if path.endswith('/publications'):
                return httpx.Response(200, json=dict(status='succeeded', document_id='remote', version_id='remote-version'))
            raise AssertionError(f'Unexpected {request.method} {path}')
        http = httpx.Client(transport=httpx.MockTransport(respond))
        monkeypatch.setattr(cloud_client, '_http', http)
        monkeypatch.setattr(sharing, '_http', http)
        monkeypatch.setattr(deliveries, '_http', http)
        return calls
    return install


@pytest.mark.parametrize('status,doc_type', [('organized', 'file'), ('pending', 'file'), ('organized', 'archive')])
def test_pull_identical_entity_skips_download_and_all_metadata(dedup_library, upstream, status, doc_type):
    identifier, path = local_file(dedup_library, status=status, doc_type=doc_type)
    calls = upstream()
    before = counts()
    result = client.post('/api/sharing/pull', json={'cloud_doc_id': 'remote'})
    assert result.status_code == 200, result.text
    assert result.json().get('status') == 'duplicate'
    assert result.json()['code'] == 'DUPLICATE_CONTENT'
    assert result.json()['document_id'] == identifier
    assert result.json()['document_status'] == status
    assert result.json()['duplicate_document']['id'] == identifier
    assert counts() == before and path.read_bytes() == CONTENT
    assert not any(str(call.url).endswith('/download') for call in calls)
    assert not (dedup_library / '.teamhub' / 'downloads').exists()


@pytest.mark.parametrize('old_content', [b'old snapshot', b'x' * len(CONTENT)])
def test_pull_hashes_unsaved_current_bytes_instead_of_db_hash(dedup_library, upstream, old_content):
    identifier, path = local_file(dedup_library, content=old_content)
    path.write_bytes(CONTENT)
    upstream()
    before = counts()
    result = client.post('/api/sharing/pull', json={'cloud_doc_id': 'remote'})
    assert result.json().get('status') == 'duplicate' and result.json()['document_id'] == identifier
    assert counts() == before


@pytest.mark.parametrize('inactive', ['history_only', 'missing', 'symlink', 'trashed'])
def test_pull_old_or_unavailable_entity_does_not_block(dedup_library, upstream, inactive):
    identifier, path = local_file(dedup_library)
    if inactive == 'history_only':
        path.write_bytes(b'changed work')
    elif inactive == 'missing':
        path.unlink()
    elif inactive == 'symlink':
        source = dedup_library.parent / 'external.txt'
        source.write_bytes(CONTENT)
        path.unlink()
        path.symlink_to(source)
    else:
        with database.SessionLocal() as db:
            db.get(Document, identifier).status = 'trashed'
            db.commit()
    upstream()
    before = counts()
    result = client.post('/api/sharing/pull', json={'cloud_doc_id': 'remote'})
    assert result.status_code == 200, result.text
    assert result.json()['document_id'] != identifier
    assert counts()[0] == before[0] + 1


def test_pull_permission_check_precedes_local_hashing(dedup_library, upstream, monkeypatch):
    local_file(dedup_library)
    upstream(lambda request: httpx.Response(404, json={'detail': '不可见'}) if request.url.path.endswith('/documents/remote') else None)
    from backend.app.services import content_duplicates
    monkeypatch.setattr(content_duplicates, 'find_current_duplicate', lambda *a, **k: pytest.fail('unauthorized hash'))
    assert client.post('/api/sharing/pull', json={'cloud_doc_id': 'remote'}).status_code == 404


def test_pull_current_entity_read_error_fails_without_download(dedup_library, upstream, monkeypatch):
    _, path = local_file(dedup_library)
    calls = upstream()
    original = os.open
    def fail(candidate, flags, *args, **kwargs):
        if Path(candidate) == path:
            raise PermissionError('denied')
        return original(candidate, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', fail)
    before = counts()
    result = client.post('/api/sharing/pull', json={'cloud_doc_id': 'remote'})
    assert result.status_code == 409
    assert counts() == before
    assert not any(str(call.url).endswith('/download') for call in calls)


def test_actual_size_filters_before_hash_and_ignores_stale_total_size(dedup_library, upstream, monkeypatch):
    identifier, path = local_file(dedup_library, content=b'different size')
    path.write_bytes(CONTENT)
    with database.SessionLocal() as db:
        db.get(Document, identifier).total_size = 999999
        db.commit()
    upstream()
    assert client.post('/api/sharing/pull', json={'cloud_doc_id': 'remote'}).json().get('status') == 'duplicate'


def test_same_folder_manifest_blocks_second_archive(dedup_library, upstream):
    folder = dedup_library / 'different folder name'
    folder.mkdir()
    (folder / 'empty').mkdir()
    (folder / 'file.txt').write_bytes(CONTENT)
    with database.SessionLocal() as db:
        doc = Document(name='folder', status='organized', doc_type='folder', is_folder=True,
                       storage_mode='move', storage_path=folder.name)
        db.add(doc); db.flush()
        version, _ = versions.snapshot_working_copy(db, doc)
        db.commit()
        identifier, content_hash, archive_hash, archive = doc.id, version.sha256_hash, version.manifest_hash, Path(version.file_path).read_bytes()
    calls = upstream(content=archive, doc_type='folder', content_hash=content_hash, manifest_hash=archive_hash)
    before = counts()
    result = client.post('/api/sharing/pull', json={'cloud_doc_id': 'remote'})
    assert result.status_code == 200, result.text
    assert result.json().get('status') == 'duplicate' and result.json()['document_id'] == identifier
    assert counts() == before
    assert not any(str(call.url).endswith('/download') for call in calls)


def test_first_publish_captures_unsaved_working_bytes(dedup_library, upstream):
    identifier, path = local_file(dedup_library, content=b'old')
    path.write_bytes(CONTENT)
    calls = upstream()
    result = client.post('/api/sharing/publish', json=dict(doc_id=identifier, confirmed=True, visibility='workspace', request_id='publish-working'))
    assert result.status_code == 200, result.text
    uploads = [request for request in calls if request.url.path.endswith('/uploads')]
    assert uploads[0].read() == CONTENT
    with database.SessionLocal() as db:
        doc = db.get(Document, identifier)
        assert doc.version_sequence == 2 and db.get(Version, doc.current_version_id).sha256_hash == HASH


def test_publish_duplicate_precheck_does_not_add_version_journal_or_link(dedup_library, upstream):
    identifier, path = local_file(dedup_library, content=b'old')
    path.write_bytes(CONTENT)
    def duplicate(request):
        if request.url.path.endswith('/duplicates'):
            return httpx.Response(200, json=[dict(document_id='existing', name='existing.pdf', doc_type='file', current_version_id='other-version', current_version_number=3)])
    calls = upstream(duplicate)
    before = counts()
    result = client.post('/api/sharing/publish', json=dict(doc_id=identifier, confirmed=True, visibility='workspace', request_id='duplicate-publish'))
    assert result.status_code == 409, result.text
    error = result.json()['error']
    assert error['code'] == 'DUPLICATE_CONTENT' and error['details']['duplicate_document']['document_id'] == 'existing'
    assert counts() == before
    assert not (dedup_library / '.teamhub' / 'publications').exists()
    assert not any(request.url.path.endswith('/uploads') for request in calls)


def test_publish_frozen_retry_keeps_original_capture(dedup_library, upstream):
    identifier, path = local_file(dedup_library, content=b'old')
    path.write_bytes(CONTENT)
    fail = [True]
    def intermittent(request):
        if request.url.path.endswith('/uploads') and fail[0]:
            raise httpx.ConnectError('lost')
    calls = upstream(intermittent)
    body = dict(doc_id=identifier, confirmed=True, visibility='workspace', request_id='frozen')
    assert client.post('/api/sharing/publish', json=body).status_code == 502
    path.write_bytes(b'edited after failure')
    fail[0] = False
    result = client.post('/api/sharing/publish', json=body)
    assert result.status_code == 200, result.text
    uploads = [request for request in calls if request.url.path.endswith('/uploads')]
    assert uploads[-1].read() == CONTENT
    assert len([request for request in calls if request.url.path.endswith('/duplicates')]) == 1


def test_delivery_duplicate_checks_entity_without_receipt_or_new_operation(dedup_library, upstream):
    identifier, _ = local_file(dedup_library)
    def delivered(request):
        if request.url.path.endswith('/deliveries/message'):
            return httpx.Response(200, json=dict(can_download=True, note='hello', items=[dict(id='item', filename='different.txt', doc_type='file', content_hash=HASH, manifest_hash=None, file_size=len(CONTENT))]))
    calls = upstream(delivered)
    before = counts()
    response = client.post('/api/deliveries/message/items/item/intake', json={'request_id':'duplicate-intake'})
    assert response.status_code == 200, response.text
    assert response.json().get('status') == 'duplicate' and response.json()['document_id'] == identifier
    assert counts() == before
    assert not any(str(call.url).endswith('/download') for call in calls)


def test_scan_avoids_reading_different_actual_size(dedup_library, upstream, monkeypatch):
    _, path = local_file(dedup_library, content=b'large other entity with different size')
    with database.SessionLocal() as db:
        db.query(Document).one().total_size = len(CONTENT)  # stale stat must not force hashing
        db.commit()
    original = os.open
    def forbid(candidate, flags, *args, **kwargs):
        if Path(candidate) == path:
            pytest.fail('Different actual-size entity should not be opened')
        return original(candidate, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', forbid)
    upstream()
    response = client.post('/api/sharing/pull', json={'cloud_doc_id': 'remote'})
    assert response.status_code == 200 and not response.json().get('duplicate')


def test_scan_detects_fstat_change_before_duplicate_result(dedup_library, upstream, monkeypatch):
    from types import SimpleNamespace
    local_file(dedup_library)
    calls = upstream()
    original = os.fstat
    count = 0
    def changed(descriptor):
        nonlocal count
        info = original(descriptor)
        count += 1
        if count == 2:
            return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino, st_mode=info.st_mode,
                                   st_size=info.st_size, st_mtime_ns=info.st_mtime_ns + 1, st_ctime_ns=info.st_ctime_ns)
        return info
    monkeypatch.setattr(os, 'fstat', changed)
    before = counts()
    response = client.post('/api/sharing/pull', json={'cloud_doc_id': 'remote'})
    assert response.status_code == 409 and counts() == before
    assert not any(str(request.url).endswith('/download') for request in calls)


@pytest.mark.parametrize('bad_hash', ['not-a-hash', 'a' * 63, 'A' * 64, None])
def test_bad_remote_content_hash_never_passes_scan(dedup_library, upstream, bad_hash):
    local_file(dedup_library)
    calls = upstream(content_hash=bad_hash)
    before = counts()
    result = client.post('/api/sharing/pull', json={'cloud_doc_id': 'remote'})
    assert result.status_code in (409, 422) and counts() == before
    assert not any(str(call.url).endswith('/download') for call in calls)


def test_indexed_unmanaged_file_is_not_a_reusable_current_entity(dedup_library, upstream):
    identifier, _ = local_file(dedup_library)
    with database.SessionLocal() as db:
        db.get(Document, identifier).storage_mode = 'index'
        db.commit()
    upstream()
    response = client.post('/api/sharing/pull', json={'cloud_doc_id': 'remote'})
    assert response.status_code == 200 and response.json()['document_id'] != identifier


def test_folder_and_single_archive_are_distinct_entities(dedup_library, upstream):
    local_file(dedup_library, doc_type='archive')
    folder = dedup_library.parent / 'remote-folder'
    folder.mkdir(); (folder / 'file.txt').write_bytes(CONTENT)
    prepared = snapshots.store_folder_snapshot(dedup_library, folder)
    archive = snapshots.resolve_object(dedup_library, prepared[0]).read_bytes()
    upstream(content=archive, doc_type='folder', content_hash=prepared[1], manifest_hash=prepared[2])
    response = client.post('/api/sharing/pull', json={'cloud_doc_id': 'remote'})
    assert response.status_code == 200 and not response.json().get('duplicate')


def test_publish_without_change_reuses_local_version(dedup_library, upstream):
    identifier, _ = local_file(dedup_library)
    calls = upstream()
    before = counts()[1]
    result = client.post('/api/sharing/publish', json=dict(doc_id=identifier, confirmed=True, visibility='workspace'))
    assert result.status_code == 200, result.text
    assert counts()[1] == before
    assert next(request for request in calls if request.url.path.endswith('/uploads')).read() == CONTENT


def test_existing_share_link_still_requires_explicit_update_choice(dedup_library, upstream):
    identifier, _ = local_file(dedup_library)
    upstream()
    body = dict(doc_id=identifier, confirmed=True, visibility='workspace')
    assert client.post('/api/sharing/publish', json=body).status_code == 200
    response = client.post('/api/sharing/publish', json=body)
    assert response.status_code == 400 and '更新原资料' in response.json()['detail']


def test_new_publish_identity_mismatch_sends_no_file_or_token(dedup_library, upstream, monkeypatch):
    identifier, path = local_file(dedup_library)
    path.write_bytes(b'new content')
    upstream()
    calls = []
    def impostor(request):
        calls.append(request)
        return httpx.Response(200, json=dict(server_id='other-server', protocol_major=1, capabilities=['explicit_publication_scope']))
    monkeypatch.setattr(cloud_client, '_http', httpx.Client(transport=httpx.MockTransport(impostor)))
    before = counts()
    response = client.post('/api/sharing/publish', json=dict(doc_id=identifier, confirmed=True, visibility='workspace'))
    assert response.status_code == 409 and counts() == before
    assert len(calls) == 1 and 'authorization' not in calls[0].headers


def test_duplicate_race_response_preserves_candidate_details(dedup_library, upstream):
    identifier, _ = local_file(dedup_library)
    candidate = dict(document_id='concurrent', name='concurrent.txt', doc_type='file', current_version_id='v-concurrent', current_version_number=2)
    def race(request):
        if request.url.path.endswith('/publications'):
            return httpx.Response(200, json=dict(status='failed', code='DUPLICATE_CONTENT', document_id='concurrent',
                duplicate_document=candidate, error=dict(code='DUPLICATE_CONTENT', message='云端已存在相同文件，未重复新增资料')))
    upstream(race)
    response = client.post('/api/sharing/publish', json=dict(doc_id=identifier, confirmed=True, visibility='workspace', request_id='race'))
    assert response.status_code == 409, response.text
    assert response.json()['error']['details']['duplicate_document'] == candidate
    assert response.json()['error']['details']['workspace_id'] == 'workspace'
    assert counts()[2] == 0 and counts()[3] == 0


def test_delivery_succeeded_receipt_replays_without_absorbing_later_edit(dedup_library, upstream):
    def delivered(request):
        if request.url.path.endswith('/deliveries/message'):
            return httpx.Response(200, json=dict(can_download=True, note='hello', items=[dict(id='item', filename='different.txt', doc_type='file', content_hash=HASH, manifest_hash=None, file_size=len(CONTENT))]))
    calls = upstream(delivered)
    path = '/api/deliveries/message/items/item/intake'
    request = {'request_id':'receipt'}
    response = client.post(path, json=request)
    assert response.status_code == 200 and response.json()['status'] == 'pending'
    with database.SessionLocal() as db:
        from backend.app.core.storage_paths import document_path
        work = document_path(db.get(Document, response.json()['document_id']))
    work.write_bytes(b'personal edit after receipt')
    before = counts()
    calls.clear()
    assert client.post(path, json=request).json() == response.json()
    assert counts() == before and work.read_bytes() == b'personal edit after receipt'
    assert not any(str(call.url).endswith('/download') for call in calls)


def test_delivery_wrong_request_reuse_remains_rejected(dedup_library, upstream):
    def delivered(request):
        if request.url.path.endswith('/deliveries/message'):
            return httpx.Response(200, json=dict(can_download=True, note='', items=[dict(id='item', filename='file.txt', doc_type='file', content_hash=HASH, manifest_hash=None, file_size=len(CONTENT))]))
    upstream(delivered)
    path = '/api/deliveries/message/items/item/intake'
    assert client.post(path, json={'request_id':'receipt'}).status_code == 200
    assert client.post('/api/deliveries/other/items/item/intake', json={'request_id':'receipt'}).status_code == 409


@pytest.mark.parametrize('replay_access', [200, 404])
def test_final_duplicate_is_failed_and_retry_rechecks_access_without_upload(dedup_library, upstream, replay_access):
    identifier, _ = local_file(dedup_library)
    candidate = dict(document_id='concurrent', name='visible-only-if-authorized.txt', doc_type='file',
                     current_version_id='v-concurrent', current_version_number=2)
    failure = dict(status='failed', code='DUPLICATE_CONTENT', document_id='concurrent', duplicate_document=candidate,
                   error=dict(code='DUPLICATE_CONTENT', message='云端已存在相同文件，未重复新增资料'))
    def respond(request):
        if request.url.path.endswith('/publications'):
            return httpx.Response(200, json=failure)
        if request.url.path.endswith('/publications/terminal'):
            return httpx.Response(replay_access, json=failure if replay_access == 200 else {'detail': '不可见'})
    calls = upstream(respond)
    body = dict(doc_id=identifier, confirmed=True, visibility='workspace', request_id='terminal')
    first = client.post('/api/sharing/publish', json=body)
    assert first.status_code == 409
    operation = client.get('/api/sharing/operations/terminal').json()
    assert operation['status'] == 'failed'
    assert operation['result'] == failure
    assert operation['error'] == '云端已存在相同文件，未重复新增资料'
    before = counts()
    calls.clear()
    retry = client.post('/api/sharing/publish', json=body)
    assert retry.status_code == (409 if replay_access == 200 else 404), retry.text
    if replay_access == 200:
        assert retry.json() == first.json()
    else:
        assert 'visible-only-if-authorized.txt' not in retry.text
    assert counts() == before
    assert any(request.url.path.endswith('/publications/terminal') for request in calls)
    assert not any(request.method == 'POST' for request in calls)


def test_failed_duplicate_recovery_without_journal_still_gets_remote_acl(dedup_library, upstream):
    identifier, _ = local_file(dedup_library)
    candidate = dict(document_id='concurrent', name='concurrent.txt')
    failure = dict(status='failed', code='DUPLICATE_CONTENT', document_id='concurrent', duplicate_document=candidate,
                   error=dict(code='DUPLICATE_CONTENT', message='云端已存在相同文件，未重复新增资料'))
    def respond(request):
        if request.url.path.endswith('/publications') or request.url.path.endswith('/publications/recovered-failure'):
            return httpx.Response(200, json=failure)
    calls = upstream(respond)
    body = dict(doc_id=identifier, confirmed=True, visibility='workspace', request_id='recovered-failure')
    assert client.post('/api/sharing/publish', json=body).status_code == 409
    with database.SessionLocal() as db:
        assert db.query(RemoteOperation).one().status == 'failed'
    for journal in (dedup_library / '.teamhub' / 'publications').glob('*.json'):
        journal.unlink()
    calls.clear()
    response = client.post('/api/sharing/publish', json=body)
    assert response.status_code == 409
    assert any(request.url.path.endswith('/publications/recovered-failure') for request in calls)
    assert not any(request.method == 'POST' for request in calls)


@pytest.mark.parametrize('partial_work', [False, True])
def test_intake_retry_checks_duplicates_after_recovery_freeze(dedup_library, upstream, monkeypatch, partial_work):
    def delivered(request):
        if request.url.path.endswith('/deliveries/message'):
            return httpx.Response(200, json=dict(can_download=True, note='hello', items=[dict(id='item', filename='file.txt', doc_type='file', content_hash=HASH, manifest_hash=None, file_size=len(CONTENT))]))
    calls = upstream(delivered)
    path = '/api/deliveries/message/items/item/intake'
    request = {'request_id': 'frozen-intake'}
    export = snapshots.export_object
    def interrupt(*args, **kwargs):
        if partial_work:
            export(*args, **kwargs)
        raise OSError('intake interrupted before metadata')
    with monkeypatch.context() as patch:
        patch.setattr(snapshots, 'export_object', interrupt)
        with pytest.raises(OSError):
            client.post(path, json=request)
    import json
    with database.SessionLocal() as db:
        operation = db.query(RemoteOperation).one()
        recovery = json.loads(operation.recovery_json)
        assert operation.status == 'pending'
    work = Path(settings.pending_path) / recovery['name']
    assert work.exists() == partial_work
    identifier, _ = local_file(dedup_library, name='another intake.txt')
    before = counts()
    calls.clear()
    response = client.post(path, json=request)
    assert response.status_code == 200, response.text
    assert response.json().get('status') == 'duplicate' and response.json()['document_id'] == identifier
    assert counts() == before
    assert work.exists() == partial_work  # No automatic deletion of partial content.
    if partial_work:
        assert work.read_bytes() == CONTENT
    with database.SessionLocal() as db:
        operation = db.query(RemoteOperation).one()
        assert operation.status == 'failed' and json.loads(operation.result_json) == response.json()
        assert db.query(VersionSource).count() == 0
    assert not any(str(call.url).endswith('/download') for call in calls)


def test_terminal_delivery_duplicates_do_not_hide_real_pending_operations(dedup_library, upstream):
    from datetime import datetime, timezone
    import json
    upstream()
    with database.SessionLocal() as db:
        for number in range(60):
            db.add(RemoteOperation(server_id=SERVER_ID, account_id='actor', workspace_id='workspace',
                request_id=f'duplicate-{number}', kind='delivery_intake', input_json='{}', status='failed',
                result_json=json.dumps(dict(code='DUPLICATE_CONTENT', status='duplicate', duplicate=True)),
                created_at=datetime(2021, 1, 1, tzinfo=timezone.utc)))
        for request_id, status, result in [('pending', 'pending', None), ('needs-review', 'needs_review', None),
                                           ('other-failed', 'failed', '{"code":"STORAGE_ERROR"}'),
                                           ('malformed', 'failed', 'not-json'),
                                           ('contradictory', 'failed', '{"code":"DUPLICATE_CONTENT","status":"pending","duplicate":false}'),
                                           ('incomplete', 'failed', '{"code":"DUPLICATE_CONTENT"}')]:
            db.add(RemoteOperation(server_id=SERVER_ID, account_id='actor', workspace_id='workspace',
                request_id=request_id, kind='delivery_intake', input_json='{}', status=status,
                result_json=result, created_at=datetime(2020, 1, 1, tzinfo=timezone.utc)))
        db.add(RemoteOperation(server_id=SERVER_ID, account_id='actor', workspace_id='workspace',
            request_id='send-with-duplicate-code', kind='delivery_send', input_json='{}', status='failed',
            result_json=json.dumps(dict(code='DUPLICATE_CONTENT', status='duplicate', duplicate=True)),
            created_at=datetime(2020, 1, 1, tzinfo=timezone.utc)))
        db.commit()
    response = client.get('/api/deliveries/operations')
    assert response.status_code == 200
    assert {item['request_id'] for item in response.json()} == {'pending', 'needs-review', 'other-failed', 'malformed', 'contradictory', 'incomplete', 'send-with-duplicate-code'}
