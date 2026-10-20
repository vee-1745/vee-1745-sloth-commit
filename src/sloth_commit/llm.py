"""A tiny Ollama client using only the standard library."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import List

from .config import Config


class LLMError(RuntimeError):
    pass


def _opener(host: str):
    hostname = urllib.parse.urlparse(host).hostname or ""
    if hostname in ("localhost", "127.0.0.1", "::1"):
        # Local inference should never be routed through a system proxy.
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


def _unreachable(cfg: Config, err: object) -> LLMError:
    return LLMError(
        f"Cannot reach Ollama at {cfg.host} ({err}). "
        "Is it running? Start it with `ollama serve`, or set SLOTH_HOST."
    )


def chat(cfg: Config, model: str, system: str, user: str, num_predict: int = 220) -> str:
    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "options": {"temperature": cfg.temperature, "num_predict": num_predict},
    }
    req = urllib.request.Request(
        cfg.host.rstrip("/") + "/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _opener(cfg.host).open(req, timeout=cfg.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        detail = ""
        try:
            detail = json.loads(err.read().decode("utf-8")).get("error", "")
        except Exception:
            pass
        if err.code == 404:
            raise LLMError(
                f"Model {model!r} is not available in Ollama. Pull it with: ollama pull {model}"
            ) from err
        raise LLMError(f"Ollama returned HTTP {err.code}. {detail}".strip()) from err
    except (urllib.error.URLError, OSError) as err:
        reason = getattr(err, "reason", err)
        raise _unreachable(cfg, reason) from err
    except ValueError as err:
        raise LLMError("Ollama sent a response that is not valid JSON.") from err

    content = (data.get("message") or {}).get("content")
    if not isinstance(content, str):
        raise LLMError("Ollama's response had no message content.")
    return content


def list_models(cfg: Config) -> List[str]:
    """Names of locally pulled models. Raises LLMError if Ollama is unreachable."""
    req = urllib.request.Request(cfg.host.rstrip("/") + "/api/tags")
    try:
        with _opener(cfg.host).open(req, timeout=min(cfg.timeout, 10)) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError) as err:
        raise _unreachable(cfg, getattr(err, "reason", err)) from err
    except ValueError as err:
        raise LLMError("Ollama sent a response that is not valid JSON.") from err
    return [m.get("name", "") for m in data.get("models", []) if isinstance(m, dict)]
