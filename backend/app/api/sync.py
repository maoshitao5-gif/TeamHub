"""
个人端云账号会话、团队/工作空间及只读资料查询代理。

JWT 保存在个人库配置中，渲染器不持有云令牌；续期由 core.cloud_client
统一处理。设备标识只用于个人端。共享写入、取回和版本提交由 sharing
API 负责；本地与云端各自维护版本，主动操作才传递内容。

旧令牌注入、直接推拉、设备注册及加入审批通路已停用，保留兼容响应。
"""
import json
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.database import load_library_config, save_library_config, write_json_config, get_db
from backend.app.core.device import get_device_id
from backend.app.core.logger import get_logger
from backend.app.core.cloud_session import session_lock as _session_lock
from backend.app.core.cloud_client import get_cloud_url, auth_headers, cloud_req, _http, verify_server_identity, CloudIdentityError
from backend.app.api.schemas import (
    DeviceNameUpdate, CloudLoginRequest, CloudWorkspaceRequest,
)
from backend.app.models import Document, Version
from backend.app.config import settings
router = APIRouter(prefix="/api/sync", tags=["sync"])
logger = get_logger('api.sync')


_login_generation = 0


def _read_session_config(path: Path) -> dict:
    """只读捕获的配置路径，不因当前个人库改变而转读另一库。"""
    try:
        with path.open('r', encoding='utf-8') as stream:
            config = json.load(stream)
    except FileNotFoundError:
        return {}
    if not isinstance(config, dict):
        raise ValueError('个人库配置格式无效')
    return config


def _capture_session() -> tuple[dict, Path]:
    """调用方持会话锁，一起捕获个人库路径与该路径的配置。"""
    path = Path(settings.config_file)
    config = _read_session_config(path)
    if Path(settings.config_file) != path:
        raise HTTPException(409, '个人库已变化，请重试')
    return config, path


def _same_session(config: dict, path: Path, latest: dict) -> bool:
    return (Path(settings.config_file) == path and get_cloud_url(latest) == get_cloud_url(config)
            and latest.get('cloud_access_token') == config.get('cloud_access_token'))


def _latest_session_config(config: dict, path: Path) -> dict:
    """校验并返回同一原库配置；合并与写入不得再读取可变的当前库。"""
    latest = _read_session_config(path)
    if not _same_session(config, path, latest) or Path(settings.config_file) != path:
        raise HTTPException(409, '账号、服务或个人库已变化，请重试')
    return latest


def _accessible_libraries(config: dict, fetch):
    cloud_url = get_cloud_url(config)
    teams_response = fetch(f'{cloud_url}/api/teams')
    if teams_response.status_code != 200:
        raise HTTPException(teams_response.status_code, '获取组织列表失败，请重试')
    teams = teams_response.json()
    workspaces = []
    for team in teams:
        response = fetch(f"{cloud_url}/api/teams/{team['id']}/workspaces")
        if response.status_code != 200:
            raise HTTPException(response.status_code, '获取共享资料库失败，请重试')
        for workspace in response.json():
            workspaces.append({'id': workspace['id'], 'name': workspace['name'],
                               'team_id': team['id'], 'team_name': team.get('name', '')})
    return teams, workspaces


# ========== 设备信息 ==========

@router.get("/device")
def get_device_info():
    """获取当前设备标识和设备名称"""
    config = load_library_config()
    return {
        "device_id": get_device_id(),
        "device_name": config.get("device_name", "未命名设备"),
    }


@router.put("/device/name")
def update_device_name(req: DeviceNameUpdate):
    """修改设备显示名称"""
    with _session_lock:
        config, config_path = _capture_session()
        config["device_name"] = req.name.strip()
        write_json_config(config_path, config)
    return {"device_name": config["device_name"]}


# ========== Token 同步(前端登录后写入本地后端) ==========

# M1 停用:前端直连共享后端并向本地写令牌的通路已废弃(契约 §7),
# M4 起共享会话由本地服务统一管理
@router.post("/sync-token")
def sync_token_from_frontend():
    """已停用:前端写令牌到 config.json"""
    from backend.app.core.decommissioned import gone_response
    return gone_response("共享会话将由本地服务统一管理(M4),前端不再直接写入令牌。")


# ========== 云账号登录 ==========

