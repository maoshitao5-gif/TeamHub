"""
云服务数据库配置
本轮交付支持 SQLite；其他数据库须单独验证迁移与并发语义后再启用。
"""
from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from app.config import settings

# 创建引擎
engine_kwargs = {}
if settings.database_url.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(settings.database_url, **engine_kwargs)


# SQLite 启用 WAL 模式和外键约束
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if settings.database_url.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI 依赖注入：获取数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """创建所有数据库表"""
    import app.models  # noqa：确保模型注册到 metadata
    from sqlalchemy import inspect, text
    from sqlalchemy.exc import IntegrityError
    from app.models import ServerMetadata
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if tables and 'cloud_documents' not in tables:
        raise RuntimeError('云端数据库结构不完整；请在副本中检查并显式迁移后启动')
    if 'cloud_documents' in tables:
        columns = {column['name'] for column in inspector.get_columns('cloud_documents')}
        required_tables = {'document_access', 'deliveries', 'delivery_items', 'delivery_recipients', 'server_metadata'}
        if not {'visibility', 'access_revision'} <= columns or not required_tables <= tables:
            raise RuntimeError('云端数据库需要显式迁移；请备份并执行 alembic upgrade head 后启动')
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        metadata = db.get(ServerMetadata, 1)
        if metadata is None:
            db.add(ServerMetadata(id=1))
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
            metadata = db.get(ServerMetadata, 1)
        if metadata.schema_version != '005' or metadata.protocol_major != 1:
            raise RuntimeError('云端数据库 schema/protocol 版本不兼容，拒绝启动')
    # The historical 001 revision is a placeholder. Fresh schema creation must record
    # its actual revision, otherwise the next explicit upgrade would replay old ALTERs.
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) PRIMARY KEY)'))
        revision = connection.execute(text('SELECT version_num FROM alembic_version')).scalar()
        if revision is None:
            connection.execute(text("INSERT INTO alembic_version (version_num) VALUES ('005')"))
        elif revision != '005':
            raise RuntimeError('云端 Alembic revision 与当前 schema 不匹配，拒绝启动')
