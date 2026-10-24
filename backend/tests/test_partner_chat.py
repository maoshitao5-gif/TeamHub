"""自然聊天入口只在隔离库使用合成模型，覆盖状态、授权与累计预算。"""
import asyncio
import json

import pytest
from fastapi import HTTPException

import backend.app.database as database
from backend.app.partner_models import PartnerTask
from backend.tests.test_partner_runtime import configure, partner_library


async def wait_done(runtime, task):
    for _ in range(200):
        task = runtime.get_task(task['id'])
        if task['state'] not in {'queued', 'running'}:
            return task
        await asyncio.sleep(.005)
    pytest.fail('聊天任务未结束')


def arguments(runtime, **extra):
    return dict(message='你好', scope_mode='selected', document_ids=[], thinking_mode='disabled',
                request_id='direct-1', expected_service_revision=runtime.chat_config()['service_revision'],
                consent=True, **extra)


def test_send_is_single_step_idempotent_and_restores_options(partner_library, monkeypatch):
    runtime = partner_library
    configure(monkeypatch)
    calls = []
    async def agent(**values):
        calls.append(values)
        return {'answer': '你好，继续讨论。', 'sources': [], 'artifacts': []}
    monkeypatch.setattr(runtime, 'run_agent', agent)
    session = runtime.create_session()
    async def run():
        options = arguments(runtime)
        first = await runtime.send(session['id'], **options)
        same = await runtime.send(session['id'], **{**options, 'message': '  你好  '})
        assert first['id'] == same['id']
        with pytest.raises(HTTPException) as error:
            await runtime.send(session['id'], **{**options, 'message': '不同问题'})
        assert error.value.status_code == 409
        return await wait_done(runtime, first)
    result = asyncio.run(run())
    assert result['state'] == 'completed'
    assert len(calls) == 1
    assert calls[0]['limits']['model_requests'] == 24
    assert runtime.get_session(session['id'])['chat_options'] == {
        'scope_mode': 'selected', 'document_ids': [], 'thinking_mode': 'disabled'}
    assert 'fake-secret' not in str(runtime.chat_config())


def test_direct_send_queues_behind_background_ai_and_cancel_never_calls_model(partner_library, monkeypatch):
    runtime = partner_library
    configure(monkeypatch)
    calls = []
    async def agent(**values):
        calls.append(values)
        return {'answer': '不应执行'}
    monkeypatch.setattr(runtime, 'run_agent', agent)
    session = runtime.create_session()
    assert runtime._paid_call.acquire(blocking=False)
    async def run():
        task = await runtime.send(session['id'], **arguments(runtime))
        await asyncio.sleep(.02)
        assert runtime.get_task(task['id'])['state'] == 'queued'
        await runtime.cancel(task['id'])
        await asyncio.sleep(.01)
    try:
        asyncio.run(run())
        assert not calls
        assert not runtime._paid_call.acquire(blocking=False), '取消未持有租约的任务不能释放他人的锁'
    finally:
        runtime._paid_call.release()


def test_native_history_is_private_and_next_turn_reuses_it(partner_library, monkeypatch):
    runtime = partner_library
    configure(monkeypatch)
    history = [{'role': 'user', 'content': '你好'},
               {'role': 'assistant', 'content': '第一轮', 'reasoning_content': '私有协议思考'}]
    calls = []
    async def agent(**values):
        calls.append(values)
        return {'answer': '第一轮', 'sources': [], 'artifacts': [],
                'engine_history': history, 'engine_history_reusable': True}
    monkeypatch.setattr(runtime, 'run_agent', agent)
    session = runtime.create_session()
    async def run():
        first = await wait_done(runtime, await runtime.send(session['id'], **arguments(runtime)))
        assert 'engine_history' not in first
        second = await runtime.send(session['id'], **{**arguments(runtime), 'request_id': 'second', 'message': '继续'})
        await wait_done(runtime, second)
    asyncio.run(run())
    assert calls[1]['engine_history'] == history
    assert '私有协议思考' not in str(runtime.get_session(session['id']))


def test_automatic_segments_share_one_total_budget_and_task(partner_library, monkeypatch):
    from backend.app.core.partner_budget import PartnerPaused
    runtime = partner_library
    configure(monkeypatch)
    calls = []
    async def agent(**values):
        calls.append(values)
        usage = {'model_requests': values['limits']['model_requests'], 'tool_calls': 1,
                 'output_tokens': 100, 'time_seconds': 1}
        raise PartnerPaused('model_requests', {'answer': '已有进度', 'sources': [], 'artifacts': [],
            'usage': usage, 'progress': {'read_count': len(calls)},
            'checkpoint': {'evidence': [str(len(calls))]}, 'dependency_records': {}})
    monkeypatch.setattr(runtime, 'run_agent', agent)
    session = runtime.create_session()
    result = asyncio.run(wait_send(runtime, session['id']))
    assert result['state'] == 'paused'
    assert [call['limits']['model_requests'] for call in calls] == [24, 24, 16]
    assert [call['limits']['output_tokens'] for call in calls] == [128000, 127900, 127800]
    assert result['usage']['model_requests'] == 64
    assert len(runtime.get_session(session['id'])['messages']) == 1


