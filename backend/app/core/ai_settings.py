"""Device-scoped AI configuration with opaque references to protected credentials.

Only this module handles API secrets. The renderer and library metadata receive
safe settings; sandbox runs never access the user's macOS keychain.
Protected keys are cached only in this process for the committed configuration
slot. Replacing or clearing a key commits a new slot before discarding its cache;
external keychain edits to the same slot take effect after a process restart.
"""
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
from threading import RLock
from urllib.parse import urlsplit, urlunsplit
import uuid

from backend.app.config import settings


DEFAULT_BASE_URL = 'https://api.deepseek.com'
DEFAULT_MODEL = 'deepseek-flash'
_lock = RLock()
_memory_keys: dict[str, dict[str, str]] = {}
_keychain_keys: dict[tuple[str, str, str], str] = {}
_ACCOUNT = 'teamhub-ai'


class AISettingsError(ValueError):
    """A user-safe configuration error; never contains credentials or paths."""


def _is_sandbox() -> bool:
    return os.getenv('TEAMHUB_SANDBOX', '').lower() in {'1', 'true', 'yes'}


def _config_path() -> Path:
    explicit = os.getenv('TEAMHUB_AI_CONFIG_DIR', '').strip()
    if explicit:
        return Path(explicit).expanduser().resolve() / 'ai.json'
    if settings.is_electron or (_is_sandbox() and settings.user_data_dir not in {'', '.'}):
        return Path(settings.user_data_dir).expanduser().resolve() / 'config' / 'ai.json'
    if _is_sandbox():
        if not settings.library_path:
            raise AISettingsError('测试模式需要独立的 AI 配置目录')
        return Path(settings.library_path).resolve() / '.teamhub' / 'ai-sandbox' / 'ai.json'
    return Path.home() / '.config' / 'teamhub' / 'ai.json'


def _platform_keychain_available() -> bool:
    return sys.platform == 'darwin' and not _is_sandbox()


def _can_use_keychain() -> bool:
    return _platform_keychain_available()


def _normalize_url(value: str) -> str:
    error = AISettingsError('AI 服务地址须为有效 HTTPS 地址，本机测试可使用 HTTP')
    if not isinstance(value, str) or not value or any(c.isspace() or ord(c) < 32 for c in value):
        raise error
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or '').lower().rstrip('.')
        port = parsed.port
    except ValueError:
        raise error from None
    if (parsed.scheme not in {'http', 'https'} or not host or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment or '\\' in value
            or '%' in parsed.netloc or any(part in {'.', '..'} for part in parsed.path.split('/'))):
        raise error
    loopback = host == 'localhost'
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # Numeric legacy IPv4 forms and internal metadata names are not public API hosts.
        if (not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host)
                or '..' in host or '.' not in host and host != 'localhost'
                or re.fullmatch(r'[0-9.]+', host) or host.startswith('0x')
                or host.endswith(('.internal', '.local', '.localhost'))
                or host in {'metadata', 'metadata.google.internal'}):
            raise error
    else:
        loopback = address.is_loopback
        if not loopback and not address.is_global:
            raise error
    if parsed.scheme == 'http' and not loopback:
        raise error
    netloc = f'[{host}]' if ':' in host else host
    if port is not None:
        netloc += f':{port}'
    return urlunsplit((parsed.scheme, netloc, parsed.path.rstrip('/'), '', ''))


def _normalize_model(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}', value):
        raise AISettingsError('请输入有效的 AI 模型名称')
    return value


