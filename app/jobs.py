"""Gerenciador de trabalhos: fila, execução em segundo plano e persistência."""
from __future__ import annotations

import json
import queue
import shutil
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from . import separator
from .config import (
    JOBS_FILE,
    MODEL_BY_ID,
    OUTPUT_DIR,
    STEM_LABELS,
    UPLOADS_DIR,
)

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
ERROR = "error"
CANCELED = "canceled"


@dataclass
class Job:
    id: str
    source_name: str
    source_path: str
    model: str
    options: dict
    status: str = QUEUED
    progress: int = 0
    message: str = "Na fila..."
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    duration: Optional[float] = None          # duração do áudio de entrada
    estimate: Optional[int] = None            # estimativa de segundos
    elapsed: Optional[float] = None
    error: Optional[str] = None
    stems: list[dict] = field(default_factory=list)

    @property
    def work_dir(self) -> Path:
        return OUTPUT_DIR / self.id

    @property
    def display_name(self) -> str:
        """Nome do arquivo sem o prefixo interno (uuid) usado no armazenamento."""
        name = self.source_name
        head, sep, tail = name.partition("__")
        if sep and len(head) == 10:
            name = tail
        return name

    def to_public(self) -> dict:
        data = asdict(self)
        data["display_name"] = self.display_name
        data["source_url"] = f"/api/source/{self.id}"
        data["model_label"] = MODEL_BY_ID.get(self.model, {}).get("label", self.model)
        for s in data["stems"]:
            s["label"] = STEM_LABELS.get(s["stem"], s["stem"])
        return data


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._cancel_flags: set[str] = set()
        self._procs: dict[str, object] = {}
        self._worker: Optional[threading.Thread] = None
        self._load()

    # ---------------------------------------------------------------- ciclo de vida
    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._worker = threading.Thread(target=self._run_loop, name="stemlab-worker", daemon=True)
        self._worker.start()

    # ------------------------------------------------------------------- consultas
    def list_jobs(self) -> list[dict]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
            return [j.to_public() for j in jobs]

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def stats(self) -> dict:
        with self._lock:
            active = sum(1 for j in self._jobs.values() if j.status == RUNNING)
            waiting = sum(1 for j in self._jobs.values() if j.status == QUEUED)
        return {"active": active, "queued": waiting, "total": len(self._jobs)}

    # ------------------------------------------------------------------- comandos
    def submit(self, source_name: str, source_path: Path, model: str, options: dict) -> Job:
        job_id = uuid.uuid4().hex[:12]
        job = Job(
            id=job_id,
            source_name=source_name,
            source_path=str(source_path),
            model=model,
            options=options,
        )
        with self._lock:
            self._jobs[job_id] = job
            self._save()
        self._queue.put(job_id)
        return job

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.status in (DONE, ERROR, CANCELED):
                return False
            self._cancel_flags.add(job_id)
            proc = self._procs.get(job_id)
            job.message = "Cancelando..."
            self._save()
        if proc is not None:
            separator._terminate(proc)  # type: ignore[arg-type]
        return True

    def delete(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            if job.status == RUNNING:
                self.cancel(job_id)
                return False
            self._jobs.pop(job_id, None)
            self._save()
        shutil.rmtree(job.work_dir, ignore_errors=True)
        try:
            Path(job.source_path).unlink(missing_ok=True)
        except OSError:
            pass
        return True

    def clear_finished(self) -> int:
        with self._lock:
            done = [j.id for j in self._jobs.values() if j.status in (DONE, ERROR, CANCELED)]
        for jid in done:
            self.delete(jid)
        return len(done)

    # --------------------------------------------------------------------- worker
    def _run_loop(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                job = self._jobs.get(job_id)
                if not job:
                    continue
                self._execute(job)
            except Exception:  # nunca deixar a thread morrer
                traceback.print_exc()
            finally:
                self._queue.task_done()

    def _execute(self, job: Job) -> None:
        with self._lock:
            if job_id_cancelled(job, self._cancel_flags):
                job.status = CANCELED
                job.message = "Cancelado."
                self._save()
                self._cancel_flags.discard(job.id)
                return
            job.status = RUNNING
            job.started_at = time.time()
            job.progress = 2
            job.message = "Carregando modelo (na primeira vez ele é baixado, ~80 MB)..."
            self._save()

        source = Path(job.source_path)
        work = job.work_dir
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)

        opts = job.options or {}
        model_id = job.model
        shifts = int(opts.get("shifts", 1))
        karaoke = bool(opts.get("karaoke", False))

        if job.duration is None:
            job.duration = separator.probe_duration(source)
        job.estimate = separator.estimate_seconds(job.duration, model_id, shifts)

        cmd = separator.build_command(
            source,
            work,
            model_id,
            format=str(opts.get("format", "wav")),
            mp3_bitrate=int(opts.get("mp3_bitrate", 320)),
            shifts=shifts,
            overlap=float(opts.get("overlap", 0.25)),
            segment=opts.get("segment"),
            karaoke=karaoke,
        )

        last_save = [0.0]

        def on_progress(pct: int, message: str) -> None:
            with self._lock:
                job.progress = max(job.progress, min(99, int(pct)))
                job.message = message
                now = time.time()
                if now - last_save[0] > 1.0:      # não gravar em disco a cada linha
                    last_save[0] = now
                    self._save()

        def is_cancelled() -> bool:
            return job.id in self._cancel_flags

        def on_process(proc) -> None:
            with self._lock:
                if proc is None:
                    self._procs.pop(job.id, None)
                else:
                    self._procs[job.id] = proc

        try:
            produced = separator.run_separation(
                cmd,
                work,
                model_id=model_id,
                shifts=shifts,
                on_progress=on_progress,
                is_cancelled=is_cancelled,
                on_process=on_process,
            )
        except separator.Cancelled:
            with self._lock:
                job.status = CANCELED
                job.message = "Cancelado pelo usuário."
                job.finished_at = time.time()
                job.elapsed = (job.finished_at - job.started_at) if job.started_at else None
                self._cancel_flags.discard(job.id)
                self._save()
            shutil.rmtree(work, ignore_errors=True)
            return
        except separator.SeparationError as exc:
            with self._lock:
                job.status = ERROR
                job.error = str(exc)
                job.message = str(exc)
                job.finished_at = time.time()
                job.elapsed = (job.finished_at - job.started_at) if job.started_at else None
                self._cancel_flags.discard(job.id)
                self._save()
            return
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                job.status = ERROR
                job.error = f"{type(exc).__name__}: {exc}"
                job.message = "Erro inesperado."
                job.finished_at = time.time()
                self._save()
            traceback.print_exc()
            return

        with self._lock:
            job.stems = [
                {
                    "stem": p.stem,
                    "file": p.name,
                    "bytes": p.stat().st_size,
                    "url": f"/api/stems/{job.id}/{p.name}",
                }
                for p in produced
            ]
            job.status = DONE
            job.progress = 100
            job.message = "Concluído."
            job.finished_at = time.time()
            job.elapsed = (job.finished_at - job.started_at) if job.started_at else None
            self._cancel_flags.discard(job.id)
            self._save()

    # ------------------------------------------------------------------ persistência
    def _save(self) -> None:
        try:
            data = [asdict(j) for j in self._jobs.values()]
            tmp = JOBS_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(JOBS_FILE)
        except OSError:
            pass

    def _load(self) -> None:
        if not JOBS_FILE.is_file():
            return
        try:
            raw = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for item in raw:
            try:
                item.pop("source_url", None)
                item.pop("model_label", None)
                item.pop("display_name", None)
                for s in item.get("stems", []):
                    s.pop("label", None)
                job = Job(**item)
                if job.status in (RUNNING, QUEUED):
                    job.status = ERROR
                    job.error = "Interrompido: o aplicativo foi fechado durante a execução."
                    job.message = "Interrompido."
                self._jobs[job.id] = job
            except TypeError:
                continue


def job_id_cancelled(job: Job, flags: set[str]) -> bool:
    return job.id in flags


def ensure_dirs() -> None:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


manager = JobManager()