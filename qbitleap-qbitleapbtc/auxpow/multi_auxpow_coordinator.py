#!/usr/bin/env python3
"""QBitLeap multi-child AuxPoW extension for Qbit and Fractal Bitcoin.

The upstream Qbit coordinator remains unmodified.  This module subclasses its
Stratum server and switches to a standard, shared AuxPoW Merkle tree whenever
the configured Fractal node is fully synchronized and able to provide work.
If Fractal is unavailable or still in initial block download, Qbit/Bitcoin
mining continues with the upstream single-child path.
"""

from __future__ import annotations

import copy
import io
import os
import time
from dataclasses import dataclass, field, replace
from decimal import Decimal

from lab.auxpow import auxpow_coordinator as base


FRACTAL_CHAIN_ID = int(os.environ.get("FRACTAL_AUXPOW_CHAIN_ID", "8228"))
QBIT_CHAIN_ID = int(os.environ.get("QBIT_AUXPOW_CHAIN_ID", "47"))
MERKLE_SIZE = int(os.environ.get("AUXPOW_MERKLE_SIZE", "16"))
MERKLE_NONCE = int(os.environ.get("AUXPOW_MERKLE_NONCE", "0"))
FRACTAL_EXPECTED_GENESIS_HASH = os.environ.get("FRACTAL_EXPECTED_GENESIS_HASH", "").strip()


def merkle_height(size: int) -> int:
    if size <= 0 or size & (size - 1):
        raise RuntimeError("AUXPOW_MERKLE_SIZE must be a positive power of two")
    return size.bit_length() - 1


def merkle_parent(left: int, right: int) -> int:
    return base.uint256_from_str(base.hash256(base.ser_uint256(left) + base.ser_uint256(right)))


def build_aux_merkle_tree(
    templates: dict[str, dict[str, object]],
    *,
    size: int = MERKLE_SIZE,
    nonce: int = MERKLE_NONCE,
) -> tuple[int, dict[str, int], dict[str, tuple[int, ...]]]:
    """Return the shared root, deterministic indices, and per-chain branches."""
    height = merkle_height(size)
    leaves = [0] * size
    indices: dict[str, int] = {}
    occupied: dict[int, str] = {}
    for name, template in templates.items():
        chain_id = int(template["chainid"])
        index = base.get_expected_index(nonce=nonce, chain_id=chain_id, merkle_height=height)
        if index in occupied:
            raise RuntimeError(
                f"AuxPoW slot collision: {name} and {occupied[index]} both map to slot {index}"
            )
        occupied[index] = name
        indices[name] = index
        leaves[index] = int(str(template["hash"]), 16)

    levels = [leaves]
    while len(levels[-1]) > 1:
        current = levels[-1]
        levels.append([merkle_parent(current[i], current[i + 1]) for i in range(0, len(current), 2)])

    branches: dict[str, tuple[int, ...]] = {}
    for name, leaf_index in indices.items():
        index = leaf_index
        branch: list[int] = []
        for level in levels[:-1]:
            branch.append(level[index ^ 1])
            index //= 2
        branches[name] = tuple(branch)
        computed = base.check_merkle_branch(
            leaf=int(str(templates[name]["hash"]), 16),
            branch=list(branch),
            index=leaf_index,
        )
        if computed != levels[-1][0]:
            raise RuntimeError(f"failed to construct AuxPoW branch for {name}")
    return levels[-1][0], indices, branches


def shared_commitment(
    templates: dict[str, dict[str, object]],
    *,
    size: int = MERKLE_SIZE,
    nonce: int = MERKLE_NONCE,
) -> tuple[bytes, dict[str, int], dict[str, tuple[int, ...]]]:
    qbit_order = base.auxpow_commitment_order(templates["qbit"])
    if qbit_order != "display":
        raise RuntimeError(
            "Qbit must advertise createauxblock.commitmentorder=display before Fractal can share its AuxPoW tree"
        )
    root, indices, branches = build_aux_merkle_tree(templates, size=size, nonce=nonce)
    commitment = (
        base.MERGED_MINING_HEADER
        + base.ser_uint256(root)[::-1]
        + size.to_bytes(4, "little")
        + nonce.to_bytes(4, "little")
    )
    return commitment, indices, branches


