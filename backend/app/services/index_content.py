"""对固定字节快照生成可定位的原文索引；不执行文档内容或访问网络。"""
from io import BytesIO
from pathlib import Path
import re
import zipfile
import zlib

from pypdf import PdfReader, apply_configuration
from pypdf.errors import LimitReachedError, PyPdfError
from pypdf.generic import ContentStream

from .ai_text import (
    AITextError, _members, _xml, _worksheet_targets, _cell_value,
    _TEXT_SUFFIXES, _W, _S, _R, _decode_text, canonical_suffix,
)
from .image_formats import IMAGE_SUFFIXES as _IMAGE_SUFFIXES

MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_PDF_PAGES = 500
MAX_UNITS = 5000
MAX_GAPS = 1000
MAX_PDF_GRAPHICS_BYTES = 8 * 1024 * 1024
MAX_PDF_GRAPHICS_OPERATIONS = 100_000
MAX_PDF_STREAM_TOKENS = 50_000
MAX_PDF_PAGE_TOKENS = 100_000
# 保守计数：包含字符串/注释中的词及分隔符，宁可拒绝复杂内容也不先物化。
_PDF_LEXICAL_ITEMS = re.compile(rb'[^\x00\t\n\f\r ()<>\[\]{}/%]+|[()<>\[\]{}/%]')
MAX_TEXT_CHARS = 1_000_000
UNIT_TEXT_CHARS = 4000
SKILL_PATH = Path(__file__).resolve().parent.parent / 'skills/document-index/SKILL.md'
SCHEMA_VERSION = 1
SKILL_VERSION = '1'
PARSER_VERSION = '2'
_MESSAGES = {
    'input_limit': '文件超过 32 MiB，本次未读取内容。',
    'page_limit': 'PDF 超过本次页数上限，剩余页未读取。',
    'unit_limit': '内容超过本次索引单元上限，剩余内容未读取。',
    'text_limit': '文字超过本次字符上限，剩余文字未读取。',
    'resource_limit': '文件超过安全解析资源上限，部分或全部内容未读取。',
    'visual_not_parsed': '图片或视觉内容尚未识别，不能依据文字索引理解图片。',
    'chart_not_parsed': '图表尚未解析，单元格文字不能代替图表含义。',
    'comments_not_parsed': '批注尚未读取。',
    'header_footer_not_parsed': '页眉页脚尚未读取。',
    'notes_not_parsed': '脚注或尾注尚未读取。',
    'embedded_content_not_parsed': '嵌入对象或替代正文尚未读取。',
    'formula_cache_missing': '公式没有可用缓存值；未执行公式。',
    'unsupported_format': '此格式暂不支持本地索引。',
    'invalid_text': '内容不是可安全读取的普通文本，或文字编码暂不支持。',
    'invalid_document': '文件损坏、结构不安全或格式暂不支持。',
    'encrypted_pdf': '加密 PDF 暂不支持索引，请选择未加密的资料。',
    'empty_text': '文件中没有可提取的文字。',
    'no_text_layer': '此页没有可提取的文字层，尚未进行图像识别。',
    'skill_unavailable': '索引规则缺失或版本不匹配，无法生成索引。',
}


def load_skill() -> str:
    """本地解析及后续 AI 增强共用同一固定版本规则。"""
    text = SKILL_PATH.read_text(encoding='utf-8')
    versions = {'skill_version': f'"{SKILL_VERSION}"', 'schema_version': str(SCHEMA_VERSION),
                'parser_version': f'"{PARSER_VERSION}"'}
    if not text.startswith('---\n'):
        raise ValueError(_MESSAGES['skill_unavailable'])
    frontmatter = text.split('---', 2)[1]
    if any(not re.search(rf'^{key}: {re.escape(value)}$', frontmatter, re.M)
           for key, value in versions.items()):
        raise ValueError(_MESSAGES['skill_unavailable'])
    return text


