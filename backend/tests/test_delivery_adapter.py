"""Desktop ↔ real isolated cloud: receipt requires explicit intake."""
import uuid
from pathlib import Path
import pytest
from backend.tests.test_m4_sharing_e2e import cloud_server, library, client, _ingest
from backend.app import database
from backend.app.models import Document, ShareLink, VersionSource, RemoteOperation


def test_send_then_explicit_intake_and_recall_preserves_local(library, cloud_server, tmp_path):
    ws = cloud_server.workspace_id
    source = tmp_path / 'delivery.txt'
    source.write_text('fixed payload', encoding='utf-8')
    doc = _ingest(source)
    members = client.get('/api/deliveries/members', params={'workspace_id': ws})
    assert members.status_code == 200, members.text
    recipient = next(m for m in members.json() if m['display_name'] == 'B')
    body = dict(doc_id=doc['id'], workspace_id=ws, recipient_ids=[recipient['id']], request_id=str(uuid.uuid4()))
    response = client.post('/api/deliveries/send', json=body)
    assert response.status_code == 200, response.text
    sent = response.json()
    assert client.post('/api/deliveries/send', json=body).json()['id'] == sent['id']
    response = client.post('/api/sync/login', json={'email':'member-b@example.com','password':cloud_server.password})
    assert response.status_code == 200, response.text
    with database.SessionLocal() as db:
        before = db.query(Document).count()
    inbox = client.get('/api/deliveries', params=dict(workspace_id=ws)).json()
    assert inbox['items'][0]['id'] == sent['id']
    with database.SessionLocal() as db:
        assert db.query(Document).count() == before
    intake_body = dict(workspace_id=ws, request_id=str(uuid.uuid4()))
    path = f"/api/deliveries/{sent['id']}/items/{sent['items'][0]['id']}/intake"
    # Sender and receiver share this fixture library; a changed sender working
    # entity lets this test exercise actual receiver intake and receipt replay.
    (library / doc['storage_path']).write_text('sender later edit')
    received = client.post(path, json=intake_body)
    assert received.status_code == 200, received.text
    assert client.post(path, json=intake_body).json() == received.json()
    with database.SessionLocal() as db:
        result = db.get(Document, received.json()['document_id'])
        assert result.status == 'pending' and result.storage_area == 'pending'
        assert not db.query(ShareLink).filter_by(local_document_id=result.id).first()
        provenance = db.query(VersionSource).filter_by(local_document_id=result.id).one()
        assert provenance.source_type == 'delivery' and provenance.delivery_id == sent['id']
        from backend.app.core.storage_paths import document_path
        work = document_path(result)
        assert work.read_text() == 'fixed payload'
    client.post('/api/sync/login', json={'email':cloud_server.email,'password':cloud_server.password})
    assert client.post(f"/api/deliveries/{sent['id']}/recall", params={'workspace_id':ws}).status_code == 200
    assert work.read_text() == 'fixed payload'


def test_frozen_send_does_not_change_on_retry(library, cloud_server, tmp_path, monkeypatch):
    from backend.app.api import deliveries
    source = tmp_path / 'retry.txt'
    source.write_text('initial')
    doc = _ingest(source)
    ws = cloud_server.workspace_id
    recipient = next(m for m in client.get('/api/deliveries/members', params={'workspace_id':ws}).json() if m['display_name']=='B')
    body = dict(doc_id=doc['id'], workspace_id=ws, recipient_ids=[recipient['id']], request_id=str(uuid.uuid4()))
    original = deliveries._http.post
    import httpx
    with monkeypatch.context() as patch:
        patch.setattr(deliveries._http, 'post', lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError('offline')))
        assert client.post('/api/deliveries/send', json=body).status_code == 502
    from backend.app.core.storage_paths import document_path
    with database.SessionLocal() as db:
        work = document_path(db.get(Document,doc['id']))
    work.write_text('later edit')
    result = client.post('/api/deliveries/send', json=body)
    assert result.status_code == 200, result.text
    import hashlib
    assert result.json()['items'][0]['content_hash'] == hashlib.sha256(b'initial').hexdigest()
    changed = {**body, 'note': 'different'}
    assert client.post('/api/deliveries/send', json=changed).status_code == 409


def test_folder_intake_survives_metadata_failure(library, cloud_server, tmp_path, monkeypatch):
    from sqlalchemy.orm import Session
    from backend.app.core.storage_paths import document_path
    folder = tmp_path/'folder'
    folder.mkdir()
    (folder/'empty').mkdir()
    (folder/'diagram.txt').write_text('drawing')
    doc = _ingest(folder, is_folder=True)
    ws = cloud_server.workspace_id
    myself = next(m for m in client.get('/api/deliveries/members',params={'workspace_id':ws}).json() if m['display_name']=='E2E')
    sent = client.post('/api/deliveries/send',json=dict(doc_id=doc['id'], workspace_id=ws,
             recipient_ids=[myself['id']],request_id=str(uuid.uuid4())))
    assert sent.status_code == 200, sent.text
    item = sent.json()['items'][0]
    (library / doc['storage_path'] / 'sender-only.txt').write_text('later sender work')
    path = f"/api/deliveries/{sent.json()['id']}/items/{item['id']}/intake"
    request = dict(workspace_id=ws, request_id=str(uuid.uuid4()))
    original = Session.commit
    def fail_final(self):
        # The operation's result is only populated in the final atomic DB commit.
        if any(isinstance(row, RemoteOperation) and row.status=='succeeded' for row in self.dirty):
            raise OSError('simulated metadata disk failure')
        return original(self)
    with monkeypatch.context() as patch:
        patch.setattr(Session,'commit',fail_final)
        with pytest.raises(OSError):
            client.post(path,json=request)
    resumed = client.post(path,json=request)
    assert resumed.status_code == 200, resumed.text
    with database.SessionLocal() as db:
        received = db.get(Document,resumed.json()['document_id'])
        assert received.status == 'pending'
        work = document_path(received)
        assert (work/'diagram.txt').read_text() == 'drawing'
        assert (work/'empty').is_dir()
        assert db.query(VersionSource).filter_by(local_document_id=received.id).count() == 1
