"""Configuration: sensible defaults, an optional JSON file, and env overrides."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields
from pathlib import Path


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / "sloth-commit"
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "sloth-commit"
    return Path.home() / ".config" / "sloth-commit"


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    if base:
        return Path(base) / "sloth-commit"
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "sloth-commit"
    return Path.home() / ".local" / "share" / "sloth-commit"


@dataclass
class Config:
    host: str = "http://localhost:11434"
    # Fast drafting model. A 3B coder model fits comfortably on a 6 GB GPU.
    model: str = "qwen2.5-coder:3b"
    # Used by --slow to polish the draft. Any Ollama model name works.
    slow_model: str = "qwen2.5-coder:7b"
    max_diff_chars: int = 6000
    per_file_chars: int = 1500
    timeout: int = 120
    temperature: float = 0.2
    # Assumptions behind the "time saved" estimate in `sloth-commit stats`.
    typing_cpm: int = 200
    think_seconds: int = 20


_ENV = {
    "SLOTH_HOST": "host",
    "SLOTH_MODEL": "model",
    "SLOTH_SLOW_MODEL": "slow_model",
    "SLOTH_TIMEOUT": "timeout",
}


def _coerce(default, value):
    """Return value converted to the type of default, or None if it doesn't fit."""
    if isinstance(default, bool):
        return None
    try:
        if isinstance(default, int):
            return int(value)
        if isinstance(default, float):
            return float(value)
        if isinstance(default, str):
            return str(value)
    except (TypeError, ValueError):
        return None
    return None


def load_config() -> Config:
    cfg = Config()
    known = {f.name for f in fields(Config)}

    path = config_dir() / "config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if isinstance(data, dict):
        for key, value in data.items():
            if key in known:
                coerced = _coerce(getattr(cfg, key), value)
                if coerced is not None:
                    setattr(cfg, key, coerced)

    for env_name, key in _ENV.items():
        raw = os.environ.get(env_name)
        if raw:
            coerced = _coerce(getattr(cfg, key), raw)
            if coerced is not None:
                setattr(cfg, key, coerced)
    return cfg
