"""AI 伙伴仅使用明确授权的固定快照，真实资料均位于临时库。"""
import hashlib
from io import BytesIO
import json
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from backend.app import database
from backend.app.config import settings
from backend.app.models import Document, Tag, Version
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


def saved(root, name, data):
    path = root / name
    path.write_bytes(data)
    with database.SessionLocal() as db:
        doc = Document(name=name, storage_path=name, status='pending', storage_mode='copy',
                       is_folder=False, doc_type='file')
        db.add(doc)
        db.flush()
        version, _ = versions.snapshot_working_copy(db, doc)
        db.commit()
        return doc.id, {'name': doc.name, 'version_id': version.id,
            'content_hash': version.sha256_hash, 'metadata_hash': indexing.metadata_fingerprint(doc),
            'version_number': version.version_number, 'is_folder': False}


def toolset(root, scope, **kwargs):
    from backend.app.services.partner_tools import PartnerTools
    return PartnerTools(engine=database.engine, root=str(root.resolve()), scope=scope,
                        guard=kwargs.pop('guard', lambda: None), **kwargs)


def pdf(pages=2):
    output, writer = BytesIO(), PdfWriter()
    for number in range(pages):
        page = writer.add_blank_page(width=300, height=200)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
            NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):
            DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(f'BT /F1 12 Tf 10 10 Td (Page {number+1} text and diagram) Tj ET 20 20 80 80 re S'.encode())
        page[NameObject('/Contents')] = writer._add_object(stream)
    writer.write(output)
    return output.getvalue()


def test_search_only_returns_authorized_documents_and_never_paths(library):
    doc, meta = saved(library, '授权资料.txt', b'public')
    saved(library, '未授权资料.txt', b'secret')
    tools = toolset(library, {doc: meta})
    result = tools.search_documents('资料')
    assert [item['document_id'] for item in result['documents']] == [doc]
    assert str(library) not in json.dumps(result, ensure_ascii=False)


def test_search_can_continue_past_first_candidates_without_duplicates(library):
    scope = dict(saved(library, f'项目资料{number:02d}.txt', f'record {number}'.encode())
                 for number in range(13))
    tools = toolset(library, scope)
    first = tools.search_documents('项目资料', limit=8, offset=0)
    second = tools.search_documents('项目资料', limit=8, offset=first['next_offset'])
    first_ids = {item['document_id'] for item in first['documents']}
    second_ids = {item['document_id'] for item in second['documents']}
    assert len(first_ids) == 8 and len(second_ids) == 5
    assert not first_ids & second_ids
    assert first['has_more'] is True and second['has_more'] is False
    assert first_ids | second_ids == set(scope)


@pytest.mark.parametrize('offset', [-1, True, 10001])
def test_search_rejects_invalid_candidate_offset(library, offset):
    tools = toolset(library, {})
    with pytest.raises(ValueError, match='位置'):
        tools.search_documents('资料', offset=offset)


def test_empty_scope_cannot_discover_or_read_any_document(library):
    doc, _ = saved(library, '资料.txt', b'text')
    tools = toolset(library, {})
    assert tools.search_documents('资料')['documents'] == []
    with pytest.raises(ValueError, match='授权'):
        tools.read_document(doc)


def test_read_parses_snapshot_without_index_and_ignores_working_edits(library):
    doc, meta = saved(library, '说明.txt', '固定版本原文\n尾部关键词'.encode())
    (library / '说明.txt').write_text('未保存内容')
    tools = toolset(library, {doc: meta})
    result = tools.read_document(doc)
    assert '尾部关键词' in ''.join(unit['text'] for unit in result['units'])
    assert '未保存内容' not in json.dumps(result, ensure_ascii=False)
    assert result['version_id'] == meta['version_id']
    assert result['coverage']['processed'] == 2
    assert tools.sources and tools.sources[0]['id'] == tools.sources[0]['source_id']
    assert tools.sources[0]['version_id'] == meta['version_id']


def test_search_current_body_and_metadata_literal_queries(library):
    doc, meta = saved(library, '普通名称.txt', '正文独有关键字\n100%_literal'.encode())
    assert indexing.process_one()
    tools = toolset(library, {doc: meta})
    assert tools.search_documents('独有关键')['documents'][0]['document_id'] == doc
    assert tools.search_documents('100%_literal')['documents']
    assert tools.search_documents('%_')['documents']
    assert not tools.search_documents('" OR *')['documents']


