"""跨轮上下文只使用临时库与假模型，不在准备阶段请求任何外部服务。"""
import asyncio
import json

import pytest
from fastapi import HTTPException

import backend.app.database as database
from backend.app.partner_models import PartnerMessage, PartnerTask
from backend.tests.test_partner_runtime import configure, ingest_test_file, partner_library, sdk_tool_response


async def completed(runtime, session_id, prepared, request_id):
    task = await runtime.start(session_id, prepared['preparation_id'], True, request_id)
    for _ in range(100):
        await asyncio.sleep(.005)
        task = runtime.get_task(task['id'])
        if task['state'] not in {'queued', 'running'}:
            return task
    pytest.fail('假模型任务未完成')


def seed_messages(session_id, count=32):
    from datetime import datetime, timedelta
    with database.SessionLocal() as db:
        task = PartnerTask(session_id=session_id, request_id='history-seed', preparation_id='history',
                           state='completed', message='原目标', input_json='{}')
        db.add(task)
        db.flush()
        ids = []
        for index in range(count):
            message = PartnerMessage(session_id=session_id, task_id=task.id,
                role='user' if index % 2 == 0 else 'assistant',
                content=('确定目标：按原版规程培训，预算500元，不得删除资料。' if index == 0
                         else f'第{index}条已经确认的内容。'),
                created_at=datetime(2025, 1, 1) + timedelta(seconds=index))
            db.add(message)
            db.flush()
            ids.append(message.id)
        db.commit()
        return ids


def test_old_history_is_traceable_in_preview_and_exact_model_context(partner_library, monkeypatch):
    runtime = partner_library
    configure(monkeypatch)
    session = runtime.create_session()
    ids = seed_messages(session['id'])
    calls = []
    async def fake_agent(**values):
        calls.append(values)
        return {'answer': '根据目标继续。', 'sources': [], 'artifacts': []}
    monkeypatch.setattr(runtime, 'run_agent', fake_agent)
    prepared = runtime.prepare(session['id'], '继续完善计划')
    assert len(runtime.get_session(session['id'])['messages']) == 32
    assert '预算500元' in prepared['context']['summary']
    assert ids[0] in prepared['context']['summary']
    assert '历史摘录' in prepared['context']['summary']
    assert len(prepared['history']) <= 24
    result = asyncio.run(completed(runtime, session['id'], prepared, 'history-context'))
    assert result['state'] == 'completed'
    assert calls[0]['context'] == prepared['context']


def test_changing_older_message_after_prepare_invalidates_authorization(partner_library, monkeypatch):
    runtime = partner_library
    configure(monkeypatch)
    session = runtime.create_session()
    ids = seed_messages(session['id'])
    prepared = runtime.prepare(session['id'], '继续')
    with database.SessionLocal() as db:
        db.get(PartnerMessage, ids[0]).content = '目标已经变化'
        db.commit()
    async def attempt():
        with pytest.raises(HTTPException) as error:
            await runtime.start(session['id'], prepared['preparation_id'], True, 'changed-history')
        assert error.value.detail['error']['code'] == 'PARTNER_HISTORY_CHANGED'
    asyncio.run(attempt())


def test_completed_turn_restores_full_evidence_without_old_draft_or_usage(partner_library, tmp_path, monkeypatch):
    from backend.app.services.partner_tools import PartnerTools
    runtime = partner_library
    configure(monkeypatch)
    document = ingest_test_file(tmp_path, '原文内容' * 120 + '已读尾部证据')
    session = runtime.create_session()
    prepared = runtime.prepare(session['id'], '阅读资料', 'selected', [document['id']])
    scope = runtime._preparations[prepared['preparation_id']]['scope_records']
    tools = PartnerTools(engine=database.engine, root=str(tmp_path / 'library'), scope=scope,
                         guard=lambda: None)
    tools.read_document(document['id'])
    checkpoint = tools.export_checkpoint()
    checkpoint['image_usage'] = {'count': 2, 'bytes': 1000}
    calls = []
    async def fake_agent(**values):
        calls.append(values)
        if len(calls) == 1:
            return {'answer': '已读并产出旧草稿。[S1]', 'sources': checkpoint['sources'],
                'checkpoint': checkpoint, 'artifacts': [{'id': 'old-draft'}],
                'dependency_records': scope, 'usage': {'model_requests': 5, 'output_tokens': 100}}
        restored = values['checkpoint']
        assert restored['sources'][0]['source_id'] == 'S1'
        assert '已读尾部证据' in restored['evidence'][0]['text']
        assert restored['image_usage'] == {'count': 0, 'bytes': 0}
        assert values['previous_artifacts'] == []
        assert not values.get('previous_answer')
        assert values['context'] == next_prepared['context']
        return {'answer': '继续完成。[S1]', 'sources': restored['sources'], 'artifacts': [],
                'checkpoint': restored, 'usage': {'model_requests': 1}}
    monkeypatch.setattr(runtime, 'run_agent', fake_agent)
    first = asyncio.run(completed(runtime, session['id'], prepared, 'first-evidence'))
    assert first['state'] == 'completed'
    assert 'checkpoint' not in first
    next_prepared = runtime.prepare(session['id'], '针对刚读过的材料继续', 'selected', [document['id']])
    assert next_prepared['context']['sources'][0]['source_id'] == 'S1'
    assert '已读尾部证据' in next_prepared['context']['evidence'][0]['text']
    second = asyncio.run(completed(runtime, session['id'], next_prepared, 'second-evidence'))
    assert second['state'] == 'completed'
    assert second['artifacts'] == []
    assert second['usage']['model_requests'] == 1


