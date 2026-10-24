"""分块发布在临时文件库中验证；不读取用户库、不调用模型。"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest
from sqlalchemy import select, text

from backend.app import database
from backend.app.config import settings
from backend.app.models import Document, Version
from backend.app.services import index_content, indexing, versions


@pytest.fixture
def library(tmp_path, monkeypatch):
    path = tmp_path / 'library'
    path.mkdir()
    monkeypatch.setattr(settings, 'library_path', str(path))
    monkeypatch.setattr(settings, 'pending_path', '')
    database.rebind_engine()
    database.init_db()
    yield path
    indexing.stop_worker()
    assert database.active_session_count() == 0


def saved(library, content='一行\n二行\n三行\n四行\n最后一行'):
    path = library / 'report.txt'
    path.write_text(content)
    with database.SessionLocal() as db:
        doc = Document(name='report.txt', storage_path='report.txt', status='pending', storage_mode='copy')
        db.add(doc)
        db.flush()
        version, _ = versions.snapshot_working_copy(db, doc)
        db.commit()
        return doc.id, version.id, path


def status(document_id):
    with database.SessionLocal() as db:
        return indexing.status(db, document_id)


def chunks(document_id):
    from backend.app.services.index_chunks import current_chunks_query
    from backend.app.index_models import IndexChunk
    with database.SessionLocal() as db:
        return list(db.scalars(current_chunks_query().where(
            IndexChunk.document_id == document_id).order_by(IndexChunk.ordinal)))


def finish():
    for _ in range(10):
        if not indexing.process_one():
            return
    pytest.fail('index queue did not finish')


def test_bounded_publish_survives_process_restart_without_reparse(library, monkeypatch):
    from backend.app.index_models import IndexWorkItem
    monkeypatch.setattr(indexing, 'CHUNK_BATCH_SIZE', 2)
    doc_id, version_id, _ = saved(library)
    assert indexing.process_one()
    first = status(doc_id)
    assert first['state'] == 'processing'
    assert first['processing_progress'] == {'stage': 'publish', 'cursor': 2, 'total_units': 5}
    assert [chunk.body for chunk in chunks(doc_id)] == ['一行', '二行']
    with database.SessionLocal() as db:
        assert indexing.load_record(db, doc_id) is None
        work = db.scalars(select(IndexWorkItem)).one()
        assert work.parsed_result_json and work.cursor == 2
    database.rebind_engine()
    database.init_db()
    monkeypatch.setattr(index_content, 'extract_index', lambda *a: pytest.fail('completed parse repeated'))
    monkeypatch.setattr(indexing, '_snapshot_bytes', lambda *a: pytest.fail('snapshot reread during publish'))
    restarted = subprocess.run([sys.executable, '-c', '''
import sys
from backend.app.config import settings
settings.library_path = sys.argv[1]
settings.pending_path = ''
from backend.app import database
from backend.app.services import indexing, index_content
database.init_db()
def forbidden(*args):
    raise AssertionError('persisted parse must not be repeated after process restart')
index_content.extract_index = forbidden
indexing._snapshot_bytes = forbidden
indexing.CHUNK_BATCH_SIZE = 2
for _ in range(10):
    if not indexing.process_one():
        break
else:
    raise AssertionError('queue did not finish')
''', str(library)], cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=20)
    assert restarted.returncode == 0, restarted.stdout + restarted.stderr
    result = status(doc_id)
    assert result['state'] == 'ready'
    assert result['unit_count'] == 5
    rows = chunks(doc_id)
    assert len(rows) == 5 and len({row.unit_id for row in rows}) == 5
    assert [json.loads(row.location_json)['line'] for row in rows] == [1, 2, 3, 4, 5]
    assert all(row.version_id == version_id and row.source_hash == result['content_hash'] for row in rows)
    assert all(row.content_hash == hashlib.sha256(row.body.encode()).hexdigest() for row in rows)
    with database.SessionLocal() as db:
        assert db.scalars(select(IndexWorkItem)).one().stage == 'done'
        assert indexing.load_record(db, doc_id)['units'] == result['units']


def test_failed_publish_rolls_back_batch_and_resumes_expired_lease(library, monkeypatch):
    from backend.app.index_models import IndexWorkItem
    from backend.app.services import index_chunks
    monkeypatch.setattr(indexing, 'CHUNK_BATCH_SIZE', 2)
    doc_id, _, _ = saved(library)
    assert indexing.process_one()
    write = index_chunks.write_batch

    def interrupted(db, job, units, start):
        write(db, job, units[:1], start)
        raise RuntimeError('simulated disk failure')

    monkeypatch.setattr(index_chunks, 'write_batch', interrupted)
    with pytest.raises(RuntimeError, match='disk failure'):
        indexing.process_one()
    assert [row.body for row in chunks(doc_id)] == ['一行', '二行']
    assert status(doc_id)['processing_progress']['cursor'] == 2
    with database.SessionLocal() as db:
        work = db.scalars(select(IndexWorkItem)).one()
        work.lease_until = time.time() - 1
        db.commit()
    database.rebind_engine()
    database.init_db()
    monkeypatch.setattr(index_chunks, 'write_batch', write)
    monkeypatch.setattr(index_content, 'extract_index', lambda *a: pytest.fail('failed batch reparsed source'))
    finish()
    assert len(chunks(doc_id)) == 5
    assert status(doc_id)['state'] == 'ready'


def test_live_foreign_lease_is_not_stolen_and_expired_publish_resumes(library, monkeypatch):
    from backend.app.index_models import IndexWorkItem
    monkeypatch.setattr(indexing, 'CHUNK_BATCH_SIZE', 2)
    doc_id, _, _ = saved(library)
    assert indexing.process_one()
    with database.SessionLocal() as db:
        work = db.scalars(select(IndexWorkItem)).one()
        work.state = 'processing'
        work.lease_token = 'another-process'
        work.lease_until = time.time() + 60
        db.commit()
    monkeypatch.setattr(index_content, 'extract_index', lambda *a: pytest.fail('persisted source reparsed'))
    assert not indexing.process_one()
    assert len(chunks(doc_id)) == 2
    with database.SessionLocal() as db:
        db.scalars(select(IndexWorkItem)).one().lease_until = time.time() - 1
        db.commit()
    finish()
    assert len(chunks(doc_id)) == 5


def test_generation_version_trash_and_delete_hide_stale_chunks(library, monkeypatch):
    from backend.app.index_models import IndexChunk, IndexWorkItem
    monkeypatch.setattr(indexing, 'CHUNK_BATCH_SIZE', 2)
    doc_id, _, path = saved(library)
    assert indexing.process_one()
    old_generation = status(doc_id)['generation']
    with database.SessionLocal() as db:
        indexing.rebuild(db, doc_id)
        assert not db.scalars(select(IndexChunk)).all()
    assert not chunks(doc_id)
    assert indexing.process_one()
    assert all(row.generation > old_generation for row in chunks(doc_id))
    path.write_text('新版内容')
    with database.SessionLocal() as db:
        versions.snapshot_working_copy(db, db.get(Document, doc_id))
        db.commit()
    assert not chunks(doc_id)
    finish()
    assert [row.body for row in chunks(doc_id)] == ['新版内容']
    with database.SessionLocal() as db:
        db.get(Document, doc_id).status = 'trashed'
        db.commit()
    assert not chunks(doc_id)
    with database.SessionLocal() as db:
        db.delete(db.get(Document, doc_id))
        db.commit()
        assert not db.scalars(select(IndexChunk)).all()
        assert not db.scalars(select(IndexWorkItem)).all()
        assert not db.execute(text('SELECT rowid FROM index_chunk_fts')).all()


def test_legacy_result_backfills_in_batches_without_reparsing(library, monkeypatch):
    from backend.app.index_models import DocumentIndex, IndexWorkItem
    monkeypatch.setattr(indexing, 'CHUNK_BATCH_SIZE', 2)
    doc_id, version_id, path = saved(library)
    parsed = index_content.extract_index(path.read_bytes(), path.name)
    with database.SessionLocal() as db:
        version = db.get(Version, version_id)
        db.add(DocumentIndex(document_id=doc_id, version_id=version_id,
                             content_hash=version.sha256_hash, parser_version=indexing.PARSER_VERSION,
                             generation=4, state='ready', result_json=json.dumps(parsed)))
        db.commit()
    monkeypatch.setattr(index_content, 'extract_index', lambda *a: pytest.fail('legacy index reparsed'))
    monkeypatch.setattr(indexing, '_snapshot_bytes', lambda *a: pytest.fail('legacy snapshot reread'))
    assert indexing.process_one()
    assert len(chunks(doc_id)) == 2
    assert status(doc_id)['state'] == 'ready'
    finish()
    assert len(chunks(doc_id)) == 5
    with database.SessionLocal() as db:
        assert db.get(DocumentIndex, doc_id).generation == 4
        assert db.scalars(select(IndexWorkItem)).one().cursor == 5


def test_partial_parser_coverage_and_locations_survive_chunks(library, monkeypatch):
    monkeypatch.setattr(index_content, 'MAX_UNITS', 3)
    monkeypatch.setattr(indexing, 'CHUNK_BATCH_SIZE', 2)
    doc_id, _, _ = saved(library)
    finish()
    result = status(doc_id)
    assert result['state'] == 'partial'
    assert result['coverage'] == {'total': 5, 'processed': 3, 'failed': 2, 'unit': 'lines'}
    assert len(chunks(doc_id)) == 3
    assert any(gap['reason'] == 'unit_limit' for gap in result['gaps'])
    assert chunks(doc_id)[-1].label == '第 3 行'


def test_schema_installation_is_idempotent_and_fts_tracks_chunk_updates(library):
    from backend.app.index_models import IndexChunk
    doc_id, _, _ = saved(library, '可定位的关键词')
    finish()
    indexing.install_schema(database.engine)
    indexing.install_schema(database.engine)
    with database.SessionLocal() as db:
        assert db.execute(text("SELECT rowid FROM index_chunk_fts WHERE index_chunk_fts MATCH :q"),
                          {'q': '"关键词"'}).all()
        chunk = db.scalars(select(IndexChunk)).one()
        chunk.body = '更新后的正文'
        db.commit()
        assert not db.execute(text("SELECT rowid FROM index_chunk_fts WHERE index_chunk_fts MATCH :q"),
                              {'q': '"关键词"'}).all()
        assert db.execute(text("SELECT rowid FROM index_chunk_fts WHERE index_chunk_fts MATCH :q"),
                          {'q': '"更新后"'}).all()


def test_contiguous_units_bridge_fts_across_batch_boundaries(library, monkeypatch):
    monkeypatch.setattr(indexing, 'CHUNK_BATCH_SIZE', 1)
    prefix = '文' * (index_content.UNIT_TEXT_CHARS - 3)
    doc_id, _, _ = saved(library, prefix + 'SERIAL-123456')
    assert indexing.process_one()
    first = chunks(doc_id)[0]
    assert indexing.process_one()
    with database.SessionLocal() as db:
        hits = db.execute(text(
            'SELECT rowid FROM index_chunk_fts WHERE index_chunk_fts MATCH :query'),
            {'query': '"SERIAL-123456"'}).scalars().all()
        assert first.id in hits
    assert chunks(doc_id)[0].body == prefix + 'SER'
    # 两个独立行不会制造原文中不存在的连续短语。
    other_id, _, _ = saved(library, 'prefixSER\nIAL-123456')
    finish()
    with database.SessionLocal() as db:
        assert not db.execute(text('''SELECT index_chunks.id FROM index_chunk_fts
            JOIN index_chunks ON index_chunks.id=index_chunk_fts.rowid
            WHERE index_chunk_fts MATCH :query AND index_chunks.document_id=:document_id'''),
            {'query': '"SERIAL-123456"', 'document_id': other_id}).all()


def test_legacy_chunk_table_migrates_real_fragment_boundaries_idempotently(library, monkeypatch):
    from backend.app.index_models import IndexChunk
    from backend.app.services import index_chunks, search_retrieval
    monkeypatch.setattr(indexing, 'CHUNK_BATCH_SIZE', 1)
    query = 'ZX-20480'
    doc_id, _, _ = saved(library, '文' * (4000 - len(query)) + query + '9 尾部')
    assert indexing.process_one()
    with database.engine.begin() as conn:
        conn.execute(text('ALTER TABLE index_chunks DROP COLUMN search_segments_json'))
    # A real upgrade starts from the old table shape, with an unfinished work
    # item retaining the full parse but only one published fragment.
    index_chunks.install_schema(database.engine)
    index_chunks.install_schema(database.engine)
    with database.SessionLocal() as db:
        row = db.scalars(select(IndexChunk)).one()
        assert row.search_segments_json
        assert json.loads(row.search_segments_json)[0]['after'] == '9'
        assert not search_retrieval.search(db, query, [doc_id])['items']
        found = search_retrieval.search(db, query + '9', [doc_id])
        assert found['items'][0]['passages'][0]['ordinal'] == 0
    finish()
    with database.SessionLocal() as db:
        assert not search_retrieval.search(db, query, [doc_id])['items']
        assert search_retrieval.search(db, query + '9', [doc_id])['total'] == 1


def test_ready_legacy_fulltext_fallback_has_no_fake_read_position(library, monkeypatch):
    from backend.app.index_models import DocumentIndex, IndexText
    from backend.app.services import search_retrieval
    monkeypatch.setattr(indexing, 'CHUNK_BATCH_SIZE', 1)
    doc_id, version_id, path = saved(library, '前部正文\n尾部专属needle')
    parsed = index_content.extract_index(path.read_bytes(), path.name)
    with database.SessionLocal() as db:
        version = db.get(Version, version_id)
        db.add(DocumentIndex(document_id=doc_id, version_id=version_id,
                             content_hash=version.sha256_hash, parser_version=indexing.PARSER_VERSION,
                             generation=4, state='ready', result_json=json.dumps(parsed)))
        db.flush()
        db.add(IndexText(document_id=doc_id, kind='content', body=indexing._search_body(parsed['units'])))
        db.commit()
    for backfill_started in (False, True):
        if backfill_started:
            assert indexing.process_one()
        with database.SessionLocal() as db:
            item = search_retrieval.search(db, 'needle', [doc_id])['items'][0]
            assert item['passages'][0]['ordinal'] is None
            assert item['passages'][0]['location'] is None
            if backfill_started:
                assert item['coverage']['complete'] is False
    finish()
    with database.SessionLocal() as db:
        item = search_retrieval.search(db, 'needle', [doc_id])['items'][0]
        assert item['passages'][0]['ordinal'] == 1
        assert item['coverage']['complete'] is True


def test_migration_cannot_invent_end_boundary_for_short_budget_clipped_fragment(library, monkeypatch):
    from backend.app.index_models import DocumentIndex, IndexChunk, IndexText, IndexWorkItem
    from backend.app.services import index_chunks, search_retrieval
    monkeypatch.setattr(index_content, 'MAX_TEXT_CHARS', 13)
    doc_id, _, _ = saved(library, '前文五字长\nZX-204809')
    finish()
    with database.SessionLocal() as db:
        row = db.scalars(select(IndexChunk).where(IndexChunk.document_id == doc_id,
                                                IndexChunk.ordinal == 1)).one()
        assert row.body == 'ZX-20480'
        assert json.loads(row.location_json) == {'line': 2, 'char_start': 1, 'char_end': 8}
        # Simulate an older chunk table whose authoritative derived parse was
        # lost. The original source is longer than this short ranged fragment.
        db.get(DocumentIndex, doc_id).result_json = None
        db.scalars(select(IndexWorkItem).where(IndexWorkItem.document_id == doc_id)).one().parsed_result_json = None
        for projection in db.scalars(select(IndexText).where(IndexText.document_id == doc_id)):
            db.delete(projection)
        db.commit()
    with database.engine.begin() as conn:
        conn.execute(text('ALTER TABLE index_chunks DROP COLUMN search_segments_json'))
    index_chunks.install_schema(database.engine)
    index_chunks.install_schema(database.engine)
    with database.SessionLocal() as db:
        assert not search_retrieval.search(db, 'ZX-20480', [doc_id])['items']
        assert search_retrieval.search(db, '前文', [doc_id])['total'] == 1
        row = db.scalars(select(IndexChunk).where(IndexChunk.document_id == doc_id,
                                                IndexChunk.ordinal == 1)).one()
        assert json.loads(row.search_segments_json)[0]['after'] is None
