"""Read one frozen folder archive through the existing single-file evidence tools.

Members remain derived evidence of their parent document/version. Nothing is
extracted into the working library, and a directory listing is never a source.
"""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile
import zlib

from backend.app.services.partner_tools import (
    MAX_INPUT_BYTES, PartnerTools, VisualResult, _valid_member_path,
)
from backend.app.services.ai_text import _TEXT_SUFFIXES, canonical_suffix
from backend.app.services.image_formats import IMAGE_SUFFIXES
from backend.app.services.object_skills import object_kind

_PLAIN = _TEXT_SUFFIXES
_IMAGES = IMAGE_SUFFIXES
_OFFICE = {'.docx', '.xlsx', '.pptx', '.doc', '.xls', '.ppt', '.odt', '.ods', '.odp'}
_READABLE = _PLAIN | _IMAGES | _OFFICE | {'.pdf', '.rtf', '.html', '.htm', '.xml', '.tsv'}
_ARCHIVE_ERROR = '文件夹已保存快照不可读取、归档结构不安全或校验失败，请重新保存版本后重试。'
_CHUNK = 1024 * 1024


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


class FolderReader(PartnerTools):
    """A task-local member selector sharing one source ledger and evidence budget."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if len(self.scope) != 1 or next(iter(self.scope.values())).get('is_folder') is not True:
            raise ValueError('文件夹阅读需要明确授权的一份文件夹资料。')
        self.document_id = next(iter(self.scope))
        self._member_path = None
        self._archive_digest = None
        self._verified_identity = None
        self._members: dict[str, dict] | None = None
        self._member_states: dict[str, dict] = {}
        self._prefixes: dict[str, str] = {}

    def _archive_hash(self, source):
        digest = hashlib.sha256()
        for chunk in iter(lambda: source.read(_CHUNK), b''):
            self._check()
            digest.update(chunk)
        return digest.hexdigest()

    @contextmanager
    def _archive(self, document_id):
        """Anchor each open to no-follow directory FDs; stream the archive hash."""
        from backend.app.services.snapshots import SnapshotError, _checked_zip_entries
        self._assert_current(document_id)
        with self._session() as db:
            doc, version = self._document(db, document_id)
            digest, filename = version.manifest_hash, version.file_path
            if not doc.is_folder or not digest or not re.fullmatch(r'[a-f0-9]{64}', digest):
                raise ValueError(_ARCHIVE_ERROR)
        relative = Path('.teamhub') / 'objects' / digest[:2] / digest
        expected = {Path(self.root) / relative, Path(self.configured_root) / relative}
        if (not isinstance(filename, str) or Path(filename) not in expected
                or self._archive_digest not in {None, digest}):
            raise ValueError(_ARCHIVE_ERROR)
        descriptors = []
        try:
            directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, 'O_NOFOLLOW', 0)
            descriptors.append(os.open(self.root, directory_flags))
            for part in ('.teamhub', 'objects', digest[:2]):
                descriptors.append(os.open(part, directory_flags, dir_fd=descriptors[-1]))
            fd = os.open(digest, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | os.O_NONBLOCK,
                         dir_fd=descriptors[-1])
            with os.fdopen(fd, 'rb') as source:
                before = os.fstat(source.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise ValueError(_ARCHIVE_ERROR)
                # Every read reopens via no-follow FDs. An unchanged inode,
                # ctime, mtime and size can reuse this task's verified digest;
                # any mutation requires a full streaming checksum again.
                unchanged = self._verified_identity == _identity(before)
                if not unchanged:
                    if (self._archive_hash(source) != digest
                            or _identity(before) != _identity(os.fstat(source.fileno()))):
                        raise ValueError(_ARCHIVE_ERROR)
                source.seek(0)
                with zipfile.ZipFile(source) as archive:
                    if not unchanged:
                        entries = _checked_zip_entries(archive)
                        members = {}
                        for entry in entries:
                            path = entry.filename.rstrip('/') if entry.is_dir() else entry.filename
                            if (entry.orig_filename != entry.filename
                                    or not _valid_member_path(path) or entry.flag_bits & 1
                                    or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}):
                                raise ValueError(_ARCHIVE_ERROR)
                            if not entry.is_dir():
                                members[path] = {'member_path': path, 'size': entry.file_size,
                                                 'type': PurePosixPath(path).suffix.lower() or 'unknown',
                                                 'object_kind': object_kind(path)}
                        # A file cannot also be an ancestor of another entry.
                        for entry in entries:
                            parts = entry.filename.rstrip('/').split('/')
                            if any('/'.join(parts[:index]) in members for index in range(1, len(parts))):
                                raise ValueError(_ARCHIVE_ERROR)
                        self._members = dict(sorted(members.items()))
                    self._verified_identity = _identity(before)
                    self._archive_digest = digest
                    yield archive
                if _identity(before) != _identity(os.fstat(source.fileno())):
                    raise ValueError(_ARCHIVE_ERROR)
            self._assert_current(document_id)
        except (OSError, zipfile.BadZipFile, RuntimeError, SnapshotError, EOFError, zlib.error):
            raise ValueError(_ARCHIVE_ERROR) from None
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def preflight(self):
        with self._archive(self.document_id):
            pass
        if not self._members:
            raise ValueError('此文件夹的已保存版本中没有文件，暂不能生成有原文依据的 AI 整理方案。')

    def list_folder_members(self, document_id, offset=0, limit=100):
        if (type(offset) is not int or not 0 <= offset <= 10000
                or type(limit) is not int or not 1 <= limit <= 100):
            raise ValueError('目录分页起点须为 0 至 10000，每次列出 1 至 100 个成员。')
        with self._archive(document_id):
            pass
        members = list(self._members.values())
        read_paths = self._read_members()
        listed = [{**entry, 'read': entry['member_path'] in read_paths,
                   'readable': canonical_suffix(entry['type']) in _READABLE and entry['size'] <= MAX_INPUT_BYTES,
                   'note': ('超过单个成员 32 MiB 读取上限。' if entry['size'] > MAX_INPUT_BYTES
                            else '此格式可能无法解析，需人工核对。'
                            if canonical_suffix(entry['type']) not in _READABLE else '')}
                  for entry in members[offset:offset + limit]]
        next_offset = min(offset + len(listed), len(members))
        self.exposed_document_ids.add(document_id)
        return {'document_id': document_id, 'name': self.scope[document_id]['name'],
            'version_id': self.scope[document_id]['version_id'], 'source': 'saved_version',
            'members': listed, 'offset': offset, 'total_members': len(members),
            'read_members': len(read_paths), 'unread_members': len(members) - len(read_paths),
            'has_more': next_offset < len(members), 'next_offset': next_offset,
            'next_call': ({'document_id': document_id, 'offset': next_offset, 'limit': limit}
                          if next_offset < len(members) else None),
            'note': '这是已保存版本的目录清单，不是已读正文。请按 member_path 使用 read_folder_member 阅读成员；不改动内部结构。'}

    def _snapshot(self, document_id):
        member = self._member_path
        if member is None:
            raise ValueError('请使用 read_folder_member 指定清单内的成员。')
        with self._archive(document_id) as archive:
            entry = self._members.get(member)
            if entry is None:
                raise ValueError('成员不存在于本次授权的文件夹已保存版本中。')
            if entry['size'] > MAX_INPUT_BYTES:
                raise ValueError('该成员超过 32 MiB 读取上限，本次未读取内容。')
            with archive.open(member) as source:
                data = source.read(MAX_INPUT_BYTES + 1)
                if len(data) > MAX_INPUT_BYTES or len(data) != entry['size']:
                    raise ValueError('成员大小超过读取上限或与快照清单不符。')
        self.exposed_document_ids.add(document_id)
        return data, member

    def _parsed_document(self, document_id):
        from backend.app.services import index_content
        data, filename = self._snapshot(document_id)
        # Revalidate the archive even when this member's parsed result is cached.
        cache_key = (document_id, filename)
        result = self._parsed.get(cache_key)
        if result is None:
            result = index_content.extract_index(data, filename)
            index_content.validate_index(result)
            self._parsed[cache_key] = result
            if len(self._parsed) > 3:
                self._parsed.popitem(last=False)
        self._assert_current(document_id)
        self._member_states.setdefault(filename, {})['units'] = len(result['units'])
        return result, filename

    def _prefix(self, member):
        if member not in self._prefixes:
            digest = hashlib.sha256(member.encode('utf-8')).hexdigest()
            self._prefixes[member] = f'{self.document_id}:member:{digest}:'
        return self._prefixes[member]

    def _record(self, document_id, records, unit_keys, read_range):
        member = self._member_path
        if member is None:
            raise ValueError('文件夹正文读取缺少成员范围。')
        records = [dict(record, location={**record['location'], 'member_path': member},
                        label=f'{member} · {record["label"]}'[:500]) for record in records]
        prefix = self._prefix(member)
        keys = [prefix + key.split(':', 1)[1] for key in unit_keys]
        return super()._record(document_id, records, keys, {**read_range, 'member_path': member})

    def read_folder_member(self, document_id, member_path, action='overview', offset=0, limit=60,
                           sheet=None, start_row=1, row_count=80, pages=None, image_ids=None):
        if not _valid_member_path(member_path):
            raise ValueError('成员路径必须是清单中完整的安全相对路径。')
        if action not in {'overview', 'text', 'table', 'visual', 'images'}:
            raise ValueError('请选择 overview、text、table、visual 或 images 阅读方式。')
        with self._archive(document_id):
            pass
        if member_path not in self._members:
            raise ValueError('成员不存在于本次授权的文件夹已保存版本中。')
        self._member_path = member_path
        try:
            if action == 'overview':
                result = super().document_overview(document_id)
            elif action == 'text':
                result = super().read_document(document_id, offset=offset, limit=limit)
            elif action == 'table':
                result = super().read_table(document_id, sheet=sheet, start_row=start_row,
                                            row_count=row_count, offset=offset)
            elif action == 'visual':
                result = super().read_visual(document_id, pages=pages)
            else:
                result = super().read_embedded_images(document_id, image_ids=image_ids)
            metadata = result.metadata if isinstance(result, VisualResult) else result
            self._observe(member_path, metadata)
            self._member_metadata(member_path, action, metadata)
            return result
        except ValueError as exc:
            # Bad tool arguments are repairable. Content failures are already
            # reflected by parser metadata; only stable size failures need a gap.
            if self._members[member_path]['size'] > MAX_INPUT_BYTES:
                state = self._member_states.setdefault(member_path, {})
                state['size_gap'] = str(exc)
            raise
        finally:
            self._member_path = None

    def _member_metadata(self, member, action, metadata):
        metadata['member_path'] = member
        metadata['member_name'] = PurePosixPath(member).name
        for unit in [*metadata.get('units', []), *metadata.get('header_context', [])]:
            if 'location' in unit:
                unit['location'] = {**unit['location'], 'member_path': member}
            unit['label'] = f'{member} · {unit["label"]}'[:500]
        for image in metadata.get('images', []):
            image['location'] = {**image.get('location', {}), 'member_path': member}
            image['label'] = f'{member} · {image["label"]}'[:500]
        if action == 'overview' and metadata.get('format') == 'image':
            metadata['next_call'] = {'document_id': self.document_id, 'member_path': member,
                                     'action': 'visual', 'pages': [1]}
        elif action == 'text':
            metadata['next_call'] = ({'document_id': self.document_id, 'member_path': member,
                'action': action, 'offset': metadata['next_offset']}
                if metadata.get('has_more') else None)
        elif action == 'table' and metadata.get('next_call'):
            metadata['next_call'] = {**metadata['next_call'], 'member_path': member, 'action': action}
        metadata['note'] = ('仅代表此成员的本次已读范围。继续阅读仍使用 read_folder_member 和相同 member_path；'
            'overview 查看结构，text 读取文字，table 读取表格，visual 核对 PDF 原页或图片，images 读取实际列出的内嵌图片。')

    def _observe(self, member, metadata):
        state = self._member_states.setdefault(member, {})
        for field, key in (('unit_count', 'units'), ('page_count', 'pages'),
                           ('visual_asset_count', 'assets'), ('asset_count', 'assets')):
            if type(metadata.get(field)) is int:
                state[key] = metadata[field]
        if 'state' in metadata and canonical_suffix(PurePosixPath(member).suffix) not in _IMAGES:
            state['parser_gaps'] = (list(metadata.get('notes', []))
                if metadata.get('gaps') or metadata['state'] in {'failed', 'partial'} else [])
            if (metadata.get('gaps') or metadata['state'] in {'failed', 'partial'}) and not state['parser_gaps']:
                state['parser_gaps'] = ['部分内容无法解析，需结合原件核对。']
        if 'visual_notes' in metadata:
            state['asset_gaps'] = list(metadata['visual_notes'])
        if metadata.get('images'):
            state['visual_gaps'] = list(metadata.get('notes', []))

    def _read_members(self):
        return {source['location']['member_path'] for source in self.sources}

    def _coverage_details(self):
        counts = {}
        for key in self._seen_evidence:
            parts = key.split(':', 3)
            if len(parts) != 4 or parts[1] != 'member':
                continue
            prefix = ':'.join(parts[:3]) + ':'
            values = counts.setdefault(prefix, [0, 0, 0])
            for index, kind in enumerate(('text:', 'visual:', 'embedded:')):
                values[index] += parts[3].startswith(kind)
        return {member: self._member_coverage(member, counts.get(self._prefix(member), [0, 0, 0]))
                for member in self._members or {}}

    def _member_coverage(self, member, values):
        state = self._member_states.get(member, {})
        suffix = canonical_suffix(PurePosixPath(member).suffix)
        totals = (0 if suffix in _IMAGES else state.get('units'),
                  0 if suffix in _PLAIN else state.get('pages'),
                  0 if suffix in _PLAIN | _IMAGES | {'.pdf'} else state.get('assets'))
        gaps = [*state.get('parser_gaps', []), *state.get('asset_gaps', []),
                *state.get('visual_gaps', [])]
        if state.get('size_gap'):
            gaps.append(state['size_gap'])
        for read, total, kind in zip(values, totals, ('原文单元', '原图页', '内嵌图片')):
            if total is not None and read < total:
                gaps.append(f'已读{kind} {read} / {total}，其余尚未阅读。')
        if suffix == '.pdf' and not values[1]:
            gaps.append('尚未核对 PDF 原页图形与版式。')
        if suffix in _OFFICE:
            gaps.append('未还原办公资料的整页布局、图表和样式，需结合原件核对。')
        if not any(values):
            gaps.append('尚未获得此成员的可用原文或图像依据。')
        elif any(total is None for total in totals):
            gaps.append('此成员部分内容的完整覆盖范围尚未核对。')
        return values, totals, list(dict.fromkeys(gaps))

    def coverage_gaps(self):
        return self._coverage_gaps(self._coverage_details())

    def _coverage_gaps(self, details):
        if self._members is None:
            return ['尚未读取文件夹已保存版本的成员清单。']
        if not self._members:
            return ['文件夹已保存版本中没有文件，无法形成原文依据。']
        gaps = []
        read_members = self._read_members()
        if len(read_members) < len(self._members):
            gaps.append(f'已读取成员 {len(read_members)} / {len(self._members)}，其余成员尚未阅读或无法识别。')
        for member, detail in details.items():
            for gap in detail[2]:
                if len(gaps) >= 29:
                    return [*gaps, '其余成员缺口未逐项列出，完整覆盖仍未完成。']
                gaps.append(f'{member}：{gap}'[:500])
        return gaps

    def coverage(self):
        by_member = self._coverage_details()
        details = list(by_member.values())
        counts = [sum(item[0][index] for item in details) for index in range(3)]
        totals = [sum(item[1][index] for item in details)
                  if details and all(item[1][index] is not None for item in details) else None
                  for index in range(3)]
        gaps = self._coverage_gaps(by_member)
        return {'read_units': counts[0], 'total_units': totals[0],
            'visual_pages': counts[1], 'total_visual_pages': totals[1],
            'embedded_images': counts[2], 'total_embedded_images': totals[2],
            'read_members': len(self._read_members()), 'total_members': len(self._members or {}),
            'gaps': gaps, 'complete': bool(details) and not gaps
                and all(total is not None and read >= total for read, total in zip(counts, totals))}
