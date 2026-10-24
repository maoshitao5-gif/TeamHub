"""
云后端 HTTP 客户端工具
封装本地后端与云后端（默认 :9000）之间的 JWT 鉴权请求逻辑

使用示例：
    config = load_library_config()
    resp = cloud_req("GET", f"{get_cloud_url(config)}/api/users/me", config)
    if resp.status_code == 200:
        ...

Token 刷新策略：
    首次请求 401 时，自动用 refresh_token 换取新 access_token，
    并将新 token 写回 config.json；随后重试原请求一次。
"""
from typing import Optional
from pathlib import Path
from collections import OrderedDict
import posixpath
from urllib.parse import unquote, urlsplit
import httpx
from fastapi import HTTPException

from backend.app.database import write_json_config
from backend.app.config import settings
from backend.app.core.logger import get_logger
from backend.app.core.cloud_session import read_config_at, session_lock, refresh_lock as _refresh_lock

# 对所有 localhost / 127.0.0.1 请求（无论端口）绕过系统代理
# httpx mounts 按前缀匹配，带端口的 URL 需要精确包含端口才能匹配，
# 故同时注册不带端口和带常用端口的变体，并用通配 "all://" 兜底本机地址。
# 外网请求（如 OSS 上传）仍走系统代理，保持正常访问。
_no_proxy = httpx.HTTPTransport()
_http = httpx.Client(
    mounts={
        "http://localhost": _no_proxy,
        "http://127.0.0.1": _no_proxy,
        # httpx 支持 "all://<host>" 匹配该 host 的任意端口
        "all://localhost": _no_proxy,
        "all://127.0.0.1": _no_proxy,
    },
    timeout=30,
)

logger = get_logger("core.cloud_client")


class CloudIdentityError(ValueError):
    """The configured endpoint is not the service bound to this operation."""


def verify_server_identity(config: dict, expected_server_id: str | None = None) -> str:
    """Probe public service identity before sending credentials to a moved endpoint."""
    response = _http.get(f'{get_cloud_url(config)}/api/server-info', timeout=15)
    if response.status_code != 200:
        raise CloudIdentityError('共享服务未提供可验证身份，请先升级服务')
    data = response.json()
    server_id = data.get('server_id') if isinstance(data, dict) else None
    if not isinstance(server_id, str) or not server_id or len(server_id) > 36:
        raise CloudIdentityError('共享服务身份响应无效')
    expected = expected_server_id or config.get('cloud_server_id')
    if expected and expected != server_id:
        raise CloudIdentityError('共享服务身份已变化，未发送凭据或资料；请核对原服务')
    if data.get('protocol_major') != 1:
        raise CloudIdentityError('共享服务协议版本不兼容，请升级兼容客户端或服务')
    if 'explicit_publication_scope' not in data.get('capabilities', []):
        raise CloudIdentityError('共享服务缺少明确共享范围能力，不能使用新版共享入口')
    return server_id


def get_cloud_url(config: dict) -> str:
    """从配置中读取云后端地址（末尾不带斜杠）"""
    return config.get("cloud_api_url", "http://localhost:9000").rstrip("/")


def auth_headers(config: dict) -> dict:
    """构建带 JWT 的请求头"""
    token = config.get("cloud_access_token", "")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


_refresh_lineage = OrderedDict()


def _matching_session(config: dict, config_path: Path) -> dict | None:
    """调用方持会话锁；只接受同路径、同服务及同一对凭据。"""
    if Path(settings.config_file) != config_path:
        return None
    latest = read_config_at(config_path)
    keys = ('cloud_access_token', 'cloud_refresh_token')
    if (get_cloud_url(latest) != get_cloud_url(config)
            or any(latest.get(key) != config.get(key) for key in keys)
            or Path(settings.config_file) != config_path):
        return None
    return latest


def _expire_session(config: dict, config_path: Path) -> None:
    """仅清除此请求所属的失效云会话，个人资料及共享来源保持不变。"""
    with session_lock:
        latest = _matching_session(config, config_path)
        if latest is None:
            return
        for key in ('cloud_access_token', 'cloud_refresh_token', 'active_workspace_id', 'active_workspace_name'):
            latest.pop(key, None)
        write_json_config(config_path, latest)
    raise HTTPException(401, detail={'error': {
        'code': 'CLOUD_SESSION_EXPIRED', 'message': '云服务登录已失效，请重新登录', 'details': None,
    }})


