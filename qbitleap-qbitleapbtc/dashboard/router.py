#!/usr/bin/env python3
import os
import json
import selectors
import socket
import threading
import re
from pathlib import Path

LISTEN_PORT = int(os.environ.get("ROUTER_PORT", "3335"))
MODE_FILE = Path(os.environ.get("MODE_FILE", "/config/mining-mode.txt"))
STATUS_FILE = Path(os.environ.get("ROUTER_STATUS_FILE", "/telemetry/router-status.json"))
QBT_ADDRESS_FILE = Path(os.environ.get("QBT_ADDRESS_FILE", "/config/qbt-payout-address.txt"))
BACKENDS = {
    "auxpow": (os.environ.get("AUXPOW_BACKEND_HOST", "auxpow"), int(os.environ.get("AUXPOW_BACKEND_PORT", "3336"))),
    "permissionless": (os.environ.get("PERMISSIONLESS_BACKEND_HOST", "permissionless"), int(os.environ.get("PERMISSIONLESS_BACKEND_PORT", "3333"))),
}
active_connections = 0
connection_lock = threading.Lock()


def write_status():
    payload = json.dumps({"mode": selected_mode(), "active_connections": active_connections}) + "\n"
    temp = STATUS_FILE.with_suffix(".tmp")
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp.write_text(payload, encoding="utf-8")
    os.replace(temp, STATUS_FILE)


def selected_mode():
    try:
        mode = MODE_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        mode = "auxpow"
    return mode if mode in BACKENDS else "auxpow"


def permissionless_messages(buffer, data):
    buffer += data
    output = []
    while b"\n" in buffer:
        line, buffer = buffer.split(b"\n", 1)
        try:
            message = json.loads(line)
            if message.get("method") == "mining.authorize" and message.get("params"):
                payout = QBT_ADDRESS_FILE.read_text(encoding="utf-8").strip()
                worker = re.sub(r"[^A-Za-z0-9_-]", "-", str(message["params"][0]))[:32]
                message["params"][0] = payout + (f".{worker}" if worker else "")
                line = json.dumps(message, separators=(",", ":")).encode()
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        output.append(line + b"\n")
    return buffer, b"".join(output)


def proxy(client):
    global active_connections
    mode = selected_mode()
    upstream = None
    selector = selectors.DefaultSelector()
    client_buffer = b""
    try:
        with connection_lock:
            active_connections += 1
            write_status()
        upstream = socket.create_connection(BACKENDS[mode], timeout=5)
        client.setblocking(False)
        upstream.setblocking(False)
        selector.register(client, selectors.EVENT_READ, upstream)
        selector.register(upstream, selectors.EVENT_READ, client)
        while selected_mode() == mode:
            for key, _events in selector.select(timeout=1):
                source = key.fileobj
                target = key.data
                data = source.recv(65536)
                if not data:
                    return
                if mode == "permissionless" and source is client:
                    client_buffer, data = permissionless_messages(client_buffer, data)
                    if not data:
                        continue
                target.sendall(data)
    except OSError as exc:
        print(f"router: {mode} connection ended: {exc}", flush=True)
    finally:
        selector.close()
        client.close()
        if upstream is not None:
            upstream.close()
        with connection_lock:
            active_connections -= 1
            write_status()


def main():
    write_status()
    with socket.create_server(("0.0.0.0", LISTEN_PORT), reuse_port=False) as server:
        print(f"Stratum mode router listening on {LISTEN_PORT}", flush=True)
        while True:
            client, _address = server.accept()
            threading.Thread(target=proxy, args=(client,), daemon=True).start()


if __name__ == "__main__":
    main()
