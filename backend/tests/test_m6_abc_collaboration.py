"""
M6 A/B/C 多账号端到端协作验收(蓝图 §1.3 / §6.4)

一个真实共享服务进程 + 三个独立本地后端进程(各独立个人库/端口/云端账号),
验证:
  A 发布资料 → B 拉取并补标签 → C 用标签搜索找到同一资料;
  B 尝试移除 A 新增的标签关联被拒(403,贡献者校验)。
"""
import os
import secrets
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest

REPO = Path(__file__).resolve().parents[2]
CLOUD_DIR = REPO / "cloud_backend"
CLOUD_PY = CLOUD_DIR / "venv" / "bin" / "python"
BACKEND_PY = REPO / "venv" / "bin" / "python"

ACCOUNTS = {
    "a": {"email": "a@abc.example", "password": "a-pass-123"},
    "b": {"email": "b@abc.example", "password": "b-pass-123"},
    "c": {"email": "c@abc.example", "password": "c-pass-123"},
}

SEED_SCRIPT = """
import uuid
from app.database import Base, engine, SessionLocal
from app import models
from app.core.security import hash_password

Base.metadata.create_all(bind=engine)
db = SessionLocal()
team = models.Team(id=str(uuid.uuid4()), name="ABC团队", slug="abc-" + uuid.uuid4().hex[:8])
db.add(team)
db.flush()
ws = models.Workspace(id=str(uuid.uuid4()), team_id=team.id, name="ABC空间")
db.add(ws)
for name in ("a", "b", "c", "admin"):
    u = models.User(id=str(uuid.uuid4()), email=f"{name}@abc.example",
                    hashed_password=hash_password(f"{name}-pass-123"), display_name=name.upper(), is_superuser=(name == "admin"))
    db.add(u)
    db.flush()
    db.add(models.TeamMember(team_id=team.id, user_id=u.id, role="owner" if name == "admin" else "member"))
db.commit()
print(ws.id)
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_http(url: str, timeout: float = 30.0) -> bool:
    with httpx.Client(trust_env=False, timeout=1) as probe:
        for _ in range(int(timeout / 0.2)):
            try:
                if probe.get(f"{url}/api/health").status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
    return False


@pytest.fixture(scope="module")
def cloud_server(tmp_path_factory):
    """真实共享服务子进程(独立临时数据根),种子团队/空间/A、B、C 三个成员"""
    tmp = tmp_path_factory.mktemp("abc-cloud")
    env = os.environ.copy()
    env["TEAMHUB_SANDBOX"] = "1"
    env["JWT_SECRET_KEY"] = secrets.token_hex(32)
    env["DATABASE_URL"] = f"sqlite:///{tmp}/cloud.db"
    env["STORAGE_DIR"] = str(tmp / "storage")

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
    try:
        assert _wait_http(url), "共享服务 30s 内未就绪"
        yield {"url": url, "workspace_id": ws_id, "root": tmp, "proc": proc, "env": env}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        log.close()


@pytest.fixture(scope="module")
def abc(tmp_path_factory, cloud_server):
    """三个独立本地后端进程(A/B/C),各独立个人库、端口、云端账号"""
    tmp = tmp_path_factory.mktemp("abc-local")
    cloud_url = cloud_server["url"]
    clients = {}
    procs = []

    try:
        for name in ("a", "b", "c", "admin"):
            lib = tmp / name / "library"
            lib.mkdir(parents=True)
            (lib / ".teamhub").mkdir(parents=True)
            (lib / ".teamhub" / "config.json").write_text(
                f'{{"cloud_api_url": "{cloud_url}"}}', encoding="utf-8")

            port = _free_port()
            env = os.environ.copy()
            env["TEAMHUB_SANDBOX"] = "1"
            env["LIBRARY_PATH"] = str(lib)
            env["TRASH_AUTO_CLEAN_DAYS"] = "0"

            log = open(tmp / name / "backend.log", "w")
            proc = subprocess.Popen(
                [str(BACKEND_PY), "-m", "uvicorn", "main:app",
                 "--host", "127.0.0.1", "--port", str(port)],
                cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT,
            )
            procs.append((proc, log))
            url = f"http://127.0.0.1:{port}"
            assert _wait_http(url), f"{name} 后端 30s 内未就绪"

            client = httpx.Client(base_url=url, trust_env=False, timeout=20)
            resp = client.post("/api/sync/login", json={"email": f"{name}@abc.example", "password": f"{name}-pass-123"})
            assert resp.status_code == 200, f"{name} 登录失败: {resp.text}"
            clients[name] = client

        yield {"clients": clients, "workspace_id": cloud_server["workspace_id"], "tmp": tmp, "procs": procs}
    finally:
        for c in clients.values():
            c.close()
        for proc, log in procs:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            proc.wait(timeout=5)
            log.close()


def _ingest(client, tmp_path, name, content):
    src = tmp_path / name
    src.write_text(content, encoding="utf-8")
    resp = client.post("/api/documents", json={
        "name": name, "source_path": str(src), "tags": ["本地"],
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_abc_full_collaboration(abc):
    clients = abc["clients"]
    ws = abc["workspace_id"]
    a, b, c = clients["a"], clients["b"], clients["c"]

    # 1. A 收录资料并发布到共享空间
    doc = _ingest(a, abc["tmp"], "方案.txt", "A 的初稿")
    resp = a.post("/api/sharing/publish", json={'visibility': 'workspace', "doc_id": doc["id"], "workspace_id": ws, "confirmed": True})
    assert resp.status_code == 200, resp.text
    shared_doc_id = resp.json()["document_id"]

    # 2. B 拉取到自己的个人库
    resp = b.post("/api/sharing/pull", json={"cloud_doc_id": shared_doc_id, "workspace_id": ws, "confirmed": True})
    assert resp.status_code == 200, resp.text
    b_local = resp.json()["document_id"]
    b_doc = b.get(f"/api/documents/{b_local}").json()
    assert b_doc["name"] == "方案.txt"

    # 3. B 给共享资料补标签(经本地代理)
    resp = b.post(f"/api/sharing/tags/{shared_doc_id}", json={
        "add": ["乙标签"], "workspace_id": ws,
    })
    assert resp.status_code == 200, resp.text
    links = resp.json()
    assert any(l["name"] == "乙标签" for l in links)

    # 4. C 用该标签搜索,找到同一共享资料
    resp = c.get("/api/sync/docs/search", params={"tags": "乙标签", "workspace_id": ws, "confirmed": True})
    assert resp.status_code == 200, resp.text
    items = resp.json().get("items", [])
    assert any(it["id"] == shared_doc_id for it in items), f"C 搜索未找到: {resp.text}"


def test_member_cannot_remove_others_tag(abc):
    clients = abc["clients"]
    ws = abc["workspace_id"]
    a, b = clients["a"], clients["b"]

    # A 发布资料
    doc = _ingest(a, abc["tmp"], "归属.txt", "内容")
    resp = a.post("/api/sharing/publish", json={'visibility': 'workspace', "doc_id": doc["id"], "workspace_id": ws, "confirmed": True})
    assert resp.status_code == 200, resp.text
    shared_doc_id = resp.json()["document_id"]

    # A 补一个标签,拿到自己新增的关联 tag_id
    resp = a.post(f"/api/sharing/tags/{shared_doc_id}", json={
        "add": ["甲标签"], "workspace_id": ws,
    })
    assert resp.status_code == 200, resp.text
    a_tag_id = next(l["tag_id"] for l in resp.json() if l["name"] == "甲标签")

    # B 尝试移除 A 的关联 → 403(贡献者校验,经本地代理透传)
    resp = b.post(f"/api/sharing/tags/{shared_doc_id}", json={
        "remove": [a_tag_id], "workspace_id": ws,
    })
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "FORBIDDEN"


def test_versions_deletion_admin_and_restored_service(abc, cloud_server, tmp_path):
    from backend.app.core.backup import backup_cloud, restore_cloud, backup_library, restore_library
    import sqlite3
    a, b, c, admin = (abc['clients'][name] for name in ('a', 'b', 'c', 'admin'))
    ws = abc['workspace_id']
    source = abc['tmp'] / '整体文件夹'; source.mkdir(); (source / '空目录').mkdir()
    (source / '图纸.txt').write_text('图纸 v1')
    response = a.post('/api/documents', json={'source_path': str(source), 'name': '整体文件夹', 'tags': ['设备']})
    assert response.status_code == 200, response.text
    doc = response.json()
    private = _ingest(a, abc['tmp'], '私人资料.txt', '私有内容')
    assert c.get('/api/sync/docs/search', params={'q': '私人资料', 'workspace_id': ws}).json()['total'] == 0
    result = a.post('/api/sharing/publish', json={'visibility': 'workspace', 'doc_id': doc['id'], 'workspace_id': ws, 'confirmed': True})
    assert result.status_code == 200, result.text
    shared = result.json()['document_id']
    first = result.json()['version_id']
    working = abc['tmp'] / 'a/library' / doc['storage_path'] / '图纸.txt'
    working.write_text('图纸 v2')
    result = a.post('/api/sharing/submit-version', json={'doc_id': doc['id'], 'workspace_id': ws, 'confirmed': True})
    assert result.status_code == 200, result.text
    current = result.json()['version_id']
    assert current != first
    pulled = b.post('/api/sharing/pull', json={'cloud_doc_id': shared, 'workspace_id': ws, 'version_id': first})
    assert pulled.status_code == 200, pulled.text
    b_doc = b.get('/api/documents/' + pulled.json()['document_id']).json()
    assert (abc['tmp'] / 'b/library' / b_doc['storage_path'] / '图纸.txt').read_text() == '图纸 v1'
    assert a.delete('/api/documents/' + doc['id']).status_code == 200
    assert len(c.get(f'/api/sync/cloud-docs/{shared}/versions', params={'workspace_id': ws}).json()) == 2
    assert b.post(f'/api/sharing/documents/{shared}/purge', params={'workspace_id': ws}).status_code == 403
    assert admin.post(f'/api/sharing/documents/{shared}/trash', params={'workspace_id': ws}).status_code == 200
    assert c.get(f'/api/sharing/tags/{shared}', params={'workspace_id': ws}).status_code == 404
    assert admin.post(f'/api/sharing/documents/{shared}/restore', params={'workspace_id': ws}).status_code == 200
    a_id = a.get('/api/sync/status').json()['user']['id']
    assert admin.post(f'/api/sharing/members/{a_id}/deactivate', params={'workspace_id': ws}).status_code == 200
    assert c.get(f'/api/sync/cloud-docs/{shared}/versions', params={'workspace_id': ws}).status_code == 200

    # 停止本测试自己的全部服务，完整离线备份，恢复到全新根并重新启动。
    for proc, _ in abc['procs']:
        proc.terminate(); proc.wait(timeout=10)
    cloud_server['proc'].terminate(); cloud_server['proc'].wait(timeout=10)
    personal_backup = tmp_path / 'personal-backup'
    backup_library(abc['tmp'] / 'b/library', personal_backup)
    restored_personal = tmp_path / 'new-personal'
    restore_library(personal_backup, restored_personal)
    assert (restored_personal / b_doc['storage_path'] / '图纸.txt').read_text() == '图纸 v1'
    cloud_backup = tmp_path / 'cloud-backup'
    root = cloud_server['root']
    backup_cloud(root / 'cloud.db', root / 'storage', cloud_backup)
    new_root = tmp_path / 'new-cloud'
    restore_cloud(cloud_backup, new_root / 'cloud.db', new_root / 'storage')
    with sqlite3.connect(new_root / 'cloud.db') as conn:
        assert conn.execute('SELECT current_version_id FROM cloud_documents WHERE id=?', (shared,)).fetchone()[0] == current
        assert conn.execute('SELECT is_active FROM users WHERE id=?', (a_id,)).fetchone()[0] == 0
    port = _free_port(); url = f'http://127.0.0.1:{port}'
    env = {**cloud_server['env'], 'DATABASE_URL': f'sqlite:///{new_root}/cloud.db', 'STORAGE_DIR': str(new_root / 'storage')}
    with (new_root / 'cloud.log').open('w') as log:
        proc = subprocess.Popen([str(CLOUD_PY), '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', str(port)],
                                cwd=CLOUD_DIR, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            assert _wait_http(url)
            with httpx.Client(base_url=url, trust_env=False, timeout=20) as remote:
                login = remote.post('/api/auth/login', json=ACCOUNTS['c'])
                assert login.status_code == 200, login.text
                remote.headers['Authorization'] = 'Bearer ' + login.json()['access_token']
                history = remote.get(f'/api/workspaces/{ws}/documents/{shared}/versions')
                assert history.status_code == 200 and len(history.json()) == 2
                for vid in (first, current):
                    downloaded = remote.get(f'/api/workspaces/{ws}/documents/{shared}/versions/{vid}/download')
                    assert downloaded.status_code == 200, downloaded.text
                    assert len(downloaded.content) > 0
        finally:
            proc.terminate(); proc.wait(timeout=10)
