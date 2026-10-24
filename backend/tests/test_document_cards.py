"""资料卡的持久化、版本隔离及全文检索；只使用临时资料库。"""
import pytest
from pydantic import ValidationError
from sqlalchemy import select, text

from backend.app import database
from backend.app.config import settings
from backend.app.models import Document, Version
from backend.app.services import indexing, versions
from backend.app.services import document_cards as cards
from backend.app.services.card_schema import CardContent


@pytest.fixture
def library(tmp_path, monkeypatch):
    root = tmp_path / 'library'
    root.mkdir()
    monkeypatch.setattr(settings, 'library_path', str(root))
    monkeypatch.setattr(settings, 'pending_path', '')
    monkeypatch.setenv('TEAMHUB_AI_CONFIG_DIR', str(tmp_path / 'ai-profile'))
    database.rebind_engine()
    database.init_db()
    yield root
    indexing.stop_worker()
    assert database.active_session_count() == 0


def saved(library):
    path = library / 'sample.txt'
    path.write_text('测试原文一')
    with database.SessionLocal() as db:
        doc = Document(name=path.name, storage_path=path.name, status='pending', storage_mode='copy')
        db.add(doc)
        db.flush()
        version, _ = versions.snapshot_working_copy(db, doc)
        db.commit()
        return doc.id, version.id, version.sha256_hash, path


def content():
    return {'summary': '关于断路器巡视的资料', 'document_type': '巡视记录',
            'topics': ['设备运行'], 'sections': [{'title': '巡视记录', 'summary': '日常巡视', 'source_ids': ['S1']}],
            'facts': [{'label': '设备', 'value': '5053 断路器', 'source_ids': ['S1']}],
            'search_terms': ['气室压力线索'], 'uses': ['查找巡视记录'], 'gaps': [], 'source_ids': ['S1']}


def evidence(doc_id, version_id, digest):
    return [{'id': 'S1', 'source_id': 'S1', 'document_id': doc_id, 'version_id': version_id,
             'content_hash': digest, 'version_number': 1, 'name': 'sample.txt', 'label': '正文',
             'location': {'line': 1}, 'excerpt': '测试原文一'}]


def save(doc_id, version_id, digest, **kwargs):
    with database.SessionLocal() as db:
        return cards.save_card(db, doc_id, version_id, digest, content(),
                               evidence(doc_id, version_id, digest),
                               {'complete': True, 'total_units': 1, 'read_units': 1,
                                'visual_pages': 0, 'total_visual_pages': 0,
                                'embedded_images': 0, 'total_embedded_images': 0},
                               'local-test', commit=True, **kwargs)


def status(doc_id):
    with database.SessionLocal() as db:
        return cards.status(db, doc_id)


def matches(term):
    with database.SessionLocal() as db:
        return list(db.scalars(select(Document.id).where(Document.id.in_(indexing.content_document_ids(term)))))


def test_card_persists_and_metadata_changes_do_not_invalidate(library):
    doc_id, version_id, digest, path = saved(library)
    result = save(doc_id, version_id, digest)
    assert result['state'] == 'ready'
    assert result['card']['revision'] == 1
    assert result['card']['version_number'] == 1
    assert matches('气室压力线索') == [doc_id]
    with database.SessionLocal() as db:
        doc = db.get(Document, doc_id)
        doc.name, doc.description = '新的显示名称.txt', '人工备注'
        db.commit()
    assert status(doc_id)['state'] == 'ready'
    assert matches('气室压力线索') == [doc_id]
    assert path.read_text() == '测试原文一'
    assert save(doc_id, version_id, digest, expected_revision=1)['card']['revision'] == 2
    with pytest.raises(ValueError):
        save(doc_id, version_id, digest, expected_revision=1)
    with database.SessionLocal() as db:
        assert db.query(Version).count() == 1


def test_new_version_excludes_old_card_and_restore_reuses_immediately(library):
    doc_id, version_id, digest, path = saved(library)
    save(doc_id, version_id, digest)
    path.write_text('测试原文二')
    with database.SessionLocal() as db:
        doc = db.get(Document, doc_id)
        versions.snapshot_working_copy(db, doc)
        db.commit()
    result = status(doc_id)
    assert result['state'] == 'stale' and result['card'] is None
    assert result['previous_card']['version_id'] == version_id
    assert result['previous_card']['version_number'] == 1
    assert result['version_number'] == 2
    assert matches('气室压力线索') == []
    with pytest.raises(ValueError):
        save(doc_id, version_id, digest)
    with database.SessionLocal() as db:
        versions.restore_working_version(db, db.get(Document, doc_id), db.get(Version, version_id))
    assert status(doc_id)['state'] == 'ready'
    assert matches('气室压力线索') == [doc_id]