def build_parent_with_commitment(
    *,
    btc_template: dict[str, object],
    bitcoin_script_pubkey_hex: str,
    commitment: bytes,
    extranonce_prefix: bytes = b"",
    extranonce_suffix: bytes = b"",
    parent_time: int | None = None,
    header_nonce: int = 0,
):
    coinbase = base.build_parent_coinbase(
        height=int(btc_template["height"]),
        coinbase_value=int(btc_template["coinbasevalue"]),
        script_pubkey_hex=bitcoin_script_pubkey_hex,
        commitment=commitment,
        extranonce_prefix=extranonce_prefix,
        extranonce_suffix=extranonce_suffix,
    )
    ntime = int(btc_template["curtime"]) if parent_time is None else parent_time
    block = base.create_block(
        hashprev=int(str(btc_template["previousblockhash"]), 16),
        coinbase=coinbase,
        ntime=ntime,
        version=int(btc_template["version"]),
        tmpl={
            "previousblockhash": btc_template["previousblockhash"],
            "bits": btc_template["bits"],
            "height": btc_template["height"],
            "curtime": ntime,
        },
        txlist=[str(tx["data"]) for tx in btc_template.get("transactions", [])],
    )
    if btc_template.get("default_witness_commitment"):
        base.add_witness_commitment(block)
    block.nBits = int(str(btc_template["bits"]), 16)
    block.nNonce = header_nonce
    return block


@dataclass
class MultiAuxPowJob(base.AuxPowStratumJob):
    fractal_template: dict[str, object] | None = None
    fractal_target: int = 0
    aux_target: int = 0
    chain_indices: dict[str, int] = field(default_factory=dict)
    chain_branches: dict[str, tuple[int, ...]] = field(default_factory=dict)
    shared_commitment: bytes = b""


