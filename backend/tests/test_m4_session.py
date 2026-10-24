"""云会话登录与续期：现行端点、并发合并、不复活退出会话、不串写配置。"""
import httpx
import pytest
from concurrent.futures import ThreadPoolExecutor
from backend.app.config import settings
from backend.app.database import save_library_config, load_library_config
from backend.app.core import cloud_client


@pytest.fixture
def session_config(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'library_path', str(tmp_path))
    config = dict(cloud_api_url='http://127.0.0.1:9000', cloud_access_token='old', cloud_refresh_token='refresh', active_workspace_id='ws')
    save_library_config(config, strict=True)
    return config


def test_parallel_refresh_is_single_request(session_config, monkeypatch):
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        return httpx.Response(200, json={'access_token': 'new', 'refresh_token': 'rotated'})
    monkeypatch.setattr(cloud_client._http, 'post', post)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(cloud_client.refresh_token, [session_config.copy(), session_config.copy()]))
    assert len(calls) == 1
    assert all(r['cloud_access_token'] == 'new' for r in results)
    assert load_library_config()['active_workspace_id'] == 'ws'


def test_refresh_does_not_revive_logout(session_config, monkeypatch):
    def post(*args, **kwargs):
        save_library_config({'active_workspace_id': 'new-workspace'}, strict=True)
        return httpx.Response(200, json={'access_token': 'new', 'refresh_token': 'rotated'})
    monkeypatch.setattr(cloud_client._http, 'post', post)
    assert cloud_client.refresh_token(session_config) is None
    assert 'cloud_access_token' not in load_library_config()


def test_refresh_cannot_write_switched_library(session_config, tmp_path, monkeypatch):
    def post(*args, **kwargs):
        settings.library_path = str(tmp_path / 'other')
        save_library_config({'active_workspace_id': 'other'}, strict=True)
        return httpx.Response(200, json={'access_token': 'new'})
    monkeypatch.setattr(cloud_client._http, 'post', post)
    assert cloud_client.refresh_token(session_config) is None
    assert load_library_config() == {'active_workspace_id': 'other'}


def test_old_request_cannot_use_another_login(session_config):
    save_library_config({**session_config, 'cloud_access_token': 'another-user', 'cloud_refresh_token': 'another-refresh'}, strict=True)
    assert cloud_client.refresh_token(session_config) is None


def test_changing_server_address_drops_old_credentials(session_config):
    from backend.app.api.settings_api import update_settings
    from backend.app.api.schemas import SettingsUpdate
    update_settings(SettingsUpdate(cloud_api_url='https://new.example.test'), None)
    config = load_library_config()
    assert config['cloud_api_url'] == 'https://new.example.test'
    assert not config.get('cloud_access_token') and not config.get('cloud_refresh_token')
    assert not config.get('active_workspace_id')


def test_login_uses_active_endpoints_and_keeps_tokens_private(session_config, monkeypatch):
    from backend.app.api.sync import cloud_login
    from backend.app.api.schemas import CloudLoginRequest

    calls = []
    payloads = {
        ('GET', '/api/server-info'): {'server_id': 'server-a', 'protocol_major': 1, 'capabilities': ['explicit_publication_scope']},
        ('POST', '/api/auth/login'): {'access_token': 'new', 'refresh_token': 'rotated'},
        ('GET', '/api/users/me'): {'id': 'alice', 'email': 'alice@example.test'},
        ('GET', '/api/teams'): [{'id': 'team', 'name': '团队'}],
        ('GET', '/api/teams/team/workspaces'): [{'id': 'space', 'name': '资料空间'}],
    }

    def handle(request):
        route = (request.method, request.url.path)
        calls.append(route)
        if route == ('GET', '/api/server-info'):
            assert 'Authorization' not in request.headers
        elif route != ('POST', '/api/auth/login'):
            assert request.headers['Authorization'] == 'Bearer new'
        return httpx.Response(200, json=payloads[route])

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr('backend.app.api.sync._http', client)
        monkeypatch.setattr(cloud_client, '_http', client)
        result = cloud_login(CloudLoginRequest(email='alice@example.test', password='test-password'))

    assert calls == list(payloads)
    assert result == {
        'server_id': 'server-a',
        'user': payloads[('GET', '/api/users/me')],
        'teams': payloads[('GET', '/api/teams')],
        'workspaces': [{'id': 'space', 'name': '资料空间', 'team_id': 'team', 'team_name': '团队'}],
    }
    config = load_library_config()
    assert config['cloud_access_token'] == 'new'
    assert config['cloud_refresh_token'] == 'rotated'
