"""AI device settings never persist plaintext credentials or touch real keychains."""
from types import SimpleNamespace

import pytest

from backend.app.core import ai_settings as ai


@pytest.fixture(autouse=True)
def profile(monkeypatch, tmp_path):
    monkeypatch.setenv('TEAMHUB_AI_CONFIG_DIR', str(tmp_path / 'profile'))
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    monkeypatch.setattr(ai, '_can_use_keychain', lambda: False)
    ai._memory_keys.clear()
    ai._keychain_keys.clear()


def test_default_and_secret_never_appear_on_disk():
    assert ai.get_settings() == {
        'provider': 'deepseek', 'base_url': 'https://api.deepseek.com',
        'model': 'deepseek-flash', 'has_api_key': False,
        'key_storage': 'memory', 'can_persist_key': False,
    }
    result = ai.update_settings('https://api.deepseek.com/v1/', 'deepseek-flash', 'secret-unit-only')
    assert result['has_api_key'] and result['base_url'].endswith('/v1')
    path = ai._config_path()
    assert 'secret-unit-only' not in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600
    safe, key, revision = ai.get_connection()
    assert key == 'secret-unit-only' and len(revision) == 64
    assert 'secret-unit-only' not in str(safe) + revision


def test_profile_independent_of_library_and_isolated(monkeypatch, tmp_path):
    ai.update_settings('https://api.deepseek.com', 'first', 'secret-one')
    first_revision = ai.get_connection()[2]
    monkeypatch.setattr(ai.settings, 'library_path', str(tmp_path / 'other-library'))
    assert ai.get_connection()[1:] == ('secret-one', first_revision)
    monkeypatch.setenv('TEAMHUB_AI_CONFIG_DIR', str(tmp_path / 'second-profile'))
    assert not ai.get_settings()['has_api_key']


def test_electron_uses_device_directory(monkeypatch, tmp_path):
    monkeypatch.delenv('TEAMHUB_AI_CONFIG_DIR')
    monkeypatch.setattr(ai.settings, 'is_electron', True)
    monkeypatch.setattr(ai.settings, 'user_data_dir', str(tmp_path / 'desktop'))
    assert ai._config_path() == tmp_path / 'desktop' / 'config' / 'ai.json'


def test_sandbox_never_uses_home_or_keychain(monkeypatch, tmp_path):
    monkeypatch.delenv('TEAMHUB_AI_CONFIG_DIR')
    monkeypatch.setenv('TEAMHUB_SANDBOX', '1')
    monkeypatch.setattr(ai.settings, 'is_electron', False)
    monkeypatch.setattr(ai.settings, 'user_data_dir', '.')
    monkeypatch.setattr(ai.settings, 'library_path', str(tmp_path / 'library'))
    monkeypatch.setattr(ai.sys, 'platform', 'darwin')
    assert ai._config_path().is_relative_to(tmp_path)
    # Undo the fixture override to exercise the real safety predicate.
    monkeypatch.setattr(ai, '_can_use_keychain', ai._platform_keychain_available)
    assert not ai.get_settings()['can_persist_key']


@pytest.mark.parametrize('base', [
    'http://api.deepseek.com', 'https://user:pass@api.deepseek.com',
    'https://api.deepseek.com?key=secret', 'https://api.deepseek.com/#x',
    'https://169.254.169.254', 'https://10.0.0.2', 'https://192.168.2.1',
    'https://[fd00::1]', 'https://metadata.google.internal', 'file:///tmp/a',
    'https://api.deepseek.com/../metadata', 'https://api.deepseek.com\n',
])
def test_reject_unsafe_service_urls(base):
    with pytest.raises(ai.AISettingsError):
        ai.update_settings(base, 'deepseek-flash', 'secret-unit-only')
    assert not ai._config_path().exists()


@pytest.mark.parametrize('base', ['http://127.0.0.1:9234/v1', 'http://[::1]:9234', 'http://localhost:9234'])
def test_allow_local_api_for_isolated_tests(base):
    assert ai.update_settings(base, 'fake-model', 'fake-key')['base_url'] == base


