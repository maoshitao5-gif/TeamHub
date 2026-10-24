"""AI 预整理只产生建议；用户采用才移动工作文件，全部使用隔离资料。"""
import asyncio
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app import database
from backend.app.config import settings
from backend.app.models import Document, Version
from backend.tests.test_partner_runtime import configure
from main import app

client = TestClient(app)


@pytest.fixture
def library(tmp_path, monkeypatch):
    previous = settings.library_path
    monkeypatch.setattr(settings, 'library_path', str(tmp_path / 'library'))
    monkeypatch.setattr(settings, 'pending_path', '')
    monkeypatch.setenv('TEAMHUB_AI_CONFIG_DIR', str(tmp_path / 'ai-profile'))
    database.rebind_engine()
    database.init_db()
    from backend.app.services import preorganize as service
    configure(monkeypatch)
    yield service
    asyncio.run(service.stop_worker())
    settings.library_path = previous
    database.rebind_engine()


def pending(tmp_path, name='资料.txt', content='设备检修说明'):
    path = tmp_path / name
    path.write_text(content, encoding='utf-8')
    response = client.post('/api/documents/quick-add', json={'source_path': str(path)})
    assert response.status_code == 200, response.text
    return response.json()


def start(service, docs, request_id='start-1'):
    config = service.get_config()
    return service.start(config['library_token'], config['service_revision'], True, request_id,
                         [doc['id'] for doc in docs])


def proposal_output(doc_id, **extra):
    return {'proposal': {'name': '设备检修说明.txt', 'tags': ['检修'], 'target_dir': '',
                        'summary': '设备检修说明', 'related_clues': [], 'gaps': [],
                        'source_ids': ['S1'], **extra},
            'sources': [{'id': 'S1', 'document_id': doc_id, 'excerpt': '设备检修说明'}],
            'progress': {'read_count': 1}, 'usage': {'model_requests': 1}}


def ready(service, monkeypatch, doc):
    async def agent(**kwargs):
        assert database.active_session_count() == 0
        kwargs['guard']()
        assert list(kwargs['scope']) == [doc['id']]
        assert kwargs['purpose'] == 'preorganize'
        return proposal_output(doc['id'])
    monkeypatch.setattr(service, 'run_agent', agent)
    start(service, [doc])
    assert asyncio.run(service.process_one())
    return service.get_document(doc['id'])


def apply(service, doc, detail, request_id='apply-1', **changes):
    return service.apply(service.get_config()['library_token'], [{
        'document_id': doc['id'], 'proposal_id': detail['proposal_id'],
        'request_id': request_id, **detail['proposal'], **changes
    }])['items'][0]


def test_default_off_explicit_consent_and_library_guard(library, tmp_path):
    doc = pending(tmp_path)
    config = library.get_config()
    assert config['policy'] == {'enabled': False, 'active': False, 'paused_reason': None}
    assert library.get_document(doc['id'])['state'] == 'waiting'
    assert asyncio.run(library.process_one()) is False
    for token, revision, consent in [
        ('old-library', config['service_revision'], True),
        ('非ASCII令牌', config['service_revision'], True),
        (config['library_token'], 'old-service', True),
        (config['library_token'], config['service_revision'], False),
    ]:
        with pytest.raises(HTTPException):
            library.start(token, revision, consent, 'not-authorized', [doc['id']])
    assert library.get_document(doc['id'])['state'] == 'waiting'


def test_binary_office_failure_keeps_specific_reason_without_paid_call(library, tmp_path, monkeypatch):
    from backend.app.core import hermes_agent
    source = tmp_path / '显示名.docx'
    content = bytes.fromhex('d0cf11e0a1b11ae1') + b'synthetic binary office'
    source.write_bytes(content)
    imported = client.post('/api/documents/quick-add', json={'source_path': str(source)})
    assert imported.status_code == 200
    doc = imported.json()
    def must_not_start():
        pytest.fail('二进制 Office 预检应在启动模型前失败')
    monkeypatch.setattr(hermes_agent, '_command', must_not_start)
    start(library, [doc])
    assert asyncio.run(library.process_one())
    detail = library.get_document(doc['id'])
    assert detail['state'] == 'failed'
    assert 'Office/WPS' in detail['error'] and '另存为' in detail['error']
    current = client.get(f"/api/documents/{doc['id']}").json()
    assert current['status'] == 'pending' and current['name'] == doc['name']
    assert Path(current['working_path']).read_bytes() == content
    assert len(client.get(f"/api/documents/{doc['id']}/versions").json()) == 1