@pytest.mark.parametrize('change', ['version', 'metadata', 'trashed'])
def test_frozen_document_changes_abort_reads(library, change):
    doc, meta = saved(library, '旧名称.txt', b'old')
    tools = toolset(library, {doc: meta})
    with database.SessionLocal() as db:
        current = db.get(Document, doc)
        if change == 'version':
            (library / '旧名称.txt').write_bytes(b'new')
            versions.snapshot_working_copy(db, current)
        elif change == 'metadata':
            current.name = '新名称.txt'
        else:
            current.status = 'trashed'
        db.commit()
    with pytest.raises(ValueError, match='变化|失效'):
        tools.read_document(doc)


def test_snapshot_hash_and_symlinks_are_rejected(library, tmp_path):
    doc, meta = saved(library, '资料.txt', b'original')
    obj = library / '.teamhub/objects' / meta['content_hash'][:2] / meta['content_hash']
    obj.write_bytes(b'tampered')
    tools = toolset(library, {doc: meta})
    with pytest.raises(ValueError, match='快照'):
        tools.read_document(doc)
    obj.unlink()
    outside = tmp_path / 'outside.txt'
    outside.write_bytes(b'original')
    obj.symlink_to(outside)
    with pytest.raises(ValueError, match='快照'):
        tools.read_document(doc)


def test_library_switch_and_cancellation_stop_before_output(library, monkeypatch):
    doc, meta = saved(library, '资料.txt', b'text')
    def cancel():
        raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError, match='cancelled'):
        toolset(library, {doc: meta}, guard=cancel).read_document(doc)
    tools = toolset(library, {doc: meta})
    monkeypatch.setattr(settings, 'library_path', str(library.parent / 'new'))
    with pytest.raises(ValueError, match='文件库'):
        tools.read_document(doc)


def test_pdf_visual_includes_requested_text_dense_page(library):
    doc, meta = saved(library, '图纸.pdf', pdf())
    tools = toolset(library, {doc: meta})
    result = tools.read_visual(doc, pages=[2])
    assert result.metadata['page_count'] == 2
    assert result.metadata['read_pages'] == [2]
    assert result.images[0].page == 2
    assert result.images[0].data.startswith(b'\x89PNG')
    assert result.images[0].source_id == tools.sources[0]['source_id']
    assert '第 2 页' in tools.sources[0]['label']


def test_image_visual_uses_saved_bytes_and_reports_resolution(library):
    from PIL import Image
    image = BytesIO()
    Image.new('RGB', (120, 80), 'red').save(image, format='PNG')
    doc, meta = saved(library, '图片.png', image.getvalue())
    result = toolset(library, {doc: meta}).read_visual(doc)
    assert result.images[0].media_type == 'image/png'
    assert result.metadata['page_count'] == 1
    assert result.metadata['read_pages'] == [1]
    assert result.metadata['images'][0]['width'] == 120


def test_very_thin_image_is_scaled_to_nonzero_dimensions(library):
    from PIL import Image
    image = BytesIO()
    Image.new('RGB', (1, 4_000_001), 'red').save(image, format='PNG')
    doc, meta = saved(library, '细长图片.png', image.getvalue())
    result = toolset(library, {doc: meta}).read_visual(doc)
    dimensions = result.metadata['images'][0]
    assert dimensions['width'] >= 1 and dimensions['height'] >= 1
    assert dimensions['width'] * dimensions['height'] <= 4_000_000


@pytest.mark.parametrize('pages', [[0], [True], [1,2,3,4], [1,1], [-1], ['1']])
def test_invalid_visual_selection_rejected_without_render(library, pages):
    doc, meta = saved(library, '图纸.pdf', pdf())
    with pytest.raises(ValueError, match='页'):
        toolset(library, {doc: meta}).read_visual(doc, pages=pages)


def test_out_of_range_and_unsupported_visual_are_explicit(library):
    doc, meta = saved(library, '图纸.pdf', pdf())
    with pytest.raises(ValueError, match='页'):
        toolset(library, {doc: meta}).read_visual(doc, pages=[3])
    doc2, meta2 = saved(library, '材料.txt', b'no picture')
    with pytest.raises(ValueError, match='格式|图片|PDF'):
        toolset(library, {doc2: meta2}).read_visual(doc2)


