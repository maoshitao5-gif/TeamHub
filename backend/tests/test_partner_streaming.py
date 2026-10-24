"""通过合成 SSE 验证真实 SDK 流式输出；所有外发均由本机假传输截获。"""
import asyncio
import gzip
import json

import httpx2
import pytest

from backend.app import database
from backend.app.config import settings
from backend.app.core.ai_client import AIError
from backend.app.core.partner_protocol import BoundedTransport
from backend.tests.support.legacy_partner_agent import run_agent
from backend.app.core.partner_budget import normalize_budget


@pytest.fixture
def library(tmp_path, monkeypatch):
    root = tmp_path / 'library'
    root.mkdir()
    monkeypatch.setattr(settings, 'library_path', str(root))
    database.rebind_engine()
    database.init_db()
    yield root
    assert database.active_session_count() == 0


def chunk(delta=None, *, finish=None, usage=None):
    value = {'id': 'synthetic-stream', 'object': 'chat.completion.chunk', 'created': 1,
             'model': 'deepseek-flash', 'choices': [] if delta is None else [
                 {'index': 0, 'delta': delta, 'finish_reason': finish}]}
    if usage is not None:
        value['usage'] = usage
    return ('data: ' + json.dumps(value, ensure_ascii=False) + '\n\n').encode()


def tool_delta(name, arguments):
    return {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'call-stream', 'type': 'function',
             'function': {'name': name, 'arguments': arguments}}]}


class SyntheticStream(httpx2.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for value in self.chunks:
            yield value

    async def aclose(self):
        self.closed = True


def json_answer(answer='完成。', *, reasoning=None, name='final_result', arguments=None):
    message = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'synthetic-call',
        'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments or {
            'answer': answer, 'source_ids': []}, ensure_ascii=False)}}]}
    if reasoning:
        message['reasoning_content'] = reasoning
    return {'id': 'synthetic', 'object': 'chat.completion', 'created': 1, 'model': 'deepseek-flash',
        'choices': [{'index': 0, 'message': message, 'finish_reason': 'tool_calls'}],
        'usage': {'prompt_tokens': 13, 'completion_tokens': 57, 'total_tokens': 70}}


async def execute(root, handler, *, config=None, emit=None, limits=None, guard=None):
    return await run_agent(message='讨论一个学习计划', history=[], scope={},
        config=config or {'base_url': 'https://synthetic.invalid/v1', 'model': 'deepseek-flash'},
        key='synthetic-not-a-real-secret', engine=database.engine, root=str(root),
        guard=guard or (lambda: None), emit=emit or (lambda *args: None),
        limits=limits or normalize_budget(), transport_override=httpx2.MockTransport(handler))


def test_real_sdk_previews_answer_before_provider_finishes_and_never_exposes_reasoning(library):
    async def check():
        preview_seen = asyncio.Event()
        events, payloads = [], []
        class GatedStream(SyntheticStream):
            async def __aiter__(self):
                yield chunk({'role': 'assistant', 'reasoning_content': 'PRIVATE-REASONING-DO-NOT-EXPOSE'})
                yield chunk(tool_delta('final_result', '{"answer":"正在形成的答复'))
                await asyncio.wait_for(preview_seen.wait(), timeout=.8)
                yield chunk({'tool_calls': [{'index': 0, 'function': {'arguments': '，已完成。","source_ids":[]}'}}]}, finish='tool_calls')
                yield chunk(usage={'prompt_tokens': 13, 'completion_tokens': 57, 'total_tokens': 70})
                yield b'data: [DONE]\n\n'
        stream = GatedStream([])
        def emit(kind, content):
            events.append((kind, content))
            if kind == 'answer_preview' and content == '正在形成的答复':
                preview_seen.set()
        def handler(request):
            payloads.append(json.loads(request.content))
            return httpx2.Response(200, headers={'content-type': 'text/event-stream'}, stream=stream)
        result = await execute(library, handler, emit=emit)
        assert payloads[0]['stream'] is True
        assert payloads[0]['thinking'] == {'type': 'enabled'}
        assert payloads[0]['max_tokens'] > 6000
        assert result['answer'] == '正在形成的答复，已完成。'
        assert result['usage']['input_tokens'] == 13 and result['usage']['output_tokens'] == 57
        assert result['usage']['token_usage_complete'] is True
        assert len(payloads) == result['usage']['model_requests'] == 1
        assert 'PRIVATE-REASONING' not in str(events) + str(result)
        assert stream.closed
    asyncio.run(check())


@pytest.mark.parametrize(('model', 'mode', 'expected'), [
    ('deepseek-flash', None, 'enabled'), ('deepseek-flash', 'disabled', 'disabled'),
    ('synthetic-openai-compatible', 'enabled', None)])
