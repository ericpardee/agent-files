#!/usr/bin/env python3
"""Antigravity CLI status line, laid out like the Claude Code one in statusline.py:
account badge, model and effort, mode, directory and branch on line one; context
bar, cost, elapsed time, quota and background work on line two.

Reads the status JSON the Antigravity CLI passes on stdin. Wire it in
~/.gemini/antigravity-cli/settings.json:
    "statusLine": {"type": "command", "command": "python3 ~/path/to/antigravity-statusline.py"}

An optional, untracked identity.json next to that settings file names the account
the install should be signed in to:
    {"label": "work", "email_domain": "example.com", "icon": "🏢"}
If email_domain is set and the signed-in account does not match, the badge shows a
WRONG ACCOUNT warning.
"""
import json, os, re, subprocess, sys

CONFIG_DIR = os.path.expanduser("~/.gemini/antigravity-cli")
MAGENTA, REDBG, DIM = "\033[35m", "\033[41m\033[97m", "\033[2m"
CYAN, GREEN, YELLOW, RED, BLUE, RESET = "\033[36m", "\033[32m", "\033[33m", "\033[31m", "\033[94m", "\033[0m"


def account_badge(data):
    """Show the signed-in account and flag one the install was not meant for."""
    email = data.get("email") or ""
    if not email:
        starting = data.get("agent_state") in ("authenticating", "initializing")
        return f"{DIM}{'signing in' if starting else 'no account'}{RESET} "
    try:
        ident = json.load(open(os.path.join(CONFIG_DIR, "identity.json")))
    except Exception:
        ident = {}
    domain = ident.get("email_domain", "")
    if domain and not email.endswith("@" + domain):
        return f"{REDBG} ⚠ WRONG ACCOUNT {RESET} "
    color = MAGENTA if ident else DIM
    return f"{color}{ident.get('icon', '🏠')} {ident.get('label', 'personal')}:{email.split('@')[0]}{RESET} "


def model_label(data):
    """'Gemini 4 Neon (Medium)' with effort 'medium' reads '[Gemini 4 Neon · medium]'."""
    model = data.get("model") or {}
    name = model.get("display_name") or model.get("id") or "no model"
    effort = model.get("effort") or ""
    variant = re.match(r"^(.*?)\s*\(([^()]+)\)$", name)
    if variant and effort.lower() in ("", variant.group(2).lower()):
        name, effort = variant.group(1), effort or variant.group(2).lower()
    return f"[{name} · {effort}]" if effort else f"[{name}]"


def branch_label(data, directory):
    if (data.get("vcs") or {}).get("type") != "git":
        return ""
    try:
        branch = subprocess.check_output(
            ["git", "-C", directory, "branch", "--show-current"],
            text=True, stderr=subprocess.DEVNULL, timeout=1).strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    return f" | 🌿 {branch}" if branch else ""


def etime_seconds(text):
    """ps prints elapsed time as [[dd-]hh:]mm:ss."""
    days, _, clock = text.rpartition("-")
    parts = [int(p) for p in clock.split(":")]
    parts = [0] * (3 - len(parts)) + parts
    return (int(days or 0) * 24 + parts[0]) * 3600 + parts[1] * 60 + parts[2]


def session_seconds():
    """Age of the CLI process this script runs under; the payload carries no duration."""
    pid = os.getppid()
    for _ in range(3):
        try:
            fields = subprocess.check_output(
                ["ps", "-o", "ppid=,etime=,comm=", "-p", str(pid)],
                text=True, stderr=subprocess.DEVNULL, timeout=1).split(None, 2)
            if len(fields) < 3:
                return None
            if os.path.basename(fields[2].strip()).startswith(("antigravity", "agy")):
                return etime_seconds(fields[1])
            pid = int(fields[0])
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
        if pid <= 1:
            return None
    return None


def quota_label(data):
    """The quota bucket with the least left."""
    lowest = None
    for name, bucket in (data.get("quota") or {}).items():
        left = (bucket or {}).get("remaining_fraction")
        if left is None or (bucket or {}).get("disabled"):
            continue
        if lowest is None or left < lowest[1]:
            lowest = (name, left)
    if not lowest:
        return ""
    pct = int(lowest[1] * 100)
    color = RED if pct <= 10 else YELLOW if pct <= 30 else GREEN
    return f" | {color}{pct}%{RESET} {lowest[0]} left"


def background_label(data):
    tasks = int(data.get("task_count") or 0)
    agents = sum(1 for a in data.get("subagents") or [] if (a or {}).get("status") == "running")
    out = ""
    if agents:
        out += f" | 🤖 {agents} subagent{'s' if agents != 1 else ''}"
    if tasks:
        out += f" | 🔧 {tasks} task{'s' if tasks != 1 else ''}"
    return out


def main():
    data = json.load(sys.stdin)
    workspace = data.get("workspace") or {}
    directory = workspace.get("current_dir") or data.get("cwd") or os.getcwd()
    mode = data.get("cycle_mode") or ""
    mode = f" 🧭 {BLUE}{mode}{RESET}" if mode else ""

    pct = int(float((data.get("context_window") or {}).get("used_percentage") or 0))
    pct = max(0, min(100, pct))
    bar_color = RED if pct >= 90 else YELLOW if pct >= 70 else GREEN
    bar = "█" * (pct // 10) + "░" * (10 - pct // 10)

    cost = (data.get("cost") or {}).get("total_usd") or 0
    seconds = session_seconds()
    elapsed = f" | ⏱️ {seconds // 60}m {seconds % 60}s" if seconds is not None else ""

    print(f"{account_badge(data)}{CYAN}{model_label(data)}{RESET}{mode} "
          f"📁 {os.path.basename(directory.rstrip('/')) or directory}{branch_label(data, directory)}")
    print(f"{bar_color}{bar}{RESET} {pct}% | {YELLOW}${cost:.2f}{RESET}{elapsed}"
          f"{quota_label(data)}{background_label(data)}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
