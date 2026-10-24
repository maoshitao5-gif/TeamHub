"""Durable remote operations and explicit local receipt of a fixed shared version.

The working file is independent of its current saved snapshot. A receipt compares
both with the fixed remote target; filesystem changes have a persisted recovery
record before the first rename. Recovery never overwrites an unrecognised edit.
"""
import hashlib
import json
import os
import tempfile
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models import Document, RemoteOperation, ShareLink, Version, VersionSource
from backend.app.services import snapshots, versions


class RemoteUpdateError(versions.VersionServiceError):
    pass


def _json(value: dict) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def freeze_operation(db: Session, *, server_id: str, account_id: str,
                     workspace_id: str, request_id: str, kind: str,
                     payload: dict, local_document_id: str | None = None) -> RemoteOperation:
    """Commit a frozen request before network I/O; retries must have the same input.

    Callers must prepare and flush any local snapshot before this commit. No
    token belongs in payload. The identity scope deliberately excludes `kind`,
    so a reused request ID cannot silently become another operation.
    """
    if not all((server_id, account_id, workspace_id, request_id, kind)):
        raise RemoteUpdateError('远端操作缺少稳定身份或请求编号', 400)
    query = db.query(RemoteOperation).filter_by(
        server_id=server_id, account_id=account_id, workspace_id=workspace_id,
        request_id=request_id,
    )
    operation = query.first()
    encoded = _json(payload)
    if not operation:
        operation = RemoteOperation(
            server_id=server_id, account_id=account_id, workspace_id=workspace_id,
            request_id=request_id, kind=kind, local_document_id=local_document_id,
            input_json=encoded, status='pending',
        )
        db.add(operation)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            operation = query.one()
    if (operation.kind != kind or operation.local_document_id != local_document_id
            or operation.input_json != encoded):
        raise RemoteUpdateError('同一请求编号不能改变目标、身份或已冻结内容', 409)
    return operation


def record_source(db: Session, doc: Document, version: Version, *, server_id: str,
                  account_id: str, workspace_id: str, source_type: str,
                  shared_document_id: str | None = None, shared_version_id: str | None = None,
                  delivery_id: str | None = None, item_id: str | None = None,
                  operation_id: str | None = None) -> VersionSource:
    """Record correspondence without changing a version's author or creation time."""
    if version.document_id != doc.id or source_type not in {'shared', 'delivery'}:
        raise RemoteUpdateError('版本来源不属于当前资料或来源类型无效', 400)
    if source_type == 'shared' and not all((shared_document_id, shared_version_id)):
        raise RemoteUpdateError('共享版本来源不完整', 400)
    if source_type == 'delivery' and not all((delivery_id, item_id)):
        raise RemoteUpdateError('投递附件来源不完整', 400)
    values = dict(local_document_id=doc.id, local_version_id=version.id,
                  server_id=server_id, account_id=account_id, workspace_id=workspace_id,
                  source_type=source_type, shared_document_id=shared_document_id,
                  shared_version_id=shared_version_id, delivery_id=delivery_id, item_id=item_id)
    source = db.query(VersionSource).filter_by(**values).first()
    if source is None:
        source = VersionSource(**values, operation_id=operation_id)
        db.add(source)
        db.flush()
    return source


def content_hash(path: Path, is_folder: bool) -> str:
    if path.is_symlink() or not path.exists() or path.is_dir() != is_folder:
        raise RemoteUpdateError('工作位置缺失或类型变化，未覆盖本地内容', 409)
    if is_folder:
        manifest, _ = snapshots._build_folder_manifest(path)
        return hashlib.sha256(manifest).hexdigest()
    return snapshots._hash_file(path)


def capture_working(doc: Document) -> tuple:
    library, work = Path(settings.library_path), versions.working_path(doc)
    if doc.is_folder:
        return snapshots.store_folder_snapshot(library, work)
    key, digest, size = snapshots.store_file_snapshot(library, work)
    return key, digest, None, size


def bind_link(link: ShareLink, *, server_id: str, account_id: str, server_url: str) -> None:
    """Bind a verified legacy URL once; never infer an identity from its hostname."""
    if link.server_id and link.server_id != server_id:
        raise RemoteUpdateError('当前服务身份与资料来源不一致', 409)
    if link.account_id and link.account_id != account_id:
        raise RemoteUpdateError('当前账号与该资料的已确认关联不一致', 409)
    if not link.server_id and link.server_url.rstrip('/') != server_url.rstrip('/'):
        raise RemoteUpdateError('旧关联须先连接原地址核实服务身份', 409)
    link.server_id, link.account_id = server_id, account_id
    link.server_url = server_url.rstrip('/')


