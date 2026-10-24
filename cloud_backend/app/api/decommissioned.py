"""
已停用旧路径的统一响应(M1,契约 §7)

设备 manifest 共享、通用双向同步、设备注册、用户级备份上传、
公开注册与加入审批,在 MVP 首版中均不提供;
返回 410 与统一错误信封,不静默 404,不保留可写入的后台入口。
"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["已停用"])

_ALL_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH"]


def _gone(alternative: str) -> JSONResponse:
    return JSONResponse(
        status_code=410,
        content={
            "error": {
                "code": "GONE_DECOMMISSIONED",
                "message": f"该接口已停用。{alternative}",
                "details": None,
            }
        },
    )


@router.api_route("/api/sharing/{path:path}", methods=_ALL_METHODS)
@router.api_route("/api/sharing", methods=_ALL_METHODS)
def sharing_gone(path: str = ""):
    """已停用:设备 manifest 共享"""
    return _gone("发布/取回能力将在 M4 由唯一的共享通路提供。")


@router.api_route("/api/workspaces/{ws_id}/sync/{path:path}", methods=_ALL_METHODS)
def sync_gone(ws_id: str, path: str = ""):
    """已停用:通用双向同步"""
    return _gone("发布/取回能力将在 M4 由唯一的共享通路提供。")


@router.api_route("/api/devices/{path:path}", methods=_ALL_METHODS)
@router.api_route("/api/devices", methods=_ALL_METHODS)
def devices_gone(path: str = ""):
    """已停用:设备注册与同步游标"""
    return _gone("首版不需要设备注册;共享会话由客户端本地服务统一管理。")


@router.api_route("/api/backup/{path:path}", methods=_ALL_METHODS)
@router.api_route("/api/backup", methods=_ALL_METHODS)
def backup_gone(path: str = ""):
    """已停用:用户级备份上传 OSS"""
    return _gone("备份将在 M6 由维护者脚本方案提供。")


@router.post("/api/auth/register")
def register_gone():
    """已停用:公开注册"""
    return _gone("首版由管理员预置成员,不开放注册。")


@router.api_route("/api/teams/discover", methods=_ALL_METHODS)
def discover_gone():
    """已停用:团队发现"""
    return _gone("首版由管理员预置成员,不开放团队发现与加入申请。")


@router.api_route("/api/teams/{team_id}/join-requests/{path:path}", methods=_ALL_METHODS)
@router.api_route("/api/teams/{team_id}/join-requests", methods=_ALL_METHODS)
@router.api_route("/api/teams/join-requests/{path:path}", methods=_ALL_METHODS)
@router.api_route("/api/teams/join-requests", methods=_ALL_METHODS)
def join_requests_gone(team_id: str = "", path: str = ""):
    """已停用:加入申请"""
    return _gone("首版由管理员预置成员,不开放团队发现与加入申请。")


# ========== M1-R02:旧文档写入入口停用 ==========
# create/update/delete 文档、push、snapshot、独立 tag 增删均产生不符合新契约的
# 组织数据(如 current_version_id 为 NULL、重复版本号),M1 统一停用;
# 新的发布/版本提交/标签关联通路在 M4 由发布通路实现。

@router.api_route("/api/workspaces/{ws_id}/documents/{doc_id}/push", methods=_ALL_METHODS)
def doc_push_gone(ws_id: str, doc_id: str):
    """已停用:旧推送文档(创建/更新资料与版本)"""
    return _gone("发布/版本提交能力将在 M4 由唯一的发布通路提供。")


@router.api_route("/api/workspaces/{ws_id}/documents/{doc_id}/versions/snapshot", methods=_ALL_METHODS)
def version_snapshot_gone(ws_id: str, doc_id: str):
    """已停用:旧版本快照复制(重复版本号,与唯一约束冲突)"""
    return _gone("版本提交能力将在 M4 由唯一的发布通路提供。")


@router.api_route("/api/workspaces/{ws_id}/documents", methods=["POST"])
def doc_create_gone(ws_id: str):
    """已停用:直接创建共享资料(资料应由发布动作创建)"""
    return _gone("共享资料将通过 M4 发布通路创建,不提供直接建档入口。")


@router.api_route("/api/workspaces/{ws_id}/documents/{doc_id}", methods=["PUT", "DELETE"])
def doc_mutate_gone(ws_id: str, doc_id: str):
    """已停用:直接改删共享资料(改名/删除走权限化通路)"""
    return _gone("资料改名、回收与永久删除将在 M4/M5 按权限矩阵提供。")


@router.api_route("/api/workspaces/{ws_id}/tags", methods=["POST"])
def tag_create_gone(ws_id: str):
    """已停用:独立创建共享标签(标签经资料关联增删)"""
    return _gone("标签将随资料关联增删(契约 §5.2 /documents/{id}/tags),不提供独立建档。")


@router.api_route("/api/workspaces/{ws_id}/tags/{tag_id}", methods=["DELETE"])
def tag_delete_gone(ws_id: str, tag_id: str):
    """已停用:独立删除共享标签"""
    return _gone("标签关联的移除将按贡献来源走权限化通路(M5)。")


# ========== M4:storage 预签名旧旁路停用(契约 §7) ==========
# 预签名直传 OSS、客户端自报摘要、版本缺失静默 200 的旧通路,
# 由 M4 流式上传(POST /uploads)+ 服务端重算摘要 + 发布/提交事务整体替代。

@router.post("/api/storage/presign-upload")
def presign_upload_gone():
    """已停用:预签名直传 OSS 上传"""
    return _gone("M4 起改用流式上传 POST /api/workspaces/{ws_id}/uploads,再经 /publications 或 /documents/{id}/versions 发布/提交。")


@router.post("/api/storage/presign-download")
def presign_download_gone():
    """已停用:预签名直传 OSS 下载"""
    return _gone("M4 起改用 GET /api/workspaces/{ws_id}/documents/{doc_id}/versions/{vid}/download。")


@router.post("/api/storage/confirm-upload")
def confirm_upload_gone():
    """已停用:客户端自报摘要确认上传"""
    return _gone("M4 起由服务端在 POST /uploads 重算摘要,发布/提交事务内直接落库,无需确认环节。")
