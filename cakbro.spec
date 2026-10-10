# -*- mode: python ; coding: utf-8 -*-
block_cipher = None

a = Analysis(
    ['cakbro.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['psutil', 'requests'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib', 'numpy', 'PIL', 'PyQt5', 'PySide2',
        # Pastikan tidak ada yang menarik `keyboard` atau `pynput`
        'keyboard', 'pynput',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name='CakBro',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                    # UPX dinonaktifkan — UPX sering trigger AV
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
