"""Bounded, offline adapters for text, presentation and OpenDocument snapshots.

All adapters share the index contract and the existing ZIP/XML safety boundary.
They never resolve URLs, evaluate formulas, render objects or execute macros.
"""
import csv
from html.parser import HTMLParser
from io import StringIO
import re

from .ai_text import AITextError, _decode_text, _xml, _R, _REL

P = '{http://schemas.openxmlformats.org/presentationml/2006/main}'
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
OFFICE = '{urn:oasis:names:tc:opendocument:xmlns:office:1.0}'
TEXT = '{urn:oasis:names:tc:opendocument:xmlns:text:1.0}'
TABLE = '{urn:oasis:names:tc:opendocument:xmlns:table:1.0}'
DRAW = '{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}'
PRESENTATION = '{urn:oasis:names:tc:opendocument:xmlns:presentation:1.0}'
XLINK = '{http://www.w3.org/1999/xlink}'
MAX_STRUCTURAL_CELLS = 50_000
MAX_ODF_REPEAT = 50_000
MAX_INLINE_CHARS = 1_000_000
_ODF_SKIPPED = {OFFICE + 'annotation', TEXT + 'note', TEXT + 'tracked-changes',
                OFFICE + 'scripts', DRAW + 'image', DRAW + 'object', DRAW + 'object-ole',
                DRAW + 'plugin', PRESENTATION + 'notes'}


def _cell_location(sheet, ordinal, row, column):
    from .index_content import _column_letters
    return {'sheet': sheet, 'sheet_index': ordinal, 'row': row, 'column': column,
            'cell': f'{_column_letters(column)}{row}'}


def delimited(data, suffix, index):
    """CSV record numbers are logical rows, including quoted embedded newlines."""
    try:
        value = _decode_text(data)
    except AITextError:
        index.gap('invalid_text', {'scope': 'file'})
        return index.finish(failed=True)
    rows, total = [], 0
    try:
        for row in csv.reader(StringIO(value, newline=''), delimiter='\t' if suffix == '.tsv' else ',', strict=True):
            if len(row) > 16_384 or len(rows) >= 1_048_576:
                raise AITextError('表格行列过多，超过安全解析上限。')
            total += len(row)
            if total > MAX_STRUCTURAL_CELLS:
                raise AITextError('表格单元格过多，超过安全解析上限。')
            rows.append(row)
    except csv.Error:
        raise AITextError('表格字段过大或引号结构无效。') from None
    index.unit, index.total = 'cells', total
    index.notes.append('按分隔符与引号解析记录，保留原始单元格文字；未推断表头、执行公式或加载链接。')
    for number, row in enumerate(rows, 1):
        for column, value in enumerate(row, 1):
            location = _cell_location('数据', 1, number, column)
            if not index.add(value, 'table', location, f'数据 · {location["cell"]}'):
                return index.finish()
            index.processed += 1
    return index.finish()


class _VisibleHTML(HTMLParser):
    _BLOCKS = {'p', 'div', 'section', 'article', 'header', 'footer', 'main', 'aside',
               'nav', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'tr', 'table',
               'blockquote', 'pre', 'ul', 'ol', 'hr'}
    _VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
             'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self, index):
        super().__init__(convert_charrefs=True)
        self.index = index
        self.stack, self.parts, self.blocks = [], [], []

    def flush(self):
        value = re.sub(r'[ \t\r\f\v]+', ' ', ''.join(self.parts)).strip()
        self.parts = []
        if value:
            if len(self.blocks) >= MAX_STRUCTURAL_CELLS:
                raise AITextError('HTML 正文块过多，超过安全解析上限。')
            self.blocks.append(value)

    def handle_starttag(self, tag, attrs):
        if len(self.stack) >= 256:
            raise AITextError('HTML 嵌套结构过于复杂，超过安全解析上限。')
        attrs = dict(attrs)
        style = re.sub(r'\s+', '', attrs.get('style') or '').lower()
        hidden = (any(item[1] for item in self.stack) or tag in {'script', 'style', 'head', 'template'}
                  or 'hidden' in attrs or 'display:none' in style or 'visibility:hidden' in style)
        if not hidden:
            if tag in self._BLOCKS:
                self.flush()
            if tag == 'br':
                self.parts.append('\n')
            elif tag in {'td', 'th'} and self.parts:
                self.parts.append('\t')
            if tag in {'img', 'svg', 'canvas', 'video'}:
                self.index.gap('visual_not_parsed', {'paragraph': len(self.blocks) + 1})
            if tag in {'iframe', 'object', 'embed', 'audio'}:
                self.index.gap('embedded_content_not_parsed', {'paragraph': len(self.blocks) + 1})
        if tag not in self._VOID:
            self.stack.append((tag, hidden))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        hidden = any(item[1] for item in self.stack)
        for position in range(len(self.stack) - 1, -1, -1):
            if self.stack[position][0] == tag:
                del self.stack[position:]
                break
        if tag in self._BLOCKS and not hidden:
            self.flush()

    def handle_data(self, data):
        if not any(item[1] for item in self.stack):
            self.parts.append(data)


