"""Run with: RUN_CMD="sh -c" pytest wrapper/test_api.py"""
import importlib
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("RUN_CMD", "sh -c")
    monkeypatch.setenv("JOBS_DIR", str(tmp_path))
    import api as module
    module = importlib.reload(module)
    (tmp_path / "job1").mkdir()
    return module, TestClient(module.app), tmp_path


def _wait(client, job_id, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = client.get(f"/status/{job_id}").json()["state"]
        if state != "running":
            return state
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_success_writes_log_and_done(api):
    _, client, jobs = api
    assert client.post("/run/job1", json={"args": ["echo hello; pwd"]}).status_code == 202
    assert _wait(client, "job1") == "done"
    log = (jobs / "job1" / "job.log").read_text()
    assert "hello" in log and str(jobs / "job1") in log  # runs inside the job folder


def test_nonzero_exit_is_failed(api):
    _, client, _ = api
    client.post("/run/job1", json={"args": ["exit 3"]})
    assert _wait(client, "job1") == "failed"
    assert client.get("/status/job1").json()["returncode"] == 3


def test_same_job_twice_is_conflict(api):
    _, client, _ = api
    client.post("/run/job1", json={"args": ["true"]})
    assert client.post("/run/job1", json={"args": ["true"]}).status_code == 409


def test_unknown_and_missing_jobs(api):
    _, client, _ = api
    assert client.get("/status/nope").status_code == 404
    assert client.post("/run/nope", json={"args": []}).status_code == 404
    assert client.post("/run/..%2Fetc", json={"args": []}).status_code in (404, 422)


def test_job_cut_off_by_restart_is_lost(api):
    module, client, jobs = api
    module._write_status(jobs / "job1", state="running")  # left over by a dead process
    assert client.get("/status/job1").json()["state"] == "lost"


def test_finished_job_survives_restart(api):
    module, client, jobs = api
    client.post("/run/job1", json={"args": ["true"]})
    _wait(client, "job1")
    module = importlib.reload(module)  # fresh process, empty memory
    assert TestClient(module.app).get("/status/job1").json()["state"] == "done"
