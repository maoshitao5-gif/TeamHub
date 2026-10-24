"""
不可变快照对象存储(M2;M3 扩展文件夹清单语义)

按 docs/MVP数据与接口契约.md §1/§3.3:
- 快照对象存 <文件库>/.teamhub/objects/<sha256前2位>/<sha256>,内容寻址,写入后不可改写;
- 写入路径:流式复制到临时文件,同时计算 sha256,校验大小后原子 rename 落位;
- 同内容(同哈希)对象直接复用,不重复占用磁盘;
- 文件夹:确定性清单(manifest)+ 确定性 zip 归档;
  - 内容摘要 = 清单字节 sha256(判断文件夹内容身份);
  - 归档摘要(manifest_hash) = zip 归档字节 sha256(传输校验);二者分开保存(契约 §3.3.6);
  - 空目录纳入;符号链接拒绝;应用自身管理目录 .teamhub 排除,其余隐藏项不一律忽略(§3.3.4)。
"""
import hashlib
import json
import os
import shutil
import stat
import time
from contextlib import nullcontext
import uuid
import zipfile
from pathlib import Path

from backend.app.core.logger import get_logger
from backend.app.config import settings

logger = get_logger("services.snapshots")

_CHUNK = 1024 * 1024  # 1MB 流式块

# zip 内固定时间戳,保证同内容文件夹产出同字节归档
_FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)

# 清单编码版本(契约 §3.3.1:有版本号的规范化编码)
_MANIFEST_VERSION = "1"

# 应用自身管理目录,快照时排除(契约 §3.3.4)
_EXCLUDED_DIRS = {".teamhub"}

# 符号链接条目的 mode 位(S_IFLNK)
_S_IFLNK = 0o120000


class SnapshotError(Exception):
    """快照存储/读取失败,对应契约错误码 STORAGE_ERROR"""


class SnapshotChangedError(SnapshotError):
    """捕获期间源内容或目录发生变化；可以重新捕获，不能复用半成品。"""


def objects_dir(library: Path) -> Path:
    return library / ".teamhub" / "objects"


def storage_key_for(sha256_hex: str) -> str:
    """内容摘要 → 存储键(相对文件库 .teamhub 目录)"""
    return f"objects/{sha256_hex[:2]}/{sha256_hex}"


def resolve_object(library: Path, storage_key: str) -> Path:
    """存储键 → 对象绝对路径;越界键拒绝解析"""
    if not storage_key or storage_key.startswith("/") or ".." in storage_key.split("/"):
        raise SnapshotError(f"非法存储键: {storage_key!r}")
    return library / ".teamhub" / storage_key


