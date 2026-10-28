"""API HTTP + servidor da interface (FastAPI)."""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import separator
from .config import (
    ALLOWED_EXTENSIONS,
    MAX_UPLOAD_MB,
    MODEL_BY_ID,
    OUTPUT_DIR,
    STEM_LABELS,
    UPLOADS_DIR,
    WEB_DIR,
    public_models,
)
from .jobs import ensure_dirs, manager

app = FastAPI(title="StemLab", version="1.0.0")
ensure_dirs()

SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


def _safe(name: str) -> str:
    cleaned = SAFE_NAME.sub("_", Path(name).name).strip(" .")
    return cleaned or "audio"


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
        "ffmpeg": shutil.which("ffmpeg") or None,
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
@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Formato '{ext or '?'}' não suportado.",
        )

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
                    fh.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(413, f"Arquivo acima de {MAX_UPLOAD_MB} MB.")
                fh.write(chunk)
    except HTTPException:
        raise
    finally:
        await file.close()

    if written == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "Arquivo vazio.")

    duration = separator.probe_duration(dest)
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
    return {"ok": True, "path": str(target)}


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


@app.get("/api/jobs/{job_id}/zip")
def get_zip(job_id: str):
    job = manager.get(job_id)
    if not job:
        raise HTTPException(404, "Trabalho não encontrado.")
    stems = job.work_dir / "stems"
    if not stems.is_dir() or not job.stems:
        raise HTTPException(409, "Este trabalho ainda não tem faixas prontas.")

    tmp = Path(tempfile.gettempdir()) / f"stemlab_{job_id}.zip"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=3) as zf:
        for item in job.stems:
            f = stems / item["file"]
            if f.is_file():
                zf.write(f, arcname=f"{Path(job.display_name).stem}/{f.name}")

    name = f"{Path(job.display_name).stem}_{job.model}.zip"
    return FileResponse(tmp, media_type="application/zip", filename=name)


# ---------------------------------------------------------------- interface web
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")