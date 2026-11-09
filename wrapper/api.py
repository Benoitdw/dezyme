"""Generic HTTP wrapper around a command-line tool.

One instance runs inside each software container. The backend drives it:

    POST /run/<job_id>   {"args": [...]}  -> start RUN_CMD + args in /data/jobs/<job_id>
    GET  /status/<job_id>                 -> running | done | failed | lost

There is no queue here: a job starts as soon as it is posted. The backend keeps
the queue in its database and never sends more jobs than the software should
run at the same time (max_parallel in its config.toml).

The job directory is created by the backend on the shared volume before the
call. The wrapper writes two files in it:

    job.log      stdout + stderr of the command
    status.json  state, written atomically, so a job that finished is still
                 known after the container restarts

A job whose status.json says running but that this process does not know
about was cut off by a restart: it is reported as "lost".

Environment:
    RUN_CMD   command prefix, e.g. "popmusicevol" (required)
    JOBS_DIR  shared jobs root (default /data/jobs)
"""

import json
import os
import re
import shlex
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

RUN_CMD = shlex.split(os.environ["RUN_CMD"])
JOBS_DIR = Path(os.environ.get("JOBS_DIR", "/data/jobs"))

_JOB_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_active: set[str] = set()  # jobs this process is running
_lock = threading.Lock()

app = FastAPI(title="Software wrapper")


class RunRequest(BaseModel):
    args: list[str] = []


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_dir(job_id: str) -> Path:
    if not _JOB_ID.match(job_id):
        raise HTTPException(status_code=422, detail="Invalid job id")
    return JOBS_DIR / job_id


def _read_status(job_dir: Path) -> dict | None:
    try:
        return json.loads((job_dir / "status.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _write_status(job_dir: Path, **fields) -> None:
    status = {**(_read_status(job_dir) or {}), **fields}
    tmp = job_dir / "status.json.tmp"
    tmp.write_text(json.dumps(status, indent=2))
    os.replace(tmp, job_dir / "status.json")


def _run(job_id: str, job_dir: Path, argv: list[str]) -> None:
    try:
        try:
            with open(job_dir / "job.log", "ab") as log:
                proc = subprocess.run(
                    argv, cwd=job_dir, stdout=log, stderr=subprocess.STDOUT
                )
            returncode = proc.returncode
        except OSError as e:  # command not found, permission denied...
            with open(job_dir / "job.log", "a") as log:
                log.write(f"\nwrapper: {e}\n")
            returncode = -1
        _write_status(
            job_dir,
            state="done" if returncode == 0 else "failed",
            returncode=returncode,
            finished_at=_now(),
        )
    finally:
        with _lock:
            _active.discard(job_id)


@app.post("/run/{job_id}", status_code=202)
def run(job_id: str, req: RunRequest) -> dict:
    job_dir = _job_dir(job_id)
    if not job_dir.is_dir():
        raise HTTPException(status_code=404, detail="Job directory does not exist")
    if any("\0" in a for a in req.args):
        raise HTTPException(status_code=422, detail="Invalid argument")

    with _lock:
        if job_id in _active or _read_status(job_dir) is not None:
            raise HTTPException(status_code=409, detail="Job already submitted")
        _active.add(job_id)
        argv = [*RUN_CMD, *req.args]
        _write_status(job_dir, state="running", argv=argv, started_at=_now())

    threading.Thread(target=_run, args=(job_id, job_dir, argv), daemon=True).start()
    return {"job_id": job_id, "state": "running"}


@app.get("/status/{job_id}")
def status(job_id: str) -> dict:
    job_dir = _job_dir(job_id)
    with _lock:
        active = job_id in _active
    status = _read_status(job_dir)
    if status is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    if status.get("state") == "running" and not active:
        status["state"] = "lost"
    return status


@app.get("/health")
def health() -> dict:
    return {"ok": True}