async def wait_send(runtime, session_id):
    return await wait_done(runtime, await runtime.send(session_id, **arguments(runtime)))


def test_partial_answer_survives_cancel_without_final_assistant_message(partner_library, monkeypatch):
    runtime = partner_library
    configure(monkeypatch)
    emitted = asyncio.Event()
    async def agent(**values):
        values['emit']('answer_preview', '这部分已经生成')
        emitted.set()
        await asyncio.sleep(10)
    monkeypatch.setattr(runtime, 'run_agent', agent)
    session = runtime.create_session()
    async def run():
        task = await runtime.send(session['id'], **arguments(runtime))
        await asyncio.wait_for(emitted.wait(), 1)
        return await runtime.cancel(task['id'])
    result = asyncio.run(run())
    assert result['answer_preview'] == '这部分已经生成'
    assert result['state'] == 'cancelled'
    assert [m['role'] for m in runtime.get_session(session['id'])['messages']] == ['user']


def test_service_change_requires_new_visible_target(partner_library, monkeypatch):
    from backend.app.core import ai_settings
    runtime = partner_library
    configure(monkeypatch)
    session = runtime.create_session()
    request = arguments(runtime)
    changed = {'base_url': 'https://different.example', 'model': 'deepseek-flash'}
    monkeypatch.setattr(ai_settings, 'get_settings', lambda: {**changed, 'has_api_key': True})
    monkeypatch.setattr(ai_settings, 'get_connection', lambda: (changed, 'fake-secret', 'revision-2'))
    async def run():
        with pytest.raises(HTTPException) as error:
            await runtime.send(session['id'], **request)
        assert error.value.detail['error']['code'] == 'AI_SERVICE_CHANGED'
    asyncio.run(run())
    assert runtime.get_session(session['id'])['messages'] == []


def test_new_question_closes_paused_execution_and_preserves_progress(partner_library, monkeypatch):
    runtime = partner_library
    configure(monkeypatch)
    session = runtime.create_session()
    with database.SessionLocal() as db:
        old = PartnerTask(session_id=session['id'], request_id='old', preparation_id='old', state='paused',
            message='此前问题', input_json='{}', result_json=json.dumps({'answer': '已取得的成果'}))
        db.add(old); db.commit(); old_id = old.id
    async def agent(**values):
        return {'answer': '新问题的回答', 'sources': [], 'artifacts': []}
    monkeypatch.setattr(runtime, 'run_agent', agent)
    result = asyncio.run(wait_send(runtime, session['id']))
    assert result['state'] == 'completed'
    assert runtime.get_task(old_id)['state'] == 'cancelled'
    assert runtime.get_task(old_id)['answer'] == '已取得的成果'


def test_resume_is_idempotent_and_refuses_silent_provider_change(partner_library, monkeypatch):
    from backend.app.core.partner_budget import PartnerPaused
    from backend.app.core import ai_settings
    runtime = partner_library
    configure(monkeypatch)
    session = runtime.create_session()
    calls = []
    async def agent(**values):
        calls.append(values)
        if len(calls) == 1:
            raise PartnerPaused('output_tokens', {'answer': '阶段结果', 'sources': [], 'artifacts': [],
                'usage': {'model_requests': 1, 'tool_calls': 0, 'output_tokens': 128000, 'time_seconds': 1},
                'checkpoint': {'evidence': []}, 'dependency_records': {}})
        return {'answer': '完成', 'sources': [], 'artifacts': [],
                'usage': {'model_requests': 1, 'output_tokens': 10}}
    monkeypatch.setattr(runtime, 'run_agent', agent)
    async def run():
        first = await wait_send(runtime, session['id'])
        assert first['state'] == 'paused'
        args = {'request_id': 'continue-1', 'expected_service_revision': runtime.chat_config()['service_revision'], 'consent': True}
        resumed = await runtime.resume(first['id'], **args)
        repeat = await runtime.resume(first['id'], **args)
        assert repeat['id'] == resumed['id']
        assert repeat['last_request_id'] == args['request_id']
        return await wait_done(runtime, resumed)
    result = asyncio.run(run())
    assert result['state'] == 'completed'
    assert result['usage']['model_requests'] == 2
    assert result['usage']['output_tokens'] == 128010
    assert len(runtime.get_session(session['id'])['messages']) == 2
    # 新服务虽被当前页面展示，原任务仍不能把原服务历史静默转给另一个服务。
    with database.SessionLocal() as db:
        task = db.get(PartnerTask, result['id'])
        task.state = 'paused'
        stored = json.loads(task.result_json); stored['pause'] = {'resumable': True}
        task.result_json = json.dumps(stored); db.commit()
    changed = {'base_url': 'https://another.example', 'model': 'deepseek-flash'}
    monkeypatch.setattr(ai_settings, 'get_settings', lambda: {**changed, 'has_api_key': True})
    monkeypatch.setattr(ai_settings, 'get_connection', lambda: (changed, 'fake', 'new'))
    async def rejected():
        with pytest.raises(HTTPException) as error:
            await runtime.resume(result['id'], request_id='changed',
                expected_service_revision=runtime.chat_config()['service_revision'], consent=True)
        assert error.value.detail['error']['code'] == 'AI_SERVICE_CHANGED'
    asyncio.run(rejected())
    assert len(calls) == 2