class MultiAuxPowStratumServer(base.AuxPowStratumServer):
    def __init__(self, *, fractal_rpc: base.JsonRpc, fractal_miner_address: str, **kwargs):
        super().__init__(**kwargs)
        self.fractal_rpc = fractal_rpc
        self.fractal_miner_address = fractal_miner_address
        self.fractal_address_validated = False
        self.fractal_active = False
        self.last_fractal_status = "starting"

    def fractal_ready(self) -> tuple[bool, str | None]:
        try:
            info = self.fractal_rpc.call("getblockchaininfo")
            if not isinstance(info, dict) or info.get("chain") != "main":
                raise RuntimeError("Fractal RPC is not on mainnet")
            if bool(info.get("initialblockdownload", True)):
                self.last_fractal_status = f"syncing ({float(info.get('verificationprogress', 0)) * 100:.2f}%)"
                return False, None
            if not self.fractal_address_validated:
                validated = base.resolve_validated_address(
                    self.fractal_rpc,
                    self.fractal_miner_address,
                    field_name="FRACTAL_MINER_ADDRESS",
                )
                self.fractal_miner_address = validated.address
                self.fractal_address_validated = True
            best = str(info.get("bestblockhash") or self.fractal_rpc.call("getbestblockhash"))
            if FRACTAL_EXPECTED_GENESIS_HASH:
                genesis = str(self.fractal_rpc.call("getblockhash", [0]))
                if genesis != FRACTAL_EXPECTED_GENESIS_HASH:
                    raise RuntimeError("Fractal genesis hash does not match FRACTAL_EXPECTED_GENESIS_HASH")
            self.last_fractal_status = "ready"
            return True, best
        except Exception as exc:
            self.last_fractal_status = f"unavailable ({exc})"
            return False, None

    def refresh_job(self, *, force: bool) -> bool:
        ready, fractal_best = self.fractal_ready()
        mode_changed = ready != self.fractal_active
        if not ready:
            if mode_changed or force:
                print(
                    f"auxpow stratum: Fractal inactive; continuing Bitcoin + Qbit ({self.last_fractal_status})",
                    flush=True,
                )
            self.fractal_active = False
            return super().refresh_job(force=force or mode_changed)

        parent_template_age_expired = self.invalidate_expired_parent_work()
        qbit_best = str(self.qbit_rpc.call("getbestblockhash"))
        bitcoin_best = str(self.bitcoin_rpc.call("getbestblockhash"))
        snapshot = (qbit_best, bitcoin_best, str(fractal_best))
        now = time.monotonic()
        with self.lock:
            current_job = self.current_job
            tip_snapshot = self.tip_snapshot
            job_age_expired = self.job_age_expired(current_job, now)
            if self.parent_template_age_expired(current_job):
                self.current_job = None
                self.jobs = {}
                current_job = None
                parent_template_age_expired = True
        if (
            not force
            and not mode_changed
            and current_job is not None
            and snapshot == tip_snapshot
            and not job_age_expired
            and not parent_template_age_expired
        ):
            return False

        try:
            qbit_template = self.qbit_rpc.call("createauxblock", [self.qbit_miner_address])
            fractal_template = self.fractal_rpc.call("createauxblock", [self.fractal_miner_address])
            if int(qbit_template["chainid"]) != QBIT_CHAIN_ID:
                raise RuntimeError(f"unexpected Qbit AuxPoW chain ID {qbit_template['chainid']}")
            if int(fractal_template["chainid"]) != FRACTAL_CHAIN_ID:
                raise RuntimeError(f"unexpected Fractal AuxPoW chain ID {fractal_template['chainid']}")
            btc_template = self.bitcoin_rpc.call("getblocktemplate", [{"rules": ["segwit"]}])
            base.validate_bitcoin_parent_template(
                btc_template,
                max_age_seconds=base.AUXPOW_TEMPLATE_MAX_AGE_SECONDS,
                max_future_seconds=base.AUXPOW_TEMPLATE_MAX_FUTURE_SECONDS,
            )
            job = self.make_multi_job(
                job_id=self.next_job_id(),
                qbit_template=qbit_template,
                fractal_template=fractal_template,
                btc_template=btc_template,
                desired_share_difficulty=self.fixed_share_difficulty,
            )
        except Exception as exc:
            print(f"auxpow stratum: Fractal work unavailable; using Qbit-only job: {exc}", flush=True)
            self.fractal_active = False
            return super().refresh_job(force=True)

        with self.lock:
            self.tip_snapshot = snapshot
            self.current_job = job
            self.jobs = {} if self.vardiff_config.enabled else {job.job_id: job}
        self.fractal_active = True
        print(
            "auxpow stratum: new multi-chain job "
            f"{job.job_id} qbit_height={qbit_template['height']} "
            f"fractal_height={fractal_template['height']} bitcoin_height={btc_template['height']} "
            f"qbit_slot={job.chain_indices['qbit']} fractal_slot={job.chain_indices['fractal']} "
            f"tree_size={MERKLE_SIZE} nonce={MERKLE_NONCE}",
            flush=True,
        )
        self.log_event(
            "job",
            job_id=job.job_id,
            mode="qbit+fractal",
            qbit_height=qbit_template["height"],
            fractal_height=fractal_template["height"],
            bitcoin_height=btc_template["height"],
            qbit_slot=job.chain_indices["qbit"],
            fractal_slot=job.chain_indices["fractal"],
            merkle_size=MERKLE_SIZE,
            merkle_nonce=MERKLE_NONCE,
        )
        self.broadcast_job(job)
        return True

    def make_multi_job(
        self,
        *,
        job_id: str,
        qbit_template: dict[str, object],
        fractal_template: dict[str, object],
        btc_template: dict[str, object],
        desired_share_difficulty: Decimal,
    ) -> MultiAuxPowJob:
        templates = {"qbit": qbit_template, "fractal": fractal_template}
        commitment, indices, branches = shared_commitment(templates)
        qbit_target = base.compact_target(int(str(qbit_template["bits"]), 16))
        fractal_target = base.compact_target(int(str(fractal_template["bits"]), 16))
        aux_target = max(qbit_target, fractal_target)
        effective_share_target = self.effective_share_target(desired_share_difficulty, aux_target)
        parent_block = build_parent_with_commitment(
            btc_template=btc_template,
            bitcoin_script_pubkey_hex=self.bitcoin_miner_address.script_pubkey_hex,
            commitment=commitment,
            extranonce_prefix=base.AUXPOW_PLACEHOLDER_BYTES,
        )
        coinbase_bytes = parent_block.vtx[0].serialize_without_witness()
        marker = base.AUXPOW_PLACEHOLDER_BYTES
        marker_index = coinbase_bytes.find(marker)
        if marker_index == -1 or coinbase_bytes.find(marker, marker_index + 1) != -1:
            raise RuntimeError("failed to split multi-chain coinbase into Stratum coinb1/coinb2")
        return MultiAuxPowJob(
            job_id=job_id,
            aux_template=qbit_template,
            btc_template=btc_template,
            bitcoin_script_pubkey_hex=self.bitcoin_miner_address.script_pubkey_hex,
            chain_nonce=MERKLE_NONCE,
            chain_index=indices["qbit"],
            share_target=effective_share_target,
            qbit_target=qbit_target,
            parent_target=base.compact_target(int(str(btc_template["bits"]), 16)),
            share_difficulty=base.target_difficulty(effective_share_target),
            coinbase_merkle_branch=base.build_coinbase_merkle_branch(parent_block),
            prevhash=base.stratum_codec.stratum_prevhash_from_display_hash(str(btc_template["previousblockhash"])),
            coinb1=coinbase_bytes[:marker_index].hex(),
            coinb2=coinbase_bytes[marker_index + len(marker):].hex(),
            version=f"{int(btc_template['version']) & 0xFFFFFFFF:08x}",
            nbits=str(btc_template["bits"]),
            ntime=f"{int(btc_template['curtime']) & 0xFFFFFFFF:08x}",
            fractal_template=fractal_template,
            fractal_target=fractal_target,
            aux_target=aux_target,
            chain_indices=indices,
            chain_branches=branches,
            shared_commitment=commitment,
        )

    def job_for_client(self, client, base_job, *, clean_jobs: bool):
        if not isinstance(base_job, MultiAuxPowJob) or not self.vardiff_config.enabled:
            return super().job_for_client(client, base_job, clean_jobs=clean_jobs)
        desired = client.pending_share_difficulty or client.share_difficulty
        target = self.effective_share_target(desired, base_job.aux_target)
        job = replace(
            base_job,
            job_id=self.next_job_id(),
            share_target=target,
            share_difficulty=base.target_difficulty(target),
            clean_jobs=clean_jobs,
        )
        with self.lock:
            self.jobs[job.job_id] = job
        return job

    def build_multi_submission(self, job: MultiAuxPowJob, coinbase_bytes: bytes, header_bytes: bytes):
        coinbase = base.CTransaction()
        coinbase.deserialize(io.BytesIO(coinbase_bytes))
        header = base.CBlockHeader()
        header.deserialize(io.BytesIO(header_bytes))
        parent_block = build_parent_with_commitment(
            btc_template=job.btc_template,
            bitcoin_script_pubkey_hex=job.bitcoin_script_pubkey_hex,
            commitment=job.shared_commitment,
            parent_time=header.nTime,
            header_nonce=header.nNonce,
        )
        coinbase.wit = copy.deepcopy(parent_block.vtx[0].wit)
        parent_block.vtx[0] = coinbase
        parent_block.nVersion = header.nVersion
        parent_block.hashPrevBlock = header.hashPrevBlock
        parent_block.hashMerkleRoot = header.hashMerkleRoot
        parent_block.nTime = header.nTime
        parent_block.nBits = header.nBits
        parent_block.nNonce = header.nNonce
        payloads = {}
        for name in ("qbit", "fractal"):
            payloads[name] = base.AuxPowPayload(
                coinbase_tx=coinbase,
                coinbase_merkle_branch=list(job.coinbase_merkle_branch),
                coinbase_branch_index=0,
                chain_merkle_branch=list(job.chain_branches[name]),
                chain_index=job.chain_indices[name],
                parent_block=base.CBlockHeader(parent_block),
            )
        return payloads, parent_block

    def handle_submit(self, client, params: list[object]) -> None:
        job_id = str(params[1]) if len(params) > 1 else ""
        with self.lock:
            job = self.jobs.get(job_id)
        if not isinstance(job, MultiAuxPowJob):
            return super().handle_submit(client, params)
        if len(params) < 5:
            raise base.StratumError(20, "submit params are incomplete")
        _, _, extranonce2_hex, ntime_hex, nonce_hex = [str(item) for item in params[:5]]
        version_bits_hex = str(params[5]) if len(params) > 5 else None
        if len(extranonce2_hex) != base.AUXPOW_STRATUM_EXTRANONCE2_SIZE * 2:
            raise base.StratumError(20, "unexpected extranonce2 size")
        if len(ntime_hex) != 8 or len(nonce_hex) != 8:
            raise base.StratumError(20, "ntime and nonce must be 4-byte hex strings")
        if version_bits_hex is not None and client.version_mask == 0:
            raise base.StratumError(20, "version_bits provided without version-rolling negotiation")
        if version_bits_hex is None and client.version_mask != 0:
            raise base.StratumError(20, "version_bits required after version-rolling negotiation")
        if self.parent_template_age_expired(job):
            raise base.StratumError(21, "stale job")
        if self.vardiff_config.enabled and job_id not in client.active_job_ids:
            raise base.StratumError(21, "stale job")

        worker = self.worker_key(client)
        client.last_submit_monotonic = time.monotonic()
        self.record_stats(worker, "submitted")
        self.note_vardiff_submitted_share(client)
        try:
            version_hex = base.stratum_codec.apply_version_bits(job.version, version_bits_hex, client.version_mask)
            coinbase_bytes, header_bytes = base.assemble_header(
                job,
                client.extranonce1_hex,
                extranonce2_hex,
                nonce_hex,
                ntime_hex=ntime_hex,
                version_hex=version_hex,
            )
        except ValueError as exc:
            raise base.StratumError(20, str(exc)) from exc

        share_key = ("header", worker, header_bytes.hex())
        with self.lock:
            if share_key in self.recent_share_keys:
                self.worker_stats[worker].duplicate += 1
                raise base.StratumError(22, "duplicate share")
            if len(self.recent_share_keys) > 50000:
                self.recent_share_keys.clear()
            self.recent_share_keys.add(share_key)

        candidate_hash = base.header_hash_int(header_bytes)
        share_pass = candidate_hash <= job.share_target
        qbit_pass = candidate_hash <= job.qbit_target
        fractal_pass = candidate_hash <= job.fractal_target
        parent_pass = candidate_hash <= job.parent_target
        if not share_pass and not qbit_pass and not fractal_pass:
            self.record_stats(worker, "low_difficulty")
            raise base.StratumError(23, "low difficulty share")
        if share_pass:
            self.record_stats(worker, "accepted")
            self.note_vardiff_accepted_share(client, job, worker)
            print(
                f"auxpow stratum: accepted share user={client.username or '-'} "
                f"job={job.job_id} variant=canonical hash={candidate_hash:064x}",
                flush=True,
            )
        elif qbit_pass or fractal_pass:
            print(
                f"auxpow stratum: child block candidate user={client.username or '-'} "
                f"job={job.job_id} hash={candidate_hash:064x}",
                flush=True,
            )

        if not qbit_pass and not fractal_pass:
            return
        payloads, parent_block = self.build_multi_submission(job, coinbase_bytes, header_bytes)
        accepted_child = False
        if qbit_pass:
            self.record_stats(worker, "qbit_candidates")
            try:
                result = self.qbit_rpc.call("submitauxblock", [job.aux_template["hash"], payloads["qbit"].to_hex()])
                if result is None:
                    accepted_child = True
                    self.record_stats(worker, "qbit_accepted")
                    print(f"auxpow stratum: qbit accepted AuxPoW block via canonical user={worker}", flush=True)
                else:
                    print(f"auxpow stratum: qbit rejected AuxPoW block result={result!r}", flush=True)
            except Exception as exc:
                print(f"auxpow stratum: qbit AuxPoW submission failed: {exc}", flush=True)
            self.refresh_now.set()
        if fractal_pass and job.fractal_template is not None:
            try:
                result = self.fractal_rpc.call(
                    "submitauxblock",
                    [job.fractal_template["hash"], payloads["fractal"].to_hex()],
                )
                if result is True:
                    accepted_child = True
                    print(f"auxpow stratum: fractal accepted AuxPoW block via canonical user={worker}", flush=True)
                else:
                    print(f"auxpow stratum: fractal rejected AuxPoW block result={result!r}", flush=True)
            except Exception as exc:
                print(f"auxpow stratum: fractal AuxPoW submission failed: {exc}", flush=True)
            self.refresh_now.set()
        if parent_pass:
            self.record_stats(worker, "parent_submitted")
            try:
                result = self.bitcoin_rpc.call("submitblock", [parent_block.serialize().hex()])
                if result in (None, "duplicate", "inconclusive"):
                    self.record_stats(worker, "parent_accepted")
                    print(
                        f"auxpow stratum: parent submit accepted after child evaluation job={job.job_id} result={result!r}",
                        flush=True,
                    )
                else:
                    print(f"auxpow stratum: parent submit rejected job={job.job_id} result={result!r}", flush=True)
            except Exception as exc:
                print(f"auxpow stratum: Bitcoin block submission failed: {exc}", flush=True)
        elif accepted_child:
            print(
                f"auxpow stratum: child accepted; share missed Bitcoin target job={job.job_id}",
                flush=True,
            )


