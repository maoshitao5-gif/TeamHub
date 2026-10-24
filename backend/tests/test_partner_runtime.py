"""智能伙伴的授权、会话和持久任务边界；全部资料均为临时测试资料。"""
import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

import backend.app.database as database
from backend.app.config import settings


@pytest.fixture
def partner_library(tmp_path, monkeypatch):
    previous = settings.library_path
    monkeypatch.setenv('TEAMHUB_AI_CONFIG_DIR', str(tmp_path / 'ai-profile'))
    monkeypatch.setattr(settings, 'library_path', str(tmp_path / 'library'))
    database.rebind_engine()
    from backend.app import partner_models  # noqa: F401
    database.init_db()
    from backend.app.services import partner_runtime as runtime
    runtime.initialize()
    yield runtime
    asyncio.run(runtime.shutdown())
    settings.library_path = previous
    database.rebind_engine()


def configure(monkeypatch):
    from backend.app.core import ai_settings
    public = {'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash'}
    monkeypatch.setattr(ai_settings, 'get_settings', lambda: {**public, 'has_api_key': True})
    monkeypatch.setattr(ai_settings, 'get_connection', lambda: (public, 'fake-secret', 'revision-1'))


def test_paused_task_continues_with_checkpoint_and_only_one_final_message(partner_library, monkeypatch):
    from backend.app.core.partner_budget import PartnerPaused
    runtime = partner_library
    configure(monkeypatch)
    calls = []
    async def fake_agent(**values):
        calls.append(values)
        assert database.active_session_count() == 0
        if not values.get('checkpoint'):
            raise PartnerPaused('model_requests', {'answer': '已读第一部分，尚未完成。', 'sources': [],
                'artifacts': [], 'usage': {'model_requests': 4, 'tool_calls': 3, 'output_tokens': 100, 'time_seconds': 1},
                'progress': {'read_count': 1}, 'checkpoint': {'evidence': ['真实原文']}, 'dependency_records': {}})
        assert values['checkpoint'] == {'evidence': ['真实原文']}
        return {'answer': '完成分析。', 'sources': [], 'artifacts': [],
                'usage': {'model_requests': 1, 'tool_calls': 0, 'output_tokens': 20, 'time_seconds': 1}}
    monkeypatch.setattr(runtime, 'run_agent', fake_agent)
    session = runtime.create_session()
    prepared = runtime.prepare(session['id'], '阅读完整资料', 'selected', [], {'mode': 'quick'})
    async def execute():
        task = await runtime.start(session['id'], prepared['preparation_id'], True, 'start-budget')
        for _ in range(30):
            await asyncio.sleep(.01)
            task = runtime.get_task(task['id'])
            if task['state'] == 'paused':
                break
        assert task['state'] == 'paused'
        assert task['pause']['reason'] == 'model_requests'
        assert 'checkpoint' not in task
        assert task['usage']['model_requests'] == 4
        with pytest.raises(HTTPException):
            runtime.prepare(session['id'], '新问题')
        resume = runtime.prepare_continue(task['id'], {'mode': 'quick'})
        assert resume['continuation'] is True
        result = await runtime.continue_task(task['id'], resume['preparation_id'], True, 'continue-budget')
        repeated = await runtime.continue_task(task['id'], resume['preparation_id'], True, 'continue-budget')
        assert result['id'] == repeated['id'] == task['id']
        for _ in range(30):
            await asyncio.sleep(.01)
            result = runtime.get_task(task['id'])
            if result['state'] == 'completed':
                break
        return result
    result = asyncio.run(execute())
    assert result['state'] == 'completed'
    assert result['usage']['model_requests'] == 5
    assert len(calls) == 2
    assert [m['role'] for m in runtime.get_session(session['id'])['messages']] == ['user', 'assistant']


def test_cancel_paused_task_and_restart_keeps_progress(partner_library, monkeypatch):
    import json
    from backend.app.partner_models import PartnerTask
    runtime = partner_library
    configure(monkeypatch)
    session = runtime.create_session()
    with database.SessionLocal() as db:
        task = PartnerTask(session_id=session['id'], request_id='paused', preparation_id='old', state='paused',
            message='旧问题', input_json='{}', result_json=json.dumps({'answer': '阶段成果',
            'pause': {'reason': 'model_requests', 'resumable': True}, 'checkpoint': {'evidence': []}}))
        db.add(task); db.commit(); task_id = task.id
    runtime.initialize()
    assert runtime.get_task(task_id)['state'] == 'paused'
    result = asyncio.run(runtime.cancel(task_id))
    assert result['state'] == 'cancelled'
    assert result['answer'] == '阶段成果'


def test_prepare_general_conversation_is_local_and_requires_no_documents(partner_library, monkeypatch):
    runtime = partner_library
    configure(monkeypatch)
    session = runtime.create_session('伙伴')
    prepared = runtime.prepare(session['id'], '一起讨论变电站培训计划', 'selected', [])
    assert prepared['scope'] == {'mode': 'selected', 'documents': [], 'count': 0}
    assert prepared['history'] == []
    assert prepared['service']['model'] == 'deepseek-flash'
    assert 'fake-secret' not in str(prepared)
    assert runtime.list_sessions()['items'][0]['id'] == session['id']


def test_start_requires_consent_before_any_model_call(partner_library, monkeypatch):
    runtime = partner_library
    configure(monkeypatch)
    session = runtime.create_session('伙伴')
    prepared = runtime.prepare(session['id'], '你好', 'selected', [])
    async def attempt():
        with pytest.raises(HTTPException) as error:
            await runtime.start(session['id'], prepared['preparation_id'], False, 'request-1')
        assert error.value.status_code == 400
    asyncio.run(attempt())


def test_task_is_idempotent_and_retains_dialogue_after_completion(partner_library, monkeypatch):
    runtime = partner_library
    configure(monkeypatch)
    calls = []
    async def fake_agent(**values):
        calls.append(values['message'])
        assert database.active_session_count() == 0
        return {'answer': '可以从培训对象和目标开始。', 'sources': [], 'artifacts': []}
    monkeypatch.setattr(runtime, 'run_agent', fake_agent)
    session = runtime.create_session('培训计划')
    prepared = runtime.prepare(session['id'], '一起拟培训计划', 'selected', [])
    async def execute():
        first = await runtime.start(session['id'], prepared['preparation_id'], True, 'stable-request')
        repeated = await runtime.start(session['id'], prepared['preparation_id'], True, 'stable-request')
        assert repeated['id'] == first['id']
        for _ in range(30):
            await asyncio.sleep(.01)
            task = runtime.get_task(first['id'])
            if task['state'] == 'completed':
                return task
        pytest.fail('临时假模型任务未完成')
    result = asyncio.run(execute())
    assert result['answer'] == '可以从培训对象和目标开始。'
    assert calls == ['一起拟培训计划']
    detail = runtime.get_session(session['id'])
    assert [m['role'] for m in detail['messages']] == ['user', 'assistant']
    prepared2 = runtime.prepare(session['id'], '重点是新员工', 'selected', [])
    assert [m['content'] for m in prepared2['history']] == ['一起拟培训计划', '可以从培训对象和目标开始。']


def test_cancelled_task_never_publishes_late_answer(partner_library, monkeypatch):
    runtime = partner_library
    configure(monkeypatch)
    async def fake_agent(**values):
        await asyncio.sleep(10)
        return {'answer': '不应显示', 'sources': [], 'artifacts': []}
    monkeypatch.setattr(runtime, 'run_agent', fake_agent)
    session = runtime.create_session()
    prepared = runtime.prepare(session['id'], '检索资料', 'selected', [])
    async def execute():
        task = await runtime.start(session['id'], prepared['preparation_id'], True, 'cancel-request')
        await asyncio.sleep(.01)
        cancelled = await runtime.cancel(task['id'])
        await asyncio.sleep(.01)
        return cancelled, runtime.get_task(task['id'])
    cancelled, task = asyncio.run(execute())
    assert cancelled['state'] == task['state'] == 'cancelled'
    assert task['answer'] is None


