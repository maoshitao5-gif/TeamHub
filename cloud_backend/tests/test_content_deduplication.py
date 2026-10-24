"""Content deduplication checks the current visible entity, never its name or history."""
import hashlib
import io
import json
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core import object_store
from app.core.deps import get_current_user
from app.database import SessionLocal
from app.models import (
    CloudDocument, CloudTag, CloudVersion, DocumentAccess, Team, TeamMember,
    User, Workspace, cloud_document_tags,
)
from main import app

client = TestClient(app)


def uid():
    return str(uuid.uuid4())


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def space(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'storage_dir', str(tmp_path / 'objects'))
    with SessionLocal() as db:
        team = Team(name='Content duplicates', slug=uid())
        users = {name: User(email=f'{uid()}@dedup.test', hashed_password='x',
                            display_name=name, is_superuser=name == 'admin')
                 for name in ('creator', 'reader', 'other', 'admin')}
        db.add_all([team, *users.values()])
        db.flush()
        spaces = [Workspace(team_id=team.id, name=name) for name in ('main', 'other')]
        db.add_all(spaces)
        db.add_all([TeamMember(team_id=team.id, user_id=user.id,
                              role='admin' if name == 'admin' else 'member')
                    for name, user in users.items()])
        db.commit()
        for user in users.values():
            db.refresh(user)
        result = dict(users, ws=spaces[0].id, second_ws=spaces[1].id)
    try:
        as_user(result['creator'])
        yield result
    finally:
        app.dependency_overrides.clear()


def as_user(user):
    app.dependency_overrides[get_current_user] = lambda: user


def upload(ws, data, filename='source.txt', purpose='publication'):
    response = client.post(f'/api/workspaces/{ws}/uploads',
                           params={'filename': filename, 'purpose': purpose}, content=data)
    assert response.status_code == 200, response.text
    return response.json()


def publication_body(uploaded):
    return dict(request_id=uid(), confirmed=True, name='original', visibility='workspace',
                doc_type='file', content_hash=uploaded['sha256'], upload_id=uploaded['upload_id'],
                tags=['original-tag'])


def publish(ws, uploaded, **kwargs):
    body = publication_body(uploaded)
    body.update(kwargs)
    response = client.post(f'/api/workspaces/{ws}/publications', json=body)
    assert response.status_code == 200, response.text
    return body, response.json()


def duplicates(ws, digest, doc_type='file'):
    response = client.post(f'/api/workspaces/{ws}/duplicates',
                           json={'content_hash': digest, 'doc_type': doc_type})
    assert response.status_code == 200, response.text
    return response.json()


def counts(ws):
    with SessionLocal() as db:
        doc_ids = db.query(CloudDocument.id).filter_by(workspace_id=ws).subquery()
        return (
            db.query(CloudDocument).filter_by(workspace_id=ws).count(),
            db.query(CloudVersion).filter(CloudVersion.document_id.in_(db.query(doc_ids.c.id))).count(),
            db.query(DocumentAccess).filter(DocumentAccess.document_id.in_(db.query(doc_ids.c.id))).count(),
            db.query(CloudTag).filter_by(workspace_id=ws).count(),
            db.execute(cloud_document_tags.select().where(
                cloud_document_tags.c.document_id.in_(db.query(doc_ids.c.id)))).all(),
        )


def assert_duplicate(result, original):
    assert result['status'] == 'failed'
    assert result['code'] == 'DUPLICATE_CONTENT'
    assert result['error']['message'] == '云端已存在相同文件，未重复新增资料'
    assert result['document_id'] == original['document_id']
    assert result['duplicate_document']['document_id'] == original['document_id']
    assert result['duplicate_document']['current_version_id'] == original['version_id']


