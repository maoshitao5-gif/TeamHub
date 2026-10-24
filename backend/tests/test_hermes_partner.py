"""Hermes 独立进程契约；只使用临时资料库和本机假供应商。"""
import asyncio
import json
import sys
import threading
import time

import httpx2
import pytest

from backend.tests.test_partner_runtime import partner_library, configure  # noqa: F401


def test_prepare_discloses_frozen_local_hermes_engine(partner_library, monkeypatch):
    configure(monkeypatch)
    session = partner_library.create_session()
    prepared = partner_library.prepare(session['id'], '讨论培训计划')
    assert prepared['service']['engine'] == 'hermes'
    assert prepared['service']['engine_version'] == 'v0.22.2'
    assert prepared['service']['engine_protocol'] == 1


@pytest.fixture
def fake_worker(tmp_path, monkeypatch):
    from backend.app.core import hermes_agent
    worker = tmp_path / 'worker.py'
    worker.write_text('''import base64,json,sys
def send(value):
 print(json.dumps({'v':1,**value}),flush=True)
def receive():
 return json.loads(sys.stdin.readline())
send({'type':'ready','protocol':1,'version':'v0.22.2','adapter_revision':7})
start=receive()
assert start['type']=='start'
assert 'key' not in start and 'root' not in start
assert start['model']=='deepseek-synthetic'
send({'type':'model_request','id':'m1','body':{'model':start['model'],'messages':[{'role':'user','content':start['question']}],'stream':True}})
chunks=[]
while True:
 value=receive()
 if value['type']=='model_chunk': chunks.append(base64.b64decode(value['data']))
 if value['type']=='model_end': break
assert b'synthetic-answer' in b''.join(chunks)
send({'type':'answer_preview','text':'可以一起讨论培训目标。'})
send({'type':'completed','answer':'可以一起讨论培训目标。'})
''', encoding='utf-8')
    monkeypatch.setattr(hermes_agent, '_command', lambda: [sys.executable, str(worker)])
    return hermes_agent


def _values(partner_library, emit=lambda *_: None):
    from backend.app import database
    from backend.app.config import settings
    return dict(message='你好', history=[], scope={},
        config={'base_url': 'https://api.deepseek.com/v1', 'model': 'deepseek-synthetic',
                'thinking_mode': 'enabled'}, key='synthetic-not-a-real-key',
        engine=database.engine, root=settings.library_path, guard=lambda: None, emit=emit,
        limits={'model_requests': 4, 'tool_calls': 8, 'output_tokens': 4000, 'time_seconds': 60})


def test_separate_worker_streams_through_host_metered_provider(partner_library, fake_worker):
    requests, previews = [], []
    def provider(request):
        requests.append(request)
        assert request.url == 'https://api.deepseek.com/v1/chat/completions'
        assert request.headers['authorization'] == 'Bearer synthetic-not-a-real-key'
        value = json.loads(request.content)
        assert value['thinking'] == {'type': 'enabled'}
        return httpx2.Response(200, json={'id': 'synthetic', 'model': value['model'],
            'choices': [{'index': 0, 'finish_reason': 'stop',
                         'message': {'role': 'assistant', 'content': 'synthetic-answer'}}],
            'usage': {'prompt_tokens': 20, 'completion_tokens': 10}})
    result = asyncio.run(fake_worker.run_agent(**_values(partner_library,
        emit=lambda kind, text: previews.append((kind, text))),
        transport_override=httpx2.MockTransport(provider)))
    assert result['answer'] == '可以一起讨论培训目标。'
    assert result['usage']['model_requests'] == 1
    assert result['usage']['output_tokens'] == 10
    assert result['usage']['token_usage_complete'] is True
    assert ('answer_preview', result['answer']) in previews
    assert len(requests) == 1


