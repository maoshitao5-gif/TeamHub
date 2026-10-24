"""Synthetic document packages exercise the public, offline extraction boundary."""
from io import BytesIO
from pathlib import Path
import zipfile

import pytest

from backend.app.services import ai_text, index_content

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
OFFICE = 'urn:oasis:names:tc:opendocument:xmlns:office:1.0'
TEXT = 'urn:oasis:names:tc:opendocument:xmlns:text:1.0'
TABLE = 'urn:oasis:names:tc:opendocument:xmlns:table:1.0'
DRAW = 'urn:oasis:names:tc:opendocument:xmlns:drawing:1.0'


def package(files):
    output = BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return output.getvalue()


def word(body, namespace=W, extra=None):
    return package({'word/document.xml': f'<w:document xmlns:w="{namespace}" xmlns:a="{A}"><w:body>{body}</w:body></w:document>', **(extra or {})})


def odf(body, kind, extra=None):
    return package({'mimetype': f'application/vnd.oasis.opendocument.{kind}',
        'content.xml': f'<office:document-content xmlns:office="{OFFICE}" xmlns:text="{TEXT}" xmlns:table="{TABLE}" xmlns:draw="{DRAW}" xmlns:xlink="http://www.w3.org/1999/xlink"><office:body>{body}</office:body></office:document-content>', **(extra or {})})


def presentation(target='slides/slide7.xml'):
    return package({
        'ppt/presentation.xml': f'<p:presentation xmlns:p="{P}" xmlns:r="{R}"><p:sldIdLst><p:sldId id="7" r:id="second"/><p:sldId id="1" r:id="first"/></p:sldIdLst></p:presentation>',
        'ppt/_rels/presentation.xml.rels': f'<Relationships xmlns="{REL}"><Relationship Id="second" Type="{R}/slide" Target="{target}"/><Relationship Id="first" Type="{R}/slide" Target="slides/slide1.xml"/></Relationships>',
        'ppt/slides/slide7.xml': f'<p:sld xmlns:p="{P}" xmlns:a="{A}"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>FIRST IN PRESENTATION</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>',
        'ppt/slides/slide1.xml': f'<p:sld xmlns:p="{P}" xmlns:a="{A}"><p:cSld><p:spTree><p:graphicFrame><a:graphic><a:graphicData><a:tbl><a:tr><a:tc><a:txBody><a:p><a:r><a:t>TABLE CELL</a:t></a:r></a:p></a:txBody></a:tc></a:tr></a:tbl></a:graphicData></a:graphic></p:graphicFrame></p:spTree></p:cSld></p:sld>',
    })


def text(result):
    return '\n'.join(unit['text'] for unit in result['units'])


def reasons(result):
    return {gap['reason'] for gap in result['gaps']}


@pytest.mark.parametrize('suffix,canonical', [('.DOCM', '.docx'), ('.dotx', '.docx'), ('.xlsm', '.xlsx'), ('.xltm', '.xlsx'), ('.potx', '.pptx'), ('.ppsx', '.pptx'), ('.ott', '.odt'), ('.ots', '.ods'), ('.otp', '.odp'), ('.txt', '.txt')])
def test_canonical_office_aliases(suffix, canonical):
    assert ai_text.canonical_suffix(suffix) == canonical


def test_strict_word_namespace_and_unknown_namespace():
    data = word('<w:p><w:r><w:t>STRICT WORD</w:t></w:r></w:p>', 'http://purl.oclc.org/ooxml/wordprocessingml/main')
    result = index_content.extract_index(data, 'sample.docx')
    assert result['state'] == 'ready' and text(result) == 'STRICT WORD'
    assert index_content.extract_index(word('<w:p><w:r><w:t>wrong</w:t></w:r></w:p>', 'urn:unrelated'), 'sample.docx')['state'] == 'failed'


def test_word_drawing_text_and_nested_textbox_are_not_lost_or_duplicated():
    body = '<w:p><w:r><w:drawing><a:p><a:r><a:t>DRAWING WORDS</a:t></a:r></a:p><w:txbxContent><w:p><w:r><w:t>BOX WORDS</w:t></w:r></w:p></w:txbxContent></w:drawing></w:r></w:p>'
    data = word(body)
    result = index_content.extract_index(data, 'sample.docx')
    assert text(result).count('DRAWING WORDS') == text(result).count('BOX WORDS') == 1
    old = ai_text.extract_text(Path('/tmp/not-read'), 'sample.docx', data=data)
    assert old['text'].count('BOX WORDS') == old['text'].count('DRAWING WORDS') == 1
    assert 'visual_not_parsed' in reasons(result)


