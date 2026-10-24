"""
个人库 MVP 迁移测试(M1,契约 §6.1)

- 在旧 schema 副本上验证迁移:新列出现、is_folder→doc_type、
  is_current→current_version_id(含最大版本号兜底)、organized→active。
- 迁移幂等:重复执行结果一致。
- 空库从零建表 + 迁移也可成功。
"""
import uuid

import pytest
from sqlalchemy import create_engine, text

from backend.app.core.migrations import run_mvp_migrations, MigrationConflict


def _build_old_schema_db(db_path):
    """构造一个"旧 schema"的个人库(迁移前的列结构)"""
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE documents (
                id VARCHAR(36) PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                is_folder BOOLEAN NOT NULL DEFAULT 0,
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                file_count INTEGER NOT NULL DEFAULT 1,
                total_size BIGINT NOT NULL DEFAULT 0
            )
        """))
        conn.execute(text("""
            CREATE TABLE versions (
                id VARCHAR(36) PRIMARY KEY,
                document_id VARCHAR(36) NOT NULL,
                version_number INTEGER NOT NULL,
                is_current BOOLEAN NOT NULL DEFAULT 0,
                sha256_hash VARCHAR(64)
            )
        """))
        conn.commit()
    return engine


def _insert_old_data(engine):
    """插入旧数据:一个文件夹文档(两版,is_current 标记 v2)、一个文件文档(无 is_current 标记)"""
    doc_folder, doc_file = str(uuid.uuid4()), str(uuid.uuid4())
    v1, v2, v3 = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    with engine.connect() as conn:
        conn.execute(text(
            "INSERT INTO documents (id, name, is_folder, status) VALUES (:i, :n, :f, :s)"
        ), [
            {"i": doc_folder, "n": "旧文件夹", "f": True, "s": "organized"},
            {"i": doc_file, "n": "旧文件", "f": False, "s": "organized"},
        ])
        conn.execute(text(
            "INSERT INTO versions (id, document_id, version_number, is_current) VALUES (:i, :d, :n, :c)"
        ), [
            {"i": v1, "d": doc_folder, "n": 1, "c": False},
            {"i": v2, "d": doc_folder, "n": 2, "c": True},
            {"i": v3, "d": doc_file, "n": 5, "c": False},  # 无 is_current,应兜底取最大版本号
        ])
        conn.commit()
    return doc_folder, doc_file, v2, v3


def _fetch_docs(engine):
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, doc_type, status, current_version_id FROM documents")
        ).fetchall()
    return {r[0]: {"doc_type": r[1], "status": r[2], "current_version_id": r[3]} for r in rows}


def test_migrate_old_schema_copy(tmp_path):
    engine = _build_old_schema_db(tmp_path / "old-copy.db")
    doc_folder, doc_file, v2, v3 = _insert_old_data(engine)

    run_mvp_migrations(engine)
    docs = _fetch_docs(engine)

    assert docs[doc_folder]["doc_type"] == "folder"
    assert docs[doc_file]["doc_type"] == "file"
    assert docs[doc_folder]["status"] == "organized"  # 状态值保留旧语义(更名已撤销)
    assert docs[doc_folder]["current_version_id"] == v2  # is_current 优先
    assert docs[doc_file]["current_version_id"] == v3    # 无标记时取最大版本号

    # versions 表应有 manifest_hash 列
    with engine.connect() as conn:
        ver_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(versions)")).fetchall()}
    assert "manifest_hash" in ver_cols


def test_migration_idempotent(tmp_path):
    engine = _build_old_schema_db(tmp_path / "old-copy.db")
    doc_folder, doc_file, v2, v3 = _insert_old_data(engine)

    run_mvp_migrations(engine)
    first = _fetch_docs(engine)
    run_mvp_migrations(engine)  # 第二次执行不得报错或改变结果
    second = _fetch_docs(engine)

    assert first == second


def test_migrate_empty_db(tmp_path):
    """空库:先按新模型建表,再跑迁移"""
    from backend.app.database import Base
    import backend.app.models  # noqa: F401 注册模型

    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    Base.metadata.create_all(bind=engine)
    run_mvp_migrations(engine)

    with engine.connect() as conn:
        tables = {r[0] for r in conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        ).fetchall()}
    assert {"documents", "versions", "share_links", "favorites"} <= tables


def test_migration_rejects_ambiguous_history_without_mutation(tmp_path):
    """M1-R03:旧库有歧义时不重编号、不改结构，重复执行仍明确报告"""
    engine = create_engine(f"sqlite:///{tmp_path / 'dup.db'}")
    doc_id, v1, v2a, v2b, v2c = (str(uuid.uuid4()) for _ in range(5))
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE documents (
                id VARCHAR(36) PRIMARY KEY, name VARCHAR(255) NOT NULL,
                is_folder BOOLEAN NOT NULL DEFAULT 0,
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                file_count INTEGER NOT NULL DEFAULT 1,
                total_size BIGINT NOT NULL DEFAULT 0
            )
        """))
        conn.execute(text("""
            CREATE TABLE versions (
                id VARCHAR(36) PRIMARY KEY, document_id VARCHAR(36) NOT NULL,
                version_number INTEGER NOT NULL, is_current BOOLEAN NOT NULL DEFAULT 0,
                sha256_hash VARCHAR(64), relative_path VARCHAR(500)
            )
        """))
        conn.execute(text(
            "INSERT INTO documents (id, name, is_folder, status) VALUES (:i, '旧文件夹', 1, 'organized')"
        ), {"i": doc_id})
        # 旧文件夹"快照"由多条子文件版本共享同一 version_number=2
        conn.execute(text(
            "INSERT INTO versions (id, document_id, version_number, is_current, relative_path) "
            "VALUES (:i, :d, :n, :c, :r)"
        ), [
            {"i": v1, "d": doc_id, "n": 1, "c": False, "r": None},
            {"i": v2a, "d": doc_id, "n": 2, "c": True, "r": "a.txt"},
            {"i": v2b, "d": doc_id, "n": 2, "c": False, "r": "b.txt"},
            {"i": v2c, "d": doc_id, "n": 2, "c": False, "r": "c.txt"},
        ])
        conn.commit()

    def snapshot():
        with engine.connect() as conn:
            return (
                conn.execute(text("SELECT * FROM versions ORDER BY id")).fetchall(),
                conn.execute(text("PRAGMA table_info(documents)")).fetchall(),
            )
    before = snapshot()
    for _ in range(2):
        with pytest.raises(MigrationConflict, match="旧子文件历史"):
            run_mvp_migrations(engine)
        assert snapshot() == before


