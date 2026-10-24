"""Synthetic Office packages exercise embedded images without user files or models."""
from io import BytesIO
import json
import zipfile

from PIL import Image
import pytest

from backend.tests.test_partner_tools import library, saved, toolset


R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
ODF = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
       'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
       'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
       'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
       'xmlns:xlink="http://www.w3.org/1999/xlink"')


def png(color='red'):
    output = BytesIO()
    Image.new('RGB', (32, 24), color).save(output, 'PNG')
    return output.getvalue()


def package(parts):
    output = BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for name, value in parts.items():
            archive.writestr(name, value)
    return output.getvalue()


def office_images(suffix, *, duplicate=False, external=False):
    """One referenced image plus an unreferenced private image in every package."""
    if suffix == '.docx':
        reference = '<w:p><w:r><w:drawing><a:blip r:embed="picture"/></w:drawing></w:r></w:p>'
        body = reference * (2 if duplicate else 1)
        if external:
            body += '<w:p><w:r><w:drawing><a:blip r:link="linked"/></w:drawing></w:r></w:p>'
        return package({'word/document.xml': f'<w:document xmlns:w="{W}" xmlns:a="{A}" xmlns:r="{R}"><w:body>{body}</w:body></w:document>',
            'word/_rels/document.xml.rels': f'<Relationships xmlns="{REL}"><Relationship Id="picture" Type="{R}/image" Target="media/visible.png"/><Relationship Id="linked" Type="{R}/image" Target="https://invalid.example/private.png" TargetMode="External"/></Relationships>',
            'word/media/visible.png': png(), 'word/media/private.png': png('blue')})
    if suffix == '.xlsx':
        return package({'xl/workbook.xml': f'<workbook xmlns="{S}" xmlns:r="{R}"><sheets><sheet name="电路" sheetId="1" r:id="sheet"/></sheets></workbook>',
            'xl/_rels/workbook.xml.rels': f'<Relationships xmlns="{REL}"><Relationship Id="sheet" Type="{R}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
            'xl/worksheets/sheet1.xml': f'<worksheet xmlns="{S}" xmlns:r="{R}"><sheetData/><drawing r:id="drawing"/></worksheet>',
            'xl/worksheets/_rels/sheet1.xml.rels': f'<Relationships xmlns="{REL}"><Relationship Id="drawing" Type="{R}/drawing" Target="../drawings/drawing1.xml"/></Relationships>',
            'xl/drawings/drawing1.xml': f'<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" xmlns:a="{A}" xmlns:r="{R}"><xdr:twoCellAnchor><xdr:from><xdr:col>2</xdr:col><xdr:row>4</xdr:row></xdr:from><xdr:pic><a:blip r:embed="picture"/></xdr:pic></xdr:twoCellAnchor></xdr:wsDr>',
            'xl/drawings/_rels/drawing1.xml.rels': f'<Relationships xmlns="{REL}"><Relationship Id="picture" Type="{R}/image" Target="../media/visible.png"/></Relationships>',
            'xl/media/visible.png': png(), 'xl/media/private.png': png('blue')})
    if suffix == '.pptx':
        return package({'ppt/presentation.xml': f'<p:presentation xmlns:p="{P}" xmlns:r="{R}"><p:sldIdLst><p:sldId id="256" r:id="slide"/></p:sldIdLst></p:presentation>',
            'ppt/_rels/presentation.xml.rels': f'<Relationships xmlns="{REL}"><Relationship Id="slide" Type="{R}/slide" Target="slides/slide1.xml"/></Relationships>',
            'ppt/slides/slide1.xml': f'<p:sld xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}"><p:cSld><p:spTree><p:pic><a:blip r:embed="picture"/></p:pic></p:spTree></p:cSld></p:sld>',
            'ppt/slides/_rels/slide1.xml.rels': f'<Relationships xmlns="{REL}"><Relationship Id="picture" Type="{R}/image" Target="../media/visible.png"/></Relationships>',
            'ppt/media/visible.png': png(), 'ppt/media/private.png': png('blue')})
    image = '<draw:frame><draw:image xlink:href="Pictures/visible.png"/></draw:frame>'
    if suffix == '.odt':
        body = f'<office:text><text:p>{image}</text:p></office:text>'
    elif suffix == '.ods':
        body = f'<office:spreadsheet><table:table table:name="电路"><table:table-row><table:table-cell>{image}</table:table-cell></table:table-row></table:table></office:spreadsheet>'
    else:
        body = f'<office:presentation><draw:page draw:name="首页">{image}</draw:page></office:presentation>'
    return package({'content.xml': f'<office:document-content {ODF}><office:body>{body}</office:body></office:document-content>',
                    'Pictures/visible.png': png(), 'Pictures/private.png': png('blue')})


