from lib.crew import state


def test_init_job_creates_job_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_ROOT", tmp_path)

    job = state.init_job(
        job_id="job-001",
        user_request="Build the thing",
        director_channel_id="123",
    )

    assert job["status"] == "intake"
    assert state.job_path("job-001").exists()
    assert state.artifacts_dir("job-001").is_dir()


def test_upsert_task_and_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_ROOT", tmp_path)
    job = state.init_job(job_id="job-002", user_request="Ship it")

    state.upsert_task(
        job,
        task_id="T1",
        role="qa",
        worker="qa",
        prompt="Verify behavior",
        status="running",
    )
    artifact = state.write_artifact("job-002", "T1", "QA result")
    job = state.load_job("job-002")
    state.upsert_task(
        job,
        task_id="T1",
        role="qa",
        worker="qa",
        prompt="Verify behavior",
        status="completed",
        result_path=str(artifact),
        note="exit=0",
    )

    saved = state.load_job("job-002")
    assert saved["tasks"][0]["status"] == "completed"
    assert saved["tasks"][0]["result_path"].endswith("T1.md")
    assert artifact.read_text() == "QA result"


def test_rejects_unsafe_job_id(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_ROOT", tmp_path)

    try:
        state.init_job(job_id="../escape", user_request="bad")
    except state.CrewStateError as exc:
        assert "invalid job_id" in str(exc)
    else:
        raise AssertionError("expected unsafe job_id rejection")


def test_iter_job_ids_and_active_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_ROOT", tmp_path)
    job = state.init_job(job_id="job-003", user_request="Coordinate")
    state.upsert_task(
        job,
        task_id="T1",
        role="planner",
        worker="planner",
        prompt="Plan",
        status="completed",
    )
    job = state.load_job("job-003")
    state.upsert_task(
        job,
        task_id="T2",
        role="qa",
        worker="qa",
        prompt="Check",
        status="blocked",
    )

    loaded = state.load_job("job-003")

    assert state.iter_job_ids() == ["job-003"]
    assert [task["task_id"] for task in state.active_tasks(loaded)] == ["T2"]
    assert not state.job_is_terminal(loaded)
    assert state.find_task(loaded, "T2")["worker"] == "qa"


def test_incomplete_dependencies_report_waiting_and_missing_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_ROOT", tmp_path)
    job = state.init_job(job_id="job-deps", user_request="Coordinate")
    state.upsert_task(
        job,
        task_id="T1",
        role="planner",
        worker="planner",
        prompt="Plan",
        status="pending",
    )
    job = state.load_job("job-deps")
    state.upsert_task(
        job,
        task_id="T2",
        role="developer",
        worker="developer",
        prompt="Build",
        status="pending",
        depends_on=["T1", "T-missing"],
    )

    loaded = state.load_job("job-deps")
    blockers = state.incomplete_dependencies(loaded, state.find_task(loaded, "T2"))

    assert blockers == [
        {"task_id": "T1", "status": "pending", "worker": "planner", "role": "planner"},
        {"task_id": "T-missing", "status": "missing", "worker": None, "role": None},
    ]
    assert not state.task_is_ready(loaded, state.find_task(loaded, "T2"))

    state.upsert_task(
        loaded,
        task_id="T1",
        role="planner",
        worker="planner",
        prompt="Plan",
        status="completed",
    )
    loaded = state.load_job("job-deps")

    assert state.format_dependency_blockers(
        state.incomplete_dependencies(loaded, state.find_task(loaded, "T2"))
    ) == "T-missing (missing)"


def test_infer_and_refresh_job_lifecycle_status(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_ROOT", tmp_path)
    job = state.init_job(job_id="job-status", user_request="Ship")
    state.upsert_task(
        job,
        task_id="T1",
        role="planner",
        worker="planner",
        prompt="Plan",
        status="pending",
    )
    job = state.load_job("job-status")

    assert state.infer_job_status(job) == "planning"
    assert state.refresh_job_status(job) == "planning"

    state.upsert_task(
        state.load_job("job-status"),
        task_id="T1",
        role="planner",
        worker="planner",
        prompt="Plan",
        status="completed",
    )
    state.upsert_task(
        state.load_job("job-status"),
        task_id="T2",
        role="developer",
        worker="developer",
        prompt="Build",
        status="running",
        depends_on=["T1"],
    )

    assert state.refresh_job_status(state.load_job("job-status")) == "working"

    state.upsert_task(
        state.load_job("job-status"),
        task_id="T2",
        role="developer",
        worker="developer",
        prompt="Build",
        status="completed",
        depends_on=["T1"],
    )
    state.upsert_task(
        state.load_job("job-status"),
        task_id="T3",
        role="qa",
        worker="qa",
        prompt="Verify",
        status="pending",
        depends_on=["T2"],
    )

    assert state.refresh_job_status(state.load_job("job-status")) == "qa"

    job = state.load_job("job-status")
    state.set_job_status(job, "delivered", note="done")

    assert state.infer_job_status(state.load_job("job-status")) == "delivered"