@pytest.mark.parametrize('suffix', ['.docm', '.dotx', '.dotm'])
def test_macro_and_template_word_read_only_body(suffix):
    result = index_content.extract_index(word('<w:p><w:r><w:t>SAFE BODY</w:t></w:r></w:p>', extra={'word/vbaProject.bin': b'NEVER EXECUTE'}), 'sample' + suffix)
    assert text(result) == 'SAFE BODY' and result['state'] == 'partial'
    assert 'embedded_content_not_parsed' in reasons(result)


def test_pptx_order_table_coordinates_and_legacy_access():
    data = presentation()
    result = index_content.extract_index(data, 'sample.pptx')
    assert result['coverage'] == {'total': 2, 'processed': 2, 'failed': 0, 'unit': 'pages'}
    assert [u['location']['page'] for u in result['units']] == [1, 2]
    assert result['units'][0]['text'] == 'FIRST IN PRESENTATION'
    assert result['units'][1]['kind'] == 'table'
    assert result['units'][1]['location']['row'] == 1
    assert all('幻灯片' in u['label'] for u in result['units'])
    assert 'TABLE CELL' in ai_text.extract_text(Path('/tmp/not-read'), 'sample.potx', data=data)['text']


@pytest.mark.parametrize('target', ['../private.xml', 'https://example.invalid/slide.xml', '/etc/passwd'])
def test_pptx_external_or_escaping_slide_is_rejected(target):
    result = index_content.extract_index(presentation(target), 'sample.pptx')
    assert result['state'] == 'failed' and not result['units']


@pytest.mark.parametrize('suffix,delimiter', [('.csv', ','), ('.tsv', '\t')])
def test_delimited_text_keeps_cells_and_quoted_newlines(suffix, delimiter):
    data = f'name{delimiter}value\r\n"line1\nline2"{delimiter}"=SUM(1)"\r\n'.encode('utf-16')
    result = index_content.extract_index(data, 'sample' + suffix)
    assert result['state'] == 'ready' and result['coverage']['unit'] == 'cells'
    assert result['coverage']['total'] == 4
    assert result['units'][2]['text'] == 'line1\nline2'
    assert result['units'][2]['location']['cell'] == 'A2'
    assert result['units'][3]['text'] == '=SUM(1)'


def test_html_keeps_visible_text_but_never_script_style_or_remote_content(monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *args, **kwargs: pytest.fail('network access'))
    result = index_content.extract_index(b'<html><head><title>Title</title><style>SECRET CSS</style></head><body><h1>Visible &amp; clear</h1><script>SECRET JS</script><p>Tail</p><img src="https://example.invalid/image"><iframe src="file:///private/secret"></iframe></body></html>', 'sample.html')
    assert 'Visible & clear' in text(result) and 'Tail' in text(result)
    assert 'SECRET' not in text(result) and '<h1>' not in text(result)
    assert {'visual_not_parsed', 'embedded_content_not_parsed'} <= reasons(result)


def test_odt_body_tables_whitespace_and_visual_gaps():
    body = '<office:text><text:p>First<text:s text:c="3"/>word<text:tab/>tail<text:line-break/>end</text:p><table:table><table:table-row><table:table-cell><text:p>Table word</text:p></table:table-cell></table:table-row></table:table><draw:frame><draw:image xlink:href="Pictures/a.png"/></draw:frame></office:text>'
    result = index_content.extract_index(odf(body, 'text', {'Pictures/a.png': b'not decoded'}), 'sample.odt')
    assert result['coverage']['unit'] == 'blocks'
    assert 'First   word\ttail\nend' in text(result)
    table = next(u for u in result['units'] if u['kind'] == 'table')
    assert table['location']['row'] == table['location']['column'] == 1
    assert 'visual_not_parsed' in reasons(result)


def test_ods_repeated_coordinates_cached_formula_and_missing_cache():
    body = '<office:spreadsheet><table:table table:name="Sheet"><table:table-row table:number-rows-repeated="2"><table:table-cell table:number-columns-repeated="2" office:value-type="string"><text:p>Repeated</text:p></table:table-cell><table:table-cell table:formula="of:=1+2" office:value-type="float" office:value="3"/><table:table-cell table:formula="of:=WEBSERVICE(&quot;https://private&quot;)"/></table:table-row></table:table></office:spreadsheet>'
    result = index_content.extract_index(odf(body, 'spreadsheet'), 'sample.ods')
    assert [u['location']['cell'] for u in result['units']] == ['A1', 'B1', 'C1', 'A2', 'B2', 'C2']
    assert result['coverage'] == {'total': 8, 'processed': 6, 'failed': 2, 'unit': 'cells'}
    assert 'formula_cache_missing' in reasons(result)
    assert 'WEBSERVICE' not in text(result) and '3' in text(result)


