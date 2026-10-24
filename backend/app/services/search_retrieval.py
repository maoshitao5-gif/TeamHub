"""Local, explainable lexical retrieval over authorized, current saved material.

SQLite finds and ranks matching rows before pagination. Python sees only one page
and at most three passages per document; no embedding model or network is used.
"""
import json
import re
import unicodedata

from sqlalchemy import (String, and_, case, cast, distinct, func, literal,
                        literal_column, or_, select, text, union_all)
from sqlalchemy.sql.selectable import SelectBase

from backend.app.card_models import DocumentCard, DocumentCardAnnotation
from backend.app.index_models import DocumentIndex, IndexText, IndexWorkItem
from backend.app.models import Document, Tag, Version, document_tag
from backend.app.services.card_schema import SCHEMA_VERSION as CARD_SCHEMA, SKILL_VERSION as CARD_SKILL
from backend.app.services.index_content import PARSER_VERSION
from backend.app.services.indexing import _CURRENT_METADATA_SQL

MAX_QUERY_LENGTH = 512
MAX_TERMS = 16
MAX_LIMIT = 100
MAX_PASSAGES = 3
MAX_EXCERPT = 500
_ACTIVE = ('pending', 'organized')
_IDENTIFIER = re.compile(r'(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9][A-Za-z0-9_.:/-]*\Z')
_WEIGHTS = {'content': 12, 'name': 10, 'tag': 8, 'description': 6, 'note': 5, 'card': 4, 'ai': 3}


def install_schema(engine):
    """Idempotently add trigram metadata indexes, maintaining them via triggers.

    The chunk index belongs to ``index_chunks.install_schema``. Existing text
    projections and their FTS index belong to ``indexing.install_schema``.
    """
    with engine.begin() as conn:
        for table, columns, fts in (
            ('documents', ('name', 'description'), 'search_document_fts'),
            ('tags', ('name',), 'search_tag_fts'),
        ):
            existed = conn.execute(text(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name"
            ), {'name': fts}).scalar()
            names = ', '.join(columns)
            conn.execute(text(f"CREATE VIRTUAL TABLE IF NOT EXISTS {fts} USING fts5("
                              f"{names}, content='{table}', content_rowid='rowid', tokenize='trigram')"))
            new = ', '.join(f'new.{column}' for column in columns)
            old = ', '.join(f'old.{column}' for column in columns)
            conn.execute(text(f'CREATE TRIGGER IF NOT EXISTS {fts}_ai AFTER INSERT ON {table} BEGIN '
                              f'INSERT INTO {fts}(rowid, {names}) VALUES(new.rowid, {new}); END'))
            conn.execute(text(f'CREATE TRIGGER IF NOT EXISTS {fts}_ad AFTER DELETE ON {table} BEGIN '
                              f"INSERT INTO {fts}({fts}, rowid, {names}) VALUES('delete', old.rowid, {old}); END"))
            conn.execute(text(f'CREATE TRIGGER IF NOT EXISTS {fts}_au AFTER UPDATE OF {names} ON {table} BEGIN '
                              f"INSERT INTO {fts}({fts}, rowid, {names}) VALUES('delete', old.rowid, {old}); "
                              f'INSERT INTO {fts}(rowid, {names}) VALUES(new.rowid, {new}); END'))
            if not existed:
                conn.execute(text(f"INSERT INTO {fts}({fts}) VALUES('rebuild')"))


def _terms(query):
    if not isinstance(query, str):
        raise ValueError('检索词必须是文字。')
    if len(query) > MAX_QUERY_LENGTH:
        raise ValueError(f'检索词不能超过 {MAX_QUERY_LENGTH} 字。')
    if any(unicodedata.category(char).startswith('C') for char in query):
        raise ValueError('检索词不能包含控制字符。')
    query = query.strip()
    # Preserve operator-looking legacy queries as one literal phrase. None of
    # these characters is ever sent to MATCH as executable FTS query syntax.
    parts = [query] if any(char in query for char in '"%*') else query.split()
    # Keep the actual spelling passed to SQLite's trigram tokenizer. Python's
    # expanding casefold (ß -> ss, İ -> i + combining dot) changes its tokens.
    # Only fold single-codepoint lowercases when deduplicating query terms.
    seen, result = set(), []
    for part in parts:
        key = ''.join(char.lower() if len(char.lower()) == 1 else char for char in part)
        if part and key not in seen:
            seen.add(key)
            result.append(part)
    if len(result) > MAX_TERMS:
        raise ValueError(f'一次检索最多 {MAX_TERMS} 个关键词。')
    return result


