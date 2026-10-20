"""The prepare-commit-msg hook: your commit message is waiting in the editor."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import Optional

from . import generate, git, guard, llm, stats
from .config import load_config

MARKER = "# sloth-commit-managed-hook"
BLOCKED = 3  # exit code the hook script turns into "abort the commit"


class HookError(RuntimeError):
    pass


def _script() -> str:
    py = Path(sys.executable).as_posix()
    return (
        "#!/bin/sh\n"
        f"{MARKER} (remove with: sloth-commit hook uninstall)\n"
        '[ -n "$SLOTH_SKIP" ] && exit 0\n'
        f'PY="{py}"\n'
        'if [ -e "$PY" ]; then\n'
        '  "$PY" -m sloth_commit hook-run "$1" "$2"\n'
        "else\n"
        '  sloth-commit hook-run "$1" "$2"\n'
        "fi\n"
        "rc=$?\n"
        f'[ "$rc" -eq {BLOCKED} ] && exit 1\n'
        "exit 0\n"
    )


def hook_path(cwd: Optional[str] = None) -> Path:
    return git.git_path("hooks/prepare-commit-msg", cwd)


def is_ours(path: Path) -> bool:
    try:
        return MARKER in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def install(cwd: Optional[str] = None, force: bool = False) -> Path:
    path = hook_path(cwd)
    if path.exists() and not is_ours(path) and not force:
        raise HookError(
            f"{path} already exists and was not created by sloth-commit. "
            "Re-run with --force to replace it."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_script(), encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def uninstall(cwd: Optional[str] = None) -> Optional[Path]:
    path = hook_path(cwd)
    if not path.exists():
        return None
    if not is_ours(path):
        raise HookError(f"{path} was not created by sloth-commit, so it was left alone.")
    path.unlink()
    return path


def run(msg_file: str, source: str = "", cwd: Optional[str] = None) -> int:
    """Called by git. Fills the commit message file; never blocks a commit by accident."""
    if source or os.environ.get("SLOTH_SKIP"):
        return 0  # -m, merge, squash, amend, template: the message is already decided
    try:
        if not git.staged_files(cwd) or git.operation_in_progress(cwd):
            return 0
        cfg = load_config()

        findings = guard.scan_staged(cwd)
        if findings:
            print("sloth-commit: possible secrets in staged changes:", file=sys.stderr)
            for f in findings:
                print("  " + f.format(), file=sys.stderr)
            if os.environ.get("SLOTH_GUARD", "").lower() == "block":
                print("sloth-commit: commit blocked (SLOTH_GUARD=block). Set SLOTH_SKIP=1 to override.", file=sys.stderr)
                return BLOCKED

        result = generate.draft(cfg, cwd)
        stats.record(result.message, result.seconds, result.mode, cfg)
        path = Path(msg_file)
        existing = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        path.write_text(result.message + "\n\n" + existing.lstrip("\n"), encoding="utf-8")
        print(f"sloth-commit: drafted a message with {result.model} in {result.seconds:.1f}s", file=sys.stderr)
    except (llm.LLMError, git.GitError, generate.NothingStaged, OSError) as err:
        print(f"sloth-commit: skipped ({err})", file=sys.stderr)
    return 0
