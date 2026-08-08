#!/usr/bin/env python3
import base64
import html
import json
import math
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
FRACTAL_FILE = CONFIG_DIR / "fractal-payout-address.txt"
EXPECTED_FILE = CONFIG_DIR / "miner-expected-hashrates.json"
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

FRACTAL_RPC_HOST = os.environ.get("FRACTAL_RPC_HOST", "")
FRACTAL_RPC_PORT = int(os.environ.get("FRACTAL_RPC_PORT", "8332"))
FRACTAL_RPC_USER = os.environ.get("FRACTAL_RPC_USER", "")
FRACTAL_RPC_PASSWORD = os.environ.get("FRACTAL_RPC_PASSWORD", "")

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


def fractal_rpc(method, params=None):
    return rpc_call(
        FRACTAL_RPC_HOST,
        FRACTAL_RPC_PORT,
        FRACTAL_RPC_USER,
        FRACTAL_RPC_PASSWORD,
        method,
        params,
    )


def chain_status(rpc):
    try:
        info = rpc("getblockchaininfo")
        if not isinstance(info, dict):
            raise RuntimeError("Invalid blockchain status response")
        block_height = int(info.get("blocks", 0))
        progress = max(0.0, min(1.0, float(info.get("verificationprogress", 0))))
        state = "syncing" if bool(info.get("initialblockdownload", False)) else "ready"
        return state, block_height, progress
    except Exception:
        return "offline", None, None


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