def _phrase(term):
    return '"' + term.replace('"', '""') + '"'


def _identifier(term):
    return bool(_IDENTIFIER.fullmatch(term) or term.isascii() and term.isdecimal())


def _boundary_chars(term):
    return '[0-9]' if term.isascii() and term.isdecimal() else '[A-Za-z0-9_-]'


def _matches(term, body, rowid, fts, column=None, segments=None):
    if len(term) >= 3:
        phrase = (column + ' : ' if column else '') + _phrase(term)
        rows = select(literal_column('rowid')).select_from(text(fts)).where(
            literal_column(fts).op('MATCH')(phrase))
        condition = rowid.in_(rows)
    else:
        escaped = term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        condition = body.like('%' + escaped + '%', escape='\\')
    if _identifier(term):
        # ASCII identifier boundaries distinguish ZX-2048 from ZX-20480, while
        # still accepting adjacent Chinese punctuation or prose.
        chars = _boundary_chars(term)
        pattern = r'(?ai)(?<!' + chars + ')' + re.escape(term) + '(?!' + chars + ')'
        if segments is None:
            exact = body.regexp_match(pattern)
        else:
            pieces = func.json_each(segments).table_valued('key', 'value').alias('search_segments')
            before = func.json_extract(pieces.c.value, '$.before')
            after = func.json_extract(pieces.c.value, '$.after')
            # A clipped edge is eligible only when the real adjacent source
            # character is a boundary. NULL means the source edge is unknown.
            start_guard = case((or_(before.is_(None), before.regexp_match(chars)), r'(?!\A)'), else_='')
            end_guard = case((or_(after.is_(None), after.regexp_match(chars)), r'(?!\Z)'), else_='')
            safe_pattern = literal('(?ai)') + start_guard + literal('(?<!' + chars + ')' + re.escape(term)
                               + '(?!' + chars + ')') + end_guard
            kind = func.json_extract(pieces.c.value, '$.kind')
            # Positions such as "row 5053" are generated labels, not an
            # occurrence of device 5053 in the saved source. Older safe JSON
            # used the fixed order body/context/label/bridge without kind.
            original = or_(kind.in_(('body', 'bridge', 'sheet_name')),
                           and_(kind.is_(None), pieces.c.key.in_((0, 3))))
            exact = select(literal(1)).select_from(pieces).where(original,
                func.json_extract(pieces.c.value, '$.text').regexp_match(safe_pattern)).exists()
        condition = and_(condition, exact)
    return condition


def _current_index():
    return and_(DocumentIndex.version_id == Version.id,
                DocumentIndex.content_hash == Version.sha256_hash,
                DocumentIndex.parser_version == PARSER_VERSION,
                DocumentIndex.state.in_(('ready', 'partial', 'processing')))


def _current_card():
    return and_(DocumentCard.version_id == Version.id,
                DocumentCard.content_hash == Version.sha256_hash,
                DocumentCard.parser_version == PARSER_VERSION,
                DocumentCard.skill_version == CARD_SKILL,
                DocumentCard.schema_version == CARD_SCHEMA)


def _current_work():
    return and_(IndexWorkItem.document_id == DocumentIndex.document_id,
                IndexWorkItem.version_id == Version.id,
                IndexWorkItem.generation == DocumentIndex.generation,
                IndexWorkItem.source_hash == Version.sha256_hash,
                IndexWorkItem.parser_version == PARSER_VERSION)


def _legacy_content():
    completed = select(IndexWorkItem.id).where(_current_work(), IndexWorkItem.stage == 'done',
        IndexWorkItem.cursor >= IndexWorkItem.total_units).correlate(DocumentIndex, Version).exists()
    return and_(_current_index(), DocumentIndex.state.in_(('ready', 'partial')), ~completed)


def _scope(document_ids):
    if document_ids is None:
        return literal(False)
    if isinstance(document_ids, SelectBase):
        return Document.id.in_(document_ids)
    # JSON is a single bound parameter even for a 10k-document authorized set.
    # SQL callers should pass their existing permission/filter Select directly.
    values = list(dict.fromkeys(str(value) for value in document_ids))
    rows = select(literal_column('value')).select_from(func.json_each(json.dumps(values)))
    return Document.id.in_(rows)


