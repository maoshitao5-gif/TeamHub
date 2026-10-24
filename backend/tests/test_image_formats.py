"""Synthetic image formats, real pixels, native conversion and hostile SVG."""
import base64
from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys

from PIL import Image
import pytest

from backend.app.services import image_formats
from backend.app.services.partner_tools import _probe_image_isolated, _render_isolated
from backend.tests.test_partner_tools import library, saved, toolset


def raster(format='PNG', *, mode='RGB', size=(64, 64)):
    output = BytesIO()
    with Image.new(mode, size, 1 if mode == '1' else 64 if mode == 'L' else 'red') as image:
        image.save(output, format)
    return output.getvalue()


@pytest.mark.parametrize('suffix,format,mode', [
    ('.png', 'PNG', 'RGB'), ('.apng', 'PNG', 'RGB'), ('.jfif', 'JPEG', 'RGB'),
    ('.dib', 'DIB', 'RGB'), ('.avif', 'AVIF', 'RGB'), ('.jp2', 'JPEG2000', 'RGB'),
    ('.j2k', 'JPEG2000', 'RGB'), ('.jpx', 'JPEG2000', 'RGB'),
    ('.ico', 'ICO', 'RGB'), ('.icns', 'ICNS', 'RGB'),
    ('.pbm', 'PPM', '1'), ('.pgm', 'PPM', 'L'), ('.ppm', 'PPM', 'RGB'),
    ('.pnm', 'PPM', 'RGB'), ('.tga', 'TGA', 'RGB'), ('.pcx', 'PCX', 'RGB'),
])
def test_supported_rasters_are_real_decoded_pixels_and_have_structural_metadata(suffix, format, mode):
    data = raster(format, mode=mode)
    info = _probe_image_isolated(data, suffix)
    assert min(info['width'], info['height']) >= 1
    assert info['frame_count'] == 1
    result = _render_isolated(data, suffix, [1])
    assert result['page_count'] == 1
    with Image.open(BytesIO(base64.b64decode(result['images'][0]['data']))) as rendered:
        assert rendered.format == 'PNG' and rendered.mode == 'RGB'
        assert rendered.width == info['width'] and rendered.height == info['height']
        if mode == 'RGB':
            red, green, blue = rendered.getpixel((rendered.width // 2, rendered.height // 2))
            assert red > 200 and green < 60 and blue < 60


def test_image_overview_provides_visual_next_call_without_inventing_text_or_sources(library):
    doc, meta = saved(library, '结构.avif', raster('AVIF', size=(96, 48)))
    tools = toolset(library, {doc: meta})
    overview = tools.document_overview(doc)
    assert overview['format'] == 'image'
    assert overview['image']['width'] == 96 and overview['image']['height'] == 48
    assert overview['next_call'] == {'tool': 'read_visual', 'arguments': {'document_id': doc, 'pages': [1]}}
    assert overview['unit_count'] == 0 and tools.sources == []
    assert tools.progress['visual_pages'] == 0
    assert tools.read_visual(doc).images
    assert tools.progress['visual_pages'] == 1 and len(tools.sources) == 1


@pytest.mark.parametrize('suffix', ['.avif', '.jp2', '.ico', '.heif', '.svg'])
def test_image_text_index_records_visual_gap_instead_of_unsupported_format(suffix):
    from backend.app.services.index_content import extract_index
    index = extract_index(b'not-a-text-extractable-image', 'diagram' + suffix)
    assert index['state'] != 'failed'
    assert any(gap['reason'] == 'visual_not_parsed' for gap in index['gaps'])
    assert not any(gap['reason'] == 'unsupported_format' for gap in index['gaps'])


def test_animated_image_overview_counts_frames_and_reading_one_preserves_remaining_gap(library):
    output = BytesIO()
    first, second = Image.new('RGB', (8, 8), 'red'), Image.new('RGB', (8, 8), 'blue')
    first.save(output, 'GIF', save_all=True, append_images=[second], duration=50)
    doc, meta = saved(library, '动画.gif', output.getvalue())
    tools = toolset(library, {doc: meta})
    assert tools.document_overview(doc)['image']['frame_count'] == 2
    result = tools.read_visual(doc, [2])
    assert result.metadata['page_count'] == 2
    assert any('尚未阅读' in note for note in result.metadata['notes'])


@pytest.mark.parametrize('suffix', ['.avif', '.jpg', '.heic', '.svg'])
def test_corrupted_image_has_explicit_failure_without_leaking_document_text(suffix):
    with pytest.raises(ValueError, match='图片|图像|SVG|HEIC') as error:
        _render_isolated(b'PRIVATE-DOCUMENT-CONTENT-not-image', suffix, [1])
    assert 'PRIVATE-DOCUMENT' not in str(error.value)


@pytest.mark.parametrize('payload', [
    '<script>alert(1)</script>',
    '<foreignObject><iframe src="https://invalid.example"/></foreignObject>',
    '<image href="https://invalid.example/image.png"/>',
    '<use href="file:///private/secret.svg#id"/>',
    '<style>@import "https://invalid.example/style.css";</style>',
    '<rect width="4" height="4" fill="url(https://invalid.example/image.png)"/>',
    '<rect width="4" height="4" style="fill:u/**/rl(https://invalid.example)"/>',
    '<rect width="4" height="4" onload="alert(1)"/>',
    '<use xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="data:image/svg+xml,unsafe"/>',
])
def test_svg_external_or_active_content_rejected_before_native_decoder(payload, monkeypatch):
    calls = []
    monkeypatch.setattr(image_formats, '_native_run', lambda *args: calls.append(args))
    data = ('<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8">' + payload + '</svg>').encode()
    with pytest.raises(ValueError, match='SVG'):
        image_formats.normalized_image_bytes(data, '.svg', [1])
    assert calls == []


@pytest.mark.parametrize('prefix', [
    '<!DOCTYPE svg SYSTEM "file:///private/secret">',
    '<!DOCTYPE svg [<!ENTITY x "secret">]>',
    '<?xml-stylesheet href="https://invalid.example/style.css"?>',
])
def test_svg_external_xml_declarations_rejected(prefix):
    with pytest.raises(ValueError, match='SVG'):
        image_formats._svg_size((prefix + '<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"/>').encode())


def test_svg_safe_local_refs_and_viewbox_remain_supported():
    data = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 80"><defs><path id="line" d="M 0 0 L 10 10"/></defs><use href="#line"/></svg>'
    assert image_formats._svg_size(data) == (120, 80)


@pytest.mark.parametrize('dimensions', ['width="90000" height="90000"', 'width="0" height="8"', ''])
def test_svg_missing_or_excessive_dimensions_are_explicit(dimensions):
    with pytest.raises(ValueError, match='SVG|图片'):
        image_formats._svg_size(f'<svg xmlns="http://www.w3.org/2000/svg" {dimensions}/>'.encode())


def test_native_unsupported_platform_is_explicit(monkeypatch):
    monkeypatch.setattr(image_formats.sys, 'platform', 'linux')
    with pytest.raises(ValueError, match='平台.*解码器'):
        image_formats.normalized_image_bytes(b'anything', '.heic', [1])


def test_native_secondary_frames_are_never_silently_replaced_with_first_frame():
    with pytest.raises(ValueError, match='首图'):
        image_formats.normalized_image_bytes(b'anything', '.heic', [2])


def test_unknown_native_heif_frame_count_remains_unknown(monkeypatch):
    monkeypatch.setattr(image_formats, '_native_run', lambda args: 'pixelWidth: 80\npixelHeight: 60\n')
    monkeypatch.setattr(image_formats, 'supported_visual_suffix', lambda suffix: True)
    header = b'\0\0\0\x18ftypheic\0\0\0\0heicmif1'
    info = image_formats.image_probe(header, '.heic')
    assert info['frame_count'] is None
    assert any('尚未核对' in note for note in info['notes'])


def test_parent_cancellation_kills_native_descendants_and_cleans_conversion_workspace(monkeypatch):
    from backend.app.services import partner_tools
    captured, killed = {}, []
    class WaitingProcess:
        pid, returncode = 123456, None
        def communicate(self, input=None, timeout=None):
            if self.returncode is not None:
                return
            raise subprocess.TimeoutExpired(['worker'], timeout)
        def poll(self):
            return self.returncode
    process = WaitingProcess()
    def spawn(command, **kwargs):
        captured.update(kwargs)
        path = Path(kwargs['env']['TEAMHUB_RENDER_TEMP']) / 'native-sensitive-temp'
        path.write_bytes(b'synthetic')
        return process
    def killpg(pid, signal):
        killed.append(pid)
        process.returncode = -9
    monkeypatch.setattr(partner_tools.subprocess, 'Popen', spawn)
    monkeypatch.setattr(partner_tools.os, 'killpg', killpg)
    checks = []
    def guard():
        checks.append(1)
        if len(checks) > 1:
            raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError, match='cancelled'):
        _render_isolated(b'synthetic', '.heic', [1], guard=guard)
    assert killed == [process.pid] and captured['start_new_session'] is True
    assert not Path(captured['env']['TEAMHUB_RENDER_TEMP']).exists()


@pytest.mark.skipif(sys.platform != 'darwin' or os.environ.get('TEAMHUB_NATIVE_IMAGE_TESTS') != '1',
                    reason='Native ImageIO requires the explicit unsandboxed synthetic-image acceptance run.')
def test_native_heic_and_safe_svg_return_actual_pixels(tmp_path, library):
    source = tmp_path / 'source.png'
    source.write_bytes(raster(size=(80, 60)))
    heic = tmp_path / 'source.heic'
    subprocess.run(['/usr/bin/sips', '-s', 'format', 'heic', str(source), '--out', str(heic)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    for suffix, data in [('.heic', heic.read_bytes()),
                         ('.svg', b'<svg xmlns="http://www.w3.org/2000/svg" width="80" height="60"><rect width="80" height="60" fill="red"/></svg>')]:
        info = _probe_image_isolated(data, suffix)
        assert info['width'] == 80 and info['height'] == 60
        result = _render_isolated(data, suffix, [1])
        with Image.open(BytesIO(base64.b64decode(result['images'][0]['data']))) as rendered:
            red, green, blue = rendered.getpixel((20, 20))
            assert red > 200 and green < 60 and blue < 60
        if suffix == '.heic':
            assert info['frame_count'] is None
            assert any('尚未核对' in note for note in result['notes'])
    from backend.tests.test_partner_visual_assets import office_images, package
    import zipfile
    with zipfile.ZipFile(BytesIO(office_images('.docx'))) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    parts['word/_rels/document.xml.rels'] = parts['word/_rels/document.xml.rels'].replace(
        b'Target="media/visible.png"', b'Target="media/visible.svg"')
    parts['word/media/visible.svg'] = b'<svg xmlns="http://www.w3.org/2000/svg" width="80" height="60"><rect width="80" height="60" fill="red"/></svg>'
    doc, meta = saved(library, 'vector.docx', package(parts))
    tools = toolset(library, {doc: meta})
    asset = tools.document_overview(doc)['visual_assets'][0]
    image = tools.read_embedded_images(doc, [asset['image_id']]).images[0]
    assert image.source_id == tools.sources[0]['source_id']
    with Image.open(BytesIO(image.data)) as rendered:
        assert rendered.getpixel((20, 20)) == (255, 0, 0)
