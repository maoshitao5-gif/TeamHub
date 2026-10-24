"""伙伴 API 的本机来源校验与严格请求校验。"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.app.database as database
from backend.app.config import settings


@pytest.fixture
def api(tmp_path, monkeypatch):
    previous = settings.library_path
    monkeypatch.setattr(settings, 'library_path', str(tmp_path / 'library'))
    database.rebind_engine()
    from backend.app.api.partner import router
    database.init_db()
    app = FastAPI()
    app.include_router(router)
    with TestClient(app, headers={'X-TeamHub-AI': '1'}) as client:
        yield client
    settings.library_path = previous
    database.rebind_engine()


def test_conversation_routes_are_local_only(api):
    for headers in ({'X-TeamHub-AI': ''}, {'Origin': 'https://untrusted.invalid'}, {'Host': 'untrusted.invalid'}):
        assert api.get('/api/ai/partner/sessions', headers=headers).status_code == 403


def test_create_list_and_delete_conversation(api):
    created = api.post('/api/ai/partner/sessions', json={'title': '技术伙伴'})
    assert created.status_code == 200, created.text
    session = created.json()
    assert session['title'] == '技术伙伴'
    assert api.get('/api/ai/partner/sessions').json()['items'][0]['id'] == session['id']
    detail = api.get('/api/ai/partner/sessions/' + session['id']).json()
    assert detail['messages'] == []
    assert api.delete('/api/ai/partner/sessions/' + session['id']).json() == {'deleted': True}


def test_prepare_rejects_unknown_fields_without_echoing_body(api):
    session = api.post('/api/ai/partner/sessions', json={}).json()
    response = api.post('/api/ai/partner/sessions/' + session['id'] + '/prepare', json={
        'message': '正常问题', 'scope_mode': 'selected', 'api_key': 'secret-must-not-echo'})
    assert response.status_code == 422
    assert 'secret-must-not-echo' not in response.text


def test_budget_policy_and_prepare_expose_exact_authorized_limits(api):
    policy = api.get('/api/ai/partner/budget-policy').json()
    assert policy['default_mode'] == 'standard'
    assert policy['presets']['standard']['model_requests'] == 32
    session = api.post('/api/ai/partner/sessions', json={}).json()
    custom = {'mode': 'custom', 'model_requests': 4, 'tool_calls': 8,
              'output_tokens': 4000, 'time_seconds': 60}
    response = api.post('/api/ai/partner/sessions/' + session['id'] + '/prepare', json={
        'message': '读取表格', 'budget': custom})
    assert response.status_code == 200
    assert response.json()['budget'] == custom
    assert response.json()['limits']['time_seconds'] == 60
    for invalid in ({**custom, 'model_requests': True}, {**custom, 'tool_calls': 999999},
                    {'mode': 'quick', 'model_requests': 128}):
        assert api.post('/api/ai/partner/sessions/' + session['id'] + '/prepare', json={
            'message': '读取表格', 'budget': invalid}).status_code == 422


def test_continue_routes_require_local_origin_and_explicit_consent(api):
    path = '/api/ai/partner/tasks/not-found/'
    assert api.post(path + 'prepare-continue', json={}, headers={'Origin': 'https://evil.invalid'}).status_code == 403
    assert api.post(path + 'continue', json={'preparation_id': 'p', 'consent': False,
        'request_id': 'r'}).status_code == 400


def test_chat_routes_are_local_strict_and_send_with_one_request(api, monkeypatch):
    from backend.app.services import partner_runtime as runtime
    from backend.tests.test_partner_runtime import configure
    import asyncio
    configure(monkeypatch)
    seen = []
    async def agent(**values):
        seen.append(values)
        return {'answer': '单次发送完成', 'sources': [], 'artifacts': []}
    monkeypatch.setattr(runtime, 'run_agent', agent)
    config = api.get('/api/ai/partner/chat-config')
    assert config.status_code == 200
    assert config.json()['defaults']['thinking_mode'] == 'disabled'
    assert 'fake-secret' not in config.text
    session = api.post('/api/ai/partner/sessions', json={}).json()
    path = '/api/ai/partner/sessions/' + session['id'] + '/send'
    body = {'message': '你好', 'request_id': 'api-direct', 'consent': True,
            'expected_service_revision': config.json()['service_revision']}
    assert api.post(path, json={**body, 'consent': 'true'}).status_code == 422
    assert api.post(path, json=body, headers={'Origin': 'https://evil.invalid'}).status_code == 403
    assert api.post(path, json={**body, 'api_key': 'secret'}).status_code == 422
    response = api.post(path, json=body)
    assert response.status_code == 202, response.text
    repeat = api.post(path, json=body)
    assert repeat.status_code == 202
    assert response.json()['id'] == repeat.json()['id']
    for _ in range(50):
        snapshot = api.get('/api/ai/partner/tasks/' + response.json()['id']).json()
        if snapshot['state'] == 'completed':
            break
        import time
        time.sleep(.005)
    assert snapshot['state'] == 'completed'
    assert len(seen) == 1
    assert api.post('/api/ai/partner/tasks/not-found/resume', json={
        'request_id': 'r', 'expected_service_revision': config.json()['service_revision'],
        'consent': False}).status_code == 400
    asyncio.run(runtime.shutdown())


@pytest.mark.parametrize('state', ['queued', 'running', 'completed'])
def test_request_lookup_is_read_only_session_scoped_and_private(api, monkeypatch, state):
    """POST响应丢失后按原请求号核对，不重放模型调用或泄露另一个会话。"""
    import json
    from backend.app.services import partner_runtime as runtime
    from backend.app.partner_models import PartnerTask
    calls = []
    async def forbidden_model(**values):
        calls.append(values)
        raise AssertionError('GET 核对不能调用模型')
    monkeypatch.setattr(runtime, 'run_agent', forbidden_model)
    session = api.post('/api/ai/partner/sessions', json={}).json()
    other = api.post('/api/ai/partner/sessions', json={}).json()
    request_id = 'lost-post-response-' + state
    path = '/api/ai/partner/sessions/' + session['id'] + '/requests/' + request_id
    assert api.get(path).status_code == 404
    with database.SessionLocal() as db:
        task = PartnerTask(session_id=session['id'], request_id=request_id,
            preparation_id='legacy-or-direct', state=state, message='已登记的问题', input_json='{}',
            result_json=json.dumps({'answer': '已完成' if state == 'completed' else None,
                'engine_history': [{'role': 'assistant', 'content': 'private-protocol-secret'}]}))
        db.add(task); db.commit(); task_id = task.id
    found = api.get(path)
    assert found.status_code == 200, found.text
    assert found.json()['id'] == task_id
    assert found.json()['state'] == state
    assert found.json()['request_id'] == request_id
    assert 'private-protocol-secret' not in found.text
    assert 'engine_history' not in found.json()
    assert api.get('/api/ai/partner/sessions/' + other['id'] + '/requests/' + request_id).status_code == 404
    assert api.get(path, headers={'Origin': 'https://evil.invalid'}).status_code == 403
    assert api.get(path, headers={'X-TeamHub-AI': ''}).status_code == 403
    detail = api.get('/api/ai/partner/sessions/' + session['id']).json()
    assert detail['tasks'][0]['request_id'] == request_id
    assert not calls
