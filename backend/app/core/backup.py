"""M6 停写维护备份：逐文件摘要、受管内容、空目标恢复与失败清理。

调用前停止对应服务及外部编辑；SQLite 写锁保护数据库/对象复制窗口，
不宣称能阻止外部编辑器修改工作文件。旧的无内容摘要备份拒绝自动恢复。
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

REDACT_FIELDS = {'cloud_access_token', 'cloud_refresh_token'}
CONFIG_FIELDS = {'cloud_server_id', 'cloud_api_url', 'active_workspace_id', 'active_workspace_name',
                 'auto_scan_on_startup', 'default_storage_mode', 'on_conflict',
                 'trash_auto_clean_days', 'theme', 'language'}
FORMAT_VERSION = 2


def _sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _backup_sqlite(src, dst):
    # mode=ro 防止错误源路径被 sqlite 悄悄创建成空数据库。
    with closing(sqlite3.connect(Path(src).resolve().as_uri() + '?mode=ro', uri=True)) as source:
        with closing(sqlite3.connect(str(dst))) as target:
            source.backup(target)
            # backup() includes committed WAL rows. Normalize only the private
            # destination so the resulting snapshot is one standalone DB file.
            if target.execute('PRAGMA journal_mode=DELETE').fetchone() != ('delete',):
                raise ValueError('无法生成独立数据库快照')


@contextmanager
def _snapshot_connection(db, *, writable=False):
    """Open only generated/copied standalone snapshots, never the active source."""
    path = Path(db).resolve()
    if any(Path(str(path) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
        raise ValueError('数据库快照含事务旁文件，不能按独立快照读取')
    query = '?mode=rw' if writable else '?mode=ro&immutable=1'
    # Immutable reads cannot create WAL/SHM or mutate the frozen backup. It is
    # safe here only after sidecar rejection and snapshot generation/verification.
    with closing(sqlite3.connect(path.as_uri() + query, uri=True)) as conn:
        with conn:
            yield conn


def _redact_config(config):
    return {k: v for k, v in config.items() if k in CONFIG_FIELDS}


def _safe_path(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError(f'备份路径不能包含符号链接: {part}')
    return path.resolve()


def _disjoint(a, b):
    if a == b or a in b.parents or b in a.parents:
        raise ValueError('源和目标路径不能相同或互相包含')


def _empty_target(path):
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise FileExistsError(f'目标必须不存在或为空目录: {path}')


@contextmanager
def _staged_directory(target):
    target = _safe_path(target)
    _empty_target(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f'.{target.name}-partial-', dir=target.parent))
    try:
        yield stage
        _empty_target(target)
        # POSIX rename 可替换空目录，拒绝非空目录；不对已有数据使用 copytree 合并。
        if os.name == 'nt' and target.exists():
            target.rmdir()
        stage.rename(target)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


@contextmanager
def _frozen_database(db):
    db = _safe_path(db)
    if not db.is_file():
        raise FileNotFoundError(f'数据库不存在: {db}')
    connection = sqlite3.connect(db.as_uri() + '?mode=rw', uri=True, timeout=5)
    try:
        connection.execute('BEGIN IMMEDIATE')
        yield
    finally:
        connection.rollback()
        connection.close()


def _inventory(root):
    entries = {}
    if not root.exists():
        return entries
    for path in sorted(root.rglob('*')):
        mode = path.lstat().st_mode
        key = path.relative_to(root).as_posix()
        if stat.S_ISDIR(mode):
            entries[key] = {'type': 'directory'}
        elif stat.S_ISREG(mode):
            entries[key] = {'type': 'file', 'size': path.stat().st_size, 'sha256': _sha256_file(path)}
        else:
            raise ValueError(f'备份拒绝链接或特殊文件: {path}')
    return entries


def _copy_tree(source, target):
    if not source.exists():
        return
    if source.is_symlink():
        raise ValueError(f'备份拒绝链接: {source}')
    before = _inventory(source)
    shutil.copytree(source, target)
    if _inventory(source) != before or _inventory(target) != before:
        raise RuntimeError('备份期间源文件发生变化，请停写后重试')


def _check_database(db):
    with _snapshot_connection(db) as conn:
        if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('数据库完整性检查失败')
        if conn.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('数据库外键检查失败')


def _verify_versions(db, objects, kind, source_library=None, target_library=None):
    with _snapshot_connection(db, writable=target_library is not None) as conn:
        if kind == 'personal':
            rows = conn.execute('SELECT id, file_path, sha256_hash, manifest_hash FROM versions').fetchall()
            base = Path(source_library) / '.teamhub' / 'objects'
            for vid, old, content, archive in rows:
                try:
                    relative = Path(old).relative_to(base)
                except ValueError as exc:
                    raise ValueError(f'版本 {vid} 不在受管快照目录，需先迁移') from exc
                digest = archive or content
                if not digest or relative.parts != (digest[:2], digest):
                    raise ValueError(f'版本 {vid} 的对象路径与摘要不一致')
                obj = objects / relative
                if not obj.is_file() or _sha256_file(obj) != digest:
                    raise ValueError(f'版本 {vid} 对象缺失或损坏')
                if target_library:
                    conn.execute('UPDATE versions SET file_path=? WHERE id=?',
                                 (str(target_library / '.teamhub' / 'objects' / relative), vid))
            rewrite_remote_operation_paths(conn, Path(source_library), target_library, objects)
        else:
            rows = conn.execute('SELECT id, storage_key, content_hash, manifest_hash FROM cloud_versions').fetchall()
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'delivery_items' in tables:
                rows += conn.execute('SELECT id, storage_key, content_hash, manifest_hash FROM delivery_items').fetchall()
            for vid, key, content, archive in rows:
                digest = archive or content
                if not digest or key != f'objects/{digest[:2]}/{digest}':
                    raise ValueError(f'共享版本 {vid} 不符合受管对象格式，需先迁移')
                obj = objects / digest[:2] / digest
                if not obj.is_file() or _sha256_file(obj) != digest:
                    raise ValueError(f'共享版本 {vid} 对象缺失或损坏')
        conn.commit()


def rewrite_remote_operation_paths(conn, source_library, target_library=None, objects=None):
    """Verify frozen snapshots, and relocate only paths owned by this library."""
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'remote_operations' not in tables:
        return
    source = Path(source_library)
    for oid, status, encoded, recovery in conn.execute(
            'SELECT id, status, input_json, recovery_json FROM remote_operations').fetchall():
        payload = json.loads(encoded)
        object_path = payload.get('object_path')
        if object_path:
            relative = _owned_relative(object_path, source / '.teamhub' / 'objects')
            digest = relative.name
            _check_object_relative(relative)
            if objects is not None:
                obj = Path(objects) / relative
                if not obj.is_file() or _sha256_file(obj) != digest:
                    raise ValueError(f'远端操作 {oid} 的冻结快照缺失或损坏')
            if target_library:
                payload['object_path'] = str(Path(target_library) / '.teamhub' / 'objects' / relative)
        state = json.loads(recovery) if recovery else None
        if state:
            if state.get('work') and state.get('backup'):
                work, backup = Path(state['work']), Path(state['backup'])
                if (not work.is_absolute() or not backup.is_absolute()
                        or '..' in work.parts or '..' in backup.parts
                        or backup.name != work.name or backup.parent.parent != work.parent
                        or not backup.parent.name.startswith('.teamhub-update-')):
                    raise ValueError(f'远端操作 {oid} 的恢复路径关系无效')
            for key in ('work', 'backup'):
                if not state.get(key):
                    continue
                try:
                    relative = _owned_relative(state[key], source)
                except ValueError as exc:
                    if status != 'succeeded':
                        raise ValueError(f'远端操作 {oid} 有库外恢复内容，请先完成恢复再备份或迁移') from exc
                    # Completed metadata/versions are authoritative; this optional
                    # retained working backup is outside the personal backup scope.
                    if target_library:
                        state[key] = None
                    continue
                if target_library:
                    state[key] = str(Path(target_library) / relative)
        if target_library:
            conn.execute('UPDATE remote_operations SET input_json=?, recovery_json=? WHERE id=?',
                         (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')),
                         json.dumps(state, ensure_ascii=False) if state else None, oid))


def _owned_relative(value, root):
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('受管路径必须为绝对路径且不能包含上级跳转')
    return path.relative_to(Path(root))


def _check_object_relative(relative):
    if (len(relative.parts) != 2 or not re.fullmatch(r'[0-9a-f]{64}', relative.name)
            or relative.parts[0] != relative.name[:2]):
        raise ValueError('冻结快照必须使用两层 SHA-256 对象路径')


def rewrite_publication_paths(directory, source_library, target_library):
    """Legacy publication journals are also durable frozen requests."""
    if not Path(directory).exists():
        return
    for path in Path(directory).glob('*.json'):
        journal = json.loads(path.read_text(encoding='utf-8'))
        if journal.get('object_path'):
            try:
                relative = _owned_relative(journal['object_path'], Path(source_library) / '.teamhub' / 'objects')
                _check_object_relative(relative)
            except ValueError as exc:
                raise ValueError('发布日志的冻结快照不在原对象库') from exc
            journal['object_path'] = str(Path(target_library) / '.teamhub' / 'objects' / relative)
            path.write_text(json.dumps(journal, ensure_ascii=False), encoding='utf-8')


def _write_manifest(stage, kind, **extra):
    manifest = dict(format_version=FORMAT_VERSION, kind=kind,
                    created_at=datetime.now(timezone.utc).isoformat(),
                    files=_inventory(stage), **extra)
    manifest['object_count'] = sum(v['type'] == 'file' for k, v in manifest['files'].items() if k.startswith('objects/'))
    (stage / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


@contextmanager
def _frozen_backup_database(backup, kind, manifest):
    """Keep old format-2 WAL backups readable without editing their frozen files."""
    name = 'db.sqlite' if kind == 'personal' else 'cloud.db'
    db = backup / name
    sidecars = [suffix for suffix in ('-wal', '-shm') if Path(str(db) + suffix).exists()]
    if not sidecars:
        yield db
        return
    # Old backups can contain committed rows only in WAL. Checkpoint a complete
    # verified private copy, never open the original backup as a writable DB.
    with tempfile.TemporaryDirectory(prefix='teamhub-backup-snapshot-') as directory:
        copied = Path(directory) / name
        for suffix in ('', *sidecars):
            source = Path(str(db) + suffix)
            target = Path(str(copied) + suffix)
            shutil.copy2(source, target)
            expected = manifest['files'].get(name + suffix)
            if (not expected or expected.get('type') != 'file'
                    or target.stat().st_size != expected.get('size')
                    or _sha256_file(target) != expected.get('sha256')):
                raise ValueError('备份数据库文件在复制期间发生变化')
        with closing(sqlite3.connect(copied.as_uri() + '?mode=rw', uri=True)) as conn:
            checkpoint = conn.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
            if checkpoint[0] != 0 or conn.execute('PRAGMA journal_mode=DELETE').fetchone() != ('delete',):
                raise ValueError('备份 WAL 无法合并为独立数据库快照')
        yield copied


def _read_backup(backup, kind):
    manifest = json.loads((backup / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('format_version') != FORMAT_VERSION or manifest.get('kind') != kind:
        raise ValueError('备份类型或格式不匹配；旧版备份需独立核验，不能自动恢复')
    actual = _inventory(backup)
    actual.pop('manifest.json', None)
    if manifest.get('files') != actual:
        raise ValueError('备份文件缺失、被增补或摘要不一致')
    with _frozen_backup_database(backup, kind, manifest) as db:
        _check_database(db)
        _verify_versions(db, backup / 'objects', kind, manifest.get('source_library'))
    return manifest


def backup_library(library_path, backup_dir):
    library, target = _safe_path(library_path), _safe_path(backup_dir)
    _disjoint(library, target)
    meta = library / '.teamhub'
    with _frozen_database(meta / 'db.sqlite'), _staged_directory(target) as stage:
        _backup_sqlite(meta / 'db.sqlite', stage / 'db.sqlite')
        _copy_tree(meta / 'objects', stage / 'objects')
        _copy_tree(meta / 'trash', stage / 'trash')
        _copy_tree(meta / 'publications', stage / 'publications')
        working = stage / 'working'
        working.mkdir()
        for child in library.iterdir():
            if child.name == '.teamhub':
                continue
            if child.is_symlink() or not (child.is_file() or child.is_dir()):
                raise ValueError(f'备份拒绝链接或特殊文件: {child}')
            if child.is_dir():
                _copy_tree(child, working / child.name)
            else:
                before = _sha256_file(child)
                shutil.copy2(child, working / child.name)
                if before != _sha256_file(child) or before != _sha256_file(working / child.name):
                    raise RuntimeError('备份期间工作文件发生变化')
        _safe_path(meta / 'config.json')
        config = json.loads((meta / 'config.json').read_text(encoding='utf-8')) if (meta / 'config.json').exists() else {}
        (stage / 'config.json').write_text(json.dumps(_redact_config(config), ensure_ascii=False, indent=2), encoding='utf-8')
        _check_database(stage / 'db.sqlite')
        _verify_versions(stage / 'db.sqlite', stage / 'objects', 'personal', str(library))
        return _write_manifest(stage, 'personal', source_library=str(library), config_redacted=True)


def restore_library(backup_dir, target_library):
    backup, target = _safe_path(backup_dir), _safe_path(target_library)
    _disjoint(backup, target)
    _empty_target(target)
    manifest = _read_backup(backup, 'personal')
    with _staged_directory(target) as stage:
        meta = stage / '.teamhub'
        meta.mkdir()
        with _frozen_backup_database(backup, 'personal', manifest) as snapshot:
            expected = _sha256_file(snapshot)
            shutil.copy2(snapshot, meta / 'db.sqlite')
            if _sha256_file(meta / 'db.sqlite') != expected:
                raise ValueError('恢复数据库摘要不一致')
        for name in ('objects', 'trash', 'publications'):
            _copy_tree(backup / name, meta / name)
        config = json.loads((backup / 'config.json').read_text(encoding='utf-8'))
        (meta / 'config.json').write_text(json.dumps(_redact_config(config), ensure_ascii=False, indent=2), encoding='utf-8')
        working = backup / 'working'
        if working.exists():
            for child in working.iterdir():
                if child.is_dir():
                    _copy_tree(child, stage / child.name)
                else:
                    shutil.copy2(child, stage / child.name)
                    if _sha256_file(stage / child.name) != manifest['files']['working/' + child.name]['sha256']:
                        raise ValueError('恢复工作文件摘要不一致')
        _read_backup(backup, 'personal')  # 拒绝复制期间备份源变化
        _verify_versions(meta / 'db.sqlite', meta / 'objects', 'personal', manifest['source_library'], target)
        rewrite_publication_paths(meta / 'publications', manifest['source_library'], target)
        _check_database(meta / 'db.sqlite')
        counts = _db_counts(meta / 'db.sqlite')
    return {'manifest': manifest, 'verify': {**counts, 'object_count': manifest['object_count']}}


def backup_cloud(db_path, storage_dir, backup_dir):
    db, storage, target = map(_safe_path, (db_path, storage_dir, backup_dir))
    _disjoint(storage, target)
    _disjoint(db, target)
    with _frozen_database(db), _staged_directory(target) as stage:
        _backup_sqlite(db, stage / 'cloud.db')
        with _snapshot_connection(stage / 'cloud.db', writable=True) as conn:
            conn.execute('DELETE FROM refresh_tokens')
            conn.commit()
        _copy_tree(storage / 'objects', stage / 'objects')
        _check_database(stage / 'cloud.db')
        _verify_versions(stage / 'cloud.db', stage / 'objects', 'cloud')
        return _write_manifest(stage, 'cloud', refresh_tokens_removed=True)


def restore_cloud(backup_dir, target_db, target_storage):
    backup, db, storage = map(_safe_path, (backup_dir, target_db, target_storage))
    for a, b in ((backup, db), (backup, storage), (db, storage)):
        _disjoint(a, b)
    if db.exists() or Path(str(db) + '-wal').exists() or Path(str(db) + '-shm').exists():
        raise FileExistsError('目标数据库或 WAL 已存在，拒绝覆盖')
    _empty_target(storage)
    manifest = _read_backup(backup, 'cloud')
    db.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix='.cloud-restore-', dir=db.parent)
    os.close(fd)
    temp_db = Path(temp_name)
    published_storage = False
    try:
        with _frozen_backup_database(backup, 'cloud', manifest) as snapshot:
            expected = _sha256_file(snapshot)
            shutil.copy2(snapshot, temp_db)
            if _sha256_file(temp_db) != expected:
                raise ValueError('恢复数据库摘要不一致')
        with _staged_directory(storage) as stage:
            _copy_tree(backup / 'objects', stage / 'objects')
            _verify_versions(temp_db, stage / 'objects', 'cloud')
            _read_backup(backup, 'cloud')
        published_storage = True
        os.link(temp_db, db)  # 排他发布；竞态中出现的目标数据库不覆盖。
    except Exception:
        if published_storage:
            shutil.rmtree(storage)
        raise
    finally:
        temp_db.unlink(missing_ok=True)
    return {'restored': True, 'object_count': manifest['object_count']}


def _db_counts(db_path):
    with _snapshot_connection(db_path) as conn:
        return {name: conn.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
                for name in ('documents', 'versions', 'tags')}


def backup_is_redacted(backup_dir):
    data = json.loads((Path(backup_dir) / 'config.json').read_text(encoding='utf-8'))
    return not (set(data) - CONFIG_FIELDS)