def test_slow_authorization_checks_do_not_block_server_event_loop(partner_library, fake_worker):
    """Synthetic OS/config latency must not freeze health requests during AI streaming."""
    async def run():
        loop_thread = threading.get_ident()
        check_threads, gaps = [], []
        running = True

        def guard():
            check_threads.append(threading.get_ident())
            time.sleep(.06)

        async def heartbeat():
            previous = time.monotonic()
            while running:
                await asyncio.sleep(.005)
                now = time.monotonic()
                gaps.append(now - previous)
                previous = now

        pulse = asyncio.create_task(heartbeat())
        await asyncio.sleep(0)
        try:
            values = {**_values(partner_library), 'guard': guard}
            result = await fake_worker.run_agent(**values,
                transport_override=httpx2.MockTransport(lambda request: httpx2.Response(200,
                    json={'choices': [{'message': {'content': 'synthetic-answer'},
                                       'finish_reason': 'stop'}]})))
            assert result['answer']
        finally:
            running = False
            await pulse
        assert check_threads and loop_thread not in check_threads
        assert gaps and max(gaps) < .05
    asyncio.run(run())


def test_stream_chunks_only_do_light_checks_and_final_sources_stay_verified(
        partner_library, fake_worker, monkeypatch):
    from backend.app.services.hermes_tools import HermesTools
    from backend.app.core.ai_client import AIError
    checks, complete_checks = [], []

    def verify(_self):
        complete_checks.append(True)
        if len(complete_checks) == 2:
            raise AIError(409, 'PARTNER_DOCUMENT_CHANGED', '合成资料版本已变化')

    class Stream(httpx2.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(40):
                yield b'synthetic-answer'

    monkeypatch.setattr(HermesTools, 'verify', verify)
    values = {**_values(partner_library), 'guard': lambda: checks.append(True)}
    with pytest.raises(AIError) as error:
        asyncio.run(fake_worker.run_agent(**values,
            transport_override=httpx2.MockTransport(lambda request:
                httpx2.Response(200, stream=Stream()))))
    assert error.value.code == 'PARTNER_DOCUMENT_CHANGED'
    assert len(checks) >= 40  # Cancel/config/library checks remain per chunk.
    assert len(complete_checks) == 2  # Before dispatch and before accepting the result.


def test_failed_provider_is_not_retried_by_worker(partner_library, fake_worker):
    from backend.app.core.ai_client import AIError
    requests = []
    def provider(request):
        requests.append(request)
        return httpx2.Response(429, json={'error': 'do-not-leak-provider-body'})
    with pytest.raises(AIError) as error:
        asyncio.run(fake_worker.run_agent(**_values(partner_library),
            transport_override=httpx2.MockTransport(provider)))
    assert error.value.code == 'AI_RATE_LIMITED'
    assert 'do-not-leak' not in str(error.value)
    assert len(requests) == 1


def test_runtime_uses_hermes_without_changing_public_task_contract():
    from backend.app.services import partner_runtime
    assert partner_runtime.run_agent.__module__ == 'backend.app.core.hermes_agent'


def test_unknown_tool_name_type_is_a_safe_tool_error(partner_library):
    from backend.app import database
    from backend.app.config import settings
    from backend.app.services.hermes_tools import HermesTools
    bridge = HermesTools(engine=database.engine, root=settings.library_path, scope={},
                         guard=lambda: None, emit=lambda *_: None)
    result = asyncio.run(bridge.call([], {}))
    assert result['result']['ok'] is False


def test_protocol_requires_explicit_integer_version():
    from types import SimpleNamespace
    from backend.app.core import hermes_agent
    from backend.app.core.ai_client import AIError
    async def run(value):
        reader = asyncio.StreamReader()
        reader.feed_data((json.dumps(value) + '\n').encode())
        reader.feed_eof()
        with pytest.raises(AIError):
            await hermes_agent._read(SimpleNamespace(stdout=reader))
    for value in ({'type': 'ready'}, {'v': True, 'type': 'ready'}, {'v': 2, 'type': 'ready'}):
        asyncio.run(run(value))


def test_cancel_reaps_owned_worker_without_model_request(partner_library, tmp_path, monkeypatch):
    from backend.app.core import hermes_agent
    worker = tmp_path / 'hang.py'
    worker.write_text('''import json,sys,time
print(json.dumps({'v':1,'type':'ready','protocol':1,'version':'v0.22.2','adapter_revision':7}),flush=True)
sys.stdin.readline()
time.sleep(60)
''', encoding='utf-8')
    monkeypatch.setattr(hermes_agent, '_command', lambda: [sys.executable, str(worker)])
    async def run():
        started = asyncio.Event()
        task = asyncio.create_task(hermes_agent.run_agent(**_values(partner_library,
            emit=lambda kind, _text: started.set() if kind == 'status' else None)))
        await asyncio.wait_for(started.wait(), 3)
        children = tuple(hermes_agent._active)
        assert children
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert all(child.returncode is not None for child in children)
        assert not hermes_agent._active
    asyncio.run(run())


def test_repeated_cancel_still_reaps_worker_ignoring_term(partner_library, tmp_path, monkeypatch):
    from backend.app.core import hermes_agent
    worker = tmp_path / 'ignore-term.py'
    worker.write_text('''import json,signal,sys,time
signal.signal(signal.SIGTERM,signal.SIG_IGN)
print(json.dumps({'v':1,'type':'ready','protocol':1,'version':'v0.22.2','adapter_revision':7}),flush=True)
sys.stdin.readline()
time.sleep(60)
''', encoding='utf-8')
    monkeypatch.setattr(hermes_agent, '_command', lambda: [sys.executable, str(worker)])
    async def run():
        started = asyncio.Event()
        task = asyncio.create_task(hermes_agent.run_agent(**_values(partner_library,
            emit=lambda kind, _text: started.set() if kind == 'status' else None)))
        await asyncio.wait_for(started.wait(), 3)
        children = tuple(hermes_agent._active)
        try:
            task.cancel()
            await asyncio.sleep(.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert all(child.returncode is not None for child in children)
            assert not hermes_agent._active
        finally:
            for child in children:
                if child.returncode is None:
                    child.kill()
                await child.wait()
                hermes_agent._active.discard(child)
    asyncio.run(run())


def _scripted_worker(tmp_path, monkeypatch, actions):
    from backend.app.core import hermes_agent
    worker = tmp_path / 'scripted.py'
    worker.write_text('''import json,sys
def send(value): print(json.dumps({'v':1,**value}),flush=True)
send({'type':'ready','protocol':1,'version':'v0.22.2','adapter_revision':7})
start=json.loads(sys.stdin.readline())
actions=json.loads(sys.argv[1])
for value in actions:
 send(value)
 if value['type']=='tool_call':
  result=json.loads(sys.stdin.readline())
  assert result['type']=='tool_result'
''', encoding='utf-8')
    monkeypatch.setattr(hermes_agent, '_command', lambda: [sys.executable, str(worker), json.dumps(actions)])
    return hermes_agent


def test_tool_budget_pauses_and_preserves_progress(partner_library, tmp_path, monkeypatch):
    from backend.app.core.partner_budget import PartnerPaused
    actions = [{'type': 'tool_call', 'id': f'tool-{number}', 'name': 'search_documents',
                'arguments': {'query': '资料'}} for number in range(9)]
    agent = _scripted_worker(tmp_path, monkeypatch, actions)
    with pytest.raises(PartnerPaused) as error:
        asyncio.run(agent.run_agent(**_values(partner_library)))
    assert error.value.reason == 'tool_calls'
    assert error.value.output['usage']['tool_calls'] == 8
    assert error.value.output['checkpoint']['schema_version'] == 3
    assert not agent._active


def test_unapproved_model_is_rejected_before_provider_dispatch(partner_library, tmp_path, monkeypatch):
    from backend.app.core.ai_client import AIError
    calls = []
    agent = _scripted_worker(tmp_path, monkeypatch, [{'type': 'model_request', 'id': 'model',
        'body': {'model': 'unapproved-model', 'messages': []}}])
    with pytest.raises(AIError) as error:
        asyncio.run(agent.run_agent(**_values(partner_library),
            transport_override=httpx2.MockTransport(lambda request: calls.append(request))))
    assert error.value.code == 'HERMES_PROTOCOL_ERROR'
    assert not calls


def test_host_tool_bridge_rejects_cross_scope_and_invalid_arguments(partner_library):
    from backend.app import database
    from backend.app.config import settings
    from backend.app.services.hermes_tools import HermesTools
    bridge = HermesTools(engine=database.engine, root=settings.library_path, scope={},
                         guard=lambda: None, emit=lambda *_: None)
    async def check():
        for name, arguments in [('read_document', {'document_id': 'not-authorized'}),
                                ('read_table', {'document_id': 'doc', 'start_row': True}),
                                ('read_visual', {'document_id': 'doc', 'pages': [1, 2, 3, 4]}),
                                ('terminal', {'command': 'cat private-file'})]:
            result = await bridge.call(name, arguments)
            assert result['result'].get('ok') is False
        assert bridge.tools.sources == []
        for name in ('../escape.md', 'file.xlsx'):
            result = await bridge.call('create_draft', {'name': name, 'content': 'content'})
            assert result['result']['ok'] is False
        assert bridge.artifacts == []
    asyncio.run(check())


def test_usage_uses_last_cumulative_sse_frame_and_flags_unknown():
    from backend.app.core.hermes_agent import _usage_from_response
    content = b'data: {"usage":{"prompt_tokens":20,"completion_tokens":10}}\n\n' + \
              b'data: {"usage":{"prompt_tokens":20,"completion_tokens":30}}\n\ndata: [DONE]\n\n'
    assert _usage_from_response(content) == (20, 30)
    for value in (b'{}', b'[]', b'data: broken\n\n',
                  b'{"usage":{"prompt_tokens":true,"completion_tokens":-1}}'):
        assert _usage_from_response(value) is None


def test_unknown_citation_preserves_answer_and_marks_verification_gap(partner_library, tmp_path, monkeypatch):
    events = []
    agent = _scripted_worker(tmp_path, monkeypatch,
        [{'type': 'completed', 'answer': '这里有可用分析。[S999]'}])
    output = asyncio.run(agent.run_agent(**_values(partner_library, lambda *item: events.append(item))))
    assert '这里有可用分析' in output['answer']
    assert '[S999]' not in output['answer']
    assert '未核实' in output['answer']
    assert output['sources'] == []
    assert any(kind == 'warning' for kind, _ in events)


def test_long_answer_is_preserved_without_old_18000_character_limit(partner_library, tmp_path, monkeypatch):
    answer = '连续长回答。' * 5000
    agent = _scripted_worker(tmp_path, monkeypatch,
        [{'type': 'answer_preview', 'text': answer}, {'type': 'completed', 'answer': answer}])
    output = asyncio.run(agent.run_agent(**_values(partner_library)))
    assert output['answer'] == answer


def test_no_progress_tools_return_recovery_instructions_instead_of_aborting(partner_library, monkeypatch):
    from backend.app import database
    from backend.app.config import settings
    from backend.app.services.hermes_tools import HermesTools
    from backend.app.services.partner_tools import PartnerNoProgress
    bridge = HermesTools(engine=database.engine, root=settings.library_path, scope={},
                         guard=lambda: None, emit=lambda *_: None)
    def no_progress(**_kwargs):
        raise PartnerNoProgress('no new evidence')
    monkeypatch.setattr(bridge.tools, 'search_documents', no_progress)
    output = asyncio.run(bridge.call('search_documents', {'query': 'missing'}))
    assert output['result']['ok'] is False
    assert output['result']['code'] == 'NO_NEW_EVIDENCE'
    assert output['result']['next_steps']


def test_hermes_response_allows_stream_overhead_above_legacy_limit(partner_library, fake_worker):
    class LargeStream(httpx2.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(24):
                yield b'synthetic-answer ' + b' ' * 32768
    result = asyncio.run(fake_worker.run_agent(**_values(partner_library),
        transport_override=httpx2.MockTransport(lambda request:
            httpx2.Response(200, stream=LargeStream()))))
    assert result['answer'] == '可以一起讨论培训目标。'


def test_oversized_answer_has_specific_error_instead_of_silent_truncation(partner_library, tmp_path, monkeypatch):
    from backend.app.core.ai_client import AIError
    agent = _scripted_worker(tmp_path, monkeypatch,
        [{'type': 'completed', 'answer': 'x' * 64001}])
    with pytest.raises(AIError) as error:
        asyncio.run(agent.run_agent(**_values(partner_library)))
    assert error.value.code == 'PARTNER_ANSWER_TOO_LONG'


def test_repeated_draft_after_internal_resume_reuses_existing_artifact(partner_library):
    from backend.app import database
    from backend.app.config import settings
    from backend.app.services.hermes_tools import HermesTools
    bridge = HermesTools(engine=database.engine, root=settings.library_path, scope={},
                         guard=lambda: None, emit=lambda *_: None)
    async def check():
        first = await bridge.call('create_draft', {'name': '计划.md', 'content': '同一草稿'})
        second = await bridge.call('create_draft', {'name': '计划.md', 'content': '同一草稿'})
        assert first['result']['id'] == second['result']['id']
        assert len(bridge.artifacts) == 1
    asyncio.run(check())


def test_cancel_during_worker_spawn_still_takes_ownership_and_reaps(partner_library, tmp_path, monkeypatch):
    from backend.app.core import hermes_agent
    worker = tmp_path / 'spawn-cancel.py'
    worker.write_text('import time\ntime.sleep(60)\n', encoding='utf-8')
    monkeypatch.setattr(hermes_agent, '_command', lambda: [sys.executable, str(worker)])
    async def run():
        original = asyncio.create_subprocess_exec
        spawned, release = asyncio.Event(), asyncio.Event()
        children = []
        async def delayed(*args, **kwargs):
            child = await original(*args, **kwargs)
            children.append(child)
            spawned.set()
            await release.wait()
            return child
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', delayed)
        task = asyncio.create_task(hermes_agent.run_agent(**_values(partner_library)))
        try:
            await asyncio.wait_for(spawned.wait(), 3)
            task.cancel()
            await asyncio.sleep(.01)
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            assert all(child.returncode is not None for child in children)
            assert not hermes_agent._active
        finally:
            release.set()
            for child in children:
                if child.returncode is None:
                    child.kill()
                await child.wait()
                hermes_agent._active.discard(child)
    asyncio.run(run())


def test_outdated_worker_adapter_explains_rebuild_before_any_model_request(partner_library, tmp_path, monkeypatch):
    from backend.app.core import hermes_agent
    from backend.app.core.ai_client import AIError
    worker = tmp_path / 'old-worker.py'
    worker.write_text('import json,sys\nprint(json.dumps({"v":1,"type":"ready","protocol":1,"version":"v0.22.2"}),flush=True)\nsys.stdin.readline()\n', encoding='utf-8')
    monkeypatch.setattr(hermes_agent, '_command', lambda: [sys.executable, str(worker)])
    calls = []
    with pytest.raises(AIError) as error:
        asyncio.run(hermes_agent.run_agent(**_values(partner_library),
            transport_override=httpx2.MockTransport(lambda request: calls.append(request))))
    assert error.value.code == 'HERMES_ADAPTER_OUTDATED'
    assert not calls
