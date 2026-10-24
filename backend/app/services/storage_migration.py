"""登记清单迁移：复制校验 → 原子切换配置 → 清理旧副本；持久日志支持中断恢复。"""
import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from pathlib import Path
from fastapi import HTTPException
from backend.app.config import settings
from backend.app import database
from backend.app.models import Document
from backend.app.core.storage_paths import document_path, pending_root
from backend.app.core.cloud_session import refresh_lock, session_lock

JOURNAL = 'storage-migration.json'
active = False


def bootstrap_path():
    return Path(settings.user_data_dir) / 'config/config.json' if settings.is_electron else Path('config.json').resolve()


def _load(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def _fingerprint(path):
    """完整字节清单，包含隐藏文件/空目录；不跟随符号链接或特殊文件。"""
    result = {}
    def add(p, name):
        if p.is_symlink():
            raise HTTPException(409, f'迁移不支持符号链接：{p}')
        if p.is_dir():
            result[name] = None
            for child in sorted(p.iterdir()):
                if child.name == JOURNAL or child.name in {'db.sqlite-wal', 'db.sqlite-shm'}:
                    # 只对受管元数据根使用这些排除项，普通资料同名文件必须保留。
                    if path.name == '.teamhub' and p == path:
                        continue
                add(child, str(Path(name) / child.name) if name else child.name)
        elif p.is_file():
            h = hashlib.sha256()
            with p.open('rb') as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b''):
                    h.update(chunk)
            result[name] = h.hexdigest()
        else:
            raise HTTPException(409, f'资料缺失或存在特殊文件：{p}')
    add(path, '')
    return result


def _remove_recorded(path, inventory):
    """仅删除清单中仍一致的条目；后来新增的内容不会被递归删除。"""
    if not path.exists():
        return
    current = _fingerprint(path)
    if any(name not in inventory or inventory[name] != value for name, value in current.items()):
        raise HTTPException(409, f'内容发生变化，保留副本等待人工核对：{path}')
    for name, digest in sorted(current.items(), key=lambda item: len(Path(item[0]).parts), reverse=True):
        p = path / name if name else path
        if digest is not None:
            if p.is_symlink() or _fingerprint(p) != {'': digest}:
                raise HTTPException(409, f'文件变化，保留：{p}')
            p.unlink()
        elif p.exists():
            try:
                p.rmdir()
            except OSError:
                # 元数据迁移日志最后处理，其他新增内容保留。
                pass


def _save(journal):
    # 来源日志一直保留到清理完成；新文件库内也保存一份以支持重启。
    roots = {Path(journal['old_library'])}
    if journal.get('metadata_copied'):
        roots.add(Path(journal['new_library']))
    for root in roots:
        database.write_json_config(root / '.teamhub' / JOURNAL, journal)


def status():
    if not settings.library_path:
        return None
    p = Path(settings.library_path) / '.teamhub' / JOURNAL
    return _load(p) or None


def _plan(area, destination):
    if area not in {'library', 'pending'} or not settings.library_path:
        raise HTTPException(400, '请选择已初始化的存储区域')
    raw = Path(destination).expanduser()
    if not raw.is_absolute():
        raise HTTPException(400, '请输入绝对路径')
    # 路径的每级不允许以链接伪装为另一个区域。
    if any(p.is_symlink() for p in [raw, *raw.parents]):
        raise HTTPException(400, '迁移目标不能包含符号链接')
    target = raw.resolve()
    old_lib = Path(settings.library_path).resolve()
    old_pending = pending_root()
    source_root = old_lib if area == 'library' else old_pending
    new_lib = target if area == 'library' else old_lib
    new_pending = target if area == 'pending' else old_pending
    if source_root == target:
        raise HTTPException(400, '新旧路径相同')
    if new_lib == new_pending:
        raise HTTPException(400, '两个区域不能使用完全相同的路径')
    if target.is_relative_to(old_lib / '.teamhub') or new_pending.is_relative_to(new_lib / '.teamhub'):
        raise HTTPException(400, '区域不能位于系统元数据目录内')
    previous = status()
    if previous and previous['phase'] not in {'complete', 'cancelled'}:
        raise HTTPException(409, '存在未完成迁移，请先处理迁移恢复')
    with database.SessionLocal() as db:
        from backend.app.models import RemoteOperation
        unfinished = db.query(RemoteOperation).filter(
            RemoteOperation.kind.in_(['update_local', 'delivery_intake']),
            RemoteOperation.status != 'succeeded', RemoteOperation.recovery_json.isnot(None),
        ).first()
        if unfinished:
            raise HTTPException(409, '存在未完成远端接收，请先处理恢复状态再迁移存储区域')
        docs = db.query(Document).filter(Document.storage_path.isnot(None), Document.storage_mode != 'index').all()
        live = [d for d in docs if d.status != 'trashed']
        entries = []
        future = []
        for doc in live:
            source = document_path(doc)
            own_root = old_pending if doc.storage_area == 'pending' else old_lib
            new_root = new_pending if doc.storage_area == 'pending' else new_lib
            dest = new_root / source.relative_to(own_root)
            if doc.is_folder:
                # 区域根目录不可嵌进一个整体文件夹对象（迁移前后均检查）。
                if any(root.is_relative_to(source) or root.is_relative_to(dest)
                       for root in {old_lib, old_pending, new_lib, new_pending}):
                    raise HTTPException(409, f'存储区域不能嵌入整套资料内部：{doc.name}')
            future.append((doc.id, dest, doc.storage_area, doc.is_folder))
            if doc.storage_area != area:
                continue
            if not source.exists():
                raise HTTPException(409, f'已登记资料缺失，迁移已停止：{doc.name}')
            if dest.exists() or dest.is_symlink():
                raise HTTPException(409, f'目标已存在同名内容，不会覆盖：{dest}')
            if dest.is_relative_to(source) or source.is_relative_to(dest):
                raise HTTPException(409, f'资料迁移前后路径互相嵌套：{doc.name}')
            entries.append({'id': doc.id, 'name': doc.name, 'source': str(source), 'target': str(dest),
                            'folder': doc.is_folder, 'size': doc.total_size, 'copied': False})
        for i, (_, path, owner, folder) in enumerate(future):
            for _, other, _, other_folder in future[i + 1:]:
                if path == other or (folder and other.is_relative_to(path)) or (other_folder and path.is_relative_to(other)):
                    raise HTTPException(409, '迁移后资料对象的路径重叠，无法明确归属')
        if area == 'library' and (new_lib / '.teamhub').exists():
            raise HTTPException(409, '目标已存在 TeamHub 元数据，不能合并或覆盖另一个库')
    return {'id': uuid.uuid4().hex, 'area': area, 'old_library': str(old_lib), 'new_library': str(new_lib),
            'old_pending': str(old_pending), 'new_pending': str(new_pending), 'entries': entries,
            'phase': 'planned', 'metadata_copied': False}


