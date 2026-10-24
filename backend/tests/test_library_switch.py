"""
切库引擎生命周期测试(M1)

验证契约 §8 "两库不混写":
切换文件库后,写入只落新库;旧库内容不变;旧引擎已 dispose。
"""
import sqlite3

import pytest

import backend.app.database as database
from backend.app.config import settings
from backend.app.models import Document


def _doc_names(db_file):
    """直接读 SQLite 文件,返回 documents 表中的 name 列表"""
    conn = sqlite3.connect(str(db_file))
    try:
        rows = conn.execute("SELECT name FROM documents").fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


@pytest.fixture
def two_libraries(tmp_path):
    """准备 A/B 两个库目录,结束后把引擎恢复到隔离默认库"""
    lib_a = tmp_path / "lib-a"
    lib_b = tmp_path / "lib-b"
    lib_a.mkdir()
    lib_b.mkdir()
    yield lib_a, lib_b
    # 恢复到一个干净的临时库,避免影响其他测试
    settings.library_path = str(tmp_path / "lib-rest")
    database.rebind_engine()
    database.init_db()


def test_switch_library_rebinds_engine(two_libraries):
    lib_a, lib_b = two_libraries

    # 绑定 A 库并写入
    settings.library_path = str(lib_a)
    database.rebind_engine()
    database.init_db()
    old_engine = database.engine

    db = database.SessionLocal()
    db.add(Document(name="甲文档"))
    db.commit()
    db.close()

    assert (lib_a / ".teamhub" / "db.sqlite").exists()
    assert _doc_names(lib_a / ".teamhub" / "db.sqlite") == {"甲文档"}

    # 切换到 B 库并写入
    settings.library_path = str(lib_b)
    database.rebind_engine()
    database.init_db()

    db = database.SessionLocal()
    db.add(Document(name="乙文档"))
    db.commit()
    db.close()

    # B 库只有乙文档;A 库仍然只有甲文档(两库不混写)
    assert _doc_names(lib_b / ".teamhub" / "db.sqlite") == {"乙文档"}
    assert _doc_names(lib_a / ".teamhub" / "db.sqlite") == {"甲文档"}

    # 旧引擎已 dispose(连接池归还)
    assert old_engine is not database.engine
    old_engine.dispose()  # 重复 dispose 不应报错


def test_switch_rejected_when_session_open(tmp_path):
    """M1-R04:持有在途会话时切库必须拒绝,旧会话不得继续写旧库"""
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)
    lib_a = tmp_path / "lib-a"
    lib_b = tmp_path / "lib-b"
    lib_a.mkdir()
    lib_b.mkdir()

    # 绑定 A 库
    settings.library_path = str(lib_a)
    database.rebind_engine()
    database.init_db()

    # 持有 A 库会话(不关闭),模拟在途请求
    db = database.SessionLocal()
    try:
        assert database.active_session_count() >= 1
        resp = client.post("/api/settings/library", json={"path": str(lib_b)})
        assert resp.status_code == 409, resp.text
        # 配置未切换到 B,旧库仍可继续被旧会话使用
        assert settings.library_path == str(lib_a)
    finally:
        db.close()

    # 会话关闭后无在途会话,可正常切库
    assert database.active_session_count() == 0
    settings.library_path = str(lib_b)
    database.rebind_engine()
    database.init_db()

    db2 = database.SessionLocal()
    try:
        db2.add(Document(name="乙文档"))
        db2.commit()
    finally:
        db2.close()
    assert _doc_names(lib_b / ".teamhub" / "db.sqlite") == {"乙文档"}
    # 切库前 A 的最终状态未被污染
    assert _doc_names(lib_a / ".teamhub" / "db.sqlite") == set()


def test_double_close_cannot_hide_other_session(two_libraries):
    a = database.SessionLocal()
    b = database.SessionLocal()
    try:
        a.close()
        a.close()
        assert database.active_session_count() == 1
        with pytest.raises(database.LibraryBusy):
            with database.library_change():
                pass
    finally:
        a.close()
        b.close()


def test_closed_session_cannot_reopen(two_libraries):
    from sqlalchemy.exc import InvalidRequestError
    session = database.SessionLocal()
    session.close()
    with pytest.raises(InvalidRequestError):
        session.query(Document).first()
    assert database.active_session_count() == 0


def test_session_creation_waits_for_library_change(two_libraries):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from backend.app.database import SessionLocal as cached_factory
    lib_a, lib_b = two_libraries
    settings.library_path = str(lib_a)
    database.rebind_engine()
    database.init_db()
    entered, finished = Event(), Event()

    def create_session():
        entered.set()
        with cached_factory() as session:
            actual = session.bind.url.database
        finished.set()
        return actual

    with ThreadPoolExecutor(max_workers=1) as pool:
        with database.library_change():
            future = pool.submit(create_session)
            assert entered.wait(2)
            assert not finished.wait(0.1)
            settings.library_path = str(lib_b)
            database.rebind_engine()
            database.init_db()
        assert future.result(timeout=2) == str(lib_b / '.teamhub/db.sqlite')


