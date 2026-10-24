"""Standalone snapshots stay immutable; active-source WAL rows are never ignored."""
from contextlib import closing
import hashlib
from pathlib import Path
import shutil
import sqlite3
from urllib.parse import unquote, urlsplit

import pytest

from backend.app.core import backup


def cloud_source(tmp_path):
    path = tmp_path / 'source.db'
    objects = tmp_path / 'storage' / 'objects'
    payload = b'WAL source object'
    digest = hashlib.sha256(payload).hexdigest()
    obj = objects / digest[:2] / digest
    obj.parent.mkdir(parents=True)
    obj.write_bytes(payload)
    connection = sqlite3.connect(path)
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('PRAGMA wal_autocheckpoint=0')
    connection.execute('CREATE TABLE cloud_versions(id TEXT, storage_key TEXT, content_hash TEXT, manifest_hash TEXT)')
    connection.execute('CREATE TABLE refresh_tokens(token_hash TEXT)')
    connection.execute('CREATE TABLE metadata(value TEXT)')
    connection.commit()
    connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    connection.execute('INSERT INTO cloud_versions VALUES (?, ?, ?, NULL)',
                       ('wal-version', f'objects/{digest[:2]}/{digest}', digest))
    connection.execute("INSERT INTO refresh_tokens VALUES ('private-token')")
    connection.execute("INSERT INTO metadata VALUES ('committed only in WAL')")
    connection.commit()
    return path, objects.parent, connection


def track_connections(monkeypatch, *, frozen_directory=None):
    actual_connect = sqlite3.connect
    connections = []

    class TrackedConnection(sqlite3.Connection):
        closed = False
        frozen = False
        filename = None

        def execute(self, *args, **kwargs):
            result = super().execute(*args, **kwargs)
            if self.frozen:
                assert not any(Path(str(self.filename) + suffix).exists()
                               for suffix in ('-wal', '-shm', '-journal'))
            return result

        def close(self):
            super().close()
            self.closed = True

    def connect(database, *args, **kwargs):
        kwargs['factory'] = TrackedConnection
        connection = actual_connect(database, *args, **kwargs)
        encoded = str(database)
        connection.filename = (Path(unquote(urlsplit(encoded).path))
                               if kwargs.get('uri') else Path(database))
        if frozen_directory and connection.filename.is_relative_to(frozen_directory):
            connection.frozen = True
            assert 'mode=ro&immutable=1' in encoded
        connections.append((encoded, connection))
        return connection

    monkeypatch.setattr(backup.sqlite3, 'connect', connect)
    return connections


def test_wal_backup_is_standalone_includes_rows_and_closes_every_connection(tmp_path, monkeypatch):
    source, storage, writer = cloud_source(tmp_path)
    try:
        assert Path(str(source) + '-wal').stat().st_size > 0
        # Immutable source reads would miss this row: prove it resides in WAL.
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro&immutable=1', uri=True)) as stale:
            assert stale.execute('SELECT COUNT(*) FROM metadata').fetchone()[0] == 0
        connections = track_connections(monkeypatch)
        destination = tmp_path / 'backup'
        backup.backup_cloud(source, storage, destination)
        assert connections and all(conn.closed for _, conn in connections)
        assert all('immutable' not in encoded for encoded, _ in connections if source.name in encoded)
        assert set(path.name for path in destination.iterdir()) == {'cloud.db', 'objects', 'manifest.json'}
        with backup._snapshot_connection(destination / 'cloud.db') as snapshot:
            assert snapshot.execute('PRAGMA journal_mode').fetchone() == ('delete',)
            assert snapshot.execute('SELECT value FROM metadata').fetchone() == ('committed only in WAL',)
            assert snapshot.execute('SELECT COUNT(*) FROM refresh_tokens').fetchone() == (0,)
        with closing(sqlite3.connect(source)) as untouched:
            assert untouched.execute('SELECT COUNT(*) FROM refresh_tokens').fetchone() == (1,)
        assert all(conn.closed for _, conn in connections)
    finally:
        writer.close()