def test_parser_change_during_read_is_discarded(library, monkeypatch):
    from backend.app.services import index_content
    doc, meta = saved(library, '资料.txt', b'old')
    original = index_content.extract_index
    def mutate(data, filename):
        assert database.active_session_count() == 0
        with database.SessionLocal() as db:
            db.get(Document, doc).name = 'changed.txt'
            db.commit()
        return original(data, filename)
    monkeypatch.setattr(index_content, 'extract_index', mutate)
    tools = toolset(library, {doc: meta})
    with pytest.raises(ValueError, match='变化|失效'):
        tools.read_document(doc)
    assert tools.sources == []


def test_oversized_snapshot_is_rejected_without_reading(library):
    doc, meta = saved(library, '超大.txt', b'file')
    obj = library / '.teamhub/objects' / meta['content_hash'][:2] / meta['content_hash']
    with obj.open('r+b') as output:
        output.truncate(32 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match='32|大小|上限'):
        toolset(library, {doc: meta}).read_document(doc)


def test_text_pagination_does_not_claim_full_read(library):
    doc, meta = saved(library, '长资料.txt', ('正文\n' * 60).encode())
    result = toolset(library, {doc: meta}).read_document(doc, offset=10, limit=3)
    assert len(result['units']) == 3
    assert result['offset'] == 10 and result['has_more']
    assert result['unit_count'] == 60
    assert result['returned_units'] == 3


def test_render_cancellation_kills_child_without_returning_output(monkeypatch):
    import subprocess
    from backend.app.services import partner_tools
    class WaitingProcess:
        returncode = None
        killed = False
        def communicate(self, input=None, timeout=None):
            if self.killed:
                return b'', b''
            raise subprocess.TimeoutExpired(['worker'], timeout)
        def kill(self):
            self.killed = True
            self.returncode = -9
        def poll(self):
            return self.returncode
    process = WaitingProcess()
    monkeypatch.setattr(partner_tools.subprocess, 'Popen', lambda *args, **kwargs: process)
    calls = []
    def cancel():
        calls.append(True)
        if len(calls) >= 2:
            raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError, match='cancelled'):
        partner_tools._render_isolated(b'pdf', '.pdf', [1], guard=cancel)
    assert process.killed


def test_render_timeout_kills_child(monkeypatch):
    import subprocess
    from backend.app.services import partner_tools
    class WaitingProcess:
        returncode = None
        killed = False
        def communicate(self, input=None, timeout=None):
            if self.killed:
                return b'', b''
            raise subprocess.TimeoutExpired(['worker'], timeout)
        def kill(self):
            self.killed = True
            self.returncode = -9
        def poll(self):
            return self.returncode
    process = WaitingProcess()
    monkeypatch.setattr(partner_tools.subprocess, 'Popen', lambda *args, **kwargs: process)
    clock = iter([0, 16, 20])
    monkeypatch.setattr(partner_tools.time, 'monotonic', lambda: next(clock))
    with pytest.raises(ValueError, match='15 秒'):
        partner_tools._render_isolated(b'pdf', '.pdf', [1])
    assert process.killed


@pytest.mark.parametrize('stdout,code', [
    (b'not JSON', 0), (b'{}', 0), (b'', -9),
    (b'x' * (16 * 1024 * 1024 + 65537), 0),
    (json.dumps({'images': [], 'page_count': 1}).encode(), 0),
], ids=['invalid-json', 'missing-data', 'killed', 'oversized', 'missing-images'])
def test_invalid_worker_outputs_are_rejected(monkeypatch, stdout, code):
    from backend.app.services import partner_tools
    monkeypatch.setattr(partner_tools, '_run_render_process', lambda *args: (stdout, code))
    with pytest.raises(ValueError, match='原页'):
        partner_tools._render_isolated(b'pdf', '.pdf', [1])


