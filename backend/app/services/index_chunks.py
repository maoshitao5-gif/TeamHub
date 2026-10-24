"""分块派生数据与 FTS；所有读方必须核验当前快照和 generation。"""
import hashlib
import json

from sqlalchemy import and_, select, text, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from backend.app.index_models import DocumentIndex, IndexChunk, IndexWorkItem
from backend.app.models import Document, Version
from .index_content import PARSER_VERSION


def install_schema(engine):
    # install_schema 也可独立运行在升级前的数据库上。
    IndexChunk.__table__.create(engine, checkfirst=True)
    IndexWorkItem.__table__.create(engine, checkfirst=True)
    with engine.begin() as conn:
        columns = {row[1] for row in conn.execute(text('PRAGMA table_info(index_chunks)'))}
        if 'search_segments_json' not in columns:
            conn.execute(text('ALTER TABLE index_chunks ADD COLUMN search_segments_json TEXT'))
        existed = conn.execute(text(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='index_chunk_fts'"
        )).scalar()
        conn.execute(text("""CREATE VIRTUAL TABLE IF NOT EXISTS index_chunk_fts USING fts5(
            body, context, label, content='index_chunks', content_rowid='id', tokenize='trigram'
        )"""))
        conn.execute(text("""CREATE TRIGGER IF NOT EXISTS index_chunks_ai AFTER INSERT ON index_chunks BEGIN
            INSERT INTO index_chunk_fts(rowid, body, context, label)
            VALUES (new.id, new.body, new.context, new.label);
        END"""))
        conn.execute(text("""CREATE TRIGGER IF NOT EXISTS index_chunks_ad AFTER DELETE ON index_chunks BEGIN
            INSERT INTO index_chunk_fts(index_chunk_fts, rowid, body, context, label)
            VALUES ('delete', old.id, old.body, old.context, old.label);
        END"""))
        conn.execute(text("""CREATE TRIGGER IF NOT EXISTS index_chunks_au AFTER UPDATE ON index_chunks BEGIN
            INSERT INTO index_chunk_fts(index_chunk_fts, rowid, body, context, label)
            VALUES ('delete', old.id, old.body, old.context, old.label);
            INSERT INTO index_chunk_fts(rowid, body, context, label)
            VALUES (new.id, new.body, new.context, new.label);
        END"""))
        if not existed:
            conn.execute(text("INSERT INTO index_chunk_fts(index_chunk_fts) VALUES ('rebuild')"))
        # Older chunk tables did not preserve the real boundaries of clipped
        # bodies/bridges. Rebuild their projections from persisted parse units.
        with Session(bind=conn) as db:
            sources = db.execute(select(IndexChunk.document_id, IndexChunk.version_id,
                IndexChunk.generation, IndexChunk.source_hash).where(
                IndexChunk.search_segments_json.is_(None)).distinct()).all()
            for doc_id, version_id, generation, digest in sources:
                job = {'document_id': doc_id, 'version_id': version_id,
                       'generation': generation, 'content_hash': digest}
                parsed = _source_result(db, job)
                scope = and_(IndexChunk.document_id == doc_id, IndexChunk.version_id == version_id,
                             IndexChunk.generation == generation)
                units = {i: unit for i, unit in enumerate(parsed['units'])} if parsed else {
                    row.ordinal: {**as_unit(row), 'context': row.context.partition('\n\n')[0]}
                    for row in db.scalars(select(IndexChunk).where(scope))}
                # Read one document's source and scalar row descriptors at a
                # time; migration never loads every library body into memory.
                for chunk_id, ordinal in db.execute(select(IndexChunk.id, IndexChunk.ordinal).where(
                        scope, IndexChunk.search_segments_json.is_(None))).all():
                    if ordinal in units:
                        _refresh_projection(db, chunk_id, ordinal, units, parsed)
            db.flush()


def current_chunks_query():
    """已落盘的当前块可立即检索；processing 不代表全部原文均已发布。"""
    return (select(IndexChunk).join(DocumentIndex,
            DocumentIndex.document_id == IndexChunk.document_id)
            .join(Document, Document.id == IndexChunk.document_id)
            .join(Version, and_(Version.id == Document.current_version_id,
                               Version.document_id == Document.id))
            .where(Document.status.in_(('pending', 'organized')),
                   DocumentIndex.state.in_(('processing', 'ready', 'partial')),
                   DocumentIndex.version_id == Version.id,
                   DocumentIndex.content_hash == Version.sha256_hash,
                   DocumentIndex.parser_version == PARSER_VERSION,
                   IndexChunk.version_id == Version.id,
                   IndexChunk.generation == DocumentIndex.generation,
                   IndexChunk.source_hash == Version.sha256_hash,
                   IndexChunk.parser_version == PARSER_VERSION))