@pytest.mark.parametrize('repeat', ['0', '-1', '1000000000', 'not-an-int'])
def test_ods_repetition_is_checked_before_expansion(repeat):
    body = f'<office:spreadsheet><table:table table:name="Sheet"><table:table-row table:number-rows-repeated="{repeat}"><table:table-cell><text:p>value</text:p></table:table-cell></table:table-row></table:table></office:spreadsheet>'
    result = index_content.extract_index(odf(body, 'spreadsheet'), 'sample.ods')
    assert result['state'] in {'partial', 'failed'} and not result['units']
    assert reasons(result) & {'resource_limit', 'invalid_document'}


def test_odp_slide_order_and_image_gap():
    data = odf('<office:presentation><draw:page><draw:frame><draw:text-box><text:p>First slide</text:p></draw:text-box></draw:frame></draw:page><draw:page><draw:frame><draw:text-box><text:p>Last slide</text:p></draw:text-box><draw:image xlink:href="https://example.invalid/remote"/></draw:frame></draw:page></office:presentation>', 'presentation')
    result = index_content.extract_index(data, 'sample.odp')
    assert result['coverage'] == {'total': 2, 'processed': 2, 'failed': 0, 'unit': 'pages'}
    assert [u['location']['page'] for u in result['units']] == [1, 2]
    assert 'visual_not_parsed' in reasons(result)


def test_new_package_formats_keep_archive_and_xml_security():
    for extra in [{'../private': 'secret'}, {'content.xml': '<!DOCTYPE x [<!ENTITY e SYSTEM "file:///private/secret">]><x>&e;</x>'}]:
        result = index_content.extract_index(odf('<office:text><text:p>safe</text:p></office:text>', 'text', extra), 'sample.odt')
        assert result['state'] == 'failed' and not result['units']


def test_new_formats_legacy_character_and_input_limits():
    result = ai_text.extract_text(Path('/tmp/not-read'), 'sample.pptx', data=presentation())
    assert result['text'] and len(result['text']) <= 12000
    result = ai_text.extract_text(Path('/tmp/not-read'), 'sample.odt', data=odf('<office:text><text:p>' + 'x' * 12001 + '</text:p></office:text>', 'text'))
    assert len(result['text']) == 12000 and result['truncated']
    with pytest.raises(ai_text.AITextError, match='10 MiB'):
        ai_text.extract_text(Path('/tmp/not-read'), 'sample.pptx', data=b'x' * (10 * 1024**2 + 1))


def test_html_deep_nesting_is_bounded():
    result = index_content.extract_index(('<div>' * 1000 + 'body' + '</div>' * 1000).encode(), 'sample.html')
    assert result['state'] in {'partial', 'failed'}
    assert 'resource_limit' in reasons(result)


def test_odt_repeated_table_rows_and_columns_keep_real_coordinates():
    body = '<office:text><table:table><table:table-row table:number-rows-repeated="2"><table:table-cell table:number-columns-repeated="2"><text:p>repeat</text:p></table:table-cell></table:table-row></table:table></office:text>'
    result = index_content.extract_index(odf(body, 'text'), 'sample.odt')
    assert [(u['location']['row'], u['location']['column']) for u in result['units']] == [(1, 1), (1, 2), (2, 1), (2, 2)]


def test_odp_speaker_notes_are_gaps_and_not_body():
    body = '<office:presentation><draw:page><text:p>Slide body</text:p><presentation:notes xmlns:presentation="urn:oasis:names:tc:opendocument:xmlns:presentation:1.0"><text:p>SECRET SPEAKER NOTES</text:p></presentation:notes></draw:page></office:presentation>'
    result = index_content.extract_index(odf(body, 'presentation'), 'sample.odp')
    assert text(result) == 'Slide body' and 'notes_not_parsed' in reasons(result)


def test_strict_xlsx_and_pptx_use_known_namespace_mapping():
    from backend.tests.test_index_content import xlsx
    spreadsheet = xlsx('<row r="1"><c r="A1" t="inlineStr"><is><t>Strict cell</t></is></c></row>')
    def strict(data):
        out = {}
        with zipfile.ZipFile(BytesIO(data)) as archive:
            for name in archive.namelist():
                value = archive.read(name).decode()
                for family in ('spreadsheetml', 'presentationml', 'drawingml'):
                    value = value.replace(f'http://schemas.openxmlformats.org/{family}/2006/main', f'http://purl.oclc.org/ooxml/{family}/main')
                value = value.replace(R, 'http://purl.oclc.org/ooxml/officeDocument/relationships')
                out[name] = value
        return package(out)
    assert text(index_content.extract_index(strict(spreadsheet), 'sample.xlsx')) == 'Strict cell'
    assert 'TABLE CELL' in text(index_content.extract_index(strict(presentation()), 'sample.pptx'))