def main() -> int:
    qbit_rpc = base.JsonRpc(
        host=base.env("QBIT_RPC_HOST"),
        port=int(base.env("QBIT_RPC_PORT")),
        user=base.env("QBIT_RPC_USER"),
        password=base.env("QBIT_RPC_PASSWORD"),
    )
    bitcoin_rpc = base.JsonRpc(
        host=base.env("BITCOIN_RPC_HOST"),
        port=int(base.env("BITCOIN_RPC_PORT")),
        user=base.env("BITCOIN_RPC_USER"),
        password=base.env("BITCOIN_RPC_PASSWORD"),
    )
    fractal_rpc = base.JsonRpc(
        host=base.env("FRACTAL_RPC_HOST"),
        port=int(base.env("FRACTAL_RPC_PORT")),
        user=base.env("FRACTAL_RPC_USER"),
        password=base.env("FRACTAL_RPC_PASSWORD"),
    )
    for rpc in (qbit_rpc, bitcoin_rpc):
        base.wait_for_rpc(rpc)
    base.validate_auxpow_startup(qbit_rpc, bitcoin_rpc)
    qbit_address = base.resolve_qbit_miner_address(qbit_rpc)
    bitcoin_address = base.resolve_bitcoin_miner_address(bitcoin_rpc)
    fractal_address = base.env("FRACTAL_MINER_ADDRESS").strip()
    if not fractal_address:
        raise RuntimeError("FRACTAL_MINER_ADDRESS is empty")
    print(f"auxpow: using qbit payout address {qbit_address}", flush=True)
    print(f"auxpow: using Bitcoin payout address {bitcoin_address.address}", flush=True)
    print(f"auxpow: configured Fractal payout address {fractal_address}", flush=True)
    return MultiAuxPowStratumServer(
        qbit_rpc=qbit_rpc,
        bitcoin_rpc=bitcoin_rpc,
        fractal_rpc=fractal_rpc,
        qbit_miner_address=qbit_address,
        bitcoin_miner_address=bitcoin_address,
        fractal_miner_address=fractal_address,
    ).serve()


if __name__ == "__main__":
    raise SystemExit(main())