def test_switch_failure_restores_configuration_and_database(tmp_path, monkeypatch):
    from backend.app.api import settings_api
    from backend.app.api.schemas import LibrarySetup
    from fastapi import HTTPException
    lib_a, lib_b = tmp_path / 'a', tmp_path / 'b'
    settings.library_path = str(lib_a)
    database.rebind_engine()
    database.init_db()
    monkeypatch.chdir(tmp_path)
    def fail():
        raise RuntimeError('injected migration failure')
    monkeypatch.setattr(settings_api, 'init_db', fail)
    with pytest.raises(HTTPException):
        settings_api.setup_library(LibrarySetup(path=str(lib_b)))
    assert settings.library_path == str(lib_a)
    with database.SessionLocal() as session:
        session.add(Document(name='still-a'))
        session.commit()
    assert _doc_names(lib_a / '.teamhub/db.sqlite') == {'still-a'}
    assert not (tmp_path / 'config.json').exists()


def test_failed_new_library_init_only_recovers_old_partner_state(tmp_path, monkeypatch):
    """目标迁移器持续失败时，回退不重复运行迁移器，仍清理旧库遗留任务。"""
    from backend.app.api import settings_api
    from backend.app.api.schemas import LibrarySetup
    from backend.app.partner_models import PartnerSession, PartnerTask
    from fastapi import HTTPException
    lib_a, lib_b = tmp_path / 'old', tmp_path / 'new'
    settings.library_path = str(lib_a)
    database.rebind_engine()
    database.init_db()
    with database.SessionLocal() as db:
        session = PartnerSession(title='旧对话')
        db.add(session)
        db.flush()
        task = PartnerTask(session_id=session.id, request_id='old-running', preparation_id='old',
                           state='running', message='遗留任务', input_json='{}')
        db.add(task)
        db.commit()
        task_id = task.id
    calls = []
    def fail():
        calls.append(True)
        raise RuntimeError('persistent new-library migration failure')
    monkeypatch.setattr(settings_api, 'init_db', fail)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(HTTPException) as error:
        settings_api.setup_library(LibrarySetup(path=str(lib_b)))
    assert error.value.status_code == 500
    assert calls == [True]
    assert settings.library_path == str(lib_a)
    with database.SessionLocal() as db:
        assert db.get(PartnerTask, task_id).state == 'interrupted'


def test_failed_config_replace_keeps_old_bootstrap_and_binding(tmp_path, monkeypatch):
    import json
    from fastapi import HTTPException
    from backend.app.api import settings_api
    from backend.app.api.schemas import LibrarySetup
    a, b = tmp_path / 'a', tmp_path / 'b'
    settings.library_path = str(a)
    database.rebind_engine()
    database.init_db()
    monkeypatch.chdir(tmp_path)
    bootstrap = tmp_path / 'config.json'
    original = json.dumps({'library_path': str(a)})
    bootstrap.write_text(original)
    original_replace = database.os.replace
    def fail_bootstrap(source, target):
        if str(target) == 'config.json':
            raise OSError('injected bootstrap write failure')
        return original_replace(source, target)
    monkeypatch.setattr(database.os, 'replace', fail_bootstrap)
    with pytest.raises(HTTPException) as error:
        settings_api.setup_library(LibrarySetup(path=str(b)))
    assert error.value.status_code == 500
    assert settings.library_path == str(a)
    assert database.engine.url.database == str(a / '.teamhub/db.sqlite')
    assert bootstrap.read_text() == original
    assert database.active_session_count() == 0


def test_successful_switch_persists_and_preserves_user_probe_name(tmp_path, monkeypatch):
    import json
    from backend.app.api import settings_api
    from backend.app.api.schemas import LibrarySetup
    monkeypatch.chdir(tmp_path)
    b = tmp_path / 'b'
    b.mkdir()
    (b / '.teamhub_test').write_text('user-owned')
    result = settings_api.setup_library(LibrarySetup(path=str(b)))
    assert result['path'] == str(b)
    assert json.loads((tmp_path / 'config.json').read_text())['library_path'] == str(b)
    assert (b / '.teamhub_test').read_text() == 'user-owned'


def test_queued_old_background_task_does_not_touch_new_library(two_libraries, monkeypatch):
    from backend.app.api import documents
    a, b = two_libraries
    settings.library_path = str(b)
    database.rebind_engine()
    database.init_db()
    def unexpected_read(*args):
        pytest.fail('stale task must not read source or update the new library')
    monkeypatch.setattr(documents, 'calculate_sha256', unexpected_read)
    documents._compute_hash_background('old-version', str(a / 'old.txt'), str(a))
    assert database.active_session_count() == 0
