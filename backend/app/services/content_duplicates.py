"""Compare current managed entities; history and names cannot establish duplication."""
import hashlib
import os
import re
import stat
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.app.models import Document
from backend.app.core.storage_paths import document_path
from backend.app.services import snapshots

_CHUNK = 1024 * 1024
_HASH = re.compile(r'^[0-9a-f]{64}$')


class DuplicateCheckError(Exception):
    """An incomplete or unstable scan must not be reported as successful deduplication."""


def _identity(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _stable_file_hash(path: Path, initial) -> str:
    digest = hashlib.sha256()
    size = 0
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or _identity(initial) != _identity(before):
            raise DuplicateCheckError('文件在查重期间发生变化，请完成编辑后重试')
        for chunk in iter(lambda: stream.read(_CHUNK), b''):
            digest.update(chunk)
            size += len(chunk)
            if size > before.st_size:
                raise DuplicateCheckError('文件在查重期间发生变化，请完成编辑后重试')
        if (size != before.st_size or _identity(before) != _identity(os.fstat(stream.fileno()))
                or _identity(before) != _identity(path.lstat())):
            raise DuplicateCheckError('文件在查重期间发生变化，请完成编辑后重试')
    return digest.hexdigest()


def find_current_duplicate(db: Session, *, content_hash: str, file_size: int,
                           is_folder: bool) -> Document | None:
    """Only pending/organized managed entities participate, using their actual bytes.

    A folder's hash is its canonical manifest, not the transport ZIP hash. For a
    file, actual stat size filters before hashing; stale database size/hash does
    not affect the comparison. Missing/unmanaged/symlink entities are skipped.
    All other I/O errors or changes stop intake before any download or creation.
    """
    if (not isinstance(content_hash, str) or not _HASH.fullmatch(content_hash)
            or isinstance(file_size, bool) or not isinstance(file_size, int) or file_size < 0):
        raise DuplicateCheckError('远端资料摘要或大小无效，无法完成实体查重')
    rows = db.query(Document).filter(Document.status.in_(('pending', 'organized')),
        Document.is_folder.is_(is_folder), Document.storage_mode != 'index',
        Document.storage_path.isnot(None)).order_by(Document.created_at, Document.id).all()
    for doc in rows:
        try:
            path = document_path(doc)
        except HTTPException as exc:
            if '符号链接' in str(exc.detail):
                continue
            raise DuplicateCheckError('本机资料登记位置无效，无法完成实体查重') from exc
        try:
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode):
                continue
            if is_folder:
                if not stat.S_ISDIR(info.st_mode):
                    continue
                manifest, _ = snapshots._build_folder_manifest(path)
                digest = hashlib.sha256(manifest).hexdigest()
                if _identity(info) != _identity(path.lstat()):
                    raise DuplicateCheckError('文件夹在查重期间发生变化，请完成编辑后重试')
            else:
                if not stat.S_ISREG(info.st_mode) or info.st_size != file_size:
                    continue
                digest = _stable_file_hash(path, info)
            if digest == content_hash:
                return doc
        except (OSError, snapshots.SnapshotError) as exc:
            raise DuplicateCheckError('无法稳定读取本机资料，查重未完成，请检查文件后重试') from exc
    return None


def duplicate_result(doc: Document) -> dict:
    return dict(status='duplicate', duplicate=True, code='DUPLICATE_CONTENT',
                message='本地已存在相同文件，未重复收录', document_id=doc.id, name=doc.name,
                storage_path=doc.storage_path, document_status=doc.status,
                duplicate_document=dict(id=doc.id, name=doc.name, status=doc.status,
                                        storage_path=doc.storage_path))
