"""系统复验：真实令牌下的团队、空间与成员管理边界。"""
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from app.core.security import create_access_token
from app.database import SessionLocal
from app.models import User, TeamMember
from main import app

client = TestClient(app)


@pytest.fixture
def management():
    users = {}
    with SessionLocal() as db:
        for role in ('owner', 'admin', 'member', 'outsider'):
            user = User(email=uuid.uuid4().hex + '@management.example', display_name=role, hashed_password='unused')
            db.add(user); db.flush()
            users[role] = {'id': user.id, 'email': user.email, 'headers': {'Authorization': 'Bearer ' + create_access_token({'sub': user.id})}}
        db.commit()
    team = client.post('/api/teams', headers=users['owner']['headers'], json={'name': '测试团队', 'slug': 'test-' + uuid.uuid4().hex[:12]})
    assert team.status_code == 201, team.text
    for role in ('admin', 'member'):
        response = client.post(f"/api/teams/{team.json()['id']}/members", headers=users['owner']['headers'], json={'email': users[role]['email'], 'role': role})
        assert response.status_code == 201, response.text
    return team.json()['id'], users


def test_team_workspace_and_profile_lifecycle(management):
    team, users = management
    base = f'/api/teams/{team}'
    owner, member = users['owner']['headers'], users['member']['headers']
    assert client.put('/api/users/me', headers=member, json={'display_name': '修改昵称'}).status_code == 200
    assert client.get('/api/users/me', headers=member).json()['display_name'] == '修改昵称'
    assert client.put(base, headers=member, json={'name': '越权'}).status_code == 403
    assert client.put(base, headers=owner, json={'name': '新团队名'}).json()['name'] == '新团队名'
    assert client.post(base + '/workspaces', headers=member, json={'name': '越权空间'}).status_code == 403
    created = client.post(base + '/workspaces', headers=owner, json={'name': '测试空间'})
    assert created.status_code == 201, created.text
    ws = '/api/workspaces/' + created.json()['id']
    assert client.get(ws, headers=member).status_code == 200
    assert client.get(ws, headers=users['outsider']['headers']).status_code in (403, 404)
    assert client.put(ws, headers=owner, json={'name': '已改名'}).json()['name'] == '已改名'
    assert client.delete(ws, headers=owner).status_code == 204
    assert client.delete(base + '/members/' + users['member']['id'], headers=owner).status_code == 204
    assert client.get(base, headers=member).status_code == 403
    assert client.delete(base, headers=owner).status_code == 204


@pytest.mark.parametrize('operation', ['invite', 'update'])
def test_invalid_member_roles_are_rejected(management, operation):
    team, users = management
    base = f'/api/teams/{team}/members'
    if operation == 'invite':
        response = client.post(base, headers=users['owner']['headers'], json={'email': users['outsider']['email'], 'role': 'superadmin'})
    else:
        response = client.put(base + '/' + users['member']['id'], headers=users['owner']['headers'], json={'role': 'superadmin'})
    assert response.status_code == 422, response.text


@pytest.mark.parametrize('operation', ['self_promote', 'demote_owner', 'invite_owner'])
def test_admin_cannot_assign_or_modify_owner(management, operation):
    team, users = management
    base = f'/api/teams/{team}/members'
    headers = users['admin']['headers']
    if operation == 'invite_owner':
        response = client.post(base, headers=headers, json={'email': users['outsider']['email'], 'role': 'owner'})
    else:
        target = 'admin' if operation == 'self_promote' else 'owner'
        response = client.put(base + '/' + users[target]['id'], headers=headers, json={'role': 'owner' if operation == 'self_promote' else 'member'})
    assert response.status_code == 403, response.text
    with SessionLocal() as db:
        assert db.get(TeamMember, (team, users['owner']['id'])).role == 'owner'
        assert db.get(TeamMember, (team, users['admin']['id'])).role == 'admin'


def test_last_owner_retained_until_explicit_transfer(management):
    team, users = management
    base = f'/api/teams/{team}/members'
    owner = users['owner']['headers']
    assert client.put(base + '/' + users['owner']['id'], headers=owner, json={'role': 'member'}).status_code == 409
    assert client.put(base + '/' + users['member']['id'], headers=owner, json={'role': 'owner'}).status_code == 200
    assert client.put(base + '/' + users['owner']['id'], headers=owner, json={'role': 'member'}).status_code == 200


def test_concurrent_owner_demotions_leave_one_owner(management):
    team, users = management
    base = f'/api/teams/{team}/members'
    assert client.put(base + '/' + users['member']['id'], headers=users['owner']['headers'], json={'role': 'owner'}).status_code == 200
    ready = Barrier(2)

    def demote(role):
        ready.wait(timeout=5)
        return client.put(base + '/' + users[role]['id'], headers=users[role]['headers'], json={'role': 'member'}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(demote, ['owner', 'member'])) == [200, 409]
    with SessionLocal() as db:
        assert db.query(TeamMember).filter_by(team_id=team, role='owner').count() == 1


def test_inactive_accounts_cannot_be_invited_or_receive_ownership(management):
    team, users = management
    base = f'/api/teams/{team}/members'
    with SessionLocal() as db:
        for key in ['member', 'outsider']:
            db.get(User, users[key]['id']).is_active = False
        db.commit()
    headers = users['owner']['headers']
    assert client.post(base, headers=headers, json={'email': users['outsider']['email'], 'role': 'member'}).status_code == 400
    assert client.put(base + '/' + users['member']['id'], headers=headers, json={'role': 'owner'}).status_code == 400