def validate_index(result: dict) -> None:
    """拒绝缺字段、错误状态及自相矛盾的覆盖报告。"""
    def require(condition):
        if not condition:
            raise ValueError('索引输出不符合固定版本契约。')

    def positive(value):
        return type(value) is int and value > 0

    required = {'schema_version', 'skill_version', 'parser_version', 'state', 'overview',
                'units', 'coverage', 'gaps', 'notes'}
    require(isinstance(result, dict) and set(result) == required)
    require(type(result['schema_version']) is int and result['schema_version'] == SCHEMA_VERSION)
    require(result['skill_version'] == SKILL_VERSION and result['parser_version'] == PARSER_VERSION)
    require(isinstance(result['state'], str) and result['state'] in {'ready', 'partial', 'failed'})
    require(isinstance(result['overview'], str) and '摘录' in result['overview'])
    require(isinstance(result['notes'], list) and all(isinstance(n, str) for n in result['notes']))
    coverage = result['coverage']
    require(isinstance(coverage, dict) and set(coverage) == {'total', 'processed', 'failed', 'unit'})
    require(all(type(coverage[k]) is int and coverage[k] >= 0 for k in ('total', 'processed', 'failed')))
    require(coverage['total'] == coverage['processed'] + coverage['failed'])
    require(isinstance(coverage['unit'], str) and coverage['unit'] in {'lines', 'pages', 'blocks', 'cells', 'file'})
    require(isinstance(result['units'], list) and len(result['units']) <= MAX_UNITS)
    seen, characters = set(), 0
    for unit in result['units']:
        require(isinstance(unit, dict) and set(unit) == {'id', 'kind', 'text', 'location', 'label', 'context'})
        require(isinstance(unit['id'], str) and bool(unit['id']) and unit['id'] not in seen)
        seen.add(unit['id'])
        require(isinstance(unit['kind'], str) and unit['kind'] in {'text', 'table', 'image'})
        require(all(isinstance(unit[key], str) for key in ('text', 'label', 'context')))
        require(isinstance(unit['location'], dict) and bool(unit['location']))
        location = unit['location']
        locator = {'lines': 'line', 'pages': 'page', 'blocks': 'paragraph'}.get(coverage['unit'])
        if locator:
            require(positive(location.get(locator)))
        if coverage['unit'] == 'blocks' and unit['kind'] == 'table':
            require(all(positive(location.get(key)) for key in ('table', 'row', 'column')))
        if coverage['unit'] == 'cells':
            require(all(positive(location.get(key)) for key in ('sheet_index', 'row', 'column')))
            require(isinstance(location.get('sheet'), str) and 0 < len(location['sheet']) <= 31)
            require(location.get('cell') == f'{_column_letters(location["column"])}{location["row"]}')
        if 'char_start' in location or 'char_end' in location:
            require(positive(location.get('char_start')) and positive(location.get('char_end')))
            require(location['char_end'] >= location['char_start'])
        characters += len(unit['text'])
    require(characters <= MAX_TEXT_CHARS)
    require(isinstance(result['gaps'], list) and len(result['gaps']) <= MAX_GAPS)
    for gap in result['gaps']:
        require(isinstance(gap, dict) and set(gap) == {'location', 'reason'})
        require(isinstance(gap['location'], dict) and bool(gap['location']))
        require(isinstance(gap['reason'], str) and gap['reason'] in _MESSAGES)
    require(result['state'] != 'ready' or (not result['gaps'] and coverage['failed'] == 0
                                         and coverage['processed'] > 0 and bool(result['units'])))
    require(result['state'] == 'ready' or bool(result['gaps']))


