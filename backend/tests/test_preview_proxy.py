"""Desktop preview proxy keeps credentials private and never takes files into the library."""
from contextlib import contextmanager

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import database
from backend.app.api import sharing
from backend.app.config import settings
from backend.app.core import cloud_client
from backend.app.models import Document, Version, ShareLink, RemoteOperation
from main import app

client = TestClient(app)
PREFIX = "/api/sharing/cloud-docs/document/versions/fixed-version/preview"
SERVER_ID = "b74c1bb8-c04b-4761-8c14-126d36ef0a56"


@pytest.fixture
def proxy_library(tmp_path, monkeypatch):
    library = tmp_path / "library"
    library.mkdir()
    monkeypatch.setattr(settings, "library_path", str(library))
    database.rebind_engine(); database.init_db()
    config = database.load_library_config()
    config.update(cloud_api_url="http://preview.invalid", cloud_access_token="private-access-token",
                  cloud_server_id=SERVER_ID, active_workspace_id="workspace")
    database.save_library_config(config)
    yield library
    database.engine.dispose()


@pytest.fixture
def upstream(monkeypatch):
    requests = []

    def install(handler):
        def respond(request):
            requests.append(request)
            if request.url.path == "/api/server-info":
                return httpx.Response(200, json={"server_id": SERVER_ID, "protocol_major": 1,
                                               "capabilities": ["explicit_publication_scope"]})
            return handler(request)
        http = httpx.Client(transport=httpx.MockTransport(respond))
        monkeypatch.setattr(cloud_client, "_http", http)
        monkeypatch.setattr(sharing, "_http", http)
        return requests

    return install


def snapshot(library):
    # DB connections may update WAL metadata; compare semantic records and files
    # outside SQLite internals, including all working files and immutable objects.
    with database.SessionLocal() as db:
        counts = tuple(db.query(model).count() for model in (Document, Version, ShareLink, RemoteOperation))
    files = {str(path.relative_to(library)): path.read_bytes() for path in library.rglob("*")
             if path.is_file() and not path.name.startswith("teamhub.db")}
    return counts, files


def test_json_proxy_keeps_version_token_and_local_state(proxy_library, upstream):
    requests = upstream(lambda _: httpx.Response(200, json={"document_id": "document", "version_id": "fixed-version",
                                                          "kind": "text", "text": "预览", "truncated": False}))
    before = snapshot(proxy_library)
    response = client.get(PREFIX, params={"workspace_id": "chosen-space"})
    assert response.status_code == 200 and response.json()["version_id"] == "fixed-version"
    request = requests[-1]
    assert request.url.path == "/api/workspaces/chosen-space/documents/document/versions/fixed-version/preview"
    assert request.headers["authorization"] == "Bearer private-access-token"
    assert "token" not in str(request.url)
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert snapshot(proxy_library) == before


def test_binary_proxy_returns_safe_mime_bytes_and_no_library_write(proxy_library, upstream):
    requests = upstream(lambda _: httpx.Response(200, content=b"%PDF-1.7\npreview", headers={"Content-Type": "application/pdf"}))
    before = snapshot(proxy_library)
    response = client.get(PREFIX + "/content")
    assert response.status_code == 200 and response.content == b"%PDF-1.7\npreview"
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "token" not in str(requests[-1].url)
    assert snapshot(proxy_library) == before


@pytest.mark.parametrize("suffix", ["", "/content"])
def test_upstream_permission_error_is_preserved(proxy_library, upstream, suffix):
    upstream(lambda _: httpx.Response(404, json={"detail": "资源不存在"}))
    response = client.get(PREFIX + suffix)
    assert response.status_code == 404
    assert response.json()["error"]["message"] == "资源不存在"


@pytest.mark.parametrize("suffix", ["", "/content"])
def test_offline_error_is_friendly_and_contains_no_credentials(proxy_library, upstream, suffix):
    def fail(request):
        raise httpx.ConnectError("private-access-token connection diagnostics", request=request)
    upstream(fail)
    response = client.get(PREFIX + suffix)
    assert response.status_code == 502
    assert response.json()["detail"] == "共享服务不可达，请重试"
    assert "private-access-token" not in response.text


@pytest.mark.parametrize("suffix", ["", "/content"])
def test_changed_service_identity_sends_no_token(proxy_library, upstream, monkeypatch, suffix):
    requests = []
    def wrong_identity(request):
        requests.append(request)
        return httpx.Response(200, json={"server_id": "other-id", "protocol_major": 1,
                                       "capabilities": ["explicit_publication_scope"]})
    http = httpx.Client(transport=httpx.MockTransport(wrong_identity))
    monkeypatch.setattr(cloud_client, "_http", http)
    monkeypatch.setattr(sharing, "_http", http)
    response = client.get(PREFIX + suffix)
    assert response.status_code == 409 and len(requests) == 1
    assert "authorization" not in requests[0].headers


