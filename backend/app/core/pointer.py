"""
指针文件生成模块（参考 Git LFS 设计）
整理完成后为文件/文件夹生成 .ptr 指针文件，存储在 .teamhub/pointers/ 目录
用途：完整性验证、快速去重、未来云同步的 OID 基础
"""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from backend.app.core.logger import get_logger

logger = get_logger("pointer")

CHUNK_SIZE = 128 * 1024  # 128KB 分块，比 file_utils 的 4KB 更高效


def compute_file_hash(path: str) -> str:
    """
    计算单个文件的 SHA-256 哈希，返回 'sha256:{hex64}' 格式
    流式读取，适合大文件
    """
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
            sha256.update(chunk)
    return f"sha256:{sha256.hexdigest()}"


def compute_folder_hashes(folder_path: str) -> tuple[str, list[dict]]:
    """
    递归计算文件夹下所有文件的哈希，返回 (tree_oid, files_list)

    tree_oid 计算规则：
      1. os.walk 遍历，跳过 . 开头的隐藏目录/文件
      2. 每个子文件单独计算 SHA-256（失败则跳过）
      3. 按 relative_path 字母序排序，拼接 "{relative_path}\t{oid}\n"
      4. 对拼接结果求 SHA-256 → tree_oid（确定性：内容相同则哈希相同）

    files_list 每项：{"relative_path": str, "oid": str, "size": int}
    """
    root = Path(folder_path)
    files_list: list[dict] = []

    for dirpath, dirnames, filenames in os.walk(folder_path):
        # 跳过隐藏目录（.teamhub 等）
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for filename in filenames:
            if filename.startswith("."):
                continue
            abs_path = Path(dirpath) / filename
            rel_path = abs_path.relative_to(root).as_posix()
            try:
                oid = compute_file_hash(str(abs_path))
                size = abs_path.stat().st_size
                files_list.append({"relative_path": rel_path, "oid": oid, "size": size})
            except Exception as e:
                logger.warning(f"[指针] 子文件哈希计算失败，跳过: {abs_path}, 错误: {e}")

    # 按路径字母序排序，保证 tree_oid 的确定性
    files_list.sort(key=lambda x: x["relative_path"])

    # 拼接并求 SHA-256
    combined = "".join(f"{f['relative_path']}\t{f['oid']}\n" for f in files_list)
    tree_hex = hashlib.sha256(combined.encode("utf-8")).hexdigest()
    tree_oid = f"sha256:{tree_hex}"

    return tree_oid, files_list


def write_pointer_file(library_path: str, doc_id: str, pointer_data: dict) -> Path:
    """
    将指针数据写入 .teamhub/pointers/{doc_id}.ptr
    每行格式：key value（Git LFS 风格），首行固定为 version 声明
    """
    pointers_dir = Path(library_path) / ".teamhub" / "pointers"
    pointers_dir.mkdir(parents=True, exist_ok=True)
    ptr_path = pointers_dir / f"{doc_id}.ptr"

    lines = [f"version https://teamhub.local/spec/pointer/v1\n"]
    for key, value in pointer_data.items():
        lines.append(f"{key} {value}\n")

    with open(ptr_path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    return ptr_path


def write_folder_manifest(library_path: str, doc_id: str, manifest_data: dict) -> tuple[Path, str]:
    """
    将 manifest 写入 .teamhub/pointers/{doc_id}.manifest.json
    返回 (path, manifest_oid)，manifest_oid 为文件内容的 sha256 hex（无前缀）
    """
    pointers_dir = Path(library_path) / ".teamhub" / "pointers"
    pointers_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = pointers_dir / f"{doc_id}.manifest.json"

    content = json.dumps(manifest_data, ensure_ascii=False, indent=2)
    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write(content)

    manifest_oid_hex = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return manifest_path, manifest_oid_hex


def generate_pointer(library_path: str, doc, organized_storage_path: str) -> Optional[str]:
    """
    主入口：根据 doc.is_folder 自动选择单文件/文件夹流程，生成指针文件。

    返回 64 位 hex（无前缀）供写入 Version.sha256_hash；
    所有异常在内部捕获，失败时 warning 日志 + 返回 None，不影响整理流程。

    参数：
        library_path: 文件库根目录绝对路径
        doc: Document ORM 对象（需要 id / name / is_folder）
        organized_storage_path: 整理后的相对路径（相对于 library_path），
                                 index 模式下为 None/空，直接返回 None
    """
    if not organized_storage_path:
        logger.warning(f"[指针] storage_path 为空，跳过: doc_id={doc.id}")
        return None

    try:
        # Windows 路径分隔符统一为正斜杠（写入指针文件时）
        storage_path_posix = organized_storage_path.replace("\\", "/")
        actual_path = Path(library_path) / organized_storage_path
        organized_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        if not actual_path.exists():
            logger.warning(f"[指针] 文件/文件夹不存在，跳过: {actual_path}")
            return None

        if doc.is_folder:
            # ── 文件夹流程 ──────────────────────────────────────
            tree_oid, files_list = compute_folder_hashes(str(actual_path))
            file_count = len(files_list)
            total_size = sum(f["size"] for f in files_list)

            # 写 manifest
            manifest_data = {
                "doc_id": doc.id,
                "tree_oid": tree_oid,
                "files": files_list,
            }
            _, manifest_oid_hex = write_folder_manifest(library_path, doc.id, manifest_data)

            # 写主指针
            pointer_data = {
                "type": "folder",
                "tree_oid": tree_oid,
                "file_count": file_count,
                "total_size": total_size,
                "foldername": doc.name,
                "doc_id": doc.id,
                "storage_path": storage_path_posix,
                "organized_at": organized_at,
                "manifest": f"sha256:{manifest_oid_hex}",
            }
            ptr_path = write_pointer_file(library_path, doc.id, pointer_data)
            logger.info(f"[指针] 文件夹指针已生成: {ptr_path}, tree_oid={tree_oid}, files={file_count}")

            # 返回 tree_oid 的 64 位 hex（去掉 'sha256:' 前缀）
            return tree_oid.removeprefix("sha256:")

        else:
            # ── 单文件流程 ──────────────────────────────────────
            oid = compute_file_hash(str(actual_path))
            size = actual_path.stat().st_size

            pointer_data = {
                "type": "file",
                "oid": oid,
                "size": size,
                "filename": doc.name,
                "doc_id": doc.id,
                "storage_path": storage_path_posix,
                "organized_at": organized_at,
            }
            ptr_path = write_pointer_file(library_path, doc.id, pointer_data)
            logger.info(f"[指针] 文件指针已生成: {ptr_path}, oid={oid}")

            # 返回 64 位 hex（去掉 'sha256:' 前缀）
            return oid.removeprefix("sha256:")

    except Exception as e:
        logger.warning(f"[指针] 生成异常: doc_id={doc.id}, 错误: {e}")
        return None