def _valid_text(kind):
    if kind == 'card':
        return select(DocumentCard.document_id).where(
            DocumentCard.document_id == Document.id, _current_card()).exists()
    if kind == 'note':
        return select(DocumentCardAnnotation.document_id).where(
            DocumentCardAnnotation.document_id == Document.id,
            DocumentCardAnnotation.source_version_id == Version.id).exists()
    if kind == 'content':
        return _legacy_content()
    return and_(_current_index(), DocumentIndex.state.in_(('ready', 'partial')),
                DocumentIndex.ai_json.isnot(None),
                DocumentIndex.ai_metadata_json == literal_column(_CURRENT_METADATA_SQL))


def _match_rows(terms, document_ids):
    from backend.app.services.index_chunks import current_chunks_query
    documents = select(Document.id).where(Document.status.in_(_ACTIVE), _scope(document_ids)).cte('authorized')
    # Keep this as a flattenable subquery: a multiply referenced CTE would
    # materialize every authorized chunk body before applying the FTS rowids.
    chunks = current_chunks_query().where(
        literal_column('index_chunks.document_id').in_(select(documents.c.id))).subquery('current_chunks')
    statements = []

    def row(doc_id, term_index, channel, key, *, ordinal=None):
        return select(doc_id.label('document_id'), literal(term_index).label('term_index'),
                      literal(channel).label('channel'), literal(_WEIGHTS[channel]).label('weight'),
                      key.label('source_key'),
                      (ordinal if ordinal is not None else literal(None)).label('ordinal'))

    for number, term in enumerate(terms):
        combined = chunks.c.body + literal('\n') + func.coalesce(chunks.c.context, '') + literal('\n') + chunks.c.label
        segments = func.coalesce(chunks.c.search_segments_json, func.json_array(
            func.json_object('text', chunks.c.body, 'before', '', 'after', ''),
            func.json_object('text', chunks.c.context, 'before', '', 'after', ''),
            func.json_object('text', chunks.c.label, 'before', '', 'after', '')))
        # The saved workbook sheet name is real source metadata. Append it
        # separately from generated row/cell labels, also for older safe JSON.
        segments = func.json_insert(segments, '$[#]', func.json_object(
            'kind', 'sheet_name', 'text', func.json_extract(chunks.c.location_json, '$.sheet'),
            'before', '', 'after', ''))
        statements.append(row(chunks.c.document_id, number, 'content', cast(chunks.c.id, String),
                              ordinal=chunks.c.ordinal)
                          .where(_matches(term, combined, chunks.c.id, 'index_chunk_fts', segments=segments)))
        for channel, column in (('name', Document.name), ('description', Document.description)):
            statements.append(row(Document.id, number, channel, literal(channel) + Document.id)
                              .where(Document.id.in_(select(documents.c.id)),
                                     _matches(term, column, literal_column('documents.rowid'),
                                              'search_document_fts', column.key)))
        statements.append(row(document_tag.c.document_id, number, 'tag', literal('tag:') + Tag.id)
                          .select_from(document_tag.join(Tag, Tag.id == document_tag.c.tag_id))
                          .where(document_tag.c.document_id.in_(select(documents.c.id)),
                                 _matches(term, Tag.name, literal_column('tags.rowid'), 'search_tag_fts', 'name')))
        for kind in ('content', 'card', 'note', 'ai'):
            statements.append(row(IndexText.document_id, number, kind, literal(kind + ':') + cast(IndexText.id, String))
                              .join(Document, Document.id == IndexText.document_id)
                              .join(Version, and_(Version.id == Document.current_version_id,
                                                  Version.document_id == Document.id))
                              .join(DocumentIndex, DocumentIndex.document_id == Document.id)
                              .where(Document.id.in_(select(documents.c.id)), IndexText.kind == kind, _valid_text(kind),
                                     _matches(term, IndexText.body, IndexText.id, 'index_text_fts')))
    return union_all(*statements).cte('matches')


