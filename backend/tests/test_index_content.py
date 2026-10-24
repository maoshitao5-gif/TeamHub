"""通用本地资料索引的真实文件、覆盖缺口与安全边界。"""
from copy import deepcopy
from io import BytesIO
import zipfile

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from backend.app.services import index_content as parser

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'


def archive(files):
    data = BytesIO()
    with zipfile.ZipFile(data, 'w', zipfile.ZIP_DEFLATED) as target:
        for name, value in files.items():
            target.writestr(name, value)
    return data.getvalue()


def docx(body, extras=None):
    return archive({'word/document.xml': f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>', **(extras or {})})


def xlsx(rows, extras=None, target='worksheets/sheet1.xml'):
    return archive({
        'xl/workbook.xml': f'<workbook xmlns="{S}" xmlns:r="{R}"><sheets><sheet name="设备" r:id="rId1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels': f'<Relationships xmlns="{REL}"><Relationship Id="rId1" Type="{R}/worksheet" Target="{target}"/></Relationships>',
        'xl/worksheets/sheet1.xml': f'<worksheet xmlns="{S}"><sheetData>{rows}</sheetData></worksheet>',
        **(extras or {}),
    })


def pdf(pages=1, *, text=True, picture=False, encrypted=False):
    output, writer = BytesIO(), PdfWriter()
    for index in range(pages):
        page = writer.add_blank_page(width=200, height=200)
        resources = DictionaryObject()
        page[NameObject('/Resources')] = resources
        if text:
            font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
            resources[NameObject('/Font')] = DictionaryObject({NameObject('/F1'): writer._add_object(font)})
            stream = DecodedStreamObject()
            stream.set_data(f'BT /F1 12 Tf 10 10 Td (Page {index + 1} tail content) Tj ET'.encode())
            page[NameObject('/Contents')] = writer._add_object(stream)
        if picture:
            stream = DecodedStreamObject()
            stream.set_data(b'\x00\x00\x00')
            stream.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Image'), NameObject('/Width'): NumberObject(1), NameObject('/Height'): NumberObject(1), NameObject('/BitsPerComponent'): NumberObject(8), NameObject('/ColorSpace'): NameObject('/DeviceRGB')})
            resources[NameObject('/XObject')] = DictionaryObject({NameObject('/Image1'): writer._add_object(stream)})
            contents = DecodedStreamObject()
            previous = page.get_contents()
            contents.set_data((previous.get_data() if previous is not None else b'') + b'\n/Image1 Do')
            page[NameObject('/Contents')] = writer._add_object(contents)
    if encrypted:
        writer.encrypt('secret')
    writer.write(output)
    return output.getvalue()


def text_of(result):
    return '\n'.join(unit['text'] for unit in result['units'])


def reasons(result):
    return {gap['reason'] for gap in result['gaps']}


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-8-sig', 'utf-16', 'gb18030'])
def test_text_full_tail_and_stable_locations(encoding):
    data = ('序言\n' + '正文' * 7000 + '\n末尾答案').encode(encoding)
    result = parser.extract_index(data, '材料.txt')
    assert result['state'] == 'ready'
    assert '末尾答案' in text_of(result)
    assert result['units'][-1]['location']['line'] == 3
    assert result['coverage'] == {'total': 3, 'processed': 3, 'failed': 0, 'unit': 'lines'}
    assert result == parser.extract_index(data, '材料.txt')
    assert '摘录' in result['overview']
    parser.validate_index(result)


def test_pdf_reads_beyond_old_twenty_pages():
    result = parser.extract_index(pdf(23), '资料.pdf')
    assert result['state'] == 'ready'
    assert 'Page 23 tail content' in text_of(result)
    assert result['units'][-1]['location']['page'] == 23
    assert result['coverage']['processed'] == 23


def test_docx_paragraph_table_and_tail_locations():
    body = '<w:p><w:r><w:t>开头</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>表内</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
    body += ''.join(f'<w:p><w:r><w:t>正文{i}</w:t></w:r></w:p>' for i in range(1050))
    result = parser.extract_index(docx(body), '资料.docx')
    assert result['state'] == 'ready'
    assert '正文1049' in text_of(result)
    table = next(unit for unit in result['units'] if unit['kind'] == 'table')
    assert table['location']['table'] == 1
    assert table['location']['row'] == 1
    assert table['location']['column'] == 1


def test_docx_records_unread_visual_comments_headers_and_footnotes():
    data = docx('<w:p><w:r><w:t>正文</w:t><w:drawing/></w:r></w:p>', {
        'word/media/image1.png': b'fake', 'word/comments.xml': '<comments/>',
        'word/header1.xml': '<header/>', 'word/footer1.xml': '<footer/>',
        'word/footnotes.xml': '<footnotes/>',
    })
    result = parser.extract_index(data, '资料.docx')
    assert result['state'] == 'partial'
    assert {'visual_not_parsed', 'comments_not_parsed', 'header_footer_not_parsed', 'notes_not_parsed'} <= reasons(result)


def test_xlsx_keeps_sparse_coordinates_and_reads_last_row():
    rows = '<row r="1"><c r="B1" t="inlineStr"><is><t>不是自动表头</t></is></c></row><row r="50000"><c r="ZZ50000" t="inlineStr"><is><t>末行值</t></is></c></row>'
    result = parser.extract_index(xlsx(rows), '资料.xlsx')
    assert result['state'] == 'ready'
    tail = result['units'][-1]
    assert tail['kind'] == 'table'
    assert tail['text'] == '末行值'
    assert tail['location'] == {'sheet': '设备', 'sheet_index': 1, 'row': 50000, 'column': 702, 'cell': 'ZZ50000'}
    assert result['coverage'] == {'total': 2, 'processed': 2, 'failed': 0, 'unit': 'cells'}
    assert any('表头' in note for note in result['notes'])


def test_xlsx_shared_strings_and_cached_formula_are_not_executed():
    rows = '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><f>WEBSERVICE("https://private")</f><v>42</v></c><c r="C1"><f>1+1</f></c></row>'
    result = parser.extract_index(xlsx(rows, {'xl/sharedStrings.xml': f'<sst xmlns="{S}"><si><t>共享文字</t></si></sst>'}), '资料.xlsx')
    assert '共享文字' in text_of(result) and '42' in text_of(result)
    assert 'WEBSERVICE' not in text_of(result) and 'https://' not in text_of(result)
    assert 'formula_cache_missing' in reasons(result)
    assert any('未执行' in note and '缓存' in note for note in result['notes'])


def test_xlsx_visual_chart_gap():
    data = xlsx('<row r="1"><c r="A1"><v>1</v></c></row>', {'xl/media/image.png': b'fake', 'xl/charts/chart1.xml': '<chart/>'})
    result = parser.extract_index(data, '资料.xlsx')
    assert result['state'] == 'partial'
    assert {'visual_not_parsed', 'chart_not_parsed'} <= reasons(result)


@pytest.mark.parametrize('text', [True, False])
def test_pdf_images_are_never_claimed_understood(text):
    result = parser.extract_index(pdf(text=text, picture=True), '扫描.pdf')
    assert result['state'] == 'partial'
    assert 'visual_not_parsed' in reasons(result)
    assert any(gap['location']['page'] == 1 for gap in result['gaps'])


def test_pdf_encrypted_and_blank_are_explicit():
    assert 'encrypted_pdf' in reasons(parser.extract_index(pdf(encrypted=True), '加密.pdf'))
    assert 'no_text_layer' in reasons(parser.extract_index(pdf(text=False), '空白.pdf'))


@pytest.mark.parametrize('filename,data,reason', [
    ('资料.png', b'\x89PNG', 'visual_not_parsed'),
    ('资料.doc', b'old', 'unsupported_format'),
    ('资料.txt', b' \n\t', 'empty_text'),
    ('资料.txt', b'\x00secret', 'invalid_text'),
    ('资料.pdf', b'%PDF-1.7\nprivate-body', 'invalid_document'),
    ('资料.docx', b'private-body', 'invalid_document'),
])
def test_failures_have_fixed_messages(filename, data, reason):
    result = parser.extract_index(data, filename)
    assert result['state'] in {'partial', 'failed'}
    assert reason in reasons(result)
    assert 'private-body' not in str(result)
    parser.validate_index(result)


def test_limits_are_visible_and_coverage_is_honest(monkeypatch):
    monkeypatch.setattr(parser, 'MAX_UNITS', 2)
    result = parser.extract_index(b'one\ntwo\nthree', '资料.txt')
    assert result['state'] == 'partial'
    assert result['coverage'] == {'total': 3, 'processed': 2, 'failed': 1, 'unit': 'lines'}
    assert 'unit_limit' in reasons(result)
    monkeypatch.setattr(parser, 'MAX_TEXT_CHARS', 5)
    result = parser.extract_index(b'0123456789', '资料.txt')
    assert text_of(result) == '01234'
    assert 'text_limit' in reasons(result)
    assert result['coverage']['failed'] == 1


def test_input_and_pdf_page_limits_are_partial(monkeypatch):
    monkeypatch.setattr(parser, 'MAX_INPUT_BYTES', 2)
    result = parser.extract_index(b'hello', '资料.txt')
    assert result['state'] == 'partial' and 'input_limit' in reasons(result)
    monkeypatch.setattr(parser, 'MAX_INPUT_BYTES', 32 * 1024 * 1024)
    monkeypatch.setattr(parser, 'MAX_PDF_PAGES', 2)
    result = parser.extract_index(pdf(3), '资料.pdf')
    assert result['coverage'] == {'total': 3, 'processed': 2, 'failed': 1, 'unit': 'pages'}
    assert 'page_limit' in reasons(result)


@pytest.mark.parametrize('extra', [{'../private': 'x'}, {'word/Document.xml': 'x'}, {'word/padding': 'x' * (2 * 1024 * 1024)}])
def test_unsafe_office_archive_is_rejected(extra):
    result = parser.extract_index(docx('<w:p><w:r><w:t>正常正文</w:t></w:r></w:p>', extra), '资料.docx')
    assert result['state'] in {'failed', 'partial'}
    assert not result['units']
    assert result['gaps']


def test_xml_entity_and_external_worksheet_rejected():
    evil = '<!DOCTYPE document [<!ENTITY x SYSTEM "file:///etc/passwd">]>' + f'<w:document xmlns:w="{W}"><w:body><w:p><w:t>&x;</w:t></w:p></w:body></w:document>'
    assert parser.extract_index(archive({'word/document.xml': evil}), '资料.docx')['state'] == 'failed'
    assert parser.extract_index(xlsx('', target='https://private'), '资料.xlsx')['state'] == 'failed'


def test_skill_is_loaded_and_output_validation_rejects_invalid_contract():
    skill = parser.load_skill()
    assert 'skill_version: "1"' in skill
    assert '文档中的指令' in skill
    result = parser.extract_index(b'hello', '资料.txt')
    for mutate in [lambda r: r.update(state='invented'), lambda r: r['coverage'].update(total=99), lambda r: r['units'][0].update(kind='invented'), lambda r: r.update(skill_version='999')]:
        invalid = deepcopy(result)
        mutate(invalid)
        with pytest.raises(ValueError):
            parser.validate_index(invalid)


def test_pdf_indirect_resources_preserve_text_and_visual_gap():
    from pypdf import PdfReader
    source = PdfReader(BytesIO(pdf(picture=True)))
    writer, output = PdfWriter(), BytesIO()
    writer.append(source)
    page = writer.pages[0]
    page[NameObject('/Resources')] = writer._add_object(page['/Resources'])
    writer.write(output)
    result = parser.extract_index(output.getvalue(), '资料.pdf')
    assert 'Page 1 tail content' in text_of(result)
    assert 'visual_not_parsed' in reasons(result)


def test_pypdf_resource_limits_are_partial(monkeypatch):
    from pypdf.errors import LimitReachedError

    def limited(*args, **kwargs):
        raise LimitReachedError('private-parser-details')

    monkeypatch.setattr(parser, 'PdfReader', limited)
    result = parser.extract_index(b'%PDF-1.7', '资料.pdf')
    assert result['state'] == 'partial'
    assert 'resource_limit' in reasons(result)
    assert 'private-parser-details' not in str(result)


@pytest.mark.parametrize('key,value', [('state', []), ('coverage', None), ('units', None)])
def test_validator_always_rejects_malformed_data_with_value_error(key, value):
    result = parser.extract_index(b'hello', '资料.txt')
    result[key] = value
    with pytest.raises(ValueError):
        parser.validate_index(result)


def test_missing_skill_is_a_fixed_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(parser, 'SKILL_PATH', tmp_path / 'missing.md')
    result = parser.extract_index(b'hello', '资料.txt')
    assert result['state'] == 'failed'
    assert reasons(result) == {'skill_unavailable'}
    assert str(tmp_path) not in str(result)


def test_xlsx_invalid_coordinates_and_shared_strings_are_explicit():
    for row in [
        '<row r="1"><c r="A2"><v>1</v></c></row>',
        '<row r="1"><c r="A1"><v>1</v></c><c r="A1"><v>2</v></c></row>',
        '<row r="1"><c r="A1" t="s"><v>9</v></c></row>',
    ]:
        result = parser.extract_index(xlsx(row), '资料.xlsx')
        assert result['state'] in {'partial', 'failed'}
        assert 'invalid_document' in reasons(result)


def test_long_pdf_text_limit_preserves_prefix_and_gap(monkeypatch):
    monkeypatch.setattr(parser, 'MAX_TEXT_CHARS', 8)
    result = parser.extract_index(pdf(2), '资料.pdf')
    assert text_of(result) == 'Page 1 t'
    assert reasons(result) == {'text_limit'}
    assert result['coverage']['failed'] == 2


def test_office_empty_and_missing_parts():
    for data, filename in [(archive({}), '资料.docx'), (docx('<w:p/>'), '资料.docx'), (xlsx(''), '资料.xlsx')]:
        result = parser.extract_index(data, filename)
        assert result['state'] == 'failed'
        assert reasons(result) <= {'invalid_document', 'empty_text'}


def test_xlsx_invalid_sheet_identity_does_not_amplify_output():
    for name, sheets in [('x' * 10000, 1), ('设备', 2)]:
        files = {
            'xl/workbook.xml': f'<workbook xmlns="{S}" xmlns:r="{R}"><sheets>' + f'<sheet name="{name}" r:id="rId1"/>' * sheets + '</sheets></workbook>',
            'xl/_rels/workbook.xml.rels': f'<Relationships xmlns="{REL}"><Relationship Id="rId1" Type="{R}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
            'xl/worksheets/sheet1.xml': f'<worksheet xmlns="{S}"><sheetData><row r="1"><c r="A1"><v>1</v></c></row></sheetData></worksheet>',
        }
        result = parser.extract_index(archive(files), '资料.xlsx')
        assert result['state'] == 'failed'
        assert not result['units']
        assert reasons(result) == {'invalid_document'}


def test_docx_unresolved_note_and_header_references_are_gaps():
    data = docx('<w:p><w:r><w:t>正文</w:t><w:footnoteReference w:id="1"/><w:commentReference w:id="1"/></w:r></w:p><w:sectPr><w:headerReference/></w:sectPr>')
    result = parser.extract_index(data, '资料.docx')
    assert result['state'] == 'partial'
    assert {'notes_not_parsed', 'comments_not_parsed', 'header_footer_not_parsed'} <= reasons(result)


def test_xlsx_drawing_without_raster_media_is_a_visual_gap():
    data = xlsx('<row r="1"><c r="A1"><v>1</v></c></row>', {'xl/drawings/drawing1.xml': '<shape>未解析形状文字</shape>'})
    result = parser.extract_index(data, '资料.xlsx')
    assert result['state'] == 'partial'
    assert 'visual_not_parsed' in reasons(result)


def test_docx_math_and_symbols_are_explicit_unparsed_content():
    math = 'http://schemas.openxmlformats.org/officeDocument/2006/math'
    data = docx(f'<w:p xmlns:m="{math}"><w:r><w:t>答案为</w:t><w:sym w:char="F0A3"/></w:r><m:oMath><m:r><m:t>x=42</m:t></m:r></m:oMath></w:p>')
    result = parser.extract_index(data, '资料.docx')
    assert result['state'] == 'partial'
    assert 'embedded_content_not_parsed' in reasons(result)
    assert text_of(result) == '答案为'


@pytest.mark.parametrize('location', [{'line': -1}, {'line': 1, 'char_start': 4, 'char_end': 2}, {'page': 1}])
def test_validator_rejects_invalid_or_incorrect_unit_locations(location):
    result = parser.extract_index(b'hello', '资料.txt')
    result['units'][0]['location'] = location
    with pytest.raises(ValueError):
        parser.validate_index(result)


def test_validator_rejects_ready_content_without_any_processed_sources():
    result = parser.extract_index(b'hello', '资料.txt')
    result['coverage'].update(total=0, processed=0, failed=0)
    with pytest.raises(ValueError):
        parser.validate_index(result)


def test_pdf_inline_image_inside_form_is_a_visual_gap():
    from pypdf import PdfReader
    from pypdf.generic import ArrayObject
    writer, output = PdfWriter(), BytesIO()
    writer.append(PdfReader(BytesIO(pdf())))
    page = writer.pages[0]
    form = DecodedStreamObject()
    form.set_data(b'q BI /W 1 /H 1 /BPC 8 /CS /RGB ID \x00\x00\x00 EI Q')
    form.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Form'), NameObject('/Resources'): DictionaryObject(), NameObject('/BBox'): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(1), NumberObject(1)])})
    page['/Resources'][NameObject('/XObject')] = DictionaryObject({NameObject('/Fm1'): writer._add_object(form)})
    contents = DecodedStreamObject()
    contents.set_data(page.get_contents().get_data() + b'\n/Fm1 Do')
    page[NameObject('/Contents')] = writer._add_object(contents)
    writer.write(output)
    result = parser.extract_index(output.getvalue(), '资料.pdf')
    assert result['state'] == 'partial'
    assert 'visual_not_parsed' in reasons(result)
    assert 'Page 1 tail content' in text_of(result)


def test_unparsed_cells_have_a_bounded_gap_report(monkeypatch):
    monkeypatch.setattr(parser, 'MAX_GAPS', 3, raising=False)
    rows = ''.join(f'<row r="{number}"><c r="A{number}"><f>1+1</f></c></row>' for number in range(1, 1001))
    result = parser.extract_index(xlsx(rows), '资料.xlsx')
    assert result['state'] == 'partial'
    assert len(result['gaps']) <= 3
    assert {'formula_cache_missing', 'resource_limit'} <= reasons(result)
    assert result['gaps'][-1]['location']['omitted_gaps'] == 998
    assert result['coverage'] == {'total': 1000, 'processed': 0, 'failed': 1000, 'unit': 'cells'}


@pytest.mark.parametrize('mode', ['page_vector', 'form_vector', 'drawing_words_only'])
def test_pdf_vector_drawing_gaps_follow_operations_not_text(mode):
    from pypdf import PdfReader
    from pypdf.generic import ArrayObject
    writer, output = PdfWriter(), BytesIO()
    writer.append(PdfReader(BytesIO(pdf())))
    page = writer.pages[0]
    content = page.get_contents().get_data()
    drawing = b'10 10 m 80 80 l S\n20 20 30 30 re f'
    if mode == 'page_vector':
        content += b'\n' + drawing
    elif mode == 'form_vector':
        form = DecodedStreamObject()
        form.set_data(drawing)
        form.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Form'), NameObject('/Resources'): DictionaryObject(), NameObject('/BBox'): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(100), NumberObject(100)])})
        page['/Resources'][NameObject('/XObject')] = DictionaryObject({NameObject('/Drawing'): writer._add_object(form)})
        content += b'\n/Drawing Do'
    else:
        content += b'\nBT /F1 12 Tf 10 30 Td (Instructions: BI S re f sh /Drawing Do) Tj ET'
    contents = DecodedStreamObject()
    contents.set_data(content)
    page[NameObject('/Contents')] = writer._add_object(contents)
    writer.write(output)
    result = parser.extract_index(output.getvalue(), '图纸.pdf')
    assert 'Page 1 tail content' in text_of(result)
    assert result['coverage']['processed'] == 1
    if mode == 'drawing_words_only':
        assert result['state'] == 'ready'
        assert not result['gaps']
        assert 'Instructions: BI S re f sh /Drawing Do' in text_of(result)
    else:
        assert result['state'] == 'partial'
        assert reasons(result) == {'visual_not_parsed'}
        assert result['gaps'][0]['location'] == {'page': 1}


@pytest.mark.parametrize('mode', ['drawing_then_many_ops', 'many_q_ops', 'form_many_ops', 'compact_delimiters'])
def test_pdf_complexity_is_bounded_before_operation_materialization_and_text(monkeypatch, mode):
    from pypdf import PdfReader
    from pypdf._page import PageObject
    from pypdf.generic import ArrayObject, ContentStream
    writer, output = PdfWriter(), BytesIO()
    writer.append(PdfReader(BytesIO(pdf())))
    page = writer.pages[0]
    large = b'q Q ' * 60000 if mode != 'compact_delimiters' else b'q/Q ' * 60000
    content = page.get_contents().get_data()
    if mode == 'form_many_ops':
        form = DecodedStreamObject()
        form.set_data(large)
        form.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Form'), NameObject('/Resources'): DictionaryObject(), NameObject('/BBox'): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(100), NumberObject(100)])})
        page['/Resources'][NameObject('/XObject')] = DictionaryObject({NameObject('/Large'): writer._add_object(form)})
        content += b'\n10 10 m 80 80 l S\n/Large Do'
    else:
        content += b'\n' + (b'10 10 m 80 80 l S\n' if mode == 'drawing_then_many_ops' else b'') + large
    contents = DecodedStreamObject()
    contents.set_data(content)
    page[NameObject('/Contents')] = writer._add_object(contents)
    writer.write(output)
    original = ContentStream.operations
    large_materializations, text_extractions = [], []

    def guarded_operations(stream):
        if len(stream.get_data()) > 10000:
            large_materializations.append(True)
            raise AssertionError('复杂度上限必须在全量物化操作前检查')
        return original.fget(stream)

    def forbidden_text(*args, **kwargs):
        text_extractions.append(True)
        raise AssertionError('超限页不得继续提取全文')

    monkeypatch.setattr(ContentStream, 'operations', property(guarded_operations, original.fset, original.fdel))
    monkeypatch.setattr(PageObject, 'extract_text', forbidden_text)
    result = parser.extract_index(output.getvalue(), '复杂图纸.pdf')
    assert result['state'] == 'partial'
    assert 'resource_limit' in reasons(result)
    assert not large_materializations
    assert not text_extractions