def test_read_and_restore_existing_standalone_wal_snapshot_never_make_sidecars(tmp_path, monkeypatch):
    frozen = tmp_path / 'frozen'
    frozen.mkdir()
    db = frozen / 'cloud.db'
    # An older standalone backup may retain WAL mode in its main-file header.
    # Closing its last writer checkpoints all content and removes WAL/SHM.
    with closing(sqlite3.connect(db)) as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('CREATE TABLE cloud_versions(id TEXT, storage_key TEXT, content_hash TEXT, manifest_hash TEXT)')
        conn.execute('CREATE TABLE refresh_tokens(token_hash TEXT)')
        conn.commit()
    (frozen / 'objects').mkdir()
    backup._write_manifest(frozen, 'cloud', refresh_tokens_removed=True)
    before = backup._inventory(frozen)
    connections = track_connections(monkeypatch, frozen_directory=frozen)
    for _ in range(3):
        backup._read_backup(frozen, 'cloud')
        assert backup._inventory(frozen) == before
        assert all(connection.closed for _, connection in connections)
    result = backup.restore_cloud(frozen, tmp_path / 'restored.db', tmp_path / 'restored-storage')
    assert result['restored'] and backup._inventory(frozen) == before
    assert all(connection.closed for _, connection in connections)
    assert not any(Path(str(db) + suffix).exists() for suffix in ('-wal', '-shm', '-journal'))
    assert not list(tmp_path.glob('.cloud-restore-*'))


def test_snapshot_validator_refuses_uncheckpointed_sidecars(tmp_path):
    source, _, writer = cloud_source(tmp_path)
    try:
        assert Path(str(source) + '-wal').stat().st_size > 0
        with pytest.raises(ValueError, match='事务旁文件'):
            backup._check_database(source)
    finally:
        writer.close()


def test_snapshot_connection_closes_after_validation_failure(tmp_path, monkeypatch):
    db = tmp_path / 'invalid.db'
    with closing(sqlite3.connect(db)) as connection:
        connection.execute('CREATE TABLE unrelated(id TEXT)')
    connections = track_connections(monkeypatch)
    with pytest.raises(sqlite3.OperationalError):
        backup._verify_versions(db, tmp_path / 'objects', 'cloud')
    assert connections and all(connection.closed for _, connection in connections)


def test_legacy_format2_wal_backup_restores_committed_rows_without_mutating_original(tmp_path, monkeypatch):
    source, storage, writer = cloud_source(tmp_path)
    frozen = tmp_path / 'legacy-format2'
    frozen.mkdir()
    try:
        writer.execute('DELETE FROM refresh_tokens')
        writer.commit()
        # Capture the historical format exactly: standalone main + committed WAL.
        for suffix in ('', '-wal', '-shm'):
            shutil.copy2(Path(str(source) + suffix), frozen / ('cloud.db' + suffix))
        shutil.copytree(storage / 'objects', frozen / 'objects')
    finally:
        writer.close()
    backup._write_manifest(frozen, 'cloud', refresh_tokens_removed=True)
    before = backup._inventory(frozen)
    connections = track_connections(monkeypatch, frozen_directory=frozen)
    backup._read_backup(frozen, 'cloud')
    target = tmp_path / 'restored.db'
    backup.restore_cloud(frozen, target, tmp_path / 'restored-storage')
    assert backup._inventory(frozen) == before
    assert connections and all(conn.closed for _, conn in connections)
    with backup._snapshot_connection(target) as restored:
        assert restored.execute('SELECT value FROM metadata').fetchone() == ('committed only in WAL',)
        assert restored.execute('SELECT id FROM cloud_versions').fetchone() == ('wal-version',)
        assert restored.execute('SELECT COUNT(*) FROM refresh_tokens').fetchone() == (0,)
        assert restored.execute('PRAGMA journal_mode').fetchone() == ('delete',)
