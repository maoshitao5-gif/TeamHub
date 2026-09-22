"""
指针文件模块单元测试
覆盖：格式验证、空文件夹、多文件排序确定性、文件不存在、文件夹生成两个文件、覆盖幂等性
"""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.core.pointer import (
    compute_file_hash,
    compute_folder_hashes,
    generate_pointer,
    write_folder_manifest,
    write_pointer_file,
)


# ── 工具：创建 fake doc 对象 ─────────────────────────────────────────────────

def _make_doc(doc_id="test-doc-001", name="test.txt", is_folder=False):
    return SimpleNamespace(id=doc_id, name=name, is_folder=is_folder)


# ── compute_file_hash ────────────────────────────────────────────────────────

def test_compute_file_hash_format(tmp_path):
    """返回值必须以 'sha256:' 开头，后接 64 位 hex"""
    f = tmp_path / "hello.txt"
    f.write_bytes(b"hello world")
    oid = compute_file_hash(str(f))
    assert oid.startswith("sha256:")
    hex_part = oid[len("sha256:"):]
    assert len(hex_part) == 64
    assert all(c in "0123456789abcdef" for c in hex_part)


def test_compute_file_hash_consistency_with_file_utils(tmp_path):
    """compute_file_hash 的 hex 部分与 file_utils.calculate_sha256 结果一致"""
    from backend.app.core.file_utils import calculate_sha256
    content = b"TeamHub pointer test content"
    f = tmp_path / "data.bin"
    f.write_bytes(content)
    oid = compute_file_hash(str(f))
    expected_hex = calculate_sha256(str(f))
    assert oid == f"sha256:{expected_hex}"


def test_compute_file_hash_empty_file(tmp_path):
    """空文件哈希也应正常返回（SHA-256 of empty = e3b0c44...）"""
    f = tmp_path / "empty.bin"
    f.write_bytes(b"")
    oid = compute_file_hash(str(f))
    assert oid == "sha256:" + hashlib.sha256(b"").hexdigest()


# ── compute_folder_hashes ────────────────────────────────────────────────────

def test_compute_folder_hashes_empty_folder(tmp_path):
    """空文件夹：files_list 为空，tree_oid 为空串的 SHA-256"""
    folder = tmp_path / "empty_folder"
    folder.mkdir()
    tree_oid, files_list = compute_folder_hashes(str(folder))
    assert files_list == []
    expected_tree_oid = "sha256:" + hashlib.sha256(b"").hexdigest()
    assert tree_oid == expected_tree_oid


def test_compute_folder_hashes_multiple_files(tmp_path):
    """多文件：files_list 元素数正确，每项含 relative_path / oid / size"""
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "a.txt").write_text("AAA", encoding="utf-8")
    (folder / "b.txt").write_text("BBB", encoding="utf-8")
    tree_oid, files_list = compute_folder_hashes(str(folder))
    assert len(files_list) == 2
    paths = [f["relative_path"] for f in files_list]
    assert "a.txt" in paths and "b.txt" in paths
    for item in files_list:
        assert item["oid"].startswith("sha256:")
        assert isinstance(item["size"], int) and item["size"] >= 0


def test_compute_folder_hashes_sort_determinism(tmp_path):
    """tree_oid 与文件遍历顺序无关，相同内容两次调用结果相同"""
    folder = tmp_path / "same"
    folder.mkdir()
    for name in ["z.txt", "a.txt", "m.txt"]:
        (folder / name).write_text(f"content of {name}", encoding="utf-8")
    oid1, _ = compute_folder_hashes(str(folder))
    oid2, _ = compute_folder_hashes(str(folder))
    assert oid1 == oid2


def test_compute_folder_hashes_skips_hidden(tmp_path):
    """以 . 开头的隐藏文件/目录应被跳过"""
    folder = tmp_path / "root"
    folder.mkdir()
    (folder / "visible.txt").write_text("visible", encoding="utf-8")
    (folder / ".hidden_file").write_text("hidden", encoding="utf-8")
    hidden_dir = folder / ".hidden_dir"
    hidden_dir.mkdir()
    (hidden_dir / "secret.txt").write_text("secret", encoding="utf-8")
    _, files_list = compute_folder_hashes(str(folder))
    paths = [f["relative_path"] for f in files_list]
    assert "visible.txt" in paths
    assert ".hidden_file" not in paths
    assert not any(".hidden_dir" in p for p in paths)


# ── generate_pointer：文件不存在 ─────────────────────────────────────────────

def test_generate_pointer_file_not_exist(tmp_path):
    """storage_path 对应文件不存在时返回 None"""
    doc = _make_doc(is_folder=False)
    result = generate_pointer(str(tmp_path), doc, "nonexistent/file.txt")
    assert result is None