@pytest.mark.parametrize('suffix', ['.docx', '.xlsx', '.pptx', '.odt', '.ods', '.odp'])
def test_referenced_office_images_are_discoverable_readable_and_fixed_to_version(library, suffix):
    doc, meta = saved(library, '图文资料' + suffix, office_images(suffix))
    tools = toolset(library, {doc: meta})
    overview = tools.document_overview(doc)
    assert overview['visual_asset_count'] == 1
    asset = overview['visual_assets'][0]
    assert set(asset) == {'image_id', 'label', 'location'}
    assert asset['location']['image_index'] == 1
    assert 'visible.png' not in json.dumps(overview, ensure_ascii=False)
    assert 'private.png' not in json.dumps(overview, ensure_ascii=False)
    (library / ('图文资料' + suffix)).write_bytes(b'unsaved edits')
    result = tools.read_embedded_images(doc, [asset['image_id']])
    assert result.images[0].data.startswith(b'\x89PNG\r\n\x1a\n')
    assert result.metadata['images'][0]['location'] == asset['location']
    assert result.metadata['content_hash'] == meta['content_hash']
    assert result.metadata['images'][0]['source_id'] == tools.sources[0]['source_id']
    assert tools.sources[0]['location'] == asset['location']
    with Image.open(BytesIO(result.images[0].data)) as image:
        assert image.getpixel((0, 0)) == (255, 0, 0)


def test_docx_repeated_image_references_have_distinct_real_anchors_and_resume(library):
    doc, meta = saved(library, '纯图.docx', office_images('.docx', duplicate=True, external=True))
    tools = toolset(library, {doc: meta})
    overview = tools.document_overview(doc)
    assets = overview['visual_assets']
    assert [asset['location']['paragraph'] for asset in assets] == [1, 2]
    assert len({asset['image_id'] for asset in assets}) == 2
    first = tools.read_embedded_images(doc, [assets[0]['image_id']])
    checkpoint = tools.export_checkpoint()
    assert 'data:image' not in json.dumps(checkpoint)
    assert 'iVBOR' not in json.dumps(checkpoint)
    restored = toolset(library, {doc: meta})
    restored.restore_checkpoint(checkpoint)
    assert restored.document_overview(doc)['visual_assets'] == assets
    again = restored.read_embedded_images(doc, [assets[0]['image_id']])
    second = restored.read_embedded_images(doc, [assets[1]['image_id']])
    assert first.metadata['images'][0]['source_id'] == again.metadata['images'][0]['source_id']
    assert second.metadata['images'][0]['source_id'] != again.metadata['images'][0]['source_id']
    assert restored.progress['embedded_images'] == 2
    assert any('外部' in note for note in overview['visual_notes'])


