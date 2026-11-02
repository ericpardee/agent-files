#!/usr/bin/env python3
"""Lint a deliverable for metaspeak, banned punctuation, and tropes.

Usage: lint.py <file> [<file> ...]
Exit 0 = clean, 1 = findings, 2 = usage/read error.
"""
import re
import sys

METASPEAK = [
    r"\bas discussed\b",
    r"\bas mentioned\b",
    r"\bas requested\b",
    r"\bper (?:your|our|the) (?:feedback|conversation|discussion|review)\b",
    r"\bupdated? to reflect\b",
    r"\bin this (?:revision|version|draft|update)\b",
    r"\bthis (?:revised|updated) (?:version|draft|report|doc)\b",
    r"\b(?:previous|prior|earlier) (?:draft|version|revision)\b",
    r"\bI'?ve (?:updated|revised|incorporated|addressed)\b",
    r"\bchanges? (?:made|from) (?:in|to|the) (?:previous|prior|last)\b",
    r"\baddress(?:ed|ing) (?:your|the) (?:feedback|comments|concerns)\b",
    r"\bfeedback has been\b",
    r"\bnow (?:includes|reflects|incorporates)\b",
    r"\bv\d+ of this\b",
]

GREETING = r"^\s*(?:hi|hey|hello|dear)\s+[A-Z][\w'-]*"

RULES = [
    ("metaspeak", METASPEAK),
    ("greeting: no comma, nothing else on the line", [GREETING + r"\s*,", GREETING + r"[^\n]*\S[^\n]*\s\S"]),
    ("em/en dash", [r"—", r"–"]),
    ("'isn't just' trope", [r"\bisn'?t just\b", r"\bnot just [^.\n]{0,60}, it'?s\b"]),
]


def lint(path):
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError as exc:
        print(f"ERROR reading {path}: {exc}")
        return None
    findings = []
    for lineno, line in enumerate(lines, 1):
        if re.match(GREETING + r"\s*$", line, re.IGNORECASE) and lineno < len(lines) and lines[lineno].strip():
            findings.append((lineno, "greeting: blank line must follow", line.strip()[:120]))
        for label, patterns in RULES:
            for pat in patterns:
                if re.search(pat, line, re.IGNORECASE):
                    findings.append((lineno, label, line.strip()[:120]))
                    break
    return findings


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    total = 0
    for path in sys.argv[1:]:
        findings = lint(path)
        if findings is None:
            return 2
        for lineno, label, text in findings:
            print(f"{path}:{lineno}: [{label}] {text}")
        total += len(findings)
    if total:
        print(f"\n{total} finding(s). Fix and re-run.")
        return 1
    print("clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
