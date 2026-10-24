"""Independent review regressions: recall race, retained identity, historical folder metadata."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from app.core.deps import get_current_user
from app.core.security import create_access_token
from app.database import SessionLocal
from app.models import User
from tests.test_deliveries import client, group, send
from tests.test_m4_publications import space, _as
from main import app


def test_recall_committed_during_hash_blocks_transfer(group, monkeypatch):
    from app.core import object_store
    response, _ = send(group)
    delivery = response.json()
    base = f"/api/workspaces/{group['ws1'].id}/deliveries"
    path = f"{base}/{delivery['id']}/items/{delivery['items'][0]['id']}/download"
    app.dependency_overrides.pop(get_current_user, None)
    sender = {'Authorization': 'Bearer ' + create_access_token({'sub': group['u1'].id})}
    receiver = {'Authorization': 'Bearer ' + create_access_token({'sub': group['b'].id})}
    hashing, released = Event(), Event()
    original = object_store.hash_file

    def blocked_hash(path):
        result = original(path)
        hashing.set()
        assert released.wait(5)
        return result

    monkeypatch.setattr(object_store, 'hash_file', blocked_hash)
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(client.get, path, headers=receiver)
        try:
            assert hashing.wait(5)
            assert client.post(f"{base}/{delivery['id']}/recall", headers=sender).status_code == 200
        finally:
            released.set()
        assert pending.result().status_code == 410


def test_committed_delivery_replay_survives_recipient_deactivation(group):
    response, body = send(group)
    with SessionLocal() as db:
        db.get(User, group['b'].id).is_active = False
        db.commit()
    replay = client.post(f"/api/workspaces/{group['ws1'].id}/deliveries", json=body)
    assert replay.status_code == 200
    assert replay.json()['id'] == response.json()['id']


def test_shared_publication_and_versions_share_delivery_quota(group):
    from app.models import Team, CloudVersion
    from tests.test_m4_publications import _upload, _publish, _uid
    response, _ = send(group)
    assert response.status_code == 200
    ws = group['ws1'].id
    with SessionLocal() as db:
        db.get(Team, group['ws1'].team_id).storage_quota = 6
        db.commit()
    upload = _upload(ws, b'xy')
    denied = _publish(ws, upload)
    assert denied.status_code == 413
    with SessionLocal() as db:
        db.get(Team, group['ws1'].team_id).storage_quota = 7
        db.commit()
    result = _publish(ws, upload).json()
    replacement = _upload(ws, b'z')
    denied = client.post(f"/api/workspaces/{ws}/documents/{result['document_id']}/versions", json={
        'request_id': _uid(), 'base_version_id': result['version_id'], 'upload_id': replacement['upload_id'],
        'content_hash': replacement['sha256'], 'confirmed': True})
    assert denied.status_code == 413
    with SessionLocal() as db:
        assert db.query(CloudVersion).filter_by(document_id=result['document_id']).count() == 1


def test_source_folder_freezes_selected_archive_metadata(group, tmp_path):
    import hashlib
    import io
    import zipfile
    from app.core import object_store
    from app.models import CloudDocument
    from tests.test_m4_publications import _upload, _uid
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as target:
        target.writestr('a.txt', b'old bytes')
    payload = archive.getvalue()
    path = tmp_path / 'folder.zip'
    path.write_bytes(payload)
    digest, count, total = object_store.inspect_folder(path)
    _as(group['u1'])
    ws = group['ws1'].id
    upload = _upload(ws, payload, 'folder.zip')
    publication = client.post(f'/api/workspaces/{ws}/publications', json={
        'request_id': _uid(), 'name': 'folder', 'doc_type': 'folder', 'visibility': 'workspace',
        'upload_id': upload['upload_id'], 'content_hash': digest,
        'manifest_hash': hashlib.sha256(payload).hexdigest(), 'confirmed': True}).json()
    with SessionLocal() as db:
        doc = db.get(CloudDocument, publication['document_id'])
        doc.file_count = 99; doc.total_size = 99999
        db.commit()
    response = client.post(f'/api/workspaces/{ws}/deliveries', json={
        'request_id': _uid(), 'recipient_ids': [group['b'].id],
        'source_document_id': publication['document_id'], 'source_version_id': publication['version_id']})
    assert response.status_code == 200, response.text
    item = response.json()['items'][0]
    assert item['file_count'] == count == 1
    assert item['total_size'] == total == len(b'old bytes')


def test_sender_demoted_to_viewer_can_still_recall(group):
    from app.models import TeamMember
    response, _ = send(group)
    with SessionLocal() as db:
        db.get(TeamMember, (group['ws1'].team_id, group['u1'].id)).role = 'viewer'
        db.commit()
    recall = client.post(f"/api/workspaces/{group['ws1'].id}/deliveries/{response.json()['id']}/recall")
    assert recall.status_code == 200 and recall.json()['status'] == 'recalled'


def test_source_purge_does_not_destroy_delivery_download(group):
    from tests.test_m4_publications import _upload, _publish, _uid
    _as(group['u1'])
    ws = group['ws1'].id
    publication = _publish(ws, _upload(ws, b'independent source')).json()
    response = client.post(f'/api/workspaces/{ws}/deliveries', json={
        'request_id': _uid(), 'recipient_ids': [group['b'].id],
        'source_document_id': publication['document_id'], 'source_version_id': publication['version_id']})
    assert response.status_code == 200, response.text
    delivery = response.json()
    _as(group['m'])
    base = f"/api/workspaces/{ws}/documents/{publication['document_id']}"
    assert client.post(base + '/trash').status_code == 200
    assert client.post(base + '/purge').json()['objects_retained'] == 1
    _as(group['b'])
    download = client.get(f"/api/workspaces/{ws}/deliveries/{delivery['id']}/items/{delivery['items'][0]['id']}/download")
    assert download.status_code == 200 and download.content == b'independent source'