def test_word_textbox_and_following_picture_share_text_index_paragraph_numbering(library):
    from backend.app.services.index_content import extract_index
    data = package({'word/document.xml': f'<w:document xmlns:w="{W}" xmlns:a="{A}" xmlns:r="{R}"><w:body>'
        '<w:p><w:r><w:t>第一段</w:t><w:drawing><w:txbxContent><w:p><w:r><w:t>文本框</w:t>'
        '<a:blip r:embed="picture"/></w:r></w:p></w:txbxContent></w:drawing></w:r></w:p>'
        '<w:p><w:r><w:t>第二段</w:t><w:drawing><a:blip r:embed="picture"/></w:drawing></w:r></w:p>'
        '</w:body></w:document>',
        'word/_rels/document.xml.rels': f'<Relationships xmlns="{REL}"><Relationship Id="picture" Type="{R}/image" Target="media/visible.png"/></Relationships>',
        'word/media/visible.png': png()})
    indexed = extract_index(data, '文本框.docx')
    doc, meta = saved(library, '文本框.docx', data)
    assets = toolset(library, {doc: meta}).document_overview(doc)['visual_assets']
    assert [unit['location']['paragraph'] for unit in indexed['units']] == [1, 2]
    assert [asset['location']['paragraph'] for asset in assets] == [1, 2]


@pytest.mark.parametrize('suffix', ['.pptx', '.xlsx'])
def test_repeated_structure_targets_cannot_amplify_archive_parsing(suffix, monkeypatch):
    from backend.app.services import document_visuals
    with zipfile.ZipFile(BytesIO(office_images(suffix))) as archive:
        parts = {item.filename: archive.read(item) for item in archive.infolist()}
    if suffix == '.pptx':
        part = 'ppt/presentation.xml'
        parts[part] = parts[part].replace(b'<p:sldId id="256" r:id="slide"/>',
            b'<p:sldId id="256" r:id="slide"/>' * 500)
    else:
        part = 'xl/workbook.xml'
        parts[part] = parts[part].replace('<sheet name="电路" sheetId="1" r:id="sheet"/>'.encode(),
            '<sheet name="电路" sheetId="1" r:id="sheet"/>'.encode() * 500)
    parsed = []
    original = document_visuals._xml
    def tracked(*args, **kwargs):
        parsed.append(args[2])
        return original(*args, **kwargs)
    monkeypatch.setattr(document_visuals, '_xml', tracked)
    with pytest.raises(ValueError, match='重复'):
        document_visuals.discover_visual_assets(package(parts), 'test' + suffix)
    assert len(parsed) < 10


@pytest.mark.parametrize('mode', ['RGBA', 'P'])
@pytest.mark.parametrize('embedded', [False, True])
def test_transparent_black_diagram_keeps_visible_lines_on_white(library, mode, embedded):
    from PIL import ImageDraw
    output = BytesIO()
    if mode == 'RGBA':
        image = Image.new('RGBA', (32, 24), (0, 0, 0, 0))
        ImageDraw.Draw(image).rectangle((8, 8, 20, 16), fill=(0, 0, 0, 255))
    else:
        image = Image.new('P', (32, 24), 0)
        image.putpalette([0, 0, 0] * 256)
        image.info['transparency'] = 0
        ImageDraw.Draw(image).rectangle((8, 8, 20, 16), fill=1)
    image.save(output, 'PNG')
    if embedded:
        with zipfile.ZipFile(BytesIO(office_images('.docx'))) as archive:
            parts = {item.filename: archive.read(item) for item in archive.infolist()}
        parts['word/media/visible.png'] = output.getvalue()
        doc, meta = saved(library, '透明图.docx', package(parts))
        tools = toolset(library, {doc: meta})
        result = tools.read_embedded_images(doc, ['image-1'])
    else:
        doc, meta = saved(library, '透明图.png', output.getvalue())
        result = toolset(library, {doc: meta}).read_visual(doc)
    with Image.open(BytesIO(result.images[0].data)) as rendered:
        assert rendered.getpixel((0, 0)) == (255, 255, 255)
        assert rendered.getpixel((12, 12)) == (0, 0, 0)


