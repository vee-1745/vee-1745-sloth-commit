"""Command line interface: `sloth-commit` (alias `sloth`)."""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import __version__, generate, git, guard, hook, llm, split, stats, style
from .config import Config, load_config


def _out(text: str = "", file=None) -> None:
    print(text, file=file or sys.stdout)


def _err(text: str) -> None:
    print(text, file=sys.stderr)


def _ask(prompt: str, choices: str, default: str) -> str:
    try:
        ans = input(f"{prompt} [{choices}] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        _out()
        return "n"
    return ans[:1] if ans else default


def _show(message: str) -> None:
    _out()
    for line in message.splitlines():
        _out("  " + line)
    _out()


def _guard_check(force: bool, cwd: Optional[str] = None) -> bool:
    """Print any findings. Returns True if it's OK to go on."""
    findings = guard.scan_staged(cwd)
    if not findings:
        return True
    _err("Secret Guard found something suspicious in your staged changes:")
    for f in findings:
        _err("  " + f.format())
    if force:
        _err("Continuing because --force was given.")
        return True
    _err("Nothing was committed. Fix it, or re-run with --force if these are false alarms.")
    return False


def _generate(cfg: Config, args, paths=None) -> generate.Draft:
    _err(f"Asking {cfg.model}{' then ' + cfg.slow_model if args.slow else ''}...")
    d = generate.draft(cfg, paths=paths, slow=args.slow, body=args.body)
    if d.slow_failed:
        _err(f"Slow polish skipped: {d.slow_failed}")
    stats.record(d.message, d.seconds, d.mode, cfg)
    return d


def cmd_msg(cfg: Config, args) -> int:
    if not args.no_guard and not _guard_check(args.force):
        return 1
    d = _generate(cfg, args)
    _out(d.message)
    return 0


def cmd_commit(cfg: Config, args) -> int:
    if not args.no_guard and not _guard_check(args.force):
        return 1
    if args.auto_stage:
        git.run_git(["add", "-A"])
    d = _generate(cfg, args)
    message = d.message
    if args.yes:
        return git.commit(message)
    _show(message)
    choice = _ask("Commit with this message?", "Y/e/n", "y")
    if choice == "y":
        return git.commit(message)
    if choice == "e":
        return git.commit(message, edit=True)
    _out("Nothing committed.")
    return 1


def cmd_split(cfg: Config, args) -> int:
    if not args.no_guard and not _guard_check(args.force):
        return 1
    split.check_safe_to_split()
    _err(f"Looking for unrelated changes with {cfg.model}...")
    groups = split.propose(cfg)
    if not groups:
        _out("Nothing is staged.")
        return 1
    if len(groups) < 2:
        _out("These changes look like one logical commit. Use `sloth-commit commit` instead.")
        return 0

    drafts = [generate.draft(cfg, paths=g.files, slow=args.slow, body=args.body) for g in groups]
    messages = [d.message for d in drafts]

    _out(f"Proposed {len(groups)} commits:")
    for i, (g, m) in enumerate(zip(groups, messages), 1):
        _out(f"\n  {i}. {g.title}")
        for f in g.files:
            _out(f"       {f}")
        _out(f"     -> {m.splitlines()[0]}")
    _out()
    if args.dry_run:
        return 0
    if not args.yes and _ask("Make these commits?", "y/N", "n") != "y":
        _out("Nothing committed.")
        return 1
    for d in drafts:  # only count messages that were actually used, not dry runs
        stats.record(d.message, d.seconds, d.mode, cfg)
    n = split.execute(groups, messages, on_commit=lambda i, m: _out(f"  committed {i + 1}/{len(groups)}: {m.splitlines()[0]}"))
    _out(f"Done: {n} commits.")
    return 0


def cmd_guard(cfg: Config, args) -> int:
    findings = guard.scan_staged()
    if not findings:
        _out("Secret Guard: nothing suspicious in the staged changes.")
        return 0
    _out("Secret Guard found possible problems:")
    for f in findings:
        _out("  " + f.format())
    return 1


def cmd_style(cfg: Config, args) -> int:
    _out(style.describe(style.analyze(git.recent_subjects(30))))
    return 0


def cmd_stats(cfg: Config, args) -> int:
    _out(stats.render(stats.load(), cfg))
    return 0


def cmd_standup(cfg: Config, args) -> int:
    author = None if args.all else (git.user_email() or None)
    subjects = git.log_subjects(args.since, author)
    if not subjects:
        _out(f"No commits found since {args.since!r}.")
        return 0
    system = (
        "You write a short standup update from a list of git commit subjects. "
        "Group related commits, use 3 to 6 bullet points starting with '- ', plain language, past tense. "
        "Do not invent work that is not in the list. Output only the bullets."
    )
    user = f"Commits since {args.since}:\n" + "\n".join(f"- {s}" for s in subjects[:80])
    _out(generate.clean(llm.chat(cfg, cfg.model, system, user, num_predict=400), max_body_lines=10))
    return 0


def cmd_hook(cfg: Config, args) -> int:
    if args.action == "install":
        path = hook.install(force=args.force)
        _out(f"Installed {path}")
        _out("Now `git commit` opens your editor with a message already written.")
        _out("Set SLOTH_GUARD=block to also stop commits that contain secrets.")
    else:
        path = hook.uninstall()
        _out(f"Removed {path}" if path else "No hook installed.")
    return 0


def cmd_hook_run(cfg: Config, args) -> int:
    return hook.run(args.msg_file, args.source or "")


def cmd_doctor(cfg: Config, args) -> int:
    ok = True
    try:
        git.repo_root()
        _out("ok    inside a git repository")
    except git.GitError as err:
        _out(f"warn  {err}")
    try:
        models = llm.list_models(cfg)
        _out(f"ok    Ollama is running at {cfg.host} ({len(models)} models)")
        for label, name in (("fast model", cfg.model), ("slow model", cfg.slow_model)):
            have = name in models or f"{name}:latest" in models
            _out(f"{'ok  ' if have else 'FAIL'}  {label} {name}" + ("" if have else f"  -> ollama pull {name}"))
            ok = ok and (have or label == "slow model")
    except llm.LLMError as err:
        _out(f"FAIL  {err}")
        ok = False
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sloth-commit",
        description="Let a local open-weight model write your commit messages. Private, offline, free.",
    )
    p.add_argument("--version", action="version", version=f"sloth-commit {__version__}")
    sub = p.add_subparsers(dest="command", metavar="<command>")

    def common(sp, commit_like=True):
        sp.add_argument("--slow", action="store_true", help="polish the draft with the bigger slow model")
        sp.add_argument("--body", action="store_true", help="also write a short bullet-point body")
        sp.add_argument("--no-guard", action="store_true", help="skip Secret Guard")
        sp.add_argument("--force", action="store_true", help="continue even if Secret Guard complains")

    sp = sub.add_parser("msg", help="print a message for the staged changes")
    common(sp)
    sp.set_defaults(func=cmd_msg)

    sp = sub.add_parser("commit", help="write a message and commit")
    common(sp)
    sp.add_argument("-y", "--yes", action="store_true", help="commit without asking")
    sp.add_argument("-a", "--auto-stage", action="store_true", help="stage all changes first (git add -A)")
    sp.set_defaults(func=cmd_commit)

    sp = sub.add_parser("split", help="split mixed staged changes into several commits")
    common(sp)
    sp.add_argument("-y", "--yes", action="store_true", help="make the commits without asking")
    sp.add_argument("--dry-run", action="store_true", help="show the plan only")
    sp.set_defaults(func=cmd_split)

    sub.add_parser("guard", help="scan staged changes for secrets").set_defaults(func=cmd_guard)
    sub.add_parser("style", help="show the commit style learned from this repo").set_defaults(func=cmd_style)
    sub.add_parser("stats", help="show how much time you saved").set_defaults(func=cmd_stats)
    sub.add_parser("doctor", help="check git, Ollama and models").set_defaults(func=cmd_doctor)

    sp = sub.add_parser("standup", help="summarise your recent commits")
    sp.add_argument("--since", default="1 week ago", help='time window (default: "1 week ago")')
    sp.add_argument("--all", action="store_true", help="include commits from everyone")
    sp.set_defaults(func=cmd_standup)

    sp = sub.add_parser("hook", help="install or remove the prepare-commit-msg hook")
    sp.add_argument("action", choices=["install", "uninstall"])
    sp.add_argument("--force", action="store_true", help="replace an existing hook")
    sp.set_defaults(func=cmd_hook)

    sp = sub.add_parser("hook-run")  # called by git, not by people
    sp.add_argument("msg_file")
    sp.add_argument("source", nargs="?", default="")
    sp.set_defaults(func=cmd_hook_run)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    cfg = load_config()
    try:
        return args.func(cfg, args)
    except (llm.LLMError, git.GitError, generate.NothingStaged, split.SplitError, hook.HookError) as err:
        _err(f"sloth-commit: {err}")
        return 1
    except KeyboardInterrupt:
        _err("\ninterrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
