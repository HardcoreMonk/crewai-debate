from datetime import datetime, timezone

from lib.crew import director
from lib.crew import state


def _config():
    def agent(name, role=None):
        return {
            "name": name,
            "role": role or name,
            "runner": "codex",
            "discord_channel_id": f"{name}-channel",
            "cwd": "/tmp",
            "persona": f"crew/personas/{name}.md",
            "timeout_sec": 60,
            "may_invoke_harness": name == "developer",
        }

    return {
        "director_channel": {"discord_channel_id": "director-channel"},
        "agents": [
            agent("director"),
            agent("planner"),
            agent("designer"),
            {**agent("ue-expert", "domain-expert"), "aliases": ["codex-ue-expert"]},
            agent("developer"),
            agent("critic"),
            agent("qa"),
            agent("qc"),
            agent("docs-release"),
        ],
    }


def test_make_job_id_is_state_safe_for_korean_request():
    job_id = director.make_job_id(
        "한국어 요청 UI 개선",
        now=datetime(2026, 4, 29, 1, 2, 3, tzinfo=timezone.utc),
    )

    state.validate_job_id(job_id)
    assert job_id == "20260429-010203-ui"


def test_infer_roles_adds_keyword_roles_in_canonical_order():
    roles = director.infer_roles("Review UE5 UI docs release risk")

    assert roles == [
        "planner",
        "designer",
        "ue-expert",
        "developer",
        "critic",
        "qa",
        "qc",
        "docs-release",
    ]


def test_create_job_writes_pending_task_graph(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_ROOT", tmp_path)

    job = director.create_job(
        request="Build a UI flow",
        job_id="job-director",
        roles=["planner", "designer", "developer", "qa", "qc"],
        skip_roles=[],
        config=_config(),
    )

    saved = state.load_job("job-director")

    assert job["status"] == "planning"
    assert job["director_channel_id"] == "director-channel"
    assert [task["worker"] for task in saved["tasks"]] == [
        "planner",
        "designer",
        "developer",
        "qa",
        "qc",
    ]
    assert saved["tasks"][0]["depends_on"] == []
    assert saved["tasks"][1]["depends_on"] == ["T01-planner"]
    assert saved["tasks"][-1]["depends_on"] == ["T04-qa"]
    assert saved["director_plan"]["roles"] == ["planner", "designer", "developer", "qa", "qc"]


def test_create_job_can_resolve_role_by_agent_alias(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_ROOT", tmp_path)

    job = director.create_job(
        request="Need Unreal guidance",
        job_id="job-alias",
        roles=["codex-ue-expert", "qa", "qc"],
        skip_roles=[],
        config=_config(),
    )

    assert [task["worker"] for task in job["tasks"]] == ["ue-expert", "qa", "qc"]


def test_create_job_rejects_unknown_role(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_ROOT", tmp_path)

    try:
        director.create_job(
            request="Do work",
            job_id="job-unknown",
            roles=["missing"],
            skip_roles=[],
            config=_config(),
        )
    except Exception as exc:
        assert "unknown role/agent" in str(exc)
    else:
        raise AssertionError("expected unknown role rejection")


def test_skip_role_accepts_alias(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_ROOT", tmp_path)

    job = director.create_job(
        request="Need Unreal guidance",
        job_id="job-skip",
        roles=["planner", "codex-ue-expert", "developer", "qa", "qc"],
        skip_roles=["ue-expert"],
        config=_config(),
    )

    assert [task["worker"] for task in job["tasks"]] == ["planner", "developer", "qa", "qc"]
