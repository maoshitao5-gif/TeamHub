"""
已停用旧路径的统一响应(M1)

按 docs/MVP数据与接口契约.md §4/§7:旧共享通路返回 410 与统一错误信封,
不静默 404,也不保留可写入的后台入口。
"""
from fastapi.responses import JSONResponse


def gone_response(alternative: str) -> JSONResponse:
    """返回 410 GONE_DECOMMISSIONED 错误信封"""
    return JSONResponse(
        status_code=410,
        content={
            "error": {
                "code": "GONE_DECOMMISSIONED",
                "message": f"该共享通路已停用。{alternative}",
                "details": None,
            }
        },
    )
