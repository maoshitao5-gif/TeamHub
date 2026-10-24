"""预整理只生成有当前阅读依据的方案，不修改资料。"""
import asyncio
from copy import deepcopy

import pytest

from backend.tests.test_partner_tools import library, saved, pdf  # noqa: F401


def bridge_for(library, doc_id, meta, **context):
    from backend.app import database
    from backend.app.services.organization_tools import OrganizationTools
    return OrganizationTools(engine=database.engine, root=str(library), scope={doc_id: meta},
        guard=lambda: None, emit=lambda *_: None,
        purpose_context={'document_id': doc_id, 'tags': ['培训'], 'folders': ['规程'], **context})


def proposal(**changes):
    return {'name': '新员工培训.txt', 'tags': ['培训'], 'target_dir': '规程',
        'summary': '用于两周的新员工培训。', 'related_clues': ['新员工'],
        'gaps': [], 'source_ids': ['S1'],
        'card': {'summary': changes.get('summary', '新员工培训周期为两周。'),
                 'document_type': '资料', 'topics': [], 'sections': [], 'facts': [],
                 'search_terms': [], 'uses': [], 'gaps': [], 'source_ids': ['S1']},
        **changes}


def invoke(bridge, name, args):
    return asyncio.run(bridge.call(name, args))['result']


@pytest.mark.parametrize('missing_card', ['omitted', 'null'])
def test_new_submission_requires_card_but_legacy_proposals_remain_readable(library, missing_card):
    from backend.app.services.organization_tools import OrganizationProposal
    doc_id, meta = saved(library, '培训资料.txt', '新员工培训周期为两周。'.encode())
    bridge = bridge_for(library, doc_id, meta)
    invoke(bridge, 'read_document', {'document_id': doc_id})
    legacy = proposal()
    legacy.pop('card', None)
    if missing_card == 'null':
        legacy['card'] = None
    assert OrganizationProposal.model_validate(legacy).card is None
    result = invoke(bridge, 'propose_organization', legacy)
    assert result['ok'] is False
    assert bridge.proposal is None and bridge.card is None
    assert 'card' in result['error']
    definition = next(item['function'] for item in bridge.definitions()
                      if item['function']['name'] == 'propose_organization')
    assert 'card' in definition['parameters']['required']


def test_proposal_requires_current_task_reading_and_never_mutates_document(library):
    from backend.app import database
    from backend.app.models import Document
    doc_id, meta = saved(library, '培训资料.txt', '新员工培训周期为两周。'.encode())
    bridge = bridge_for(library, doc_id, meta)
    assert invoke(bridge, 'propose_organization', proposal())['ok'] is False
    assert bridge.proposal is None
    invoke(bridge, 'document_overview', {'document_id': doc_id})
    assert invoke(bridge, 'propose_organization', proposal())['ok'] is False
    invoke(bridge, 'read_document', {'document_id': doc_id})
    assert invoke(bridge, 'propose_organization', proposal())['ok'] is True
    assert bridge.proposal == proposal()
    assert (library / '培训资料.txt').read_text() == '新员工培训周期为两周。'
    with database.SessionLocal() as db:
        doc = db.get(Document, doc_id)
        assert doc.name == '培训资料.txt'
        assert doc.status == 'pending'
        assert doc.storage_path == '培训资料.txt'
        assert list(doc.tags) == []


@pytest.mark.parametrize('changes', [
    {'name': '../离开库.txt'}, {'name': '子目录\\名称.txt'}, {'name': '错误.docx'},
    {'name': '空\x00字符.txt'}, {'name': '控制\u202e字符.txt'},
    {'target_dir': '../规程'}, {'target_dir': '未经提供的新分类'},
    {'source_ids': []}, {'source_ids': ['S999']}, {'tags': ['']}, {'extra': '未知字段'},
])
def test_invalid_proposal_cannot_be_accepted(library, changes):
    doc_id, meta = saved(library, '资料.txt', '培训两周'.encode())
    bridge = bridge_for(library, doc_id, meta)
    invoke(bridge, 'read_document', {'document_id': doc_id})
    assert invoke(bridge, 'propose_organization', proposal(**changes))['ok'] is False
    assert bridge.proposal is None


def test_empty_tags_and_no_folder_are_valid_suggestions(library):
    doc_id, meta = saved(library, '资料.txt', '培训两周'.encode())
    bridge = bridge_for(library, doc_id, meta)
    invoke(bridge, 'read_document', {'document_id': doc_id})
    result = invoke(bridge, 'propose_organization', proposal(tags=[], target_dir=''))
    assert result['ok'] is True
    assert bridge.proposal['tags'] == []


def test_toolset_cannot_search_others_create_draft_or_modify_files(library):
    doc_id, meta = saved(library, '资料.txt', '培训两周'.encode())
    other_id, _ = saved(library, '另一个文件.txt', b'secret')
    bridge = bridge_for(library, doc_id, meta)
    definitions = bridge.definitions()
    names = {item['function']['name'] for item in definitions}
    assert names == {'document_overview', 'read_document', 'read_table',
                     'read_visual', 'read_embedded_images', 'read_skill_reference', 'read_object_skill',
                     'propose_organization'}
    for name, args in [('search_documents', {'query': 'secret'}),
                       ('create_draft', {'name': 'draft.txt', 'content': 'new'}),
                       ('read_document', {'document_id': other_id})]:
        assert invoke(bridge, name, args)['ok'] is False
    assert not bridge.tools.sources
    assert bridge.proposal is None


