"""pytest 配置:共享端测试独立运行,不导入根 backend 包"""
import os
import sys
import tempfile
import secrets

# 必须在任何 app 模块导入前设置:独立临时数据库,绝不触碰 cloud_dev.db 或真实数据
_TEST_DIR = tempfile.mkdtemp(prefix="teamhub-cloud-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DIR}/cloud_test.db"
os.environ["JWT_SECRET_KEY"] = secrets.token_hex(32)

# 确保 cloud_backend 在导入路径最前(包名为 app)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import Base, engine, SessionLocal  # noqa: E402
import app.models  # noqa: E402,F401 注册全部模型

Base.metadata.create_all(bind=engine)