def html(data, index):
    try:
        value = _decode_text(data)
    except AITextError:
        index.gap('invalid_text', {'scope': 'file'})
        return index.finish(failed=True)
    parser = _VisibleHTML(index)
    parser.feed(value)
    parser.close()
    parser.flush()
    index.unit, index.total = 'blocks', len(parser.blocks)
    index.notes.append('仅提取 HTML 可见正文块；未运行脚本、应用样式表、还原页面布局或加载关联资源。')
    for number, value in enumerate(parser.blocks, 1):
        if not index.add(value, 'text', {'paragraph': number}, f'正文块 {number}'):
            return index.finish()
        index.processed += 1
    return index.finish()


def _slide_targets(root):
    if root.tag != _REL + 'Relationships':
        raise AITextError('演示文稿关系结构无效。')
    targets = {}
    for rel in root:
        if not (rel.get('Type') or '').endswith('/slide'):
            continue
        target = rel.get('Target', '')
        name = target[1:] if target.startswith('/ppt/') else 'ppt/' + target
        identifier = rel.get('Id')
        if (rel.tag != _REL + 'Relationship' or rel.get('TargetMode') not in {None, 'Internal'}
                or not re.fullmatch(r'ppt/slides/[^/:\\]+\.xml', name)
                or '..' in name.split('/') or not identifier or identifier in targets):
            raise AITextError('演示文稿页面引用不安全或无效。')
        targets[identifier] = name
    return targets


def _drawing_text(paragraph):
    return ''.join((node.text or '') if node.tag == A + 't' else '\n'
                   for node in paragraph.iter() if node.tag in {A + 't', A + 'br'})


def pptx(archive, members, index):
    from .index_content import MAX_PDF_PAGES, _office_gaps
    root = _xml(archive, members, 'ppt/presentation.xml')
    if root.tag != P + 'presentation':
        raise AITextError('演示文稿正文结构无效。')
    targets = _slide_targets(_xml(archive, members, 'ppt/_rels/presentation.xml.rels'))
    slides, seen = [], set()
    for item in root.findall(P + 'sldIdLst/' + P + 'sldId'):
        target = targets.get(item.get(_R + 'id'))
        if not target or target in seen:
            raise AITextError('演示文稿页面引用无效或重复。')
        seen.add(target)
        slides.append(target)
    _office_gaps(index, members, 'ppt')
    index.unit, index.total = 'pages', len(slides)
    index.notes.append('页码表示演示文稿中的幻灯片顺序；仅提取形状与表格文字，未读取母版、布局或演讲者备注。')
    for page, target in enumerate(slides[:MAX_PDF_PAGES], 1):
        slide = _xml(archive, members, target)
        if slide.tag != P + 'sld':
            raise AITextError('幻灯片正文结构无效。')
        if any(node.tag in {P + 'pic', A + 'blip', A + 'custGeom', A + 'prstGeom'} for node in slide.iter()):
            index.gap('visual_not_parsed', {'page': page})
        if any(node.tag == P + 'oleObj' for node in slide.iter()):
            index.gap('embedded_content_not_parsed', {'page': page})
        if any(node.tag == A + 'graphicData' and any(kind in node.get('uri', '') for kind in ('chart', 'diagram')) for node in slide.iter()):
            index.gap('chart_not_parsed', {'page': page})
        table_number, paragraph_number = 0, 0

        def walk(element):
            nonlocal table_number, paragraph_number
            if element.tag == A + 'tbl':
                table_number += 1
                for row, line in enumerate(element.findall(A + 'tr'), 1):
                    for column, cell in enumerate(line.findall(A + 'tc'), 1):
                        value = '\n'.join(_drawing_text(p) for p in cell.iter(A + 'p'))
                        location = {'page': page, 'table': table_number, 'row': row, 'column': column}
                        if not index.add(value, 'table', location, f'第 {page} 张幻灯片 · 表 {table_number} · 行 {row} · 列 {column}'):
                            return False
                return True
            if element.tag == A + 'p':
                paragraph_number += 1
                return index.add(_drawing_text(element), 'text', {'page': page, 'paragraph': paragraph_number}, f'第 {page} 张幻灯片 · 段 {paragraph_number}')
            return all(walk(child) for child in element)

        if not walk(slide):
            return index.finish()
        index.processed += 1
    if len(slides) > MAX_PDF_PAGES:
        index.gap('page_limit', {'page_start': MAX_PDF_PAGES + 1, 'page_end': len(slides)})
    return index.finish()


