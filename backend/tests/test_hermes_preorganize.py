"""真实 Hermes 完成结构化预整理；供应商由内存模拟，不外发资料。"""
import asyncio
import json
from io import BytesIO
import sys

import httpx2
import pytest
from PIL import Image

from backend.tests.test_partner_tools import library, saved  # noqa: F401
from backend.tests.test_hermes_real_engine import (
    ROOT, current_worker_source, response, values,  # noqa: F401
)
from backend.tests.test_organization_tools import proposal

pytestmark = pytest.mark.skipif(
    not (ROOT / '.mvp-sandbox/hermes-venv/bin/python').exists(),
    reason='真实 Hermes 契约需先准备独立引擎环境')


def arguments(library, doc_id, meta):
    return {**values(library, {doc_id: meta}, []), 'message': '为当前资料准备整理建议。',
        'purpose': 'preorganize', 'scope_mode': 'selected',
        'purpose_context': {'document_id': doc_id, 'tags': ['培训'], 'folders': ['规程']}}


def test_actual_engine_finishes_at_valid_proposal_without_extra_model_request(library):
    from backend.app.core.hermes_agent import run_agent
    doc_id, meta = saved(library, '资料.txt', '新员工培训周期两周。'.encode())
    saved(library, '私有资料.txt', b'do-not-expose')
    requests = []
    def provider(request):
        body = json.loads(request.content)
        requests.append(body)
        assert len(requests) <= 2
        raw = request.content.decode()
        assert '私有资料' not in raw and str(library) not in raw
        assert {tool['function']['name'] for tool in body['tools']} == {
            'document_overview', 'read_document', 'read_table', 'read_visual',
            'read_embedded_images', 'read_skill_reference', 'read_object_skill', 'propose_organization'}
        if len(requests) == 1:
            assert doc_id in raw and 'organization' in raw and '规程' in raw
            return response(body, tool='read_document', arguments={'document_id': doc_id})
        assert '两周' in raw
        return response(body, tool='propose_organization', arguments=proposal())
    result = asyncio.run(run_agent(**arguments(library, doc_id, meta),
        transport_override=httpx2.MockTransport(provider)))
    assert result['proposal'] == proposal()
    assert result['usage']['model_requests'] == 2 and result['usage']['tool_calls'] == 2
    assert result['usage']['token_usage_complete'] is True
    assert result['sources'][0]['document_id'] == doc_id
    assert result['artifacts'] == []
    assert result['engine_history'] == [] and result['engine_history_reusable'] is False


def test_natural_language_answer_without_proposal_is_failure(library):
    from backend.app.core.hermes_agent import run_agent
    from backend.app.core.ai_client import AIError
    doc_id, meta = saved(library, '资料.txt', b'test')
    with pytest.raises(AIError) as caught:
        asyncio.run(run_agent(**arguments(library, doc_id, meta),
            transport_override=httpx2.MockTransport(lambda request:
                response(json.loads(request.content), answer='已经整理好了'))))
    assert caught.value.code == 'AI_ORGANIZATION_INCOMPLETE'