@router.post("/login")
def cloud_login(req: CloudLoginRequest):
    """Verify service identity and authenticate without inheriting another account's library."""
    global _login_generation
    with _session_lock:
        config, config_path = _capture_session()
        _login_generation += 1
        generation = _login_generation
    cloud_url = get_cloud_url(config)
    try:
        server_id = verify_server_identity(config)
        response = _http.post(f'{cloud_url}/api/auth/login',
                              json={'email': req.email, 'password': req.password}, timeout=15)
        if response.status_code != 200:
            raise HTTPException(response.status_code, response.json().get('detail', '登录失败'))
        data = response.json()
        if not data.get('access_token'):
            raise HTTPException(502, '云服务登录响应无效，请重试')
        authenticated = {**config, 'cloud_access_token': data['access_token'],
                         'cloud_refresh_token': data.get('refresh_token', '')}
        headers = auth_headers(authenticated)
        user_response = _http.get(f'{cloud_url}/api/users/me', headers=headers, timeout=15)
        if user_response.status_code != 200 or not user_response.json().get('id'):
            raise HTTPException(user_response.status_code if user_response.status_code != 200 else 502,
                                '无法确认当前云端账号，请重新登录')
        user = user_response.json()
        teams, workspaces = _accessible_libraries(authenticated,
            lambda url: _http.get(url, headers=headers, timeout=15))
        with _session_lock:
            if generation != _login_generation:
                raise HTTPException(409, '账号、服务或个人库已变化，请重新登录')
            latest = _latest_session_config(config, config_path)
            latest.update({'cloud_access_token': authenticated['cloud_access_token'],
                           'cloud_refresh_token': authenticated['cloud_refresh_token'],
                           'cloud_server_id': server_id})
            latest.pop('active_workspace_id', None)
            latest.pop('active_workspace_name', None)
            write_json_config(config_path, latest)
        return {'user': user, 'teams': teams, 'workspaces': workspaces, 'server_id': server_id}
    except HTTPException:
        raise
    except CloudIdentityError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, '连接云服务失败，请检查地址后重试') from exc


@router.get("/workspaces")
def list_cloud_workspaces():
    """Return all accessible libraries; failures never masquerade as an empty membership."""
    config = load_library_config()
    if not config.get('cloud_access_token'):
        raise HTTPException(401, '请先登录云端账号')
    try:
        verify_server_identity(config)
        teams, workspaces = _accessible_libraries(config,
            lambda url: cloud_req('GET', url, config, timeout=15))
        return {'workspaces': workspaces, 'teams': teams}
    except HTTPException:
        raise
    except CloudIdentityError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, '获取共享资料库失败，请重试') from exc


@router.post("/logout")
def cloud_logout():
    """退出云端登录，清除本地保存的 JWT"""
    global _login_generation
    with _session_lock:
        config, config_path = _capture_session()
        _login_generation += 1
        for key in ("cloud_access_token", "cloud_refresh_token",
                    "active_workspace_id", "active_workspace_name"):
            config.pop(key, None)
        if Path(settings.config_file) != config_path:
            raise HTTPException(409, '个人库已变化，请重试退出登录')
        try:
            write_json_config(config_path, config)
        except OSError as exc:
            logger.exception('退出云端登录时无法保存原个人库配置')
            raise HTTPException(500, '退出登录未完成：无法保存个人库配置，请重试') from exc
    return {"message": "已退出登录"}


# ========== 绑定工作空间 ==========

