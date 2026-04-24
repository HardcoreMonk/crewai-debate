"""Mock end-to-end MVP-D dry run.

Exercises the 5 review phases (review-wait → merge) against a local scratch git
repo with the `gh` module and `runner.run_claude` monkey-patched out. No network
calls, no LLM calls — this verifies state-machine arithmetic and data flow only.

Live-LLM verification happens in the real smoke (task #13) against a PR.

Run:
    python lib/harness/tests/mock_e2e.py
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_LIB = _HERE.parent
sys.path.insert(0, str(_LIB))

import state           # noqa: E402
import runner          # noqa: E402
import gh              # noqa: E402
import phase           # noqa: E402


# ---- scratch repo setup ----


def _setup_scratch_repo() -> Path:
    root = Path(tempfile.mkdtemp(prefix="harness-mock-"))
    subprocess.run(["git", "-C", str(root), "init", "-q", "-b", "main"], check=True)
    # Mirror fixture-referenced paths so apply-phase edits can land.
    (root / "lib" / "harness").mkdir(parents=True)
    (root / "lib" / "harness" / "state.py").write_text(
        "# mock file for nitpick autofix\n"
        "def save_state(state):\n"
        "    return state\n"
    )
    (root / "lib" / "harness" / "runner.py").write_text(
        "# mock file for refactor autofix\n"
        "def run_claude():\n"
        "    pass\n"
    )
    subprocess.run(
        ["git", "-C", str(root),
         "-c", "user.email=t@t", "-c", "user.name=t",
         "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(root),
         "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", "initial"], check=True)
    # Mimic a PR head branch.
    subprocess.run(["git", "-C", str(root), "checkout", "-q", "-b", "feat/mock"], check=True)
    (root / "extra.txt").write_text("WIP\n")
    subprocess.run(
        ["git", "-C", str(root),
         "-c", "user.email=t@t", "-c", "user.name=t",
         "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(root),
         "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", "WIP"], check=True)
    return root


# ---- fixtures loader ----


_FIX = _LIB / "fixtures" / "coderabbit"


def _load_fixture(name: str) -> dict:
    return json.loads((_FIX / name).read_text())


# ---- monkey-patches ----


def _patch_gh(scratch_repo: Path) -> None:
    """Route every gh.* call to a canned fixture response."""
    state_store = {
        "merged": False,
        "reply_comment_id": 9999,
    }

    def fake_pr_view(base_repo, pr_number, fields=gh.DEFAULT_PR_VIEW_FIELDS):
        base = {
            "number": pr_number,
            "state": "OPEN",
            "title": "mock PR",
            "body": "",
            "author": {"login": "hardcoremonk"},
            "baseRefName": "main",
            "headRefName": "feat/mock",
            "headRefOid": "abc",
            "isDraft": False,
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "reviewDecision": "APPROVED",
            "statusCheckRollup": [{"name": "ci", "conclusion": "SUCCESS"}],
            "url": f"https://github.com/{base_repo}/pull/{pr_number}",
            "createdAt": "2026-04-25T00:00:00Z",
            "updatedAt": "2026-04-25T03:00:00Z",
            "mergeCommit": {"oid": "mock-merge-sha"} if state_store["merged"] else None,
        }
        # Trim to requested fields to match real gh behaviour.
        req = set(f.strip() for f in fields.split(","))
        return {k: v for k, v in base.items() if k in req}

    def fake_list_reviews(base_repo, pr_number):
        # Return only the "complete" review so review-wait succeeds immediately.
        return [_load_fixture("review_complete.json")]

    def fake_list_inline_comments(base_repo, pr_number):
        return [
            _load_fixture("inline_nitpick.json"),
            _load_fixture("inline_potential_issue.json"),
            _load_fixture("inline_refactor.json"),
            _load_fixture("inline_resolved.json"),
        ]

    def fake_list_review_thread_resolutions(base_repo, pr_number):
        # Only the "resolved" fixture thread is resolved.
        resolved_id = _load_fixture("inline_resolved.json")["id"]
        return [gh.ThreadResolution(comment_id=resolved_id, is_resolved=True)]

    def fake_post_pr_comment(base_repo, pr_number, body):
        return {
            "id": state_store["reply_comment_id"],
            "html_url": f"https://github.com/{base_repo}/pull/{pr_number}#issuecomment-{state_store['reply_comment_id']}",
            "body": body,
        }

    def fake_merge_pr(base_repo, pr_number, *, strategy="squash", commit_title=None, dry_run=False):
        if dry_run:
            return None
        state_store["merged"] = True
        return "mock-merge-sha"

    gh.pr_view = fake_pr_view
    gh.list_reviews = fake_list_reviews
    gh.list_inline_comments = fake_list_inline_comments
    gh.list_review_thread_resolutions = fake_list_review_thread_resolutions
    gh.post_pr_comment = fake_post_pr_comment
    gh.merge_pr = fake_merge_pr


def _patch_runner(scratch_repo: Path) -> None:
    """Fake implementer: for each apply prompt, infer the target file from the
    prompt body and write a canned edit matching the CodeRabbit nitpick diff.
    No real LLM call."""
    def fake_run_claude(*, prompt, cwd, log_path, timeout_sec, stdout_path=None):
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(f"[fake_run_claude]\nprompt head:\n{prompt[:200]}\n")

        # Extract the file path from the apply prompt. Format: "File: <path>\n"
        import re
        m = re.search(r"^File:\s*(\S+)\s*$", prompt, re.MULTILINE)
        if m:
            file_rel = m.group(1)
            target = Path(cwd) / file_rel
            if target.exists():
                original = target.read_text()
                if "save_state" in original:
                    # Nitpick fixture: rename shadowed var.
                    edited = original.replace(
                        "def save_state(state):\n    return state\n",
                        "def save_state(st):\n    return st\n",
                    )
                elif "run_claude" in original:
                    # Refactor fixture: add a trivial docstring so diff is non-empty.
                    edited = original.replace(
                        "def run_claude():\n    pass\n",
                        'def run_claude():\n    """Invoke claude."""\n    pass\n',
                    )
                else:
                    edited = original + "# harness-mock edit\n"
                target.write_text(edited)
        return runner.RunResult(exit_code=0, stdout="applied", log_path=log_path, timed_out=False)

    runner.run_claude = fake_run_claude


# ---- assertions ----


def _assert(cond: bool, msg: str, failures: list[str]) -> None:
    mark = "ok" if cond else "FAIL"
    print(f"  [{mark}] {msg}")
    if not cond:
        failures.append(msg)


# ---- main ----


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="keep scratch dirs after run")
    args = parser.parse_args()

    failures: list[str] = []
    scratch = _setup_scratch_repo()
    print(f"scratch repo: {scratch}")

    task_slug = f"mock-e2e-{os.getpid()}"
    # Route state to a throwaway dir so we don't pollute the crewai state tree.
    state_root = Path(tempfile.mkdtemp(prefix="harness-mock-state-"))
    os.environ["HARNESS_STATE_ROOT"] = str(state_root)
    # Reload state module so it picks up the new env.
    import importlib
    importlib.reload(state)
    importlib.reload(phase)  # phase.state is the same module object

    _patch_gh(scratch)
    _patch_runner(scratch)

    # Shared CLI args namespace (argparse.Namespace substitute).
    class NS:
        pass
    a = NS()
    a.task_slug = task_slug
    a.pr = 42
    a.base_repo = "HardcoreMonk/crewai-debate"
    a.target_repo = str(scratch)
    a.intent = None
    a.dry_run = True  # merge stays dry-run to match expected first-smoke mode

    # 1) review-wait
    print("\n== review-wait ==")
    rc = phase.cmd_review_wait(a)
    _assert(rc == 0, "review-wait exit 0", failures)
    s = state.load_state(task_slug)
    _assert(s["phases"]["review-wait"]["status"] == state.STATUS_COMPLETED, "wait completed", failures)
    _assert(s["phases"]["review-wait"]["actionable_count"] == 3, "actionable == 3", failures)
    _assert(s["head_branch"] == "feat/mock", "head_branch captured", failures)

    # 2) review-fetch
    print("\n== review-fetch ==")
    rc = phase.cmd_review_fetch(a)
    _assert(rc == 0, "fetch exit 0", failures)
    s = state.load_state(task_slug)
    _assert(s["phases"]["review-fetch"]["status"] == state.STATUS_COMPLETED, "fetch completed", failures)
    cpath = s["phases"]["review-fetch"]["comments_path"]
    _assert(Path(cpath).exists(), f"comments.json at {cpath}", failures)
    comments = json.loads(Path(cpath).read_text())
    _assert(len(comments) == 4, f"4 comments parsed (got {len(comments)})", failures)
    auto = [c for c in comments if c["auto_applicable"]]
    # nitpick + refactor => auto. potential_issue filtered. resolved filtered.
    _assert(len(auto) == 2, f"2 auto-applicable (got {len(auto)})", failures)

    # 3) review-apply (fake_run_claude only edits files matching the nitpick fixture).
    print("\n== review-apply ==")
    rc = phase.cmd_review_apply(a)
    _assert(rc == 0, "apply exit 0", failures)
    s = state.load_state(task_slug)
    applied = s["phases"]["review-apply"]["applied_commits"]
    skipped = s["phases"]["review-apply"]["skipped_comment_ids"]
    # The nitpick fixture targets lib/harness/state.py but our scratch has lib/state.py.
    # So boundary check will fail OR the edit won't match. Either way the phase completes.
    print(f"    applied={len(applied)} skipped={len(skipped)}")
    _assert(s["phases"]["review-apply"]["status"] == state.STATUS_COMPLETED, "apply completed", failures)

    # 4) review-reply
    print("\n== review-reply ==")
    rc = phase.cmd_review_reply(a)
    _assert(rc == 0, "reply exit 0", failures)
    s = state.load_state(task_slug)
    _assert(s["phases"]["review-reply"]["status"] == state.STATUS_COMPLETED, "reply completed", failures)
    _assert(s["phases"]["review-reply"]["posted_comment_id"] == 9999, "posted comment id = 9999", failures)

    # 5) merge (dry-run, since skipped may be non-empty)
    print("\n== merge (dry-run) ==")
    rc = phase.cmd_merge(a)
    if skipped:
        # Gate blocks because skipped_comments > 0 — expected behaviour.
        s = state.load_state(task_slug)
        _assert(s["phases"]["merge"]["status"] == state.STATUS_FAILED,
                f"merge blocked by skipped ({len(skipped)})", failures)
    else:
        _assert(rc == 0, "merge dry-run exit 0", failures)
        s = state.load_state(task_slug)
        _assert(s["phases"]["merge"]["status"] == state.STATUS_COMPLETED, "merge dry-run completed", failures)
        _assert(s["phases"]["merge"]["dry_run"] is True, "merge flagged dry_run", failures)

    # Cleanup
    if not args.keep:
        import shutil
        shutil.rmtree(scratch, ignore_errors=True)
        shutil.rmtree(state_root, ignore_errors=True)

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("mock E2E: all phases verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