def test_word_nested_drawing_paragraphs_preserve_text_boundaries():
    data = word('<w:p><w:r><w:drawing><a:p><a:r><a:t>First line</a:t></a:r></a:p><a:p><a:r><a:t>Second line</a:t></a:r></a:p></w:drawing></w:r></w:p>')
    assert text(index_content.extract_index(data, 'sample.docx')) == 'First line\nSecond line'


def test_word_zero_units_distinguishes_visual_chunk_and_invalid_container():
    image = index_content.extract_index(word('<w:p><w:r><w:drawing/></w:r></w:p>', extra={'word/media/image.png': b'not decoded by text parser'}), 'sample.docx')
    chunk = index_content.extract_index(word('<w:altChunk/>', extra={'word/chunk.html': '<p>Not opened</p>'}), 'sample.docx')
    ole = index_content.extract_index(bytes.fromhex('d0cf11e0a1b11ae1') + b'synthetic legacy container', 'sample.docx')
    assert not image['units'] and image['state'] == 'partial' and 'visual_not_parsed' in reasons(image)
    assert not chunk['units'] and chunk['state'] == 'partial' and 'embedded_content_not_parsed' in reasons(chunk)
    assert not ole['units'] and ole['state'] == 'failed' and reasons(ole) == {'invalid_document'}


def test_ods_full_height_empty_tail_keeps_real_first_cell():
    body = '<office:spreadsheet><table:table table:name="Sheet"><table:table-row><table:table-cell office:value-type="string"><text:p>Retain me</text:p></table:table-cell></table:table-row><table:table-row table:number-rows-repeated="1048575"><table:table-cell/></table:table-row></table:table></office:spreadsheet>'
    result = index_content.extract_index(odf(body, 'spreadsheet'), 'sample.ods')
    assert result['state'] == 'ready' and text(result) == 'Retain me'
    assert result['units'][0]['location']['cell'] == 'A1'
    assert result['coverage'] == {'total': 1048576, 'processed': 1048576, 'failed': 0, 'unit': 'cells'}


def test_ods_empty_repeats_advance_real_rows_and_columns():
    body = '<office:spreadsheet><table:table table:name="Sheet"><table:table-row table:number-rows-repeated="100000"><table:table-cell table:number-columns-repeated="16384"/></table:table-row><table:table-row><table:table-cell table:number-columns-repeated="16383"/><table:table-cell><text:p>Last column</text:p></table:table-cell></table:table-row></table:table></office:spreadsheet>'
    result = index_content.extract_index(odf(body, 'spreadsheet'), 'sample.ods')
    assert result['state'] == 'ready' and text(result) == 'Last column'
    assert result['units'][0]['location']['cell'] == 'XFD100001'
    assert result['coverage']['total'] == result['coverage']['processed'] == 100001 * 16384


def test_ods_nonempty_repeat_limit_retains_preceding_data():
    body = '<office:spreadsheet><table:table table:name="Sheet"><table:table-row><table:table-cell><text:p>Prefix retained</text:p></table:table-cell></table:table-row><table:table-row table:number-rows-repeated="100000"><table:table-cell><text:p>Repeat beyond budget</text:p></table:table-cell></table:table-row></table:table></office:spreadsheet>'
    result = index_content.extract_index(odf(body, 'spreadsheet'), 'sample.ods')
    assert result['state'] == 'partial' and text(result) == 'Prefix retained'
    assert 'resource_limit' in reasons(result)
    assert result['coverage'] == {'total': 100001, 'processed': 1, 'failed': 100000, 'unit': 'cells'}


def test_ods_image_only_repeated_cell_is_not_classified_as_empty():
    body = '<office:spreadsheet><table:table table:name="Sheet"><table:table-row><table:table-cell><text:p>Prefix</text:p></table:table-cell></table:table-row><table:table-row table:number-rows-repeated="100000"><table:table-cell><draw:frame><draw:image xlink:href="Pictures/image.png"/></draw:frame></table:table-cell></table:table-row></table:table></office:spreadsheet>'
    result = index_content.extract_index(odf(body, 'spreadsheet'), 'sample.ods')
    assert text(result) == 'Prefix' and result['state'] == 'partial'
    assert {'visual_not_parsed', 'resource_limit'} <= reasons(result)
    assert result['coverage']['failed'] == 100000


