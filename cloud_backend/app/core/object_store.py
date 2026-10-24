"""受管对象存储：上传先完整落盘，校验后建引用；暂存保留以支持提交失败重试。"""
import hashlib
import fcntl
import json
import os
import re
import stat
import uuid
import zipfile
import time
import zlib
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from app.config import settings

CHUNK_SIZE = 1024 * 1024
STAGING_METADATA_BYTES = 4096


class StorageValidationError(ValueError):
    pass


class StagingCapacityError(StorageValidationError):
    pass


def _root():
    return Path(settings.storage_dir)


def _staging_dir():
    return _root() / 'staging'


def object_path(content_hash):
    if not re.fullmatch(r'[0-9a-f]{64}', content_hash):
        raise StorageValidationError('摘要格式不合法')
    return _root() / 'objects' / content_hash[:2] / content_hash


def storage_key_for(content_hash):
    object_path(content_hash)
    return f'objects/{content_hash[:2]}/{content_hash}'


def _upload_path(upload_id, suffix):
    try:
        if str(uuid.UUID(upload_id)) != upload_id:
            return None
    except (ValueError, TypeError, AttributeError):
        return None
    return _staging_dir() / f'{upload_id}.{suffix}'


def safe_name(name):
    parts = name.rstrip('/').split('/')
    if (not name or name.startswith('/') or '\\' in name or ':' in name
            or any(p in {'', '.', '..'} for p in parts)):
        raise StorageValidationError('文件路径不合法')
    return '/'.join(parts)


