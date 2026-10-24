"""AI 整理只发送已确认内容，不改文件、标签或版本。"""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import httpx
import pytest
from fastapi.testclient import TestClient

import backend.app.database as database
from backend.app.config import settings
from main import app

HEADERS = {'X-TeamHub-AI': '1'}
client = TestClient(app, headers=HEADERS)


@pytest.fixture
def library(tmp_path, monkeypatch):
    previous = settings.library_path
    monkeypatch.setenv('TEAMHUB_AI_CONFIG_DIR', str(tmp_path / 'ai-profile'))
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    monkeypatch.setattr(settings, 'library_path', str(tmp_path / 'library'))
    database.rebind_engine()
    database.init_db()
    yield tmp_path / 'library'
    settings.library_path = previous
    database.rebind_engine()


def pending(tmp_path, name='设备说明.txt'):
    path = tmp_path / name
    path.write_text('110kV 变压器检修项目\n设备说明及验收记录：' + name, encoding='utf-8')
    response = client.post('/api/documents/quick-add', json={'source_path': str(path)})
    assert response.status_code == 200, response.text
    return response.json()


def prepare(doc):
    response = client.post(f"/api/ai/documents/{doc['id']}/prepare", json={})
    assert response.status_code == 200, response.text
    return response.json()


def configure(monkeypatch, handler):
    from backend.app.core import ai_client, ai_settings
    monkeypatch.setattr(ai_settings, 'get_connection', lambda: (
        {'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash'},
        'sk-test-not-real', 'configuration-a'))
    transport = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(ai_client, '_http', transport)
    return transport


def answer(summary='介绍设备及检修验收资料。', tags=None):
    return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
        'content': json.dumps({'summary': summary, 'tags': tags or ['变压器', '检修']}, ensure_ascii=False),
    }}]})


def analyze(prepared, **values):
    return client.post('/api/ai/analyze', json={
        'preparation_id': prepared['preparation_id'], 'consent': True, **values,
    })


def test_ai_settings_status_never_returns_a_secret(library):
    response = client.get('/api/ai/settings')
    assert response.status_code == 200, response.text
    assert response.json()['provider'] == 'deepseek'
    assert 'api_key' not in response.json()


def test_prepare_is_local_and_does_not_require_a_key(library, tmp_path):
    doc = pending(tmp_path)
    result = prepare(doc)
    assert '变压器' in result['text']
    assert len(result['content_hash']) == 64
    assert result['document_id'] == doc['id']
    assert result['expires_in'] == 600


def test_prepare_declares_the_actual_service_being_authorized(library, tmp_path, monkeypatch):
    from backend.app.core import ai_settings
    monkeypatch.setattr(ai_settings, 'get_settings', lambda: {
        'base_url': 'https://other.example', 'model': 'other-model', 'has_api_key': True})
    prepared = prepare(pending(tmp_path))
    assert prepared['service'] == {'base_url': 'https://other.example', 'model': 'other-model'}


def test_expired_preparation_cannot_be_sent(library, tmp_path, monkeypatch):
    from backend.app.api import ai
    prepared = prepare(pending(tmp_path))
    ai._preparations[prepared['preparation_id']]['created'] -= 601
    with configure(monkeypatch, lambda req: pytest.fail('过期内容不得联网')):
        assert analyze(prepared).status_code == 409


def test_service_changed_before_consent_requires_new_preparation(library, tmp_path, monkeypatch):
    from backend.app.core import ai_settings
    prepared = prepare(pending(tmp_path))
    monkeypatch.setattr(ai_settings, 'get_connection', lambda: (
        {'base_url': 'https://other.example', 'model': 'deepseek-flash'}, 'fake-key', 'new-revision'))
    response = analyze(prepared)
    assert response.status_code == 409
    assert response.json()['detail']['error']['code'] == 'AI_CONFIGURATION_CHANGED'


def test_key_changed_while_calling_discards_the_result(library, tmp_path, monkeypatch):
    from backend.app.core import ai_settings
    prepared = prepare(pending(tmp_path))
    def handler(request):
        monkeypatch.setattr(ai_settings, 'get_connection', lambda: (
            {'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash'}, 'new-key', 'new-revision'))
        return answer()
    with configure(monkeypatch, handler):
        assert analyze(prepared).status_code == 409


def test_consent_is_required_even_with_unknown_preparation(library):
    response = client.post('/api/ai/analyze', json={'preparation_id': 'unknown', 'consent': False})
    assert response.status_code == 400, response.text
    assert response.json()['detail']['error']['code'] == 'AI_CONSENT_REQUIRED'


