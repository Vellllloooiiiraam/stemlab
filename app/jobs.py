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
    ORPHAN_MIN_AGE_H,
    OUTPUT_DIR,
    RETENTION_DAYS,
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
        """Representação exposta pela API.

        Construída campo a campo de propósito: NUNCA inclui `source_path`
        (o caminho absoluto do arquivo no computador do usuário).
        """
        data = {
            "id": self.id,
            "source_name": self.source_name,
            "model": self.model,
            "options": self.options,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration": self.duration,
            "estimate": self.estimate,
            "elapsed": self.elapsed,
            "error": self.error,
        }
        data["display_name"] = self.display_name
        data["source_url"] = f"/api/source/{self.id}"
        data["model_label"] = MODEL_BY_ID.get(self.model, {}).get("label", self.model)
        data["stems"] = [
            {**s, "label": STEM_LABELS.get(s["stem"], s["stem"])}
            for s in self.stems
        ]
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

    def purge_expired(self, days: float = RETENTION_DAYS) -> int:
        """Remove trabalhos finalizados mais antigos que `days` dias.

        Trabalhos em execução/na fila nunca são tocados. Retorna quantos saíram.
        """
        if days <= 0:
            return 0
        cutoff = time.time() - days * 86400
        with self._lock:
            expired = [
                j.id for j in self._jobs.values()
                if j.status in (DONE, ERROR, CANCELED)
                and (j.finished_at or j.created_at) < cutoff
            ]
        for jid in expired:
            self.delete(jid)
        return len(expired)

    def cleanup_orphans(self, *, min_age_h: float = ORPHAN_MIN_AGE_H) -> dict:
        """Remove sobras que não pertencem a nenhum trabalho.

        - Envios em `uploads/` sem trabalho correspondente (mais velhos que
          `min_age_h` horas, para não apagar algo recém-enviado).
        - Pastas em `output/` cujo nome não corresponde a nenhum trabalho.

        Trabalhos em execução/na fila e seus arquivos são sempre preservados.
        Retorna o que foi removido.
        """
        now = time.time()
        min_age_s = max(0.0, min_age_h) * 3600

        with self._lock:
            known_ids = set(self._jobs.keys())
            referenced = {Path(j.source_path).name for j in self._jobs.values()}

        removed_uploads = 0
        removed_dirs = 0
        freed = 0

        try:
            for entry in UPLOADS_DIR.iterdir():
                if not entry.is_file() or entry.name in referenced:
                    continue
                try:
                    st = entry.stat()
                    if now - st.st_mtime < min_age_s:
                        continue
                    freed += st.st_size
                    entry.unlink()
                    removed_uploads += 1
                except OSError:
                    pass
        except OSError:
            pass

        try:
            for entry in OUTPUT_DIR.iterdir():
                if not entry.is_dir() or entry.name in known_ids:
                    continue
                try:
                    size = sum(p.stat().st_size for p in entry.rglob("*") if p.is_file())
                    shutil.rmtree(entry, ignore_errors=True)
                    if not entry.exists():
                        freed += size
                        removed_dirs += 1
                except OSError:
                    pass
        except OSError:
            pass

        return {
            "uploads": removed_uploads,
            "dirs": removed_dirs,
            "freed_bytes": freed,
        }

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

        work = job.work_dir
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
            # Toda a preparação fica DENTRO do try: se algo falhar aqui (disco
            # cheio ao criar a pasta, arquivo ilegível, comando inválido...), a
            # tarefa precisa terminar em ERROR em vez de ficar presa em RUNNING.
            source = Path(job.source_path)
            shutil.rmtree(work, ignore_errors=True)
            work.mkdir(parents=True, exist_ok=True)

            opts = job.options or {}
            model_id = job.model
            if model_id not in MODEL_BY_ID:
                raise separator.SeparationError(f"Modelo desconhecido: {model_id}")
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
            shutil.rmtree(work, ignore_errors=True)
            self._finish(job, CANCELED, message="Cancelado pelo usuário.")
            return
        except separator.SeparationError as exc:
            self._finish(job, ERROR, error=str(exc), message=str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._finish(
                job, ERROR,
                error=f"{type(exc).__name__}: {exc}",
                message="Erro inesperado.",
            )
            return
        finally:
            # Nenhum processo externo pode ficar rodando solto.
            proc = self._procs.pop(job.id, None)
            if proc is not None:
                separator._terminate(proc)  # type: ignore[arg-type]

        # Cancelamento pedido bem no fim da separação: não marcar como concluído.
        if is_cancelled():
            shutil.rmtree(work, ignore_errors=True)
            self._finish(job, CANCELED, message="Cancelado pelo usuário.")
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

    def _finish(
        self,
        job: Job,
        status: str,
        *,
        message: str,
        error: Optional[str] = None,
    ) -> None:
        """Consolida o estado final (ERROR/CANCELED) de uma tarefa."""
        with self._lock:
            job.status = status
            job.message = message
            if error:
                job.error = error
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