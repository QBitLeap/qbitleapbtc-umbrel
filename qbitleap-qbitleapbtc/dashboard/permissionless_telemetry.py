#!/usr/bin/env python3
import base64
import json
import os
import time
from pathlib import Path
from urllib.request import Request, urlopen


CKPOOL_STATUS_FILE = Path(os.environ.get("CKPOOL_STATUS_FILE", "/ckpool/pool/rejects.status"))
OUTPUT_FILE = Path(os.environ.get("PERMISSIONLESS_TELEMETRY_FILE", "/telemetry/permissionless.json"))
STATE_FILE = Path(os.environ.get("PERMISSIONLESS_STATE_FILE", "/telemetry/permissionless-state.json"))
QBT_ADDRESS_FILE = Path(os.environ.get("QBT_ADDRESS_FILE", "/config/qbt-payout-address.txt"))
POLL_SECONDS = float(os.environ.get("TELEMETRY_POLL_SECONDS", "2"))
STALE_SECONDS = int(os.environ.get("TELEMETRY_STALE_SECONDS", "180"))
QBIT_RPC_HOST = os.environ.get("QBIT_RPC_HOST", "qbitd")
QBIT_RPC_PORT = int(os.environ.get("QBIT_RPC_PORT", "8352"))
QBIT_RPC_USER = os.environ.get("QBIT_RPC_USER", "qbitrpc")
QBIT_RPC_PASSWORD = os.environ.get("QBIT_RPC_PASSWORD", "")
HASHES_PER_DIFF = 2**32
REJECT_REASONS = (
    "above_target",
    "stale",
    "duplicate",
    "invalid_job",
    "invalid_ntime",
    "invalid_version",
    "malformed",
)


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_json(path, default):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else default
    except (OSError, ValueError, json.JSONDecodeError):
        return default


def bucket(entry, name, field="count"):
    value = entry.get(name, {})
    try:
        return float(value.get(field, 0)) if isinstance(value, dict) else 0.0
    except (TypeError, ValueError):
        return 0.0


def display_worker(workername):
    workername = str(workername or "unknown")
    return workername.split(".", 1)[1] if "." in workername else workername


def reconcile_block_history(history):
    reconciled = []
    positions = {}
    for item in history:
        if not isinstance(item, dict):
            continue
        block_hash = item.get("block_hash")
        height = item.get("height")
        key = ("height", int(height)) if height is not None else (("hash", str(block_hash)) if block_hash else None)
        if key is None or key not in positions:
            positions[key] = len(reconciled) if key is not None else None
            reconciled.append(dict(item))
            continue

        current = reconciled[positions[key]]
        current_worker = str(current.get("worker") or "")
        incoming_worker = str(item.get("worker") or "")
        if incoming_worker and incoming_worker != "permissionless miner":
            current["worker"] = incoming_worker
            current["found_at"] = item.get("found_at", current.get("found_at"))
        elif not current_worker:
            current["worker"] = incoming_worker
        if block_hash:
            current["block_hash"] = block_hash
    return reconciled[:100]


def rpc(method, params=None):
    payload = json.dumps({"jsonrpc": "1.0", "id": "permissionless-telemetry", "method": method, "params": params or []}).encode()
    auth = base64.b64encode(f"{QBIT_RPC_USER}:{QBIT_RPC_PASSWORD}".encode()).decode()
    request = Request(
        f"http://{QBIT_RPC_HOST}:{QBIT_RPC_PORT}",
        data=payload,
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=4) as response:
        body = json.load(response)
    if body.get("error"):
        raise RuntimeError(str(body["error"]))
    return body.get("result")


def block_height():
    try:
        return int(rpc("getblockcount"))
    except Exception:
        return None


def coinbase_pays(block, address):
    try:
        coinbase = block["tx"][0]
        for output in coinbase.get("vout", []):
            script = output.get("scriptPubKey", {})
            if script.get("address") == address or address in script.get("addresses", []):
                return True
    except (KeyError, IndexError, TypeError):
        pass
    return False


def scan_qbit_blocks(state, history, now=None):
    now = int(time.time() if now is None else now)
    try:
        address = QBT_ADDRESS_FILE.read_text(encoding="utf-8").strip()
        tip = int(rpc("getblockcount"))
    except Exception:
        return history, state.get("qbit_scan_height")
    previous = state.get("qbit_scan_height")
    start = max(0, tip - 20) if previous is None else int(previous) + 1
    known_heights = {int(item["height"]): item for item in history if item.get("height") is not None}
    for height in range(start, tip + 1):
        try:
            block_hash = str(rpc("getblockhash", [height]))
            block = rpc("getblock", [block_hash, 2])
        except Exception:
            return history, height - 1
        if address and coinbase_pays(block, address):
            if height in known_heights:
                known_heights[height]["block_hash"] = block_hash
            else:
                item = {"found_at": now, "height": height, "worker": "permissionless miner", "block_hash": block_hash}
                history.insert(0, item)
                known_heights[height] = item
    history.sort(key=lambda item: int(item.get("height") or 0), reverse=True)
    return history[:100], tip