def test_raw_index_rebuild_keeps_card_search(library):
    doc_id, version_id, digest, _ = saved(library)
    save(doc_id, version_id, digest)
    with database.SessionLocal() as db:
        indexing.rebuild(db, doc_id)
    assert matches('气室压力线索') == [doc_id]
    assert indexing.process_one()
    assert matches('气室压力线索') == [doc_id]
    assert matches('测试原文一') == [doc_id]


def test_deleting_old_index_version_keeps_current_card_and_note_search(library):
    """历史版本无卡片时，其索引级联删除也不能清空当前派生检索入口。"""
    from backend.app.index_models import DocumentIndex

    doc_id, old_version_id, _, path = saved(library)
    assert indexing.process_one()
    path.write_text('第二版正文')
    with database.SessionLocal() as db:
        version, _ = versions.snapshot_working_copy(db, db.get(Document, doc_id))
        db.commit()
        version_id, digest = version.id, version.sha256_hash
        sources = evidence(doc_id, version_id, digest)
        sources[0]['version_number'] = version.version_number
        cards.save_card(db, doc_id, version_id, digest, content(), sources, {}, 'test', commit=True)
        cards.save_annotation(db, doc_id, version_id, 0, '人工交接背景', commit=True)
        assert db.get(DocumentIndex, doc_id).version_id == old_version_id

        db.delete(db.get(Version, old_version_id))
        db.commit()

    assert status(doc_id)['card']['version_id'] == version_id
    assert matches('气室压力线索') == [doc_id]
    assert matches('人工交接背景') == [doc_id]
    assert indexing.process_one()
    assert matches('气室压力线索') == [doc_id]
    assert matches('人工交接背景') == [doc_id]
    assert matches('第二版正文') == [doc_id]


@pytest.mark.parametrize('field,value', [('version_id', 'other'), ('content_hash', '0' * 64),
                                       ('document_id', 'other'), ('id', 'S2')])
def test_invalid_source_is_rejected(library, field, value):
    doc_id, version_id, digest, _ = saved(library)
    sources = evidence(doc_id, version_id, digest)
    sources[0][field] = value
    with database.SessionLocal() as db, pytest.raises(ValueError):
        cards.save_card(db, doc_id, version_id, digest, content(), sources, {}, 'local-test')
    assert status(doc_id)['state'] == 'missing'


@pytest.mark.parametrize('mutation', [lambda c: c.update(summary='\x00bad'),
                                     lambda c: c.update(extra='bad'),
                                     lambda c: c['facts'][0].update(source_ids=[]),
                                     lambda c: c.update(search_terms=['x' * 301]),
                                     lambda c: c.update(summary=1)])
def test_strict_card_schema(mutation):
    card = content()
    mutation(card)
    with pytest.raises(ValidationError):
        CardContent.model_validate(card)


def test_partial_coverage_cannot_claim_ready(library):
    doc_id, version_id, digest, _ = saved(library)
    with database.SessionLocal() as db:
        result = cards.save_card(db, doc_id, version_id, digest, content(),
                                evidence(doc_id, version_id, digest),
                                {'complete': False, 'total_units': 20, 'read_units': 1}, 'test', commit=True)
        assert result['state'] == 'partial'


def test_annotation_cas_preserved_on_regeneration_and_new_version(library):
    doc_id, version_id, digest, path = saved(library)
    save(doc_id, version_id, digest)
    with database.SessionLocal() as db:
        result = cards.save_annotation(db, doc_id, version_id, 0, '人工交接场景', commit=True)
        assert result['annotation']['revision'] == 1
        with pytest.raises(ValueError):
            cards.save_annotation(db, doc_id, version_id, 0, '覆盖', commit=True)
    save(doc_id, version_id, digest)
    assert status(doc_id)['annotation']['text'] == '人工交接场景'
    assert matches('人工交接场景') == [doc_id]
    path.write_text('新版正文')
    with database.SessionLocal() as db:
        versions.snapshot_working_copy(db, db.get(Document, doc_id))
        db.commit()
    assert status(doc_id)['annotation']['needs_review'] is True
    assert matches('人工交接场景') == []


def test_trashed_cards_hidden_and_delete_cascades(library):
    from backend.app.card_models import DocumentCard, DocumentCardAnnotation
    doc_id, version_id, digest, _ = saved(library)
    save(doc_id, version_id, digest)
    with database.SessionLocal() as db:
        cards.save_annotation(db, doc_id, version_id, 0, '人工补充', commit=True)
        doc = db.get(Document, doc_id)
        doc.status = 'trashed'
        db.commit()
        with pytest.raises(ValueError):
            cards.status(db, doc_id)
        assert matches('气室压力线索') == []
        db.delete(doc)
        db.commit()
        assert db.query(DocumentCard).count() == db.query(DocumentCardAnnotation).count() == 0


