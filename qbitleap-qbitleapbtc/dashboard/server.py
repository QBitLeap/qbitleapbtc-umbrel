#!/usr/bin/env python3
import base64
import html
import json
import os
import re
import socket
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs
from urllib.request import Request, urlopen

PORT = int(os.environ.get("DASHBOARD_PORT", "8080"))
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config"))
QBT_FILE = CONFIG_DIR / "qbt-payout-address.txt"
BTC_FILE = CONFIG_DIR / "btc-payout-address.txt"
CKPOOL_LOG_DIR = Path(os.environ.get("CKPOOL_LOG_DIR", "/var/log/ckpool"))

QBIT_RPC_HOST = os.environ.get("QBIT_RPC_HOST", "qbitd")
QBIT_RPC_PORT = int(os.environ.get("QBIT_RPC_PORT", "8352"))
QBIT_RPC_USER = os.environ.get("QBIT_RPC_USER", "qbitrpc")
QBIT_RPC_PASSWORD = os.environ.get("QBIT_RPC_PASSWORD", "")
CKPOOL_HOST = os.environ.get("CKPOOL_HOST", "ckpool")
CKPOOL_PORT = int(os.environ.get("CKPOOL_PORT", "3333"))

ADDRESS_RE = re.compile(r"^[A-Za-z0-9]{14,120}$")


def read_text(path):
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def atomic_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def qbit_rpc(method, params=None):
    payload = json.dumps({
        "jsonrpc": "1.0",
        "id": "qbitleap-dashboard",
        "method": method,
        "params": params or [],
    }).encode("utf-8")
    auth = base64.b64encode(
        f"{QBIT_RPC_USER}:{QBIT_RPC_PASSWORD}".encode("utf-8")
    ).decode("ascii")
    req = Request(
        f"http://{QBIT_RPC_HOST}:{QBIT_RPC_PORT}",
        data=payload,
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/json",
        },
    )
    with urlopen(req, timeout=3) as response:
        body = json.load(response)
    if body.get("error"):
        raise RuntimeError(str(body["error"]))
    return body.get("result")


def qbit_running():
    try:
        qbit_rpc("getblockchaininfo")
        return True
    except Exception:
        return False


def ckpool_running():
    try:
        with socket.create_connection((CKPOOL_HOST, CKPOOL_PORT), timeout=2):
            return True
    except OSError:
        return False


def qbit_blocks_found():
    # CKPool records solved-block events in its persistent logs.
    patterns = (
        re.compile(r"\bsolved\b.*\bblock\b", re.I),
        re.compile(r"\bblock\b.*\bsolved\b", re.I),
        re.compile(r"\bconfirmed\b.*\bblock\b", re.I),
    )
    seen = set()
    try:
        files = [p for p in CKPOOL_LOG_DIR.rglob("*") if p.is_file()]
    except OSError:
        return 0
    for path in files:
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    if any(pattern.search(line) for pattern in patterns):
                        seen.add(line.strip())
        except OSError:
            continue
    return len(seen)


def status_label(up):
    cls = "up" if up else "down"
    text = "Running" if up else "Not Running"
    return f'<span class="status {cls}"><span class="dot"></span>{text}</span>'


def render(message="", error=""):
    qbt = html.escape(read_text(QBT_FILE), quote=True)
    btc = html.escape(read_text(BTC_FILE), quote=True)
    qbit_up = qbit_running()
    ckpool_up = ckpool_running()
    notice = ""
    if message:
        notice = f'<div class="notice success">{html.escape(message)}</div>'
    elif error:
        notice = f'<div class="notice error">{html.escape(error)}</div>'

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>QBitLeap Solo Miner</title>
<style>
:root {{
  color-scheme: dark;
  --bg:#0c1017; --panel:#151b25; --line:#283142; --text:#f5f7fa;
  --muted:#98a2b3; --accent:#7c9cff; --good:#36c275; --bad:#f05d68;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font-family:system-ui,-apple-system,sans-serif; }}
main {{ width:min(720px,calc(100% - 32px)); margin:40px auto; }}
h1 {{ font-size:28px; margin:0 0 28px; }}
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:22px; margin-bottom:18px; }}
label {{ display:block; font-weight:600; margin:0 0 8px; }}
input {{ width:100%; border:1px solid var(--line); border-radius:9px; padding:12px; margin-bottom:18px; background:#0e141e; color:var(--text); font:inherit; }}
button {{ border:0; border-radius:9px; padding:11px 20px; background:var(--accent); color:#08101f; font:inherit; font-weight:700; cursor:pointer; }}
.row {{ display:flex; justify-content:space-between; align-items:center; gap:20px; padding:11px 0; }}
.row + .row {{ border-top:1px solid var(--line); }}
.status {{ display:inline-flex; align-items:center; gap:8px; font-weight:600; }}
.dot {{ width:10px; height:10px; border-radius:50%; background:currentColor; }}
.up {{ color:var(--good); }} .down {{ color:var(--bad); }}
.metric {{ font-size:28px; font-weight:750; }}
.muted {{ color:var(--muted); font-size:13px; margin-top:12px; }}
.notice {{ border-radius:9px; padding:11px 13px; margin-bottom:18px; }}
.success {{ background:#123522; color:#8ce7b2; }} .error {{ background:#3a181c; color:#ff9ca5; }}
</style>
</head>
<body>
<main>
<h1>QBitLeap Solo Miner</h1>
{notice}
<section class="card">
<form method="post" action="/save">
<label for="qbt">QBT Payout Address</label>
<input id="qbt" name="qbt_payout" value="{qbt}" autocomplete="off" required>
<label for="btc">BTC Payout Address</label>
<input id="btc" name="btc_payout" value="{btc}" autocomplete="off" required>
<button type="submit">Save</button>
</form>
<p class="muted">The QBT address is read by CKPool when CKPool starts. The BTC address is stored in the same persistent mining configuration.</p>
</section>
<section class="card">
<div class="row"><span>Qbit Core</span>{status_label(qbit_up)}</div>
<div class="row"><span>CKPool</span>{status_label(ckpool_up)}</div>
</section>
<section class="card">
<div class="row"><span>Qbit Blocks Found</span><span class="metric">{qbit_blocks_found()}</span></div>
<div class="row"><span>Bitcoin Blocks Found</span><span class="metric">0</span></div>
</section>
</main>
</body>
</html>""".encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/":
            self.send_error(404)
            return
        body = render()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/save":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 8192:
                raise ValueError("Invalid request size.")
            form = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
            qbt = form.get("qbt_payout", [""])[0].strip()
            btc = form.get("btc_payout", [""])[0].strip()
            if not ADDRESS_RE.fullmatch(qbt):
                raise ValueError("Enter a valid QBT payout address.")
            if not ADDRESS_RE.fullmatch(btc):
                raise ValueError("Enter a valid BTC payout address.")
            # Validate the QBT address against the running Qbit node.
            result = qbit_rpc("validateaddress", [qbt])
            if not isinstance(result, dict) or not result.get("isvalid"):
                raise ValueError("The QBT payout address is not valid for this Qbit network.")
            atomic_write(QBT_FILE, qbt)
            atomic_write(BTC_FILE, btc)
            body = render(message="Mining payout addresses saved.")
            code = 200
        except ValueError as exc:
            body = render(error=str(exc))
            code = 400
        except Exception:
            body = render(error="The payout addresses could not be saved.")
            code = 500
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}", flush=True)


if __name__ == "__main__":
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    print(f"QBitLeap dashboard listening on 0.0.0.0:{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