def preview(area, destination):
    with database.library_change():
        plan = _plan(area, destination)
        return {'area': area, 'source': plan['old_library'] if area == 'library' else plan['old_pending'],
                'target': plan['new_library'] if area == 'library' else plan['new_pending'],
                'count': len(plan['entries']), 'names': [e['name'] for e in plan['entries']],
                'metadata': area == 'library'}


def _copy(entry, journal):
    source, target = Path(entry['source']), Path(entry['target'])
    inventory = _fingerprint(source)
    entry['inventory'] = inventory
    _save(journal)
    target.parent.mkdir(parents=True, exist_ok=True)
    # 先保留目标对象，避免覆盖用户文件；发生中断时依靠登记的目标路径核对。
    if entry['folder']:
        target.mkdir(exist_ok=False)
        for name, digest in inventory.items():
            if not name:
                continue
            dest = target / name
            if digest is None:
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                with (source / name).open('rb') as src, dest.open('xb') as dst:
                    shutil.copyfileobj(src, dst)
                    dst.flush()
                    os.fsync(dst.fileno())
    else:
        with source.open('rb') as src, target.open('xb') as dst:
            shutil.copyfileobj(src, dst)
            dst.flush()
            os.fsync(dst.fileno())
    if _fingerprint(source) != inventory or _fingerprint(target) != inventory:
        raise HTTPException(409, f'迁移期间内容变化或校验失败：{source}')
    entry['copied'] = True
    _save(journal)


def _is_switched(journal):
    # 配置中的提交标记是切换的唯一事实来源；日志阶段可因断电落后。
    config = database.load_library_config()
    return config.get('storage_migration_id') == journal['id']


def _finish(journal):
    journal['phase'] = 'cleanup'
    _save(journal)
    warnings = []
    for entry in journal['entries']:
        try:
            if _fingerprint(Path(entry['target'])) != entry['inventory']:
                raise HTTPException(409, f"新位置内容发生变化，保留原件：{entry['target']}")
            _remove_recorded(Path(entry['source']), entry['inventory'])
        except Exception as exc:
            warnings.append(str(getattr(exc, 'detail', exc)))
    if journal.get('metadata_entry'):
        entry = journal['metadata_entry']
        try:
            wal = Path(entry['source']) / 'db.sqlite-wal'
            if wal.exists() and wal.stat().st_size:
                raise HTTPException(409, '旧库仍有其他进程写入，已保留旧数据库，请关闭其他实例后核对')
            _remove_recorded(Path(entry['source']), entry['inventory'])
        except Exception as exc:
            warnings.append(str(getattr(exc, 'detail', exc)))
    # 旧位置只保留迁移日志，不保留完整资料与版本副本。
    journal['phase'] = 'complete' if not warnings else 'cleanup'
    journal['warnings'] = warnings
    _save(journal)
    return {'message': '迁移完成' if not warnings else '已切换新位置，部分旧副本保留，请处理恢复',
            'phase': journal['phase'], 'warnings': warnings, 'count': len(journal['entries'])}


