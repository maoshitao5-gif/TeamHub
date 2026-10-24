"""隔离验证启动密钥、暂存容量/有效期和不可信压缩数据。"""
import asyncio
import hashlib
import io
import os
from pathlib import Path
import secrets
import struct
import subprocess
import sys
import time
import zipfile

import pytest

from app.config import CloudSettings, settings
from app.core import object_store


@pytest.mark.parametrize("secret", [
    "", "short", "dev-secret-key-change-in-production-min32chars",
    "replace-with-a-random-secret-at-least-32-characters",
])
def test_runtime_rejects_missing_default_and_example_signing_keys(secret):
    config = CloudSettings(_env_file=None, jwt_secret_key=secret)
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        config.validate_runtime_security()


def test_runtime_accepts_independent_random_signing_key():
    CloudSettings(_env_file=None, jwt_secret_key=secrets.token_hex(32)).validate_runtime_security()


def test_invalid_signing_key_stops_application_before_database_startup(monkeypatch):
    from fastapi.testclient import TestClient
    import main

    monkeypatch.setattr(settings, "jwt_secret_key", "")
    monkeypatch.setattr(main, "init_db", lambda: pytest.fail("不安全启动不能初始化数据库"))
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        with TestClient(main.app):
            pass


@pytest.fixture
def staging(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    monkeypatch.setattr(settings, "staging_max_bytes", 10_000)
    monkeypatch.setattr(settings, "staging_max_uploads", 10)
    monkeypatch.setattr(settings, "staging_ttl_seconds", 60)
    return tmp_path / "staging"


def upload(data):
    async def stream():
        yield data
    return asyncio.run(object_store.save_upload_stream(
        stream(), workspace_id="synthetic-workspace", uploader_id="synthetic-user", filename="sample.bin"))


def test_staging_total_budget_rejects_stream_and_removes_partial_content(staging):
    first = upload(b"x" * 5000)
    with pytest.raises(object_store.StagingCapacityError):
        upload(b"y" * 5000)
    assert [p.name for p in staging.glob("*.bin")] == [first["upload_id"] + ".bin"]
    assert object_store.load_staging(first["upload_id"])["file_size"] == 5000


def test_stream_checks_actual_cumulative_bytes_without_content_length(staging):
    async def stream():
        yield b"a" * 4000
        yield b"b" * 4000
    with pytest.raises(object_store.StagingCapacityError):
        asyncio.run(object_store.save_upload_stream(stream(), workspace_id="w", uploader_id="u",
                                                   filename="stream.bin"))
    assert list(staging.glob("*.bin")) == []
    assert list(staging.glob("*.json")) == []


def test_zero_byte_uploads_are_bounded_by_entry_limit(staging, monkeypatch):
    monkeypatch.setattr(settings, "staging_max_uploads", 1)
    first = upload(b"")
    with pytest.raises(object_store.StagingCapacityError):
        upload(b"")
    assert object_store.load_staging(first["upload_id"]) is not None


def test_expired_staging_cleanup_preserves_objects_and_recent_retry(staging):
    old = upload(b"old")
    digest = hashlib.sha256(b"old").hexdigest()
    object_store.promote_staging(old["upload_id"], digest)
    recent = upload(b"recent")
    for path in staging.glob(old["upload_id"] + ".*"):
        os.utime(path, (time.time() - 120, time.time() - 120))
    upload(b"new")
    assert object_store.load_staging(old["upload_id"]) is None
    assert object_store.load_staging(recent["upload_id"]) is not None
    assert object_store.object_path(digest).read_bytes() == b"old"


def test_overlapping_streams_share_the_same_staging_budget(staging):
    async def both():
        started = asyncio.Event()
        proceed = asyncio.Event()

        async def slow_stream():
            yield b"a" * 5000
            started.set()
            await proceed.wait()

        task = asyncio.create_task(object_store.save_upload_stream(
            slow_stream(), workspace_id="synthetic-workspace", uploader_id="synthetic-user", filename="first.bin"))
        await started.wait()

        async def second_stream():
            yield b"b" * 5000
        try:
            with pytest.raises(object_store.StagingCapacityError):
                await object_store.save_upload_stream(second_stream(), workspace_id="other-workspace",
                                                       uploader_id="other-user", filename="second.bin")
        finally:
            proceed.set()
        return await task

    first = asyncio.run(both())
    assert (staging / (first["upload_id"] + ".bin")).stat().st_size == 5000


def test_expiry_never_deletes_an_active_upload(staging):
    async def exercise():
        async def active_stream():
            yield b"a"
            for path in staging.iterdir():
                if path.name != ".quota.lock":
                    os.utime(path, (time.time() - 120, time.time() - 120))

            async def other_stream():
                yield b"b"
            await object_store.save_upload_stream(
                other_stream(), workspace_id="w", uploader_id="u", filename="other.bin")
            yield b"c"
        return await object_store.save_upload_stream(
            active_stream(), workspace_id="w", uploader_id="u", filename="active.bin")

    result = asyncio.run(exercise())
    assert (staging / (result["upload_id"] + ".bin")).read_bytes() == b"ac"


def test_independent_processes_share_capacity_and_active_upload_lock(staging):
    root = staging.parent
    script = """
import asyncio, os, time
from pathlib import Path
from app.core import object_store
root = Path(os.environ['STORAGE_DIR'])
async def stream():
    yield b'x' * 5000
    for path in (root / 'staging').iterdir():
        if path.name != '.quota.lock':
            os.utime(path, (time.time() - 120, time.time() - 120))
    (root / 'ready').touch()
    deadline = time.monotonic() + 10
    while not (root / 'finish').exists():
        if time.monotonic() > deadline:
            raise RuntimeError('test process timed out')
        await asyncio.sleep(.01)
asyncio.run(object_store.save_upload_stream(stream(), workspace_id='w', uploader_id='u', filename='child.bin'))
"""
    env = {**os.environ, "STORAGE_DIR": str(root), "STAGING_MAX_BYTES": "10000",
           "STAGING_TTL_SECONDS": "60"}
    process = subprocess.Popen([sys.executable, "-c", script], cwd=str(root),
                               env={**env, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 10
        while not (root / "ready").exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert (root / "ready").exists()
        with pytest.raises(object_store.StagingCapacityError):
            upload(b"parent")
        (root / "finish").touch()
        _, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        assert len(list(staging.glob("*.bin"))) == 1
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=5)


def test_corrupt_deflate_is_a_storage_validation_error(tmp_path):
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("file.txt", b"synthetic content")
    broken = bytearray(content.getvalue())
    name_length, extra_length = struct.unpack_from("<HH", broken, 26)
    broken[30 + name_length + extra_length] = 255
    path = tmp_path / "malformed.zip"
    path.write_bytes(broken)
    with pytest.raises(object_store.StorageValidationError):
        object_store.inspect_folder(path)