def _source_result(db, job):
    """Only the exact source generation may supply unpublished neighbours."""
    work = db.scalar(select(IndexWorkItem.parsed_result_json).where(
        IndexWorkItem.document_id == job['document_id'],
        IndexWorkItem.version_id == job['version_id'],
        IndexWorkItem.generation == job['generation'],
        IndexWorkItem.source_hash == job['content_hash'],
        IndexWorkItem.parser_version == PARSER_VERSION,
        IndexWorkItem.parsed_result_json.isnot(None)))
    value = work or db.scalar(select(DocumentIndex.result_json).where(
        DocumentIndex.document_id == job['document_id'],
        DocumentIndex.version_id == job['version_id'],
        DocumentIndex.generation == job['generation'],
        DocumentIndex.content_hash == job['content_hash'],
        DocumentIndex.parser_version == PARSER_VERSION))
    if value:
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            # A corrupt legacy derived result is not an authoritative source
            # for boundaries; discovery can queue it for a fresh parse.
            return None
        if isinstance(parsed, dict) and isinstance(parsed.get('units'), list):
            return parsed
    return None


def _anchor(location):
    return {key: val for key, val in location.items() if key not in ('char_start', 'char_end')}


def _contiguous(left, right):
    return bool(left and right and right['location'].get('char_start', 1) > 1
                and left['location'].get('char_end') == right['location']['char_start'] - 1
                and _anchor(left['location']) == _anchor(right['location']))


def _after(unit, following, parsed):
    if _contiguous(unit, following):
        return following['text'][:1]
    # Unknown omitted source cannot establish an exact identifier boundary.
    if not parsed and 'char_end' in unit['location']:
        return None
    if parsed and any(_anchor(gap['location']) == _anchor(unit['location'])
                      and gap['location'].get('char_start') == unit['location'].get('char_end', 0) + 1
                      for gap in parsed.get('gaps', [])):
        return None
    return ''


def _refresh_projection(db, chunk_id, ordinal, units, parsed):
    unit = units[ordinal]
    previous, following, successor = (units.get(ordinal + delta) for delta in (-1, 1, 2))
    before = previous['text'][-1:] if _contiguous(previous, unit) else (
        None if unit['location'].get('char_start', 1) > 1 else '')
    after = _after(unit, following, parsed)
    segments = [{'kind': 'body', 'text': unit['text'], 'before': before, 'after': after},
                {'kind': 'context', 'text': unit['context'], 'before': '', 'after': ''},
                {'kind': 'label', 'text': unit['label'], 'before': '', 'after': ''}]
    context = unit['context']
    if _contiguous(unit, following):
        left, right = unit['text'][-511:], following['text'][:511]
        bridge_before = unit['text'][-512:-511] if len(unit['text']) > 511 else before
        bridge_after = following['text'][511:512] if len(following['text']) > 511 else _after(
            following, successor, parsed)
        segments.append({'kind': 'bridge', 'text': left + right,
                         'before': bridge_before, 'after': bridge_after})
        context += '\n\n' + left + right
    db.execute(update(IndexChunk).where(IndexChunk.id == chunk_id).values(
        context=context, search_segments_json=json.dumps(segments, ensure_ascii=False, separators=(',', ':'))))


def write_batch(db, job, units, start):
    """与游标在调用方同一事务中提交；重放同一批次不会产生重复块。"""
    values = [dict(document_id=job['document_id'], version_id=job['version_id'],
                   generation=job['generation'], unit_id=unit['id'], ordinal=start + offset,
                   kind=unit['kind'], body=unit['text'], context=unit['context'], label=unit['label'],
                   location_json=json.dumps(unit['location'], ensure_ascii=False, separators=(',', ':')),
                   content_hash=hashlib.sha256(unit['text'].encode('utf-8')).hexdigest(),
                   source_hash=job['content_hash'], parser_version=PARSER_VERSION)
              for offset, unit in enumerate(units)]
    if values:
        statement = insert(IndexChunk).values(values)
        db.execute(statement.on_conflict_do_nothing(
            index_elements=['document_id', 'version_id', 'generation', 'unit_id']))
        parsed = _source_result(db, job)
        nearby = db.scalars(select(IndexChunk).where(
            IndexChunk.document_id == job['document_id'], IndexChunk.version_id == job['version_id'],
            IndexChunk.generation == job['generation'],
            IndexChunk.ordinal.between(max(0, start - 2), start + len(units) + 1))).all()
        source_units = {i: unit for i, unit in enumerate(parsed['units'])} if parsed else {
            row.ordinal: {**as_unit(row), 'context': row.context.partition('\n\n')[0]} for row in nearby}
        source_units.update({start + i: unit for i, unit in enumerate(units)})
        for row in nearby:
            if start - 1 <= row.ordinal < start + len(units):
                _refresh_projection(db, row.id, row.ordinal, source_units, parsed)


def as_unit(chunk):
    return {'id': chunk.unit_id, 'kind': chunk.kind, 'text': chunk.body,
            'location': json.loads(chunk.location_json), 'label': chunk.label, 'context': chunk.context}
