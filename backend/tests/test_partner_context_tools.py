"""Synthetic frozen evidence follows authorized documents into an ordinary new turn."""
from copy import deepcopy

import pytest

from backend.app import database
from backend.app.models import Document
from backend.app.services import partner_tools
from backend.tests.test_partner_tools import library, saved, toolset


def read_checkpoint(root, *, lines=3):
    doc, meta = saved(root, '合成记录.txt', '\n'.join(f'完整原文 {n}' for n in range(lines)).encode())
    old_scope = {doc: meta}
    tools = toolset(root, old_scope)
    for offset in range(0, lines, 100):
        tools.read_document(doc, offset=offset, limit=100)
    return doc, old_scope, tools.export_checkpoint()


def test_rebase_retains_exact_text_and_cursors_but_clears_previous_task_counters(library):
    doc, old_scope, checkpoint = read_checkpoint(library)
    checkpoint['image_usage'] = {'count': 2, 'bytes': 1000}
    checkpoint['no_progress_count'] = 2
    tools = toolset(library, old_scope)
    rebased = tools.rebase_checkpoint(checkpoint, old_scope)
    assert rebased['evidence'] == checkpoint['evidence']
    assert rebased['sources'] == checkpoint['sources']
    assert rebased['read_ranges'] == checkpoint['read_ranges']
    assert rebased['seen_evidence'] == checkpoint['seen_evidence']
    assert rebased['image_usage'] == {'count': 0, 'bytes': 0}
    assert rebased['no_progress_count'] == 0
    tools.restore_checkpoint(rebased)
    assert tools.progress['read_units'] == 3
    assert tools.exposed_document_ids == {doc}


def test_rebase_prunes_scope_graph_preserves_sparse_ids_and_allocates_safely(library):
    removed, removed_meta = saved(library, '移除.txt', b'removed')
    kept, kept_meta = saved(library, '保留.txt', b'first\nsecond')
    old_scope = {removed: removed_meta, kept: kept_meta}
    old = toolset(library, old_scope)
    old.read_document(removed)
    old.read_document(kept, limit=1)
    new = toolset(library, {kept: kept_meta})
    checkpoint = new.rebase_checkpoint(old.export_checkpoint(), old_scope)
    assert [item['id'] for item in checkpoint['sources']] == ['S2']
    assert checkpoint['exposed_document_ids'] == [kept]
    assert all(item['document_id'] == kept for item in checkpoint['read_ranges'])
    assert all(key.startswith(kept + ':text:') for key in checkpoint['seen_evidence'])
    new.restore_checkpoint(checkpoint)
    result = new.read_document(kept, offset=1, limit=1)
    assert result['units'][0]['source_id'] == 'S3'
    assert [item['id'] for item in new.sources] == ['S2', 'S3']
    assert new.export_checkpoint()['next_source_number'] == 4


@pytest.mark.parametrize('field', ['name', 'version_id', 'version_number', 'content_hash', 'metadata_hash', 'is_folder'])
def test_rebase_requires_the_entire_frozen_document_record_to_match(library, field):
    doc, old_scope, checkpoint = read_checkpoint(library)
    new_scope = deepcopy(old_scope)
    new_scope[doc][field] = 'changed'
    new = toolset(library, new_scope)
    rebased = new.rebase_checkpoint(checkpoint, old_scope)
    assert rebased['sources'] == rebased['evidence'] == rebased['read_ranges'] == []
    assert rebased['seen_evidence'] == rebased['exposed_document_ids'] == []
    new.restore_checkpoint(rebased)


def test_rebase_does_not_reauthorize_new_documents_via_old_scope(library):
    doc, old_scope, checkpoint = read_checkpoint(library)
    added, added_meta = saved(library, '新增.txt', b'new')
    tampered_old_scope = {**old_scope, added: added_meta}
    new = toolset(library, tampered_old_scope)
    with pytest.raises(ValueError, match='范围|检查点|进度'):
        new.rebase_checkpoint(checkpoint, tampered_old_scope)
    rebased = new.rebase_checkpoint(checkpoint, old_scope)
    assert rebased['exposed_document_ids'] == [doc]


