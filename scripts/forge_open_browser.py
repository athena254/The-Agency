"""Agency opens the landing page in the user's browser (no hand typing).

Agency finds the built page via Forge smoke, starts a local server,
and opens the default browser. Run:
  .venv/Scripts/python.exe scripts/forge_open_browser.py [--port 8000]
"""

from __future__ import annotations

import socket
import subprocess
import sys
import webbrowser
from pathlib import Path

from agency.autonomy.proof import ProofBundle
from agency.forge.smoke import smoke_app

proof = ProofBundle()
root = Path(__file__).resolve().parents[1] / "agency-landing"
if not (root / "index.html").is_file():
    raise SystemExit("landing page missing; run forge_build_landing.py first")

# Prove it still runs
smoke = smoke_app(root)
proof.append("forge", "preopen_smoke", {"gate": smoke.gate, "status": smoke.status_code})
print(f"Agency pre-open smoke: {smoke.gate} (port {smoke.port} status {smoke.status_code})")
if smoke.gate != "PASS":
    raise SystemExit("smoke failed; not opening browser (fail-closed)")

# Start persistent server for the user
import threading
import http.server
import socketserver

port = 8000
handler = http.server.SimpleHTTPRequestHandler
httpd = socketserver.TCPServer(("127.0.0.1", port), handler)
# Serve from landing dir
import os
os.chdir(root)
thread = threading.Thread(target=httpd.serve_forever, daemon=True)
thread.start()
url = f"http://127.0.0.1:{port}/index.html"
proof.append("agency", "open_browser", {"url": url})
print(f"Agency opening: {url}")
webbrowser.open(url)
print(f"Proof: {len(proof.events)} events head={proof.head[:12]}...")
print("Browser opened. Press Ctrl+C in server to stop (server is for this session).")
try:
    import time
    while True:
        time.sleep(10)
except KeyboardInterrupt:
    httpd.shutdown()
