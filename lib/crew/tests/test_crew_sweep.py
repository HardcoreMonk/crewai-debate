from lib.crew import state
from lib.crew import sweep


def test_collect_rows_lists_resumable_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_ROOT", tmp_path)
    job = state.init_job(job_id="job-001", user_request="Build")
    state.upsert_task(
        job,
        task_id="T1",
        role="planner",
        worker="planner",
        prompt="Plan this",
        status="pending",
    )

    rows = sweep.collect_rows(tmp_path)

    assert rows == [
        {
            "job_id": "job-001",
            "job_status": "intake",
            "task_id": "T1",
            "worker": "planner",
            "task_status": "pending",
            "ready": True,
            "blocked_by": "",
            "next": "python3 lib/crew/dispatch.py --job-id 'job-001' --task-id 'T1' --agent 'planner' --task-from-job",
        }
    ]


def test_collect_rows_skips_delivered_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_ROOT", tmp_path)
    state.init_job(job_id="job-done", user_request="Done", status="delivered")

    assert sweep.collect_rows(tmp_path) == []


def test_collect_rows_marks_jobs_without_active_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_ROOT", tmp_path)
    state.init_job(job_id="job-empty", user_request="Needs director")

    rows = sweep.collect_rows(tmp_path)

    assert rows[0]["task_status"] == "no-active-tasks"
    assert rows[0]["ready"] is True
    assert rows[0]["next"] == "python3 lib/crew/gate.py 'job-empty'"


def test_collect_rows_points_completed_jobs_to_finalize(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_ROOT", tmp_path)
    job = state.init_job(job_id="job-ready", user_request="Needs final")
    state.upsert_task(
        job,
        task_id="T1",
        role="qa",
        worker="qa",
        prompt="QA",
        status="completed",
    )
    job = state.load_job("job-ready")
    state.upsert_task(
        job,
        task_id="T2",
        role="qc",
        worker="qc",
        prompt="QC",
        status="completed",
    )

    rows = sweep.collect_rows(tmp_path)

    assert rows[0]["task_status"] == "no-active-tasks"
    assert rows[0]["next"] == "python3 lib/crew/finalize.py 'job-ready'"


def test_collect_rows_marks_dependency_waiting_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_ROOT", tmp_path)
    job = state.init_job(job_id="job-deps", user_request="Build")
    state.upsert_task(
        job,
        task_id="T1",
        role="planner",
        worker="planner",
        prompt="Plan this",
        status="pending",
    )
    job = state.load_job("job-deps")
    state.upsert_task(
        job,
        task_id="T2",
        role="developer",
        worker="developer",
        prompt="Build this",
        status="pending",
        depends_on=["T1"],
    )

    rows = sweep.collect_rows(tmp_path)

    assert rows[0]["ready"] is True
    assert rows[1]["ready"] is False
    assert rows[1]["blocked_by"] == "T1 (planner, pending)"
    assert rows[1]["next"] == "waiting: dependencies not completed: T1 (planner, pending)"


def test_collect_rows_reports_malformed_dependency_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_ROOT", tmp_path)
    job = state.init_job(job_id="job-bad-deps", user_request="Build")
    job["tasks"] = [{
        "task_id": "T1",
        "role": "developer",
        "worker": "developer",
        "prompt": "Build",
        "status": "pending",
        "depends_on": "T0",
    }]
    state.save_job(job)

    rows = sweep.collect_rows(tmp_path)

    assert rows[0]["job_status"] == "unreadable"
    assert rows[0]["task_status"] == "error"
    assert rows[0]["ready"] is False
    assert "repair job.json" in rows[0]["next"]