def _load_passages(db, descriptors, terms):
    """Read bodies only after SQL has selected at most MAX_LIMIT * MAX_PASSAGES."""
    from backend.app.index_models import IndexChunk
    from backend.app.services.index_chunks import current_chunks_query
    payloads = {}

    def payload(body, label, *, context='', unit_id=None, ordinal=None, location=None):
        return {'unit_id': unit_id, 'location': location, 'label': label,
                'excerpt': _excerpt(body, context, terms), 'ordinal': ordinal}

    content_ids = [int(row['source_key']) for row in descriptors
                   if row['channel'] == 'content' and ':' not in row['source_key']]
    if content_ids:
        for chunk in db.scalars(current_chunks_query().where(IndexChunk.id.in_(content_ids))):
            payloads[str(chunk.id)] = payload(chunk.body, chunk.label, context=chunk.context,
                                              unit_id=chunk.unit_id, ordinal=chunk.ordinal,
                                              location=json.loads(chunk.location_json))
    metadata_ids = [row['document_id'] for row in descriptors if row['channel'] in ('name', 'description')]
    if metadata_ids:
        for row in db.execute(select(Document.id, Document.name, Document.description).where(
            Document.id.in_(metadata_ids), Document.status.in_(_ACTIVE))):
            payloads['name' + row.id] = payload(row.name, '名称')
            payloads['description' + row.id] = payload(row.description, '描述')
    tag_ids = [row['source_key'][4:] for row in descriptors if row['channel'] == 'tag']
    if tag_ids:
        for row in db.execute(select(Tag.id, Tag.name).where(Tag.id.in_(tag_ids))):
            payloads['tag:' + row.id] = payload(row.name, '标签')
    text_ids = [int(row['source_key'].split(':', 1)[1]) for row in descriptors
                if ':' in row['source_key'] and row['channel'] in ('content', 'card', 'note', 'ai')]
    if text_ids:
        rows = db.execute(select(IndexText.id, IndexText.kind, IndexText.body)
                          .join(Document, Document.id == IndexText.document_id)
                          .join(Version, and_(Version.id == Document.current_version_id,
                                              Version.document_id == Document.id))
                          .join(DocumentIndex, DocumentIndex.document_id == Document.id)
                          .where(IndexText.id.in_(text_ids), Document.status.in_(_ACTIVE), or_(
                              *(and_(IndexText.kind == kind, _valid_text(kind))
                                for kind in ('content', 'card', 'note', 'ai')))))
        labels = {'content': '原文（位置回填中）', 'card': '资料卡线索', 'note': '用户补充', 'ai': 'AI检索线索'}
        for row in rows:
            payloads[f'{row.kind}:{row.id}'] = payload(row.body, labels[row.kind])
    result = {}
    for row in descriptors:
        value = payloads.get(row['source_key'])
        if value is not None:
            result.setdefault(row['document_id'], []).append({**value, 'source_kind': row['channel']})
    return result


def _excerpt(body, context, terms):
    body, context = body or '', context or ''
    text_value = body
    def offsets_in(value):
        return [match.start() for term in terms
                if (match := re.search(re.escape(term), value, re.IGNORECASE))]

    offsets = offsets_in(body)
    if not offsets and offsets_in(context):
        text_value = context + '\n' + body
        offsets = offsets_in(text_value)
    start = max(0, min(offsets, default=0) - 100)
    # Keep the excerpt at the match, including a tail match after 4k characters.
    end = min(len(text_value), start + MAX_EXCERPT - (1 if start else 0))
    suffix = '…' if end < len(text_value) else ''
    return ('…' if start else '') + text_value[start:end - len(suffix)] + suffix


def _coverage(db, document_ids):
    rows = db.execute(select(
        Document.id, DocumentIndex.state, _current_index().label('current'),
        func.json_extract(DocumentIndex.result_json, '$.coverage').label('index_coverage'),
        func.json_extract(DocumentIndex.result_json, '$.gaps').label('gaps'),
        IndexWorkItem.stage, IndexWorkItem.cursor, IndexWorkItem.total_units,
    ).outerjoin(Version, and_(Version.id == Document.current_version_id, Version.document_id == Document.id))
        .outerjoin(DocumentIndex, DocumentIndex.document_id == Document.id)
        .outerjoin(IndexWorkItem, _current_work())
        .where(Document.id.in_(document_ids))).mappings()
    result = {}
    for row in rows:
        coverage = json.loads(row['index_coverage']) if row['current'] and row['index_coverage'] else {}
        gaps = json.loads(row['gaps']) if row['current'] and row['gaps'] else []
        state = row['state'] if row['current'] else ('stale' if row['state'] else 'missing')
        publishing = bool(row['current'] and row['stage'] is not None
                          and (row['stage'] != 'done' or row['cursor'] < row['total_units']))
        result[row['id']] = {'state': state, 'index_state': state, 'current_index': bool(row['current']),
                             'complete': state == 'ready' and not gaps and not publishing,
                             'processed': coverage.get('processed'), 'total': coverage.get('total'),
                             'failed': coverage.get('failed'), 'unit': coverage.get('unit'), 'gaps': gaps}
        if publishing:
            result[row['id']]['processing_progress'] = {
                'stage': row['stage'], 'cursor': row['cursor'], 'total_units': row['total_units']}
    return result


