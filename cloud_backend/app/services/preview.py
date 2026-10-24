"""Read-only previews of fixed cloud versions; never extract archives or run content."""
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile
import zlib
from xml.etree import ElementTree

from app.core import object_store

PDF_MAX_BYTES = 50 * 1024 * 1024
IMAGE_MAX_BYTES = 20 * 1024 * 1024
TEXT_MAX_BYTES = 1024 * 1024
OFFICE_MAX_BYTES = 10 * 1024 * 1024
ZIP_MAX_BYTES = 50 * 1024 * 1024
XML_MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS, MAX_COLS, MAX_SHEETS, MAX_BLOCKS = 200, 50, 20, 1000
MAX_ZIP_ENTRIES = 3000
MAX_OUTPUT_CHARS = 200_000
MAX_CELL_CHARS = 2000
MAX_IMAGE_PIXELS = 40_000_000
PREVIEW_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
_IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}
_TEXT_SUFFIXES = {".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml", ".log",
                  ".ini", ".cfg", ".conf", ".py", ".js", ".ts", ".tsx", ".jsx",
                  ".css", ".html", ".htm", ".svg", ".sql", ".sh", ".c", ".cpp",
                  ".h", ".java", ".go", ".rs", ".toml", ".properties"}
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


class PreviewError(ValueError):
    """Public, fixed messages only; no filesystem paths or parser diagnostics."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(message)


class _Unsupported(ValueError):
    pass


class _TextBudget:
    """Bound output while building it, including repeated shared-string references."""

    def __init__(self):
        self.used = 0
        self.truncated = False

    def take(self, text: str, per_value: int | None = None) -> str:
        if per_value is None:
            per_value = MAX_CELL_CHARS
        available = max(0, min(per_value, MAX_OUTPUT_CHARS - self.used))
        self.truncated |= len(text) > available
        output = text[:available]
        self.used += len(output)
        return output


def _format(filename: str) -> tuple[str, str, int]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return "pdf", "application/pdf", PDF_MAX_BYTES
    if suffix in _IMAGE_TYPES:
        return "image", _IMAGE_TYPES[suffix], IMAGE_MAX_BYTES
    if suffix in _TEXT_SUFFIXES:
        return "text", "text/plain", TEXT_MAX_BYTES
    if suffix == ".docx":
        return "document", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", OFFICE_MAX_BYTES
    if suffix == ".xlsx":
        return "spreadsheet", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", OFFICE_MAX_BYTES
    return "unsupported", "application/octet-stream", 0


def _base(document, version) -> dict:
    return {"document_id": document.id, "version_id": version.id,
            "version_number": version.version_number,
            "filename": version.original_filename or document.name,
            "file_size": version.file_size, "kind": "unsupported",
            "mime_type": "application/octet-stream", "truncated": False}


def _unsupported(result: dict, message: str) -> dict:
    return {**result, "kind": "unsupported", "message": message}


def _validated_path(version, max_bytes: int) -> Path:
    """Same hash/size check as download, bounded before reading the object."""
    digest = version.manifest_hash or version.content_hash
    if not version.content_hash:
        raise PreviewError(404, "版本内容缺失")
    try:
        path = object_store.object_path(digest)
        size = path.stat().st_size
        if not path.is_file():
            raise PreviewError(404, "版本内容缺失")
        if size > max_bytes or version.file_size > max_bytes:
            raise _Unsupported("文件超过此格式的在线预览大小上限，请下载后查看")
        if object_store.hash_file(path) != (digest, version.file_size):
            raise PreviewError(422, "版本内容校验失败")
        return path
    except FileNotFoundError as exc:
        raise PreviewError(404, "版本内容缺失") from exc
    except object_store.StorageValidationError as exc:
        raise PreviewError(422, "版本内容校验失败") from exc
    except OSError as exc:
        raise PreviewError(507, "版本内容读取失败") from exc


def _binary_magic(path: Path, mime: str) -> None:
    with path.open("rb") as source:
        header = source.read(1024 * 1024 if mime == "image/jpeg" else 64)
    valid = {
        "application/pdf": header.startswith(b"%PDF-"),
        "image/png": header.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": header.startswith(b"\xff\xd8\xff"),
        "image/gif": header.startswith((b"GIF87a", b"GIF89a")),
        "image/webp": header.startswith(b"RIFF") and header[8:12] == b"WEBP",
        "image/bmp": header.startswith(b"BM"),
    }.get(mime, False)
    if not valid:
        raise PreviewError(422, "文件内容与声明的预览格式不符")
    if mime.startswith("image/"):
        width, height = _image_dimensions(header, mime)
        if width <= 0 or height <= 0:
            raise PreviewError(422, "图片尺寸信息无效")
        if width * height > MAX_IMAGE_PIXELS:
            raise _Unsupported("图片像素尺寸过大，请下载后查看")


def _image_dimensions(header: bytes, mime: str) -> tuple[int, int]:
    """Read dimensions only; no allocation/decompression of image pixels."""
    def integer(start, size, order="little", signed=False):
        if len(header) < start + size:
            raise PreviewError(422, "图片尺寸信息缺失")
        return int.from_bytes(header[start:start + size], order, signed=signed)

    if mime == "image/png":
        if header[12:16] != b"IHDR":
            raise PreviewError(422, "PNG 图片标头无效")
        return integer(16, 4, "big"), integer(20, 4, "big")
    if mime == "image/gif":
        return integer(6, 2), integer(8, 2)
    if mime == "image/bmp":
        dib_size = integer(14, 4)
        if dib_size == 12:
            return integer(18, 2), integer(20, 2)
        if dib_size in {40, 52, 56, 108, 124}:
            return integer(18, 4, signed=True), abs(integer(22, 4, signed=True))
        raise _Unsupported("此 BMP 图片标头暂不支持预览，请下载后查看")
    if mime == "image/webp":
        chunk = header[12:16]
        if chunk == b"VP8X":
            return integer(24, 3) + 1, integer(27, 3) + 1
        if chunk == b"VP8L" and header[20:21] == b"\x2f":
            packed = integer(21, 4)
            return (packed & 0x3fff) + 1, ((packed >> 14) & 0x3fff) + 1
        if chunk == b"VP8 " and header[23:26] == b"\x9d\x01\x2a":
            return integer(26, 2) & 0x3fff, integer(28, 2) & 0x3fff
        raise PreviewError(422, "WebP 图片标头无效")
    if mime == "image/jpeg":
        offset = 2
        frame_markers = {0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7,
                         0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf}
        while offset + 3 < len(header):
            if header[offset] != 0xff:
                raise PreviewError(422, "JPEG 图片标头无效")
            while offset < len(header) and header[offset] == 0xff:
                offset += 1
            if offset >= len(header):
                break
            marker = header[offset]
            offset += 1
            if marker in {0xd9, 0xda}:
                break
            if marker in {0x01, *range(0xd0, 0xd9)}:
                continue
            length = integer(offset, 2, "big")
            if length < 2:
                raise PreviewError(422, "JPEG 图片标头无效")
            if marker in frame_markers:
                if length < 8:
                    raise PreviewError(422, "JPEG 图片尺寸信息无效")
                return integer(offset + 5, 2, "big"), integer(offset + 3, 2, "big")
            offset += length
        if len(header) >= 1024 * 1024:
            raise _Unsupported("JPEG 图片标头过大，请下载后查看")
        raise PreviewError(422, "JPEG 图片尺寸信息缺失")
    raise PreviewError(422, "图片格式不受支持")


def _text(path: Path) -> str:
    content = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16" if content.startswith((b"\xff\xfe", b"\xfe\xff")) else "gb18030"):
        try:
            text = content.decode(encoding)
            if "\x00" in text:
                break
            return text
        except UnicodeError:
            continue
    raise _Unsupported("此文件无法作为文本安全解码，请下载后查看")


def _zip_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > MAX_ZIP_ENTRIES:
        raise _Unsupported("Office 文件内容过多，请下载后查看")
    total, members, names = 0, {}, set()
    for info in infos:
        name = info.filename
        parts = name.rstrip("/").split("/")
        mode = (info.external_attr >> 16) & 0o170000
        if (not name or name.startswith("/") or "\\" in name or ":" in name
                or any(part in {"", ".", ".."} for part in parts)
                or name.casefold() in names or info.flag_bits & 1
                or mode not in {0, stat.S_IFREG, stat.S_IFDIR}):
            raise PreviewError(422, "Office 文件包含不安全的归档结构")
        names.add(name.casefold())
        if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise PreviewError(422, "Office 文件使用不支持的压缩格式")
        total += info.file_size
        if total > ZIP_MAX_BYTES:
            raise _Unsupported("Office 文件展开内容过大，请下载后查看")
        if info.file_size > 1024 * 1024 and info.file_size > max(info.compress_size, 1) * 200:
            raise PreviewError(422, "Office 文件压缩结构异常")
        members[name] = info
    return members


def _xml(archive, members: dict, name: str, *, optional: bool = False):
    info = members.get(name)
    if not info:
        if optional:
            return None
        raise PreviewError(422, "Office 文件缺少必要内容")
    if info.file_size > XML_MAX_BYTES:
        raise _Unsupported("Office 文件单项内容过大，请下载后查看")
    with archive.open(info) as source:
        content = source.read(XML_MAX_BYTES + 1)
    if len(content) > XML_MAX_BYTES:
        raise _Unsupported("Office 文件单项内容过大，请下载后查看")
    try:
        # Decode before declaration checks so UTF-16 cannot hide ENTITY/DOCTYPE.
        decoded = content.decode("utf-8-sig")
        if "\x00" in decoded or re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", decoded, re.I):
            raise PreviewError(422, "Office 文件包含不安全的 XML 声明")
        return ElementTree.fromstring(decoded)
    except (UnicodeError, ElementTree.ParseError) as exc:
        raise PreviewError(422, "Office 文件内容损坏或编码不受支持") from exc


def _word_text(node) -> str:
    values = []
    for child in node.iter():
        if child.tag == _W + "t":
            values.append(child.text or "")
        elif child.tag == _W + "tab":
            values.append("\t")
        elif child.tag in {_W + "br", _W + "cr"}:
            values.append("\n")
    return "".join(values)


def _docx(archive, members: dict) -> dict:
    root = _xml(archive, members, "word/document.xml")
    body = root.find(_W + "body")
    if root.tag != _W + "document" or body is None:
        raise PreviewError(422, "Word 文件正文结构无效")
    blocks, truncated, budget = [], False, _TextBudget()
    for node in body:
        if node.tag not in {_W + "p", _W + "tbl"}:
            continue
        if len(blocks) >= MAX_BLOCKS or budget.used >= MAX_OUTPUT_CHARS:
            truncated = True
            break
        if node.tag == _W + "p":
            blocks.append({"type": "paragraph", "text": budget.take(_word_text(node), 10_000)})
        else:
            rows = []
            table_rows = node.findall(_W + "tr")
            truncated |= len(table_rows) > MAX_ROWS
            for row in table_rows[:MAX_ROWS]:
                cells = row.findall(_W + "tc")
                truncated |= len(cells) > MAX_COLS
                rows.append([budget.take("\n".join(_word_text(p) for p in cell.findall(_W + "p")))
                             for cell in cells[:MAX_COLS]])
            blocks.append({"type": "table", "rows": rows})
    return {"blocks": blocks, "truncated": truncated or budget.truncated}


def _column_index(reference: str) -> int:
    match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]{0,6})", reference)
    if not match:
        raise PreviewError(422, "Excel 单元格位置无效")
    column = 0
    for char in match[1]:
        column = column * 26 + ord(char) - ord("A") + 1
    return column - 1


def _cell_value(cell, shared: list[str]) -> str:
    value = cell.find(_S + "v")
    text = value.text or "" if value is not None else ""
    kind = cell.get("t")
    if kind == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(_S + "t"))
    if kind == "s":
        try:
            index = int(text)
            if index < 0:
                raise ValueError
            return shared[index]
        except (ValueError, IndexError) as exc:
            raise PreviewError(422, "Excel 共享文本引用无效") from exc
    if kind == "b":
        return "TRUE" if text == "1" else "FALSE" if text == "0" else text
    formula = cell.find(_S + "f")
    if not text and formula is not None:
        return "=" + (formula.text or "")  # Display only; never evaluate formulas.
    return text


def _sheet_rows(root, shared, budget) -> tuple[list, bool]:
    if root.tag != _S + "worksheet":
        raise PreviewError(422, "Excel 工作表结构无效")
    rows, truncated, previous = [], False, 0
    for row in root.findall(_S + "sheetData/" + _S + "row"):
        if budget.used >= MAX_OUTPUT_CHARS:
            truncated = True
            break
        try:
            index = int(row.get("r", str(previous + 1)))
        except ValueError as exc:
            raise PreviewError(422, "Excel 行位置无效") from exc
        if index <= previous:
            raise PreviewError(422, "Excel 行位置无效")
        previous = index
        if index > MAX_ROWS:
            truncated = True
            continue
        while len(rows) < index:
            rows.append([])
        values, seen = [], set()
        for cell in row.findall(_S + "c"):
            reference = cell.get("r")
            column = _column_index(reference) if reference else len(values)
            if reference and int(re.search(r"[0-9]+$", reference)[0]) != index:
                raise PreviewError(422, "Excel 单元格位置无效")
            if column in seen:
                raise PreviewError(422, "Excel 包含重复单元格")
            seen.add(column)
            if column >= MAX_COLS:
                truncated = True
                continue
            while len(values) <= column:
                values.append("")
            values[column] = budget.take(_cell_value(cell, shared), MAX_CELL_CHARS)
        rows[index - 1] = values
    return rows, truncated or budget.truncated


def _xlsx(archive, members: dict) -> dict:
    workbook = _xml(archive, members, "xl/workbook.xml")
    relationships = _xml(archive, members, "xl/_rels/workbook.xml.rels")
    if workbook.tag != _S + "workbook" or relationships.tag != _REL + "Relationships":
        raise PreviewError(422, "Excel 工作簿结构无效")
    targets = {}
    for rel in relationships:
        if rel.tag != _REL + "Relationship" or rel.get("TargetMode") == "External":
            continue
        if not (rel.get("Type") or "").endswith("/worksheet"):
            continue
        target = rel.get("Target", "")
        name = "xl/" + target
        if target.startswith("/xl/"):
            name = target[1:]
        if ("\\" in target or ":" in target or ".." in PurePosixPath(target).parts
                or not re.fullmatch(r"xl/worksheets/[^/]+\.xml", name)):
            raise PreviewError(422, "Excel 工作表引用不安全")
        relation_id = rel.get("Id")
        if not relation_id or relation_id in targets:
            raise PreviewError(422, "Excel 工作表引用无效")
        targets[relation_id] = name
    strings = _xml(archive, members, "xl/sharedStrings.xml", optional=True)
    shared = ([] if strings is None else ["".join(n.text or "" for n in item.iter(_S + "t"))
                                         for item in strings.findall(_S + "si")])
    sheet_nodes = workbook.findall(_S + "sheets/" + _S + "sheet")
    sheets, truncated, budget = [], len(sheet_nodes) > MAX_SHEETS, _TextBudget()
    for sheet in sheet_nodes[:MAX_SHEETS]:
        if budget.used >= MAX_OUTPUT_CHARS:
            truncated = True
            break
        target = targets.get(sheet.get(_R + "id"))
        if not target:
            raise PreviewError(422, "Excel 工作表引用无效或为外部内容")
        rows, limited = _sheet_rows(_xml(archive, members, target), shared, budget)
        sheets.append({"name": (sheet.get("name") or "工作表")[:100], "rows": rows, "truncated": limited})
        truncated |= limited
    return {"sheets": sheets, "truncated": truncated}


def preview_metadata(document, version) -> dict:
    result = _base(document, version)
    if document.doc_type != "file":
        return _unsupported(result, "文件夹和归档暂不支持在线预览，请下载后查看")
    kind, mime, limit = _format(result["filename"])
    if kind == "unsupported":
        return _unsupported(result, "此格式暂不支持在线预览，请下载后查看；Word/Excel 支持 .docx/.xlsx")
    result.update(kind=kind, mime_type=mime)
    try:
        path = _validated_path(version, limit)
        if kind in {"pdf", "image"}:
            _binary_magic(path, mime)
        elif kind == "text":
            text = _text(path)
            result["text"] = text[:MAX_OUTPUT_CHARS]
            result["truncated"] = len(text) > MAX_OUTPUT_CHARS
        else:
            with zipfile.ZipFile(path) as archive:
                members = _zip_members(archive)
                result.update(_docx(archive, members) if kind == "document" else _xlsx(archive, members))
            result["message"] = ("显示正文与表格内容；版式、图片及批注请下载后查看" if kind == "document"
                                 else "显示单元格原始值与已缓存的公式结果；样式和图表请下载后查看")
        if result["truncated"]:
            result["message"] = "仅预览部分内容，完整内容请下载后查看"
        return result
    except _Unsupported as exc:
        return _unsupported(result, str(exc))
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError, zlib.error) as exc:
        raise PreviewError(422, "Office 文件内容损坏或格式不受支持") from exc
    except OSError as exc:
        raise PreviewError(507, "版本内容读取失败") from exc


def preview_content(document, version) -> tuple[Path, str]:
    if document.doc_type != "file":
        raise PreviewError(415, "此格式不支持二进制在线预览")
    kind, mime, limit = _format(version.original_filename or document.name)
    if kind not in {"pdf", "image"}:
        raise PreviewError(415, "此格式不支持二进制在线预览")
    try:
        path = _validated_path(version, limit)
        _binary_magic(path, mime)
        return path, mime
    except _Unsupported as exc:
        raise PreviewError(413, str(exc)) from exc
    except OSError as exc:
        raise PreviewError(507, "版本内容读取失败") from exc
