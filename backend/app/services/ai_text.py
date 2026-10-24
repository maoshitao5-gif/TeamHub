"""Bounded text extraction for an explicitly selected personal document.

No archive extraction, formula execution, linked-resource fetching, or OCR occurs.
"""
from io import BytesIO
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile
import zlib
from xml.etree import ElementTree

from pypdf import PdfReader, apply_configuration
from pypdf.errors import PyPdfError

MAX_INPUT_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 12_000
MAX_XML_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 2000
MAX_PDF_PAGES = 20
MAX_SHEETS, MAX_ROWS, MAX_COLUMNS = 10, 200, 50
_TEXT_SUFFIXES = {'.txt', '.md', '.csv', '.tsv', '.json', '.yaml', '.yml', '.log', '.ini',
                  '.cfg', '.conf', '.toml', '.xml', '.html', '.htm', '.sql', '.properties'}
_W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
_S = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
_R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
_REL = '{http://schemas.openxmlformats.org/package/2006/relationships}'
_PARTIAL_NOTE = '仅提取部分内容，AI 建议可能不涵盖整份资料。'
_OFFICE_ALIASES = {
    '.docm': '.docx', '.dotx': '.docx', '.dotm': '.docx',
    '.xlsm': '.xlsx', '.xltx': '.xlsx', '.xltm': '.xlsx',
    '.pptm': '.pptx', '.potx': '.pptx', '.potm': '.pptx',
    '.ppsx': '.pptx', '.ppsm': '.pptx',
    '.ott': '.odt', '.ots': '.ods', '.otp': '.odp',
}
_STRICT_NAMESPACES = {
    'http://purl.oclc.org/ooxml/wordprocessingml/main': _W[1:-1],
    'http://purl.oclc.org/ooxml/spreadsheetml/main': _S[1:-1],
    'http://purl.oclc.org/ooxml/officeDocument/relationships': _R[1:-1],
    'http://purl.oclc.org/ooxml/presentationml/main': 'http://schemas.openxmlformats.org/presentationml/2006/main',
    'http://purl.oclc.org/ooxml/drawingml/main': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'http://purl.oclc.org/ooxml/drawingml/spreadsheetDrawing': 'http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing',
    'http://purl.oclc.org/ooxml/package/relationships': _REL[1:-1],
    'http://purl.oclc.org/ooxml/officeDocument/math': 'http://schemas.openxmlformats.org/officeDocument/2006/math',
}


def canonical_suffix(suffix: str) -> str:
    """Normalize supported read-only Office aliases without guessing legacy formats."""
    suffix = suffix.lower()
    return _OFFICE_ALIASES.get(suffix, suffix)


class AITextError(ValueError):
    """Only fixed user-facing messages may escape parsers."""


class _BudgetDone(Exception):
    pass


class _TextBudget:
    def __init__(self):
        self.parts = []
        self.used = 0
        self.truncated = False

    def add(self, text, separator='\n'):
        if not text:
            return
        value = (separator if self.parts else '') + text
        remaining = MAX_TEXT_CHARS - self.used
        self.parts.append(value[:remaining])
        self.used += min(remaining, len(value))
        self.truncated |= len(value) > remaining

    def result(self, notes):
        text = ''.join(self.parts)
        if not text.strip():
            raise AITextError('文件中没有可提取的文字，请选择包含正文的资料。')
        return {'text': text, 'truncated': self.truncated,
                'notes': notes + ([_PARTIAL_NOTE] if self.truncated else [])}


def _decode_text(data):
    if data.startswith((b'PK\x03\x04', b'%PDF-', b'\x89PNG', b'\xff\xd8\xff',
                        b'GIF8', b'\xd0\xcf\x11\xe0', b'\x1f\x8b')):
        raise AITextError('文件内容不是可安全读取的普通文本。')
    encodings = ('utf-16',) if data.startswith((b'\xff\xfe', b'\xfe\xff')) else ('utf-8-sig', 'gb18030')
    for encoding in encodings:
        try:
            text = data.decode(encoding)
        except UnicodeError:
            continue
        if any((ord(char) < 32 and char not in '\t\n\r\f') or ord(char) == 127 for char in text):
            break
        return text
    raise AITextError('文件内容不是可安全读取的普通文本，或文字编码暂不支持。')


