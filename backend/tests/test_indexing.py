"""可重建正文索引：仅用临时库及不可变版本，不启动用户服务。"""
from pathlib import Path
import threading

import pytest
from sqlalchemy import select, text

from backend.app import database
from backend.app.config import settings
from backend.app.models import Document, Tag, Version
from backend.app.services import versions


@pytest.fixture
def indexed_library(tmp_path, monkeypatch):
    library = tmp_path / 'library'
    library.mkdir()
    monkeypatch.setattr(settings, 'library_path', str(library))
    monkeypatch.setattr(settings, 'pending_path', '')
    database.rebind_engine()
    database.init_db()
    yield library
    from backend.app.services import indexing
    indexing.stop_worker()
    assert database.active_session_count() == 0


def _saved(library, content='正文末端关键词', *, name='report.txt', folder=False):
    path = library / name
    if folder:
        path.mkdir()
        (path / 'inside.txt').write_text(content)
    else:
        path.write_text(content)
    with database.SessionLocal() as db:
        doc = Document(name=name, storage_path=name, status='pending', storage_mode='copy',
                       is_folder=folder, doc_type='folder' if folder else 'file')
        db.add(doc)
        db.flush()
        version, _ = versions.snapshot_working_copy(db, doc)
        db.commit()
        return doc.id, version.id, path


def _status(doc_id, **kwargs):
    from backend.app.services import indexing
    with database.SessionLocal() as db:
        return indexing.status(db, doc_id, **kwargs)


def _matches(keyword):
    from backend.app.services import indexing
    with database.SessionLocal() as db:
        return list(db.scalars(select(Document.id).where(
            Document.id.in_(indexing.content_document_ids(keyword)))))


def test_parser_v1_index_is_requeued_and_rebuilt_with_current_contract(indexed_library):
    from backend.app.index_models import DocumentIndex
    from backend.app.services import index_content, indexing
    assert indexing.PARSER_VERSION == index_content.PARSER_VERSION == '2'
    doc_id, _, _ = _saved(indexed_library, '正文升级后仍可检索')
    assert indexing.process_one()
    with database.SessionLocal() as db:
        old = db.get(DocumentIndex, doc_id)
        generation = old.generation
        old.parser_version = '1'
        db.commit()
    assert not _matches('升级后')
    queued = _status(doc_id)
    assert queued['state'] == 'queued' and queued['parser_version'] == '2'
    assert queued['skill_version'] == index_content.SKILL_VERSION
    assert queued['generation'] > generation
    assert indexing.process_one()
    rebuilt = _status(doc_id)
    assert rebuilt['state'] == 'ready' and rebuilt['parser_version'] == '2'
    assert _matches('升级后') == [doc_id]


def test_saved_snapshot_tail_is_searchable_and_working_edits_are_not(indexed_library):
    from backend.app.services import indexing
    doc_id, version_id, work = _saved(indexed_library, '开头' * 10000 + '\nZX-2048 尾部检索')
    work.write_text('尚未保存的工作副本')
    assert indexing.process_one()
    result = _status(doc_id)
    assert result['state'] == 'ready'
    assert result['version_id'] == version_id
    assert result['source'] == 'saved_version'
    assert result['unit_count'] > 0
    assert _matches('ZX-2048') == [doc_id]
    assert _matches('尾部') == [doc_id]
    assert _matches('工作副本') == []
    assert not indexing.process_one()


def test_configured_library_root_alias_can_read_its_own_snapshot(indexed_library, tmp_path):
    from backend.app.services import indexing
    alias = tmp_path / 'library-alias'
    alias.symlink_to(indexed_library, target_is_directory=True)
    settings.library_path = str(alias)
    database.rebind_engine()
    database.init_db()
    doc_id, _, _ = _saved(alias, '路径别名资料正文')
    assert indexing.process_one()
    assert _status(doc_id)['state'] == 'ready'
    assert _matches('别名资料') == [doc_id]


def test_new_and_restored_versions_never_return_stale_content(indexed_library):
    from backend.app.services import indexing
    doc_id, first_id, work = _saved(indexed_library, '旧版独有术语')
    assert indexing.process_one()
    work.write_text('新版特有条目')
    with database.SessionLocal() as db:
        doc = db.get(Document, doc_id)
        versions.snapshot_working_copy(db, doc)
        db.commit()
    assert not _matches('旧版独有')
    assert _status(doc_id)['state'] == 'queued'
    assert indexing.process_one()
    assert _matches('新版特有') == [doc_id]
    with database.SessionLocal() as db:
        doc = db.get(Document, doc_id)
        versions.restore_working_version(db, doc, db.get(Version, first_id))
    assert not _matches('新版特有')
    assert indexing.process_one()
    assert _matches('旧版独有') == [doc_id]


def test_trash_and_delete_exclude_content_and_delete_cascades(indexed_library):
    from backend.app.index_models import DocumentIndex, IndexText
    from backend.app.services import indexing
    doc_id, _, _ = _saved(indexed_library)
    indexing.process_one()
    with database.SessionLocal() as db:
        doc = db.get(Document, doc_id)
        doc.status = 'trashed'
        db.commit()
    assert not _matches('关键词')
    with database.SessionLocal() as db:
        db.delete(db.get(Document, doc_id))
        db.commit()
        assert db.get(DocumentIndex, doc_id) is None
        assert not db.query(IndexText).count()
        assert not db.execute(text('SELECT rowid FROM index_text_fts')).all()