def test_odt_auxiliary_paragraphs_and_images_do_not_shift_body_sources(library):
    from backend.app.services.index_content import extract_index
    from backend.app.services.document_visuals import discover_visual_assets
    body = ('<office:text><text:p>正文一<office:annotation><text:p>批注'
            '<draw:image xlink:href="Pictures/private.png"/></text:p></office:annotation>'
            '<text:note><text:note-body><text:p>脚注</text:p></text:note-body></text:note></text:p>'
            '<text:p>正文二<draw:frame><draw:image xlink:href="Pictures/visible.png"/></draw:frame></text:p>'
            '</office:text>')
    data = package({'content.xml': f'<office:document-content {ODF}><office:body>{body}</office:body></office:document-content>',
                    'Pictures/visible.png': png(), 'Pictures/private.png': png('blue')})
    indexed = extract_index(data, '批注.odt')
    assets, _ = discover_visual_assets(data, '批注.odt')
    assert [unit['location']['paragraph'] for unit in indexed['units']] == [1, 2]
    assert len(assets) == 1 and assets[0].location['paragraph'] == 2


def test_odt_repeated_invalid_images_have_a_global_node_work_limit(monkeypatch):
    from backend.app.services import document_visuals
    monkeypatch.setattr(document_visuals, 'MAX_DISCOVERY_NODES', 32, raising=False)
    attempted = []
    original = document_visuals._local_target
    def tracked(*args):
        attempted.append(True)
        return original(*args)
    monkeypatch.setattr(document_visuals, '_local_target', tracked)
    body = ('<office:text><table:table><table:table-row table:number-rows-repeated="100">'
            '<table:table-cell><text:p>' + '<draw:image xlink:href="https://example.invalid/a.png"/>' * 10 +
            '</text:p></table:table-cell></table:table-row></table:table></office:text>')
    data = package({'content.xml': f'<office:document-content {ODF}><office:body>{body}</office:body></office:document-content>'})
    assets, notes = document_visuals.discover_visual_assets(data, '重复.odt')
    assert assets == [] and len(attempted) <= 32
    assert any('工作量' in note for note in notes)


@pytest.mark.parametrize('image_ids', [[], ['unknown'], ['image-1', 'image-1'], ['x'] * 4, [True], 'image-1'])
def test_invalid_embedded_image_selection_exposes_no_source(library, image_ids):
    doc, meta = saved(library, '图.docx', office_images('.docx'))
    tools = toolset(library, {doc: meta})
    with pytest.raises(ValueError):
        tools.read_embedded_images(doc, image_ids)
    assert tools.sources == []


def test_image_reads_obey_total_task_image_limits_and_checkpoint_carries_counter(library, monkeypatch):
    from backend.app.services import partner_tools
    doc, meta = saved(library, '图.docx', office_images('.docx', duplicate=True))
    tools = toolset(library, {doc: meta})
    assets = tools.document_overview(doc)['visual_assets']
    monkeypatch.setattr(partner_tools, 'MAX_TASK_IMAGES', 1)
    tools.read_embedded_images(doc, [assets[0]['image_id']])
    restored = toolset(library, {doc: meta})
    restored.restore_checkpoint(tools.export_checkpoint())
    with pytest.raises(ValueError, match='图像|图片'):
        restored.read_embedded_images(doc, [assets[1]['image_id']])
    assert len(restored.sources) == 1


@pytest.mark.parametrize('suffix', ['.docm', '.xltx', '.ppsx', '.ott', '.ots', '.otp'])
def test_office_aliases_share_safe_embedded_image_reading(library, suffix):
    canonical = dict(zip(['.docm', '.xltx', '.ppsx', '.ott', '.ots', '.otp'],
                         ['.docx', '.xlsx', '.pptx', '.odt', '.ods', '.odp']))[suffix]
    doc, meta = saved(library, '模板' + suffix, office_images(canonical))
    tools = toolset(library, {doc: meta})
    asset = tools.document_overview(doc)['visual_assets'][0]
    assert tools.read_embedded_images(doc, [asset['image_id']]).images