def test_odt_large_empty_table_tail_does_not_discard_body():
    body = '<office:text><text:p>Retain paragraph</text:p><table:table><table:table-row table:number-rows-repeated="100000"><table:table-cell><text:p/></table:table-cell></table:table-row></table:table><text:p>After blank region</text:p></office:text>'
    result = index_content.extract_index(odf(body, 'text'), 'sample.odt')
    assert result['state'] == 'ready' and text(result) == 'Retain paragraph\nAfter blank region'
    assert result['units'][1]['location']['paragraph'] == 100002


def test_ods_repeated_million_spaces_are_counted_without_expanding(monkeypatch):
    from backend.app.services import index_formats
    original, calls = index_formats._odf_text, []
    def record(element):
        calls.append(element)
        return original(element)
    monkeypatch.setattr(index_formats, '_odf_text', record)
    body = '<office:spreadsheet><table:table table:name="Sheet"><table:table-row><table:table-cell><text:p>Prefix</text:p></table:table-cell></table:table-row><table:table-row table:number-rows-repeated="1000"><table:table-cell><text:p><text:s text:c="1000000"/></text:p></table:table-cell></table:table-row></table:table></office:spreadsheet>'
    result = index_content.extract_index(odf(body, 'spreadsheet'), 'sample.ods')
    assert result['state'] == 'ready' and text(result) == 'Prefix'
    assert result['coverage']['processed'] == 1001 and len(calls) == 1


def test_ods_cached_whitespace_does_not_repeat_index_add(monkeypatch):
    original, calls = index_content._Index.add, []
    def record(self, value, *args):
        calls.append(len(value))
        return original(self, value, *args)
    monkeypatch.setattr(index_content._Index, 'add', record)
    body = '<office:spreadsheet><table:table table:name="Sheet"><table:table-row><table:table-cell><text:p>Prefix</text:p></table:table-cell></table:table-row><table:table-row table:number-rows-repeated="1000"><table:table-cell office:string-value=" "><text:p><text:s text:c="1000"/></text:p></table:table-cell></table:table-row></table:table></office:spreadsheet>'
    result = index_content.extract_index(odf(body, 'spreadsheet'), 'sample.ods')
    assert result['state'] == 'ready' and text(result) == 'Prefix'
    assert result['coverage']['processed'] == 1001 and calls == [6]


def test_ods_repeated_nonempty_text_budget_keeps_prior_cells():
    body = '<office:spreadsheet><table:table table:name="Sheet"><table:table-row><table:table-cell><text:p>Prefix</text:p></table:table-cell></table:table-row><table:table-row table:number-rows-repeated="1000"><table:table-cell><text:p>' + 'x' * 2000 + '</text:p></table:table-cell></table:table-row></table:table></office:spreadsheet>'
    result = index_content.extract_index(odf(body, 'spreadsheet'), 'sample.ods')
    assert result['state'] == 'partial' and text(result) == 'Prefix'
    assert 'resource_limit' in reasons(result)
    assert result['coverage'] == {'total': 1001, 'processed': 1, 'failed': 1000, 'unit': 'cells'}


def test_odt_shared_body_paragraphs_skip_notes_and_match_text_locations():
    from backend.app.services import index_formats
    body = '<office:text><text:p>First<office:annotation><text:p>COMMENT</text:p></office:annotation><text:note><text:p>FOOTNOTE</text:p></text:note></text:p><text:p>Second<draw:image xlink:href="Pictures/image.png"/></text:p><table:table><table:table-row table:number-rows-repeated="2"><table:table-cell><text:p>Cell</text:p></table:table-cell></table:table-row></table:table></office:text>'
    data = odf(body, 'text')
    with zipfile.ZipFile(BytesIO(data)) as archive:
        root = ai_text._xml(archive, ai_text._members(archive), 'content.xml')
    content = root.find('{' + OFFICE + '}body/{' + OFFICE + '}text')
    paragraphs = list(index_formats.iter_odf_body_paragraphs(content))
    result = index_content.extract_index(data, 'sample.odt')
    assert [loc for _, loc in paragraphs] == [u['location'] for u in result['units']]
    assert [loc['paragraph'] for _, loc in paragraphs] == [1, 2, 3, 4]
    assert paragraphs[2][1]['row'] == 1 and paragraphs[3][1]['row'] == 2
    assert next(loc['paragraph'] for node, loc in paragraphs if any(child.tag == '{' + DRAW + '}image' for child in node.iter())) == 2
