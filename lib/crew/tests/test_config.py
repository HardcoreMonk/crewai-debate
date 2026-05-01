from pathlib import Path

from lib.crew import config


def test_example_config_resolves_legacy_aliases():
    cfg = config.load_config(Path("crew/agents.example.json"))

    assert config.resolve_agent("claude-coder", cfg).name == "developer"
    assert config.resolve_agent("codex-critic", cfg).name == "critic"
    assert config.resolve_agent("codex-ue-expert", cfg).name == "ue-expert"
    assert config.resolve_agent("developer", cfg).discord_account_id == "claudeai-bot"


def test_valid_agent_names_includes_aliases_and_canonical_names():
    cfg = config.load_config(Path("crew/agents.example.json"))
    names = config.valid_agent_names(cfg)

    assert "developer" in names
    assert "claude-coder" in names
    assert "qa" in names
    assert "qc" in names


def test_example_config_declares_discord_accounts():
    cfg = config.load_config(Path("crew/agents.example.json"))

    assert config.director_discord_account_id(cfg) == "crewai-bot"
    assert config.discord_account_ids(cfg) == [
        "claudeai-bot",
        "codexai-bot",
        "crewai-bot",
    ]


def test_rejects_duplicate_alias():
    cfg = {
        "agents": [
            {
                "name": "a",
                "role": "x",
                "runner": "codex",
                "discord_channel_id": "1",
                "cwd": "/tmp",
                "persona": "crew/personas/director.md",
                "aliases": ["dup"],
            },
            {
                "name": "b",
                "role": "y",
                "runner": "codex",
                "discord_channel_id": "2",
                "cwd": "/tmp",
                "persona": "crew/personas/director.md",
                "aliases": ["dup"],
            },
        ]
    }

    try:
        config.agents_by_name(cfg)
    except config.CrewConfigError as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("expected duplicate alias rejection")
