#!/usr/bin/env python3
import json, sys, subprocess, os


def account_badge():
    """Show the active Claude account and flag a sandbox/account mismatch.

    Install identity comes from an untracked identity.json in the active
    config dir (CLAUDE_CONFIG_DIR, or ~/.claude for the default install):
        {"label": "work", "email_domain": "example.com", "icon": "🏢"}
    If email_domain is set and the logged-in account doesn't match,
    the badge shows a WRONG ACCOUNT warning.
    """
    MAGENTA, REDBG, DIM, RST = "\033[35m", "\033[41m\033[97m", "\033[2m", "\033[0m"
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    cfg_dir = os.path.realpath(os.path.expanduser(cfg)) if cfg else None
    acct_path = os.path.join(cfg_dir, ".claude.json") if cfg_dir else os.path.expanduser("~/.claude.json")
    try:
        email = (json.load(open(acct_path)).get("oauthAccount") or {}).get("emailAddress", "")
    except Exception:
        email = ""
    try:
        ident = json.load(open(os.path.join(cfg_dir or os.path.expanduser("~/.claude"), "identity.json")))
    except Exception:
        ident = {}
    domain = ident.get("email_domain", "")
    if domain and not email.endswith("@" + domain):
        return f"{REDBG} ⚠ WRONG ACCOUNT {RST} "
    if not email:
        return f"{DIM}logged out{RST} "
    user = email.split("@")[0]
    icon = ident.get("icon", "🏢" if cfg_dir else "🏠")
    label = ident.get("label", os.path.basename(os.path.dirname(cfg_dir)) if cfg_dir else "personal")
    color = MAGENTA if cfg_dir else DIM
    return f"{color}{icon} {label}:{user}{RST} "


data = json.load(sys.stdin)
model = data['model']['display_name']
directory = os.path.basename(data['workspace']['current_dir'])
cost = data.get('cost', {}).get('total_cost_usd', 0) or 0
pct = int(data.get('context_window', {}).get('used_percentage', 0) or 0)
duration_ms = data.get('cost', {}).get('total_duration_ms', 0) or 0
effort = (data.get('effort') or {}).get('level', '')
effort = f" · {effort}" if effort else ""

# Claude Code reports the built-in style as "default"; name what it actually is.
STYLE_LABELS = {
    'default': 'Software Engineering',
    'proactive': 'Proactive',
    'concise': 'Concise',
    'explanatory': 'Explanatory',
    'learning': 'Learning',
}
style = ((data.get('output_style') or {}).get('name') or '').strip()
style = STYLE_LABELS.get(style.lower(), style)

CYAN, GREEN, YELLOW, RED, BLUE, RESET = '\033[36m', '\033[32m', '\033[33m', '\033[31m', '\033[94m', '\033[0m'
style = f" 🎭 {BLUE}{style}{RESET}" if style else ""

bar_color = RED if pct >= 90 else YELLOW if pct >= 70 else GREEN
filled = pct // 10
bar = '█' * filled + '░' * (10 - filled)

mins, secs = duration_ms // 60000, (duration_ms % 60000) // 1000

try:
    branch = subprocess.check_output(['git', 'branch', '--show-current'], text=True, stderr=subprocess.DEVNULL).strip()
    branch = f" | 🌿 {branch}" if branch else ""
except:
    branch = ""

print(f"{account_badge()}{CYAN}[{model}{effort}]{RESET}{style} 📁 {directory}{branch}")
print(f"{bar_color}{bar}{RESET} {pct}% | {YELLOW}${cost:.2f}{RESET} | ⏱️ {mins}m {secs}s")
