# QBitLeap BTC

A minimal Umbrel Community App for self-hosted Bitcoin AuxPoW mining on the Qbit and Fractal Bitcoin networks.

## Purpose

QBitLeap BTC turns an Umbrel node into a lightweight AuxPoW mining appliance. Connect one or more SHA-256 ASIC miners, point them at the built-in Stratum server, and merge-mine Qbit and Fractal Bitcoin using the same Bitcoin proof of work.

Version 0.5.0 requires the QBitLeap Fractal Bitcoin Node app. While that node is synchronizing or temporarily unavailable, the coordinator automatically continues Bitcoin + Qbit mining. Fractal is added to new jobs once its node reports that initial block download is complete.

The project intentionally favors simplicity over feature count.

## Core Principles

- Minimal dashboard
- Local-first operation
- Self-hosted
- Open source
- No custodial wallet
- No unnecessary telemetry

The dashboard exists to answer one question:

> Are my miners connected and mining correctly?

## Features

- Bitcoin Core integration
- Qbit Core integration
- Fractal Bitcoin Core integration
- AuxPoW Stratum server
- Local ASIC miner support
- NiceHash-compatible Stratum endpoint
- Multiple miner support
- Persistent payout addresses
- Bitcoin block history
- Qbit block history
- Fractal Bitcoin block history

## Dashboard

Displays only operational health:

- Mining Services
- Connected Miners
- Total Hashrate
- Rejected Share Rate
- Last Share Received
- Bitcoin Blocks Found
- Qbit Blocks Found
- Fractal Blocks Found

## Development Goal

Keep QBitLeap simple. Every feature should directly improve reliability, usability, or mining operations.
