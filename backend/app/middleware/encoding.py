"""
编码中间件
确保所有响应都使用 UTF-8 编码
"""
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


class UTF8EncodingMiddleware(BaseHTTPMiddleware):
    """确保所有响应都包含 UTF-8 编码头"""
    
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        # 确保 Content-Type 包含 charset=utf-8（对于文本响应）
        if "content-type" in response.headers:
            content_type = response.headers["content-type"]
            if "application/json" in content_type and "charset" not in content_type:
                response.headers["content-type"] = content_type.replace(
                    "application/json", "application/json; charset=utf-8"
                )
            elif "text/" in content_type and "charset" not in content_type:
                response.headers["content-type"] = content_type + "; charset=utf-8"
        return response
