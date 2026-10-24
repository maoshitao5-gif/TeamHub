"""
M4 端到端联调:本地后端 ↔ 真实共享服务进程

用 cloud_backend/venv 以子进程启动真实共享服务(独立临时 DB 与对象库),
本地后端经 HTTP 全链路调用,验证蓝图 §6.4 M4 交付:
A 发布 → (同空间)取回 → 修改后提交新版本 → 再取回得新内容;
伪造/过期基准按信封透传;共享服务不可达不产生"已共享"假象。
"""
import os
import secrets
import socket
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.app.database as database
from backend.app.config import settings
from main import app

client = TestClient(app)

REPO = Path(__file__).resolve().parents[2]
CLOUD_DIR = REPO / "cloud_backend"
CLOUD_PY = CLOUD_DIR / "venv" / "bin" / "python"

SEED_SCRIPT = """
import uuid
from app.database import Base, engine, SessionLocal
from app import models
from app.core.security import hash_password
Base.metadata.create_all(bind=engine)
db = SessionLocal()
u = models.User(id=str(uuid.uuid4()), email="e2e@example.com",
                hashed_password=hash_password("e2e-pass-123"), display_name="E2E")
t = models.Team(id=str(uuid.uuid4()), name="E2E团队", slug="e2e-" + uuid.uuid4().hex[:8])
db.add_all([u, t])
db.flush()
db.add(models.TeamMember(team_id=t.id, user_id=u.id, role="member"))
u2 = models.User(id=str(uuid.uuid4()), email="member-b@example.com",
                 hashed_password=hash_password("e2e-pass-123"), display_name="B")
db.add(u2)
db.flush()
db.add(models.TeamMember(team_id=t.id, user_id=u2.id, role="member"))
ws = models.Workspace(id=str(uuid.uuid4()), team_id=t.id, name="E2E空间")
db.add(ws)
db.commit()
print(ws.id)
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def cloud_server(tmp_path_factory):
    """真实共享服务子进程(独立临时数据根),模块级共享"""
    tmp = tmp_path_factory.mktemp("cloud-e2e")
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{tmp}/cloud.db"
    env["STORAGE_DIR"] = str(tmp / "objects")
    env["TEAMHUB_SANDBOX"] = "1"
    env["JWT_SECRET_KEY"] = secrets.token_hex(32)

    seed = subprocess.run(
        [str(CLOUD_PY), "-c", SEED_SCRIPT],
        cwd=CLOUD_DIR, env=env, capture_output=True, text=True, timeout=60,
    )
    assert seed.returncode == 0, f"云端种子失败: {seed.stderr}"
    ws_id = seed.stdout.strip().splitlines()[-1]

    port = _free_port()
    log = open(tmp / "cloud.log", "w")
    proc = subprocess.Popen(
        [str(CLOUD_PY), "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=CLOUD_DIR, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}"
    import httpx
    try:
        with httpx.Client(trust_env=False, timeout=1) as probe:
            for _ in range(150):
                try:
                    if probe.get(f"{url}/api/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.2)
            else:
                raise RuntimeError("共享服务 30s 内未就绪")
        yield type("Cloud", (), {"url": url, "workspace_id": ws_id,
                                 "email": "e2e@example.com", "password": "e2e-pass-123"})
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


@pytest.fixture
def library(tmp_path, cloud_server):
    """本地隔离文件库 + 指向真实共享服务的登录会话"""
    lib = tmp_path / "lib"
    lib.mkdir()
    settings.library_path = str(lib)
    database.rebind_engine()
    database.init_db()

    config = database.load_library_config()
    config["cloud_api_url"] = cloud_server.url
    database.save_library_config(config)

    resp = client.post("/api/sync/login", json={
        "email": cloud_server.email, "password": cloud_server.password,
    })
    assert resp.status_code == 200, resp.text

    yield lib

    settings.library_path = str(tmp_path / "lib-rest")
    database.rebind_engine()
    database.init_db()


def _ingest(source, name=None, is_folder=False):
    resp = client.post("/api/documents", json={
        "name": name or source.stem,
        "source_path": str(source),
        "is_folder": is_folder,
        "tags": ["E2E"],
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


# ========== 主流程:A 发布 → 取回 → 提交新版本 → 再取回 ==========

def test_publish_submit_pull_full_cycle(library, cloud_server, tmp_path):
    ws = cloud_server.workspace_id

    # A 收录并发布
    src = tmp_path / "方案.txt"
    src.write_text("初稿", encoding="utf-8")
    doc = _ingest(src)
    resp = client.post("/api/sharing/publish", json={'visibility': 'workspace', "confirmed": True, "doc_id": doc["id"], "workspace_id": ws})
    assert resp.status_code == 200, resp.text
    shared_doc_id = resp.json()["document_id"]

    # 成功后才落 share_links
    link = client.get(f"/api/sharing/links/{doc['id']}").json()
    assert link["linked"] is True
    assert link["shared_document_id"] == shared_doc_id
    assert link["base_version_id"] == resp.json()["version_id"]

    # 重复发布(未选另建)→ 400
    resp = client.post("/api/sharing/publish", json={'visibility': 'workspace', "confirmed": True, "doc_id": doc["id"], "workspace_id": ws})
    assert resp.status_code == 400

    # 未发布过的资料直接提交 → 400
    src2 = tmp_path / "未发布.txt"
    src2.write_text("x", encoding="utf-8")
    doc2 = _ingest(src2)
    assert client.post("/api/sharing/submit-version", json={"doc_id": doc2["id"]}).status_code == 400

    # Same-library first pull now reports the existing entity. Change A's
    # unsaved working bytes to exercise a genuinely new intake for B next.
    duplicate = client.post("/api/sharing/pull", json={"cloud_doc_id": shared_doc_id, "workspace_id": ws})
    assert duplicate.json()['status'] == 'duplicate' and duplicate.json()['document_id'] == doc['id']
    (library / doc['storage_path']).write_text('A 的私有修改', encoding='utf-8')
    # B 取回(另一位置;同名自动加序号,不覆盖)
    resp = client.post("/api/sharing/pull", json={"cloud_doc_id": shared_doc_id, "workspace_id": ws})
    assert resp.status_code == 200, resp.text
    pulled = client.get(f"/api/documents/{resp.json()['document_id']}").json()
    assert (library / pulled["storage_path"]).read_text(encoding="utf-8") == "初稿"

    # A 修改工作副本并提交新版本(更新原资料)
    (library / doc["storage_path"]).write_text("二稿", encoding="utf-8")
    resp = client.post("/api/sharing/submit-version", json={"confirmed": True, "doc_id": doc["id"], "note": "二稿"})
    assert resp.status_code == 200, resp.text

    # 再次取回 → 拿到二稿
    resp = client.post("/api/sharing/pull", json={"cloud_doc_id": shared_doc_id, "workspace_id": ws})
    assert resp.status_code == 200, resp.text
    pulled2 = client.get(f"/api/documents/{resp.json()['document_id']}").json()
    assert (library / pulled2["storage_path"]).read_text(encoding="utf-8") == "二稿"
    assert resp.json()['status'] == 'duplicate'
    assert pulled2['id'] == doc['id']  # 二稿当前已在 A 的工作实体中，不重复创建
    assert pulled2["storage_path"] != pulled["storage_path"]


# ========== 基准过期:409 信封透传 ==========

def test_stale_base_conflict_passthrough(library, cloud_server, tmp_path):
    ws = cloud_server.workspace_id
    src = tmp_path / "冲突.txt"
    src.write_text("v1", encoding="utf-8")
    doc = _ingest(src)
    client.post("/api/sharing/publish", json={'visibility': 'workspace', "confirmed": True, "doc_id": doc["id"], "workspace_id": ws})

    # 模拟他人已推新版:本地基准被倒回旧版
    (library / doc["storage_path"]).write_text("v2", encoding="utf-8")
    client.post("/api/sharing/submit-version", json={"confirmed": True, "doc_id": doc["id"]})
    from backend.app.models import ShareLink
    db = database.SessionLocal()
    try:
        link = db.query(ShareLink).filter(ShareLink.local_document_id == doc["id"]).first()
        old_base = link.base_version_id
        link.base_version_id = " outdated-base "
        db.commit()
    finally:
        db.close()
    assert old_base

    (library / doc["storage_path"]).write_text("v3", encoding="utf-8")
    resp = client.post("/api/sharing/submit-version", json={"confirmed": True, "doc_id": doc["id"]})
    assert resp.status_code == 409
    body = resp.json()["error"]
    assert body["code"] == "STALE_BASE_VERSION"
    assert body["details"]["current_version_id"]


# ========== 文件夹:发布与取回 ==========

def test_folder_publish_and_pull(library, cloud_server, tmp_path):
    ws = cloud_server.workspace_id
    folder = tmp_path / "项目资料"
    (folder / "子目录").mkdir(parents=True)
    (folder / "说明.txt").write_text("文件夹内容", encoding="utf-8")
    (folder / "子目录" / "数据.csv").write_text("a,b", encoding="utf-8")

    doc = _ingest(folder, is_folder=True)
    resp = client.post("/api/sharing/publish", json={'visibility': 'workspace', "confirmed": True, "doc_id": doc["id"], "workspace_id": ws})
    assert resp.status_code == 200, resp.text
    shared_doc_id = resp.json()["document_id"]

    resp = client.post("/api/sharing/pull", json={"cloud_doc_id": shared_doc_id, "workspace_id": ws})
    assert resp.status_code == 200, resp.text
    pulled_path = library / resp.json()["storage_path"]
    assert pulled_path.is_dir()
    assert (pulled_path / "说明.txt").read_text(encoding="utf-8") == "文件夹内容"
    assert (pulled_path / "子目录" / "数据.csv").read_text(encoding="utf-8") == "a,b"


# ========== 共享服务不可达:不产生"已共享"假象 ==========

def test_cloud_unreachable_records_nothing(library, cloud_server, tmp_path):
    config = database.load_library_config()
    config["cloud_api_url"] = "http://127.0.0.1:1"  # 不可达
    database.save_library_config(config)

    src = tmp_path / "离线.txt"
    src.write_text("x", encoding="utf-8")
    doc = _ingest(src)
    resp = client.post("/api/sharing/publish",
                       json={'visibility': 'workspace', "confirmed": True, "doc_id": doc["id"], "workspace_id": cloud_server.workspace_id})
    assert resp.status_code in (401, 502)

    from backend.app.models import ShareLink
    db = database.SessionLocal()
    try:
        assert db.query(ShareLink).count() == 0
    finally:
        db.close()


def test_lost_publish_response_replays_frozen_request(library, cloud_server, tmp_path, monkeypatch):
    import httpx
    from backend.app.api import sharing
    source = tmp_path / '重试.txt'
    source.write_text('首次确认内容')
    doc = _ingest(source)
    req = dict(visibility='workspace', doc_id=doc['id'], workspace_id=cloud_server.workspace_id, confirmed=True, request_id='lost-response')
    original = sharing.cloud_req
    def lose_response(method, url, *args, **kwargs):
        response = original(method, url, *args, **kwargs)
        if method == 'POST' and url.endswith('/publications'):
            raise httpx.ReadError('response lost')
        return response
    with monkeypatch.context() as patch:
        patch.setattr(sharing, 'cloud_req', lose_response)
        response = client.post('/api/sharing/publish', json=req)
        assert response.status_code == 502
    assert client.get(f"/api/sharing/links/{doc['id']}").json()['linked'] is False
    (library / doc['storage_path']).write_text('之后的修改')
    assert client.post(f"/api/documents/{doc['id']}/versions", json={}).status_code == 200
    result = client.post('/api/sharing/publish', json=req)
    assert result.status_code == 200, result.text
    again = client.post('/api/sharing/publish', json=req)
    assert again.json()['version_id'] == result.json()['version_id']
    pulled = client.post('/api/sharing/pull', json=dict(cloud_doc_id=result.json()['document_id'], workspace_id=cloud_server.workspace_id))
    assert (library / pulled.json()['storage_path']).read_text() == '首次确认内容'


def test_duplicate_does_not_create_local_share_link(library, cloud_server, tmp_path):
    from backend.app.models import ShareLink
    source = tmp_path / '查重.txt'
    source.write_text('组织中已有的内容')
    doc = _ingest(source)
    first = client.post('/api/sharing/publish', json=dict(visibility='workspace', doc_id=doc['id'], workspace_id=cloud_server.workspace_id, confirmed=True)).json()
    with database.SessionLocal() as db:
        db.query(ShareLink).delete()
        db.commit()
    response = client.post('/api/sharing/publish', json=dict(visibility='workspace', doc_id=doc['id'], workspace_id=cloud_server.workspace_id, confirmed=True))
    assert response.status_code == 409 and response.json()['error']['code'] == 'DUPLICATE_CONTENT'
    assert client.get(f"/api/sharing/links/{doc['id']}").json()['linked'] is False
    forced = client.post('/api/sharing/publish', json=dict(visibility='workspace', doc_id=doc['id'], workspace_id=cloud_server.workspace_id, confirmed=True, force_new=True))
    assert forced.status_code == 409 and forced.json()['error']['code'] == 'DUPLICATE_CONTENT'
    assert forced.json()['error']['details']['document_id'] == first['document_id']


def test_pull_rejects_path_escape_and_corruption(library, cloud_server, tmp_path, monkeypatch):
    import httpx
    from backend.app.api import sharing
    from backend.app.models import Document
    source = tmp_path / '完整性.txt'
    source.write_text('校验原文')
    doc = _ingest(source)
    shared = client.post('/api/sharing/publish', json=dict(visibility='workspace', doc_id=doc['id'], workspace_id=cloud_server.workspace_id, confirmed=True)).json()
    request = dict(cloud_doc_id=shared['document_id'], workspace_id=cloud_server.workspace_id)
    response = client.post('/api/sharing/pull', json={**request, 'target_dir': '../escape'})
    assert response.status_code == 400 and not (tmp_path / 'escape').exists()
    (library / doc['storage_path']).write_text('本机已修改')  # 当前实体不相同，进入下载校验
    original = sharing._stream_to_file
    def corrupted(resp, target, expected_hash, expected_size):
        return original(httpx.Response(200, content=b'tampered'), target, expected_hash, expected_size)
    monkeypatch.setattr(sharing, '_stream_to_file', corrupted)
    response = client.post('/api/sharing/pull', json=request)
    assert response.status_code == 422
    assert list((library / '.teamhub' / 'downloads').iterdir()) == []
    with database.SessionLocal() as db:
        assert db.query(Document).count() == 1
    assert list(library.glob('完整性*')) == [library / doc['storage_path']]


def test_pull_database_failure_removes_working_copy(library, cloud_server, tmp_path, monkeypatch):
    from sqlalchemy.orm import Session
    source = tmp_path / '事务.txt'
    source.write_text('事务测试')
    doc = _ingest(source)
    shared = client.post('/api/sharing/publish', json=dict(visibility='workspace', doc_id=doc['id'], workspace_id=cloud_server.workspace_id, confirmed=True)).json()
    (library / doc['storage_path']).write_text('本机已修改')  # 必须进入真正的新资料收录事务
    def fail(self):
        raise OSError('commit failure')
    monkeypatch.setattr(Session, 'commit', fail)
    response = client.post('/api/sharing/pull', json=dict(cloud_doc_id=shared['document_id'], workspace_id=cloud_server.workspace_id))
    assert response.status_code == 422
    assert list(library.glob('事务*')) == [library / doc['storage_path']]


def test_submit_rejects_different_server(library, cloud_server, tmp_path):
    source = tmp_path / '来源.txt'
    source.write_text('来源绑定')
    doc = _ingest(source)
    client.post('/api/sharing/publish', json=dict(visibility='workspace', doc_id=doc['id'], workspace_id=cloud_server.workspace_id, confirmed=True))
    config = database.load_library_config()
    config['cloud_api_url'] = 'http://127.0.0.1:1'
    database.save_library_config(config)
    response = client.post('/api/sharing/submit-version', json=dict(doc_id=doc['id'], confirmed=True))
    assert response.status_code == 409


def test_two_members_publish_pull_update_and_history(library, cloud_server, tmp_path):
    from backend.app.api.sharing import cloud_req
    source = tmp_path / '成员协作.txt'
    source.write_text('A 的初稿')
    doc_a = _ingest(source)
    published = client.post('/api/sharing/publish', json=dict(visibility='workspace', doc_id=doc_a['id'], workspace_id=cloud_server.workspace_id, confirmed=True)).json()
    lib_b = tmp_path / 'B-library'
    lib_b.mkdir()
    settings.library_path = str(lib_b)
    database.rebind_engine()
    database.init_db()
    database.save_library_config({'cloud_api_url': cloud_server.url})
    assert client.post('/api/sync/login', json={'email': 'member-b@example.com', 'password': 'e2e-pass-123'}).status_code == 200
    fetched = client.post('/api/sharing/pull', json=dict(cloud_doc_id=published['document_id'], workspace_id=cloud_server.workspace_id)).json()
    working_b = lib_b / fetched['storage_path']
    assert working_b.read_text() == 'A 的初稿'
    working_b.write_text('B 的修订')
    updated = client.post('/api/sharing/submit-version', json=dict(doc_id=fetched['document_id'], confirmed=True))
    assert updated.status_code == 200, updated.text
    settings.library_path = str(library)
    database.rebind_engine()
    config_a = database.load_library_config()
    versions = cloud_req('GET', f"{cloud_server.url}/api/workspaces/{cloud_server.workspace_id}/documents/{published['document_id']}/versions", config_a).json()
    assert [v['version_number'] for v in versions] == [2, 1]
    assert versions[0]['author_id'] != versions[1]['author_id']
    assert [v['version_number'] for v in versions if v['is_current']] == [2]
    assert (library / doc_a['storage_path']).read_text() == 'A 的初稿'
    old = client.post('/api/sharing/pull', json=dict(cloud_doc_id=published['document_id'], workspace_id=cloud_server.workspace_id, version_id=published['version_id'])).json()
    assert (library / old['storage_path']).read_text() == 'A 的初稿'