@pytest.mark.parametrize('change', ['scope', 'metadata', 'version'])
def test_old_evidence_and_answer_are_removed_when_dependency_invalid(partner_library, tmp_path, monkeypatch, change):
    from backend.app.models import Document, Version
    from backend.app.services.partner_tools import PartnerTools
    runtime = partner_library
    configure(monkeypatch)
    document = ingest_test_file(tmp_path, '旧私密证据不会再次发送')
    session = runtime.create_session()
    prepared = runtime.prepare(session['id'], '旧私密目标：读取资料并制定计划', 'selected', [document['id']])
    scope = runtime._preparations[prepared['preparation_id']]['scope_records']
    tools = PartnerTools(engine=database.engine, root=str(tmp_path / 'library'), scope=scope, guard=lambda: None)
    tools.read_document(document['id'])
    async def fake_agent(**values):
        return {'answer': '旧私密回答。[S1]', 'sources': tools.sources, 'artifacts': [],
                'checkpoint': tools.export_checkpoint(), 'dependency_records': scope}
    monkeypatch.setattr(runtime, 'run_agent', fake_agent)
    assert asyncio.run(completed(runtime, session['id'], prepared, 'old-sensitive'))['state'] == 'completed'
    if change != 'scope':
        with database.SessionLocal() as db:
            doc = db.get(Document, document['id'])
            if change == 'metadata':
                doc.name = '已改名.txt'
            else:
                db.get(Version, doc.current_version_id).sha256_hash = 'changed-hash'
            db.commit()
    preview = runtime.prepare(session['id'], '继续讨论', 'selected', [] if change == 'scope' else [document['id']])
    assert '旧私密证据' not in str(preview)
    assert '旧私密回答' not in str(preview)
    assert '旧私密目标' not in str(preview)
    assert preview['context']['evidence'] == []


def test_completed_checkpoint_change_after_preview_requires_new_confirmation(partner_library, tmp_path, monkeypatch):
    from backend.app.services.partner_tools import PartnerTools
    runtime = partner_library
    configure(monkeypatch)
    document = ingest_test_file(tmp_path)
    session = runtime.create_session()
    prepared = runtime.prepare(session['id'], '读取', 'selected', [document['id']])
    scope = runtime._preparations[prepared['preparation_id']]['scope_records']
    tools = PartnerTools(engine=database.engine, root=str(tmp_path / 'library'), scope=scope, guard=lambda: None)
    tools.read_document(document['id'])
    async def fake_agent(**values):
        return {'answer': '完成。[S1]', 'sources': tools.sources, 'artifacts': [], 'checkpoint': tools.export_checkpoint()}
    monkeypatch.setattr(runtime, 'run_agent', fake_agent)
    first = asyncio.run(completed(runtime, session['id'], prepared, 'checkpoint-original'))
    next_prepared = runtime.prepare(session['id'], '继续', 'selected', [document['id']])
    with database.SessionLocal() as db:
        task = db.get(PartnerTask, first['id'])
        result = json.loads(task.result_json)
        result['checkpoint']['evidence'][0]['text'] = '准备后变化'
        task.result_json = json.dumps(result)
        db.commit()
    async def attempt():
        with pytest.raises(HTTPException) as error:
            await runtime.start(session['id'], next_prepared['preparation_id'], True, 'changed-context')
        assert error.value.detail['error']['code'] == 'PARTNER_HISTORY_CHANGED'
    asyncio.run(attempt())


