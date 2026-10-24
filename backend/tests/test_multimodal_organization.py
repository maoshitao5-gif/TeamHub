"""Object-specific rules drive real structured tasks without new confirmations."""
import asyncio
import base64
from io import BytesIO
import json

import httpx2
from PIL import Image
import pytest

from backend.app import database
from backend.app.models import Document, Version
from backend.app.services import document_cards
from backend.tests.test_archive_reader import packed
from backend.tests.test_partner_tools import library, saved  # noqa: F401
from backend.tests.test_organization_tools import bridge_for, invoke, proposal
from backend.tests.test_hermes_real_engine import ROOT, current_worker_source, response, values  # noqa: F401


def picture(format='PNG'):
    buffer = BytesIO()
    Image.new('RGB', (24, 32), 'red').save(buffer, format)
    return buffer.getvalue()


def test_image_kind_follows_saved_version_even_after_display_rename(library):
    from backend.app.services.indexing import metadata_fingerprint
    doc, meta = saved(library, '现场.png', picture())
    with database.SessionLocal() as db:
        record = db.get(Document, doc)
        record.name = '用户自定义名称.zip'
        meta.update(name=record.name, metadata_hash=metadata_fingerprint(record))
        db.commit()
    bridge = bridge_for(library, doc, meta)
    assert bridge.object_kind == 'image' and not bridge.is_container
    assert 'read_visual' in {item['function']['name'] for item in bridge.definitions()}
    assert 'list_archive_members' not in {item['function']['name'] for item in bridge.definitions()}


def test_member_object_skill_loads_once_without_registering_evidence(library):
    doc, meta = saved(library, '资料.zip', packed('zip', {'说明.txt': b'content'}))
    bridge = bridge_for(library, doc, meta)
    first = invoke(bridge, 'read_object_skill', {'kind': 'image'})
    assert first['ok'] and 'content' in first
    assert invoke(bridge, 'read_object_skill', {'kind': 'image'})['already_loaded']
    assert bridge.tools.sources == []
    assert not invoke(bridge, 'read_object_skill', {'kind': '/etc/passwd'})['ok']
    assert invoke(bridge, 'read_object_skill', {'kind': 'archive'})['already_loaded']


def test_archive_card_can_save_current_member_provenance_and_search(library):
    doc, meta = saved(library, '资料.zip', packed('zip', {'目录/说明.txt': '设备检修说明'.encode(),
                                                       '未读.txt': b'not read'}))
    bridge = bridge_for(library, doc, meta)
    bridge.preflight()
    invoke(bridge, 'list_archive_members', {'document_id': doc})
    invoke(bridge, 'read_archive_member', {'document_id': doc, 'member_path': '目录/说明.txt', 'action': 'text'})
    data = proposal(name='设备检修资料.zip', target_dir='')
    data['card']['search_terms'] = ['压缩包独有索引ABC']
    assert invoke(bridge, 'propose_organization', data)['ok']
    with database.SessionLocal() as db:
        result = document_cards.save_card(db, doc, meta['version_id'], meta['content_hash'],
            bridge.card, bridge.tools.sources, bridge.coverage(), 'synthetic', commit=True)
        assert result['object_kind'] == 'archive'
        assert result['state'] == 'partial'
        assert result['card']['coverage']['read_members'] == 1
        assert result['card']['sources'][0]['location']['member_path'] == '目录/说明.txt'
        assert db.query(Document).count() == 1 and db.query(Version).count() == 1
    from backend.app.services.partner_tools import PartnerTools
    lookup = PartnerTools(engine=database.engine, root=str(library), scope={doc: meta}, guard=lambda: None)
    assert lookup.search_documents('压缩包独有索引ABC')['documents'][0]['document_id'] == doc


def test_archive_full_extension_is_required_in_proposal_and_apply_validation(library):
    from backend.app.services.preorganize import _validate_fields
    from fastapi import HTTPException
    doc, meta = saved(library, '工程.tar.gz', packed('tar.gz', {'说明.txt': b'content'}))
    bridge = bridge_for(library, doc, meta)
    invoke(bridge, 'read_archive_member', {'document_id': doc, 'member_path': '说明.txt', 'action': 'text'})
    assert not invoke(bridge, 'propose_organization', proposal(name='新名称.gz'))['ok']
    assert invoke(bridge, 'propose_organization', proposal(name='新名称.tar.gz'))['ok']
    with pytest.raises(HTTPException):
        _validate_fields(proposal(name='新名称.gz'), meta)


@pytest.mark.parametrize('parent', ['image', 'folder', 'archive'])
@pytest.mark.parametrize('first_action', ['overview', 'text'])
def test_expanded_image_inventory_and_complete_coverage_are_consistent(library, parent, first_action):
    from backend.tests.test_folder_reader import saved_folder
    if parent == 'folder':
        doc, meta = saved_folder(library, {'现场.avif': picture('AVIF')})
    else:
        doc, meta = saved(library, '现场.avif' if parent == 'image' else '工程.zip',
                          picture('AVIF') if parent == 'image' else packed('zip', {'现场.avif': picture('AVIF')}))
    bridge = bridge_for(library, doc, meta)
    if parent != 'image':
        inventory = invoke(bridge, 'list_folder_members' if parent == 'folder' else 'list_archive_members',
                           {'document_id': doc})
        assert inventory['members'][0]['readable'] is True
        assert inventory['members'][0]['object_kind'] == 'image'
        tool = 'read_folder_member' if parent == 'folder' else 'read_archive_member'
        initial = invoke(bridge, tool, {'document_id': doc, 'member_path': '现场.avif', 'action': first_action})
        if first_action == 'overview':
            assert initial['next_call'] == {'document_id': doc, 'member_path': '现场.avif',
                                            'action': 'visual', 'pages': [1]}
        assert not bridge.tools.sources
        invoke(bridge, tool, {'document_id': doc, 'member_path': '现场.avif', 'action': 'visual'})
    else:
        invoke(bridge, 'document_overview' if first_action == 'overview' else 'read_document', {'document_id': doc})
        assert not bridge.tools.sources
        invoke(bridge, 'read_visual', {'document_id': doc})
    assert bridge.coverage()['complete'] is True
    assert bridge.coverage()['total_units'] == 0