@router.post("/workspace")
def set_workspace(req: CloudWorkspaceRequest, db: Session = Depends(get_db)):
    """Persist an accessible library after verification; empty ID explicitly clears selection."""
    with _session_lock:
        config, config_path = _capture_session()
    workspace_name = req.workspace_name
    if req.workspace_id:
        if not config.get('cloud_access_token'):
            raise HTTPException(401, '请先登录云端账号')
        try:
            verify_server_identity(config)
            response = cloud_req('GET', f"{get_cloud_url(config)}/api/workspaces/{req.workspace_id}/collaboration-context", config)
        except CloudIdentityError as exc:
            raise HTTPException(409, str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(502, '云服务不可达，未切换资料库') from exc
        if response.status_code != 200:
            raise HTTPException(response.status_code, '资料库不存在或无权访问')
        workspace_name = response.json().get('workspace_name') or workspace_name
    with _session_lock:
        latest = _latest_session_config(config, config_path)
        latest['active_workspace_id'] = req.workspace_id
        latest['active_workspace_name'] = workspace_name
        write_json_config(config_path, latest)
    return {'workspace_id': req.workspace_id, 'workspace_name': workspace_name}


# ========== 同步状态统计 ==========

@router.get("/status")
def get_sync_status(db: Session = Depends(get_db)):
    """
    查询本地同步状态统计（不调用云端，静默可用）
    """
    with _session_lock:
        config, config_path = _capture_session()
    logged_in = bool(config.get("cloud_access_token"))
    ws_id = config.get("active_workspace_id")
    ws_name = config.get("active_workspace_name")

    # 用户信息（尝试从云端获取，失败则跳过）
    user = None
    server_id = config.get("cloud_server_id")
    if logged_in:
        try:
            server_id = verify_server_identity(config)
            cloud_url = get_cloud_url(config)
            resp = cloud_req("GET", f"{cloud_url}/api/users/me", config, timeout=5)
            if resp.status_code == 200:
                user = resp.json()
                if user.get('id') and not config.get('cloud_server_id'):
                    with _session_lock:
                        latest = _latest_session_config(config, config_path)
                        latest['cloud_server_id'] = server_id
                        write_json_config(config_path, latest)
        except HTTPException as exc:
            if exc.status_code == 401 and isinstance(exc.detail, dict) and exc.detail.get('error', {}).get('code') == 'CLOUD_SESSION_EXPIRED':
                raise
            logger.warning('云端会话信息暂不可验证，保留本机状态供稍后重试')
        except (httpx.HTTPError, ValueError, OSError):
            logger.warning('云端会话信息暂不可验证，保留本机状态供稍后重试')

    result = {
        "logged_in": logged_in,
        "server_id": server_id,
        "workspace_id": ws_id,
        "workspace_name": ws_name,
        "user": user,
    }

    if ws_id:
        # 已推送文档数
        pushed = db.query(Document).filter(
            Document.workspace_id == ws_id,
            Document.cloud_pushed_at.isnot(None),
            Document.status != "trashed",
        ).count()

        modified_count = (
            db.query(Document)
            .join(Version, (Version.document_id == Document.id) & (Version.id == Document.current_version_id))
            .filter(
                Document.workspace_id == ws_id,
                Document.cloud_pushed_at.isnot(None),
                Document.cloud_hash.isnot(None),
                Document.status != "trashed",
                Version.sha256_hash.isnot(None),
                Version.sha256_hash != Document.cloud_hash,
            )
            .count()
        )

        result.update({
            "pushed": pushed,
            "modified": modified_count,
        })

    return result


# ========== 推送单个文档到云端 ==========

# M1 停用:通用双向同步写入通路已废弃(契约 §7),M4 由唯一发布通路替代
@router.post("/push-doc")
def push_doc():
    """已停用:推送文档到云端(通用同步通路)"""
    from backend.app.core.decommissioned import gone_response
    return gone_response("发布能力将在 M4 由唯一的发布/取回通路提供。")


# ========== 从云端拉取单个文档到本地 ==========

@router.post("/pull-doc")
def pull_doc():
    """已停用:从云端拉取文档(通用同步通路)"""
    from backend.app.core.decommissioned import gone_response
    return gone_response("取回能力将在 M4 由唯一的发布/取回通路提供。")


# ========== 检查文档本地与云端哈希差异 ==========

@router.post("/check-doc")
def check_doc():
    """已停用:本地与云端哈希差异检查(通用同步通路)"""
    from backend.app.core.decommissioned import gone_response
    return gone_response("内容查重能力将在 M2/M4 由新契约接口提供。")


# ========== 列出云端文档的版本历史 ==========

@router.get("/cloud-docs/{doc_id}/versions")
def list_cloud_doc_versions(doc_id: str, workspace_id: Optional[str] = None):
    """
    列出云端某文档的所有版本历史（供云仓库页展示）。
    """
    config = load_library_config()
    if not config.get("cloud_access_token"):
        raise HTTPException(status_code=401, detail="请先登录云端账号")

    ws_id = workspace_id or config.get("active_workspace_id")
    if not ws_id:
        raise HTTPException(status_code=400, detail="请选择共享资料库")

    cloud_url = get_cloud_url(config)
    try:
        resp = cloud_req(
            "GET",
            f"{cloud_url}/api/workspaces/{ws_id}/documents/{doc_id}/versions",
            config, timeout=15,
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail="获取版本历史失败")
        return resp.json()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"连接云端失败: {e}")


