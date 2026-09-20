#!/usr/bin/env python3
"""session-ledger: automatic catalog of Claude Code sessions.

Modes:
  --sweep                 distill sessions with new activity since last run
  --session <path|id>     distill one session now (used by the SessionEnd hook)
  --seed [--days N]       catalog all sessions active in the last N days (default 14)
  --dream                 weekly consolidation pass over the ledger file
  --dry-run               with any mode: print what would happen, write nothing

Config comes from <config dir>/session-ledger.env (KEY=VALUE), where the
config dir is CLAUDE_CONFIG_DIR when set (e.g. a secondary work install)
and ~/.claude otherwise, so each install keeps its own env file, state,
lock, log, and ledger. Process environment variables with the same names
take precedence, which is handy for testing. Only LEDGER_FILE is required.

stdlib only. No third-party imports.
"""

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

HOME = os.path.expanduser("~")
CLAUDE_DIR = os.path.realpath(
    os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude"))
)
ENV_FILE = os.path.join(CLAUDE_DIR, "session-ledger.env")
STATE_FILE = os.path.join(CLAUDE_DIR, "session-ledger.state.json")
LOCK_DIR = os.path.join(CLAUDE_DIR, "session-ledger.lock")
LOG_FILE = os.path.join(CLAUDE_DIR, "session-ledger.log")
RUNS_DIR = os.path.join(CLAUDE_DIR, "session-ledger-runs")

