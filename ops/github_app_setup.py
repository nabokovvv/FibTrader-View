"""Create the feedback GitHub App with the manifest flow (two clicks in the browser, no copy-pasting keys).

    venv/bin/python ops/github_app_setup.py            # opens the browser: "Create GitHub App", then "Install"
    venv/bin/python ops/github_app_setup.py --installation   # after installing: find the installation id

Saves the App id, slug and private key into .secrets/ (gitignored): github_app.env + github_app.pem.
The App gets one permission: Issues read & write; no webhooks.
"""
from __future__ import annotations

import http.server
import json
import secrets
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / ".secrets"
PORT = 8739
STATE = secrets.token_urlsafe(16)
MANIFEST = {
    "name": "FibTrader View Feedback",
    "url": "https://charts.fibtrader.dev",
    "hook_attributes": {"url": "https://charts.fibtrader.dev/", "active": False},
    "redirect_url": f"http://127.0.0.1:{PORT}/callback",
    "public": False,
    "default_permissions": {"issues": "write"},
    "default_events": [],
}
done = threading.Event()


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, html: str) -> None:
        body = html.encode()
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        url = urllib.parse.urlparse(self.path)
        if url.path == "/":
            m = json.dumps(MANIFEST).replace('"', "&quot;")
            self._send(f"""<form id=f method=post action="https://github.com/settings/apps/new?state={STATE}">
<input type=hidden name=manifest value="{m}"></form><p>Opening GitHub…</p><script>f.submit()</script>""")
            return
        if url.path == "/callback":
            q = urllib.parse.parse_qs(url.query)
            if q.get("state", [""])[0] != STATE or "code" not in q:
                self._send("<p>Wrong state, start again.</p>")
                return
            req = urllib.request.Request(f"https://api.github.com/app-manifests/{q['code'][0]}/conversions", method="POST",
                                         headers={"Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                app = json.load(r)
            SECRETS.mkdir(exist_ok=True)
            pem = SECRETS / "github_app.pem"
            pem.write_text(app["pem"])
            pem.chmod(0o600)
            env = SECRETS / "github_app.env"
            env.write_text(f"GITHUB_APP_ID={app['id']}\nGITHUB_APP_SLUG={app['slug']}\n")
            env.chmod(0o600)
            install = f"https://github.com/apps/{app['slug']}/installations/new"
            self._send(f"""<p>App created: <b>{app['slug']}</b>. Now install it on the FibTrader-View repository only:</p>
<p><a href="{install}">{install}</a></p><script>location.href={json.dumps(install)}</script>""")
            print(f"created app {app['slug']} (id {app['id']}); install: {install}")
            done.set()


def find_installation() -> None:
    import jwt  # PyJWT, already a backend dependency
    env = dict(line.split("=", 1) for line in (SECRETS / "github_app.env").read_text().split())
    now = int(time.time())
    token = jwt.encode({"iat": now - 60, "exp": now + 540, "iss": env["GITHUB_APP_ID"]},
                       (SECRETS / "github_app.pem").read_text(), algorithm="RS256")
    req = urllib.request.Request("https://api.github.com/app/installations",
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        inst = json.load(r)
    for i in inst:
        print(i["id"], i["account"]["login"], i.get("repository_selection"))
    if len(inst) == 1:
        with (SECRETS / "github_app.env").open("a") as f:
            f.write(f"GITHUB_APP_INSTALLATION_ID={inst[0]['id']}\n")
        print("saved GITHUB_APP_INSTALLATION_ID")


if __name__ == "__main__":
    if "--installation" in sys.argv:
        find_installation()
        sys.exit(0)
    srv = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    webbrowser.open(f"http://127.0.0.1:{PORT}/")
    print(f"waiting for GitHub (http://127.0.0.1:{PORT}/) …")
    done.wait(timeout=3600)
    time.sleep(1)
    srv.shutdown()
