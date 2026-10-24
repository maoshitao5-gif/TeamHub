"""本机维护者账号开通入口：原生界面经 stdin 传入 JSON，密码不进入命令参数。"""
from contextlib import closing
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Literal, TypedDict, cast
from urllib.parse import quote
from uuid import UUID


TEAM_SLUG = "mvp"
TEAM_NAME = "MVP团队"
WORKSPACE_NAME = "组织资料"
MAX_INPUT_CHARACTERS = 16384
SCHEMA_ERROR = "云端数据库尚未达到当前 005 版本；请先初始化或显式升级共享云服务数据库。"
AccountRole = Literal["admin", "member"]


class StatusResult(TypedDict):
    ok: Literal[True]
    has_admin: bool
    team_exists: bool


class AccountResult(TypedDict):
    ok: Literal[True]
    email: str
    role: AccountRole
    user_id: str
    team_id: str
    workspace_id: str


class ErrorResult(TypedDict):
    ok: Literal[False]
    error: str

# Besides version markers, verify the tables this entry point reads and writes.
REQUIRED_COLUMNS = {
    "server_metadata": {"id", "server_id", "schema_version", "protocol_major"},
    "alembic_version": {"version_num"},
    "users": {"id", "email", "hashed_password", "display_name", "is_active", "is_superuser",
              "created_at", "updated_at"},
    "teams": {"id", "name", "slug", "storage_quota", "storage_used", "created_at", "updated_at"},
    "team_members": {"team_id", "user_id", "role", "joined_at"},
    "workspaces": {"id", "team_id", "name", "description", "created_at", "updated_at"},
    "cloud_documents": {"visibility", "access_revision"},
    "document_access": {"document_id", "user_id", "role"},
    "deliveries": {"id", "team_id", "workspace_id"},
    "delivery_items": {"id", "delivery_id"},
    "delivery_recipients": {"delivery_id", "recipient_id"},
}


class SetupError(Exception):
    """Only constant, user-safe messages may cross the CLI JSON boundary."""


@dataclass(frozen=True)
class AccountRequest:
    email: str
    name: str
    password: str = field(repr=False)
    role: AccountRole


def _target_database() -> Path:
    from sqlalchemy.engine import make_url

    if os.environ.get("TEAMHUB_SANDBOX") != "1" or not os.environ.get("DATABASE_URL"):
        raise SetupError("账号开通必须明确指定本机沙箱数据库。")
    try:
        url = make_url(os.environ["DATABASE_URL"])
    except Exception:
        raise SetupError("账号开通数据库配置无效。") from None
    if (url.drivername not in ("sqlite", "sqlite+pysqlite") or url.query
            or url.host or url.username or url.password or not url.database
            or not Path(url.database).is_absolute()):
        raise SetupError("账号开通仅支持明确指定绝对路径的本机 SQLite 数据库。")
    database = Path(url.database).resolve()
    if not database.is_file():
        raise SetupError("共享云服务数据库尚不存在；请先启动并初始化共享云服务。")
    return database


def _database_uri(database: Path, mode: Literal["ro", "rw"]) -> str:
    return "file:" + quote(str(database), safe="/") + "?mode=" + mode


def check_status(database: Path) -> StatusResult:
    """Use SQLite mode=ro, with no app imports, initialization, or schema writes."""
    try:
        with closing(sqlite3.connect(_database_uri(database, "ro"), uri=True, timeout=15)) as db:
            db.execute("BEGIN")
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not REQUIRED_COLUMNS.keys() <= tables:
                raise SetupError(SCHEMA_ERROR)
            for table, required in REQUIRED_COLUMNS.items():
                # Table names come exclusively from the constant allowlist above.
                columns = {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}
                if not required <= columns:
                    raise SetupError(SCHEMA_ERROR)
            identities = db.execute("SELECT id,server_id,schema_version,protocol_major FROM server_metadata").fetchall()
            revisions = db.execute("SELECT version_num FROM alembic_version").fetchall()
            if (len(identities) != 1 or identities[0][0] != 1 or identities[0][2:] != ("005", 1)
                    or revisions != [("005",)]):
                raise SetupError(SCHEMA_ERROR)
            try:
                UUID(identities[0][1])
            except (ValueError, TypeError, AttributeError):
                raise SetupError("共享云服务身份无效；请先检查共享云服务数据库。") from None
            team_exists = bool(db.execute("SELECT EXISTS(SELECT 1 FROM teams WHERE slug=?)",
                                          (TEAM_SLUG,)).fetchone()[0])
            has_admin = bool(db.execute(
                "SELECT EXISTS(SELECT 1 FROM users u JOIN team_members tm ON tm.user_id=u.id "
                "JOIN teams t ON t.id=tm.team_id WHERE t.slug=? AND u.is_active=1 "
                "AND u.is_superuser=1 AND tm.role IN ('owner','admin'))", (TEAM_SLUG,),
            ).fetchone()[0])
            return {"ok": True, "has_admin": has_admin, "team_exists": team_exists}
    except sqlite3.Error:
        raise SetupError("无法只读检查共享云服务数据库；请确认数据库可用且已完成初始化。") from None


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SetupError("账号开通信息格式无效；字段不能重复。")
        result[key] = value
    return result


