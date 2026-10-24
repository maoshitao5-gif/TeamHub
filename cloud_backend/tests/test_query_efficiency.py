"""List query budgets use only the isolated test database and synthetic records."""
from contextlib import contextmanager
from datetime import datetime, timezone
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.core.deps import get_current_user
from app.database import SessionLocal, engine
from app.models import (
    CloudDocument, CloudTag, CloudVersion, Delivery, DeliveryItem, DeliveryRecipient,
    DocumentAccess, Team, TeamMember, User, Workspace,
)
from main import app


client = TestClient(app)


@contextmanager
def query_work():
    work = {'selects': 0, 'versions_loaded': 0}

    def executed(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith('SELECT'):
            work['selects'] += 1

    def loaded(target, context):
        work['versions_loaded'] += 1

    event.listen(engine, 'before_cursor_execute', executed)
    event.listen(CloudVersion, 'load', loaded)
    try:
        yield work
    finally:
        event.remove(engine, 'before_cursor_execute', executed)
        event.remove(CloudVersion, 'load', loaded)


@pytest.fixture
def records():
    with SessionLocal() as db:
        team = Team(name='query budget', slug=str(uuid.uuid4()), storage_used=999999)
        actor, sender = [User(email=f'{uuid.uuid4()}@query.test', display_name=name,
                              hashed_password='unused', is_superuser=True)
                         for name in ('reader', 'sender')]
        db.add_all([team, actor, sender]); db.flush()
        ws = Workspace(team_id=team.id, name='lists')
        db.add(ws); db.flush()
        db.add_all([TeamMember(team_id=team.id, user_id=user.id, role='member')
                    for user in (actor, sender)])
        tag = CloudTag(workspace_id=ws.id, name='shared tag')
        db.add(tag)
        document_ids = []
        for index in range(24):
            doc = CloudDocument(workspace_id=ws.id, name=f'document {index:02}',
                                created_by=sender.id, uploader_id=sender.id,
                                visibility='workspace' if index % 4 == 0 else 'restricted',
                                tags=[tag])
            db.add(doc); db.flush()
            if doc.visibility == 'restricted':
                db.add(DocumentAccess(document_id=doc.id, user_id=actor.id,
                                      role=('reader', 'contributor', 'manager')[index % 3]))
            for number in range(1, 13):
                version = CloudVersion(document_id=doc.id, version_number=number,
                                       content_hash='a' * 64, file_size=number,
                                       original_filename='f.txt')
                db.add(version); db.flush()
                if number == 5:
                    doc.current_version_id = version.id
            document_ids.append(doc.id)
        hidden = CloudDocument(workspace_id=ws.id, name='hidden', visibility='restricted')
        db.add(hidden)
        for index in range(24):
            delivery = Delivery(team_id=team.id, workspace_id=ws.id,
                                sender_id=actor.id if index == 0 else sender.id,
                                request_id=str(uuid.uuid4()), request_hash='a' * 64,
                                status='recalled' if index == 1 else 'sent')
            db.add(delivery); db.flush()
            db.add(DeliveryItem(delivery_id=delivery.id, filename='fixed.txt',
                                storage_key='objects/aa/' + 'a' * 64, content_hash='a' * 64,
                                file_size=7, source_document_id=document_ids[0]))
            db.add_all([DeliveryRecipient(delivery_id=delivery.id, recipient_id=user.id,
                                         read_at=datetime.now(timezone.utc) if index == 2 else None)
                        for user in (actor, sender)])
        db.commit()
        result = dict(actor=actor, sender=sender, ws=ws.id, team=team.id,
                      documents=document_ids, usage=24 * sum(range(1, 13)) + 24 * 7)
        app.dependency_overrides[get_current_user] = lambda: actor
        yield result
    app.dependency_overrides.clear()


def assert_capabilities(items, records):
    by_id = {item['id']: item for item in items}
    for index, document_id in enumerate(records['documents']):
        public = index % 4 == 0
        role = ('reader', 'contributor', 'manager')[index % 3]
        manager = not public and role == 'manager'
        assert by_id[document_id]['capabilities'] == dict(
            read=True, contribute=public or role != 'reader', manage=manager,
            distribute=manager, delete=False)


def test_search_has_bounded_queries_and_loads_only_current_versions(records, record_property):
    with query_work() as work:
        response = client.get(f"/api/workspaces/{records['ws']}/documents/search?page_size=100")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['total'] == len(body['items']) == 24
    assert all(item['version_count'] == 12 and item['current_version']['file_size'] == 5
               and item['uploader_display_name'] == 'sender' for item in body['items'])
    assert_capabilities(body['items'], records)
    record_property('selects', work['selects'])
    record_property('versions_loaded', work['versions_loaded'])
    assert work['selects'] <= 14, work
    assert work['versions_loaded'] <= 24, work


def test_full_document_list_keeps_history_with_bounded_queries(records, record_property):
    with query_work() as work:
        response = client.get(f"/api/workspaces/{records['ws']}/documents")
    assert response.status_code == 200, response.text
    items = response.json()
    assert len(items) == 24
    assert all(len(item['versions']) == 12 for item in items)
    assert all([v['version_number'] for v in item['versions'] if v['is_current']] == [5]
               for item in items)
    assert_capabilities(items, records)
    record_property('selects', work['selects'])
    record_property('versions_loaded', work['versions_loaded'])
    assert work['selects'] <= 12, work


@pytest.mark.parametrize('box', ['inbox', 'outbox'])
def test_delivery_list_queries_are_bounded_without_expanding_access(records, box, record_property):
    if box == 'outbox':
        app.dependency_overrides[get_current_user] = lambda: records['sender']
    with query_work() as work:
        response = client.get(f"/api/workspaces/{records['ws']}/deliveries?box={box}&page_size=100")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['total'] == len(body['items']) == (24 if box == 'inbox' else 23)
    for delivery in body['items']:
        assert delivery['can_download'] == (delivery['status'] == 'sent')
        assert 'recipients' not in delivery
        assert 'source_document_id' not in delivery['items'][0]
        if delivery['sender_id'] == (records['sender'] if box == 'outbox' else records['actor']).id:
            assert delivery['recipient_count'] == 2
        else:
            assert 'recipient_count' not in delivery
    record_property('selects', work['selects'])
    record_property('versions_loaded', work['versions_loaded'])
    assert work['selects'] <= 12, work


def test_list_permissions_are_reloaded_after_revocation_and_role_change(records):
    base = f"/api/workspaces/{records['ws']}/documents"
    removed = records['documents'][1]
    with SessionLocal() as db:
        db.delete(db.get(DocumentAccess, (removed, records['actor'].id)))
        db.get(TeamMember, (records['team'], records['actor'].id)).role = 'viewer'
        db.commit()
    for url in (base, base + '/search?page_size=100'):
        response = client.get(url)
        assert response.status_code == 200, response.text
        body = response.json()
        items = body if isinstance(body, list) else body['items']
        assert len(items) == 23
        assert removed not in {item['id'] for item in items}
        assert all(item['capabilities'] == dict(read=True, contribute=False, manage=False,
                                               distribute=False, delete=False) for item in items)


def test_admin_team_and_member_list_queries_are_bounded(records):
    with SessionLocal() as db:
        for _ in range(24):
            member = User(email=f'{uuid.uuid4()}@query.test', display_name='member', hashed_password='unused')
            team = Team(name='empty', slug=str(uuid.uuid4()))
            db.add_all([member, team]); db.flush()
            db.add(TeamMember(team_id=records['team'], user_id=member.id))
        db.commit()
    with query_work() as work:
        response = client.get('/api/admin/teams?page_size=100')
    assert response.status_code == 200, response.text
    team = next(team for team in response.json()['items'] if team['id'] == records['team'])
    assert (team['member_count'], team['workspace_count'], team['storage_used']) == (26, 1, records['usage'])
    assert work['selects'] <= 8, work
    with query_work() as work:
        response = client.get(f"/api/teams/{records['team']}/members")
    assert response.status_code == 200, response.text
    assert len(response.json()) == 26
    assert work['selects'] <= 6, work


def test_team_capacity_responses_use_retained_logical_bytes(records):
    with SessionLocal() as db:
        db.get(CloudDocument, records['documents'][0]).trashed_at = datetime.now(timezone.utc)
        other_team = Team(name='foreign', slug=str(uuid.uuid4()))
        db.add(other_team); db.flush()
        foreign_ws = Workspace(team_id=other_team.id, name='foreign')
        second_ws = Workspace(team_id=records['team'], name='another workspace')
        db.add_all([foreign_ws, second_ws]); db.flush()
        for ws, size in ((foreign_ws, 9999), (second_ws, 11)):
            doc = CloudDocument(workspace_id=ws.id, name='capacity')
            db.add(doc); db.flush()
            db.add(CloudVersion(document_id=doc.id, version_number=1,
                                original_filename='f.txt', content_hash='a' * 64, file_size=size))
        db.commit()
    expected = records['usage'] + 11
    detail = client.get(f"/api/teams/{records['team']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()['storage_used'] == expected
    teams = client.get('/api/teams').json()
    assert next(team for team in teams if team['id'] == records['team'])['storage_used'] == expected
    admin_teams = client.get('/api/admin/teams?page_size=100').json()['items']
    assert next(team for team in admin_teams if team['id'] == records['team'])['storage_used'] == expected
    with SessionLocal() as db:
        # Computing responses must not overwrite the retained compatibility column.
        assert db.get(Team, records['team']).storage_used == 999999