def refresh_token(config: dict) -> Optional[dict]:
    """轮换并持久保存完整凭据；暂时故障保留会话，明确失效才请求重新登录。"""
    config_path = Path(settings.config_file)
    with _refresh_lock:
        try:
            with session_lock:
                if Path(settings.config_file) != config_path:
                    return None
                latest = read_config_at(config_path)
                if get_cloud_url(latest) != get_cloud_url(config) or not latest.get('cloud_refresh_token'):
                    return None
                keys = ('cloud_access_token', 'cloud_refresh_token')
                lineage_key = (str(config_path), get_cloud_url(config), config.get('cloud_refresh_token'))
                latest_pair = tuple(latest.get(key) for key in keys)
                if latest_pair != tuple(config.get(key) for key in keys):
                    if _refresh_lineage.get(lineage_key) != latest_pair:
                        return None
                    config.update(dict(zip(keys, latest_pair)))
                    return config
                refresh = latest['cloud_refresh_token']
            resp = _http.post(f"{get_cloud_url(config)}/api/auth/refresh",
                              json={'refresh_token': refresh}, timeout=15)
            with session_lock:
                if _matching_session(config, config_path) is None:
                    return None
            if resp.status_code == 401:
                _expire_session(config, config_path)
                return None
            if resp.status_code != 200:
                raise HTTPException(503, '云服务暂时无法续期，请稍后重试')
            data = resp.json()
            if (not isinstance(data, dict)
                    or any(not isinstance(data.get(key), str) or not data[key].strip() for key in ('access_token', 'refresh_token'))
                    or data['refresh_token'] == refresh):
                raise HTTPException(502, '云服务续期响应不完整，请确认服务已更新后重新登录')
            with session_lock:
                current = _matching_session(config, config_path)
                if current is None:
                    return None
                current.update(cloud_access_token=data['access_token'], cloud_refresh_token=data['refresh_token'])
                write_json_config(config_path, current)
            _refresh_lineage[lineage_key] = (current['cloud_access_token'], current['cloud_refresh_token'])
            if len(_refresh_lineage) > 128:
                _refresh_lineage.popitem(last=False)
            config.update({key: current[key] for key in keys})
            return config
        except httpx.HTTPError as exc:
            logger.warning('云服务暂时无法连接，保留原会话供稍后重试')
            raise HTTPException(503, '云服务暂时无法连接，请稍后重试') from exc
        except ValueError as exc:
            raise HTTPException(502, '云服务续期响应无效，请稍后重试') from exc
        except OSError as exc:
            logger.warning('云会话凭据无法写入原个人库配置')
            raise HTTPException(503, '云会话无法保存，请检查文件库后重新登录') from exc


def cloud_req(method: str, url: str, config: dict, timeout: int = 30, **kwargs) -> httpx.Response:
    """
    带 JWT 鉴权的云端 HTTP 请求，401 时自动刷新 token 并重试一次

    参数：
        method  — HTTP 方法（GET / POST / PUT / DELETE）
        url     — 完整请求 URL
        config  — 当前 config.json 字典（读取 / 更新 JWT）
        timeout — 超时秒数（默认 30，大文件上传可适当加大）
        **kwargs — 透传给 httpx.request（如 json=, content=, headers= 等）

    返回 httpx.Response（调用方自行判断状态码）
    """
    _validate_request_target(url, config)
    headers = auth_headers(config)
    resp = _http.request(method, url, headers=headers, timeout=timeout, **kwargs)
    if resp.status_code == 401:
        new_config = refresh_token(config)
        if new_config:
            config.update(new_config)
            headers = auth_headers(config)
            resp = _http.request(method, url, headers=headers, timeout=timeout, **kwargs)
    return resp


def _validate_request_target(url: str, config: dict) -> None:
    """Only the configured service origin/base path may receive its Bearer token."""
    try:
        base, target = urlsplit(get_cloud_url(config)), urlsplit(url)
        def origin(parsed):
            if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('无效共享服务地址')
            return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == 'https' else 80)
        if origin(base) != origin(target) or base.query or base.fragment or target.fragment:
            raise ValueError('请求不属于当前共享服务')
        base_path = posixpath.normpath(unquote(base.path or '/')).rstrip('/')
        target_path = posixpath.normpath(unquote(target.path or '/'))
        if base_path and target_path != base_path and not target_path.startswith(base_path + '/'):
            raise ValueError('请求超出共享服务路径范围')
        if any(ord(char) < 32 for char in url) or '\\' in unquote(target.path):
            raise ValueError('请求地址包含非法字符')
    except (TypeError, ValueError) as exc:
        raise CloudIdentityError('拒绝将共享凭据发送到当前服务范围之外') from exc