LEDGER_HEADER = "# Coding agent session ledger"
# Ledgers written before Codex support carry the old header; both are valid.
LEGACY_LEDGER_HEADERS = ("# Claude Code session ledger",)
RESUME_ID_RE = re.compile(r"--resume ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
LOCK_STALE_SECONDS = 2 * 60 * 60
# One distill call sends a capped excerpt and returns three lines; the dream
# call sends the whole ledger and rewrites it, which takes well over five
# minutes once the file holds a few dozen entries.
DISTILL_TIMEOUT_SECONDS = 300
DREAM_TIMEOUT_SECONDS = 1800

DEFAULTS = {
    "MODEL": "claude-sonnet-5",
    "PROJECTS_DIR": os.path.join(CLAUDE_DIR, "projects"),
    "MIN_NEW_PROMPTS": "1",
    "MAX_EXCERPT_CHARS": "10000",
    # Codex CLI sessions (rollout-*.jsonl). Default: $CODEX_HOME/sessions when
    # CODEX_HOME is set, else ~/.codex/sessions. Set to "none" to ignore Codex.
    "CODEX_SESSIONS_DIR": "",
    # Which CLI distills: "claude" (claude -p, MODEL) or "codex" (codex exec,
    # CODEX_MODEL and CODEX_REASONING_EFFORT; empty model = Codex's default).
    "DISTILL_TOOL": "claude",
    "CODEX_MODEL": "",
    "CODEX_REASONING_EFFORT": "low",
    # A session never seen before whose last activity is older than this is
    # recorded as stale and not distilled, so a first install (or newly
    # enabled Codex support) does not pour months of history into the top
    # of the ledger. --seed --days N still backfills deliberately.
    "BACKFILL_MAX_AGE_DAYS": "30",
    # Optional shell command run after a sweep that distilled at least one
    # session, and after a successful dream (for example a script that commits
    # and pushes the ledger). Runs via the shell, 300s timeout, exit logged.
    "POST_SWEEP_CMD": "",
    # Optional Pushover alert on any failure exit (a sweep whose distills all
    # failed, a dream pass that could not be applied). Both keys required.
    "PUSHOVER_APP_TOKEN": "",
    "PUSHOVER_USER_KEY": "",
}

NOISE_PREFIXES = (
    "<command-name>",
    "<command-message>",
    "<local-command-",
    "<system-reminder>",
    "<task-notification",
    "<bash-",
    "[Request interrupted",
)

DISTILL_INSTRUCTIONS = """\
You are a session cataloger. Below is a distilled excerpt of one coding-agent
session: the user's typed prompts, the latest public assistant response for
each user turn, and metadata. Responses can include progress on unfinished turns.

Return EXACTLY three markdown list lines in this format and NOTHING else. No
heading, no preamble, no code fences, no Resume line, no trailing commentary:

- Outcome: <max 3 lines: what actually happened, was decided, or was shipped>
- Artifacts: <files/PRs/reports touched, or "none">
- Open threads: <unresolved items, or "none">

Base everything only on the excerpt. Be terse and factual.
Preserve completed outcomes from earlier turns when a later turn changes topic.
An omitted or trimmed detail is unknown; it is not evidence that work is unfinished.

--- SESSION EXCERPT ---
{excerpt}
"""

DREAM_INSTRUCTIONS = """\
You are consolidating a Claude Code session ledger file. Below is the full
current content. Rewrite it and return ONLY the new file content (raw
markdown, no code fences, no commentary). Rules:

1. Keep the first line exactly: {header}
2. Directly under the header, add (or refresh) a short section titled
   "## Promote to memory" listing durable facts worth adding to long-term
   memory, for human review. Keep it under 10 bullets. If nothing qualifies,
   write "- none".
3. Merge duplicate or related entries for the same session or the same thread
   of work into a single entry. When merging, keep the newest date and the
   union of Artifacts and Open threads.
4. Mark clearly resolved threads as closed (change the Open threads line to
   "none (closed)" when a later entry shows the thread was finished).
5. CRITICAL: keep every distinct Resume line (`claude --resume <id>`). When
   entries merge, keep all their Resume lines inside the merged entry.
6. Keep newest entries at the top. Do not invent information.

--- CURRENT LEDGER ---
{ledger}
"""


def log(msg):
    line = "%s %s\n" % (datetime.datetime.now().isoformat(timespec="seconds"), msg)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


def read_env_file():
    """KEY=VALUE pairs from the per-install env file, over DEFAULTS."""
    cfg = dict(DEFAULTS)
    if os.path.isfile(ENV_FILE):
        with open(ENV_FILE, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key:
                    cfg[key] = val
    return cfg


_ALERTED = False


def notify_failure(msg):
    """Pushover alert for a failure exit, when both Pushover keys are configured.

    Reads the env file directly so it works from die() before or after config
    load. Sends at most once per process. Never raises.
    """
    global _ALERTED
    if _ALERTED:
        return
    _ALERTED = True
    try:
        cfg = read_env_file()
        token = os.environ.get("PUSHOVER_APP_TOKEN") or cfg.get("PUSHOVER_APP_TOKEN")
        user = os.environ.get("PUSHOVER_USER_KEY") or cfg.get("PUSHOVER_USER_KEY")
        if not token or not user:
            return
        data = urllib.parse.urlencode({
            "token": token,
            "user": user,
            "title": "session-ledger failed",
            "message": ("%s: %s" % (CLAUDE_DIR, msg))[:1000],
            "priority": 1,
        }).encode()
        req = urllib.request.Request("https://api.pushover.net/1/messages.json", data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            log("pushover alert sent (HTTP %d)" % resp.status)
    except Exception as exc:  # alerting must never mask the original failure
        log("pushover alert failed: %s" % exc)


def die(msg, code=1):
    sys.stderr.write("session-ledger: %s\n" % msg)
    if code:
        notify_failure(msg)
    sys.exit(code)


def load_config():
    cfg = read_env_file()
    for key in ("LEDGER_FILE", "MODEL", "PROJECTS_DIR", "MIN_NEW_PROMPTS", "MAX_EXCERPT_CHARS",
                "POST_SWEEP_CMD", "CODEX_SESSIONS_DIR", "DISTILL_TOOL", "CODEX_MODEL",
                "CODEX_REASONING_EFFORT", "BACKFILL_MAX_AGE_DAYS",
                "PUSHOVER_APP_TOKEN", "PUSHOVER_USER_KEY"):
        if os.environ.get(key):
            cfg[key] = os.environ[key]
    if not cfg.get("LEDGER_FILE"):
        die(
            "LEDGER_FILE is not set. Add LEDGER_FILE=/path/to/ledger.md to %s "
            "(run install.sh to create it) or export LEDGER_FILE." % ENV_FILE
        )
    cfg["LEDGER_FILE"] = os.path.expanduser(cfg["LEDGER_FILE"])
    cfg["PROJECTS_DIR"] = os.path.expanduser(cfg["PROJECTS_DIR"])
    cfg["MIN_NEW_PROMPTS"] = int(cfg["MIN_NEW_PROMPTS"])
    cfg["MAX_EXCERPT_CHARS"] = int(cfg["MAX_EXCERPT_CHARS"])
    cfg["BACKFILL_MAX_AGE_DAYS"] = int(cfg["BACKFILL_MAX_AGE_DAYS"])
    codex_dir = (cfg.get("CODEX_SESSIONS_DIR") or "").strip()
    if not codex_dir:
        codex_home = os.environ.get("CODEX_HOME") or os.path.join(HOME, ".codex")
        codex_dir = os.path.join(codex_home, "sessions")
    cfg["CODEX_SESSIONS_DIR"] = "" if codex_dir.lower() == "none" else os.path.expanduser(codex_dir)
    cfg["DISTILL_TOOL"] = (cfg.get("DISTILL_TOOL") or "claude").strip().lower()
    if cfg["DISTILL_TOOL"] not in ("claude", "codex"):
        die("DISTILL_TOOL must be claude or codex, not %r" % cfg["DISTILL_TOOL"])
    return cfg


# --- locking (atomic mkdir) -------------------------------------------------

def _lock_holder_dead():
    """True if the lock's recorded pid no longer exists."""
    try:
        with open(os.path.join(LOCK_DIR, "pid"), encoding="utf-8") as fh:
            pid = int(fh.read().strip())
    except (OSError, ValueError):
        return False
    try:
        os.kill(pid, 0)
        return False
    except ProcessLookupError:
        return True
    except PermissionError:
        return False


def acquire_lock():
    try:
        st = os.stat(LOCK_DIR)
        import time
        if time.time() - st.st_mtime > LOCK_STALE_SECONDS or _lock_holder_dead():
            log("breaking stale lock (%s)" % LOCK_DIR)
            shutil.rmtree(LOCK_DIR, ignore_errors=True)
    except FileNotFoundError:
        pass
    try:
        os.mkdir(LOCK_DIR)
    except FileExistsError:
        log("another instance holds the lock (%s); exiting" % LOCK_DIR)
        return False
    try:
        with open(os.path.join(LOCK_DIR, "pid"), "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    except OSError:
        pass
    return True


def release_lock():
    shutil.rmtree(LOCK_DIR, ignore_errors=True)


# --- state -------------------------------------------------------------------

def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(state):
    fd, tmp = tempfile.mkstemp(dir=CLAUDE_DIR, prefix=".session-ledger.state.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=1, sort_keys=True)
        os.replace(tmp, STATE_FILE)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --- session discovery -------------------------------------------------------

def encode_project_dir(path):
    """Mirror Claude Code's cwd -> project dir encoding.

    Verified against real dirs: every non-alphanumeric character becomes '-'
    (e.g. /Users/x/Development/github.com/y -> -Users-x-Development-github-com-y).
    """
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(path))


CODEX_ROLLOUT_RE = re.compile(r"^rollout-.*-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$")


def find_sessions(cfg):
    """Yield (session_id, jsonl_path) for top-level session transcripts.

    Claude Code transcripts live under PROJECTS_DIR/<project>/<id>.jsonl; Codex
    rollouts under CODEX_SESSIONS_DIR/YYYY/MM/DD/rollout-<stamp>-<id>.jsonl.
    Both are keyed by their session id.
    """
    projects_dir = cfg["PROJECTS_DIR"]
    skip_project = encode_project_dir(RUNS_DIR)
    if os.path.isdir(projects_dir):
        for proj in sorted(os.listdir(projects_dir)):
            proj_path = os.path.join(projects_dir, proj)
            if not os.path.isdir(proj_path) or proj == skip_project:
                continue
            for name in sorted(os.listdir(proj_path)):
                if not name.endswith(".jsonl"):
                    continue
                path = os.path.join(proj_path, name)
                if "/subagents/" in path:
                    continue
                yield name[:-6], path
    codex_dir = cfg.get("CODEX_SESSIONS_DIR") or ""
    if codex_dir and os.path.isdir(codex_dir):
        for dirpath, dirnames, filenames in os.walk(codex_dir):
            dirnames.sort()
            for name in sorted(filenames):
                match = CODEX_ROLLOUT_RE.match(name)
                if match:
                    yield match.group(1), os.path.join(dirpath, name)


def resolve_session_arg(cfg, arg):
    """Resolve --session argument (transcript path or session id) to a path."""
    if os.path.sep in arg or arg.endswith(".jsonl"):
        path = os.path.expanduser(arg)
        if os.path.isfile(path):
            return path
        die("transcript not found: %s" % arg)
    for sid, path in find_sessions(cfg):
        if sid == arg:
            return path
    die("no session jsonl found for id %s under %s or %s"
        % (arg, cfg["PROJECTS_DIR"], cfg.get("CODEX_SESSIONS_DIR") or "(no Codex dir)"))


def session_is_excluded(path):
    """Skip subagent transcripts and the recursion-guard runs project."""
    norm = os.path.abspath(path)
    if "/subagents/" in norm:
        return True
    return os.path.basename(os.path.dirname(norm)) == encode_project_dir(RUNS_DIR)


# --- transcript parsing -------------------------------------------------------

def extract_text_from_content(content):
    """Return typed-prompt text from a user record's message.content, or None.

    content is either a plain string or a list of blocks; tool_result-only
    lists carry no typed prompt.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if text:
                    parts.append(text)
        if parts:
            return "\n".join(parts)
    return None


def is_noise_prompt(text):
    stripped = text.lstrip()
    return stripped.startswith(NOISE_PREFIXES)


def parse_session(path):
    """Parse a session jsonl into the bits the distiller needs.

    Detects the format from the first record: a Codex rollout opens with a
    ``session_meta`` record; anything else is a Claude Code transcript.
    """
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                first = json.loads(raw)
            except ValueError:
                first = None
            break
        else:
            first = None
    if isinstance(first, dict) and first.get("type") == "session_meta":
        return parse_codex_session(path)
    return parse_claude_session(path)


CODEX_IMAGE_TAG_RE = re.compile(r"^\s*<image\b[^>]*>\s*(?:</image>\s*)?", re.IGNORECASE)
# Typed prompts that carry no work: quitting the TUI, slash commands, and the
# AGENTS.md block that older Codex versions injected as a user message.
CODEX_NOISE_EXACT = ("exit", "quit", "q", "/exit", "/quit", "/clear", "/new", "/status")
CODEX_NOISE_PREFIXES = ("# AGENTS.md instructions", "# Files mentioned by the user")


def _codex_message_text(payload):
    parts = []
    for block in payload.get("content") or []:
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(parts)


def _codex_user_prompt(text):
    """The typed prompt from a Codex user message, or None for injected context.

    Codex injects blocks such as <environment_context>, <recommended_plugins>,
    <user_action> and <turn_aborted> as user messages; those start with a tag
    and are not prompts. Attached images have leading <image ...> wrappers
    (with closing tags in newer rollouts), which are stripped from typed text.
    """
    stripped = text.lstrip()
    while CODEX_IMAGE_TAG_RE.match(stripped):
        stripped = CODEX_IMAGE_TAG_RE.sub("", stripped, count=1)
    if not stripped.strip() or stripped.startswith("<"):
        return None
    prompt = stripped.strip()
    if prompt.lower() in CODEX_NOISE_EXACT or prompt.startswith(CODEX_NOISE_PREFIXES):
        return None
    return prompt


def parse_codex_session(path):
    """Parse a Codex CLI rollout into the same shape as a Claude transcript.

    Scripted runs (``codex exec``) and spawned subagent threads are marked
    ``excluded`` so only interactive sessions reach the ledger.
    """
    prompts = []
    turns = []
    last_assistant = None
    cwd = None
    first_ts = None
    last_ts = None
    session_id = None
    excluded = None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            ts = rec.get("timestamp")
            if isinstance(ts, str) and ts:
                if first_ts is None or ts < first_ts:
                    first_ts = ts
                if last_ts is None or ts > last_ts:
                    last_ts = ts
            rtype = rec.get("type")
            payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
            if rtype == "session_meta":
                session_id = payload.get("id") or payload.get("session_id") or session_id
                cwd = payload.get("cwd") or cwd
                source = payload.get("source")
                thread_source = payload.get("thread_source")
                if source == "exec" or payload.get("originator") == "codex_exec":
                    excluded = "codex exec run"
                elif isinstance(source, dict) or isinstance(thread_source, dict):
                    excluded = "codex subagent thread"
            elif rtype == "turn_context":
                cwd = cwd or payload.get("cwd")
            elif rtype == "response_item" and payload.get("type") == "message":
                role = payload.get("role")
                if role == "user":
                    prompt = _codex_user_prompt(_codex_message_text(payload))
                    if prompt and not is_noise_prompt(prompt):
                        prompts.append(prompt)
                        turns.append({"prompt": prompt, "assistant": None})
                elif role == "assistant" and payload.get("channel") != "analysis":
                    text = _codex_message_text(payload).strip()
                    if text:
                        last_assistant = text
                        if turns:
                            turns[-1]["assistant"] = text
            elif rtype == "event_msg" and payload.get("type") == "task_complete":
                message = payload.get("last_agent_message")
                if isinstance(message, str) and message.strip():
                    last_assistant = message.strip()
                    if turns:
                        turns[-1]["assistant"] = last_assistant
    if not session_id:
        match = CODEX_ROLLOUT_RE.match(os.path.basename(path))
        session_id = match.group(1) if match else os.path.splitext(os.path.basename(path))[0]
    title = re.sub(r"\s+", " ", prompts[0]).strip()[:60] if prompts else "untitled"
    return {
        "tool": "codex",
        "session_id": session_id,
        "path": path,
        "prompts": prompts,
        "turns": turns,
        "last_assistant": last_assistant,
        "title": title,
        "cwd": cwd or os.getcwd(),
        "first_ts": first_ts,
        "last_ts": last_ts,
        "excluded": excluded,
    }


def parse_claude_session(path):
    """Parse a Claude Code transcript into the bits the distiller needs."""
    prompts = []
    turns = []
    last_assistant = None
    ai_title = None
    custom_title = None
    cwd = None
    first_ts = None
    last_ts = None
    session_id = None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            rtype = rec.get("type")
            if rec.get("sessionId") and not session_id:
                session_id = rec["sessionId"]
            ts = rec.get("timestamp")
            if ts:
                if first_ts is None or ts < first_ts:
                    first_ts = ts
                if last_ts is None or ts > last_ts:
                    last_ts = ts
            if rec.get("cwd") and not cwd:
                cwd = rec["cwd"]
            if rtype == "ai-title":
                ai_title = rec.get("aiTitle") or ai_title
            elif rtype == "custom-title":
                custom_title = rec.get("customTitle") or custom_title
            elif rtype == "user":
                if rec.get("isMeta"):
                    continue
                msg = rec.get("message") or {}
                if msg.get("role") != "user":
                    continue
                text = extract_text_from_content(msg.get("content"))
                if text is None or not text.strip() or is_noise_prompt(text):
                    continue
                prompts.append(text.strip())
                turns.append({"prompt": text.strip(), "assistant": None})
            elif rtype == "assistant":
                msg = rec.get("message") or {}
                content = msg.get("content")
                if isinstance(content, list):
                    texts = [
                        b.get("text", "")
                        for b in content
                        if isinstance(b, dict) and b.get("type") == "text" and b.get("text")
                    ]
                    if texts:
                        last_assistant = "\n".join(texts)
                        if turns:
                            turns[-1]["assistant"] = last_assistant
                elif isinstance(content, str) and content.strip():
                    last_assistant = content
                    if turns:
                        turns[-1]["assistant"] = last_assistant
    if not session_id:
        session_id = os.path.splitext(os.path.basename(path))[0]
    title = custom_title or ai_title
    if not title and prompts:
        title = re.sub(r"\s+", " ", prompts[0]).strip()[:60]
    if not title:
        title = "untitled"
    return {
        "tool": "claude",
        "session_id": session_id,
        "path": path,
        "prompts": prompts,
        "turns": turns,
        "last_assistant": last_assistant,
        "title": title,
        "cwd": cwd or os.getcwd(),
        "first_ts": first_ts,
        "last_ts": last_ts,
        "excluded": None,
    }


def _clip_excerpt_text(text, budget):
    """Keep both ends of a text field, including the marker within the cap."""
    if budget <= 0:
        return ""
    if len(text) <= budget:
        return text
    marker = "\n[... excerpt trimmed ...]\n"
    if budget <= len(marker):
        return text[:budget]
    remaining = budget - len(marker)
    head = int(remaining * 0.7)
    tail = remaining - head
    return text[:head] + marker + text[-tail:]


def build_excerpt(info, max_chars):
    metadata = "cwd: %s\nstarted: %s\nlast activity: %s\n\n" % (
        info["cwd"], info["first_ts"] or "unknown", info["last_ts"] or "unknown")
    turns = info.get("turns")
    if turns is None:
        # Accept callers using the older parsed-info shape.
        turns = [{"prompt": p, "assistant": None} for p in info["prompts"]]
        if info.get("last_assistant"):
            if not turns:
                turns.append({"prompt": "", "assistant": None})
            turns[-1]["assistant"] = info["last_assistant"]

    def block(index, prompt, response):
        return "USER PROMPT %d:\n%s\nASSISTANT RESPONSE %d:\n%s\n\n" % (
            index, prompt, index, response)

    excerpt = metadata + "".join(
        block(i, t["prompt"], t["assistant"] or "[No response captured]")
        for i, t in enumerate(turns, 1))
    if len(excerpt) <= max_chars:
        return excerpt
    overhead = len(metadata) + sum(len(block(i, "", "")) for i in range(1, len(turns) + 1))
    if not turns or overhead >= max_chars:
        return _clip_excerpt_text(excerpt, max_chars)
    # Give every turn room for its outcome. Global head/tail truncation loses
    # completed work in the middle whenever the user asks a follow-up question.
    turn_budget = (max_chars - overhead) // len(turns)
    blocks = []
    for i, turn in enumerate(turns, 1):
        prompt = turn["prompt"]
        response = turn["assistant"] or "[No response captured]"
        prompt_budget = min(len(prompt), int(turn_budget * 0.3))
        response_budget = min(len(response), turn_budget - prompt_budget)
        prompt_budget = min(len(prompt), turn_budget - response_budget)
        blocks.append(block(i, _clip_excerpt_text(prompt, prompt_budget),
                            _clip_excerpt_text(response, response_budget)))
    return metadata + "".join(blocks)


# --- distillation ------------------------------------------------------------

def entry_date(info):
    ts = info["last_ts"] or info["first_ts"]
    if ts:
        return ts[:10]
    return datetime.date.today().isoformat()


def run_claude(prompt, cfg, timeout=DISTILL_TIMEOUT_SECONDS):
    """Run the configured distiller on ``prompt`` (stdin). Returns (text, error)."""
    os.makedirs(RUNS_DIR, exist_ok=True)
    if cfg.get("DISTILL_TOOL") == "codex":
        return _run_codex(prompt, cfg, timeout)
    try:
        proc = subprocess.run(
            ["claude", "-p", "--model", cfg["MODEL"]],
            input=prompt,
            capture_output=True,
            text=True,
            cwd=RUNS_DIR,
            timeout=timeout,
        )
    except FileNotFoundError:
        return None, "claude CLI not found on PATH"
    except subprocess.TimeoutExpired:
        return None, "claude -p timed out after %ds" % timeout
    if proc.returncode != 0:
        detail = (proc.stderr.strip() or proc.stdout.strip())[:200]
        return None, "claude exited %d: %s" % (proc.returncode, detail)
    out = proc.stdout.strip()
    if not out:
        return None, "claude returned empty output"
    return out, None


def _run_codex(prompt, cfg, timeout):
    """Distill with ``codex exec``.

    Ephemeral so the run leaves no rollout behind (a rollout would be swept
    as a session next time), read-only sandbox, and the answer is read from
    the last-message file because stdout carries Codex's own progress output.
    """
    fd, out_path = tempfile.mkstemp(dir=RUNS_DIR, prefix=".codex-distill.", suffix=".md")
    os.close(fd)
    argv = [
        "codex", "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only",
        "-c", 'model_reasoning_effort="%s"' % (cfg.get("CODEX_REASONING_EFFORT") or "low"),
        "-o", out_path,
    ]
    if cfg.get("CODEX_MODEL"):
        argv += ["-m", cfg["CODEX_MODEL"]]
    try:
        proc = subprocess.run(
            argv, input=prompt, capture_output=True, text=True, cwd=RUNS_DIR, timeout=timeout,
        )
    except FileNotFoundError:
        return None, "codex CLI not found on PATH"
    except subprocess.TimeoutExpired:
        return None, "codex exec timed out after %ds" % timeout
    finally:
        try:
            with open(out_path, encoding="utf-8") as fh:
                out = fh.read().strip()
        except OSError:
            out = ""
        try:
            os.unlink(out_path)
        except OSError:
            pass
    if proc.returncode != 0:
        detail = (proc.stderr.strip() or proc.stdout.strip())[-200:]
        return None, "codex exited %d: %s" % (proc.returncode, detail)
    if not out:
        return None, "codex returned empty output"
    return out, None


def strip_code_fence(text):
    """Remove one wrapping ``` fence if the model added it despite instructions."""
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t


def distill_session(info, cfg):
    """Return (entry_markdown, error).

    The model produces only the three content lines; the header and Resume
    line are built here from known metadata, so model formatting drift can
    never invalidate an entry. Tolerates a model that returns a full block
    anyway (its heading/Resume lines are discarded).
    """
    excerpt = build_excerpt(info, cfg["MAX_EXCERPT_CHARS"])
    sid = info["session_id"]
    prompt = DISTILL_INSTRUCTIONS.format(excerpt=excerpt)
    out, err = run_claude(prompt, cfg)
    if err:
        return None, err
    out = strip_code_fence(out)
    fields, current = {}, None
    for raw in out.splitlines():
        s = raw.strip()
        low = s.lower()
        if not s or s.startswith("#") or low.startswith(("- resume:", "resume:")):
            continue
        matched = False
        for key in ("Outcome", "Artifacts", "Open threads"):
            for pre in ("- %s:" % key.lower(), "* %s:" % key.lower(), "%s:" % key.lower()):
                if low.startswith(pre):
                    fields[key] = s[len(pre):].strip()
                    current = key
                    matched = True
                    break
            if matched:
                break
        if not matched and current:
            fields[current] = (fields[current] + " " + s.lstrip("-* ").strip()).strip()
    if not fields.get("Outcome"):
        return None, "malformed distill output (%r...)" % out[:80]
    project = os.path.basename(info["cwd"].rstrip(os.sep)) or info["cwd"]
    entry = "\n".join([
        "## %s | %s | %s | %s" % (entry_date(info), project, info["title"], sid[:8]),
        "- Outcome: %s" % fields["Outcome"],
        "- Artifacts: %s" % (fields.get("Artifacts") or "none"),
        "- Open threads: %s" % (fields.get("Open threads") or "none"),
        "- Resume: `%s` (in %s)" % (resume_command(info), info["cwd"]),
    ])
    return entry, None


def resume_command(info):
    if info.get("tool") == "codex":
        return "codex resume %s" % info["session_id"]
    return "claude --resume %s" % info["session_id"]


def append_entry(cfg, entry):
    ledger = cfg["LEDGER_FILE"]
    if os.path.isfile(ledger):
        with open(ledger, encoding="utf-8") as fh:
            content = fh.read()
    else:
        content = ""
    if not content.strip():
        content = LEDGER_HEADER + "\n"
    lines = content.splitlines()
    insert_at = len(lines)
    for i, line in enumerate(lines):
        if line.startswith("# "):
            insert_at = i + 1
            break
    new_lines = lines[:insert_at] + ["", entry.rstrip()] + lines[insert_at:]
    body = "\n".join(new_lines).rstrip() + "\n"
    os.makedirs(os.path.dirname(os.path.abspath(ledger)), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(ledger)), prefix=".ledger.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.replace(tmp, ledger)


def has_new_activity(path, sid, state, min_new_prompts):
    """Decide whether a session needs (re)distilling, based on size/mtime."""
    try:
        st = os.stat(path)
    except OSError:
        return False, None
    prev = state.get(sid) or {}
    if st.st_size <= prev.get("last_size", -1) and st.st_mtime <= prev.get("last_mtime", -1):
        return False, st
    return True, st


def session_is_stale(info, cfg):
    """True when a never-seen session's last activity is past the backfill window."""
    max_age = cfg.get("BACKFILL_MAX_AGE_DAYS") or 0
    ts = info.get("last_ts") or info.get("first_ts")
    if max_age <= 0 or not ts:
        return False
    try:
        last = datetime.date.fromisoformat(ts[:10])
    except ValueError:
        return False
    return (datetime.date.today() - last).days > max_age


def process_one(sid, path, cfg, state, dry_run, force=False):
    """Distill one session and append its entry. Returns status string."""
    if session_is_excluded(path):
        return "excluded"
    fresh, st = has_new_activity(path, sid, state, cfg["MIN_NEW_PROMPTS"])
    if st is None:
        return "unchanged"
    if not fresh and not force:
        return "unchanged"
    info = parse_session(path)
    prev = state.get(sid) or {}
    if info.get("excluded"):
        if not dry_run:
            state[sid] = {"last_size": st.st_size, "last_mtime": st.st_mtime,
                          "excluded": info["excluded"], "prompt_count": 0}
            save_state(state)
        return "excluded"
    if not prev and not force and session_is_stale(info, cfg):
        if not dry_run:
            state[sid] = {"last_size": st.st_size, "last_mtime": st.st_mtime,
                          "stale": True, "prompt_count": len(info["prompts"])}
            save_state(state)
        return "stale"
    new_prompts = len(info["prompts"]) - prev.get("prompt_count", 0)
    if len(info["prompts"]) == 0:
        return "no-prompts"
    if not force and new_prompts < cfg["MIN_NEW_PROMPTS"]:
        return "below-min-prompts"
    if dry_run:
        print(
            "WOULD DISTILL %s %s | %s | %s | %d prompt(s), %d new"
            % (info.get("tool", "claude"), sid[:8], entry_date(info), info["title"][:60],
               len(info["prompts"]), new_prompts)
        )
        return "dry-run"
    entry, err = distill_session(info, cfg)
    if err:
        log("skip %s: %s" % (sid, err))
        return "failed"
    append_entry(cfg, entry)
    state[sid] = {
        "last_size": st.st_size,
        "last_mtime": st.st_mtime,
        "entries": prev.get("entries", 0) + 1,
        "prompt_count": len(info["prompts"]),
    }
    save_state(state)
    log("distilled %s -> %s" % (sid, cfg["LEDGER_FILE"]))
    return "distilled"


POST_SWEEP_TIMEOUT_SECONDS = 300


def run_post_sweep(cfg, what):
    """Run POST_SWEEP_CMD, if configured, after the ledger changed. Never raises."""
    cmd = (cfg.get("POST_SWEEP_CMD") or "").strip()
    if not cmd:
        return
    try:
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=POST_SWEEP_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        log("post-sweep command timed out after %ds (%s)" % (POST_SWEEP_TIMEOUT_SECONDS, what))
        return
    except OSError as exc:
        log("post-sweep command could not run (%s): %s" % (what, exc))
        return
    tail = (proc.stdout + proc.stderr).strip().splitlines()
    log("post-sweep command exit %d after %s%s"
        % (proc.returncode, what, (": " + tail[-1][:200]) if tail else ""))


# --- modes ---------------------------------------------------------------

def mode_sweep(cfg, dry_run):
    state = load_state()
    counts = {}
    for sid, path in find_sessions(cfg):
        status = process_one(sid, path, cfg, state, dry_run)
        counts[status] = counts.get(status, 0) + 1
    summary = ", ".join("%s=%d" % kv for kv in sorted(counts.items())) or "no sessions found"
    print("sweep: %s" % summary)
    if not dry_run:
        log("sweep done: %s" % summary)
        if counts.get("distilled"):
            run_post_sweep(cfg, "sweep")
        if counts.get("failed") and not counts.get("distilled"):
            # every distill attempt failed: exit non-zero so launchd records it
            die("sweep: all %d distill attempt(s) failed, see %s" % (counts["failed"], LOG_FILE))


def mode_seed(cfg, days, dry_run):
    state = load_state()
    cutoff = datetime.datetime.now().timestamp() - days * 86400
    candidates = []
    for sid, path in find_sessions(cfg):
        try:
            st = os.stat(path)
        except OSError:
            continue
        if st.st_mtime >= cutoff:
            candidates.append((st.st_mtime, sid, path))
    candidates.sort()  # oldest first so newest entries land on top
    counts = {}
    for _, sid, path in candidates:
        status = process_one(sid, path, cfg, state, dry_run, force=True)
        counts[status] = counts.get(status, 0) + 1
    summary = ", ".join("%s=%d" % kv for kv in sorted(counts.items())) or "no sessions in window"
    print("seed (last %d days): %s" % (days, summary))
    if not dry_run:
        log("seed done: %s" % summary)


def mode_session(cfg, arg, dry_run):
    path = resolve_session_arg(cfg, arg)
    name = os.path.basename(path)
    match = CODEX_ROLLOUT_RE.match(name)
    sid = match.group(1) if match else os.path.splitext(name)[0]
    state = load_state()
    status = process_one(sid, path, cfg, state, dry_run)
    print("session %s: %s" % (sid[:8], status))
    if status == "failed":
        sys.exit(1)


def split_entries(content):
    """The ledger's entry blocks (each starting with a '## ' heading), in file order."""
    return [b for b in re.split(r"(?m)^(?=## )", content) if b.startswith("## ")]


def restore_lost_entries(content, out, lost):
    """Append, verbatim, the original entries whose Resume ids the rewrite dropped.

    The consolidation model reliably loses a few entries when it rewrites a
    large ledger. Rather than discard the whole pass, keep its output and put
    the dropped entries back from the original file so no resume line is ever
    lost. Restored entries land at the bottom, out of date order.
    """
    restored = []
    covered = set()
    for block in split_entries(content):
        ids = set(RESUME_ID_RE.findall(block))
        if ids & lost and not ids <= covered:
            restored.append(block.rstrip() + "\n")
            covered |= ids
    if not restored:
        return out
    return out.rstrip() + "\n\n" + "\n".join(restored)


def mode_dream(cfg, dry_run):
    ledger = cfg["LEDGER_FILE"]
    if not os.path.isfile(ledger):
        print("dream: no ledger file at %s, nothing to do" % ledger)
        return
    with open(ledger, encoding="utf-8") as fh:
        content = fh.read()
    if not content.strip():
        print("dream: ledger is empty, nothing to do")
        return
    prompt = DREAM_INSTRUCTIONS.format(header=LEDGER_HEADER, ledger=content)
    if dry_run:
        entries = content.count("\n## ") + (1 if content.startswith("## ") else 0)
        print("dream dry-run: would consolidate %d entries in %s via %s" % (entries, ledger, cfg["MODEL"]))
        return
    out, err = run_claude(prompt, cfg, timeout=DREAM_TIMEOUT_SECONDS)
    if err:
        log("dream failed: %s" % err)
        die("dream failed: %s" % err)
    out = strip_code_fence(out)
    if not out.startswith((LEDGER_HEADER,) + LEGACY_LEDGER_HEADERS) or "## " not in out:
        log("dream produced malformed output, ledger left untouched")
        die("dream output malformed, ledger left untouched")
    if "--resume" in content and "--resume" not in out:
        log("dream output lost all Resume lines, ledger left untouched")
        die("dream output lost Resume lines, ledger left untouched")
    lost = set(RESUME_ID_RE.findall(content)) - set(RESUME_ID_RE.findall(out))
    if lost:
        short = ", ".join(sorted(i[:8] for i in lost))
        out = restore_lost_entries(content, out, lost)
        still = lost - set(RESUME_ID_RE.findall(out))
        if still:
            log("dream output lost %d Resume id(s) that could not be restored (%s), ledger left untouched"
                % (len(still), ", ".join(sorted(i[:8] for i in still))))
            die("dream output lost %d Resume id(s): %s; ledger left untouched" % (len(still), short))
        log("dream output dropped %d entr%s (%s); restored verbatim at the bottom of the ledger"
            % (len(lost), "y" if len(lost) == 1 else "ies", short))
    shutil.copy2(ledger, ledger + ".bak")
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(ledger)), prefix=".ledger.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(out.rstrip() + "\n")
    os.replace(tmp, ledger)
    log("dream rewrote %s (backup at %s.bak)" % (ledger, ledger))
    run_post_sweep(cfg, "dream")
    print("dream: ledger consolidated, backup at %s.bak" % ledger)
    print("review the '## Promote to memory' section at the top of the ledger")


def main():
    ap = argparse.ArgumentParser(description="Claude Code session ledger")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--sweep", action="store_true")
    group.add_argument("--session", metavar="TRANSCRIPT_OR_ID")
    group.add_argument("--seed", action="store_true")
    group.add_argument("--dream", action="store_true")
    ap.add_argument("--days", type=int, default=14, help="window for --seed (default 14)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load_config()

    if args.dry_run:
        locked = True  # no writes happen, no lock needed
    else:
        locked = acquire_lock()
    if not locked:
        # another instance (hook vs cron) is running; exit quietly
        sys.exit(0)
    try:
        if args.sweep:
            mode_sweep(cfg, args.dry_run)
        elif args.session:
            mode_session(cfg, args.session, args.dry_run)
        elif args.seed:
            mode_seed(cfg, args.days, args.dry_run)
        elif args.dream:
            mode_dream(cfg, args.dry_run)
    finally:
        if not args.dry_run:
            release_lock()


if __name__ == "__main__":
    main()
