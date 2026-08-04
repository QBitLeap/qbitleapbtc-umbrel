# Architecture

## Overview

QBitLeap BTC is a minimal Umbrel Community App that runs a complete self-hosted Bitcoin AuxPoW mining stack for Qbit.

```
SHA-256 ASIC Miners
        │
        ▼
AuxPoW Stratum Server
        │
        ├── Bitcoin Core
        └── Qbit Core
                │
                ▼
        Qbit AuxPoW Submission
```

## Components

- Bitcoin Core: Parent chain node.
- Qbit Core: AuxPoW child chain node.
- AuxPoW Stratum: Accepts standard SHA-256 miners and coordinates merge mining.
- Dashboard: Displays miner health and block history.

## Dashboard Philosophy

The dashboard is intentionally minimal.

Primary health indicators:

- Service status
- Connected miners
- Current hashrate
- Rejected share percentage
- Last share received
- Bitcoin blocks found
- Qbit blocks found

Avoid adding telemetry that does not help diagnose miner or service health.

## Supported Miners

Any SHA-256 Stratum-compatible miner, including ASICs and NiceHash.

## Repository Philosophy

Keep the codebase small, readable, and modular. Prefer incremental improvements over feature expansion.
