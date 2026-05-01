from lib.crew import gate
from lib.crew import state


def _task(task_id, role, status="completed"):
    return {
        "task_id": task_id,
        "role": role,
        "worker": role,
        "status": status,
        "prompt": "x",
        "result_path": f"artifacts/{task_id}.md",
    }


def test_delivery_gate_passes_with_completed_qa_and_qc():
    job = {
        "job_id": "job-ready",
        "status": "qc",
        "tasks": [
            _task("T1", "planner"),
            _task("T2", "developer"),
            _task("T3", "qa"),
            _task("T4", "qc"),
        ],
        "final_result_path": None,
    }

    result = gate.evaluate_job(job)

    assert result["ready"] is True
    assert result["verdict"] == "delivery-ready"
    assert result["findings"] == []


def test_delivery_gate_blocks_missing_qc():
    job = {
        "job_id": "job-missing-qc",
        "status": "qa",
        "tasks": [_task("T1", "developer"), _task("T2", "qa")],
    }

    result = gate.evaluate_job(job)

    assert result["ready"] is False
    assert [item["code"] for item in result["findings"]] == ["required_role_missing"]
    assert "qc" in result["findings"][0]["message"]


def test_delivery_gate_blocks_failed_or_pending_tasks():
    job = {
        "job_id": "job-blocked",
        "status": "reviewing",
        "tasks": [
            _task("T1", "developer", "failed"),
            _task("T2", "qa", "completed"),
            _task("T3", "qc", "pending"),
        ],
    }

    result = gate.evaluate_job(job)

    assert result["ready"] is False
    assert [item["code"] for item in result["findings"]] == [
        "task_failed_or_blocked",
        "task_not_finished",
        "required_role_missing",
    ]


def test_delivery_gate_can_require_final_result_file(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_ROOT", tmp_path)
    job = state.init_job(job_id="job-final", user_request="Ship")
    state.upsert_task(job, task_id="T1", role="qa", worker="qa", prompt="QA", status="completed")
    job = state.load_job("job-final")
    state.upsert_task(job, task_id="T2", role="qc", worker="qc", prompt="QC", status="completed")
    job = state.load_job("job-final")
    job["final_result_path"] = "artifacts/final.md"
    state.save_job(job)

    missing = gate.evaluate_job(job, require_final_result=True, state_root=tmp_path)
    (tmp_path / "job-final" / "artifacts" / "final.md").write_text("final")
    present = gate.evaluate_job(job, require_final_result=True, state_root=tmp_path)

    assert missing["ready"] is False
    assert missing["findings"][0]["code"] == "final_result_missing"
    assert present["ready"] is True


def test_delivery_gate_rejects_malformed_tasks():
    job = {"job_id": "bad", "status": "qc", "tasks": "not-a-list"}

    try:
        gate.evaluate_job(job)
    except state.CrewStateError as exc:
        assert "job.tasks" in str(exc)
    else:
        raise AssertionError("expected malformed tasks rejection")
