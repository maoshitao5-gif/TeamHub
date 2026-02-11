"""
数据库配置模块
负责数据库连接和会话管理
"""
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# SQLite 数据库文件路径
SQLALCHEMY_DATABASE_URL = "sqlite:///./teamhub.db"

# 创建数据库引擎
# connect_args={"check_same_thread": False} 是 SQLite 特有的参数，允许多线程访问
# SQLite 默认使用 UTF-8 编码，但显式指定可以确保兼容性
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={
        "check_same_thread": False,
        # 确保 SQLite 使用 UTF-8 编码
        "timeout": 20
    },
    # 设置连接池编码（SQLite 本身支持 UTF-8）
    pool_pre_ping=True,
    echo=False  # 设置为 True 可以看到 SQL 语句（调试用）
)

# 创建会话工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 创建基础模型类，所有数据模型都将继承自此类
Base = declarative_base()


def get_db():
    """
    获取数据库会话的依赖注入函数
    用于 FastAPI 的路由依赖注入
    确保每个请求结束后自动关闭数据库连接
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """
    初始化数据库，创建所有表
    在应用启动时调用此函数
    """
    Base.metadata.create_all(bind=engine)