def test_empty_progress_does_not_automatically_repeat_paid_segment(partner_library, monkeypatch):
    from backend.app.core.partner_budget import PartnerPaused
    runtime = partner_library
    configure(monkeypatch)
    calls = []
    async def agent(**values):
        calls.append(values)
        raise PartnerPaused('tool_calls', {'answer': '还未找到相关资料', 'sources': [], 'artifacts': [],
            'usage': {'model_requests': 1, 'tool_calls': 64}, 'checkpoint': {}})
    monkeypatch.setattr(runtime, 'run_agent', agent)
    result = asyncio.run(wait_send(runtime, runtime.create_session()['id']))
    assert result['state'] == 'paused'
    assert len(calls) == 1
    assert '没有获得' in result['pause']['message']


def test_full_library_general_chat_has_no_arbitrary_document_dependencies(partner_library, monkeypatch, tmp_path):
    from backend.tests.test_partner_runtime import ingest_test_file
    from backend.app.models import Document
    runtime = partner_library
    configure(monkeypatch)
    first = ingest_test_file(tmp_path)
    calls = []
    async def agent(**values):
        calls.append(values)
        with database.SessionLocal() as db:
            db.get(Document, first['id']).name = '无关文件已改名.txt'
            db.commit()
        return {'answer': '通用培训建议', 'sources': [], 'artifacts': [], 'dependency_records': {}}
    monkeypatch.setattr(runtime, 'run_agent', agent)
    session = runtime.create_session()
    async def run():
        return await wait_done(runtime, await runtime.send(session['id'],
            **{**arguments(runtime), 'scope_mode': 'library'}))
    result = asyncio.run(run())
    assert result['state'] == 'completed'
    assert calls[0]['scope_mode'] == 'library'
    with database.SessionLocal() as db:
        assert json.loads(db.get(PartnerTask, result['id']).result_json)['dependency_records'] == {}


@pytest.mark.parametrize('change', ['metadata', 'version'])
def test_version_change_retains_user_goal_but_drops_stale_evidence_and_native_history(partner_library, monkeypatch, tmp_path, change):
    from backend.tests.test_partner_runtime import ingest_test_file
    from backend.app.models import Document
    runtime = partner_library
    configure(monkeypatch)
    doc = ingest_test_file(tmp_path)
    calls = []
    async def agent(**values):
        calls.append(values)
        return {'answer': '旧名称资料描述', 'sources': [], 'artifacts': [], 'dependency_records': values['scope'],
            'engine_history': [{'role': 'user', 'content': values['message']},
                               {'role': 'assistant', 'content': '旧名称资料描述'}], 'engine_history_reusable': True}
    monkeypatch.setattr(runtime, 'run_agent', agent)
    session = runtime.create_session()
    async def run():
        params = {**arguments(runtime), 'message': '目标是为新员工培训', 'document_ids': [doc['id']]}
        await wait_done(runtime, await runtime.send(session['id'], **params))
        with database.SessionLocal() as db:
            if change == 'metadata':
                db.get(Document, doc['id']).name = '更新后的名称.txt'
                db.commit()
            else:
                from backend.app.api.versions import add_version
                from backend.app.api.schemas import VersionCreate
                updated = tmp_path / 'updated-version.txt'
                updated.write_text('实际保存的新版本正文', encoding='utf-8')
                add_version(doc['id'], VersionCreate(file_path=str(updated)), db)
        second = await runtime.send(session['id'], **{**params, 'message': '继续', 'request_id': 'next'})
        return await wait_done(runtime, second)
    assert asyncio.run(run())['state'] == 'completed'
    assert calls[1].get('engine_history') is None
    assert {'role': 'user', 'content': '目标是为新员工培训'} in calls[1]['history']
    assert '旧名称资料描述' not in str(calls[1]['history'])