def test_ready_is_read_only_and_duplicate_start_is_idempotent(library, tmp_path, monkeypatch):
    doc = pending(tmp_path)
    detail = ready(library, monkeypatch, doc)
    assert detail['state'] == 'ready'
    assert detail['proposal']['name'] == '设备检修说明.txt'
    current = client.get(f"/api/documents/{doc['id']}").json()
    assert current['name'] == doc['name']
    assert current['status'] == 'pending'
    assert current['tags'] == []
    assert start(library, [doc])['items'][0]['proposal_id'] == detail['proposal_id']
    assert asyncio.run(library.process_one()) is False


def test_apply_and_undo_are_atomic_idempotent_and_do_not_create_versions(library, tmp_path, monkeypatch):
    doc = pending(tmp_path)
    detail = ready(library, monkeypatch, doc)
    result = apply(library, doc, detail)
    assert result['ok'], result
    assert apply(library, doc, detail)['operation_id'] == result['operation_id']
    current = client.get(f"/api/documents/{doc['id']}").json()
    assert current['name'] == '设备检修说明.txt'
    assert current['status'] == 'organized'
    assert [tag['name'] for tag in current['tags']] == ['检修']
    with database.SessionLocal() as db:
        assert db.query(Version).filter_by(document_id=doc['id']).count() == 1
    token = library.get_config()['library_token']
    assert library.undo(token, result['operation_id'])['undone']
    assert library.undo(token, result['operation_id'])['undone']
    restored = client.get(f"/api/documents/{doc['id']}").json()
    assert (restored['name'], restored['status'], restored['storage_path'], restored['tags']) == (
        doc['name'], 'pending', doc['storage_path'], [])
    assert library.operations()['items'][0]['undone_at']


def test_stale_metadata_and_unsaved_content_cannot_be_applied(library, tmp_path, monkeypatch):
    doc = pending(tmp_path)
    detail = ready(library, monkeypatch, doc)
    from pathlib import Path
    Path(doc['working_path']).write_text('未保存的更改', encoding='utf-8')
    result = apply(library, doc, detail)
    assert not result['ok'] and '保存' in result['error']
    Path(doc['working_path']).write_text('设备检修说明', encoding='utf-8')
    client.put(f"/api/documents/{doc['id']}/tags", json=['人工标签'])
    assert library.get_document(doc['id'])['state'] != 'ready'
    assert not apply(library, doc, detail)['ok']


def test_batch_partial_failure_and_undo_never_overwrite_human_changes(library, tmp_path, monkeypatch):
    first = pending(tmp_path)
    detail = ready(library, monkeypatch, first)
    result = apply(library, first, detail, tags=[' '])
    assert not result['ok']
    result = apply(library, first, detail)
    assert result['ok']
    client.put(f"/api/documents/{first['id']}/tags", json=['用户新标签'])
    with pytest.raises(HTTPException) as error:
        library.undo(library.get_config()['library_token'], result['operation_id'])
    assert error.value.status_code == 409


def test_auto_policy_discovers_future_imports_and_cancel_prevents_requeue(library, tmp_path, monkeypatch):
    config = library.get_config()
    library.set_policy(config['library_token'], config['service_revision'], True, True)
    doc = pending(tmp_path)
    calls = []
    async def agent(**kwargs):
        calls.append(kwargs)
        return proposal_output(doc['id'])
    monkeypatch.setattr(library, 'run_agent', agent)
    assert asyncio.run(library.process_one())
    assert len(calls) == 1
    library.cancel(config['library_token'], [doc['id']])
    assert asyncio.run(library.process_one()) is False
    assert library.get_document(doc['id'])['state'] == 'waiting'


def test_model_failure_is_not_automatically_replayed(library, tmp_path, monkeypatch):
    doc = pending(tmp_path)
    calls = []
    async def agent(**kwargs):
        calls.append(kwargs)
        raise RuntimeError('private model payload must not escape')
    monkeypatch.setattr(library, 'run_agent', agent)
    start(library, [doc])
    assert asyncio.run(library.process_one())
    result = library.get_document(doc['id'])
    assert result['state'] == 'failed'
    assert 'private model' not in result['error']
    assert asyncio.run(library.process_one()) is False
    assert len(calls) == 1


