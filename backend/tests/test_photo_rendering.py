"""Camera-like synthetic images exercise bounded multimodal encoding end to end."""
import base64
from io import BytesIO
import json
import random
import zipfile

from PIL import Image, ImageDraw
import pytest

from backend.app.services import partner_tools
from backend.tests.test_archive_reader import packed, reader
from backend.tests.test_partner_tools import library, saved, toolset  # noqa: F401
from backend.tests.test_partner_visual_assets import office_images, package


@pytest.fixture(scope='module')
def camera_jpeg():
    # Independent RGB detail approximates the difficult, poorly compressible
    # case; a solid colour image cannot reproduce the former PNG-only failure.
    image = Image.frombytes('RGB', (3072, 2048), random.Random(5053).randbytes(3072 * 2048 * 3))
    output = BytesIO()
    image.save(output, 'JPEG', quality=95)
    image.close()
    data = output.getvalue()
    with Image.open(BytesIO(data)) as source:
        source.thumbnail((2304, 1736))
        png = BytesIO()
        source.save(png, 'PNG')
        assert len(png.getvalue()) > partner_tools.MAX_IMAGE_BYTES
    return data


def assert_photo_result(result):
    record = result['images'][0]
    raw = base64.b64decode(record['data'], validate=True)
    assert record['media_type'] == 'image/jpeg'
    assert len(raw) <= partner_tools.MAX_IMAGE_BYTES
    assert record['width'] * record['height'] <= partner_tools.MAX_PAGE_PIXELS
    with Image.open(BytesIO(raw)) as image:
        assert image.format == 'JPEG'
        assert image.size == (record['width'], record['height'])
    assert any('有损' in note and '细' in note for note in result['notes'])
    return raw


def test_high_entropy_camera_image_is_readable_in_isolation(camera_jpeg):
    result = partner_tools._render_isolated(camera_jpeg, '.jpeg', [1])
    assert_photo_result(result)


def test_small_diagram_stays_lossless_png():
    image = Image.new('RGB', (400, 200), 'white')
    ImageDraw.Draw(image).line((10, 100, 390, 100), fill='black', width=1)
    data = BytesIO()
    image.save(data, 'PNG')
    result = partner_tools._render_isolated(data.getvalue(), '.png', [1])
    record = result['images'][0]
    assert record['media_type'] == 'image/png'
    assert not any('有损' in note for note in result['notes'])
    with Image.open(BytesIO(base64.b64decode(record['data']))) as rendered:
        assert rendered.getpixel((100, 100)) == (0, 0, 0)
        assert rendered.getpixel((100, 99)) == (255, 255, 255)
    image.close()


def test_transparency_is_composited_on_white_before_encoding():
    image = Image.new('RGBA', (200, 100), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((50, 25, 150, 75), fill=(0, 0, 0, 255))
    data = BytesIO()
    image.save(data, 'PNG')
    result = partner_tools._render_isolated(data.getvalue(), '.png', [1])
    with Image.open(BytesIO(base64.b64decode(result['images'][0]['data']))) as rendered:
        assert rendered.mode == 'RGB'
        assert rendered.getpixel((0, 0)) == (255, 255, 255)
        assert rendered.getpixel((75, 50)) == (0, 0, 0)
    image.close()


@pytest.mark.parametrize('size', [(1050, 1001), (1001, 1050), (1000, 1000)])
def test_pdf_rounding_stays_within_per_page_pixel_bound(size):
    from pypdf import PdfWriter
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=size[0], height=size[1])
    writer.write(output)
    result = partner_tools._render_isolated(output.getvalue(), '.pdf', [1])
    item = result['images'][0]
    assert item['width'] * item['height'] <= partner_tools.MAX_PAGE_PIXELS
    with Image.open(BytesIO(base64.b64decode(item['data']))) as image:
        assert image.size == (item['width'], item['height'])


