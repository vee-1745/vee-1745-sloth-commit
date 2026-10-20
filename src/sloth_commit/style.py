"""Style Mimic: learn a repo's commit-message conventions from its own history."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Sequence

CC_RE = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]+)\))?(?P<bang>!)?: (?P<desc>.+)$")
GITMOJI_RE = re.compile(r"^(:[a-z0-9_+-]+:|[\U0001F300-\U0001FAFF☀-➿])\s*")
MIN_SAMPLES = 5


@dataclass
class StyleProfile:
    samples: int = 0
    conventional: bool = False
    types: List[str] = field(default_factory=list)
    scopes: List[str] = field(default_factory=list)
    lowercase_start: bool = True
    trailing_period: bool = False
    emoji_prefix: bool = False
    avg_len: int = 0
    max_len: int = 72
    examples: List[str] = field(default_factory=list)

    @property
    def confident(self) -> bool:
        return self.samples >= MIN_SAMPLES


def analyze(subjects: Sequence[str]) -> StyleProfile:
    subs = [s.strip() for s in subjects if s and s.strip()]
    prof = StyleProfile(samples=len(subs))
    if not subs:
        return prof

    cc = [CC_RE.match(s) for s in subs]
    cc_hits = [m for m in cc if m]
    prof.conventional = len(cc_hits) / len(subs) >= 0.5
    if prof.conventional:
        prof.types = [t for t, _ in Counter(m.group("type") for m in cc_hits).most_common(6)]
        prof.scopes = [
            s for s, _ in Counter(m.group("scope") for m in cc_hits if m.group("scope")).most_common(6)
        ]

    bodies: List[str] = []
    for s, m in zip(subs, cc):
        text = m.group("desc") if m else s
        text = GITMOJI_RE.sub("", text)
        bodies.append(text)

    letters = [b[0] for b in bodies if b and b[0].isalpha()]
    if letters:
        prof.lowercase_start = sum(c.islower() for c in letters) / len(letters) >= 0.5
    prof.trailing_period = sum(s.endswith(".") for s in subs) / len(subs) >= 0.5
    prof.emoji_prefix = sum(bool(GITMOJI_RE.match(s)) for s in subs) / len(subs) >= 0.3

    lens = sorted(len(s) for s in subs)
    prof.avg_len = sum(lens) // len(lens)
    p90 = lens[min(len(lens) - 1, int(len(lens) * 0.9))]
    prof.max_len = max(50, min(100, p90))
    prof.examples = subs[:4]
    return prof


def describe(prof: StyleProfile) -> str:
    """Human-readable summary for `sloth-commit style`."""
    if prof.samples == 0:
        return "No commits yet. Using the default style (Conventional Commits)."
    lines = [f"Learned from {prof.samples} recent commit(s):"]
    if prof.conventional:
        lines.append(f"  format    Conventional Commits (types: {', '.join(prof.types)})")
        if prof.scopes:
            lines.append(f"  scopes    {', '.join(prof.scopes)}")
    else:
        lines.append("  format    plain subject lines")
    lines.append(f"  casing    {'lowercase' if prof.lowercase_start else 'Capitalised'} first word")
    lines.append(f"  period    {'yes' if prof.trailing_period else 'no'}")
    lines.append(f"  emoji     {'yes' if prof.emoji_prefix else 'no'}")
    lines.append(f"  length    ~{prof.avg_len} chars (limit {prof.max_len})")
    if not prof.confident:
        lines.append(f"  note      only {prof.samples} commit{'' if prof.samples == 1 else 's'}, so the default style is blended in")
    return "\n".join(lines)


def style_instructions(prof: StyleProfile) -> str:
    """Prompt text that tells the model how to word the message for this repo."""
    if not prof.confident:
        return (
            "Use Conventional Commits: type(scope): description. "
            "Types: feat, fix, docs, style, refactor, perf, test, build, ci, chore. "
            "Lowercase description, imperative mood, no trailing period."
        )
    parts = []
    if prof.conventional:
        parts.append("Use Conventional Commits: type(scope): description.")
        parts.append(f"Types used in this repo: {', '.join(prof.types)}.")
        if prof.scopes:
            parts.append(f"Scopes used in this repo: {', '.join(prof.scopes)} (only when relevant).")
    else:
        parts.append("Write a plain subject line with no type prefix.")
    parts.append(
        "Start the description with a "
        + ("lowercase" if prof.lowercase_start else "capitalised")
        + " word in imperative mood."
    )
    parts.append("End the subject with a period." if prof.trailing_period else "No trailing period.")
    if prof.emoji_prefix:
        parts.append("Start the subject with a fitting gitmoji emoji.")
    else:
        parts.append("Do not use emoji.")
    parts.append(f"Keep the subject under {prof.max_len} characters.")
    if prof.examples:
        parts.append("Recent commits in this repo, for tone:\n" + "\n".join(f"- {e}" for e in prof.examples))
    return " ".join(p if "\n" not in p else "\n" + p for p in parts)