def test_incomplete_task_persists_only_safe_diagnostics_without_ready_or_card(library, tmp_path, monkeypatch, caplog):
    from backend.app.core.ai_client import AIError
    from backend.app.preorganize_models import PreorganizeProposal
    from backend.app.services import document_cards
    doc = pending(tmp_path)
    async def agent(**values):
        values['emit']('answer', {'text': 'SECRET-ANSWER'})
        values['emit']('structured_diagnostic', {
            'purpose': 'preorganize', 'terminal_tool': 'propose_organization',
            'outcome': 'incomplete', 'model_requests': 3, 'tool_calls': 5,
            'read_sources': 1, 'visual_pages': 1, 'submission_attempts': 2,
            'recovery_attempts': 10**100, 'answer': 'SECRET-ANSWER' * 10000,
            'last_error': {'code': 'SUBMISSION_SCHEMA_INVALID', 'input': 'SECRET-INPUT',
                'validation_errors': [{'field': 'card.summary', 'code': 'required',
                                       'message': 'SECRET-ERROR' * 10000, 'input': 'SECRET-INPUT'},
                                      {'field': 'SECRET-FIELD' * 10000, 'code': 'required'}]}})
        raise AIError(502, 'AI_ORGANIZATION_INCOMPLETE', 'AI 未能提交有效方案')
    monkeypatch.setattr(library, 'run_agent', agent)
    start(library, [doc])
    assert asyncio.run(library.process_one())
    detail = library.get_document(doc['id'])
    assert detail['state'] == 'failed' and detail['proposal'] is None
    assert detail['diagnostics']['submission_attempts'] == 2
    assert detail['diagnostics']['last_error'] == {'code': 'SUBMISSION_SCHEMA_INVALID',
        'validation_errors': [{'field': 'card.summary', 'code': 'required'}]}
    with database.SessionLocal() as db:
        stored = db.get(PreorganizeProposal, doc['id']).result_json
        assert set(json.loads(stored)) == {'diagnostics'}
        assert len(stored) < 1500
        assert json.loads(stored)['diagnostics']['recovery_attempts'] == 1000000
        assert document_cards.status(db, doc['id'])['card'] is None
    assert 'SECRET-' not in stored + caplog.text
    assert 'SUBMISSION_SCHEMA_INVALID' in caplog.text
    assert not asyncio.run(library.process_one())


def test_success_output_diagnostics_are_filtered_and_stale_diagnostics_are_hidden(library, tmp_path, monkeypatch):
    doc = pending(tmp_path)
    async def agent(**values):
        return {**proposal_output(doc['id']), 'diagnostics': {
            'purpose': 'preorganize', 'terminal_tool': 'propose_organization',
            'outcome': 'submitted', 'model_requests': 2, 'secret': 'SECRET'}}
    monkeypatch.setattr(library, 'run_agent', agent)
    start(library, [doc])
    assert asyncio.run(library.process_one())
    detail = library.get_document(doc['id'])
    assert detail['state'] == 'ready' and detail['diagnostics']['outcome'] == 'submitted'
    assert 'SECRET' not in json.dumps(detail)
    client.put(f"/api/documents/{doc['id']}/tags", json=['人工更新'])
    detail = library.get_document(doc['id'])
    assert detail['state'] == 'waiting' and detail['diagnostics'] == {}


def test_diagnostic_only_response_stays_failed_and_retains_safe_reason(library, tmp_path, monkeypatch):
    doc = pending(tmp_path)
    async def agent(**values):
        return {'diagnostics': {'purpose': 'preorganize', 'terminal_tool': 'propose_organization',
            'outcome': 'incomplete', 'model_requests': 2, 'last_error': {
                'code': 'SUBMISSION_RULE_INVALID', 'validation_errors': [
                    {'field': 'name', 'code': 'extension_mismatch', 'input': 'SECRET'}]}}}
    monkeypatch.setattr(library, 'run_agent', agent)
    start(library, [doc])
    assert asyncio.run(library.process_one())
    result = library.get_document(doc['id'])
    assert result['state'] == 'failed' and result['proposal'] is None
    assert result['diagnostics']['last_error']['validation_errors'] == [
        {'field': 'name', 'code': 'extension_mismatch'}]
    assert 'SECRET' not in json.dumps(result)