def test_requests_need_local_host_origin_and_custom_header(library):
    for headers in ({'X-TeamHub-AI': ''}, {'Origin': 'https://untrusted.invalid'}, {'Host': 'untrusted.invalid'}):
        response = client.get('/api/ai/settings', headers=headers)
        assert response.status_code == 403, response.text


def test_electron_null_origin_requires_private_startup_capability(library, monkeypatch):
    monkeypatch.setattr(settings, 'is_electron', True)
    monkeypatch.setenv('TEAMHUB_AI_CAPABILITY', 'isolated-test-capability-only')
    headers = {'Origin': 'null'}
    assert client.get('/api/ai/settings', headers=headers).status_code == 403
    headers['X-TeamHub-AI-Capability'] = 'incorrect'
    assert client.get('/api/ai/settings', headers=headers).status_code == 403
    assert client.get('/api/ai/settings', headers={b'Origin': b'null', b'X-TeamHub-AI-Capability': b'\xff'}).status_code == 403
    headers['X-TeamHub-AI-Capability'] = 'isolated-test-capability-only'
    response = client.get('/api/ai/settings', headers=headers)
    assert response.status_code == 200, response.text
    assert 'isolated-test-capability-only' not in response.text
    assert 'isolated-test-capability-only' not in client.get('/api/health').text
    monkeypatch.delenv('TEAMHUB_AI_CAPABILITY')
    assert client.get('/api/ai/settings', headers=headers).status_code == 403


def test_success_sends_exact_prepared_text_and_never_mutates_document(library, tmp_path, monkeypatch):
    doc = pending(tmp_path)
    original = client.get(f"/api/documents/{doc['id']}").json()
    versions = client.get(f"/api/documents/{doc['id']}/versions").json()
    prepared = prepare(doc)
    calls = []
    def handler(request):
        calls.append(request)
        payload = json.loads(request.content)
        user = json.loads(payload['messages'][-1]['content'])
        assert user == {'filename': prepared['document_name'], 'text': prepared['text']}
        assert request.headers['authorization'] == 'Bearer sk-test-not-real'
        assert 'sk-test-not-real' not in request.content.decode()
        assert payload['thinking'] == {'type': 'disabled'}
        return answer(tags=['变压器', '  检修  ', '变压器'])
    with configure(monkeypatch, handler):
        result = analyze(prepared)
        assert result.status_code == 200, result.text
        assert result.json()['tags'] == ['变压器', '检修']
        assert analyze(prepared).json() == result.json()
    assert len(calls) == 1, '重复确认同一成功任务不能重复扣费'
    assert client.get(f"/api/documents/{doc['id']}").json() == original
    assert client.get(f"/api/documents/{doc['id']}/versions").json() == versions


@pytest.mark.parametrize('envelope', [None, [], {'choices': {}}, {'choices': [None]},
                                    {'choices': [7]}, {'choices': ['x']}, {'choices': [[]]},
                                    {'choices': [{'finish_reason': 'stop', 'message': None}]}])
def test_malformed_upstream_envelope_is_sanitized_and_consumes_paid_task(library, tmp_path, monkeypatch, envelope):
    prepared = prepare(pending(tmp_path))
    calls = []
    with configure(monkeypatch, lambda request: calls.append(request) or httpx.Response(
            200, content=json.dumps(envelope), headers={'Content-Type': 'application/json'})):
        response = analyze(prepared)
        assert response.status_code == 502
        assert response.json()['detail']['error']['code'] == 'AI_INVALID_RESULT'
        assert analyze(prepared).status_code == 409
    assert len(calls) == 1


@pytest.mark.parametrize('change', ['content', 'rename', 'trash'])
def test_changed_document_requires_new_confirmation(library, tmp_path, monkeypatch, change):
    doc = pending(tmp_path)
    prepared = prepare(doc)
    if change == 'content':
        (library / doc['storage_path']).write_text('文件已经变化', encoding='utf-8')
    elif change == 'rename':
        assert client.put(f"/api/documents/{doc['id']}", json={'name': '新名称.txt'}).status_code == 200
    else:
        assert client.delete(f"/api/documents/{doc['id']}").status_code == 200
    calls = []
    with configure(monkeypatch, lambda req: calls.append(req) or answer()):
        response = analyze(prepared)
    assert response.status_code == 409, response.text
    assert not calls


