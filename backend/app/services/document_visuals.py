"""Discover only referenced local images in bounded Office snapshots.

Archive member names stay inside this module. Public assets contain stable IDs
and real structural anchors; image decoding remains in the render subprocess.
"""
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
import posixpath
from urllib.parse import unquote, urlsplit
import zipfile
import zlib

from .ai_text import AITextError, _members, _xml, canonical_suffix, _W, _S, _R, _REL
from .image_formats import IMAGE_SUFFIXES

MAX_ASSETS = 300
MAX_DISCOVERY_NODES = 100_000
MAX_ASSET_BYTES = 12 * 1024 * 1024
OFFICE_SUFFIXES = {'.docx', '.xlsx', '.pptx', '.odt', '.ods', '.odp'}
_A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
_P = '{http://schemas.openxmlformats.org/presentationml/2006/main}'
_XDR = '{http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing}'
_V = '{urn:schemas-microsoft-com:vml}'
_O = '{urn:oasis:names:tc:opendocument:xmlns:office:1.0}'
_T = '{urn:oasis:names:tc:opendocument:xmlns:text:1.0}'
_TABLE = '{urn:oasis:names:tc:opendocument:xmlns:table:1.0}'
_D = '{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}'
_XLINK = '{http://www.w3.org/1999/xlink}'


@dataclass(frozen=True)
class EmbeddedImage:
    image_id: str
    member: str
    location: dict
    label: str

    def public(self):
        return {'image_id': self.image_id, 'location': dict(self.location), 'label': self.label}


def _local_target(part, target):
    """Resolve package relationships, permitting ../ only while inside the ZIP."""
    if not isinstance(target, str) or not target or '\\' in target or target.startswith('//'):
        return None
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        return None
    path = unquote(parsed.path)
    if '\\' in path or '\x00' in path or ':' in path:
        return None
    resolved = posixpath.normpath(path.lstrip('/') if path.startswith('/')
                                 else posixpath.join(posixpath.dirname(part), path))
    if resolved in {'', '.', '..'} or resolved.startswith('../'):
        return None
    return resolved