def test_generate_pointer_empty_storage_path(tmp_path):
    """storage_path 为空字符串时返回 None"""
    doc = _make_doc()
    assert generate_pointer(str(tmp_path), doc, "") is None
    assert generate_pointer(str(tmp_path), doc, None) is None


# ── generate_pointer：单文件 ─────────────────────────────────────────────────

def test_generate_pointer_single_file(tmp_path):
    """单文件整理：生成 .ptr，返回 64 位 hex"""
    # 准备文件结构（模拟 library_path）
    (tmp_path / ".teamhub").mkdir()
    target_file = tmp_path / "合同.pdf"
    target_file.write_bytes(b"PDF content mock")

    doc = _make_doc(doc_id="doc-file-001", name="合同.pdf", is_folder=False)
    result = generate_pointer(str(tmp_path), doc, "合同.pdf")

    assert result is not None
    assert len(result) == 64
    assert all(c in "0123456789abcdef" for c in result)

    # 验证指针文件内容
    ptr_path = tmp_path / ".teamhub" / "pointers" / "doc-file-001.ptr"
    assert ptr_path.exists()
    content = ptr_path.read_text(encoding="utf-8")
    assert "version https://teamhub.local/spec/pointer/v1" in content
    assert "type file" in content
    assert f"sha256:{result}" in content
    assert "doc_id doc-file-001" in content


# ── generate_pointer：文件夹 ─────────────────────────────────────────────────

def test_generate_pointer_folder(tmp_path):
    """文件夹整理：同时生成 .ptr 和 .manifest.json"""
    (tmp_path / ".teamhub").mkdir()
    folder = tmp_path / "项目文件夹"
    folder.mkdir()
    (folder / "报告.txt").write_text("内容A", encoding="utf-8")
    (folder / "附件.txt").write_text("内容B", encoding="utf-8")

    doc = _make_doc(doc_id="doc-folder-001", name="项目文件夹", is_folder=True)
    result = generate_pointer(str(tmp_path), doc, "项目文件夹")

    assert result is not None
    assert len(result) == 64

    pointers_dir = tmp_path / ".teamhub" / "pointers"
    ptr_path = pointers_dir / "doc-folder-001.ptr"
    manifest_path = pointers_dir / "doc-folder-001.manifest.json"

    assert ptr_path.exists(), ".ptr 文件未生成"
    assert manifest_path.exists(), ".manifest.json 文件未生成"

    # 验证 manifest 内容
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["doc_id"] == "doc-folder-001"
    assert "tree_oid" in manifest
    assert len(manifest["files"]) == 2

    # 验证 .ptr 内容
    ptr_content = ptr_path.read_text(encoding="utf-8")
    assert "type folder" in ptr_content
    assert "file_count 2" in ptr_content
    assert "manifest sha256:" in ptr_content


# ── 覆盖幂等性 ───────────────────────────────────────────────────────────────

def test_generate_pointer_idempotent(tmp_path):
    """同一 doc_id 调用两次，第二次覆盖第一次，最终结果相同"""
    (tmp_path / ".teamhub").mkdir()
    f = tmp_path / "report.txt"
    f.write_bytes(b"idempotent test")

    doc = _make_doc(doc_id="doc-idem-001", name="report.txt", is_folder=False)

    r1 = generate_pointer(str(tmp_path), doc, "report.txt")
    r2 = generate_pointer(str(tmp_path), doc, "report.txt")

    assert r1 == r2
    ptr_path = tmp_path / ".teamhub" / "pointers" / "doc-idem-001.ptr"
    assert ptr_path.exists()


# ── write_pointer_file / write_folder_manifest 单独测试 ─────────────────────

def test_write_pointer_file_format(tmp_path):
    """指针文件第一行必须是 version 声明"""
    ptr_path = write_pointer_file(str(tmp_path), "test-id", {"type": "file", "oid": "sha256:abc"})
    lines = ptr_path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "version https://teamhub.local/spec/pointer/v1"
    assert "type file" in lines
    assert "oid sha256:abc" in lines


def test_write_folder_manifest_returns_oid(tmp_path):
    """write_folder_manifest 返回的 manifest_oid 与文件内容 SHA-256 一致"""
    data = {"doc_id": "x", "tree_oid": "sha256:aaa", "files": []}
    manifest_path, manifest_oid = write_folder_manifest(str(tmp_path), "x", data)
    content = manifest_path.read_text(encoding="utf-8")
    expected_hex = hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert manifest_oid == expected_hex
