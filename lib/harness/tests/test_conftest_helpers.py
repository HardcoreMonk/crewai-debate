"""Tests for `conftest.py` helpers themselves — caught by the 3-reviewer pass."""
from __future__ import annotations

import subprocess
import sys

import pytest

from conftest import _PHASE_LAZY_DEPS, _load_module, git_in, init_repo


def test_git_in_failure_includes_stderr(tmp_path):
    """`subprocess.CalledProcessError`'s default repr drops stderr; git_in
    must surface it so test failures are diagnosable."""
    init_repo(tmp_path)
    with pytest.raises(subprocess.CalledProcessError) as ei:
        git_in(tmp_path, "checkout", "no-such-branch")
    # stderr propagates as the exception's `stderr` attribute and
    # ends up in pytest's failure dump.
    assert ei.value.stderr
    assert "no-such-branch" in (ei.value.stderr or "")


def test_git_in_check_false_returns_proc_on_failure(tmp_path):
    """With `check=False`, callers can inspect returncode/stdout/stderr."""
    init_repo(tmp_path)
    proc = git_in(tmp_path, "checkout", "no-such-branch", check=False)
    assert proc.returncode != 0
    assert proc.stderr  # populated, not empty


def test_load_module_also_pop_purges_lazy_deps(tmp_path, monkeypatch):
    """Pop targets in `also_pop` must be removed from `sys.modules` before
    the named module reloads. Otherwise a fresh `phase` would lazy-import
    a stale `coderabbit`/`gh` left over from a prior test."""
    # Seed sys.modules with sentinel objects under the lazy-dep names.
    sentinel = object()
    monkeypatch.setitem(sys.modules, "coderabbit", sentinel)
    monkeypatch.setitem(sys.modules, "runner", sentinel)
    monkeypatch.setitem(sys.modules, "gh", sentinel)

    _load_module("state", also_pop=_PHASE_LAZY_DEPS)

    # All three should have been popped before the load.
    assert sys.modules.get("coderabbit") is not sentinel
    assert sys.modules.get("runner") is not sentinel
    assert sys.modules.get("gh") is not sentinel


def test_phase_mod_fixture_reloads_lazy_deps(phase_mod, monkeypatch):
    """The `phase_mod` fixture composes `_PHASE_LAZY_DEPS` so phase's
    lazy `import coderabbit` / `import gh` paths see the fresh modules."""
    # Just verify the fixture-yielded phase is callable + has the expected
    # symbol set; the also_pop semantics are exercised by the previous test.
    assert hasattr(phase_mod, "cmd_review_wait")
    assert hasattr(phase_mod, "ensure_clean_repo")
