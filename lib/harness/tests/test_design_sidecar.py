"""Tests for ADR-0003 design.md sidecar injection.

Two surfaces under test:
  - `build_plan_prompt(persona, intent, target_repo, approved_design=…)` —
    pure-function prompt composition with and without the sidecar.
  - `_read_design_sidecar(task_slug)` — disk lookup with HARNESS_STATE_ROOT
    pinned to a tmp dir so we never touch the real state/harness/.

Plus integration check: `state.init_state` no longer fatal-errors when the
task directory pre-exists (because the bridge skill wrote design.md first).
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_LIB = _HERE.parent
sys.path.insert(0, str(_LIB))


@pytest.fixture
def phase_with_state_root(tmp_path, monkeypatch):
    """Reload state.py + phase.py with HARNESS_STATE_ROOT pinned to tmp_path
    so tests are isolated and `_read_design_sidecar` looks under tmp_path/state.
    """
    monkeypatch.setenv("HARNESS_STATE_ROOT", str(tmp_path / "state"))
    sys.modules.pop("state", None)
    sys.modules.pop("harness_state", None)
    sys.modules.pop("harness_phase", None)
    spec_state = importlib.util.spec_from_file_location("harness_state", _LIB / "state.py")
    state_mod = importlib.util.module_from_spec(spec_state)
    sys.modules["harness_state"] = state_mod
    sys.modules["state"] = state_mod  # phase.py imports `state` directly
    spec_state.loader.exec_module(state_mod)

    spec_phase = importlib.util.spec_from_file_location("harness_phase", _LIB / "phase.py")
    phase_mod = importlib.util.module_from_spec(spec_phase)
    sys.modules["harness_phase"] = phase_mod
    spec_phase.loader.exec_module(phase_mod)

    return phase_mod, state_mod


# ---- build_plan_prompt: pure-function composition ----


def test_prompt_without_design_matches_pre_adr_shape(phase_with_state_root, tmp_path):
    phase, _ = phase_with_state_root
    p = phase.build_plan_prompt(
        persona="PERSONA",
        intent="add greet helper",
        target_repo=tmp_path,
    )
    assert "Approved design context" not in p
    assert "PERSONA" in p
    assert "Intent: add greet helper" in p
    # Section ordering: persona → --- → Task (no design block in between).
    assert p.index("PERSONA") < p.index("---") < p.index("# Task")


def test_prompt_with_design_inserts_block_before_task(phase_with_state_root, tmp_path):
    phase, _ = phase_with_state_root
    design = "Decision A: use mtime fallback.\nDecision B: clock-skew normalize ±24h."
    p = phase.build_plan_prompt(
        persona="PERSONA",
        intent="add gc time filter",
        target_repo=tmp_path,
        approved_design=design,
    )
    assert "## Approved design context (do not deviate)" in p
    assert "Decision A: use mtime fallback." in p
    assert "Decision B: clock-skew normalize" in p
    # Design block precedes the Task header.
    assert p.index("Approved design context") < p.index("# Task")


def test_prompt_empty_design_string_treated_as_absent(phase_with_state_root, tmp_path):
    """Empty / whitespace-only design must NOT inject the block — the
    "do not deviate" framing is misleading when there's nothing to honour."""
    phase, _ = phase_with_state_root
    for empty in ("", "   ", "\n\n\t\n"):
        p = phase.build_plan_prompt(
            persona="PERSONA",
            intent="x",
            target_repo=tmp_path,
            approved_design=empty,
        )
        assert "Approved design context" not in p, f"failed for {empty!r}"


def test_prompt_preserves_design_internal_whitespace(phase_with_state_root, tmp_path):
    """Internal newlines/blank lines inside the design block survive — only
    leading/trailing whitespace is stripped."""
    phase, _ = phase_with_state_root
    design = "  line A\n\nline B  "
    p = phase.build_plan_prompt(
        persona="P",
        intent="i",
        target_repo=tmp_path,
        approved_design=design,
    )
    assert "line A\n\nline B" in p


# ---- _read_design_sidecar: disk lookup ----


def test_read_sidecar_returns_none_when_dir_missing(phase_with_state_root):
    phase, _ = phase_with_state_root
    assert phase._read_design_sidecar("never-created") is None


def test_read_sidecar_returns_none_when_file_missing(phase_with_state_root, tmp_path):
    phase, state_mod = phase_with_state_root
    state_mod.task_dir("with-dir-no-file").mkdir(parents=True, exist_ok=True)
    assert phase._read_design_sidecar("with-dir-no-file") is None


def test_read_sidecar_returns_file_contents(phase_with_state_root):
    phase, state_mod = phase_with_state_root
    d = state_mod.task_dir("with-design")
    d.mkdir(parents=True, exist_ok=True)
    (d / "design.md").write_text("approved decisions here\n")
    assert phase._read_design_sidecar("with-design") == "approved decisions here\n"


# ---- init_state tolerates pre-existing dir ----


def test_init_state_succeeds_when_design_md_pre_exists(phase_with_state_root):
    """The bridge skill writes design.md FIRST, then operator runs `plan`.
    Pre-existing task dir must not block init_state — only state.json
    existence is the real guard."""
    _, state_mod = phase_with_state_root
    slug = "bridge-task"
    d = state_mod.task_dir(slug)
    d.mkdir(parents=True, exist_ok=True)
    (d / "design.md").write_text("debate decisions")

    s = state_mod.init_state(slug, intent="x", target_repo=str(Path.cwd()))
    assert s["task_slug"] == slug
    assert state_mod.state_path(slug).exists()
    # Sidecar untouched by init_state.
    assert (d / "design.md").read_text() == "debate decisions"


def test_init_state_still_fails_when_state_json_already_exists(phase_with_state_root):
    """The relaxation must NOT loosen the real "task already exists" guard."""
    _, state_mod = phase_with_state_root
    slug = "double-init"
    state_mod.init_state(slug, intent="x", target_repo=str(Path.cwd()))
    with pytest.raises(FileExistsError):
        state_mod.init_state(slug, intent="x", target_repo=str(Path.cwd()))
