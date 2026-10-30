"""Ponte com o Demucs: monta o comando, executa e lê o progresso."""
from __future__ import annotations

import array
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from .config import (
    ALLOWED_EXTENSIONS,
    BAG_MODELS,
    BASE_DIR,
    MAX_JOB_S,
    MODEL_BY_ID,
    STEM_ORDER,
)

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


def _prepend_path(directory) -> None:
    """Coloca um diretório no começo do PATH do processo."""
    os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")


def demucs_prefix() -> list[str]:
    """Comando que inicia o Demucs.

    * Empacotado (`StemLab.exe`): o próprio executável, no modo `--demucs`.
    * Desenvolvimento: `run.py --demucs`.

    Nos dois casos quem roda o Demucs é `run_demucs`, que instala o substituto
    do `ffprobe` antes de começar (ver `install_ffprobe_compat`).
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, "--demucs"]
    return [python_executable(), str(BASE_DIR / "run.py"), "--demucs"]


def locate_ffmpeg() -> bool:
    """Garante o ffmpeg no PATH.

    Ordem: binário já disponível → `bin/` embutido no pacote portátil →
    instalações usuais (winget/chocolatey). O Demucs precisa do `ffmpeg` para
    ler e gravar mp3, m4a etc.
    """
    if ffmpeg_available():
        return True

    from .config import BUNDLED_BIN

    if (BUNDLED_BIN / "ffmpeg.exe").is_file():
        _prepend_path(BUNDLED_BIN)
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
            _prepend_path(root)
            return True
        for found in root.rglob("ffmpeg.exe"):
            _prepend_path(found.parent)
            return True
    return False


def probe_duration(path: Path) -> Optional[float]:
    """Duração em segundos.

    Tenta, em ordem: soundfile (wav/flac/ogg/mp3) → ffprobe → ffmpeg.
    Assim funciona mesmo quando só o `ffmpeg` está disponível.
    """
    try:
        import soundfile as sf

        return float(sf.info(str(path)).duration)
    except Exception:
        pass

    if shutil.which("ffprobe"):
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "json", str(path)],
                capture_output=True, text=True, timeout=60,
            )
            return float(json.loads(out.stdout)["format"]["duration"])
        except Exception:
            pass

    ff = shutil.which("ffmpeg")
    if ff:
        try:
            out = subprocess.run(
                [ff, "-hide_banner", "-i", str(path)],
                capture_output=True, text=True, timeout=60,
            )
            m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", out.stderr or "")
            if m:
                return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        except Exception:
            pass
    return None


def validate_audio(path: Path) -> float:
    """Duração (s) se o arquivo for áudio válido; senão levanta ValueError.

    Diferente de `probe_duration`, exige que exista uma faixa de áudio de
    verdade — um vídeo sem áudio ou um texto renomeado para `.mp3` são
    rejeitados.
    """
    # 1) soundfile: rápido e confiável para wav/flac/ogg/mp3.
    try:
        import soundfile as sf

        info = sf.info(str(path))
        if info.frames > 0 and info.samplerate > 0:
            return float(info.duration)
    except Exception:
        pass

    # 2) ffprobe: precisa de uma faixa de áudio.
    if shutil.which("ffprobe"):
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "a:0",
                 "-show_entries", "stream=codec_type",
                 "-show_entries", "format=duration",
                 "-of", "json", str(path)],
                capture_output=True, text=True, timeout=60,
            )
            data = json.loads(out.stdout or "{}")
            if data.get("streams") and data.get("format", {}).get("duration"):
                dur = float(data["format"]["duration"])
                if dur > 0:
                    return dur
        except Exception:
            pass

    # 3) ffmpeg: confirma a existência de áudio e lê a duração.
    if shutil.which("ffmpeg"):
        try:
            dur = float(_probe_metadata(path)["format"]["duration"])
            if dur > 0:
                return dur
        except Exception:
            pass

    raise ValueError("O arquivo não parece ser um áudio válido.")


def _probe_metadata(path) -> dict:
    """Equivalente mínimo de `ffprobe -show_format -show_streams`, via ffmpeg.

    O Demucs lê os metadados do arquivo chamando `ffprobe`. Quando esse binário
    não existe (mas o `ffmpeg` sim), montamos a mesma estrutura a partir da
    saída do próprio `ffmpeg`. As chaves usadas pelo Demucs são: `format.duration`,
    `streams[].codec_type`, `channels` e `sample_rate`.
    """
    ff = shutil.which("ffmpeg")
    if not ff:
        raise RuntimeError("ffmpeg indisponível para ler os metadados do áudio.")

    out = subprocess.run(
        [ff, "-hide_banner", "-i", str(path)],
        capture_output=True, text=True, timeout=120,
    )
    text = out.stderr or ""

    duration = 0.0
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if m:
        duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))

    layouts = {
        "mono": 1, "stereo": 2, "2.1": 3, "quad": 4,
        "5.0": 5, "5.1(side)": 6, "5.1": 6, "6.1": 7, "7.1": 8,
    }

    # A posição na lista tem de casar com o índice do stream no ffmpeg,
    # portanto TODOS os streams entram (o Demucs mapeia `0:<posição>`).
    streams: list[dict] = []
    stream_re = re.compile(
        r"Stream #\d+:(\d+)[^:\n]*:\s*([A-Za-z]+):\s*([^\n]*)"
    )
    for sm in stream_re.finditer(text):
        index = int(sm.group(1))
        kind = sm.group(2).lower()
        attrs = sm.group(3)
        entry: dict = {"index": index, "codec_type": kind}
        if kind == "audio":
            sr = re.search(r"(\d+)\s*Hz", attrs)
            entry["sample_rate"] = int(sr.group(1)) if sr else 44100
            nch = re.search(r"(\d+)\s*channels", attrs)
            if nch:
                entry["channels"] = int(nch.group(1))
            else:
                entry["channels"] = next(
                    (n for name, n in layouts.items() if name in attrs), 2
                )
        streams.append(entry)

    if not any(s["codec_type"] == "audio" for s in streams):
        raise RuntimeError("Não encontrei nenhuma faixa de áudio no arquivo.")

    return {"streams": streams, "format": {"duration": f"{duration:.6f}"}}


def install_ffprobe_compat() -> None:
    """Ensina o Demucs a ler metadados mesmo sem um `ffprobe` de verdade.

    Precisa rodar NO processo que executa o Demucs (ele é um processo à parte).
    Se existir um `ffprobe` real, ele continua sendo usado: o substituto só
    entra em ação quando a chamada original falha.
    """
    try:
        from demucs import audio as demucs_audio
    except Exception:
        return
    if getattr(demucs_audio, "_stemlab_ffprobe_compat", False):
        return

    original = demucs_audio._read_info

    def _read_info(path):
        try:
            return original(path)
        except Exception:
            return _probe_metadata(path)

    demucs_audio._read_info = _read_info
    demucs_audio._stemlab_ffprobe_compat = True


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
    """Monta os argumentos passados ao Demucs (`demucs_prefix()` + opções).

    O Demucs roda via `run.py --demucs` (ou pelo próprio .exe, quando
    empacotado) para que o substituto do `ffprobe` seja instalado antes.
    """
    if model_id not in MODEL_BY_ID:
        raise SeparationError(f"Modelo desconhecido: {model_id}")

    cmd = demucs_prefix() + [
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

    # `--` encerra a leitura de opções: o caminho do arquivo é sempre tratado
    # como posicional, mesmo que o nome comece com "-".
    cmd += ["--", str(source)]
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
    timeout_s: Optional[float] = None,
) -> list[Path]:
    """Executa o Demucs, reporta progresso e devolve os arquivos gerados.

    Os argumentos vão em lista (nunca `shell=True`), então nomes com espaços ou
    caracteres especiais são tratados literalmente. Um watchdog encerra o
    processo em caso de cancelamento — mesmo se a saída ficar silenciosa — ou
    ao estourar o tempo máximo.
    """
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
    # Watchdog: encerra o processo se o usuário cancelar (mesmo com saída
    # silenciosa, quando o `os.read` ficaria bloqueado) ou se estourar o tempo.
    deadline = time.time() + (timeout_s if timeout_s is not None else MAX_JOB_S)
    timed_out = {"v": False}
    stop_watch = threading.Event()

    def _watch() -> None:
        while not stop_watch.wait(0.5):
            if is_cancelled():
                _terminate(proc)
                return
            if time.time() >= deadline:
                timed_out["v"] = True
                _terminate(proc)
                return

    watcher = threading.Thread(target=_watch, name="stemlab-demucs-watchdog", daemon=True)
    watcher.start()

    fd = proc.stdout.fileno()
    buf = b""
    try:
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
    finally:
        stop_watch.set()
        watcher.join(timeout=2)

    on_process(None)

    if timed_out["v"]:
        secs = int(timeout_s if timeout_s is not None else MAX_JOB_S)
        human = f"{secs // 60} min" if secs >= 60 else f"{secs} s"
        raise SeparationError(f"Tempo máximo de execução excedido ({human}).")
    if is_cancelled():
        raise Cancelled()
    if proc.returncode != 0:
        detail = errors[-1] if errors else f"código de saída {proc.returncode}"
        raise SeparationError(f"O Demucs falhou: {detail}")

    on_progress(95, "Movendo arquivos gerados...")
    return _collect_outputs(work_root, model_id)


def waveform_peaks(path: Path, points: int = 1200, *, sample_rate: int = 8000) -> list[float]:
    """Picos normalizados (0..1) para a forma de onda.

    Decodifica o áudio no servidor por streaming (ffmpeg) e reduz a poucas
    amostras, para o navegador não precisar baixar/decodificar o arquivo
    inteiro. O resultado é cacheado em disco pela camada HTTP.
    """
    ff = shutil.which("ffmpeg")
    if not ff:
        raise RuntimeError("ffmpeg indisponível para gerar a forma de onda.")
    duration = probe_duration(path)
    if not duration or duration <= 0:
        raise ValueError("não foi possível determinar a duração do áudio.")

    points = max(1, int(points))
    total = max(1, int(duration * sample_rate))
    step = max(1, total // points)

    proc = subprocess.Popen(
        [ff, "-v", "error", "-i", str(path), "-ac", "1",
         "-ar", str(sample_rate), "-f", "s16le", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
    )
    peaks = [0] * points
    try:
        leftover = b""
        index = 0
        while True:
            chunk = proc.stdout.read(1 << 16)
            if not chunk:
                break
            data = leftover + chunk
            if len(data) % 2:
                leftover = data[-1:]
                data = data[:-1]
            else:
                leftover = b""
            if not data:
                continue
            samples = array.array("h")
            samples.frombytes(data)
            for v in samples:
                bucket = index // step
                if bucket >= points:
                    bucket = points - 1
                av = v if v >= 0 else -v
                if av > peaks[bucket]:
                    peaks[bucket] = av
                index += 1
    finally:
        if proc.stdout:
            proc.stdout.close()
        proc.wait()

    top = max(peaks) or 1
    return [p / top for p in peaks]


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