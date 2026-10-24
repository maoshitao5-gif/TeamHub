# -*- mode: python ; coding: utf-8 -*-

"""
TeamHub Backend PyInstaller 打包配置
用于将 FastAPI 后端打包为独立的可执行文件
"""

block_cipher = None

from PyInstaller.utils.hooks import collect_all, collect_data_files, copy_metadata

# PDFium 的原生库和 SDK 的发布元数据必须随冻结后端交付。
pdfium_datas, pdfium_binaries, pdfium_hidden = collect_all('pypdfium2_raw')
partner_datas = collect_data_files('pypdfium2')
for distribution in ('pypdfium2', 'Pillow', 'httpx2', 'httpcore2'):
    partner_datas += copy_metadata(distribution)

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=pdfium_binaries,
    datas=[
        ('backend/app/skills', 'backend/app/skills'),
    ] + partner_datas + pdfium_datas,
    hiddenimports=[
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'click',
        'h11',
        'httptools',
        'websockets',
        'sqlalchemy',
        'pydantic',
        'pydantic_settings',
        'pypdfium2',
        'PIL.PngImagePlugin',
        'PIL.JpegImagePlugin',
        'PIL.GifImagePlugin',
        'PIL.WebPImagePlugin',
        'PIL.BmpImagePlugin',
        'PIL.TiffImagePlugin',
    ] + pdfium_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'numpy',
        'pandas',
        'pytest',
        'setuptools',
        # 历史 SDK 仅在 tests/support 作回归对照，生产采用独立 Hermes。
        'pydantic_ai',
        'pydantic_graph',
        'genai_prices',
        'openai',
        'oss2',
        'boto3',
        'botocore',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='teamhub-backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # 不显示控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
