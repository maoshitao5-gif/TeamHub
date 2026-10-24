"""Isolated, read-only libarchive decoder. Receives a verified inherited FD only.

No archive pathname or output directory is accepted. All files are streamed into
bounded memory, including inventory validation; nothing is extracted to disk.
"""
import base64
import ctypes
import json
import os
import resource
import stat
import sys
import unicodedata
import zipfile

FLAG = '--teamhub-archive-read'
MAX_ENTRIES = 10_000
MAX_EXPANDED = 256 * 1024 * 1024
MAX_MEMBER = 32 * 1024 * 1024
MAX_OUTPUT = 48 * 1024 * 1024
CHUNK = 256 * 1024


class ArchiveReadError(ValueError):
    pass


def _safe_path(value):
    return (isinstance(value, str) and 1 <= len(value) <= 4000
            and not any(char in value for char in '\\:')
            and not any(unicodedata.category(char).startswith('C') for char in value)
            and all(part not in {'', '.', '..'} for part in value.split('/')))


def _load():
    candidates = (['/usr/lib/libarchive.2.dylib'] if sys.platform == 'darwin'
                  else ['libarchive.so.13', 'libarchive.so'] if sys.platform.startswith('linux')
                  else [])
    for name in candidates:
        try:
            lib = ctypes.CDLL(name)
            break
        except OSError:
            continue
    else:
        raise ArchiveReadError('当前运行环境未提供安全的压缩包解码器，请使用已打包的桌面应用或将资料解压后收录。')
    signatures = {
        'archive_read_new': (ctypes.c_void_p, []),
        'archive_read_open_fd': (ctypes.c_int, [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t]),
        'archive_read_next_header': (ctypes.c_int, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]),
        'archive_read_data': (ctypes.c_ssize_t, [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]),
        'archive_read_data_skip': (ctypes.c_int, [ctypes.c_void_p]),
        'archive_read_free': (ctypes.c_int, [ctypes.c_void_p]),
        'archive_error_string': (ctypes.c_char_p, [ctypes.c_void_p]),
        'archive_entry_pathname_utf8': (ctypes.c_char_p, [ctypes.c_void_p]),
        'archive_entry_size': (ctypes.c_int64, [ctypes.c_void_p]),
        'archive_entry_size_is_set': (ctypes.c_int, [ctypes.c_void_p]),
        'archive_entry_filetype': (ctypes.c_uint, [ctypes.c_void_p]),
        'archive_entry_symlink': (ctypes.c_char_p, [ctypes.c_void_p]),
        'archive_entry_hardlink': (ctypes.c_char_p, [ctypes.c_void_p]),
        'archive_entry_sparse_count': (ctypes.c_int, [ctypes.c_void_p]),
        'archive_format_name': (ctypes.c_char_p, [ctypes.c_void_p]),
        'archive_filter_code': (ctypes.c_int, [ctypes.c_void_p, ctypes.c_int]),
    }
    for name, (restype, argtypes) in signatures.items():
        function = getattr(lib, name)
        function.restype, function.argtypes = restype, argtypes
    return lib


def _error(lib, archive):
    raw = lib.archive_error_string(archive)
    return raw.decode('utf-8', 'replace')[:200] if raw else '无法读取归档数据'


def _configure(lib, archive, kind):
    formats = {'zip': ['zip'], 'tar': ['tar'], 'tar.gz': ['tar'], 'tar.bz2': ['tar'],
               'tar.xz': ['tar'], '7z': ['7zip'], 'rar': ['rar', 'rar5'],
               'gz': ['raw'], 'bz2': ['raw'], 'xz': ['raw']}
    for item in formats[kind]:
        function = getattr(lib, f'archive_read_support_format_{item}', None)
        if function is not None:
            function.argtypes, function.restype = [ctypes.c_void_p], ctypes.c_int
            function(archive)
    for item in ('none', 'gzip', 'bzip2', 'xz'):
        function = getattr(lib, f'archive_read_support_filter_{item}')
        function.argtypes, function.restype = [ctypes.c_void_p], ctypes.c_int
        function(archive)