@pytest.mark.parametrize('first_type,second_type', [('file', 'file'), ('file', 'archive'), ('archive', 'file')])
@pytest.mark.parametrize('force_new', [False, True])
def test_identical_bytes_ignore_name_tags_and_single_file_type(space, first_type, second_type, force_new):
    ws = space['ws']
    _, original = publish(ws, upload(ws, b'identical-bytes'), doc_type=first_type)
    before = counts(ws)
    body, result = publish(ws, upload(ws, b'identical-bytes', 'renamed.zip'),
                           doc_type=second_type, name='renamed', tags=['new-tag'],
                           force_new=force_new, original_filename='renamed.zip')
    assert_duplicate(result, original)
    assert duplicates(ws, sha(b'identical-bytes'), second_type) == [result['duplicate_document']]
    assert counts(ws) == before
    assert client.post(f'/api/workspaces/{ws}/publications', json=dict(body, upload_id='consumed')).json() == result
    assert client.get(f"/api/workspaces/{ws}/publications/{body['request_id']}").json() == result


def test_same_name_with_different_bytes_is_a_distinct_entity(space):
    ws = space['ws']
    _, first = publish(ws, upload(ws, b'first'))
    _, second = publish(ws, upload(ws, b'second'))
    assert second['status'] == 'succeeded'
    assert second['document_id'] != first['document_id']


def submit(ws, document, uploaded, **changes):
    body = dict(request_id=uid(), confirmed=True, base_version_id=document['version_id'],
                upload_id=uploaded['upload_id'], content_hash=uploaded['sha256'])
    body.update(changes)
    response = client.post(f"/api/workspaces/{ws}/documents/{document['document_id']}/versions", json=body)
    assert response.status_code == 200, response.text
    return body, response.json()


def test_history_only_match_does_not_block_new_entity_or_explicit_new_version(space):
    ws = space['ws']
    old_upload = upload(ws, b'old-content')
    _, original = publish(ws, old_upload)
    _, next_version = submit(ws, original, upload(ws, b'new-content'))
    assert duplicates(ws, old_upload['sha256']) == []
    _, another = publish(ws, old_upload)
    assert another['status'] == 'succeeded' and another['document_id'] != original['document_id']
    _, restored = submit(ws, next_version, old_upload)
    assert restored['version_id'] not in (original['version_id'], next_version['version_id'])
    with SessionLocal() as db:
        assert db.query(CloudVersion).filter_by(document_id=original['document_id']).count() == 3


def test_content_unchanged_reuses_current_version_and_idempotent_result(space):
    ws = space['ws']
    uploaded = upload(ws, b'unchanged')
    _, original = publish(ws, uploaded)
    before = counts(ws)
    body, result = submit(ws, original, uploaded)
    assert result['code'] == 'CONTENT_UNCHANGED'
    assert result['version_id'] == original['version_id']
    assert counts(ws) == before
    url = f"/api/workspaces/{ws}/documents/{original['document_id']}/versions"
    assert client.post(url, json=dict(body, upload_id='consumed')).json() == result


@pytest.mark.parametrize('trash_marker', ['timestamp', 'legacy_status'])
def test_recycled_entity_does_not_count_as_active_duplicate(space, trash_marker):
    ws = space['ws']
    uploaded = upload(ws, b'trashed')
    _, original = publish(ws, uploaded)
    with SessionLocal() as db:
        doc = db.get(CloudDocument, original['document_id'])
        if trash_marker == 'timestamp':
            doc.trashed_at = datetime.now(timezone.utc)
        else:
            doc.status = 'trashed'
        db.commit()
    assert duplicates(ws, uploaded['sha256']) == []
    _, result = publish(ws, uploaded)
    assert result['status'] == 'succeeded'
    assert result['document_id'] != original['document_id']


def test_nondefault_workspace_is_scoped_independently(space):
    uploaded = upload(space['ws'], b'same-data')
    _, first = publish(space['ws'], uploaded)
    assert duplicates(space['second_ws'], uploaded['sha256']) == []
    second_upload = upload(space['second_ws'], b'same-data')
    _, second = publish(space['second_ws'], second_upload)
    assert second['status'] == 'succeeded' and second['document_id'] != first['document_id']
    _, result = publish(space['second_ws'], second_upload, force_new=True)
    assert_duplicate(result, second)


