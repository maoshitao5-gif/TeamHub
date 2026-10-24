"""Ranked retrieval uses only isolated SQLite data and explicit authorized scopes."""
import hashlib
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.app import database
from backend.app.models import Document, Tag, Version
from backend.app.services import search_retrieval


@pytest.fixture
def db():
    from backend.app import card_models, index_models  # noqa: F401
    from backend.app.services import index_chunks, indexing
    engine = create_engine('sqlite:///:memory:')
    database.Base.metadata.create_all(engine)
    indexing.install_schema(engine)
    index_chunks.install_schema(engine)
    search_retrieval.install_schema(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def document(db, name='unrelated.txt', *, description=None, status='pending'):
    from backend.app.index_models import DocumentIndex
    from backend.app.services.index_content import PARSER_VERSION
    doc = Document(name=name, description=description, status=status)
    db.add(doc)
    db.flush()
    version = Version(document_id=doc.id, version_number=1, file_path='/unused',
                      original_filename=name, sha256_hash=hashlib.sha256(doc.id.encode()).hexdigest())
    db.add(version)
    db.flush()
    doc.current_version_id = version.id
    record = DocumentIndex(document_id=doc.id, version_id=version.id,
                           content_hash=version.sha256_hash, parser_version=PARSER_VERSION,
                           generation=1, state='ready', result_json=json.dumps({
                               'coverage': {'total': 1, 'processed': 1, 'failed': 0, 'unit': 'blocks'},
                               'gaps': []}))
    db.add(record)
    db.flush()
    return doc, version, record


def chunk(db, doc, version, body, *, ordinal=0, context='', label='第1段', location=None, **overrides):
    from backend.app.index_models import IndexChunk
    from backend.app.services.index_content import PARSER_VERSION
    values = dict(document_id=doc.id, version_id=version.id, generation=1,
                  unit_id=f'T{ordinal + 1}', ordinal=ordinal, kind='text', body=body,
                  context=context, label=label, location_json=json.dumps(location or {'block': ordinal + 1}),
                  content_hash=hashlib.sha256(body.encode()).hexdigest(),
                  source_hash=version.sha256_hash, parser_version=PARSER_VERSION)
    values.update(overrides)
    row = IndexChunk(**values)
    db.add(row)
    db.flush()
    return row


def ids(result):
    return [item['document_id'] for item in result['items']]


def test_multiple_terms_rank_combined_current_passages_ahead_of_name(db):
    named, nv, _ = document(db, 'battery manual.txt')
    chunk(db, named, nv, 'Only a list of unrelated products.')
    relevant, rv, _ = document(db)
    chunk(db, relevant, rv, 'battery is disconnected', ordinal=0)
    chunk(db, relevant, rv, 'fault recovery procedure', ordinal=1)
    found = search_retrieval.search(db, 'battery fault', [named.id, relevant.id])
    assert ids(found) == [relevant.id, named.id]
    assert found['items'][0]['relevance']['matched_terms'] == ['battery', 'fault']
    assert found['items'][0]['channels'] == ['content']
    assert {p['unit_id'] for p in found['items'][0]['passages']} == {'T1', 'T2'}
    assert found['mode'] == 'lexical'
    assert found['total'] == 2


def test_full_tail_context_and_locations_are_searchable_and_excerpt_is_local(db):
    doc, version, _ = document(db)
    chunk(db, doc, version, '无关开头' * 995 + 'ZX-2048 尾部维修步骤',
          ordinal=7, context='设备巡检表 温度阈值', label='巡检表第9行',
          location={'sheet': '巡检', 'row': 9})
    found = search_retrieval.search(db, 'ZX-2048', [doc.id])
    passage = found['items'][0]['passages'][0]
    assert 'ZX-2048' in passage['excerpt']
    assert len(passage['excerpt']) <= 500
    assert passage['location'] == {'sheet': '巡检', 'row': 9}
    assert passage['ordinal'] == 7 and passage['label'] == '巡检表第9行'
    assert passage['source_kind'] == 'content'
    assert ids(search_retrieval.search(db, '温度阈值', [doc.id])) == [doc.id]


@pytest.mark.parametrize('keyword', ['%', '_', '" OR *'])
def test_query_syntax_is_literal_and_never_widens_scope(db, keyword):
    wanted, version, _ = document(db)
    chunk(db, wanted, version, f'包含字面量 {keyword} 的内容')
    other, ov, _ = document(db)
    chunk(db, other, ov, 'ordinary OR unrelated text')
    assert ids(search_retrieval.search(db, keyword, [wanted.id, other.id])) == [wanted.id]


def test_exact_equipment_identifier_excludes_similar_number_even_with_other_terms(db):
    wanted, version, _ = document(db)
    chunk(db, wanted, version, 'ZX-2048 fault diagnosis')
    similar, sv, _ = document(db, 'ZX-20480 fault.txt')
    chunk(db, similar, sv, 'ZX-2049 fault diagnosis')
    assert ids(search_retrieval.search(db, 'ZX-2048 fault', [wanted.id, similar.id])) == [wanted.id]


def test_numeric_identifiers_are_exact_and_filename_extension_is_a_boundary(db):
    wanted, version, _ = document(db, 'ZX-2048.txt')
    chunk(db, wanted, version, '5053 压力异常')
    other, ov, _ = document(db, 'ZX-20480.txt')
    chunk(db, other, ov, '50530 压力异常')
    scope = [wanted.id, other.id]
    assert ids(search_retrieval.search(db, '5053 压力异常', scope)) == [wanted.id]
    assert ids(search_retrieval.search(db, 'ZX-2048', scope)) == [wanted.id]


@pytest.mark.parametrize('change', ['version', 'source_hash', 'parser', 'generation', 'state'])
def test_stale_or_unpublished_chunks_are_excluded(db, change):
    doc, version, record = document(db)
    row = chunk(db, doc, version, 'unique needle')
    if change == 'version':
        doc.current_version_id = None
    elif change == 'source_hash':
        row.source_hash = '0' * 64
    elif change == 'parser':
        row.parser_version = 'obsolete'
    elif change == 'generation':
        record.generation = 2
    else:
        record.state = 'queued'
    db.flush()
    assert search_retrieval.search(db, 'needle', [doc.id])['items'] == []


def test_scope_must_be_explicit_and_sql_scopes_exclude_unauthorized_and_trash(db):
    visible, vv, _ = document(db)
    chunk(db, visible, vv, 'needle')
    private, pv, _ = document(db)
    chunk(db, private, pv, 'needle')
    trash, tv, _ = document(db, status='trashed')
    chunk(db, trash, tv, 'needle')
    scope = select(Document.id).where(Document.id.in_([visible.id, trash.id]))
    assert ids(search_retrieval.search(db, 'needle', scope)) == [visible.id]
    assert search_retrieval.search(db, 'needle', [])['items'] == []
    assert search_retrieval.search(db, 'needle', None)['items'] == []


def test_current_cards_notes_and_ai_are_labeled_clues_and_revalidated(db):
    from backend.app.card_models import DocumentCard, DocumentCardAnnotation
    from backend.app.index_models import IndexText
    from backend.app.services import card_schema, indexing
    from backend.app.services.index_content import PARSER_VERSION
    doc, version, record = document(db)
    card = DocumentCard(document_id=doc.id, version_id=version.id, content_hash=version.sha256_hash,
                        parser_version=PARSER_VERSION, skill_version=card_schema.SKILL_VERSION,
                        schema_version=card_schema.SCHEMA_VERSION, content_json='{"gaps":[]}',
                        sources_json='[]', coverage_json='{"complete":true}',
                        search_text='卡片专属线索', model='local-test')
    db.add(card)
    note = DocumentCardAnnotation(document_id=doc.id, source_version_id=version.id, note='人工独有补充')
    db.add(note)
    record.ai_json = '{"overview":"AI别称"}'
    record.ai_metadata_json = indexing._metadata_json(doc)
    record.ai_metadata_hash = indexing.metadata_fingerprint(doc)
    db.add(IndexText(document_id=doc.id, kind='ai', body='AI独有别称'))
    db.flush()
    for query, kind in [('卡片专属', 'card'), ('人工独有', 'note'), ('AI独有', 'ai')]:
        item = search_retrieval.search(db, query, [doc.id])['items'][0]
        assert item['passages'][0]['source_kind'] == kind
        assert item['passages'][0]['unit_id'] is None
        assert item['passages'][0]['location'] is None
    card.skill_version = 'obsolete'
    note.source_version_id = None
    doc.description = '改变后的整理语境'
    db.flush()
    for query in ['卡片专属', '人工独有', 'AI独有']:
        assert not search_retrieval.search(db, query, [doc.id])['items']


def test_name_description_and_tags_update_without_reparsing(db):
    doc, _, _ = document(db, '原始名称', description='初始描述')
    tag = Tag(name='初始标签')
    doc.tags.append(tag)
    db.flush()
    for query in ['原始名称', '初始描述', '初始标签']:
        assert ids(search_retrieval.search(db, query, [doc.id])) == [doc.id]
    doc.name, doc.description, tag.name = '新的名称', '新的描述', '新的标签'
    db.flush()
    for query in ['原始名称', '初始描述', '初始标签']:
        assert not search_retrieval.search(db, query, [doc.id])['items']
    for query in ['新的名称', '新的描述', '新的标签']:
        assert ids(search_retrieval.search(db, query, [doc.id])) == [doc.id]


def test_pagination_has_exact_total_and_no_ten_document_ceiling_or_duplicates(db):
    scope = []
    for number in range(27):
        doc, version, _ = document(db, f'{number:03d}.txt')
        chunk(db, doc, version, 'pagination needle')
        scope.append(doc.id)
    seen, offset = [], 0
    while True:
        page = search_retrieval.search(db, 'needle', scope, limit=10, offset=offset)
        assert page['total'] == 27
        seen.extend(ids(page))
        if not page['has_more']:
            assert page['next_offset'] is None
            break
        offset = page['next_offset']
    assert len(seen) == len(set(seen)) == 27
    assert seen == ids(search_retrieval.search(db, 'needle', scope, limit=100))


def test_query_budget_is_explicit_and_empty_query_does_not_browse(db):
    doc, version, _ = document(db)
    chunk(db, doc, version, 'needle')
    assert not search_retrieval.search(db, '  ', [doc.id])['items']
    with pytest.raises(ValueError, match='512'):
        search_retrieval.search(db, 'a' * 513, [doc.id])
    with pytest.raises(ValueError, match='16'):
        search_retrieval.search(db, ' '.join(f'term{x}' for x in range(17)), [doc.id])


@pytest.mark.parametrize('control', ['\x00', '\x1f', '\x7f', '\u200b', '\n'])
def test_control_characters_fail_as_validation_errors_before_fts(db, control):
    with pytest.raises(ValueError, match='^检索词不能包含控制字符。$'):
        search_retrieval.search(db, f'needle{control}fault', [])


def test_published_processing_chunks_have_explicit_incomplete_coverage(db):
    doc, version, record = document(db)
    chunk(db, doc, version, 'needle')
    record.state = 'processing'
    record.result_json = json.dumps({'coverage': {'total': 20, 'processed': 1, 'failed': 0, 'unit': 'blocks'},
                                     'gaps': []})
    db.flush()
    result = search_retrieval.search(db, 'needle', [doc.id])
    assert ids(result) == [doc.id]
    assert result['items'][0]['coverage']['index_state'] == 'processing'
    assert result['items'][0]['coverage']['complete'] is False


def test_search_schema_can_be_installed_repeatedly(db):
    doc, _, _ = document(db, 'needle metadata')
    db.commit()
    search_retrieval.install_schema(db.get_bind())
    search_retrieval.install_schema(db.get_bind())
    assert ids(search_retrieval.search(db, 'needle', [doc.id])) == [doc.id]


@pytest.mark.parametrize('sort_by', ['name', 'updated_at', 'created_at', 'total_size'])
def test_explicit_sorting_preserves_multiword_matches_and_stable_pagination(db, sort_by):
    from datetime import datetime, timedelta
    docs = []
    for number in range(3):
        doc, version, _ = document(db, f'{number}.txt')
        chunk(db, doc, version, 'battery fault')
        doc.total_size = number
        doc.created_at = datetime(2020, 1, 1) + timedelta(days=number)
        doc.updated_at = datetime(2020, 1, 1) + timedelta(days=number)
        docs.append(doc)
    db.flush()
    page = search_retrieval.search(db, 'battery fault', [doc.id for doc in docs],
                                   sort_by=sort_by, sort_order='asc', limit=2)
    assert ids(page) == [docs[0].id, docs[1].id]
    assert page['total'] == 3 and page['next_offset'] == 2
    tail = search_retrieval.search(db, 'battery fault', [doc.id for doc in docs],
                                   sort_by=sort_by, sort_order='asc', limit=2, offset=2)
    assert ids(tail) == [docs[2].id]


def test_sorting_is_allowlisted(db):
    with pytest.raises(ValueError, match='排序'):
        search_retrieval.search(db, 'needle', [], sort_by='name; DROP TABLE documents')


def test_query_across_4000_character_boundary_returns_first_block_for_verification(db):
    from backend.app.services.index_chunks import write_batch
    doc, version, _ = document(db)
    job = {'document_id': doc.id, 'version_id': version.id,
           'generation': 1, 'content_hash': version.sha256_hash}
    first = {'id': 'u000001', 'kind': 'text', 'text': '前文' * 1999 + '压力',
             'context': '第1行', 'label': '第1行',
             'location': {'line': 1, 'char_start': 1, 'char_end': 4000}}
    second = {'id': 'u000002', 'kind': 'text', 'text': '异常处置方法',
              'context': '第1行', 'label': '第1行',
              'location': {'line': 1, 'char_start': 4001, 'char_end': 4006}}
    write_batch(db, job, [first], 0)
    write_batch(db, job, [second], 1)
    found = search_retrieval.search(db, '压力异常', [doc.id])
    passage = found['items'][0]['passages'][0]
    assert passage['ordinal'] == 0 and passage['unit_id'] == 'u000001'
    assert '压力异常' in passage['excerpt']
    assert passage['location']['char_start'] == 1


def test_passage_bodies_are_loaded_only_after_page_and_passage_limits(db, monkeypatch):
    docs = []
    for number in range(6):
        doc, version, _ = document(db, f'{number}.txt')
        docs.append(doc.id)
        for ordinal in range(5):
            chunk(db, doc, version, 'needle' * 600, ordinal=ordinal)
    original = search_retrieval._load_passages
    loaded = []

    def record_descriptors(session, descriptors, terms):
        loaded.extend(descriptors)
        return original(session, descriptors, terms)

    monkeypatch.setattr(search_retrieval, '_load_passages', record_descriptors)
    page = search_retrieval.search(db, 'needle', docs, limit=2)
    assert page['total'] == 6 and page['has_more']
    assert len(loaded) == 6
    assert len({row['document_id'] for row in loaded}) == 2
    assert all(len(item['passages']) == 3 for item in page['items'])


def persisted_units(db, doc, version, record, texts):
    from backend.app.index_models import IndexWorkItem
    units, offset = [], 1
    for number, value in enumerate(texts):
        units.append({'id': f'u{number + 1:06d}', 'kind': 'text', 'text': value,
                      'context': '第1行', 'label': '第1行',
                      'location': {'line': 1, 'char_start': offset,
                                   'char_end': offset + len(value) - 1}})
        offset += len(value)
    parsed = {'units': units, 'coverage': {'total': len(units), 'processed': len(units),
                                         'failed': 0, 'unit': 'blocks'}, 'gaps': []}
    work = IndexWorkItem(document_id=doc.id, version_id=version.id, generation=record.generation,
                         source_hash=version.sha256_hash, parser_version=record.parser_version,
                         stage='publish', state='queued', cursor=0, total_units=len(units),
                         parsed_result_json=json.dumps(parsed))
    db.add(work)
    db.flush()
    return units, work, {'document_id': doc.id, 'version_id': version.id,
                         'generation': record.generation, 'content_hash': version.sha256_hash}


@pytest.mark.parametrize('edge', ['body_right', 'body_left', 'bridge_right', 'bridge_left'])
def test_fragment_edges_never_turn_identifier_prefix_or_suffix_into_exact_match(db, edge):
    from backend.app.services.index_chunks import write_batch
    doc, version, record = document(db)
    query = 'ZX-20480'
    if edge == 'body_right':
        texts = ['文' * (4000 - len(query)) + query, '9 尾部']
    elif edge == 'body_left':
        texts = ['文' * 3999 + 'X', query + ' 尾部']
    elif edge == 'bridge_right':
        texts = ['文' * 4000, '文' * (511 - len(query)) + query + '9 尾部']
    else:
        texts = ['文' * (4000 - 512) + 'X' + query + '文' * (511 - len(query)), '下一片']
    units, work, job = persisted_units(db, doc, version, record, texts)
    record.state = 'processing'
    write_batch(db, job, units[:1], 0)
    work.cursor = 1
    db.flush()
    assert not search_retrieval.search(db, query, [doc.id])['items']
    write_batch(db, job, units[1:], 1)
    work.cursor = 2
    db.flush()
    assert not search_retrieval.search(db, query, [doc.id])['items']


def test_processing_first_fragment_uses_persisted_successor_and_preserves_ordinal(db):
    from backend.app.services.index_chunks import write_batch
    doc, version, record = document(db)
    query = 'ZX-204809'
    units, work, job = persisted_units(db, doc, version, record,
                                      ['文' * 3994 + 'ZX-204', '809 真实编号'])
    record.state = 'processing'
    write_batch(db, job, units[:1], 0)
    work.cursor = 1
    db.flush()
    result = search_retrieval.search(db, query, [doc.id])
    assert ids(result) == [doc.id]
    assert result['items'][0]['passages'][0]['ordinal'] == 0
    assert query in result['items'][0]['passages'][0]['excerpt']


def test_ready_legacy_backfill_coverage_is_incomplete_until_current_work_finishes(db):
    doc, version, record = document(db)
    units, work, _ = persisted_units(db, doc, version, record, ['needle', 'tail'])
    chunk(db, doc, version, units[0]['text'])
    work.cursor = 1
    db.flush()
    coverage = search_retrieval.search(db, 'needle', [doc.id])['items'][0]['coverage']
    assert coverage['index_state'] == 'ready'
    assert coverage['complete'] is False
    assert coverage['processing_progress'] == {'stage': 'publish', 'cursor': 1, 'total_units': 2}
    work.cursor = 2
    work.stage = work.state = 'done'
    db.flush()
    assert search_retrieval.search(db, 'needle', [doc.id])['items'][0]['coverage']['complete'] is True


@pytest.mark.parametrize('phase', ['before_backfill', 'during_backfill'])
def test_current_legacy_content_remains_searchable_until_backfill_completes(db, phase):
    from backend.app.index_models import IndexText
    doc, version, record = document(db)
    db.add(IndexText(document_id=doc.id, kind='content', body='前部正文\n尾部专属needle'))
    if phase == 'during_backfill':
        units, work, _ = persisted_units(db, doc, version, record, ['前部正文', '尾部专属needle'])
        work.cursor = 1
        chunk(db, doc, version, units[0]['text'])
    db.flush()
    result = search_retrieval.search(db, 'needle', [doc.id])
    assert ids(result) == [doc.id]
    passage = result['items'][0]['passages'][0]
    assert passage['source_kind'] == 'content'
    assert passage['ordinal'] is None and passage['location'] is None and passage['unit_id'] is None
    assert 'needle' in passage['excerpt']


@pytest.mark.parametrize('change', ['version', 'source_hash', 'parser', 'state', 'completed'])
def test_legacy_content_fallback_rejects_stale_or_completed_sources(db, change):
    from backend.app.index_models import IndexText
    doc, version, record = document(db)
    db.add(IndexText(document_id=doc.id, kind='content', body='needle'))
    _, work, _ = persisted_units(db, doc, version, record, ['elsewhere'])
    if change == 'version':
        doc.current_version_id = None
    elif change == 'source_hash':
        record.content_hash = '0' * 64
    elif change == 'parser':
        record.parser_version = 'old'
    elif change == 'state':
        record.state = 'queued'
    else:
        work.cursor = work.total_units
        work.stage = work.state = 'done'
    db.flush()
    assert not search_retrieval.search(db, 'needle', [doc.id])['items']


@pytest.mark.parametrize('word', ['Straße', 'İNPUT'])
def test_unicode_query_preserves_fts_spelling_and_excerpt_offsets(db, word):
    doc, version, _ = document(db)
    chunk(db, doc, version, '前' * 700 + word + ' 检索命中')
    result = search_retrieval.search(db, word, [doc.id])
    assert ids(result) == [doc.id]
    assert result['query_terms'] == [word]
    assert word in result['items'][0]['passages'][0]['excerpt']


def test_unicode_terms_are_not_deduplicated_by_expanding_casefold(db):
    doc, version, _ = document(db)
    chunk(db, doc, version, 'Straße STRASSE')
    result = search_retrieval.search(db, 'Straße STRASSE Straße', [doc.id])
    assert result['query_terms'] == ['Straße', 'STRASSE']
    assert result['items'][0]['relevance']['matched_term_count'] == 2


def test_numeric_query_matches_year_with_date_separators_but_not_longer_number(db):
    doc, version, _ = document(db)
    chunk(db, doc, version, '2026-10-22 / 50530')
    assert ids(search_retrieval.search(db, '2026', [doc.id])) == [doc.id]
    assert not search_retrieval.search(db, '5053', [doc.id])['items']


def test_bridge_cannot_claim_exact_identifier_at_an_omitted_source_edge(db):
    from backend.app.services.index_chunks import write_batch
    doc, version, record = document(db)
    units, work, job = persisted_units(db, doc, version, record,
                                      ['文' * 4000, 'ZX-20480'])
    parsed = json.loads(work.parsed_result_json)
    parsed['gaps'] = [{'reason': 'text_limit', 'location': {'line': 1, 'char_start': 4009}}]
    work.parsed_result_json = json.dumps(parsed)
    db.flush()
    write_batch(db, job, units, 0)
    assert not search_retrieval.search(db, 'ZX-20480', [doc.id])['items']


@pytest.mark.parametrize('prefix', ['中', 'İ', 'K', 'ſ'])
def test_compound_identifier_boundaries_are_ascii_even_under_case_insensitive_matching(db, prefix):
    doc, version, _ = document(db)
    chunk(db, doc, version, prefix + 'ZX-2048')
    assert ids(search_retrieval.search(db, 'zx-2048', [doc.id])) == [doc.id]


@pytest.mark.parametrize('identifier', ['2026', '5053', 'ZX-2048'])
def test_generated_location_labels_never_establish_an_identifier_content_match(db, identifier):
    from backend.app.services.index_chunks import write_batch
    doc, version, record = document(db)
    units, _, job = persisted_units(db, doc, version, record, ['设备正常，与目标编号无关。'])
    units[0]['label'] = units[0]['context'] = f'第 {identifier} 行'
    write_batch(db, job, units, 0)
    assert not search_retrieval.search(db, identifier, [doc.id])['items']
    assert ids(search_retrieval.search(db, '第', [doc.id])) == [doc.id]


@pytest.mark.parametrize('identifier_in', ['body', 'context', 'label', 'bridge'])
def test_preexisting_safe_segment_json_keeps_identifier_body_bridge_only(db, identifier_in):
    doc, version, _ = document(db)
    names = ('body', 'context', 'label', 'bridge')
    segments = [{'text': '5053' if name == identifier_in else '普通内容',
                 'before': '', 'after': ''} for name in names]
    chunk(db, doc, version, '5053' if identifier_in == 'body' else '普通正文',
          context='5053' if identifier_in in ('context', 'bridge') else '普通位置',
          label='5053' if identifier_in == 'label' else '普通标签',
          search_segments_json=json.dumps(segments))
    result = search_retrieval.search(db, '5053', [doc.id])
    assert ids(result) == ([doc.id] if identifier_in in ('body', 'bridge') else [])


@pytest.mark.parametrize('legacy_json', [False, True])
@pytest.mark.parametrize('identifier', ['5053', 'ZX-2048'])
def test_real_workbook_sheet_name_is_identifier_evidence_without_generated_row_numbers(db, legacy_json, identifier):
    doc, version, _ = document(db)
    options = {}
    if legacy_json:
        options['search_segments_json'] = json.dumps([
            {'text': '设备状态正常', 'before': '', 'after': ''},
            {'text': identifier + ' · A1', 'before': '', 'after': ''},
            {'text': identifier + ' · A1', 'before': '', 'after': ''}])
    chunk(db, doc, version, '设备状态正常', label=identifier + ' · A1', context=identifier + ' · A1',
          location={'sheet': identifier, 'cell': 'A1', 'row': 1, 'column': 1}, **options)
    assert ids(search_retrieval.search(db, identifier, [doc.id])) == [doc.id]


def test_real_sheet_identifier_still_rejects_similar_longer_identifier(db):
    doc, version, _ = document(db)
    chunk(db, doc, version, '设备正常', label='ZX-20480 · A1', context='ZX-20480 · A1',
          location={'sheet': 'ZX-20480', 'cell': 'A1', 'row': 1, 'column': 1})
    assert not search_retrieval.search(db, 'ZX-2048', [doc.id])['items']