def test_configuration_change_during_task_discards_result(partner_library, monkeypatch):
    runtime = partner_library
    configure(monkeypatch)
    from backend.app.core import ai_settings
    async def fake_agent(**values):
        monkeypatch.setattr(ai_settings, 'get_connection', lambda: (
            {'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash'}, 'changed', 'revision-2'))
        return {'answer': '过期回答', 'sources': [], 'artifacts': []}
    monkeypatch.setattr(runtime, 'run_agent', fake_agent)
    session = runtime.create_session()
    prepared = runtime.prepare(session['id'], '你好', 'selected', [])
    async def execute():
        task = await runtime.start(session['id'], prepared['preparation_id'], True, 'changed-request')
        for _ in range(30):
            await asyncio.sleep(.01)
            task = runtime.get_task(task['id'])
            if task['state'] not in {'queued', 'running'}:
                return task
        pytest.fail('未结束')
    task = asyncio.run(execute())
    assert task['state'] == 'failed'
    assert task['answer'] is None
    assert 'revision-2' not in str(task)


def test_startup_marks_inflight_tasks_interrupted(partner_library, monkeypatch):
    runtime = partner_library
    configure(monkeypatch)
    from backend.app.partner_models import PartnerTask
    session = runtime.create_session()
    with database.SessionLocal() as db:
        db.add(PartnerTask(session_id=session['id'], request_id='orphan',
                           preparation_id='old', state='running', message='旧请求', input_json='{}'))
        db.commit()
    runtime.initialize()
    task = runtime.get_session(session['id'])['tasks'][0]
    assert task['state'] == 'interrupted'
    assert '重新' in task['error']['message']


def test_cancel_before_first_schedule_releases_shared_paid_lease(partner_library, monkeypatch):
    runtime = partner_library
    configure(monkeypatch)
    async def fake_agent(**values):
        await asyncio.sleep(1)
        return {'answer': '晚到结果', 'sources': [], 'artifacts': []}
    monkeypatch.setattr(runtime, 'run_agent', fake_agent)
    session = runtime.create_session()
    prepared = runtime.prepare(session['id'], '请分析', 'selected', [])
    async def execute():
        task = await runtime.start(session['id'], prepared['preparation_id'], True, 'immediate-cancel')
        await runtime.cancel(task['id'])
        await asyncio.sleep(.01)
        acquired = runtime._paid_call.acquire(blocking=False)
        if acquired:
            runtime._paid_call.release()
        assert acquired, '尚未首次调度的取消任务也必须释放共享付费调用锁'
        assert not runtime._running
    asyncio.run(execute())


def test_paused_task_can_continue_before_deferred_cleanup_callback(partner_library, monkeypatch):
    from backend.app.core.partner_budget import PartnerPaused
    runtime = partner_library
    configure(monkeypatch)

    async def fake_agent(**values):
        if not values.get('checkpoint'):
            raise PartnerPaused('model_requests', {'answer': '已保留阶段成果。', 'sources': [],
                'artifacts': [], 'checkpoint': {'evidence': ['已读正文']},
                'usage': {'model_requests': 4, 'tool_calls': 1, 'output_tokens': 20, 'time_seconds': 1}})
        return {'answer': '已继续完成。', 'sources': [], 'artifacts': []}

    monkeypatch.setattr(runtime, 'run_agent', fake_agent)
    session = runtime.create_session()
    prepared = runtime.prepare(session['id'], '请分析', 'selected', [])

    async def execute():
        task = await runtime.start(session['id'], prepared['preparation_id'], True, 'deferred-cleanup')
        runner = runtime._running[task['id']]['task']
        # Hold the registered completion callback to reproduce the scheduling
        # window deterministically; awaiting the coroutine must suffice to resume.
        callbacks = [callback for callback, _ in runner._callbacks]
        assert len(callbacks) == 1
        for callback in callbacks:
            runner.remove_done_callback(callback)
        try:
            await runner
            assert runtime.get_task(task['id'])['state'] == 'paused'
            preview = runtime.prepare_continue(task['id'])
            await runtime.continue_task(task['id'], preview['preparation_id'], True, 'resume-immediately')
            continuation = runtime._running[task['id']]['task']
            # A late callback from the prior segment must not release the new
            # segment's reservation or its shared paid-call lease.
            for callback in callbacks:
                callback(runner)
            acquired = runtime._paid_call.acquire(blocking=False)
            if acquired:
                runtime._paid_call.release()
            assert not acquired
            assert runtime._running[task['id']]['task'] is continuation
            await continuation
            assert runtime.get_task(task['id'])['state'] == 'completed'
        finally:
            runtime._release_task(task['id'], runner)

    asyncio.run(execute())


def test_draft_save_checks_content_hash_and_is_idempotent(partner_library, monkeypatch):
    runtime = partner_library
    configure(monkeypatch)
    import hashlib
    import json
    from backend.app.partner_models import PartnerTask
    session = runtime.create_session()
    content = '# 培训计划\n开展资料学习。'
    artifact = {'id': 'draft-1', 'name': '培训计划.md', 'content': content,
                'content_hash': hashlib.sha256(content.encode()).hexdigest(),
                'media_type': 'text/markdown', 'source_ids': [], 'saved_document_id': None}
    with database.SessionLocal() as db:
        task = PartnerTask(session_id=session['id'], request_id='draft-task', preparation_id='draft-prep',
            state='completed', message='生成计划', input_json='{}',
            result_json=json.dumps({'answer': '已生成草稿', 'artifacts': [artifact]}))
        db.add(task)
        db.commit()
        task_id = task.id
    first = runtime.save_artifact(task_id, 'draft-1', True, 'save-1')
    repeated = runtime.save_artifact(task_id, 'draft-1', True, 'save-1')
    assert first == repeated
    from backend.app.models import Document, Version
    with database.SessionLocal() as db:
        assert db.query(Document).count() == 1
        assert db.query(Version).count() == 1
        doc = db.get(Document, first['document_id'])
        assert doc.status == 'pending'
        assert (Path(settings.library_path) / doc.storage_path).read_text() == content


def test_draft_tampering_is_rejected_before_ingest(partner_library):
    import json
    from backend.app.partner_models import PartnerTask
    runtime = partner_library
    session = runtime.create_session()
    with database.SessionLocal() as db:
        task = PartnerTask(session_id=session['id'], request_id='tampered', preparation_id='old',
            state='completed', message='生成', input_json='{}', result_json=json.dumps({'artifacts': [{
                'id': 'bad', 'name': 'bad.md', 'content': '改过的内容', 'content_hash': '0' * 64}]}))
        db.add(task)
        db.commit()
        task_id = task.id
    with pytest.raises(HTTPException) as error:
        runtime.save_artifact(task_id, 'bad', True, 'save-bad')
    assert error.value.status_code == 409


@pytest.mark.parametrize('state', ['completed', 'paused', 'failed', 'cancelled', 'interrupted'])
def test_draft_dependency_check_and_ingest_are_one_write_transaction(partner_library, tmp_path, monkeypatch, state):
    import hashlib
    import json
    import threading
    from backend.app.models import Document
    from backend.app.partner_models import PartnerTask
    from backend.app.api import documents as documents_api
    runtime = partner_library
    doc = ingest_test_file(tmp_path)
    session = runtime.create_session()
    preview = runtime.prepare(session['id'], '生成成果', 'selected', [doc['id']])
    scope = runtime._preparations[preview['preparation_id']]['scope_records']
    content = '# 独立草稿'
    with database.SessionLocal() as db:
        task = PartnerTask(session_id=session['id'], request_id='atomic-' + state, preparation_id='atomic-prep',
            state=state, message='生成成果', input_json=json.dumps({'scope': scope}),
            result_json=json.dumps({'dependency_records': scope, 'artifacts': [{
                'id': 'atomic-artifact', 'name': '独立草稿.md', 'content': content,
                'content_hash': hashlib.sha256(content.encode()).hexdigest(), 'source_ids': []}]}))
        db.add(task); db.commit(); task_id = task.id
    edited, started, errors = threading.Event(), threading.Event(), []
    def edit_source():
        try:
            with database.SessionLocal() as other:
                source = other.get(Document, doc['id'])
                source.name = '并发改名.txt'
                started.set()
                other.commit()
                edited.set()
        except Exception as exc:
            errors.append(exc)
    worker = threading.Thread(target=edit_source)
    validate = runtime._validate_scope
    def validate_and_attempt_concurrent_edit(db, records, document_ids=None):
        validate(db, records, document_ids)
        worker.start()
        assert started.wait(2)
        edited.wait(.1)
    monkeypatch.setattr(runtime, '_validate_scope', validate_and_attempt_concurrent_edit)
    ingest = documents_api._ingest_document
    def ensure_edit_has_not_committed(*args, **kwargs):
        assert not edited.is_set(), '来源校验至草稿收录提交之前，不允许另一个事务先修改来源'
        return ingest(*args, **kwargs)
    monkeypatch.setattr(documents_api, '_ingest_document', ensure_edit_has_not_committed)
    try:
        saved = runtime.save_artifact(task_id, 'atomic-artifact', True, 'atomic-save')
    finally:
        worker.join(3)
    assert not errors
    assert edited.is_set()
    with database.SessionLocal() as db:
        assert db.get(Document, saved['document_id']).status == 'pending'