@pytest.mark.parametrize("kind", ["multiple_current", "child_only"])
def test_migration_preflight_other_ambiguities(tmp_path, kind):
    engine = _build_old_schema_db(tmp_path / "ambiguous.db")
    doc, _, _, _ = _insert_old_data(engine)
    with engine.begin() as conn:
        if kind == "multiple_current":
            conn.execute(text("UPDATE versions SET is_current=1 WHERE document_id=:d"), {"d": doc})
        else:
            conn.execute(text("ALTER TABLE versions ADD COLUMN relative_path TEXT"))
            conn.execute(text("UPDATE versions SET relative_path='child.txt' WHERE document_id=:d"), {"d": doc})
    with pytest.raises(MigrationConflict):
        run_mvp_migrations(engine)
    with engine.connect() as conn:
        assert "current_version_id" not in {r[1] for r in conn.execute(text("PRAGMA table_info(documents)"))}


def test_migrated_library_guards_current_version(tmp_path):
    from sqlalchemy.exc import IntegrityError
    engine = _build_old_schema_db(tmp_path / "old.db")
    folder, file, v2, v3 = _insert_old_data(engine)
    run_mvp_migrations(engine)
    for invalid in ("missing", v3):
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(text("UPDATE documents SET current_version_id=:v WHERE id=:d"), {"v": invalid, "d": folder})
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("UPDATE versions SET document_id=:d WHERE id=:v"), {"d": file, "v": v2})
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM versions WHERE id=:v"), {"v": v2})
        assert conn.execute(text("SELECT current_version_id FROM documents WHERE id=:d"), {"d": folder}).scalar() is None
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("INSERT INTO versions(id,document_id,version_number) VALUES ('duplicate',:d,5)"), {"d": file})


def test_init_db_preflight_runs_before_other_schema_changes(tmp_path, monkeypatch):
    import backend.app.database as database
    from backend.app.config import settings
    engine = _build_old_schema_db(tmp_path / 'old.db')
    doc, _, _, _ = _insert_old_data(engine)
    with engine.begin() as conn:
        conn.execute(text('UPDATE versions SET is_current=1 WHERE document_id=:d'), {'d': doc})
        before = conn.execute(text("SELECT name,sql FROM sqlite_master ORDER BY name")).fetchall()
    monkeypatch.setattr(database, 'engine', engine)
    monkeypatch.setattr(settings, 'library_path', str(tmp_path / 'library'))
    with pytest.raises(MigrationConflict):
        database.init_db()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT name,sql FROM sqlite_master ORDER BY name")).fetchall() == before
