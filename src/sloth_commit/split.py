"""Commit Splitter: spot unrelated changes in one staged diff and commit them separately.

Splitting is done per file. Hunks inside one file are never separated, because that
would mean rewriting the index behind your back.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from . import git, llm
from .config import Config


class SplitError(RuntimeError):
    pass


@dataclass
class Group:
    title: str
    files: List[str] = field(default_factory=list)


SPLIT_SYSTEM = (
    "You review a staged git diff and decide whether it mixes unrelated changes that should be "
    "separate commits (for example a bug fix plus a README tweak). Group the files by purpose. "
    "Only split when the changes are genuinely independent; a feature and its tests belong together. "
    'Reply with JSON only, in this shape: {"groups": [{"title": "short purpose", "files": ["path", ...]}]}. '
    "Every file must appear in exactly one group. If nothing should be split, return a single group."
)

_DOC_EXT = (".md", ".rst", ".txt", ".adoc")
_BUILD_NAMES = {
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "package.json", "dockerfile",
    "makefile", "tox.ini", ".gitignore", ".editorconfig",
}


def _category(path: str) -> str:
    low = path.lower()
    name = os.path.basename(low)
    if name in git.LOCKFILES or name in _BUILD_NAMES or low.startswith((".github/", ".gitlab")):
        return "build, deps and CI"
    if low.endswith(_DOC_EXT) or low.startswith("docs/"):
        return "documentation"
    return "code"


def heuristic_groups(files: List[str]) -> List[Group]:
    """Model-free fallback: documentation, build/deps/CI and code are kept apart."""
    buckets: Dict[str, List[str]] = {}
    for f in files:
        buckets.setdefault(_category(f), []).append(f)
    return [Group(title, fs) for title, fs in buckets.items()]


def _extract_json(raw: str) -> Optional[dict]:
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S | re.I)
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(raw[start : end + 1])
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def validate_groups(obj: Optional[dict], files: List[str]) -> Optional[List[Group]]:
    """Accept the model's grouping only if it covers every staged file exactly once."""
    if not obj or not isinstance(obj.get("groups"), list):
        return None
    wanted = set(files)
    seen: set = set()
    groups: List[Group] = []
    for g in obj["groups"]:
        if not isinstance(g, dict) or not isinstance(g.get("files"), list):
            return None
        members = []
        for f in g["files"]:
            if not isinstance(f, str) or f not in wanted or f in seen:
                return None
            seen.add(f)
            members.append(f)
        if members:
            groups.append(Group(str(g.get("title") or "changes")[:60], members))
    return groups if seen == wanted and groups else None


def propose(cfg: Config, cwd: Optional[str] = None) -> List[Group]:
    """Return the groups to commit separately. A single group means: don't split."""
    files = git.staged_files(cwd)
    if len(files) < 2:
        return [Group("all changes", files)] if files else []

    d = git.staged_diff(cwd, None, cfg.max_diff_chars, cfg.per_file_chars)
    user = "Files:\n" + "\n".join(files) + "\n\nDiff:\n" + d.text
    raw = llm.chat(cfg, cfg.model, SPLIT_SYSTEM, user, num_predict=400)
    return validate_groups(_extract_json(raw), files) or heuristic_groups(files)


def check_safe_to_split(cwd: Optional[str] = None) -> None:
    """Refuse when splitting would mix in, or lose, work that isn't fully staged."""
    op = git.operation_in_progress(cwd)
    if op:
        raise SplitError(f"Cannot split while {op} is in progress.")
    partial = sorted(set(git.staged_files(cwd)) & set(git.unstaged_files(cwd)))
    if partial:
        raise SplitError(
            "These files have both staged and unstaged changes, so splitting would pull the "
            "unstaged parts into a commit: " + ", ".join(partial)
        )


def execute(
    groups: List[Group],
    messages: List[str],
    cwd: Optional[str] = None,
    on_commit: Optional[Callable[[int, str], None]] = None,
) -> int:
    """Make one commit per group. Messages must already be written, so a model failure
    can't leave the index half-rearranged. On failure, the remaining files are re-staged."""
    if len(groups) != len(messages):
        raise SplitError("Need exactly one message per group.")
    check_safe_to_split(cwd)

    git.run_git(["reset", "-q"], cwd)
    done = 0
    try:
        for i, (group, msg) in enumerate(zip(groups, messages)):
            git.run_git(["add", "-A", "--", *group.files], cwd)
            if git.commit(msg, cwd) != 0:
                raise SplitError(f"git commit failed for group {i + 1} ({group.title}).")
            done += 1
            if on_commit:
                on_commit(i, msg)
    except BaseException:
        rest = [f for g in groups[done:] for f in g.files]
        if rest:
            git.run_git(["add", "-A", "--", *rest], cwd, check=False)
        raise
    return done
