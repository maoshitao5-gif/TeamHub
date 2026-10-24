"""Actual Hermes repairs structured completion without losing read pixels or scope."""
import asyncio
from io import BytesIO
import json

import httpx2
from PIL import Image
import pytest

from backend.tests.test_partner_tools import library, saved  # noqa: F401
from backend.tests.test_hermes_real_engine import current_worker_source, response, values  # noqa: F401
from backend.tests.test_organization_tools import proposal
from backend.tests.test_card_tools import card


def image_document(library):
    output = BytesIO()
    Image.new('RGB', (30, 40), 'red').save(output, 'JPEG')
    return saved(library, '现场.jpeg', output.getvalue())


def arguments(library, document, meta, events, purpose='preorganize', thinking='disabled'):
    args = values(library, {document: meta}, events)
    args.update(purpose=purpose, scope_mode='selected',
        purpose_context={'document_id': document, **({'tags': [], 'folders': []}
                                                    if purpose == 'preorganize' else {})})
    args['config']['thinking_mode'] = thinking
    args['limits'].update(model_requests=8, tool_calls=12)
    return args


@pytest.mark.parametrize('purpose', ['preorganize', 'card'])
@pytest.mark.parametrize('thinking', ['disabled', 'enabled'])
def test_image_text_end_is_repaired_in_same_context_with_one_read(library, purpose, thinking):
    from backend.app.core.hermes_agent import run_agent
    document, meta = image_document(library)
    calls, events = [], []
    terminal = 'propose_organization' if purpose == 'preorganize' else 'submit_document_card'
    def provider(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            result = response(body, tool='read_visual', arguments={'document_id': document})
            value = json.loads(result.content)
            value['choices'][0]['message']['reasoning_content'] = 'synthetic original reading decision'
            return httpx2.Response(200, json=value)
        if len(calls) == 2:
            return response(body, answer='图片为红色，可以采用现场照片名称。')
        assert len(calls) == 3, 'only one completion correction, no restart or repeated read'
        raw = request.content.decode()
        assert 'data:image/png;base64,' in raw and 'S1' in raw
        assert 'synthetic original reading decision' in raw
        tool_calls = [call for message in body['messages'] for call in message.get('tool_calls', [])]
        assert [call['function']['name'] for call in tool_calls] == ['read_visual']
        assert any(terminal in message.get('content', '') for message in body['messages']
                   if message.get('role') == 'user' and isinstance(message.get('content'), str))
        assert body.get('tool_choice', 'auto') == 'auto'  # compatible with thinking providers
        return response(body, tool=terminal,
            arguments=proposal(name='红色现场照片.jpeg', target_dir='') if purpose == 'preorganize' else card())
    output = asyncio.run(run_agent(**arguments(library, document, meta, events, purpose, thinking),
                                  transport_override=httpx2.MockTransport(provider)))
    assert output['card'] and output['coverage']['visual_pages'] == 1
    assert output['usage']['model_requests'] == 3 and output['usage']['tool_calls'] == 2
    assert output['diagnostics']['recovery_attempts'] == 1
    assert output['diagnostics']['outcome'] == 'submitted'
    assert any(kind == 'structured_diagnostic' for kind, _ in events)
    assert output['engine_history'] == []  # image history remains private task memory


def test_invalid_nested_card_then_text_end_is_corrected_with_specific_feedback(library):
    from backend.app.core.hermes_agent import run_agent
    document, meta = image_document(library)
    calls = []
    def provider(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            return response(body, tool='read_visual', arguments={'document_id': document})
        if len(calls) == 2:
            value = proposal(name='现场.jpeg', target_dir='')
            value['card']['sections'] = [{'title': '画面', 'summary': '红色现场'}]
            return response(body, tool='propose_organization', arguments=value)
        if len(calls) == 3:
            raw = request.content.decode()
            assert 'card.sections.0.source_ids' in raw and 'SUBMISSION_SCHEMA_INVALID' in raw
            return response(body, answer='我已经完成图片分析。')
        assert len(calls) == 4
        assert 'data:image/png;base64,' in request.content.decode()
        return response(body, tool='propose_organization', arguments=proposal(name='现场.jpeg', target_dir=''))
    result = asyncio.run(run_agent(**arguments(library, document, meta, []),
                                  transport_override=httpx2.MockTransport(provider)))
    assert result['diagnostics']['submission_attempts'] == 2
    assert result['diagnostics']['recovery_attempts'] == 1
    assert len(calls) == 4


def test_repeated_text_end_is_bounded_and_has_safe_diagnostics(library):
    from backend.app.core.hermes_agent import run_agent
    from backend.app.core.ai_client import AIError
    document, meta = image_document(library)
    calls, events = [], []
    def provider(request):
        calls.append(json.loads(request.content))
        return response(calls[-1], answer='sensitive model text must not enter diagnostic')
    with pytest.raises(AIError) as caught:
        asyncio.run(run_agent(**arguments(library, document, meta, events),
                              transport_override=httpx2.MockTransport(provider)))
    assert len(calls) == 2
    assert caught.value.code == 'AI_ORGANIZATION_INCOMPLETE'
    reports = [value for kind, value in events if kind == 'structured_diagnostic']
    assert reports[-1]['outcome'] == 'incomplete'
    assert reports[-1]['recovery_attempts'] == 1 and reports[-1]['read_sources'] == 0
    assert 'sensitive model text' not in json.dumps(reports)
    assert '未提交' in str(caught.value) and '补交' in str(caught.value)


def test_request_budget_is_shared_across_completion_correction(library):
    from backend.app.core.hermes_agent import run_agent
    from backend.app.core.partner_budget import PartnerPaused
    document, meta = image_document(library)
    args = arguments(library, document, meta, [])
    args['limits']['model_requests'] = 2
    calls = []
    def provider(request):
        body = json.loads(request.content)
        calls.append(body)
        return (response(body, tool='read_visual', arguments={'document_id': document})
                if len(calls) == 1 else response(body, answer='红色图片。'))
    with pytest.raises(PartnerPaused) as caught:
        asyncio.run(run_agent(**args, transport_override=httpx2.MockTransport(provider)))
    assert len(calls) == 2 and caught.value.output['usage']['model_requests'] == 2
    assert caught.value.output['coverage']['visual_pages'] == 1


def test_provider_rejection_is_not_replayed_as_completion_correction(library):
    from backend.app.core.hermes_agent import run_agent
    from backend.app.core.ai_client import AIError
    document, meta = image_document(library)
    calls, events = [], []
    def provider(request):
        body = json.loads(request.content)
        calls.append(body)
        return (response(body, tool='read_visual', arguments={'document_id': document})
                if len(calls) == 1 else httpx2.Response(429, json={'error': 'do not retry'}))
    with pytest.raises(AIError) as caught:
        asyncio.run(run_agent(**arguments(library, document, meta, events),
                              transport_override=httpx2.MockTransport(provider)))
    assert caught.value.code == 'AI_RATE_LIMITED' and len(calls) == 2
    assert not any(kind == 'structured_diagnostic' and value['outcome'] == 'recovery' for kind, value in events)
