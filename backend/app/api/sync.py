"""
本地后端 — 同步 API（简化版）
层：本地后端（FastAPI，端口 8001）

端点概览：
  GET  /api/sync/device              — 查询设备 ID 与名称
  PUT  /api/sync/device/name         — 修改设备显示名称
  POST /api/sync/sync-token          — 前端登录后同步 token 到本地后端
  POST /api/sync/login               — 云账号登录，保存 JWT 到 config.json
  POST /api/sync/logout              — 退出登录，清除 JWT
  GET  /api/sync/workspaces          — 获取已登录账号下的工作空间列表
  POST /api/sync/workspace           — 绑定云端工作空间
  GET  /api/sync/status              — 查询本地同步状态统计
  POST /api/sync/push-doc            — 推送单个文档到云端（含文件上传）
  POST /api/sync/pull-doc            — 从云端拉取单个文档到本地
  POST /api/sync/check-doc           — 检查文档本地与云端哈希差异
  GET  /api/sync/cloud-docs          — 列出云端工作空间的所有文档
  GET  /api/sync/cloud-docs/{doc_id}/versions/{ver_id}/download-url — 获取历史版本下载链接

架构说明：
  本地后端通过 httpx 调用云后端（:9000），JWT 对前端透明。
  Token 刷新由 core.cloud_client 统一处理。
  版本管理仅在云端：每次推送自动归档旧版本。
"""
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.database import load_library_config, save_library_config, get_db
from backend.app.core.device import get_device_id
from backend.app.core.cloud_client import get_cloud_url, auth_headers, cloud_req, _http
from backend.app.core.file_utils import calculate_sha256, get_document_or_404
from backend.app.api.documents import _get_or_create_tags
from backend.app.api.schemas import (
    DeviceNameUpdate, CloudLoginRequest, CloudWorkspaceRequest,
    SyncTokenRequest, PushDocRequest, PullDocRequest, CheckDocRequest,
)
from backend.app.models import Document, Version, utc_now, generate_uuid
from backend.app.core.logger import get_logger
from backend.app.config import settings

logger = get_logger("sync")

router = APIRouter(prefix="/api/sync", tags=["sync"])


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
    config = load_library_config()
    config["device_name"] = req.name.strip()
    save_library_config(config)
    return {"device_name": config["device_name"]}


# ========== Token 同步（前端登录后写入本地后端） ==========

@router.post("/sync-token")
def sync_token_from_frontend(req: SyncTokenRequest):
    """前端登录成功后将 token 同步写入 config.json（保持两端登录态一致）"""
    config = load_library_config()
    config["cloud_access_token"] = req.access_token
    config["cloud_refresh_token"] = req.refresh_token
    save_library_config(config)
    return {"ok": True}


# ========== 云账号登录 ==========

@router.post("/login")
def cloud_login(req: CloudLoginRequest):
    """登录云端账号，保存 JWT 到 config.json，返回用户信息和工作空间列表"""
    config = load_library_config()
    cloud_url = get_cloud_url(config)

    try:
        resp = _http.post(
            f"{cloud_url}/api/auth/login",
            json={"email": req.email, "password": req.password},
            timeout=15,
        )
        if resp.status_code != 200:
            detail = resp.json().get("detail", "登录失败")
            raise HTTPException(status_code=resp.status_code, detail=detail)
        data = resp.json()

        config["cloud_access_token"] = data.get("access_token", "")
        config["cloud_refresh_token"] = data.get("refresh_token", "")
        save_library_config(config)

        # 注册/更新设备
        device_id = get_device_id()
        device_name = config.get("device_name", "未命名设备")
        headers = auth_headers(config)
        try:
            _http.post(
                f"{cloud_url}/api/devices/register",
                json={"device_id": device_id, "device_name": device_name, "platform": "desktop"},
                headers=headers,
                timeout=15,
            )
        except Exception as e:
            logger.warning(f"设备注册失败（非致命）: {e}")

        user_resp = _http.get(f"{cloud_url}/api/users/me", headers=headers, timeout=15)
        user = user_resp.json() if user_resp.status_code == 200 else {}

        teams_resp = _http.get(f"{cloud_url}/api/teams/", headers=headers, timeout=15)
        teams = teams_resp.json() if teams_resp.status_code == 200 else []

        workspaces = []
        for team in teams:
            ws_resp = _http.get(
                f"{cloud_url}/api/teams/{team['id']}/workspaces",
                headers=headers, timeout=15,
            )
            if ws_resp.status_code == 200:
                for ws in ws_resp.json():
                    workspaces.append({
                        "id": ws["id"],
                        "name": ws["name"],
                        "team_id": team["id"],
                        "team_name": team.get("name", ""),
                    })

        return {"user": user, "teams": teams, "workspaces": workspaces}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"连接云端失败: {e}")


