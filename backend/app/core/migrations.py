"""
个人库 MVP 幂等迁移(M1;M1-R01/R03 整改)

按 docs/MVP数据与接口契约.md §6.1:
- documents 新增 doc_type / current_version_id;is_folder → doc_type;
  is_current=true 的版本写入 current_version_id(缺失时取最大版本号);
- versions 新增 manifest_hash。
- 新表 share_links / favorites 由 Base.metadata.create_all 创建,这里不处理。
- 不删旧列,旧列写入方在 M2 重写前保持原样;迁移可重复执行。

M1-R01/M1-R03 整改:
- 建立 (document_id, version_number) 唯一索引前,先预检旧库的历史关系:
  重复号、多当前标记或旧子文件历史在任何结构修改前明确拒绝;
  旧文件夹子文件版本(relative_path 非空)无法合并为整体快照,停止迁移并报告资料 ID，待副本核实后重试。
- 不自动重编号，不把子文件记录解释为整份资料历史。
"""
from sqlalchemy import text

from backend.app.core.logger import get_logger

logger = get_logger("migrations")


def _column_names(conn, table: str) -> set:
    return {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()}


class MigrationConflict(RuntimeError):
    """旧数据含无法自动判断的历史关系；保留原数据并停止迁移。"""


def check_legacy_versions(conn) -> None:
    issues = []
    for label, query in (
        ("重复版本号", "SELECT document_id FROM versions GROUP BY document_id, version_number HAVING COUNT(*) > 1"),
        ("多个当前版本", "SELECT document_id FROM versions WHERE is_current=1 GROUP BY document_id HAVING COUNT(*) > 1"),
    ):
        issues.extend(f"{label}: {row[0]}" for row in conn.execute(text(query)))
    if "relative_path" in _column_names(conn, "versions"):
        issues.extend(f"旧子文件历史: {row[0]}" for row in conn.execute(text(
            "SELECT DISTINCT document_id FROM versions WHERE relative_path IS NOT NULL AND relative_path != ''"
        )))
    if "current_version_id" in _column_names(conn, "documents"):
        issues.extend(f"非法当前版本引用: {row[0]}" for row in conn.execute(text(
            "SELECT d.id FROM documents d WHERE d.current_version_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM versions v WHERE v.id=d.current_version_id AND v.document_id=d.id)"
        )))
    if issues:
        raise MigrationConflict("迁移已停止，未自动重编号或选择历史。请在副本中核实并处理后重试：" + "; ".join(issues))


def install_current_version_guards(conn) -> None:
    # SQLite 旧表无法直接补 FK；触发器使空库与升级库都保证引用存在且属于本资料。
    for operation in ("INSERT", "UPDATE OF current_version_id, id"):
        name = "insert" if operation == "INSERT" else "update"
        conn.execute(text(f"""
            CREATE TRIGGER IF NOT EXISTS guard_current_{name}
            BEFORE {operation} ON documents
            WHEN NEW.current_version_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM versions WHERE id=NEW.current_version_id AND document_id=NEW.id
            ) BEGIN SELECT RAISE(ABORT, 'current version must belong to document'); END
        """))
    conn.execute(text("""
        CREATE TRIGGER IF NOT EXISTS guard_version_owner
        BEFORE UPDATE OF document_id, id ON versions
        WHEN EXISTS (SELECT 1 FROM documents WHERE current_version_id=OLD.id
                     AND (id != NEW.document_id OR NEW.id != OLD.id))
        BEGIN SELECT RAISE(ABORT, 'cannot move a referenced current version'); END
    """))
    conn.execute(text("""
        CREATE TRIGGER IF NOT EXISTS clear_deleted_current
        BEFORE DELETE ON versions BEGIN
            UPDATE documents SET current_version_id=NULL WHERE current_version_id=OLD.id;
        END
    """))


def run_mvp_migrations(engine) -> None:
    """执行个人库 MVP 迁移(幂等)"""
    with engine.begin() as conn:
        check_legacy_versions(conn)
        doc_cols = _column_names(conn, "documents")
        if "version_sequence" not in doc_cols:
            conn.execute(text("ALTER TABLE documents ADD COLUMN version_sequence INTEGER NOT NULL DEFAULT 0"))
        conn.execute(text("""
            UPDATE documents SET version_sequence = MAX(version_sequence, COALESCE(
                (SELECT MAX(version_number) FROM versions WHERE document_id=documents.id), 0))
        """))
        ver_cols = _column_names(conn, "versions")

        if "storage_area" not in doc_cols:
            from backend.app.config import settings
            conn.execute(text("ALTER TABLE documents ADD COLUMN storage_area VARCHAR(10) NOT NULL DEFAULT 'library'"))
            condition = "status='pending'"
            if "storage_path" in doc_cols:
                condition += " OR storage_path LIKE :prefix"
            conn.execute(text("UPDATE documents SET storage_area='pending' WHERE " + condition),
                         {"prefix": settings.pending_folder_name + '/%'})

        # ── 新增列 ────────────────────────────────────────────
        if "doc_type" not in doc_cols:
            conn.execute(text("ALTER TABLE documents ADD COLUMN doc_type VARCHAR(10) NOT NULL DEFAULT 'file'"))
            logger.info("迁移:documents.doc_type 已添加")
        if "current_version_id" not in doc_cols:
            conn.execute(text("ALTER TABLE documents ADD COLUMN current_version_id VARCHAR(36)"))
            logger.info("迁移:documents.current_version_id 已添加")
        if "manifest_hash" not in ver_cols:
            conn.execute(text("ALTER TABLE versions ADD COLUMN manifest_hash VARCHAR(64)"))
            logger.info("迁移:versions.manifest_hash 已添加")


        # ── 数据迁移(仅处理尚未迁移的行,保证幂等) ─────────────────
        # is_folder → doc_type
        conn.execute(text("UPDATE documents SET doc_type='folder' WHERE is_folder=1 AND doc_type='file'"))

        # is_current=true → current_version_id;缺失时取最大版本号
        conn.execute(text("""
            UPDATE documents SET current_version_id = (
                SELECT v.id FROM versions v
                WHERE v.document_id = documents.id AND v.is_current = 1
            )
            WHERE current_version_id IS NULL
              AND EXISTS (SELECT 1 FROM versions v
                          WHERE v.document_id = documents.id AND v.is_current = 1)
        """))
        conn.execute(text("""
            UPDATE documents SET current_version_id = (
                SELECT v.id FROM versions v
                WHERE v.document_id = documents.id
                ORDER BY v.version_number DESC LIMIT 1
            )
            WHERE current_version_id IS NULL
              AND EXISTS (SELECT 1 FROM versions v WHERE v.document_id = documents.id)
        """))

        # organized → active 的语义更名已撤销(M2 决定:现有代码与前端深度依赖
        # 'organized' 值,更名属于装饰性改动且破坏过滤;MVP 期间保留旧值,
        # 详见 docs/MVP数据与接口契约.md §6.1)

        # versions (document_id, version_number) 唯一索引(契约 §2.2);
        # 有歧义的数据已在结构修改前拒绝
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_versions_doc_num "
            "ON versions(document_id, version_number)"
        ))

        install_current_version_guards(conn)
        for table, additions in {
            'share_links': {'server_id': 'VARCHAR(36)', 'account_id': 'VARCHAR(36)',
                            'observed_version_id': 'VARCHAR(36)'},
            'favorites': {'server_id': 'VARCHAR(36)'},
        }.items():
            columns = _column_names(conn, table)
            for name, definition in additions.items():
                if columns and name not in columns:
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {definition}'))
    logger.info("MVP 迁移检查完成")