def test_renderer_cli_has_no_config_or_library_side_effects(tmp_path):
    import os
    import subprocess
    import sys
    env = dict(os.environ, TEAMHUB_LIBRARY_PATH=str(tmp_path / 'must-not-create'),
               TEAMHUB_USER_DATA_DIR=str(tmp_path / 'must-not-create-profile'))
    process = subprocess.run([sys.executable, '-m', 'backend.app.services.partner_tools',
        '--teamhub-partner-render', json.dumps({'suffix': '.exe', 'pages': [1]})],
        input=b'PRIVATE-DOCUMENT-TEXT', stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=5, env=env)
    assert process.returncode == 0
    result = json.loads(process.stdout)
    assert 'error' in result
    assert b'PRIVATE-DOCUMENT-TEXT' not in process.stdout + process.stderr
    assert not list(tmp_path.iterdir())


def test_encrypted_and_malformed_pdf_are_reported_without_source_text(library):
    writer, stream = PdfWriter(), BytesIO()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt('secret')
    writer.write(stream)
    for number, content in enumerate([stream.getvalue(), b'PRIVATE-DOCUMENT-TEXT-not-PDF']):
        doc, meta = saved(library, f'bad-{number}.pdf', content)
        with pytest.raises(ValueError, match='原页') as error:
            toolset(library, {doc: meta}).read_visual(doc)
        assert 'PRIVATE-DOCUMENT-TEXT' not in str(error.value)


def test_source_ids_are_stable_for_repeated_reads(library):
    doc, meta = saved(library, '说明.txt', '第一行\n第二行'.encode())
    tools = toolset(library, {doc: meta})
    first = tools.read_document(doc)
    second = tools.read_document(doc)
    assert len(tools.sources) == 2
    assert [unit['source_id'] for unit in first['units']] == [unit['source_id'] for unit in second['units']]