def test_cancelled_task_cannot_restore_diagnostics_or_ready(library, tmp_path, monkeypatch):
    from backend.app.preorganize_models import PreorganizeProposal
    doc = pending(tmp_path)
    async def agent(**values):
        values['emit']('structured_diagnostic', {'purpose': 'preorganize',
            'terminal_tool': 'propose_organization', 'outcome': 'submitted', 'model_requests': 2})
        library.cancel(library.get_config()['library_token'], [doc['id']])
        return proposal_output(doc['id'])
    monkeypatch.setattr(library, 'run_agent', agent)
    start(library, [doc])
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(library.process_one())
    result = library.get_document(doc['id'])
    assert result['state'] == 'waiting' and result['diagnostics'] == {}
    with database.SessionLocal() as db:
        assert db.get(PreorganizeProposal, doc['id']).result_json is None


@pytest.mark.parametrize('change', ['service', 'metadata', 'binding'])
def test_changed_task_cannot_accept_or_keep_old_submitted_diagnostics(library, tmp_path, monkeypatch, change):
    from backend.app.core import ai_settings
    from backend.app.preorganize_models import PreorganizeProposal
    doc = pending(tmp_path)
    async def agent(**values):
        values['emit']('structured_diagnostic', {'purpose': 'preorganize',
            'terminal_tool': 'propose_organization', 'outcome': 'submitted', 'model_requests': 2})
        if change == 'service':
            monkeypatch.setattr(ai_settings, 'get_connection', lambda: (
                {'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash'}, 'new-key', 'revision-2'))
        elif change == 'metadata':
            client.put(f"/api/documents/{doc['id']}/tags", json=['人工更改'])
        else:
            original_binding = library._binding()
            monkeypatch.setattr(library, '_binding', lambda: (*original_binding[:-1], -1))
        return proposal_output(doc['id'])
    monkeypatch.setattr(library, 'run_agent', agent)
    start(library, [doc])
    assert asyncio.run(library.process_one())
    result = library.get_document(doc['id'])
    assert result['state'] != 'ready' and result['proposal'] is None and result['diagnostics'] == {}
    with database.SessionLocal() as db:
        assert db.get(PreorganizeProposal, doc['id']).result_json is None


def test_api_requires_local_capability_and_validates_bounded_batch(library):
    assert client.get('/api/ai/preorganize/config').status_code == 403
    headers = {'x-teamhub-ai': '1'}
    config = client.get('/api/ai/preorganize/config', headers=headers)
    assert config.status_code == 200, config.text
    response = client.post('/api/ai/preorganize/start', headers=headers, json={
        'library_token': config.json()['library_token'], 'service_revision': config.json()['service_revision'],
        'consent': True, 'request_id': 'too-many', 'document_ids': [str(i) for i in range(101)]})
    assert response.status_code == 422


def test_counts_filters_search_content_and_stale_metadata(library, tmp_path, monkeypatch):
    from backend.app.services import indexing
    first = pending(tmp_path, content='图纸中的继电保护试验说明')
    second = pending(tmp_path, '其他.txt', '另一份材料')
    ready(library, monkeypatch, first)
    while indexing.process_one():
        pass
    rows = library.list_documents(page_size=1)
    assert rows['total'] == 2 and len(rows['items']) == 1
    assert rows['counts'] == {'waiting': 1, 'ready': 1, 'processing': 0, 'failed': 0}
    content = library.list_documents(search='继电保护')
    assert [row['id'] for row in content['items']] == [first['id']]
    assert library.list_documents(state='ready')['total'] == 1
    client.put(f"/api/documents/{first['id']}/tags", json=['新线索'])
    assert library.list_documents(state='ready')['total'] == 0
    assert library.list_documents(search='新线索')['total'] == 1
    assert library.list_documents(state='waiting')['counts']['waiting'] == 2


def test_original_description_search_remains_available(library, tmp_path):
    doc = pending(tmp_path)
    client.put(f"/api/documents/{doc['id']}", json={'description': '西山站校验'})
    assert library.list_documents(search='西山站')['total'] == 1


def test_successful_start_request_cannot_be_reused_for_another_batch(library, tmp_path):
    first = pending(tmp_path)
    second = pending(tmp_path, '其他.txt', '其他内容')
    start(library, [first])
    with pytest.raises(HTTPException) as error:
        start(library, [second])
    assert error.value.status_code == 409
    assert library.get_document(second['id'])['state'] == 'waiting'