@pytest.mark.parametrize('actor', ['other', 'admin'])
def test_unauthorized_document_is_neither_exposed_nor_implicitly_merged(space, actor):
    ws = space['ws']
    uploaded = upload(ws, b'restricted-content')
    _, private = publish(ws, uploaded, visibility='restricted',
                         access=[dict(user_id=space['reader'].id, role='reader')])
    as_user(space[actor])
    assert duplicates(ws, uploaded['sha256']) == []
    _, independent = publish(ws, upload(ws, b'restricted-content'), visibility='restricted', tags=[])
    assert independent['status'] == 'succeeded'
    assert independent['document_id'] != private['document_id']
    assert 'duplicate_document' not in independent
    with SessionLocal() as db:
        assert db.get(DocumentAccess, (private['document_id'], space[actor].id)) is None


def test_visible_reader_duplicate_does_not_merge_tags_or_expand_grants(space):
    ws = space['ws']
    _, original = publish(ws, upload(ws, b'readable'), visibility='restricted',
                          access=[dict(user_id=space['reader'].id, role='reader')])
    as_user(space['reader'])
    before = counts(ws)
    _, result = publish(ws, upload(ws, b'readable'), visibility='workspace', tags=['not-attached'],
                        access=[dict(user_id=space['other'].id, role='manager')], force_new=True)
    assert_duplicate(result, original)
    assert counts(ws) == before
    with SessionLocal() as db:
        doc = db.get(CloudDocument, original['document_id'])
        assert doc.visibility == 'restricted'
        assert db.get(DocumentAccess, (doc.id, space['reader'].id)).role == 'reader'
        assert db.get(DocumentAccess, (doc.id, space['other'].id)) is None


def test_duplicate_result_is_hidden_after_reader_access_revocation(space):
    ws = space['ws']
    _, original = publish(ws, upload(ws, b'revocable'), visibility='restricted',
                          access=[dict(user_id=space['reader'].id, role='reader')])
    as_user(space['reader'])
    body, duplicate = publish(ws, upload(ws, b'revocable'))
    assert_duplicate(duplicate, original)
    as_user(space['creator'])
    response = client.put(f"/api/workspaces/{ws}/documents/{original['document_id']}/access", json=dict(
        visibility='restricted', base_access_revision=1,
        access=[dict(user_id=space['creator'].id, role='manager')]))
    assert response.status_code == 200, response.text
    as_user(space['reader'])
    assert client.post(f'/api/workspaces/{ws}/publications', json=body).status_code == 404
    assert client.get(f"/api/workspaces/{ws}/publications/{body['request_id']}").status_code == 404
    assert duplicates(ws, sha(b'revocable')) == []


def test_claimed_duplicate_hash_does_not_skip_upload_validation(space):
    ws = space['ws']
    publish(ws, upload(ws, b'legitimate'))
    forged = publication_body(upload(ws, b'different'))
    forged['content_hash'] = sha(b'legitimate')
    before = counts(ws)
    response = client.post(f'/api/workspaces/{ws}/publications', json=forged)
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'VALIDATION_ERROR'
    assert counts(ws) == before
    assert 'duplicate_document' not in response.json()
    as_user(space['reader'])
    assert client.post(f'/api/workspaces/{ws}/publications', json=forged).status_code == 404


def test_concurrent_identical_publications_create_only_one_current_entity(space):
    ws = space['ws']
    bodies = [dict(publication_body(upload(ws, b'concurrent')), force_new=True,
                   name=f'concurrent-{i}') for i in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda body: client.post(f'/api/workspaces/{ws}/publications', json=body), bodies))
    assert [response.status_code for response in responses] == [200, 200]
    results = [response.json() for response in responses]
    assert sorted(result['status'] for result in results) == ['failed', 'succeeded']
    succeeded = next(result for result in results if result['status'] == 'succeeded')
    assert_duplicate(next(result for result in results if result['status'] == 'failed'), succeeded)
    assert counts(ws)[:4] == (1, 1, 1, 1)