@pytest.mark.parametrize('container', ['image', 'office', 'archive'])
def test_jpeg_media_type_and_fixed_source_flow_through_readers(library, camera_jpeg, container):
    if container == 'archive':
        doc, meta, tools = reader(library, '现场照片.zip', packed('zip', {'图片/现场.jpeg': camera_jpeg}))
        result = tools.read_archive_member(doc, '图片/现场.jpeg', action='visual', pages=[1])
    elif container == 'office':
        with zipfile.ZipFile(BytesIO(office_images('.docx'))) as archive:
            parts = {item.filename: archive.read(item) for item in archive.infolist()}
        parts['word/media/visible.png'] = camera_jpeg  # Decoder follows actual bytes.
        doc, meta = saved(library, '现场记录.docx', package(parts))
        tools = toolset(library, {doc: meta})
        result = tools.read_embedded_images(doc, ['image-1'])
    else:
        doc, meta = saved(library, '现场.jpeg', camera_jpeg)
        tools = toolset(library, {doc: meta})
        (library / '现场.jpeg').write_bytes(b'unsaved working edit')
        result = tools.read_visual(doc)
    assert result.images[0].media_type == 'image/jpeg'
    assert result.images[0].data.startswith(b'\xff\xd8\xff')
    assert len(result.images[0].data) <= partner_tools.MAX_IMAGE_BYTES
    assert result.metadata['content_hash'] == meta['content_hash']
    assert tools.sources[0]['version_id'] == meta['version_id']
    assert tools.sources[0]['content_hash'] == meta['content_hash']
    assert tools.progress['image_usage']['bytes'] == len(result.images[0].data)
    assert any('有损' in note for note in result.metadata['notes'])
    assert result.metadata['images'][0]['media_type'] == 'image/jpeg'
    if container == 'archive':
        assert tools.sources[0]['location']['member_path'] == '图片/现场.jpeg'


def worker_record(format='PNG', size=(30, 20)):
    output = BytesIO()
    Image.new('RGB', size, 'red').save(output, format)
    return {'page_count': 1, 'images': [{'page': 1, 'width': size[0], 'height': size[1],
        'media_type': 'image/png' if format == 'PNG' else 'image/jpeg',
        'data': base64.b64encode(output.getvalue()).decode()}], 'notes': []}


@pytest.mark.parametrize('damage', ['mime', 'mismatch', 'signature', 'dimensions', 'page_pixels', 'oversize'])
def test_untrusted_worker_cannot_forge_mime_dimensions_or_limits(monkeypatch, damage):
    result = worker_record()
    item = result['images'][0]
    if damage == 'mime':
        item['media_type'] = 'image/svg+xml'
    elif damage == 'mismatch':
        item['media_type'] = 'image/jpeg'
    elif damage == 'signature':
        item['data'] = base64.b64encode(b'\x89PNG\r\n\x1a\nnot-an-image').decode()
    elif damage == 'dimensions':
        item['width'] += 1
    elif damage == 'page_pixels':
        item['width'], item['height'] = 2500, 2000
    else:
        item['data'] = base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'x' * partner_tools.MAX_IMAGE_BYTES).decode()
    output = json.dumps(result).encode()
    monkeypatch.setattr(partner_tools, '_run_render_process', lambda *args: (output, 0))
    with pytest.raises(ValueError, match='原页'):
        partner_tools._render_isolated(b'input', '.jpeg', [1])


@pytest.mark.parametrize('format', ['PNG', 'JPEG'])
def test_valid_worker_formats_keep_mime_and_actual_dimensions(monkeypatch, format):
    result = worker_record(format)
    output = json.dumps(result).encode()
    monkeypatch.setattr(partner_tools, '_run_render_process', lambda *args: (output, 0))
    assert partner_tools._render_isolated(b'input', '.jpeg', [1]) == result


def test_jpeg_does_not_bypass_task_image_budget_or_commit_partial_evidence(library, camera_jpeg):
    doc, meta = saved(library, '现场.jpeg', camera_jpeg)
    tools = toolset(library, {doc: meta})
    result = tools.read_visual(doc)
    assert result.images[0].media_type == 'image/jpeg'
    tools._image_usage['bytes'] = partner_tools.MAX_TASK_IMAGE_BYTES
    before = json.dumps(tools.sources, sort_keys=True)
    with pytest.raises(partner_tools.PartnerEvidenceLimit, match='12 MiB'):
        tools.read_visual(doc)
    assert json.dumps(tools.sources, sort_keys=True) == before
    assert tools._image_usage['bytes'] == partner_tools.MAX_TASK_IMAGE_BYTES


def test_extra_downscale_is_bounded_and_explicit(monkeypatch):
    monkeypatch.setattr(partner_tools, 'MAX_IMAGE_BYTES', 9_000)
    image = Image.frombytes('RGB', (128, 128), random.Random(5053).randbytes(128 * 128 * 3))
    notes = []
    record = partner_tools._png_record(image, 1, notes=notes)
    assert record['width'] < 128 and record['height'] < 128
    assert len(base64.b64decode(record['data'])) <= 9_000
    assert any('额外缩放' in note and '细' in note for note in notes)
    image.close()


def test_unrepresentable_image_fails_after_finite_attempts(monkeypatch):
    monkeypatch.setattr(partner_tools, 'MAX_IMAGE_BYTES', 1)
    image = Image.new('RGB', (32, 32), 'white')
    with pytest.raises(ValueError, match='4 MiB|编码|上限'):
        partner_tools._png_record(image, 1, notes=[])
    assert image.size == (32, 32)
    image.close()