def test_cancel_during_read_discards_late_result_and_releases_lease(library, tmp_path, monkeypatch):
    doc = pending(tmp_path)
    entered = asyncio.Event()
    async def agent(**kwargs):
        entered.set()
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            # 模拟不能取消的下游已经返回；宿主 guard 仍必须丢弃结果。
            return proposal_output(doc['id'])
    monkeypatch.setattr(library, 'run_agent', agent)
    start(library, [doc])
    async def run():
        task = asyncio.create_task(library.process_one())
        await entered.wait()
        library.cancel(library.get_config()['library_token'], [doc['id']])
        await task
    asyncio.run(run())
    assert library.get_document(doc['id'])['state'] == 'waiting'
    assert library._paid_call.acquire(blocking=False)
    library._paid_call.release()


def test_manual_organize_during_read_wins(library, tmp_path, monkeypatch):
    doc = pending(tmp_path)
    async def agent(**kwargs):
        response = client.post(f"/api/documents/{doc['id']}/organize", json={'tags': ['人工决定']})
        assert response.status_code == 200, response.text
        return proposal_output(doc['id'])
    monkeypatch.setattr(library, 'run_agent', agent)
    start(library, [doc])
    assert asyncio.run(library.process_one())
    current = client.get(f"/api/documents/{doc['id']}").json()
    assert current['status'] == 'organized'
    assert current['name'] == doc['name']
    assert [tag['name'] for tag in current['tags']] == ['人工决定']
    assert library.list_documents()['total'] == 0


def test_service_changed_during_read_drops_result_and_pauses_policy(library, tmp_path, monkeypatch):
    from backend.app.core import ai_settings
    doc = pending(tmp_path)
    config = library.get_config()
    library.set_policy(config['library_token'], config['service_revision'], True, True)
    async def agent(**kwargs):
        monkeypatch.setattr(ai_settings, 'get_connection',
            lambda: ({'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash'}, 'new-key', 'revision-2'))
        return proposal_output(doc['id'])
    monkeypatch.setattr(library, 'run_agent', agent)
    assert asyncio.run(library.process_one())
    assert library.get_document(doc['id'])['state'] == 'failed'
    assert library.get_config()['policy']['active'] is False
    assert asyncio.run(library.process_one()) is False


def test_turning_off_auto_preserves_explicit_batch(library, tmp_path, monkeypatch):
    from backend.app.preorganize_models import PreorganizeProposal
    first = pending(tmp_path)
    second = pending(tmp_path, '其他.txt', '其他内容')
    config = library.get_config()
    library.set_policy(config['library_token'], config['service_revision'], True, True)
    library._discover()
    start(library, [second])
    library.set_policy(config['library_token'], enabled=False)
    with database.SessionLocal() as db:
        assert db.get(PreorganizeProposal, first['id']).state == 'waiting'
        assert db.get(PreorganizeProposal, second['id']).state == 'processing'
        assert db.get(PreorganizeProposal, second['id']).automatic is False


def test_chat_waiters_and_shared_lease_have_priority(library, tmp_path, monkeypatch):
    from backend.app.services import partner_runtime
    doc = pending(tmp_path)
    start(library, [doc])
    monkeypatch.setattr(partner_runtime, 'has_pending_interactive_tasks', lambda: True)
    assert asyncio.run(library.process_one()) is False
    monkeypatch.setattr(partner_runtime, 'has_pending_interactive_tasks', lambda: False)
    library._paid_call.acquire()
    try:
        assert asyncio.run(library.process_one()) is False
    finally:
        library._paid_call.release()
    assert library.get_document(doc['id'])['phase'] == 'queued'


def test_restart_recovers_queued_but_never_replays_running(library, tmp_path):
    from backend.app.preorganize_models import PreorganizeProposal
    first = pending(tmp_path)
    second = pending(tmp_path, '其他.txt', '其他内容')
    start(library, [first, second])
    with database.SessionLocal() as db:
        db.get(PreorganizeProposal, first['id']).phase = 'running'
        db.commit()
    library.initialize()
    assert library.get_document(first['id'])['state'] == 'failed'
    assert library.get_document(second['id'])['phase'] == 'queued'
    library.initialize()
    assert library.get_document(first['id'])['state'] == 'failed'


def test_library_reactivation_rejects_old_page_token(library):
    token = library.get_config()['library_token']
    database.rebind_engine()
    database.init_db()
    assert library.get_config()['library_token'] != token
    with pytest.raises(HTTPException) as error:
        library.cancel(token, ['old-doc'])
    assert error.value.status_code == 409


