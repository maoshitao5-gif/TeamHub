#!/usr/bin/env python3
"""Build a source + static-web deployment archive without any personal data."""
from pathlib import Path
import hashlib
import json
import zipfile

ROOT = Path(__file__).resolve().parents[1]

def build():
    web = ROOT / 'frontend' / 'dist'
    for name in ('cloud.html', 'admin.html'):
        if not (web / name).is_file():
            raise SystemExit('请先运行 npm run frontend:build')
    selected = []
    for directory in ('app', 'alembic'):
        selected += [p for p in (ROOT / 'cloud_backend' / directory).rglob('*')
                     if p.is_file() and p.suffix in ('.py', '.mako') and '__pycache__' not in p.parts]
    for name in ('main.py', 'manage.py', 'provision_mvp.py', 'alembic.ini', 'requirements.txt', 'requirements-mvp-macos.lock', '.env.example'):
        selected.append(ROOT / 'cloud_backend' / name)
    target = ROOT / 'dist' / 'teamhub-cloud.zip'
    target.parent.mkdir(exist_ok=True)
    inventory = {}
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for source in sorted(selected):
            relative = str(source.relative_to(ROOT / 'cloud_backend'))
            if source.is_symlink() or not source.resolve().is_relative_to(ROOT):
                raise ValueError('部署包拒绝符号链接或工作区外文件')
            data = source.read_bytes()
            archive.writestr(relative, data)
            inventory[relative] = hashlib.sha256(data).hexdigest()
        for source in [web/'cloud.html', web/'admin.html', *sorted((web/'assets').rglob('*'))]:
            if source.is_file():
                relative = 'web/' + str(source.relative_to(web))
                if source.is_symlink() or not source.resolve().is_relative_to(ROOT):
                    raise ValueError('部署包拒绝符号链接或工作区外文件')
                data = source.read_bytes()
                archive.writestr(relative, data)
                inventory[relative] = hashlib.sha256(data).hexdigest()
        guide = ROOT/'docs/guides/云端与投递启动及迁移指引.md'
        if guide.is_symlink() or not guide.resolve().is_relative_to(ROOT):
            raise ValueError('部署指引不能是符号链接或工作区外文件')
        guide_bytes = guide.read_bytes()
        archive.writestr('部署指引.md', guide_bytes)
        inventory['部署指引.md'] = hashlib.sha256(guide_bytes).hexdigest()
        archive.writestr('manifest.json', json.dumps(inventory, ensure_ascii=False, indent=2))
    print(json.dumps({'archive':str(target),'files':len(inventory),'bytes':target.stat().st_size},ensure_ascii=False))

if __name__ == '__main__':
    build()
