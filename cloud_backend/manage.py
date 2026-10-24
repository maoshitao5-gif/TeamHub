"""Explicit maintenance commands; never infer a production migration on startup."""
import argparse
import json
import os
import uuid
from pathlib import Path
from sqlalchemy import text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['init', 'upgrade', 'check', 'clone-identity'])
    parser.add_argument('--services-stopped', action='store_true')
    args = parser.parse_args()
    if not os.environ.get('DATABASE_URL'):
        parser.error('维护命令必须通过 DATABASE_URL 显式指定目标数据库')
    from app.database import init_db, SessionLocal, engine
    from app.models import ServerMetadata, RefreshToken
    if args.action in ('upgrade', 'clone-identity'):
        if engine.url.get_backend_name() == 'sqlite' and not Path(engine.url.database or '').is_file():
            parser.error('维护目标数据库不存在；请核对路径，不会自动初始化')
    if args.action == 'check':
        from sqlalchemy import create_engine, inspect
        from sqlalchemy.engine import make_url
        from urllib.parse import quote
        url = make_url(os.environ['DATABASE_URL'])
        if url.get_backend_name() == 'sqlite':
            database = Path(url.database or '').resolve()
            if not database.is_file():
                parser.error('目标数据库不存在；检查命令不会创建数据库')
            url = url.set(database='file:' + quote(str(database), safe='/'), query={'mode': 'ro', 'uri': 'true'})
        readonly = create_engine(url)
        with readonly.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            required = {'cloud_documents', 'document_access', 'deliveries', 'delivery_items', 'delivery_recipients', 'server_metadata', 'alembic_version'}
            if not required <= tables:
                parser.error('数据库结构未达到005，请在备份副本中显式升级')
            identity = connection.execute(text('SELECT server_id,schema_version,protocol_major FROM server_metadata WHERE id=1')).mappings().first()
            revision = connection.execute(text('SELECT version_num FROM alembic_version')).scalar()
            if not identity or identity['schema_version'] != '005' or identity['protocol_major'] != 1 or revision != '005':
                parser.error('数据库版本或服务身份不兼容')
            print(json.dumps(dict(identity), ensure_ascii=False))
        readonly.dispose()
        return
    if args.action in ('upgrade','clone-identity') and not args.services_stopped:
        parser.error('维护操作须停止服务并提供 --services-stopped')
    if args.action == 'upgrade':
        from alembic import command
        from alembic.config import Config
        cfg = Config(str(Path(__file__).parent/'alembic.ini'))
        cfg.set_main_option('script_location',str(Path(__file__).parent/'alembic'))
        command.upgrade(cfg,'head')
    init_db()
    if args.action == 'clone-identity':
        with SessionLocal() as db:
            db.execute(text('BEGIN IMMEDIATE'))
            db.get(ServerMetadata,1).server_id = str(uuid.uuid4())
            db.query(RefreshToken).delete()
            db.commit()
    with SessionLocal() as db:
        identity = db.get(ServerMetadata,1)
        print(json.dumps({'server_id':identity.server_id,'schema_version':identity.schema_version,
                          'protocol_major':identity.protocol_major},ensure_ascii=False))
    if args.action == 'clone-identity':
        print('独立克隆服务还必须使用新的 JWT_SECRET_KEY；不要复用原服务的地址或凭据。')

if __name__ == '__main__':
    main()