def _validate_zip_headers(fd, parameters):
    """Catch NUL truncation and type flags before libarchive exposes C strings.

    Python's central-directory allocation stays inside this disposable process,
    covered by the parent's RSS watchdog and the worker's resource limits.
    """
    with os.fdopen(os.dup(fd), 'rb') as source, zipfile.ZipFile(source) as archive:
        entries, names, total = archive.infolist(), set(), 0
        if len(entries) > parameters['max_entries']:
            raise ArchiveReadError('压缩包条目数量超过安全上限。')
        for entry in entries:
            path = entry.filename.rstrip('/') if entry.is_dir() else entry.filename
            mode = (entry.external_attr >> 16) & 0o170000
            if (entry.orig_filename != entry.filename or not _safe_path(path)
                    or mode not in {0, stat.S_IFDIR, stat.S_IFREG}):
                raise ArchiveReadError('压缩包包含不安全路径、链接或特殊文件。')
            key = unicodedata.normalize('NFC', path).casefold()
            if key in names:
                raise ArchiveReadError('压缩包路径重复或存在大小写冲突。')
            names.add(key)
            total += entry.file_size
            if total > parameters['max_expanded']:
                raise ArchiveReadError('压缩包声明的展开大小超过安全上限。')
    os.lseek(fd, 0, os.SEEK_SET)


