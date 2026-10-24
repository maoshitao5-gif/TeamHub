"""同一次预整理的理解成果贯穿入库、检索和伙伴阅读。"""
import asyncio

from backend.app import database
from backend.app.services import indexing
from backend.tests.test_preorganize import library, pending, start, apply, proposal_output  # noqa: F401
from backend.tests.test_card_jobs import output
from backend.tests.test_partner_tools import toolset


def test_preorganization_card_survives_apply_and_is_searchable(library, tmp_path, monkeypatch):
    doc = pending(tmp_path)
    async def agent(**kwargs):
        result = proposal_output(doc['id'])
        card_result = output(doc['id'], kwargs['scope'][doc['id']])
        result.update(card=card_result['card'], coverage=card_result['coverage'], sources=card_result['sources'])
        return result
    monkeypatch.setattr(library, 'run_agent', agent)
    start(library, [doc])
    assert asyncio.run(library.process_one())
    from backend.app.services import document_cards
    with database.SessionLocal() as db:
        before = document_cards.status(db, doc['id'])
        assert before['card']['content']['summary'] == '设备检修说明'
        assert doc['id'] in db.execute(indexing.content_document_ids('设备维护手册')).scalars().all()
    # 再次预整理也获得既有理解，不能把它当作本轮原文证据。
    retry_context = {}
    original_agent = agent
    async def retry_agent(**kwargs):
        retry_context.update(kwargs['purpose_context'])
        return await original_agent(**kwargs)
    monkeypatch.setattr(library, 'run_agent', retry_agent)
    library.cancel(library.get_config()['library_token'], [doc['id']])
    start(library, [doc], request_id='retry-with-card')
    assert asyncio.run(library.process_one())
    assert retry_context['existing_card']['content']['summary'] == '设备检修说明'
    assert 'source_ids' not in str(retry_context)
    with database.SessionLocal() as db:
        before = document_cards.status(db, doc['id'])
    detail = library.get_document(doc['id'])
    assert apply(library, doc, detail)['ok']
    with database.SessionLocal() as db:
        after = document_cards.status(db, doc['id'])
        assert after['card']['revision'] == before['card']['revision']
        assert doc['id'] in db.execute(indexing.content_document_ids('设备维护手册')).scalars().all()


def test_partner_card_is_derived_not_raw_evidence(library, tmp_path, monkeypatch):
    doc = pending(tmp_path)
    captured = {}
    async def agent(**kwargs):
        captured.update(kwargs['scope'])
        result = proposal_output(doc['id'])
        data = output(doc['id'], kwargs['scope'][doc['id']])
        result.update(card=data['card'], sources=data['sources'], coverage=data['coverage'])
        return result
    monkeypatch.setattr(library, 'run_agent', agent)
    start(library, [doc])
    assert asyncio.run(library.process_one())
    from backend.app.config import settings
    from pathlib import Path
    tools = toolset(Path(settings.library_path), captured)
    result = tools.read_document_card(doc['id'])
    assert result['origin'] == 'ai_derived'
    assert result['card']['summary'] == '设备检修说明'
    assert tools.sources == []
    assert doc['id'] in tools.exposed_document_ids
    restored = toolset(Path(settings.library_path), captured)
    restored.restore_checkpoint(tools.export_checkpoint())
    assert restored.sources == []
    candidates = tools.search_documents('设备维护手册')
    assert candidates['documents'][0]['card_summary'] == '设备检修说明'
    assert candidates['documents'][0]['card_match'] is True
