"""路由总注册(M1 收敛入口)

契约 §7:设备 manifest 共享、通用双向同步、设备注册、用户级备份上传、
公开注册与加入审批在 MVP 首版停用——注册时跳过原路由,
由 decommissioned 桩返回 410(不静默 404,不保留可写入入口)。
"""
from fastapi import FastAPI
from app.api import auth, users, teams, workspaces, documents, publications, collaboration, admin, decommissioned, deliveries, web_auth

# 首版停用的路由(方法, 路径);被跳过后由 decommissioned 桩返回 410
_SKIP_ROUTES = {
    ("POST", "/api/auth/register"),
    ("GET", "/api/teams/discover"),
    ("POST", "/api/teams/join-requests"),
    ("GET", "/api/teams/join-requests/my"),
    ("GET", "/api/teams/join-requests/pending-count"),
    ("GET", "/api/teams/{team_id}/join-requests"),
    ("POST", "/api/teams/{team_id}/join-requests/{request_id}/review"),
}


def _include_filtered(app: FastAPI, router) -> None:
    """注册路由,跳过 _SKIP_ROUTES 中列出的 (方法, 路径)"""
    kept = [
        r for r in router.routes
        if not any((m, r.path) in _SKIP_ROUTES for m in r.methods)
    ]
    router.routes = kept
    app.include_router(router)


def register_routers(app: FastAPI):
    # M1:停用桩必须最先注册——否则 /api/teams/discover 会先命中
    # teams 路由的 /{team_id} 通配;桩只匹配停用清单中的精确路径,不影响正常路由
    app.include_router(decommissioned.router)
    _include_filtered(app, auth.router)
    app.include_router(users.router)
    _include_filtered(app, teams.router)
    app.include_router(workspaces.router)
    # M5:协作(标签/回收站/联想)注册在 documents 之前,使 /documents/suggestions 精确匹配
    app.include_router(collaboration.router)
    app.include_router(documents.router)
    # M4:唯一共享写入通路(上传/发布/版本提交/下载/查重)
    app.include_router(publications.router)
    # M4:storage 旧旁路(预签名直传 OSS)停用,不再注册,
    # 由 decommissioned 桩对 presign-upload/presign-download/confirm-upload 返回 410
    app.include_router(admin.router)
    app.include_router(deliveries.router)
    app.include_router(web_auth.router)
    # sharing(设备 manifest)、sync(双向同步)、devices(设备注册)、
    # backup(用户级备份上传)整体停用,不再注册,由停用桩统一返回 410
