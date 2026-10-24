"""Archive evidence is derived only from a verified immutable saved object."""
from io import BytesIO
import base64
import bz2
import gzip
import lzma
from pathlib import Path
import tarfile
import zipfile

import pytest

from backend.app import database
from backend.app.models import Version
from backend.tests.test_partner_tools import library, saved, pdf  # noqa: F401


def packed(kind, files):
    output = BytesIO()
    if kind == 'zip':
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for path, data in files.items():
                archive.writestr(path, data)
    else:
        mode = {'tar': 'w', 'tar.gz': 'w:gz', 'tar.bz2': 'w:bz2', 'tar.xz': 'w:xz'}[kind]
        with tarfile.open(fileobj=output, mode=mode) as archive:
            for path, data in files.items():
                info = tarfile.TarInfo(path)
                info.size = len(data)
                archive.addfile(info, BytesIO(data))
    return output.getvalue()


def reader(root, filename, data, **kwargs):
    from backend.app.services.archive_reader import ArchiveReader
    doc_id, meta = saved(root, filename, data)
    tools = ArchiveReader(engine=database.engine, root=str(root), scope={doc_id: meta},
                          guard=lambda: None, **kwargs)
    return doc_id, meta, tools


@pytest.mark.parametrize('kind', ['zip', 'tar', 'tar.gz', 'tar.bz2', 'tar.xz'])
def test_supported_archives_read_members_and_never_count_inventory_as_evidence(library, kind):
    doc_id, meta, tools = reader(library, f'project.{kind}', packed(kind,
        {'甲/说明.txt': b'original A', '乙/说明.txt': b'original B'}))
    tools.preflight()
    inventory = tools.list_archive_members(doc_id, limit=1)
    assert inventory['total_members'] == 2 and inventory['next_call']['offset'] == 1
    assert tools.sources == [] and not tools.coverage()['complete']
    for path in ('甲/说明.txt', '乙/说明.txt'):
        result = tools.read_archive_member(doc_id, path, action='text')
        assert result['units'][0]['text'].startswith('original')
        assert result['units'][0]['location']['member_path'] == path
    assert tools.coverage()['complete']
    assert all(item['document_id'] == doc_id and item['version_id'] == meta['version_id']
               and item['content_hash'] == meta['content_hash'] for item in tools.sources)


@pytest.mark.parametrize('suffix, compress', [('gz', gzip.compress), ('bz2', bz2.compress), ('xz', lzma.compress)])
def test_standalone_compressed_file_has_one_named_member(library, suffix, compress):
    doc_id, _, tools = reader(library, f'说明.txt.{suffix}', compress(b'compressed source'))
    assert tools.list_archive_members(doc_id)['members'][0]['member_path'] == '说明.txt'
    assert tools.read_archive_member(doc_id, '说明.txt', action='text')['units'][0]['text'] == 'compressed source'


@pytest.mark.parametrize('filename, kind', [('A.TGZ', 'tar.gz'), ('A.tbz2', 'tar.bz2'),
    ('A.TXZ', 'tar.xz'), ('A.ZIP', 'zip'), ('A.7z', '7z'), ('A.rar', 'rar')])
def test_archive_kind_is_explicit_and_case_insensitive(filename, kind):
    from backend.app.services.archive_formats import archive_kind
    assert archive_kind(filename) == kind


@pytest.mark.parametrize('filename', ['file.docx', 'file.xlsx', 'file.pptx', 'file.odt', 'file.zip.txt', 'zip'])
def test_office_zip_containers_are_not_archive_objects(filename):
    from backend.app.services.archive_formats import archive_kind
    assert archive_kind(filename) is None


@pytest.mark.parametrize('kind', ['zip', 'tar'])
@pytest.mark.parametrize('path', ['../bad.txt', '/bad.txt', 'a\\bad.txt', 'bad\nname.txt'])
def test_unsafe_paths_reject_entire_archive_before_any_evidence(library, kind, path):
    _, _, tools = reader(library, f'a.{kind}', packed(kind, {'safe.txt': b'ok', path: b'bad'}))
    with pytest.raises(ValueError, match='路径|归档|压缩'):
        tools.preflight()
    assert tools.sources == []


