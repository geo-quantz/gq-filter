# -*- mode: python ; coding: utf-8 -*-
import os
import sys
import subprocess
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

block_cipher = None

# Entry point
entry_point = os.path.abspath(os.path.join(os.getcwd(), 'cli.py'))

# Basic PDAL collection
datas = collect_data_files('pdal')
binaries = collect_dynamic_libs('pdal')

# Handle PDAL shared libraries and plugins on macOS
# PDAL on macOS (Conda or Homebrew) uses .dylib files. Bundle the base PDAL
# library and its plugins from ONE install only. Mixing them (e.g. conda's
# libpdalcpp with Homebrew's libpdal_plugin_*_arrow, which links Homebrew's
# libarrow) makes PDAL print plugin load errors when it scans its plugins, or
# breaks the bundle outright when the two PDAL versions differ.


def _pdal_dylibs(lib_dir):
    if not os.path.isdir(lib_dir):
        return []
    return [
        (os.path.join(lib_dir, f), '.')
        for f in sorted(os.listdir(lib_dir))
        if 'libpdal' in f and f.endswith('.dylib')
    ]


def _homebrew_lib():
    try:
        prefix = subprocess.check_output(['brew', '--prefix'], encoding='utf-8').strip()
    except Exception:
        return None
    return os.path.join(prefix, 'lib')


# 1. Conda environment (the supported build: scripts/build_macos.sh, CI)
conda_prefix = os.environ.get('CONDA_PREFIX')
pdal_dylibs = _pdal_dylibs(os.path.join(conda_prefix, 'lib')) if conda_prefix else []

# 2. Homebrew, only when no conda PDAL was found
if not pdal_dylibs:
    brew_lib = _homebrew_lib()
    if brew_lib:
        pdal_dylibs = _pdal_dylibs(brew_lib)

binaries += [b for b in pdal_dylibs if b[0] not in {src for src, _ in binaries}]

a = Analysis(
    [entry_point],
    pathex=[],
    binaries=binaries,
    datas=datas,
    # `gqfilter mcp` imports its server lazily; name it so the MCP SDK is always bundled.
    hiddenimports=['pdal', 'gqfilter_mcp.server', 'mcp.server', 'mcp.server.stdio', 'mcp_types'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='gqfilter',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='gqfilter',
)