def test_apply_collision_rolls_back_created_tags_and_pending_state(library, tmp_path, monkeypatch):
    doc = pending(tmp_path)
    detail = ready(library, monkeypatch, doc)
    target = Path(settings.library_path) / detail['proposal']['name']
    target.write_text('不要覆盖', encoding='utf-8')
    result = apply(library, doc, detail, tags=['未提交的新标签'])
    assert result['ok'] is False
    assert target.read_text(encoding='utf-8') == '不要覆盖'
    assert Path(doc['working_path']).exists()
    from backend.app.models import Tag
    with database.SessionLocal() as db:
        assert db.query(Tag).filter_by(name='未提交的新标签').count() == 0
    assert library.get_document(doc['id'])['state'] == 'ready'


def test_apply_commit_failure_restores_working_file_and_can_retry(library, tmp_path, monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError
    doc = pending(tmp_path)
    detail = ready(library, monkeypatch, doc)
    def fail(_self):
        raise SQLAlchemyError('injected')
    with monkeypatch.context() as patch:
        patch.setattr(database.TrackedSession, 'commit', fail)
        result = apply(library, doc, detail)
        assert result['ok'] is False
    assert Path(doc['working_path']).exists()
    assert library.get_document(doc['id'])['state'] == 'ready'
    assert apply(library, doc, detail)['ok']


def test_undo_conflicts_and_commit_failure_preserve_organized_file(library, tmp_path, monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError
    doc = pending(tmp_path)
    detail = ready(library, monkeypatch, doc)
    result = apply(library, doc, detail)
    token = library.get_config()['library_token']
    Path(doc['working_path']).write_text('占用原位置', encoding='utf-8')
    with pytest.raises(HTTPException):
        library.undo(token, result['operation_id'])
    Path(doc['working_path']).unlink()
    def fail(_self):
        raise SQLAlchemyError('injected')
    with monkeypatch.context() as patch:
        patch.setattr(database.TrackedSession, 'commit', fail)
        with pytest.raises(HTTPException):
            library.undo(token, result['operation_id'])
    current = client.get(f"/api/documents/{doc['id']}").json()
    assert current['status'] == 'organized'
    assert Path(current['working_path']).exists()
    assert library.undo(token, result['operation_id'])['undone']


def test_batch_failure_does_not_block_other_items(library, tmp_path, monkeypatch):
    first = pending(tmp_path)
    first_detail = ready(library, monkeypatch, first)
    second = pending(tmp_path, '其他.txt', '其他内容')
    async def agent(**kwargs):
        return proposal_output(second['id'], name='其他整理.txt')
    monkeypatch.setattr(library, 'run_agent', agent)
    start(library, [second], 'second')
    assert asyncio.run(library.process_one())
    second_detail = library.get_document(second['id'])
    response = library.apply(library.get_config()['library_token'], [
        {'document_id': first['id'], 'proposal_id': first_detail['proposal_id'], 'request_id': 'bad',
         'name': '../逃逸.txt', 'tags': ['检修'], 'target_dir': ''},
        {'document_id': second['id'], 'proposal_id': second_detail['proposal_id'], 'request_id': 'good',
         'name': '其他整理.txt', 'tags': ['检修'], 'target_dir': ''}])
    assert [item['ok'] for item in response['items']] == [False, True]
    assert library.get_document(first['id'])['state'] == 'ready'


@pytest.mark.parametrize('change', [
    {'name': '../逃逸.txt'}, {'name': '更改格式.pdf'}, {'target_dir': '../'},
    {'target_dir': '待整理'}, {'target_dir': '.teamhub'}, {'target_dir': '不存在'},
])
def test_apply_rejects_unsafe_or_changed_paths(library, tmp_path, monkeypatch, change):
    doc = pending(tmp_path)
    detail = ready(library, monkeypatch, doc)
    assert apply(library, doc, detail, **change)['ok'] is False
    assert Path(doc['working_path']).exists()


def test_auto_folder_is_queued_and_can_finish_without_modifying_children(library, tmp_path, monkeypatch):
    source = tmp_path / '整体资料'
    source.mkdir()
    (source / '说明.txt').write_text('文件夹内容', encoding='utf-8')
    response = client.post('/api/documents/quick-add', json={'source_path': str(source)})
    doc = response.json()
    calls = []
    async def agent(**values):
        calls.append(values)
        assert values['scope'][doc['id']]['is_folder'] is True
        return proposal_output(doc['id'], name='项目资料')
    monkeypatch.setattr(library, 'run_agent', agent)
    config = library.get_config()
    library.set_policy(config['library_token'], config['service_revision'], True, True)
    assert asyncio.run(library.process_one())
    assert library.get_document(doc['id'])['state'] == 'ready'
    assert len(calls) == 1
    assert (Path(doc['working_path']) / '说明.txt').read_text() == '文件夹内容'
    assert asyncio.run(library.process_one()) is False


def test_catalog_excludes_managed_folders_hidden_pending_and_system_labels(library, tmp_path):
    from backend.app.models import Tag
    root = Path(settings.library_path)
    (root / '工程' / '试验').mkdir(parents=True)
    (root / '.隐藏').mkdir()
    with database.SessionLocal() as db:
        db.add_all([Tag(name='待整理'), Tag(name='设备')])
        db.commit()
        catalog = library._catalog(db)
    assert catalog == {'tags': ['设备'], 'folders': ['工程', '工程/试验'], 'limited': False}


@pytest.mark.parametrize('delete', [False, True])
def test_undo_does_not_recreate_removed_or_renamed_original_tag(library, tmp_path, monkeypatch, delete):
    from backend.app.models import Tag
    doc = pending(tmp_path)
    client.put(f"/api/documents/{doc['id']}/tags", json=['原始标签'])
    detail = ready(library, monkeypatch, doc)
    result = apply(library, doc, detail)
    with database.SessionLocal() as db:
        tag = db.query(Tag).filter_by(name='原始标签').one()
        if delete:
            db.delete(tag)
        else:
            tag.name = '修改后的标签'
        db.commit()
    with pytest.raises(HTTPException) as error:
        library.undo(library.get_config()['library_token'], result['operation_id'])
    assert error.value.status_code == 409
    with database.SessionLocal() as db:
        assert db.query(Tag).filter_by(name='原始标签').count() == 0


def test_api_end_to_end_batch_review_and_undo(library, tmp_path, monkeypatch):
    doc = pending(tmp_path)
    headers = {'x-teamhub-ai': '1'}
    base = '/api/ai/preorganize'
    config = client.get(base + '/config', headers=headers).json()
    async def agent(**kwargs):
        return proposal_output(doc['id'])
    monkeypatch.setattr(library, 'run_agent', agent)
    payload = {'library_token': config['library_token'], 'service_revision': config['service_revision'],
               'consent': True, 'request_id': 'api-start', 'document_ids': [doc['id']]}
    response = client.post(base + '/start', headers=headers, json=payload)
    assert response.status_code == 200, response.text
    assert asyncio.run(library.process_one())
    listing = client.get(base + '/documents?state=ready&page=1&page_size=2', headers=headers)
    assert listing.status_code == 200 and listing.json()['total'] == 1
    detail = client.get(base + f"/documents/{doc['id']}", headers=headers).json()
    response = client.post(base + '/apply', headers=headers, json={'library_token': config['library_token'],
        'items': [{'document_id': doc['id'], 'proposal_id': detail['proposal_id'], 'request_id': 'api-apply',
                   **{key: detail['proposal'][key] for key in ('name', 'tags', 'target_dir')}}]})
    assert response.status_code == 200 and response.json()['items'][0]['ok']
    history = client.get(base + '/operations', headers=headers).json()['items']
    assert len(history) == 1
    assert client.post(base + f"/operations/{history[0]['id']}/undo", headers=headers,
                       json={'library_token': config['library_token']}).json()['undone']
    assert client.post(base + '/cancel', headers=headers,
                       json={'library_token': config['library_token'], 'document_ids': [doc['id']]}).status_code == 200
    assert client.put(base + '/policy', headers=headers,
        json={'library_token': config['library_token'], 'enabled': False}).status_code == 200


def test_worker_cancellation_keeps_queue_available_and_shutdown_finishes(library, tmp_path, monkeypatch):
    doc = pending(tmp_path)
    entered = asyncio.Event()
    async def agent(**kwargs):
        entered.set()
        await asyncio.sleep(60)
    monkeypatch.setattr(library, 'run_agent', agent)
    start(library, [doc])
    async def run():
        library.start_worker()
        task = library._worker
        library.start_worker()
        assert task is library._worker
        await asyncio.wait_for(entered.wait(), timeout=3)
        library.cancel(library.get_config()['library_token'], [doc['id']])
        for _ in range(50):
            if library._running is None:
                break
            await asyncio.sleep(.01)
        assert not library._worker.done()
        await library.stop_worker()
        assert library._worker is None
    asyncio.run(run())
    assert library.get_document(doc['id'])['state'] == 'waiting'
    assert library._paid_call.acquire(blocking=False)
    library._paid_call.release()


def test_library_switch_during_model_execution_cannot_write_to_new_library(library, tmp_path, monkeypatch):
    doc = pending(tmp_path)
    first_root = settings.library_path
    async def agent(**kwargs):
        settings.library_path = str(tmp_path / 'second-library')
        database.rebind_engine()
        database.init_db()
        # 切库的恢复器取消当前任务，模拟已经交付的模型结果仍需丢弃。
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            pass
        return proposal_output(doc['id'])
    monkeypatch.setattr(library, 'run_agent', agent)
    start(library, [doc])
    asyncio.run(library.process_one())
    assert library.list_documents()['total'] == 0
    settings.library_path = first_root
    database.rebind_engine()
    database.init_db()
    assert library.get_document(doc['id'])['state'] == 'failed'


def test_explicit_reenable_resumes_auto_disabled_queue_but_not_user_cancelled(library, tmp_path):
    first = pending(tmp_path)
    second = pending(tmp_path, '其他.txt', '其他内容')
    config = library.get_config()
    library.set_policy(config['library_token'], config['service_revision'], True, True)
    library._discover()
    library.cancel(config['library_token'], [first['id']])
    library.set_policy(config['library_token'], enabled=False)
    assert library.get_document(second['id'])['state'] == 'waiting'
    library.set_policy(config['library_token'], config['service_revision'], True, True)
    library._discover()
    assert library.get_document(second['id'])['phase'] == 'queued'
    assert library.get_document(first['id'])['state'] == 'waiting'


def test_disabling_auto_does_not_cancel_running_job_adopted_by_explicit_start(library, tmp_path, monkeypatch):
    doc = pending(tmp_path)
    config = library.get_config()
    library.set_policy(config['library_token'], config['service_revision'], True, True)
    entered, finish = asyncio.Event(), asyncio.Event()
    async def agent(**kwargs):
        entered.set()
        await finish.wait()
        return proposal_output(doc['id'])
    monkeypatch.setattr(library, 'run_agent', agent)
    async def run():
        task = asyncio.create_task(library.process_one())
        await asyncio.wait_for(entered.wait(), timeout=3)
        start(library, [doc], 'explicitly-take-over-running-job')
        library.set_policy(config['library_token'], enabled=False)
        finish.set()
        try:
            await task
        except asyncio.CancelledError:
            pytest.fail('关闭自动策略错误取消了用户另行启动的任务')
    asyncio.run(run())
    assert library.get_document(doc['id'])['state'] == 'ready'


@pytest.mark.parametrize('clear_key', [False, True])
def test_service_change_at_commit_boundary_rejects_late_result(library, tmp_path, monkeypatch, clear_key):
    from backend.app.core import ai_settings
    doc = pending(tmp_path)
    async def agent(**kwargs):
        return proposal_output(doc['id'])
    monkeypatch.setattr(library, 'run_agent', agent)
    original_finish = library._finish
    def commit_after_config_change(job, result=None, error=None, diagnostics=None):
        if result is not None:
            def changed_connection():
                if clear_key:
                    raise ai_settings.AISettingsError('请先配置 AI 服务密钥')
                return {'base_url': 'https://api.deepseek.com', 'model': 'another-model'}, 'new-key', 'revision-2'
            monkeypatch.setattr(ai_settings, 'get_connection', changed_connection)
        return original_finish(job, result, error, diagnostics=diagnostics)
    monkeypatch.setattr(library, '_finish', commit_after_config_change)
    start(library, [doc])
    assert asyncio.run(library.process_one())
    detail = library.get_document(doc['id'])
    assert detail['state'] == 'failed'
    assert detail['proposal'] is None
    assert '配置' in detail['error']


def test_already_ready_proposal_remains_applicable_after_service_change(library, tmp_path, monkeypatch):
    from backend.app.core import ai_settings
    doc = pending(tmp_path)
    detail = ready(library, monkeypatch, doc)
    monkeypatch.setattr(ai_settings, 'get_connection',
        lambda: ({'base_url': 'https://api.deepseek.com', 'model': 'another-model'}, 'new-key', 'revision-2'))
    assert library.get_document(doc['id'])['state'] == 'ready'
    assert apply(library, doc, detail)['ok']
