import pytest

from sloth_commit import generate, guard, llm, stats, style
from sloth_commit.config import Config, load_config

AWS = "AKIA" + "IOSFODNN7EXAMPLQ"  # shaped like a key, assembled so this file isn't a hit itself


# --- generate.clean ---------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("feat: add login", "feat: add login"),
        ("```\nfeat: add login\n```", "feat: add login"),
        ('"fix: handle empty input"', "fix: handle empty input"),
        ("Here is the commit message:\nfix: x", "fix: x"),
        ("Commit message: docs: update readme", "docs: update readme"),
        ("<think>hmm let me see</think>\nfeat: a", "feat: a"),
        ("feat: a\n\n\n\n- one\n- two", "feat: a\n\n- one\n- two"),
    ],
)
def test_clean(raw, expected):
    assert generate.clean(raw) == expected


def test_clean_empty():
    assert generate.clean("<think>only thoughts</think>") == ""


# --- config -----------------------------------------------------------------

def test_env_overrides_config(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "sloth-commit").mkdir()
    (tmp_path / "sloth-commit" / "config.json").write_text('{"model": "a", "timeout": "nope"}')
    monkeypatch.setenv("SLOTH_MODEL", "b")
    cfg = load_config()
    assert cfg.model == "b"
    assert cfg.timeout == Config().timeout  # bad value ignored


# --- Secret Guard -----------------------------------------------------------

def test_guard_detects_aws_key_and_masks_it(repo):
    repo.write("app.py", f'KEY = "{AWS}"\n')
    repo.git("add", "-A")
    findings = guard.scan_staged()
    assert [f.label for f in findings] == ["AWS access key id"]
    assert findings[0].line == 1
    assert AWS not in findings[0].format()


def test_guard_ignores_placeholders(repo):
    repo.write("app.py", 'password = "changeme-please"\napi_key = "<your-api-key-here>"\n')
    repo.git("add", "-A")
    assert guard.scan_staged() == []


def test_guard_flags_hardcoded_password(repo):
    repo.write("app.py", 'DB_PASSWORD = "s3cr3tvalue99"\n')
    repo.git("add", "-A")
    assert [f.label for f in guard.scan_staged()] == ["Hard-coded secret assignment"]


def test_guard_sensitive_files(repo):
    repo.write(".env", "A=1\n")
    repo.write(".env.example", "A=\n")
    repo.git("add", "-A")
    flagged = [f.path for f in guard.scan_staged()]
    assert flagged == [".env"]


def test_guard_only_looks_at_added_lines(repo):
    repo.write("app.py", f'KEY = "{AWS}"\n')
    repo.commit_all("old")
    repo.write("app.py", f'KEY = "{AWS}"\nprint(1)\n')
    repo.git("add", "-A")
    assert guard.scan_staged() == []


# --- Style Mimic ------------------------------------------------------------

def test_style_conventional():
    prof = style.analyze(["feat(api): add route", "fix: crash", "docs: readme", "feat: more", "chore: x"])
    assert prof.conventional and prof.confident
    assert "feat" in prof.types and "api" in prof.scopes
    assert "Conventional Commits" in style.style_instructions(prof)


def test_style_plain_capitalised_with_period():
    prof = style.analyze(["Add the thing.", "Fix a bug.", "Update docs.", "Remove cruft.", "Tidy up."])
    assert not prof.conventional and not prof.lowercase_start and prof.trailing_period
    text = style.style_instructions(prof)
    assert "plain subject line" in text and "capitalised" in text


def test_style_few_commits_falls_back_to_default():
    prof = style.analyze(["whatever"])
    assert not prof.confident
    assert "Conventional Commits" in style.style_instructions(prof)


# --- generate.draft with the fake model -------------------------------------

def test_draft_uses_diff_and_style(repo, ollama):
    for i in range(6):
        repo.write(f"f{i}.txt", "a\n")
        repo.commit_all(f"Update file {i}.")
    repo.write("new.py", "print('hi')\n")
    repo.git("add", "-A")
    ollama.reply = "```\nAdd greeting script.\n```"

    d = generate.draft(load_config())
    assert d.message == "Add greeting script." and d.mode == "fast"
    system = ollama.requests[0]["messages"][0]["content"]
    user = ollama.requests[0]["messages"][1]["content"]
    assert "plain subject line" in system  # learned from this repo's history
    assert "new.py" in user and "print('hi')" in user


def test_draft_nothing_staged(repo, ollama):
    repo.write("a.txt")
    with pytest.raises(generate.NothingStaged):
        generate.draft(load_config())


def test_slow_mode_polishes_with_second_model(repo, ollama):
    repo.write("a.py", "x = 1\n")
    repo.git("add", "-A")
    ollama.reply = lambda p: "feat: slow polished" if p["model"] == "slow-model" else "feat: quick draft"
    d = generate.draft(load_config(), slow=True)
    assert (d.message, d.mode, d.model) == ("feat: slow polished", "slow", "slow-model")
    assert "feat: quick draft" in ollama.requests[1]["messages"][1]["content"]


def test_slow_mode_falls_back_to_fast_draft(repo, ollama, monkeypatch):
    monkeypatch.setenv("SLOTH_SLOW_MODEL", "missing-model")
    repo.write("a.py", "x = 1\n")
    repo.git("add", "-A")
    ollama.reply = "feat: quick draft"
    d = generate.draft(load_config(), slow=True)
    assert d.message == "feat: quick draft" and d.mode == "fast"
    assert "ollama pull missing-model" in d.slow_failed


def test_missing_model_error_is_helpful(ollama, monkeypatch):
    monkeypatch.setenv("SLOTH_MODEL", "missing-model")
    with pytest.raises(llm.LLMError, match="ollama pull missing-model"):
        llm.chat(load_config(), "missing-model", "s", "u")


def test_unreachable_ollama_error(monkeypatch):
    monkeypatch.setenv("SLOTH_HOST", "http://127.0.0.1:9")
    with pytest.raises(llm.LLMError, match="Cannot reach Ollama"):
        llm.chat(load_config(), "m", "s", "u")


# --- Stats ------------------------------------------------------------------

def test_stats_record_and_render(ollama):
    cfg = load_config()
    assert "No stats yet" in stats.render(stats.load(), cfg)
    stats.record("feat: add a thing", 1.5, "fast", cfg)
    stats.record("fix: another thing", 2.0, "slow", cfg)
    entries = stats.load()
    assert len(entries) == 2
    s = stats.summarize(entries, cfg)
    assert s.count == 2 and s.slow_count == 1
    assert "messages written for you   2" in stats.render(entries, cfg)
