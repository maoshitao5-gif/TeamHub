"""A durable card is grounded in this version's actual sources."""
from copy import deepcopy

import pytest

from backend.tests.test_partner_tools import library, saved  # noqa: F401
from backend.tests.test_organization_tools import bridge_for, invoke, proposal


def card(**changes):
    return {'summary': '新员工培训周期为两周。', 'document_type': '培训说明',
        'topics': ['新员工培训'], 'sections': [{'title': '培训安排',
            'summary': '培训周期与对象。', 'source_ids': ['S1']}],
        'facts': [{'label': '培训周期', 'value': '两周', 'source_ids': ['S1']}],
        'search_terms': ['入职培训周期'], 'uses': ['可用于了解培训安排'],
        'gaps': [], 'source_ids': ['S1'], **changes}


def card_bridge(library, doc_id, meta, **context):
    from backend.app import database
    from backend.app.services.card_tools import CardTools
    return CardTools(engine=database.engine, root=str(library), scope={doc_id: meta},
        guard=lambda: None, emit=lambda *_: None,
        purpose_context={'document_id': doc_id, **context})


def test_preorganize_submits_card_in_same_tool_call_with_host_coverage(library):
    doc_id, meta = saved(library, '培训.txt', '\n'.join(f'第{i}条培训说明' for i in range(4)).encode())
    bridge = bridge_for(library, doc_id, meta)
    invoke(bridge, 'read_document', {'document_id': doc_id, 'limit': 1})
    assert invoke(bridge, 'propose_organization', proposal(card=card()))['ok'] is True
    assert bridge.card['summary'] == card()['summary']
    assert bridge.coverage()['read_units'] == 1
    assert bridge.coverage()['total_units'] == 4
    assert bridge.coverage()['complete'] is False
    assert any('1 / 4' in gap for gap in bridge.card['gaps'])


@pytest.mark.parametrize('payload', [
    card(source_ids=['S99']),
    card(facts=[{'label': '周期', 'value': '两周', 'source_ids': ['S99']}]),
    card(sections=[{'title': '培训', 'summary': '说明', 'source_ids': ['S99']}]),
    card(coverage={'complete': True}),
])
def test_card_submission_rejects_invented_nested_sources_and_model_coverage(library, payload):
    doc_id, meta = saved(library, '培训.txt', '新员工培训两周。'.encode())
    bridge = card_bridge(library, doc_id, meta)
    invoke(bridge, 'read_document', {'document_id': doc_id})
    assert invoke(bridge, 'submit_document_card', payload)['ok'] is False
    assert bridge.card is None


def test_card_scope_cannot_organize_search_create_or_trust_old_card_sources(library):
    doc_id, meta = saved(library, '培训.txt', '新员工培训两周。'.encode())
    bridge = card_bridge(library, doc_id, meta,
                         existing_card={'summary': '旧理解', 'source_ids': ['S1']})
    names = {item['function']['name'] for item in bridge.definitions()}
    assert names == {'document_overview', 'read_document', 'read_table', 'read_visual',
                    'read_embedded_images', 'read_skill_reference', 'read_object_skill', 'submit_document_card'}
    assert invoke(bridge, 'submit_document_card', card())['ok'] is False
    for name in ('propose_organization', 'search_documents', 'create_draft'):
        assert invoke(bridge, name, {})['ok'] is False
    assert not bridge.tools.sources
    invoke(bridge, 'read_document', {'document_id': doc_id})
    assert invoke(bridge, 'submit_document_card', card())['ok'] is True
    assert (library / '培训.txt').read_text() == '新员工培训两周。'


def test_plain_text_can_have_complete_host_coverage_but_overview_alone_cannot(library):
    doc_id, meta = saved(library, '培训.txt', '新员工培训两周。'.encode())
    bridge = card_bridge(library, doc_id, meta)
    invoke(bridge, 'document_overview', {'document_id': doc_id})
    assert bridge.coverage()['complete'] is False
    assert invoke(bridge, 'submit_document_card', card())['ok'] is False
    invoke(bridge, 'read_document', {'document_id': doc_id})
    assert bridge.coverage()['complete'] is True
    assert invoke(bridge, 'submit_document_card', card())['ok'] is True


def test_card_source_validation_rechecks_version_identity(library):
    doc_id, meta = saved(library, '培训.txt', '新员工培训两周。'.encode())
    bridge = card_bridge(library, doc_id, meta)
    invoke(bridge, 'read_document', {'document_id': doc_id})
    bridge.tools.sources[0]['content_hash'] = '0' * 64
    assert invoke(bridge, 'submit_document_card', card())['ok'] is False


@pytest.mark.parametrize(('original_name', 'display_name', 'is_office'), [
    ('原稿.docx', '显示.txt', True),
    ('原稿.txt', '显示.png', False),
])
def test_coverage_uses_saved_format_while_naming_preserves_display_suffix(
        library, original_name, display_name, is_office):
    from backend.app import database
    from backend.app.models import Document
    from backend.app.services.indexing import metadata_fingerprint
    from backend.tests.test_index_content import docx
    data = (docx('<w:p><w:r><w:t>培训计划</w:t></w:r></w:p>')
            if is_office else '培训计划'.encode())
    doc_id, meta = saved(library, original_name, data)
    with database.SessionLocal() as db:
        document = db.get(Document, doc_id)
        document.name = display_name
        db.commit()
        meta.update(name=document.name, metadata_hash=metadata_fingerprint(document))
    bridge = card_bridge(library, doc_id, meta)
    bridge.preflight()
    invoke(bridge, 'read_document', {'document_id': doc_id})
    coverage = bridge.coverage()
    assert coverage['read_units'] == coverage['total_units'] == 1
    if is_office:
        assert coverage['complete'] is False
        assert coverage['total_visual_pages'] is None
        assert any('页面布局' in gap for gap in coverage['gaps'])
    else:
        assert coverage['complete'] is True
        assert coverage['total_visual_pages'] == coverage['total_embedded_images'] == 0
    organizing = bridge_for(library, doc_id, meta)
    organizing.preflight()
    invoke(organizing, 'read_document', {'document_id': doc_id})
    assert invoke(organizing, 'propose_organization', proposal(name=display_name))['ok'] is True
    assert invoke(organizing, 'propose_organization', proposal(name=original_name))['ok'] is False
