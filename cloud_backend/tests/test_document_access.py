"""Document ACL boundary tests, with the shared isolated test database."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.deps import get_current_user
from app.database import SessionLocal
from app.models import (User, Team, TeamMember, Workspace, CloudDocument, DocumentAccess,
                        CloudChangeLog, CloudVersion, Delivery, DeliveryItem)
from main import app

client = TestClient(app)


@pytest.fixture
def acl_space(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'storage_dir', str(tmp_path / 'objects'))
    with SessionLocal() as db:
        team = Team(name='ACL', slug=str(uuid.uuid4()))
        users = [User(email=f'{uuid.uuid4()}@acl.test', hashed_password='x',
                      display_name=name, is_superuser=name == 'admin')
                 for name in ('creator', 'reader', 'other', 'admin')]
        db.add_all([team, *users]); db.flush()
        ws = Workspace(team_id=team.id, name='ACL')
        db.add(ws)
        db.add_all([TeamMember(team_id=team.id, user_id=u.id,
                              role='admin' if u.display_name == 'admin' else 'member') for u in users])
        db.commit()
        result = {u.display_name: u for u in users}
        result['ws'] = ws.id
        result['team'] = team.id
        yield result
    app.dependency_overrides.clear()


def as_user(user):
    app.dependency_overrides[get_current_user] = lambda: user


def publish(space, visibility='restricted'):
    as_user(space['creator'])
    prefix = f"/api/workspaces/{space['ws']}"
    upload = client.post(prefix + '/uploads?filename=secret.txt', content=b'private').json()
    body = dict(request_id=str(uuid.uuid4()), confirmed=True, name='secret', tags=['hidden'],
                upload_id=upload['upload_id'], content_hash=upload['sha256'], visibility=visibility,
                access=[dict(user_id=space['reader'].id, role='reader')])
    response = client.post(prefix + '/publications', json=body)
    assert response.status_code == 200, response.text
    return prefix, body, response.json()


def test_restricted_hidden_across_read_and_aggregate_surfaces(acl_space):
    prefix, body, result = publish(acl_space)
    doc = result['document_id']; version = result['version_id']
    for name in ('other', 'admin'):
        as_user(acl_space[name])
        for suffix in (f'/documents/{doc}', f'/documents/{doc}/versions',
                       f'/documents/{doc}/versions/{version}/download', f'/documents/{doc}/tags',
                       f'/documents/{doc}/access'):
            assert client.get(prefix + suffix).status_code == 404
        assert client.get(prefix + '/documents').json() == []
        assert client.get(prefix + '/documents/search').json()['total'] == 0
        assert client.get(prefix + '/tags').json() == []
        for kind in ('name', 'tag', 'cooccurrence'):
            assert client.get(prefix + '/documents/suggestions', params={'type': kind}).json() == []
        assert client.post(prefix + '/duplicates', json={'content_hash': body['content_hash']}).json() == []


def test_reader_cannot_write_or_manage_and_manager_revision_is_guarded(acl_space):
    prefix, _, result = publish(acl_space)
    doc = result['document_id']
    as_user(acl_space['reader'])
    assert client.get(prefix + f'/documents/{doc}').status_code == 200
    assert client.post(prefix + f'/documents/{doc}/tags', json={'add': ['no']}).status_code == 403
    assert client.get(prefix + f'/documents/{doc}/access').status_code == 403
    as_user(acl_space['creator'])
    payload = {'visibility': 'restricted', 'base_access_revision': 1, 'access': []}
    assert client.put(prefix + f'/documents/{doc}/access', json=payload).status_code == 409
    payload['access'] = [{'user_id': acl_space['creator'].id, 'role': 'manager'}]
    assert client.put(prefix + f'/documents/{doc}/access', json=payload).status_code == 200
    assert client.put(prefix + f'/documents/{doc}/access', json=payload).status_code == 409
    as_user(acl_space['reader'])
    assert client.get(prefix + f'/documents/{doc}').status_code == 404


def test_revoked_creator_cannot_replay_or_fetch_publication_result(acl_space):
    prefix, body, result = publish(acl_space)
    doc = result['document_id']
    response = client.put(prefix + f'/documents/{doc}/access', json={
        'visibility': 'restricted', 'base_access_revision': 1,
        'access': [{'user_id': acl_space['reader'].id, 'role': 'manager'}]})
    assert response.status_code == 200
    assert client.post(prefix + '/publications', json=body).status_code == 404
    assert client.get(prefix + '/publications/' + body['request_id']).status_code == 404


def test_contributor_is_explicit_and_viewer_remains_read_only(acl_space):
    prefix, _, result = publish(acl_space)
    doc = result['document_id']
    assert client.put(prefix + f'/documents/{doc}/access', json={
        'visibility': 'restricted', 'base_access_revision': 1,
        'access': [{'user_id': acl_space['creator'].id, 'role': 'manager'},
                   {'user_id': acl_space['reader'].id, 'role': 'contributor'}]}).status_code == 200
    as_user(acl_space['reader'])
    assert client.post(prefix + f'/documents/{doc}/tags', json={'add': ['allowed']}).status_code == 200
    assert client.get(prefix + f'/documents/{doc}/access').status_code == 403
    with SessionLocal() as db:
        db.get(TeamMember, (acl_space['team'], acl_space['reader'].id)).role = 'viewer'
        db.commit()
    assert client.post(prefix + f'/documents/{doc}/tags', json={'add': ['no']}).status_code == 404
    assert client.get(prefix + f'/documents/{doc}').status_code == 200


def test_inactive_manager_does_not_remove_other_grants(acl_space):
    prefix, _, result = publish(acl_space)
    with SessionLocal() as db:
        db.get(User, acl_space['creator'].id).is_active = False
        db.commit()
    assert client.get(prefix + f"/documents/{result['document_id']}").status_code == 404
    as_user(acl_space['reader'])
    assert client.get(prefix + f"/documents/{result['document_id']}").status_code == 200
    as_user(acl_space['admin'])
    assert client.get(prefix + f"/documents/{result['document_id']}/access").status_code == 404


def test_activity_trash_and_container_deletion_do_not_bypass_acl(acl_space):
    prefix, _, result = publish(acl_space)
    with SessionLocal() as db:
        db.add(CloudChangeLog(workspace_id=acl_space['ws'], sequence_number=1, entity_type='document',
                              entity_id=result['document_id'], operation='create', payload={'name': 'secret'}))
        db.commit()
    as_user(acl_space['admin'])
    assert client.get(prefix + '/activity').json() == []
    assert client.post(prefix + f"/documents/{result['document_id']}/trash").status_code == 404
    assert client.delete(prefix).status_code == 409
    assert client.delete('/api/admin/teams/' + acl_space['team']).status_code == 409
    with SessionLocal() as db:
        db.get(TeamMember, (acl_space['team'], acl_space['admin'].id)).role = 'owner'
        db.commit()
    assert client.delete('/api/teams/' + acl_space['team']).status_code == 409


def test_delivery_reference_preserves_object_after_public_document_purge(acl_space):
    prefix, _, result = publish(acl_space, 'workspace')
    with SessionLocal() as db:
        version = db.get(CloudVersion, result['version_id'])
        delivery = Delivery(team_id=acl_space['team'], workspace_id=acl_space['ws'],
                            sender_id=acl_space['creator'].id, request_id=str(uuid.uuid4()), request_hash='a'*64)
        db.add(delivery); db.flush()
        db.add(DeliveryItem(delivery_id=delivery.id, filename='secret.txt', storage_key=version.storage_key,
                            content_hash=version.content_hash, file_size=version.file_size,
                            source_document_id=result['document_id'], source_version_id=version.id))
        digest = version.content_hash
        db.commit()
    as_user(acl_space['admin'])
    assert client.post(prefix + f"/documents/{result['document_id']}/trash").status_code == 200
    response = client.post(prefix + f"/documents/{result['document_id']}/purge")
    assert response.status_code == 200 and response.json()['objects_retained'] == 1
    from app.core.object_store import object_path
    assert object_path(digest).read_bytes() == b'private'


def test_scope_is_explicit_and_upload_purpose_cannot_be_reused(acl_space):
    as_user(acl_space['creator'])
    prefix = f"/api/workspaces/{acl_space['ws']}"
    upload = client.post(prefix + '/uploads?filename=private.txt&purpose=delivery', content=b'bytes').json()
    body = dict(request_id=str(uuid.uuid4()), confirmed=True, name='private', upload_id=upload['upload_id'],
                content_hash=upload['sha256'])
    response = client.post(prefix + '/publications', json=body)
    assert response.status_code == 400 and response.json()['error']['code'] == 'SCOPE_REQUIRED'
    body['visibility'] = 'workspace'
    assert client.post(prefix + '/publications', json=body).status_code == 404


def test_server_identity_is_persistent_and_contains_no_user_data():
    first = client.get('/api/server-info').json()
    assert first == client.get('/api/server-info').json()
    assert set(first) == {'server_id', 'protocol_major', 'schema_version', 'capabilities'}


def test_concurrent_acl_updates_have_one_winner(acl_space):
    from concurrent.futures import ThreadPoolExecutor
    prefix, _, result = publish(acl_space)
    url = prefix + f"/documents/{result['document_id']}/access"
    body = {'visibility': 'restricted', 'base_access_revision': 1,
            'access': [{'user_id': acl_space['creator'].id, 'role': 'manager'}]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: client.put(url, json=body).status_code, range(2)))
    assert sorted(statuses) == [200, 409]


def test_foreign_or_inactive_grants_fail_without_partial_publication(acl_space):
    as_user(acl_space['creator'])
    prefix = f"/api/workspaces/{acl_space['ws']}"
    upload = client.post(prefix + '/uploads?filename=private.txt', content=b'bytes').json()
    with SessionLocal() as db:
        db.get(User, acl_space['reader'].id).is_active = False
        db.commit()
    for user_id in (str(uuid.uuid4()), acl_space['reader'].id):
        body = dict(request_id=str(uuid.uuid4()), confirmed=True, name='private',
                    upload_id=upload['upload_id'], content_hash=upload['sha256'], visibility='restricted',
                    access=[{'user_id': user_id, 'role': 'reader'}])
        assert client.post(prefix + '/publications', json=body).status_code == 422
    assert client.get(prefix + '/documents').json() == []