class _Index:
    def __init__(self, unit='file', total=1):
        self.units, self.gaps, self.notes = [], [], []
        self.unit, self.total, self.processed, self.characters = unit, total, 0, 0
        self.limit = None

    def gap(self, reason, location):
        if len(self.gaps) < MAX_GAPS:
            self.gaps.append({'location': location, 'reason': reason})
        elif self.gaps[-1]['location'].get('scope') == 'additional_gaps':
            self.gaps[-1]['location']['omitted_gaps'] += 1
        else:
            # 最后一项用于汇总被替换的一项及后续未逐个列出的缺口。
            self.gaps[-1] = {'location': {'scope': 'additional_gaps', 'omitted_gaps': 2},
                             'reason': 'resource_limit'}
            self.notes.append('缺口数量超过报告上限，额外缺口仅汇总计数，覆盖统计仍包含全部失败项。')
        if _MESSAGES[reason] not in self.notes:
            self.notes.append(_MESSAGES[reason])

    def add(self, text, kind, location, label):
        if not text.strip():
            return True
        for offset in range(0, len(text), UNIT_TEXT_CHARS):
            available = MAX_TEXT_CHARS - self.characters
            if len(self.units) >= MAX_UNITS or available <= 0:
                self.limit = 'unit_limit' if len(self.units) >= MAX_UNITS else 'text_limit'
                self.gap(self.limit, {**location, 'char_start': offset + 1, 'char_end': len(text)})
                return False
            part = text[offset:offset + min(UNIT_TEXT_CHARS, available)]
            loc = dict(location)
            if len(text) > UNIT_TEXT_CHARS or len(part) < len(text):
                loc.update(char_start=offset + 1, char_end=offset + len(part))
            self.units.append({'id': f'u{len(self.units) + 1:06d}', 'kind': kind,
                               'text': part, 'location': loc, 'label': label, 'context': label})
            self.characters += len(part)
            if len(part) < min(UNIT_TEXT_CHARS, len(text) - offset):
                self.limit = 'text_limit'
                self.gap('text_limit', {**location, 'char_start': offset + len(part) + 1, 'char_end': len(text)})
                return False
        return True

    def finish(self, *, failed=False):
        if not self.units and not self.gaps:
            self.gap('empty_text', {'scope': 'file'})
            failed = True
        excerpt = '\n'.join(unit['text'] for unit in self.units if unit['text'])[:300]
        result = {'schema_version': SCHEMA_VERSION, 'skill_version': SKILL_VERSION,
                  'parser_version': PARSER_VERSION,
                  'state': 'failed' if failed else ('partial' if self.gaps else 'ready'),
                  'overview': '原文摘录：' + (excerpt or '暂无可提取文字。'),
                  'units': self.units,
                  'coverage': {'total': self.total, 'processed': self.processed,
                               'failed': self.total - self.processed, 'unit': self.unit},
                  'gaps': self.gaps, 'notes': self.notes}
        validate_index(result)
        return result


def _plain(data, index):
    try:
        text = _decode_text(data)
    except AITextError:
        index.gap('invalid_text', {'scope': 'file'})
        return index.finish(failed=True)
    index.unit = 'lines'
    index.total = text.count('\n') + text.count('\r') - text.count('\r\n')
    index.total += bool(text and not text.endswith(('\n', '\r')))
    for line, match in enumerate(re.finditer(r'[^\r\n]*(?:\r\n|\r|\n|$)', text), 1):
        if not match.group():
            break
        if not index.add(match.group().rstrip('\r\n'), 'text', {'line': line}, f'第 {line} 行'):
            if line < index.total:
                index.gap(index.limit, {'line_start': line + 1, 'line_end': index.total})
            break
        index.processed += 1
    return index.finish()


def _paragraph_text(paragraph):
    values = []
    drawing = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
    for node in paragraph.iter():
        if node is not paragraph and node.tag in {_W + 'p', drawing + 'p'}:
            if values and not values[-1].endswith('\n'):
                values.append('\n')
        elif node.tag in {_W + 't', drawing + 't'}:
            values.append(node.text or '')
        elif node.tag == _W + 'tab':
            values.append('\t')
        elif node.tag in {_W + 'br', _W + 'cr', drawing + 'br'}:
            values.append('\n')
    return ''.join(values)