def _repeat(value, maximum=MAX_ODF_REPEAT):
    if value is None:
        return 1
    if len(value) > 9:
        raise AITextError('ODF 重复内容过多，超过安全解析上限。')
    if not value.isascii() or not value.isdecimal() or int(value) < 1:
        raise AITextError('ODF 重复属性无效。')
    number = int(value)
    if number > maximum:
        raise AITextError('ODF 重复内容过多，超过安全解析上限。')
    return number


def _odf_text(element):
    parts, used = [], 0

    def append(value):
        nonlocal used
        used += len(value)
        if used > MAX_INLINE_CHARS:
            raise AITextError('ODF 文字展开内容过大。')
        parts.append(value)

    def walk(node):
        if node.tag in _ODF_SKIPPED:
            return
        if node.tag == TEXT + 's':
            append(' ' * _repeat(node.get(TEXT + 'c'), MAX_INLINE_CHARS))
        elif node.tag == TEXT + 'tab':
            append('\t')
        elif node.tag == TEXT + 'line-break':
            append('\n')
        elif node.text:
            append(node.text)
        for child in node:
            walk(child)
            if child.tail:
                append(child.tail)

    walk(element)
    return ''.join(parts)


def _odf_gaps(root, index):
    for node in root.iter():
        if node.tag in {DRAW + 'image', DRAW + 'custom-shape', DRAW + 'line', DRAW + 'path'}:
            index.gap('visual_not_parsed', {'part': 'content.xml'})
        elif node.tag in {DRAW + 'object', DRAW + 'object-ole', DRAW + 'plugin', OFFICE + 'scripts'}:
            index.gap('embedded_content_not_parsed', {'part': 'content.xml'})
        elif node.tag == OFFICE + 'annotation':
            index.gap('comments_not_parsed', {'part': 'content.xml'})
        elif node.tag == TEXT + 'note':
            index.gap('notes_not_parsed', {'part': 'content.xml'})
        elif node.tag == PRESENTATION + 'notes':
            index.gap('notes_not_parsed', {'part': 'content.xml'})
        elif node.tag in {TABLE + 'table-source', TABLE + 'cell-range-source', TEXT + 'tracked-changes'}:
            index.gap('embedded_content_not_parsed', {'part': 'content.xml'})


def _empty_odf_cell(cell):
    """Only inert whitespace cells may be counted without materializing repeats."""
    blank_tags = {TABLE + 'table-cell', TABLE + 'covered-table-cell', TEXT + 'p',
                  TEXT + 'h', TEXT + 'span', TEXT + 's', TEXT + 'tab', TEXT + 'line-break'}
    value_attributes = {TABLE + 'formula', *(OFFICE + name for name in
                        ('string-value', 'value', 'boolean-value', 'date-value', 'time-value'))}
    return all(node.tag in blank_tags and not (node.text or '').strip()
               and not (node.tail or '').strip()
               and not value_attributes.intersection(node.attrib)
               for node in cell.iter())


def _body_paragraph_count(element):
    if element.tag in _ODF_SKIPPED:
        return 0
    if element.tag in {TEXT + 'p', TEXT + 'h'}:
        return 1
    return sum(_body_paragraph_count(child) for child in element)


