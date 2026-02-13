"""
认证 API 测试
"""
import pytest


@pytest.mark.integration
def test_login_success(client, db):
    """测试登录成功"""
    # 注意：需要先创建默认管理员用户
    # 这里假设应用启动时会自动创建
    from backend.app.models import User
    from backend.app.core.security import get_password_hash
    from datetime import datetime

    # 创建测试用户
    test_user = User(
        username="admin",
        hashed_password=get_password_hash("admin123"),
        created_at=datetime.utcnow(),
        is_admin=True
    )
    db.add(test_user)
    db.commit()

    # 尝试登录
    response = client.post(
        "/login",
        json={"username": "admin", "password": "admin123"}
    )

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "token_type" in data
    assert data["token_type"] == "bearer"


@pytest.mark.integration
def test_login_invalid_credentials(client, db):
    """测试登录失败（错误凭据）"""
    response = client.post(
        "/login",
        json={"username": "invalid", "password": "wrong"}
    )

    assert response.status_code == 401


@pytest.mark.integration
def test_get_current_user(client, db):
    """测试获取当前用户信息"""
    from backend.app.models import User
    from backend.app.core.security import get_password_hash, create_access_token
    from datetime import datetime

    # 创建测试用户
    test_user = User(
        username="testuser",
        hashed_password=get_password_hash("testpass"),
        created_at=datetime.utcnow(),
        is_admin=False
    )
    db.add(test_user)
    db.commit()

    # 创建 token
    token = create_access_token(data={"sub": "testuser"})

    # 获取用户信息
    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "testuser"