def _plain_text(data):
    budget = _TextBudget()
    budget.add(_decode_text(data), separator='')
    return budget.result([])


def _members(archive):
    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_ENTRIES:
        raise AITextError('Office 文件归档内容过多，请选择较小的文件。')
    members, folded, total = {}, set(), 0
    for info in infos:
        name = info.filename
        parts = name.rstrip('/').split('/')
        mode = (info.external_attr >> 16) & 0o170000
        if (not name or name.startswith('/') or '\\' in name or ':' in name
                or any(part in {'', '.', '..'} for part in parts)
                or name.casefold() in folded or info.flag_bits & 1
                or mode not in {0, stat.S_IFREG, stat.S_IFDIR}
                or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}):
            raise AITextError('Office 文件包含不安全或不支持的归档结构。')
        total += info.file_size
        if total > MAX_ARCHIVE_BYTES:
            raise AITextError('Office 文件展开内容过大，请选择较小的文件。')
        if info.file_size > 1024 * 1024 and info.file_size > max(1, info.compress_size) * 200:
            raise AITextError('Office 文件归档压缩比例异常。')
        folded.add(name.casefold())
        members[name] = info
    return members


def _xml(archive, members, name, optional=False):
    info = members.get(name)
    if info is None:
        if optional:
            return None
        raise AITextError('Office 文件缺少必要的正文内容。')
    if info.file_size > MAX_XML_BYTES:
        raise AITextError('Office 文件单项展开内容过大，请选择较小的文件。')
    with archive.open(info) as source:
        data = source.read(MAX_XML_BYTES + 1)
    if len(data) > MAX_XML_BYTES:
        raise AITextError('Office 文件单项展开内容过大，请选择较小的文件。')
    try:
        decoded = data.decode('utf-8-sig')
        if '\x00' in decoded or re.search(r'<!\s*(?:DOCTYPE|ENTITY)\b', decoded, re.I):
            raise AITextError('Office 文件包含不安全的 XML 声明。')
        root = ElementTree.fromstring(decoded)
        def normalize(name):
            if name.startswith('{'):
                namespace, local = name[1:].split('}', 1)
                return '{' + _STRICT_NAMESPACES.get(namespace, namespace) + '}' + local
            return name
        for node in root.iter():
            node.tag = normalize(node.tag)
            node.attrib = {normalize(key): value for key, value in node.attrib.items()}
        return root
    except (UnicodeError, ElementTree.ParseError) as exc:
        raise AITextError('Office 文件正文损坏或文字编码暂不支持。') from exc


def _docx(archive, members):
    # The index walks each outer paragraph once, including nested text boxes.
    from . import index_content
    result = index_content._docx(archive, members, index_content._Index())
    return _indexed_text(result, '.docx')


def _indexed_text(result, suffix):
    """Project the bounded index into the legacy preparation text budget."""
    if not result['units']:
        raise AITextError('文件中没有可提取的文字，或文件内容损坏、格式暂不支持。')
    budget = _TextBudget()
    seen_sheets = set()
    for unit in result['units']:
        location = unit['location']
        if suffix == '.docx' and location.get('paragraph', 0) > 1000:
            budget.truncated = True
            break
        if result['coverage']['unit'] == 'cells':
            sheet = location['sheet_index']
            if sheet > MAX_SHEETS or location['row'] > MAX_ROWS or location['column'] > MAX_COLUMNS:
                budget.truncated = True
                continue
            if sheet not in seen_sheets and suffix not in {'.csv', '.tsv'}:
                seen_sheets.add(sheet)
                budget.add('工作表：' + location['sheet'])
        if budget.used >= MAX_TEXT_CHARS:
            budget.truncated = True
            break
        budget.add(unit['text'])
    limited = {'resource_limit', 'page_limit', 'unit_limit', 'text_limit'}
    budget.truncated |= any(gap['reason'] in limited for gap in result['gaps'])
    return budget.result(list(result['notes']) or ['已提取正文文字；未执行宏、公式或访问外部链接。'])