def test_binary_proxy_rejects_active_upstream_mime(proxy_library, upstream):
    upstream(lambda _: httpx.Response(200, content=b"<script>evil</script>", headers={"content-type": "text/html"}))
    response = client.get(PREFIX + "/content")
    assert response.status_code == 502 and "<script>" not in response.text


@pytest.mark.parametrize("length", [str(51 * 1024 * 1024), "invalid", "-1"])
def test_binary_proxy_rejects_invalid_length_before_stream(proxy_library, upstream, length):
    upstream(lambda _: httpx.Response(200, content=b"small", headers={"content-type": "application/pdf", "content-length": length}))
    assert client.get(PREFIX + "/content").status_code == 502


def test_binary_proxy_refreshes_once_without_token_in_url(proxy_library, upstream, monkeypatch):
    count = 0
    def content(request):
        nonlocal count
        count += 1
        if count == 1:
            return httpx.Response(401, json={"detail": "expired"})
        assert request.headers["authorization"] == "Bearer refreshed-token"
        return httpx.Response(200, content=b"%PDF-1.7", headers={"content-type": "application/pdf"})
    requests = upstream(content)
    def refresh(config):
        config["cloud_access_token"] = "refreshed-token"
        return config
    monkeypatch.setattr(sharing, "refresh_token", refresh)
    response = client.get(PREFIX + "/content")
    assert response.status_code == 200 and count == 2
    assert all("token" not in str(request.url) for request in requests)


def test_binary_stream_size_limit_closes_upstream(proxy_library, monkeypatch):
    closed = []
    class Response:
        status_code = 200
        headers = {"content-type": "image/png"}
        def iter_bytes(self, chunk_size):
            for _ in range(21):
                yield b"x" * 1024 * 1024
    class Http:
        @contextmanager
        def stream(self, *args, **kwargs):
            try:
                yield Response()
            finally:
                closed.append(True)
    monkeypatch.setattr(sharing, "_http", Http())
    monkeypatch.setattr(sharing, "verify_server_identity", lambda _: SERVER_ID)
    response = sharing.proxy_cloud_preview_content("document", "fixed-version")
    async def consume():
        total = 0
        with pytest.raises(httpx.StreamError, match="超过大小上限"):
            async for value in response.body_iterator:
                total += len(value)
        assert total == 20 * 1024 * 1024
    import asyncio
    asyncio.run(consume())
    assert closed == [True]


def test_stream_read_failure_closes_upstream(proxy_library, monkeypatch):
    closed = []
    class Response:
        status_code = 200
        headers = {"content-type": "application/pdf"}
        def iter_bytes(self, chunk_size):
            yield b"%PDF-1.7"
            raise httpx.ReadError("interrupted")
    class Http:
        @contextmanager
        def stream(self, *args, **kwargs):
            try:
                yield Response()
            finally:
                closed.append(True)
    monkeypatch.setattr(sharing, "_http", Http())
    monkeypatch.setattr(sharing, "verify_server_identity", lambda _: SERVER_ID)
    response = sharing.proxy_cloud_preview_content("document", "fixed-version")
    async def consume():
        with pytest.raises(httpx.ReadError):
            async for _ in response.body_iterator:
                pass
    import asyncio
    asyncio.run(consume())
    assert closed == [True]


def test_preview_requires_cloud_login(proxy_library):
    config = database.load_library_config(); config.pop("cloud_access_token", None)
    database.save_library_config(config)
    assert client.get(PREFIX).status_code == 401


@pytest.mark.parametrize("disconnect", [True, False])
def test_disconnect_before_body_closes_upstream_without_gc(proxy_library, monkeypatch, disconnect):
    import asyncio
    import anyio

    closed, reads = [], []

    class Response:
        status_code = 200
        headers = {"content-type": "application/pdf"}

        def iter_bytes(self, chunk_size):
            reads.append(True)
            yield b"%PDF-1.7"

    class Http:
        @contextmanager
        def stream(self, *args, **kwargs):
            try:
                yield Response()
            finally:
                closed.append(True)

    monkeypatch.setattr(sharing, "_http", Http())
    monkeypatch.setattr(sharing, "verify_server_identity", lambda _: SERVER_ID)
    response = sharing.proxy_cloud_preview_content("document", "fixed-version")

    async def receive():
        if disconnect:
            return {"type": "http.disconnect"}
        await anyio.sleep_forever()

    async def send(message):
        assert message["type"] == "http.response.start"
        if disconnect:
            await anyio.sleep_forever()
        else:
            raise ConnectionError("client disconnected during response headers")

    async def run_response():
        if disconnect:
            await response({"type": "http", "asgi": {"version": "3.0", "spec_version": "2.0"}}, receive, send)
        else:
            # Starlette 1.9 展开单个任务异常；仍须传播原始断连并同步关闭上游。
            with pytest.raises(ConnectionError, match="client disconnected during response headers"):
                await response({"type": "http", "asgi": {"version": "3.0", "spec_version": "2.0"}}, receive, send)

    asyncio.run(run_response())
    assert not reads
    assert closed == [True]
    response._close_upstream()
    assert closed == [True]
