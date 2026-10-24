"""Publication target comes from the original link and verified remote access."""
import json
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app import database
from backend.app.api import sharing
from backend.app.config import settings
from backend.app.core import cloud_client
from backend.app.models import Document, ShareLink
from main import app

client = TestClient(app)
SERVER = '4d5a22f0-2f80-486c-aa24-51c1fc76760b'


@pytest.fixture
def target_library(tmp_path, monkeypatch):
    library = tmp_path / 'library'; library.mkdir()
    monkeypatch.setattr(settings, 'library_path', str(library))
    database.rebind_engine(); database.init_db()
    config = database.load_library_config()
    config.update(cloud_api_url='http://target.invalid', cloud_access_token='token',
                  cloud_server_id=SERVER, active_workspace_id='browse-b')
    database.save_library_config(config)
    with database.SessionLocal() as db:
        db.add(Document(id='local', name='本机资料', status='organized'))
        db.flush()
        db.add(ShareLink(local_document_id='local', server_url='http://target.invalid',
                         server_id=SERVER, account_id='actor', workspace_id='source-a',
                         shared_document_id='cloud-original'))
        db.commit()
    yield
    database.engine.dispose()


@pytest.fixture
def target_remote(monkeypatch):
    calls = []
    def install(*, actor='actor', server=SERVER, denied=False, contribute=True, expired=False):
        def handler(req):
            calls.append(req.url.path)
            if req.url.path == '/api/server-info':
                return httpx.Response(200, json=dict(server_id=server, protocol_major=1,
                    capabilities=['explicit_publication_scope']))
            if req.url.path == '/api/users/me':
                if expired and req.headers.get('Authorization') == 'Bearer token':
                    return httpx.Response(401, json=dict(detail='expired'))
                return httpx.Response(200, json=dict(id=actor))
            if req.url.path == '/api/auth/refresh':
                return httpx.Response(200, json=dict(access_token='renewed-token', refresh_token='rotated-refresh'))
            if req.url.path == '/api/workspaces/source-a/documents/cloud-original':
                if denied: return httpx.Response(403, json=dict(detail='无权访问'))
                return httpx.Response(200, json=dict(id='cloud-original', workspace_id='source-a',
                    name='原云资料', capabilities=dict(contribute=contribute)))
            if req.url.path == '/api/workspaces/source-a':
                return httpx.Response(200, json=dict(id='source-a', name='原资料库', team_id='team-a'))
            if req.url.path == '/api/teams/team-a':
                return httpx.Response(200, json=dict(id='team-a', name='原组织'))
            return httpx.Response(404)
        http = httpx.Client(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(cloud_client, '_http', http)
        monkeypatch.setattr(sharing, '_http', http)
        return calls
    return install


def test_source_target_uses_original_link_without_changing_browse_library(target_library, target_remote):
    calls = target_remote()
    response = client.get('/api/sharing/links/local/target')
    assert response.status_code == 200, response.text
    assert response.json() == dict(workspace_id='source-a', workspace_name='原资料库', team_id='team-a',
        team_name='原组织', shared_document_id='cloud-original', shared_document_name='原云资料', can_contribute=True)
    assert not any('browse-b' in route for route in calls)
    assert database.load_library_config()['active_workspace_id'] == 'browse-b'
    assert response.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('which', ['account', 'server'])
def test_foreign_identity_never_exposes_original_names(target_library, target_remote, which):
    calls = target_remote(actor='other' if which == 'account' else 'actor',
                          server='b835928f-762e-4b2b-a071-7ce7df53a09c' if which == 'server' else SERVER)
    response = client.get('/api/sharing/links/local/target')
    assert response.status_code == 409
    assert '原云资料' not in response.text and '原资料库' not in response.text
    assert not any('/documents/' in route for route in calls)


def test_revoked_read_access_does_not_expose_target_names(target_library, target_remote):
    target_remote(denied=True)
    response = client.get('/api/sharing/links/local/target')
    assert response.status_code == 403
    assert '原云资料' not in response.text and '原资料库' not in response.text


def test_read_only_original_target_is_explicit(target_library, target_remote):
    target_remote(contribute=False)
    assert client.get('/api/sharing/links/local/target').json()['can_contribute'] is False


def test_local_link_inspection_remains_offline(target_library, monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('local links must not call cloud')
    monkeypatch.setattr(sharing, '_verified_identity', forbidden)
    response = client.get('/api/sharing/links/local')
    assert response.status_code == 200 and response.json()['workspace_id'] == 'source-a'


@pytest.mark.parametrize('expected', [dict(expected_workspace_id='browse-b'), dict(expected_document_id='changed')])
def test_update_stops_if_original_link_changed_after_dialog_open(target_library, monkeypatch, expected):
    def forbidden(*args, **kwargs): raise AssertionError('changed link must stop before cloud calls')
    monkeypatch.setattr(sharing, '_verified_identity', forbidden)
    response = client.post('/api/sharing/submit-version', json=dict(doc_id='local', confirmed=True, **expected))
    assert response.status_code == 409
    assert '原共享关联已变化' in response.json()['detail']


def test_target_identity_never_copies_original_config_to_new_library(
        target_library, target_remote, tmp_path, monkeypatch):
    target_remote()
    original_path = Path(settings.config_file)
    original_config = database.load_library_config()
    other_library = tmp_path / 'other'
    other_path = other_library / '.teamhub' / 'config.json'
    other_config = {**original_config, 'cloud_access_token': 'bob-token',
                    'active_workspace_id': 'bob-library'}
    database.write_json_config(other_path, other_config)
    load = sharing.load_library_config
    reads = 0

    def switch_after_latest_read():
        nonlocal reads
        config = load()
        reads += 1
        if reads == 2:
            settings.library_path = str(other_library)
        return config

    monkeypatch.setattr(sharing, 'load_library_config', switch_after_latest_read)
    response = client.get('/api/sharing/links/local/target')
    assert response.status_code in (200, 409), response.text
    assert json.loads(original_path.read_text()) == original_config
    assert json.loads(other_path.read_text()) == other_config


def test_late_identity_cannot_mutate_legacy_link_after_personal_library_switch(
        target_library, target_remote, tmp_path, monkeypatch):
    target_remote()
    original_path = Path(settings.config_file)
    original_config = database.load_library_config()
    other_library = tmp_path / 'other'
    other_path = other_library / '.teamhub' / 'config.json'
    other_config = {**original_config, 'personal_library_name': 'other'}
    database.write_json_config(other_path, other_config)

    def switch_after_account_confirmation(config):
        settings.library_path = str(other_library)
        return {'id': 'actor'}

    monkeypatch.setattr(sharing, '_ensure_fresh_token', switch_after_account_confirmation)
    with database.SessionLocal() as db:
        link = db.query(ShareLink).filter_by(local_document_id='local').one()
        link.server_id = None
        link.account_id = None
        db.commit()
        with pytest.raises(HTTPException) as exc:
            sharing._verified_identity(original_config, link)
        assert exc.value.status_code == 409
        assert link.server_id is None and link.account_id is None
        db.commit()
    assert json.loads(original_path.read_text()) == original_config
    assert json.loads(other_path.read_text()) == other_config


def test_identity_rejects_link_from_different_personal_database(
        target_library, target_remote, tmp_path, monkeypatch):
    calls = target_remote()
    original_config = database.load_library_config()
    with database.SessionLocal() as db:
        link = db.query(ShareLink).filter_by(local_document_id='local').one()
        other_library = tmp_path / 'other'
        database.write_json_config(other_library / '.teamhub' / 'config.json', original_config)
        monkeypatch.setattr(settings, 'library_path', str(other_library))
        with pytest.raises(HTTPException) as exc:
            sharing._verified_identity(original_config, link)
        assert exc.value.status_code == 409
    assert not calls, 'a foreign personal database must stop before cloud calls'


def test_original_target_accepts_normal_same_session_token_refresh(target_library, target_remote):
    config = database.load_library_config()
    config['cloud_refresh_token'] = 'refresh-token'
    database.save_library_config(config, strict=True)
    calls = target_remote(expired=True)
    response = client.get('/api/sharing/links/local/target')
    assert response.status_code == 200, response.text
    assert response.json()['shared_document_id'] == 'cloud-original'
    assert calls.count('/api/auth/refresh') == 1
    saved = database.load_library_config()
    assert saved['cloud_access_token'] == 'renewed-token'
    assert saved['cloud_refresh_token'] == 'rotated-refresh'
    assert saved['active_workspace_id'] == 'browse-b'
