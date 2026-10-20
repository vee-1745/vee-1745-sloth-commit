"""Secret Guard: scan staged changes locally, before they ever reach history.

Everything here is a regex over text that never leaves the machine, which is the
whole point: a hosted tool would have to upload your diff to do the same check.
"""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass
from typing import List, Optional

from . import git

SECRET_PATTERNS = [
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("Stripe live key", re.compile(r"\b[sr]k_live_[A-Za-z0-9]{16,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("OpenAI-style key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b")),
    ("Private key block", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    (
        "Credentials in URL",
        re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@'\"]+:[^\s/@'\"]{3,}@[^\s/'\"]+", re.I),
    ),
    (
        "Hard-coded secret assignment",
        re.compile(
            r"""(?ix)
            \b[\w.-]*(?:password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|private[_-]?key)\b
            ["']?\s*[:=]\s*
            ["']([^"'\s]{8,})["']
            """
        ),
    ),
]

PLACEHOLDER_RE = re.compile(
    r"(?i)(example|placeholder|changeme|change_me|your[_-]|xxxx|<[^>]+>|\$\{|\{\{|dummy|sample|test|fake|redacted|\*{3,})"
)

SENSITIVE_FILES = [
    ".env",
    ".env.*",
    "*.pem",
    "*.p12",
    "*.pfx",
    "*.keystore",
    "*.jks",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    ".npmrc",
    ".pypirc",
    ".netrc",
    "credentials.json",
    "service-account*.json",
    "*.tfstate",
]
SAFE_FILES = [".env.example", ".env.sample", ".env.template", "*.pub", ".env.dist"]
LARGE_FILE_BYTES = 5 * 1024 * 1024


@dataclass
class Finding:
    kind: str  # "secret", "file" or "size"
    path: str
    line: int
    label: str
    preview: str = ""

    def format(self) -> str:
        loc = f"{self.path}:{self.line}" if self.line else self.path
        tail = f"  {self.preview}" if self.preview else ""
        return f"{loc}  {self.label}{tail}"


def mask(text: str) -> str:
    """Show enough to recognise a hit without printing the whole secret."""
    text = text.strip()
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:4]}...{text[-2:]}"


def _matches(name: str, patterns: List[str]) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in patterns)


def scan_text(path: str, lineno: int, text: str) -> Optional[Finding]:
    if len(text) > 2000:
        text = text[:2000]
    for label, pattern in SECRET_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        value = m.group(1) if m.groups() else m.group(0)
        if label == "Hard-coded secret assignment" and PLACEHOLDER_RE.search(value):
            continue
        if label == "Credentials in URL" and PLACEHOLDER_RE.search(m.group(0)):
            continue
        return Finding("secret", path, lineno, label, mask(value))
    return None


def scan_staged(cwd: Optional[str] = None) -> List[Finding]:
    findings: List[Finding] = []

    for path in git.staged_files(cwd):
        name = os.path.basename(path)
        if _matches(name, SENSITIVE_FILES) and not _matches(name, SAFE_FILES):
            findings.append(Finding("file", path, 0, "Sensitive-looking file"))
            continue
        size = git.staged_blob_size(path, cwd)
        if size is not None and size > LARGE_FILE_BYTES:
            findings.append(Finding("size", path, 0, f"Large file ({size / 1024 / 1024:.1f} MB)"))

    flagged_files = {f.path for f in findings if f.kind == "file"}
    seen = set()
    for path, lineno, text in git.added_lines(cwd):
        if path in flagged_files:
            continue
        hit = scan_text(path, lineno, text)
        if hit and (hit.path, hit.line, hit.label) not in seen:
            seen.add((hit.path, hit.line, hit.label))
            findings.append(hit)
    return findings