def snapshot(status, state, now=None):
    now = int(time.time() if now is None else now)
    lastupdate = int(status.get("lastupdate", now))
    runtime = int(status.get("runtime", 0))
    previous_runtime = int(state.get("runtime", -1))
    previous_update = int(state.get("lastupdate", 0))
    reset = previous_runtime < 0 or runtime < previous_runtime or lastupdate < previous_update
    elapsed = max(1, runtime) if reset else max(1, lastupdate - previous_update)
    previous_workers = state.get("workers", {}) if isinstance(state.get("workers"), dict) else {}
    workers = []
    worker_state = {}
    new_blocks = []

    for entry in status.get("workers", []):
        raw_name = str(entry.get("workername", "unknown"))
        old = previous_workers.get(raw_name, {}) if not reset else {}
        accepted = int(bucket(entry, "accepted"))
        rejected = int(sum(bucket(entry, reason) for reason in REJECT_REASONS))
        accepted_diff = bucket(entry, "accepted", "diff")
        accepted_delta = max(0, accepted - int(old.get("accepted", 0)))
        diff_delta = max(0.0, accepted_diff - float(old.get("accepted_diff", 0)))
        rejected_delta = max(0, rejected - int(old.get("rejected", 0)))
        last_share = int(old.get("last_share_at", 0))
        if accepted_delta or rejected_delta:
            last_share = lastupdate
        hashrate = float(old.get("hashrate_hs", 0) or 0)
        if elapsed and diff_delta:
            hashrate = diff_delta * HASHES_PER_DIFF / elapsed
        elif last_share and now - last_share > STALE_SECONDS:
            hashrate = 0.0

        blocks = int(bucket(entry, "block_accepted"))
        previous_blocks = int(old.get("blocks", 0)) if not reset else 0
        for _ in range(max(0, blocks - previous_blocks)):
            new_blocks.append({"found_at": lastupdate, "height": None, "worker": display_worker(raw_name)})

        worker_state[raw_name] = {
            "accepted": accepted,
            "rejected": rejected,
            "accepted_diff": accepted_diff,
            "last_share_at": last_share,
            "hashrate_hs": hashrate,
            "blocks": blocks,
        }
        workers.append({
            "name": display_worker(raw_name),
            "active": bool(last_share and now - last_share <= STALE_SECONDS),
            "accepted": accepted,
            "rejected": rejected,
            "last_share_at": last_share,
            "hashrate_hs": hashrate,
        })

    if new_blocks:
        height = block_height()
        for item in new_blocks:
            item["height"] = height

    history = state.get("block_history", []) if isinstance(state.get("block_history"), list) else []
    history = reconcile_block_history(new_blocks + history)
    pool = status.get("pool", {}) if isinstance(status.get("pool"), dict) else {}
    accepted_total = int(bucket(pool, "accepted"))
    rejected_total = int(sum(bucket(pool, reason) for reason in REJECT_REASONS))
    telemetry = {
        "updated_at": now,
        "source_updated_at": lastupdate,
        "connected_workers": sum(1 for worker in workers if worker["active"]),
        "current_hashrate_hs": sum(float(worker["hashrate_hs"]) for worker in workers if worker["active"]),
        "accepted_shares": accepted_total,
        "rejected_shares": rejected_total,
        "workers": workers,
        "block_history": {"qbit": history, "fractal": [], "bitcoin": []},
    }
    next_state = {
        "runtime": runtime,
        "lastupdate": lastupdate,
        "workers": worker_state,
        "block_history": history,
        "qbit_scan_height": state.get("qbit_scan_height"),
    }
    return telemetry, next_state


def main():
    state = load_json(STATE_FILE, {})
    last_source_update = None
    while True:
        try:
            status = load_json(CKPOOL_STATUS_FILE, {})
            source_update = int(status.get("lastupdate", 0))
            if status:
                telemetry, state = snapshot(status, state)
                history, scanned_height = scan_qbit_blocks(state, state.get("block_history", []))
                state["block_history"] = history
                state["qbit_scan_height"] = scanned_height
                telemetry["block_history"]["qbit"] = history
                atomic_json(STATE_FILE, state)
                atomic_json(OUTPUT_FILE, telemetry)
                last_source_update = source_update
        except Exception as exc:
            print(f"permissionless telemetry: {exc}", flush=True)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