def spreadsheet(rows=101, columns=5, *, long_value=None):
    """真实 OOXML，仅用于临时库，不执行公式或宏。"""
    from xml.sax.saxutils import escape
    import zipfile
    namespace = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    relation = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    body = []
    for row in range(1, rows + 1):
        cells = []
        for column in range(1, columns + 1):
            value = long_value if long_value is not None else f'项目{row}-字段{column}'
            reference = f'{chr(64 + column)}{row}'
            cells.append(f'<c r="{reference}" t="inlineStr"><is><t>{escape(value)}</t></is></c>')
        body.append(f'<row r="{row}">{"".join(cells)}</row>')
    output = BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as target:
        target.writestr('xl/workbook.xml', f'<workbook xmlns="{namespace}" xmlns:r="{relation}"><sheets><sheet name="设备" r:id="rId1"/></sheets></workbook>')
        target.writestr('xl/_rels/workbook.xml.rels', f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{relation}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        target.writestr('xl/worksheets/sheet1.xml', f'<worksheet xmlns="{namespace}"><sheetData>{"".join(body)}</sheetData></worksheet>')
    return output.getvalue()


def test_spreadsheet_overview_and_two_row_blocks_cover_five_hundred_cells(library):
    doc, meta = saved(library, '设备表.xlsx', spreadsheet())
    events = []
    tools = toolset(library, {doc: meta}, emit=lambda kind, text: events.append((kind, text)))
    overview = tools.document_overview(doc)
    assert overview['format'] == 'spreadsheet'
    assert overview['sheets'][0]['sheet'] == '设备'
    assert overview['sheets'][0]['row_end'] == 101
    first = tools.read_table(doc, sheet='设备', start_row=1, row_count=80)
    assert first['returned_cells'] == 400
    assert first['next_call']['start_row'] == 81
    second = tools.read_table(**first['next_call'])
    assert second['returned_cells'] == 105
    assert second['next_call'] is None
    assert 'A81=项目81-字段1' in second['units'][0]['text']
    assert 'E101=项目101-字段5' in second['units'][0]['text']
    assert len(tools.sources) <= 4
    assert second['header_context'] and second['header_context'][0]['source_id']
    assert tools.progress['read_units'] == 505
    assert any('81' in text and '101' in text for _, text in events)


def test_table_long_row_uses_cursor_without_losing_cells_or_looping(library):
    doc, meta = saved(library, '长值.xlsx', spreadsheet(rows=2, columns=5, long_value='值' * 3900))
    tools = toolset(library, {doc: meta})
    calls, consumed, current = 0, set(), {'document_id': doc, 'row_count': 2}
    while current:
        result = tools.read_table(**current)
        assert sum(len(unit['text']) for unit in result['units']) <= 16000
        calls += 1
        consumed.update(result['read_cells'])
        current = result['next_call']
        assert calls <= 4
    assert len(consumed) == 10
    assert tools.progress['read_units'] == 10


@pytest.mark.parametrize('arguments', [
    {'start_row': 0}, {'start_row': True}, {'row_count': 201}, {'row_count': 0},
    {'offset': -1}, {'offset': True}, {'sheet': '../设备'},
])
def test_table_rejects_invalid_ranges_and_unknown_sheets(library, arguments):
    doc, meta = saved(library, '设备.xlsx', spreadsheet(rows=2))
    with pytest.raises(ValueError):
        toolset(library, {doc: meta}).read_table(doc, **arguments)


def test_generic_text_reads_more_than_twenty_units_and_reports_actual_offset(library):
    doc, meta = saved(library, '长说明.txt', ('内容\n' * 100).encode())
    events = []
    tools = toolset(library, {doc: meta}, emit=lambda kind, text: events.append(text))
    result = tools.read_document(doc)
    assert result['returned_units'] == 60
    assert result['next_offset'] == 60 and result['has_more']
    assert result['progress']['read_units'] == 60
    assert any('0' in event and '60' in event for event in events)


def test_checkpoint_keeps_full_read_text_and_resumes_with_stable_source_ids(library):
    doc, meta = saved(library, '长说明.txt', (('完整原文' * 200) + '\n下一段').encode())
    tools = toolset(library, {doc: meta})
    first = tools.read_document(doc, limit=1)
    checkpoint = tools.export_checkpoint()
    assert len(checkpoint['sources'][0]['excerpt']) == 300
    assert checkpoint['evidence'][0]['text'] == first['units'][0]['text']
    assert len(checkpoint['evidence'][0]['text']) > 300
    assert str(library) not in json.dumps(checkpoint, ensure_ascii=False)
    resumed = toolset(library, {doc: meta})
    resumed.restore_checkpoint(checkpoint)
    second = resumed.read_document(doc, offset=first['next_offset'])
    assert first['units'][0]['source_id'] == resumed.sources[0]['source_id']
    assert second['units'][0]['source_id'] == 'S2'
    assert resumed.progress['read_units'] == 2
    assert resumed.exposed_document_ids == {doc}


def test_checkpoint_restores_table_cursor_and_allows_forward_progress(library):
    doc, meta = saved(library, '设备.xlsx', spreadsheet())
    first_tools = toolset(library, {doc: meta})
    first = first_tools.read_table(doc, row_count=40)
    second_tools = toolset(library, {doc: meta})
    second_tools.restore_checkpoint(first_tools.export_checkpoint())
    second = second_tools.read_table(**first['next_call'])
    assert second['row_start'] == 41 and second_tools.progress['read_units'] == 400
    assert second_tools.progress['no_progress_count'] == 0


def test_repeated_reads_pause_only_after_repeated_absence_of_new_evidence(library):
    from backend.app.services.partner_tools import PartnerNoProgress
    doc, meta = saved(library, '说明.txt', ('一\n二\n三\n四').encode())
    tools = toolset(library, {doc: meta})
    tools.read_document(doc, limit=1)
    tools.read_document(doc, limit=1)
    tools.read_document(doc, limit=1)
    with pytest.raises(PartnerNoProgress, match='新增|重复'):
        tools.read_document(doc, limit=1)
    # 向后翻页即产生新证据，不是按同名工具累计的死循环。
    result = tools.read_document(doc, offset=1, limit=1)
    assert result['progress']['no_progress_count'] == 0


def test_overlapping_table_and_cell_reads_share_real_evidence_progress(library):
    doc, meta = saved(library, '设备.xlsx', spreadsheet(rows=2, columns=2))
    tools = toolset(library, {doc: meta})
    tools.read_table(doc, row_count=2)
    result = tools.read_document(doc, limit=4)
    assert result['progress']['read_units'] == 4
    assert result['progress']['no_progress_count'] == 1


def test_checkpoint_rejects_changed_scope_version_and_unknown_fields(library):
    doc, meta = saved(library, '说明.txt', b'one')
    tools = toolset(library, {doc: meta})
    tools.read_document(doc)
    checkpoint = tools.export_checkpoint()
    with pytest.raises(ValueError, match='授权|范围'):
        toolset(library, {}).restore_checkpoint(checkpoint)
    tampered = dict(checkpoint, arbitrary_path='/private/secret')
    with pytest.raises(ValueError, match='进度|检查点'):
        toolset(library, {doc: meta}).restore_checkpoint(tampered)
    with database.SessionLocal() as db:
        db.get(Document, doc).name = 'renamed.txt'
        db.commit()
    with pytest.raises(ValueError, match='变化|失效'):
        toolset(library, {doc: meta}).restore_checkpoint(checkpoint)


def test_evidence_capacity_is_explicit_and_does_not_claim_unreturned_text(library, monkeypatch):
    from backend.app.services import partner_tools
    doc, meta = saved(library, '长说明.txt', ('原文' * 1000).encode())
    tools = toolset(library, {doc: meta})
    monkeypatch.setattr(partner_tools, 'MAX_CHECKPOINT_TEXT', 1000)
    with pytest.raises(partner_tools.PartnerEvidenceLimit, match='证据|进度'):
        tools.read_document(doc)
    assert tools.progress['read_units'] == 0 and tools.sources == []


def test_visual_checkpoint_has_no_image_bytes_and_repeat_pages_are_not_progress(library, monkeypatch):
    from backend.app.services import partner_tools
    doc, meta = saved(library, '图纸.pdf', pdf())
    png = b'\x89PNG\r\n\x1a\n' + b'FAKE'
    monkeypatch.setattr(partner_tools, '_render_isolated', lambda *args, **kwargs: {
        'page_count': 2, 'images': [{'page': args[2][0], 'width': 1, 'height': 1,
            'data': __import__('base64').b64encode(png).decode()}], 'notes': []})
    tools = toolset(library, {doc: meta})
    tools.read_visual(doc, [1])
    checkpoint = tools.export_checkpoint()
    serialized = json.dumps(checkpoint)
    assert 'FAKE' not in serialized and __import__('base64').b64encode(png).decode() not in serialized
    resumed = toolset(library, {doc: meta})
    resumed.restore_checkpoint(checkpoint)
    resumed.read_visual(doc, [1])
    assert resumed.progress['no_progress_count'] == 1
    resumed.read_visual(doc, [2])
    assert resumed.progress['no_progress_count'] == 0


def test_distinct_search_excerpts_of_same_document_are_real_progress(library):
    doc, meta = saved(library, '说明.txt', b'alpha text\nbeta text\ngamma text\ndelta text')
    assert indexing.process_one()
    tools = toolset(library, {doc: meta})
    for query in ('alpha', 'beta', 'gamma', 'delta'):
        tools.search_documents(query)
        assert tools.progress['no_progress_count'] == 0


def test_visual_then_text_same_pdf_page_preserves_full_text_checkpoint(library):
    doc, meta = saved(library, '图纸.pdf', pdf())
    tools = toolset(library, {doc: meta})
    visual = tools.read_visual(doc, [1])
    textual = tools.read_document(doc, limit=1)
    assert visual.images[0].source_id == textual['units'][0]['source_id']
    checkpoint = tools.export_checkpoint()
    assert checkpoint['evidence'][0]['text'] == textual['units'][0]['text']


@pytest.mark.parametrize('change', ['source_path', 'range_path', 'source_ids', 'raw_bytes'])
def test_checkpoint_schema_rejects_paths_unknown_sources_and_binary(library, change):
    from copy import deepcopy
    doc, meta = saved(library, '说明.txt', b'one')
    tools = toolset(library, {doc: meta})
    tools.read_document(doc)
    value = deepcopy(tools.export_checkpoint())
    if change == 'source_path':
        value['sources'][0]['location']['path'] = '/etc/passwd'
        value['evidence'][0]['location']['path'] = '/etc/passwd'
    elif change == 'range_path':
        value['read_ranges'][0]['path'] = '/etc/passwd'
    elif change == 'source_ids':
        value['evidence'][0]['source_id'] = 'S999'
    else:
        value['evidence'][0]['text'] = b'raw-image-bytes'
    with pytest.raises(ValueError, match='检查点|进度'):
        toolset(library, {doc: meta}).restore_checkpoint(value)
