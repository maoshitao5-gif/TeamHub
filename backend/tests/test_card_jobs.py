"""资料卡后台任务：隔离资料库与模拟模型，不发送真实资料。"""
import asyncio
import json

import pytest
from fastapi import HTTPException

from backend.app import database
from backend.app.models import Document, Version
from backend.app.services import versions
from backend.tests.test_partner_tools import library, saved  # noqa: F401
from backend.tests.test_partner_runtime import configure


def service(monkeypatch):
    from backend.app.services import card_jobs
    configure(monkeypatch)
    return card_jobs


def start(jobs, document_id, request_id='generate-1', **values):
    config = jobs.get_config()
    return jobs.start(document_id, config['library_token'], config['service_revision'],
                      True, request_id, **values)


def output(document_id, meta):
    return {'card': {'summary': '设备检修说明', 'document_type': '说明', 'topics': ['设备检修'],
                    'sections': [], 'facts': [], 'search_terms': ['设备维护手册'],
                    'uses': ['查找设备维护说明'], 'gaps': [], 'source_ids': ['S1']},
            'sources': [{'id': 'S1', 'source_id': 'S1', 'document_id': document_id,
                         'version_id': meta['version_id'], 'content_hash': meta['content_hash'],
                         'version_number': meta['version_number'], 'name': meta['name'],
                         'location': {'line': 1}, 'label': '第1行', 'excerpt': '设备检修说明'}],
            'coverage': {'read_units': 1, 'total_units': 1, 'visual_pages': 0,
                         'total_visual_pages': 0, 'embedded_images': 0,
                         'total_embedded_images': 0, 'complete': True, 'gaps': []},
            'usage': {'model_requests': 1}, 'progress': {'read_units': 1}}


def new_version(root, document_id, text):
    with database.SessionLocal() as db:
        doc = db.get(Document, document_id)
        (root / doc.storage_path).write_text(text)
        version, _ = versions.snapshot_working_copy(db, doc)
        db.commit()
        return version.id


def test_generation_requires_scope_and_explicit_authorization(library, monkeypatch):
    jobs = service(monkeypatch)
    doc, _ = saved(library, '说明.txt', '设备检修说明'.encode())
    config = jobs.get_config()
    for token, revision, consent in [('old', config['service_revision'], True),
                                     (config['library_token'], 'old', True),
                                     (config['library_token'], config['service_revision'], False)]:
        with pytest.raises(HTTPException):
            jobs.start(doc, token, revision, consent, 'invalid')
    assert jobs.get_job_status(doc)['job']['state'] == 'waiting'


def test_generate_preserves_document_and_retries_are_idempotent(library, monkeypatch):
    jobs = service(monkeypatch)
    doc, meta = saved(library, '说明.txt', '设备检修说明'.encode())
    calls = []
    async def agent(**values):
        assert database.active_session_count() == 0
        assert values['purpose'] == 'card'
        values['guard']()
        calls.append(values)
        return output(doc, meta)
    monkeypatch.setattr(jobs, 'run_agent', agent)
    start(jobs, doc)
    assert asyncio.run(jobs.process_one())
    assert jobs.get_job_status(doc)['job']['state'] == 'ready'
    start(jobs, doc)
    assert not asyncio.run(jobs.process_one())
    from backend.app.services import document_cards
    with database.SessionLocal() as db:
        result = document_cards.status(db, doc)
        assert result['card']['content']['summary'] == '设备检修说明'
        assert db.get(Document, doc).status == 'pending'
        assert db.query(Version).filter_by(document_id=doc).count() == 1
    assert len(calls) == 1


def test_failed_task_not_automatically_replayed(library, monkeypatch):
    jobs = service(monkeypatch)
    doc, _ = saved(library, '说明.txt', b'content')
    calls = []
    async def agent(**values):
        calls.append(values)
        raise RuntimeError('private path/key must not appear')
    monkeypatch.setattr(jobs, 'run_agent', agent)
    start(jobs, doc, auto_update=True)
    assert asyncio.run(jobs.process_one())
    assert not asyncio.run(jobs.process_one())
    result = jobs.get_job_status(doc)
    assert result['job']['state'] == 'failed'
    assert 'private' not in json.dumps(result)
    assert len(calls) == 1


