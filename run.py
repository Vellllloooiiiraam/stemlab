"""StemLab — inicializador. Sobe o servidor local e abre o navegador."""
from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

os.environ.setdefault("PYTHONUNBUFFERED", "1")

from app import separator  # noqa: E402
from app.config import HOST, PORT  # noqa: E402
from app.jobs import ensure_dirs, manager  # noqa: E402

BANNER = r"""
   ____  _                  _          _
  / ___|| |_ ___ _ __ ___  | |    __ _| |__
  \___ \| __/ _ \ '_ ` _ \ | |   / _` | '_ \
   ___) | ||  __/ | | | | || |__| (_| | |_) |
  |____/ \__\___|_| |_| |_|_____\__,_|_.__/
"""


def run_demucs(argv: list[str]) -> int:
    """Modo Demucs: repassa os argumentos para `demucs.separate`.

    O app chama o Demucs em um processo separado, então é aqui que o
    substituto do `ffprobe` é instalado (ver `app/separator.py`).
    """
    sys.argv = ["demucs.separate"] + list(argv)

    from app import separator

    separator.locate_ffmpeg()
    separator.install_ffprobe_compat()

    from demucs.separate import main as demucs_main

    demucs_main()
    return 0


def main() -> int:
    ensure_dirs()

    if not separator.locate_ffmpeg():
        print("  [aviso] ffmpeg não encontrado no PATH.")
        print("          Formatos como mp3/m4a podem falhar. Instale com:")
        print("          winget install Gyan.FFmpeg\n")

    manager.start()

    url = f"http://{HOST}:{PORT}/"
    print(BANNER)
    print(f"  Interface: {url}")
    print("  Encerre com Ctrl+C\n")

    threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=HOST,
        port=PORT,
        log_level="warning",
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--demucs":
        raise SystemExit(run_demucs(sys.argv[2:]))
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n  Encerrado.")