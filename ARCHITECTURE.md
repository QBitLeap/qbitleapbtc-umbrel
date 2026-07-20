# QBitLeap BTC Architecture

## Vision

QBitLeap BTC is an Umbrel application that enables a single source of SHA256d
hashpower to mine both Bitcoin (BTC) and Qbit (QBT) simultaneously wherever the
protocols support merge mining (AuxPoW).

The project is designed for:

- Local ASIC miners
- Home mining farms
- Rented hashpower (NiceHash, MiningRigRentals, etc.)
- Future multi-miner deployments

The objective is to maximize utilization of the same SHA256d work rather than
requiring independent mining infrastructure for each blockchain.

---

# Core Principles

## 1. Umbrel First

QBitLeap is built specifically for Umbrel.

It should integrate naturally with existing Umbrel applications rather than
duplicating them.

Existing Umbrel services should always be reused whenever practical.

---

## 2. Preserve Upstream

The official Qbit bootstrap project remains the canonical upstream source.

```
qbitleap-qbitleapbtc/
└── upstream/
    └── qbit-mining-bootstrap/
```

Files inside the upstream subtree should never be modified directly.

Umbrel-specific functionality belongs outside the subtree.

---

## 3. Thin Wrapper

The Umbrel application should act primarily as a wrapper around the official
bootstrap.

Responsibilities include:

- configuration
- orchestration
- dependency wiring
- persistent storage
- dashboard
- user settings
- monitoring

The wrapper should avoid replacing upstream logic whenever possible.

---

## 4. One Working Milestone At A Time

Development follows small, testable milestones.

Each commit should represent one logical improvement.

Every commit should leave the application in a working state.

---

# Long-Term Architecture

```
                    SHA256d Hashpower
              (ASICs / NiceHash / MRR)
                         │
                         ▼
                  QBitLeap Stratum
                         │
                  Job Distribution
                         │
        ┌────────────────┴────────────────┐
        │                                 │
        ▼                                 ▼
 Existing Umbrel                    Qbit Core
 Bitcoin Core                         (qbitd)
    (bitcoind)                           │
        │                                │
        └──────────────┬─────────────────┘
                       │
                 AuxPoW Coordinator
                       │
                  AuxPoW Bridge
                       │
                Block Construction
                       │
                 Share Submission
                       │
              BTC + QBT Block Rewards
```

---

# Umbrel Components

## Existing Umbrel Bitcoin App

Provided by Umbrel.

Responsibilities:

- Bitcoin blockchain
- Bitcoin RPC
- Bitcoin peer network
- Wallet (optional)
- Existing Bitcoin data

QBitLeap should connect to this node rather than launching another Bitcoin Core
container.

---

## Qbit Core

Responsibilities:

- Maintain Qbit blockchain
- Validate Qbit blocks
- JSON-RPC
- AuxPoW validation

Persistent storage belongs inside:

```
${APP_DATA_DIR}/data/qbit
```

---

## AuxPoW Coordinator

Responsibilities:

- Coordinate merge-mining workflow
- Build AuxPoW work
- Coordinate Bitcoin and Qbit templates

---

## AuxPoW Bridge

Responsibilities:

- Exchange information between Bitcoin Core and Qbit Core
- Construct AuxPoW payloads
- Submit valid merged work

---

## CKPool

Responsibilities:

- Provide Stratum server
- Accept miner connections
- Deliver work
- Submit completed shares

Primary Stratum port:

```
3333
```

---

## Dashboard

Responsibilities:

- Display live service status
- Connected miners
- Current hashrate
- Accepted shares
- Rejected shares
- Best share
- Latest Bitcoin block
- Latest Qbit block
- Sync status
- Mining status
- AuxPoW status
- Logs

Dashboard state should always reflect actual running services rather than stored
configuration values.

---

# External Hashpower

Supported sources include:

- ASIC miners
- NiceHash
- MiningRigRentals
- Other SHA256d-compatible miners

No mining software should be required inside Umbrel.

Umbrel acts as the coordinator.

---

# Repository Layout

```
qbitleapbtc-umbrel/

├── README.md
├── LICENSE
├── ARCHITECTURE.md
├── umbrel-app-store.yml
│
└── qbitleap-qbitleapbtc/
    ├── umbrel-app.yml
    ├── docker-compose.yml
    ├── web/
    ├── services/
    ├── dashboard/
    ├── scripts/
    ├── config/
    └── upstream/
        └── qbit-mining-bootstrap/
```

---

# Design Goals

The application should:

- reuse Umbrel Bitcoin Core
- avoid duplicate Bitcoin blockchains
- preserve upstream code
- survive upgrades without data loss
- preserve configuration
- support local and rented hashpower
- support merge mining
- expose clear diagnostics
- minimize manual configuration
- remain easy to maintain

---

# Development Roadmap

## Phase 1

- Umbrel application shell
- Dashboard
- Settings
- Persistent storage

---

## Phase 2

- Connect to Umbrel Bitcoin Core

---

## Phase 3

- Launch Qbit Core

---

## Phase 4

- Integrate AuxPoW services

---

## Phase 5

- Launch CKPool
- External miner connectivity

---

## Phase 6

- Dashboard telemetry
- Miner management
- Diagnostics

---

## Phase 7

- Advanced monitoring
- Performance tuning
- Additional mining features

---

# Guiding Philosophy

QBitLeap should feel like a native Umbrel application.

The official Qbit bootstrap should remain the upstream implementation.

The Umbrel wrapper should provide an intuitive user experience while keeping the
underlying mining stack as close as practical to the official upstream project.
