"""
本地后端 — 云端共享 API(M1 已停用)

原"设备 manifest 共享"通路(quick-share / sync-enabled)按
docs/MVP数据与接口契约.md §7 停用:返回 410 与统一错误信封,
发布/取回能力将由 M4 的唯一共享通路提供。
"""
from fastapi import APIRouter, Query

from backend.app.core.decommissioned import gone_response

router = APIRouter(prefix="/api/share", tags=["share"])

_ALTERNATIVE = "发布与取回能力将在 M4 提供;请勿依赖设备 manifest 共享。"


@router.put("/documents/{doc_id}/sync-enabled")
def toggle_doc_sync(doc_id: str, enabled: bool = Query(...)):
    """已停用:文档云同步开关"""
    return gone_response(_ALTERNATIVE)


@router.post("/documents/{doc_id}/quick-share")
def quick_share(doc_id: str):
    """已停用:一键共享(设备 manifest 通路)"""
    return gone_response(_ALTERNATIVE)