def search(db, query, document_ids, limit=10, offset=0, *, sort_by='relevance', sort_order='desc'):
    """Return an exact-count, stable page of authorized lexical matches.

    Ordinary whitespace combines up to 16 literal terms. All explicit device
    identifiers must match exactly; other terms may partially match, with full
    term coverage ranked first. There is no candidate-count truncation: SQLite
    aggregates every authorized match and fetches only the requested page.
    ``has_more`` therefore describes actual additional matching documents.
    ``source_kind=content`` denotes saved original text; every other passage is
    a metadata or derived clue and deliberately has no original-text location.
    """
    terms = _terms(query)
    sort_columns = {'name': Document.name, 'updated_at': Document.updated_at,
                    'created_at': Document.created_at, 'total_size': Document.total_size}
    if sort_by not in {'relevance', *sort_columns} or sort_order not in {'asc', 'desc'}:
        raise ValueError('检索排序参数无效。')
    limit, offset = max(1, min(int(limit), MAX_LIMIT)), max(0, int(offset))
    empty = {'items': [], 'total': 0, 'has_more': False, 'next_offset': None,
             'mode': 'lexical', 'query_terms': terms}
    if not terms:
        return empty
    matches = _match_rows(terms, document_ids)
    # Repeated occurrences and long documents cannot inflate a channel's score.
    channels = select(matches.c.document_id, matches.c.term_index, matches.c.channel,
                      func.max(matches.c.weight).label('weight')).group_by(
        matches.c.document_id, matches.c.term_index, matches.c.channel).cte('term_channels')
    term_count = func.count(distinct(channels.c.term_index))
    score = (term_count * 1000 + func.sum(channels.c.weight)).label('score')
    ranked = select(channels.c.document_id, term_count.label('term_count'), score).group_by(channels.c.document_id)
    identifier_terms = [number for number, term in enumerate(terms) if _identifier(term)]
    if identifier_terms:
        ranked = ranked.having(func.count(distinct(case(
            (channels.c.term_index.in_(identifier_terms), channels.c.term_index)))) == len(identifier_terms))
    ranked = ranked.cte('ranked_documents')
    total = db.scalar(select(func.count()).select_from(ranked)) or 0
    if not total or offset >= total:
        return {**empty, 'total': total}
    primary = ranked.c.score if sort_by == 'relevance' else sort_columns[sort_by]
    order = primary.desc() if sort_order == 'desc' else primary.asc()
    page = db.execute(select(ranked).join(Document, Document.id == ranked.c.document_id)
                      .order_by(order, Document.id.asc()).offset(offset).limit(limit)).mappings().all()
    page_ids = [row['document_id'] for row in page]
    # Rank source rows in SQLite, returning at most three bodies per document.
    sources = select(matches.c.document_id, matches.c.source_key,
                     func.min(matches.c.channel).label('channel'),
                     func.count(distinct(matches.c.term_index)).label('term_count'),
                     func.max(matches.c.weight).label('weight'), func.min(matches.c.ordinal).label('ordinal')).where(
        matches.c.document_id.in_(page_ids)).group_by(matches.c.document_id, matches.c.source_key).cte('sources')
    passages = select(sources, func.row_number().over(partition_by=sources.c.document_id, order_by=(
        sources.c.term_count.desc(), sources.c.weight.desc(), sources.c.ordinal.asc(),
        sources.c.source_key.asc())).label('position')).cte('passages')
    passage_rows = db.execute(select(passages).where(passages.c.position <= MAX_PASSAGES)
                              .order_by(passages.c.document_id, passages.c.position)).mappings().all()
    by_document = _load_passages(db, passage_rows, terms)
    matched = db.execute(select(channels.c.document_id, channels.c.channel, channels.c.term_index).where(
        channels.c.document_id.in_(page_ids))).all()
    details = {document_id: {'channels': set(), 'terms': set()} for document_id in page_ids}
    for document_id, channel, term_index in matched:
        details[document_id]['channels'].add(channel)
        details[document_id]['terms'].add(term_index)
    coverage = _coverage(db, page_ids)
    items = []
    for row in page:
        document_id = row['document_id']
        detail = details[document_id]
        items.append({'document_id': document_id, 'score': row['score'],
                      'channels': sorted(detail['channels'], key=lambda channel: -_WEIGHTS[channel]),
                      'relevance': {'matched_terms': [term for number, term in enumerate(terms) if number in detail['terms']],
                                    'matched_term_count': row['term_count'], 'query_term_count': len(terms)},
                      'passages': by_document.get(document_id, []), 'coverage': coverage[document_id]})
    has_more = offset + len(items) < total
    return {'items': items, 'total': total, 'has_more': has_more,
            'next_offset': offset + len(items) if has_more else None, 'mode': 'lexical', 'query_terms': terms}
