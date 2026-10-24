"""桌面与网页登录共用的有界限流；多 worker 时每个进程独立计数。"""
from collections import OrderedDict, deque
import hashlib
import math
import threading
import time

from fastapi import HTTPException, Request

from app.config import settings


class LoginLimiter:
    """在校验密码之前原子预留账号与来源 IP 的尝试次数。"""

    def __init__(self, *, account_limit=10, ip_limit=60, window_seconds=60, max_keys=10000, clock=None):
        if min(account_limit, ip_limit, window_seconds) <= 0 or max_keys < 2:
            raise ValueError("登录限流配置无效")
        self.account_limit = account_limit
        self.ip_limit = ip_limit
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._buckets = OrderedDict()

    def check(self, email: str, ip: str) -> None:
        # 账号键仅保留摘要，不缓存邮箱或认证结果；不存在账号也使用同一预算。
        account = hashlib.sha256(email.casefold().encode("utf-8")).hexdigest()
        limits = ((("ip", ip), self.ip_limit), (("account", account), self.account_limit))
        with self._lock:
            now = self._clock()
            cutoff = now - self.window_seconds
            for key, limit in limits:
                bucket = self._buckets.get(key)
                if bucket is None:
                    continue
                while bucket and bucket[0] <= cutoff:
                    bucket.popleft()
                self._buckets.move_to_end(key)
                if len(bucket) >= limit:
                    retry = max(1, math.ceil(bucket[0] + self.window_seconds - now))
                    raise HTTPException(429, "登录尝试过于频繁，请稍后重试",
                                        headers={"Retry-After": str(retry)})
            for key, _ in limits:
                self._buckets.setdefault(key, deque()).append(now)
                self._buckets.move_to_end(key)
            while len(self._buckets) > self.max_keys:
                self._buckets.popitem(last=False)


login_limiter = LoginLimiter(
    account_limit=settings.login_account_attempts,
    ip_limit=settings.login_ip_attempts,
    window_seconds=settings.login_window_seconds,
    max_keys=settings.login_bucket_limit,
)


def check_login_rate(email: str, request: Request) -> None:
    # 不自行信任客户端传入的转发头；反向代理由 ASGI 服务的可信代理配置处理。
    login_limiter.check(email, request.client.host if request.client else "unknown")
