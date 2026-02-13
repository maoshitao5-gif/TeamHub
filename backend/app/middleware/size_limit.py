"""
请求大小限制中间件
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from backend.app.config import settings
from backend.app.core.logger import get_logger

logger = get_logger("middleware.size_limit")


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """请求体大小限制中间件"""

    def __init__(self, app, max_size: int = None):
        super().__init__(app)
        self.max_size = max_size or settings.max_file_size

    async def dispatch(self, request: Request, call_next):
        # 检查 Content-Length header
        content_length = request.headers.get("content-length")

        if content_length:
            content_length = int(content_length)
            if content_length > self.max_size:
                size_mb = self.max_size / (1024 * 1024)
                logger.warning(
                    f"请求体过大: {content_length / (1024 * 1024):.2f}MB, "
                    f"限制: {size_mb:.2f}MB, "
                    f"路径: {request.url.path}"
                )
                return JSONResponse(
                    status_code=413,
                    content={
                        "detail": f"请求体过大（最大 {size_mb:.0f}MB）"
                    }
                )

        response = await call_next(request)
        return response
