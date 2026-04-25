
<details>
<summary>🧹 Nitpick comments (1)</summary><blockquote>

<details>
<summary>lib/harness/tests/test_merge_dry_run_rerun.py (1)</summary><blockquote>

`62-70`: **Consider asserting `merge_pr` is invoked exactly once.**

This would harden the test against regressions where a second invocation accidentally reaches GitHub merge logic before failing.


<details>
<summary>♻️ Proposed cleanup</summary>

```diff
-def _install_gh_mocks(phase, monkeypatch, *, merge_sha: str = "abc123def456") -> None:
+def _install_gh_mocks(phase, monkeypatch, *, merge_sha: str = "abc123def456") -> dict:
```
</details>

</blockquote></details>

</blockquote></details>
