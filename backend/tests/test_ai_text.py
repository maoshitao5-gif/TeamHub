"""Bounded, read-only extraction of selected personal documents for AI requests."""
from io import BytesIO
from pathlib import Path
import zipfile

import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from backend.app.services.ai_text import AITextError, extract_text

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'


def archive(tmp_path, files, filename='sample.docx'):
    path = tmp_path / filename
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as target:
        for name, value in files.items():
            target.writestr(name, value)
    return path


def docx(text):
    return f'<w:document xmlns:w="{W}"><w:body>{text}</w:body></w:document>'


def excel(tmp_path, sheet, extra=None, target='worksheets/sheet1.xml'):
    files = {
        'xl/workbook.xml': f'<workbook xmlns="{S}" xmlns:r="{R}"><sheets><sheet name="设备" r:id="rId1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels': f'<Relationships xmlns="{REL}"><Relationship Id="rId1" Type="{R}/worksheet" Target="{target}"/></Relationships>',
        'xl/worksheets/sheet1.xml': f'<worksheet xmlns="{S}"><sheetData>{sheet}</sheetData></worksheet>',
    }
    files.update(extra or {})
    return archive(tmp_path, files, 'sample.xlsx')


def pdf(tmp_path, text='Hello TeamHub', *, pages=1, encrypted=False, compressed=False):
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=200, height=200)
        if text:
            font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
            page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
            content = DecodedStreamObject()
            content.set_data(f'BT /F1 12 Tf 10 10 Td ({text}) Tj ET'.encode())
            page[NameObject('/Contents')] = writer._add_object(content.flate_encode() if compressed else content)
    if encrypted:
        writer.encrypt('not-shared')
    path = tmp_path / 'sample.pdf'
    with path.open('wb') as output:
        writer.write(output)
    return path


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-8-sig', 'gb18030'])
def test_text_encodings_and_no_file_mutation(tmp_path, encoding):
    path = tmp_path / 'sample.txt'
    value = '变压器\n试验报告'
    path.write_bytes(value.encode(encoding))
    before = path.read_bytes()
    result = extract_text(path, 'sample.txt')
    assert result == {'text': value, 'truncated': False, 'notes': []}
    assert path.read_bytes() == before


@pytest.mark.parametrize('suffix', ['.md', '.csv', '.json', '.yaml', '.yml', '.log', '.toml'])
def test_plain_text_formats(tmp_path, suffix):
    path = tmp_path / ('file' + suffix)
    path.write_text('设备 330kV', encoding='utf-8')
    assert extract_text(path, path.name)['text'] == '设备 330kV'


@pytest.mark.parametrize('content', [b'\x00abc', b'\x01\x02\x03 binary', b'PK\x03\x04' + b'x' * 50, b'%PDF-1.7\ncontents'])
def test_binary_renamed_to_text_rejected(tmp_path, content):
    path = tmp_path / 'private-location.txt'
    path.write_bytes(content)
    with pytest.raises(AITextError, match='文本') as failure:
        extract_text(path, path.name)
    assert str(tmp_path) not in str(failure.value)


def test_empty_text_rejected(tmp_path):
    path = tmp_path / 'empty.txt'
    path.write_text(' \n\t', encoding='utf-8')
    with pytest.raises(AITextError, match='文字'):
        extract_text(path, path.name)


def test_text_output_limit_is_visible(tmp_path):
    path = tmp_path / 'long.txt'
    path.write_text('变' * 12001, encoding='utf-8')
    result = extract_text(path, path.name)
    assert len(result['text']) == 12000
    assert result['truncated'] is True
    assert any('部分' in note for note in result['notes'])


def test_oversize_rejected_before_loading(tmp_path):
    path = tmp_path / 'large.txt'
    with path.open('wb') as source:
        source.truncate(10 * 1024 * 1024 + 1)
    with pytest.raises(AITextError, match='10 MiB'):
        extract_text(path, path.name)


