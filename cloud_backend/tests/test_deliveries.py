"""Fixed deliveries, independent access and durable recall; all synthetic data."""
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from app.database import SessionLocal
from app.models import User, TeamMember
from app.core.security import create_access_token
from main import app
from tests.test_m4_publications import space, _upload, _as, _uid

client = TestClient(app)


@pytest.fixture
def group(space):
    with SessionLocal() as db:
        users = []
        for name, role in [('B', 'member'), ('C', 'member'), ('M', 'admin')]:
            user = User(id=_uid(), email=_uid()+'@test.local', display_name=name,
                        hashed_password='x', is_superuser=name == 'M')
            db.add(user)
            db.flush()
            db.add(TeamMember(team_id=space['ws1'].team_id, user_id=user.id, role=role))
            users.append(user)
        db.commit()
        for user in users:
            db.refresh(user)
        return {**space, **dict(zip(['b', 'c', 'm'], users))}


def send(group, recipients=None, **changes):
    _as(group['u1'])
    ws = group['ws1'].id
    up = client.post(f'/api/workspaces/{ws}/uploads?filename=f.txt&purpose=delivery', content=b'fixed').json()
    body = dict(request_id=_uid(), recipient_ids=recipients or [group['b'].id],
                upload_id=up['upload_id'], content_hash=up['sha256'], original_filename='f.txt', doc_type='file')
    body.update(changes)
    result = client.post(f'/api/workspaces/{ws}/deliveries', json=body)
    return result, body


def test_private_receipt_read_recall_and_replay(group):
    result, body = send(group)
    assert result.status_code == 200, result.text
    delivery = result.json()
    base = f"/api/workspaces/{group['ws1'].id}/deliveries"
    path = f"{base}/{delivery['id']}/items/{delivery['items'][0]['id']}/download"
    for outsider in ['c', 'm']:
        _as(group[outsider])
        assert client.get(base).json()['total'] == 0
        assert client.get(path).status_code == 404
        assert client.get(f"{base}/{delivery['id']}").status_code == 404
    _as(group['b'])
    assert client.get(base+'/unread-count').json()['count'] == 1
    assert client.get(path).content == b'fixed'
    assert client.post(f"{base}/{delivery['id']}/read").status_code == 200
    assert client.get(base+'/unread-count').json()['count'] == 0
    _as(group['u1'])
    assert client.post(f"{base}/{delivery['id']}/recall").status_code == 200
    assert client.post(base, json=body).json()['status'] == 'recalled'
    body['note'] = 'changed'
    assert client.post(base, json=body).status_code == 409
    _as(group['b'])
    assert client.get(path).status_code == 410
    assert client.get(base).json()['items'][0]['status'] == 'recalled'


def test_invalid_recipient_atomic_and_quota(group):
    response, _ = send(group, [group['b'].id, _uid()])
    assert response.status_code == 422
    _as(group['b'])
    assert client.get(f"/api/workspaces/{group['ws1'].id}/deliveries").json()['total'] == 0
    from app.models import Team
    with SessionLocal() as db:
        db.get(Team, group['ws1'].team_id).storage_quota = 1
        db.commit()
    response, _ = send(group)
    assert response.status_code == 413
    # 被配额拒绝的附件必须停留在有界暂存区，不能产生无法回收的永久对象。
    from app.core import object_store
    assert not list((object_store._root() / "objects").glob("*/*"))


def test_concurrent_idempotent_send_and_recall(group):
    response, body = send(group)
    assert response.status_code == 200, response.text
    base = f"/api/workspaces/{group['ws1'].id}/deliveries"
    body['request_id'] = _uid()  # race two first submissions, not only replay an existing row
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: client.post(base, json=body), range(2)))
    assert all(r.status_code == 200 for r in results)
    assert len({r.json()['id'] for r in results}) == 1
    assert client.get(base+'?box=outbox').json()['total'] == 2


def test_commit_failure_has_no_partial_recipients(group, monkeypatch):
    from sqlalchemy.orm import Session
    original = Session.commit
    with monkeypatch.context() as patch:
        patch.setattr(Session, 'commit', lambda self: (_ for _ in ()).throw(OSError('disk failure')))
        with pytest.raises(OSError):
            send(group)
    _as(group['b'])
    assert client.get(f"/api/workspaces/{group['ws1'].id}/deliveries").json()['total'] == 0