def _check_managed(db: Session, doc: Document) -> Path:
    if doc.storage_mode == 'index' or doc.status not in {'pending', 'organized'}:
        raise RemoteUpdateError('请先将资料恢复为正常受管状态', 409)
    work = versions.working_path(doc)
    if work.is_symlink() or any(parent.is_symlink() for parent in work.parents):
        raise RemoteUpdateError('工作路径包含符号链接', 409)
    for other in db.query(Document).filter(Document.id != doc.id, Document.status != 'trashed'):
        if not other.storage_path and other.storage_mode != 'index':
            continue
        other_path = versions.working_path(other).resolve()
        if work == other_path or work.is_relative_to(other_path) or other_path.is_relative_to(work):
            raise RemoteUpdateError('工作位置与另一份受管资料重叠', 409)
    return work


def _restore_interrupted(db: Session, operation: RemoteOperation) -> bool:
    """Compensate an uncommitted install only when current bytes are recognised."""
    try:
        state = json.loads(operation.recovery_json or '{}')
        if not state:
            operation.status = 'interrupted'
            db.commit()
            return True
        doc = db.get(Document, operation.local_document_id)
        if not doc or str(versions.working_path(doc)) != state['work']:
            raise RemoteUpdateError('资料位置已改变，保留恢复目录', 409)
        work, backup = Path(state['work']), Path(state['backup'])
        if (not work.is_absolute() or not backup.is_absolute()
                or '..' in work.parts or '..' in backup.parts
                or backup.name != work.name or backup.parent.parent != work.parent
                or not backup.parent.name.startswith('.teamhub-update-')):
            raise RemoteUpdateError('恢复目录关系无效，未改动文件', 409)
        if any(path.is_symlink() for path in (work, backup, *work.parents, *backup.parents)):
            raise RemoteUpdateError('恢复位置出现符号链接', 409)
        if backup.exists():
            if work.exists():
                if content_hash(work, doc.is_folder) != state['target_hash']:
                    raise RemoteUpdateError('工作内容在中断后被编辑，双方内容均已保留', 409)
                # Preserve the installed bytes too; never delete content during recovery.
                preserved = backup.parent / ('uncommitted-' + operation.id)
                if preserved.exists():
                    raise RemoteUpdateError('恢复保留位置已存在', 409)
                os.rename(work, preserved)
                if content_hash(preserved, doc.is_folder) != state['target_hash']:
                    # An external writer changed the file during the move. Keep both.
                    raise RemoteUpdateError('补偿期间检测到外部修改，保留双方内容', 409)
            if work.exists() or work.is_symlink():
                raise RemoteUpdateError('恢复位置被占用', 409)
            # Export with exclusive creation; retain the backup if another writer races.
            if doc.is_folder:
                snapshot = snapshots.store_folder_snapshot(Path(settings.library_path), backup)
            else:
                key, digest, size = snapshots.store_file_snapshot(Path(settings.library_path), backup)
                snapshot = key, digest, None, size
            snapshots.export_object(Path(settings.library_path), snapshot[0], work.name,
                                    work.parent, doc.is_folder)
        elif not work.exists() or content_hash(work, doc.is_folder) != state['working_hash']:
            raise RemoteUpdateError('原工作内容无法确认，保留操作状态等待处理', 409)
        operation.status, operation.error = 'interrupted', '更新未完成，原工作内容已保留；可用原编号重试'
        db.commit()
        return True
    except (OSError, snapshots.SnapshotError, versions.VersionServiceError, HTTPException,
            ValueError, KeyError, TypeError) as exc:
        operation.status, operation.error = 'needs_review', str(getattr(exc, 'detail', exc))
        db.commit()
        return False


def recover_pending_updates(db: Session) -> list[dict]:
    """Startup recovery is local and runs before scanning; it never contacts cloud."""
    results = []
    operations = db.query(RemoteOperation).filter(
        RemoteOperation.kind == 'update_local', RemoteOperation.status == 'installing',
    ).all()
    for operation in operations:
        _restore_interrupted(db, operation)
        results.append(dict(operation_id=operation.id, status=operation.status, error=operation.error))
    return results