def test_unused_archive_images_are_never_opened_even_when_their_bytes_are_invalid(library, monkeypatch):
    raw = office_images('.docx', external=True)
    with zipfile.ZipFile(BytesIO(raw)) as original:
        data = package({name: (b'not an image' if name.endswith('private.png') else original.read(name))
                        for name in original.namelist()})
    opened = []
    original_open = zipfile.ZipFile.open
    def tracked_open(archive, name, *args, **kwargs):
        opened.append(name.filename if isinstance(name, zipfile.ZipInfo) else name)
        return original_open(archive, name, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, 'open', tracked_open)
    doc, meta = saved(library, '引用.docx', data)
    tools = toolset(library, {doc: meta})
    asset = tools.document_overview(doc)['visual_assets'][0]
    tools.read_embedded_images(doc, [asset['image_id']])
    assert 'word/media/private.png' not in opened


@pytest.mark.parametrize('target', ['https://invalid.example/image.png', 'file:///private/secret.png',
                                  '../../../secret.png', '//invalid.example/image.png',
                                  'media/visible.emf'])
def test_external_traversal_and_unsupported_image_targets_are_explicit_gaps(library, target):
    raw = office_images('.docx')
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    parts['word/_rels/document.xml.rels'] = parts['word/_rels/document.xml.rels'].replace(
        b'Target="media/visible.png"', ('Target="' + target + '"').encode())
    if target.endswith('.emf'):
        parts['word/media/visible.emf'] = b'unsupported EMF'
    doc, meta = saved(library, '无效图.docx', package(parts))
    tools = toolset(library, {doc: meta})
    overview = tools.document_overview(doc)
    assert overview['visual_assets'] == []
    assert overview['visual_notes']
    with pytest.raises(ValueError):
        tools.read_embedded_images(doc, ['image-1'])
    assert tools.sources == []


def test_corrupt_embedded_image_and_byte_budget_never_create_unreturned_sources(library, monkeypatch):
    from backend.app.services import partner_tools
    doc, meta = saved(library, '图.docx', office_images('.docx'))
    tools = toolset(library, {doc: meta})
    asset = tools.document_overview(doc)['visual_assets'][0]
    monkeypatch.setattr(partner_tools, 'MAX_TASK_IMAGE_BYTES', 1)
    with pytest.raises(ValueError, match='图像|图片'):
        tools.read_embedded_images(doc, [asset['image_id']])
    assert tools.sources == [] and tools.progress['image_usage'] == {'count': 0, 'bytes': 0}
    monkeypatch.setattr(partner_tools, 'MAX_TASK_IMAGE_BYTES', 12 * 1024 * 1024)
    raw = office_images('.docx')
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    parts['word/media/visible.png'] = b'corrupted raster'
    broken, broken_meta = saved(library, '损坏图.docx', package(parts))
    broken_tools = toolset(library, {broken: broken_meta})
    with pytest.raises(ValueError, match='原页'):
        broken_tools.read_embedded_images(broken, ['image-1'])
    assert broken_tools.sources == []


def test_visual_asset_limit_is_explicit_and_unlisted_ids_cannot_be_read(library, monkeypatch):
    from backend.app.services import document_visuals
    monkeypatch.setattr(document_visuals, 'MAX_ASSETS', 1)
    doc, meta = saved(library, '多图.docx', office_images('.docx', duplicate=True))
    tools = toolset(library, {doc: meta})
    overview = tools.document_overview(doc)
    assert overview['visual_asset_count'] == 1 and overview['visual_state'] == 'partial'
    assert any('上限' in note for note in overview['visual_notes'])
    with pytest.raises(ValueError, match='不存在'):
        tools.read_embedded_images(doc, ['image-2'])


def test_native_multiframe_tiff_preserves_real_frame_numbers_and_image_budget(library):
    output = BytesIO()
    first, second = Image.new('RGB', (8, 8), 'red'), Image.new('RGB', (8, 8), 'blue')
    first.save(output, 'TIFF', save_all=True, append_images=[second])
    doc, meta = saved(library, '老扫描.tiff', output.getvalue())
    tools = toolset(library, {doc: meta})
    result = tools.read_visual(doc, [2])
    assert result.metadata['page_count'] == 2
    assert result.metadata['read_pages'] == [2]
    assert tools.sources[0]['location'] == {'page': 2}
    with Image.open(BytesIO(result.images[0].data)) as image:
        assert image.getpixel((0, 0)) == (0, 0, 255)
    assert tools.progress['image_usage']['count'] == 1