def test_service_change_requires_fresh_key_and_does_not_mutate():
    ai.update_settings('https://api.deepseek.com', 'first', 'old-secret')
    before = ai.get_connection()
    with pytest.raises(ai.AISettingsError, match='密钥'):
        ai.update_settings('https://example.com', 'second')
    assert ai.get_connection() == before
    ai.update_settings('https://example.com', 'second', 'new-secret')
    assert ai.get_connection()[1] == 'new-secret'
    assert ai.get_connection()[2] != before[2]


def test_environment_key_clear_is_durable(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'environment-secret')
    assert ai.get_settings()['key_storage'] == 'environment'
    assert ai.get_connection()[1] == 'environment-secret'
    ai.update_settings('https://api.deepseek.com', 'deepseek-flash', clear_api_key=True)
    assert not ai.get_settings()['has_api_key']
    with pytest.raises(ai.AISettingsError, match='密钥'):
        ai.get_connection()


def test_write_failure_keeps_existing_secret_and_revision(monkeypatch):
    ai.update_settings('https://api.deepseek.com', 'first', 'old-secret')
    before = ai.get_connection()
    def fail(*_args):
        raise OSError('secret-new /sensitive/path')
    monkeypatch.setattr(ai, '_write_config', fail)
    with pytest.raises(ai.AISettingsError) as caught:
        ai.update_settings('https://api.deepseek.com', 'second', 'secret-new')
    assert 'secret-new' not in str(caught.value) and '/sensitive' not in str(caught.value)
    assert ai.get_connection() == before


def test_blank_key_and_clear_replace_conflict_rejected():
    for kwargs in ({'api_key': '   '}, {'api_key': 'secret', 'clear_api_key': True}):
        with pytest.raises(ai.AISettingsError):
            ai.update_settings('https://api.deepseek.com', 'model', **kwargs)


def test_keychain_stdin_only_and_profile_scoped(monkeypatch):
    monkeypatch.setattr(ai, '_can_use_keychain', lambda: True)
    stored = {}
    seen = []
    def fake_run(argv, **kwargs):
        seen.append((argv, kwargs))
        if argv[-1] == '-i':
            import shlex
            command = shlex.split(kwargs['input'].strip())
            stored['payload'] = command[command.index('-w') + 1]
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        if argv[1] == 'find-generic-password':
            return SimpleNamespace(returncode=0 if stored else 44, stdout=stored.get('payload', ''), stderr='')
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(ai.subprocess, 'run', fake_run)
    ai.update_settings('https://api.deepseek.com', 'model', 'a-secret-with-"quotes"')
    assert ai.get_connection()[1] == 'a-secret-with-"quotes"'
    assert all('a-secret' not in str(argv) for argv, _ in seen)
    assert all(not opts.get('shell') for _, opts in seen)
    assert ai.get_settings()['key_storage'] == 'keychain'
    assert 'a-secret' not in ai._config_path().read_text()


def test_keychain_interactive_command_failure_cannot_report_success(monkeypatch):
    monkeypatch.setattr(ai, '_can_use_keychain', lambda: True)
    monkeypatch.setattr(ai, '_read_keychain', lambda _path: {})
    monkeypatch.setattr(ai.subprocess, 'run', lambda *_args, **_kwargs:
                        SimpleNamespace(returncode=0, stdout='', stderr='command denied'))
    with pytest.raises(ai.AISettingsError, match='密钥'):
        ai.update_settings('https://api.deepseek.com', 'model', 'unit-only-secret')
    assert not ai._config_path().exists()


def test_clear_keychain_erases_secret_material_after_config_commit(monkeypatch):
    monkeypatch.setattr(ai, '_can_use_keychain', lambda: True)
    store = {}
    monkeypatch.setattr(ai, '_read_keychain', lambda _path: dict(store))
    def save(_path, value):
        store.clear()
        store.update(value)
    monkeypatch.setattr(ai, '_write_keychain', save)
    ai.update_settings('https://api.deepseek.com', 'model', 'old-key')
    ai.update_settings('https://api.deepseek.com', 'model', clear_api_key=True)
    assert store == {} and not ai.get_settings()['has_api_key']