def _office_gaps(index, members, prefix):
    for name in members:
        if not name.startswith(prefix + '/'):
            continue
        reason = None
        if '/media/' in name or '/drawings/' in name:
            reason = 'visual_not_parsed'
        elif '/charts/' in name:
            reason = 'chart_not_parsed'
        elif re.search(r'/(?:comments[^/]*|threadedComments/[^/]+)\.xml$', name):
            reason = 'comments_not_parsed'
        elif re.search(r'/(?:header|footer)[^/]*\.xml$', name):
            reason = 'header_footer_not_parsed'
        elif name in {'word/footnotes.xml', 'word/endnotes.xml'}:
            reason = 'notes_not_parsed'
        elif '/embeddings/' in name or '/externalLinks/' in name:
            reason = 'embedded_content_not_parsed'
        elif 'vbaProject' in name or '/activeX/' in name:
            reason = 'embedded_content_not_parsed'
        elif '/notesSlides/' in name:
            reason = 'notes_not_parsed'
        if reason:
            index.gap(reason, {'part': name})


def _word_body_blocks(body):
    """正文与嵌图共用段落锚点；文本框并入所属外层段落，不另占编号。"""
    tables, paragraphs = 0, 0

    def walk(element, location):
        nonlocal tables, paragraphs
        row, column = 0, 0
        for child in element:
            loc = dict(location)
            if child.tag == _W + 'tbl':
                tables += 1
                loc['table'] = tables
            elif child.tag == _W + 'tr':
                row += 1
                loc['row'] = row
            elif child.tag == _W + 'tc':
                column += 1
                loc['column'] = column
            elif child.tag == _W + 'p':
                paragraphs += 1
                loc['paragraph'] = paragraphs
                yield child, loc
                continue
            elif child.tag in {_W + 'altChunk', _W + 'object'}:
                yield child, loc
            yield from walk(child, loc)

    yield from walk(body, {})


def _docx(archive, members, index):
    root = _xml(archive, members, 'word/document.xml')
    body = root.find(_W + 'body')
    if root.tag != _W + 'document' or body is None:
        raise AITextError('Word 文件正文结构无效。')
    _office_gaps(index, members, 'word')
    references = {'footnoteReference': 'notes_not_parsed', 'endnoteReference': 'notes_not_parsed',
                  'commentReference': 'comments_not_parsed', 'headerReference': 'header_footer_not_parsed',
                  'footerReference': 'header_footer_not_parsed'}
    present = {node.tag for node in body.iter()}
    for tag, reason in references.items():
        if _W + tag in present:
            index.gap(reason, {'part': 'word/document.xml', 'reference': tag})
    paragraphs = []
    for node, location in _word_body_blocks(body):
        if node.tag == _W + 'p':
            paragraphs.append((node, location))
        else:
            index.gap('embedded_content_not_parsed', {'part': 'word/document.xml', **location})
    index.unit, index.total = 'blocks', len(paragraphs)
    for paragraph, location in paragraphs:
        if any(node.tag in {_W + 'drawing', _W + 'pict', _W + 'object'} for node in paragraph.iter()):
            index.gap('visual_not_parsed', dict(location))
        math = '{http://schemas.openxmlformats.org/officeDocument/2006/math}'
        if any(node.tag in {math + 'oMath', math + 'oMathPara', _W + 'sym'} for node in paragraph.iter()):
            index.gap('embedded_content_not_parsed', dict(location))
        kind = 'table' if 'table' in location else 'text'
        label = f'第 {location["paragraph"]} 段'
        if kind == 'table':
            label += f' · 表 {location["table"]} · 行 {location.get("row", 1)} · 列 {location.get("column", 1)}'
        if not index.add(_paragraph_text(paragraph), kind, location, label):
            if location['paragraph'] < index.total:
                index.gap(index.limit, {'paragraph_start': location['paragraph'] + 1, 'paragraph_end': index.total})
            break
        index.processed += 1
    return index.finish()