def _hash_file(path: Path) -> str:
    """流式计算单文件 sha256"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _place_tmp(library: Path, tmp_path: Path, digest: str, size: int) -> str:
    """校验已有对象，原子落位；任何失败都清理本次暂存。"""
    key = storage_key_for(digest)
    dest = resolve_object(library, key)
    try:
        if dest.exists():
            if dest.stat().st_size != size or _hash_file(dest) != digest:
                raise SnapshotError(f"快照对象校验失败，拒绝复用: {dest}")
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp_path, dest)
        return key
    except OSError as exc:
        raise SnapshotError(f"快照落盘失败: {exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)


def _file_identity(stat):
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def store_file_snapshot(library: Path, source: Path) -> tuple[str, str, int]:
    """流式捕获单文件；源文件在读取期间变化时明确失败。"""
    if source.is_symlink() or not source.is_file():
        raise SnapshotError(f"快照源文件不存在或为符号链接: {source}")
    tmp_path = objects_dir(library) / "tmp" / uuid.uuid4().hex
    digest = hashlib.sha256()
    size = 0
    try:
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        with open(source, "rb") as src, open(tmp_path, "xb") as dst:
            before = os.fstat(src.fileno())
            for chunk in iter(lambda: src.read(_CHUNK), b""):
                digest.update(chunk)
                dst.write(chunk)
                size += len(chunk)
            if (_file_identity(before) != _file_identity(os.fstat(src.fileno()))
                    or _file_identity(before) != _file_identity(source.stat())
                    or size != before.st_size):
                raise SnapshotError("文件在保存期间发生变化，请完成编辑后重试")
            dst.flush()
            os.fsync(dst.fileno())
        content_hash = digest.hexdigest()
        key = _place_tmp(library, tmp_path, content_hash, size)
        return key, content_hash, size
    except OSError as exc:
        raise SnapshotError(f"快照写入失败({source}): {exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)


def _safe_relative_name(name: str) -> str:
    parts = name.rstrip("/").split("/")
    if (not name or name.startswith("/") or "\\" in name or ":" in name
            or any(part in {"", ".", ".."} for part in parts)):
        raise SnapshotError(f"不支持的相对路径: {name!r}")
    return "/".join(parts)


def _folder_inventory(folder: Path) -> dict:
    """严格遍历；记录目录及文件身份，以便捕获前后核对。"""
    def walk_error(error):
        raise SnapshotError(f"目录读取失败: {error}") from error
    if folder.is_symlink() or not folder.is_dir():
        raise SnapshotError(f"目录不存在或为符号链接: {folder}")
    inventory = {"": folder.stat()}
    names = set()
    total = 0
    for root, dirs, files in os.walk(folder, onerror=walk_error, followlinks=False):
        kept_dirs = []
        for name in sorted(dirs + files):
            full = Path(root) / name
            info = full.lstat()
            rel = _safe_relative_name(full.relative_to(folder).as_posix())
            if stat.S_ISLNK(info.st_mode):
                raise SnapshotError(f"不支持符号链接: {rel}")
            if stat.S_ISDIR(info.st_mode):
                if name in _EXCLUDED_DIRS:
                    continue
                kept_dirs.append(name)
            elif stat.S_ISREG(info.st_mode):
                total += info.st_size
                if info.st_size > settings.snapshot_max_file_bytes or total > settings.snapshot_max_total_bytes:
                    raise SnapshotError("文件夹内容超过大小上限")
            else:
                raise SnapshotError(f"不支持特殊文件: {rel}")
            if rel.casefold() in names:
                raise SnapshotError(f"路径大小写冲突，无法安全复原: {rel}")
            names.add(rel.casefold())
            inventory[rel] = info
            if len(inventory) - 1 > settings.snapshot_max_entries:
                raise SnapshotError("文件夹条目超过数量上限")
        dirs[:] = sorted(kept_dirs)
    return inventory


def _capture_folder(folder: Path, archive=None) -> tuple[bytes, list[dict]]:
    """流式写入归档的同一批字节用于计算清单，不二次读取内容。"""
    before = _folder_inventory(folder)
    entries = []
    for rel in sorted(before):
        if not rel:
            continue
        info = before[rel]
        is_dir = stat.S_ISDIR(info.st_mode)
        zip_info = zipfile.ZipInfo(rel + ("/" if is_dir else ""), date_time=_FIXED_DATE_TIME)
        zip_info.external_attr = (0o40755 if is_dir else 0o100644) << 16
        if is_dir:
            if archive:
                archive.writestr(zip_info, b"")
            entries.append({"path": rel, "type": "dir", "size": 0, "sha256": None})
            continue
        zip_info.compress_type = zipfile.ZIP_DEFLATED
        digest = hashlib.sha256()
        size = 0
        # O_NOFOLLOW 在支持的平台阻止最后一级被替换为链接。
        fd = os.open(folder / rel, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as src:
            if _file_identity(os.fstat(src.fileno())) != _file_identity(info):
                raise SnapshotChangedError(f"文件夹在快照期间发生变化: {rel}")
            output = archive.open(zip_info, "w", force_zip64=True) if archive else nullcontext(None)
            with output as dst:
                for chunk in iter(lambda: src.read(_CHUNK), b""):
                    size += len(chunk)
                    if size > info.st_size or size > settings.snapshot_max_file_bytes:
                        raise SnapshotChangedError(f"文件在快照期间发生变化或超过上限: {rel}")
                    digest.update(chunk)
                    if dst:
                        dst.write(chunk)
            if size != info.st_size or _file_identity(os.fstat(src.fileno())) != _file_identity(info):
                raise SnapshotChangedError(f"文件夹在快照期间发生变化: {rel}")
        entries.append({"path": rel, "type": "file", "size": size, "sha256": digest.hexdigest()})
    after = _folder_inventory(folder)
    if before.keys() != after.keys() or any(_file_identity(before[p]) != _file_identity(after[p]) for p in before):
        raise SnapshotChangedError("目录结构或文件在快照期间发生变化")
    manifest = {"version": _MANIFEST_VERSION, "entries": entries}
    return json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8"), entries


def _build_folder_manifest(folder: Path) -> tuple[bytes, list[dict]]:
    try:
        return _capture_folder(folder)
    except OSError as exc:
        raise SnapshotError(f"文件夹读取失败: {exc}") from exc


def store_folder_snapshot(library: Path, folder: Path) -> tuple[str, str, str, int]:
    """短暂变化时重新捕获；每次都完整校验，持续变化则明确失败。"""
    for attempt in range(3):
        try:
            return _store_folder_snapshot_once(library, folder)
        except SnapshotChangedError:
            if attempt == 2:
                raise
            logger.info("文件夹捕获期间发生变化，重新捕获 (%s/3): %s", attempt + 2, folder)
            time.sleep(0.2 * (attempt + 1))


def _store_folder_snapshot_once(library: Path, folder: Path) -> tuple[str, str, str, int]:
    """归档与清单来自同一次读取；失败无成功记录，暂存始终清理。"""
    tmp_path = objects_dir(library) / "tmp" / uuid.uuid4().hex
    try:
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as archive:
            manifest, _ = _capture_folder(folder, archive)
        with open(tmp_path, "rb") as stored:
            os.fsync(stored.fileno())
        archive_hash = _hash_file(tmp_path)
        size = tmp_path.stat().st_size
        key = _place_tmp(library, tmp_path, archive_hash, size)
        return key, hashlib.sha256(manifest).hexdigest(), archive_hash, size
    except OSError as exc:
        raise SnapshotError(f"文件夹快照失败: {exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)


def _checked_zip_entries(archive) -> list:
    entries = archive.infolist()
    if len(entries) > settings.snapshot_max_entries:
        raise SnapshotError("归档条目超过数量上限")
    names = set()
    total = 0
    for info in entries:
        rel = _safe_relative_name(info.filename)
        mode = (info.external_attr >> 16) & 0o170000
        if mode not in (0, stat.S_IFDIR, stat.S_IFREG):
            raise SnapshotError(f"归档含链接或特殊文件: {rel}")
        if rel.casefold() in names:
            raise SnapshotError(f"归档路径重复或大小写冲突: {rel}")
        names.add(rel.casefold())
        total += info.file_size
        if info.file_size > settings.snapshot_max_file_bytes or total > settings.snapshot_max_total_bytes:
            raise SnapshotError("归档展开大小超过上限")
    return entries


def folder_statistics(obj: Path) -> tuple[int, int]:
    with zipfile.ZipFile(obj) as archive:
        files = [info for info in _checked_zip_entries(archive) if not info.is_dir()]
        return len(files), sum(info.file_size for info in files)


def _extract_zip_safely(obj: Path, target: Path) -> None:
    """全量预检后流式解包；拒绝不安全路径及超限内容。"""
    with zipfile.ZipFile(obj) as archive:
        entries = _checked_zip_entries(archive)
        total = 0
        for info in entries:
            dest = target / _safe_relative_name(info.filename)
            if not dest.resolve().is_relative_to(target.resolve()):
                raise SnapshotError(f"归档路径越界: {info.filename}")
            if info.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            size = 0
            with archive.open(info) as src, open(dest, "xb") as dst:
                for chunk in iter(lambda: src.read(_CHUNK), b""):
                    size += len(chunk)
                    total += len(chunk)
                    if size > info.file_size or total > settings.snapshot_max_total_bytes:
                        raise SnapshotError("归档实际展开大小超过上限")
                    dst.write(chunk)
            if size != info.file_size:
                raise SnapshotError("归档条目大小不一致")


def export_object(library: Path, storage_key: str, original_filename: str,
                  target_dir: Path, is_folder: bool) -> Path:
    """完整写入后才公开导出文件；同名文件不覆盖，失败不遗留半份内容。"""
    obj = resolve_object(library, storage_key)
    if not obj.is_file():
        raise SnapshotError(f"快照对象缺失: {storage_key}")
    if (not original_filename or original_filename in {".", ".."}
            or "/" in original_filename or "\\" in original_filename):
        raise SnapshotError("导出文件名不合法")
    if not Path(target_dir).is_absolute():
        raise SnapshotError("导出目录必须为绝对路径")
    target_dir = Path(target_dir).resolve()
    if target_dir.is_relative_to((library / ".teamhub").resolve()):
        raise SnapshotError("不能导出到内部管理目录")
    target = target_dir / original_filename
    tmp_path = target_dir / f".teamhub-export-{uuid.uuid4().hex}"
    made_folder = False
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            raise SnapshotError(f"目标位置已存在,导出未覆写: {target}")
        if is_folder:
            target.mkdir()
            made_folder = True
            _extract_zip_safely(obj, target)
        else:
            with open(obj, "rb") as src, open(tmp_path, "xb") as dst:
                shutil.copyfileobj(src, dst, _CHUNK)
                dst.flush()
                os.fsync(dst.fileno())
            # 与目标同卷；link 对已存在目标报错，消除 exists/copy 的覆盖窗口。
            os.link(tmp_path, target)
        return target
    except (OSError, zipfile.BadZipFile, SnapshotError) as exc:
        if made_folder:
            shutil.rmtree(target)
        raise SnapshotError(f"导出失败({obj} → {target}): {exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)