# ========== 获取云端某版本的下载链接 ==========

@router.get("/cloud-docs/{doc_id}/versions/{version_id}/download-url")
def get_cloud_version_download_url(doc_id: str, version_id: str, workspace_id: Optional[str] = None):
    """旧 OSS 下载链接入口只返回停用信封；保留 URL 兼容性。"""
    from backend.app.core.decommissioned import gone_response
    return gone_response("M4 已使用受管对象下载，请通过 /api/sharing 取回指定版本。")


# ========== 团队发现与加入申请（透传代理） ==========

@router.get("/teams/discover")
def discover_teams(q: str = ""):
    """透传：搜索可加入的团队"""
    config = load_library_config()
    if not config.get("cloud_access_token"):
        raise HTTPException(status_code=401, detail="请先登录云端账号")
    cloud_url = get_cloud_url(config)
    resp = cloud_req("GET", f"{cloud_url}/api/teams/discover", config, params={"q": q}, timeout=15)
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.json().get("detail", "请求失败"))
    return resp.json()


@router.post("/join-requests")
def create_join_request(body: dict):
    """透传：提交加入团队申请"""
    config = load_library_config()
    if not config.get("cloud_access_token"):
        raise HTTPException(status_code=401, detail="请先登录云端账号")
    cloud_url = get_cloud_url(config)
    resp = cloud_req("POST", f"{cloud_url}/api/teams/join-requests", config, json=body, timeout=15)
    if resp.status_code not in (200, 201):
        raise HTTPException(status_code=resp.status_code, detail=resp.json().get("detail", "请求失败"))
    return resp.json()


@router.get("/join-requests/my")
def my_join_requests():
    """透传：查看自己的所有申请"""
    config = load_library_config()
    if not config.get("cloud_access_token"):
        raise HTTPException(status_code=401, detail="请先登录云端账号")
    cloud_url = get_cloud_url(config)
    resp = cloud_req("GET", f"{cloud_url}/api/teams/join-requests/my", config, timeout=15)
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.json().get("detail", "请求失败"))
    return resp.json()


@router.get("/join-requests/pending-count")
def pending_join_request_count():
    """透传：管理员待审申请总数"""
    config = load_library_config()
    if not config.get("cloud_access_token"):
        raise HTTPException(status_code=401, detail="请先登录云端账号")
    cloud_url = get_cloud_url(config)
    resp = cloud_req("GET", f"{cloud_url}/api/teams/join-requests/pending-count", config, timeout=15)
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.json().get("detail", "请求失败"))
    return resp.json()


@router.get("/teams/{team_id}/join-requests")
def list_team_join_requests(team_id: str, status: str = "pending"):
    """透传：管理员查看某团队的申请列表"""
    config = load_library_config()
    if not config.get("cloud_access_token"):
        raise HTTPException(status_code=401, detail="请先登录云端账号")
    cloud_url = get_cloud_url(config)
    resp = cloud_req(
        "GET", f"{cloud_url}/api/teams/{team_id}/join-requests", config,
        params={"status": status}, timeout=15,
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.json().get("detail", "请求失败"))
    return resp.json()


@router.post("/teams/{team_id}/join-requests/{request_id}/review")
def review_join_request(team_id: str, request_id: str, body: dict):
    """透传：审批加入申请"""
    config = load_library_config()
    if not config.get("cloud_access_token"):
        raise HTTPException(status_code=401, detail="请先登录云端账号")
    cloud_url = get_cloud_url(config)
    resp = cloud_req(
        "POST", f"{cloud_url}/api/teams/{team_id}/join-requests/{request_id}/review",
        config, json=body, timeout=15,
    )
    if resp.status_code not in (200, 201):
        raise HTTPException(status_code=resp.status_code, detail=resp.json().get("detail", "请求失败"))
    return resp.json()


# ========== 服务端搜索（透传代理） ==========