def test_nested_word_textbox_image_has_only_its_actual_paragraph_anchor(library):
    raw = office_images('.docx')
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    content = parts['word/document.xml'].decode()
    content = content.replace('<w:body>', '<w:body><w:p><w:r><w:drawing><w:txbxContent>')
    content = content.replace('</w:body>', '</w:txbxContent></w:drawing></w:r></w:p></w:body>')
    parts['word/document.xml'] = content
    doc, meta = saved(library, '文本框.docx', package(parts))
    assets = toolset(library, {doc: meta}).document_overview(doc)['visual_assets']
    # 文本框随所属外层段落定位，与正文索引共享编号。
    assert len(assets) == 1 and assets[0]['location']['paragraph'] == 1


def test_odt_body_image_without_paragraph_has_explicit_image_anchor(library):
    raw = office_images('.odt')
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    parts['content.xml'] = parts['content.xml'].replace(b'<text:p>', b'').replace(b'</text:p>', b'')
    doc, meta = saved(library, '无段落图.odt', package(parts))
    assets = toolset(library, {doc: meta}).document_overview(doc)['visual_assets']
    assert len(assets) == 1 and assets[0]['location'] == {'image_index': 1}


def test_render_process_rejects_oversized_stdout_while_child_is_running(monkeypatch):
    import subprocess
    from backend.app.services import partner_tools
    class OversizedProcess:
        returncode = None
        killed = False
        def __init__(self, stdout):
            self.stdout = stdout
        def communicate(self, input=None, timeout=None):
            if self.killed:
                return None, None
            if hasattr(self.stdout, 'write'):
                self.stdout.write(b'x' * (16 * 1024 * 1024 + 65537))
                self.stdout.flush()
            raise subprocess.TimeoutExpired(['worker'], timeout)
        def kill(self):
            self.killed, self.returncode = True, -9
        def poll(self):
            return self.returncode
    processes = []
    def start(*args, **kwargs):
        process = OversizedProcess(kwargs['stdout'])
        processes.append(process)
        return process
    monkeypatch.setattr(partner_tools.subprocess, 'Popen', start)
    clock = iter([0, 1, 2, 16, 20])
    monkeypatch.setattr(partner_tools.time, 'monotonic', lambda: next(clock))
    with pytest.raises(ValueError, match='输出'):
        partner_tools._render_isolated(b'png', '.png', [1])
    assert processes[0].killed


@pytest.mark.parametrize('suffix', ['.csv', '.ods'])
def test_table_reading_uses_cell_capability_for_non_xlsx_formats(library, suffix):
    if suffix == '.csv':
        data = '设备,编号\n开关,101\n'.encode()
    else:
        data = package({'content.xml': f'<office:document-content {ODF}><office:body><office:spreadsheet><table:table table:name="数据"><table:table-row><table:table-cell office:value-type="string"><text:p>开关</text:p></table:table-cell></table:table-row></table:table></office:spreadsheet></office:body></office:document-content>'})
    doc, meta = saved(library, '表格' + suffix, data)
    tools = toolset(library, {doc: meta})
    assert tools.document_overview(doc)['format'] == 'spreadsheet'
    result = tools.read_table(doc)
    assert result['returned_cells'] > 0
    assert '开关' in result['units'][0]['text']


def test_strict_ooxml_visual_relationships_and_anchors_are_normalized(library):
    raw = office_images('.xlsx')
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    replacements = {
        S: 'http://purl.oclc.org/ooxml/spreadsheetml/main',
        R: 'http://purl.oclc.org/ooxml/officeDocument/relationships',
        REL: 'http://purl.oclc.org/ooxml/package/relationships',
        A: 'http://purl.oclc.org/ooxml/drawingml/main',
        'http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing':
            'http://purl.oclc.org/ooxml/drawingml/spreadsheetDrawing',
    }
    for name, value in parts.items():
        if name.endswith(('.xml', '.rels')):
            for old, new in replacements.items():
                value = value.replace(old.encode(), new.encode())
            parts[name] = value
    doc, meta = saved(library, '严格.xlsx', package(parts))
    tools = toolset(library, {doc: meta})
    asset = tools.document_overview(doc)['visual_assets'][0]
    assert asset['location'] == {'sheet': '电路', 'sheet_index': 1, 'row': 5, 'column': 3, 'image_index': 1}
    assert tools.read_embedded_images(doc, [asset['image_id']]).images