def _read_config(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        if not isinstance(raw, dict):
            raise ValueError
        base = _normalize_url(raw.get('base_url', DEFAULT_BASE_URL))
        model = _normalize_model(raw.get('model', DEFAULT_MODEL))
        source = raw.get('key_source')
        slot = raw.get('key_slot')
        disabled = raw.get('key_disabled', False)
        if source not in {None, 'memory', 'keychain'} or not isinstance(disabled, bool):
            raise ValueError
        if slot is not None and (not isinstance(slot, str) or not re.fullmatch('[a-f0-9]{32}', slot)):
            raise ValueError
        if bool(source) != bool(slot):
            raise ValueError
        return {'provider': 'deepseek', 'base_url': base, 'model': model,
                'key_source': source, 'key_slot': slot, 'key_disabled': disabled}
    except (OSError, ValueError, TypeError):
        raise AISettingsError('无法读取 AI 配置，请检查后重试') from None


def _write_config(path: Path, config: dict) -> None:
    """Write settings atomically. The payload contains no secret material."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix='.ai-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(config, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _service(path: Path) -> str:
    profile = hashlib.sha256(str(path).encode()).hexdigest()[:32]
    return f'TeamHub.AI.{profile}'


def _read_keychain(path: Path) -> dict[str, str]:
    try:
        result = subprocess.run(
            ['/usr/bin/security', 'find-generic-password', '-a', _ACCOUNT, '-s', _service(path), '-w'],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if result.returncode == 44:
            return {}
        if result.returncode != 0:
            raise ValueError
        encoded = result.stdout.strip()
        if not encoded.startswith('v1.'):
            raise ValueError
        payload = json.loads(base64.urlsafe_b64decode(encoded[3:]).decode('utf-8'))
        if (not isinstance(payload, dict) or len(payload) > 2
                or any(not isinstance(k, str) or not re.fullmatch('[a-f0-9]{32}', k)
                       or not isinstance(v, str) or not v for k, v in payload.items())):
            raise ValueError
        return payload
    except (OSError, ValueError, subprocess.SubprocessError):
        raise AISettingsError('无法读取 AI 服务密钥，请检查系统钥匙串') from None


def _write_keychain(path: Path, payload: dict[str, str]) -> None:
    # security -i reads its command from stdin: secrets never enter process argv.
    encoded = 'v1.' + base64.urlsafe_b64encode(json.dumps(
        payload, ensure_ascii=True, separators=(',', ':'),
    ).encode()).decode('ascii')
    command = ['add-generic-password', '-U', '-a', _ACCOUNT, '-s', _service(path), '-w', encoded]
    try:
        result = subprocess.run(
            ['/usr/bin/security', '-i'], input=shlex.join(command) + '\n',
            capture_output=True, text=True, timeout=15, check=False,
        )
        if result.returncode != 0:
            raise ValueError
        # Interactive security may exit successfully despite a failed command.
        # Only a matching read-back proves the requested protected update exists.
        if _read_keychain(path) != payload:
            raise ValueError
    except (OSError, ValueError, subprocess.SubprocessError):
        raise AISettingsError('无法保存 AI 服务密钥，请检查系统钥匙串') from None


def _secret(path: Path, config: dict) -> tuple[str, str]:
    preferred = 'keychain' if _can_use_keychain() else 'memory'
    if config['key_disabled']:
        return '', preferred
    source, slot = config['key_source'], config['key_slot']
    if source == 'memory':
        return _memory_keys.get(str(path), {}).get(slot, ''), 'memory'
    if source == 'keychain':
        # A profile copied into a sandbox must not cause keychain access.
        # Check this before the cache too: sandbox isolation applies to secrets
        # already held in memory as well as calls to the operating system.
        if not _can_use_keychain():
            return '', 'keychain'
        cache_key = (str(path.resolve()), source, slot)
        key = _keychain_keys.get(cache_key)
        if key is None:
            key = _read_keychain(path).get(slot, '')
            if key:
                _keychain_keys[cache_key] = key
        return key, 'keychain'
    key = os.getenv('DEEPSEEK_API_KEY', '').strip()
    return (key, 'environment') if key else ('', preferred)


def _safe(config: dict, key: str, source: str) -> dict:
    return {'provider': 'deepseek', 'base_url': config['base_url'], 'model': config['model'],
            'has_api_key': bool(key), 'key_storage': source,
            'can_persist_key': _can_use_keychain()}


def get_settings() -> dict:
    with _lock:
        path = _config_path()
        config = _read_config(path)
        key, source = _secret(path, config)
        return _safe(config, key, source)


def get_connection() -> tuple[dict, str, str]:
    with _lock:
        path = _config_path()
        config = _read_config(path)
        key, source = _secret(path, config)
        if not key:
            raise AISettingsError('请先配置 AI 服务密钥')
        revision = hashlib.sha256(json.dumps(
            [str(path), config, hashlib.sha256(key.encode()).hexdigest()],
            sort_keys=True, ensure_ascii=True,
        ).encode()).hexdigest()
        return _safe(config, key, source), key, revision


def update_settings(base_url: str, model: str, api_key: str | None = None,
                    clear_api_key: bool = False) -> dict:
    base_url, model = _normalize_url(base_url), _normalize_model(model)
    if (api_key is not None and (not isinstance(api_key, str) or not api_key.strip()
                                or len(api_key) > 4096 or any(ord(c) < 32 for c in api_key))):
        raise AISettingsError('请输入有效的 AI 服务密钥')
    if api_key is not None and clear_api_key:
        raise AISettingsError('不能同时更换和清除 AI 服务密钥')
    new_key = api_key.strip() if api_key is not None else None
    with _lock:
        path = _config_path()
        old = _read_config(path)
        if base_url != old['base_url'] and new_key is None and not clear_api_key:
            raise AISettingsError('更换 AI 服务地址时，请重新填写对应服务的密钥')
        config = {**old, 'base_url': base_url, 'model': model}
        pending_memory = None
        if clear_api_key:
            config.update(key_disabled=True, key_source=None, key_slot=None)
        elif new_key is not None:
            slot = uuid.uuid4().hex
            source = 'keychain' if _can_use_keychain() else 'memory'
            config.update(key_disabled=False, key_source=source, key_slot=slot)
            if source == 'keychain':
                stored = _read_keychain(path)
                # Keep the committed slot until the settings switch succeeds. A
                # failed disk write cannot destroy the previous usable secret.
                payload = {k: v for k, v in stored.items() if k == old['key_slot']}
                payload[slot] = new_key
                _write_keychain(path, payload)
            else:
                pending_memory = {slot: new_key}
        try:
            _write_config(path, config)
        except (OSError, ValueError, TypeError):
            raise AISettingsError('无法保存 AI 配置，请重试') from None
        # Only a successful config commit makes prior slots obsolete. Do not
        # invalidate a working credential if either protected or disk writes fail.
        profile = str(path.resolve())
        active_slot = ((profile, 'keychain', config['key_slot'])
                       if config['key_source'] == 'keychain' and not config['key_disabled'] else None)
        for cache_key in list(_keychain_keys):
            if cache_key[0] == profile and cache_key != active_slot:
                del _keychain_keys[cache_key]
        if new_key is not None and active_slot is not None:
            _keychain_keys[active_slot] = new_key
        if pending_memory is not None:
            _memory_keys[str(path)] = pending_memory
        elif clear_api_key:
            _memory_keys.pop(str(path), None)
            if _can_use_keychain():
                try:
                    _write_keychain(path, {})
                except AISettingsError:
                    # Disabling was committed first so a failed OS cleanup can
                    # never silently reactivate a credential on the next launch.
                    raise AISettingsError('AI 密钥已停用，但系统钥匙串清理失败，请重试清除') from None
        key, source = _secret(path, config)
        return _safe(config, key, source)
