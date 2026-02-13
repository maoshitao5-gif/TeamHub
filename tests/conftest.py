"""
pytest 配置和共享 fixtures
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.main import app
from backend.app.database import Base, get_db


# 测试数据库配置
SQLALCHEMY_DATABASE_URL = "sqlite:///./test.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False}
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="function")
def db():
    """数据库 fixture"""
    # 创建表
    Base.metadata.create_all(bind=engine)

    # 创建会话
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        # 清理表
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def client(db):
    """测试客户端 fixture"""
    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture
def test_file_data():
    """测试文件数据"""
    return {
        "filename": "test.txt",
        "content": b"Hello, World!",
        "tags": ["test", "demo"]
    }


@pytest.fixture
def test_user():
    """测试用户数据"""
    return {
        "username": "testuser",
        "password": "testpass123"
    }
