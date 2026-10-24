"""Exercise the 004 -> 005 boundary on an isolated legacy database copy."""
import uuid

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from test_migration import _alembic_cfg, _build_old_schema, _insert_old_data


def test_acl_delivery_upgrade_preserves_history_and_service_identity(tmp_path):
    path = tmp_path / 'old.db'
    _build_old_schema(path)
    ids = _insert_old_data(path)
    config = _alembic_cfg(path)
    command.stamp(config, '003')
    command.upgrade(config, '004')
    engine = create_engine(f'sqlite:///{path}')
    with engine.connect() as db:
        versions = db.execute(text('SELECT id,content_hash,version_number FROM cloud_versions ORDER BY id')).all()
    command.upgrade(config, 'head')
    with engine.connect() as db:
        assert db.execute(text('SELECT id,content_hash,version_number FROM cloud_versions ORDER BY id')).all() == versions
        assert db.execute(text('SELECT DISTINCT visibility FROM cloud_documents')).all() == [('workspace',)]
        assert db.execute(text('SELECT COUNT(*) FROM document_access WHERE role=\'manager\'')).scalar() == 2
        server_id = db.execute(text('SELECT server_id FROM server_metadata')).scalar()
        assert str(uuid.UUID(server_id)) == server_id
    assert {'document_access', 'deliveries', 'delivery_recipients', 'delivery_items'} <= set(inspect(engine).get_table_names())
    command.upgrade(config, 'head')
    with engine.connect() as db:
        assert db.execute(text('SELECT server_id FROM server_metadata')).scalar() == server_id
        with pytest.raises(IntegrityError):
            db.execute(text("INSERT INTO document_access (document_id,user_id,role,created_at) VALUES (:doc,:user,'reader',CURRENT_TIMESTAMP)"),
                       {'doc': ids['doc_file'], 'user': ids['u']})


def test_downgrade_refuses_reopening_restricted_content(tmp_path):
    path = tmp_path / 'restricted.db'
    _build_old_schema(path)
    _insert_old_data(path)
    config = _alembic_cfg(path)
    command.stamp(config, '003'); command.upgrade(config, 'head')
    engine = create_engine(f'sqlite:///{path}')
    with engine.begin() as db:
        db.execute(text("UPDATE cloud_documents SET visibility='restricted'"))
    with pytest.raises(RuntimeError, match='降级已停止'):
        command.downgrade(config, '004')
    with engine.connect() as db:
        assert db.execute(text('SELECT DISTINCT visibility FROM cloud_documents')).all() == [('restricted',)]
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar() == '005'


def test_startup_rejects_legacy_schema_before_mutation(tmp_path, monkeypatch):
    from app import database
    path = tmp_path / 'legacy.db'
    engine = _build_old_schema(path)
    before = set(inspect(engine).get_table_names())
    monkeypatch.setattr(database, 'engine', engine)
    with pytest.raises(RuntimeError, match='显式迁移'):
        database.init_db()
    assert set(inspect(engine).get_table_names()) == before


def test_fresh_startup_initializes_identity_and_records_revision(tmp_path, monkeypatch):
    from sqlalchemy.orm import sessionmaker
    from app import database
    path = tmp_path / 'fresh.db'
    engine = create_engine(f'sqlite:///{path}')
    monkeypatch.setattr(database, 'engine', engine)
    monkeypatch.setattr(database, 'SessionLocal', sessionmaker(bind=engine))
    database.init_db()
    with engine.connect() as db:
        identity = db.execute(text('SELECT server_id FROM server_metadata')).scalar()
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar() == '005'
    database.init_db()
    with engine.connect() as db:
        assert db.execute(text('SELECT server_id FROM server_metadata')).scalar() == identity