@pytest.mark.parametrize('kind', ['zip', 'tar'])
def test_casefold_duplicates_and_file_ancestor_collisions_are_rejected(library, kind):
    _, _, tools = reader(library, f'a.{kind}', packed(kind, {'Safe.txt': b'ok', 'safe.TXT': b'bad'}))
    with pytest.raises(ValueError):
        tools.preflight()
    _, _, tools = reader(library, f'b.{kind}', packed(kind, {'a': b'ok', 'a/b.txt': b'bad'}))
    with pytest.raises(ValueError):
        tools.preflight()


@pytest.mark.parametrize('entry_type', [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE])
def test_tar_links_and_special_files_are_rejected(library, entry_type):
    output = BytesIO()
    with tarfile.open(fileobj=output, mode='w') as archive:
        entry = tarfile.TarInfo('link')
        entry.type = entry_type
        entry.linkname = 'safe.txt'
        archive.addfile(entry)
    _, _, tools = reader(library, 'unsafe.tar', output.getvalue())
    with pytest.raises(ValueError):
        tools.preflight()


def test_zip_crc_corruption_is_not_evidence(library):
    data = packed('zip', {'safe.txt': b'abcdef'})
    with zipfile.ZipFile(BytesIO(data)) as archive:
        info = archive.getinfo('safe.txt')
        start = info.header_offset + 30 + len(info.filename.encode())
    damaged = bytearray(data)
    damaged[start + 2] ^= 8
    _, _, tools = reader(library, 'bad.zip', bytes(damaged))
    with pytest.raises(ValueError):
        tools.preflight()
    assert tools.sources == []


def test_nested_archives_are_visible_with_an_honest_unexpanded_gap(library):
    doc_id, _, tools = reader(library, 'a.zip', packed('zip', {'small.txt': b'ok',
        'nested.zip': packed('zip', {'secret.txt': b'inside'})}))
    listed = tools.list_archive_members(doc_id)['members']
    assert any(item['member_path'] == 'nested.zip' and not item['readable'] for item in listed)
    tools.read_archive_member(doc_id, 'small.txt', action='text')
    assert not tools.coverage()['complete']
    assert any('nested.zip' in gap for gap in tools.coverage_gaps())
    with pytest.raises(ValueError, match='嵌套|递归'):
        tools.read_archive_member(doc_id, 'nested.zip', action='text')


def test_cached_archive_rechecks_snapshot_and_ignores_live_changes(library):
    doc_id, meta, tools = reader(library, 'a.zip', packed('zip', {'safe.txt': b'old'}))
    (library / 'a.zip').write_bytes(b'changed working copy')
    assert tools.read_archive_member(doc_id, 'safe.txt', action='text')['units'][0]['text'] == 'old'
    with database.SessionLocal() as db:
        target = Path(db.get(Version, meta['version_id']).file_path)
    data = bytearray(target.read_bytes())
    data[35] ^= 4
    target.write_bytes(data)
    with pytest.raises(ValueError, match='校验|变化'):
        tools.read_archive_member(doc_id, 'safe.txt', action='text')


def test_large_container_does_not_use_the_single_member_limit(library, monkeypatch):
    from backend.app.services import archive_reader
    monkeypatch.setattr(archive_reader, 'MAX_INPUT_BYTES', 10)
    doc_id, _, tools = reader(library, 'large.zip', packed('zip', {'large.txt': b'x' * 20, 'small.txt': b'ok'}))
    with pytest.raises(ValueError, match='上限'):
        tools.read_archive_member(doc_id, 'large.txt', action='text')
    assert tools.read_archive_member(doc_id, 'small.txt', action='text')['units'][0]['text'] == 'ok'
    assert not tools.coverage()['complete']


def test_archive_expansion_budget_and_entry_limit_are_enforced(library, monkeypatch):
    from backend.app.services import archive_reader
    monkeypatch.setattr(archive_reader, 'MAX_ARCHIVE_EXPANDED_BYTES', 30)
    _, _, tools = reader(library, 'budget.zip', packed('zip', {'large.txt': b'x' * 31}))
    with pytest.raises(ValueError, match='上限|展开'):
        tools.preflight()
    monkeypatch.setattr(archive_reader, 'MAX_ARCHIVE_ENTRIES', 1)
    _, _, tools = reader(library, 'entries.zip', packed('zip', {'one.txt': b'x', 'two.txt': b'y'}))
    with pytest.raises(ValueError, match='上限|条目'):
        tools.preflight()


