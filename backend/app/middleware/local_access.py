"""在路由执行前验证本机连接、浏览器来源及桌面会话能力。"""
import os
import secrets

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from backend.app.config import settings


class LocalAccessMiddleware:
    """CORS 只控制响应读取；个人 API 还必须拒绝不可信来源的实际操作。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        peer = request.client.host if request.client else ""
        hosts = {"localhost", "127.0.0.1", "::1"}
        if peer == "testclient":
            hosts.add("testserver")
        origin = request.headers.get("origin")
        allowed = (
            peer in {"127.0.0.1", "::1", "testclient"}
            and request.url.hostname in hosts
            and (origin is None or origin in settings.allowed_origins)
        )
        if allowed and origin == "null":
            # 浏览器预检不发送私有头的值；实际请求仍须独立验证能力。
            preflight = (
                request.method == "OPTIONS"
                and request.headers.get("access-control-request-method") is not None
            )
            if not preflight:
                expected = os.environ.get("TEAMHUB_AI_CAPABILITY", "")
                supplied = request.headers.get("x-teamhub-ai-capability", "")
                allowed = bool(expected) and secrets.compare_digest(
                    expected.encode(), supplied.encode()
                )
        if not allowed:
            response = JSONResponse(
                {"error": {
                    "code": "LOCAL_REQUEST_REQUIRED",
                    "message": "个人资料接口仅允许从本机 TeamHub 界面或本机工具调用",
                    "details": None,
                }},
                status_code=403,
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
