"""Tests for the --impl-timeout CLI flag and HARNESS_IMPL_TIMEOUT env override."""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_LIB = _HERE.parent
sys.path.insert(0, str(_LIB))


@pytest.fixture
def phase_mod(monkeypatch):
    """Load phase.py via importlib so the module is independent of sys.path
    state (mirrors the sibling test_rate_limit_auto_bypass.py pattern)."""
    monkeypatch.delenv("HARNESS_IMPL_TIMEOUT", raising=False)
    for name in ("state", "phase"):
        sys.modules.pop(name, None)
    state_spec = importlib.util.spec_from_file_location(
        "state", _LIB / "state.py"
    )
    state_mod = importlib.util.module_from_spec(state_spec)
    sys.modules["state"] = state_mod
    state_spec.loader.exec_module(state_mod)

    spec = importlib.util.spec_from_file_location("phase", _LIB / "phase.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["phase"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_cli_only_returns_cli_value(phase_mod, monkeypatch):
    monkeypatch.delenv("HARNESS_IMPL_TIMEOUT", raising=False)
    args = argparse.Namespace(impl_timeout=900)
    assert phase_mod._resolve_impl_timeout(args) == 900


def test_cli_wins_over_env(phase_mod, monkeypatch):
    monkeypatch.setenv("HARNESS_IMPL_TIMEOUT", "300")
    args = argparse.Namespace(impl_timeout=900)
    assert phase_mod._resolve_impl_timeout(args) == 900


def test_env_used_when_cli_none(phase_mod, monkeypatch):
    monkeypatch.setenv("HARNESS_IMPL_TIMEOUT", "1500")
    args = argparse.Namespace(impl_timeout=None)
    assert phase_mod._resolve_impl_timeout(args) == 1500


def test_default_when_cli_none_and_env_unset(phase_mod, monkeypatch):
    monkeypatch.delenv("HARNESS_IMPL_TIMEOUT", raising=False)
    args = argparse.Namespace(impl_timeout=None)
    assert phase_mod._resolve_impl_timeout(args) == phase_mod.PHASE_TIMEOUTS["impl"]


def test_unparseable_env_warns_and_defaults(phase_mod, monkeypatch, capsys):
    monkeypatch.setenv("HARNESS_IMPL_TIMEOUT", "abc")
    args = argparse.Namespace(impl_timeout=None)
    default = phase_mod.PHASE_TIMEOUTS["impl"]
    assert phase_mod._resolve_impl_timeout(args) == default
    err = capsys.readouterr().err
    assert f"impl: warn — HARNESS_IMPL_TIMEOUT=abc ignored, using default {default}s" in err


@pytest.mark.parametrize("raw", ["0", "-5"])
def test_non_positive_env_warns_and_defaults(phase_mod, monkeypatch, capsys, raw):
    monkeypatch.setenv("HARNESS_IMPL_TIMEOUT", raw)
    args = argparse.Namespace(impl_timeout=None)
    default = phase_mod.PHASE_TIMEOUTS["impl"]
    assert phase_mod._resolve_impl_timeout(args) == default
    err = capsys.readouterr().err
    assert f"impl: warn — HARNESS_IMPL_TIMEOUT={raw} ignored, using default {default}s" in err
