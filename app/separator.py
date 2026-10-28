"""Ponte com o Demucs: monta o comando, executa e lê o progresso."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from .config import ALLOWED_EXTENSIONS, BAG_MODELS, MODEL_BY_ID, STEM_ORDER

PROGRESS_RE = re.compile(r"(\d{1,3})%")
TRACK_RE = re.compile(r"Separating track\s+(.+)$")
ERROR_HINTS = (
    "error", "exception", "traceback", "no such file", "invalid",
    "not found", "cuda", "out of memory", "cannot",
)

# Ruído que o Demucs/HuggingFace cospe e que não deve virar mensagem de status.
NOISE_HINTS = (
    "warnings.warn", "warning:", "hf_token", "unauthenticated",
    "userwarning", "futurewarning", "deprecat", "huggingface",
    "enable higher rate limits", "set a hf_token",
)


class SeparationError(RuntimeError):
    pass


def python_executable() -> str:
    """Interpretador do próprio ambiente virtual (onde o Demucs está instalado)."""
    return sys.executable


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def locate_ffmpeg() -> bool:
    """Garante o ffmpeg no PATH, procurando nos locais usuais de instalação.

    O Demucs precisa do binário `ffmpeg` para ler/gravar mp3, m4a etc.
    """
    if ffmpeg_available():
        return True

    local = os.environ.get("LOCALAPPDATA", "")
    candidates: list[Path] = [
        Path(local) / "Microsoft" / "WinGet" / "Links",
        Path("C:/ProgramData/chocolatey/bin"),
        Path("C:/ffmpeg/bin"),
    ]
    packages = Path(local) / "Microsoft" / "WinGet" / "Packages"
    if packages.is_dir():
        candidates.append(packages)

    for root in candidates:
        if not root.is_dir():
            continue
        if (root / "ffmpeg.exe").is_file():
            os.environ["PATH"] = str(root) + os.pathsep + os.environ["PATH"]
            return True
        for found in root.rglob("ffmpeg.exe"):
            os.environ["PATH"] = str(found.parent) + os.pathsep + os.environ["PATH"]
            return True
    return False


def probe_duration(path: Path) -> Optional[float]:
    """Duração em segundos via ffprobe, se disponível."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        return float(json.loads(out.stdout)["format"]["duration"])
    except Exception:
        return None


