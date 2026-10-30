"""API HTTP + servidor da interface (FastAPI)."""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import shutil
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask

from . import separator
from .config import (
    ALLOWED_EXTENSIONS,
    MAX_CONCURRENT_UPLOADS,
    MAX_DURATION_S,
    MAX_UPLOAD_MB,
    MIN_FREE_MB,
    MODEL_BY_ID,
    OUTPUT_DIR,
    STEM_LABELS,
    UPLOADS_DIR,
    WEB_DIR,
    public_models,
)
from .jobs import ensure_dirs, manager

app = FastAPI(title="StemLab", version="1.3.0")
ensure_dirs()

# ------------------------------------------------------------- proteção local
# Token por sessão: só a interface servida pela própria máquina consegue lê-lo
# (não há CORS, então outra origem não lê a resposta de /api/session). Toda rota
# que altera dados exige o cabeçalho X-StemLab-Token. O token nunca é logado.
SESSION_TOKEN = secrets.token_urlsafe(32)

_ALLOWED_HOSTS = {"127.0.0.1:8765", "localhost:8765", "127.0.0.1", "localhost"}
_ALLOWED_ORIGINS = {"http://127.0.0.1:8765", "http://localhost:8765"}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@app.middleware("http")
async def _guard(request: Request, call_next):
    # 1) Host: bloqueia DNS rebinding (domínio externo apontando para 127.0.0.1).
    if request.headers.get("host", "") not in _ALLOWED_HOSTS:
        return PlainTextResponse("Host não permitido.", status_code=400)

    # 2) Para métodos que alteram dados: exigir token e conferir a origem.
    if request.method not in _SAFE_METHODS:
        origin = request.headers.get("origin")
        if origin and origin not in _ALLOWED_ORIGINS:
            return PlainTextResponse("Origem não autorizada.", status_code=403)
        if request.headers.get("x-stemlab-token") != SESSION_TOKEN:
            return PlainTextResponse("Token ausente ou inválido.", status_code=403)

    return await call_next(request)

SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


def _safe(name: str) -> str:
    cleaned = SAFE_NAME.sub("_", Path(name).name).strip(" .")
    return cleaned or "audio"


def _remove_file(path: Path) -> None:
    """Apaga um arquivo temporário, ignorando erros (ex.: já removido)."""
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


# --------------------------------------------------------------------- sessão
@app.get("/api/session")
def get_session():
    """Entrega o token à interface local (GET simples, sem CORS)."""
    return {"token": SESSION_TOKEN}


# --------------------------------------------------------------------- modelos
@app.get("/api/models")
def get_models():
    return {"models": public_models(), "stem_labels": STEM_LABELS}


# --------------------------------------------------------------------- sistema
@app.get("/api/system")
def get_system():
    import platform
    import sys

    info = {
        "python": sys.version.split()[0],
        "platform": f"{platform.system()} {platform.release()}",
        "cpu_count": os.cpu_count(),
        "ffmpeg": bool(shutil.which("ffmpeg")),   # só se está disponível, sem o caminho
        "torch": None,
        "device": "cpu",
        "gpu_note": None,
    }
    try:
        import torch

        info["torch"] = torch.__version__
        if torch.cuda.is_available():
            info["device"] = "cuda"
            info["gpu_note"] = torch.cuda.get_device_name(0)
        elif hasattr(torch, "backends") and hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            info["device"] = "mps"
    except Exception as exc:  # noqa: BLE001
        info["torch_error"] = str(exc)

    info["active_jobs"] = manager.stats()
    return info


@app.get("/api/health")
def health():
    return {"ok": True, "ffmpeg": separator.ffmpeg_available(), "jobs": manager.stats()}


# --------------------------------------------------------------------- upload
_UPLOAD_SLOTS = asyncio.Semaphore(MAX_CONCURRENT_UPLOADS)


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Formato '{ext or '?'}' não suportado.",
        )

    # Espaço em disco: não aceitar se estiver quase cheio.
    free_mb = shutil.disk_usage(str(UPLOADS_DIR)).free / (1024 * 1024)
    if free_mb < MIN_FREE_MB:
        raise HTTPException(507, "Pouco espaço em disco para receber o arquivo.")

    # Limita envios simultâneos (espera curta; depois recusa).
    acquired = False
    try:
        await asyncio.wait_for(_UPLOAD_SLOTS.acquire(), timeout=10)
        acquired = True
    except asyncio.TimeoutError:
        raise HTTPException(429, "Muitos envios ao mesmo tempo. Tente novamente.")

    stored = f"{uuid.uuid4().hex[:10]}__{_safe(file.filename or 'audio' + ext)}"
    dest = UPLOADS_DIR / stored
    limit = MAX_UPLOAD_MB * 1024 * 1024
    written = 0

    try:
        with dest.open("wb") as fh:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    raise HTTPException(413, f"Arquivo acima de {MAX_UPLOAD_MB} MB.")
                fh.write(chunk)

        if written == 0:
            raise HTTPException(400, "Arquivo vazio.")

        # Valida o CONTEÚDO, não só a extensão: precisa ser áudio reconhecível.
        try:
            duration = separator.validate_audio(dest)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

        if duration > MAX_DURATION_S:
            raise HTTPException(
                400,
                f"Áudio muito longo ({duration / 3600:.1f}h). "
                f"Limite: {MAX_DURATION_S // 3600}h.",
            )
    except HTTPException:
        _remove_file(dest)          # nunca deixar um arquivo rejeitado no disco
        raise
    except Exception:
        _remove_file(dest)
        raise
    finally:
        await file.close()
        if acquired:
            _UPLOAD_SLOTS.release()

    return {
        "file": stored,
        "name": Path(file.filename or stored).name,
        "bytes": written,
        "duration": duration,
    }