def test_rebase_drops_visual_and_metadata_only_read_claims(library):
    doc, old_scope, _ = read_checkpoint(library, lines=1)
    old = toolset(library, old_scope)
    old.read_document(doc)
    old._record(doc, [{'kind': 'visual', 'text': '', 'location': {'page': 2}, 'label': '原页'}],
                [f'{doc}:visual:2'], {'document_id': doc, 'kind': 'visual', 'pages': [2],
                'page_count': 2, 'images_need_reread_after_resume': True})
    old.document_overview(doc)
    old.search_documents('合成记录')
    rebased_tools = toolset(library, old_scope)
    rebased = rebased_tools.rebase_checkpoint(old.export_checkpoint(), old_scope)
    assert len(rebased['sources']) == 1
    assert all(item['kind'] == 'text' and item['text'] for item in rebased['evidence'])
    assert all(':text:' in key for key in rebased['seen_evidence'])
    assert all(item['kind'] == 'text' for item in rebased['read_ranges'])
    assert any('图片' in note or '原页' in note for note in rebased_tools.rebase_notes)


def test_rebase_keeps_most_recent_100_complete_sources_and_prunes_omitted_read_claims(library):
    doc, old_scope, checkpoint = read_checkpoint(library, lines=120)
    new = toolset(library, old_scope)
    rebased = new.rebase_checkpoint(checkpoint, old_scope)
    assert [item['id'] for item in rebased['sources']] == [f'S{n}' for n in range(21, 121)]
    assert rebased['evidence'] == checkpoint['evidence'][-100:]
    assert rebased['seen_evidence'] == rebased['read_ranges'] == []
    assert any('省略' in note for note in new.rebase_notes)
    new.restore_checkpoint(rebased)
    assert len(new.sources) == 100
    assert new.progress['read_units'] == 0


def test_rebase_respects_character_capacity_without_slicing_evidence(library):
    doc, meta = saved(library, '大段落.txt', b'fixture')
    scope = {doc: meta}
    old = toolset(library, scope)
    for number in range(6):
        old._record(doc, [{'kind': 'text', 'text': str(number) * 12000,
                    'location': {'line': number + 1}, 'label': str(number)}],
                    [f'{doc}:text:u{number + 1:06d}'],
                    {'document_id': doc, 'kind': 'text', 'offset': number,
                     'next_offset': number + 1, 'unit_count': 6})
    new = toolset(library, scope)
    rebased = new.rebase_checkpoint(old.export_checkpoint(), scope)
    assert sum(len(item['text']) for item in rebased['evidence']) == 60000
    assert rebased['evidence'] == old.export_checkpoint()['evidence'][1:]
    assert any('省略' in note for note in new.rebase_notes)


@pytest.mark.parametrize('schema_version', [1, 2])
def test_legacy_checkpoints_still_restore_and_rebase(library, schema_version):
    _, old_scope, checkpoint = read_checkpoint(library)
    checkpoint.pop('next_source_number', None)
    checkpoint['schema_version'] = schema_version
    if schema_version == 1:
        checkpoint.pop('image_usage')
    new = toolset(library, old_scope)
    new.restore_checkpoint(checkpoint)
    new.restore_checkpoint(new.rebase_checkpoint(checkpoint, old_scope))
    assert len(new.sources) == 3


@pytest.mark.parametrize('mutation', ['duplicate_source', 'duplicate_seen', 'duplicate_exposed',
    'boolean_next', 'zero_next', 'next_collision', 'oversized_text', 'oversized_bytes',
    'oversized_sources', 'unknown_field', 'visual_text', 'forged_scope', 'unhashable_doc'])
def test_rebase_rejects_invalid_old_checkpoints_before_filtering(library, mutation):
    doc, old_scope, checkpoint = read_checkpoint(library)
    if mutation == 'duplicate_source':
        checkpoint['sources'][1] = deepcopy(checkpoint['sources'][0])
    elif mutation == 'duplicate_seen':
        checkpoint['seen_evidence'].append(checkpoint['seen_evidence'][0])
    elif mutation == 'duplicate_exposed':
        checkpoint['exposed_document_ids'].append(doc)
    elif mutation == 'boolean_next':
        checkpoint['next_source_number'] = True
    elif mutation == 'zero_next':
        checkpoint['next_source_number'] = 0
    elif mutation == 'next_collision':
        checkpoint['next_source_number'] = 3
    elif mutation == 'oversized_text':
        checkpoint['evidence'][0]['text'] = 'x' * 180001
    elif mutation == 'oversized_bytes':
        checkpoint['seen_evidence'] = ['x' * (1024 * 1024)]
    elif mutation == 'oversized_sources':
        checkpoint['sources'] *= 334
    elif mutation == 'unknown_field':
        checkpoint['artifacts'] = []
    elif mutation == 'visual_text':
        checkpoint['evidence'][0]['kind'] = 'visual'
    elif mutation == 'forged_scope':
        checkpoint['scope_fingerprint'] = '0' * 64
    elif mutation == 'unhashable_doc':
        checkpoint['sources'][0]['document_id'] = []
    with pytest.raises(ValueError):
        toolset(library, {}).rebase_checkpoint(checkpoint, old_scope)