def apply_received_snapshot(db: Session, doc: Document, link: ShareLink,
                            operation: RemoteOperation, prepared: tuple,
                            remote_version: dict) -> dict:
    """Apply the fixed, verified target and atomically confirm source/base metadata."""
    if operation.kind != 'update_local' or operation.local_document_id != doc.id:
        raise RemoteUpdateError('操作不属于此资料', 409)
    if operation.status == 'succeeded':
        # A retry is only a receipt lookup: never replay writes over later edits.
        return json.loads(operation.result_json)
    if operation.status == 'installing':
        _restore_interrupted(db, operation)
    if operation.status == 'needs_review':
        raise RemoteUpdateError(operation.error or '更新待人工核对，已保留双方内容', 409)
    work = _check_managed(db, doc)
    current = db.get(Version, doc.current_version_id)
    if not current:
        raise RemoteUpdateError('本地资料缺少当前版本', 409)
    library = Path(settings.library_path)
    target_hash = prepared[1]
    obj = snapshots.resolve_object(library, prepared[0])
    if (not obj.is_file() or obj.is_symlink()
            or snapshots._hash_file(obj) != (prepared[2] if doc.is_folder else target_hash)
            or target_hash != remote_version['content_hash']):
        raise RemoteUpdateError('云端暂存内容校验失败', 422)
    captured = capture_working(doc)
    working_hash, previous_id = captured[1], current.id
    old_state = json.loads(operation.recovery_json or '{}')
    protected = None
    if working_hash != current.sha256_hash and working_hash != target_hash:
        protected_id = old_state.get('protected_version_id')
        existing = db.get(Version, protected_id) if protected_id else None
        if existing and existing.document_id == doc.id and existing.sha256_hash == working_hash:
            protected = existing
        else:
            protected, _ = versions.snapshot_working_copy(
                db, doc, prepared_snapshot=captured, note='更新前自动保存的修改')
            doc.current_version_id = previous_id
            doc.file_count, doc.total_size = (snapshots.folder_statistics(Path(current.file_path))
                                             if doc.is_folder else (1, current.file_size))
    needs_install = working_hash != target_hash
    state = dict(work=str(work), working_hash=working_hash, target_hash=target_hash,
                 previous_id=previous_id, protected_version_id=protected.id if protected else None)
    if needs_install:
        staging = Path(tempfile.mkdtemp(prefix='.teamhub-update-', dir=work.parent))
        state['backup'] = str(staging / work.name)
    else:
        # No filesystem operation is required, but protection/receipt metadata is durable.
        state['backup'] = ''
    operation.recovery_json, operation.status = _json(state), 'pending'
    db.commit()
    try:
        if content_hash(work, doc.is_folder) != working_hash:
            raise RemoteUpdateError('下载或准备期间本地内容已修改，请重新确认更新', 409)
        if needs_install:
            operation.status = 'installing'
            db.commit()  # Must be durable before the working copy leaves its location.
            backup = Path(state['backup'])
            os.rename(work, backup)
            if content_hash(backup, doc.is_folder) != working_hash:
                raise RemoteUpdateError('切换期间检测到外部修改，更新中止', 409)
            snapshots.export_object(library, prepared[0], work.name, work.parent, doc.is_folder)
            if content_hash(backup, doc.is_folder) != working_hash:
                raise RemoteUpdateError('原工作文件仍被外部程序编辑，更新中止', 409)
        if content_hash(work, doc.is_folder) != target_hash:
            raise RemoteUpdateError('工作内容在切换期间改变，未推进提交基准', 409)
        # Compare against the original current snapshot, not against a protection version.
        if current.sha256_hash == target_hash:
            received, created = current, False
            versions._mark_current(db, doc, current)
        else:
            doc.current_version_id = previous_id
            received, created = versions.snapshot_working_copy(
                db, doc, prepared_snapshot=prepared,
                note=f"接收自云端 v{remote_version.get('version_number', '?')}")
        record_source(db, doc, received, server_id=operation.server_id,
                      account_id=operation.account_id, workspace_id=operation.workspace_id,
                      source_type='shared', shared_document_id=link.shared_document_id,
                      shared_version_id=remote_version['id'], operation_id=operation.id)
        link.base_version_id = remote_version['id']
        result = dict(document_id=doc.id, local_version_id=received.id,
                      version_number=received.version_number, created=created,
                      protected_version_id=protected.id if protected else None,
                      remote_version_id=remote_version['id'], request_id=operation.request_id)
        operation.result_json, operation.status, operation.error = _json(result), 'succeeded', None
        db.commit()
        return result
    except Exception:
        db.rollback()
        operation = db.get(RemoteOperation, operation.id)
        if operation.status == 'installing':
            _restore_interrupted(db, operation)
        raise
