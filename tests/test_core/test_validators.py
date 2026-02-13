"""
验证器测试
"""
import pytest
from io import BytesIO
from fastapi import UploadFile, HTTPException
from backend.app.core.validators import FileValidator, TagValidator


@pytest.mark.unit
def test_validate_filename_valid():
    """测试有效文件名"""
    FileValidator.validate_filename("test.txt")
    FileValidator.validate_filename("文件.pdf")
    FileValidator.validate_filename("my-file_123.doc")


@pytest.mark.unit
def test_validate_filename_invalid():
    """测试无效文件名"""
    with pytest.raises(HTTPException) as exc:
        FileValidator.validate_filename("")
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        FileValidator.validate_filename("test<file>.txt")
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        FileValidator.validate_filename("a" * 256)  # 超过长度
    assert exc.value.status_code == 400


@pytest.mark.unit
def test_validate_tag_name_valid():
    """测试有效标签名"""
    TagValidator.validate_tag_name("测试")
    TagValidator.validate_tag_name("test-tag")
    TagValidator.validate_tag_name("标签123")


@pytest.mark.unit
def test_validate_tag_name_invalid():
    """测试无效标签名"""
    with pytest.raises(HTTPException) as exc:
        TagValidator.validate_tag_name("")
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        TagValidator.validate_tag_name("tag/with/slash")
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        TagValidator.validate_tag_name("a" * 51)  # 超过长度
    assert exc.value.status_code == 400


@pytest.mark.unit
def test_validate_tags():
    """测试标签列表验证"""
    # 有效标签列表
    TagValidator.validate_tags(["tag1", "tag2"])

    # 空列表
    with pytest.raises(HTTPException) as exc:
        TagValidator.validate_tags([])
    assert exc.value.status_code == 400

    # 太多标签
    with pytest.raises(HTTPException) as exc:
        TagValidator.validate_tags([f"tag{i}" for i in range(21)])
    assert exc.value.status_code == 400
