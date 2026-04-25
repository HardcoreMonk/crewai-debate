"""Tests for normalize_tests_command env-adaptation branch (pyenv-without-`python`)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_LIB = _HERE.parent
sys.path.insert(0, str(_LIB))

_spec = importlib.util.spec_from_file_location("harness_phase", _LIB / "phase.py")
phase = importlib.util.module_from_spec(_spec)
sys.modules["harness_phase"] = phase
_spec.loader.exec_module(phase)


def _which_only_python3(name: str):
    # Simulate pyenv-style env: `python` absent, `python3` present.
    if name == "python":
        return None
    if name == "python3":
        return "/usr/bin/python3"
    return None


def _which_both_present(name: str):
    if name == "python":
        return "/usr/bin/python"
    if name == "python3":
        return "/usr/bin/python3"
    return None


def test_rewrites_python_dash_m_pytest(monkeypatch):
    monkeypatch.setattr(phase.shutil, "which", _which_only_python3)
    assert phase.normalize_tests_command("python -m pytest") == "python3 -m pytest"


def test_rewrites_python_script_invocation(monkeypatch):
    monkeypatch.setattr(phase.shutil, "which", _which_only_python3)
    assert phase.normalize_tests_command("python script.py") == "python3 script.py"


def test_does_not_rewrite_python3_token(monkeypatch):
    monkeypatch.setattr(phase.shutil, "which", _which_only_python3)
    # Already `python3` — must remain untouched (no `python33`).
    assert phase.normalize_tests_command("python3 -m pytest") == "python3 -m pytest"


def test_does_not_rewrite_pythonic_token(monkeypatch):
    monkeypatch.setattr(phase.shutil, "which", _which_only_python3)
    # Word-boundary safety: a longer identifier starting with `python` must
    # not be rewritten.
    assert phase.normalize_tests_command("pythonic -v") == "pythonic -v"


def test_does_not_rewrite_python_dot_exe_token(monkeypatch):
    monkeypatch.setattr(phase.shutil, "which", _which_only_python3)
    # Trailing `.exe` (or any `.`-suffixed form) must not be rewritten.
    assert phase.normalize_tests_command("python.exe -m pytest") == "python.exe -m pytest"


def test_empty_string_passes_through(monkeypatch):
    monkeypatch.setattr(phase.shutil, "which", _which_only_python3)
    assert phase.normalize_tests_command("") == ""


def test_no_rewrite_when_python_present(monkeypatch):
    # Negative control: when `python` is on PATH, the command is returned as-is
    # regardless of whether `python3` is also available.
    monkeypatch.setattr(phase.shutil, "which", _which_both_present)
    assert phase.normalize_tests_command("python -m pytest") == "python -m pytest"
