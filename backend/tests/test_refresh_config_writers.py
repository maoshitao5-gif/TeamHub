"""普通配置写入及资料库切换不得复活已撤销的刷新令牌。"""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import httpx
import pytest

from backend.app import database
from backend.app.api import settings_api, sync
from backend.app.api.schemas import DeviceNameUpdate, LibrarySetup
from backend.app.config import settings
from backend.app.core import cloud_client, cloud_session, device
from backend.app.services import storage_migration as migration


@pytest.fixture
def library(tmp_path, monkeypatch):
    old = settings.library_path, settings.pending_path
    monkeypatch.setattr(settings, 'library_path', str(tmp_path / 'library'))
    monkeypatch.setattr(settings, 'pending_path', None)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(migration, 'bootstrap_path', lambda: tmp_path / 'bootstrap.json')
    database.rebind_engine()
    database.init_db()
    config = dict(cloud_api_url='http://refresh.example', cloud_access_token='old-access',
                  cloud_refresh_token='old-refresh', cloud_server_id='server-a')
    database.save_library_config(config, strict=True)
    device.reset_device_id_cache()
    cloud_client._refresh_lineage.clear()
    yield Path(settings.config_file), config
    device.reset_device_id_cache()
    settings.library_path, settings.pending_path = old
    database.rebind_engine()


@pytest.mark.parametrize('writer', ['name', 'id'])
def test_device_writer_cannot_overwrite_rotated_pair(library, monkeypatch, writer):
    _, config = library
    read, release, requested = Event(), Event(), Event()
    original_read = database.load_library_config
    original_path_read = cloud_session.read_config_at
    def delay_read(*args):
        value = original_path_read(*args) if args else original_read()
        read.set()
        assert release.wait(5)
        return value
    monkeypatch.setattr(database, 'load_library_config', delay_read)
    monkeypatch.setattr(sync, 'load_library_config', delay_read)
    monkeypatch.setattr(sync, '_read_session_config', delay_read)
    monkeypatch.setattr(device, 'read_config_at', delay_read, raising=False)
    def refresh(request):
        requested.set()
        return httpx.Response(200, json={'access_token': 'new', 'refresh_token': 'rotated'})
    with httpx.Client(transport=httpx.MockTransport(refresh)) as http, ThreadPoolExecutor(2) as pool:
        monkeypatch.setattr(cloud_client, '_http', http)
        operation = (lambda: sync.update_device_name(DeviceNameUpdate(name='新名称'))) if writer == 'name' else device.get_device_id
        saving = pool.submit(operation)
        assert read.wait(5)
        renewing = pool.submit(cloud_client.refresh_token, config.copy())
        try:
            assert not requested.wait(.1), '续期不得穿过持有旧配置的写入者'
        finally:
            release.set()
        saving.result(timeout=5)
        assert renewing.result(timeout=5)['cloud_refresh_token'] == 'rotated'
    saved = original_read()
    assert saved['cloud_access_token'] == 'new' and saved['cloud_refresh_token'] == 'rotated'
    if writer == 'name':
        assert saved['device_name'] == '新名称'
    else:
        assert saved['device_id'] == device.get_device_id()


@pytest.mark.parametrize('writer', ['name', 'id'])
def test_device_write_failure_does_not_report_success_or_cache_id(library, monkeypatch, writer):
    _, config = library
    def fail(*args):
        raise PermissionError('只读目录')
    monkeypatch.setattr(database, 'write_json_config', fail)
    monkeypatch.setattr(sync, 'write_json_config', fail)
    operation = (lambda: sync.update_device_name(DeviceNameUpdate(name='新名称'))) if writer == 'name' else device.get_device_id
    with pytest.raises(OSError):
        operation()
    assert database.load_library_config() == config
    assert device._cached_device_id is None


@pytest.mark.parametrize('operation', ['migration', 'setup'])
def test_library_change_waits_for_inflight_refresh_and_keeps_latest_pair(library, tmp_path, monkeypatch, operation):
    original_path, config = library
    requested, release, copying, changing = Event(), Event(), Event(), Event()
    original_copy = migration._copy
    original_setup = settings_api._setup_library_locked
    def copied(entry, journal):
        if Path(entry['source']).name == '.teamhub':
            copying.set()
        return original_copy(entry, journal)
    def setup(req):
        changing.set()
        return original_setup(req)
    monkeypatch.setattr(migration, '_copy', copied)
    monkeypatch.setattr(settings_api, '_setup_library_locked', setup)
    def refresh(request):
        requested.set()
        assert release.wait(5)
        return httpx.Response(200, json={'access_token': 'new', 'refresh_token': 'rotated'})
    target = tmp_path / 'new'
    with httpx.Client(transport=httpx.MockTransport(refresh)) as http, ThreadPoolExecutor(2) as pool:
        monkeypatch.setattr(cloud_client, '_http', http)
        renewing = pool.submit(cloud_client.refresh_token, config.copy())
        assert requested.wait(5)
        change = pool.submit(migration.migrate, 'library', str(target)) if operation == 'migration' else pool.submit(settings_api.setup_library, LibrarySetup(path=str(target)))
        try:
            event = copying if operation == 'migration' else changing
            assert not event.wait(.1), '在飞续期完成前不能复制会话或切换个人库'
        finally:
            release.set()
        assert renewing.result(timeout=5)['cloud_refresh_token'] == 'rotated'
        change.result(timeout=5)
    saved_path = target / '.teamhub/config.json' if operation == 'migration' else original_path
    saved = json.loads(saved_path.read_text())
    assert saved['cloud_access_token'] == 'new' and saved['cloud_refresh_token'] == 'rotated'
    if operation == 'setup':
        assert not database.load_library_config().get('cloud_refresh_token')


def test_device_writer_waits_for_metadata_copy_then_writes_new_library(library, tmp_path, monkeypatch):
    _, config = library
    copied, release, writing, finished = Event(), Event(), Event(), Event()
    original_copy = migration._copy
    def copy(entry, journal):
        original_copy(entry, journal)
        if Path(entry['source']).name == '.teamhub':
            copied.set()
            assert release.wait(5)
    monkeypatch.setattr(migration, '_copy', copy)
    def rename():
        writing.set()
        result = sync.update_device_name(DeviceNameUpdate(name='迁移后的设备'))
        finished.set()
        return result
    target = tmp_path / 'new'
    with ThreadPoolExecutor(2) as pool:
        moving = pool.submit(migration.migrate, 'library', str(target))
        assert copied.wait(5)
        saving = pool.submit(rename)
        assert writing.wait(5)
        try:
            assert not finished.wait(.1), '元数据复制完成到库切换之间不能写回旧位置'
        finally:
            release.set()
        assert moving.result(timeout=5)['phase'] == 'complete'
        assert saving.result(timeout=5)['device_name'] == '迁移后的设备'
    saved = database.load_library_config()
    assert saved['device_name'] == '迁移后的设备'
    assert saved['cloud_refresh_token'] == config['cloud_refresh_token']
    assert Path(settings.config_file) == target / '.teamhub/config.json'
