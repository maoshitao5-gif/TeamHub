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
import httpx

from backend.app.database import save_library_config
from backend.app.core.logger import get_logger

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


def get_cloud_url(config: dict) -> str:
    """从配置中读取云后端地址（末尾不带斜杠）"""
    return config.get("cloud_api_url", "http://localhost:9000").rstrip("/")


def auth_headers(config: dict) -> dict:
    """构建带 JWT 的请求头"""
    token = config.get("cloud_access_token", "")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def refresh_token(config: dict) -> Optional[dict]:
    """
    用 refresh_token 换新 access_token（Token Rotation）

    成功：更新 config 字典并写回 config.json，返回更新后的 config
    失败：返回 None（用户需要重新登录）
    """
    refresh = config.get("cloud_refresh_token")
    if not refresh:
        return None
    cloud_url = get_cloud_url(config)
    try:
        resp = _http.post(
            f"{cloud_url}/api/auth/refresh",
            json={"refresh_token": refresh},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            config["cloud_access_token"] = data.get("access_token", "")
            if data.get("refresh_token"):
                config["cloud_refresh_token"] = data["refresh_token"]
            save_library_config(config)
            return config
    except Exception as e:
        logger.warning(f"Token 刷新失败: {e}")
    return None


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
    headers = auth_headers(config)
    resp = _http.request(method, url, headers=headers, timeout=timeout, **kwargs)
    if resp.status_code == 401:
        new_config = refresh_token(config)
        if new_config:
            config.update(new_config)
            headers = auth_headers(config)
            resp = _http.request(method, url, headers=headers, timeout=timeout, **kwargs)
    return resp
