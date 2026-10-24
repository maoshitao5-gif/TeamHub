# -*- mode: python ; coding: utf-8 -*-
"""Hermes 独立服务；只冻结运行代码，不附带上游网页、插件目录或开发工具。"""

import os
from importlib import metadata
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

source = Path(os.environ['TEAMHUB_HERMES_SOURCE']).resolve(strict=True)
worker = Path(os.environ['TEAMHUB_HERMES_WORKER']).resolve(strict=True)
manifest = worker.with_name('upstream.json')
if not manifest.is_file():
    raise SystemExit('Hermes 上游版本清单缺失，请先完成源码准备。')


def package_modules(name):
    # 不用 collect_submodules：避免枚举期间导入 Hermes，触发用户配置、插件和工具发现。
    modules = []
    for file in (source / name).rglob('*.py'):
        relative = file.relative_to(source).with_suffix('')
        if any(part in {'tests', '__pycache__'} for part in relative.parts):
            continue
        parts = relative.parts[:-1] if relative.name == '__init__' else relative.parts
        if all(part.isidentifier() for part in parts):
            modules.append('.'.join(parts))
    return modules


hidden = ['run_agent', 'model_tools', 'toolsets', 'utils', 'hermes_bootstrap',
          'hermes_constants', 'hermes_constants_scratch', 'hermes_logging',
          'hermes_yaml', 'hermes_time', 'trajectory_compressor']
hidden += [file.stem for file in source.glob('hermes_state*.py')]
for package in ('agent', 'hermes_cli', 'tools', 'providers', 'pm', 'hermes_platform'):
    hidden += package_modules(package)

datas = [(str(source / 'LICENSE'), 'licenses/hermes-agent'), (str(manifest), '.')]
datas += collect_data_files('certifi')
for distribution in ('hermes-agent', 'openai', 'httpx', 'pydantic', 'truststore',
                     'requests', 'rich', 'python-dotenv', 'tenacity', 'ruamel.yaml',
                     'PyYAML', 'tiktoken', 'packaging', 'jinja2'):
    try:
        metadata.distribution(distribution)
    except metadata.PackageNotFoundError:
        continue
    datas += copy_metadata(distribution)

a = Analysis(
    [str(worker)],
    pathex=[str(source), str(worker.parent)],
    binaries=[],
    datas=datas,
    hiddenimports=sorted(set(hidden)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'pytest', 'IPython', 'notebook', 'matplotlib'],
    noarchive=False,
)

pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='teamhub-hermes',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
collect = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='hermes')