def test_thinking_mode_is_per_turn_and_continuation_uses_confirmed_mode(partner_library, monkeypatch):
    from backend.app.core.partner_budget import PartnerPaused
    runtime = partner_library
    configure(monkeypatch)
    session = runtime.create_session()
    prepared = runtime.prepare(session['id'], '先分析', thinking_mode='disabled')
    assert prepared['service']['thinking_mode'] == prepared['thinking_mode'] == 'disabled'
    calls = []
    async def fake_agent(**values):
        calls.append(values)
        if len(calls) == 1:
            raise PartnerPaused('model_requests', {'answer': '未完成', 'sources': [], 'artifacts': [],
                                                    'checkpoint': {'evidence': []}})
        return {'answer': '已完成', 'sources': [], 'artifacts': []}
    monkeypatch.setattr(runtime, 'run_agent', fake_agent)
    async def execute():
        first = await completed(runtime, session['id'], prepared, 'mode-first')
        assert first['state'] == 'paused'
        assert runtime.prepare_continue(first['id'])['thinking_mode'] == 'disabled'
        resume = runtime.prepare_continue(first['id'], thinking_mode='enabled')
        assert resume['service']['thinking_mode'] == 'enabled'
        assert resume['thinking_mode'] == 'enabled'
        await runtime.continue_task(first['id'], resume['preparation_id'], True, 'mode-continue')
        for _ in range(100):
            await asyncio.sleep(.005)
            if runtime.get_task(first['id'])['state'] == 'completed':
                break
    asyncio.run(execute())
    assert [call['config']['thinking_mode'] for call in calls] == ['disabled', 'enabled']
    assert runtime.prepare(session['id'], '另一个问题')['thinking_mode'] == 'enabled'


def test_invalid_old_checkpoint_is_omitted_without_blocking_new_turn(partner_library, tmp_path, monkeypatch):
    runtime = partner_library
    configure(monkeypatch)
    session = runtime.create_session()
    with database.SessionLocal() as db:
        db.add(PartnerTask(session_id=session['id'], state='completed', request_id='invalid-old',
            preparation_id='p', message='旧任务', input_json='{"scope":{}}',
            result_json='{"checkpoint":{"schema_version":2,"evidence":["malformed-private"]}}'))
        db.commit()
    prepared = runtime.prepare(session['id'], '新的问题')
    assert prepared['context']['evidence'] == []
    assert 'malformed-private' not in str(prepared)


def test_user_goal_attached_to_revoked_material_is_not_reused(partner_library, tmp_path, monkeypatch):
    runtime = partner_library
    configure(monkeypatch)
    document = ingest_test_file(tmp_path)
    session = runtime.create_session()
    prepared = runtime.prepare(session['id'], '文档机密目标', 'selected', [document['id']])
    async def fake_agent(**values):
        return {'answer': '旧回答', 'sources': [], 'artifacts': []}
    monkeypatch.setattr(runtime, 'run_agent', fake_agent)
    asyncio.run(completed(runtime, session['id'], prepared, 'old-goal'))
    revoked = runtime.prepare(session['id'], '新问题', 'selected', [])
    assert '文档机密目标' not in str(revoked)


def test_new_scoped_turn_can_cite_retained_sparse_source_via_real_sdk(partner_library, tmp_path, monkeypatch):
    import httpx2
    from backend.tests.support.legacy_partner_agent import run_agent
    from backend.app.services.partner_tools import PartnerTools
    runtime = partner_library
    configure(monkeypatch)
    first_doc = ingest_test_file(tmp_path, '甲资料旧证据', '甲.txt')
    second_doc = ingest_test_file(tmp_path, '乙资料仍授权的证据', '乙.txt')
    session = runtime.create_session()
    first_preview = runtime.prepare(session['id'], '阅读两份资料', 'selected', [first_doc['id'], second_doc['id']])
    scope = runtime._preparations[first_preview['preparation_id']]['scope_records']
    tools = PartnerTools(engine=database.engine, root=str(tmp_path / 'library'), scope=scope, guard=lambda: None)
    tools.read_document(first_doc['id'])
    tools.read_document(second_doc['id'])
    async def initial_agent(**values):
        return {'answer': '两份资料已读。[S1][S2]', 'sources': tools.sources, 'artifacts': [],
                'checkpoint': tools.export_checkpoint()}
    monkeypatch.setattr(runtime, 'run_agent', initial_agent)
    assert asyncio.run(completed(runtime, session['id'], first_preview, 'sparse-first'))['state'] == 'completed'
    second_preview = runtime.prepare(session['id'], '只分析乙资料', 'selected', [second_doc['id']])
    assert [source['source_id'] for source in second_preview['context']['sources']] == ['S2']
    assert '甲资料旧证据' not in str(second_preview)
    calls = []
    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        prompt = json.loads(next(message['content'] for message in reversed(payload['messages'])
                                 if message['role'] == 'user'))
        assert prompt['context'] == second_preview['context']
        assert prompt['continuation_context'] is None
        return httpx2.Response(200, json=sdk_tool_response('final_result', {
            'answer': '依据乙资料继续完成。[S2]', 'source_ids': ['S2']}))
    async def next_agent(**values):
        return await run_agent(**values, transport_override=httpx2.MockTransport(handler))
    monkeypatch.setattr(runtime, 'run_agent', next_agent)
    second = asyncio.run(completed(runtime, session['id'], second_preview, 'sparse-second'))
    assert second['state'] == 'completed', second
    assert second['answer'] == '依据乙资料继续完成。[S2]'
    assert len(calls) == 1