class _Discovery:
    def __init__(self, archive, members):
        self.archive, self.members = archive, members
        self.assets, self.notes = [], []
        self._rels = {}
        self._structural_targets = set()
        self._visited_nodes = 0
        self._scan_exhausted = False

    def note(self, text):
        if text not in self.notes:
            self.notes.append(text)

    def nodes(self, element, *, odf=False):
        """限制累计遍历工作量，包含重复呈现和无效引用，不只计算成功资产。"""
        from .index_formats import _ODF_SKIPPED
        stack = [element]
        while stack and not self._scan_exhausted:
            self._visited_nodes += 1
            if self._visited_nodes > MAX_DISCOVERY_NODES:
                self._scan_exhausted = True
                self.note('图像发现达到累计工作量上限，剩余内容未读取，请缩小资料范围或另存为 PDF。')
                return
            node = stack.pop()
            if odf and node.tag in _ODF_SKIPPED and node.tag != _D + 'image':
                continue
            yield node
            stack.extend(reversed(list(node)))

    def relationships(self, part):
        if part not in self._rels:
            path = str(PurePosixPath(part).parent / '_rels' / (PurePosixPath(part).name + '.rels'))
            root = _xml(self.archive, self.members, path, optional=True)
            relationships = {}
            if root is not None:
                if root.tag != _REL + 'Relationships':
                    raise AITextError('Office 图像引用结构无效。')
                for rel in root:
                    identifier = rel.get('Id')
                    if rel.tag != _REL + 'Relationship' or not identifier or identifier in relationships:
                        raise AITextError('Office 图像引用结构无效。')
                    relationships[identifier] = rel
            self._rels[part] = relationships
        return self._rels[part]

    def target(self, part, identifier, kind):
        relation = self.relationships(part).get(identifier)
        if relation is None or not (relation.get('Type') or '').endswith('/' + kind):
            self.note('部分图像引用缺失或类型无效，未读取。')
            return None
        if relation.get('TargetMode') not in {None, 'Internal'}:
            self.note('外部链接图像未读取或联网获取。')
            return None
        target = _local_target(part, relation.get('Target'))
        if target not in self.members:
            self.note('部分内部图像或结构引用无效，未读取。')
            return None
        # 工作表/幻灯片/绘图结构不能重复展开；图片实体可在多个真实锚点复用。
        if kind in {'worksheet', 'slide', 'drawing'}:
            if target in self._structural_targets:
                raise AITextError('文档包含重复结构引用，无法可靠定位图片。')
            self._structural_targets.add(target)
        return target

    def add(self, member, location, label):
        if member is None:
            return
        if (location.get('row', 1) > 1_048_576 or location.get('column', 1) > 16_384):
            self.note('部分图片锚点超出支持的行列范围，未读取；请另存为 PDF 核对。')
            return
        if PurePosixPath(member).suffix.lower() not in IMAGE_SUFFIXES:
            self.note('存在非位图或暂不支持编码的图像，尚未识别；请另存为 PDF 或常规图片。')
            return
        if self.members[member].file_size > MAX_ASSET_BYTES:
            self.note('部分嵌入图片超过 12 MiB 原图上限，未读取。')
            return
        if len(self.assets) >= MAX_ASSETS:
            self.note(f'嵌入图片超过 {MAX_ASSETS} 个安全上限，仅列出前 {MAX_ASSETS} 个，其余未读取。')
            return
        number = len(self.assets) + 1
        self.assets.append(EmbeddedImage(f'image-{number}', member,
                                        {**location, 'image_index': number}, label))

    def ooxml_images(self, element, part, location, label):
        for node in self.nodes(element):
            identifier = None
            if node.tag == _A + 'blip':
                identifier = node.get(_R + 'embed')
                if node.get(_R + 'link'):
                    self.note('外部链接图像未读取或联网获取。')
            elif node.tag == _V + 'imagedata':
                identifier = node.get(_R + 'id')
                if node.get('src'):
                    self.note('外部或无受管引用的图像未读取或联网获取。')
            if identifier:
                self.add(self.target(part, identifier, 'image'), location, label)

    def docx(self):
        part = 'word/document.xml'
        root = _xml(self.archive, self.members, part)
        body = root.find(_W + 'body')
        if root.tag != _W + 'document' or body is None:
            raise AITextError('Word 文件正文结构无效。')
        from .index_content import _word_body_blocks
        for element, location in _word_body_blocks(body):
            if element.tag == _W + 'p':
                self.ooxml_images(element, part, location, f'第 {location["paragraph"]} 段嵌入图片')
        self.note('嵌图列表仅包含正文实际引用的位图；页眉页脚、批注及嵌入对象仍须核对原件。')

    def xlsx(self):
        part = 'xl/workbook.xml'
        root = _xml(self.archive, self.members, part)
        if root.tag != _S + 'workbook':
            raise AITextError('Excel 工作簿结构无效。')
        for ordinal, sheet in enumerate(root.findall(_S + 'sheets/' + _S + 'sheet'), 1):
            name = sheet.get('name', '')
            if not name or len(name) > 31:
                raise AITextError('Excel 工作表引用无效。')
            target = self.target(part, sheet.get(_R + 'id'), 'worksheet')
            if target is None:
                continue
            worksheet = _xml(self.archive, self.members, target)
            for drawing in worksheet.findall(_S + 'drawing'):
                drawing_part = self.target(target, drawing.get(_R + 'id'), 'drawing')
                if drawing_part is None:
                    continue
                drawn = _xml(self.archive, self.members, drawing_part)
                for anchor in drawn:
                    location = {'sheet': name, 'sheet_index': ordinal}
                    if anchor.tag in {_XDR + 'oneCellAnchor', _XDR + 'twoCellAnchor'}:
                        start = anchor.find(_XDR + 'from')
                        if start is not None:
                            for xml_key, key in (('row', 'row'), ('col', 'column')):
                                value = start.findtext(_XDR + xml_key)
                                maximum = 1_048_575 if key == 'row' else 16_383
                                if (value is None or len(value) > 7 or not value.isascii()
                                        or not value.isdecimal() or int(value) > maximum):
                                    raise AITextError('Excel 图片锚点无效。')
                                location[key] = int(value) + 1
                    self.ooxml_images(anchor, drawing_part, location, f'{name} · 嵌入图片')
        self.note('嵌图仅保留工作表锚点，不等同整个表格或图表的实际排版。')

    def pptx(self):
        part = 'ppt/presentation.xml'
        root = _xml(self.archive, self.members, part)
        if root.tag != _P + 'presentation':
            raise AITextError('演示文稿结构无效。')
        for page, slide in enumerate(root.findall(_P + 'sldIdLst/' + _P + 'sldId'), 1):
            target = self.target(part, slide.get(_R + 'id'), 'slide')
            if target:
                root = _xml(self.archive, self.members, target)
                self.ooxml_images(root, target, {'page': page}, f'第 {page} 张幻灯片嵌入图片')
        self.note('仅读取幻灯片实际引用的位图，母版、矢量图形及组合布局尚未还原。')

    def odf_images(self, element, location, label):
        handled = set()
        for image in self.nodes(element, odf=True):
            if image.tag != _D + 'image':
                continue
            handled.add(id(image))
            href = image.get(_XLINK + 'href')
            target = _local_target('content.xml', href)
            if target not in self.members:
                self.note('外部链接或无效引用的图像未读取或联网获取。')
                continue
            self.add(target, location, label)
        return handled

    def odf(self, suffix):
        root = _xml(self.archive, self.members, 'content.xml')
        body = root.find(_O + 'body')
        if body is None:
            raise AITextError('开放文档正文结构无效。')
        if suffix == '.odt':
            text = body.find(_O + 'text')
            if text is None:
                raise AITextError('开放文档正文结构无效。')
            from .index_formats import iter_odf_body_paragraphs
            handled = set()
            for node, location in iter_odf_body_paragraphs(text):
                if self._scan_exhausted:
                    break
                handled.update(self.odf_images(node, location, f'第 {location["paragraph"]} 段嵌入图片'))
            for image in self.nodes(text, odf=True):
                if image.tag != _D + 'image':
                    continue
                if id(image) not in handled:
                    self.odf_images(image, {}, '正文嵌入图片（无段落锚点）')
            self.note('仅读取正文明确引用的位图；其他绘图对象尚未还原。')
        elif suffix == '.odp':
            presentation = body.find(_O + 'presentation')
            if presentation is None:
                raise AITextError('开放演示文稿结构无效。')
            for page, node in enumerate(presentation.findall(_D + 'page'), 1):
                self.odf_images(node, {'page': page}, f'第 {page} 张幻灯片嵌入图片')
        else:
            self.ods(body)

    def ods(self, body):
        spreadsheet = body.find(_O + 'spreadsheet')
        if spreadsheet is None:
            raise AITextError('开放表格结构无效。')
        for ordinal, sheet in enumerate(spreadsheet.findall(_TABLE + 'table'), 1):
            name = sheet.get(_TABLE + 'name', '')
            if not name or len(name) > 31:
                raise AITextError('工作表名称不支持可靠定位。')
            base = {'sheet': name, 'sheet_index': ordinal}
            row_number, handled = 1, set()
            for row in sheet.iter(_TABLE + 'table-row'):
                row_repeat = _repeat(row, 'number-rows-repeated')
                column = 1
                for cell in row:
                    if cell.tag not in {_TABLE + 'table-cell', _TABLE + 'covered-table-cell'}:
                        continue
                    repeat = _repeat(cell, 'number-columns-repeated')
                    self.odf_images(cell, {**base, 'row': row_number, 'column': column},
                                    f'{name} · 行 {row_number} · 列 {column} 嵌入图片')
                    handled.update(id(image) for image in cell.iter(_D + 'image'))
                    if (row_repeat > 1 or repeat > 1) and any(cell.iter(_D + 'image')):
                        self.note('重复行列的图片仅列出文件中的首个锚点，重复呈现未展开。')
                    column += repeat
                row_number += row_repeat
            for image in self.nodes(sheet, odf=True):
                if image.tag != _D + 'image':
                    continue
                if id(image) not in handled:
                    self.odf_images(image, base, f'{name} · 工作表嵌入图片（无单元格锚点）')
            self.note('嵌图仅保留文件已有工作表锚点，未还原排版、图表及重复呈现。')


