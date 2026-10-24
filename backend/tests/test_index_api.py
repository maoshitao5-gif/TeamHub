"""索引使用已保存版本；AI 补充只处理明确确认的单元，不改人工资料。"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import database
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


def pending(tmp_path, name='运行说明.txt', content='本站设备检查记录。\n备用电源切换试验，结论为通过。'):
    path = tmp_path / name
    path.write_text(content, encoding='utf-8')
    response = client.post('/api/documents/quick-add', json={'source_path': str(path)})
    assert response.status_code == 200, response.text
    return response.json()


def indexed(tmp_path):
    doc = pending(tmp_path)
    response = client.get(f"/api/ai/indexes/{doc['id']}")
    assert response.status_code == 200, response.text
    from backend.app.services import indexing
    assert indexing.process_one()
    result = client.get(f"/api/ai/indexes/{doc['id']}").json()
    assert result['state'] in {'ready', 'partial'}
    assert result['units']
    return doc, result


def prepare(doc):
    response = client.post(f"/api/ai/indexes/{doc['id']}/prepare", json={})
    assert response.status_code == 200, response.text
    return response.json()


def enrich(prepared, consent=True):
    return client.post('/api/ai/indexes/enrich', json={
        'preparation_id': prepared['preparation_id'], 'consent': consent,
    })


def configure(monkeypatch, handler):
    from backend.app.core import ai_client, ai_settings
    public = {'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash'}
    monkeypatch.setattr(ai_settings, 'get_settings', lambda: {**public, 'has_api_key': True})
    monkeypatch.setattr(ai_settings, 'get_connection', lambda: (public, 'fake-test-key', 'revision-a'))
    transport = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(ai_client, '_http', transport)
    return transport


def answer(unit_id, terms=None):
    return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
        'content': json.dumps({'overview': '提供设备检查和备用电源试验记录。',
                               'clues': [{'unit_id': unit_id, 'terms': terms or ['供电可靠性']}]}, ensure_ascii=False),
    }}]})


def search(keyword, **extra):
    return client.post('/api/documents/search', json={
        'keyword': keyword, 'include_content': True, **extra,
    })


def test_index_status_and_body_search_need_local_capability(library, tmp_path, monkeypatch):
    doc = pending(tmp_path)
    for path in (f"/api/ai/indexes/{doc['id']}",):
        assert client.get(path, headers={'X-TeamHub-AI': ''}).status_code == 403
    response = client.post('/api/documents/search', json={'include_content': True, 'keyword': '检查'},
                           headers={'X-TeamHub-AI': ''})
    assert response.status_code == 403
    monkeypatch.setattr(settings, 'is_electron', True)
    monkeypatch.setenv('TEAMHUB_AI_CAPABILITY', 'index-desktop-session')
    assert client.get(f"/api/ai/indexes/{doc['id']}", headers={'Origin': 'null'}).status_code == 403


def test_saved_version_is_searchable_with_locations_and_no_network(library, tmp_path, monkeypatch):
    with configure(monkeypatch, lambda request: pytest.fail('本机索引不能调用外部模型')):
        doc, result = indexed(tmp_path)
        assert result['version_id'] == doc['current_version']['id']
        assert result['units'][0]['location']
        assert search('备用电源').json()['total'] == 1
        assert search('备用电源', status='organized').json()['total'] == 0
        prepared = prepare(doc)
        assert prepared['version_id'] == result['version_id']
        assert prepared['content_hash'] == result['content_hash']
        assert '备用电源' in prepared['text']
        assert enrich(prepared, False).status_code == 400


def test_ai_clues_link_to_units_and_preserve_files_tags_versions(library, tmp_path, monkeypatch):
    doc, index = indexed(tmp_path)
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        return answer(index['units'][0]['id'])
    before = client.get(f"/api/documents/{doc['id']}").json()
    versions = client.get(f"/api/documents/{doc['id']}/versions").json()
    with configure(monkeypatch, handler):
        prepared = prepare(doc)
        response = enrich(prepared)
        assert response.status_code == 200, response.text
        assert enrich(prepared).json() == response.json()
    assert len(calls) == 1
    submitted = json.loads(calls[0]['messages'][1]['content'])
    assert submitted['text'] == prepared['text']
    assert submitted['filename'] == prepared['document_name']
    assert 'Skill' in calls[0]['messages'][0]['content']
    assert search('供电可靠性').json()['total'] == 1
    after = client.get(f"/api/documents/{doc['id']}").json()
    for field in ('current_version', 'tags', 'description', 'storage_path'):
        assert after[field] == before[field]
    assert client.get(f"/api/documents/{doc['id']}/versions").json() == versions
    status = client.get(f"/api/ai/indexes/{doc['id']}").json()
    assert status['ai']['processed_units'] > 0
    assert status['ai']['clues'][0]['unit_id'] == index['units'][0]['id']


def test_unknown_unit_cannot_become_evidence_and_failed_task_is_consumed(library, tmp_path, monkeypatch):
    doc, _ = indexed(tmp_path)
    calls = []
    with configure(monkeypatch, lambda request: calls.append(request) or answer('invented-unit')):
        prepared = prepare(doc)
        assert enrich(prepared).status_code == 502
        assert enrich(prepared).status_code == 409
    assert len(calls) == 1
    assert client.get(f"/api/ai/indexes/{doc['id']}").json()['ai'] is None


def test_index_rebuild_invalidates_an_old_ai_preparation(library, tmp_path, monkeypatch):
    doc, _ = indexed(tmp_path)
    with configure(monkeypatch, lambda request: pytest.fail('旧索引不能发送')):
        prepared = prepare(doc)
        assert client.post(f"/api/ai/indexes/{doc['id']}/rebuild", json={}).status_code == 200
        assert enrich(prepared).status_code == 409


def test_metadata_change_invalidates_consent_and_existing_ai_clues(library, tmp_path, monkeypatch):
    doc, index = indexed(tmp_path)
    with configure(monkeypatch, lambda request: answer(index['units'][0]['id'])):
        assert enrich(prepare(doc)).status_code == 200
        prepared = prepare(doc)
        assert client.put(f"/api/documents/{doc['id']}/tags", json=['另一个项目']).status_code == 200
        assert enrich(prepared).status_code == 409
    assert search('供电可靠性').json()['total'] == 0
    assert search('备用电源').json()['total'] == 1


def test_model_or_key_change_discards_response(library, tmp_path, monkeypatch):
    from backend.app.core import ai_settings
    doc, index = indexed(tmp_path)
    def handler(request):
        monkeypatch.setattr(ai_settings, 'get_connection', lambda: (
            {'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash'}, 'different', 'revision-b'))
        return answer(index['units'][0]['id'])
    with configure(monkeypatch, handler):
        assert enrich(prepare(doc)).status_code == 409
    assert client.get(f"/api/ai/indexes/{doc['id']}").json()['ai'] is None


def test_deleted_or_trashed_material_is_not_available_to_index_api(library, tmp_path):
    doc, _ = indexed(tmp_path)
    assert client.delete(f"/api/documents/{doc['id']}").status_code == 200
    assert client.get(f"/api/ai/indexes/{doc['id']}").status_code in {404, 409}
    assert search('备用电源').json()['total'] == 0


def test_prepared_content_is_bounded_and_records_partial_ai_coverage(library, tmp_path):
    doc = pending(tmp_path, content='背景说明和检修流程。\n' * 3000 + '最末尾验收关键字')
    assert client.get(f"/api/ai/indexes/{doc['id']}").status_code == 200
    from backend.app.services import indexing
    assert indexing.process_one()
    while indexing.process_one():
        pass
    prepared = prepare(doc)
    assert len(prepared['text']) <= 12000
    assert prepared['truncated'] is True
    assert search('最末尾验收关键字').json()['total'] == 1
