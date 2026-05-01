from lib.crew import finalize
from lib.crew import state


def _complete_task(job_id, task_id, role, worker, content, depends_on=None):
    job = state.load_job(job_id)
    state.upsert_task(
        job,
        task_id=task_id,
        role=role,
        worker=worker,
        prompt=f"{role} prompt",
        status="running",
        depends_on=depends_on or [],
    )
    artifact = state.write_artifact(job_id, task_id, content)
    job = state.load_job(job_id)
    state.upsert_task(
        job,
        task_id=task_id,
        role=role,
        worker=worker,
        prompt=f"{role} prompt",
        status="completed",
        depends_on=depends_on or [],
        result_path=str(artifact),
        note="exit=0",
    )


def test_finalize_blocks_when_delivery_gate_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_ROOT", tmp_path)
    state.init_job(job_id="job-blocked", user_request="Ship")
    _complete_task("job-blocked", "T1", "developer", "developer", "implementation")

    result = finalize.finalize_job("job-blocked")
    job = state.load_job("job-blocked")

    assert result["ready"] is False
    assert result["written"] is False
    assert job["final_result_path"] is None
    assert job["status"] == "intake"


def test_finalize_writes_final_artifact_and_marks_delivered(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_ROOT", tmp_path)
    state.init_job(job_id="job-ready", user_request="Ship a feature")
    _complete_task("job-ready", "T1", "planner", "planner", "plan")
    _complete_task("job-ready", "T2", "developer", "developer", "implementation", ["T1"])
    _complete_task("job-ready", "T3", "qa", "qa", "qa passed", ["T2"])
    _complete_task("job-ready", "T4", "qc", "qc", "qc approved", ["T3"])

    result = finalize.finalize_job("job-ready")
    job = state.load_job("job-ready")
    final_path = tmp_path / "job-ready" / "artifacts" / "final.md"

    assert result["ready"] is True
    assert result["written"] is True
    assert result["delivered"] is True
    assert job["status"] == "delivered"
    assert job["final_result_path"] == "artifacts/final.md"
    body = final_path.read_text()
    assert "# Crew Final Result" in body
    assert "Ship a feature" in body
    assert "### T3 - qa (qa)" in body
    assert "qa passed" in body
    assert "No blocking findings." in body


def test_finalize_can_write_without_delivering(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_ROOT", tmp_path)
    state.init_job(job_id="job-no-deliver", user_request="Ship")
    _complete_task("job-no-deliver", "T1", "qa", "qa", "qa passed")
    _complete_task("job-no-deliver", "T2", "qc", "qc", "qc approved", ["T1"])

    result = finalize.finalize_job("job-no-deliver", deliver=False)
    job = state.load_job("job-no-deliver")

    assert result["ready"] is True
    assert result["delivered"] is False
    assert job["status"] == "qc"
    assert job["final_result_path"] == "artifacts/final.md"