def _repeat(element, attribute):
    value = element.get(_TABLE + attribute, '1')
    if (len(value) > 7 or not value.isascii() or not value.isdecimal()
            or not 1 <= int(value) <= 1_048_576):
        raise AITextError('开放表格重复范围无效。')
    return int(value)


def discover_visual_assets(data: bytes, filename: str):
    suffix = canonical_suffix(PurePosixPath(filename).suffix)
    if suffix not in OFFICE_SUFFIXES:
        return [], []
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            discovery = _Discovery(archive, _members(archive))
            if suffix == '.docx':
                discovery.docx()
            elif suffix == '.xlsx':
                discovery.xlsx()
            elif suffix == '.pptx':
                discovery.pptx()
            else:
                discovery.odf(suffix)
            return discovery.assets, discovery.notes
    except AITextError as exc:
        raise ValueError(str(exc)) from None
    except (zipfile.BadZipFile, OSError, UnicodeError, ValueError, KeyError, zlib.error):
        raise ValueError('无法安全读取文档嵌入图片结构，请核对原件或另存为 PDF。') from None


def read_asset_bytes(data: bytes, filename: str, image_ids: list[str]):
    assets, notes = discover_visual_assets(data, filename)
    by_id = {asset.image_id: asset for asset in assets}
    if any(identifier not in by_id for identifier in image_ids):
        raise ValueError('所选嵌入图片不存在，请先查看资料结构并使用返回的 image_id。')
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            selected = []
            for identifier in image_ids:
                asset = by_id[identifier]
                with archive.open(asset.member) as stream:
                    raw = stream.read(MAX_ASSET_BYTES + 1)
                if len(raw) > MAX_ASSET_BYTES:
                    raise ValueError('嵌入图片超过原图读取上限。')
                selected.append((asset, raw, PurePosixPath(asset.member).suffix.lower()))
            return selected, notes, len(assets)
    except (zipfile.BadZipFile, OSError, KeyError, zlib.error):
        raise ValueError('嵌入图片内容无法安全读取。') from None
