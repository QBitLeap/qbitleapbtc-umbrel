#!/usr/bin/env bash
set -euo pipefail

: "${QBIT_MINER_ADDRESS_FILE:=/config/qbt-payout-address.txt}"
: "${QBIT_MINER_ADDRESS_POLL_SECONDS:=2}"
: "${CKPOOL_UPSTREAM_START:=/usr/local/bin/start-ckpool.sh}"

printf 'QBitLeap CKPool: waiting for a saved QBT payout address in %s\n' "${QBIT_MINER_ADDRESS_FILE}"

while true; do
  if [[ -s "${QBIT_MINER_ADDRESS_FILE}" ]]; then
    QBIT_MINER_ADDRESS="$(tr -d '\r\n' < "${QBIT_MINER_ADDRESS_FILE}")"
    if [[ -n "${QBIT_MINER_ADDRESS}" ]]; then
      export QBIT_MINER_ADDRESS
      printf 'QBitLeap CKPool: saved QBT payout address found; starting upstream CKPool\n'
      exec "${CKPOOL_UPSTREAM_START}"
    fi
  fi
  sleep "${QBIT_MINER_ADDRESS_POLL_SECONDS}"
done
