"""系统复验补充：标签维护、智能扫描和批量操作的真实 API 闭环。"""
from backend.tests.test_organize_workflow import client, library, _pending


def test_tag_edit_merge_and_usage_protection(library, tmp_path):
    first = _pending(tmp_path, name='图纸一.txt', content='一')
    second = _pending(tmp_path, name='图纸二.txt', content='二')
    target = client.post('/api/tags', json={'name': '设备'}).json()
    source = client.post('/api/tags', json={'name': '旧称'}).json()
    assert client.post('/api/tags', json={'name': '设备'}).status_code == 400
    renamed = client.put('/api/tags/' + source['id'], json={'name': '旧设备名称', 'color': '#123456'})
    assert renamed.status_code == 200
    assert renamed.json()['color'] == '#123456'
    for doc, names in [(first, ['设备', '旧设备名称']), (second, ['旧设备名称'])]:
        assert client.put(f"/api/documents/{doc['id']}/tags", json=names).status_code == 200
    result = client.post('/api/tags/merge', json={'target_tag_id': target['id'], 'source_tag_ids': [source['id']]})
    assert result.status_code == 200, result.text
    assert client.get('/api/tags/' + source['id']).status_code == 404
    for doc in [first, second]:
        assert [t['name'] for t in client.get('/api/documents/' + doc['id']).json()['tags']] == ['设备']
    assert client.delete('/api/tags/' + target['id']).status_code == 409
    for doc in [first, second]:
        assert client.put(f"/api/documents/{doc['id']}/tags", json=[]).status_code == 200
    assert client.delete('/api/tags/' + target['id']).status_code == 200


def test_pending_analysis_batch_tags_and_trash_roundtrip(library, tmp_path):
    docs = [_pending(tmp_path, name=f'检修报告_v{i}.pdf', content=f'版本{i}') for i in [1, 2, 3]]
    ids = [d['id'] for d in docs]
    response = client.post('/api/documents/pending/analyze')
    assert response.status_code == 200, response.text
    assert any({d['id'] for d in g['documents']} == set(ids) for g in response.json()['version_groups'])
    assert any(s['tag'] == 'PDF' for s in response.json()['tag_suggestions'])
    assert client.post('/api/documents/batch/tags', json={'document_ids': ids, 'add_tags': ['检修'], 'remove_tags': []}).status_code == 200
    found = client.post('/api/documents/search', json={'status': 'pending', 'tags': ['检修']}).json()
    assert found['total'] == 3
    for doc in docs:
        assert client.delete('/api/documents/' + doc['id']).status_code == 200
    assert client.get('/api/documents/pending/count').json()['count'] == 0
    assert client.post('/api/documents/batch/restore', json={'document_ids': ids}).status_code == 200
    assert client.get('/api/documents/pending/count').json()['count'] == 3
    for doc in docs:
        current = client.get('/api/documents/' + doc['id']).json()
        assert (library / current['storage_path']).read_text() == f"版本{docs.index(doc) + 1}"
        assert [t['name'] for t in current['tags']] == ['检修']