def test_partial_reading_always_adds_host_coverage_gap(library):
    doc_id, meta = saved(library, '长资料.txt', '\n'.join(f'第{i}条内容' for i in range(150)).encode())
    bridge = bridge_for(library, doc_id, meta)
    invoke(bridge, 'read_document', {'document_id': doc_id, 'limit': 2})
    assert invoke(bridge, 'propose_organization', proposal(gaps=[]))['ok'] is True
    assert any('2 / 150' in gap for gap in bridge.proposal['gaps'])
    assert bridge.tools.progress['read_units'] == 2


def test_context_is_single_document_and_bounded_catalog(library):
    from backend.app import database
    from backend.app.services.organization_tools import OrganizationTools
    doc_id, meta = saved(library, '资料.txt', b'test')
    other_id, other_meta = saved(library, '其他.txt', b'private')
    kwargs = dict(engine=database.engine, root=str(library), guard=lambda: None, emit=lambda *_: None)
    with pytest.raises(ValueError):
        OrganizationTools(**kwargs, scope={doc_id: meta, other_id: other_meta},
            purpose_context={'document_id': doc_id, 'tags': [], 'folders': []})
    for context in ({'document_id': other_id, 'tags': [], 'folders': []},
                    {'document_id': doc_id, 'tags': [], 'folders': ['/outside']},
                    {'document_id': doc_id, 'tags': ['x'] * 501, 'folders': []}):
        with pytest.raises(ValueError):
            OrganizationTools(**kwargs, scope={doc_id: meta}, purpose_context=context)


def test_catalog_and_frozen_scope_are_copied(library):
    doc_id, meta = saved(library, '资料.txt', b'test')
    folders = ['规程']
    bridge = bridge_for(library, doc_id, deepcopy(meta), folders=folders)
    folders.append('后来新增')
    invoke(bridge, 'read_document', {'document_id': doc_id})
    assert invoke(bridge, 'propose_organization', proposal(target_dir='后来新增'))['ok'] is False


def test_pdf_text_is_not_pretended_to_include_visual_layout(library):
    doc_id, meta = saved(library, '图纸.pdf', pdf())
    bridge = bridge_for(library, doc_id, meta)
    invoke(bridge, 'read_document', {'document_id': doc_id})
    assert invoke(bridge, 'propose_organization', proposal(name='图纸.pdf'))['ok'] is True
    assert any('PDF 原页' in gap for gap in bridge.proposal['gaps'])


def test_only_reading_one_pdf_page_preserves_actual_visual_gap(library):
    doc_id, meta = saved(library, '图纸.pdf', pdf(pages=2))
    bridge = bridge_for(library, doc_id, meta)
    invoke(bridge, 'read_visual', {'document_id': doc_id, 'pages': [1]})
    assert invoke(bridge, 'propose_organization', proposal(name='图纸.pdf'))['ok'] is True
    assert any('原图页 1 / 2' in gap for gap in bridge.proposal['gaps'])


def test_office_parser_gaps_are_not_erased_by_empty_model_gaps(library):
    from backend.tests.test_index_content import docx
    data = docx('<w:p><w:r><w:t>培训计划</w:t></w:r></w:p>',
                {'word/comments.xml': '<comments>评论</comments>'})
    doc_id, meta = saved(library, '培训.docx', data)
    bridge = bridge_for(library, doc_id, meta)
    invoke(bridge, 'read_document', {'document_id': doc_id})
    assert invoke(bridge, 'propose_organization', proposal(name='培训.docx'))['ok'] is True
    assert any('批注' in gap for gap in bridge.proposal['gaps'])
    assert any('页面布局' in gap for gap in bridge.proposal['gaps'])


def test_table_without_full_structure_is_not_claimed_as_complete(library):
    from backend.tests.test_index_content import xlsx
    data = xlsx('<row r="1"><c r="A1" t="inlineStr"><is><t>培训计划</t></is></c></row>')
    doc_id, meta = saved(library, '培训.xlsx', data)
    bridge = bridge_for(library, doc_id, meta)
    invoke(bridge, 'read_table', {'document_id': doc_id})
    assert invoke(bridge, 'propose_organization', proposal(name='培训.xlsx'))['ok'] is True
    assert any('完整覆盖范围' in gap for gap in bridge.proposal['gaps'])


@pytest.mark.parametrize('suffix', ['.png', '.pdf', '.docx'])
def test_visual_downscaling_notes_survive_empty_model_gaps(library, suffix):
    from io import BytesIO
    import zipfile
    from PIL import Image
    from pypdf import PdfWriter
    from backend.tests.test_partner_visual_assets import office_images, package

    output = BytesIO()
    if suffix == '.pdf':
        writer = PdfWriter()
        writer.add_blank_page(width=4000, height=4000)
        writer.write(output)
    else:
        Image.new('RGB', (4000, 4000), 'red').save(output, 'PNG')
    data = output.getvalue()
    if suffix == '.docx':
        with zipfile.ZipFile(BytesIO(office_images('.docx'))) as archive:
            parts = {name: archive.read(name) for name in archive.namelist()}
        parts['word/media/visible.png'] = data
        data = package(parts)
    doc_id, meta = saved(library, '大幅图' + suffix, data)
    bridge = bridge_for(library, doc_id, meta)
    if suffix == '.docx':
        reading = invoke(bridge, 'read_embedded_images',
                         {'document_id': doc_id, 'image_ids': ['image-1']})
    else:
        reading = invoke(bridge, 'read_visual', {'document_id': doc_id})
    scaling_notes = [note for note in reading['notes'] if '缩放' in note]
    assert scaling_notes, reading
    assert invoke(bridge, 'propose_organization', proposal(name='大幅图' + suffix))['ok']
    assert set(scaling_notes).issubset(bridge.proposal['gaps'])