def decode(parameters):
    required = {'fd', 'kind', 'member', 'standalone_name', 'max_entries', 'max_expanded', 'max_member'}
    if not isinstance(parameters, dict) or set(parameters) != required:
        raise ArchiveReadError('压缩包读取参数无效。')
    fd, kind, member = parameters['fd'], parameters['kind'], parameters['member']
    if (type(fd) is not int or fd < 3 or kind not in {'zip', 'tar', 'tar.gz', 'tar.bz2', 'tar.xz', '7z', 'rar', 'gz', 'bz2', 'xz'}
            or (member is not None and not _safe_path(member))):
        raise ArchiveReadError('压缩包读取范围无效。')
    for key, maximum in (('max_entries', MAX_ENTRIES), ('max_expanded', MAX_EXPANDED), ('max_member', MAX_MEMBER)):
        if type(parameters[key]) is not int or not 1 <= parameters[key] <= maximum:
            raise ArchiveReadError('压缩包读取上限无效。')
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        raise ArchiveReadError('压缩包快照不是普通文件。')
    os.lseek(fd, 0, os.SEEK_SET)
    if kind == 'zip':
        _validate_zip_headers(fd, parameters)
    lib = _load()
    archive = lib.archive_read_new()
    if not archive:
        raise ArchiveReadError('压缩包解码器无法初始化。')
    inventory, names, total, expanded = [], set(), 0, 0
    buffer = ctypes.create_string_buffer(CHUNK)
    try:
        _configure(lib, archive, kind)
        if lib.archive_read_open_fd(archive, fd, CHUNK) != 0:
            raise ArchiveReadError('压缩包格式损坏、不匹配或解码器不支持：' + _error(lib, archive))
        entry = ctypes.c_void_p()
        while True:
            status = lib.archive_read_next_header(archive, ctypes.byref(entry))
            if status == 1:  # ARCHIVE_EOF
                break
            if status != 0:
                raise ArchiveReadError('压缩包头信息损坏或不可读取：' + _error(lib, archive))
            expected_filter = {'gz': 1, 'tar.gz': 1, 'bz2': 2, 'tar.bz2': 2, 'xz': 6, 'tar.xz': 6}.get(kind)
            if expected_filter is not None and lib.archive_filter_code(archive, 0) != expected_filter:
                raise ArchiveReadError('压缩包实际压缩格式与已保存的文件类型不一致。')
            if len(inventory) >= parameters['max_entries']:
                raise ArchiveReadError('压缩包条目数量超过安全上限。')
            raw = lib.archive_entry_pathname_utf8(entry)
            if raw is None:
                raise ArchiveReadError('压缩包成员路径编码不可识别。')
            try:
                path = raw.decode('utf-8')
            except UnicodeError:
                raise ArchiveReadError('压缩包成员路径编码不可识别。') from None
            if kind in {'gz', 'bz2', 'xz'}:
                path = parameters['standalone_name']
            filetype = lib.archive_entry_filetype(entry)
            is_dir = filetype == stat.S_IFDIR
            path = path.rstrip('/') if is_dir else path
            if (not _safe_path(path) or lib.archive_entry_symlink(entry)
                    or lib.archive_entry_hardlink(entry) or lib.archive_entry_sparse_count(entry)
                    or filetype not in {stat.S_IFREG, stat.S_IFDIR}):
                raise ArchiveReadError('压缩包包含不安全路径、链接、稀疏文件或特殊文件。')
            key = unicodedata.normalize('NFC', path).casefold()
            if key in names:
                raise ArchiveReadError('压缩包路径重复或存在大小写冲突。')
            names.add(key)
            declared = lib.archive_entry_size(entry) if lib.archive_entry_size_is_set(entry) else None
            if declared is not None:
                if declared < 0:
                    raise ArchiveReadError('压缩包成员声明的大小无效。')
                total += declared
                if total > parameters['max_expanded']:
                    raise ArchiveReadError('压缩包声明的展开大小超过安全上限。')
            actual, payload, note = 0, bytearray(), ''
            while True:
                count = lib.archive_read_data(archive, buffer, CHUNK)
                if count == 0:
                    break
                if count < 0:
                    detail = _error(lib, archive)
                    lowered = detail.casefold()
                    if (any(word in lowered for word in ('passphrase', 'password', 'encrypt'))
                            or ('compress' in lowered and 'support' in lowered)):
                        note = '此成员加密或压缩方法不受支持，本次未读取正文。'
                        if lib.archive_read_data_skip(archive) < 0:
                            raise ArchiveReadError('压缩包包含不可跳过的加密成员，请解密后重新收录。')
                        break
                    raise ArchiveReadError('压缩包内容校验失败或数据损坏：' + detail)
                actual += count
                expanded += count
                if expanded > parameters['max_expanded']:
                    raise ArchiveReadError('压缩包实际展开大小超过安全上限。')
                if member == path and not is_dir:
                    if actual > parameters['max_member']:
                        raise ArchiveReadError('该成员超过 32 MiB 单个成员读取上限。')
                    payload.extend(buffer.raw[:count])
            if declared is not None and actual != declared and not note:
                raise ArchiveReadError('压缩包成员实际大小与声明不符。')
            inventory.append({'member_path': path, 'size': declared if note and declared is not None else actual,
                              'is_dir': is_dir, 'note': note})
            if member == path:
                if note:
                    raise ArchiveReadError(note)
                return {'member_path': path, 'size': actual, 'data': base64.b64encode(payload).decode('ascii')}
        files = {unicodedata.normalize('NFC', item['member_path']).casefold()
                 for item in inventory if not item['is_dir']}
        for item in inventory:
            parts = unicodedata.normalize('NFC', item['member_path']).casefold().split('/')
            if any('/'.join(parts[:index]) in files for index in range(1, len(parts))):
                raise ArchiveReadError('压缩包中的文件路径与子目录冲突。')
        if member is not None:
            raise ArchiveReadError('成员不存在于已保存的压缩包内。')
        return {'members': inventory, 'expanded_bytes': expanded}
    finally:
        lib.archive_read_free(archive)


def main():
    try:
        index = sys.argv.index(FLAG)
        if index + 2 != len(sys.argv):
            raise ArchiveReadError('压缩包读取参数无效。')
        parameters = json.loads(sys.argv[index + 1])
        # Decoder C code stays outside the API process. The parent also enforces
        # a wall-clock timeout and reads only this bounded temporary output file.
        resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT, MAX_OUTPUT))
        # Darwin may reject address/data limits for its shared-cache mappings;
        # the parent independently watches resident memory and kills the worker.
        try:
            resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
        except (ValueError, OSError):
            pass
        result = decode(parameters)
        encoded = json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        if len(encoded) > MAX_OUTPUT:
            raise ArchiveReadError('压缩包读取结果超过安全上限。')
        sys.stdout.buffer.write(encoded)
        return 0
    except (ValueError, OSError, AttributeError, MemoryError, zipfile.BadZipFile, EOFError) as exc:
        message = str(exc) if isinstance(exc, ArchiveReadError) else '压缩包无法安全解码，请检查格式与运行环境。'
        sys.stdout.write(json.dumps({'error': message}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