def test_rebuild_during_parse_discards_old_generation(indexed_library, monkeypatch):
    from backend.app.services import index_content, indexing
    doc_id, _, _ = _saved(indexed_library)
    extract = index_content.extract_index

    def rebuild_while_parsing(data, filename):
        assert database.active_session_count() == 0
        with database.SessionLocal() as db:
            assert indexing.rebuild(db, doc_id)['state'] == 'queued'
        return extract(data, filename)

    monkeypatch.setattr(index_content, 'extract_index', rebuild_while_parsing)
    assert indexing.process_one()
    assert _status(doc_id)['state'] == 'queued'
    assert not _matches('关键词')
    monkeypatch.setattr(index_content, 'extract_index', extract)
    assert indexing.process_one()
    assert _status(doc_id)['state'] == 'ready'


def test_library_switch_during_parse_cannot_write_into_new_library(indexed_library, tmp_path, monkeypatch):
    from backend.app.index_models import DocumentIndex
    from backend.app.services import index_content, indexing
    doc_id, _, _ = _saved(indexed_library)
    extract = index_content.extract_index
    other = tmp_path / 'other'

    def switch_while_parsing(data, filename):
        assert database.active_session_count() == 0
        settings.library_path = str(other)
        database.rebind_engine()
        database.init_db()
        return extract(data, filename)

    monkeypatch.setattr(index_content, 'extract_index', switch_while_parsing)
    assert indexing.process_one()
    with database.SessionLocal() as db:
        assert db.get(DocumentIndex, doc_id) is None
    assert not _matches('关键词')
    settings.library_path = str(indexed_library)
    database.rebind_engine()
    database.init_db()
    monkeypatch.setattr(index_content, 'extract_index', extract)
    assert indexing.process_one()
    assert _status(doc_id)['state'] == 'ready'


def test_interrupted_processing_is_recovered(indexed_library):
    from backend.app.index_models import DocumentIndex
    from backend.app.services import indexing
    doc_id, _, _ = _saved(indexed_library)
    with database.SessionLocal() as db:
        indexing.rebuild(db, doc_id)
        db.get(DocumentIndex, doc_id).state = 'processing'
        db.commit()
    assert indexing.process_one()
    assert _status(doc_id)['state'] == 'ready'


def test_tag_changes_do_not_reparse_and_stale_ai_is_not_searchable(indexed_library, monkeypatch):
    from backend.app.services import index_content, indexing
    doc_id, version_id, _ = _saved(indexed_library)
    indexing.process_one()
    with database.SessionLocal() as db:
        doc = db.get(Document, doc_id)
        record = indexing.load_record(db, doc_id)
        indexing.save_ai(db, doc_id, version_id, record['content_hash'],
                         indexing.metadata_fingerprint(doc), {
                             'overview': 'AI线索综述',
                             'clues': [{'unit_id': record['units'][0]['id'], 'terms': ['AI专有别称']}],
                             'model': 'local-test', 'processed_units': 1,
                         })
        db.commit()
    assert _matches('AI专有别称') == [doc_id]
    assert _status(doc_id)['ai']['model'] == 'local-test'
    with database.SessionLocal() as db:
        doc = db.get(Document, doc_id)
        doc.tags.append(Tag(name='人工标签'))
        db.commit()
    assert not _matches('AI专有别称')
    assert _status(doc_id)['ai'] is None
    monkeypatch.setattr(index_content, 'extract_index', lambda *args: pytest.fail('metadata reparsed'))
    assert not indexing.process_one()
    assert _matches('关键词') == [doc_id]
    assert not _matches('人工标签')


def test_failed_index_waits_for_explicit_retry(indexed_library, monkeypatch):
    from backend.app.services import index_content, indexing
    doc_id, _, _ = _saved(indexed_library)
    extract = index_content.extract_index
    monkeypatch.setattr(index_content, 'extract_index', lambda *args: (_ for _ in ()).throw(ValueError('bad')))
    assert indexing.process_one()
    assert _status(doc_id)['state'] == 'failed'
    assert not indexing.process_one()
    monkeypatch.setattr(index_content, 'extract_index', extract)
    with database.SessionLocal() as db:
        assert indexing.rebuild(db, doc_id)['state'] == 'queued'
    assert indexing.process_one()
    assert _status(doc_id)['state'] == 'ready'


@pytest.mark.parametrize('tampering', ['hash', 'outside', 'symlink'])
def test_snapshot_hash_and_managed_path_are_verified(indexed_library, tampering):
    from backend.app.services import indexing
    doc_id, version_id, _ = _saved(indexed_library)
    with database.SessionLocal() as db:
        version = db.get(Version, version_id)
        snapshot = Path(version.file_path)
        if tampering == 'hash':
            snapshot.write_text('被改写')
        elif tampering == 'outside':
            outside = indexed_library.parent / 'private.txt'
            outside.write_text('外部文件绝不解析')
            version.file_path = str(outside)
            db.commit()
        else:
            outside = indexed_library.parent / 'secret.txt'
            outside.write_bytes(snapshot.read_bytes())
            snapshot.unlink()
            snapshot.symlink_to(outside)
    assert indexing.process_one()
    assert _status(doc_id)['state'] == 'failed'
    assert not _matches('关键词')


