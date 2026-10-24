"""个人端续期必须持续轮换、区分失效与暂时故障，且不串写会话。"""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import httpx
import pytest
from fastapi import HTTPException

from backend.app.config import settings
from backend.app.core import cloud_client
from backend.app.database import load_library_config, save_library_config


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'library_path', str(tmp_path / 'library'))
    config = dict(cloud_api_url='http://refresh.example', cloud_access_token='old-access',
                  cloud_refresh_token='old-refresh', active_workspace_id='library-a',
                  active_workspace_name='资料库', cloud_server_id='server-a', items_per_page=25)
    save_library_config(config, strict=True)
    cloud_client._refresh_lineage.clear()
    return config


def install(monkeypatch, handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(cloud_client, '_http', client)
    return client


def test_three_consecutive_expirations_persist_each_rotated_pair(session, monkeypatch):
    state = dict(refresh='old-refresh', access=None, generation=0, expired=True)

    def handle(request):
        if request.url.path == '/api/auth/refresh':
            assert json.loads(request.content)['refresh_token'] == state['refresh']
            state['generation'] += 1
            state['access'] = f"access-{state['generation']}"
            state['refresh'] = f"refresh-{state['generation']}"
            state['expired'] = False
            return httpx.Response(200, json=dict(access_token=state['access'], refresh_token=state['refresh']))
        if state['expired'] or request.headers['authorization'] != f"Bearer {state['access']}":
            return httpx.Response(401, json=dict(detail='无效的认证令牌'))
        return httpx.Response(200, json=dict(id='actor-a'))

    with install(monkeypatch, handle):
        for number in range(1, 4):
            state['expired'] = True
            request_config = load_library_config()
            response = cloud_client.cloud_req('GET', 'http://refresh.example/api/users/me', request_config)
            assert response.status_code == 200
            assert load_library_config()['cloud_refresh_token'] == f'refresh-{number}'
            assert request_config['cloud_access_token'] == f'access-{number}'
    assert load_library_config()['active_workspace_id'] == 'library-a'


def test_missing_rotated_refresh_never_silently_reuses_revoked_value(session, monkeypatch):
    with install(monkeypatch, lambda request: httpx.Response(200, json=dict(access_token='new-access'))):
        with pytest.raises(HTTPException) as exc:
            cloud_client.refresh_token(session.copy())
    assert exc.value.status_code == 502
    assert load_library_config() == session


def test_revoked_refresh_emits_explicit_terminal_error_and_preserves_personal_settings(session, monkeypatch):
    with install(monkeypatch, lambda request: httpx.Response(401, json=dict(detail='无效或已撤销的刷新令牌'))):
        with pytest.raises(HTTPException) as exc:
            cloud_client.cloud_req('GET', 'http://refresh.example/api/users/me', session.copy())
    assert exc.value.status_code == 401
    assert exc.value.detail['error']['code'] == 'CLOUD_SESSION_EXPIRED'
    saved = load_library_config()
    assert not saved.get('cloud_access_token') and not saved.get('cloud_refresh_token')
    assert not saved.get('active_workspace_id')
    assert saved['items_per_page'] == 25 and saved['cloud_server_id'] == 'server-a'


@pytest.mark.parametrize('status', [429, 503])
def test_temporary_refresh_failure_keeps_credentials(session, monkeypatch, status):
    with install(monkeypatch, lambda request: httpx.Response(status, json=dict(detail='暂不可用'))):
        with pytest.raises(HTTPException) as exc:
            cloud_client.refresh_token(session.copy())
    assert exc.value.status_code == 503
    assert load_library_config() == session


def test_network_failure_keeps_credentials(session, monkeypatch):
    def fail(request):
        raise httpx.ConnectError('temporary network failure', request=request)
    with install(monkeypatch, fail):
        with pytest.raises(HTTPException) as exc:
            cloud_client.refresh_token(session.copy())
    assert exc.value.status_code == 503
    assert load_library_config() == session


def test_late_revoked_response_cannot_clear_a_new_login(session, monkeypatch):
    replacement = {**session, 'cloud_access_token': 'bob-access', 'cloud_refresh_token': 'bob-refresh'}
    def late(request):
        save_library_config(replacement, strict=True)
        return httpx.Response(401, json=dict(detail='已撤销'))
    with install(monkeypatch, late):
        assert cloud_client.refresh_token(session.copy()) is None
    assert load_library_config() == replacement


def test_storage_failure_never_reports_success_or_changes_caller_pair(session, monkeypatch):
    request_config = session.copy()
    def fail(*args):
        raise PermissionError('read-only')
    monkeypatch.setattr(cloud_client, 'write_json_config', fail)
    with install(monkeypatch, lambda request: httpx.Response(200, json=dict(access_token='new', refresh_token='rotated'))):
        with pytest.raises(HTTPException) as exc:
            cloud_client.refresh_token(request_config)
    assert exc.value.status_code == 503
    assert request_config == session and load_library_config() == session


def test_final_business_401_after_successful_refresh_is_not_session_expired(session, monkeypatch):
    def handler(request):
        if request.url.path == '/api/auth/refresh':
            return httpx.Response(200, json=dict(access_token='new', refresh_token='rotated'))
        return httpx.Response(401, json=dict(detail='当前操作需要单独确认'))
    with install(monkeypatch, handler):
        response = cloud_client.cloud_req('GET', 'http://refresh.example/api/special-operation', session.copy())
    assert response.status_code == 401 and 'CLOUD_SESSION_EXPIRED' not in response.text
    assert load_library_config()['cloud_refresh_token'] == 'rotated'


def test_config_path_change_after_read_never_copies_another_library(session, tmp_path, monkeypatch):
    original = Path(settings.config_file)
    other = tmp_path / 'other'
    replacement = {**session, 'cloud_access_token': 'bob-access', 'cloud_refresh_token': 'bob-refresh'}
    def handler(request):
        settings.library_path = str(other)
        save_library_config(replacement, strict=True)
        return httpx.Response(200, json=dict(access_token='new', refresh_token='rotated'))
    with install(monkeypatch, handler):
        assert cloud_client.refresh_token(session.copy()) is None
    assert json.loads(original.read_text()) == session
    assert load_library_config() == replacement


def test_settings_save_and_refresh_do_not_overwrite_each_others_fields(session, monkeypatch):
    from backend.app.api import settings_api
    from backend.app.api.schemas import SettingsUpdate
    read_settings = Event()
    release_settings = Event()
    posting_refresh = Event()
    original_read = settings_api.load_library_config

    def delayed_read():
        value = original_read()
        read_settings.set()
        assert release_settings.wait(5)
        return value

    def response(request):
        posting_refresh.set()
        return httpx.Response(200, json=dict(access_token='new', refresh_token='rotated'))

    monkeypatch.setattr(settings_api, 'load_library_config', delayed_read)
    with install(monkeypatch, response), ThreadPoolExecutor(2) as pool:
        saving = pool.submit(settings_api.update_settings, SettingsUpdate(items_per_page=30), None)
        assert read_settings.wait(5)
        refreshing = pool.submit(cloud_client.refresh_token, session.copy())
        try:
            assert not posting_refresh.wait(0.1), '续期越过设置保存后会被旧设置覆盖'
        finally:
            release_settings.set()
        saving.result(timeout=5)
        assert refreshing.result(timeout=5)['cloud_refresh_token'] == 'rotated'
    saved = load_library_config()
    assert saved['items_per_page'] == 30
    assert saved['cloud_access_token'] == 'new' and saved['cloud_refresh_token'] == 'rotated'
