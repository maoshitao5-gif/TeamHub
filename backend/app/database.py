"""
数据库配置模块
负责数据库连接和会话管理
"""
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from backend.app.core.logger import get_logger

logger = get_logger("database")

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
    
    # 初始化默认存储位置
    from backend.app.models import StorageLocation
    from backend.app.config import STORAGE_DIR
    from pathlib import Path
    
    db = SessionLocal()
    try:
        # 检查是否已有存储位置
        existing = db.query(StorageLocation).first()
        if not existing:
            # 创建默认存储位置
            default_path = Path(STORAGE_DIR).resolve()
            default_path.mkdir(parents=True, exist_ok=True)
            
            default_location = StorageLocation(
                name="默认存储",
                path=str(default_path),
                enabled=True,
                is_default=True
            )
            db.add(default_location)
            db.commit()
            logger.info(f"已创建默认存储位置: {default_path}")
        else:
            # 确保至少有一个默认存储位置
            default_location = db.query(StorageLocation).filter(
                StorageLocation.is_default == True
            ).first()
            if not default_location:
                # 如果没有默认位置，将第一个启用的位置设为默认
                first_enabled = db.query(StorageLocation).filter(
                    StorageLocation.enabled == True
                ).first()
                if first_enabled:
                    first_enabled.is_default = True
                    db.commit()
                    logger.info(f"已将 '{first_enabled.name}' 设为默认存储位置")
    except Exception as e:
        logger.error(f"创建默认存储位置失败: {e}", exc_info=True)
        db.rollback()
    finally:
        db.close()