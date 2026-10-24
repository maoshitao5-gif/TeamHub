"""Read fixed archive members through the same evidence tools as folder objects."""
import base64
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata

from .archive_formats import archive_kind, standalone_member_name
from .folder_reader import FolderReader, _identity
from .partner_tools import MAX_INPUT_BYTES, PartnerTools, _valid_member_path
from .object_skills import object_kind

MAX_ARCHIVE_ENTRIES = 10_000
MAX_ARCHIVE_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_OUTPUT_BYTES = 48 * 1024 * 1024
_ERROR = '压缩包已保存快照不可读取、归档结构不安全或校验失败，请重新保存版本后重试。'


def _resident_memory(pid):
    """Observe the disposable decoder without reading its files or contents."""
    if sys.platform == 'darwin':
        library = ctypes.CDLL('/usr/lib/libproc.dylib')
        function = library.proc_pidinfo
        function.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
        function.restype = ctypes.c_int
        buffer = ctypes.create_string_buffer(96)  # struct proc_taskinfo
        if function(pid, 4, 0, buffer, len(buffer)) >= 16:
            return ctypes.c_uint64.from_buffer(buffer, 8).value
    elif sys.platform.startswith('linux'):
        with open(f'/proc/{pid}/statm', 'r', encoding='ascii') as stream:
            return int(stream.read(200).split()[1]) * os.sysconf('SC_PAGE_SIZE')
    return None