def test_large_native_svg_retains_conversion_scaling_gap(monkeypatch):
    from backend.app.services import image_formats
    monkeypatch.setattr(image_formats, 'supported_visual_suffix', lambda _: True)
    data = b'<svg xmlns="http://www.w3.org/2000/svg" width="5000" height="300"><rect width="5000" height="300" fill="red"/></svg>'
    result = image_formats.image_probe(data, '.svg')
    assert result['width'] == 5000 and result['height'] == 300
    assert any('缩放' in note and '2304' in note for note in result['notes'])


@pytest.mark.parametrize('suffix, kind', [('zip', 'zip'), ('tar.gz', 'tar.gz')])
def test_manual_archive_organization_preserves_extension_hash_and_single_version(library, suffix, kind):
    from fastapi.testclient import TestClient
    from main import app
    data = packed(kind, {'说明.txt': b'content'})
    doc, meta = saved(library, '工程.' + suffix, data)
    result = TestClient(app).post(f'/api/documents/{doc}/organize', json={
        'name': '项目.v2', 'tags': ['项目'], 'target_dir': ''})
    assert result.status_code == 200, result.text
    assert result.json()['name'] == '项目.v2.' + suffix
    assert (library / result.json()['storage_path']).read_bytes() == data
    with database.SessionLocal() as db:
        assert db.get(Document, doc).current_version_id == meta['version_id']
        assert db.query(Version).filter_by(document_id=doc).count() == 1


@pytest.mark.skipif(not (ROOT / '.mvp-sandbox/hermes-venv/bin/python').exists(),
                    reason='Requires the local real Hermes engine')
@pytest.mark.parametrize('filename, content, kind', [
    ('现场.avif', picture('AVIF'), 'image'),
    ('现场.jp2', picture('JPEG2000'), 'image'),
    ('成套.zip', packed('zip', {'现场.png': picture()}), 'archive'),
])
def test_real_engine_receives_selected_skill_and_actual_pixels(library, filename, content, kind):
    from backend.app.core.hermes_agent import run_agent
    doc, meta = saved(library, filename, content)
    requests = []
    def provider(request):
        body = json.loads(request.content)
        requests.append(body)
        raw = request.content.decode()
        assert f'name: teamhub-{kind}-understanding' in raw
        if len(requests) == 1:
            return response(body, tool='read_archive_member' if kind == 'archive' else 'read_visual',
                arguments={'document_id': doc, **({'member_path': '现场.png', 'action': 'visual'}
                                                  if kind == 'archive' else {})})
        assert len(requests) == 2
        images = [part for message in body['messages'] if isinstance(message.get('content'), list)
                  for part in message['content'] if part.get('type') == 'image_url']
        assert len(images) == 1
        raw_image = base64.b64decode(images[0]['image_url']['url'].split(',', 1)[1])
        with Image.open(BytesIO(raw_image)) as image:
            assert image.size == (24, 32)
            red, green, blue = image.convert('RGB').getpixel((12, 16))
            assert red > 240 and green < 20 and blue < 20
        return response(body, tool='propose_organization', arguments=proposal(name=filename,
            target_dir='', summary='已核对红色画面。'))
    result = asyncio.run(run_agent(**{**values(library, {doc: meta}, []), 'purpose': 'preorganize',
        'purpose_context': {'document_id': doc, 'tags': [], 'folders': []},
        'transport_override': httpx2.MockTransport(provider)}))
    assert result['proposal']['name'] == filename
    assert result['usage']['model_requests'] == 2
    assert result['sources'][0]['document_id'] == doc
    if kind == 'archive':
        assert result['sources'][0]['location']['member_path'] == '现场.png'


@pytest.mark.skipif(not (ROOT / '.mvp-sandbox/hermes-venv/bin/python').exists(),
                    reason='Requires the local real Hermes engine')
def test_image_request_rejection_is_specific_and_not_replayed(library):
    from backend.app.core.hermes_agent import run_agent
    from backend.app.core.ai_client import AIError
    doc, meta = saved(library, '现场.png', picture())
    calls = []
    def provider(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            return response(body, tool='read_visual', arguments={'document_id': doc})
        return httpx2.Response(400, text='sensitive upstream error')
    with pytest.raises(AIError) as error:
        asyncio.run(run_agent(**{**values(library, {doc: meta}, []), 'purpose': 'preorganize',
            'purpose_context': {'document_id': doc, 'tags': [], 'folders': []},
            'transport_override': httpx2.MockTransport(provider)}))
    assert error.value.code == 'AI_IMAGE_REQUEST_REJECTED'
    assert '图像理解' in str(error.value) and 'sensitive' not in str(error.value)
    assert len(calls) == 2
