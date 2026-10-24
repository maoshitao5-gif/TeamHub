"""Folder evidence comes only from the frozen, validated archive."""
import hashlib
from io import BytesIO
from pathlib import Path
import stat
import zipfile

import pytest

from backend.app import database
from backend.app.models import Document, Version
from backend.app.services import indexing, versions
from backend.tests.test_partner_tools import library  # noqa: F401


def saved_folder(root, files, name='项目资料'):
    folder = root / name
    folder.mkdir()
    for relative, data in files.items():
        target = folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    with database.SessionLocal() as db:
        doc = Document(name=name, storage_path=name, status='pending', storage_mode='copy',
                       is_folder=True, doc_type='folder')
        db.add(doc)
        db.flush()
        version, _ = versions.snapshot_working_copy(db, doc)
        db.commit()
        return doc.id, {'name': doc.name, 'version_id': version.id,
            'content_hash': version.sha256_hash, 'metadata_hash': indexing.metadata_fingerprint(doc),
            'version_number': version.version_number, 'is_folder': True}


def reader(root, doc_id, meta, **kwargs):
    from backend.app.services.folder_reader import FolderReader
    return FolderReader(engine=database.engine, root=str(root), scope={doc_id: meta},
                        guard=kwargs.pop('guard', lambda: None), **kwargs)


def archive_path(meta):
    with database.SessionLocal() as db:
        return Path(db.get(Version, meta['version_id']).file_path)


def replace_archive(root, meta, data):
    digest = hashlib.sha256(data).hexdigest()
    target = root / '.teamhub' / 'objects' / digest[:2] / digest
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    with database.SessionLocal() as db:
        version = db.get(Version, meta['version_id'])
        version.manifest_hash = digest
        version.file_path = str(target)
        db.commit()
    return target


def test_inventory_is_paginated_and_never_counts_as_content(library):
    doc_id, meta = saved_folder(library, {'甲/说明.txt': b'A', '乙/说明.txt': b'B', '附件.bin': b'raw'})
    tools = reader(library, doc_id, meta)
    tools.preflight()
    first = tools.list_folder_members(doc_id, limit=2)
    second = tools.list_folder_members(**first['next_call'])
    paths = [item['member_path'] for part in (first, second) for item in part['members']]
    assert len(set(paths)) == 3
    assert first['total_members'] == 3 and second['next_call'] is None
    assert tools.sources == []
    assert tools.coverage()['read_members'] == 0
    assert not tools.coverage()['complete']
    assert str(library) not in str(first)


def test_same_member_locations_remain_distinct_and_live_changes_are_ignored(library):
    doc_id, meta = saved_folder(library, {'甲/说明.txt': b'original A', '乙/说明.txt': b'original B'})
    (library / '项目资料' / '甲' / '说明.txt').write_text('unsaved replacement')
    tools = reader(library, doc_id, meta)
    first = tools.read_folder_member(doc_id, '甲/说明.txt', action='text')
    second = tools.read_folder_member(doc_id, '乙/说明.txt', action='text')
    assert first['units'][0]['text'] == 'original A'
    assert second['units'][0]['text'] == 'original B'
    assert first['units'][0]['source_id'] != second['units'][0]['source_id']
    assert {source['location']['member_path'] for source in tools.sources} == {'甲/说明.txt', '乙/说明.txt'}
    assert all(source['document_id'] == doc_id and source['version_id'] == meta['version_id']
               and source['content_hash'] == meta['content_hash'] for source in tools.sources)
    assert all(item['location'] == next(source['location'] for source in tools.sources
               if source['id'] == item['source_id']) for item in tools.export_checkpoint()['evidence'])
    coverage = tools.coverage()
    assert coverage['read_members'] == coverage['total_members'] == 2
    assert coverage['read_units'] == coverage['total_units'] == 2
    assert coverage['complete'] is True


def test_partial_and_unsupported_members_are_reported(library):
    doc_id, meta = saved_folder(library, {'notes.txt': b'first\nsecond', 'unknown.bin': b'\x00\x01'})
    tools = reader(library, doc_id, meta)
    result = tools.read_folder_member(doc_id, 'notes.txt', action='text', limit=1)
    assert result['next_call']['member_path'] == 'notes.txt'
    assert result['next_call']['action'] == 'text'
    tools.read_folder_member(doc_id, 'unknown.bin', action='text')
    coverage = tools.coverage()
    assert coverage['read_members'] == 1 and not coverage['complete']
    assert any('unknown.bin' in gap for gap in tools.coverage_gaps())
    assert any('1 / 2' in gap for gap in tools.coverage_gaps())


@pytest.mark.parametrize('member_path', ['../outside.txt', '/etc/passwd', 'a/../b.txt',
    'a\\b.txt', 'a\nb.txt', 'a//b.txt', 'a/./b.txt', 'missing.txt'])