@pytest.mark.parametrize('filename', ['secret.png', 'old.doc', 'old.xls', 'archive.zip', 'macro.docm', 'macro.xlsm'])
def test_unsupported_formats_rejected(tmp_path, filename):
    path = tmp_path / filename
    path.write_bytes(b'not content')
    with pytest.raises(AITextError, match='暂不支持'):
        extract_text(path, filename)


def test_folder_and_missing_file_error_are_fixed(tmp_path):
    for path in [tmp_path, tmp_path / 'private-name.txt']:
        with pytest.raises(AITextError) as failure:
            extract_text(path, 'sample.txt')
        assert str(path) not in str(failure.value)


def test_docx_paragraphs_and_tables(tmp_path):
    path = archive(tmp_path, {'word/document.xml': docx('<w:p><w:r><w:t>变压器报告</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>项目A</w:t></w:r></w:p></w:tc></w:tr></w:tbl>')})
    result = extract_text(path, path.name)
    assert '变压器报告' in result['text'] and '项目A' in result['text']
    assert result['truncated'] is False
    assert result['notes']


@pytest.mark.parametrize('xml', [
    '<!DOCTYPE document [<!ENTITY x "secret">]>' + docx('<w:p><w:r><w:t>&x;</w:t></w:r></w:p>'),
    ('<!DOCTYPE document [<!ENTITY x SYSTEM "file:///etc/passwd">]>' + docx('')).encode('utf-16'),
])
def test_docx_entity_and_non_utf8_xml_rejected(tmp_path, xml):
    path = archive(tmp_path, {'word/document.xml': xml})
    with pytest.raises(AITextError):
        extract_text(path, path.name)


@pytest.mark.parametrize('name', ['../payload', '/abs', 'word/../payload', 'word\\document.xml'])
def test_unsafe_zip_members_rejected(tmp_path, name):
    path = archive(tmp_path, {'word/document.xml': docx(''), name: 'payload'})
    with pytest.raises(AITextError, match='归档'):
        extract_text(path, path.name)


def test_zip_bomb_rejected(tmp_path):
    path = archive(tmp_path, {'word/document.xml': docx(''), 'word/padding.xml': 'x' * (2 * 1024 * 1024)})
    with pytest.raises(AITextError):
        extract_text(path, path.name)


def test_excel_shared_inline_numeric_cells_and_cached_formula(tmp_path):
    path = excel(tmp_path, '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr"><is><t>项目A</t></is></c><c r="C1"><f>SUM(1,2)</f><v>3</v></c></row>', {'xl/sharedStrings.xml': f'<sst xmlns="{S}"><si><t>变压器</t></si></sst>'})
    result = extract_text(path, path.name)
    assert all(value in result['text'] for value in ['设备', '变压器', '项目A', '3'])
    assert 'SUM' not in result['text']


def test_excel_formula_without_cached_value_is_never_evaluated(tmp_path):
    path = excel(tmp_path, '<row r="1"><c r="A1"><f>WEBSERVICE("https://private")</f></c><c r="B1" t="inlineStr"><is><t>安全文字</t></is></c></row>')
    result = extract_text(path, path.name)
    assert '安全文字' in result['text']
    assert 'WEBSERVICE' not in result['text'] and 'https://' not in result['text']


@pytest.mark.parametrize('target', ['../secret.xml', 'https://example.com/file', '/etc/passwd'])
def test_excel_external_or_escaping_relationship_rejected(tmp_path, target):
    path = excel(tmp_path, '', target=target)
    with pytest.raises(AITextError):
        extract_text(path, path.name)


def test_excel_row_limit_marks_partial(tmp_path):
    path = excel(tmp_path, '<row r="1"><c t="inlineStr"><is><t>首行</t></is></c></row><row r="201"><c t="inlineStr"><is><t>末行</t></is></c></row>')
    result = extract_text(path, path.name)
    assert '首行' in result['text'] and '末行' not in result['text']
    assert result['truncated'] is True


def test_pdf_text_layer_is_extracted(tmp_path):
    path = pdf(tmp_path)
    result = extract_text(path, path.name)
    assert 'Hello TeamHub' in result['text']
    assert result['truncated'] is False