def test_old_card_rules_are_stale_and_not_searchable(library):
    from backend.app.card_models import DocumentCard
    doc_id, version_id, digest, _ = saved(library)
    save(doc_id, version_id, digest)
    with database.SessionLocal() as db:
        db.get(DocumentCard, (doc_id, version_id)).skill_version = 'old'
        db.commit()
    assert status(doc_id)['state'] == 'stale'
    assert matches('气室压力线索') == []


def test_reinitialize_additive_schema_and_projection(library):
    doc_id, version_id, digest, _ = saved(library)
    save(doc_id, version_id, digest)
    database.init_db()
    database.init_db()
    assert status(doc_id)['card']['revision'] == 1
    assert matches('气室压力线索') == [doc_id]


def test_shallow_preorganization_cannot_replace_completed_card(library):
    doc_id, version_id, digest, _ = saved(library)
    save(doc_id, version_id, digest)
    with database.SessionLocal() as db:
        shallow = content()
        shallow['summary'] = '更浅的新结果'
        result = cards.save_card(db, doc_id, version_id, digest, shallow,
                                evidence(doc_id, version_id, digest),
                                {'complete': False, 'read_units': 0, 'total_units': 1},
                                'test', expected_revision=1, commit=True)
        assert result['card']['revision'] == 1
        assert result['card']['content']['summary'] == content()['summary']


def test_nested_fact_reference_and_extra_source_payload_are_rejected(library):
    doc_id, version_id, digest, _ = saved(library)
    invalid = content()
    invalid['facts'][0]['source_ids'] = ['S99']
    with database.SessionLocal() as db, pytest.raises(ValueError):
        cards.save_card(db, doc_id, version_id, digest, invalid,
                        evidence(doc_id, version_id, digest), {}, 'test')
    sources = evidence(doc_id, version_id, digest)
    sources[0]['unexpected_path'] = '/private/internal/path'
    with database.SessionLocal() as db, pytest.raises(ValueError):
        cards.save_card(db, doc_id, version_id, digest, content(), sources, {}, 'test')


def test_uncommitted_card_is_rolled_back_with_parent_transaction(library):
    doc_id, version_id, digest, _ = saved(library)
    with database.SessionLocal() as db:
        cards.save_card(db, doc_id, version_id, digest, content(),
                        evidence(doc_id, version_id, digest), {}, 'test', commit=False)
    assert status(doc_id)['state'] == 'missing'
    assert matches('气室压力线索') == []


def test_old_library_gains_card_tables_without_changing_saved_versions(library):
    doc_id, version_id, digest, _ = saved(library)
    with database.engine.begin() as connection:
        connection.execute(text('DROP TABLE document_card_annotations'))
        connection.execute(text('DROP TABLE document_cards'))
    database.init_db()
    assert status(doc_id)['state'] == 'missing'
    assert save(doc_id, version_id, digest)['state'] == 'ready'
    with database.SessionLocal() as db:
        assert db.get(Document, doc_id).current_version_id == version_id
        assert db.query(Version).count() == 1


def test_api_local_guard_annotation_scope_and_read_only_status(library, monkeypatch):
    from fastapi.testclient import TestClient
    from main import app
    from backend.app.services import card_jobs
    doc_id, version_id, _, _ = saved(library)
    client = TestClient(app)
    base = f'/api/ai/cards/{doc_id}'
    headers = {'x-teamhub-ai': '1'}
    def unexpected_model(*args, **kwargs):
        pytest.fail('读取资料卡不能发起模型请求')
    monkeypatch.setattr(card_jobs, 'run_agent', unexpected_model)
    assert client.get(base).status_code == 403
    config = client.get('/api/ai/cards/config', headers=headers).json()
    response = client.get(base, headers=headers)
    assert response.status_code == 200 and response.json()['state'] == 'missing'
    payload = {'library_token': 'old', 'version_id': version_id, 'expected_revision': 0, 'text': '补充场景'}
    assert client.put(base + '/annotation', headers=headers, json=payload).status_code == 409
    payload['library_token'] = config['library_token']
    response = client.put(base + '/annotation', headers=headers, json=payload)
    assert response.status_code == 200 and response.json()['annotation']['text'] == '补充场景'
    assert client.put(base + '/annotation', headers=headers, json=payload).status_code == 409
    assert matches('补充场景') == [doc_id]
