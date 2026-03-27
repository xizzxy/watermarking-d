# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for watermark-tool Python backend.

Hidden imports cover dynamic-import patterns used by:
  - blind_watermark (uses importlib internally)
  - cv2 / numpy      (C extensions)
  - uvicorn          (imports handlers by string)
  - anyio            (backend plugins loaded by name)
  - fastapi / starlette (middleware, routing)
"""

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

a = Analysis(
    ['server.py'],
    pathex=['.'],
    binaries=[],
    datas=collect_data_files('imageio_ffmpeg'),
    hiddenimports=[
        # blind_watermark
        'blind_watermark',
        'blind_watermark.blind_watermark',
        'blind_watermark.recover_from_img',
        'blind_watermark.pool',
        # numpy / cv2
        'numpy',
        'numpy.core._multiarray_umath',
        'cv2',
        # PIL
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'PIL.ImageFont',
        # fastapi / starlette
        'fastapi',
        'fastapi.middleware.cors',
        'starlette',
        'starlette.middleware.cors',
        'starlette.routing',
        'starlette.staticfiles',
        'starlette.responses',
        # uvicorn
        'uvicorn',
        'uvicorn.main',
        'uvicorn.config',
        'uvicorn.server',
        'uvicorn.loops',
        'uvicorn.loops.asyncio',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'uvicorn.lifespan.off',
        'uvicorn.logging',
        # anyio / sniffio
        'anyio',
        'anyio._backends._asyncio',
        'sniffio',
        # h11
        'h11',
        # python-multipart
        'multipart',
        # email / http (stdlib, sometimes missed)
        'email.mime.text',
        'email.mime.multipart',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'unittest', 'test'],
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
    name='server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX can corrupt numpy DLLs on Windows; keep off
    console=True,       # keep console so Electron can read stdout/stderr
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
