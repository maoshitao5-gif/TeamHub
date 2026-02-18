"""
异常处理中间件
统一处理所有异常，确保包含 CORS 头
"""
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
import traceback

from backend.app.config import settings

ALLOWED_ORIGINS = settings.allowed_origins
from backend.app.core.encoding import safe_str, safe_print


def get_cors_origin(request: Request) -> str:
    """获取允许的 CORS origin"""
    origin = request.headers.get("origin")
    
    # 对于开发环境，允许所有 localhost 和 127.0.0.1 的请求
    if origin and (origin.startswith("http://localhost:") or origin.startswith("http://127.0.0.1:")):
        return origin
    elif origin and origin in ALLOWED_ORIGINS:
        return origin
    else:
        return ALLOWED_ORIGINS[0] if ALLOWED_ORIGINS else "http://localhost:5173"


def get_cors_headers(origin: str) -> dict:
    """获取 CORS 响应头"""
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Allow-Methods": "*",
        "Access-Control-Allow-Headers": "*",
    }


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """处理 HTTP 异常，确保包含 CORS 头"""
    cors_origin = get_cors_origin(request)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=get_cors_headers(cors_origin)
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """处理请求验证错误，确保包含 CORS 头"""
    cors_origin = get_cors_origin(request)
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body": exc.body},
        headers=get_cors_headers(cors_origin)
    )


async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """处理所有未捕获的异常，确保包含 CORS 头"""
    error_detail = safe_str(exc)
    error_traceback = traceback.format_exc()
    safe_print(f"未捕获的异常: {error_detail}")
    safe_print(f"错误堆栈: {error_traceback}")
    
    cors_origin = get_cors_origin(request)
    return JSONResponse(
        status_code=500,
        content={"detail": f"服务器内部错误: {error_detail}"},
        headers=get_cors_headers(cors_origin)
    )