def read_expected_rates():
    try:
        data = json.loads(EXPECTED_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        result = {}
        for name, raw_rate in data.items():
            rate = float(raw_rate)
            if math.isfinite(rate) and rate > 0:
                result[str(name)] = rate
        return result
    except Exception:
        return {}


def parse_expected_fields(workers, rates):
    if len(workers) != len(rates):
        raise ValueError("Expected miner hashrates could not be read.")
    result = {}
    for worker, raw_rate in zip(workers, rates):
        worker = worker.strip()
        raw_rate = raw_rate.strip()
        if not worker or not raw_rate:
            continue
        if worker in result:
            raise ValueError("Each miner may have only one expected hashrate.")
        try:
            rate = float(raw_rate)
        except ValueError as exc:
            raise ValueError("Expected miner hashrates must be numbers in TH/s.") from exc
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError("Each expected miner hashrate must be greater than zero.")
        result[worker] = rate * 1_000_000_000_000
    return result


def age_text(timestamp):
    try:
        seconds = max(0, int(datetime.now().timestamp()) - int(timestamp))
    except (TypeError, ValueError):
        return "—"
    if seconds < 60:
        return f"{seconds} sec ago"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    return f"{seconds // 3600} hr ago"


def reject_rate(accepted, rejected):
    total = accepted + rejected
    return 0.0 if total <= 0 else rejected * 100.0 / total


def health_class(worker):
    if not worker.get("active"):
        return "down", "Not Mining"
    rejected = reject_rate(int(worker.get("accepted", 0)), int(worker.get("rejected", 0)))
    expected = float(worker.get("expected_hashrate_hs", 0) or 0)
    actual = float(worker.get("hashrate_hs", 0) or 0)
    if rejected >= 5 or (expected and actual < expected * 0.7):
        return "warn", "Needs Attention"
    return "up", "Mining Normally"


def block_history_rows(items, empty_text):
    if not items:
        return f'<p class="muted empty">{html.escape(empty_text)}</p>'
    rows = []
    for item in items:
        when = datetime.fromtimestamp(int(item.get("found_at", 0))).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        height = item.get("height")
        height_text = f"Block {int(height):,}" if height is not None else "Block height unavailable"
        rows.append('<div class="history-row"><div><strong>' + html.escape(height_text) + '</strong><div class="muted compact">' + html.escape(when) + '</div></div><span>' + html.escape(str(item.get("worker", "unknown"))) + '</span></div>')
    return "".join(rows)


def state_badge(ok, yes_text, no_text):
    cls = "up" if ok else "down"
    text = yes_text if ok else no_text
    icon = "✅" if ok else "❌"
    return f'<span class="state {cls}">{icon} {html.escape(text)}</span>'


def service_row(name, state, status=""):
    dot_class = {"ready": "up", "syncing": "warn", "offline": "down"}.get(state, "down")
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
    fractal = html.escape(read_text(FRACTAL_FILE), quote=True)
    configured_expected = read_expected_rates()
    qbit_state, qbit_height, qbit_progress = chain_status(qbit_rpc)
    bitcoin_state, bitcoin_height, bitcoin_progress = chain_status(bitcoin_rpc)
    fractal_state, fractal_height, fractal_progress = chain_status(fractal_rpc)
    auxpow_up = auxpow_connected()
    telemetry = read_telemetry()
    workers = telemetry.get("workers", []) if telemetry else []
    history = telemetry.get("block_history", {}) if telemetry else {}
    accepted = int(telemetry.get("accepted_shares", 0)) if telemetry else 0
    rejected = int(telemetry.get("rejected_shares", 0)) if telemetry else 0
    rejects = reject_rate(accepted, rejected)
    last_share = max((int(worker.get("last_share_at", 0)) for worker in workers), default=0)
    expected_total = sum(float(worker.get("expected_hashrate_hs", 0) or 0) for worker in workers)
    total_rate = float(telemetry.get("current_hashrate_hs", 0) or 0) if telemetry else 0
    if not telemetry or not workers or not last_share or int(datetime.now().timestamp()) - last_share > 180:
        overall_cls, overall_text = "down", "Not Mining"
    elif rejects >= 5 or (expected_total and total_rate < expected_total * 0.7):
        overall_cls, overall_text = "warn", "Needs Attention"
    else:
        overall_cls, overall_text = "up", "Mining Normally"

    notice = f'<div class="notice success">{html.escape(message)}</div>' if message else (f'<div class="notice error">{html.escape(error)}</div>' if error else "")
    updated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    worker_rows = []
    for worker in workers:
        cls, label = health_class(worker)
        expected_rate = float(worker.get("expected_hashrate_hs", 0) or 0)
        details = [format_hashrate(worker.get("hashrate_hs")), f'{reject_rate(int(worker.get("accepted",0)), int(worker.get("rejected",0))):.1f}% rejected', f'last share {age_text(worker.get("last_share_at"))}']
        if expected_rate:
            details.insert(1, f'expected {format_hashrate(expected_rate)}')
        worker_rows.append(f'<div class="worker-row"><div class="worker-title"><span class="service-dot {cls}"></span><strong>{html.escape(str(worker.get("name","unknown")))}</strong><span class="worker-state {cls}">{html.escape(label)}</span></div><div class="worker-detail">{html.escape(" · ".join(details))}</div></div>')
    if not worker_rows:
        worker_rows.append('<p class="muted empty">No local miners connected.</p>')

    detected_names = {
        str(worker.get("name", "")).strip()
        for worker in workers
        if str(worker.get("name", "")).strip()
    }
    expected_names = sorted(detected_names | set(configured_expected))
    missing_expected = sorted(detected_names - set(configured_expected))
    expected_rows = []
    for name in expected_names:
        expected_rate = configured_expected.get(name)
        value = f'{expected_rate / 1_000_000_000_000:g}' if expected_rate else ""
        prompt = '<span class="expected-prompt">Expected hashrate needed</span>' if name in detected_names and not expected_rate else ""
        expected_rows.append(
            '<div class="expected-row">'
            f'<div class="expected-title"><strong>{html.escape(name)}</strong>{prompt}</div>'
            f'<input type="hidden" name="expected_worker" value="{html.escape(name, quote=True)}">'
            f'<div class="rate-input"><input type="number" name="expected_rate" value="{html.escape(value, quote=True)}" min="0" step="any" inputmode="decimal" placeholder="Enter expected rate"><span>TH/s</span></div>'
            '</div>'
        )
    if not expected_rows:
        expected_rows.append('<p class="muted empty">Connect a miner to add its expected hashrate.</p>')

    if not message and not error and missing_expected:
        names = ", ".join(missing_expected)
        notice = f'<div class="notice warning">Set the expected hashrate for: {html.escape(names)}.</div>'

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="300"><title>QBitLeap BTC</title>
<style>
:root {{color-scheme:dark;--bg:#0c1017;--panel:#151b25;--line:#283142;--text:#f5f7fa;--muted:#98a2b3;--accent:#7c9cff;--good:#36c275;--warn:#e4ad3d;--bad:#f05d68;}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,sans-serif}} main{{width:min(760px,calc(100% - 32px));margin:40px auto}}
.header{{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:24px}} h1{{font-size:28px;margin:0}} .card{{background:var(--panel);border:1px solid var(--line);border-radius:14px;margin-bottom:18px}}
h2{{margin:0;font-size:17px}} summary{{position:relative;display:flex;align-items:center;justify-content:center;padding:22px;cursor:pointer;list-style:none}} summary::-webkit-details-marker{{display:none}} summary::after{{content:"▸";position:absolute;right:22px;color:var(--muted)}} details[open]>summary::after{{transform:rotate(90deg)}} .card-body{{padding:0 22px 22px}}
.service-row{{display:flex;justify-content:center;padding:12px 0;text-align:center}} .service-row+.service-row,.metric-row+.metric-row,.worker-row+.worker-row,.history-row+.history-row{{border-top:1px solid var(--line)}} .service-line{{display:inline-flex;align-items:center;gap:10px;font-weight:650}} .service-dot{{width:12px;height:12px;border-radius:3px;background:currentColor;flex:0 0 auto}}
.metric-row,.history-row{{display:flex;justify-content:space-between;align-items:center;gap:18px;padding:12px 0}} .metric-value{{font-weight:700}} .status-text{{font-weight:700}} .up{{color:var(--good)}} .warn{{color:var(--warn)}} .down{{color:var(--bad)}}
.worker-row{{padding:14px 0}} .worker-title{{display:flex;align-items:center;gap:10px;flex-wrap:wrap}} .worker-state{{margin-left:auto;font-weight:650}} .worker-detail{{color:var(--muted);font-size:13px;margin:7px 0 0 22px}}
label{{display:block;font-weight:600;margin:0 0 8px}} input{{width:100%;border:1px solid var(--line);border-radius:9px;padding:12px;margin-bottom:18px;background:#0e141e;color:var(--text);font:inherit}} button,.refresh{{border:0;border-radius:9px;padding:10px 16px;background:var(--accent);color:#08101f;font:inherit;font-weight:700;cursor:pointer;text-decoration:none}}
.expected-row{{padding:12px 0}} .expected-row+.expected-row{{border-top:1px solid var(--line)}} .expected-title{{display:flex;justify-content:space-between;gap:12px;margin-bottom:8px}} .expected-prompt{{color:var(--warn);font-size:13px}} .rate-input{{display:flex;align-items:center;gap:10px}} .rate-input input{{margin:0}} .rate-input span{{color:var(--muted);white-space:nowrap}}
.muted{{color:var(--muted);font-size:13px;margin-top:12px}} .compact{{margin:3px 0 0}} .empty{{text-align:center;padding:12px 0}} .notice{{border-radius:9px;padding:11px 13px;margin-bottom:18px}} .success{{background:#123522;color:#8ce7b2}} .warning{{background:#392d13;color:#f4cd78}} .error{{background:#3a181c;color:#ff9ca5}} .footer{{color:var(--muted);font-size:12px;text-align:center}}
</style></head><body><main>
<div class="header"><h1>QBitLeap BTC</h1><a class="refresh" href="/">Refresh</a></div>{notice}
<details class="card" open><summary><h2>Mining Services</h2></summary><div class="card-body">
{service_row("Qbit Core", qbit_state, f"Synchronizing {qbit_progress * 100:.2f}% · Block {qbit_height:,}" if qbit_state == "syncing" else (f"Block {qbit_height:,}" if qbit_height is not None else "Not Running"))}{service_row("Bitcoin Core", bitcoin_state, f"Synchronizing {bitcoin_progress * 100:.2f}% · Block {bitcoin_height:,}" if bitcoin_state == "syncing" else (f"Block {bitcoin_height:,}" if bitcoin_height is not None else "Not Running"))}{service_row("Fractal Bitcoin Core", fractal_state, f"Synchronizing {fractal_progress * 100:.2f}% · Block {fractal_height:,}" if fractal_state == "syncing" else (f"Block {fractal_height:,}" if fractal_height is not None else "Not Running"))}{service_row("AuxPoW Merge Mine", "ready" if auxpow_up else "offline")}
</div></details>
<details class="card" open><summary><h2>Local Mining Status</h2></summary><div class="card-body">
<div class="metric-row"><span>Status</span><span class="status-text {overall_cls}">{html.escape(overall_text)}</span></div>
<div class="metric-row"><span>Connected Miners</span><span class="metric-value">{int(telemetry.get("connected_workers",0)) if telemetry else 0}</span></div>
<div class="metric-row"><span>Total Hashrate</span><span class="metric-value">{format_hashrate(total_rate)}</span></div>
{f'<div class="metric-row"><span>Expected Hashrate</span><span class="metric-value">{format_hashrate(expected_total)}</span></div>' if expected_total else ''}
<div class="metric-row"><span>Rejected Shares</span><span class="metric-value">{rejects:.1f}%</span></div>
<div class="metric-row"><span>Last Share Received</span><span class="metric-value">{age_text(last_share) if last_share else "—"}</span></div>
</div></details>
<details class="card" open><summary><h2>Connected Miners</h2></summary><div class="card-body">{''.join(worker_rows)}</div></details>
<details class="card"><summary><h2>Qbit Blocks Found ({len(history.get("qbit",[]))})</h2></summary><div class="card-body">{block_history_rows(history.get("qbit",[]),"No Qbit blocks found yet.")}</div></details>
<details class="card"><summary><h2>Fractal Blocks Found ({len(history.get("fractal",[]))})</h2></summary><div class="card-body">{block_history_rows(history.get("fractal",[]),"No Fractal blocks found yet.")}</div></details>
<details class="card"><summary><h2>Bitcoin Blocks Found ({len(history.get("bitcoin",[]))})</h2></summary><div class="card-body">{block_history_rows(history.get("bitcoin",[]),"No Bitcoin blocks found yet.")}</div></details>
<details class="card"{' open' if missing_expected else ''}><summary><h2>Payout Addresses &amp; Miner Expectations</h2></summary><div class="card-body"><form method="post" action="/save">
<label for="qbt">QBT Payout Address</label><input id="qbt" name="qbt_payout" value="{qbt}" autocomplete="off" required>
<label for="btc">BTC Payout Address</label><input id="btc" name="btc_payout" value="{btc}" autocomplete="off" required>
<label for="fractal">Fractal BTC Payout Address (optional)</label><input id="fractal" name="fractal_payout" value="{fractal}" autocomplete="off">
<label>Expected Local Miner Hashrates</label>{''.join(expected_rows)}
<button type="submit">Save</button></form><p class="muted">Miners appear here automatically after the AuxPoW server receives their worker identity.</p></div></details>
<p class="footer">Last updated: {html.escape(updated)} · automatic refresh every 5 minutes</p></main></body></html>""".encode("utf-8")


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
            fractal = form.get("fractal_payout", [""])[0].strip()
            expected_rates = parse_expected_fields(
                form.get("expected_worker", []),
                form.get("expected_rate", []),
            )
            if not ADDRESS_RE.fullmatch(qbt):
                raise ValueError("Enter a valid QBT payout address.")
            if not ADDRESS_RE.fullmatch(btc):
                raise ValueError("Enter a valid BTC payout address.")
            if fractal and not ADDRESS_RE.fullmatch(fractal):
                raise ValueError("Enter a valid Fractal BTC payout address.")

            qbit_result = qbit_rpc("validateaddress", [qbt])
            if not isinstance(qbit_result, dict) or not qbit_result.get("isvalid"):
                raise ValueError("The QBT payout address is not valid for this Qbit network.")

            bitcoin_result = bitcoin_rpc("validateaddress", [btc])
            if not isinstance(bitcoin_result, dict) or not bitcoin_result.get("isvalid"):
                raise ValueError("The BTC payout address is not valid for this Bitcoin network.")

            if fractal:
                fractal_result = fractal_rpc("validateaddress", [fractal])
                if not isinstance(fractal_result, dict) or not fractal_result.get("isvalid"):
                    raise ValueError("The Fractal BTC payout address is not valid for this Fractal network.")

            atomic_write(QBT_FILE, qbt)
            atomic_write(BTC_FILE, btc)
            atomic_write(FRACTAL_FILE, fractal)
            atomic_write(EXPECTED_FILE, json.dumps(expected_rates, sort_keys=True))
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
