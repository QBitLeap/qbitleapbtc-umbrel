# Official Upstream Service Map

This document maps the services provided by the official
`Qbit-Org/qbit-mining-bootstrap` subtree and identifies how QBitLeap BTC will
adapt them for Umbrel.

The official upstream source is located at:

```text
qbitleap-qbitleapbtc/upstream/qbit-mining-bootstrap/
```

Files inside that subtree must remain unchanged.

---

## Mining Modes Provided Upstream

The upstream project currently defines several Docker Compose profiles:

| Profile | Purpose |
|---|---|
| `permissionless` | Direct Qbit mining through CKPool |
| `real-miner-smoke` | Qbit mining test using bundled CPU mining software |
| `auxpow` | Bitcoin and Qbit merge-mining reference stack |
| `prism` | PRISM mining, accounting, and audit stack |

QBitLeap BTC is primarily based on the upstream `auxpow` profile because the
application's objective is to use the same SHA256d hashpower for Bitcoin and
Qbit.

---

# Required QBitLeap Services

## qbitd

Upstream service name:

```text
qbitd
```

Purpose:

- Runs Qbit Core.
- Maintains the Qbit blockchain.
- Produces Qbit AuxPoW templates.
- Accepts completed AuxPoW block submissions.
- Provides JSON-RPC to the merge-mining services.

Important RPC methods:

```text
createauxblock
submitauxblock
getblockchaininfo
```

Persistent container path:

```text
/var/lib/qbit
```

Planned Umbrel storage:

```text
${APP_DATA_DIR}/data/qbit
```

The Qbit RPC port must remain internal to the application network.

The Qbit peer-to-peer port may be published to the Umbrel host when required
for public-chain connectivity.

---

## Existing Umbrel Bitcoin Core

Upstream service being replaced:

```text
bitcoind
```

The upstream `auxpow` profile normally starts its own Bitcoin Core container.

QBitLeap must not start a second Bitcoin Core instance.

Instead, it will connect to the Bitcoin Node app already running on Umbrel
through Umbrel-provided connection variables:

```text
APP_BITCOIN_NODE_IP
APP_BITCOIN_RPC_PORT
APP_BITCOIN_RPC_USER
APP_BITCOIN_RPC_PASS
```

QBitLeap's Umbrel manifest must declare:

```yaml
dependencies:
  - bitcoin
```

The existing Umbrel Bitcoin node remains responsible for:

- Bitcoin blockchain storage
- Bitcoin network connectivity
- Bitcoin block-template generation
- Bitcoin block validation
- Bitcoin RPC

QBitLeap must not mount or modify the Bitcoin data directory unless a later
feature explicitly requires read-only access.

---

## auxpow-stratum

Upstream service name:

```text
auxpow-stratum
```

Purpose:

- Provides the external Stratum mining endpoint.
- Requests Bitcoin parent-chain templates.
- Requests Qbit AuxPoW candidates.
- Adds the Qbit merged-mining commitment to Bitcoin work.
- Sends combined SHA256d work to miners.
- Receives and validates submitted shares.
- Submits qualifying Bitcoin blocks to Bitcoin Core.
- Constructs and submits qualifying AuxPoW payloads to Qbit Core.

Default upstream Stratum port:

```text
3335
```

This is the primary miner-facing service for simultaneous BTC and QBT mining.

Local ASICs and rented SHA256d hashpower should connect to this service.

The port must be published to the Umbrel host.

---

## auxpow-bridge

Upstream service name:

```text
auxpow-bridge
```

Upstream mode:

```text
AUXPOW_MODE=bridge
```

Purpose:

- Runs the upstream reference bridge workflow.
- Requests Qbit AuxPoW candidates.
- Coordinates candidate refreshes.
- Builds and submits AuxPoW payloads using parent-chain work.

The exact role of this service alongside `auxpow-stratum` must be preserved from
upstream unless testing proves that the Stratum service fully supersedes it for
the production mining path.

It must connect to:

- Qbit Core RPC
- Umbrel Bitcoin Core RPC

---

## auxpow-coordinator

Upstream service name:

```text
auxpow-coordinator
```

Purpose:

- Provides the upstream non-Stratum reference coordination workflow.
- Connects Qbit templates with Bitcoin parent-chain work.
- Serves as an implementation and regression reference for merge mining.

The upstream production override currently disables this service.

QBitLeap should not assume it is required in the final runtime until the
upstream production path and tests are fully mapped.

---

# Upstream Services Not Required in Normal Runtime

