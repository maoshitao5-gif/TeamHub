"""
文件名相似度分析模块
用于识别可能的版本文件和建议标签
"""
import re
import os
from typing import List, Dict, Any
from collections import defaultdict


# 版本后缀正则模式
VERSION_PATTERNS = [
    r'[_\-\s]?v\d+$',           # _v1, -v2, v3
    r'[_\-\s]?V\d+$',           # _V1, -V2
    r'\(\d+\)$',                 # (1), (2)
    r'[_\-\s]?final$',          # _final
    r'[_\-\s]?最终版$',         # _最终版
    r'[_\-\s]?最终$',           # _最终
    r'[_\-\s]?最新$',           # _最新
    r'[_\-\s]?修改版$',         # _修改版
    r'[_\-\s]?修改$',           # _修改
    r'[_\-\s]?修订$',           # _修订
    r'[_\-\s]?revised$',        # _revised
    r'[_\-\s]?copy$',           # _copy
    r'[_\-\s]?副本$',           # _副本
    r'[_\-\s]?备份$',           # _备份
    r'[_\-\s]?new$',            # _new
    r'[_\-\s]?old$',            # _old
    r'[_\-\s]?旧$',             # _旧
    r'[_\-\s]?新$',             # _新
    r'[_\-\s]?\d{8}$',          # _20240101（日期后缀）
    r'[_\-\s]?\d{4}\.\d{2}\.\d{2}$',  # _2024.01.01
]

# 编译正则
COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in VERSION_PATTERNS]

# 文件类型 → 标签建议映射
EXTENSION_TAG_MAP = {
    'xlsx': '表格', 'xls': '表格', 'csv': '表格',
    'docx': '文档', 'doc': '文档',
    'pdf': 'PDF',
    'pptx': '演示文稿', 'ppt': '演示文稿',
    'jpg': '图片', 'jpeg': '图片', 'png': '图片', 'gif': '图片',
    'bmp': '图片', 'svg': '图片', 'webp': '图片',
    'mp4': '视频', 'avi': '视频', 'mkv': '视频', 'mov': '视频',
    'mp3': '音频', 'wav': '音频', 'flac': '音频',
    'zip': '压缩包', 'rar': '压缩包', '7z': '压缩包',
}


def strip_version_suffix(filename: str) -> str:
    """
    去除文件名中的版本后缀，返回基础名称
    例如: "合同_v2.pdf" -> "合同"
    """
    # 先去掉扩展名
    name = filename
    dot_index = name.rfind('.')
    ext = ''
    if dot_index > 0:
        ext = name[dot_index:]
        name = name[:dot_index]

    # 依次尝试去除版本后缀
    for pattern in COMPILED_PATTERNS:
        name = pattern.sub('', name)

    # 去除尾部的空格和连字符
    name = name.rstrip(' _-')

    return name if name else filename


def get_extension(filename: str) -> str:
    """获取小写扩展名"""
    dot = filename.rfind('.')
    if dot < 0:
        return ''
    return filename[dot + 1:].lower()


def group_by_similarity(documents: list) -> List[Dict[str, Any]]:
    """
    将文档按文件名相似度分组

    Args:
        documents: 文档列表，每个文档需有 id, name 属性

    Returns:
        相似文件分组列表，每组包含 base_name, documents, suggested_name
    """
    groups = defaultdict(list)

    for doc in documents:
        base_name = strip_version_suffix(doc.name)
        groups[base_name].append(doc)

    # 只返回有多个文档的分组
    result = []
    for base_name, docs in groups.items():
        if len(docs) >= 2:
            result.append({
                'base_name': base_name,
                'documents': docs,
                'suggested_name': base_name,
            })

    # 按文档数量降序排列
    result.sort(key=lambda g: len(g['documents']), reverse=True)
    return result


def suggest_tags(documents: list) -> List[Dict[str, Any]]:
    """
    根据文件类型建议标签

    Args:
        documents: 文档列表

    Returns:
        标签建议列表
    """
    # 按扩展名建议的标签分组
    tag_docs = defaultdict(list)

    for doc in documents:
        if doc.is_folder:
            tag_docs['整体收纳'].append(doc.id)
        else:
            ext = get_extension(doc.name)
            tag = EXTENSION_TAG_MAP.get(ext)
            if tag:
                tag_docs[tag].append(doc.id)

    # 只有 3+ 同类型文件才建议
    result = []
    for tag, doc_ids in tag_docs.items():
        if len(doc_ids) >= 3:
            result.append({
                'tag': tag,
                'document_ids': doc_ids,
                'reason': f'{len(doc_ids)} 个同类型文件',
            })

    result.sort(key=lambda s: len(s['document_ids']), reverse=True)
    return result