def read_request() -> AccountRequest:
    from pydantic import EmailStr, TypeAdapter, ValidationError

    raw = sys.stdin.read(MAX_INPUT_CHARACTERS + 1)
    if len(raw) > MAX_INPUT_CHARACTERS:
        raise SetupError("账号开通信息过长。")
    try:
        payload = json.loads(raw, object_pairs_hook=_unique_json_object)
    except (ValueError, UnicodeError):
        raise SetupError("账号开通信息不是有效的 JSON。") from None
    if not isinstance(payload, dict) or set(payload) != {"email", "name", "password", "role"}:
        raise SetupError("账号开通须且仅须提供邮箱、姓名、密码和明确选择的角色。")
    if not all(isinstance(payload[field], str) for field in ("email", "name", "password", "role")):
        raise SetupError("邮箱、姓名、密码和角色必须是文本。")
    if payload["role"] not in ("admin", "member"):
        raise SetupError("请明确选择管理员或普通成员。")
    email, name, password = payload["email"].strip(), payload["name"].strip(), payload["password"]
    if not email or len(email) > 255:
        raise SetupError("邮箱不能为空且不能超过 255 字符。")
    try:
        email = str(TypeAdapter(EmailStr).validate_python(email))
    except ValidationError:
        raise SetupError("请输入有效邮箱，例如 admin@mvp.example。") from None
    if not name or len(name) > 100 or any(ord(character) < 32 or ord(character) == 127 for character in name):
        raise SetupError("姓名须为 1 至 100 字符，且不能包含控制字符。")
    try:
        name.encode("utf-8")
        password_bytes = password.encode("utf-8")
    except UnicodeError:
        raise SetupError("姓名或密码包含无效字符。") from None
    if len(password) < 8 or len(password_bytes) > 72 or "\x00" in password:
        raise SetupError("密码至少 8 字符，UTF-8 编码不超过 72 字节，且不能包含空字符。")
    return AccountRequest(email=email, name=name, password=password, role=cast(AccountRole, payload["role"]))


def create_account(database: Path, request: AccountRequest) -> AccountResult:
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import Session
    from provision_mvp import provision

    # mode=rw prevents creating a missing database even if it disappears after preflight.
    engine = create_engine(
        "sqlite+pysqlite:///" + _database_uri(database, "rw") + "&uri=true",
        connect_args={"check_same_thread": False, "timeout": 15}, hide_parameters=True,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection: sqlite3.Connection, _record: object) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    try:
        with Session(engine) as db:
            try:
                result = provision(
                    db, email=request.email, name=request.name, password=request.password,
                    team_slug=TEAM_SLUG, team_name=TEAM_NAME, workspace_name=WORKSPACE_NAME,
                    role="owner" if request.role == "admin" else "member",
                    system_admin=request.role == "admin",
                )
            except Exception as exc:
                db.rollback()
                safe_errors = {
                    "该账号已存在；本命令不重置密码或提升现有账号权限":
                        "该账号已存在；本入口不会重置密码或提升现有账号权限。",
                    "首次创建团队必须开通 admin 或 owner":
                        "首次开通请明确选择管理员，之后再开通普通成员。",
                }
                if isinstance(exc, ValueError) and exc.args and exc.args[0] in safe_errors:
                    raise SetupError(safe_errors[exc.args[0]]) from None
                raise SetupError("账号未开通；请确认共享云服务数据库可用后重试。") from None
        return {"ok": True, "email": request.email, "role": request.role,
                "user_id": result["user_id"], "team_id": result["team_id"],
                "workspace_id": result["workspace_id"]}
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    result: StatusResult | AccountResult | ErrorResult
    try:
        if arguments not in ([], ["--status"]):
            raise SetupError("账号开通仅接受 stdin 信息或 --status 状态检查。")
        database = _target_database()
        status = check_status(database)
        result = status if arguments else create_account(database, read_request())
    except SetupError as exc:
        result = {"ok": False, "error": exc.args[0]}
    except Exception:
        # Never emit raw JSON, validation errors, SQL parameters, or exception tracebacks.
        result = {"ok": False, "error": "账号开通未完成；请检查共享云服务配置后重试。"}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