def ingest_test_file(tmp_path, content='检修规程：应检查记录。', name='检修规程.txt'):
    from backend.app.api.documents import _ingest_document
    from backend.app.api.schemas import DocumentCreate
    path = tmp_path / name
    path.write_text(content, encoding='utf-8')
    with database.SessionLocal() as db:
        return _ingest_document(DocumentCreate(name=name, source_path=str(path), tags=['检修']), db, pending=True)


def test_scope_freezes_version_and_rejects_changed_metadata(partner_library, tmp_path, monkeypatch):
    runtime = partner_library
    configure(monkeypatch)
    doc = ingest_test_file(tmp_path)
    session = runtime.create_session()
    prepared = runtime.prepare(session['id'], '分析这份资料', 'selected', [doc['id']])
    from backend.app.models import Document
    with database.SessionLocal() as db:
        db.get(Document, doc['id']).name = '名称已变化.txt'
        db.commit()
    async def execute():
        with pytest.raises(HTTPException) as error:
            await runtime.start(session['id'], prepared['preparation_id'], True, 'scope-stale')
        assert error.value.status_code == 409
    asyncio.run(execute())


def test_library_scope_does_not_expand_when_file_is_added(partner_library, tmp_path, monkeypatch):
    runtime = partner_library
    configure(monkeypatch)
    first = ingest_test_file(tmp_path)
    session = runtime.create_session()
    prepared = runtime.prepare(session['id'], '找检修资料', 'library', [])
    ingest_test_file(tmp_path, '新的资料', '新资料.txt')
    scope = runtime._preparations[prepared['preparation_id']]['scope_records']
    assert set(scope) == {first['id']}
    assert prepared['scope']['count'] == 1