@router.get("/workspaces")
def list_cloud_workspaces():
    """已登录状态下拉取所有可用工作空间列表（自动刷新 token）"""
    config = load_library_config()
    if not config.get("cloud_access_token"):
        raise HTTPException(status_code=401, detail="未登录云端")
    cloud_url = get_cloud_url(config)
    try:
        teams_resp = cloud_req("GET", f"{cloud_url}/api/teams", config, timeout=15)
        if teams_resp.status_code != 200:
            raise HTTPException(status_code=teams_resp.status_code, detail="获取团队列表失败")
        teams = teams_resp.json()
        workspaces = []
        for team in teams:
            ws_resp = cloud_req(
                "GET",
                f"{cloud_url}/api/teams/{team['id']}/workspaces",
                config, timeout=15,
            )
            if ws_resp.status_code == 200:
                for ws in ws_resp.json():
                    workspaces.append({
                        "id": ws["id"],
                        "name": ws["name"],
                        "team_id": team["id"],
                        "team_name": team.get("name", ""),
                    })
        return {"workspaces": workspaces}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"连接云端失败: {e}")


@router.post("/logout")
def cloud_logout():
    """退出云端登录，清除本地保存的 JWT"""
    config = load_library_config()
    for key in ("cloud_access_token", "cloud_refresh_token",
                "active_workspace_id", "active_workspace_name"):
        config.pop(key, None)
    save_library_config(config)
    return {"message": "已退出登录"}


# ========== 绑定工作空间 ==========

@router.post("/workspace")
def set_workspace(req: CloudWorkspaceRequest):
    """绑定当前工作空间"""
    config = load_library_config()
    config["active_workspace_id"] = req.workspace_id
    config["active_workspace_name"] = req.workspace_name
    save_library_config(config)
    return {
        "workspace_id": req.workspace_id,
        "workspace_name": req.workspace_name,
    }


# ========== 同步状态统计 ==========

