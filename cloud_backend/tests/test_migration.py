"""
共享库 Alembic 迁移测试(M1,契约 §6.2)

构造"旧 schema"库副本(迁移前的列结构,stamp 到 003),
升级到 head(004),验证新列/新表/唯一约束/数据回填。
"""
import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from alembic.config import Config
from alembic import command

CLOUD_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _uid():
    return str(uuid.uuid4())


def _alembic_cfg(db_path):
    """迁移配置;env.py 会从 app.config.settings 读取 URL,需同步设置"""
    from app.config import settings
    settings.database_url = f"sqlite:///{db_path}"
    cfg = Config()
    cfg.set_main_option("script_location", os.path.join(CLOUD_BACKEND_DIR, "alembic"))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    return cfg


def _build_old_schema(db_path):
    """构造迁移前(003 时点)的旧表结构"""
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, email VARCHAR(255), "
            "hashed_password VARCHAR(255), display_name VARCHAR(100), "
            "is_active BOOLEAN DEFAULT 1, is_superuser BOOLEAN DEFAULT 0, "
            "created_at DATETIME, updated_at DATETIME)"
        ))
        conn.execute(text(
            "CREATE TABLE teams (id VARCHAR(36) PRIMARY KEY, name VARCHAR(100), slug VARCHAR(50), "
            "storage_quota BIGINT, storage_used BIGINT, created_at DATETIME, updated_at DATETIME)"
        ))
        conn.execute(text(
            "CREATE TABLE workspaces (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36), "
            "name VARCHAR(100), description TEXT, created_at DATETIME, updated_at DATETIME)"
        ))
        conn.execute(text(
            "CREATE TABLE cloud_documents (id VARCHAR(36) PRIMARY KEY, workspace_id VARCHAR(36), "
            "name VARCHAR(255), description TEXT, is_folder BOOLEAN DEFAULT 0, "
            "status VARCHAR(20) DEFAULT 'pending', file_count INTEGER DEFAULT 1, "
            "total_size BIGINT DEFAULT 0, server_version INTEGER DEFAULT 1, "
            "uploader_id VARCHAR(36), created_at DATETIME, updated_at DATETIME, trashed_at DATETIME)"
        ))
        conn.execute(text(
            "CREATE TABLE cloud_versions (id VARCHAR(36) PRIMARY KEY, document_id VARCHAR(36), "
            "version_number INTEGER, sha256_hash VARCHAR(64), oss_key VARCHAR(1000), "
            "file_size BIGINT DEFAULT 0, original_filename VARCHAR(255), relative_path VARCHAR(500), "
            "note TEXT, is_current BOOLEAN DEFAULT 1, created_at DATETIME)"
        ))
        conn.execute(text(
            "CREATE TABLE cloud_document_tags (document_id VARCHAR(36), tag_id VARCHAR(36))"
        ))
    return engine


def _insert_old_data(db_path):
    """旧数据:一个文件夹文档(两版,is_current 在 v2),一个文件文档(无 is_current)"""
    ids = {"u": _uid(), "t": _uid(), "ws": _uid(),
           "doc_folder": _uid(), "doc_file": _uid(),
           "v1": _uid(), "v2": _uid(), "v3": _uid()}
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id, email, hashed_password, display_name) "
                          "VALUES (:i, 'u@t.local', 'x', '上传者')"), {"i": ids["u"]})
        conn.execute(text("INSERT INTO teams (id, name, slug) VALUES (:i, '团队', :s)"),
                     {"i": ids["t"], "s": f"t-{ids['t'][:8]}"})
        conn.execute(text("INSERT INTO workspaces (id, team_id, name) VALUES (:i, :t, '空间')"),
                     {"i": ids["ws"], "t": ids["t"]})
        conn.execute(text(
            "INSERT INTO cloud_documents (id, workspace_id, name, is_folder, status, uploader_id) "
            "VALUES (:i, :ws, :n, :f, 'organized', :u)"), [
            {"i": ids["doc_folder"], "ws": ids["ws"], "n": "旧文件夹", "f": True, "u": ids["u"]},
            {"i": ids["doc_file"], "ws": ids["ws"], "n": "旧文件", "f": False, "u": ids["u"]},
        ])
        conn.execute(text(
            "INSERT INTO cloud_versions (id, document_id, version_number, is_current, sha256_hash, oss_key) "
            "VALUES (:i, :d, :n, :c, :h, :k)"), [
            {"i": ids["v1"], "d": ids["doc_folder"], "n": 1, "c": False, "h": "aa" * 32, "k": "k1"},
            {"i": ids["v2"], "d": ids["doc_folder"], "n": 2, "c": True, "h": "bb" * 32, "k": "k2"},
            {"i": ids["v3"], "d": ids["doc_file"], "n": 7, "c": False, "h": "cc" * 32, "k": "k3"},
        ])
    return ids


