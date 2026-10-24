"""合成文档通过真实 SDK 工具链读取内嵌图；不访问真实模型或用户资料。"""
import asyncio
from io import BytesIO
import json
import zipfile

import httpx2
import pytest
from PIL import Image

from backend.app import database
from backend.app.config import settings
from backend.app.core.ai_client import AIError
from backend.app.core.partner_protocol import BoundedTransport
from backend.tests.support.legacy_partner_agent import run_agent
from backend.app.core.partner_budget import normalize_budget
from backend.app.models import Document
from backend.app.services import indexing, versions


@pytest.fixture
def library(tmp_path, monkeypatch):
    root = tmp_path / 'library'
    root.mkdir()
    monkeypatch.setattr(settings, 'library_path', str(root))
    monkeypatch.setattr(settings, 'pending_path', '')
    database.rebind_engine()
    database.init_db()
    yield root
    assert database.active_session_count() == 0


def picture_document():
    image, output = BytesIO(), BytesIO()
    Image.new('RGB', (48, 32), 'blue').save(image, format='PNG')
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body><w:p><w:r><w:drawing><a:blip r:embed="rId1"/></w:drawing></w:r></w:p></w:body></w:document>')
        archive.writestr('word/_rels/document.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image1.png"/></Relationships>')
        archive.writestr('word/media/image1.png', image.getvalue())
    return output.getvalue()


def save(root, name, content):
    (root / name).write_bytes(content)
    with database.SessionLocal() as db:
        document = Document(name=name, storage_path=name, status='pending', storage_mode='copy',
                            is_folder=False, doc_type='file')
        db.add(document)
        db.flush()
        version, _ = versions.snapshot_working_copy(db, document)
        db.commit()
        return {document.id: {'name': name, 'version_id': version.id,
            'content_hash': version.sha256_hash, 'metadata_hash': indexing.metadata_fingerprint(document),
            'version_number': version.version_number, 'is_folder': False}}


def response(name, arguments, number):
    return httpx2.Response(200, json={'id': f'test-{number}', 'object': 'chat.completion',
        'created': 1, 'model': 'synthetic', 'choices': [{'index': 0, 'finish_reason': 'tool_calls',
        'message': {'role': 'assistant', 'content': None, 'tool_calls': [{'id': f'tool-{number}',
        'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments)}}]}}],
        'usage': {'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20}})


def run(root, scope, handler):
    return asyncio.run(run_agent(message='请阅读这份资料中的图形并说明实际读取范围。', history=[], scope=scope,
        config={'base_url': 'https://example.com/v1', 'model': 'synthetic'}, key='synthetic-not-real',
        engine=database.engine, root=str(root), guard=lambda: None, emit=lambda *args: None,
        limits=normalize_budget(), transport_override=httpx2.MockTransport(handler)))


def test_image_only_docx_reaches_model_as_real_image_with_fixed_source(library):
    scope = save(library, '纯图文档.docx', picture_document())
    document_id = next(iter(scope))
    calls = []
    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert 'read_embedded_images' in [item['function']['name'] for item in payload['tools']]
        if len(calls) == 1:
            return response('document_overview', {'document_id': document_id}, 1)
        results = [json.loads(item['content']) for item in payload['messages'] if item['role'] == 'tool']
        if len(calls) == 2:
            overview = results[-1]
            assert overview['visual_asset_count'] == 1
            return response('read_embedded_images', {'document_id': document_id,
                'image_ids': [overview['visual_assets'][0]['image_id']]}, 2)
        images = [part for item in payload['messages'] if isinstance(item.get('content'), list)
                  for part in item['content'] if part['type'] == 'image_url']
        assert images and images[0]['image_url']['url'].startswith('data:image/png;base64,')
        source = results[-1]['images'][0]['source_id']
        return response('final_result', {'answer': f'已读取正文内嵌图，未据此声称全文排版已读。[{source}]',
                                        'source_ids': [source]}, 3)
    result = run(library, scope, handler)
    assert len(calls) == 3
    assert result['sources'][0]['version_id'] == scope[document_id]['version_id']
    assert result['sources'][0]['location']['image_index'] == 1


def test_empty_read_reports_honest_next_steps_and_rejects_invented_source(library):
    scope = save(library, '未支持的资料.bin', b'not a supported document')
    calls = []
    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if len(calls) == 1:
            return response('read_document', {'document_id': next(iter(scope))}, 1)
        observed = json.loads(next(item['content'] for item in reversed(payload['messages']) if item['role'] == 'tool'))
        assert observed['reading_status'] == 'no_extractable_text'
        assert '内嵌图片' in observed['next_steps']
        return response('final_result', {'answer': '臆造事实。[S1]', 'source_ids': ['S1']}, 2)
    with pytest.raises(AIError) as exc:
        run(library, scope, handler)
    assert exc.value.code == 'PARTNER_INVALID_SOURCES'
    assert '未取得可引用' in str(exc.value)
    assert len(calls) == 2


@pytest.mark.parametrize('mode', ['single', 'cumulative'])
def test_outbound_payload_limits_stop_before_dispatch_or_attempt_count(monkeypatch, mode):
    import backend.app.core.partner_protocol as module
    monkeypatch.setattr(module, 'MAX_REQUEST_BYTES', 8, raising=False)
    monkeypatch.setattr(module, 'MAX_TOTAL_REQUEST_BYTES', 12, raising=False)
    sent, attempts = [], []
    async def execute():
        transport = BoundedTransport(lambda: None,
            httpx2.MockTransport(lambda request: sent.append(request) or httpx2.Response(200, content=b'{}')),
            before_send=lambda: attempts.append(True))
        async with httpx2.AsyncClient(transport=transport) as client:
            if mode == 'cumulative':
                await client.post('https://example.com', content=b'12345678')
            with pytest.raises(AIError) as exc:
                await client.post('https://example.com', content=b'123456789' if mode == 'single' else b'12345')
            assert exc.value.code == 'PARTNER_REQUEST_SIZE_LIMIT'
    asyncio.run(execute())
    assert len(sent) == len(attempts) == (1 if mode == 'cumulative' else 0)


def test_bounded_transport_preserves_one_shot_request_stream():
    sent = []
    async def stream():
        yield b'first'
        yield b'-second'
    async def execute():
        transport = BoundedTransport(lambda: None,
            httpx2.MockTransport(lambda request: sent.append(request.content) or httpx2.Response(200, content=b'{}')))
        async with httpx2.AsyncClient(transport=transport) as client:
            await client.post('https://example.com', content=stream())
    asyncio.run(execute())
    assert sent == [b'first-second']


def test_end_of_nonempty_document_is_not_reported_as_unreadable(library):
    scope = save(library, '说明.txt', '一行真实原文'.encode())
    calls = []
    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if len(calls) == 1:
            return response('read_document', {'document_id': next(iter(scope)), 'offset': 1}, 1)
        observed = json.loads(next(item['content'] for item in reversed(payload['messages']) if item['role'] == 'tool'))
        assert observed['unit_count'] == 1 and observed['units'] == []
        assert 'reading_status' not in observed
        return response('final_result', {'answer': '本次所选读取位置已到末尾。', 'source_ids': []}, 2)
    assert '末尾' in run(library, scope, handler)['answer']