def folder_upload(ws, compression):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=compression) as archive:
        archive.writestr('document.txt', b'folder-data')
    digest = sha(json.dumps(dict(version='1', entries=[dict(path='document.txt', type='file',
                   size=11, sha256=sha(b'folder-data'))]), ensure_ascii=False, sort_keys=True,
                   separators=(',', ':')).encode())
    uploaded = upload(ws, output.getvalue(), 'folder.zip')
    return uploaded, digest


def test_folder_uses_canonical_content_not_zip_container_or_single_file_type(space):
    ws = space['ws']
    first_upload, digest = folder_upload(ws, zipfile.ZIP_STORED)
    _, original = publish(ws, first_upload, doc_type='folder', content_hash=digest,
                          manifest_hash=first_upload['sha256'])
    second_upload, second_digest = folder_upload(ws, zipfile.ZIP_DEFLATED)
    assert second_digest == digest and first_upload['sha256'] != second_upload['sha256']
    _, result = publish(ws, second_upload, doc_type='folder', content_hash=digest,
                        manifest_hash=second_upload['sha256'], force_new=True)
    assert_duplicate(result, original)
    assert not object_store.object_path(second_upload['sha256']).exists()
    assert duplicates(ws, digest, 'folder') == [result['duplicate_document']]
    assert duplicates(ws, digest, 'archive') == []
    assert duplicates(ws, first_upload['sha256'], 'file') == []
    _, archive = publish(ws, first_upload, doc_type='archive')
    assert archive['status'] == 'succeeded' and archive['document_id'] != original['document_id']


def test_unchanged_folder_version_does_not_promote_an_unreferenced_zip_object(space):
    ws = space['ws']
    first_upload, digest = folder_upload(ws, zipfile.ZIP_STORED)
    _, original = publish(ws, first_upload, doc_type='folder', content_hash=digest,
                          manifest_hash=first_upload['sha256'])
    second_upload, _ = folder_upload(ws, zipfile.ZIP_DEFLATED)
    _, result = submit(ws, original, second_upload, content_hash=digest, manifest_hash=second_upload['sha256'])
    assert result['code'] == 'CONTENT_UNCHANGED'
    assert result['version_id'] == original['version_id']
    assert not object_store.object_path(second_upload['sha256']).exists()


def request_reuse(ws, original, uploaded, target, *, content_hash=None, manifest_hash=None, doc_type='file'):
    digest = content_hash or uploaded['sha256']
    if target == 'duplicates':
        return client.post(f'/api/workspaces/{ws}/duplicates', json=dict(content_hash=digest, doc_type=doc_type))
    if target == 'publish':
        body = publication_body(uploaded)
        body.update(content_hash=digest, manifest_hash=manifest_hash, doc_type=doc_type, force_new=True)
        return client.post(f'/api/workspaces/{ws}/publications', json=body)
    return client.post(f"/api/workspaces/{ws}/documents/{original['document_id']}/versions", json=dict(
        request_id=uid(), confirmed=True, base_version_id=original['version_id'], upload_id=uploaded['upload_id'],
        content_hash=digest, manifest_hash=manifest_hash))


