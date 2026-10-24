"""真实 Hermes 子进程 + 本机假模型贯穿，绝不调用付费服务。"""
import asyncio
from io import BytesIO
import json
from pathlib import Path

import httpx2
import pytest
from PIL import Image

from backend.tests.test_partner_tools import library, saved  # noqa: F401
from backend.tests.test_partner_runtime import partner_library  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    not (ROOT / '.mvp-sandbox/hermes-venv/bin/python').exists()
    and not (ROOT / 'dist/hermes/teamhub-hermes').exists(),
    reason='真实 Hermes 契约需先运行 scripts/prepare-hermes.py')


@pytest.fixture(autouse=True)
def current_worker_source(monkeypatch):
    # Exercise this checkout, not a previously packaged worker left in dist/.
    if (ROOT / '.mvp-sandbox/hermes-venv/bin/python').exists():
        from backend.app.core import hermes_agent
        monkeypatch.setattr(hermes_agent, '_command', lambda: [
            str(ROOT / '.mvp-sandbox/hermes-venv/bin/python'), '-I',
            str(ROOT / 'integrations/hermes/worker.py')])


def response(body, *, tool=None, arguments=None, answer=None):
    number = response.sequence = getattr(response, 'sequence', 0) + 1
    message = {'role': 'assistant', 'content': answer}
    if tool:
        message['tool_calls'] = [{'id': f'call-{number}', 'type': 'function',
            'function': {'name': tool, 'arguments': json.dumps(arguments)}}]
    return httpx2.Response(200, json={'id': f'completion-{number}', 'object': 'chat.completion',
        'created': 1, 'model': body['model'], 'choices': [{'index': 0,
            'finish_reason': 'tool_calls' if tool else 'stop', 'message': message}],
        'usage': {'prompt_tokens': 80, 'completion_tokens': 30}})


def values(library, scope, events):
    from backend.app import database
    return dict(message='阅读资料，分析并生成说明草稿。', history=[], scope=scope,
        config={'base_url': 'https://api.deepseek.com/v1', 'model': 'deepseek-synthetic',
                'thinking_mode': 'enabled'}, key='synthetic-no-real-key', engine=database.engine,
        root=str(library), guard=lambda: None, emit=lambda *item: events.append(item),
        limits={'model_requests': 4, 'tool_calls': 8, 'output_tokens': 4000, 'time_seconds': 60})


def test_actual_hermes_reads_fixed_snapshot_creates_draft_and_answers(library):
    from backend.app.core.hermes_agent import run_agent
    doc_id, meta = saved(library, '培训说明.txt', '培训对象是新员工，培训周期为两周。'.encode())
    (library / '培训说明.txt').write_text('未保存的修改不能发送', encoding='utf-8')
    saved(library, '未授权机密.txt', b'private-never-send')
    calls, events = [], []
    def provider(request):
        assert request.headers['authorization'] == 'Bearer synthetic-no-real-key'
        body = json.loads(request.content)
        raw = request.content.decode()
        assert str(library) not in raw and 'private-never-send' not in raw
        assert '未保存的修改不能发送' not in raw
        tools = {item['function']['name'] for item in body.get('tools', [])}
        assert tools == {'search_documents', 'document_overview', 'read_document', 'read_table',
                         'read_visual', 'read_embedded_images', 'create_draft',
                         'read_document_card', 'read_skill_reference', 'read_object_skill'}
        calls.append(body)
        if len(calls) == 1:
            return response(body, tool='read_document', arguments={'document_id': doc_id})
        if len(calls) == 2:
            assert '两周' in raw
            return response(body, tool='create_draft', arguments={
                'name': '培训计划.md', 'content': '培训周期为两周。[S1]', 'source_ids': ['S1']})
        assert len(calls) == 3
        return response(body, answer='建议按两周安排新员工培训。[S1]')
    result = asyncio.run(run_agent(**values(library, {doc_id: meta}, events),
                                  transport_override=httpx2.MockTransport(provider)))
    assert result['answer'] == '建议按两周安排新员工培训。[S1]'
    assert len(result['artifacts']) == 1
    assert result['artifacts'][0]['name'] == '培训计划.md'
    assert result['sources'][0]['version_id'] == meta['version_id']
    assert result['usage']['model_requests'] == 3 and result['usage']['tool_calls'] == 2
    assert result['usage']['output_tokens'] == 90
    assert result['usage']['token_usage_complete'] is True
    assert any(kind == 'answer_preview' for kind, _ in events)


