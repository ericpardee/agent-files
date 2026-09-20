#!/usr/bin/env python3
"""Public-repo guard: refuse a commit that would publish a secret, a home path,
or a private identifier. Runs as pre-commit (staged files) and commit-msg.

Blocked: token shapes (AWS, GitHub, Slack, Anthropic, OpenAI, Google, private
keys, inline credential assignments), absolute home directories (/Users/<name>,
/home/<name>), and the words listed in hooks/blocked-words.txt (one regex per
line, case-insensitive; blocked-words.local.txt is the untracked, machine-local
list). Append "lint-allow" to a line to exempt it.
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOW = "lint-allow"
SHAPES = [
    (r"AKIA[0-9A-Z]{16}", "AWS access key"),
    (r"\b(?:ghp|gho|ghu|ghs)_[A-Za-z0-9]{20,}", "GitHub token"),
    (r"github_pat_[A-Za-z0-9_]{20,}", "GitHub fine-grained PAT"),
    (r"\bxox[bpoas]-[A-Za-z0-9-]{10,}", "Slack token"),
    (r"\bsk-ant-[A-Za-z0-9_\-]{16,}", "Anthropic key"),
    (r"\bsk-[A-Za-z0-9_\-]{32,}", "OpenAI-style key"),
    (r"\bAIza[A-Za-z0-9_\-]{30,}", "Google API key"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key material"),
    (r"(?i)\b(?:password|passwd|secret|token|api_?key|client_secret)\b\s*[:=]\s*['\"]?[A-Za-z0-9+/_.\-]{16,}", "inline credential assignment"),
    (r"\b[A-Z0-9_]*(?:KEY|TOKEN|PASSWORD|SECRET)\s*=\s*['\"]?[A-Za-z0-9+/_.\-]{16,}", "env-style credential assignment"),
    (r"(?<![\w.])/(?:Users|home)/[A-Za-z0-9._-]+/", "absolute home path; use ~ or $HOME"),
]


def blocked_words():
    pats = []
    for name in ("blocked-words.txt", "blocked-words.local.txt"):
        path = os.path.join(ROOT, "hooks", name)
        if os.path.isfile(path):
            for raw in open(path, encoding="utf-8"):
                line = raw.strip()
                if line and not line.startswith("#"):
                    pats.append(line)
    return re.compile("(?i)(?:" + "|".join(pats) + ")") if pats else None


def scan(label, text, findings):
    words = blocked_words()
    for n, line in enumerate(text.splitlines(), 1):
        if ALLOW in line:
            continue
        for pat, what in SHAPES:
            if re.search(pat, line):
                findings.append(f"{label}:{n}: {what}")
                break
        if words and words.search(line):
            findings.append(f"{label}:{n}: blocked word (hooks/blocked-words.txt)")


def staged_files():
    out = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [f for f in out.split("\0") if f]


def main():
    findings = []
    if len(sys.argv) == 3 and sys.argv[1] == "--message":
        scan("commit message", open(sys.argv[2], encoding="utf-8", errors="replace").read(), findings)
    else:
        files = staged_files()
        if len(sys.argv) == 2 and sys.argv[1] == "--all":
            files = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\0")
            files = [f for f in files if f]
        for rel in files:
            if rel == "hooks/lint.py" or rel == "hooks/blocked-words.txt":
                continue
            blob = subprocess.run(["git", "show", ":" + rel], cwd=ROOT, capture_output=True).stdout if "--all" not in sys.argv else open(os.path.join(ROOT, rel), "rb").read()
            if b"\0" in blob[:4096]:
                continue
            scan(rel, blob.decode("utf-8", errors="replace"), findings)
    for f in findings:
        print(f)
    if findings:
        print(f"\n{len(findings)} finding(s). This repository is public; fix before committing.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
