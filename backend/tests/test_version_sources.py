"""C5 durable identities and provenance, independent of mutable local history."""
import json

import pytest

from backend.app.core import cloud_client
from backend.app.models import Document, RemoteOperation, VersionSource
from backend.app.services import remote_update, versions
from backend.tests.test_remote_update import local, _document, _operation, _target


def test_frozen_payload_rejects_mutation_and_survives_session_restart(local):
    _, db = local
    doc, _, _ = _document(local)
    _, remote = _target(local, 'target')
    operation = _operation(db, doc, remote)
    operation_id = operation.id
    doc_id = doc.id
    db.expunge_all()
    doc = db.get(Document, doc_id)
    same = _operation(db, doc, remote)
    assert same.id == operation_id
    with pytest.raises(remote_update.RemoteUpdateError):
        _operation(db, doc, {**remote, 'id': 'other'})
    assert db.query(RemoteOperation).count() == 1


def test_delivery_source_does_not_grant_shared_link_or_rewrite_version(local):
    _, db = local
    doc, link, _ = _document(local)
    db.delete(link)
    db.commit()
    version = versions.snapshot_working_copy(db, doc)[0]
    created_at, note = version.created_at, version.note
    kwargs = dict(server_id='server', account_id='actor', workspace_id='workspace',
                  source_type='delivery', delivery_id='delivery', item_id='item')
    first = remote_update.record_source(db, doc, version, **kwargs)
    assert remote_update.record_source(db, doc, version, **kwargs).id == first.id
    db.commit()
    from backend.app.models import ShareLink
    assert db.query(ShareLink).count() == 0
    assert version.created_at == created_at and version.note == note


def test_base_survives_deleting_noncurrent_source_version(local):
    _, db = local
    doc, link, work = _document(local)
    version = versions.snapshot_working_copy(db, doc)[0]
    remote_update.record_source(db, doc, version, server_id='server', account_id='actor',
        workspace_id='workspace', source_type='shared', shared_document_id='remote-doc',
        shared_version_id='remote-1')
    work.write_text('second')
    versions.snapshot_working_copy(db, doc)
    db.commit()
    source_id = version.id
    db.delete(version)
    db.commit()
    assert link.base_version_id == 'remote-1'
    assert db.query(VersionSource).one().local_version_id == source_id


def test_link_refuses_account_or_server_change_but_accepts_verified_address_move(local):
    _, db = local
    _, link, _ = _document(local)
    with pytest.raises(remote_update.RemoteUpdateError):
        remote_update.bind_link(link, server_id='other', account_id='actor', server_url='http://moved')
    with pytest.raises(remote_update.RemoteUpdateError):
        remote_update.bind_link(link, server_id='server', account_id='other', server_url='http://moved')
    remote_update.bind_link(link, server_id='server', account_id='actor', server_url='http://moved')
    assert link.server_url == 'http://moved'


def test_identity_probe_sends_no_credentials_and_rejects_mismatch(monkeypatch):
    calls = []

    class Response:
        status_code = 200
        def json(self):
            return {'server_id': 'wrong', 'protocol_major': 1,
                    'capabilities': ['explicit_publication_scope']}

    def probe(url, **kwargs):
        calls.append(kwargs)
        return Response()

    monkeypatch.setattr(cloud_client._http, 'get', probe)
    with pytest.raises(cloud_client.CloudIdentityError):
        cloud_client.verify_server_identity({'cloud_api_url': 'http://moved',
            'cloud_access_token': 'secret', 'cloud_server_id': 'original'})
    assert 'headers' not in calls[0] and 'auth' not in calls[0]


def test_backup_relocates_frozen_request_and_publication_journal(local, tmp_path):
    import sqlite3
    from pathlib import Path
    from backend.app.core.backup import backup_library, restore_library
    library, db = local
    doc, _, _ = _document(local)
    version = versions.snapshot_working_copy(db, doc)[0]
    payload = {'object_path': version.file_path, 'body': {'content_hash': version.sha256_hash}}
    remote_update.freeze_operation(db, server_id='server', account_id='actor', workspace_id='workspace',
        request_id='send', kind='delivery_send', local_document_id=doc.id, payload=payload)
    journals = library / '.teamhub' / 'publications'
    journals.mkdir()
    (journals / 'request.json').write_text(json.dumps(payload))
    backup, restored = tmp_path / 'backup', tmp_path / 'restored'
    backup_library(library, backup)
    restore_library(backup, restored)
    with sqlite3.connect(restored / '.teamhub' / 'db.sqlite') as conn:
        frozen = json.loads(conn.execute('SELECT input_json FROM remote_operations').fetchone()[0])
    assert Path(frozen['object_path']).is_relative_to(restored)
    assert Path(frozen['object_path']).read_text() == 'a'
    restored_journal = json.loads((restored / '.teamhub' / 'publications' / 'request.json').read_text())
    assert restored_journal['object_path'] == frozen['object_path']


def test_backup_refuses_unfinished_external_recovery_content(local, tmp_path):
    from backend.app.core.backup import backup_library
    library, db = local
    doc, _, _ = _document(local)
    _, remote = _target(local, 'target')
    operation = _operation(db, doc, remote)
    operation.status = 'installing'
    operation.recovery_json = json.dumps({'work': str(tmp_path / 'external' / 'file'),
                                          'backup': str(tmp_path / 'external' / '.teamhub-update-id' / 'file')})
    db.commit()
    with pytest.raises(ValueError, match='库外恢复内容'):
        backup_library(library, tmp_path / 'backup')
    assert not (tmp_path / 'backup').exists()


def test_identity_rejects_unknown_protocol_before_credentials(monkeypatch):
    class Response:
        status_code = 200
        def json(self):
            return {'server_id': 'same', 'protocol_major': 2,
                    'capabilities': ['explicit_publication_scope']}
    monkeypatch.setattr(cloud_client._http, 'get', lambda *a, **k: Response())
    with pytest.raises(cloud_client.CloudIdentityError, match='协议'):
        cloud_client.verify_server_identity({'cloud_server_id': 'same'})


@pytest.mark.parametrize('url', ['https://other.example/tenant/api/users/me',
    'https://safe.example/tenant/../outside', 'https://safe.example.evil/tenant/api',
    'https://user:secret@safe.example/tenant/api', 'https://safe.example/tenant2/api'])
def test_cloud_request_refuses_outside_configured_origin_and_base(url, monkeypatch):
    calls = []
    monkeypatch.setattr(cloud_client._http, 'request', lambda *a, **k: calls.append((a, k)))
    with pytest.raises(cloud_client.CloudIdentityError):
        cloud_client.cloud_req('GET', url, {'cloud_api_url': 'https://safe.example/tenant',
                                          'cloud_access_token': 'not-to-be-sent'})
    assert not calls


def test_backup_rejects_traversal_in_frozen_object_paths(local, tmp_path):
    from backend.app.core.backup import backup_library, rewrite_publication_paths
    library, db = local
    doc, _, _ = _document(local)
    operation = remote_update.freeze_operation(db, server_id='server', account_id='actor',
        workspace_id='workspace', request_id='bad-path', kind='delivery_send', local_document_id=doc.id,
        payload={'object_path': str(library / '.teamhub' / 'objects' / '..' / 'config.json')})
    with pytest.raises(ValueError):
        backup_library(library, tmp_path / 'backup')
    journals = tmp_path / 'journals'
    journals.mkdir()
    (journals / 'request.json').write_text(operation.input_json)
    with pytest.raises(ValueError):
        rewrite_publication_paths(journals, library, tmp_path / 'restored')