def sdk_tool_response(name, arguments, number=1):
    import json
    return {'id': f'chatcmpl-{number}', 'object': 'chat.completion', 'created': 1,
            'model': 'deepseek-flash', 'choices': [{'index': 0, 'finish_reason': 'tool_calls',
                'message': {'role': 'assistant', 'content': None, 'tool_calls': [{
                    'id': f'call-{number}', 'type': 'function', 'function': {
                        'name': name, 'arguments': json.dumps(arguments, ensure_ascii=False)}}]}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20}}


def test_real_sdk_normal_reading_can_exceed_old_eight_requests(partner_library, tmp_path, monkeypatch):
    import httpx2
    from backend.tests.support.legacy_partner_agent import run_agent
    runtime = partner_library
    configure(monkeypatch)
    documents = [ingest_test_file(tmp_path, f'第{number}份规程的真实正文。', f'规程{number}.txt') for number in range(10)]
    session = runtime.create_session()
    prepared = runtime.prepare(session['id'], '比较十份规程', 'selected', [doc['id'] for doc in documents])
    entry, calls = runtime._preparations[prepared['preparation_id']], []
    def handler(request):
        calls.append(request)
        number = len(calls)
        if number <= 10:
            return httpx2.Response(200, json=sdk_tool_response('read_document', {'document_id': documents[number - 1]['id']}, number))
        return httpx2.Response(200, json=sdk_tool_response('final_result', {'answer': '已阅读十份规程。[S10]', 'source_ids': ['S10']}, number))
    result = asyncio.run(run_agent(message=entry['message'], history=[], scope=entry['scope_records'],
        config=entry['service'], key='fake-test-secret', engine=database.engine, root=str(Path(settings.library_path).resolve()),
        guard=lambda: None, emit=lambda *args: None, limits=entry['limits'], transport_override=httpx2.MockTransport(handler)))
    assert len(calls) == result['usage']['model_requests'] == 11
    assert len(result['sources']) == 10


def test_real_sdk_pause_resume_reuses_full_text_evidence_and_stable_sources(partner_library, tmp_path, monkeypatch):
    import json
    import httpx2
    from functools import partial
    from backend.tests.support.legacy_partner_agent import run_agent
    runtime = partner_library
    configure(monkeypatch)
    text = '真实正文' * 120 + '这是超过300字摘录后的尾部证据'
    doc = ingest_test_file(tmp_path, text)
    session = runtime.create_session()
    prepared = runtime.prepare(session['id'], '完整阅读并汇总', 'selected', [doc['id']], {
        'mode': 'custom', 'model_requests': 4, 'tool_calls': 8, 'output_tokens': 4000, 'time_seconds': 60})
    calls = []
    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        prompts = [message['content'] for message in payload['messages'] if message['role'] == 'user']
        initial = json.loads(prompts[-1])
        if initial.get('purpose') == 'progress_summary':
            assert '尾部证据' in str(initial['continuation_context']['evidence'])
            return httpx2.Response(200, json=sdk_tool_response('final_result', {'answer': '已读正文，仍需最终归纳。[S1]', 'source_ids': ['S1']}, len(calls)))
        if initial.get('continuation_context'):
            assert '尾部证据' in str(initial['continuation_context']['evidence'])
            assert initial['continuation_context']['summary'] == '已读正文，仍需最终归纳。[S1]'
            return httpx2.Response(200, json=sdk_tool_response('final_result', {'answer': '根据已保留全文完成归纳。[S1]', 'source_ids': ['S1']}, len(calls)))
        if len(calls) == 1:
            name, arguments = 'read_document', {'document_id': doc['id']}
        elif len(calls) == 2:
            name, arguments = 'create_draft', {'name': '阶段.md', 'content': '阶段结论。[S1]', 'source_ids': ['S1']}
        else:
            name, arguments = 'document_overview', {'document_id': doc['id']}
        return httpx2.Response(200, json=sdk_tool_response(name, arguments, len(calls)))
    monkeypatch.setattr(runtime, 'run_agent', partial(run_agent, transport_override=httpx2.MockTransport(handler)))
    async def execute():
        task = await runtime.start(session['id'], prepared['preparation_id'], True, 'sdk-pause')
        for _ in range(100):
            await asyncio.sleep(.01)
            task = runtime.get_task(task['id'])
            if task['state'] not in {'queued', 'running'}:
                break
        assert task['state'] == 'paused', task
        assert task['pause']['reason'] == 'model_requests'
        assert task['usage']['model_requests'] == 4
        assert task['artifacts'][0]['name'] == '阶段.md'
        assert 'checkpoint' not in task
        assert not any(m['role'] == 'assistant' for m in runtime.get_session(session['id'])['messages'])
        runtime.initialize()  # 刷新/重启不消耗预算，也不抹掉paused。
        continuation = runtime.prepare_continue(task['id'])
        assert '尾部证据' in str(continuation['continuation_context']['evidence'])
        await runtime.continue_task(task['id'], continuation['preparation_id'], True, 'sdk-continue')
        await runtime.continue_task(task['id'], continuation['preparation_id'], True, 'sdk-continue')
        for _ in range(100):
            await asyncio.sleep(.01)
            task = runtime.get_task(task['id'])
            if task['state'] not in {'queued', 'running'}:
                return task
    result = asyncio.run(execute())
    assert result['state'] == 'completed', result
    assert result['usage']['model_requests'] == len(calls) == 5
    assert result['sources'][0]['source_id'] == 'S1'
    assert len(runtime.get_session(session['id'])['messages']) == 2


@pytest.mark.parametrize('kind', ['tool_calls', 'output_tokens', 'no_progress'])
def test_real_sdk_pause_distinguishes_budget_and_no_progress(partner_library, tmp_path, monkeypatch, kind):
    import json
    import httpx2
    from backend.tests.support.legacy_partner_agent import run_agent
    from backend.app.core.partner_budget import PartnerPaused
    runtime = partner_library
    configure(monkeypatch)
    documents = [ingest_test_file(tmp_path, f'资料{index}的正文', f'资料{index}.txt') for index in range(10)]
    session = runtime.create_session()
    prepared = runtime.prepare(session['id'], '汇总', 'selected', [item['id'] for item in documents], {
        'mode': 'custom', 'model_requests': 32, 'tool_calls': 8, 'output_tokens': 4000, 'time_seconds': 60})
    entry, calls = runtime._preparations[prepared['preparation_id']], []
    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        last_user = next(item for item in reversed(payload['messages']) if item['role'] == 'user')
        if json.loads(last_user['content']).get('purpose') == 'progress_summary':
            return httpx2.Response(200, json=sdk_tool_response('final_result', {'answer': '阶段进度已保留，尚未完成。[S1]', 'source_ids': ['S1']}, len(calls)))
        index = 0 if kind == 'no_progress' else min(len(calls) - 1, 9)
        response = sdk_tool_response('read_document', {'document_id': documents[index]['id']}, len(calls))
        if kind == 'output_tokens' and len(calls) > 1:
            response['usage'] = {'prompt_tokens': 10, 'completion_tokens': 3500, 'total_tokens': 3510}
        return httpx2.Response(200, json=response)
    with pytest.raises(PartnerPaused) as caught:
        asyncio.run(run_agent(message=entry['message'], history=[], scope=entry['scope_records'],
            config=entry['service'], key='fake-test-secret', engine=database.engine, root=str(Path(settings.library_path).resolve()),
            guard=lambda: None, emit=lambda *args: None, limits=entry['limits'], transport_override=httpx2.MockTransport(handler)))
    assert caught.value.reason == kind
    assert caught.value.output['sources']
    assert caught.value.output['usage']['input_tokens'] >= 10
    assert len(calls) <= 11


def test_continue_rejects_changed_source_and_config_and_requires_fresh_consent(partner_library, tmp_path, monkeypatch):
    import json
    from backend.app.models import Document
    from backend.app.partner_models import PartnerTask
    runtime = partner_library
    configure(monkeypatch)
    doc = ingest_test_file(tmp_path)
    session = runtime.create_session()
    prepared = runtime.prepare(session['id'], '旧问题', 'selected', [doc['id']])
    entry = runtime._preparations[prepared['preparation_id']]
    with database.SessionLocal() as db:
        task = PartnerTask(session_id=session['id'], request_id='paused-guard', preparation_id='original',
            state='paused', message='旧问题', input_json=json.dumps({'scope': entry['scope_records'],
            'scope_mode': 'selected', 'history': [], 'history_dependencies': {}}),
            result_json=json.dumps({'pause': {'resumable': True}, 'checkpoint': {'evidence': []}}))
        db.add(task); db.commit(); task_id = task.id
    preview = runtime.prepare_continue(task_id)
    async def attempt():
        with pytest.raises(HTTPException) as missing:
            await runtime.continue_task(task_id, preview['preparation_id'], False, 'no-consent')
        assert missing.value.detail['error']['code'] == 'AI_CONSENT_REQUIRED'
        from backend.app.core import ai_settings
        monkeypatch.setattr(ai_settings, 'get_connection', lambda: (entry['service'], 'new-key', 'new-revision'))
        with pytest.raises(HTTPException) as changed:
            await runtime.continue_task(task_id, preview['preparation_id'], True, 'config-change')
        assert changed.value.detail['error']['code'] == 'AI_CONFIGURATION_CHANGED'
    asyncio.run(attempt())
    configure(monkeypatch)
    with database.SessionLocal() as db:
        task = db.get(PartnerTask, task_id)
        result = json.loads(task.result_json)
        result['answer'] = '预览之后发生变化的阶段结论'
        task.result_json = json.dumps(result)
        db.commit()
    async def changed_summary():
        with pytest.raises(HTTPException) as changed:
            await runtime.continue_task(task_id, preview['preparation_id'], True, 'summary-change')
        assert changed.value.detail['error']['code'] == 'PARTNER_CHECKPOINT_CHANGED'
    asyncio.run(changed_summary())
    with database.SessionLocal() as db:
        db.get(Document, doc['id']).name = '改名.txt'
        db.commit()
    with pytest.raises(HTTPException) as changed:
        runtime.prepare_continue(task_id)
    assert changed.value.detail['error']['code'] == 'PARTNER_DOCUMENT_CHANGED'


def test_real_sdk_elapsed_budget_pauses_and_preserves_last_completed_tool(partner_library, tmp_path, monkeypatch):
    import json
    import httpx2
    from backend.tests.support.legacy_partner_agent import run_agent
    from backend.app.core.partner_budget import PartnerPaused
    runtime = partner_library
    configure(monkeypatch)
    doc = ingest_test_file(tmp_path)
    session = runtime.create_session()
    preview = runtime.prepare(session['id'], '继续分析', 'selected', [doc['id']])
    entry, calls = runtime._preparations[preview['preparation_id']], []
    async def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        last_user = next(item for item in reversed(payload['messages']) if item['role'] == 'user')
        if json.loads(last_user['content']).get('purpose') == 'progress_summary':
            return httpx2.Response(200, json=sdk_tool_response('final_result', {
                'answer': '已保留阅读内容，后续分析尚未完成。[S1]', 'source_ids': ['S1']}, len(calls)))
        if len(calls) == 1:
            return httpx2.Response(200, json=sdk_tool_response('read_document', {'document_id': doc['id']}))
        await asyncio.sleep(2)
        return httpx2.Response(200, json=sdk_tool_response('final_result', {'answer': '迟到内容不应发布', 'source_ids': []}, 2))
    with pytest.raises(PartnerPaused) as paused:
        asyncio.run(run_agent(message=entry['message'], history=[], scope=entry['scope_records'],
            config=entry['service'], key='fake-test-secret', engine=database.engine, root=str(Path(settings.library_path).resolve()),
            guard=lambda: None, emit=lambda *args: None,
            limits={**entry['limits'], 'time_seconds': 5.01}, transport_override=httpx2.MockTransport(handler)))
    assert paused.value.reason == 'time_seconds'
    assert paused.value.output['sources'][0]['source_id'] == 'S1'
    assert '迟到' not in paused.value.output['answer']
    assert paused.value.output['checkpoint']['evidence']
    assert paused.value.output['usage']['model_requests'] == len(calls) == 3
    assert paused.value.output['usage']['token_usage_complete'] is False
    assert paused.value.output['usage']['unmetered_requests'] == 1


def test_timed_out_sent_attempts_are_cumulative_after_real_sdk_continue(partner_library, tmp_path, monkeypatch):
    import json
    import httpx2
    from backend.tests.support.legacy_partner_agent import run_agent
    runtime = partner_library
    configure(monkeypatch)
    doc = ingest_test_file(tmp_path)
    session = runtime.create_session()
    prepared = runtime.prepare(session['id'], '分析并继续', 'selected', [doc['id']])
    calls = []
    async def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        prompt = json.loads(next(item['content'] for item in reversed(payload['messages']) if item['role'] == 'user'))
        if prompt.get('purpose') == 'progress_summary':
            return httpx2.Response(200, json=sdk_tool_response('final_result', {'answer': '已阅读，分析未完成。[S1]', 'source_ids': ['S1']}, len(calls)))
        if prompt.get('continuation_context'):
            assert prompt['continuation_context']['summary'] == '已阅读，分析未完成。[S1]'
            return httpx2.Response(200, json=sdk_tool_response('final_result', {'answer': '继续已完成。[S1]', 'source_ids': ['S1']}, len(calls)))
        if len(calls) == 1:
            return httpx2.Response(200, json=sdk_tool_response('read_document', {'document_id': doc['id']}))
        await asyncio.sleep(2)
        return httpx2.Response(200, json=sdk_tool_response('final_result', {'answer': '迟到回答', 'source_ids': []}, 2))
    async def timed_agent(**values):
        if not values.get('checkpoint'):
            values['limits'] = {**values['limits'], 'time_seconds': 5.01}
        return await run_agent(**values, transport_override=httpx2.MockTransport(handler))
    monkeypatch.setattr(runtime, 'run_agent', timed_agent)
    async def execute():
        task = await runtime.start(session['id'], prepared['preparation_id'], True, 'timeout-start')
        for _ in range(200):
            await asyncio.sleep(.01)
            task = runtime.get_task(task['id'])
            if task['state'] not in {'queued', 'running'}:
                break
        assert task['state'] == 'paused'
        assert task['usage']['model_requests'] == len(calls) == 3
        assert task['usage']['token_usage_complete'] is False
        preview = runtime.prepare_continue(task['id'])
        await runtime.continue_task(task['id'], preview['preparation_id'], True, 'timeout-continue')
        for _ in range(100):
            await asyncio.sleep(.01)
            task = runtime.get_task(task['id'])
            if task['state'] not in {'queued', 'running'}:
                return task
    task = asyncio.run(execute())
    assert task['state'] == 'completed'
    assert task['usage']['model_requests'] == len(calls) == 4
    assert task['usage']['unmetered_requests'] == 1
    assert task['usage']['token_usage_complete'] is False
    assert task['segment_usage']['model_requests'] == 1


def test_each_model_request_caps_output_to_remaining_authorized_budget(partner_library, tmp_path, monkeypatch):
    import json
    import httpx2
    from backend.tests.support.legacy_partner_agent import run_agent
    runtime = partner_library
    configure(monkeypatch)
    doc = ingest_test_file(tmp_path)
    session = runtime.create_session()
    preview = runtime.prepare(session['id'], '分析', 'selected', [doc['id']], {
        'mode': 'custom', 'model_requests': 4, 'tool_calls': 8, 'output_tokens': 4000, 'time_seconds': 60})
    entry, calls = runtime._preparations[preview['preparation_id']], []
    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if len(calls) == 1:
            response = sdk_tool_response('read_document', {'document_id': doc['id']})
            response['usage'] = {'prompt_tokens': 10, 'completion_tokens': 2600, 'total_tokens': 2610}
            return httpx2.Response(200, json=response)
        assert payload['max_tokens'] == 400
        return httpx2.Response(200, json=sdk_tool_response('final_result', {'answer': '已完成。[S1]', 'source_ids': ['S1']}, 2))
    result = asyncio.run(run_agent(message=entry['message'], history=[], scope=entry['scope_records'],
        config=entry['service'], key='fake-test-secret', engine=database.engine, root=str(Path(settings.library_path).resolve()),
        guard=lambda: None, emit=lambda *args: None, limits=entry['limits'], transport_override=httpx2.MockTransport(handler)))
    assert result['usage']['output_tokens'] == 2610


def test_real_sdk_autonomously_searches_reads_and_creates_draft(partner_library, tmp_path, monkeypatch):
    """使用真实 PydanticAI/OpenAI SDK 和模拟HTTP，不替换智能体执行循环。"""
    import json
    import httpx2
    from backend.tests.support.legacy_partner_agent import run_agent
    runtime = partner_library
    configure(monkeypatch)
    doc = ingest_test_file(tmp_path)
    session = runtime.create_session()
    prepared = runtime.prepare(session['id'], '找检修规程并生成学习计划', 'library', [])
    entry = runtime._preparations[prepared['preparation_id']]
    requests = []
    def handler(request):
        assert database.active_session_count() == 0
        assert request.headers['authorization'] == 'Bearer fake-test-secret'
        assert 'fake-test-secret' not in request.content.decode()
        payload = json.loads(request.content)
        requests.append(payload)
        number = len(requests)
        if number == 1:
            assert {tool['function']['name'] for tool in payload['tools']} >= {
                'search_documents', 'read_document', 'read_visual', 'create_draft', 'final_result'}
            result = sdk_tool_response('search_documents', {'query': '检修'})
        elif number == 2:
            assert any('检修规程' in str(message.get('content')) for message in payload['messages'] if message['role'] == 'tool')
            result = sdk_tool_response('read_document', {'document_id': doc['id']}, number)
        elif number == 3:
            assert any('S1' in str(message.get('content')) for message in payload['messages'] if message['role'] == 'tool')
            result = sdk_tool_response('create_draft', {'name': '学习计划.md',
                'content': '# 学习计划\n先阅读规程并检查记录。[S1]', 'source_ids': ['S1']}, number)
        else:
            result = sdk_tool_response('final_result', {'answer': '已根据规程生成学习计划。[S1]', 'source_ids': ['S1']}, number)
        return httpx2.Response(200, json=result)
    result = asyncio.run(run_agent(message=prepared['message'], history=[], scope=entry['scope_records'],
        config=prepared['service'], key='fake-test-secret', engine=database.engine, root=str(Path(settings.library_path).resolve()),
        guard=lambda: None, emit=lambda *args: None, limits=runtime.LIMITS,
        transport_override=httpx2.MockTransport(handler)))
    assert len(requests) == 4
    assert result['sources'][0]['document_id'] == doc['id']
    assert result['sources'][0]['version_id'] == prepared['scope']['documents'][0]['version_id']
    assert result['artifacts'][0]['name'] == '学习计划.md'
    assert '资料来源' in result['artifacts'][0]['content']
    assert result['sources'][0]['version_id'] in result['artifacts'][0]['content']
    assert result['answer'] == '已根据规程生成学习计划。[S1]'


def test_real_sdk_sends_original_image_for_visual_tool(partner_library, tmp_path, monkeypatch):
    import json
    import httpx2
    from PIL import Image
    from backend.app.api.documents import _ingest_document
    from backend.app.api.schemas import DocumentCreate
    from backend.tests.support.legacy_partner_agent import run_agent
    runtime = partner_library
    configure(monkeypatch)
    path = tmp_path / '设备照片.png'
    Image.new('RGB', (32, 20), 'red').save(path)
    with database.SessionLocal() as db:
        doc = _ingest_document(DocumentCreate(name=path.name, source_path=str(path), tags=['设备']), db, pending=True)
    session = runtime.create_session()
    prepared = runtime.prepare(session['id'], '观察设备照片', 'selected', [doc['id']])
    scope = runtime._preparations[prepared['preparation_id']]['scope_records']
    calls = []
    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if len(calls) == 1:
            return httpx2.Response(200, json=sdk_tool_response('read_visual', {'document_id': doc['id'], 'pages': [1]}))
        image_parts = [part for message in payload['messages'] if isinstance(message.get('content'), list)
                       for part in message['content'] if part.get('type') == 'image_url']
        assert image_parts and image_parts[0]['image_url']['url'].startswith('data:image/png;base64,')
        return httpx2.Response(200, json=sdk_tool_response('final_result', {'answer': '图片为红色。[S1]', 'source_ids': ['S1']}, 2))
    result = asyncio.run(run_agent(message=prepared['message'], history=[], scope=scope,
        config=prepared['service'], key='fake-test-secret', engine=database.engine, root=str(Path(settings.library_path).resolve()),
        guard=lambda: None, emit=lambda *args: None, limits=runtime.LIMITS,
        transport_override=httpx2.MockTransport(handler)))
    assert len(calls) == 2
    assert result['sources'][0]['location'] == {'page': 1}


def test_real_sdk_rejects_fabricated_source_without_retry(partner_library, monkeypatch):
    import httpx2
    from backend.app.core.ai_client import AIError
    from backend.tests.support.legacy_partner_agent import run_agent
    calls = []
    def handler(request):
        calls.append(request)
        return httpx2.Response(200, json=sdk_tool_response('final_result', {'answer': '有依据。[S999]', 'source_ids': ['S999']}))
    with pytest.raises(AIError) as error:
        asyncio.run(run_agent(message='你好', history=[], scope={},
            config={'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash'}, key='fake-test-secret',
            engine=database.engine, root=str(Path(settings.library_path).resolve()),
            guard=lambda: None, emit=lambda *args: None, limits=partner_library.LIMITS,
            transport_override=httpx2.MockTransport(handler)))
    assert error.value.code == 'PARTNER_INVALID_SOURCES'
    assert len(calls) == 1


def test_real_sdk_upstream_error_is_sanitized_and_not_retried(partner_library):
    import httpx2
    from backend.app.core.ai_client import AIError
    from backend.tests.support.legacy_partner_agent import run_agent
    calls = []
    def handler(request):
        calls.append(request)
        return httpx2.Response(500, json={'error': {'message': 'upstream-private-body fake-test-secret'}})
    with pytest.raises(AIError) as error:
        asyncio.run(run_agent(message='你好', history=[], scope={},
            config={'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash'}, key='fake-test-secret',
            engine=database.engine, root=str(Path(settings.library_path).resolve()),
            guard=lambda: None, emit=lambda *args: None, limits=partner_library.LIMITS,
            transport_override=httpx2.MockTransport(handler)))
    assert len(calls) == 1
    assert 'private' not in str(error.value)
    assert 'fake-test-secret' not in str(error.value)


def test_cancellation_is_visible_to_background_tool_guard(partner_library, monkeypatch):
    import threading
    import time
    runtime = partner_library
    configure(monkeypatch)
    started, stopped = threading.Event(), threading.Event()
    def background(guard):
        started.set()
        for _ in range(100):
            try:
                guard()
            except HTTPException:
                stopped.set()
                return
            time.sleep(.01)
    async def fake_agent(**values):
        await asyncio.to_thread(background, values['guard'])
        return {'answer': '不应发布', 'sources': [], 'artifacts': []}
    monkeypatch.setattr(runtime, 'run_agent', fake_agent)
    session = runtime.create_session()
    prepared = runtime.prepare(session['id'], '原页分析', 'selected', [])
    async def execute():
        task = await runtime.start(session['id'], prepared['preparation_id'], True, 'cancel-worker')
        for _ in range(100):
            await asyncio.sleep(.005)
            if started.is_set():
                break
        await runtime.cancel(task['id'])
        for _ in range(100):
            await asyncio.sleep(.005)
            if stopped.is_set():
                break
        assert stopped.is_set(), '取消必须中止解析线程中的guard，不能仅取消等待方'
    asyncio.run(execute())


def test_changed_read_source_cannot_be_sent_in_next_model_request(partner_library, tmp_path, monkeypatch):
    import httpx2
    from backend.tests.support.legacy_partner_agent import run_agent
    from backend.app.services.partner_tools import PartnerTools
    from backend.app.models import Document
    runtime = partner_library
    configure(monkeypatch)
    doc = ingest_test_file(tmp_path)
    session = runtime.create_session()
    prepared = runtime.prepare(session['id'], '阅读资料', 'selected', [doc['id']])
    scope = runtime._preparations[prepared['preparation_id']]['scope_records']
    original = PartnerTools.read_document
    def changed_read(self, *args):
        output = original(self, *args)
        with database.SessionLocal() as db:
            db.get(Document, doc['id']).status = 'trashed'
            db.commit()
        return output
    monkeypatch.setattr(PartnerTools, 'read_document', changed_read)
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx2.Response(200, json=sdk_tool_response('read_document', {'document_id': doc['id']}))
        return httpx2.Response(200, json=sdk_tool_response('final_result', {'answer': '旧资料。[S1]', 'source_ids': ['S1']}, 2))
    with pytest.raises(Exception):
        asyncio.run(run_agent(message=prepared['message'], history=[], scope=scope,
            config=prepared['service'], key='fake-test-secret', engine=database.engine, root=str(Path(settings.library_path).resolve()),
            guard=lambda: None, emit=lambda *args: None, limits=runtime.LIMITS,
            transport_override=httpx2.MockTransport(handler)))
    assert len(calls) == 1, '已读取资料被删除后，不得在下一次模型请求重发旧内容'


def test_final_commit_rechecks_sources_when_changed_after_model_return(partner_library, tmp_path, monkeypatch):
    from backend.app.models import Document
    runtime = partner_library
    configure(monkeypatch)
    doc = ingest_test_file(tmp_path)
    session = runtime.create_session()
    prepared = runtime.prepare(session['id'], '分析', 'selected', [doc['id']])
    source = {'id': 'S1', 'source_id': 'S1', 'document_id': doc['id'],
              'version_id': prepared['scope']['documents'][0]['version_id']}
    async def fake_agent(**values):
        return {'answer': '旧资料结论。[S1]', 'sources': [source], 'artifacts': [], 'read_document_ids': [doc['id']]}
    monkeypatch.setattr(runtime, 'run_agent', fake_agent)
    original = runtime._finish
    def changed_finish(task_id, engine, state, **values):
        if state == 'completed':
            with database.SessionLocal() as db:
                db.get(Document, doc['id']).status = 'trashed'
                db.commit()
        return original(task_id, engine, state, **values)
    monkeypatch.setattr(runtime, '_finish', changed_finish)
    async def execute():
        task = await runtime.start(session['id'], prepared['preparation_id'], True, 'commit-changed')
        for _ in range(50):
            await asyncio.sleep(.005)
            task = runtime.get_task(task['id'])
            if task['state'] not in runtime.ACTIVE:
                return task
        pytest.fail('未完成')
    task = asyncio.run(execute())
    assert task['state'] == 'failed'
    assert task['answer'] is None


def test_real_sdk_timeout_is_distinct_and_does_not_retry(partner_library):
    import httpx2
    from backend.app.core.ai_client import AIError
    from backend.tests.support.legacy_partner_agent import run_agent
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx2.ReadTimeout('private timeout fake-test-secret', request=request)
    with pytest.raises(AIError) as error:
        asyncio.run(run_agent(message='你好', history=[], scope={},
            config={'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash'}, key='fake-test-secret',
            engine=database.engine, root=str(Path(settings.library_path).resolve()),
            guard=lambda: None, emit=lambda *args: None, limits=partner_library.LIMITS,
            transport_override=httpx2.MockTransport(handler)))
    assert len(calls) == 1
    assert error.value.code == 'AI_TIMEOUT'
    assert 'private' not in str(error.value)


def test_library_switch_during_model_call_never_writes_into_new_library(partner_library, tmp_path, monkeypatch):
    runtime = partner_library
    configure(monkeypatch)
    original_root = settings.library_path
    async def fake_agent(**values):
        assert database.active_session_count() == 0
        settings.library_path = str(tmp_path / 'other-library')
        database.rebind_engine()
        database.init_db()
        runtime.initialize()
        return {'answer': '旧库回答', 'sources': [], 'artifacts': []}
    monkeypatch.setattr(runtime, 'run_agent', fake_agent)
    session = runtime.create_session()
    prepared = runtime.prepare(session['id'], '你好', 'selected', [])
    async def execute():
        task = await runtime.start(session['id'], prepared['preparation_id'], True, 'switch-task')
        for _ in range(100):
            await asyncio.sleep(.005)
            if not runtime._running:
                break
        assert runtime.list_sessions()['items'] == []
        settings.library_path = original_root
        database.rebind_engine()
        database.init_db()
        runtime.initialize()
        return runtime.get_task(task['id'])
    task = asyncio.run(execute())
    assert task['state'] == 'interrupted'
    assert task['answer'] is None


def scope_with_hidden_document(runtime, tmp_path):
    """目标排在首次 prompt 的前30份预览之外，分别验证历史/搜索依赖。"""
    for number in range(30):
        ingest_test_file(tmp_path, f'普通资料 {number}', f'{number:02d}-普通资料.txt')
    doc = ingest_test_file(tmp_path, '历史事实：保护动作必须核对。', 'zz-历史专用资料.txt')
    session = runtime.create_session()
    return session, doc


async def wait_task(runtime, task):
    for _ in range(150):
        await asyncio.sleep(.005)
        task = runtime.get_task(task['id'])
        if task['state'] not in runtime.ACTIVE:
            return task
    pytest.fail('伙伴任务没有在临时测试中结束')


def test_history_only_dependency_deleted_after_first_sdk_response_is_not_resent(partner_library, tmp_path, monkeypatch):
    import json
    import httpx2
    from functools import partial
    from backend.tests.support.legacy_partner_agent import run_agent
    from backend.app.models import Document
    from backend.app.partner_models import PartnerMessage, PartnerTask
    runtime = partner_library
    configure(monkeypatch)
    session, doc = scope_with_hidden_document(runtime, tmp_path)
    first = runtime.prepare(session['id'], '最初问题', 'selected', [doc['id']])
    frozen = runtime._preparations[first['preparation_id']]['scope_records']
    with database.SessionLocal() as db:
        old = PartnerTask(session_id=session['id'], request_id='old-history', preparation_id='old-prep',
            state='completed', message='最初问题', input_json=json.dumps({'scope': frozen}))
        db.add(old)
        db.flush()
        db.add(PartnerMessage(session_id=session['id'], task_id=old.id, role='assistant',
            content='历史回答包含保护动作结论。[S1]', sources_json=json.dumps([{
                'id': 'S1', 'source_id': 'S1', 'document_id': doc['id'],
                'version_id': frozen[doc['id']]['version_id'], 'content_hash': frozen[doc['id']]['content_hash']}])) )
        db.commit()
    prepared = runtime.prepare(session['id'], '继续说明历史回答', 'library', [])
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            assert '历史回答包含保护动作结论' in str(calls[-1])
            with database.SessionLocal() as db:
                db.get(Document, doc['id']).status = 'trashed'
                db.commit()
            return httpx2.Response(200, json=sdk_tool_response('search_documents', {'query': '不会命中'}))
        return httpx2.Response(200, json=sdk_tool_response('final_result', {'answer': '继续沿用旧结论', 'source_ids': []}, 2))
    monkeypatch.setattr(runtime, 'run_agent', partial(run_agent, transport_override=httpx2.MockTransport(handler)))
    async def execute():
        task = await runtime.start(session['id'], prepared['preparation_id'], True, 'history-stale')
        return await wait_task(runtime, task)
    result = asyncio.run(execute())
    assert result['state'] == 'failed', '历史来源已删除的回答不得被接受'
    assert result['answer'] is None
    assert len(calls) == 1, '不能在第二次SDK请求继续发送失效历史'


def test_search_metadata_dependency_is_guarded_and_not_exposed_as_read_source(partner_library, tmp_path, monkeypatch):
    import json
    import httpx2
    from functools import partial
    from backend.tests.support.legacy_partner_agent import run_agent
    from backend.app.models import Document
    from backend.app.services.partner_tools import PartnerTools
    runtime = partner_library
    configure(monkeypatch)
    session, doc = scope_with_hidden_document(runtime, tmp_path)
    prepared = runtime.prepare(session['id'], '搜索历史专用资料', 'library', [])
    original = PartnerTools.search_documents
    def changed_search(self, *args):
        output = original(self, *args)
        assert any(item['document_id'] == doc['id'] for item in output['documents'])
        with database.SessionLocal() as db:
            db.get(Document, doc['id']).description = '搜索后元数据已改变'
            db.commit()
        return output
    monkeypatch.setattr(PartnerTools, 'search_documents', changed_search)
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx2.Response(200, json=sdk_tool_response('search_documents', {'query': '历史专用'}))
        return httpx2.Response(200, json=sdk_tool_response('final_result', {'answer': '引用旧搜索信息', 'source_ids': []}, 2))
    monkeypatch.setattr(runtime, 'run_agent', partial(run_agent, transport_override=httpx2.MockTransport(handler)))
    async def execute():
        task = await runtime.start(session['id'], prepared['preparation_id'], True, 'search-stale')
        return await wait_task(runtime, task)
    result = asyncio.run(execute())
    assert result['state'] == 'failed'
    assert result['sources'] == []
    assert len(calls) == 1


def test_initial_document_preview_is_checked_before_first_sdk_request(partner_library, tmp_path, monkeypatch):
    import httpx2
    from backend.tests.support.legacy_partner_agent import run_agent
    from backend.app.models import Document
    runtime = partner_library
    configure(monkeypatch)
    doc = ingest_test_file(tmp_path)
    session = runtime.create_session()
    prepared = runtime.prepare(session['id'], '只聊天也包含已授权名称预览', 'selected', [doc['id']])
    calls = []
    def handler(request):
        calls.append(request)
        return httpx2.Response(200, json=sdk_tool_response('final_result', {'answer': '过期名称', 'source_ids': []}))
    async def changed_agent(**values):
        with database.SessionLocal() as db:
            db.get(Document, doc['id']).name = '首请求前被更名.txt'
            db.commit()
        return await run_agent(**values, transport_override=httpx2.MockTransport(handler))
    monkeypatch.setattr(runtime, 'run_agent', changed_agent)
    async def execute():
        task = await runtime.start(session['id'], prepared['preparation_id'], True, 'preview-stale')
        return await wait_task(runtime, task)
    result = asyncio.run(execute())
    assert result['state'] == 'failed'
    assert len(calls) == 0


def test_search_only_answer_keeps_dependencies_for_next_turn_without_fake_citations(partner_library, tmp_path, monkeypatch):
    import json
    import httpx2
    from functools import partial
    from backend.tests.support.legacy_partner_agent import run_agent
    runtime = partner_library
    configure(monkeypatch)
    session, doc = scope_with_hidden_document(runtime, tmp_path)
    prepared = runtime.prepare(session['id'], '资料名称里是否有历史专用', 'library', [])
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx2.Response(200, json=sdk_tool_response('search_documents', {'query': '历史专用'}))
        return httpx2.Response(200, json=sdk_tool_response('final_result', {'answer': '搜索确认存在历史专用资料', 'source_ids': []}, 2))
    monkeypatch.setattr(runtime, 'run_agent', partial(run_agent, transport_override=httpx2.MockTransport(handler)))
    async def execute():
        task = await runtime.start(session['id'], prepared['preparation_id'], True, 'search-only-history')
        return await wait_task(runtime, task)
    result = asyncio.run(execute())
    assert result['state'] == 'completed'
    assert result['sources'] == [], '搜索候选不得冒充已读取原文引用'
    detail = runtime.get_session(session['id'])
    assert detail['messages'][-1]['sources'] == []
    # 后续仅授权普通资料，历史专用资料的事实回答不得再外发。
    ordinary = prepared['scope']['documents'][0]['id']
    next_turn = runtime.prepare(session['id'], '继续', 'selected', [ordinary])
    assert '搜索确认存在历史专用资料' not in str(next_turn['history'])


def test_key_revision_changed_between_prepare_and_start_requires_new_consent(partner_library, monkeypatch):
    from backend.app.core import ai_settings
    runtime = partner_library
    configure(monkeypatch)
    session = runtime.create_session()
    prepared = runtime.prepare(session['id'], '你好', 'selected', [])
    monkeypatch.setattr(ai_settings, 'get_connection', lambda: (
        {'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash'}, 'other-secret', 'revision-2'))
    async def execute():
        with pytest.raises(HTTPException) as error:
            await runtime.start(session['id'], prepared['preparation_id'], True, 'changed-before-start')
        assert error.value.status_code == 409
    asyncio.run(execute())


def test_real_sdk_recovers_from_visual_format_error_by_reading_text(partner_library, tmp_path, monkeypatch):
    import httpx2
    import json
    from functools import partial
    from backend.tests.support.legacy_partner_agent import run_agent
    runtime = partner_library
    configure(monkeypatch)
    doc = ingest_test_file(tmp_path)
    session = runtime.create_session()
    prepared = runtime.prepare(session['id'], '阅读这份资料', 'selected', [doc['id']])
    calls = []
    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if len(calls) == 1:
            return httpx2.Response(200, json=sdk_tool_response('read_visual', {'document_id': doc['id'], 'pages': [1]}))
        if len(calls) == 2:
            assert any('PARTNER_TOOL_UNAVAILABLE' in str(item.get('content'))
                       for item in payload['messages'] if item['role'] == 'tool')
            return httpx2.Response(200, json=sdk_tool_response('read_document', {'document_id': doc['id']}, 2))
        return httpx2.Response(200, json=sdk_tool_response('final_result', {'answer': '改用文字读取后完成。[S1]', 'source_ids': ['S1']}, 3))
    monkeypatch.setattr(runtime, 'run_agent', partial(run_agent, transport_override=httpx2.MockTransport(handler)))
    async def execute():
        task = await runtime.start(session['id'], prepared['preparation_id'], True, 'recover-format')
        return await wait_task(runtime, task)
    result = asyncio.run(execute())
    assert result['state'] == 'completed'
    assert len(calls) == 3
    assert result['sources'][0]['document_id'] == doc['id']


def test_unreferenced_search_dependency_is_rechecked_in_final_transaction(partner_library, tmp_path, monkeypatch):
    import httpx2
    from functools import partial
    from backend.tests.support.legacy_partner_agent import run_agent
    from backend.app.models import Document
    runtime = partner_library
    configure(monkeypatch)
    session, doc = scope_with_hidden_document(runtime, tmp_path)
    prepared = runtime.prepare(session['id'], '检索资料名称', 'library', [])
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx2.Response(200, json=sdk_tool_response('search_documents', {'query': '历史专用'}))
        return httpx2.Response(200, json=sdk_tool_response('final_result', {'answer': '存在该资料', 'source_ids': []}, 2))
    monkeypatch.setattr(runtime, 'run_agent', partial(run_agent, transport_override=httpx2.MockTransport(handler)))
    original = runtime._finish
    def changed_finish(task_id, engine, state, **values):
        if state == 'completed':
            with database.SessionLocal() as db:
                db.get(Document, doc['id']).status = 'trashed'
                db.commit()
        return original(task_id, engine, state, **values)
    monkeypatch.setattr(runtime, '_finish', changed_finish)
    async def execute():
        task = await runtime.start(session['id'], prepared['preparation_id'], True, 'search-commit-change')
        return await wait_task(runtime, task)
    result = asyncio.run(execute())
    assert result['state'] == 'failed'
    assert result['answer'] is None
    assert result['sources'] == []


def test_unconfigured_local_preparation_still_works(partner_library, monkeypatch):
    from backend.app.core import ai_settings
    runtime = partner_library
    monkeypatch.setattr(ai_settings, 'get_settings', lambda: {
        'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash', 'has_api_key': False})
    def no_key():
        raise ai_settings.AISettingsError('请先配置 AI 服务密钥')
    monkeypatch.setattr(ai_settings, 'get_connection', no_key)
    session = runtime.create_session()
    prepared = runtime.prepare(session['id'], '本机准备', 'selected', [])
    assert prepared['service']['model'] == 'deepseek-flash'
    assert runtime._preparations[prepared['preparation_id']]['config_revision'] is None


def test_real_library_setup_restores_old_inflight_task_as_interrupted(partner_library, tmp_path, monkeypatch):
    """只调用真实设置入口切出再切回，不用测试代码手动 initialize 掩盖恢复缺口。"""
    from backend.app.api.settings_api import setup_library
    from backend.app.api.schemas import LibrarySetup
    runtime = partner_library
    configure(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, 'pending_path', None)
    original_root = settings.library_path
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def fake_agent(**values):
        calls.append(values['message'])
        entered.set()
        await release.wait()
        return {'answer': '旧任务不能继续发布', 'sources': [], 'artifacts': []}
    monkeypatch.setattr(runtime, 'run_agent', fake_agent)
    session = runtime.create_session()
    prepared = runtime.prepare(session['id'], '长时间资料分析', 'selected', [])
    async def execute():
        task = await runtime.start(session['id'], prepared['preparation_id'], True, 'real-library-switch')
        await asyncio.wait_for(entered.wait(), 1)
        setup_library(LibrarySetup(path=str(tmp_path / 'library-B')))
        release.set()
        for _ in range(100):
            await asyncio.sleep(.005)
            if not runtime._running:
                break
        assert runtime.list_sessions()['items'] == []
        setup_library(LibrarySetup(path=original_root))
        return task['id'], runtime.get_session(session['id'])
    task_id, detail = asyncio.run(execute())
    assert detail['session']['active_task_id'] is None
    assert runtime.get_task(task_id)['state'] == 'interrupted'
    assert calls == ['长时间资料分析'], '库激活恢复不得自动重放模型调用'


def test_same_binding_initialization_preserves_actual_running_task(partner_library, monkeypatch):
    runtime = partner_library
    configure(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    async def fake_agent(**values):
        entered.set()
        await release.wait()
        return {'answer': '仍在同一资料库正常完成', 'sources': [], 'artifacts': []}
    monkeypatch.setattr(runtime, 'run_agent', fake_agent)
    session = runtime.create_session()
    prepared = runtime.prepare(session['id'], '正在处理', 'selected', [])
    async def execute():
        task = await runtime.start(session['id'], prepared['preparation_id'], True, 'same-binding-init')
        await asyncio.wait_for(entered.wait(), 1)
        database.init_db()
        runtime.initialize()
        assert runtime.get_task(task['id'])['state'] == 'running'
        release.set()
        return await wait_task(runtime, task)
    result = asyncio.run(execute())
    assert result['state'] == 'completed'


def orphan_task(runtime, request_id):
    from backend.app.partner_models import PartnerTask
    session = runtime.create_session()
    with database.SessionLocal() as db:
        task = PartnerTask(session_id=session['id'], request_id=request_id,
            preparation_id='old-preparation', state='running', message='崩溃前任务', input_json='{}')
        db.add(task)
        db.commit()
        return task.id


def test_real_library_migration_recovers_copied_inflight_task(partner_library, tmp_path, monkeypatch):
    from backend.app.services import storage_migration
    runtime = partner_library
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, 'pending_path', None)
    monkeypatch.setattr(storage_migration, 'bootstrap_path', lambda: tmp_path / 'bootstrap.json')
    task_id = orphan_task(runtime, 'migrated-orphan')
    result = storage_migration.migrate('library', str(tmp_path / 'new-library'))
    assert result['phase'] == 'complete'
    assert runtime.get_task(task_id)['state'] == 'interrupted'


def test_real_migration_recovery_activates_database_and_clears_orphan(partner_library, tmp_path, monkeypatch):
    from backend.app.services import storage_migration
    from backend.app.partner_models import PartnerTask
    runtime = partner_library
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, 'pending_path', None)
    monkeypatch.setattr(storage_migration, 'bootstrap_path', lambda: tmp_path / 'bootstrap.json')
    task_id = orphan_task(runtime, 'recovered-orphan')
    original = storage_migration._finish
    monkeypatch.setattr(storage_migration, '_finish', lambda journal: (_ for _ in ()).throw(OSError('模拟提交后中断')))
    with pytest.raises(HTTPException):
        storage_migration.migrate('library', str(tmp_path / 'new-library'))
    monkeypatch.setattr(storage_migration, '_finish', original)
    # 模拟启动时尚未执行激活恢复的目标数据库遗留状态。
    with database.SessionLocal() as db:
        db.get(PartnerTask, task_id).state = 'running'
        db.commit()
    result = storage_migration.recover()
    assert result['phase'] == 'complete'
    assert runtime.get_task(task_id)['state'] == 'interrupted'


