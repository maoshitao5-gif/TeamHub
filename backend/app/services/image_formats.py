"""Shared image types and bounded local decoder helpers.

Only snapshot bytes enter these helpers. Native decoding, when required, runs
inside the existing disposable render worker, never against a user's path.
"""
from io import BytesIO
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import warnings
import xml.etree.ElementTree as ET


RASTER_SUFFIXES = frozenset({
    '.png', '.apng', '.jpg', '.jpeg', '.jpe', '.jfif', '.gif', '.webp',
    '.bmp', '.dib', '.tif', '.tiff', '.avif', '.avifs',
    '.jp2', '.j2k', '.jpc', '.jpf', '.jpx', '.j2c', '.ico', '.icns',
    '.pbm', '.pgm', '.ppm', '.pnm', '.tga', '.icb', '.vda', '.vst', '.pcx',
})
NATIVE_SUFFIXES = frozenset({'.heic', '.heif'})
VECTOR_SUFFIXES = frozenset({'.svg'})
IMAGE_SUFFIXES = RASTER_SUFFIXES | NATIVE_SUFFIXES | VECTOR_SUFFIXES
MAX_SOURCE_PIXELS = 50_000_000
MAX_FRAMES = 1000
MAX_INPUT_BYTES = 32 * 1024 * 1024


def is_image(filename: str) -> bool:
    return Path(filename).suffix.lower() in IMAGE_SUFFIXES


def supported_visual_suffix(suffix: str) -> bool:
    """Capability only; decoding can still fail for damaged/unknown encodings."""
    return suffix in RASTER_SUFFIXES or (
        suffix in NATIVE_SUFFIXES | VECTOR_SUFFIXES and sys.platform == 'darwin'
        and Path('/usr/bin/sips').is_file())


def _check_size(width, height):
    if (type(width) is not int or type(height) is not int or min(width, height) < 1
            or width * height > MAX_SOURCE_PIXELS):
        raise ValueError('原始图片尺寸无效或超过 5000 万像素安全上限。')


def _svg_size(data: bytes) -> tuple[int, int]:
    """Allow a self-contained SVG subset before native rasterization.

    No links, embedded documents/rasters, CSS sheets, entities or active content
    reach ImageIO. Local fragment references are accepted; the worker timeout
    remains the bound for expensive geometry or recursive local references.
    """
    if len(data) > 2 * 1024 * 1024:
        raise ValueError('SVG 超过 2 MiB 安全解析上限。')
    try:
        text = data.decode('utf-8-sig')
        text = re.sub(r'^\s*<\?xml\s+[^?]*\?>', '', text, count=1)
        if re.search(r'<!DOCTYPE|<!ENTITY|<\?', text, re.I):
            raise ValueError('SVG 包含外部声明或处理指令，未读取。')
        root = ET.fromstring(text)
    except (UnicodeError, ET.ParseError):
        raise ValueError('SVG 不是有效的 UTF-8 图像文档。') from None
    namespace = '{http://www.w3.org/2000/svg}'
    allowed = {'svg', 'g', 'defs', 'title', 'desc', 'metadata', 'rect', 'circle',
        'ellipse', 'line', 'polyline', 'polygon', 'path', 'text', 'tspan',
        'textPath', 'use', 'symbol', 'linearGradient', 'radialGradient', 'stop',
        'clipPath', 'mask', 'pattern', 'marker'}
    nodes = 0
    for node in root.iter():
        nodes += 1
        tag = node.tag.removeprefix(namespace)
        if nodes > 30_000 or not node.tag.startswith(namespace) or tag not in allowed:
            raise ValueError('SVG 包含脚本、嵌入内容或暂不支持的元素，未读取；请另存为 PNG 或 PDF。')
        for raw_name, value in node.attrib.items():
            name = raw_name.rsplit('}', 1)[-1].lower()
            if (name.startswith('on') or name in {'src', 'base'}
                    or '\\' in value or '@' in value or '\x00' in value or '/*' in value):
                raise ValueError('SVG 包含外部资源或活动内容，未读取。')
            if name == 'href' and re.fullmatch(r'#[A-Za-z_][\w.:-]*', value) is None:
                raise ValueError('SVG 包含外部图片或链接，未读取。')
            for reference in re.findall(r'url\s*\(([^)]*)\)', value, re.I):
                if re.fullmatch(r'''\s*['"]?#[A-Za-z_][\w.:-]*['"]?\s*''', reference) is None:
                    raise ValueError('SVG 包含外部样式资源，未读取。')
            if re.search(r'(?:https?|file|ftp|data|javascript)\s*:', value, re.I):
                raise ValueError('SVG 包含外部资源或活动内容，未读取。')
    if root.tag != namespace + 'svg':
        raise ValueError('SVG 根元素无效。')
    units = {'': 1, 'px': 1, 'pt': 96 / 72, 'pc': 16, 'mm': 96 / 25.4,
             'cm': 96 / 2.54, 'in': 96}
    def dimension(value):
        match = re.fullmatch(r'\s*([0-9]+(?:\.[0-9]+)?)(px|pt|pc|mm|cm|in)?\s*', value)
        return float(match[1]) * units[match[2] or ''] if match else None
    width, height = dimension(root.get('width', '')), dimension(root.get('height', ''))
    if width is None or height is None:
        try:
            bounds = [float(value) for value in re.split(r'[\s,]+', root.get('viewBox', '').strip())]
            if len(bounds) != 4 or not all(math.isfinite(item) for item in bounds):
                raise ValueError
            width, height = width or bounds[2], height or bounds[3]
        except ValueError:
            raise ValueError('SVG 需要明确的宽高或 viewBox 才能安全读取。') from None
    width, height = math.ceil(width), math.ceil(height)
    _check_size(width, height)
    return width, height


