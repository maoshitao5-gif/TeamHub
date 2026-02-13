"""
数据验证模块
提供统一的数据验证功能
"""
from pathlib import Path
from typing import List, Optional
from fastapi import UploadFile, HTTPException
from backend.app.config import settings
from backend.app.core.logger import get_logger

logger = get_logger("validators")


class FileValidator:
    """文件验证器"""

    @staticmethod
    def validate_file_size(file: UploadFile, max_size: Optional[int] = None) -> None:
        """
        验证文件大小

        Args:
            file: 上传的文件对象
            max_size: 最大文件大小（字节），如果为 None 则使用配置中的值

        Raises:
            HTTPException: 如果文件超过大小限制
        """
        max_size = max_size or settings.max_file_size

        # 尝试获取文件大小
        try:
            file.file.seek(0, 2)  # 移到文件末尾
            file_size = file.file.tell()
            file.file.seek(0)  # 回到文件开头

            if file_size > max_size:
                size_mb = max_size / (1024 * 1024)
                logger.warning(
                    f"文件大小超限: {file.filename}, "
                    f"大小: {file_size / (1024 * 1024):.2f}MB, "
                    f"限制: {size_mb:.2f}MB"
                )
                raise HTTPException(
                    status_code=400,
                    detail=f"文件大小超过限制（最大 {size_mb:.0f}MB）"
                )

            if file_size == 0:
                logger.warning(f"尝试上传空文件: {file.filename}")
                raise HTTPException(
                    status_code=400,
                    detail="不能上传空文件（0字节）"
                )

            logger.debug(f"文件大小验证通过: {file.filename}, 大小: {file_size / (1024 * 1024):.2f}MB")

        except (OSError, IOError) as e:
            logger.error(f"无法读取文件大小: {file.filename}, 错误: {e}")
            raise HTTPException(
                status_code=400,
                detail="无法读取文件信息"
            )

    @staticmethod
    def validate_file_extension(file: UploadFile, allowed_extensions: Optional[List[str]] = None) -> None:
        """
        验证文件扩展名

        Args:
            file: 上传的文件对象
            allowed_extensions: 允许的文件扩展名列表（不包含点号），如果为 None 或空列表则允许所有类型

        Raises:
            HTTPException: 如果文件类型不被允许
        """
        allowed_extensions = allowed_extensions or settings.allowed_file_extensions

        # 如果没有限制，直接通过
        if not allowed_extensions:
            return

        # 获取文件扩展名（不包含点号，转小写）
        file_extension = Path(file.filename).suffix.lstrip('.').lower()

        # 规范化允许的扩展名列表
        allowed_extensions = [ext.lstrip('.').lower() for ext in allowed_extensions]

        if file_extension not in allowed_extensions:
            logger.warning(
                f"文件类型不允许: {file.filename}, "
                f"扩展名: {file_extension}, "
                f"允许: {', '.join(allowed_extensions)}"
            )
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件类型，仅允许: {', '.join(allowed_extensions)}"
            )

        logger.debug(f"文件类型验证通过: {file.filename}, 扩展名: {file_extension}")

    @staticmethod
    def validate_filename(filename: str) -> None:
        """
        验证文件名

        Args:
            filename: 文件名

        Raises:
            HTTPException: 如果文件名无效
        """
        if not filename or not filename.strip():
            raise HTTPException(
                status_code=400,
                detail="文件名不能为空"
            )

        # 检查文件名长度
        if len(filename) > 255:
            raise HTTPException(
                status_code=400,
                detail="文件名过长（最大255字符）"
            )

        # 检查非法字符（Windows 和 Unix 共同的非法字符）
        illegal_chars = ['<', '>', ':', '"', '|', '?', '*']
        for char in illegal_chars:
            if char in filename:
                logger.warning(f"文件名包含非法字符: {filename}")
                raise HTTPException(
                    status_code=400,
                    detail=f"文件名包含非法字符: {char}"
                )

    @classmethod
    def validate_upload_file(cls, file: UploadFile) -> None:
        """
        综合验证上传文件

        Args:
            file: 上传的文件对象

        Raises:
            HTTPException: 如果验证失败
        """
        cls.validate_filename(file.filename)
        cls.validate_file_size(file)
        cls.validate_file_extension(file)


class TagValidator:
    """标签验证器"""

    @staticmethod
    def validate_tag_name(tag_name: str) -> None:
        """
        验证标签名称

        Args:
            tag_name: 标签名称

        Raises:
            HTTPException: 如果标签名称无效
        """
        if not tag_name or not tag_name.strip():
            raise HTTPException(
                status_code=400,
                detail="标签名称不能为空"
            )

        if len(tag_name) > 50:
            raise HTTPException(
                status_code=400,
                detail="标签名称过长（最大50字符）"
            )

        # 检查非法字符
        illegal_chars = ['<', '>', '/', '\\', '|', '?', '*', '"', ':']
        for char in illegal_chars:
            if char in tag_name:
                raise HTTPException(
                    status_code=400,
                    detail=f"标签名称包含非法字符: {char}"
                )

    @classmethod
    def validate_tags(cls, tags: List[str]) -> None:
        """
        验证标签列表

        Args:
            tags: 标签列表

        Raises:
            HTTPException: 如果验证失败
        """
        if not tags:
            raise HTTPException(
                status_code=400,
                detail="至少需要一个标签"
            )

        if len(tags) > 20:
            raise HTTPException(
                status_code=400,
                detail="标签数量过多（最大20个）"
            )

        for tag in tags:
            cls.validate_tag_name(tag)
