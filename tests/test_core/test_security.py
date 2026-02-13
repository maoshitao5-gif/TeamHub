"""
安全功能测试
"""
import pytest
from backend.app.core.security import (
    get_password_hash,
    verify_password,
    create_access_token,
    verify_token
)


@pytest.mark.unit
def test_password_hashing():
    """测试密码哈希"""
    password = "test_password_123"
    hashed = get_password_hash(password)

    # 哈希值应该不同于原密码
    assert hashed != password

    # 同一密码的两次哈希应该不同（因为有盐值）
    hashed2 = get_password_hash(password)
    assert hashed != hashed2


@pytest.mark.unit
def test_password_verification():
    """测试密码验证"""
    password = "test_password_123"
    hashed = get_password_hash(password)

    # 正确密码应该验证成功
    assert verify_password(password, hashed) is True

    # 错误密码应该验证失败
    assert verify_password("wrong_password", hashed) is False


@pytest.mark.unit
def test_create_access_token():
    """测试创建访问令牌"""
    data = {"sub": "testuser"}
    token = create_access_token(data)

    # 应该返回字符串类型的 token
    assert isinstance(token, str)
    assert len(token) > 0


@pytest.mark.unit
def test_verify_token():
    """测试验证令牌"""
    data = {"sub": "testuser"}
    token = create_access_token(data)

    # 验证有效 token
    payload = verify_token(token)
    assert payload is not None
    assert payload["sub"] == "testuser"

    # 验证无效 token
    invalid_payload = verify_token("invalid_token")
    assert invalid_payload is None
