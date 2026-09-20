#!/usr/bin/env python3
"""Lint an external-memory hub: line caps, secret shapes, blocked vocabulary.

Scans every text file in the repository. Exit 0 clean, 1 findings. Wired as the
pre-commit and commit-msg hooks via core.hooksPath=hooks.

Blocked vocabulary comes from lint-vocabulary.txt at the repository root, one
regular expression per line, "#" for comments. An absent or empty file blocks
nothing.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CAPS = {
    "INDEX.md": 150,
    "ACCESS.md": 200,
    "CLAUDE.md": 60,
}
SPOKE_CAP = 100
DEFAULT_MD_CAP = 150

# Data artifacts (handoffs, exported reports): no line cap, and the generic
# base64-blob heuristic is skipped because long URLs false-positive. Specific
# secret shapes are still scanned.
DATA_DIRS = ("handoffs",)

# Machine-written directories (session ledgers, mirrored agent memory): no
# caps and no vocabulary scan, since the text is distilled from sessions
# rather than hand-curated. Specific token shapes are still scanned.
LEDGER_DIRS = ("ledger", "memory")
GENERIC_SECRET_LABELS = ("long base64 blob", "inline credential assignment",
                         "env-style credential assignment")

SECRET_SHAPES = [
    (r"AKIA[0-9A-Z]{16}", "AWS access key"),
    (r"ASIA[0-9A-Z]{16}", "AWS temporary access key"),
    (r"\b(?:ghp|gho|ghu|ghs)_[A-Za-z0-9]{20,}", "GitHub token"),
    (r"github_pat_[A-Za-z0-9_]{20,}", "GitHub fine-grained PAT"),
    (r"\bAT[ACB]TT[A-Za-z0-9_\-=]{15,}", "Atlassian token"),
    (r"\bATBB[A-Za-z0-9]{15,}", "Bitbucket app password"),
    (r"\bxox[bpoas]-[A-Za-z0-9-]{10,}", "Slack token"),
    (r"\bsk-ant-[A-Za-z0-9_\-]{16,}", "Anthropic key"),
    (r"\bsk-[A-Za-z0-9_\-]{32,}", "OpenAI-style key"),
    (r"\bAIza[A-Za-z0-9_\-]{30,}", "Google API key"),
    (r"\bcfut_[A-Za-z0-9]{16,}", "Cloudflare token"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key material"),
    (r"(?:^|[^A-Za-z0-9/+=])[A-Za-z0-9/+=]{60,}(?:[^A-Za-z0-9/+=]|$)", "long base64 blob (possible key material)"),
    (r"(?i)\b(?:password|passwd|pwd|secret|token|api_?key|client_secret|private_key)\b\s*[:=]\s*['\"]?[A-Za-z0-9+/_.\-]{10,}", "inline credential assignment"),
    (r"(?i)\b[A-Z0-9_]*(?:KEY|TOKEN|PASSWORD|SECRET)\s*=\s*['\"]?[A-Za-z0-9+/_.\-]{10,}", "env-style credential assignment"),
    (r"https?://[^/\s:]+:[^@\s]+@", "URL with embedded credentials"),
]

ALLOW_MARKER = "lint-allow"  # append to a line to suppress (use sparingly)
VOCAB_FILE = os.path.join(ROOT, "lint-vocabulary.txt")


def load_vocabulary():
    patterns = []
    try:
        with open(VOCAB_FILE, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if line and not line.startswith("#"):
                    patterns.append(line)
    except OSError:
        return None
    if not patterns:
        return None
    return re.compile("(?i)(?:" + "|".join(patterns) + ")")


BLOCKED = load_vocabulary()


def is_text(path):
    try:
        with open(path, "rb") as fh:
            chunk = fh.read(4096)
        return b"\x00" not in chunk
    except OSError:
        return False


def repo_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if not d.startswith(".git")
                       and d not in ("__pycache__",)]
        for f in filenames:
            if f in (".gitignore", "lint-vocabulary.txt"):
                continue
            # gitignored machine-local files are never synced and never linted
            if f.endswith(".local.md") or f.endswith(".env"):
                continue
            p = os.path.join(dirpath, f)
            if os.path.abspath(p) == os.path.abspath(__file__):
                continue  # the shape definitions above match themselves
            if is_text(p):
                yield p


def is_data_dir(rel):
    return rel.startswith(tuple(d + os.sep for d in DATA_DIRS))


def is_ledger_dir(rel):
    return rel.startswith(tuple(d + os.sep for d in LEDGER_DIRS))


def cap_for(rel):
    if rel in CAPS:
        return CAPS[rel]
    if is_data_dir(rel) or is_ledger_dir(rel):
        return None
    if rel.startswith("initiatives" + os.sep):
        return SPOKE_CAP
    if rel.endswith(".md"):
        return DEFAULT_MD_CAP
    return None


def lint_message(path):
    """Commit-message mode (hooks/commit-msg): secret and vocabulary checks, no caps."""
    findings = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    for lineno, line in enumerate(lines, 1):
        if line.startswith("#") or ALLOW_MARKER in line:
            continue
        for pat, label in SECRET_SHAPES:
            if label.startswith("long base64 blob"):
                continue
            if re.search(pat, line):
                findings.append(f"commit message line {lineno}: possible {label}")
                break
        if BLOCKED and BLOCKED.search(line):
            findings.append(f"commit message line {lineno}: blocked vocabulary; describe the decision, not the source")
    for f in findings:
        print(f)
    if findings:
        print(f"\n{len(findings)} finding(s) in the commit message. Reword it.")
        return 1
    return 0


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--message":
        return lint_message(sys.argv[2])
    findings = []
    for path in repo_files():
        rel = os.path.relpath(path, ROOT)
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
        cap = cap_for(rel)
        if cap is not None and len(lines) > cap:
            findings.append(f"{rel}: {len(lines)} lines exceeds cap {cap}; prune before committing")
        for lineno, line in enumerate(lines, 1):
            if ALLOW_MARKER in line:
                continue
            for pat, label in SECRET_SHAPES:
                if label.startswith("long base64 blob") and is_data_dir(rel):
                    continue
                if label.startswith(GENERIC_SECRET_LABELS) and is_ledger_dir(rel):
                    continue
                if re.search(pat, line):
                    findings.append(f"{rel}:{lineno}: possible {label}; pointers only (see ACCESS.md)")
                    break
            if is_ledger_dir(rel):
                continue
            if BLOCKED and BLOCKED.search(line):
                findings.append(f"{rel}:{lineno}: blocked vocabulary (lint-vocabulary.txt); this content belongs elsewhere")
    for f in findings:
        print(f)
    if findings:
        print(f"\n{len(findings)} finding(s). Do not commit until clean.")
        return 1
    print("clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
