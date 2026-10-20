# sloth-commit

A lazy git commit assistant. A **local open-weight model** (via [Ollama](https://ollama.com)) reads your staged diff and writes the commit message, so you don't have to. Your code never leaves your machine, it works offline, and it costs nothing to run.

Zero Python dependencies: standard library only.

```
$ git add .
$ git commit            # editor opens with the message already written
```

## What makes it different

| Feature | What it does |
|---|---|
| **Style Mimic** | Reads your repo's last 30 commits and copies the conventions (Conventional Commits or plain, casing, length, emoji). No config file. |
| **Secret Guard** | Scans staged changes for API keys, tokens, private keys, `.env` files and huge binaries *before* the model or your history sees them. Local regexes, so nothing is uploaded to check. |
| **Commit Splitter** | Spots unrelated changes in one staged diff (a fix plus a README tweak) and commits them separately, each with its own message. Splits per file, never per hunk. |
| **Slow-and-Steady mode** | `--slow` lets a bigger model polish the fast model's draft. Speed is a dial you control. |
| **Sloth Stats** | A local log of the time you saved, with a sloth. |
| **Standup** | `sloth-commit standup` turns your week of commits into a few bullets. |

## Install

```bash
# 1. Install Ollama and pull a small code model (fits a 6 GB GPU)
ollama pull qwen2.5-coder:3b
ollama pull qwen2.5-coder:7b      # optional, for --slow

# 2. Install sloth-commit
pip install .                      # from a clone of this repo

# 3. Check everything is wired up
sloth-commit doctor
```

## Use

```bash
sloth-commit msg              # print a message for what's staged
sloth-commit commit           # write it, confirm [Y/e/n], commit
sloth-commit commit -a -y     # stage everything and commit without asking
sloth-commit commit --slow    # let the bigger model polish the draft
sloth-commit commit --body    # add a short bullet-point body
sloth-commit split --dry-run  # see how mixed changes would be split
sloth-commit split            # ...and do it
sloth-commit guard            # just run Secret Guard
sloth-commit style            # show the style it learned from this repo
sloth-commit stats            # how lazy have you been?
sloth-commit standup --since "3 days ago"
```

`sloth` works as a shorter alias for `sloth-commit`.

### The hook (the laziest way)

```bash
sloth-commit hook install     # per repository
```

From then on, plain `git commit` opens your editor with the message already written. The hook stays out of the way: it does nothing for `-m`, merges, squashes and amends, and if Ollama is down it quietly lets you write the message yourself. It will never be the reason a commit fails, with one opt-in exception:

- `SLOTH_GUARD=block` aborts commits that contain suspected secrets (they are always *printed* as a warning).
- `SLOTH_SKIP=1` turns the hook off for one command.
- `sloth-commit hook uninstall` removes it. An existing hook that isn't ours is never overwritten without `--force`.

## Configuration

Defaults work out of the box. Override with environment variables or `~/.config/sloth-commit/config.json` (`%APPDATA%\sloth-commit\config.json` on Windows):

| Setting | Env var | Default |
|---|---|---|
| `host` | `SLOTH_HOST` | `http://localhost:11434` |
| `model` | `SLOTH_MODEL` | `qwen2.5-coder:3b` |
| `slow_model` | `SLOTH_SLOW_MODEL` | `qwen2.5-coder:7b` |
| `timeout` | `SLOTH_TIMEOUT` | `120` seconds |

Any model Ollama can run works. That is the point of building on open weights: swap the model, point it at another machine on your LAN, or fine-tune your own.

## Why open?

- **Private.** A diff is your unreleased source code. Hosted commit tools have to upload it. This one can't, because there's nothing to upload it to.
- **Secret Guard only makes sense locally.** Checking a diff for leaked keys by sending it to a server is how you leak the keys.
- **Free and offline.** No API key, no per-token bill, works on a train.
- **Swappable.** Different model, different machine, your own fine-tune.

## How it works

1. `git diff --cached` is trimmed to fit a small model: lock files and binaries are summarised, each file and the total are capped, and the model is told when the diff was cut.
2. Style Mimic analyses recent subjects and writes the style rules into the prompt.
3. The model's reply is cleaned (code fences, quotes, `<think>` blocks and "Here is the message:" lead-ins are stripped).
4. Every message is recorded locally in `stats.jsonl`. It stores length and timing, never the diff.

## Honest limits

- A 3B model writes a *good* message for focused changes and a *vague* one for sprawling diffs. That's what `--slow`, `--body` and the confirm step are for.
- Commit Splitter groups whole files. If one file holds two unrelated changes, they stay together, and files with both staged and unstaged edits are refused rather than guessed at.
- The "time saved" figure is an estimate (typing speed plus a fixed think time per commit, configurable), not a measurement.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The tests run against a fake Ollama server, so they need no GPU and no network.

## License

MIT