@contextmanager
def _staging_lock():
    """所有进程在检查总量、写入块和回收票据时共用同一文件锁。"""
    with (_root() / '.staging-quota.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _staged_usage(*, cleanup=False):
    groups = {}
    for path in _staging_dir().iterdir():
        if path.suffix not in ('.bin', '.json', '.lock') or _upload_path(path.stem, 'bin') is None:
            continue
        groups.setdefault(path.stem, []).append(path)
    size, count = 0, 0
    cutoff = time.time() - settings.staging_ttl_seconds
    for upload_id, paths in groups.items():
        if cleanup and max(path.stat().st_mtime for path in paths) < cutoff:
            # 上传期间持有票据锁；即使慢连接超过 TTL，也不能回收正在写入的文件。
            lock_path = _upload_path(upload_id, 'lock')
            with lock_path.open('a+b') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    pass
                else:
                    for path in paths:
                        path.unlink(missing_ok=True)
                    lock_path.unlink(missing_ok=True)
                    continue
        count += 1
        size += STAGING_METADATA_BYTES + sum(path.stat().st_size for path in paths if path.suffix == '.bin')
    return size, count


async def save_upload_stream(stream, *, workspace_id, uploader_id, filename, purpose='publication'):
    if '/' in safe_name(filename) or filename.endswith('/') or len(filename) > 255:
        raise StorageValidationError('文件名不合法')
    upload_id = str(uuid.uuid4())
    _staging_dir().mkdir(parents=True, exist_ok=True)
    path = _upload_path(upload_id, 'bin')
    metadata = _upload_path(upload_id, 'json')
    lock_path = _upload_path(upload_id, 'lock')
    digest, size = hashlib.sha256(), 0
    lease = None
    try:
        with _staging_lock():
            used, count = _staged_usage(cleanup=True)
            if (used + STAGING_METADATA_BYTES > settings.staging_max_bytes
                    or count >= settings.staging_max_uploads):
                raise StagingCapacityError('上传暂存空间已满，请稍后重试或联系管理员')
            lease = lock_path.open('xb')
            fcntl.flock(lease, fcntl.LOCK_EX)
            # 无缓冲写确保其他进程统计的是实际写入量。
            output = path.open('xb', buffering=0)
        with output:
            async for chunk in stream:
                size += len(chunk)
                if size > settings.upload_max_bytes:
                    raise StorageValidationError('上传内容超过大小上限')
                with _staging_lock():
                    used, _ = _staged_usage()
                    if used + len(chunk) > settings.staging_max_bytes:
                        raise StagingCapacityError('上传暂存空间已满，请稍后重试或联系管理员')
                    view = memoryview(chunk)
                    while view:
                        written = output.write(view)
                        if not written:
                            raise OSError('上传写入未完成')
                        view = view[written:]
                digest.update(chunk)
            output.flush()
            os.fsync(output.fileno())
        sidecar = dict(upload_id=upload_id, sha256=digest.hexdigest(), file_size=size,
                       filename=filename, workspace_id=workspace_id, uploader_id=uploader_id, purpose=purpose,
                       created_at=datetime.now(timezone.utc).isoformat())
        with metadata.open('x', encoding='utf-8') as output:
            json.dump(sidecar, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        return sidecar
    except BaseException:
        with _staging_lock():
            path.unlink(missing_ok=True)
            metadata.unlink(missing_ok=True)
            lock_path.unlink(missing_ok=True)
        raise
    finally:
        if lease:
            lease.close()


def load_staging(upload_id):
    path = _upload_path(upload_id, 'json')
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def hash_file(path):
    digest, size = hashlib.sha256(), 0
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(CHUNK_SIZE), b''):
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def inspect_folder(path):
    """重建与个人端同编码的规范化清单；绝不解包不可信归档。"""
    entries, names, total = [], {}, 0
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > settings.snapshot_max_entries:
                raise StorageValidationError('文件夹条目超过上限')
            for info in infos:
                rel = safe_name(info.filename)
                mode = (info.external_attr >> 16) & 0o170000
                if mode not in (0, stat.S_IFREG, stat.S_IFDIR) or info.flag_bits & 1:
                    raise StorageValidationError('不支持链接、特殊文件或加密归档')
                if '.teamhub' in rel.split('/') or rel.casefold() in names:
                    raise StorageValidationError('归档含管理目录、重复或大小写冲突路径')
                names[rel.casefold()] = 'dir' if info.is_dir() else 'file'
                total += info.file_size
                if info.file_size > settings.snapshot_max_file_bytes or total > settings.snapshot_max_total_bytes:
                    raise StorageValidationError('文件夹展开大小超过上限')
                digest, size = hashlib.sha256(), 0
                if info.is_dir():
                    if info.file_size:
                        raise StorageValidationError('目录条目含内容')
                else:
                    with archive.open(info) as source:
                        for chunk in iter(lambda: source.read(CHUNK_SIZE), b''):
                            size += len(chunk)
                            if size > info.file_size:
                                raise StorageValidationError('归档大小不符')
                            digest.update(chunk)
                    if size != info.file_size:
                        raise StorageValidationError('归档大小不符')
                entries.append(dict(path=rel, type='dir' if info.is_dir() else 'file',
                                    size=size, sha256=None if info.is_dir() else digest.hexdigest()))
            for entry in entries:
                parts = entry['path'].split('/')
                for end in range(1, len(parts)):
                    if names.get('/'.join(parts[:end]).casefold()) != 'dir':
                        raise StorageValidationError('文件夹清单缺少父目录或路径冲突')
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError, zlib.error) as exc:
        raise StorageValidationError('无效文件夹归档') from exc
    entries.sort(key=lambda e: e['path'])
    manifest = json.dumps({'version': '1', 'entries': entries}, ensure_ascii=False,
                          sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(manifest).hexdigest(), sum(e['type'] == 'file' for e in entries), total


def validate_upload(upload_id, sidecar, doc_type, content_hash, manifest_hash):
    path = _upload_path(upload_id, 'bin')
    if path is None or not path.is_file():
        raise StorageValidationError('上传内容缺失')
    actual, size = hash_file(path)
    expected = manifest_hash if doc_type == 'folder' else content_hash
    if actual != sidecar['sha256'] or size != sidecar['file_size'] or actual != expected:
        raise StorageValidationError('声明摘要与上传字节不符')
    count, total = 1, size
    if doc_type == 'folder':
        digest, count, total = inspect_folder(path)
        if digest != content_hash:
            raise StorageValidationError('文件夹清单摘要不符')
    return actual, count, total


def promote_staging(upload_id, content_hash):
    source = _upload_path(upload_id, 'bin')
    if source is None:
        raise StorageValidationError('上传不存在')
    target = object_path(content_hash)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)  # 同卷、原子且绝不覆盖既有对象
    except FileExistsError:
        if hash_file(target) != hash_file(source):
            raise StorageValidationError('既有对象损坏，拒绝复用')
    # 保留完整暂存，数据库失败或响应丢失后仍可重试；回收另行实施。
    return storage_key_for(content_hash)
