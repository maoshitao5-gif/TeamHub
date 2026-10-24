"""维护者在共享服务器本机开通 MVP 成员。密码由终端隐式输入，不开放公开注册。"""
import argparse
import getpass
import json


def provision(db, email, name, password, team_slug, team_name, workspace_name, role='member', system_admin=False):
    from sqlalchemy import text
    from app.models import User, Team, TeamMember, Workspace
    from app.core.security import hash_password
    if len(password) < 8 or len(password.encode('utf-8')) > 72:
        raise ValueError('密码至少 8 字符且 UTF-8 编码不超过 72 字节')
    if role not in ('owner', 'admin', 'member', 'viewer'):
        raise ValueError('未知成员角色')
    if '@' not in email or not all(value.strip() for value in (name, team_slug, team_name, workspace_name)):
        raise ValueError('邮箱及名称不能为空且须有效')
    db.execute(text('BEGIN IMMEDIATE'))
    if db.query(User).filter_by(email=email).first():
        raise ValueError('该账号已存在；本命令不重置密码或提升现有账号权限')
    team = db.query(Team).filter_by(slug=team_slug).first()
    if not team:
        if role not in ('admin', 'owner'):
            raise ValueError('首次创建团队必须开通 admin 或 owner')
        team = Team(name=team_name, slug=team_slug)
        db.add(team); db.flush()
    workspace = db.query(Workspace).filter_by(team_id=team.id, name=workspace_name).first()
    if not workspace:
        workspace = Workspace(team_id=team.id, name=workspace_name)
        db.add(workspace); db.flush()
    user = User(email=email, display_name=name, hashed_password=hash_password(password), is_superuser=system_admin)
    db.add(user); db.flush()
    db.add(TeamMember(team_id=team.id, user_id=user.id, role=role))
    result = dict(user_id=user.id, team_id=team.id, workspace_id=workspace.id, role=role, system_admin=system_admin)
    db.commit()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--email', required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--team-slug', default='mvp')
    parser.add_argument('--team-name', default='MVP团队')
    parser.add_argument('--workspace-name', default='组织资料')
    parser.add_argument('--role', choices=['owner', 'admin', 'member', 'viewer'], default='member')
    parser.add_argument('--system-admin', action='store_true')
    args = parser.parse_args()
    password = getpass.getpass('新账号密码：')
    if password != getpass.getpass('再次输入：'):
        parser.error('两次密码不一致')
    from app.database import init_db, SessionLocal
    init_db()
    with SessionLocal() as db:
        try:
            result = provision(db, password=password, **vars(args))
        except ValueError as exc:
            db.rollback(); parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