def migrate(area, destination):
    global active
    with refresh_lock, database.library_change():
        active = True
        journal = None
        try:
            journal = _plan(area, destination)
            destination_root = Path(journal['new_library'] if area == 'library' else journal['new_pending'])
            destination_root.mkdir(parents=True, exist_ok=True)
            journal['phase'] = 'copying'
            _save(journal)
            for entry in journal['entries']:
                _copy(entry, journal)
            # 元数据中的会话配置与切换是同一临界区，普通设备/设置写入不得夹入。
            with session_lock:
                if area == 'library':
                    with database.engine.connect() as conn:
                        checkpoint = conn.exec_driver_sql('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
                        if checkpoint[0]:
                            raise HTTPException(409, '数据库仍被其他进程使用，暂不能迁移')
                    database.engine.dispose()
                    wal = Path(journal['old_library']) / '.teamhub/db.sqlite-wal'
                    if wal.exists() and wal.stat().st_size:
                        raise HTTPException(409, '旧库仍在被其他进程写入，请关闭其他实例后重试')
                    meta = {'source': str(Path(journal['old_library']) / '.teamhub'),
                            'target': str(Path(journal['new_library']) / '.teamhub'), 'folder': True}
                    journal['metadata_entry'] = meta
                    _copy(meta, journal)
                    journal['metadata_copied'] = True
                    _save(journal)
                    # 兼容历史绝对版本路径，UUID、版本号及标签关系均保持不变。
                    db_path = Path(meta['target']) / 'db.sqlite'
                    from contextlib import closing
                    with closing(sqlite3.connect(db_path)) as conn:
                        for vid, old in conn.execute('SELECT id, file_path FROM versions').fetchall():
                            try:
                                rel = Path(old).relative_to(Path(meta['source']))
                            except (ValueError, TypeError):
                                raise HTTPException(409, '存在系统对象目录之外的历史版本，需先修复')
                            conn.execute('UPDATE versions SET file_path=? WHERE id=?', (str(Path(meta['target']) / rel), vid))
                        from backend.app.core.backup import rewrite_remote_operation_paths, rewrite_publication_paths
                        rewrite_remote_operation_paths(conn, journal['old_library'], journal['new_library'],
                                                       Path(meta['target']) / 'objects')
                        conn.commit()
                        rewrite_publication_paths(Path(meta['target']) / 'publications',
                                                  journal['old_library'], journal['new_library'])
                        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                            raise HTTPException(409, '复制后的数据库校验失败，未切换')
                new_config_path = Path(journal['new_library']) / '.teamhub/config.json'
                cfg = _load(new_config_path)
                cfg.update(library_path=journal['new_library'], pending_path=journal['new_pending'], storage_migration_id=journal['id'])
                journal['phase'] = 'switching'
                _save(journal)
                if area == 'library':
                    database.write_json_config(new_config_path, cfg)
                    boot = _load(bootstrap_path())
                    boot['library_path'] = journal['new_library']
                    database.write_json_config(bootstrap_path(), boot)
                    settings.library_path = journal['new_library']
                    settings.pending_path = journal['new_pending']
                    database.rebind_engine()
                    database.init_db()
                else:
                    database.write_json_config(new_config_path, cfg)
                    settings.pending_path = journal['new_pending']
                journal['phase'] = 'committed'
                _save(journal)
            return _finish(journal)
        except Exception as exc:
            # 不覆盖或清除副本：失败后原库仍可恢复，日志供继续/取消。
            if journal:
                journal['error'] = str(getattr(exc, 'detail', exc))
                try:
                    _save(journal)
                except OSError:
                    pass
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(500, '迁移中断，原件和日志已保留，请在设置中处理恢复') from exc
        finally:
            active = False


def recover():
    """提交后继续清理；提交前保留全部副本并取消，绝不猜测删除未完成文件。"""
    global active
    with refresh_lock, database.library_change(), session_lock:
        active = True
        try:
            journal = status()
            if not journal or journal['phase'] in {'complete', 'cancelled'}:
                return {'message': '没有待恢复的迁移'}
            if _is_switched(journal):
                settings.library_path = journal['new_library']
                settings.pending_path = journal['new_pending']
                if journal['area'] == 'library':
                    if str(database.engine.url) != settings.database_url:
                        database.rebind_engine()
                    database.init_db()
                return _finish(journal)
            journal['phase'] = 'cancelled'
            _save(journal)
            return {'message': '未完成迁移已取消，仍使用原位置；目标位置的已复制内容保留，重试请选空目录',
                    'phase': 'cancelled'}
        finally:
            active = False


def follow_library_redirect(path):
    """旧启动入口仍指向迁移来源时，沿已提交日志找到同一资料库。"""
    current = Path(path).resolve()
    followed = False
    seen = set()
    while current not in seen:
        seen.add(current)
        journal = _load(current / '.teamhub' / JOURNAL)
        if (journal.get('area') != 'library' or journal.get('phase') not in {'committed', 'cleanup', 'complete'}
                or journal.get('old_library') != str(current) or journal.get('new_library') == str(current)):
            return str(current) if followed else str(path)
        destination = Path(journal['new_library'])
        if not (destination / '.teamhub').is_dir():
            raise RuntimeError(f'迁移后的文件库不可访问：{destination}；不创建空库覆盖原登记')
        current = destination.resolve()
        followed = True
    raise RuntimeError('文件库迁移日志包含循环，请检查路径')
