"""C5 real HTTP integration against a temporary cloud process."""
import uuid

import backend.app.database as database
from backend.app.models import VersionSource, RemoteOperation
from backend.app.api import sharing
from backend.tests.test_m4_sharing_e2e import cloud_server, library, client, _ingest


def test_observe_fail_receive_retry_and_old_content_confirmation(library, cloud_server, tmp_path, monkeypatch):
    source = tmp_path / 'collaboration.txt'
    source.write_text('initial')
    local_a = _ingest(source)
    published = client.post('/api/sharing/publish', json={'visibility': 'workspace', 
        'doc_id': local_a['id'], 'workspace_id': cloud_server.workspace_id, 'confirmed': True})
    assert published.status_code == 200, published.text
    shared = published.json()
    # Two actors share one fixture library: make A's current entity distinct so
    # B's intake represents another user's absent file, not a duplicate import.
    (library / local_a['storage_path']).write_text('A private working edit')
    pulled = client.post('/api/sharing/pull', json={
        'cloud_doc_id': shared['document_id'], 'workspace_id': cloud_server.workspace_id})
    assert pulled.status_code == 200, pulled.text
    b_id = pulled.json()['document_id']
    local_b = client.get(f'/api/documents/{b_id}').json()
    (library / local_a['storage_path']).write_text('remote change')
    submitted = client.post('/api/sharing/submit-version', json={'doc_id': local_a['id'], 'confirmed': True})
    assert submitted.status_code == 200, submitted.text
    target = submitted.json()['version_id']
    observed = client.get(f'/api/sharing/links/{b_id}/remote-status')
    assert observed.status_code == 200, observed.text
    assert observed.json()['base_version_id'] == shared['version_id']
    assert observed.json()['observed_version_id'] == target
    work = library / local_b['storage_path']
    work.write_text('private changes')
    request = {'doc_id': b_id, 'request_id': str(uuid.uuid4()), 'confirmed': True}
    original_download = sharing._download_fixed
    from fastapi import HTTPException
    monkeypatch.setattr(sharing, '_download_fixed', lambda *a, **k: (_ for _ in ()).throw(HTTPException(422, 'checksum')))
    failure = client.post('/api/sharing/update-local', json=request)
    assert failure.status_code == 422 and work.read_text() == 'private changes'
    assert client.get(f'/api/sharing/links/{b_id}').json()['base_version_id'] == shared['version_id']
    monkeypatch.setattr(sharing, '_download_fixed', original_download)
    received = client.post('/api/sharing/update-local', json=request)
    assert received.status_code == 200, received.text
    assert received.json()['version_number'] == 3
    assert work.read_text() == 'remote change'
    work.write_text('edit after success')
    assert client.post('/api/sharing/update-local', json=request).json() == received.json()
    assert work.read_text() == 'edit after success'
    with database.SessionLocal() as db:
        assert db.query(VersionSource).filter_by(local_document_id=b_id).count() == 2
        assert db.query(RemoteOperation).filter_by(request_id=request['request_id']).one().status == 'succeeded'
    first = client.get(f'/api/documents/{b_id}/versions').json()[-1]
    restored = client.post(f"/api/documents/{b_id}/versions/{first['id']}/restore")
    assert restored.status_code == 200, restored.text
    refused = client.post('/api/sharing/submit-version', json={'doc_id': b_id, 'confirmed': True})
    assert refused.status_code == 409
    assert refused.json()['error']['code'] == 'OLD_CONTENT_CONFIRMATION_REQUIRED'
    assert client.get(f'/api/sharing/links/{b_id}').json()['base_version_id'] == target
    allowed = client.post('/api/sharing/submit-version', json={
        'doc_id': b_id, 'confirmed': True, 'confirm_old_content': True})
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()['version_id'] != shared['version_id']


def test_publication_requires_explicit_scope(library, tmp_path):
    source = tmp_path / 'private.txt'
    source.write_text('private contents')
    doc = _ingest(source)
    response = client.post('/api/sharing/publish', json={'doc_id': doc['id'], 'confirmed': True})
    assert response.status_code == 422