## bitcoind

Not launched by QBitLeap because the existing Umbrel Bitcoin Node is reused.

---

## permissionless-miner

A simulated test miner for the direct Qbit CKPool workflow.

Not required for real external miners.

---

## real-miner

A bundled CPU miner used for smoke testing.

Not required for local ASICs or rented hashpower.

---

## auxpow-real-miner

A bundled CPU miner used to test the AuxPoW Stratum endpoint.

Not required in production.

---

## ckpool

The upstream CKPool service provides direct Qbit-only Stratum mining on port
`3333`.

It is separate from the `auxpow-stratum` merge-mining service.

QBitLeap may retain CKPool later as an optional Qbit-only fallback mode, but it
is not the primary endpoint for simultaneous BTC and QBT mining.

The primary merge-mining endpoint is:

```text
auxpow-stratum:3335
```

---

## PRISM Services

The upstream PRISM profile includes:

```text
prism-postgres
prism-coordinator
```

These services provide more advanced mining coordination, share accounting,
audit evidence, and public dashboard capabilities.

They are not required for the first working BTC + QBT merge-mining milestone,
but they remain candidates for a later QBitLeap release.

---

# Correct Runtime Flow

```text
Local ASIC or rented SHA256d hashpower
                  │
                  ▼
       QBitLeap AuxPoW Stratum
          auxpow-stratum:3335
                  │
          Combined mining job
          ┌───────┴────────┐
          │                │
          ▼                ▼
Umbrel Bitcoin Core      Qbit Core
     Bitcoin RPC          Qbit RPC
          │                │
          ├── BTC block    ├── createauxblock
          │   templates    └── submitauxblock
          │
          └── BTC block submission
```

The miner performs one stream of SHA256d work.

A submitted share may qualify as:

- an ordinary pool share
- a Qbit block
- a Bitcoin block
- both a Bitcoin and Qbit block

The work is not divided between BTC and QBT. Qbit is committed into the Bitcoin
parent work through AuxPoW merge mining.

---

# Startup Order

The intended startup order is:

```text
1. Confirm the Umbrel Bitcoin Node dependency is installed.
2. Confirm Umbrel Bitcoin Core RPC is reachable.
3. Start Qbit Core.
4. Wait until Qbit Core RPC is healthy.
5. Confirm Qbit network and sync state.
6. Start the required AuxPoW bridge/coordinator services.
7. Start AuxPoW Stratum.
8. Confirm the Stratum listener is reachable.
9. Allow external miners to connect.
10. Display live state in the dashboard.
```

Services must not report themselves as ready solely because their containers are
running.

Readiness should be based on successful RPC, sync, template, and listener
checks.

---

# Required Connection Mapping

The upstream Bitcoin environment variables must be mapped to Umbrel exports:

```yaml
BITCOIN_RPC_HOST: ${APP_BITCOIN_NODE_IP}
BITCOIN_RPC_PORT: ${APP_BITCOIN_RPC_PORT}
BITCOIN_RPC_USER: ${APP_BITCOIN_RPC_USER}
BITCOIN_RPC_PASSWORD: ${APP_BITCOIN_RPC_PASS}
```

The Qbit services should continue to use the internal service name:

```yaml
QBIT_RPC_HOST: qbitd
```

---

# Persistent Data

Initial persistent paths:

```text
${APP_DATA_DIR}/data/qbit
${APP_DATA_DIR}/data/config
${APP_DATA_DIR}/data/logs
```

The existing Umbrel Bitcoin blockchain remains owned by the Bitcoin app and
must not be duplicated inside QBitLeap.

---

# Open Implementation Questions

The following items must be verified through upstream code and live testing
before finalizing the Umbrel Compose stack:

1. Whether `auxpow-bridge` must run alongside `auxpow-stratum`.
2. Whether `auxpow-coordinator` is required outside upstream regression tests.
3. Which production-ready multi-architecture images are officially published.
4. The final Qbit mainnet RPC and P2P parameters.
5. Required Bitcoin Core RPC capabilities and wallet requirements.
6. Whether Bitcoin Core must expose `getblocktemplate` rules or settings not
   enabled by default in Umbrel.
7. How BTC and QBT payout addresses are supplied without wallet custody.
8. How rented-hash providers authenticate against the Stratum endpoint.
9. Which telemetry can be read directly from `auxpow-stratum`.
10. Whether PRISM is required for reliable production share accounting.

No assumptions about these points should be encoded into the Umbrel wrapper
until verified.
