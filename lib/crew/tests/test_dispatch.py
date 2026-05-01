from pathlib import Path
from types import SimpleNamespace

from lib.crew import config
from lib.crew import dispatch
from lib.crew import state


def test_enforce_relay_header_is_idempotent():
    task = "codex-critic 가 제기한 내용:\nissue\n\nfix this"

    assert dispatch.enforce_relay_header(task, "codex-critic") == task


def test_enforce_relay_header_prepends_when_missing():
    task = "issue\n\nfix this"

    assert dispatch.enforce_relay_header(task, "codex-critic").startswith(
        "codex-critic 가 제기한 내용:\n"
    )


def test_truncate_for_discord_preserves_marker():
    out = dispatch.truncate_for_discord("x" * 300, marker="[marker]\n", limit=30)

    assert out.startswith("[marker]\n")
    assert "truncated" in out


def test_post_discord_uses_openclaw_account(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(dispatch.shutil, "which", lambda cmd: "/usr/bin/openclaw")

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(dispatch.subprocess, "run", fake_run)

    dispatch._post_discord(
        "channel-1",
        "hello",
        tmp_path / "dispatch.log",
        account_id="codexai-bot",
    )

    assert calls == [[
        "openclaw",
        "message",
        "send",
        "--channel",
        "discord",
        "--target",
        "channel-1",
        "--message",
        "hello",
        "--account",
        "codexai-bot",
    ]]


def test_build_director_summary_mentions_artifact():
    agent = config.Agent(
        name="qa",
        role="qa",
        runner="codex",
        discord_channel_id="1",
        cwd="/tmp",
        persona="crew/personas/qa.md",
        timeout_sec=60,
        may_invoke_harness=False,
    )
    result = dispatch.DispatchResult(
        exit_code=0,
        stdout="ok",
        log_path=Path("/tmp/log"),
        out_path=Path("/tmp/out"),
        timed_out=False,
    )

    summary = dispatch.build_director_summary(
        agent=agent,
        task="Run QA",
        result=result,
        job_id="job-1",
        task_id="T1",
        artifact_path=Path("state/crew/job-1/artifacts/T1.md"),
    )

    assert "qa completed" in summary
    assert "job-1" in summary
    assert "artifacts/T1.md" in summary


def test_worker_lock_reports_busy(tmp_path):
    with dispatch.worker_lock("qa", lock_dir=tmp_path) as first:
        assert first.acquired
        with dispatch.worker_lock("qa", lock_dir=tmp_path, policy="fail") as second:
            assert not second.acquired
            assert second.path.name == "qa.lock"


def test_dispatch_busy_blocks_job_without_running_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_ROOT", tmp_path / "state")
    monkeypatch.setattr(dispatch, "OPENCLAW_STATE_DIR", tmp_path / "oc-state")
    monkeypatch.setattr(dispatch, "LOCK_DIR", tmp_path / "locks")
    monkeypatch.setattr(dispatch, "_post_discord", lambda *args, **kwargs: None)

    cwd = tmp_path / "worker"
    cwd.mkdir()
    cfg_path = tmp_path / "agents.json"
    cfg_path.write_text(
        """
{
  "director_channel": {"discord_channel_id": "director"},
  "agents": [
    {
      "name": "qa",
      "role": "qa",
      "runner": "codex",
      "discord_channel_id": "qa-channel",
      "cwd": "%s",
      "persona": "crew/personas/qa.md",
      "timeout_sec": 60,
      "may_invoke_harness": false
    }
  ]
}
"""
        % cwd
    )

    def fail_run(*args, **kwargs):
        raise AssertionError("_run_agent should not run while worker is busy")

    monkeypatch.setattr(dispatch, "_run_agent", fail_run)

    with dispatch.worker_lock("qa", lock_dir=tmp_path / "locks") as lock:
        assert lock.acquired
        code = dispatch.dispatch(SimpleNamespace(
            agent="qa",
            task="check it",
            task_from_job=False,
            channel=None,
            relay_source=None,
            job_id="job-busy",
            task_id="T1",
            job_request="check it",
            director_channel=None,
            config=str(cfg_path),
            log_dir=str(tmp_path),
            busy_policy="fail",
            lock_timeout=0,
            lock_dir=str(tmp_path / "locks"),
            no_director_summary=True,
        ))

    saved = state.load_job("job-busy")

    assert code == dispatch.BUSY_EXIT_CODE
    assert saved["tasks"][0]["status"] == "blocked"
    assert saved["tasks"][0]["note"] == "worker busy"


def test_dispatch_can_read_task_prompt_from_job(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_ROOT", tmp_path / "state")
    monkeypatch.setattr(dispatch, "OPENCLAW_STATE_DIR", tmp_path / "oc-state")
    monkeypatch.setattr(dispatch, "LOCK_DIR", tmp_path / "locks")
    monkeypatch.setattr(dispatch, "_post_discord", lambda *args, **kwargs: None)

    cwd = tmp_path / "worker"
    cwd.mkdir()
    cfg_path = tmp_path / "agents.json"
    cfg_path.write_text(
        """
{
  "agents": [
    {
      "name": "planner",
      "role": "planner",
      "runner": "codex",
      "discord_channel_id": "planner-channel",
      "cwd": "%s",
      "persona": "crew/personas/product-planner.md",
      "timeout_sec": 60,
      "may_invoke_harness": false
    }
  ]
}
"""
        % cwd
    )
    job = state.init_job(job_id="job-prompt", user_request="Plan")
    state.upsert_task(
        job,
        task_id="T1",
        role="planner",
        worker="planner",
        prompt="Prompt from job",
        status="pending",
    )

    def fake_run(agent, task, log_path, out_path):
        assert task == "Prompt from job"
        out_path.write_text("ok")
        return dispatch.DispatchResult(
            exit_code=0,
            stdout="ok",
            log_path=log_path,
            out_path=out_path,
            timed_out=False,
        )

    monkeypatch.setattr(dispatch, "_run_agent", fake_run)

    code = dispatch.dispatch(SimpleNamespace(
        agent="planner",
        task=None,
        task_from_job=True,
        channel=None,
        relay_source=None,
        job_id="job-prompt",
        task_id="T1",
        job_request=None,
        director_channel=None,
        config=str(cfg_path),
        log_dir=str(tmp_path),
        busy_policy="fail",
        lock_timeout=0,
        lock_dir=str(tmp_path / "locks"),
        no_director_summary=True,
    ))

    saved = state.load_job("job-prompt")

    assert code == 0
    assert saved["tasks"][0]["status"] == "completed"


def test_dispatch_blocks_task_until_dependencies_complete(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_ROOT", tmp_path / "state")
    monkeypatch.setattr(dispatch, "OPENCLAW_STATE_DIR", tmp_path / "oc-state")
    monkeypatch.setattr(dispatch, "LOCK_DIR", tmp_path / "locks")
    monkeypatch.setattr(dispatch, "_post_discord", lambda *args, **kwargs: None)

    cwd = tmp_path / "worker"
    cwd.mkdir()
    cfg_path = tmp_path / "agents.json"
    cfg_path.write_text(
        """
{
  "agents": [
    {
      "name": "developer",
      "role": "developer",
      "runner": "codex",
      "discord_channel_id": "developer-channel",
      "cwd": "%s",
      "persona": "crew/personas/coder.md",
      "timeout_sec": 60,
      "may_invoke_harness": true
    }
  ]
}
"""
        % cwd
    )
    job = state.init_job(job_id="job-wait", user_request="Build")
    state.upsert_task(
        job,
        task_id="T1",
        role="planner",
        worker="planner",
        prompt="Plan",
        status="pending",
    )
    job = state.load_job("job-wait")
    state.upsert_task(
        job,
        task_id="T2",
        role="developer",
        worker="developer",
        prompt="Build",
        status="pending",
        depends_on=["T1"],
    )

    def fail_run(*args, **kwargs):
        raise AssertionError("_run_agent should not run while dependencies are incomplete")

    monkeypatch.setattr(dispatch, "_run_agent", fail_run)

    code = dispatch.dispatch(SimpleNamespace(
        agent="developer",
        task=None,
        task_from_job=True,
        channel=None,
        relay_source=None,
        job_id="job-wait",
        task_id="T2",
        job_request=None,
        director_channel=None,
        config=str(cfg_path),
        log_dir=str(tmp_path),
        busy_policy="fail",
        lock_timeout=0,
        lock_dir=str(tmp_path / "locks"),
        no_director_summary=True,
    ))

    saved = state.load_job("job-wait")

    assert code == dispatch.DEPENDENCY_EXIT_CODE
    assert state.find_task(saved, "T2")["status"] == "pending"


def test_dispatch_task_from_job_includes_completed_dependency_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_ROOT", tmp_path / "state")
    monkeypatch.setattr(dispatch, "OPENCLAW_STATE_DIR", tmp_path / "oc-state")
    monkeypatch.setattr(dispatch, "LOCK_DIR", tmp_path / "locks")
    monkeypatch.setattr(dispatch, "_post_discord", lambda *args, **kwargs: None)

    cwd = tmp_path / "worker"
    cwd.mkdir()
    cfg_path = tmp_path / "agents.json"
    cfg_path.write_text(
        """
{
  "agents": [
    {
      "name": "developer",
      "role": "developer",
      "runner": "codex",
      "discord_channel_id": "developer-channel",
      "cwd": "%s",
      "persona": "crew/personas/coder.md",
      "timeout_sec": 60,
      "may_invoke_harness": true
    }
  ]
}
"""
        % cwd
    )
    job = state.init_job(job_id="job-chain", user_request="Build")
    state.upsert_task(
        job,
        task_id="T1",
        role="planner",
        worker="planner",
        prompt="Plan",
        status="running",
    )
    artifact = state.write_artifact("job-chain", "T1", "planner result")
    job = state.load_job("job-chain")
    state.upsert_task(
        job,
        task_id="T1",
        role="planner",
        worker="planner",
        prompt="Plan",
        status="completed",
        result_path=str(artifact),
    )
    job = state.load_job("job-chain")
    state.upsert_task(
        job,
        task_id="T2",
        role="developer",
        worker="developer",
        prompt="Build from plan",
        status="pending",
        depends_on=["T1"],
    )

    def fake_run(agent, task, log_path, out_path):
        assert task.startswith("Build from plan")
        assert "Completed dependency artifacts:" in task
        assert "### T1 - planner (planner)" in task
        assert "planner result" in task
        out_path.write_text("done")
        return dispatch.DispatchResult(
            exit_code=0,
            stdout="done",
            log_path=log_path,
            out_path=out_path,
            timed_out=False,
        )

    monkeypatch.setattr(dispatch, "_run_agent", fake_run)

    code = dispatch.dispatch(SimpleNamespace(
        agent="developer",
        task=None,
        task_from_job=True,
        channel=None,
        relay_source=None,
        job_id="job-chain",
        task_id="T2",
        job_request=None,
        director_channel=None,
        config=str(cfg_path),
        log_dir=str(tmp_path),
        busy_policy="fail",
        lock_timeout=0,
        lock_dir=str(tmp_path / "locks"),
        no_director_summary=True,
    ))

    saved = state.load_job("job-chain")
    task = state.find_task(saved, "T2")

    assert code == 0
    assert task["status"] == "completed"
    assert task["prompt"] == "Build from plan"