def _odf_block_plan(root, index, *, page=None):
    blocks, table_number, expanded, paragraph_count, blank_processed = [], 0, 0, 0, 0

    def walk_table(table, location):
        nonlocal table_number, expanded, paragraph_count, blank_processed
        table_number += 1
        table_id, row_position = table_number, 1
        for row in _table_rows(table):
            row_repeat = _repeat(row.get(TABLE + 'number-rows-repeated'), 1_048_576)
            if row_position + row_repeat - 1 > 1_048_576:
                raise AITextError('ODF 表格行数超过安全解析上限。')
            cells, column_position = [], 1
            for cell in row:
                if cell.tag not in {TABLE + 'table-cell', TABLE + 'covered-table-cell'}:
                    continue
                repeated = _repeat(cell.get(TABLE + 'number-columns-repeated'), 16_384)
                if column_position + repeated - 1 > 16_384:
                    raise AITextError('ODF 表格列数超过安全解析上限。')
                cells.append((cell, column_position, repeated, _empty_odf_cell(cell)))
                column_position += repeated
            active = row_repeat * sum(repeated for _, _, repeated, blank in cells if not blank)
            if not active or expanded + active > MAX_STRUCTURAL_CELLS:
                for cell, _, repeated, blank in cells:
                    count = _body_paragraph_count(cell) * repeated * row_repeat
                    paragraph_count += count
                    if blank:
                        blank_processed += count
                if active:
                    index.gap('resource_limit', {**location, 'table': table_id,
                        'row_start': row_position, 'row_end': row_position + row_repeat - 1})
                row_position += row_repeat
                continue
            expanded += active
            for row_number in range(row_position, row_position + row_repeat):
                for cell, start_column, repeated, blank in cells:
                    if blank:
                        count = _body_paragraph_count(cell) * repeated
                        paragraph_count += count
                        blank_processed += count
                        continue
                    for column in range(start_column, start_column + repeated):
                        walk(cell, {**location, 'table': table_id, 'row': row_number, 'column': column})
            row_position += row_repeat

    def walk(element, location):
        nonlocal paragraph_count
        for child in element:
            if child.tag in _ODF_SKIPPED:
                continue
            loc = dict(location)
            if child.tag == TABLE + 'table':
                walk_table(child, loc)
                continue
            elif child.tag in {TEXT + 'p', TEXT + 'h'}:
                if len(blocks) >= MAX_STRUCTURAL_CELLS:
                    raise AITextError('ODF 段落过多，超过安全解析上限。')
                paragraph_count += 1
                loc['paragraph'] = paragraph_count
                blocks.append((child, loc))
                continue
            walk(child, loc)

    walk(root, {'page': page} if page is not None else {})
    return blocks, paragraph_count, blank_processed


def iter_odf_body_paragraphs(content, *, index=None):
    """Share exact ODT body positions with image discovery, including table repeats.

    Notes, comments, scripts and embedded objects do not create body paragraphs;
    paragraphs nested inside an outer paragraph belong to that outer block.
    Verified empty repeated cells advance positions without materialization.
    """
    if index is None:
        from .index_content import _Index
        index = _Index()
    blocks, _, _ = _odf_block_plan(content, index)
    yield from blocks


def _odf_blocks(root, index, *, page=None):
    blocks, paragraph_count, blank_processed = _odf_block_plan(root, index, page=page)
    if page is None:
        index.unit, index.total = 'blocks', paragraph_count
        index.processed += blank_processed
    text_cache = {}
    for node, location in blocks:
        key = id(node)
        if key not in text_cache:
            value = '' if _empty_odf_cell(node) else _odf_text(node)
            text_cache[key] = (value, bool(value.strip()))
        value, has_text = text_cache[key]
        kind = 'table' if 'table' in location else 'text'
        label = f'第 {page} 张幻灯片 · 段 {location["paragraph"]}' if page else f'第 {location["paragraph"]} 段'
        if has_text and not index.add(value, kind, location, label):
            return False
        if page is None:
            index.processed += 1
    return True


def _table_rows(table):
    for child in table:
        if child.tag == TABLE + 'table-row':
            yield child
        elif child.tag in {TABLE + 'table-header-rows', TABLE + 'table-rows', TABLE + 'table-row-group'}:
            yield from _table_rows(child)


