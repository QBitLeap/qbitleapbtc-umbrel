#!/usr/bin/env python3
import base64
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from decimal import Decimal
from pathlib import Path
from urllib.request import Request, urlopen

TELEMETRY_FILE = Path(os.environ.get("AUXPOW_TELEMETRY_FILE", "/telemetry/telemetry.json"))
HISTORY_FILE = TELEMETRY_FILE.with_name("block-history.json")
EXPECTED_FILE = Path("/config/miner-expected-hashrates.json")
STRATUM_PORT = int(os.environ.get("AUXPOW_STRATUM_PORT", "3335"))
STARTUP_DIFF = float(os.environ.get("AUXPOW_STRATUM_VARDIFF_STARTUP_DIFF", "500000"))

stats = {
    "status": "starting", "connected_workers": 0, "submitted_shares": 0,
    "accepted_shares": 0, "rejected_shares": 0, "current_hashrate_hs": 0.0,
    "best_share_difficulty": 0.0, "current_difficulty": STARTUP_DIFF,
    "qbit_blocks_found": 0, "fractal_blocks_found": 0, "bitcoin_blocks_found": 0, "workers": [],
    "block_history": {"qbit": [], "fractal": [], "bitcoin": []}, "updated_at": 0,
}
lock = threading.Lock()
worker_totals = {}
last_candidate = {"worker": "unknown", "hash": ""}

WORKER_STATS_RE = re.compile(
    r"user=(?P<user>\S+) submitted=(?P<submitted>\d+) accepted=(?P<accepted>\d+) "
    r"low_diff=(?P<low>\d+) stale=(?P<stale>\d+) duplicate=(?P<duplicate>\d+) "
    r"qbit_candidates=(?P<qbit_candidates>\d+) qbit_accepted=(?P<qbit_accepted>\d+) "
    r"accepted_per_sec=(?P<accepted_per_sec>[0-9.]+)"
)
SHARE_RE = re.compile(r"user=(?P<user>\S+).*hash=(?P<hash>[0-9a-fA-F]{64})")
DIFF_RE = re.compile(r"(?:next|share|advertised|desired|old|current)_difficulty=(?P<diff>[0-9.eE+-]+)")

def atomic_write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True); handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try: os.unlink(temp_name)
        except FileNotFoundError: pass

def load_json(path, default):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value
    except Exception:
        return default

def rpc_height(prefix):
    host = os.environ.get(f"{prefix}_RPC_HOST", "")
    port = int(os.environ.get(f"{prefix}_RPC_PORT", "0") or 0)
    user = os.environ.get(f"{prefix}_RPC_USER", "")
    password = os.environ.get(f"{prefix}_RPC_PASSWORD", "")
    try:
        payload = json.dumps({"jsonrpc":"1.0","id":"telemetry","method":"getblockcount","params":[]}).encode()
        auth = base64.b64encode(f"{user}:{password}".encode()).decode()
        req = Request(f"http://{host}:{port}", data=payload, headers={"Authorization":f"Basic {auth}","Content-Type":"application/json"})
        with urlopen(req, timeout=3) as response: return int(json.load(response)["result"])
    except Exception:
        return None

def count_connected_workers():
    target = f"{STRATUM_PORT:04X}"; count = 0
    try:
        with open("/proc/net/tcp", "r", encoding="utf-8") as handle:
            next(handle, None)
            for line in handle:
                parts = line.split()
                if len(parts) >= 4 and parts[1].rsplit(":",1)[-1].upper() == target and parts[3] == "01": count += 1
    except OSError: pass
    return count

def difficulty_from_hash(hash_hex):
    value = int(hash_hex, 16)
    return 0.0 if value <= 0 else float(Decimal(2**256 - 1) / Decimal(value) / Decimal(2**32))

def expected_rates():
    raw = load_json(EXPECTED_FILE, {})
    return raw if isinstance(raw, dict) else {}

