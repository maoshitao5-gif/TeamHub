"""The native local maintainer entry point only writes explicitly prepared databases."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.security import verify_password
from app.database import Base
from app.models import ServerMetadata


CLOUD_ROOT = Path(__file__).resolve().parents[1]
TEST_PASSWORD = "native-test-$'\\\"密碼😀"


@pytest.fixture
def target(tmp_path):
    database = tmp_path / "cloud database #状态.db"
    engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as db:
        db.add(ServerMetadata(id=1))
        db.commit()
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
        connection.execute(text("INSERT INTO alembic_version VALUES ('005')"))
    engine.dispose()
    return database


def payload(**changes):
    return {"email": "admin@mvp.example", "name": "管理员", "password": TEST_PASSWORD,
            "role": "admin", **changes}


def run_setup(database, request=None, *arguments, raw=None, environment=None):
    env = {**os.environ, "TEAMHUB_SANDBOX": "1", "DATABASE_URL": f"sqlite:///{database}",
           "STORAGE_DIR": str(database.parent / "storage")}
    if environment:
        for name, value in environment.items():
            if value is None:
                env.pop(name, None)
            else:
                env[name] = value
    result = subprocess.run(
        [sys.executable, "account_setup.py", *arguments], cwd=CLOUD_ROOT, env=env,
        input=raw if raw is not None else json.dumps(request, ensure_ascii=False),
        capture_output=True, text=True, encoding="utf-8", timeout=20,
    )
    assert result.stderr == ""
    return result, json.loads(result.stdout)


def snapshot(database):
    return {file.name: file.read_bytes() for file in database.parent.iterdir() if file.is_file()}


def assert_rejected(result, response, password=TEST_PASSWORD):
    assert result.returncode == 1
    assert set(response) == {"ok", "error"}
    assert response["ok"] is False
    assert isinstance(response["error"], str) and response["error"]
    assert password not in result.stdout + result.stderr


def test_status_is_readonly_and_does_not_read_stdin(target):
    before = snapshot(target)
    result, response = run_setup(target, None, "--status", raw="not JSON")
    assert result.returncode == 0
    assert response == {"ok": True, "has_admin": False, "team_exists": False}
    assert snapshot(target) == before


def test_status_reads_active_wal_without_changing_schema_identity_or_accounts(target):
    live = sqlite3.connect(target)
    try:
        assert live.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        live.execute("BEGIN")
        live.execute("SELECT COUNT(*) FROM users").fetchone()
        assert run_setup(target, payload())[0].returncode == 0
        live.rollback()
        assert Path(str(target) + "-wal").stat().st_size > 32

        def business_snapshot():
            queries = (
                "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name",
                "SELECT * FROM server_metadata ORDER BY id",
                "SELECT * FROM alembic_version ORDER BY version_num",
                "SELECT * FROM users ORDER BY id",
                "SELECT * FROM teams ORDER BY id",
                "SELECT * FROM team_members ORDER BY team_id,user_id",
                "SELECT * FROM workspaces ORDER BY id",
                "PRAGMA schema_version", "PRAGMA data_version",
            )
            return tuple(live.execute(query).fetchall() for query in queries)

        before = business_snapshot()
        result, response = run_setup(target, None, "--status")
        assert result.returncode == 0
        assert response == {"ok": True, "has_admin": True, "team_exists": True}
        # SQLite may maintain WAL/SHM coordination files; business data never changes.
        assert business_snapshot() == before
    finally:
        live.close()


def test_first_admin_and_member_have_correct_roles_and_login_passwords(target):
    with sqlite3.connect(target) as db:
        original_identity = db.execute("SELECT * FROM server_metadata").fetchone()
    admin_result, admin = run_setup(target, payload())
    assert admin_result.returncode == 0
    assert set(admin) == {"ok", "email", "role", "user_id", "team_id", "workspace_id"}
    assert admin["ok"] is True and admin["role"] == "admin"
    assert admin["email"] == "admin@mvp.example"
    assert TEST_PASSWORD not in admin_result.stdout
    member_result, member = run_setup(target, payload(email="member@mvp.example", name="成员", role="member"))
    assert member_result.returncode == 0
    assert member["role"] == "member"
    assert (admin["team_id"], admin["workspace_id"]) == (member["team_id"], member["workspace_id"])
    with sqlite3.connect(target) as db:
        assert db.execute("SELECT slug,name FROM teams").fetchall() == [("mvp", "MVP团队")]
        assert db.execute("SELECT name FROM workspaces").fetchall() == [("组织资料",)]
        assert db.execute("SELECT * FROM server_metadata").fetchone() == original_identity
        for account, role, system_admin in ((admin, "owner", 1), (member, "member", 0)):
            stored = db.execute("SELECT hashed_password,is_superuser FROM users WHERE id=?",
                                (account["user_id"],)).fetchone()
            assert stored[1] == system_admin
            assert verify_password(TEST_PASSWORD, stored[0])
            assert db.execute("SELECT role FROM team_members WHERE user_id=?",
                              (account["user_id"],)).fetchone() == (role,)
    before = snapshot(target)
    result, response = run_setup(target, None, "--status")
    assert result.returncode == 0
    assert response == {"ok": True, "has_admin": True, "team_exists": True}
    assert snapshot(target) == before


def test_first_member_is_rejected_and_rolls_back(target):
    before = snapshot(target)
    result, response = run_setup(target, payload(role="member"))
    assert_rejected(result, response)
    assert "管理员" in response["error"]
    assert snapshot(target) == before
    with sqlite3.connect(target) as db:
        assert db.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_duplicate_cannot_replace_password_name_or_elevate_member(target):
    assert run_setup(target, payload())[0].returncode == 0
    _, member = run_setup(target, payload(email="member@mvp.example", name="成员", role="member"))
    before = snapshot(target)
    replacement = "replacement-secret-$密碼"
    result, response = run_setup(target, payload(email="member@mvp.example", name="替换名称", password=replacement))
    assert_rejected(result, response, replacement)
    assert "已存在" in response["error"]
    assert snapshot(target) == before
    with sqlite3.connect(target) as db:
        row = db.execute("SELECT hashed_password,display_name,is_superuser FROM users WHERE id=?",
                         (member["user_id"],)).fetchone()
        assert row[1:] == ("成员", 0)
        assert verify_password(TEST_PASSWORD, row[0])
        assert not verify_password(replacement, row[0])
        assert db.execute("SELECT role FROM team_members WHERE user_id=?",
                          (member["user_id"],)).fetchone() == ("member",)


@pytest.mark.parametrize("changes", [
    {"role": "owner"}, {"role": "viewer"}, {"role": True}, {"role": None},
    {"email": "admin@mvp.local"}, {"email": "bad-email"}, {"email": 17},
    {"email": "a" * 256 + "@mvp.example"}, {"name": ""}, {"name": " "},
    {"name": "a" * 101}, {"name": 17}, {"name": "bad\nname"},
    {"password": "short"}, {"password": "a" * 73}, {"password": "😀" * 19},
    {"password": 12345678}, {"password": None}, {"password": "contains\x00null"},
    {"system_admin": True}, {"team_slug": "another-team"},
])
def test_invalid_inputs_are_rejected_without_echo_or_mutation(target, changes):
    before = snapshot(target)
    request = payload(**changes)
    result, response = run_setup(target, request)
    assert_rejected(result, response, str(request["password"]))
    assert snapshot(target) == before


@pytest.mark.parametrize("raw", [
    "{bad JSON}", "[]", "null", "true", "{}",
    '{"email":"admin@mvp.example","name":"管理员","password":"native-secret-123",'
    '"role":"member","role":"admin"}',
    "x" * 16385,
])
def test_invalid_json_and_duplicate_keys_never_echo_payload(target, raw):
    before = snapshot(target)
    result, response = run_setup(target, raw=raw)
    assert_rejected(result, response, "native-secret-123")
    assert raw not in result.stdout
    assert snapshot(target) == before


@pytest.mark.parametrize("arguments", [(), ("--status",)])
def test_missing_database_is_never_created(tmp_path, arguments):
    database = tmp_path / "missing.db"
    before = snapshot(database)
    result, response = run_setup(database, payload(), *arguments)
    assert_rejected(result, response)
    assert not database.exists()
    assert snapshot(database) == before


@pytest.mark.parametrize("mutation", [
    "UPDATE server_metadata SET schema_version='004'",
    "UPDATE server_metadata SET protocol_major=2",
    "UPDATE server_metadata SET server_id='invalid'",
    "DELETE FROM server_metadata",
    "UPDATE alembic_version SET version_num='004'",
    "DROP TABLE alembic_version",
    "DROP TABLE document_access",
    "DROP TABLE users",
    "ALTER TABLE cloud_documents DROP COLUMN access_revision",
])
@pytest.mark.parametrize("arguments", [(), ("--status",)])
def test_old_or_incomplete_schema_is_rejected_without_any_write(target, mutation, arguments):
    with sqlite3.connect(target) as db:
        db.execute(mutation)
    before = snapshot(target)
    result, response = run_setup(target, payload(), *arguments)
    assert_rejected(result, response)
    assert snapshot(target) == before


@pytest.mark.parametrize("environment", [
    {"TEAMHUB_SANDBOX": None}, {"TEAMHUB_SANDBOX": "0"}, {"DATABASE_URL": None},
    {"DATABASE_URL": "sqlite:///:memory:"}, {"DATABASE_URL": "sqlite:///relative.db"},
    {"DATABASE_URL": "sqlite:///file:unknown.db?mode=rwc&uri=true"},
    {"DATABASE_URL": "postgresql://localhost/database"},
])
def test_explicit_local_sandbox_target_is_required(target, environment):
    before = snapshot(target)
    result, response = run_setup(target, payload(), environment=environment)
    assert_rejected(result, response)
    assert snapshot(target) == before


def test_unknown_cli_arguments_are_not_echoed(target):
    before = snapshot(target)
    result, response = run_setup(target, payload(), "--password", TEST_PASSWORD)
    assert_rejected(result, response)
    assert snapshot(target) == before


def test_database_removed_after_preflight_is_not_recreated(target):
    from account_setup import AccountRequest, SetupError, check_status, create_account

    assert check_status(target)["ok"] is True
    target.rename(target.with_name("moved.db"))
    request = AccountRequest(**payload())
    with pytest.raises(SetupError) as failure:
        create_account(target, request)
    assert TEST_PASSWORD not in str(failure.value)
    assert not target.exists()


def test_unexpected_provision_failure_rolls_back_partial_write_without_echo(target, monkeypatch):
    from account_setup import AccountRequest, SetupError, create_account
    from app.models import Team
    import provision_mvp

    def failing_provision(db, **_fields):
        db.execute(text("BEGIN IMMEDIATE"))
        db.add(Team(name="未提交团队", slug="uncommitted"))
        db.flush()
        raise RuntimeError(TEST_PASSWORD)

    monkeypatch.setattr(provision_mvp, "provision", failing_provision)
    before = snapshot(target)
    with pytest.raises(SetupError) as failure:
        create_account(target, AccountRequest(**payload()))
    assert TEST_PASSWORD not in str(failure.value)
    assert snapshot(target) == before
