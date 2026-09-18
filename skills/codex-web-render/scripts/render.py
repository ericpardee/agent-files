#!/usr/bin/env python3
"""Fast, windowless render of a JS-heavy / bot-protected URL.

Drives a real system browser (Brave, then Chrome, then Chromium) in
`--headless=new` mode with `--disable-blink-features=AutomationControlled`,
which together clear Cloudflare's non-interactive "checking your browser"
challenge without ever opening a visible window. Extracts the page's visible
text (document.body.innerText) and writes it to the output path.

Usage:
    render.py <url> <output_file>

Exit codes:
    0  success: real page content written to <output_file>
    3  soft-fail: still blocked by a challenge / no usable content (caller
       should fall back to the heavier Codex renderer)
    2  bad usage
    4  no usable browser engine found / Playwright missing
"""
import os
import re
import sys
import time

URL = sys.argv[1] if len(sys.argv) > 1 else ""
OUT = sys.argv[2] if len(sys.argv) > 2 else ""
if not URL or not OUT:
    sys.stderr.write("Usage: render.py <url> <output_file>\n")
    sys.exit(2)

# First existing browser binary wins. Real browsers clear anti-bot checks that
# Playwright's bundled headless Chromium does not.
BROWSER_CANDIDATES = [
    "/Applications/Brave Origin.app/Contents/MacOS/Brave Origin",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]
EXECUTABLE = next((p for p in BROWSER_CANDIDATES if os.path.exists(p)), None)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36")
CHALLENGE = re.compile(
    r"(just a moment|verifying you are human|performing security verification|"
    r"checking your browser|enable javascript to run this app|needs to review the security)",
    re.I,
)
PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(OUT)) or ".", "_render_profile")

try:
    from playwright.sync_api import sync_playwright
except Exception as exc:  # playwright not installed
    sys.stderr.write(f"playwright import failed: {exc}\n")
    sys.exit(4)

args = [
    "--headless=new",
    "--disable-blink-features=AutomationControlled",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
]

text = ""
try:
    with sync_playwright() as p:
        launch_kwargs = dict(
            user_data_dir=PROFILE_DIR,
            headless=True,
            args=args,
            user_agent=UA,
            viewport={"width": 1280, "height": 900},
        )
        if EXECUTABLE:
            launch_kwargs["executable_path"] = EXECUTABLE
        ctx = p.chromium.launch_persistent_context(**launch_kwargs)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=45000)
        deadline = time.time() + 40
        while time.time() < deadline:
            text = page.evaluate("document.body.innerText") or ""
            if len(text) > 300 and not CHALLENGE.search(text):
                break
            time.sleep(2)
        ctx.close()
except Exception as exc:
    sys.stderr.write(f"render error: {exc}\n")
    sys.exit(3)

ok = len(text) > 300 and not CHALLENGE.search(text)
with open(OUT, "w") as fh:
    fh.write(text if ok else f"RENDER_ERROR: still blocked or empty ({len(text)} chars)\n")

elapsed_note = f"engine={'system:'+os.path.basename(EXECUTABLE) if EXECUTABLE else 'bundled-chromium'} chars={len(text)}"
sys.stderr.write(elapsed_note + "\n")
sys.exit(0 if ok else 3)