@pytest.mark.parametrize('target', ['duplicates', 'publish', 'unchanged'])
@pytest.mark.parametrize('damage', ['missing', 'corrupt', 'wrong_size', 'symlink', 'directory', 'read_error', 'unexpected_manifest'])
def test_unreadable_current_entity_stops_deduplication_without_repair_or_metadata_changes(
        space, tmp_path, monkeypatch, target, damage):
    ws = space['ws']
    uploaded = upload(ws, b'current-object')
    _, original = publish(ws, uploaded)
    path = object_store.object_path(uploaded['sha256'])
    if damage == 'missing':
        path.unlink()
    elif damage == 'corrupt':
        path.write_bytes(b'corrupt-object')
    elif damage == 'wrong_size':
        with SessionLocal() as db:
            db.get(CloudVersion, original['version_id']).file_size += 1
            db.commit()
    elif damage == 'unexpected_manifest':
        with SessionLocal() as db:
            db.get(CloudVersion, original['version_id']).manifest_hash = sha(b'unrelated')
            db.commit()
    elif damage == 'symlink':
        outside = tmp_path / 'external.txt'
        outside.write_bytes(b'current-object')
        path.unlink()
        path.symlink_to(outside)
    elif damage == 'directory':
        path.unlink()
        path.mkdir()
    else:
        real_hash = object_store.hash_file

        def fail_current_object(candidate):
            if candidate == path:
                raise OSError('private path must never be exposed')
            return real_hash(candidate)

        monkeypatch.setattr(object_store, 'hash_file', fail_current_object)
    before = counts(ws)
    # Published objects may share an inode with their old staging upload. A fresh
    # valid upload isolates candidate corruption from request-payload validation.
    if target != 'duplicates':
        uploaded = upload(ws, b'current-object')
    response = request_reuse(ws, original, uploaded, target)
    assert response.status_code == 409, response.text
    assert response.json() == dict(error=dict(code='STORAGE_ERROR',
        message='云端既有文件缺失或损坏，请联系管理员检查后重试', details=None))
    assert counts(ws) == before
    if damage == 'missing':
        assert not path.exists()
    elif damage == 'corrupt':
        assert path.read_bytes() == b'corrupt-object'
    elif damage == 'symlink':
        assert path.is_symlink() and outside.read_bytes() == b'current-object'
    elif damage == 'directory':
        assert path.is_dir()


@pytest.mark.parametrize('target', ['duplicates', 'publish', 'unchanged'])
def test_folder_reuse_revalidates_canonical_content_and_archive_digest(space, target):
    ws = space['ws']
    uploaded, digest = folder_upload(ws, zipfile.ZIP_STORED)
    _, original = publish(ws, uploaded, doc_type='folder', content_hash=digest, manifest_hash=uploaded['sha256'])
    wrong = io.BytesIO()
    with zipfile.ZipFile(wrong, 'w') as archive:
        archive.writestr('document.txt', b'other-data')
    wrong_bytes = wrong.getvalue()
    other_path = object_store.object_path(sha(wrong_bytes))
    other_path.parent.mkdir(parents=True, exist_ok=True)
    other_path.write_bytes(wrong_bytes)
    with SessionLocal() as db:
        version = db.get(CloudVersion, original['version_id'])
        version.manifest_hash = sha(wrong_bytes)
        version.file_size = len(wrong_bytes)
        version.storage_key = object_store.storage_key_for(version.manifest_hash)
        db.commit()
    before = counts(ws)
    response = request_reuse(ws, original, uploaded, target, content_hash=digest,
                             manifest_hash=uploaded['sha256'], doc_type='folder')
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] == 'STORAGE_ERROR'
    assert counts(ws) == before
    assert other_path.read_bytes() == wrong_bytes


def test_unreadable_private_entity_is_not_inspected_or_exposed_to_ungranted_admin(space):
    ws = space['ws']
    uploaded = upload(ws, b'private-object')
    publish(ws, uploaded, visibility='restricted')
    path = object_store.object_path(uploaded['sha256'])
    path.unlink()
    as_user(space['admin'])
    assert duplicates(ws, uploaded['sha256']) == []


@pytest.mark.parametrize('result_kind', ['duplicate', 'unchanged'])
@pytest.mark.parametrize('route', ['replay', 'result'])
def test_replaying_no_content_change_result_rechecks_its_entity_before_claiming_reuse(space, result_kind, route):
    ws = space['ws']
    uploaded = upload(ws, b'check-again')
    _, original = publish(ws, uploaded)
    if result_kind == 'duplicate':
        body, result = publish(ws, uploaded)
        url = f'/api/workspaces/{ws}/publications'
    else:
        body, result = submit(ws, original, uploaded)
        url = f"/api/workspaces/{ws}/documents/{original['document_id']}/versions"
    assert result['code'] in ('DUPLICATE_CONTENT', 'CONTENT_UNCHANGED')
    object_store.object_path(uploaded['sha256']).unlink()
    before = counts(ws)
    response = (client.post(url, json=body) if route == 'replay' else
                client.get(f"/api/workspaces/{ws}/publications/{body['request_id']}"))
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] == 'STORAGE_ERROR'
    assert counts(ws) == before
