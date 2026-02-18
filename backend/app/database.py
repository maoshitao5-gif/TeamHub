"""
数据库配置模块
负责数据库连接和会话管理
支持 Electron 桌面应用环境
数据库存放在文件库的 .teamhub/ 目录下
"""
import json
from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from pathlib import Path
from backend.app.core.logger import get_logger
from backend.app.config import settings

logger = get_logger("database")

# 从配置中获取数据库 URL
SQLALCHEMY_DATABASE_URL = settings.database_url

# 确保数据库目录存在
db_url = SQLALCHEMY_DATABASE_URL
if db_url.startswith("sqlite:///"):
    db_path = Path(db_url.replace("sqlite:///", ""))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"Ensured database directory exists: {db_path.parent}")

# 创建数据库引擎
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={
        "check_same_thread": False,
        "timeout": 20
    },
    pool_pre_ping=True,
    echo=False
)


# SQLite 连接时启用 WAL 模式和外键约束
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


# 创建会话工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 创建基础模型类
Base = declarative_base()


def get_db():
    """
    获取数据库会话的依赖注入函数
    用于 FastAPI 的路由依赖注入
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """
    初始化数据库，创建所有表
    同时初始化文件库目录结构
    """
    # 确保日志目录存在
    log_dir = Path(settings.log_file).parent
    log_dir.mkdir(parents=True, exist_ok=True)

    # 如果有文件库路径，确保 .teamhub 目录结构存在
    if settings.library_path:
        library = Path(settings.library_path)
        teamhub_dir = library / '.teamhub'
        (teamhub_dir).mkdir(parents=True, exist_ok=True)
        (teamhub_dir / 'index-versions').mkdir(exist_ok=True)
        (teamhub_dir / 'trash').mkdir(exist_ok=True)

        # 创建待整理目录
        pending_dir = library / '待整理'
        pending_dir.mkdir(exist_ok=True)

        logger.info(f"Library directory structure initialized: {library}")

    # 如果是 Electron 环境，确保必要目录存在
    if settings.is_electron:
        base_dir = Path(settings.user_data_dir)
        for subdir in ['database', 'logs', 'config']:
            (base_dir / subdir).mkdir(parents=True, exist_ok=True)

    # 确保模型已注册到 Base.metadata
    import backend.app.models  # noqa: F401

    # 创建所有表
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created/verified")


def load_library_config() -> dict:
    """
    从配置文件加载文件库配置
    返回配置字典，如果文件不存在返回空字典
    """
    config_path = Path(settings.config_file)
    if config_path.exists():
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Failed to load config: {e}")
    return {}


def save_library_config(config: dict):
    """
    保存文件库配置到配置文件
    """
    config_path = Path(settings.config_file)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        logger.info(f"Config saved to {config_path}")
    except IOError as e:
        logger.error(f"Failed to save config: {e}")
