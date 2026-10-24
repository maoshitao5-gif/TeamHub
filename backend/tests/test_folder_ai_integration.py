"""Folder understanding travels through the real bridge, card storage and jobs."""
import asyncio
import json

import httpx2
import pytest

from backend.app import database
from backend.app.models import Document, Version
from backend.app.services import card_jobs, document_cards, versions
from backend.tests.test_folder_reader import saved_folder
from backend.tests.test_partner_tools import library  # noqa: F401
from backend.tests.test_organization_tools import bridge_for, invoke, proposal
from backend.tests.test_card_jobs import service, start
from backend.tests.test_hermes_real_engine import ROOT, current_worker_source, response, values  # noqa: F401


def folder_result(root, doc_id, meta, purpose='card'):
    from backend.app.services.card_tools import CardTools
    bridge = (CardTools(engine=database.engine, root=str(root), scope={doc_id: meta},
        guard=lambda: None, emit=lambda *_: None, purpose_context={'document_id': doc_id})
        if purpose == 'card' else bridge_for(root, doc_id, meta))
    bridge.preflight()
    invoke(bridge, 'list_folder_members', {'document_id': doc_id})
    read = invoke(bridge, 'read_folder_member', {'document_id': doc_id,
        'member_path': '设计/说明.txt', 'action': 'text'})
    assert read['units']
    content = proposal(name='整套设计资料', target_dir='')['card']
    content.update(summary='设计阶段设备检查资料', search_terms=['成套资料检索词XYZ'],
                   sections=[{'title': '设计/说明.txt', 'summary': '设备检查说明', 'source_ids': ['S1']}])
    if purpose == 'card':
        assert invoke(bridge, 'submit_document_card', content)['ok']
    else:
        assert invoke(bridge, 'propose_organization', proposal(
            name='整套设计资料', target_dir='', card=content))['ok']
    return {'card': bridge.card, 'proposal': bridge.proposal, 'sources': bridge.tools.sources,
            'coverage': bridge.coverage(), 'progress': bridge.tools.progress, 'usage': {}}


def test_folder_card_persists_member_locations_search_and_coverage(library):
    doc, meta = saved_folder(library, {'设计/说明.txt': '设备检查说明'.encode(),
        '未阅读.txt': '后续验收资料'.encode()}, name='项目.v1')
    result = folder_result(library, doc, meta, 'preorganize')
    assert result['proposal']['name'] == '整套设计资料'  # Folder dots are not extensions.
    with database.SessionLocal() as db:
        saved = document_cards.save_card(db, doc, meta['version_id'], meta['content_hash'],
            result['card'], result['sources'], result['coverage'], 'synthetic', commit=True)
        assert saved['state'] == 'partial'
        assert saved['card']['coverage']['read_members'] == 1
        assert saved['card']['coverage']['total_members'] == 2
        assert saved['card']['sources'][0]['location']['member_path'] == '设计/说明.txt'
        assert db.get(Document, doc).status == 'pending'
        assert db.query(Document).count() == 1
        assert db.query(Version).count() == 1
    from backend.app.services.partner_tools import PartnerTools
    tools = PartnerTools(engine=database.engine, root=str(library), scope={doc: meta}, guard=lambda: None)
    assert tools.search_documents('成套资料检索词XYZ')['documents'][0]['document_id'] == doc


def test_card_folder_auto_refresh_and_restore_reuses_version(library, monkeypatch):
    jobs = service(monkeypatch)
    doc, meta = saved_folder(library, {'设计/说明.txt': '设备检查说明'.encode()})
    calls = []
    async def agent(**kwargs):
        calls.append(kwargs)
        return await asyncio.to_thread(folder_result, library, doc, kwargs['scope'][doc])
    monkeypatch.setattr(jobs, 'run_agent', agent)
    start(jobs, doc, auto_update=True)
    assert asyncio.run(jobs.process_one())
    assert jobs.get_job_status(doc)['job']['state'] == 'ready'
    with database.SessionLocal() as db:
        record = db.get(Document, doc)
        (library / record.storage_path / '设计/说明.txt').write_text('新的设备检查说明')
        second, _ = versions.snapshot_working_copy(db, record)
        db.commit()
        second_id = second.id
    assert asyncio.run(jobs.process_one())
    assert len(calls) == 2
    with database.SessionLocal() as db:
        assert document_cards.status(db, doc)['version_id'] == second_id
        db.get(Document, doc).current_version_id = meta['version_id']
        db.commit()
    assert not asyncio.run(jobs.process_one())  # Reuses the stored card without a model call.
    with database.SessionLocal() as db:
        assert document_cards.status(db, doc)['card']['version_id'] == meta['version_id']
    assert len(calls) == 2


@pytest.mark.skipif(not (ROOT / '.mvp-sandbox/hermes-venv/bin/python').exists(),
                    reason='Requires local real Hermes engine')