def test_missing_card_is_corrected_in_same_task_before_finishing(library):
    from backend.app.core.hermes_agent import run_agent
    from backend.tests.test_card_tools import card
    doc_id, meta = saved(library, '资料.txt', '新员工培训周期两周。'.encode())
    requests = []
    def provider(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return response(body, tool='read_document', arguments={'document_id': doc_id})
        if len(requests) == 2:
            missing = proposal()
            missing.pop('card', None)
            return response(body, tool='propose_organization', arguments=missing)
        assert len(requests) == 3
        tool_results = [item['content'] for item in body['messages'] if item.get('role') == 'tool']
        assert any('card' in value and 'false' in value for value in tool_results)
        return response(body, tool='propose_organization', arguments=proposal(card=card()))
    result = asyncio.run(run_agent(**arguments(library, doc_id, meta),
        transport_override=httpx2.MockTransport(provider)))
    assert result['card']['summary'] == card()['summary']
    assert result['usage']['model_requests'] == 3
    assert result['usage']['tool_calls'] == 3
    assert result['proposal']['card'] == result['card']


def test_visual_proposal_uses_current_image_bytes(library):
    from backend.app.core.hermes_agent import run_agent
    image = BytesIO()
    Image.new('RGB', (16, 20), color='red').save(image, 'PNG')
    doc_id, meta = saved(library, '图片.png', image.getvalue())
    calls = []
    def provider(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            return response(body, tool='read_visual', arguments={'document_id': doc_id})
        assert 'data:image/png;base64,' in json.dumps(body)
        return response(body, tool='propose_organization',
            arguments=proposal(name='红色图片.png', target_dir='', summary='红色图片。'))
    result = asyncio.run(run_agent(**arguments(library, doc_id, meta),
        transport_override=httpx2.MockTransport(provider)))
    assert result['proposal']['name'] == '红色图片.png'
    assert result['progress']['visual_pages'] == 1
    assert len(calls) == 2


def test_preorganization_does_not_accept_history_or_checkpoint(library):
    from backend.app.core.hermes_agent import run_agent
    from backend.app.core.ai_client import AIError
    doc_id, meta = saved(library, '资料.txt', b'test')
    with pytest.raises(AIError) as caught:
        asyncio.run(run_agent(**arguments(library, doc_id, meta),
            checkpoint={'sources': [{'source_id': 'S1'}]}))
    assert caught.value.code == 'AI_ORGANIZATION_SCOPE'


def test_outdated_adapter_is_rejected_before_paid_request(library, tmp_path, monkeypatch):
    from backend.app.core import hermes_agent
    from backend.app.core.ai_client import AIError
    doc_id, meta = saved(library, '资料.txt', b'test')
    old_worker = tmp_path / 'old_worker.py'
    old_worker.write_text(
        "import json,time\nprint(json.dumps({'v':1,'type':'ready','protocol':1,"
        "'version':'v0.22.2','adapter_revision':2}),flush=True)\ntime.sleep(30)\n")
    monkeypatch.setattr(hermes_agent, '_command', lambda: [sys.executable, str(old_worker)])
    requests = []
    with pytest.raises(AIError) as caught:
        asyncio.run(hermes_agent.run_agent(**arguments(library, doc_id, meta),
            transport_override=httpx2.MockTransport(lambda request: requests.append(request))))
    assert caught.value.code == 'HERMES_ADAPTER_OUTDATED'
    assert requests == [] and not hermes_agent._active


def test_preorganize_rejects_extra_model_tool_before_network(library, tmp_path, monkeypatch):
    from backend.app.core import hermes_agent
    from backend.app.core.ai_client import AIError
    from backend.tests.test_hermes_partner import _scripted_worker
    doc_id, meta = saved(library, '资料.txt', b'test')
    _scripted_worker(tmp_path, monkeypatch, [{'type': 'model_request', 'id': 'model',
        'body': {'model': 'deepseek-synthetic', 'messages': [],
                 'tools': [{'type': 'function', 'function': {'name': 'create_draft'}}]}}])
    requests = []
    with pytest.raises(AIError) as caught:
        asyncio.run(hermes_agent.run_agent(**arguments(library, doc_id, meta),
            transport_override=httpx2.MockTransport(lambda request: requests.append(request))))
    assert caught.value.code == 'HERMES_PROTOCOL_ERROR'
    assert requests == []


@pytest.mark.parametrize('original_name', ['旧资料.wps', '旧资料.doc', '加密资料.docx'])
def test_binary_office_is_explained_before_engine_or_paid_request(library, monkeypatch, original_name):
    from backend.app import database
    from backend.app.core import hermes_agent
    from backend.app.core.ai_client import AIError
    from backend.app.models import Document
    from backend.app.services.indexing import metadata_fingerprint
    data = bytes.fromhex('d0cf11e0a1b11ae1') + b'synthetic compound document'
    doc_id, meta = saved(library, original_name, data)
    # 与真实失败一致：显示名后来改成 DOCX，快照仍保留原始内容和原始名称。
    with database.SessionLocal() as db:
        doc = db.get(Document, doc_id)
        doc.name = '显示名称.docx'
        db.commit()
        meta.update(name=doc.name, metadata_hash=metadata_fingerprint(doc))
    def must_not_start():
        pytest.fail('不可读取的 Office 二进制不应启动引擎或调用模型')
    monkeypatch.setattr(hermes_agent, '_command', must_not_start)
    with pytest.raises(AIError) as caught:
        asyncio.run(hermes_agent.run_agent(**arguments(library, doc_id, meta)))
    assert caught.value.code == 'AI_ORGANIZATION_FORMAT_UNREADABLE'
    assert 'Office/WPS' in str(caught.value) and '另存为' in str(caught.value)
    assert '仅修改' in str(caught.value)
    assert str(library) not in str(caught.value)
    assert not hermes_agent._active
    assert (library / original_name).read_bytes() == data
