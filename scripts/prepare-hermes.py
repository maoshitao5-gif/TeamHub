#!/usr/bin/env python3
"""准备固定版本的独立 Hermes 源码和 Python 环境（开发/打包时执行）。

用户安装的 TeamHub 已携带冻结运行程序，不在首次使用时下载源码或依赖。
默认只在项目 .mvp-sandbox 中生成构建输入，不使用个人 Hermes 配置。
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / 'integrations' / 'hermes'
SANDBOX = ROOT / '.mvp-sandbox'
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_SOURCE_BYTES = 512 * 1024 * 1024
MAX_MEMBER_BYTES = 64 * 1024 * 1024


def digest_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _members(archive, prefix):
    records, total, seen = [], 0, set()
    for member in archive.getmembers():
        name = PurePosixPath(member.name)
        if (name.is_absolute() or not name.parts or name.parts[0] != prefix
                or '..' in name.parts or '\\' in member.name
                or not (member.isdir() or member.isfile())):
            raise ValueError('Hermes archive contains an unsafe or unexpected entry')
        if len(name.parts) == 1:
            if not member.isdir():
                raise ValueError('Hermes source root must be a directory')
            continue
        relative = Path(*name.parts[1:])
        if str(relative) in seen or not 0 <= member.size <= MAX_MEMBER_BYTES:
            raise ValueError('Hermes archive contains duplicate or oversized entries')
        seen.add(str(relative))
        total += member.size
        if total > MAX_SOURCE_BYTES:
            raise ValueError('Hermes archive exceeds the source capacity limit')
        records.append((member, relative))
    return records


def prepare_source(archive_path, destination, manifest):
    """验证压缩包后安全解包；已有源码必须逐文件匹配同一压缩包。"""
    archive_path, destination = Path(archive_path), Path(destination)
    if (archive_path.stat().st_size > MAX_ARCHIVE_BYTES
            or digest_file(archive_path) != manifest['sha256']):
        raise ValueError('Hermes source checksum mismatch; no source was installed')
    with tarfile.open(archive_path, 'r:gz') as archive:
        records = _members(archive, manifest['source_prefix'])
        if destination.exists():
            if destination.is_symlink() or not destination.is_dir():
                raise ValueError('Existing Hermes source is not a regular directory')
            expected_files = {relative for member, relative in records if member.isfile()}
            for target in destination.rglob('*'):
                if target.is_symlink():
                    raise ValueError('Existing Hermes source contains a symbolic link')
                if target.is_dir():
                    continue
                relative = target.relative_to(destination)
                if '__pycache__' in relative.parts and target.suffix == '.pyc' and target.is_file():
                    continue
                if not target.is_file() or relative not in expected_files:
                    raise ValueError('Existing Hermes source contains unexpected files; use a fresh source directory')
            for member, relative in records:
                target = destination / relative
                if target.is_symlink():
                    raise ValueError('Existing Hermes source contains a symbolic link')
                if member.isdir():
                    if not target.is_dir():
                        raise ValueError('Existing Hermes source does not match the pinned archive')
                    continue
                if not target.is_file() or target.stat().st_size != member.size:
                    raise ValueError('Existing Hermes source does not match the pinned archive')
                with archive.extractfile(member) as source:
                    expected = hashlib.sha256(source.read()).hexdigest()
                if digest_file(target) != expected:
                    raise ValueError('Existing Hermes source changed; use a fresh source directory')
            return destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='hermes-source-', dir=destination.parent) as temporary:
            staging = Path(temporary) / 'source'
            staging.mkdir()
            for member, relative in records:
                target = staging / relative
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.extractfile(member) as source, target.open('xb') as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                    target.chmod(0o755 if member.mode & 0o111 else 0o644)
            os.replace(staging, destination)
    return destination


def download_archive(url, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix='hermes-download-', dir=destination.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            request = Request(url, headers={'User-Agent': 'TeamHub-Hermes-Build/1'})
            with urlopen(request, timeout=30) as response:
                size = 0
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_ARCHIVE_BYTES:
                        raise ValueError('Hermes download exceeds capacity limit')
                    output.write(chunk)
            output.flush()
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)


def _run(arguments, env):
    subprocess.run([str(item) for item in arguments], check=True, env=env, cwd=ROOT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, default=SANDBOX / 'hermes-v0.22.2.tar.gz')
    parser.add_argument('--source', type=Path, default=SANDBOX / 'hermes-src')
    parser.add_argument('--venv', type=Path, default=SANDBOX / 'hermes-venv')
    parser.add_argument('--python', type=Path)
    parser.add_argument('--source-only', action='store_true')
    parser.add_argument('--offline', action='store_true', help='Use verified cached source and cached dependency wheels only')
    args = parser.parse_args()
    manifest = json.loads((PROFILE / 'upstream.json').read_text(encoding='utf-8'))
    SANDBOX.mkdir(exist_ok=True)
    if not args.archive.exists():
        if args.offline:
            parser.error('Pinned source archive is missing in offline mode')
        download_archive(manifest['archive_url'], args.archive)
    prepare_source(args.archive, args.source, manifest)
    print(f'Verified Hermes {manifest["version"]}: {args.source}')
    if args.source_only:
        return 0
    uv = shutil.which('uv')
    if not uv:
        parser.error('Build prerequisite missing: install uv before preparing the developer runtime')
    env = dict(os.environ, UV_CACHE_DIR=str(SANDBOX / 'uv-cache'),
               UV_PYTHON_INSTALL_DIR=str(SANDBOX / 'python'))
    if args.offline:
        env['UV_OFFLINE'] = '1'
    python = args.python
    if python is None:
        version = manifest['python_version']
        _run([uv, 'python', 'install', version], env)
        found = subprocess.run([uv, 'python', 'find', '--managed-python', version],
                               check=True, capture_output=True, text=True, env=env, cwd=ROOT)
        python = Path(found.stdout.strip())
    if not args.venv.exists():
        _run([uv, 'venv', '--python', python, args.venv], env)
    executable = args.venv / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    actual_version = subprocess.run([str(executable), '-I', '-c',
        'import platform; print(platform.python_version())'], check=True, capture_output=True, text=True)
    if actual_version.stdout.strip() != manifest['python_version']:
        parser.error(f'Hermes runtime requires Python {manifest["python_version"]}; existing environment differs')
    _run([uv, 'pip', 'sync', '--python', executable, '--require-hashes', PROFILE / 'requirements.lock'], env)
    print(f'Hermes runtime ready: {executable}')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, tarfile.TarError, subprocess.CalledProcessError) as error:
        print(f'Hermes preparation failed: {error}', file=sys.stderr)
        raise SystemExit(1)
