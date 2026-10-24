"""Actual Hermes + synthetic provider verifies structured card termination."""
import asyncio
import json

import httpx2
import pytest

from backend.tests.test_partner_tools import library, saved  # noqa: F401
from backend.tests.test_card_tools import card
from backend.tests.test_hermes_real_engine import ROOT, current_worker_source, response, values  # noqa: F401

pytestmark = pytest.mark.skipif(
    not (ROOT / '.mvp-sandbox/hermes-venv/bin/python').exists(),
    reason='真实 Hermes 契约需先准备独立引擎环境')


def test_actual_card_generation_loads_method_reads_and_submits_without_extra_request(library):
    from backend.app.core.hermes_agent import run_agent
    doc_id, meta = saved(library, '培训.txt', '新员工培训周期为两周。'.encode())
    saved(library, '未授权秘密.txt', b'private-do-not-send')
    calls = []

    def provider(request):
        body = json.loads(request.content)
        calls.append(body)
        assert len(calls) <= 3
        raw = request.content.decode()
        assert 'private-do-not-send' not in raw and str(library) not in raw
        assert {tool['function']['name'] for tool in body['tools']} == {
            'document_overview', 'read_document', 'read_table', 'read_visual',
            'read_embedded_images', 'read_skill_reference', 'read_object_skill', 'submit_document_card'}
        if len(calls) == 1:
            return response(body, tool='read_skill_reference', arguments={'method': 'text'})
        if len(calls) == 2:
            assert 'text' in raw
            return response(body, tool='read_document', arguments={'document_id': doc_id})
        assert '两周' in raw
        return response(body, tool='submit_document_card', arguments=card())

    result = asyncio.run(run_agent(**{**values(library, {doc_id: meta}, []),
        'purpose': 'card', 'purpose_context': {'document_id': doc_id},
        'transport_override': httpx2.MockTransport(provider)}))
    assert result['card']['facts'][0]['value'] == '两周'
    assert result['coverage']['complete'] is True
    assert result['coverage']['read_units'] == 1
    assert result['usage']['model_requests'] == 3
    assert result['usage']['tool_calls'] == 3
    assert result['engine_history'] == []
    assert len(result['sources']) == 1
    assert result['artifacts'] == []


def test_card_requires_structured_submission_and_disallows_chat_history(library):
    from backend.app.core.hermes_agent import run_agent
    from backend.app.core.ai_client import AIError
    doc_id, meta = saved(library, '培训.txt', b'test')
    args = {**values(library, {doc_id: meta}, []), 'purpose': 'card',
            'purpose_context': {'document_id': doc_id}}
    with pytest.raises(AIError) as error:
        asyncio.run(run_agent(**args, transport_override=httpx2.MockTransport(
            lambda req: response(json.loads(req.content), answer='已理解'))))
    assert error.value.code == 'AI_CARD_INCOMPLETE'
    with pytest.raises(AIError) as error:
        asyncio.run(run_agent(**{**args, 'history': [{'role': 'user', 'content': 'old'}]}))
    assert error.value.code == 'AI_CARD_SCOPE'


def test_card_proposal_and_host_coverage_share_preorganize_model_calls(library):
    from backend.app.core.hermes_agent import run_agent
    from backend.tests.test_hermes_preorganize import arguments
    from backend.tests.test_organization_tools import proposal
    doc_id, meta = saved(library, '培训.txt', '新员工培训周期为两周。'.encode())
    calls = []
    def provider(request):
        body = json.loads(request.content)
        calls.append(body)
        assert len(calls) <= 2
        return (response(body, tool='read_document', arguments={'document_id': doc_id})
                if len(calls) == 1 else response(body, tool='propose_organization',
                                               arguments=proposal(card=card())))
    result = asyncio.run(run_agent(**arguments(library, doc_id, meta),
        transport_override=httpx2.MockTransport(provider)))
    assert result['card'] == result['proposal']['card']
    assert result['coverage']['complete'] is True
    assert result['usage']['model_requests'] == 2