# Small synthetic fixtures: 7z generated by libarchive's 7zip writer; RAR4 is a
# valid stored-entry fixture verified independently with bsdtar. No user content.
@pytest.mark.parametrize('filename, encoded, expected', [
    ('a.7z', 'N3q8ryccAAPiANvjIAAAAAAAAABsAAAAAAAAACxVDrgAOZ5KAQH8ovzUHDYjNgK36dti+hNCl2xp+P//mdIAAAEEBgABCSAABwsBAAEjAwEBBV0AAIAADBUACAoBXu9ewAAABQERFwBzAG8AdQByAGMAZQAuAHQAeAB0AAAAFAoBAFjuzkIzYd0BEgoBAFjuzkIzYd0BEwoBAMLszkIzYd0BFQYBACCApIEAAA==', 'synthetic 7z evidence'),
    ('a.rar', 'UmFyIRoHAM+QcwAADQAAAAAAAAA48XQAgCoAFwAAABcAAAADDZIIMwAAAAAUMAoApIEAAHNvdXJjZS50eHRzeW50aGV0aWMgcmFyIGV2aWRlbmNlCgSwewAABwA=', 'synthetic rar evidence'),
])
def test_real_7z_and_rar_member_decoding(library, filename, encoded, expected):
    doc_id, _, tools = reader(library, filename, base64.b64decode(encoded))
    tools.preflight()
    assert tools.list_archive_members(doc_id)['members'][0]['member_path'] == 'source.txt'
    assert expected in tools.read_archive_member(doc_id, 'source.txt', action='text')['units'][0]['text']


def test_encrypted_zip_member_is_a_gap_without_hiding_safe_sibling(library):
    # Generated locally with zip's traditional encryption; password is not used.
    encoded = 'UEsDBAoACQAAABSBVV3l6KJcEgAAAAYAAAAKABwAc2VjcmV0LnR4dFVUCQADB3PYagdz2Gp1eAsAAQT1AQAABBQAAAAOr5OK5KVfKFbHRhvJ2mkF9p1QSwcI5eiiXBIAAAAGAAAAUEsDBAoAAAAAABSBVV2PKKQfBAAAAAQAAAAIABwAc2FmZS50eHRVVAkAAwdz2GoHc9hqdXgLAAEE9QEAAAQUAAAAc2FmZVBLAQIeAwoACQAAABSBVV3l6KJcEgAAAAYAAAAKABgAAAAAAAEAAACkgQAAAABzZWNyZXQudHh0VVQFAAMHc9hqdXgLAAEE9QEAAAQUAAAAUEsBAh4DCgAAAAAAFIFVXY8opB8EAAAABAAAAAgAGAAAAAAAAQAAAKSBZgAAAHNhZmUudHh0VVQFAAMHc9hqdXgLAAEE9QEAAAQUAAAAUEsFBgAAAAACAAIAngAAAKwAAAAAAA=='
    doc_id, _, tools = reader(library, 'encrypted.zip', base64.b64decode(encoded))
    tools.preflight()
    inventory = tools.list_archive_members(doc_id)
    encrypted = next(item for item in inventory['members'] if item['member_path'] == 'secret.txt')
    assert encrypted['readable'] is False and '加密' in encrypted['note']
    assert tools.read_archive_member(doc_id, 'safe.txt', action='text')['units'][0]['text'] == 'safe'
    with pytest.raises(ValueError, match='加密'):
        tools.read_archive_member(doc_id, 'secret.txt', action='text')
    assert tools.coverage()['read_members'] == 1 and not tools.coverage()['complete']
    assert any('加密' in gap for gap in tools.coverage_gaps())


def test_archive_preserves_member_specific_image_table_and_pdf_evidence(library):
    from PIL import Image
    from backend.tests.test_index_content import xlsx
    from backend.app.services.partner_tools import VisualResult
    image = BytesIO()
    Image.new('RGB', (40, 30), 'red').save(image, format='PNG')
    table = xlsx('<row r="1"><c r="A1" t="inlineStr"><is><t>设备</t></is></c>'
                 '<c r="B1" t="inlineStr"><is><t>压力</t></is></c></row>'
                 '<row r="2"><c r="A2" t="inlineStr"><is><t>A</t></is></c>'
                 '<c r="B2"><v>0.78</v></c></row>')
    doc_id, _, tools = reader(library, 'mixed.zip', packed('zip', {
        'photo.png': image.getvalue(), 'table.xlsx': table, 'drawing.pdf': pdf(1)}))
    visual = tools.read_archive_member(doc_id, 'photo.png', action='visual', pages=[1])
    assert isinstance(visual, VisualResult) and visual.images
    sheet = tools.read_archive_member(doc_id, 'table.xlsx', action='table')
    assert '0.78' in str(sheet) and sheet['member_path'] == 'table.xlsx'
    text = tools.read_archive_member(doc_id, 'drawing.pdf', action='text')
    assert 'Page 1' in str(text)
    assert {item['location']['member_path'] for item in tools.sources} == {'photo.png', 'table.xlsx', 'drawing.pdf'}
    assert not tools.coverage()['complete']  # PDF image and Office layout remain gaps.


