# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification file for Momentum Swing Terminal Backend Server.

Packages server.py and its dependencies (engine, FastAPI, Uvicorn, websockets, httpx, certifi)
into a standalone macOS binary bundle using --onedir mode for low startup latency and
compatibility with Apple Hardened Runtime notarization.
"""

import os
import sys
from pathlib import Path

import certifi
from PyInstaller.utils.hooks import collect_data_files

# Determine project root directory
spec_dir = Path(globals().get("SPECPATH", Path.cwd())).resolve()

# Explicitly collect TLS root CA bundle from certifi to avoid SSLCertVerificationError on fresh macOS systems
certifi_cacert = certifi.where()
datas = [
    (certifi_cacert, "certifi"),
]
# Collect any additional package data for certifi
for src, dst in collect_data_files("certifi"):
    if (src, dst) not in datas:
        datas.append((src, dst))

# Include static assets (HTML, CSS, JS terminal client)
static_dir = spec_dir / "static"
if static_dir.exists():
    datas.append((str(static_dir), "static"))

# Explicitly declared hidden imports for Uvicorn, FastAPI, Asyncio, Engine, and Websockets
hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops.asyncio",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "websockets",
    "websockets.legacy",
    "websockets.legacy.server",
    "fastapi",
    "fastapi.staticfiles",
    "starlette.middleware.base",
    "starlette.middleware.cors",
    "engine.protocol",
    "engine.client",
    "engine.aggregator",
    "engine.storage",
    "engine.providers",
    "engine.screener",
    "engine.market_scanner",
    "engine.market_time",
    "engine.pine_engine",
    "api",
    "api.deps",
    "api.history",
    "api.symbols",
    "api.ai",
    "api.screener",
    "api.state",
    "api.ws",
    "zoneinfo",
    "sqlite3",
    "certifi",
    "httpx",
    "httpcore",
]

# Configurable bundle name (default: server-sidecar, or architecture-specific e.g. server-aarch64-apple-darwin)
bundle_name = os.environ.get("SIDECAR_BUNDLE_NAME", "server-sidecar")

# macOS Hardened Runtime entitlements file
entitlements_path = spec_dir / "entitlements.plist"
entitlements_file_arg = str(entitlements_path) if entitlements_path.exists() else None

block_cipher = None

a = Analysis(
    [str(spec_dir / "server.py")],
    pathex=[str(spec_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "scipy",
        "pandas",
        "IPython",
        "jupyter",
        "unittest",
        "PIL",
        "Pillow",
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
    [],
    exclude_binaries=True,
    name="server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX breaks macOS code signing and Apple Silicon Mach-O binaries
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=entitlements_file_arg,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=bundle_name,
)
