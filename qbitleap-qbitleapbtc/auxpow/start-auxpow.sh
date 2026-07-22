#!/bin/sh
set -eu

QBIT_ADDRESS_FILE="${QBIT_MINER_ADDRESS_FILE:-/config/qbt-payout-address.txt}"
BITCOIN_ADDRESS_FILE="${BITCOIN_MINER_ADDRESS_FILE:-/config/btc-payout-address.txt}"
POLL_SECONDS="${AUXPOW_ADDRESS_POLL_SECONDS:-2}"

read_address() {
  file="$1"
  if [ -f "$file" ]; then
    tr -d '\r\n' < "$file"
  fi
}

while :; do
  qbit_address="$(read_address "$QBIT_ADDRESS_FILE")"
  bitcoin_address="$(read_address "$BITCOIN_ADDRESS_FILE")"

  if [ -n "$qbit_address" ] && [ -n "$bitcoin_address" ]; then
    export QBIT_MINER_ADDRESS="$qbit_address"
    export BITCOIN_MINER_ADDRESS="$bitcoin_address"
    break
  fi

  echo "auxpow: waiting for Qbit and Bitcoin payout addresses" >&2
  sleep "$POLL_SECONDS"
done

exec python3 -m lab.auxpow.auxpow_coordinator