def test_switching_library_cannot_send_previous_library_text(library, tmp_path, monkeypatch):
    prepared = prepare(pending(tmp_path))
    monkeypatch.setattr(settings, 'library_path', str(tmp_path / 'other-library'))
    database.rebind_engine()
    database.init_db()
    calls = []
    with configure(monkeypatch, lambda req: calls.append(req) or answer()):
        response = analyze(prepared)
    assert response.status_code == 409, response.text
    assert not calls


@pytest.mark.parametrize('status,expected', [(401, 400), (402, 400), (429, 429), (500, 503), (302, 502)])
def test_provider_failures_are_sanitized(library, tmp_path, monkeypatch, status, expected):
    prepared = prepare(pending(tmp_path))
    with configure(monkeypatch, lambda req: httpx.Response(status, text='sk-test-not-real PRIVATE PROVIDER CONTENT')):
        response = analyze(prepared)
    assert response.status_code == expected, response.text
    assert 'sk-test-not-real' not in response.text and 'PRIVATE PROVIDER' not in response.text


@pytest.mark.parametrize('kind', ['timeout', 'invalid_json', 'bad_schema', 'truncated', 'empty', 'oversized'])
def test_invalid_responses_do_not_become_suggestions(library, tmp_path, monkeypatch, kind):
    prepared = prepare(pending(tmp_path))
    def handler(request):
        if kind == 'timeout':
            raise httpx.ReadTimeout('sk-test-not-real', request=request)
        if kind == 'invalid_json':
            return httpx.Response(200, text='private broken response')
        if kind == 'bad_schema':
            return answer(summary=['not a string'])
        if kind == 'empty':
            return answer(summary=' ')
        if kind == 'oversized':
            return httpx.Response(200, content=b'x' * 270000)
        return httpx.Response(200, json={'choices': [{'finish_reason': 'length', 'message': {'content': '{}'}}]})
    with configure(monkeypatch, handler):
        response = analyze(prepared)
    assert response.status_code in {502, 504}, response.text
    assert 'sk-test-not-real' not in response.text and 'private broken' not in response.text


def test_schema_errors_never_echo_submitted_key(library):
    response = client.put('/api/ai/settings', json={'api_key': {'secret': 'sk-do-not-echo'}, 'base_url': 123})
    assert response.status_code == 422
    assert 'sk-do-not-echo' not in response.text


def test_same_work_file_changed_during_analysis_discards_result(library, tmp_path, monkeypatch):
    doc = pending(tmp_path)
    prepared = prepare(doc)
    def handler(request):
        (library / doc['storage_path']).write_text('分析期间有修改', encoding='utf-8')
        return answer()
    with configure(monkeypatch, handler):
        response = analyze(prepared)
    assert response.status_code == 409, response.text


def test_only_one_paid_request_runs_at_a_time(library, tmp_path, monkeypatch):
    a = prepare(pending(tmp_path, '甲.txt'))
    b = prepare(pending(tmp_path, '乙.txt'))
    entered, release = Event(), Event()
    def handler(request):
        entered.set()
        assert release.wait(5)
        return answer()
    with configure(monkeypatch, handler), ThreadPoolExecutor(2) as executor:
        first = executor.submit(analyze, a)
        assert entered.wait(3)
        second = analyze(b)
        release.set()
        assert first.result().status_code == 200
        assert second.status_code == 429


def test_failed_task_cannot_be_resubmitted_by_an_already_waiting_request(library, tmp_path, monkeypatch):
    from backend.app.api import ai
    prepared = prepare(pending(tmp_path))
    entered, second_captured, release_second, first_done = Event(), Event(), Event(), Event()
    check = ai._check
    calls = []
    def gate(entry, db):
        if entered.is_set() and not first_done.is_set():
            second_captured.set()
            assert release_second.wait(5)
        return check(entry, db)
    monkeypatch.setattr(ai, '_check', gate)
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            entered.set()
            assert second_captured.wait(5)
            raise httpx.ReadTimeout('temporary isolated failure', request=request)
        return answer()
    with configure(monkeypatch, handler), ThreadPoolExecutor(2) as executor:
        first = executor.submit(analyze, prepared)
        assert entered.wait(3)
        second = executor.submit(analyze, prepared)
        assert first.result(timeout=5).status_code == 504
        first_done.set()
        release_second.set()
        assert second.result(timeout=5).status_code == 409
    assert len(calls) == 1, '失败任务不能被并发请求再次收费'