def test_changed_version_after_embedded_render_is_rejected_without_source(library, monkeypatch):
    from backend.app import database
    from backend.app.models import Document
    from backend.app.services import partner_tools
    doc, meta = saved(library, '版本图.docx', office_images('.docx'))
    tools = toolset(library, {doc: meta})
    render = partner_tools._render_isolated
    def changed_render(*args, **kwargs):
        result = render(*args, **kwargs)
        with database.SessionLocal() as db:
            db.get(Document, doc).name = 'changed.docx'
            db.commit()
        return result
    monkeypatch.setattr(partner_tools, '_render_isolated', changed_render)
    with pytest.raises(ValueError, match='变化|失效'):
        tools.read_embedded_images(doc, ['image-1'])
    assert tools.sources == []


def test_failed_second_image_does_not_register_first_image_source(library):
    raw = office_images('.docx', duplicate=True)
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    parts['word/document.xml'] = parts['word/document.xml'].replace(b'r:embed="picture"', b'r:embed="second"', 1)
    parts['word/_rels/document.xml.rels'] = parts['word/_rels/document.xml.rels'].replace(
        b'</Relationships>', f'<Relationship Id="second" Type="{R}/image" Target="media/broken.png"/></Relationships>'.encode())
    parts['word/media/broken.png'] = b'broken'
    doc, meta = saved(library, '半坏.docx', package(parts))
    tools = toolset(library, {doc: meta})
    with pytest.raises(ValueError):
        tools.read_embedded_images(doc, ['image-2', 'image-1'])
    assert tools.sources == [] and tools.progress['image_usage']['count'] == 0


def test_ods_out_of_supported_anchor_range_is_reported_without_invalid_resume_sources(library):
    raw = office_images('.ods')
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    parts['content.xml'] = parts['content.xml'].replace(
        b'<table:table-row>', b'<table:table-row table:number-rows-repeated="1048576"/><table:table-row>', 1)
    doc, meta = saved(library, '超范围图.ods', package(parts))
    overview = toolset(library, {doc: meta}).document_overview(doc)
    assert overview['visual_assets'] == []
    assert any('锚点' in note and '范围' in note for note in overview['visual_notes'])


@pytest.mark.parametrize('column', ['16384', '٢'])
def test_xlsx_invalid_column_anchor_is_rejected_as_malformed(library, column):
    raw = office_images('.xlsx')
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    parts['xl/drawings/drawing1.xml'] = parts['xl/drawings/drawing1.xml'].replace(
        b'<xdr:col>2</xdr:col>', f'<xdr:col>{column}</xdr:col>'.encode())
    doc, meta = saved(library, '错误锚点.xlsx', package(parts))
    overview = toolset(library, {doc: meta}).document_overview(doc)
    assert overview['visual_state'] == 'failed'
    assert overview['visual_assets'] == []
    assert any('锚点无效' in note for note in overview['visual_notes'])


@pytest.mark.parametrize('value', ['٢', '9' * 5000], ids=['unicode', 'overlong'])
def test_odf_repeat_numbers_are_ascii_and_bounded_before_integer_conversion(value):
    from xml.etree import ElementTree
    from backend.app.services.document_visuals import _repeat
    element = ElementTree.Element('row', {
        '{urn:oasis:names:tc:opendocument:xmlns:table:1.0}number-rows-repeated': value})
    with pytest.raises(ValueError, match='开放表格重复范围无效'):
        _repeat(element, 'number-rows-repeated')