def test_native_worker_environment_excludes_credentials(library, monkeypatch):
    from backend.app.services import archive_reader
    captured = []
    original = archive_reader.subprocess.Popen
    def capture(*args, **kwargs):
        captured.append(kwargs)
        return original(*args, **kwargs)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'not-a-real-credential')
    monkeypatch.setenv('DYLD_LIBRARY_PATH', '/untrusted/location')
    monkeypatch.setattr(archive_reader.subprocess, 'Popen', capture)
    _, _, tools = reader(library, 'a.zip', packed('zip', {'a.txt': b'ok'}))
    tools.preflight()
    assert captured and all('DEEPSEEK_API_KEY' not in item['env'] and 'DYLD_LIBRARY_PATH' not in item['env'] for item in captured)
    assert all(item['close_fds'] and item['pass_fds'] for item in captured)


@pytest.mark.parametrize('mutation', ['nul', 'symlink', 'ancestor_case'])
def test_zip_native_string_and_type_ambiguities_are_rejected(library, mutation):
    output = BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        if mutation == 'nul':
            archive.writestr('badxname.txt', b'bad')
        elif mutation == 'symlink':
            entry = zipfile.ZipInfo('link')
            entry.external_attr = (0o120000 | 0o777) << 16
            archive.writestr(entry, b'outside')
        else:
            archive.writestr('A', b'file')
            archive.writestr('a/child.txt', b'child')
    data = output.getvalue()
    if mutation == 'nul':
        data = data.replace(b'badxname.txt', b'bad\x00name.txt')
    _, _, tools = reader(library, 'unsafe.zip', data)
    with pytest.raises(ValueError):
        tools.preflight()
    assert tools.sources == []


@pytest.mark.parametrize('replace_parent', [False, True])
def test_archive_object_and_parent_symlinks_are_rejected(library, replace_parent):
    _, meta, tools = reader(library, 'a.zip', packed('zip', {'safe.txt': b'ok'}))
    with database.SessionLocal() as db:
        target = Path(db.get(Version, meta['version_id']).file_path)
    if replace_parent:
        target = target.parent
    replacement = library / 'elsewhere'
    target.rename(replacement)
    target.symlink_to(replacement)
    with pytest.raises(ValueError, match='快照'):
        tools.preflight()


def test_empty_archive_is_not_a_paid_understanding_task(library):
    _, _, tools = reader(library, 'empty.zip', packed('zip', {}))
    with pytest.raises(ValueError, match='没有文件'):
        tools.preflight()
    assert tools.sources == [] and not tools.coverage()['complete']


def test_unicode_normalization_collision_is_rejected(library):
    _, _, tools = reader(library, 'a.zip', packed('zip', {'\u00e9.txt': b'A', 'e\u0301.txt': b'B'}))
    with pytest.raises(ValueError, match='重复|冲突'):
        tools.preflight()


def test_snapshot_bytes_are_never_mutated_by_member_reading(library):
    data = packed('zip', {'safe.txt': b'original bytes'})
    doc_id, meta, tools = reader(library, 'a.zip', data)
    tools.read_archive_member(doc_id, 'safe.txt', action='text')
    with database.SessionLocal() as db:
        snapshot = Path(db.get(Version, meta['version_id']).file_path)
    assert snapshot.read_bytes() == data
    assert (library / 'a.zip').read_bytes() == data


@pytest.mark.parametrize('filename, data', [('not.txt.gz', b'not compressed'),
    ('not.txt.bz2', gzip.compress(b'wrong compressor')), ('not.txt.xz', bz2.compress(b'wrong compressor'))])
def test_compressed_extension_is_not_falsely_accepted_as_raw_bytes(library, filename, data):
    _, _, tools = reader(library, filename, data)
    with pytest.raises(ValueError, match='格式|类型|损坏'):
        tools.preflight()