def test_migration_004_on_old_schema_copy(tmp_path):
    db_path = tmp_path / "cloud-old-copy.db"
    _build_old_schema(db_path)
    ids = _insert_old_data(db_path)

    cfg = _alembic_cfg(db_path)
    command.stamp(cfg, "003")   # 旧库停留在 003
    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        doc_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(cloud_documents)")).fetchall()}
        ver_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(cloud_versions)")).fetchall()}
        assert {"doc_type", "current_version_id", "created_by", "trashed_by"} <= doc_cols
        assert {"content_hash", "manifest_hash", "storage_key", "author_id", "base_version_id"} <= ver_cols

        tables = {r[0] for r in conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}
        assert {"publication_requests", "admin_actions"} <= tables

        docs = {r[0]: r for r in conn.execute(text(
            "SELECT id, doc_type, status, current_version_id, created_by FROM cloud_documents"
        )).fetchall()}
        assert docs[ids["doc_folder"]][1] == "folder"
        assert docs[ids["doc_folder"]][2] == "organized"  # 状态值保留旧语义(更名已撤销)
        assert docs[ids["doc_folder"]][3] == ids["v2"]  # is_current 优先
        assert docs[ids["doc_file"]][3] == ids["v3"]    # 兜底取最大版本号
        assert docs[ids["doc_folder"]][4] == ids["u"]   # uploader_id → created_by

        vers = {r[0]: r for r in conn.execute(text(
            "SELECT id, content_hash, storage_key FROM cloud_versions")).fetchall()}
        assert vers[ids["v1"]][1] == "aa" * 32  # sha256_hash → content_hash
        assert vers[ids["v1"]][2] == "k1"       # oss_key → storage_key

        # 唯一索引生效:同文档同版本号插入必须失败(1.4 下 DML 自动提交,无需 commit)
        with pytest.raises(IntegrityError):
            conn.execute(text(
                "INSERT INTO cloud_versions (id, document_id, version_number) "
                "VALUES (:i, :d, 2)"), {"i": _uid(), "d": ids["doc_folder"]})


def test_migration_downgrade_upgrade_cycle(tmp_path):
    """downgrade 再 upgrade 不报错(可重复演练)"""
    db_path = tmp_path / "cycle.db"
    _build_old_schema(db_path)
    cfg = _alembic_cfg(db_path)
    command.stamp(cfg, "003")
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "003")
    command.upgrade(cfg, "head")


def test_migration_rejects_ambiguous_history_without_mutation(tmp_path):
    """M1-R03:旧库含同文档重复版本号(子文件版本)时,迁移拒绝且不改变任何历史或结构"""
    db_path = tmp_path / "dup.db"
    _build_old_schema(db_path)
    ids = _insert_old_data(db_path)

    # 额外插入两条与 doc_folder 的 version_number=2 重复的子文件版本
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO cloud_versions (id, document_id, version_number, is_current, relative_path) "
            "VALUES (:i, :d, 2, 0, :r)"
        ), [
            {"i": _uid(), "d": ids["doc_folder"], "r": "sub/a.txt"},
            {"i": _uid(), "d": ids["doc_folder"], "r": "sub/b.txt"},
        ])

    cfg = _alembic_cfg(db_path)
    command.stamp(cfg, "003")
    def snapshot():
        with engine.connect() as conn:
            return (
                conn.execute(text("SELECT * FROM cloud_versions ORDER BY id")).fetchall(),
                conn.execute(text("PRAGMA table_info(cloud_documents)")).fetchall(),
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar(),
            )
    before = snapshot()
    for _ in range(2):
        with pytest.raises(RuntimeError, match="迁移已停止"):
            command.upgrade(cfg, "004")
        assert snapshot() == before


@pytest.mark.parametrize('kind', ['multiple_current', 'child_only'])
def test_preflight_rejects_ambiguity_before_ddl(tmp_path, kind):
    path = tmp_path / 'preflight.db'
    engine = _build_old_schema(path)
    ids = _insert_old_data(path)
    with engine.begin() as conn:
        if kind == 'multiple_current':
            conn.execute(text('UPDATE cloud_versions SET is_current=1 WHERE document_id=:d'), {'d': ids['doc_folder']})
        else:
            conn.execute(text("UPDATE cloud_versions SET relative_path='child.txt' WHERE id=:v"), {'v': ids['v1']})
    cfg = _alembic_cfg(path)
    command.stamp(cfg, '003')
    with pytest.raises(RuntimeError, match='迁移已停止'):
        command.upgrade(cfg, '004')
    with engine.connect() as conn:
        assert 'current_version_id' not in {r[1] for r in conn.execute(text('PRAGMA table_info(cloud_documents)'))}
        assert conn.execute(text('SELECT version_num FROM alembic_version')).scalar() == '003'
