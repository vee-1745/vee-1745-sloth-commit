import json
import os

import pytest

from sloth_commit import cli, git, hook, split
from sloth_commit.config import load_config

from conftest import python_cli

# --- Commit Splitter --------------------------------------------------------


def _mixed_repo(repo):
    repo.write("app.py", "print(1)\n")
    repo.write("README.md", "# hi\n")
    repo.commit_all("chore: init")
    repo.write("app.py", "print(2)\n")
    repo.write("README.md", "# hello\n")
    repo.git("add", "-A")


def test_heuristic_groups():
    groups = split.heuristic_groups(["src/a.py", "tests/test_a.py", "README.md", "package-lock.json"])
    by_title = {g.title: g.files for g in groups}
    assert by_title["code"] == ["src/a.py", "tests/test_a.py"]  # feature and its tests stay together
    assert by_title["documentation"] == ["README.md"]
    assert by_title["build, deps and CI"] == ["package-lock.json"]


@pytest.mark.parametrize(
    "obj",
    [
        None,
        {"groups": "nope"},
        {"groups": [{"title": "x", "files": ["a.py", "ghost.py"]}]},  # unknown file
        {"groups": [{"title": "x", "files": ["a.py"]}]},  # b.py missing
        {"groups": [{"title": "x", "files": ["a.py", "b.py"]}, {"title": "y", "files": ["a.py"]}]},  # duplicate
    ],
)
def test_validate_groups_rejects_bad_model_output(obj):
    assert split.validate_groups(obj, ["a.py", "b.py"]) is None


def test_validate_groups_accepts_good_output():
    obj = {"groups": [{"title": "fix", "files": ["a.py"]}, {"title": "docs", "files": ["b.py"]}]}
    assert [g.files for g in split.validate_groups(obj, ["a.py", "b.py"])] == [["a.py"], ["b.py"]]


def test_split_end_to_end(repo, ollama):
    _mixed_repo(repo)

    def reply(p):
        content = p["messages"][1]["content"]
        if "Reply with JSON" in p["messages"][0]["content"]:
            return json.dumps({"groups": [
                {"title": "fix app", "files": ["app.py"]},
                {"title": "docs", "files": ["README.md"]},
            ]})
        return "docs: reword readme" if "README.md" in content else "fix: change app output"

    ollama.reply = reply
    assert cli.main(["split", "--yes"]) == 0
    assert repo.subjects()[:2] == ["docs: reword readme", "fix: change app output"]
    assert repo.git("status", "--porcelain").strip() == ""
    assert repo.git("show", "--name-only", "--pretty=format:", "HEAD").split() == ["README.md"]


def test_split_falls_back_to_heuristic_on_garbage_json(repo, ollama):
    _mixed_repo(repo)
    ollama.reply = lambda p: "not json" if "Reply with JSON" in p["messages"][0]["content"] else "chore: x"
    groups = split.propose(load_config())
    assert sorted(g.title for g in groups) == ["code", "documentation"]


def test_split_single_group_is_left_alone(repo, ollama):
    repo.write("a.py")
    repo.write("b.py")
    repo.git("add", "-A")
    ollama.reply = json.dumps({"groups": [{"title": "all", "files": ["a.py", "b.py"]}]})
    assert len(split.propose(load_config())) == 1


def test_split_refuses_partially_staged_files(repo, ollama):
    _mixed_repo(repo)
    repo.write("app.py", "print(3)\n")  # unstaged edit on top of staged one
    with pytest.raises(split.SplitError, match="app.py"):
        split.check_safe_to_split()


def test_split_restores_index_when_a_commit_fails(repo, ollama):
    _mixed_repo(repo)
    groups = [split.Group("a", ["app.py"]), split.Group("b", ["README.md"])]
    hooks = repo.path / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    pre = hooks / "pre-commit"
    # Rejects any commit that includes README.md, so only the 2nd group fails.
    pre.write_text("#!/bin/sh\ngit diff --cached --name-only | grep -q README.md && exit 1\nexit 0\n")
    pre.chmod(0o755)
    # After the failure, README.md must be staged again rather than left dangling.
    with pytest.raises(split.SplitError):
        split.execute(groups, ["fix: a", "docs: b"])
    assert repo.subjects()[0] == "fix: a"
    assert "README.md" in git.staged_files()