# ---------------------------------------------------------------------- jobs
class JobRequest(BaseModel):
    file: str
    model: str = "htdemucs"
    format: str = "wav"
    mp3_bitrate: int = 320
    shifts: int = 1
    overlap: float = 0.25
    segment: int | None = None
    karaoke: bool = False


@app.post("/api/jobs")
def create_job(req: JobRequest):
    if req.model not in MODEL_BY_ID:
        raise HTTPException(400, f"Modelo desconhecido: {req.model}")

    source = (UPLOADS_DIR / Path(req.file).name)
    if not source.is_file():
        raise HTTPException(404, "Arquivo de entrada não encontrado. Envie novamente.")

    if req.format not in {"wav", "wav24", "mp3", "flac"}:
        raise HTTPException(400, "Formato de saída inválido.")

    options = {
        "format": req.format,
        "mp3_bitrate": max(96, min(320, int(req.mp3_bitrate))),
        "shifts": max(0, min(5, int(req.shifts))),
        "overlap": max(0.05, min(0.9, float(req.overlap))),
        "segment": req.segment,
        "karaoke": bool(req.karaoke),
    }
    job = manager.submit(source.name, source, req.model, options)
    return job.to_public()


@app.get("/api/jobs")
def list_jobs():
    return {"jobs": manager.list_jobs()}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = manager.get(job_id)
    if not job:
        raise HTTPException(404, "Trabalho não encontrado.")
    return job.to_public()


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    if not manager.cancel(job_id):
        raise HTTPException(409, "Não foi possível cancelar (já finalizado?).")
    return {"ok": True}


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    if not manager.delete(job_id):
        raise HTTPException(409, "Trabalho em execução ou inexistente.")
    return {"ok": True}


@app.post("/api/jobs/clear-finished")
def clear_finished():
    return {"removed": manager.clear_finished()}


@app.post("/api/maintenance/cleanup")
def maintenance_cleanup():
    """Limpeza sob demanda: órfãos (uploads/pastas sem trabalho) + expirados."""
    orphan = manager.cleanup_orphans()
    expired = manager.purge_expired()
    return {
        "removed_jobs": expired,
        "orphan_uploads": orphan["uploads"],
        "orphan_dirs": orphan["dirs"],
        "freed_bytes": orphan["freed_bytes"],
    }


@app.post("/api/jobs/{job_id}/reveal")
def reveal_job(job_id: str):
    job = manager.get(job_id)
    if not job:
        raise HTTPException(404, "Trabalho não encontrado.")
    stems = job.work_dir / "stems"
    target = stems if stems.is_dir() else job.work_dir
    try:
        os.startfile(str(target))  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Não foi possível abrir a pasta: {exc}")
    return {"ok": True}


# ---------------------------------------------------------------------- mídia
@app.get("/api/stems/{job_id}/{name}")
def get_stem(job_id: str, name: str, download: int = 0):
    job = manager.get(job_id)
    if not job:
        raise HTTPException(404, "Trabalho não encontrado.")
    path = (job.work_dir / "stems" / Path(name).name)
    if not path.is_file():
        raise HTTPException(404, "Faixa não encontrada.")
    headers = {}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{path.name}"'
    return FileResponse(path, headers=headers)


@app.get("/api/source/{job_id}")
def get_source(job_id: str):
    job = manager.get(job_id)
    if not job:
        raise HTTPException(404, "Trabalho não encontrado.")
    path = Path(job.source_path)
    if not path.is_file():
        raise HTTPException(404, "Arquivo original indisponível.")
    return FileResponse(path)


@app.get("/api/waveform/{job_id}/{name}")
def get_waveform(job_id: str, name: str, points: int = 1200):
    """Picos da forma de onda (poucos pontos) — evita decodificar tudo no navegador."""
    job = manager.get(job_id)
    if not job:
        raise HTTPException(404, "Trabalho não encontrado.")
    path = job.work_dir / "stems" / Path(name).name
    if not path.is_file():
        raise HTTPException(404, "Faixa não encontrada.")

    points = max(200, min(3000, int(points)))
    cache = job.work_dir / "waveforms" / f"{path.name}.{points}.json"
    if cache.is_file():
        try:
            return {"points": json.loads(cache.read_text(encoding="utf-8"))}
        except (OSError, ValueError):
            pass

    try:
        peaks = separator.waveform_peaks(path, points)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Não foi possível gerar a forma de onda: {exc}")

    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(peaks), encoding="utf-8")
    except OSError:
        pass
    return {"points": peaks}


@app.get("/api/jobs/{job_id}/zip")
def get_zip(job_id: str):
    job = manager.get(job_id)
    if not job:
        raise HTTPException(404, "Trabalho não encontrado.")
    stems = job.work_dir / "stems"
    if not stems.is_dir() or not job.stems:
        raise HTTPException(409, "Este trabalho ainda não tem faixas prontas.")

    # Nome único (mkstemp) evita colisão entre downloads simultâneos do mesmo
    # trabalho. O arquivo é apagado no fim da resposta (BackgroundTask).
    fd, tmp_name = tempfile.mkstemp(prefix=f"stemlab_{job_id}_", suffix=".zip")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=3) as zf:
            for item in job.stems:
                f = stems / item["file"]
                if f.is_file():
                    zf.write(f, arcname=f"{Path(job.display_name).stem}/{f.name}")
    except Exception:
        _remove_file(tmp)   # não deixar lixo se a geração falhar
        raise

    name = f"{Path(job.display_name).stem}_{job.model}.zip"
    return FileResponse(
        tmp,
        media_type="application/zip",
        filename=name,
        background=BackgroundTask(_remove_file, tmp),
    )


# ---------------------------------------------------------------- interface web
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")