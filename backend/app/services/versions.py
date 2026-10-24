"""
个人版本服务(M2)

契约语义(docs/MVP数据与接口契约.md §2.2 / §5.1):
- 创建版本 = 对工作位置做不可变快照,版本只引用快照对象,不指向可改写的工作文件;
- 恢复 = 替换工作文件并将所选历史版本设为当前；未保存修改先建保护版本;
- 成功提交的新版本即当前版本,documents.current_version_id 是唯一事实来源;
- 内容与当前版一致时不产生新版本(契约 CONTENT_UNCHANGED)。

兼容说明:旧代码(响应构建、删除清理等)仍读 Version.file_path 的绝对路径形式,
故 file_path 存快照对象的绝对路径(位于 <库>/.teamhub/objects/ 下,等价于存储键定位);
is_current 旧列不再维护；响应中的当前标记从 current_version_id 推导。
"""
from pathlib import Path
import hashlib
import os
import shutil
import tempfile

from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models import Document, Version, utc_now
from backend.app.core.logger import get_logger
from backend.app.services import snapshots
from backend.app.services.snapshots import SnapshotError

logger = get_logger("services.versions")


class VersionServiceError(Exception):
    """版本业务错误,message 供 API 层直接透传"""
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _library() -> Path:
    if not settings.library_path:
        raise VersionServiceError("文件库未初始化", 400)
    return Path(settings.library_path)


def working_path(doc: Document) -> Path:
    """文档当前工作位置(用户可见副本)"""
    from backend.app.core.storage_paths import document_path
    return document_path(doc)


def _next_version_number(db: Session, doc: Document) -> int:
    current_max = db.query(Version).filter(
        Version.document_id == doc.id
    ).order_by(Version.version_number.desc()).first()
    number = max(doc.version_sequence or 0, current_max.version_number if current_max else 0) + 1
    doc.version_sequence = number
    return number


def _mark_current(db: Session, doc: Document, version: Version) -> None:
    """只维护 current_version_id，历史记录不改写"""
    if version.document_id != doc.id:
        # M1-R01:当前版本归属校验——不允许把其他资料的版本绑定为当前版
        raise VersionServiceError("版本不属于该文档", 404)
    doc.current_version_id = version.id
    doc.updated_at = utc_now()


def snapshot_working_copy(db: Session, doc: Document, note: str | None = None,
                          source_path: str | None = None,
                          prepared_snapshot: tuple | None = None) -> tuple[Version, bool]:
    """
    从工作位置(或指定源路径)创建快照新版本。

    Returns:
        (version, created):created=False 表示内容与当前版一致,返回的是当前版本。
    """
    source = Path(source_path) if source_path else working_path(doc)
    library = _library()

    try:
        if prepared_snapshot is not None:
            storage_key, content_hash, manifest_hash, size = prepared_snapshot
        elif doc.is_folder:
            storage_key, content_hash, manifest_hash, size = snapshots.store_folder_snapshot(library, source)
        else:
            storage_key, content_hash, size = snapshots.store_file_snapshot(library, source)
            manifest_hash = None
    except SnapshotError as e:
        raise VersionServiceError(str(e), 500) from e

    current = db.get(Version, doc.current_version_id) if doc.current_version_id else None
    if current and current.sha256_hash == content_hash:
        return current, False

    version = Version(
        document_id=doc.id,
        version_number=_next_version_number(db, doc),
        file_path=str(snapshots.resolve_object(library, storage_key)),
        relative_path=None,
        sha256_hash=content_hash,
        manifest_hash=manifest_hash,
        file_size=size,
        original_filename=source.name,
        note=note,
        is_current=False,
    )
    db.add(version)
    db.flush()
    _mark_current(db, doc, version)

    # 当前版本统计以最新快照为准
    if doc.is_folder:
        doc.file_count, doc.total_size = snapshots.folder_statistics(Path(version.file_path))
    else:
        doc.file_count = 1
        doc.total_size = size

    return version, True


