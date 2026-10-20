import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parent.parent / "src")


class FakeOllama:
    """A stand-in for Ollama. Set .reply to a string or a callable(payload) -> string."""

    def __init__(self):
        self.reply = "feat: add thing"
        self.requests = []
        self.models = ["fast-model", "slow-model"]
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self._send(200, {"models": [{"name": m} for m in owner.models]})

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append(payload)
                if payload["model"] == "missing-model":
                    return self._send(404, {"error": "model not found"})
                reply = owner.reply(payload) if callable(owner.reply) else owner.reply
                self._send(200, {"message": {"role": "assistant", "content": reply}})

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def ollama(monkeypatch, tmp_path):
    fake = FakeOllama()
    monkeypatch.setenv("SLOTH_HOST", fake.url)
    monkeypatch.setenv("SLOTH_MODEL", "fast-model")
    monkeypatch.setenv("SLOTH_SLOW_MODEL", "slow-model")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("SLOTH_SKIP", raising=False)
    monkeypatch.delenv("SLOTH_GUARD", raising=False)
    yield fake
    fake.close()


class Repo:
    def __init__(self, path: Path):
        self.path = path

    def git(self, *args, check=True):
        return subprocess.run(
            ["git", *args], cwd=self.path, capture_output=True, text=True, check=check
        ).stdout

    def write(self, name, text="x\n"):
        p = self.path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def commit_all(self, msg):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)

    def subjects(self):
        return self.git("log", "--pretty=format:%s", check=False).splitlines()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    r = Repo(root)
    r.git("init", "-q")
    r.git("config", "user.email", "t@example.com")
    r.git("config", "user.name", "T")
    r.git("config", "commit.gpgsign", "false")
    monkeypatch.chdir(root)
    return r


@pytest.fixture
def env_for_subprocess(ollama, tmp_path):
    env = dict(os.environ)
    env.update(
        PYTHONPATH=SRC,
        SLOTH_HOST=ollama.url,
        SLOTH_MODEL="fast-model",
        SLOTH_SLOW_MODEL="slow-model",
        XDG_CONFIG_HOME=str(tmp_path / "cfg"),
        XDG_DATA_HOME=str(tmp_path / "data"),
        GIT_EDITOR="true",
    )
    env.pop("SLOTH_SKIP", None)
    env.pop("SLOTH_GUARD", None)
    return env


def python_cli(*args, cwd, env):
    return subprocess.run(
        [sys.executable, "-m", "sloth_commit", *args], cwd=cwd, env=env, capture_output=True, text=True
    )
