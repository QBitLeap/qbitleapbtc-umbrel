#!/usr/bin/env python3
import base64
import html
import json
import os
import re
import socket
import tempfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs
from urllib.request import Request, urlopen

PORT = int(os.environ.get("DASHBOARD_PORT", "8080"))
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config"))
QBT_FILE = CONFIG_DIR / "qbt-payout-address.txt"
BTC_FILE = CONFIG_DIR / "btc-payout-address.txt"
TELEMETRY_FILE = Path(os.environ.get("TELEMETRY_FILE", "/telemetry/telemetry.json"))

QBIT_RPC_HOST = os.environ.get("QBIT_RPC_HOST", "qbitd")
QBIT_RPC_PORT = int(os.environ.get("QBIT_RPC_PORT", "8352"))
QBIT_RPC_USER = os.environ.get("QBIT_RPC_USER", "qbitrpc")
QBIT_RPC_PASSWORD = os.environ.get("QBIT_RPC_PASSWORD", "")
AUXPOW_HOST = os.environ.get("AUXPOW_HOST", "auxpow")
AUXPOW_PORT = int(os.environ.get("AUXPOW_PORT", "3335"))

BITCOIN_RPC_HOST = os.environ.get("BITCOIN_RPC_HOST", "")
BITCOIN_RPC_PORT = int(os.environ.get("BITCOIN_RPC_PORT", "8332"))
BITCOIN_RPC_USER = os.environ.get("BITCOIN_RPC_USER", "")
BITCOIN_RPC_PASSWORD = os.environ.get("BITCOIN_RPC_PASSWORD", "")

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


def rpc_call(host, port, user, password, method, params=None):
    if not host or not user or not password:
        raise RuntimeError("RPC connection is not configured")
    payload = json.dumps({
        "jsonrpc": "1.0",
        "id": "qbitleap-dashboard",
        "method": method,
        "params": params or [],
    }).encode("utf-8")
    auth = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    req = Request(
        f"http://{host}:{port}",
        data=payload,
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/json",
        },
    )
    with urlopen(req, timeout=4) as response:
        body = json.load(response)
    if body.get("error"):
        raise RuntimeError(str(body["error"]))
    return body.get("result")


def qbit_rpc(method, params=None):
    return rpc_call(
        QBIT_RPC_HOST,
        QBIT_RPC_PORT,
        QBIT_RPC_USER,
        QBIT_RPC_PASSWORD,
        method,
        params,
    )


def bitcoin_rpc(method, params=None):
    return rpc_call(
        BITCOIN_RPC_HOST,
        BITCOIN_RPC_PORT,
        BITCOIN_RPC_USER,
        BITCOIN_RPC_PASSWORD,
        method,
        params,
    )


def chain_status(rpc):
    try:
        info = rpc("getblockchaininfo")
        if not isinstance(info, dict):
            raise RuntimeError("Invalid blockchain status response")
        block_height = int(info.get("blocks", 0))
        return True, block_height
    except Exception:
        return False, None


def auxpow_connected():
    try:
        with socket.create_connection((AUXPOW_HOST, AUXPOW_PORT), timeout=2):
            return True
    except OSError:
        return False



def read_telemetry():
    try:
        data = json.loads(TELEMETRY_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("invalid telemetry")
        if int(data.get("updated_at", 0)) < int(datetime.now().timestamp()) - 15:
            return None
        return data
    except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError):
        return None


def format_hashrate(value):
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return "—"
    units = ["H/s", "kH/s", "MH/s", "GH/s", "TH/s", "PH/s", "EH/s"]
    unit = units[0]
    for unit in units:
        if abs(rate) < 1000 or unit == units[-1]:
            break
        rate /= 1000
    return f"{rate:.2f} {unit}"


def format_number(value, decimals=2):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number == 0:
        return "0"
    if number >= 1000:
        return f"{number:,.0f}"
    return f"{number:.{decimals}f}"

def state_badge(ok, yes_text, no_text):
    cls = "up" if ok else "down"
    text = yes_text if ok else no_text
    icon = "✅" if ok else "❌"
    return f'<span class="state {cls}">{icon} {html.escape(text)}</span>'