def test_pdf_page_limit_marks_partial(tmp_path):
    path = pdf(tmp_path, pages=21)
    result = extract_text(path, path.name)
    assert result['text'].count('Hello TeamHub') == 20
    assert result['truncated'] is True


@pytest.mark.parametrize('encrypted,text', [(True, 'Hello'), (False, '')])
def test_pdf_encrypted_and_scan_rejected(tmp_path, encrypted, text):
    path = pdf(tmp_path, text=text, encrypted=encrypted)
    with pytest.raises(AITextError, match='加密|文字层'):
        extract_text(path, path.name)


def test_pdf_compressed_content_bomb_rejected(tmp_path):
    path = pdf(tmp_path, text='x' * (2 * 1024 * 1024 + 1), compressed=True)
    with pytest.raises(AITextError):
        extract_text(path, path.name)


def test_malformed_parser_exception_does_not_leak(tmp_path):
    for filename, content in [('bad.pdf', b'%PDF-1.7\nprivate-body'), ('bad.docx', b'private-body')]:
        path = tmp_path / filename
        path.write_bytes(content)
        with pytest.raises(AITextError) as failure:
            extract_text(path, filename)
        assert 'private-body' not in str(failure.value)
        assert str(path) not in str(failure.value)


def test_caller_byte_snapshot_does_not_reread_path(tmp_path):
    missing = tmp_path / 'missing.txt'
    result = extract_text(missing, 'selected.txt', data='本次确认快照'.encode())
    assert result['text'] == '本次确认快照'
    assert not missing.exists()


def test_utf16_bom_text_is_readable(tmp_path):
    value = '本次设备试验\n2026'
    result = extract_text(tmp_path / 'missing.txt', 'sample.txt', data=value.encode('utf-16'))
    assert result['text'] == value


def test_supplied_snapshot_has_same_size_limit(tmp_path):
    with pytest.raises(AITextError, match='10 MiB'):
        extract_text(tmp_path / 'missing.txt', 'sample.txt', data=b'x' * (10 * 1024 * 1024 + 1))


def test_docx_output_budget_and_empty_document(tmp_path):
    empty = archive(tmp_path, {'word/document.xml': docx('<w:p/>')})
    with pytest.raises(AITextError, match='文字'):
        extract_text(empty, empty.name)
    long = archive(tmp_path, {'word/document.xml': docx('<w:p><w:r><w:t>' + 'A' * 12001 + '</w:t></w:r></w:p>')})
    result = extract_text(long, long.name)
    assert len(result['text']) == 12000 and result['truncated']


def test_archive_duplicate_and_symlink_members_rejected(tmp_path):
    for symlink in (False, True):
        path = tmp_path / 'unsafe.docx'
        with zipfile.ZipFile(path, 'w') as target:
            target.writestr('word/document.xml', docx('<w:p/>'))
            info = zipfile.ZipInfo('word/Document.xml' if not symlink else 'word/payload')
            if symlink:
                info.create_system = 3
                info.external_attr = 0o120777 << 16
            target.writestr(info, 'private')
        with pytest.raises(AITextError, match='归档'):
            extract_text(path, path.name)


def test_excel_empty_and_bad_shared_string_rejected(tmp_path):
    for row in ['', '<row r="1"><c r="A1" t="s"><v>999</v></c></row>']:
        path = excel(tmp_path, row)
        with pytest.raises(AITextError):
            extract_text(path, path.name)


def test_office_missing_xml_and_malformed_xml_are_safe(tmp_path):
    for files in [{}, {'word/document.xml': '<broken private-body>'}]:
        path = archive(tmp_path, files)
        with pytest.raises(AITextError) as failure:
            extract_text(path, path.name)
        assert 'private-body' not in str(failure.value)


def test_pdf_output_limit_is_visible(tmp_path):
    path = pdf(tmp_path, text='content ' * 2000)
    result = extract_text(path, path.name)
    assert len(result['text']) == 12000
    assert result['truncated'] is True


def test_pdf_limits_are_context_local(tmp_path):
    from pypdf import get_configuration
    before = get_configuration()
    path = pdf(tmp_path)
    extract_text(path, path.name)
    assert get_configuration() is before