def test_provider_nonstream_json_fallback_never_resends_and_thinking_is_provider_scoped(library, model, mode, expected):
    requests, events = [], []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx2.Response(200, json=json_answer())
    config = {'base_url': 'https://synthetic.invalid/v1', 'model': model}
    if mode:
        config['thinking_mode'] = mode
    result = asyncio.run(execute(library, handler, config=config, emit=lambda *args: events.append(args)))
    assert len(requests) == 1
    assert requests[0]['stream'] is True
    if expected:
        assert requests[0]['thinking'] == {'type': expected}
    else:
        assert 'thinking' not in requests[0]
    assert result['answer'] == '完成。'
    assert ('answer_preview', '完成。') in events


def test_deepseek_reasoning_is_retained_only_within_same_sdk_tool_loop(library):
    requests = []
    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            return httpx2.Response(200, json=json_answer(reasoning='PRIVATE-TOOL-REASONING',
                name='search_documents', arguments={'query': '学习'}))
        assistant = next(item for item in payload['messages'] if item['role'] == 'assistant')
        assert assistant['reasoning_content'] == 'PRIVATE-TOOL-REASONING'
        return httpx2.Response(200, json=json_answer())
    result = asyncio.run(execute(library, handler))
    assert len(requests) == result['usage']['model_requests'] == 2
    assert 'PRIVATE-TOOL-REASONING' not in str(result)


def test_streamed_unverified_source_is_rejected_after_preview_without_retry(library):
    events, calls = [], []
    def handler(request):
        calls.append(request)
        values = [chunk(tool_delta('final_result', '{"answer":"未校验。[S999]","source_ids":["S999"]}'), finish='tool_calls'),
                  b'data: [DONE]\n\n']
        return httpx2.Response(200, headers={'content-type': 'text/event-stream'}, stream=SyntheticStream(values))
    with pytest.raises(AIError) as error:
        asyncio.run(execute(library, handler, emit=lambda *args: events.append(args)))
    assert error.value.code == 'PARTNER_INVALID_SOURCES'
    assert ('answer_preview', '未校验。[S999]') in events
    assert len(calls) == 1


def test_stream_usage_is_one_latest_snapshot_and_missing_usage_remains_unknown(library):
    for include_usage in (True, False):
        def handler(request):
            values = [chunk({'role': 'assistant', 'reasoning_content': 'hidden'},
                            usage={'prompt_tokens': 13, 'completion_tokens': 10, 'total_tokens': 23} if include_usage else None),
                      chunk(tool_delta('final_result', '{"answer":"完成。","source_ids":[]}'), finish='tool_calls')]
            if include_usage:
                values.append(chunk(usage={'prompt_tokens': 13, 'completion_tokens': 57, 'total_tokens': 70}))
            return httpx2.Response(200, headers={'content-type': 'text/event-stream'}, stream=SyntheticStream([*values, b'data: [DONE]\n\n']))
        result = asyncio.run(execute(library, handler))
        assert result['usage']['model_requests'] == 1
        assert result['usage']['token_usage_complete'] is include_usage
        assert result['usage']['unmetered_requests'] == (0 if include_usage else 1)
        assert result['usage']['output_tokens'] == (57 if include_usage else 0)


def test_bounded_transport_returns_headers_before_stream_is_consumed_and_closes_early():
    async def check():
        consumed, reports = [], []
        class Stream(SyntheticStream):
            async def __aiter__(self):
                consumed.append('first')
                yield b'first'
                consumed.append('second')
                yield b'second'
        upstream = Stream([])
        transport = BoundedTransport(lambda: None,
            httpx2.MockTransport(lambda request: httpx2.Response(200, stream=upstream)),
            after_response=lambda body: reports.append(body))
        async with httpx2.AsyncClient(transport=transport) as client:
            async with client.stream('GET', 'https://synthetic.invalid') as response:
                assert consumed == []
                async for part in response.aiter_bytes():
                    assert part == b'first'
                    break
        assert consumed == ['first']
        assert upstream.closed and reports == []
    asyncio.run(check())


def test_bounded_transport_limits_decompressed_stream_and_closes(monkeypatch):
    import backend.app.core.partner_protocol as module
    monkeypatch.setattr(module, 'MAX_RESPONSE_BYTES', 20)
    stream = SyntheticStream([gzip.compress(b'x' * 21)])
    async def check():
        transport = BoundedTransport(lambda: None, httpx2.MockTransport(lambda request:
            httpx2.Response(200, headers={'content-encoding': 'gzip'}, stream=stream)))
        async with httpx2.AsyncClient(transport=transport) as client:
            with pytest.raises(AIError) as error:
                await client.get('https://synthetic.invalid')
            assert error.value.code == 'AI_INVALID_RESULT'
    asyncio.run(check())
    assert stream.closed


