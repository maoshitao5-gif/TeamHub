#!/usr/bin/env python3
"""
TeamHub MVP 维护者备份/恢复 CLI(M6,契约 §3.7)

先停止对应服务和外部编辑，传 --services-stopped；备份包含数据库、受管内容与摘要清单;
个人库配置备份排除凭据;恢复到全新空目录并校验。

用法(在仓库根目录,用本地后端 venv):
  python scripts/mvp-backup.py backup-personal <library_path> <backup_dir>
  python scripts/mvp-backup.py restore-personal <backup_dir> <target_library>
  python scripts/mvp-backup.py backup-cloud <db_path> <storage_dir> <backup_dir>
  python scripts/mvp-backup.py restore-cloud <backup_dir> <target_db> <target_storage>
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.core.backup import (  # noqa: E402
    backup_library, restore_library, backup_cloud, restore_cloud,
)


def main():
    p = argparse.ArgumentParser(description="TeamHub MVP 备份/恢复")
    sub = p.add_subparsers(dest="cmd", required=True)
    for kind in ("personal", "cloud"):
        b = sub.add_parser(f"backup-{kind}", help=f"备份{'个人' if kind == 'personal' else '共享'}库")
        r = sub.add_parser(f"restore-{kind}", help=f"恢复{'个人' if kind == 'personal' else '共享'}库")
        if kind == "personal":
            b.add_argument("library_path")
            b.add_argument("backup_dir")
            r.add_argument("backup_dir")
            r.add_argument("target_library")
        else:
            b.add_argument("db_path")
            b.add_argument("storage_dir")
            b.add_argument("backup_dir")
            r.add_argument("backup_dir")
            r.add_argument("target_db")
            r.add_argument("target_storage")

        for command in (b, r):
            command.add_argument('--services-stopped', action='store_true', required=True,
                                 help='确认对应服务及外部文件编辑已经停止；不会自动停止进程')

    args = p.parse_args()
    if args.cmd == "backup-personal":
        result = backup_library(args.library_path, args.backup_dir)
    elif args.cmd == "restore-personal":
        result = restore_library(args.backup_dir, args.target_library)
    elif args.cmd == "backup-cloud":
        result = backup_cloud(args.db_path, args.storage_dir, args.backup_dir)
    elif args.cmd == "restore-cloud":
        result = restore_cloud(args.backup_dir, args.target_db, args.target_storage)
    else:
        p.print_help()
        return
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