def test_only_exact_safe_member_paths_are_accepted(library, member_path):
    doc_id, meta = saved_folder(library, {'safe.txt': b'safe'})
    tools = reader(library, doc_id, meta)
    with pytest.raises(ValueError):
        tools.read_folder_member(doc_id, member_path, action='text')
    assert tools.sources == []


@pytest.mark.parametrize('mode', ['traversal', 'control', 'null', 'symlink', 'duplicate', 'encrypted', 'corrupt'])
def test_unsafe_archives_are_rejected_before_member_reading(library, mode):
    doc_id, meta = saved_folder(library, {'safe.txt': b'safe'})
    output = BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        if mode == 'traversal':
            archive.writestr('../outside.txt', b'bad')
        elif mode == 'control':
            archive.writestr('bad\nname.txt', b'bad')
        elif mode == 'null':
            archive.writestr('badxname.txt', b'bad')
        elif mode == 'symlink':
            entry = zipfile.ZipInfo('link')
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(entry, b'/etc/passwd')
        elif mode == 'duplicate':
            archive.writestr('safe.txt', b'one')
            archive.writestr('SAFE.txt', b'two')
        else:
            archive.writestr('safe.txt', b'safe')
    data = output.getvalue()
    if mode == 'null':
        data = data.replace(b'badxname.txt', b'bad\x00name.txt')
    if mode == 'encrypted':
        patched = bytearray(data)
        central = data.index(b'PK\x01\x02')
        patched[central + 8] |= 1
        data = bytes(patched)
    if mode == 'corrupt':
        data = b'not a zip'
    replace_archive(library, meta, data)
    tools = reader(library, doc_id, meta)
    with pytest.raises(ValueError, match='快照|归档'):
        tools.preflight()
    assert tools.sources == []


def test_empty_folder_does_not_start_paid_understanding(library):
    doc_id, meta = saved_folder(library, {})
    tools = reader(library, doc_id, meta)
    with pytest.raises(ValueError, match='空|没有'):
        tools.preflight()
    assert tools.list_folder_members(doc_id)['total_members'] == 0
    assert not tools.coverage()['complete']


def test_archive_change_invalidates_cached_member_parser(library):
    doc_id, meta = saved_folder(library, {'safe.txt': b'first'})
    tools = reader(library, doc_id, meta)
    tools.read_folder_member(doc_id, 'safe.txt', action='text')
    target = archive_path(meta)
    data = bytearray(target.read_bytes())
    data[40] ^= 1
    target.write_bytes(data)
    with pytest.raises(ValueError, match='校验|变化'):
        tools.read_folder_member(doc_id, 'safe.txt', action='text')


@pytest.mark.parametrize('replace_parent', [False, True])
def test_object_symlink_and_parent_symlink_are_rejected(library, replace_parent):
    doc_id, meta = saved_folder(library, {'safe.txt': b'first'})
    target = archive_path(meta)
    if replace_parent:
        target = target.parent
    other = library / 'elsewhere'
    target.rename(other)
    target.symlink_to(other)
    with pytest.raises(ValueError, match='快照'):
        reader(library, doc_id, meta).preflight()


def test_archive_is_not_limited_to_single_file_input_bytes(library, monkeypatch):
    from backend.app.services import folder_reader
    doc_id, meta = saved_folder(library, {'a.txt': b'first', 'b.txt': b'second'})
    monkeypatch.setattr(folder_reader, 'MAX_INPUT_BYTES', 10)
    tools = reader(library, doc_id, meta)
    assert archive_path(meta).stat().st_size > 10
    assert tools.read_folder_member(doc_id, 'a.txt', action='text')['units'][0]['text'] == 'first'


def test_oversize_member_does_not_hide_readable_siblings(library, monkeypatch):
    from backend.app.services import folder_reader
    doc_id, meta = saved_folder(library, {'large.txt': b'x' * 20, 'small.txt': b'ok'})
    monkeypatch.setattr(folder_reader, 'MAX_INPUT_BYTES', 10)
    tools = reader(library, doc_id, meta)
    with pytest.raises(ValueError, match='上限'):
        tools.read_folder_member(doc_id, 'large.txt', action='text')
    assert tools.read_folder_member(doc_id, 'small.txt', action='text')['units'][0]['text'] == 'ok'
    assert tools.coverage()['read_members'] == 1
    assert any('large.txt' in gap for gap in tools.coverage_gaps())


def test_scope_change_aborts_cached_reads(library):
    doc_id, meta = saved_folder(library, {'safe.txt': b'first'})
    tools = reader(library, doc_id, meta)
    tools.read_folder_member(doc_id, 'safe.txt', action='text')
    with database.SessionLocal() as db:
        db.get(Document, doc_id).status = 'trashed'
        db.commit()
    with pytest.raises(ValueError, match='变化|失效'):
        tools.read_folder_member(doc_id, 'safe.txt', action='text')


