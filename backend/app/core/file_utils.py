"""
文件处理工具模块
包含文件哈希计算、文件夹处理等功能
"""
import os
import hashlib
import zipfile
import tempfile
from pathlib import Path

from backend.app.core.encoding import safe_print


def calculate_sha256(file_path: str) -> str:
    """
    计算文件的 SHA-256 哈希值
    
    Args:
        file_path: 文件路径
        
    Returns:
        文件的 SHA-256 哈希值（十六进制字符串）
    """
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        # 分块读取文件，避免大文件占用过多内存
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def calculate_folder_content_hash(zip_path: str) -> str:
    """
    计算文件夹压缩包的内容哈希值（基于文件夹内所有文件的内容）
    忽略文件夹名称和ZIP结构，只关注文件内容
    
    Args:
        zip_path: ZIP 文件路径
        
    Returns:
        文件夹内容的组合哈希值（十六进制字符串）
    """
    # 创建临时目录用于解压
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir_path = Path(temp_dir)
        
        safe_print(f"[内容查重] 解压 ZIP 文件到临时目录: {temp_dir_path}")
        
        # 解压 ZIP 文件
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir_path)
        
        # 收集所有文件（排除文件夹）
        file_hashes = []
        
        # 递归遍历解压后的目录
        for root, dirs, files in os.walk(temp_dir_path):
            for file_name in files:
                file_path = Path(root) / file_name
                # 计算每个文件内容的哈希值
                file_hash = calculate_sha256(str(file_path))
                file_hashes.append(file_hash)
                safe_print(f"[内容查重] 文件: {file_path.relative_to(temp_dir_path)} -> 哈希: {file_hash[:16]}...")
        
        if not file_hashes:
            raise ValueError("ZIP 文件中没有文件")
        
        # 对文件哈希值进行排序，确保顺序一致（忽略文件路径）
        file_hashes.sort()
        
        safe_print(f"[内容查重] 共找到 {len(file_hashes)} 个文件")
        safe_print(f"[内容查重] 文件哈希值列表（已排序）:")
        for i, fh in enumerate(file_hashes[:10]):  # 只显示前10个
            safe_print(f"  [{i+1}] {fh[:16]}...")
        if len(file_hashes) > 10:
            safe_print(f"  ... 还有 {len(file_hashes) - 10} 个文件")
        
        # 组合所有文件哈希值，计算最终的组合哈希值
        combined_hash = hashlib.sha256()
        for file_hash in file_hashes:
            combined_hash.update(file_hash.encode('utf-8'))
        
        content_hash = combined_hash.hexdigest()
        safe_print(f"[内容查重] 文件夹内容组合哈希值: {content_hash}")
        safe_print(f"[内容查重] 组合哈希值前16位: {content_hash[:16]}...")
        
        return content_hash