def restore_working_version(db: Session, doc: Document, target: Version) -> Version:
    """恢复到原工作位置；文件替换和当前版本提交失败时补偿回滚。"""
    from backend.app.core.changelog import record_change
    if target.document_id != doc.id:
        raise VersionServiceError("版本不属于该文档", 404)
    if doc.storage_mode == 'index' or doc.status not in {'pending', 'organized'}:
        raise VersionServiceError("请先将资料恢复为正常受管状态，再恢复版本", 409)
    library = _library()
    work = working_path(doc)
    # 不得覆盖另一份已登记资料及其内部内容。
    for other in db.query(Document).filter(Document.id != doc.id, Document.status != 'trashed').all():
        if not other.storage_path and other.storage_mode != 'index':
            continue
        other_path = working_path(other).resolve()
        if other_path == work or other_path.is_relative_to(work) or work.is_relative_to(other_path):
            raise VersionServiceError("工作位置与其他资料重叠，无法恢复版本", 409)
    key = _storage_key(target)
    obj = snapshots.resolve_object(library, key)
    expected = target.manifest_hash if doc.is_folder else target.sha256_hash
    if not obj.is_file() or obj.is_symlink() or snapshots._hash_file(obj) != expected:
        raise VersionServiceError("历史版本快照缺失或校验失败，工作文件未修改", 500)

    work.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.teamhub-restore-', dir=work.parent))
    backup = staging / 'original' / work.name
    old_moved = installed = committed = keep_staging = False
    previous_id = doc.current_version_id
    try:
        ready = snapshots.export_object(library, key, work.name, staging / 'ready', doc.is_folder)
        if doc.is_folder:
            manifest, _ = snapshots._build_folder_manifest(ready)
            restored_hash = hashlib.sha256(manifest).hexdigest()
        else:
            restored_hash = snapshots._hash_file(ready)
        if restored_hash != target.sha256_hash:
            raise VersionServiceError("历史版本内容校验失败，工作文件未修改", 500)

        protected = None
        if work.exists() or work.is_symlink():
            if work.is_symlink() or work.is_dir() != doc.is_folder:
                raise VersionServiceError("工作文件类型发生变化，无法安全恢复", 409)
            backup.parent.mkdir()
            os.rename(work, backup)
            old_moved = True
            protected, created = snapshot_working_copy(
                db, doc, source_path=str(backup),
                note=f"恢复到 v{target.version_number} 前自动保存的修改",
            )
            if not created:
                protected = None
        if work.exists() or work.is_symlink():
            raise VersionServiceError("工作位置被占用，恢复已停止", 409)
        os.rename(ready, work)
        installed = True
        _mark_current(db, doc, target)
        if doc.is_folder:
            doc.file_count, doc.total_size = snapshots.folder_statistics(obj)
        else:
            doc.file_count, doc.total_size = 1, target.file_size
        record_change(db, 'document', doc.id, 'restore_version', {
            'from_version_id': previous_id, 'to_version_id': target.id,
            'protected_version_id': protected.id if protected else None,
        })
        db.commit()
        committed = True
        return target
    except Exception as exc:
        db.rollback()
        try:
            if installed:
                os.rename(work, staging / 'failed-restored')
            if old_moved:
                if work.exists() or work.is_symlink():
                    raise OSError("工作位置已被占用")
                os.rename(backup, work)
        except OSError as rollback_error:
            keep_staging = True
            logger.exception("版本恢复补偿失败，原文件保留于 %s", backup)
            raise VersionServiceError(f"恢复未完成，原文件保留于 {backup}，请勿删除该目录", 500) from rollback_error
        logger.exception("恢复版本失败，已回滚工作文件及数据库")
        if isinstance(exc, VersionServiceError):
            raise
        raise VersionServiceError("恢复失败，原工作文件与版本关系已保留", 500) from exc
    finally:
        if not keep_staging:
            try:
                shutil.rmtree(staging)
            except OSError:
                logger.warning("版本恢复暂存待清理: %s (committed=%s)", staging, committed)


def export_version(doc: Document, target: Version, target_dir: str) -> Path:
    """把指定版本的快照内容复制到目标目录,不覆写已有文件"""
    if target.document_id != doc.id:
        raise VersionServiceError("版本不属于该文档", 404)
    try:
        return snapshots.export_object(
            _library(), _storage_key(target), target.original_filename,
            Path(target_dir), doc.is_folder,
        )
    except SnapshotError as e:
        raise VersionServiceError(str(e), 500) from e


def _storage_key(version: Version) -> str:
    """从 file_path(快照对象绝对路径)推导存储键"""
    library = _library()
    try:
        rel = Path(version.file_path).relative_to(library / ".teamhub")
        return str(rel)
    except ValueError:
        raise VersionServiceError(f"版本文件不在对象库内: {version.file_path}", 500)