def test_card_failure_logs_only_controlled_diagnostics_without_saving_card(library, monkeypatch, caplog):
    from backend.app.core.ai_client import AIError
    from backend.app.services import document_cards
    jobs = service(monkeypatch)
    doc, _ = saved(library, '说明.txt', b'content')
    async def agent(**values):
        values['emit']('answer', 'SECRET-ANSWER')
        values['emit']('structured_diagnostic', {
            'purpose': 'card', 'terminal_tool': 'submit_document_card', 'outcome': 'incomplete',
            'model_requests': 3, 'tool_calls': 4, 'submission_attempts': 2,
            'last_error': {'code': 'SUBMISSION_EVIDENCE_INVALID', 'secret': 'SECRET-INPUT',
                'validation_errors': [{'field': 'source_ids', 'code': 'source_unavailable',
                                       'message': 'SECRET-ERROR'}]}})
        raise AIError(502, 'AI_CARD_INCOMPLETE', 'AI 未提交有效资料卡')
    monkeypatch.setattr(jobs, 'run_agent', agent)
    start(jobs, doc)
    assert asyncio.run(jobs.process_one())
    assert jobs.get_job_status(doc)['job']['state'] == 'failed'
    assert 'SUBMISSION_EVIDENCE_INVALID' in caplog.text
    assert '"submission_attempts":2' in caplog.text
    assert 'SECRET-' not in caplog.text
    with database.SessionLocal() as db:
        assert document_cards.status(db, doc)['card'] is None


def test_card_diagnostic_only_result_cannot_be_marked_ready(library, monkeypatch):
    from backend.app.services import document_cards
    jobs = service(monkeypatch)
    doc, _ = saved(library, '说明.txt', b'content')
    async def agent(**values):
        return {'diagnostics': {'purpose': 'card', 'terminal_tool': 'submit_document_card',
                                'outcome': 'submitted', 'model_requests': 2}}
    monkeypatch.setattr(jobs, 'run_agent', agent)
    start(jobs, doc)
    assert asyncio.run(jobs.process_one())
    assert jobs.get_job_status(doc)['job']['state'] == 'failed'
    with database.SessionLocal() as db:
        assert document_cards.status(db, doc)['card'] is None


def test_changed_version_rejects_late_result(library, monkeypatch):
    jobs = service(monkeypatch)
    doc, meta = saved(library, '说明.txt', b'content')
    async def agent(**values):
        new_version(library, doc, 'new content')
        return output(doc, meta)
    monkeypatch.setattr(jobs, 'run_agent', agent)
    start(jobs, doc)
    assert asyncio.run(jobs.process_one())
    from backend.app.services import document_cards
    with database.SessionLocal() as db:
        assert document_cards.status(db, doc)['card'] is None


def test_auto_update_only_new_content_and_config_changes_pause(library, monkeypatch):
    jobs = service(monkeypatch)
    doc, meta = saved(library, '说明.txt', b'content')
    calls = []
    async def agent(**values):
        calls.append(values)
        return output(doc, values['scope'][doc])
    monkeypatch.setattr(jobs, 'run_agent', agent)
    start(jobs, doc, auto_update=True)
    assert asyncio.run(jobs.process_one())
    assert jobs.get_job_status(doc)['job']['state'] == 'ready'
    with database.SessionLocal() as db:
        db.get(Document, doc).name = '改名.txt'
        db.commit()
    assert not asyncio.run(jobs.process_one())
    new_version(library, doc, 'new content')
    assert asyncio.run(jobs.process_one())
    assert jobs.get_job_status(doc)['job']['state'] == 'ready'
    assert len(calls) == 2
    from backend.app.core import ai_settings
    monkeypatch.setattr(ai_settings, 'get_connection', lambda: (
        {'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash'}, 'new-key', 'revision-2'))
    new_version(library, doc, 'third content')
    assert not asyncio.run(jobs.process_one())
    assert not jobs.get_job_status(doc)['policy']['active']
    assert len(calls) == 2