def test_scope_narrowing_drops_user_pasted_source_text_and_native_context(partner_library, monkeypatch, tmp_path):
    from backend.tests.test_partner_runtime import ingest_test_file
    runtime = partner_library
    configure(monkeypatch)
    doc = ingest_test_file(tmp_path)
    calls = []
    async def agent(**values):
        calls.append(values)
        return {'answer': '基于私有文字的回复', 'sources': [], 'artifacts': [], 'dependency_records': values['scope'],
            'engine_history': [{'role': 'user', 'content': values['message']},
                               {'role': 'assistant', 'content': '基于私有文字的回复'}], 'engine_history_reusable': True}
    monkeypatch.setattr(runtime, 'run_agent', agent)
    session = runtime.create_session()
    async def run():
        await wait_done(runtime, await runtime.send(session['id'], **{**arguments(runtime),
            'message': '手工粘贴的资料秘密', 'document_ids': [doc['id']]}))
        return await wait_done(runtime, await runtime.send(session['id'], **{**arguments(runtime),
            'message': '从现在起只聊通用问题', 'request_id': 'narrow'}))
    assert asyncio.run(run())['state'] == 'completed'
    assert calls[1]['history'] == []
    assert calls[1].get('engine_history') is None


@pytest.mark.parametrize('changed', [True, False])
def test_cancel_checks_actual_exposed_dependencies_before_preserving_partial(partner_library, monkeypatch, tmp_path, changed):
    from backend.tests.test_partner_runtime import ingest_test_file
    from backend.app.models import Document
    runtime = partner_library
    configure(monkeypatch)
    doc = ingest_test_file(tmp_path)
    entered = asyncio.Event()
    async def agent(**values):
        if changed:
            values['emit']('dependency_records', values['scope'])
        values['emit']('answer_preview', '已显示的资料分析')
        with database.SessionLocal() as db:
            db.get(Document, doc['id']).status = 'trashed'; db.commit()
        entered.set()
        await asyncio.sleep(10)
    monkeypatch.setattr(runtime, 'run_agent', agent)
    session = runtime.create_session()
    async def run():
        task = await runtime.send(session['id'], **{**arguments(runtime), 'scope_mode': 'library'})
        await asyncio.wait_for(entered.wait(), 1)
        return await runtime.cancel(task['id'])
    result = asyncio.run(run())
    assert result['preview_discarded'] is changed
    assert result['answer_preview'] == ('' if changed else '已显示的资料分析')
    assert not any(step['kind'] == 'dependency_records' for step in result['steps'])


def test_network_failure_preserves_long_partial_without_assistant_message(partner_library, monkeypatch):
    from backend.app.core.ai_client import AIError
    runtime = partner_library
    configure(monkeypatch)
    partial = '较长的答复' * 9000
    async def agent(**values):
        values['emit']('answer_preview', partial)
        raise AIError(503, 'AI_NETWORK_ERROR', '连接意外断开')
    monkeypatch.setattr(runtime, 'run_agent', agent)
    session = runtime.create_session()
    result = asyncio.run(wait_send(runtime, session['id']))
    assert result['state'] == 'failed'
    assert result['answer_preview'] == partial
    assert result['error']['code'] == 'AI_NETWORK_ERROR'
    assert [item['role'] for item in runtime.get_session(session['id'])['messages']] == ['user']


@pytest.mark.parametrize('rounds_before_narrowing', [1, 3, 10])
def test_assistant_restatement_inherits_input_authorization_across_turns_and_summary(
        partner_library, monkeypatch, tmp_path, rounds_before_narrowing):
    """无工具复述也可能来自用户粘贴；不能借空版本依赖逃过授权范围过滤。"""
    from backend.tests.test_partner_runtime import ingest_test_file
    runtime = partner_library
    configure(monkeypatch)
    ingest_test_file(tmp_path)
    calls = []
    async def agent(**values):
        calls.append(values)
        return {'answer': '助手转述 SYNTHETIC_SECRET', 'sources': [], 'artifacts': [],
                'dependency_records': {}}
    monkeypatch.setattr(runtime, 'run_agent', agent)
    session = runtime.create_session()
    async def run():
        for index in range(rounds_before_narrowing):
            params = {**arguments(runtime), 'request_id': f'wide-{index}', 'scope_mode': 'library',
                      'message': '用户粘贴 SYNTHETIC_SECRET' if index == 0 else '继续概括此前内容'}
            task = await wait_done(runtime, await runtime.send(session['id'], **params))
            assert task['state'] == 'completed'
        await wait_done(runtime, await runtime.send(session['id'], **{**arguments(runtime),
            'request_id': 'narrow-final', 'message': '只聊通用知识', 'scope_mode': 'selected', 'document_ids': []}))
    asyncio.run(run())
    outbound = {key: calls[-1].get(key) for key in ['history', 'context', 'engine_history']}
    assert 'SYNTHETIC_SECRET' not in str(outbound)
    assert calls[-1]['history'] == []
