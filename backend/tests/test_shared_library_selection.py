"""Library selection state belongs to the verified server, account and personal library."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock
import httpx
import pytest
from fastapi import HTTPException
from backend.app.config import settings
from backend.app.database import save_library_config, load_library_config
from backend.app import database
from backend.app.api import sync
from backend.app.api.schemas import CloudLoginRequest, CloudWorkspaceRequest

@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'library_path', str(tmp_path / 'original'))
    initial = {'cloud_api_url': 'http://127.0.0.1:9100', 'cloud_access_token': 'old', 'active_workspace_id': 'old-account-library', 'active_workspace_name': '旧库'}
    save_library_config(initial, strict=True)
    monkeypatch.setattr(sync, 'verify_server_identity', lambda _: 'server-a', raising=False)
    return initial

def login_http(callback=None):
    payloads = {'/api/auth/login': {'access_token': 'new', 'refresh_token': 'refresh'}, '/api/users/me': {'id': 'bob'}, '/api/teams': []}
    def handle(request):
        if callback: callback(request)
        return httpx.Response(200, json=payloads[request.url.path])
    return httpx.Client(transport=httpx.MockTransport(handle))

def test_login_clears_previous_accounts_active_library(config, monkeypatch):
    with login_http() as client:
        monkeypatch.setattr(sync, '_http', client)
        result = sync.cloud_login(CloudLoginRequest(email='bob@example.test', password='test-password'))
    assert result['server_id'] == 'server-a'
    assert not load_library_config().get('active_workspace_id')
    assert load_library_config()['cloud_server_id'] == 'server-a'

def test_late_login_cannot_write_a_different_personal_library(config, tmp_path, monkeypatch):
    def switch(request):
        if request.url.path == '/api/auth/login':
            settings.library_path = str(tmp_path / 'other')
            save_library_config({'cloud_api_url': config['cloud_api_url'], 'cloud_access_token': 'other-account'}, strict=True)
    with login_http(switch) as client:
        monkeypatch.setattr(sync, '_http', client)
        with pytest.raises(HTTPException) as exc:
            sync.cloud_login(CloudLoginRequest(email='bob@example.test', password='test-password'))
    assert exc.value.status_code == 409
    assert load_library_config()['cloud_access_token'] == 'other-account'

def test_late_selection_cannot_write_a_different_personal_library(config, tmp_path, monkeypatch):
    def delayed(*args, **kwargs):
        settings.library_path = str(tmp_path / 'other')
        save_library_config({**config, 'active_workspace_id': 'other-library'}, strict=True)
        return httpx.Response(200, json={'workspace_name': '新库'})
    monkeypatch.setattr(sync, 'cloud_req', delayed)
    with pytest.raises(HTTPException) as exc:
        sync.set_workspace(CloudWorkspaceRequest(workspace_id='chosen', workspace_name='Chosen'), MagicMock())
    assert exc.value.status_code == 409
    assert load_library_config()['active_workspace_id'] == 'other-library'

def test_selection_failure_preserves_previous_library(config, monkeypatch):
    monkeypatch.setattr(sync, 'cloud_req', lambda *a, **k: httpx.Response(403, json={}))
    with pytest.raises(HTTPException) as exc:
        sync.set_workspace(CloudWorkspaceRequest(workspace_id='denied', workspace_name='Denied'), MagicMock())
    assert exc.value.status_code == 403
    assert load_library_config()['active_workspace_id'] == 'old-account-library'

def test_status_exposes_verified_server_identity_but_no_tokens(config, monkeypatch):
    monkeypatch.setattr(sync, 'cloud_req', lambda *a, **k: httpx.Response(200, json={'id': 'alice'}))
    result = sync.get_sync_status(MagicMock())
    assert result['server_id'] == 'server-a'
    assert 'cloud_access_token' not in result

def test_legacy_status_records_verified_identity_after_confirming_account(config, monkeypatch):
    monkeypatch.setattr(sync, 'cloud_req', lambda *a, **k: httpx.Response(200, json={'id': 'alice'}))
    sync.get_sync_status(MagicMock())
    assert load_library_config()['cloud_server_id'] == 'server-a'

def test_library_list_failure_is_not_reported_as_no_libraries(config, monkeypatch):
    def fetch(method, url, supplied, **kwargs):
        return httpx.Response(200, json=[{'id': 'team', 'name': '组织'}]) if url.endswith('/api/teams') else httpx.Response(503, json={})
    monkeypatch.setattr(sync, 'cloud_req', fetch)
    with pytest.raises(HTTPException) as exc:
        sync.list_cloud_workspaces()
    assert exc.value.status_code == 503

def test_login_is_not_persisted_when_library_discovery_fails(config, monkeypatch):
    original = load_library_config()
    def handle(request):
        payload = {'/api/auth/login': {'access_token': 'new'}, '/api/users/me': {'id': 'bob'}, '/api/teams': [{'id': 'team'}]}.get(request.url.path)
        return httpx.Response(200, json=payload) if payload is not None else httpx.Response(503, json={})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(sync, '_http', client)
        with pytest.raises(HTTPException) as exc:
            sync.cloud_login(CloudLoginRequest(email='bob@example.test', password='test-password'))
    assert exc.value.status_code == 503
    assert load_library_config() == original

def test_selection_rejects_server_address_changed_while_waiting(config, monkeypatch):
    def fetch(*args, **kwargs):
        save_library_config({**config, 'cloud_api_url': 'http://127.0.0.1:9200'}, strict=True)
        return httpx.Response(200, json={})
    monkeypatch.setattr(sync, 'cloud_req', fetch)
    with pytest.raises(HTTPException) as exc:
        sync.set_workspace(CloudWorkspaceRequest(workspace_id='chosen', workspace_name='Chosen'), MagicMock())
    assert exc.value.status_code == 409
    assert load_library_config()['active_workspace_id'] == 'old-account-library'

def test_selection_reports_write_failure(config, monkeypatch):
    monkeypatch.setattr(sync, 'cloud_req', lambda *a, **k: httpx.Response(200, json={}))
    def fail(*args): raise OSError('read-only')
    monkeypatch.setattr(sync, 'write_json_config', fail)
    with pytest.raises(OSError):
        sync.set_workspace(CloudWorkspaceRequest(workspace_id='chosen', workspace_name='Chosen'), MagicMock())
    assert load_library_config()['active_workspace_id'] == 'old-account-library'


def test_logout_reports_persistence_failure_without_claiming_success(config, monkeypatch):
    def fail(*args):
        raise PermissionError('read-only')
    monkeypatch.setattr(database, 'write_json_config', fail)
    monkeypatch.setattr(sync, 'write_json_config', fail)
    with pytest.raises(HTTPException) as exc:
        sync.cloud_logout()
    assert exc.value.status_code == 500
    assert '退出' in exc.value.detail
    assert load_library_config()['cloud_access_token'] == config['cloud_access_token']


def test_new_login_waits_for_pending_logout_save(config, monkeypatch):
    saving_logout = Event()
    finish_logout = Event()
    posting_login = Event()
    write = database.write_json_config

    def paused_write(path, value):
        if not value.get('cloud_access_token'):
            saving_logout.set()
            assert finish_logout.wait(5), 'logout was never released'
        write(path, value)

    def record_login(request):
        if request.url.path == '/api/auth/login':
            posting_login.set()

    monkeypatch.setattr(database, 'write_json_config', paused_write)
    monkeypatch.setattr(sync, 'write_json_config', paused_write)
    with login_http(record_login) as client, ThreadPoolExecutor(2) as pool:
        monkeypatch.setattr(sync, '_http', client)
        logout = pool.submit(sync.cloud_logout)
        assert saving_logout.wait(5)
        login = pool.submit(sync.cloud_login, CloudLoginRequest(
            email='bob@example.test', password='test-password'))
        try:
            assert not posting_login.wait(0.1), 'login overtook the pending logout save'
        finally:
            finish_logout.set()
        assert logout.result(timeout=5)['message'] == '已退出登录'
        assert login.result(timeout=5)['user']['id'] == 'bob'
    assert load_library_config()['cloud_access_token'] == 'new'


@pytest.mark.parametrize('operation', ['login', 'selection', 'status'])
def test_switch_after_session_check_cannot_copy_another_library_config(
        config, tmp_path, monkeypatch, operation):
    original_path = Path(settings.config_file)
    other_library = tmp_path / 'other'
    other_path = other_library / '.teamhub' / 'config.json'
    other_config = {**config, 'cloud_access_token': 'bob-token',
                    'active_workspace_id': 'bob-library'}
    database.write_json_config(other_path, other_config)
    same_session = sync._same_session

    def switch_immediately_after_check(*args, **kwargs):
        matches = same_session(*args, **kwargs)
        if matches:
            settings.library_path = str(other_library)
        return matches

    monkeypatch.setattr(sync, '_same_session', switch_immediately_after_check)
    monkeypatch.setattr(sync, 'cloud_req', lambda *a, **k: httpx.Response(
        200, json={'id': 'alice', 'workspace_name': 'Chosen'}))
    with login_http() as client:
        monkeypatch.setattr(sync, '_http', client)
        if operation == 'status':
            sync.get_sync_status(MagicMock())
        else:
            with pytest.raises(HTTPException) as exc:
                if operation == 'login':
                    sync.cloud_login(CloudLoginRequest(
                        email='bob@example.test', password='test-password'))
                else:
                    sync.set_workspace(CloudWorkspaceRequest(
                        workspace_id='chosen', workspace_name='Chosen'), MagicMock())
            assert exc.value.status_code == 409
    assert json.loads(original_path.read_text()) == config
    assert json.loads(other_path.read_text()) == other_config


@pytest.mark.parametrize('operation', ['login', 'selection', 'status'])
def test_session_write_rejects_changed_account_in_same_personal_library(
        config, monkeypatch, operation):
    changed = {**config, 'cloud_access_token': 'other-account-token',
               'active_workspace_id': 'other-account-library'}

    def replace_account(*args, **kwargs):
        save_library_config(changed, strict=True)
        return httpx.Response(200, json={'id': 'alice', 'workspace_name': 'Chosen'})

    def replace_during_login(request):
        if request.url.path == '/api/auth/login':
            save_library_config(changed, strict=True)

    monkeypatch.setattr(sync, 'cloud_req', replace_account)
    with login_http(replace_during_login) as client:
        monkeypatch.setattr(sync, '_http', client)
        if operation == 'status':
            sync.get_sync_status(MagicMock())
        else:
            with pytest.raises(HTTPException) as exc:
                if operation == 'login':
                    sync.cloud_login(CloudLoginRequest(
                        email='bob@example.test', password='test-password'))
                else:
                    sync.set_workspace(CloudWorkspaceRequest(
                        workspace_id='chosen', workspace_name='Chosen'), MagicMock())
            assert exc.value.status_code == 409
    assert load_library_config() == changed


def test_pending_login_cannot_revive_completed_logout(config, monkeypatch):
    def logout_during_login(request):
        if request.url.path == '/api/auth/login':
            sync.cloud_logout()

    with login_http(logout_during_login) as client:
        monkeypatch.setattr(sync, '_http', client)
        with pytest.raises(HTTPException) as exc:
            sync.cloud_login(CloudLoginRequest(
                email='bob@example.test', password='test-password'))
    assert exc.value.status_code == 409
    assert 'cloud_access_token' not in load_library_config()
