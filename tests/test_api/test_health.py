"""
健康检查 API 测试
"""
import pytest


@pytest.mark.unit
def test_read_root(client):
    """测试根路径"""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    assert "version" in data


@pytest.mark.unit
def test_app_info(client):
    """测试应用信息"""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "团队文件管理系统 API"
    assert "endpoints" in data