@router.get("/docs/search")
def search_cloud_docs(q: str = "", tags: str = "", tag_ids: str = "", page: int = 1, page_size: int = 20, workspace_id: Optional[str] = None):
    """透传：服务端文档搜索"""
    config = load_library_config()
    if not config.get("cloud_access_token"):
        raise HTTPException(status_code=401, detail="请先登录云端账号")
    ws_id = workspace_id or config.get("active_workspace_id")
    if not ws_id:
        raise HTTPException(status_code=400, detail="请选择共享资料库")
    cloud_url = get_cloud_url(config)
    resp = cloud_req(
        "GET", f"{cloud_url}/api/workspaces/{ws_id}/documents/search", config,
        params={"q": q, "tags": tags, "tag_ids": tag_ids, "page": page, "page_size": page_size},
        timeout=15,
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.json().get("detail", "搜索失败"))
    return resp.json()


# ========== 活动流（透传代理） ==========

@router.get("/workspace/activity")
def get_workspace_activity(workspace_id: Optional[str] = None, limit: int = 30):
    """透传：工作空间活动流"""
    config = load_library_config()
    if not config.get("cloud_access_token"):
        raise HTTPException(status_code=401, detail="请先登录云端账号")
    ws_id = workspace_id or config.get("active_workspace_id")
    if not ws_id:
        raise HTTPException(status_code=400, detail="请选择共享资料库")
    cloud_url = get_cloud_url(config)
    resp = cloud_req(
        "GET", f"{cloud_url}/api/workspaces/{ws_id}/activity", config,
        params={"limit": limit}, timeout=15,
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.json().get("detail", "获取活动流失败"))
    return resp.json()


# ========== 列出云端工作空间的所有文档 ==========

@router.get("/cloud-docs")
def list_cloud_docs(workspace_id: Optional[str] = None):
    """
    列出云端工作空间的所有文档（供云仓库页展示）。
    workspace_id 缺省时读 config.json 中绑定的活跃工作空间。
    """
    config = load_library_config()
    if not config.get("cloud_access_token"):
        raise HTTPException(status_code=401, detail="请先登录云端账号")

    ws_id = workspace_id or config.get("active_workspace_id")
    if not ws_id:
        raise HTTPException(status_code=400, detail="请选择共享资料库")

    cloud_url = get_cloud_url(config)
    try:
        resp = cloud_req(
            "GET",
            f"{cloud_url}/api/workspaces/{ws_id}/documents",
            config, timeout=15,
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail="获取云端文档列表失败")
        return {"workspace_id": ws_id, "documents": resp.json()}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"连接云端失败: {e}")


# ========== M4:团队/空间代理(前端不直连共享后端,契约 §7/V-13) ==========

def _cloud_proxy(method: str, path: str, **kwargs):
    """薄代理:带本地保存的 JWT 转发到共享服务,错误原样透传"""
    config = load_library_config()
    if not config.get("cloud_access_token"):
        raise HTTPException(401, "未登录云端")
    resp = cloud_req(method, f"{get_cloud_url(config)}{path}", config, timeout=30, **kwargs)
    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except ValueError:
            detail = resp.text
        raise HTTPException(resp.status_code, detail)
    return resp.json() if resp.content else {"message": "ok"}


@router.get("/teams")
def proxy_list_teams():
    """我的团队列表"""
    return _cloud_proxy("GET", "/api/teams")


@router.post("/teams")
def proxy_create_team(body: dict):
    """创建团队"""
    return _cloud_proxy("POST", "/api/teams", json=body)


@router.get("/teams/{team_id}/workspaces")
def proxy_team_workspaces(team_id: str):
    """团队下工作空间列表"""
    return _cloud_proxy("GET", f"/api/teams/{team_id}/workspaces")


@router.post("/teams/{team_id}/workspaces")
def proxy_create_workspace(team_id: str, body: dict):
    """创建工作空间"""
    return _cloud_proxy("POST", f"/api/teams/{team_id}/workspaces", json=body)


@router.get("/teams/{team_id}/members")
def proxy_team_members(team_id: str):
    """团队成员列表"""
    return _cloud_proxy("GET", f"/api/teams/{team_id}/members")


@router.post("/teams/{team_id}/members")
def proxy_invite_member(team_id: str, body: dict):
    """邀请成员"""
    return _cloud_proxy("POST", f"/api/teams/{team_id}/members", json=body)


@router.delete("/teams/{team_id}/members/{user_id}")
def proxy_remove_member(team_id: str, user_id: str):
    """移除成员"""
    return _cloud_proxy("DELETE", f"/api/teams/{team_id}/members/{user_id}")