@pytest.mark.parametrize('purpose', ['card', 'preorganize'])
def test_real_hermes_selects_members_and_submits_aggregate(library, purpose):
    from backend.app.core.hermes_agent import run_agent
    doc, meta = saved_folder(library, {'设计/说明.txt': '新员工培训周期为两周。'.encode(),
                                      '其他.txt': b'not-read'})
    calls = []
    def provider(request):
        body = json.loads(request.content)
        calls.append(body)
        names = {tool['function']['name'] for tool in body['tools']}
        assert names == {'list_folder_members', 'read_folder_member', 'read_skill_reference', 'read_object_skill',
                         'submit_document_card' if purpose == 'card' else 'propose_organization'}
        assert str(library) not in request.content.decode()
        if len(calls) == 1:
            return response(body, tool='list_folder_members', arguments={'document_id': doc})
        if len(calls) == 2:
            assert '设计/说明.txt' in request.content.decode()
            return response(body, tool='read_folder_member', arguments={
                'document_id': doc, 'member_path': '设计/说明.txt', 'action': 'text'})
        assert len(calls) == 3 and '两周' in request.content.decode()
        result = proposal(name='培训资料', target_dir='')
        return response(body, tool='submit_document_card' if purpose == 'card' else 'propose_organization',
                        arguments=result['card'] if purpose == 'card' else result)
    context = {'document_id': doc}
    if purpose == 'preorganize':
        context.update(tags=[], folders=[])
    result = asyncio.run(run_agent(**{**values(library, {doc: meta}, []), 'purpose': purpose,
        'purpose_context': context, 'transport_override': httpx2.MockTransport(provider)}))
    assert result['card']['summary']
    assert result['coverage']['read_members'] == 1
    assert result['coverage']['total_members'] == 2
    assert not result['coverage']['complete']
    assert result['sources'][0]['location']['member_path'] == '设计/说明.txt'
    assert result['usage']['model_requests'] == 3


@pytest.mark.parametrize('path', ['/etc/passwd', '../secret', 'a/../secret', 'a\\b', 'a\x00b'])
def test_card_storage_rejects_unsafe_member_source(library, path):
    doc, meta = saved_folder(library, {'设计/说明.txt': b'content'})
    result = folder_result(library, doc, meta)
    result['sources'][0]['location']['member_path'] = path
    with database.SessionLocal() as db, pytest.raises(ValueError):
        document_cards.save_card(db, doc, meta['version_id'], meta['content_hash'], result['card'],
                                 result['sources'], result['coverage'], 'synthetic')


def test_repeated_member_read_is_suppressed_but_another_member_can_progress(library, monkeypatch):
    doc, meta = saved_folder(library, {'a.txt': b'first', 'b.txt': b'second'})
    bridge = bridge_for(library, doc, meta)
    args = {'document_id': doc, 'member_path': 'a.txt', 'action': 'text'}
    invoke(bridge, 'read_folder_member', args)
    for _ in range(2):
        invoke(bridge, 'read_folder_member', args)
    result = invoke(bridge, 'read_folder_member', args)
    assert result['code'] == 'NO_NEW_EVIDENCE'
    assert invoke(bridge, 'read_folder_member', args)['code'] == 'USE_EXISTING_EVIDENCE'
    assert invoke(bridge, 'read_folder_member', {**args, 'member_path': 'b.txt'})['units']
    assert bridge.coverage()['read_members'] == 2


def test_folder_card_requires_member_provenance_and_matching_coverage(library):
    from copy import deepcopy
    doc, meta = saved_folder(library, {'设计/说明.txt': b'content'})
    original = folder_result(library, doc, meta)
    for change in ('no_path', 'no_member_counts', 'wrong_member_count'):
        result = deepcopy(original)
        if change == 'no_path':
            result['sources'][0]['location'].pop('member_path')
        elif change == 'no_member_counts':
            result['coverage'].pop('read_members')
            result['coverage'].pop('total_members')
        else:
            result['coverage']['read_members'] = 0
        with database.SessionLocal() as db, pytest.raises(ValueError):
            document_cards.save_card(db, doc, meta['version_id'], meta['content_hash'],
                result['card'], result['sources'], result['coverage'], 'synthetic')


@pytest.mark.skipif(not (ROOT / '.mvp-sandbox/hermes-venv/bin/python').exists(),
                    reason='Requires local real Hermes engine')
@pytest.mark.parametrize('purpose', ['card', 'preorganize'])
def test_folder_budget_pause_retains_last_complete_coverage(library, purpose):
    from backend.app.core.hermes_agent import run_agent
    from backend.app.core.partner_budget import PartnerPaused
    doc, meta = saved_folder(library, {'设计/说明.txt': b'content'})
    context = {'document_id': doc}
    if purpose == 'preorganize':
        context.update(tags=[], folders=[])
    def provider(request):
        return response(json.loads(request.content), tool='read_folder_member', arguments={
            'document_id': doc, 'member_path': '设计/说明.txt', 'action': 'text'})
    with pytest.raises(PartnerPaused) as error:
        asyncio.run(run_agent(**{**values(library, {doc: meta}, []), 'purpose': purpose,
            'purpose_context': context, 'limits': {'model_requests': 1},
            'transport_override': httpx2.MockTransport(provider)}))
    assert error.value.reason == 'model_requests'
    assert error.value.output['coverage']['read_members'] == 1
    assert error.value.output['sources'][0]['location']['member_path'] == '设计/说明.txt'
    from backend.app.services.preorganize import _error_message
    from backend.app.services.card_jobs import _error
    assert '已有资料卡保持不变' in (_error_message(error.value) if purpose == 'preorganize' else _error(error.value))