@router.get("/status")
def get_sync_status(db: Session = Depends(get_db)):
    """
    查询本地同步状态统计（不调用云端，静默可用）
    """
    config = load_library_config()
    logged_in = bool(config.get("cloud_access_token"))
    ws_id = config.get("active_workspace_id")
    ws_name = config.get("active_workspace_name")

    # 用户信息（尝试从云端获取，失败则跳过）
    user = None
    if logged_in:
        try:
            cloud_url = get_cloud_url(config)
            resp = cloud_req("GET", f"{cloud_url}/api/users/me", config, timeout=5)
            if resp.status_code == 200:
                user = resp.json()
        except Exception:
            pass

    result = {
        "logged_in": logged_in,
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
            .join(Version, (Version.document_id == Document.id) & (Version.is_current == True))
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


# ========== 内部工具函数 ==========

def _compute_sha256(file_path: str) -> Optional[str]:
    """计算文件 SHA-256 哈希（失败返回 None）"""
    try:
        return calculate_sha256(file_path)
    except Exception as e:
        logger.warning(f"哈希计算失败 {file_path}: {e}")
        return None



def _place_file(src: Path, target_dir: Path, filename: str, library_path: Path) -> tuple[Path, str]:
    """将文件从 inbox 移动到目标目录，返回 (最终路径, 相对路径)。始终以 filename 为目标文件名。"""
    dest = target_dir / filename
    counter = 1
    while dest.exists():
        stem = Path(filename).stem
        suffix = Path(filename).suffix
        dest = target_dir / f"{stem}_{counter}{suffix}"
        counter += 1
    src.rename(dest)
    try:
        rel = str(dest.relative_to(library_path))
    except ValueError:
        rel = str(dest)
    return dest, rel


# ========== 推送单个文档到云端 ==========

@router.post("/push-doc")
def push_doc(req: PushDocRequest, db: Session = Depends(get_db)):
    """
    推送单个文档到云端。
    1. 获取本地当前版本文件，计算哈希
    2. 向云后端申请预签名上传 URL
    3. 直接 PUT 文件到 OSS
    4. 调云后端 POST /push，云端自动归档旧版本
    5. 更新本地 cloud_pushed_at / cloud_hash / workspace_id
    """
    config = load_library_config()
    if not config.get("cloud_access_token"):
        raise HTTPException(status_code=401, detail="请先登录云端账号")

    ws_id = config.get("active_workspace_id")
    if not ws_id:
        raise HTTPException(status_code=400, detail="请先在设置中绑定工作空间")

    doc = get_document_or_404(db, req.doc_id)

    cloud_url = get_cloud_url(config)
    device_id = get_device_id()
    now = utc_now()

    # 获取当前版本
    cur_ver = next((v for v in doc.versions if v.is_current), None)
    if not doc.is_folder and cur_ver is None:
        raise HTTPException(status_code=400, detail="文档没有当前版本，无法推送")

    oss_key = None
    sha256_hash = None
    file_size = 0
    original_filename = doc.name

    if not doc.is_folder and cur_ver:
        file_path = Path(cur_ver.file_path) if cur_ver.file_path else None
        if not file_path or not file_path.exists():
            raise HTTPException(status_code=400, detail="版本文件不存在，无法推送")

        original_filename = cur_ver.original_filename or file_path.name
        file_size = file_path.stat().st_size

        # 始终从磁盘重算哈希（避免文件被外部修改后 version.sha256_hash 过期的问题）
        sha256_hash = _compute_sha256(str(file_path))
        if sha256_hash is None:
            raise HTTPException(status_code=500, detail="文件哈希计算失败，请检查文件是否可读")

        # 同步更新 version 记录的哈希（反映文件当前真实状态）
        if cur_ver.sha256_hash != sha256_hash:
            cur_ver.sha256_hash = sha256_hash

        # 哈希去重：与上次推送 hash 一致，说明文件未变化
        if doc.cloud_hash and doc.cloud_hash == sha256_hash:
            raise HTTPException(
                status_code=409,
                detail="文件内容未发生变化（hash 一致），无需重复推送"
            )

        # 申请上传预签名 URL
        # 每次推送使用新 UUID 作为 version_id，确保 oss_key 唯一，
        # 避免不同推送的文件落在同一 OSS 路径互相覆盖。
        version_id = generate_uuid()
        presign_resp = cloud_req(
            "POST", f"{cloud_url}/api/storage/presign-upload", config,
            json={
                "workspace_id": ws_id,
                "document_id": doc.id,
                "version_id": version_id,
                "filename": original_filename,
                "content_type": "application/octet-stream",
            },
            timeout=15,
        )
        if presign_resp.status_code != 200:
            detail = presign_resp.json().get("detail", "获取上传地址失败")
            raise HTTPException(status_code=502, detail=detail)

        presign_data = presign_resp.json()
        upload_url = presign_data["upload_url"]
        oss_key = presign_data["oss_key"]

        # 直传文件到 OSS（Content-Type 必须与预签名时一致，否则签名校验失败返回 400）
        try:
            with open(str(file_path), "rb") as f:
                put_resp = _http.put(
                    upload_url,
                    content=f,
                    headers={"Content-Type": "application/octet-stream"},
                    timeout=300,
                )
            if put_resp.status_code not in (200, 204):
                raise HTTPException(status_code=502, detail=f"文件上传失败（HTTP {put_resp.status_code}）")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"文件上传异常: {e}")

    # 准备标签列表
    tag_names = [t.name for t in doc.tags]

    # 调云后端推送接口（创建/更新文档，自动归档旧版本）
    push_payload = {
        "device_id": device_id,
        "name": doc.name,
        "description": doc.description or "",
        "is_folder": doc.is_folder,
        "status": doc.status,
        "file_count": doc.file_count,
        "total_size": doc.total_size,
        "tags": tag_names,
        "oss_key": oss_key,
        "sha256_hash": sha256_hash,
        "file_size": file_size,
        "original_filename": original_filename,
        "version_note": req.version_note or "",
    }

    push_resp = cloud_req(
        "POST",
        f"{cloud_url}/api/workspaces/{ws_id}/documents/{doc.id}/push",
        config,
        json=push_payload,
        timeout=30,
    )
    if push_resp.status_code not in (200, 201):
        detail = push_resp.json().get("detail", "云端推送失败")
        raise HTTPException(status_code=502, detail=detail)

    # 更新本地状态
    doc.workspace_id = ws_id
    doc.cloud_pushed_at = now
    doc.cloud_hash = sha256_hash
    if cur_ver:
        cur_ver.last_synced_at = now
    db.commit()

    return {
        "message": f"已推送「{doc.name}」到云端",
        "doc_id": doc.id,
        "cloud_pushed_at": now.isoformat(),
    }


