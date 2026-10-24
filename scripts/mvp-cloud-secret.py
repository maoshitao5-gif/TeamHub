"""为本机云端启动器提供持久随机签名密钥；仅由父进程接收 stdout。"""
import os
from pathlib import Path
import re
import secrets
import stat
import sys


def load_or_create(path: Path) -> str:
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "r", encoding="ascii") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ValueError("密钥路径必须是普通文件")
            value = source.read(129).strip()
            if re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ValueError("已有本机签名密钥无效，请检查配置；不会自动替换")
            os.fchmod(source.fileno(), 0o600)
            return value
    with os.fdopen(fd, "w", encoding="ascii") as output:
        value = secrets.token_hex(32)
        output.write(value + "\n")
        output.flush()
        os.fsync(output.fileno())
    return value


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise ValueError("缺少本机密钥文件路径")
        print(load_or_create(Path(sys.argv[1])))
    except (OSError, UnicodeError, ValueError):
        print("无法读取或创建本机云端签名密钥，请检查文件权限与配置。", file=sys.stderr)
        raise SystemExit(1)