def test_keychain_clear_config_failure_keeps_working_secret(monkeypatch):
    monkeypatch.setattr(ai, '_can_use_keychain', lambda: True)
    store = {}
    monkeypatch.setattr(ai, '_read_keychain', lambda _path: dict(store))
    def save(_path, value):
        store.clear()
        store.update(value)
    monkeypatch.setattr(ai, '_write_keychain', save)
    ai.update_settings('https://api.deepseek.com', 'model', 'old-key')
    before = ai.get_connection()
    monkeypatch.setattr(ai, '_write_config', lambda *_args: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(ai.AISettingsError):
        ai.update_settings('https://api.deepseek.com', 'model', clear_api_key=True)
    assert ai.get_connection() == before


def test_keychain_config_failure_preserves_prior_slot(monkeypatch):
    monkeypatch.setattr(ai, '_can_use_keychain', lambda: True)
    store = {}
    monkeypatch.setattr(ai, '_read_keychain', lambda _path: dict(store))
    def save(_path, value):
        store.clear()
        store.update(value)
    monkeypatch.setattr(ai, '_write_keychain', save)
    ai.update_settings('https://api.deepseek.com', 'first', 'old-key')
    before = ai.get_connection()
    monkeypatch.setattr(ai, '_write_config', lambda *_args: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(ai.AISettingsError):
        ai.update_settings('https://api.deepseek.com', 'second', 'new-key')
    assert ai.get_connection() == before
    assert 'old-key' in store.values()


def test_keychain_failure_leaves_config_unchanged(monkeypatch):
    ai.update_settings('https://api.deepseek.com', 'first', 'old-key')
    before = ai.get_connection()
    monkeypatch.setattr(ai, '_can_use_keychain', lambda: True)
    monkeypatch.setattr(ai, '_read_keychain', lambda _path: {})
    def fail(*_args):
        raise ai.AISettingsError('无法保存 AI 服务密钥，请检查系统钥匙串')
    monkeypatch.setattr(ai, '_write_keychain', fail)
    with pytest.raises(ai.AISettingsError):
        ai.update_settings('https://api.deepseek.com', 'second', 'new-key')
    after = ai.get_connection()
    assert after[1:] == before[1:]
    assert after[0]['model'] == 'first'


def test_corrupt_config_does_not_silently_reenable_environment(monkeypatch):
    path = ai._config_path()
    path.parent.mkdir(parents=True)
    path.write_text('{broken')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'environment-key')
    with pytest.raises(ai.AISettingsError, match='配置'):
        ai.get_settings()


def test_keychain_reads_are_reused_for_committed_slot(monkeypatch):
    monkeypatch.setattr(ai, '_can_use_keychain', lambda: True)
    path = ai._config_path()
    slot = 'a' * 32
    ai._write_config(path, {**ai._read_config(path), 'key_source': 'keychain', 'key_slot': slot})
    reads = []
    def read(target):
        reads.append(target)
        return {slot: 'unit-key'}
    monkeypatch.setattr(ai, '_read_keychain', read)
    assert ai.get_settings()['has_api_key']
    first = ai.get_connection()
    for _ in range(50):
        assert ai.get_connection() == first
    assert reads == [path]


def test_keychain_cache_isolated_by_profile_and_slot(monkeypatch, tmp_path):
    monkeypatch.setattr(ai, '_can_use_keychain', lambda: True)
    stores = {}
    reads = []
    def read(path):
        reads.append(path)
        return stores[path]
    monkeypatch.setattr(ai, '_read_keychain', read)
    first_path = ai._config_path()
    first_slot, second_slot = 'a' * 32, 'b' * 32
    config = {**ai._read_config(first_path), 'key_source': 'keychain', 'key_slot': first_slot}
    ai._write_config(first_path, config)
    stores[first_path] = {first_slot: 'first-key', second_slot: 'second-slot-key'}
    first_revision = ai.get_connection()[2]
    ai._write_config(first_path, {**config, 'key_slot': second_slot})
    assert ai.get_connection()[1] == 'second-slot-key'
    assert ai.get_connection()[2] != first_revision
    monkeypatch.setenv('TEAMHUB_AI_CONFIG_DIR', str(tmp_path / 'second-profile'))
    second_path = ai._config_path()
    ai._write_config(second_path, config)
    stores[second_path] = {first_slot: 'second-profile-key'}
    assert ai.get_connection()[1] == 'second-profile-key'
    assert reads == [first_path, first_path, second_path]


def test_keychain_cache_never_bypasses_sandbox_gate(monkeypatch):
    monkeypatch.setattr(ai, '_can_use_keychain', lambda: True)
    path = ai._config_path()
    slot = 'a' * 32
    ai._write_config(path, {**ai._read_config(path), 'key_source': 'keychain', 'key_slot': slot})
    monkeypatch.setattr(ai, '_read_keychain', lambda _path: {slot: 'unit-key'})
    assert ai.get_connection()[1] == 'unit-key'
    monkeypatch.setattr(ai, '_can_use_keychain', lambda: False)
    monkeypatch.setattr(ai, '_read_keychain', lambda _path: pytest.fail('sandbox read keychain'))
    assert not ai.get_settings()['has_api_key']
    with pytest.raises(ai.AISettingsError, match='密钥'):
        ai.get_connection()


def test_missing_keychain_secret_is_not_cached(monkeypatch):
    monkeypatch.setattr(ai, '_can_use_keychain', lambda: True)
    path = ai._config_path()
    slot = 'a' * 32
    ai._write_config(path, {**ai._read_config(path), 'key_source': 'keychain', 'key_slot': slot})
    store = {}
    monkeypatch.setattr(ai, '_read_keychain', lambda _path: dict(store))
    assert not ai.get_settings()['has_api_key']
    store[slot] = 'later-key'
    assert ai.get_connection()[1] == 'later-key'


def test_keychain_replace_and_clear_invalidate_committed_secret(monkeypatch):
    monkeypatch.setattr(ai, '_can_use_keychain', lambda: True)
    store = {}
    monkeypatch.setattr(ai, '_read_keychain', lambda _path: dict(store))
    def save(_path, payload):
        store.clear()
        store.update(payload)
    monkeypatch.setattr(ai, '_write_keychain', save)
    ai.update_settings('https://api.deepseek.com', 'model', 'old-key')
    old_revision = ai.get_connection()[2]
    ai.update_settings('https://api.deepseek.com', 'model', 'new-key')
    assert ai.get_connection()[1] == 'new-key'
    assert ai.get_connection()[2] != old_revision
    ai.update_settings('https://api.deepseek.com', 'model', clear_api_key=True)
    assert not ai.get_settings()['has_api_key']
    with pytest.raises(ai.AISettingsError, match='密钥'):
        ai.get_connection()
    assert not ai._keychain_keys


def test_keychain_write_verification_always_reads_protected_store(monkeypatch):
    monkeypatch.setattr(ai, '_can_use_keychain', lambda: True)
    path = ai._config_path()
    slot = 'a' * 32
    ai._write_config(path, {**ai._read_config(path), 'key_source': 'keychain', 'key_slot': slot})
    monkeypatch.setattr(ai, '_read_keychain', lambda _path: {slot: 'old-key'})
    assert ai.get_connection()[1] == 'old-key'
    monkeypatch.setattr(ai.subprocess, 'run', lambda *_args, **_kwargs:
                        SimpleNamespace(returncode=0, stdout='', stderr=''))
    with pytest.raises(ai.AISettingsError, match='密钥'):
        ai._write_keychain(path, {slot: 'new-key'})
