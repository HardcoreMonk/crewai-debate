"""Shared pytest fixtures + helpers for `lib/harness/tests/`.

pytest auto-discovers `conftest.py`, so any fixture or hook defined here is
available without explicit import. Plain helper functions (`init_repo`,
`git_in`) are still discoverable via `from conftest import …` from any
test module under this directory.

This file is the de-duplication target for the rule-of-three boilerplate
that accumulated across §13.6 #14/#15/#16 work — the `importlib.util`
module-loading dance was repeated in ~22 test files, and a near-identical
`_init_repo` shell-out lived in three of them.

New tests should depend on the fixtures/helpers here. Existing tests
keep their inline copies for now; migration happens lazily as each test
gets touched for an unrelated reason.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_LIB = _HERE.parent

# Make `lib/harness/` importable as `import state` / `import phase` / etc.,
# matching the `sys.path.insert(0, str(_HERE))` dance in `phase.py` itself.
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))


def _load_module(name: str):
    """Force-reload a sibling module by file path. Each call replaces any
    prior `sys.modules[name]` entry — needed because tests that monkeypatch
    module-level state (env vars, sys.path tweaks, etc.) leave stale
    bindings that fight the next test's setup."""
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, _LIB / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def state_mod():
    """Fresh-loaded `state` module. Function-scoped so HARNESS_STATE_ROOT
    monkeypatching in a test doesn't leak across the suite."""
    return _load_module("state")


@pytest.fixture
def phase_mod(state_mod):
    """Fresh-loaded `phase` module. Depends on `state_mod` so the import
    order matches phase.py's expectation that `state` is already in
    `sys.modules` when `import state` runs."""
    return _load_module("phase")


@pytest.fixture
def gh_mod():
    """Fresh-loaded `gh` module."""
    return _load_module("gh")


def git_in(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run `git -C <repo> <args>` capturing output. Mirrors the inline
    pattern used by `phase.py::git()`. Use in tests that need to drive
    a tmp-path repo without re-implementing the subprocess invocation
    in every file."""
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=check,
    )


def init_repo(
    tmp_path: Path,
    *,
    branch: str = "main",
    seed_file: str = "tracked.txt",
    seed_content: str = "baseline\n",
) -> Path:
    """Initialise a fresh git repo under `tmp_path` with one tracked-and-
    committed file so subsequent `git status` runs have a non-empty HEAD.

    Defaults match the most common test shape; override `branch` for
    fail-fast tests that care about main-vs-master detection, and
    override `seed_file` if the test needs a specific name.
    """
    subprocess.run(
        ["git", "init", "-q", "-b", branch, str(tmp_path)], check=True,
    )
    git_in(tmp_path, "config", "user.email", "t@t")
    git_in(tmp_path, "config", "user.name", "t")
    (tmp_path / seed_file).write_text(seed_content)
    git_in(tmp_path, "add", seed_file)
    git_in(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path
