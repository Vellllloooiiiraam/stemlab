# -*- mode: python ; coding: utf-8 -*-
"""Receita do PyInstaller para gerar o StemLab.exe (build 'onedir')."""
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

PROJ = Path(SPECPATH)

# ------------------------------------------------------------- recursos
datas = [
    (str(PROJ / "web"), "web"),
]

# ffmpeg portatil: o Demucs usa para ler e gravar o audio.
_bin = PROJ / "build_bin"
if _bin.is_dir():
    for _f in sorted(_bin.iterdir()):
        if _f.is_file():
            datas.append((str(_f), "bin"))

# Dados que o Demucs carrega em tempo de execucao (os .yaml dos modelos).
for _pkg in ("demucs", "sphn"):
    try:
        datas += collect_data_files(_pkg)
    except Exception:
        pass

# --------------------------------------------------- imports ocultos
# `app.main` entra porque o uvicorn o importa por texto ("app.main:app").
hiddenimports: list[str] = ["app.main"]

for _pkg in ("demucs", "sphn", "julius", "lameenc", "openunmix"):
    try:
        hiddenimports += collect_submodules(_pkg)
    except Exception:
        pass

hiddenimports += [
    "einops", "safetensors", "yaml", "tqdm",
    "huggingface_hub", "hf_xet", "torchaudio", "soundfile",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]

a = Analysis(
    ["run.py"],
    pathex=[str(PROJ)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="StemLab",
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
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="StemLab",
)