def _column_number(letters):
    number = 0
    for char in letters:
        number = number * 26 + ord(char) - ord('A') + 1
    return number


def _column_letters(number):
    letters = ''
    while number:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _xlsx(archive, members, index):
    workbook = _xml(archive, members, 'xl/workbook.xml')
    if workbook.tag != _S + 'workbook':
        raise AITextError('Excel 工作簿结构无效。')
    targets = _worksheet_targets(_xml(archive, members, 'xl/_rels/workbook.xml.rels'))
    strings = _xml(archive, members, 'xl/sharedStrings.xml', optional=True)
    if strings is not None and strings.tag != _S + 'sst':
        raise AITextError('Excel 共享文本结构无效。')
    shared = [] if strings is None else [''.join(node.text or '' for node in item.iter(_S + 't')) for item in strings.findall(_S + 'si')]
    if len(shared) > 20_000:
        index.gap('resource_limit', {'part': 'xl/sharedStrings.xml'})
        return index.finish()
    _office_gaps(index, members, 'xl')
    index.notes.append('单元格按原始行列索引，未推断表头；数字与日期保留文件原始值。')
    sheets, sheet_names, sheet_targets = [], set(), set()
    for ordinal, sheet in enumerate(workbook.findall(_S + 'sheets/' + _S + 'sheet'), 1):
        target = targets.get(sheet.get(_R + 'id'))
        name = sheet.get('name', '')
        if (not target or target in sheet_targets or not name or len(name) > 31
                or name.casefold() in sheet_names or any(ord(char) < 32 for char in name)):
            raise AITextError('Excel 工作表引用无效。')
        sheet_names.add(name.casefold())
        sheet_targets.add(target)
        root = _xml(archive, members, target)
        if root.tag != _S + 'worksheet':
            raise AITextError('Excel 工作表结构无效。')
        sheets.append((ordinal, name, root))
    index.unit = 'cells'
    index.total = sum(len(root.findall(_S + 'sheetData/' + _S + 'row/' + _S + 'c')) for _, _, root in sheets)
    for ordinal, name, root in sheets:
        previous_row = 0
        for position, row in enumerate(root.findall(_S + 'sheetData/' + _S + 'row'), 1):
            row_number = int(row.get('r', str(position)))
            if not previous_row < row_number <= 1_048_576:
                raise AITextError('Excel 行位置无效。')
            previous_row, previous_column = row_number, 0
            for cell in row.findall(_S + 'c'):
                reference = cell.get('r', f'{_column_letters(previous_column + 1)}{row_number}')
                match = re.fullmatch(r'([A-Z]{1,3})([1-9][0-9]{0,6})', reference)
                if not match or int(match[2]) != row_number:
                    raise AITextError('Excel 单元格位置无效。')
                column = _column_number(match[1])
                if not previous_column < column <= 16_384:
                    raise AITextError('Excel 单元格位置重复或无效。')
                previous_column = column
                location = {'sheet': name, 'sheet_index': ordinal, 'row': row_number, 'column': column, 'cell': reference}
                if cell.find(_S + 'f') is not None:
                    note = '公式仅使用文件内的缓存值，缓存可能过期；未执行公式、宏或外部链接。'
                    if note not in index.notes:
                        index.notes.append(note)
                    value = cell.find(_S + 'v')
                    if value is None or value.text is None:
                        index.gap('formula_cache_missing', location)
                        continue
                if not index.add(_cell_value(cell, shared), 'table', location, f'{name} · {reference}'):
                    index.gap(index.limit, {'scope': 'remaining_cells', 'count': index.total - index.processed})
                    return index.finish()
                index.processed += 1
    return index.finish()


class _PageTextLimit(Exception):
    pass


