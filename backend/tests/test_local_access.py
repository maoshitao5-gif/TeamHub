"""个人 API 的浏览器来源、主机和桌面能力边界。"""
import pytest
from fastapi.testclient import TestClient

from backend.app.config import settings
from backend.tests.test_organize_workflow import library
from main import app


def _client(base_url="http://127.0.0.1", peer="127.0.0.1", application=app):
    async def with_peer(scope, receive, send):
        scope = {**scope, "client": (peer, 54321)}
        await application(scope, receive, send)
    return TestClient(with_peer, base_url=base_url)


@pytest.mark.parametrize(
    ("base_url", "headers", "peer"),
    [
        ("http://evil.example", {}, "127.0.0.1"),
        ("http://127.0.0.1", {"origin": "https://evil.example"}, "127.0.0.1"),
        ("http://127.0.0.1", {"origin": "null"}, "127.0.0.1"),
        ("http://127.0.0.1", {}, "192.0.2.10"),
    ],
)
def test_untrusted_request_cannot_write_library(library, base_url, headers, peer):
    client = _client(base_url, peer)
    # 浏览器的无 Content-Type 请求不会预检；CORS 本身不会阻止这个写入。
    response = client.post(
        "/api/settings/library/mkdir", headers=headers, content='{"path":"untrusted"}'
    )
    assert response.status_code == 403
    assert not (library / "untrusted").exists()


@pytest.mark.parametrize("origin", [None, "http://localhost:5173", "http://127.0.0.1:5173"])
def test_local_scripts_and_development_browser_remain_available(library, origin):
    headers = {"origin": origin} if origin is not None else {}
    client = _client()
    assert client.get("/api/health", headers=headers).status_code == 200
    response = client.post("/api/settings/library/mkdir", headers=headers, json={"path": "allowed"})
    assert response.status_code == 200
    assert (library / "allowed").is_dir()


@pytest.mark.parametrize("capability", [None, "wrong-session", "desktop-test-only"])
def test_electron_null_origin_requires_private_capability(library, monkeypatch, capability):
    monkeypatch.setattr(settings, "is_electron", True)
    monkeypatch.setenv("TEAMHUB_AI_CAPABILITY", "desktop-test-only")
    headers = {"origin": "null"}
    if capability is not None:
        headers["x-teamhub-ai-capability"] = capability
    client = _client()
    response = client.post("/api/settings/library/mkdir", headers=headers, json={"path": "desktop"})
    expected = capability == "desktop-test-only"
    assert response.status_code == (200 if expected else 403)
    assert (library / "desktop").exists() is expected


def test_electron_preflight_does_not_authorize_actual_request(library, monkeypatch):
    monkeypatch.setattr(settings, "is_electron", True)
    monkeypatch.setenv("TEAMHUB_AI_CAPABILITY", "desktop-test-only")
    # app 的 CORS 配置在导入时固定；仅在此测试中构建启用 null Origin 的完整应用。
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from backend.app.middleware.local_access import LocalAccessMiddleware

    desktop_app = FastAPI()
    desktop_app.add_middleware(
        CORSMiddleware, allow_origins=settings.allowed_origins, allow_methods=["*"], allow_headers=["*"]
    )
    desktop_app.add_middleware(LocalAccessMiddleware)
    desktop_app.mount("/", app)
    client = _client(application=desktop_app)
    response = client.options(
        "/api/settings/library/mkdir",
        headers={
            "origin": "null",
            "access-control-request-method": "POST",
            "access-control-request-headers": "content-type,x-teamhub-ai-capability",
        },
    )
    assert response.status_code == 200
    response = client.post(
        "/api/settings/library/mkdir", headers={"origin": "null"}, json={"path": "untrusted"}
    )
    assert response.status_code == 403
    assert not (library / "untrusted").exists()


@pytest.mark.parametrize("url", ["http://127.0.0.1:5175", "http://127.0.0.1:5175/"])
def test_electron_owned_development_origin_passes_cors_and_local_guard(monkeypatch, url):
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from backend.app.middleware.local_access import LocalAccessMiddleware

    monkeypatch.setattr(settings, "is_electron", True)
    monkeypatch.setenv("TEAMHUB_DEV_FRONTEND_URL", url)
    desktop_app = FastAPI()
    desktop_app.add_middleware(
        CORSMiddleware, allow_origins=settings.allowed_origins, allow_methods=["*"], allow_headers=["*"]
    )
    desktop_app.add_middleware(LocalAccessMiddleware)
    desktop_app.mount("/", app)
    client = _client(application=desktop_app)
    origin = "http://127.0.0.1:5175"
    preflight = client.options("/api/health", headers={
        "origin": origin, "access-control-request-method": "GET",
    })
    assert preflight.status_code == 200
    response = client.get("/api/health", headers={"origin": origin})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert client.get("/api/health", headers={"origin": "http://127.0.0.1:5176"}).status_code == 403


@pytest.mark.parametrize("is_electron", [False, True])
@pytest.mark.parametrize("url", [
    "https://127.0.0.1:5175/", "http://localhost:5175/",
    "http://evil.example:5175/", "http://127.0.0.1.evil.example:5175/",
    "http://user:pass@127.0.0.1:5175/", "http://127.0.0.1:5175/path",
    "http://127.0.0.1:5175/?query=1", "http://127.0.0.1:5175/#fragment",
    "http://127.0.0.1/", "http://127.0.0.1:0/", "http://127.0.0.1:65536/",
    "\nhttp://127.0.0.1:5175/", "http://127.0.0.1:5175/\n",
])
def test_untrusted_development_url_does_not_expand_origins(monkeypatch, is_electron, url):
    monkeypatch.setattr(settings, "is_electron", is_electron)
    monkeypatch.delenv("TEAMHUB_DEV_FRONTEND_URL", raising=False)
    before = settings.allowed_origins
    monkeypatch.setenv("TEAMHUB_DEV_FRONTEND_URL", url)
    assert settings.allowed_origins == before


def test_development_url_is_not_enabled_outside_electron(monkeypatch):
    monkeypatch.setattr(settings, "is_electron", False)
    monkeypatch.setenv("TEAMHUB_DEV_FRONTEND_URL", "http://127.0.0.1:5175/")
    assert "http://127.0.0.1:5175" not in settings.allowed_origins