# ========== 从云端拉取单个文档到本地 ==========

@router.post("/pull-doc")
def pull_doc(req: PullDocRequest, db: Session = Depends(get_db)):
    """
    从云端拉取单个文档到本地。
    1. 获取云端文档元数据 + 当前版本信息
    2. 下载文件到本地 inbox
    3. 查本地有无同 cloud_doc_id 的文档
       - 有：追加新本地版本（is_current=True）
       - 无：新建文档
    4. 合并标签到本地标签库
    """
    config = load_library_config()
    if not config.get("cloud_access_token"):
        raise HTTPException(status_code=401, detail="请先登录云端账号")

    if not settings.library_path:
        raise HTTPException(status_code=400, detail="文件库路径未配置")

    cloud_url = get_cloud_url(config)
    now = utc_now()

    # 获取云端文档信息
    doc_resp = cloud_req(
        "GET",
        f"{cloud_url}/api/workspaces/{req.workspace_id}/documents/{req.cloud_doc_id}",
        config, timeout=15,
    )
    if doc_resp.status_code == 404:
        raise HTTPException(status_code=404, detail="云端文档不存在")
    if doc_resp.status_code != 200:
        raise HTTPException(status_code=502, detail="获取云端文档信息失败")

    cloud_doc = doc_resp.json()

    # 获取云端当前版本
    ver_resp = cloud_req(
        "GET",
        f"{cloud_url}/api/workspaces/{req.workspace_id}/documents/{req.cloud_doc_id}/versions",
        config, timeout=15,
    )
    cloud_versions = ver_resp.json() if ver_resp.status_code == 200 else []
    current_cloud_ver = next((v for v in cloud_versions if v.get("is_current")), None)

    # 先取云端当前版本的基本信息（用于去重判断，不需要下载文件）
    sha256_hash = current_cloud_ver.get("sha256_hash") if current_cloud_ver else None
    original_filename = (
        (current_cloud_ver.get("original_filename") if current_cloud_ver else None)
        or cloud_doc.get("name", "unknown")
    )

    # 查本地是否已有该云端文档
    # 优先按 cloud_doc_id 匹配；若未命中，再按 id 匹配
    # （本机推送的文档 id == cloud_doc_id，但 cloud_doc_id 字段可能尚未回写）
    local_doc = (
        db.query(Document).filter(Document.cloud_doc_id == req.cloud_doc_id).first()
        or db.query(Document).filter(Document.id == req.cloud_doc_id).first()
    )

    # 去重：本地 hash 与云端当前版本 hash 一致，无需重复拉取
    # 去重：仅当本地实体文件存在且 hash 与云端一致时才阻止重复拉取
    if local_doc and sha256_hash and local_doc.cloud_hash == sha256_hash:
        cur_ver = next((v for v in local_doc.versions if v.is_current), None)
        local_file_exists = bool(cur_ver and cur_ver.file_path and Path(cur_ver.file_path).exists())
        if local_file_exists:
            raise HTTPException(
                status_code=409,
                detail="本地文件与云端版本内容一致（hash 相同），无需重复拉取"
            )

    # 下载文件（非文件夹才下载）
    local_file_path = None
    file_size = 0

    if not cloud_doc.get("is_folder") and current_cloud_ver and current_cloud_ver.get("oss_key"):
        oss_key = current_cloud_ver["oss_key"]

        # 获取下载预签名 URL
        dl_resp = cloud_req(
            "POST", f"{cloud_url}/api/storage/presign-download", config,
            json={"oss_key": oss_key},
            timeout=15,
        )
        if dl_resp.status_code != 200:
            raise HTTPException(status_code=502, detail="获取下载地址失败")

        download_url = dl_resp.json()["download_url"]

        # 下载到 inbox（临时落盘，随后移动到目标目录）
        inbox_dir = Path(settings.library_path) / ".teamhub" / "inbox"
        inbox_dir.mkdir(parents=True, exist_ok=True)
        # inbox 内使用带计数器的临时名避免冲突，不影响最终文件名
        inbox_tmp = inbox_dir / original_filename
        counter = 1
        while inbox_tmp.exists():
            stem = Path(original_filename).stem
            suffix = Path(original_filename).suffix
            inbox_tmp = inbox_dir / f"{stem}_{counter}{suffix}"
            counter += 1

        try:
            with _http.stream("GET", download_url, timeout=300) as r:
                if r.status_code != 200:
                    raise HTTPException(status_code=502, detail="文件下载失败")
                with open(str(inbox_tmp), "wb") as f:
                    for chunk in r.iter_bytes(chunk_size=65536):
                        f.write(chunk)
            file_size = inbox_tmp.stat().st_size
            local_file_path = inbox_tmp
        except HTTPException:
            inbox_tmp.unlink(missing_ok=True)
            raise
        except Exception as e:
            inbox_tmp.unlink(missing_ok=True)
            raise HTTPException(status_code=502, detail=f"文件下载异常: {e}")

    # 云端 tags 可能是对象列表 [{id, name, ...}] 或字符串列表 ["标签名"]，统一转为字符串列表
    raw_tags = cloud_doc.get("tags", [])
    tag_names = [t.get("name") if isinstance(t, dict) else t for t in raw_tags if t]
    tag_objects = _get_or_create_tags(db, tag_names)

    # 确定目标存储目录（两条路径都要用）
    library_path = Path(settings.library_path)
    if req.target_dir:
        target_dir = library_path / req.target_dir
    else:
        target_dir = library_path
    target_dir.mkdir(parents=True, exist_ok=True)


    if local_doc:
        # ── 更新已有文档 ──────────────────────────────────
        local_doc.name = cloud_doc.get("name", local_doc.name)
        local_doc.description = cloud_doc.get("description", local_doc.description)
        local_doc.updated_at = now
        local_doc.cloud_pushed_at = now
        local_doc.cloud_hash = sha256_hash
        local_doc.workspace_id = req.workspace_id
        # 恢复状态（如曾被移入回收站）
        local_doc.status = "organized"
        local_doc.trashed_at = None
        # 补写 cloud_doc_id（本机推送时未回填，后续按此字段匹配）
        if not local_doc.cloud_doc_id:
            local_doc.cloud_doc_id = req.cloud_doc_id
        # 合并标签（新增，不删除本地已有标签）
        existing_tag_names = {t.name for t in local_doc.tags}
        for t in tag_objects:
            if t.name not in existing_tag_names:
                local_doc.tags.append(t)

        if local_file_path:
            # 将文件从 inbox 移动到目标目录
            dest_path, rel_path = _place_file(local_file_path, target_dir, original_filename, library_path)

            # 更新文档路径与大小
            local_doc.storage_path = rel_path
            local_doc.total_size = file_size

            # 将现有 is_current 版本改为历史
            db.query(Version).filter(
                Version.document_id == local_doc.id,
                Version.is_current == True,
            ).update({"is_current": False})

            ver_count = db.query(Version).filter(Version.document_id == local_doc.id).count()
            new_ver = Version(
                id=generate_uuid(),
                document_id=local_doc.id,
                version_number=ver_count + 1,
                file_path=str(dest_path),
                sha256_hash=sha256_hash,
                file_size=file_size,
                original_filename=original_filename,
                is_current=True,
                note="从云端拉取",
                last_synced_at=now,
            )
            db.add(new_ver)

        db.commit()
        return {
            "message": f"已更新本地文档「{local_doc.name}」",
            "doc_id": local_doc.id,
            "action": "updated",
        }

    else:
        # ── 新建文档 ───────────────────────────────────────
        doc_name = cloud_doc.get("name", "未命名")
        new_doc = Document(
            id=req.cloud_doc_id,   # 与云端 ID 保持一致
            name=doc_name,
            description=cloud_doc.get("description", ""),
            is_folder=cloud_doc.get("is_folder", False),
            status="organized",
            file_count=cloud_doc.get("file_count", 1),
            total_size=cloud_doc.get("total_size", file_size),
            storage_mode="copy",
            workspace_id=req.workspace_id,
            cloud_doc_id=req.cloud_doc_id,
            cloud_pushed_at=now,
            cloud_hash=sha256_hash,
            created_at=now,
            updated_at=now,
        )
        for t in tag_objects:
            new_doc.tags.append(t)
        db.add(new_doc)
        db.flush()

        if local_file_path:
            # 将文件从 inbox 移动到目标目录
            dest_path, rel_path = _place_file(local_file_path, target_dir, original_filename, library_path)

            new_doc.storage_path = rel_path
            new_doc.total_size = file_size

            ver = Version(
                id=generate_uuid(),
                document_id=new_doc.id,
                version_number=1,
                file_path=str(dest_path),
                sha256_hash=sha256_hash,
                file_size=file_size,
                original_filename=original_filename,
                is_current=True,
                note="从云端拉取",
                last_synced_at=now,
            )
            db.add(ver)

        db.commit()
        return {
            "message": f"已拉取文档「{doc_name}」到本地",
            "doc_id": new_doc.id,
            "action": "created",
        }