def _pdf_has_visuals(page):
    """检查实际绘制操作及调用的 Form；字符串和注释不会被当作操作。"""
    seen, decoded_bytes, operations, page_tokens = set(), 0, 0, 0
    pdf = getattr(getattr(page, 'indirect_reference', None), 'pdf', None)
    paints = {b'S', b's', b'f', b'F', b'f*', b'B', b'B*', b'b', b'b*'}

    def stream_has_visuals(stream, resources, depth=0):
        nonlocal decoded_bytes, operations, page_tokens
        if stream is None:
            return False
        if depth > 20 or len(seen) > 1000:
            raise LimitReachedError('PDF 图形结构超过解析上限。')
        data = stream.get_data()
        decoded_bytes += len(data)
        if decoded_bytes > MAX_PDF_GRAPHICS_BYTES:
            raise LimitReachedError('PDF 图形内容超过解析上限。')
        # operations 会一次性构造整份列表；必须先检查原始流，不能用列表长度限额。
        for stream_tokens, _ in enumerate(_PDF_LEXICAL_ITEMS.finditer(data), 1):
            page_tokens += 1
            if stream_tokens > MAX_PDF_STREAM_TOKENS or page_tokens > MAX_PDF_PAGE_TOKENS:
                raise LimitReachedError('PDF 内容复杂度超过解析上限。')
        content = stream if isinstance(stream, ContentStream) else ContentStream(stream, pdf)
        path_has_segments, visual_found = False, False
        for operands, operator in content.operations:
            operations += 1
            if operations > MAX_PDF_GRAPHICS_OPERATIONS:
                raise LimitReachedError('PDF 图形操作超过解析上限。')
            if operator in {b'INLINE IMAGE', b'sh'}:
                visual_found = True
            elif operator in {b'l', b'c', b'v', b'y', b're'}:
                path_has_segments = True
            elif operator in paints:
                if path_has_segments:
                    visual_found = True
                path_has_segments = False
            elif operator == b'n':
                path_has_segments = False
            elif operator == b'Do':
                objects = resources.get('/XObject', {})
                reference = objects.get(operands[0]) if operands else None
                if reference is None:
                    raise AITextError('PDF 图形引用无效。')
                obj = reference.get_object()
                if obj.get('/Subtype') == '/Image':
                    visual_found = True
                if obj.get('/Subtype') == '/Form' and id(obj) not in seen:
                    seen.add(id(obj))
                    if stream_has_visuals(obj, obj.get('/Resources', resources), depth + 1):
                        visual_found = True
        return visual_found

    return stream_has_visuals(page.get_contents(), page.get('/Resources', {}))


def _pdf(data, index):
    if not data.startswith(b'%PDF-'):
        raise AITextError('文件不是 PDF。')
    with apply_configuration(maximum_declared_stream_length=MAX_INPUT_BYTES,
                             array_based_stream_maximum_output_length=2 * 1024 * 1024,
                             zlib_maximum_output_length=2 * 1024 * 1024,
                             zlib_maximum_recovery_input_length=100_000,
                             lzw_maximum_output_length=2 * 1024 * 1024,
                             run_length_maximum_output_length=2 * 1024 * 1024,
                             stream_filters_maximum_length=4,
                             stream_decoding_work_maximum_length=16 * 1024 * 1024,
                             page_tree_maximum_entries=10_000, page_tree_maximum_depth=20,
                             xform_maximum_invocations_per_extraction=100, jbig2dec_binary=None):
        reader = PdfReader(BytesIO(data), strict=True, root_object_recovery_limit=100)
        if reader.is_encrypted:
            index.gap('encrypted_pdf', {'scope': 'file'})
            return index.finish(failed=True)
        index.unit, index.total = 'pages', len(reader.pages)
        for number in range(min(index.total, MAX_PDF_PAGES)):
            location = {'page': number + 1}
            page = reader.pages[number]
            if _pdf_has_visuals(page):
                index.gap('visual_not_parsed', location)
            parts, used, truncated = [], 0, False
            remaining = MAX_TEXT_CHARS - index.characters

            def visit(text, _cm, _tm, _font, _size):
                nonlocal used
                if text:
                    parts.append(text[:max(0, remaining - used)])
                    used += len(text)
                    if used > remaining:
                        raise _PageTextLimit

            try:
                page.extract_text(visitor_text=visit)
            except _PageTextLimit:
                truncated = True
            text = ''.join(parts)
            if not text.strip():
                index.gap('no_text_layer', location)
            elif index.add(text, 'text', location, f'第 {number + 1} 页') and not truncated:
                index.processed += 1
            if truncated:
                index.limit = 'text_limit'
                index.gap('text_limit', {**location, 'char_start': len(text) + 1})
            if index.limit:
                if number + 1 < index.total:
                    index.gap(index.limit, {'page_start': number + 2, 'page_end': index.total})
                break
        if index.total > MAX_PDF_PAGES:
            index.gap('page_limit', {'page_start': MAX_PDF_PAGES + 1, 'page_end': index.total})
    index.notes.append('仅索引 PDF 文字层及页位置；未解释图形布局，未访问链接。')
    return index.finish()


