import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import permissionless_telemetry as telemetry


def tally(accepted=0, accepted_diff=0, stale=0, blocks=0):
    return {
        "accepted": {"count": accepted, "diff": accepted_diff},
        "stale": {"count": stale, "diff": stale},
        "block_accepted": {"count": blocks, "diff": blocks},
    }


class PermissionlessTelemetryTests(unittest.TestCase):
    def test_calculates_hashrate_rejects_and_last_share(self):
        state = {
            "runtime": 60,
            "lastupdate": 1000,
            "workers": {
                "qb1address.thor-p2": {
                    "accepted": 2,
                    "accepted_diff": 2048,
                    "rejected": 0,
                    "last_share_at": 1000,
                    "hashrate_hs": 1,
                    "blocks": 0,
                }
            },
        }
        worker = {"workername": "qb1address.thor-p2", **tally(accepted=4, accepted_diff=4096, stale=1)}
        pool = tally(accepted=4, accepted_diff=4096, stale=1)
        result, next_state = telemetry.snapshot(
            {"runtime": 120, "lastupdate": 1060, "workers": [worker], "pool": pool},
            state,
            now=1060,
        )
        self.assertEqual(result["accepted_shares"], 4)
        self.assertEqual(result["rejected_shares"], 1)
        self.assertEqual(result["workers"][0]["name"], "thor-p2")
        self.assertEqual(result["workers"][0]["last_share_at"], 1060)
        self.assertAlmostEqual(result["current_hashrate_hs"], 2048 * 2**32 / 60)
        self.assertEqual(next_state["workers"]["qb1address.thor-p2"]["rejected"], 1)

    def test_persists_new_qbit_block_with_worker_and_height(self):
        worker = {"workername": "qb1address.rig-1", **tally(accepted=1, accepted_diff=1024, blocks=1)}
        with patch.object(telemetry, "block_height", return_value=68099):
            result, state = telemetry.snapshot(
                {"runtime": 60, "lastupdate": 2000, "workers": [worker], "pool": tally(blocks=1)},
                {},
                now=2000,
            )
        block = result["block_history"]["qbit"][0]
        self.assertEqual(block, {"found_at": 2000, "height": 68099, "worker": "rig-1"})
        self.assertEqual(state["block_history"], [block])

    def test_does_not_duplicate_an_already_recorded_block(self):
        worker = {"workername": "qb1address.rig-1", **tally(accepted=2, accepted_diff=2048, blocks=1)}
        state = {
            "runtime": 60,
            "lastupdate": 2000,
            "workers": {"qb1address.rig-1": {"accepted": 1, "accepted_diff": 1024, "rejected": 0, "blocks": 1}},
            "block_history": [{"found_at": 1900, "height": 68098, "worker": "rig-1"}],
        }
        result, _ = telemetry.snapshot(
            {"runtime": 120, "lastupdate": 2060, "workers": [worker], "pool": tally(accepted=2, accepted_diff=2048, blocks=1)},
            state,
            now=2060,
        )
        self.assertEqual(len(result["block_history"]["qbit"]), 1)

    def test_worker_confirmation_replaces_generic_chain_scan_record(self):
        worker = {"workername": "qb1address.thor-p2", **tally(accepted=2, accepted_diff=2048, blocks=1)}
        state = {
            "runtime": 60,
            "lastupdate": 2000,
            "workers": {"qb1address.thor-p2": {"accepted": 1, "accepted_diff": 1024, "rejected": 0, "blocks": 0}},
            "block_history": [{
                "found_at": 1990,
                "height": 69932,
                "worker": "permissionless miner",
                "block_hash": "qbit-block-hash",
            }],
        }
        with patch.object(telemetry, "block_height", return_value=69932):
            result, next_state = telemetry.snapshot(
                {"runtime": 120, "lastupdate": 2010, "workers": [worker], "pool": tally(accepted=2, accepted_diff=2048, blocks=1)},
                state,
                now=2010,
            )

        self.assertEqual(len(result["block_history"]["qbit"]), 1)
        self.assertEqual(result["block_history"]["qbit"][0], {
            "found_at": 2010,
            "height": 69932,
            "worker": "thor-p2",
            "block_hash": "qbit-block-hash",
        })
        self.assertEqual(next_state["block_history"], result["block_history"]["qbit"])

    def test_reconciles_existing_duplicate_records(self):
        history = [
            {"found_at": 2010, "height": 69932, "worker": "thor-p2"},
            {"found_at": 1990, "height": 69932, "worker": "permissionless miner", "block_hash": "qbit-block-hash"},
        ]

        self.assertEqual(telemetry.reconcile_block_history(history), [{
            "found_at": 2010,
            "height": 69932,
            "worker": "thor-p2",
            "block_hash": "qbit-block-hash",
        }])

    def test_first_snapshot_uses_runtime_for_immediate_hashrate(self):
        worker = {"workername": "qb1address.rig-1", **tally(accepted=60, accepted_diff=60_000)}
        result, _ = telemetry.snapshot(
            {"runtime": 60, "lastupdate": 2000, "workers": [worker], "pool": tally(accepted=60, accepted_diff=60_000)},
            {},
            now=2000,
        )
        self.assertAlmostEqual(result["current_hashrate_hs"], 60_000 * 2**32 / 60)

    def test_worker_becomes_inactive_without_new_shares(self):
        worker = {"workername": "qb1address.rig-1", **tally(accepted=1, accepted_diff=1024)}
        state = {
            "runtime": 60,
            "lastupdate": 2000,
            "workers": {"qb1address.rig-1": {"accepted": 1, "accepted_diff": 1024, "rejected": 0, "blocks": 0, "last_share_at": 2000, "hashrate_hs": 10_000}},
        }
        result, _ = telemetry.snapshot(
            {"runtime": 60, "lastupdate": 2000, "workers": [worker], "pool": tally(accepted=1, accepted_diff=1024)},
            state,
            now=2200,
        )
        self.assertFalse(result["workers"][0]["active"])
        self.assertEqual(result["current_hashrate_hs"], 0)

    def test_chain_scan_records_coinbase_paid_to_configured_address(self):
        with tempfile.TemporaryDirectory() as directory:
            address_file = Path(directory) / "qbt.txt"
            address_file.write_text("qb1mine\n", encoding="utf-8")

            def fake_rpc(method, params=None):
                if method == "getblockcount":
                    return 101
                if method == "getblockhash":
                    return f"hash-{params[0]}"
                height = int(params[0].split("-")[1])
                address = "qb1mine" if height == 101 else "qb1other"
                return {"tx": [{"vout": [{"scriptPubKey": {"address": address}}]}]}

            with patch.object(telemetry, "QBT_ADDRESS_FILE", address_file), patch.object(telemetry, "rpc", side_effect=fake_rpc):
                history, height = telemetry.scan_qbit_blocks({"qbit_scan_height": 100}, [], now=3000)

        self.assertEqual(height, 101)
        self.assertEqual(history[0]["height"], 101)
        self.assertEqual(history[0]["block_hash"], "hash-101")


if __name__ == "__main__":
    unittest.main()