# ========== 检查文档本地与云端哈希差异 ==========

@router.post("/check-doc")
def check_doc(req: CheckDocRequest, db: Session = Depends(get_db)):
    """
    检查单个文档本地与云端哈希是否一致。
    返回：local_hash、cloud_hash、状态描述。
    """
    config = load_library_config()

    doc = get_document_or_404(db, req.doc_id)

    if not doc.cloud_pushed_at:
        return {
            "doc_id": doc.id,
            "status": "not_pushed",
            "message": "该文档从未推送到云端",
            "local_hash": None,
            "cloud_hash": None,
        }

    # 始终从磁盘重算本地哈希（避免 Version.sha256_hash 缓存过期导致误判）
    cur_ver = next((v for v in doc.versions if v.is_current), None)
    local_hash = None
    if cur_ver and cur_ver.file_path and Path(cur_ver.file_path).exists():
        local_hash = _compute_sha256(cur_ver.file_path)
        if local_hash and cur_ver.sha256_hash != local_hash:
            cur_ver.sha256_hash = local_hash
            db.commit()

    # 默认用本地记录的推送哈希；若能连云端则更新为云端当前版本哈希
    cloud_hash = doc.cloud_hash
    cloud_version_count = None
    cloud_updated_at = None

    if config.get("cloud_access_token") and doc.workspace_id:
        try:
            cloud_url = get_cloud_url(config)
            ver_resp = cloud_req(
                "GET",
                f"{cloud_url}/api/workspaces/{doc.workspace_id}/documents/{doc.cloud_doc_id or doc.id}/versions",
                config, timeout=10,
            )
            if ver_resp.status_code == 200:
                vers = ver_resp.json()
                cloud_version_count = len(vers)
                cur_cloud = next((v for v in vers if v.get("is_current")), None)
                if cur_cloud:
                    cloud_hash = cur_cloud.get("sha256_hash")
                    cloud_updated_at = cur_cloud.get("created_at")
        except Exception as e:
            logger.warning(f"获取云端哈希失败（非致命）: {e}")

    if local_hash and cloud_hash:
        if local_hash == cloud_hash:
            status = "synced"
            message = "本地与云端一致"
        else:
            status = "modified"
            message = "本地已修改，可以推送到云端"
    elif not cloud_hash:
        status = "unknown"
        message = "无法获取云端哈希"
    else:
        status = "unknown"
        message = "无法计算本地哈希"

    return {
        "doc_id": doc.id,
        "status": status,
        "message": message,
        "local_hash": local_hash,
        "cloud_hash": cloud_hash,
        "cloud_version_count": cloud_version_count,
        "cloud_updated_at": cloud_updated_at,
        "cloud_pushed_at": doc.cloud_pushed_at.isoformat() if doc.cloud_pushed_at else None,
    }


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
        raise HTTPException(status_code=400, detail="请先绑定工作空间")

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
    """
    获取云端文档指定版本的预签名下载 URL。
    步骤：
      1. 从云端拉取该文档的版本列表，找到 version_id 对应的 oss_key
      2. 调云后端 POST /api/storage/presign-download 生成下载 URL
    """
    config = load_library_config()
    if not config.get("cloud_access_token"):
        raise HTTPException(status_code=401, detail="请先登录云端账号")

    ws_id = workspace_id or config.get("active_workspace_id")
    if not ws_id:
        raise HTTPException(status_code=400, detail="请先绑定工作空间")

    cloud_url = get_cloud_url(config)

    # 获取版本列表，找到对应 oss_key
    try:
        ver_resp = cloud_req(
            "GET",
            f"{cloud_url}/api/workspaces/{ws_id}/documents/{doc_id}/versions",
            config, timeout=15,
        )
        if ver_resp.status_code != 200:
            raise HTTPException(status_code=502, detail="获取版本信息失败")
        versions = ver_resp.json()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"连接云端失败: {e}")

    target_ver = next((v for v in versions if v.get("id") == version_id), None)
    if not target_ver:
        raise HTTPException(status_code=404, detail="版本不存在")

    oss_key = target_ver.get("oss_key")
    if not oss_key:
        raise HTTPException(status_code=400, detail="该版本暂无可下载文件")

    # 生成预签名下载 URL
    try:
        dl_resp = cloud_req(
            "POST",
            f"{cloud_url}/api/storage/presign-download",
            config,
            json={"oss_key": oss_key},
            timeout=15,
        )
        if dl_resp.status_code != 200:
            raise HTTPException(status_code=502, detail="生成下载链接失败")
        download_url = dl_resp.json().get("download_url")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成下载链接失败: {e}")

    return {
        "download_url": download_url,
        "original_filename": target_ver.get("original_filename", f"{doc_id}_v{target_ver.get('version_number', '')}.bin"),
        "version_number": target_ver.get("version_number"),
        "file_size": target_ver.get("file_size"),
    }


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
def search_cloud_docs(q: str = "", tags: str = "", page: int = 1, page_size: int = 20, workspace_id: Optional[str] = None):
    """透传：服务端文档搜索"""
    config = load_library_config()
    if not config.get("cloud_access_token"):
        raise HTTPException(status_code=401, detail="请先登录云端账号")
    ws_id = workspace_id or config.get("active_workspace_id")
    if not ws_id:
        raise HTTPException(status_code=400, detail="请先绑定工作空间")
    cloud_url = get_cloud_url(config)
    resp = cloud_req(
        "GET", f"{cloud_url}/api/workspaces/{ws_id}/documents/search", config,
        params={"q": q, "tags": tags, "page": page, "page_size": page_size},
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
        raise HTTPException(status_code=400, detail="请先绑定工作空间")
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
        raise HTTPException(status_code=400, detail="请先绑定工作空间")

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