def _ods(root, index):
    plans, names, total, blank_processed, active_planned = [], set(), 0, 0, 0
    for ordinal, table in enumerate(root.findall(TABLE + 'table'), 1):
        name = table.get(TABLE + 'name', '')
        if not name or len(name) > 31 or name.casefold() in names or any(ord(c) < 32 for c in name):
            raise AITextError('ODF 工作表名称无效或重复。')
        names.add(name.casefold())
        row_position = 1
        for row in _table_rows(table):
            row_repeat = _repeat(row.get(TABLE + 'number-rows-repeated'), 1_048_576)
            if row_position + row_repeat - 1 > 1_048_576:
                raise AITextError('ODF 表格行数超过安全解析上限。')
            cells, column = [], 1
            blank_columns = 0
            for cell in row:
                if cell.tag not in {TABLE + 'table-cell', TABLE + 'covered-table-cell'}:
                    continue
                repeated = _repeat(cell.get(TABLE + 'number-columns-repeated'), 16_384)
                if column + repeated - 1 > 16_384:
                    raise AITextError('ODF 重复列过多，超过安全解析上限。')
                if _empty_odf_cell(cell):
                    blank_columns += repeated
                else:
                    cells.append((cell, column, repeated))
                column += repeated
            total += (column - 1) * row_repeat
            blank_processed += blank_columns * row_repeat
            active = (column - 1 - blank_columns) * row_repeat
            if active_planned + active > MAX_STRUCTURAL_CELLS:
                index.gap('resource_limit', {'sheet': name, 'sheet_index': ordinal,
                    'row_start': row_position, 'row_end': row_position + row_repeat - 1})
            elif active:
                active_planned += active
                plans.append((ordinal, name, row_position, row_repeat, cells))
            row_position += row_repeat
    index.unit, index.total = 'cells', total
    index.processed = blank_processed
    index.notes.append('ODF 单元格保留保存的显示文字或缓存值；重复行列按原坐标展开，未推断表头、重新计算公式或加载链接。')
    expanded_characters = 0
    for ordinal, name, start_row, row_repeat, cells in plans:
        values = []
        for cell, start_column, repeated in cells:
            paragraphs = [p for p in cell if p.tag in {TEXT + 'p', TEXT + 'h'}]
            value = '\n'.join('' if _empty_odf_cell(p) else _odf_text(p) for p in paragraphs)
            if not value:
                for attr in ('string-value', 'value', 'boolean-value', 'date-value', 'time-value'):
                    cached = cell.get(OFFICE + attr)
                    if cached is not None:
                        value = cached
                        break
            formula_missing = cell.get(TABLE + 'formula') is not None and not value
            if not formula_missing and not value.strip():
                index.processed += repeated * row_repeat
                continue
            characters = len(value) * repeated * row_repeat
            if expanded_characters + characters > MAX_INLINE_CHARS:
                index.gap('resource_limit', {'sheet': name, 'sheet_index': ordinal,
                    'row_start': start_row, 'row_end': start_row + row_repeat - 1,
                    'column_start': start_column, 'column_end': start_column + repeated - 1})
                continue
            expanded_characters += characters
            values.append((start_column, repeated, value, formula_missing))
        for row in range(start_row, start_row + row_repeat):
            for start_column, repeated, value, formula_missing in values:
                for column in range(start_column, start_column + repeated):
                    location = _cell_location(name, ordinal, row, column)
                    if formula_missing:
                        index.gap('formula_cache_missing', location)
                        continue
                    if not index.add(value, 'table', location, f'{name} · {location["cell"]}'):
                        return index.finish()
                    index.processed += 1
    return index.finish()


def odf(archive, members, suffix, index):
    root = _xml(archive, members, 'content.xml')
    body = root.find(OFFICE + 'body')
    kind = {'.odt': 'text', '.ods': 'spreadsheet', '.odp': 'presentation'}[suffix]
    content = None if body is None else body.find(OFFICE + kind)
    if root.tag != OFFICE + 'document-content' or content is None:
        raise AITextError('ODF 正文结构无效。')
    if 'mimetype' in members:
        with archive.open(members['mimetype']) as source:
            mime = source.read(256).decode('ascii').strip()
        expected = 'application/vnd.oasis.opendocument.' + kind
        if mime not in {expected, expected + '-template'}:
            raise AITextError('ODF 文件类型与正文不匹配。')
    _odf_gaps(root, index)
    if suffix == '.ods':
        return _ods(content, index)
    if suffix == '.odt':
        _odf_blocks(content, index)
        return index.finish()
    from .index_content import MAX_PDF_PAGES
    slides = content.findall(DRAW + 'page')
    index.unit, index.total = 'pages', len(slides)
    index.notes.append('页码表示 ODP 幻灯片顺序；仅提取正文与表格文字，未读取母版和演讲者备注。')
    for page, slide in enumerate(slides[:MAX_PDF_PAGES], 1):
        if not _odf_blocks(slide, index, page=page):
            return index.finish()
        index.processed += 1
    if len(slides) > MAX_PDF_PAGES:
        index.gap('page_limit', {'page_start': MAX_PDF_PAGES + 1, 'page_end': len(slides)})
    return index.finish()