def test_cancel_queue_stops_auto_replay(library, monkeypatch):
    jobs = service(monkeypatch)
    doc, _ = saved(library, '说明.txt', b'content')
    start(jobs, doc, auto_update=True)
    jobs.cancel(doc, jobs.get_config()['library_token'])
    assert not asyncio.run(jobs.process_one())
    assert jobs.get_job_status(doc)['job']['state'] == 'waiting'


def test_organized_document_can_generate_without_reorganizing(library, monkeypatch):
    jobs = service(monkeypatch)
    doc, meta = saved(library, '说明.txt', b'content')
    with database.SessionLocal() as db:
        db.get(Document, doc).status = 'organized'
        db.commit()
    async def agent(**values):
        return output(doc, meta)
    monkeypatch.setattr(jobs, 'run_agent', agent)
    start(jobs, doc)
    assert asyncio.run(jobs.process_one())
    with database.SessionLocal() as db:
        assert db.get(Document, doc).status == 'organized'


def test_same_version_rule_upgrade_can_replace_stale_card(library, monkeypatch):
    jobs = service(monkeypatch)
    doc, meta = saved(library, '说明.txt', b'content')
    async def agent(**values):
        return output(doc, meta)
    monkeypatch.setattr(jobs, 'run_agent', agent)
    start(jobs, doc)
    assert asyncio.run(jobs.process_one())
    from backend.app.services import document_cards
    monkeypatch.setattr(document_cards, 'SKILL_VERSION', 'test-next')
    with database.SessionLocal() as db:
        assert document_cards.status(db, doc)['state'] == 'stale'
    start(jobs, doc, request_id='new-rule')
    assert asyncio.run(jobs.process_one())
    assert jobs.get_job_status(doc)['job']['state'] == 'ready'
    with database.SessionLocal() as db:
        card = document_cards.status(db, doc)['card']
        assert card['skill_version'] == 'test-next' and card['revision'] == 2


def test_completion_receives_existing_navigation_and_independent_annotation(library, monkeypatch):
    from backend.app.services import document_cards
    jobs = service(monkeypatch)
    doc, meta = saved(library, '说明.txt', b'content')
    result = output(doc, meta)
    result['card']['facts'] = [{'label': '设备', 'value': '断路器', 'source_ids': ['S1']}]
    with database.SessionLocal() as db:
        document_cards.save_card(db, doc, meta['version_id'], meta['content_hash'],
            result['card'], result['sources'], result['coverage'], 'test', commit=True)
        document_cards.save_annotation(db, doc, meta['version_id'], 0, '请核对附录', commit=True)
    calls = []
    async def agent(**values):
        calls.append(values['purpose_context'])
        return output(doc, values['scope'][doc])
    monkeypatch.setattr(jobs, 'run_agent', agent)
    start(jobs, doc)
    assert asyncio.run(jobs.process_one())
    context = calls[0]
    assert context['existing_card']['same_version'] is True
    assert context['existing_card']['content']['facts'][0]['evidence_locations']
    assert 'source_ids' not in json.dumps(context)
    assert context['annotation']['origin'] == 'user_annotation'
    assert context['annotation']['text'] == '请核对附录'
    assert context['annotation']['needs_review'] is False
    new_version(library, doc, 'changed')
    start(jobs, doc, 'new-version')
    assert asyncio.run(jobs.process_one())
    assert calls[1]['existing_card']['same_version'] is False
    assert calls[1]['annotation']['needs_review'] is True


def test_lower_coverage_retains_card_and_explains_why(library, monkeypatch):
    jobs = service(monkeypatch)
    doc, meta = saved(library, '说明.txt', b'content')
    result = output(doc, meta)
    async def agent(**values):
        return result
    monkeypatch.setattr(jobs, 'run_agent', agent)
    start(jobs, doc)
    assert asyncio.run(jobs.process_one())
    result['coverage'].update(complete=False, total_units=2, gaps=['仍有未读内容'])
    start(jobs, doc, 'retry-partial')
    assert asyncio.run(jobs.process_one())
    assert '已保留原资料卡' in jobs.get_job_status(doc)['job']['error']
    from backend.app.services import document_cards
    with database.SessionLocal() as db:
        assert document_cards.status(db, doc)['card']['revision'] == 1