def service_row(name, active, status=""):
    dot_class = "up" if active else "down"
    status_html = f'<span class="service-status">{html.escape(status)}</span>' if status else ""
    return (
        '<div class="service-row">'
        '<span class="service-line">'
        f'<span class="service-dot {dot_class}"></span>'
        f'<span>{html.escape(name)}</span>'
        f'{status_html}'
        '</span>'
        '</div>'
    )


def render(headers, message="", error=""):
    qbt = html.escape(read_text(QBT_FILE), quote=True)
    btc = html.escape(read_text(BTC_FILE), quote=True)
    qbit_up, qbit_height = chain_status(qbit_rpc)
    bitcoin_up, bitcoin_height = chain_status(bitcoin_rpc)
    auxpow_up = auxpow_connected()
    telemetry = read_telemetry()

    notice = ""
    if message:
        notice = f'<div class="notice success">{html.escape(message)}</div>'
    elif error:
        notice = f'<div class="notice error">{html.escape(error)}</div>'

    updated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="300">
<title>QBitLeap BTC</title>
<style>
:root {{
  color-scheme: dark;
  --bg:#0c1017; --panel:#151b25; --line:#283142; --text:#f5f7fa;
  --muted:#98a2b3; --accent:#7c9cff; --good:#36c275; --bad:#f05d68;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font-family:system-ui,-apple-system,sans-serif; }}
main {{ width:min(760px,calc(100% - 32px)); margin:40px auto; }}
.header {{ display:flex; align-items:center; justify-content:space-between; gap:16px; margin-bottom:24px; }}
h1 {{ font-size:28px; margin:0; }}
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:22px; margin-bottom:18px; }}
h2 {{ margin:0; font-size:17px; }}
details.card {{ padding:0; }}
summary {{ position:relative; display:flex; align-items:center; justify-content:center; gap:12px; padding:22px; cursor:pointer; list-style:none; user-select:none; text-align:center; }}
summary::-webkit-details-marker {{ display:none; }}
summary::after {{ content:"▸"; position:absolute; right:22px; color:var(--muted); font-size:18px; transition:transform .15s ease; }}
details[open] > summary::after {{ transform:rotate(90deg); }}
.card-body {{ padding:0 22px 22px; }}
label {{ display:block; font-weight:600; margin:0 0 8px; }}
input {{ width:100%; border:1px solid var(--line); border-radius:9px; padding:12px; margin-bottom:18px; background:#0e141e; color:var(--text); font:inherit; }}
button, .refresh {{ border:0; border-radius:9px; padding:10px 16px; background:var(--accent); color:#08101f; font:inherit; font-weight:700; cursor:pointer; text-decoration:none; }}
.service-row {{ display:flex; justify-content:center; align-items:center; padding:12px 0; text-align:center; }}
.metric-row {{ display:flex; justify-content:space-between; align-items:center; gap:18px; padding:12px 0; }}
.service-row + .service-row, .metric-row + .metric-row {{ border-top:1px solid var(--line); }}
.service-line {{ display:inline-flex; align-items:center; justify-content:center; gap:10px; font-weight:650; text-align:center; }}
.service-dot {{ width:12px; height:12px; border-radius:3px; background:currentColor; flex:0 0 auto; }}
.service-status {{ font-weight:600; }}
.state {{ font-weight:600; white-space:nowrap; }}
.up {{ color:var(--good); }} .down {{ color:var(--bad); }}
.metric-value {{ font-weight:700; }}
.muted {{ color:var(--muted); font-size:13px; margin-top:12px; }}
.notice {{ border-radius:9px; padding:11px 13px; margin-bottom:18px; }}
.success {{ background:#123522; color:#8ce7b2; }} .error {{ background:#3a181c; color:#ff9ca5; }}
.footer {{ color:var(--muted); font-size:12px; text-align:center; }}
@media (max-width:620px) {{
  .metric-row {{ align-items:flex-start; }}
  .service-line {{ flex-wrap:wrap; }}
}}
</style>
</head>
<body>
<main>
<div class="header"><h1>QBitLeap BTC</h1><a class="refresh" href="/">Refresh</a></div>
{notice}
<details class="card" open>
<summary><h2>Mining Services</h2></summary>
<div class="card-body">
{service_row("Qbit Core", qbit_up, f"Block {qbit_height:,}" if qbit_height is not None else "Not Running")}
{service_row("Bitcoin Core", bitcoin_up, f"Block {bitcoin_height:,}" if bitcoin_height is not None else "Not Running")}
{service_row("AuxPoW Merge Mine", auxpow_up)}
</div>
</details>
<details class="card" open>
<summary><h2>Mining Telemetry</h2></summary>
<div class="card-body">
<div class="metric-row"><span>Telemetry Status</span>{state_badge(telemetry is not None, "Live", "Not Connected")}</div>
<div class="metric-row"><span>Connected Workers</span><span class="metric-value">{int(telemetry.get("connected_workers", 0)) if telemetry else "—"}</span></div>
<div class="metric-row"><span>Current Hashrate</span><span class="metric-value">{format_hashrate(telemetry.get("current_hashrate_hs")) if telemetry else "—"}</span></div>
<div class="metric-row"><span>Current Difficulty</span><span class="metric-value">{format_number(telemetry.get("current_difficulty")) if telemetry else "—"}</span></div>
<div class="metric-row"><span>Accepted Shares</span><span class="metric-value">{int(telemetry.get("accepted_shares", 0)) if telemetry else "—"}</span></div>
<div class="metric-row"><span>Rejected Shares</span><span class="metric-value">{int(telemetry.get("rejected_shares", 0)) if telemetry else "—"}</span></div>
<div class="metric-row"><span>Best Share</span><span class="metric-value">{format_number(telemetry.get("best_share_difficulty")) if telemetry else "—"}</span></div>
<div class="metric-row"><span>Qbit Blocks Found</span><span class="metric-value">{int(telemetry.get("qbit_blocks_found", 0)) if telemetry else "—"}</span></div>
<div class="metric-row"><span>Bitcoin Blocks Found</span><span class="metric-value">{int(telemetry.get("bitcoin_blocks_found", 0)) if telemetry else "—"}</span></div>
</div>
</details>
<details class="card">
<summary><h2>Payout Addresses</h2></summary>
<div class="card-body">
<form method="post" action="/save">
<label for="qbt">QBT Payout Address</label>
<input id="qbt" name="qbt_payout" value="{qbt}" autocomplete="off" required>
<label for="btc">BTC Payout Address</label>
<input id="btc" name="btc_payout" value="{btc}" autocomplete="off" required>
<button type="submit">Save</button>
</form>
<p class="muted">Both payout addresses are stored in the app's persistent configuration.</p>
</div>
</details>
<p class="footer">Last updated: {html.escape(updated)} · automatic refresh every 5 minutes</p>
</main>
</body>
</html>""".encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/":
            self.send_error(404)
            return
        body = render(self.headers)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
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

            qbit_result = qbit_rpc("validateaddress", [qbt])
            if not isinstance(qbit_result, dict) or not qbit_result.get("isvalid"):
                raise ValueError("The QBT payout address is not valid for this Qbit network.")

            bitcoin_result = bitcoin_rpc("validateaddress", [btc])
            if not isinstance(bitcoin_result, dict) or not bitcoin_result.get("isvalid"):
                raise ValueError("The BTC payout address is not valid for this Bitcoin network.")

            atomic_write(QBT_FILE, qbt)
            atomic_write(BTC_FILE, btc)
            body = render(self.headers, message="Mining payout addresses saved.")
            code = 200
        except ValueError as exc:
            body = render(self.headers, error=str(exc))
            code = 400
        except Exception:
            body = render(self.headers, error="The payout addresses could not be saved.")
            code = 500
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}", flush=True)


if __name__ == "__main__":
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    print(f"QBitLeap dashboard listening on 0.0.0.0:{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