def _safe_extension(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise SeparationError(
            f"Formato '{ext or '?'}' não suportado. "
            f"Aceitos: {', '.join(sorted(e.lstrip('.') for e in ALLOWED_EXTENSIONS))}"
        )
    return ext


def build_command(
    source: Path,
    out_dir: Path,
    model_id: str,
    *,
    format: str = "wav",
    mp3_bitrate: int = 320,
    shifts: int = 1,
    overlap: float = 0.25,
    segment: Optional[int] = None,
    karaoke: bool = False,
) -> list[str]:
    """Monta os argumentos do `python -m demucs.separate`."""
    if model_id not in MODEL_BY_ID:
        raise SeparationError(f"Modelo desconhecido: {model_id}")

    cmd = [
        python_executable(), "-m", "demucs.separate",
        "-n", model_id,
        "-o", str(out_dir),
        "--filename", "{stem}.{ext}",
        "--shifts", str(int(shifts)),
        "--overlap", f"{float(overlap):.2f}",
    ]

    # Karaokê: vocal + playback, o que também acelera bastante.
    if karaoke:
        cmd += ["--two-stems", "vocals"]

    if segment:
        cmd += ["--segment", str(int(segment))]

    if format == "mp3":
        cmd += ["--mp3", "--mp3-bitrate", str(int(mp3_bitrate))]
    elif format == "flac":
        cmd += ["--flac"]
    elif format == "wav24":
        cmd += ["--int24"]

    cmd.append(str(source))
    return cmd


def expected_stems(model_id: str, karaoke: bool) -> list[str]:
    if karaoke:
        return ["vocals", "no_vocals"]
    return list(MODEL_BY_ID[model_id]["stems"])


def estimate_seconds(duration: Optional[float], model_id: str, shifts: int) -> Optional[int]:
    """Estimativa grosseira de tempo para CPU. Apenas orientativa."""
    if not duration:
        return None
    factor = MODEL_BY_ID[model_id].get("factor", 1.0)
    passes = (4 if model_id in BAG_MODELS else 1) * (1 + max(0, int(shifts)))
    # ~0.45x tempo real por passada em um CPU de 6 núcleos.
    return max(15, int(duration * 0.45 * factor * passes))


class Cancelled(Exception):
    pass


def run_separation(
    cmd: list[str],
    work_root: Path,
    *,
    model_id: str,
    shifts: int,
    on_progress: Callable[[int, str], None],
    is_cancelled: Callable[[], bool],
    on_process: Callable[[Optional[subprocess.Popen]], None] = lambda p: None,
) -> list[Path]:
    """Executa o Demucs, reporta progresso e devolve os arquivos gerados."""
    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    cores = str(max(1, (os.cpu_count() or 4)))
    env.setdefault("OMP_NUM_THREADS", cores)
    env.setdefault("MKL_NUM_THREADS", cores)

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            bufsize=0,
            cwd=str(work_root),
            env=env,
            creationflags=creationflags,
        )
    except FileNotFoundError as exc:
        raise SeparationError(f"Não foi possível executar o Demucs: {exc}") from exc

    on_process(proc)

    passes = (4 if model_id in BAG_MODELS else 1) * (1 + max(0, int(shifts)))
    bar_index = 0
    bar_pct = 0
    last_message = ""
    errors: list[str] = []

    def handle_line(raw: str) -> None:
        nonlocal bar_index, bar_pct, last_message
        line = raw.strip()
        if not line:
            return

        m = PROGRESS_RE.search(line)
        if m:
            pct = min(100, int(m.group(1)))
            if pct + 5 < bar_pct:  # começou uma nova barra de progresso
                bar_index += 1
                bar_pct = 0
            bar_pct = max(bar_pct, pct)
            done = min(bar_index, passes - 1) + bar_pct / 100.0
            overall = 5 + int((done / passes) * 88)
            on_progress(min(93, overall), last_message or "Separando faixas...")
            return

        t = TRACK_RE.search(line)
        if t:
            last_message = f"Separando: {Path(t.group(1).strip()).name}"
            on_progress(max(3, 5 + int(bar_index / max(1, passes) * 88)), last_message)
            return

        low = line.lower()
        if any(h in low for h in NOISE_HINTS):
            return
        if any(h in low for h in ERROR_HINTS) and len(line) < 400:
            errors.append(line)
        elif len(line) < 200 and not line.startswith(("  ", "\t")):
            last_message = line
            on_progress(5 + int((bar_index / max(1, passes)) * 88), last_message)

    fd = proc.stdout.fileno()
    buf = b""
    while True:
        if is_cancelled():
            _terminate(proc)
            raise Cancelled()

        try:
            chunk = os.read(fd, 512)
        except OSError:
            break
        if not chunk:
            break
        buf += chunk
        while True:
            positions = [p for p in (buf.find(b"\r"), buf.find(b"\n")) if p != -1]
            if not positions:
                break
            cut = min(positions)
            handle_line(buf[:cut].decode("utf-8", "replace"))
            buf = buf[cut + 1:]
    if buf:
        handle_line(buf.decode("utf-8", "replace"))

    proc.wait()
    on_process(None)

    if is_cancelled():
        raise Cancelled()
    if proc.returncode != 0:
        detail = errors[-1] if errors else f"código de saída {proc.returncode}"
        raise SeparationError(f"O Demucs falhou: {detail}")

    on_progress(95, "Movendo arquivos gerados...")
    return _collect_outputs(work_root, model_id)


def _terminate(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception:
        pass


def _collect_outputs(work_root: Path, model_id: str) -> list[Path]:
    """O Demucs grava em <work_root>/<modelo>/; arrastamos para <work_root>/stems/."""
    produced = work_root / model_id
    if not produced.is_dir():
        produced = work_root  # alguns modelos gravam direto na raiz

    stems_dir = work_root / "stems"
    stems_dir.mkdir(parents=True, exist_ok=True)

    audio_ext = {".wav", ".mp3", ".flac", ".ogg", ".opus", ".m4a"}
    files = [
        p for p in produced.rglob("*")
        if p.is_file() and p.suffix.lower() in audio_ext and p.parent.name != "stems"
    ]
    if not files:
        raise SeparationError("O Demucs terminou sem gerar arquivos de áudio.")

    moved: list[Path] = []
    for src in files:
        dest = stems_dir / src.name
        if dest.exists():
            dest.unlink()
        shutil.move(str(src), str(dest))
        moved.append(dest)

    # Limpa diretórios vazios deixados pelo Demucs.
    for junk in work_root.iterdir():
        if junk.is_dir() and junk.name != "stems":
            shutil.rmtree(junk, ignore_errors=True)

    return sort_stems(moved)


def sort_stems(files: list[Path]) -> list[Path]:
    def key(p: Path) -> tuple[int, str]:
        name = p.stem.split("_")[0] if "_" not in p.stem else p.stem
        order = STEM_ORDER.index(name) if name in STEM_ORDER else len(STEM_ORDER)
        return (order, p.name)

    return sorted(files, key=key)