def recompute_totals():
    submitted = accepted = rejected = qbit = bitcoin = 0; hashrate = Decimal("0"); workers = []
    expected = expected_rates(); now = int(time.time())
    for user, item in sorted(worker_totals.items()):
        submitted += item["submitted"]; accepted += item["accepted"]; rejected += item["rejected"]
        qbit += item["qbit"]; bitcoin += item["bitcoin"]
        rate = Decimal(str(item["accepted_per_sec"])) * Decimal(2**32) * Decimal(str(item.get("difficulty", STARTUP_DIFF)))
        hashrate += rate
        workers.append({"name":user,"hashrate_hs":float(rate),"accepted":item["accepted"],"rejected":item["rejected"],
                        "last_share_at":item.get("last_share_at",0),"difficulty":item.get("difficulty",STARTUP_DIFF),
                        "expected_hashrate_hs":float(expected.get(user,0) or 0),"active": now-item.get("last_share_at",0) <= 180})
    stats.update({"submitted_shares":submitted,"accepted_shares":accepted,"rejected_shares":rejected,
                  "qbit_blocks_found":qbit,"bitcoin_blocks_found":bitcoin,"current_hashrate_hs":float(hashrate),"workers":workers})

def record_block(chain):
    history = load_json(HISTORY_FILE, {"qbit":[],"fractal":[],"bitcoin":[]})
    if not isinstance(history, dict): history = {"qbit":[],"fractal":[],"bitcoin":[]}
    history.setdefault(chain, [])
    prefix = {"qbit": "QBIT", "fractal": "FRACTAL", "bitcoin": "BITCOIN"}[chain]
    history[chain].insert(0, {"height":rpc_height(prefix),"found_at":int(time.time()),"worker":last_candidate["worker"],"hash":last_candidate["hash"]})
    history[chain] = history[chain][:100]
    atomic_write(HISTORY_FILE, history); stats["block_history"] = history

def publish_loop():
    while True:
        with lock:
            stats["connected_workers"] = count_connected_workers(); stats["status"] = "connected" if stats["connected_workers"] else "listening"
            stats["block_history"] = load_json(HISTORY_FILE, {"qbit":[],"fractal":[],"bitcoin":[]}); stats["updated_at"] = int(time.time())
            recompute_totals(); payload = dict(stats)
        atomic_write(TELEMETRY_FILE, payload); time.sleep(2)

def process_line(line):
    with lock:
        match = WORKER_STATS_RE.search(line)
        if match:
            user = match.group("user"); previous = worker_totals.get(user,{})
            worker_totals[user] = {"submitted":int(match.group("submitted")),"accepted":int(match.group("accepted")),
                "rejected":int(match.group("low"))+int(match.group("stale"))+int(match.group("duplicate")),
                "qbit":int(match.group("qbit_accepted")),"bitcoin":previous.get("bitcoin",0),
                "accepted_per_sec":float(match.group("accepted_per_sec")),"difficulty":previous.get("difficulty",stats["current_difficulty"]),
                "last_share_at":previous.get("last_share_at",0)}
        if "accepted share" in line or "qbit block candidate" in line or "child block candidate" in line:
            share = SHARE_RE.search(line)
            if share:
                user, hash_hex = share.group("user"), share.group("hash")
                last_candidate.update(worker=user, hash=hash_hex)
                item = worker_totals.setdefault(user,{"submitted":0,"accepted":0,"rejected":0,"qbit":0,"bitcoin":0,"accepted_per_sec":0.0,"difficulty":stats["current_difficulty"]})
                item["last_share_at"] = int(time.time())
                stats["best_share_difficulty"] = max(stats["best_share_difficulty"], difficulty_from_hash(hash_hex))
        diff = DIFF_RE.search(line)
        if diff:
            try:
                value = float(diff.group("diff")); stats["current_difficulty"] = value
                if last_candidate["worker"] in worker_totals: worker_totals[last_candidate["worker"]]["difficulty"] = value
            except ValueError: pass
        if "qbit accepted AuxPoW block" in line: record_block("qbit")
        if "fractal accepted AuxPoW block" in line: record_block("fractal"); stats["fractal_blocks_found"] += 1
        if "parent submit accepted after child evaluation" in line: record_block("bitcoin")
        recompute_totals()

def main():
    child = subprocess.Popen([sys.executable,"-m","multi_auxpow_coordinator"],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
    def forward(signum,_frame):
        if child.poll() is None: child.send_signal(signum)
    signal.signal(signal.SIGTERM,forward); signal.signal(signal.SIGINT,forward)
    threading.Thread(target=publish_loop,daemon=True).start()
    assert child.stdout is not None
    for line in child.stdout: print(line,end="",flush=True); process_line(line)
    return child.wait()
if __name__ == "__main__": raise SystemExit(main())