def _native_run(arguments):
    try:
        result = subprocess.run(['/usr/bin/sips', *arguments],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=6,
            check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError('本机图像解码超时或不可用，请将资料另存为 PNG、JPEG 或 PDF。') from None
    if result.returncode != 0 or len(result.stdout) > 16_384:
        raise ValueError('本机无法解码此图片，编码可能不受支持或资料已损坏；请另存为 PNG、JPEG 或 PDF。')
    return result.stdout.decode('utf-8', errors='replace')


def _native_bytes(data, suffix, *, probe=False):
    if not supported_visual_suffix(suffix):
        raise ValueError('此平台尚无 HEIC/HEIF/SVG 本地图像解码器，请另存为 PNG、JPEG 或 PDF。')
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError('图片超过 32 MiB 读取上限。')
    if suffix == '.svg':
        width, height = _svg_size(data)
    elif (len(data) < 16 or data[4:8] != b'ftyp'
          or not any(brand in data[8:64] for brand in
                     (b'heic', b'heix', b'hevc', b'hevx', b'mif1', b'msf1'))):
        raise ValueError('HEIC/HEIF 文件内容与编码标识不符，未读取。')
    with tempfile.TemporaryDirectory(prefix='teamhub-image-',
                                     dir=os.environ.get('TEAMHUB_RENDER_TEMP')) as temp:
        source, output = Path(temp) / ('snapshot' + suffix), Path(temp) / 'page.png'
        source.write_bytes(data)
        if suffix != '.svg':
            text = _native_run(['-g', 'pixelWidth', '-g', 'pixelHeight', str(source)])
            match_width = re.search(r'pixelWidth:\s*(\d+)', text)
            match_height = re.search(r'pixelHeight:\s*(\d+)', text)
            if not match_width or not match_height:
                raise ValueError('本机无法确认图片尺寸，未读取；请另存为 PNG 或 JPEG。')
            width, height = int(match_width[1]), int(match_height[1])
            _check_size(width, height)
        notes = (['本次转换 HEIC/HEIF 首图，其他图像、连拍帧及辅助图像尚未核对。']
                 if suffix in NATIVE_SUFFIXES else [])
        if max(width, height) > 2304:
            notes.append('原始图片已在本地转换时缩放至最长边不超过 2304 像素；细小文字和图形仍需核对原件。')
        if probe:
            return {'width': width, 'height': height,
                    'frame_count': None if suffix in NATIVE_SUFFIXES else 1,
                    'image_format': suffix[1:].upper(), 'notes': notes}
        _native_run(['--resampleHeightWidthMax', '2304', '-s', 'format', 'png',
                     str(source), '--out', str(output)])
        if not output.is_file() or output.stat().st_size > 16 * 1024 * 1024:
            raise ValueError('本地图像转换超过安全输出上限。')
        raw = output.read_bytes()
        if not raw.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('本地图像转换未返回有效 PNG。')
        return raw, notes


def image_probe(data: bytes, suffix: str) -> dict:
    """Metadata only, inside the disposable worker; never marks pixels read."""
    from PIL import Image, UnidentifiedImageError
    if suffix in NATIVE_SUFFIXES | VECTOR_SUFFIXES:
        return _native_bytes(data, suffix, probe=True)
    Image.MAX_IMAGE_PIXELS = MAX_SOURCE_PIXELS
    warnings.simplefilter('error', Image.DecompressionBombWarning)
    try:
        with Image.open(BytesIO(data)) as image:
            _check_size(image.width, image.height)
            total = getattr(image, 'n_frames', 1)
            if not 1 <= total <= MAX_FRAMES:
                raise ValueError('图片帧数超过安全上限。')
            return {'width': image.width, 'height': image.height,
                    'frame_count': total, 'image_format': image.format, 'notes': []}
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError,
            Image.DecompressionBombWarning):
        raise ValueError('图片内容无法解码，格式或编码可能不受支持，也可能资料已损坏。') from None


def normalized_image_bytes(data: bytes, suffix: str, pages: list[int]):
    """Native formats become a bounded PNG; Pillow handles ordinary rasters."""
    if suffix not in NATIVE_SUFFIXES | VECTOR_SUFFIXES:
        return data, []
    if pages != [1]:
        raise ValueError('HEIC/HEIF/SVG 当前只支持读取首图，请使用 pages=[1]；其余图像尚未识别。')
    return _native_bytes(data, suffix)
