"""Thin helpers around the git CLI."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, List, Optional, Sequence, Tuple

LOCKFILES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Pipfile.lock",
    "Cargo.lock",
    "go.sum",
    "composer.lock",
    "Gemfile.lock",
    "uv.lock",
}


class GitError(RuntimeError):
    pass


def run_git(args: Sequence[str], cwd: Optional[str] = None, check: bool = True) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as err:
        raise GitError("git was not found on PATH.") from err
    if check and proc.returncode != 0:
        raise GitError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout if proc.returncode == 0 else ""


def repo_root(cwd: Optional[str] = None) -> str:
    try:
        return run_git(["rev-parse", "--show-toplevel"], cwd).strip()
    except GitError as err:
        raise GitError("Not inside a git repository.") from err


def git_path(name: str, cwd: Optional[str] = None) -> Path:
    out = run_git(["rev-parse", "--git-path", name], cwd).strip()
    path = Path(out)
    return path if path.is_absolute() else Path(cwd or os.getcwd()) / path


def _split_z(out: str) -> List[str]:
    return [p for p in out.split("\0") if p]


def staged_files(cwd: Optional[str] = None, paths: Optional[Sequence[str]] = None) -> List[str]:
    # --no-renames lists both sides of a rename, so a split can't lose the deletion.
    args = ["diff", "--cached", "--name-only", "--no-renames", "-z", "--diff-filter=ACMRTD"]
    if paths:
        args += ["--", *paths]
    return _split_z(run_git(args, cwd))


def unstaged_files(cwd: Optional[str] = None) -> List[str]:
    return _split_z(run_git(["diff", "--name-only", "--no-renames", "-z"], cwd))


def operation_in_progress(cwd: Optional[str] = None) -> Optional[str]:
    for name, label in (
        ("MERGE_HEAD", "a merge"),
        ("CHERRY_PICK_HEAD", "a cherry-pick"),
        ("REVERT_HEAD", "a revert"),
        ("rebase-merge", "a rebase"),
        ("rebase-apply", "a rebase"),
    ):
        if git_path(name, cwd).exists():
            return label
    return None


@dataclass
class StagedDiff:
    files: List[str] = field(default_factory=list)
    stat: str = ""
    text: str = ""
    truncated: bool = False


def _section_path(section: str) -> str:
    m = re.search(r"^\+\+\+ b/(.+)$", section, re.M)
    if m:
        return m.group(1).strip()
    m = re.search(r"^--- a/(.+)$", section, re.M)
    if m:
        return m.group(1).strip()
    m = re.match(r"diff --git a/(.+?) b/(.+)", section)
    return m.group(2).strip() if m else "?"


def staged_diff(
    cwd: Optional[str] = None,
    paths: Optional[Sequence[str]] = None,
    total_chars: int = 6000,
    per_file_chars: int = 1500,
) -> StagedDiff:
    """The staged diff, trimmed so a small local model can digest it."""
    tail = ["--", *paths] if paths else []
    raw = run_git(["diff", "--cached", "--no-color", "--no-ext-diff", "-U2", *tail], cwd)
    stat = run_git(["diff", "--cached", "--stat=100", "--no-color", *tail], cwd).strip()
    files = staged_files(cwd, paths)

    out: List[str] = []
    used = 0
    truncated = False
    for sec in re.split(r"(?m)^(?=diff --git )", raw):
        if not sec.strip():
            continue
        path = _section_path(sec)
        name = os.path.basename(path)
        if name in LOCKFILES or name.endswith((".min.js", ".min.css")):
            out.append(f"# {path}: generated or lock file changed (contents omitted)\n")
            continue
        if re.search(r"(?m)^(Binary files .* differ|GIT binary patch)", sec):
            out.append(f"# {path}: binary file changed\n")
            continue
        body = sec
        if len(body) > per_file_chars:
            body = body[:per_file_chars] + f"\n... [truncated {len(sec) - per_file_chars} chars]\n"
            truncated = True
        if used + len(body) > total_chars:
            out.append(f"{sec.splitlines()[0]}\n... [omitted: diff budget reached]\n")
            truncated = True
            continue
        out.append(body)
        used += len(body)
    return StagedDiff(files=files, stat=stat, text="".join(out), truncated=truncated)


_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def added_lines(cwd: Optional[str] = None) -> Iterator[Tuple[str, int, str]]:
    """Yield (path, line_number, text) for every staged added line."""
    raw = run_git(
        ["diff", "--cached", "--no-color", "--no-ext-diff", "--no-renames", "-U0", "--diff-filter=ACMR"],
        cwd,
    )
    path: Optional[str] = None
    in_header = True
    lineno = 0
    for line in raw.splitlines():
        if line.startswith("diff --git "):
            path, in_header = None, True
            continue
        if in_header:
            if line.startswith("+++ "):
                p = line[4:].strip()
                path = p[2:] if p.startswith("b/") else None
            elif line.startswith("@@"):
                in_header = False
            else:
                continue
        if line.startswith("@@"):
            m = _HUNK_RE.match(line)
            lineno = int(m.group(1)) if m else 0
            continue
        if path is None or in_header:
            continue
        if line.startswith("+"):
            yield path, lineno, line[1:]
            lineno += 1


def staged_blob_size(path: str, cwd: Optional[str] = None) -> Optional[int]:
    out = run_git(["cat-file", "-s", f":{path}"], cwd, check=False).strip()
    return int(out) if out.isdigit() else None


def recent_subjects(n: int = 30, cwd: Optional[str] = None) -> List[str]:
    out = run_git(["log", "-n", str(n), "--no-merges", "--pretty=format:%s"], cwd, check=False)
    return [s for s in out.splitlines() if s.strip()]


def log_subjects(since: str, author: Optional[str] = None, cwd: Optional[str] = None) -> List[str]:
    args = ["log", f"--since={since}", "--no-merges", "--pretty=format:%s"]
    if author:
        args.append(f"--author={author}")
    return [s for s in run_git(args, cwd, check=False).splitlines() if s.strip()]


def user_email(cwd: Optional[str] = None) -> str:
    return run_git(["config", "user.email"], cwd, check=False).strip()


def commit(message: str, cwd: Optional[str] = None, edit: bool = False) -> int:
    """Run `git commit` with the message. With edit=True the editor opens for review."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write(message.rstrip() + "\n")
        name = fh.name
    try:
        cmd = ["git", "commit"] + (["-e"] if edit else []) + ["-F", name]
        return subprocess.run(cmd, cwd=cwd).returncode
    finally:
        try:
            os.unlink(name)
        except OSError:
            pass