def _worksheet_targets(relationships):
    if relationships.tag != _REL + 'Relationships':
        raise AITextError('Excel 工作表引用结构无效。')
    targets = {}
    for rel in relationships:
        if not (rel.get('Type') or '').endswith('/worksheet'):
            continue
        target = rel.get('Target', '')
        name = target[1:] if target.startswith('/xl/') else 'xl/' + target
        relation_id = rel.get('Id')
        if (rel.tag != _REL + 'Relationship' or rel.get('TargetMode') == 'External'
                or '\\' in target or ':' in target or '..' in PurePosixPath(target).parts
                or not re.fullmatch(r'xl/worksheets/[^/]+\.xml', name)
                or not relation_id or relation_id in targets):
            raise AITextError('Excel 工作表引用不安全或无效。')
        targets[relation_id] = name
    return targets


def _cell_value(cell, shared):
    value = cell.find(_S + 'v')
    raw = (value.text or '') if value is not None else ''
    if cell.get('t') == 'inlineStr':
        return ''.join(node.text or '' for node in cell.iter(_S + 't'))
    if cell.get('t') == 's':
        try:
            index = int(raw)
            if index < 0:
                raise ValueError
            return shared[index]
        except (ValueError, IndexError) as exc:
            raise AITextError('Excel 共享文本引用无效。') from exc
    # Cached values are data only. Formula expressions are never exposed/evaluated.
    return raw


def _xlsx(archive, members):
    workbook = _xml(archive, members, 'xl/workbook.xml')
    if workbook.tag != _S + 'workbook':
        raise AITextError('Excel 工作簿结构无效。')
    targets = _worksheet_targets(_xml(archive, members, 'xl/_rels/workbook.xml.rels'))
    strings = _xml(archive, members, 'xl/sharedStrings.xml', optional=True)
    if strings is not None and strings.tag != _S + 'sst':
        raise AITextError('Excel 共享文本结构无效。')
    shared = [] if strings is None else [''.join(node.text or '' for node in item.iter(_S + 't'))
                                        for item in strings.findall(_S + 'si')]
    if len(shared) > 20_000:
        raise AITextError('Excel 共享文本过多，请选择较小的文件。')
    sheets = workbook.findall(_S + 'sheets/' + _S + 'sheet')
    budget = _TextBudget()
    budget.truncated = len(sheets) > MAX_SHEETS
    for sheet in sheets[:MAX_SHEETS]:
        if budget.used >= MAX_TEXT_CHARS:
            budget.truncated = True
            break
        target = targets.get(sheet.get(_R + 'id'))
        if not target:
            raise AITextError('Excel 工作表引用无效或属于外部内容。')
        root = _xml(archive, members, target)
        if root.tag != _S + 'worksheet':
            raise AITextError('Excel 工作表结构无效。')
        heading, previous_row = False, 0
        rows = root.findall(_S + 'sheetData/' + _S + 'row')
        for ordinal, row in enumerate(rows, 1):
            try:
                row_number = int(row.get('r', str(ordinal)))
            except ValueError as exc:
                raise AITextError('Excel 行位置无效。') from exc
            if row_number <= previous_row:
                raise AITextError('Excel 行位置无效。')
            previous_row = row_number
            if ordinal > MAX_ROWS or row_number > MAX_ROWS or budget.used >= MAX_TEXT_CHARS:
                budget.truncated = True
                break
            values, seen = [], set()
            for ordinal_column, cell in enumerate(row.findall(_S + 'c')):
                reference = cell.get('r')
                column = ordinal_column
                if reference:
                    match = re.fullmatch(r'([A-Z]{1,3})([1-9][0-9]{0,6})', reference)
                    if match is None or int(match[2]) != row_number:
                        raise AITextError('Excel 单元格位置无效。')
                    column = 0
                    for char in match[1]:
                        column = column * 26 + ord(char) - ord('A') + 1
                    column -= 1
                if column in seen:
                    raise AITextError('Excel 包含重复的单元格位置。')
                seen.add(column)
                if column >= MAX_COLUMNS or ordinal_column >= MAX_COLUMNS:
                    budget.truncated = True
                    continue
                value = _cell_value(cell, shared)
                if value:
                    values.append(value)
            if values:
                if not heading:
                    budget.add('工作表：' + (sheet.get('name') or '未命名')[:100])
                    heading = True
                budget.add('\t'.join(values))
    return budget.result(['已读取有限工作表的单元格文字和缓存值；未执行公式、宏或访问链接。'])


