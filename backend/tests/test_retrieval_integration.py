"""整理后的内容应能找回，并将候选定位与实际阅读证据明确区分。"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import database
from backend.app.services import indexing
from backend.tests.test_partner_tools import library, saved, toolset  # noqa: F401


def processed(root, name, text):
    document_id, scope = saved(root, name, text.encode())
    while indexing.process_one():
        pass
    return document_id, scope


def test_partner_search_returns_ranked_locations_and_verifiable_read_hint(library):
    target, meta = processed(library, '年度总结.txt',
                             '年度情况\n昆北站 5053 断路器发生压力异常，处理措施为检查密封。')
    other, other_meta = processed(library, 'AAA压力异常.txt', '其他站点设备运行正常。')
    tools = toolset(library, {target: meta, other: other_meta})
    result = tools.search_documents('5053 压力异常')
    assert result['documents'][0]['document_id'] == target
    passage = result['documents'][0]['matched_passages'][0]
    assert '5053' in passage['excerpt']
    assert passage['location']['line'] == 2
    assert tools.sources == [], '搜索命中只是候选，不能冒充本轮已读原文'
    hint = passage['next_call']
    assert hint['tool'] == 'read_document'
    read = tools.read_document(**hint['arguments'])
    assert any('压力异常' in unit['text'] for unit in read['units'])
    assert tools.sources and tools.sources[0]['version_id'] == meta['version_id']
    assert result['retrieval_mode'] == 'lexical'


def test_document_search_exposes_safe_match_and_keeps_filters(library):
    from backend.app.api.documents import router
    target, _ = processed(library, '检修报告.txt', '昆北站 5053 断路器压力异常处置记录。')
    processed(library, '其他记录.txt', '昆北站 50530 设备压力异常。')
    app = FastAPI()
    app.include_router(router)
    with TestClient(app, headers={'X-TeamHub-AI': '1'}) as client:
        response = client.post('/api/documents/search', json={
            'keyword': '5053 压力异常', 'include_content': True, 'sort_by': 'relevance'})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['total'] == 1
        assert result['documents'][0]['id'] == target
        match = result['documents'][0]['search_match']
        assert match['passages'][0]['location']['line'] == 1
        assert match['mode'] == 'lexical'
        assert str(library) not in str(match)
        filtered = client.post('/api/documents/search', json={
            'keyword': '5053 压力异常', 'include_content': True,
            'status': 'organized', 'sort_by': 'relevance'})
        assert filtered.json()['total'] == 0
        denied = client.post('/api/documents/search', json={
            'keyword': '5053', 'include_content': True}, headers={'X-TeamHub-AI': ''})
        assert denied.status_code == 403


def test_retrieval_results_never_expose_unauthorized_or_working_edits(library):
    target, meta = processed(library, '记录.txt', '旧版内容 5053 压力异常')
    processed(library, '机密记录.txt', '密文独有线索 5053 压力异常')
    (library / '记录.txt').write_text('未保存内容 本地改动独有词')
    tools = toolset(library, {target: meta})
    result = tools.search_documents('5053 压力异常')
    assert [item['document_id'] for item in result['documents']] == [target]
    assert not tools.search_documents('本地改动独有词')['documents']
    assert not tools.search_documents('密文独有线索')['documents']
    assert database.active_session_count() == 0


def test_search_rejects_excessive_keywords_as_a_user_error(library):
    from backend.app.api.documents import router
    app = FastAPI()
    app.include_router(router)
    with TestClient(app, headers={'X-TeamHub-AI': '1'}) as client:
        response = client.post('/api/documents/search', json={
            'keyword': ' '.join(f'关键词{i}' for i in range(17)),
            'include_content': True, 'sort_by': 'relevance'})
        assert response.status_code == 422
