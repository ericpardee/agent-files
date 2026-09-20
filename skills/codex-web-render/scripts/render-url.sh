#!/usr/bin/env bash
#
# render-url.sh: render a JavaScript-heavy or bot-protected URL by delegating to
# the Codex CLI, which drives a real (headed) browser and extracts the page's
# visible text. Prints the rendered text to stdout.
#
# Usage:
#   render-url.sh <url> [output_file]
#
# If output_file is omitted, the text is printed to stdout only.
#
# Why Codex with danger-full-access: rendering requires network access (to load
# the page and, on first run, to install a browser engine). Codex's default
# sandbox blocks the network. The safer alternative is:
#     --sandbox workspace-write
# combined with this in ~/.codex/config.toml:
#     [sandbox_workspace_write]
#     network_access = true
# Swap the flag below if you prefer that. With danger-full-access the run is
# fully unattended, which is what this fallback is for.
#
# The Codex fallback is opt-in. It runs only when RENDER_ALLOW_CODEX=1 is set;
# otherwise a failed fast path exits 3 with a message instead of handing a
# full-access agent a headed browser.

set -euo pipefail

URL="${1:-}"
if [[ -z "$URL" ]]; then
  echo "Usage: render-url.sh <url> [output_file]" >&2
  exit 2
fi

if ! command -v codex >/dev/null 2>&1; then
  echo "ERROR: 'codex' CLI not found on PATH." >&2
  echo "Install it with:  npm i -g @openai/codex   then:  codex login" >&2
  exit 127
fi

WORKDIR="$(mktemp -d)"
cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT

OUT="${2:-$WORKDIR/rendered.txt}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# FAST PATH: drive a real system browser (Brave/Chrome) headless via Playwright.
# ~5-10s, no visible window, and clears Cloudflare's non-interactive challenge.
# Only fall back to the heavier Codex agent if this fails (exit 3 = soft-fail).
# Set RENDER_NO_FAST=1 to skip straight to Codex.
# ---------------------------------------------------------------------------
if [[ "${RENDER_NO_FAST:-0}" != "1" ]] && command -v uv >/dev/null 2>&1; then
  if uv run --quiet --with playwright python "$SCRIPT_DIR/render.py" "$URL" "$OUT" \
        2>"$WORKDIR/fast.log"; then
    if [[ -s "$OUT" ]] && ! head -n 1 "$OUT" | grep -q '^RENDER_ERROR:'; then
      cat "$OUT"
      exit 0
    fi
  fi
  echo "Fast render path did not succeed." >&2
  tail -n 5 "$WORKDIR/fast.log" >&2 || true
fi

if [[ "${RENDER_ALLOW_CODEX:-0}" != "1" ]]; then
  echo "ERROR: the Codex fallback is disabled. It runs codex with" >&2
  echo "--dangerously-bypass-approvals-and-sandbox and drives a headed browser." >&2
  echo "Re-run with RENDER_ALLOW_CODEX=1 to allow it." >&2
  exit 3
fi
echo "Falling back to Codex (RENDER_ALLOW_CODEX=1)..." >&2

# Build the instruction for Codex. The single deliverable is the output file, so
# we can ignore Codex's own stdout chatter and just read the file afterwards.
PROMPT="Render the page at this URL with a real browser and extract its visible text.

URL: ${URL}

Requirements:
- Use a HEADED (NOT headless) Chromium-based browser so bot-protection
  challenges (Cloudflare 'Just a moment' / 'Performing security verification',
  etc.) auto-solve. Headless Chromium gets blocked by these challenges.
- Prefer Playwright. If a real system browser is installed, launch THAT rather
  than bundled Chromium, because a real browser binary clears anti-bot checks far
  more reliably. On macOS try, in order: Google Chrome (Playwright
  channel: 'chrome'), then Brave Origin via executablePath
  '/Applications/Brave Origin.app/Contents/MacOS/Brave Origin'. Only fall back
  to bundled Chromium (install with 'npx --yes playwright@latest install chromium')
  if no system browser launches.
- Set a realistic desktop User-Agent and a 1280x800 (or larger) viewport.
- Navigate to the URL. If the page shows an interstitial bot/security challenge
  (body text contains 'Just a moment', 'Verifying you are human', 'Performing
  security verification', 'Checking your browser', or only a Cloudflare Ray ID),
  POLL: wait and re-read document.body.innerText every ~3s for up to 60s total,
  until the challenge text is gone and real page content has appeared. Do NOT
  give up after the first read.
- Once past any challenge, allow a few more seconds for single-page-app
  hydration, then extract document.body.innerText (human-visible text), NOT HTML.
- Write ONLY that extracted text to this exact file path: ${OUT}
- Do not print explanations to stdout. The one and only deliverable is the file
  ${OUT}. If after 60s you still cannot get past a challenge or render content,
  write a line beginning with 'RENDER_ERROR:' and a short reason to ${OUT}."

if ! codex exec \
      --skip-git-repo-check \
      --cd "$WORKDIR" \
      --dangerously-bypass-approvals-and-sandbox \
      "$PROMPT" >"$WORKDIR/codex.stdout" 2>"$WORKDIR/codex.log"; then
  echo "ERROR: 'codex exec' failed. Last log lines:" >&2
  tail -n 40 "$WORKDIR/codex.log" >&2
  exit 1
fi

if [[ ! -s "$OUT" ]]; then
  echo "ERROR: Codex did not produce rendered text at $OUT." >&2
  echo "Last log lines:" >&2
  tail -n 40 "$WORKDIR/codex.log" >&2
  exit 1
fi

if head -n 1 "$OUT" | grep -q '^RENDER_ERROR:'; then
  echo "ERROR: Codex could not render the page:" >&2
  cat "$OUT" >&2
  exit 1
fi

cat "$OUT"