def _pdf(data):
    if not data.startswith(b'%PDF-'):
        raise AITextError('文件内容不是有效的 PDF。')
    budget = _TextBudget()
    # Context-local public pypdf limits also bound compressed object streams and forms.
    with apply_configuration(maximum_declared_stream_length=MAX_INPUT_BYTES,
                             array_based_stream_maximum_output_length=MAX_XML_BYTES,
                             zlib_maximum_output_length=MAX_XML_BYTES,
                             zlib_maximum_recovery_input_length=100_000,
                             lzw_maximum_output_length=MAX_XML_BYTES,
                             run_length_maximum_output_length=MAX_XML_BYTES,
                             stream_filters_maximum_length=4,
                             stream_decoding_work_maximum_length=8 * MAX_XML_BYTES,
                             page_tree_maximum_entries=500,
                             page_tree_maximum_depth=20,
                             xform_maximum_invocations_per_extraction=100,
                             jbig2dec_binary=None):
        reader = PdfReader(BytesIO(data), strict=True, root_object_recovery_limit=100)
        if reader.is_encrypted:
            raise AITextError('加密 PDF 暂不支持 AI 整理，请选择未加密的资料。')
        pages = len(reader.pages)
        budget.truncated = pages > MAX_PDF_PAGES

        def visit(text, _cm, _tm, _font, _size):
            if text:
                budget.add(text, separator='')
                if budget.used >= MAX_TEXT_CHARS:
                    budget.truncated = True
                    raise _BudgetDone

        for page in reader.pages[:MAX_PDF_PAGES]:
            try:
                # Only text operators are interpreted; images and links are not opened.
                page.extract_text(visitor_text=visit)
            except _BudgetDone:
                break
    if not ''.join(budget.parts).strip():
        raise AITextError('PDF 没有可提取的文字层，扫描件暂不支持 AI 整理。')
    return budget.result(['仅提取 PDF 文字层，未识别扫描图片或访问链接。'])


def extract_text(path: Path, filename: str, *, data: bytes | None = None) -> dict:
    """Extract one bounded byte snapshot; callers may supply the bytes they hashed."""
    suffix = canonical_suffix(Path(filename).suffix)
    if suffix not in _TEXT_SUFFIXES | {'.docx', '.xlsx', '.pdf', '.pptx', '.odt', '.ods', '.odp'}:
        raise AITextError('此格式暂不支持 AI 整理，请选择普通文本、DOCX、XLSX 或文字型 PDF。')
    try:
        if data is None:
            if not path.is_file():
                raise AITextError('所选文件不存在或不是普通文件。')
            if path.stat().st_size > MAX_INPUT_BYTES:
                raise AITextError('文件超过 10 MiB，暂不支持 AI 整理。')
            with path.open('rb') as source:
                data = source.read(MAX_INPUT_BYTES + 1)
        if len(data) > MAX_INPUT_BYTES:
            raise AITextError('文件超过 10 MiB，暂不支持 AI 整理。')
        if suffix in _TEXT_SUFFIXES - {'.csv', '.tsv', '.html', '.htm'}:
            return _plain_text(data)
        if suffix == '.pdf':
            return _pdf(data)
        if suffix in {'.csv', '.tsv', '.html', '.htm', '.pptx', '.odt', '.ods', '.odp'}:
            from .index_content import extract_index
            return _indexed_text(extract_index(data, filename), suffix)
        with zipfile.ZipFile(BytesIO(data)) as archive:
            members = _members(archive)
            return _docx(archive, members) if suffix == '.docx' else _xlsx(archive, members)
    except AITextError:
        raise
    except OSError as exc:
        raise AITextError('文件读取失败，请确认资料仍然存在且可访问。') from exc
    except (PyPdfError, zipfile.BadZipFile, UnicodeError, ValueError, TypeError,
            KeyError, IndexError, RecursionError, RuntimeError, EOFError,
            NotImplementedError, zlib.error) as exc:
        raise AITextError('文件内容损坏、结构过于复杂或格式暂不支持，请选择其他资料。') from exc
