#!/usr/bin/env python3
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

TELEMETRY_FILE = Path(os.environ.get("AUXPOW_TELEMETRY_FILE", "/telemetry/telemetry.json"))
STRATUM_PORT = int(os.environ.get("AUXPOW_STRATUM_PORT", "3335"))

stats = {
    "status": "starting",
    "connected_workers": 0,
    "submitted_shares": 0,
    "accepted_shares": 0,
    "rejected_shares": 0,
    "current_hashrate_hs": 0.0,
    "best_share_difficulty": 0.0,
    "current_difficulty": 0.0,
    "qbit_blocks_found": 0,
    "bitcoin_blocks_found": 0,
    "updated_at": 0,
}
lock = threading.Lock()
worker_totals = {}

WORKER_STATS_RE = re.compile(
    r"user=(?P<user>\S+) submitted=(?P<submitted>\d+) accepted=(?P<accepted>\d+) "
    r"low_diff=(?P<low>\d+) stale=(?P<stale>\d+) duplicate=(?P<duplicate>\d+) "
    r"qbit_candidates=(?P<qbit_candidates>\d+) qbit_accepted=(?P<qbit_accepted>\d+) "
    r"accepted_per_sec=(?P<accepted_per_sec>[0-9.]+)"
)
ACCEPTED_RE = re.compile(r"hash=(?P<hash>[0-9a-fA-F]{64})")
DIFF_RE = re.compile(r"(?:next|share|advertised)_difficulty=(?P<diff>[0-9.eE+-]+)")


def atomic_write(payload):
    TELEMETRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="telemetry.", dir=str(TELEMETRY_FILE.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, TELEMETRY_FILE)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def count_connected_workers():
    # Count established IPv4 TCP sessions whose local port is the Stratum port.
    target = f"{STRATUM_PORT:04X}"
    count = 0
    try:
        with open("/proc/net/tcp", "r", encoding="utf-8") as handle:
            next(handle, None)
            for line in handle:
                parts = line.split()
                if len(parts) < 4:
                    continue
                local = parts[1]
                state = parts[3]
                if local.rsplit(":", 1)[-1].upper() == target and state == "01":
                    count += 1
    except OSError:
        pass
    return count


def publish_loop():
    while True:
        with lock:
            stats["connected_workers"] = count_connected_workers()
            stats["status"] = "connected" if stats["connected_workers"] else "listening"
            stats["updated_at"] = int(time.time())
            payload = dict(stats)
        atomic_write(payload)
        time.sleep(2)


def difficulty_from_hash(hash_hex):
    value = int(hash_hex, 16)
    if value <= 0:
        return 0.0
    return float(Decimal(2**256 - 1) / Decimal(value) / Decimal(2**32))


def recompute_totals():
    submitted = accepted = rejected = qbit = bitcoin = 0
    hashrate = Decimal("0")
    for item in worker_totals.values():
        submitted += item["submitted"]
        accepted += item["accepted"]
        rejected += item["rejected"]
        qbit += item["qbit"]
        bitcoin += item["bitcoin"]
        hashrate += Decimal(str(item["accepted_per_sec"])) * Decimal(2**32) * Decimal(str(item.get("difficulty", 1)))
    stats["submitted_shares"] = submitted
    stats["accepted_shares"] = accepted
    stats["rejected_shares"] = rejected
    stats["qbit_blocks_found"] = qbit
    stats["bitcoin_blocks_found"] = bitcoin
    stats["current_hashrate_hs"] = float(hashrate)


def process_line(line):
    with lock:
        match = WORKER_STATS_RE.search(line)
        if match:
            user = match.group("user")
            previous = worker_totals.get(user, {})
            worker_totals[user] = {
                "submitted": int(match.group("submitted")),
                "accepted": int(match.group("accepted")),
                "rejected": int(match.group("low")) + int(match.group("stale")) + int(match.group("duplicate")),
                "qbit": int(match.group("qbit_accepted")),
                "bitcoin": previous.get("bitcoin", 0),
                "accepted_per_sec": float(match.group("accepted_per_sec")),
                "difficulty": previous.get("difficulty", stats["current_difficulty"] or 1),
            }
            recompute_totals()

        if "accepted share" in line:
            hash_match = ACCEPTED_RE.search(line)
            if hash_match:
                stats["best_share_difficulty"] = max(
                    stats["best_share_difficulty"], difficulty_from_hash(hash_match.group("hash"))
                )

        diff_match = DIFF_RE.search(line)
        if diff_match:
            try:
                difficulty = float(diff_match.group("diff"))
                stats["current_difficulty"] = difficulty
            except ValueError:
                pass

        if "qbit accepted AuxPoW block" in line:
            # Periodic worker statistics will reconcile this count later.
            stats["qbit_blocks_found"] += 1
        if "parent submit attempted after qbit acceptance" in line and "result=None" in line:
            stats["bitcoin_blocks_found"] += 1


def main():
    child = subprocess.Popen(
        [sys.executable, "-m", "lab.auxpow.auxpow_coordinator"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    def forward(signum, _frame):
        if child.poll() is None:
            child.send_signal(signum)

    signal.signal(signal.SIGTERM, forward)
    signal.signal(signal.SIGINT, forward)
    threading.Thread(target=publish_loop, daemon=True).start()

    assert child.stdout is not None
    for line in child.stdout:
        print(line, end="", flush=True)
        process_line(line)

    return child.wait()


if __name__ == "__main__":
    raise SystemExit(main())