def test_folder_reports_explicit_partial_without_recursing(indexed_library, monkeypatch):
    from backend.app.services import index_content, indexing
    doc_id, _, _ = _saved(indexed_library, folder=True, name='folder')
    monkeypatch.setattr(index_content, 'extract_index', lambda *args: pytest.fail('folder recursed'))
    assert indexing.process_one()
    result = _status(doc_id)
    assert result['state'] == 'partial'
    assert result['units'] == []
    assert result['gaps']
    assert not _matches('关键词')


@pytest.mark.parametrize('needle', ['100%', 'A_B', 'a" OR "b', '中', 'ID-123'])
def test_search_uses_literal_substrings(indexed_library, needle):
    from backend.app.services import indexing
    doc_id, _, _ = _saved(indexed_library, f'needle: {needle}')
    indexing.process_one()
    assert _matches(needle) == [doc_id]
    assert _matches('unrelated') == []


def test_schema_initialization_is_idempotent_and_preserves_index(indexed_library):
    from backend.app.services import indexing
    doc_id, _, _ = _saved(indexed_library)
    indexing.process_one()
    database.init_db()
    database.init_db()
    assert _matches('关键词') == [doc_id]


def test_ai_save_rejects_generation_changed_after_preparation(indexed_library):
    from backend.app.services import indexing
    doc_id, version_id, _ = _saved(indexed_library)
    indexing.process_one()
    with database.SessionLocal() as db:
        prepared = indexing.load_record(db, doc_id)
        indexing.rebuild(db, doc_id)
    indexing.process_one()
    with database.SessionLocal() as db:
        with pytest.raises(ValueError, match='变化'):
            indexing.save_ai(db, doc_id, version_id, prepared['content_hash'], prepared['metadata_hash'],
                             {'overview': 'old', 'clues': []}, expected_generation=prepared['generation'])
    assert _status(doc_id)['ai'] is None


@pytest.mark.parametrize('change', ['rebuild', 'metadata'])
def test_ai_commit_atomically_rechecks_context(indexed_library, monkeypatch, change):
    from backend.app.services import indexing
    doc_id, version_id, _ = _saved(indexed_library)
    indexing.process_one()
    with database.SessionLocal() as db:
        prepared = indexing.load_record(db, doc_id)
        execute = db.execute
        changed = False

        def race(statement, *args, **kwargs):
            nonlocal changed
            if str(statement).startswith('UPDATE document_indexes') and not changed:
                changed = True
                with database.SessionLocal() as other:
                    if change == 'rebuild':
                        indexing.rebuild(other, doc_id)
                    else:
                        other.get(Document, doc_id).description = '新的上下文'
                        other.commit()
            return execute(statement, *args, **kwargs)

        monkeypatch.setattr(db, 'execute', race)
        with pytest.raises(ValueError, match='变化'):
            indexing.save_ai(db, doc_id, version_id, prepared['content_hash'], prepared['metadata_hash'],
                             {'overview': '过期的AI线索', 'clues': []},
                             expected_generation=prepared['generation'])
    assert not _matches('过期的AI线索')


def test_background_worker_is_single_and_stops_cleanly(indexed_library, monkeypatch):
    from backend.app.services import index_content, indexing
    doc_id, _, _ = _saved(indexed_library)
    started, release = threading.Event(), threading.Event()
    extract = index_content.extract_index

    def paused_parse(data, filename):
        assert database.active_session_count() == 0
        started.set()
        assert release.wait(3)
        return extract(data, filename)

    monkeypatch.setattr(index_content, 'extract_index', paused_parse)
    indexing.start_worker()
    try:
        assert started.wait(3)
        worker = indexing._worker
        indexing.start_worker()
        assert worker is indexing._worker
        assert not indexing.process_one()
        assert _status(doc_id)['state'] == 'processing'
    finally:
        release.set()
        indexing.stop_worker()
    assert _status(doc_id)['state'] == 'ready'
    assert indexing._worker is None


def test_missing_saved_version_fails_once(indexed_library):
    from backend.app.services import indexing
    with database.SessionLocal() as db:
        doc = Document(name='无版本资料', status='pending')
        db.add(doc)
        db.commit()
        doc_id = doc.id
    assert indexing.process_one()
    assert _status(doc_id)['state'] == 'failed'
    assert not indexing.process_one()


def test_keyword_crossing_parser_chunk_boundary_is_preserved(indexed_library):
    from backend.app.services import index_content, indexing
    prefix = '文' * (index_content.UNIT_TEXT_CHARS - 3)
    doc_id, _, _ = _saved(indexed_library, prefix + 'SERIAL-123456')
    indexing.process_one()
    assert _matches('SERIAL-123456') == [doc_id]
