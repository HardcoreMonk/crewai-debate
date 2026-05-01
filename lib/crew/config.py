"""Config loading for Discord crew agents.

The product roster lives in `crew/agents.json` at deploy time. This module also
accepts `crew/agents.example.json` so tests and fresh clones can exercise the
shape before local channel IDs exist.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "crew" / "agents.json"
EXAMPLE_CONFIG_PATH = REPO_ROOT / "crew" / "agents.example.json"


class CrewConfigError(ValueError):
    """Raised when the crew agent config is missing or malformed."""


@dataclass(frozen=True)
class Agent:
    name: str
    role: str
    runner: str
    discord_channel_id: str
    cwd: str
    persona: str
    timeout_sec: int
    may_invoke_harness: bool
    discord_account_id: str | None = None
    aliases: tuple[str, ...] = ()


def default_config_path() -> Path:
    raw = os.environ.get("CREW_AGENTS_CONFIG")
    if raw:
        return Path(raw).expanduser()
    if DEFAULT_CONFIG_PATH.exists():
        return DEFAULT_CONFIG_PATH
    return EXAMPLE_CONFIG_PATH


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    cfg_path = Path(path) if path is not None else default_config_path()
    if not cfg_path.exists():
        raise CrewConfigError(f"crew agent config not found: {cfg_path}")
    try:
        data = json.loads(cfg_path.read_text())
    except json.JSONDecodeError as exc:
        raise CrewConfigError(f"crew agent config is not valid JSON: {cfg_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise CrewConfigError("crew agent config must be a JSON object")
    agents = data.get("agents")
    if not isinstance(agents, list) or not agents:
        raise CrewConfigError("crew agent config requires non-empty `agents` list")
    return data


def _agent_from_obj(obj: dict[str, Any]) -> Agent:
    required = ("name", "role", "runner", "discord_channel_id", "cwd", "persona")
    missing = [key for key in required if not obj.get(key)]
    if missing:
        raise CrewConfigError(f"agent missing required keys: {', '.join(missing)}")
    runner = str(obj["runner"])
    if runner not in ("codex", "claude"):
        raise CrewConfigError(f"agent {obj['name']!r} has unsupported runner {runner!r}")
    timeout_raw = obj.get("timeout_sec", 360)
    try:
        timeout_sec = int(timeout_raw)
    except (TypeError, ValueError) as exc:
        raise CrewConfigError(f"agent {obj['name']!r} timeout_sec must be an int") from exc
    if timeout_sec <= 0:
        raise CrewConfigError(f"agent {obj['name']!r} timeout_sec must be > 0")
    aliases_raw = obj.get("aliases", ())
    if aliases_raw is None:
        aliases_raw = ()
    if not isinstance(aliases_raw, (list, tuple)):
        raise CrewConfigError(f"agent {obj['name']!r} aliases must be a list")
    return Agent(
        name=str(obj["name"]),
        role=str(obj["role"]),
        runner=runner,
        discord_channel_id=str(obj["discord_channel_id"]),
        discord_account_id=str(obj["discord_account_id"]) if obj.get("discord_account_id") else None,
        cwd=str(obj["cwd"]),
        persona=str(obj["persona"]),
        timeout_sec=timeout_sec,
        may_invoke_harness=bool(obj.get("may_invoke_harness", False)),
        aliases=tuple(str(a) for a in aliases_raw),
    )


def agents_by_name(config: dict[str, Any]) -> dict[str, Agent]:
    out: dict[str, Agent] = {}
    for obj in config.get("agents", []):
        if not isinstance(obj, dict):
            raise CrewConfigError("each agent entry must be a JSON object")
        agent = _agent_from_obj(obj)
        for key in (agent.name, *agent.aliases):
            if key in out:
                raise CrewConfigError(f"duplicate agent name/alias: {key}")
            out[key] = agent
    return out


def resolve_agent(name: str, config: dict[str, Any] | None = None) -> Agent:
    config = config or load_config()
    agents = agents_by_name(config)
    try:
        return agents[name]
    except KeyError as exc:
        valid = ", ".join(sorted(agents))
        raise CrewConfigError(f"unknown agent: {name}. valid: {valid}") from exc


def director_channel_id(config: dict[str, Any] | None = None) -> str | None:
    config = config or load_config()
    obj = config.get("director_channel") or {}
    if isinstance(obj, dict):
        raw = obj.get("discord_channel_id")
        return str(raw) if raw else None
    return None


def director_discord_account_id(config: dict[str, Any] | None = None) -> str | None:
    config = config or load_config()
    obj = config.get("director_channel") or {}
    if isinstance(obj, dict):
        raw = obj.get("discord_account_id")
        return str(raw) if raw else None
    return None


def discord_account_ids(config: dict[str, Any] | None = None) -> list[str]:
    config = config or load_config()
    out: set[str] = set()
    director_account = director_discord_account_id(config)
    if director_account:
        out.add(director_account)
    for agent in agents_by_name(config).values():
        if agent.discord_account_id:
            out.add(agent.discord_account_id)
    return sorted(out)


def valid_agent_names(config: dict[str, Any] | None = None) -> list[str]:
    config = config or load_config()
    return sorted(agents_by_name(config))