def extract_index(data: bytes, filename: str) -> dict:
    """解析调用方已核验身份与版本的快照，所有文档错误均返回固定文本。"""
    index = _Index()
    try:
        load_skill()
    except (OSError, UnicodeError, ValueError):
        index.gap('skill_unavailable', {'scope': 'file'})
        return index.finish(failed=True)
    if len(data) > MAX_INPUT_BYTES:
        index.gap('input_limit', {'scope': 'file'})
        return index.finish()
    original_suffix = Path(filename).suffix.lower()
    suffix = canonical_suffix(original_suffix)
    if original_suffix != suffix:
        index.notes.append('按只读正文处理模板或宏文档；未执行宏、脚本或外部链接。')
    if original_suffix in {'.docm', '.dotm', '.xlsm', '.xltm', '.pptm', '.potm', '.ppsm'}:
        index.gap('embedded_content_not_parsed', {'scope': 'macros'})
    if suffix in _IMAGE_SUFFIXES:
        index.gap('visual_not_parsed', {'scope': 'file'})
        return index.finish()
    if suffix not in _TEXT_SUFFIXES | {'.pdf', '.docx', '.xlsx', '.pptx', '.odt', '.ods', '.odp'}:
        index.gap('unsupported_format', {'scope': 'file'})
        return index.finish(failed=True)
    try:
        from . import index_formats
        if suffix in {'.csv', '.tsv'}:
            return index_formats.delimited(data, suffix, index)
        if suffix in {'.html', '.htm'}:
            return index_formats.html(data, index)
        if suffix in _TEXT_SUFFIXES:
            return _plain(data, index)
        if suffix == '.pdf':
            return _pdf(data, index)
        with zipfile.ZipFile(BytesIO(data)) as archive:
            members = _members(archive)
            if suffix == '.pptx':
                return index_formats.pptx(archive, members, index)
            if suffix in {'.odt', '.ods', '.odp'}:
                return index_formats.odf(archive, members, suffix, index)
            return _docx(archive, members, index) if suffix == '.docx' else _xlsx(archive, members, index)
    except LimitReachedError:
        index.gap('resource_limit', {'scope': 'file'})
        return index.finish()
    except AITextError as exc:
        limited = any(word in str(exc) for word in ('过大', '过多', '压缩比例', '过于复杂'))
        index.gap('resource_limit' if limited else 'invalid_document', {'scope': 'file'})
        return index.finish(failed=not limited and not index.units)
    except (PyPdfError, zipfile.BadZipFile, OSError, UnicodeError, ValueError, TypeError,
            KeyError, IndexError, RecursionError, RuntimeError, EOFError,
            NotImplementedError, AttributeError, zlib.error):
        index.gap('invalid_document', {'scope': 'file'})
        return index.finish(failed=not index.units)
