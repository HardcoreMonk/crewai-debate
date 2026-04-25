"""Tests for _next_adr_number width resolution (DESIGN §13.6 #7-1)."""
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


def _touch(adr_dir: Path, name: str) -> None:
    (adr_dir / name).write_text("")


def test_empty_dir_no_override_returns_default_4(tmp_path):
    n, w = phase._next_adr_number(tmp_path)
    assert (n, w) == (1, 4)


def test_empty_dir_override_3_returns_3(tmp_path):
    n, w = phase._next_adr_number(tmp_path, override_width=3)
    assert (n, w) == (1, 3)


def test_empty_dir_override_5_returns_5(tmp_path):
    n, w = phase._next_adr_number(tmp_path, override_width=5)
    assert (n, w) == (1, 5)


def test_empty_dir_override_zero_falls_back_to_default(tmp_path):
    # Defensive — argparse type=int permits 0 / negative; treat as "unset".
    n, w = phase._next_adr_number(tmp_path, override_width=0)
    assert (n, w) == (1, 4)


def test_empty_dir_override_negative_falls_back_to_default(tmp_path):
    n, w = phase._next_adr_number(tmp_path, override_width=-2)
    assert (n, w) == (1, 4)


def test_existing_4_digit_adrs_authoritative_even_with_override(tmp_path):
    _touch(tmp_path, "0001-foo.md")
    _touch(tmp_path, "0002-bar.md")
    n, w = phase._next_adr_number(tmp_path, override_width=3)
    # Existing convention wins — override silently ignored to avoid mixing
    # widths in one directory.
    assert (n, w) == (3, 4)


def test_existing_3_digit_adrs_authoritative(tmp_path):
    _touch(tmp_path, "001-alpha.md")
    _touch(tmp_path, "002-beta.md")
    n, w = phase._next_adr_number(tmp_path)
    assert (n, w) == (3, 3)


def test_existing_3_digit_with_override_4_still_returns_3(tmp_path):
    _touch(tmp_path, "001-alpha.md")
    n, w = phase._next_adr_number(tmp_path, override_width=4)
    assert (n, w) == (2, 3)


def test_non_adr_files_in_dir_are_ignored(tmp_path):
    # README, dotfiles, non-numbered docs must not influence width detection.
    _touch(tmp_path, "README.md")
    _touch(tmp_path, ".gitkeep")
    _touch(tmp_path, "template.md")
    n, w = phase._next_adr_number(tmp_path, override_width=3)
    assert (n, w) == (1, 3)


def test_underscore_separator_filename_recognised(tmp_path):
    # _ADR_FILENAME_RE accepts both - and _ as separator.
    _touch(tmp_path, "0042_some-decision.md")
    n, w = phase._next_adr_number(tmp_path)
    assert (n, w) == (43, 4)


def test_max_number_used_not_count(tmp_path):
    """If files 1, 5, 7 exist, next is 8 (max+1), not 4 (count+1)."""
    _touch(tmp_path, "0001-a.md")
    _touch(tmp_path, "0005-e.md")
    _touch(tmp_path, "0007-g.md")
    n, w = phase._next_adr_number(tmp_path)
    assert (n, w) == (8, 4)