def test_activation_does_not_take_runtime_lock_in_library_change_order(partner_library, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from backend.app.api.settings_api import setup_library
    from backend.app.api.schemas import LibrarySetup
    runtime = partner_library
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, 'pending_path', None)
    finished = threading.Event()
    def switch():
        try:
            return setup_library(LibrarySetup(path=str(tmp_path / 'lock-order-library')))
        finally:
            finished.set()
    with ThreadPoolExecutor(max_workers=1) as executor:
        with runtime._lock:
            future = executor.submit(switch)
            completed_without_runtime_lock = finished.wait(1)
        result = future.result(timeout=2)
    assert completed_without_runtime_lock, '激活恢复不可持library锁再等待runtime锁'
    assert result['path'] == settings.library_path


def test_idempotent_activation_during_task_commit_preserves_queued_reservation(partner_library, monkeypatch):
    runtime = partner_library
    configure(monkeypatch)
    session = runtime.create_session()
    prepared = runtime.prepare(session['id'], '正常开始任务', 'selected', [])
    original = database.TrackedSession.commit
    activated = []
    def commit_then_activate(db):
        original(db)
        if not activated and any(entry['task'] is None for entry in runtime._running.values()):
            activated.append(True)
            runtime.initialize()
    monkeypatch.setattr(database.TrackedSession, 'commit', commit_then_activate)
    async def fake_agent(**values):
        return {'answer': '预约未被误中断', 'sources': [], 'artifacts': []}
    monkeypatch.setattr(runtime, 'run_agent', fake_agent)
    async def execute():
        task = await runtime.start(session['id'], prepared['preparation_id'], True, 'commit-activation')
        return await wait_task(runtime, task)
    result = asyncio.run(execute())
    assert activated == [True]
    assert result['state'] == 'completed'