def test_rebased_checkpoint_still_checks_current_metadata_and_guard(library):
    doc, old_scope, checkpoint = read_checkpoint(library)
    new = toolset(library, old_scope)
    rebased = new.rebase_checkpoint(checkpoint, old_scope)
    with database.SessionLocal() as db:
        db.get(Document, doc).name = '资料已变更.txt'
        db.commit()
    with pytest.raises(ValueError, match='变化|失效'):
        new.restore_checkpoint(rebased)


def test_sparse_source_update_uses_id_instead_of_list_position(library):
    removed, removed_meta = saved(library, '删除.txt', b'removed')
    doc, meta = saved(library, '保留.txt', b'read text')
    scope = {removed: removed_meta, doc: meta}
    old = toolset(library, scope)
    old.read_document(removed)
    old._record(doc, [{'kind': 'visual', 'text': '', 'location': {'line': 1}, 'label': '图像'}],
                [f'{doc}:visual:1'], {'document_id': doc, 'kind': 'visual', 'pages': [1],
                'page_count': 1, 'images_need_reread_after_resume': True})
    checkpoint = old.export_checkpoint()
    # A resumed v3 visual checkpoint can contain sparse ids even before text upgrades it.
    checkpoint['schema_version'] = 3
    checkpoint['sources'] = checkpoint['sources'][1:]
    checkpoint['evidence'] = checkpoint['evidence'][1:]
    checkpoint['exposed_document_ids'] = [doc]
    checkpoint['seen_evidence'] = [f'{doc}:visual:1']
    checkpoint['read_ranges'] = checkpoint['read_ranges'][1:]
    checkpoint['next_source_number'] = 3
    checkpoint['scope_fingerprint'] = toolset(library, {doc: meta})._scope_fingerprint()
    new = toolset(library, {doc: meta})
    new.restore_checkpoint(checkpoint)
    result = new.read_document(doc)
    assert result['units'][0]['source_id'] == 'S2'
    assert new.sources[0]['excerpt'] == 'read text'
    assert new.export_checkpoint()['evidence'][0]['text'] == 'read text'


def test_rebase_requires_matching_frozen_value_types(library):
    doc, scope, checkpoint = read_checkpoint(library)
    new_scope = deepcopy(scope)
    assert scope[doc]['version_number'] == 1
    new_scope[doc]['version_number'] = True
    rebased = toolset(library, new_scope).rebase_checkpoint(checkpoint, scope)
    assert rebased['sources'] == rebased['evidence'] == []


def test_rebase_drops_empty_text_sources_and_their_unverifiable_doc_cursors(library):
    doc, scope, checkpoint = read_checkpoint(library)
    checkpoint['evidence'][-1]['text'] = '  '
    new = toolset(library, scope)
    rebased = new.rebase_checkpoint(checkpoint, scope)
    assert len(rebased['evidence']) == 2
    assert rebased['seen_evidence'] == rebased['read_ranges'] == []


def test_rebase_rejects_malformed_frozen_record_as_value_error(library):
    doc, scope, checkpoint = read_checkpoint(library)
    malformed_scope = {doc: ['not-a-frozen-record']}
    checkpoint['scope_fingerprint'] = toolset(library, malformed_scope)._scope_fingerprint()
    with pytest.raises(ValueError):
        toolset(library, scope).rebase_checkpoint(checkpoint, malformed_scope)


def test_rebase_preserves_original_aggregate_text_hard_limit(library):
    doc, scope, checkpoint = read_checkpoint(library, lines=1)
    source, evidence = checkpoint['sources'][0], checkpoint['evidence'][0]
    checkpoint['sources'], checkpoint['evidence'] = [], []
    for number in range(1, 14):
        checkpoint['sources'].append({**deepcopy(source), 'id': f'S{number}',
            'source_id': f'S{number}', 'location': {'line': number}})
        checkpoint['evidence'].append({**deepcopy(evidence), 'source_id': f'S{number}',
            'location': {'line': number}, 'text': 'x' * 15000})
    checkpoint['next_source_number'] = 14
    with pytest.raises(ValueError):
        toolset(library, {}).rebase_checkpoint(checkpoint, scope)