def test_summary_uses_reserved_budget_in_fast_mode_after_streamed_tool_loop(library):
    from backend.app.core.partner_budget import PartnerPaused
    calls = []
    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        user = next(item for item in reversed(payload['messages']) if item['role'] == 'user')
        if json.loads(user['content']).get('purpose') == 'progress_summary':
            assert payload['thinking'] == {'type': 'disabled'}
            assert payload['max_tokens'] <= 1000
            return httpx2.Response(200, json=json_answer('已保留进度，任务尚未完成。'))
        assert payload['thinking'] == {'type': 'enabled'}
        return httpx2.Response(200, json=json_answer(name='search_documents',
            arguments={'query': f'检索{len(calls)}'}))
    limits = normalize_budget({'mode': 'custom', 'model_requests': 4, 'tool_calls': 8,
                               'output_tokens': 4000, 'time_seconds': 60})
    with pytest.raises(PartnerPaused) as paused:
        asyncio.run(execute(library, handler, limits=limits))
    assert paused.value.reason == 'model_requests'
    assert paused.value.output['usage']['model_requests'] == len(calls) == 4
    assert paused.value.output['answer'] == '已保留进度，任务尚未完成。'


def test_guard_revocation_during_stream_stops_before_next_bytes_and_closes():
    from fastapi import HTTPException
    checks = []
    stream = SyntheticStream([b'first', b'second'])
    def guard():
        checks.append(True)
        if len(checks) == 3:
            raise HTTPException(409, detail='synthetic authorization revoked')
    async def check():
        transport = BoundedTransport(guard, httpx2.MockTransport(lambda request:
            httpx2.Response(200, stream=stream)))
        async with httpx2.AsyncClient(transport=transport) as client:
            async with client.stream('GET', 'https://synthetic.invalid') as response:
                received = []
                with pytest.raises(HTTPException):
                    async for value in response.aiter_bytes():
                        received.append(value)
                assert received == [b'first']
    asyncio.run(check())
    assert stream.closed


def test_normal_round_context_matches_preview_without_duplicate_continuation(library):
    from backend.app.services.partner_tools import PartnerTools
    context = {'summary': '用户希望按周安排学习；已明确每周两次。', 'evidence': [], 'sources': []}
    checkpoint = PartnerTools(engine=database.engine, root=str(library), scope={},
                              guard=lambda: None, emit=lambda *args: None).export_checkpoint()
    calls = []
    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        prompt = json.loads(next(item['content'] for item in reversed(payload['messages']) if item['role'] == 'user'))
        assert prompt['context'] == context
        assert prompt['continuation_context'] is None
        assert str(payload).count(context['summary']) == 1
        return httpx2.Response(200, json=json_answer('每周两次安排如下。'))
    result = asyncio.run(run_agent(message='按之前的要求细化', history=[], scope={},
        config={'base_url': 'https://synthetic.invalid', 'model': 'deepseek-flash'}, key='synthetic-not-real',
        engine=database.engine, root=str(library), guard=lambda: None, emit=lambda *args: None,
        limits=normalize_budget(), checkpoint=checkpoint, context=context,
        previous_answer='这段旧总结不得重复发送', transport_override=httpx2.MockTransport(handler)))
    assert len(calls) == 1 and result['context_summary'] == result['answer']
    assert '这段旧总结' not in str(calls)


def test_stream_deadline_closes_inflight_response_and_accounts_sent_unknown_attempt(library):
    from backend.app.core.partner_budget import PartnerPaused
    class StalledStream(SyntheticStream):
        async def __aiter__(self):
            yield chunk({'role': 'assistant', 'reasoning_content': 'hidden'})
            await asyncio.sleep(2)
            yield chunk(tool_delta('final_result', '{"answer":"迟到","source_ids":[]}'), finish='tool_calls')
    stream, calls = StalledStream([]), []
    def handler(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx2.Response(200, headers={'content-type': 'text/event-stream'}, stream=stream)
        return httpx2.Response(200, json=json_answer('本段时间已到，任务未完成。'))
    limits = {**normalize_budget(), 'time_seconds': 5.01}
    with pytest.raises(PartnerPaused) as paused:
        asyncio.run(execute(library, handler, limits=limits))
    assert paused.value.reason == 'time_seconds' and stream.closed
    assert paused.value.output['usage']['model_requests'] == len(calls) == 2
    assert paused.value.output['usage']['unmetered_requests'] == 1
    assert paused.value.output['usage']['token_usage_complete'] is False
    assert '迟到' not in paused.value.output['answer']
