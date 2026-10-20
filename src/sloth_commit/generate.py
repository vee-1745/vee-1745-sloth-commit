"""Turn a staged diff into a commit message with a local model."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Optional, Sequence

from . import git, llm, style
from .config import Config


class NothingStaged(RuntimeError):
    pass


@dataclass
class Draft:
    message: str
    seconds: float
    mode: str  # "fast" or "slow"
    model: str
    truncated: bool = False
    slow_failed: str = ""  # reason, if --slow was asked for but the polish step failed


SYSTEM = (
    "You write git commit messages from a staged diff. "
    "Output ONLY the commit message: no quotes, no markdown, no code fences, no explanation. "
    "Describe what changed and why it matters, in imperative mood, based only on the diff. "
    "Never invent changes that are not in the diff. "
    "{style}"
)

BODY_RULE = (
    " After the subject add a blank line and then 1 to 3 short bullet points (start each with '- ') "
    "covering the key details. Wrap lines at 72 characters."
)
SUBJECT_ONLY_RULE = " Write the subject line only, with no body."

_THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)
_FENCE_RE = re.compile(r"^```[a-z]*\s*$", re.I)
_LEAD_RE = re.compile(r"^\s*(?:here(?:'s| is)[^:\n]*:|commit message:|message:)\s*", re.I)


def clean(raw: str, max_body_lines: int = 12) -> str:
    """Strip the wrapping small models like to add (thinking, fences, labels, quotes)."""
    text = _THINK_RE.sub("", raw).strip()
    text = re.sub(r"</?think>", "", text, flags=re.I).strip()
    lines = [ln.rstrip() for ln in text.splitlines() if not _FENCE_RE.match(ln.strip())]
    text = "\n".join(lines).strip()
    text = _LEAD_RE.sub("", text, count=1).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'`":
        text = text[1:-1].strip()
    out = []
    blank = False
    for ln in text.splitlines():
        if not ln.strip():
            if out and not blank:
                out.append("")
            blank = True
            continue
        blank = False
        out.append(ln)
    while out and not out[-1]:
        out.pop()
    if len(out) > max_body_lines + 2:
        out = out[: max_body_lines + 2]
    return "\n".join(out).strip()


def _user_prompt(d: git.StagedDiff, extra: str = "") -> str:
    parts = ["Files changed:", d.stat or "\n".join(d.files), "", "Diff:", d.text]
    if d.truncated:
        parts.append("(The diff was trimmed for length. Describe the overall change.)")
    if extra:
        parts += ["", extra]
    return "\n".join(parts)


def _system(prof: style.StyleProfile, body: bool) -> str:
    rule = BODY_RULE if body else SUBJECT_ONLY_RULE
    return SYSTEM.format(style=style.style_instructions(prof) + rule)


def draft(
    cfg: Config,
    cwd: Optional[str] = None,
    paths: Optional[Sequence[str]] = None,
    slow: bool = False,
    body: bool = False,
) -> Draft:
    """Generate a message for the staged changes (optionally limited to `paths`)."""
    d = git.staged_diff(cwd, paths, cfg.max_diff_chars, cfg.per_file_chars)
    if not d.files:
        raise NothingStaged("Nothing is staged. Stage your changes with `git add` first.")

    prof = style.analyze(git.recent_subjects(30, cwd))
    system = _system(prof, body)
    predict = 320 if body else 200

    start = time.monotonic()
    raw = llm.chat(cfg, cfg.model, system, _user_prompt(d), num_predict=predict)
    message = clean(raw)
    if not message:
        raise llm.LLMError("The model returned an empty message. Try again or use a bigger model.")
    result = Draft(message, 0.0, "fast", cfg.model, d.truncated)

    if slow:
        extra = (
            "A first draft of the commit message is below. Improve its accuracy and wording using the "
            "diff, keep the same style rules, and output only the final message.\n\nDraft:\n" + message
        )
        try:
            polished = clean(llm.chat(cfg, cfg.slow_model, system, _user_prompt(d, extra), num_predict=predict))
            if polished:
                result.message, result.mode, result.model = polished, "slow", cfg.slow_model
        except llm.LLMError as err:
            result.slow_failed = str(err)

    result.seconds = time.monotonic() - start
    return result
