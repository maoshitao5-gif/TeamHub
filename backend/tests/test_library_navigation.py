"""已整理集合、分类与收藏的独立关系及移动失败保护。"""
import pytest

from backend.tests.test_organize_workflow import client, library, _pending, _organize


def search(**kwargs):
    response = client.post('/api/documents/search', json={'status': 'organized', **kwargs})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize('folder', [False, True])
def test_remove_classification_keeps_document_and_favorite(library, tmp_path, folder):
    doc = _pending(tmp_path, name='设备资料' if folder else '设备.txt', folder=folder)
    assert _organize(doc, target_dir='项目/检修').status_code == 200
    before = client.get(f"/api/documents/{doc['id']}").json()
    versions = client.get(f"/api/documents/{doc['id']}/versions").json()
    favorite = client.post('/api/favorites', json={'target_type': 'local', 'local_document_id': doc['id']}).json()
    assert search(folder='项目', favorites_only=True)['total'] == 1

    response = client.post(f"/api/documents/{doc['id']}/remove-from-folder")
    assert response.status_code == 200, response.text
    after = response.json()
    assert after['id'] == before['id']
    assert after['status'] == 'organized'
    assert after['tags'] == before['tags']
    assert after['storage_path'] == before['name']
    assert (library / after['storage_path']).exists()
    assert not (library / before['storage_path']).exists()
    assert search(folder='项目')['total'] == 0
    assert search(favorites_only=True, tags=['设备'])['total'] == 1
    assert client.get(f"/api/documents/{doc['id']}/versions").json() == versions
    assert client.get('/api/documents/pending/count').json()['count'] == 0
    assert client.delete('/api/favorites/' + favorite['id']).status_code == 200
    assert search(favorites_only=True)['total'] == 0
    assert search()['total'] == 1
    assert (library / after['storage_path']).exists()


def test_favorites_require_organized_and_hide_temporarily_unavailable(library, tmp_path):
    doc = _pending(tmp_path)
    body = {'target_type': 'local', 'local_document_id': doc['id']}
    assert client.post('/api/favorites', json=body).status_code == 400
    assert client.post(f"/api/documents/{doc['id']}/remove-from-folder").status_code == 400
    assert _organize(doc).status_code == 200
    favorite = client.post('/api/favorites', json=body).json()
    assert client.post(f"/api/documents/{doc['id']}/move-to-pending").status_code == 200
    assert client.get('/api/favorites?target_type=local').json() == []
    assert search(favorites_only=True)['total'] == 0
    assert _organize(doc).status_code == 200
    assert client.get('/api/favorites?target_type=local').json()[0]['id'] == favorite['id']
    assert client.delete(f"/api/documents/{doc['id']}").status_code == 200
    assert search(favorites_only=True)['total'] == 0
    assert client.post('/api/favorites', json=body).status_code == 400


def test_remove_folder_conflict_preserves_source(library, tmp_path):
    doc = _pending(tmp_path)
    assert _organize(doc, target_dir='项目').status_code == 200
    (library / doc['name']).write_text('其他资料', encoding='utf-8')
    response = client.post(f"/api/documents/{doc['id']}/remove-from-folder")
    assert response.status_code == 400
    assert (library / '项目' / doc['name']).exists()
    assert (library / doc['name']).read_text(encoding='utf-8') == '其他资料'
    assert search(folder='项目')['total'] == 1


def test_folder_names_with_sql_wildcards_are_literal(library, tmp_path):
    first = _pending(tmp_path, name='one.txt', content='one')
    second = _pending(tmp_path, name='two.txt', content='two')
    assert _organize(first, target_dir='项目_1').status_code == 200
    assert _organize(second, target_dir='项目A1').status_code == 200
    assert search(folder='项目_1')['total'] == 1


@pytest.mark.parametrize('folder', [False, True])
def test_classify_organized_document_and_change_category(library, tmp_path, folder):
    doc = _pending(tmp_path, name='成套资料' if folder else '设备.txt', folder=folder)
    assert _organize(doc).status_code == 200
    before = client.get(f"/api/documents/{doc['id']}").json()
    versions = client.get(f"/api/documents/{doc['id']}/versions").json()
    fav = client.post('/api/favorites', json={'target_type': 'local', 'local_document_id': doc['id']}).json()
    for category in ['项目/检修', '技术资料']:
        response = client.post(f"/api/documents/{doc['id']}/move-to-folder", json={'target_dir': category})
        assert response.status_code == 200, response.text
        after = response.json()
        assert after['status'] == 'organized' and after['id'] == doc['id']
        assert after['tags'] == before['tags']
        assert (library / category / before['name']).exists()
        assert search()['total'] == search(folder=category, favorites_only=True)['total'] == 1
        assert client.get('/api/favorites?target_type=local').json()[0]['id'] == fav['id']
        assert client.get(f"/api/documents/{doc['id']}/versions").json() == versions
    assert client.post(f"/api/documents/{doc['id']}/remove-from-folder").status_code == 200
    assert (library / before['name']).exists()


@pytest.mark.parametrize('target', ['', '../outside', '/tmp/outside', '.teamhub/objects', '待整理', '整套/内部'])
def test_classify_rejects_invalid_targets(library, tmp_path, target):
    doc = _pending(tmp_path)
    assert _organize(doc).status_code == 200
    other = _pending(tmp_path, name='整套', folder=True)
    assert _organize(other).status_code == 200
    result = client.post(f"/api/documents/{doc['id']}/move-to-folder", json={'target_dir': target})
    assert result.status_code in (400, 409, 422), result.text
    assert (library / doc['name']).exists()
    assert client.get(f"/api/documents/{doc['id']}").json()['storage_path'] == doc['name']
    dirs = client.get('/api/settings/library/dirtree').json()['items']
    assert '整套' not in [entry['name'] for entry in dirs]
    assert client.post('/api/settings/library/mkdir', json={'path': '整套/新增'}).status_code == 409
    assert not (library / '整套/新增').exists()


def test_classify_collision_and_commit_failure_keep_source(library, tmp_path, monkeypatch):
    from sqlalchemy.orm import Session
    doc = _pending(tmp_path)
    assert _organize(doc).status_code == 200
    target = library / '项目' / doc['name']
    target.parent.mkdir()
    target.write_text('已有内容', encoding='utf-8')
    url = f"/api/documents/{doc['id']}/move-to-folder"
    assert client.post(url, json={'target_dir': '项目'}).status_code == 400
    assert target.read_text(encoding='utf-8') == '已有内容'
    def fail_commit(self):
        raise RuntimeError('模拟提交失败')
    with monkeypatch.context() as patch:
        patch.setattr(Session, 'commit', fail_commit)
        assert client.post(url, json={'target_dir': '另一分类'}).status_code == 500
    assert (library / doc['name']).exists()
    assert not (library / '另一分类' / doc['name']).exists()
    assert client.get(f"/api/documents/{doc['id']}").json()['storage_path'] == doc['name']
