"""M5 本地适配层到真实共享服务的标签和范围闭环，复用隔离服务夹具。"""
from backend.tests.test_m4_sharing_e2e import cloud_server, library, client, _ingest
import backend.app.database as database


def test_collaboration_proxy_and_workspace_selection(library, cloud_server, tmp_path):
    ws = cloud_server.workspace_id
    source = tmp_path / '协作检索.txt'
    source.write_text('共享资料')
    doc = _ingest(source)
    result = client.post('/api/sharing/publish', json=dict(visibility='workspace', doc_id=doc['id'], workspace_id=ws, confirmed=True)).json()
    shared = result['document_id']
    assert client.post('/api/sync/workspace', json={'workspace_id': ws, 'workspace_name': '协作空间'}).status_code == 200
    context = client.get('/api/sharing/context').json()
    assert context['can_tag'] and not context['can_manage']
    original = client.get(f'/api/sharing/tags/{shared}').json()
    a_tag = original[0]['tag_id']
    response = client.post('/api/sync/login', json={'email': 'member-b@example.com', 'password': 'e2e-pass-123'})
    assert response.status_code == 200 and response.json()['teams']
    assert not database.load_library_config().get('active_workspace_id')
    assert client.post('/api/sync/workspace', json={'workspace_id': ws, 'workspace_name': '协作空间'}).status_code == 200
    assert client.get('/api/sync/teams').status_code == 200
    updated = client.post(f'/api/sharing/tags/{shared}', json={'add': ['B的设备,线索'], 'workspace_id': ws})
    assert updated.status_code == 200, updated.text
    links = updated.json()
    b_tag = next(tag for tag in links if tag['name'] == 'B的设备,线索')
    assert b_tag['can_remove'] and not next(tag for tag in links if tag['tag_id'] == a_tag)['can_remove']
    assert client.post(f'/api/sharing/tags/{shared}', json={'remove': [a_tag], 'workspace_id': ws}).status_code == 403
    suggestions = client.get('/api/sharing/suggestions', params={'type': 'tag', 'q': 'B的', 'workspace_id': ws}).json()
    assert suggestions[0]['id'] == b_tag['tag_id']
    found = client.get('/api/sync/docs/search', params={'tag_ids': b_tag['tag_id'], 'workspace_id': ws}).json()
    assert [item['id'] for item in found['items']] == [shared]
    favorite_body = dict(target_type='shared', server_url=context['server_url'], workspace_id=ws,
                         shared_document_id=shared)
    favorite = client.post('/api/favorites', json=favorite_body)
    assert favorite.status_code == 201
    favorite_id = favorite.json()['id']
    assert client.post('/api/favorites', json=favorite_body).json()['id'] == favorite_id
    resolved = client.get(f'/api/favorites/{favorite_id}/resolve')
    assert resolved.status_code == 200 and resolved.json()['id'] == shared
    missing = client.post('/api/favorites', json={**favorite_body, 'shared_document_id': 'missing'}).json()
    assert client.get(f"/api/favorites/{missing['id']}/resolve").status_code == 404
    foreign = client.post('/api/favorites', json={**favorite_body, 'workspace_id': 'not-visible'}).json()
    assert client.get(f"/api/favorites/{foreign['id']}/resolve").status_code == 404
    other_server = client.post('/api/favorites', json={**favorite_body, 'server_url': 'http://wrong.invalid'}).json()
    assert client.get(f"/api/favorites/{other_server['id']}/resolve").status_code == 409
    for favorite in (favorite_id, missing['id'], foreign['id'], other_server['id']):
        assert client.delete('/api/favorites/' + favorite).status_code == 200
    assert client.get('/api/favorites?target_type=shared').json() == []
    assert client.post(f'/api/sharing/documents/{shared}/trash', params={'workspace_id': ws}).status_code == 403
    assert client.get('/api/sharing/trash', params={'workspace_id': ws}).status_code == 403
    assert client.get('/api/sharing/members', params={'workspace_id': ws}).status_code == 403
    assert client.post('/api/sync/workspace', json={'workspace_id': 'not-visible', 'workspace_name': '错误空间'}).status_code == 404
    assert database.load_library_config()['active_workspace_id'] == ws
    assert client.post('/api/sync/workspace', json={'workspace_id': '', 'workspace_name': ''}).status_code == 200
    assert client.get('/api/sharing/context').status_code == 400