def test_unchanged_archive_hash_is_reused_but_mutations_force_validation(library, monkeypatch):
    doc_id, meta = saved_folder(library, {'safe.txt': b'first'})
    tools = reader(library, doc_id, meta)
    original_hash = tools._archive_hash
    calls = []

    def counted_hash(source):
        calls.append(True)
        return original_hash(source)

    monkeypatch.setattr(tools, '_archive_hash', counted_hash)
    tools.preflight()
    tools.list_folder_members(doc_id)
    tools.read_folder_member(doc_id, 'safe.txt', action='overview')
    tools.read_folder_member(doc_id, 'safe.txt', action='text')
    assert len(calls) == 1
    target = archive_path(meta)
    target.write_bytes(target.read_bytes() + b'changed')
    with pytest.raises(ValueError, match='校验'):
        tools.read_folder_member(doc_id, 'safe.txt', action='text')
    assert len(calls) == 2


def test_table_cursor_sources_and_checkpoint_retain_member_identity(library):
    from backend.tests.test_index_content import xlsx
    rows = ''.join(f'<row r="{row}"><c r="A{row}" t="inlineStr"><is><t>r{row}</t></is></c></row>'
                   for row in range(1, 7))
    doc_id, meta = saved_folder(library, {'a/book.xlsx': xlsx(rows), 'b/book.xlsx': xlsx(rows)})
    tools = reader(library, doc_id, meta)
    first = tools.read_folder_member(doc_id, 'a/book.xlsx', action='table', row_count=2)
    assert first['next_call']['action'] == 'table'
    second = tools.read_folder_member(**first['next_call'])
    third = tools.read_folder_member(doc_id, 'b/book.xlsx', action='table', row_count=2)
    assert first['units'][0]['source_id'] != third['units'][0]['source_id']
    assert second['units'][0]['location']['member_path'] == 'a/book.xlsx'
    assert all(item['label'].startswith('a/book.xlsx') for item in second['header_context'])
    checkpoint = tools.export_checkpoint()
    tools._validate_checkpoint(checkpoint, tools.scope)
    assert tools.coverage()['total_units'] == 12
    assert tools.coverage()['read_members'] == 2


def test_visual_members_share_image_budget_without_source_collision(library, monkeypatch):
    import base64
    from backend.app.services import partner_tools
    from backend.app.services.partner_tools import PartnerEvidenceLimit
    data = b'synthetic image bytes'
    doc_id, meta = saved_folder(library, {'a.png': data, 'b.png': data, 'c.png': data})
    monkeypatch.setattr(partner_tools, 'MAX_TASK_IMAGES', 2)
    monkeypatch.setattr(partner_tools, '_render_isolated', lambda *_args, **_kwargs: {
        'page_count': 1, 'notes': [], 'images': [{'data': base64.b64encode(b'png').decode(),
            'page': 1, 'width': 2, 'height': 2}]})
    tools = reader(library, doc_id, meta)
    first = tools.read_folder_member(doc_id, 'a.png', action='visual')
    second = tools.read_folder_member(doc_id, 'b.png', action='visual')
    assert first.images[0].source_id != second.images[0].source_id
    assert tools.coverage()['visual_pages'] == 2
    assert tools.coverage()['read_members'] == 2
    with pytest.raises(PartnerEvidenceLimit):
        tools.read_folder_member(doc_id, 'c.png', action='visual')
    assert len(tools.sources) == 2
    tools._validate_checkpoint(tools.export_checkpoint(), tools.scope)


def test_actual_pdf_member_uses_existing_isolated_visual_renderer(library):
    from backend.tests.test_partner_tools import pdf
    doc_id, meta = saved_folder(library, {'drawings/one.pdf': pdf(1)})
    tools = reader(library, doc_id, meta)
    result = tools.read_folder_member(doc_id, 'drawings/one.pdf', action='visual')
    assert result.images[0].data.startswith(b'\x89PNG')
    assert tools.sources[0]['location'] == {'page': 1, 'member_path': 'drawings/one.pdf'}
    assert tools.coverage()['visual_pages'] == 1
    assert tools.coverage()['complete'] is False  # Text/structure has not been read.


def test_unknown_tool_arguments_do_not_persist_a_false_content_gap(library):
    doc_id, meta = saved_folder(library, {'safe.txt': b'first'})
    tools = reader(library, doc_id, meta)
    with pytest.raises(ValueError):
        tools.read_folder_member(doc_id, 'safe.txt', action='table')
    tools.read_folder_member(doc_id, 'safe.txt', action='text')
    assert tools.coverage()['complete'] is True
