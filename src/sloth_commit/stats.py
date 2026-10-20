"""Sloth Stats: a small local log that makes the laziness measurable."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import List

from .config import Config, data_dir

SLOTH = r'''
      .-"""-.
     / .===. \
     \/ 6 6 \/     "I saved you {mins} minutes.
     ( \___/ )      Now go take a nap."
 ____ooo__V__ooo____
'''


def _log_path():
    return data_dir() / "stats.jsonl"


def record(message: str, seconds: float, mode: str, cfg: Config) -> None:
    """Append one generated message to the log. Never raises: stats are best-effort."""
    entry = {
        "ts": int(time.time()),
        "chars": len(message),
        "gen_seconds": round(seconds, 2),
        "mode": mode,
        "model": cfg.slow_model if mode == "slow" else cfg.model,
    }
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def load() -> List[dict]:
    try:
        lines = _log_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


@dataclass
class Summary:
    count: int
    chars: int
    saved_seconds: float
    spent_seconds: float
    slow_count: int


def summarize(entries: List[dict], cfg: Config) -> Summary:
    chars = sum(int(e.get("chars", 0)) for e in entries)
    typing = chars / max(cfg.typing_cpm, 1) * 60
    thinking = len(entries) * cfg.think_seconds
    spent = sum(float(e.get("gen_seconds", 0)) for e in entries)
    return Summary(
        count=len(entries),
        chars=chars,
        saved_seconds=max(typing + thinking - spent, 0.0),
        spent_seconds=spent,
        slow_count=sum(1 for e in entries if e.get("mode") == "slow"),
    )


def fmt_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    mins, sec = divmod(seconds, 60)
    if mins < 60:
        return f"{mins}m {sec:02d}s"
    hrs, mins = divmod(mins, 60)
    return f"{hrs}h {mins:02d}m"


def render(entries: List[dict], cfg: Config) -> str:
    if not entries:
        return "No stats yet. Generate a message with `sloth-commit msg` first."
    s = summarize(entries, cfg)
    mins = max(1, round(s.saved_seconds / 60))
    art = SLOTH.replace("{mins}", str(mins)).strip("\n")
    return (
        f"{art}\n\n"
        f"  messages written for you   {s.count}\n"
        f"  characters you didn't type {s.chars}\n"
        f"  model time spent           {fmt_duration(s.spent_seconds)}\n"
        f"  estimated time saved       {fmt_duration(s.saved_seconds)}\n"
        f"  slow-mode polishes         {s.slow_count}\n"
        f"\n  (estimate: {cfg.typing_cpm} chars/min typing + {cfg.think_seconds}s of thinking per commit)"
    )