# --- hook -------------------------------------------------------------------


def test_hook_install_uninstall_and_refuse_foreign(repo):
    path = hook.install()
    assert path.exists() and hook.MARKER in path.read_text()
    assert os.access(path, os.X_OK)
    assert hook.uninstall() == path and not path.exists()

    path.write_text("#!/bin/sh\necho mine\n")
    with pytest.raises(hook.HookError):
        hook.install()
    with pytest.raises(hook.HookError):
        hook.uninstall()
    assert "mine" in path.read_text()
    hook.install(force=True)
    assert hook.MARKER in path.read_text()


def test_hook_fills_message_in_a_real_git_commit(repo, env_for_subprocess, ollama):
    repo.write("a.py", "print(1)\n")
    ollama.reply = "feat: add a script"
    assert python_cli("hook", "install", cwd=repo.path, env=env_for_subprocess).returncode == 0
    repo.git("add", "-A")
    proc = __import__("subprocess").run(
        ["git", "commit", "-q"], cwd=repo.path, env=env_for_subprocess, capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
    assert repo.subjects() == ["feat: add a script"]
    assert "drafted a message" in proc.stderr


def test_hook_does_not_touch_explicit_messages(repo, env_for_subprocess, ollama):
    repo.write("a.py")
    python_cli("hook", "install", cwd=repo.path, env=env_for_subprocess)
    repo.git("add", "-A")
    __import__("subprocess").run(
        ["git", "commit", "-q", "-m", "my own words"], cwd=repo.path, env=env_for_subprocess, check=True
    )
    assert repo.subjects() == ["my own words"]
    assert ollama.requests == []


def test_hook_never_blocks_when_ollama_is_down(repo, env_for_subprocess):
    env = dict(env_for_subprocess, SLOTH_HOST="http://127.0.0.1:9", GIT_EDITOR="true")
    repo.write("a.py")
    python_cli("hook", "install", cwd=repo.path, env=env)
    repo.git("add", "-A")
    proc = __import__("subprocess").run(
        ["git", "commit", "-q", "-m", "fallback"], cwd=repo.path, env=env, capture_output=True, text=True
    )
    assert proc.returncode == 0 and repo.subjects() == ["fallback"]


def test_hook_blocks_secrets_only_when_asked(repo, env_for_subprocess, ollama):
    secret = "AKIA" + "IOSFODNN7EXAMPLQ"
    repo.write("k.py", f'K = "{secret}"\n')
    python_cli("hook", "install", cwd=repo.path, env=env_for_subprocess)
    repo.git("add", "-A")
    sp = __import__("subprocess")

    warn = sp.run(["git", "commit", "-q"], cwd=repo.path, env=env_for_subprocess, capture_output=True, text=True)
    assert warn.returncode == 0 and "possible secrets" in warn.stderr

    repo.write("k2.py", f'K = "{secret}"\n')
    repo.git("add", "-A")
    env = dict(env_for_subprocess, SLOTH_GUARD="block")
    blocked = sp.run(["git", "commit", "-q"], cwd=repo.path, env=env, capture_output=True, text=True)
    assert blocked.returncode != 0 and len(repo.subjects()) == 1


# --- CLI --------------------------------------------------------------------


def test_cli_commit_yes(repo, ollama):
    repo.write("a.py")
    repo.git("add", "-A")
    ollama.reply = "feat: add a"
    assert cli.main(["commit", "--yes"]) == 0
    assert repo.subjects() == ["feat: add a"]
    assert cli.main(["stats"]) == 0


def test_cli_commit_aborts_on_secret_without_force(repo, ollama, capsys):
    repo.write("k.py", 'API_KEY = "abcd1234efgh5678"\n')
    repo.git("add", "-A")
    assert cli.main(["commit", "--yes"]) == 1
    assert repo.subjects() == [] and ollama.requests == []  # model never saw the secret
    assert "Secret Guard" in capsys.readouterr().err
    assert cli.main(["commit", "--yes", "--force"]) == 0


def test_cli_doctor(ollama, repo, capsys):
    assert cli.main(["doctor"]) == 0
    assert "fast model fast-model" in capsys.readouterr().out
    ollama.models = []
    assert cli.main(["doctor"]) == 1