def test_actual_hermes_transmits_real_visual_bytes(library):
    from backend.app.core.hermes_agent import run_agent
    image = BytesIO()
    Image.new('RGB', (16, 20), color='red').save(image, 'PNG')
    doc_id, meta = saved(library, '图像.png', image.getvalue())
    calls, events = [], []
    def provider(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            return response(body, tool='read_visual', arguments={'document_id': doc_id})
        raw = json.dumps(body, ensure_ascii=False)
        assert 'data:image/png;base64,' in raw
        assert 'S1' in raw
        return response(body, answer='已读取图像。[S1]')
    result = asyncio.run(run_agent(**values(library, {doc_id: meta}, events),
                                  transport_override=httpx2.MockTransport(provider)))
    assert result['sources'][0]['source_id'] == 'S1'
    assert len(calls) == 2


def test_actual_hermes_budget_pause_can_resume_without_hidden_summary_call(library):
    from backend.app.core.hermes_agent import run_agent
    from backend.app.core.partner_budget import PartnerPaused
    calls, events = [], []
    def provider(request):
        body = json.loads(request.content)
        calls.append(body)
        assert len(calls) <= 4
        return response(body, tool='search_documents', arguments={'query': f'线索{len(calls)}'})
    arguments = values(library, {}, events)
    with pytest.raises(PartnerPaused) as pause:
        asyncio.run(run_agent(**arguments, transport_override=httpx2.MockTransport(provider)))
    assert pause.value.reason == 'model_requests'
    assert pause.value.output['usage']['model_requests'] == 4
    assert pause.value.output['usage']['tool_calls'] == 4
    assert len(calls) == 4
    def complete(request):
        body = json.loads(request.content)
        assert 'continuation_context' in request.content.decode()
        return response(body, answer='当前授权范围没有资料，可以先讨论整理目标。')
    result = asyncio.run(run_agent(**arguments, checkpoint=pause.value.output['checkpoint'],
        previous_answer=pause.value.output['answer'], transport_override=httpx2.MockTransport(complete)))
    assert result['usage']['model_requests'] == 1
    assert result['sources'] == []



def test_three_turns_keep_native_tools_reasoning_and_natural_user_message(library):
    from backend.app.core.hermes_agent import run_agent
    doc_id, meta = saved(library, '培训.txt', '培训周期为两周。'.encode())
    events, calls = [], []
    def provider(request):
        body = json.loads(request.content)
        calls.append(body)
        assert any(body['messages'][-1]['content'].endswith(text) for text in ('请阅读培训资料。', '按此安排第一周。', '继续细化第二周。')) or body['messages'][-1]['role'] == 'tool'
        if len(calls) == 1:
            result = response(body, tool='read_document', arguments={'document_id': doc_id})
            payload = json.loads(result.content)
            payload['choices'][0]['message']['reasoning_content'] = 'native-tool-reasoning'
            return httpx2.Response(200, json=payload)
        assert any(item.get('reasoning_content') == 'native-tool-reasoning' for item in body['messages'])
        if body['thinking']['type'] == 'enabled':
            assert all(isinstance(item.get('reasoning_content'), str)
                       for item in body['messages'] if item['role'] == 'assistant')
        assert any(item.get('role') == 'tool' and '两周' in item['content'] for item in body['messages'])
        return response(body, answer='按两周培训安排。[S1]')
    arguments = values(library, {doc_id: meta}, events)
    arguments['config']['thinking_mode'] = 'disabled'
    result = None
    for message, thinking in [('请阅读培训资料。', 'disabled'), ('按此安排第一周。', 'enabled'),
                              ('继续细化第二周。', 'disabled')]:
        arguments['config']['thinking_mode'] = thinking
        extra = {} if result is None else {'engine_history': result['engine_history'],
            'checkpoint': result['checkpoint'], 'history_dependencies': result['dependency_records']}
        result = asyncio.run(run_agent(**{**arguments, 'message': message}, **extra,
            transport_override=httpx2.MockTransport(provider)))
        assert result['engine_history_reusable'] is True
        assert any(item.get('reasoning_content') == 'native-tool-reasoning' for item in result['engine_history'])
    assert len(calls) == 4
    assert sum(item['role'] == 'user' and item['content'].endswith('请阅读培训资料。')
               for item in result['engine_history']) == 1


def test_library_scope_does_not_expose_arbitrary_file_names(library):
    from backend.app.core.hermes_agent import run_agent
    doc_id, meta = saved(library, '任意前列文件不得预览.txt', b'never read')
    def provider(request):
        body = json.loads(request.content)
        assert '任意前列文件不得预览' not in request.content.decode()
        assert doc_id not in request.content.decode()
        return response(body, answer='你好，我们可以一起整理资料。')
    result = asyncio.run(run_agent(**values(library, {doc_id: meta}, []), scope_mode='library',
        transport_override=httpx2.MockTransport(provider)))
    assert result['dependency_records'] == {}


def test_real_worker_soft_recovers_repeated_reads_and_unverified_citation(library):
    from backend.app.core.hermes_agent import run_agent
    doc_id, meta = saved(library, '单条资料.txt', '仅有一条正文'.encode())
    calls = []
    def provider(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) <= 5:
            return response(body, tool='read_document', arguments={'document_id': doc_id})
        raw = request.content.decode()
        assert 'repeated_exact_failure_warning' not in raw
        assert 'NO_NEW_EVIDENCE' in raw
        assert 'USE_EXISTING_EVIDENCE' in raw
        return response(body, answer='有效依据仍可用。[S1] 尚待核对的部分。[S999]')
    arguments = values(library, {doc_id: meta}, [])
    arguments['limits']['model_requests'] = 8
    result = asyncio.run(run_agent(**arguments, transport_override=httpx2.MockTransport(provider)))
    assert len(calls) == 6
    assert '[S1]' in result['answer'] and '[S999]' not in result['answer']
    assert '未核实' in result['answer']
    assert result['engine_history'][-1]['content'] == result['answer']


@pytest.mark.parametrize('continuation_mode', ['automatic', 'resume'])
def test_fresh_checkpoint_reaches_actual_model_when_native_history_is_unavailable(
        partner_library, monkeypatch, continuation_mode):
    """Both runtime continuation paths must carry fresh evidence despite old context."""
    from backend.app.config import settings
    from backend.app.core.hermes_agent import run_agent
    from backend.app.core.partner_budget import PartnerPaused
    from backend.tests.test_partner_runtime import configure
    runtime = partner_library
    configure(monkeypatch)
    monkeypatch.setattr(runtime, 'CHAT_SEGMENT_LIMITS', {'model_requests': 2, 'tool_calls': 64})
    doc_id, _ = saved(Path(settings.library_path), '续段资料.txt', b'fresh-evidence-marker-81943')
    requests, execution_options = [], []
    def provider(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return response(body, tool='read_document', arguments={'document_id': doc_id})
        if len(requests) == 2:
            return response(body, tool='create_draft', arguments={
                'name': '续段草稿.md', 'content': 'fresh-draft-marker-6729 [S1]', 'source_ids': ['S1']})
        raw = request.content.decode()
        assert 'fresh-evidence-marker-81943' in raw
        assert 'fresh-draft-marker-6729' in raw
        assert 'fresh-stage-summary-5711' in raw
        assert '保持已读来源编号' in raw and '图像' in raw
        return response(body, answer='沿用上一段真实依据与草稿完成。[S1]')
    async def actual_engine(**options):
        execution_options.append(options)
        try:
            return await run_agent(**options, transport_override=httpx2.MockTransport(provider))
        except PartnerPaused as exc:
            # A visual or oversized native context legitimately falls back to the checkpoint.
            output = {**exc.output, 'engine_history': [], 'engine_history_reusable': False,
                      'answer': 'fresh-stage-summary-5711'}
            raise PartnerPaused('time_seconds' if continuation_mode == 'resume' else exc.reason, output)
    monkeypatch.setattr(runtime, 'run_agent', actual_engine)
    async def wait_task(task):
        for _ in range(2000):
            result = runtime.get_task(task['id'])
            if result['state'] not in {'queued', 'running'}:
                return result
            await asyncio.sleep(.01)
        pytest.fail('real engine continuation timed out')
    async def exercise():
        session = runtime.create_session()
        revision = runtime.chat_config()['service_revision']
        task = await runtime.send(session['id'], message='阅读资料、生成草稿并完成分析',
            scope_mode='selected', document_ids=[doc_id], thinking_mode='disabled',
            request_id='start-continuation', expected_service_revision=revision, consent=True)
        result = await wait_task(task)
        if continuation_mode == 'resume':
            assert result['state'] == 'paused'
            result = await wait_task(await runtime.resume(task['id'], request_id='resume-continuation',
                expected_service_revision=revision, consent=True))
        return result
    result = asyncio.run(exercise())
    assert result['state'] == 'completed', result.get('error')
    assert len(requests) == 3 and len(execution_options) == 2
    assert execution_options[1]['context'] is not None
    assert not execution_options[1].get('engine_history')
    assert result['sources'] and result['artifacts'][0]['name'] == '续段草稿.md'


def test_real_worker_repetition_stop_has_clear_reason_and_keeps_preview(library):
    from backend.app.core.hermes_agent import run_agent
    from backend.app.core.ai_client import AIError
    events = []
    class RepeatingStream(httpx2.AsyncByteStream):
        async def __aiter__(self):
            chunks = ['已理解这次资料整理的目标。'] + ['repeated-synthetic-fragment ' * 50] * 20
            for text in chunks:
                frame = {'id': 'synthetic', 'object': 'chat.completion.chunk', 'created': 1,
                    'choices': [{'index': 0, 'delta': {'content': text}, 'finish_reason': None}]}
                yield ('data: ' + json.dumps(frame, ensure_ascii=False) + '\n\n').encode()
                await asyncio.sleep(.005)
            yield b'data: [DONE]\n\n'
    with pytest.raises(AIError) as error:
        asyncio.run(run_agent(**values(library, {}, events), transport_override=httpx2.MockTransport(
            lambda request: httpx2.Response(200, headers={'content-type': 'text/event-stream'},
                                            stream=RepeatingStream()))))
    assert error.value.code == 'AI_REPETITION_STOPPED'
    assert '重复' in str(error.value) and 'HERMES_' not in str(error.value)
    assert any(kind == 'answer_preview' and text for kind, text in events)