class ArchiveReader(FolderReader):
    """One archive document/version; members are derived evidence, never documents."""

    def __init__(self, **kwargs):
        PartnerTools.__init__(self, **kwargs)
        if len(self.scope) != 1 or next(iter(self.scope.values())).get('is_folder') is not False:
            raise ValueError('压缩包阅读需要明确授权的一份压缩资料。')
        self.document_id = next(iter(self.scope))
        with self._session() as db:
            _, version = self._document(db, self.document_id)
            self._original_filename = version.original_filename
        self.kind = archive_kind(self._original_filename)
        if self.kind is None:
            raise ValueError('此对象的已保存版本不是受支持的压缩包类型。')
        self._member_path = None
        self._archive_digest = None
        self._verified_identity = None
        self._members = None
        self._member_states = {}
        self._prefixes = {}

    def _decode(self, source, member=None):
        from .archive_native import FLAG
        parameters = json.dumps({'fd': source.fileno(), 'kind': self.kind, 'member': member,
            'standalone_name': standalone_member_name(self._original_filename),
            'max_entries': MAX_ARCHIVE_ENTRIES, 'max_expanded': MAX_ARCHIVE_EXPANDED_BYTES,
            'max_member': MAX_INPUT_BYTES}, ensure_ascii=False)
        command = ([sys.executable, FLAG, parameters] if getattr(sys, 'frozen', False)
                   else [sys.executable, '-I', str(Path(__file__).with_name('archive_native.py')), FLAG, parameters])
        environment = {'PATH': os.defpath, 'PYTHONIOENCODING': 'utf-8'}
        for key in ('SYSTEMROOT', 'WINDIR', 'TMP', 'TEMP', 'TMPDIR'):
            if key in os.environ:
                environment[key] = os.environ[key]
        self._check()
        with tempfile.TemporaryDirectory(prefix='teamhub-archive-') as working, tempfile.TemporaryFile() as output:
            try:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output,
                    stderr=subprocess.DEVNULL, pass_fds=(source.fileno(),), close_fds=True,
                    cwd=working, env=environment)
            except OSError:
                raise ValueError('压缩包安全解码进程无法启动，请检查本机安装。') from None
            deadline = time.monotonic() + 30
            try:
                while True:
                    self._check()
                    try:
                        resident = _resident_memory(process.pid)
                    except (OSError, ValueError, IndexError, AttributeError):
                        resident = None  # A process may exit between poll and observation.
                    if resident is not None and resident > 768 * 1024 * 1024:
                        raise ValueError('压缩包解码内存超过安全上限，请将资料分成较小的包。')
                    if os.fstat(output.fileno()).st_size > MAX_ARCHIVE_OUTPUT_BYTES:
                        raise ValueError('压缩包读取输出超过安全上限。')
                    if time.monotonic() >= deadline:
                        raise ValueError('压缩包安全解码超过 30 秒上限，请将资料分成较小的包。')
                    try:
                        process.wait(timeout=0.1)
                        break
                    except subprocess.TimeoutExpired:
                        pass
                output.seek(0)
                raw = output.read(MAX_ARCHIVE_OUTPUT_BYTES + 1)
                if len(raw) > MAX_ARCHIVE_OUTPUT_BYTES:
                    raise ValueError('压缩包读取输出超过安全上限。')
                try:
                    result = json.loads(raw)
                except (ValueError, UnicodeError):
                    raise ValueError('压缩包无法安全解码，可能已损坏或超过资源上限。') from None
                if not isinstance(result, dict):
                    raise ValueError('压缩包安全解码结果无效。')
                if result.get('error'):
                    raise ValueError(str(result['error'])[:500])
                if process.returncode != 0:
                    raise ValueError('压缩包无法安全解码，可能已损坏或超过资源上限。')
                return result
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()

    @contextmanager
    def _archive(self, document_id):
        self._assert_current(document_id)
        with self._session() as db:
            doc, version = self._document(db, document_id)
            digest, filename = version.sha256_hash, version.file_path
            if (doc.is_folder or not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest)
                    or version.original_filename != self._original_filename):
                raise ValueError(_ERROR)
        relative = Path('.teamhub') / 'objects' / digest[:2] / digest
        expected = {Path(self.root) / relative, Path(self.configured_root) / relative}
        if (not isinstance(filename, str) or Path(filename) not in expected
                or self._archive_digest not in {None, digest}):
            raise ValueError(_ERROR)
        descriptors = []
        try:
            flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, 'O_NOFOLLOW', 0)
            descriptors.append(os.open(self.root, flags))
            for part in ('.teamhub', 'objects', digest[:2]):
                descriptors.append(os.open(part, flags, dir_fd=descriptors[-1]))
            fd = os.open(digest, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | os.O_NONBLOCK,
                         dir_fd=descriptors[-1])
            with os.fdopen(fd, 'rb') as source:
                before = os.fstat(source.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise ValueError(_ERROR)
                unchanged = self._verified_identity == _identity(before)
                if not unchanged:
                    if (self._archive_hash(source) != digest
                            or _identity(before) != _identity(os.fstat(source.fileno()))):
                        raise ValueError('压缩包快照校验失败或已变化。')
                    result = self._decode(source)
                    self._members = self._validated_members(result)
                self._verified_identity = _identity(before)
                self._archive_digest = digest
                yield source
                if _identity(before) != _identity(os.fstat(source.fileno())):
                    raise ValueError('压缩包快照在读取期间发生变化。')
            self._assert_current(document_id)
        except (OSError, EOFError):
            raise ValueError(_ERROR) from None
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def _validated_members(self, result):
        entries = result.get('members')
        if not isinstance(entries, list) or len(entries) > MAX_ARCHIVE_ENTRIES:
            raise ValueError('压缩包成员清单无效或超过上限。')
        members, names, total = {}, set(), 0
        for entry in entries:
            if (not isinstance(entry, dict) or set(entry) != {'member_path', 'size', 'is_dir', 'note'}
                    or not _valid_member_path(entry['member_path']) or type(entry['size']) is not int
                    or entry['size'] < 0 or type(entry['is_dir']) is not bool
                    or not isinstance(entry['note'], str) or len(entry['note']) > 500):
                raise ValueError('压缩包成员清单无效。')
            path = entry['member_path']
            key = unicodedata.normalize('NFC', path).casefold()
            if key in names:
                raise ValueError('压缩包成员路径重复。')
            names.add(key)
            total += entry['size']
            if total > MAX_ARCHIVE_EXPANDED_BYTES:
                raise ValueError('压缩包展开大小超过安全上限。')
            if not entry['is_dir']:
                members[path] = {'member_path': path, 'size': entry['size'],
                    'type': PurePosixPath(path).suffix.lower() or 'unknown',
                    'object_kind': object_kind(path), 'archive': archive_kind(path) is not None,
                    'note': entry['note']}
        files = {unicodedata.normalize('NFC', name).casefold() for name in members}
        for path in names:
            parts = path.split('/')
            if any('/'.join(parts[:index]) in files for index in range(1, len(parts))):
                raise ValueError('压缩包文件路径与子目录冲突。')
        return dict(sorted(members.items()))

    def preflight(self):
        with self._archive(self.document_id):
            pass
        if not self._members:
            raise ValueError('此压缩包的已保存版本中没有文件，暂不能生成有原文依据的 AI 整理方案。')
        if not any(not entry['note'] and entry['size'] <= MAX_INPUT_BYTES
                   and not entry['archive'] for entry in self._members.values()):
            raise ValueError('此压缩包没有可直接读取的成员；可能已加密、仅含嵌套压缩包或单个成员过大，请解压后收录。')

    def list_archive_members(self, document_id, offset=0, limit=100):
        result = super().list_folder_members(document_id, offset=offset, limit=limit)
        for entry in result['members']:
            archive_note = self._members[entry['member_path']]['note']
            if archive_note:
                entry['note'] = archive_note
            if entry['note'] or entry['archive']:
                entry['readable'] = False
            if entry['archive']:
                entry['note'] = '嵌套压缩包本轮不递归展开；需要解压后收录或单独收录内部资料。'
        result['archive_kind'] = self.kind
        result['note'] = ('这是已保存版本的压缩包成员清单，不是已读正文。'
            '请按 member_path 使用 read_archive_member 阅读成员；压缩包和内部结构均不会被修改。')
        return result

    def _snapshot(self, document_id):
        member = self._member_path
        if member is None:
            raise ValueError('请使用 read_archive_member 指定清单内的成员。')
        with self._archive(document_id) as source:
            entry = self._members.get(member)
            if entry is None:
                raise ValueError('成员不存在于本次授权的压缩包已保存版本中。')
            if entry['archive']:
                raise ValueError('嵌套压缩包本轮不递归展开，请解压后收录。')
            if entry['size'] > MAX_INPUT_BYTES:
                raise ValueError('该成员超过 32 MiB 单个成员读取上限，本次未读取内容。')
            if entry['note']:
                raise ValueError(entry['note'])
            result = self._decode(source, member)
            try:
                data = base64.b64decode(result['data'], validate=True)
            except (KeyError, ValueError, TypeError):
                raise ValueError('压缩包成员内容无效。') from None
            if (result.get('member_path') != member or result.get('size') != entry['size']
                    or len(data) != entry['size'] or len(data) > MAX_INPUT_BYTES):
                raise ValueError('压缩包成员大小与清单不一致或超过上限。')
        self.exposed_document_ids.add(document_id)
        return data, member

    def read_archive_member(self, document_id, member_path, action='overview', offset=0, limit=60,
                            sheet=None, start_row=1, row_count=80, pages=None, image_ids=None):
        return super().read_folder_member(document_id, member_path, action=action, offset=offset,
            limit=limit, sheet=sheet, start_row=start_row, row_count=row_count,
            pages=pages, image_ids=image_ids)

    def _member_metadata(self, member, action, metadata):
        super()._member_metadata(member, action, metadata)
        metadata['note'] = metadata['note'].replace('read_folder_member', 'read_archive_member')
        metadata['object_kind'] = object_kind(member)
        metadata['archive_kind'] = self.kind

    def _member_coverage(self, member, values):
        read, totals, gaps = super()._member_coverage(member, values)
        entry = (self._members or {}).get(member, {})
        if entry.get('note'):
            gaps.append(entry['note'])
        if entry.get('archive'):
            gaps.append('嵌套压缩包尚未展开或阅读。')
        if entry.get('size', 0) > MAX_INPUT_BYTES:
            gaps.append('成员超过 32 MiB 单个成员读取上限。')
        return read, totals, list(dict.fromkeys(gaps))

    def _coverage_gaps(self, details):
        return [gap.replace('文件夹', '压缩包') for gap in super()._coverage_gaps(details)]
