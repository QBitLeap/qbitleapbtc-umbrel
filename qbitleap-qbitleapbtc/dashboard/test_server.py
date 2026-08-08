import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class ExpectedHashrateTests(unittest.TestCase):
    def test_parses_detected_worker_fields_as_hashes_per_second(self):
        self.assertEqual(
            server.parse_expected_fields(["miner.one", "miner.two"], ["10", "40.5"]),
            {
                "miner.one": 10_000_000_000_000,
                "miner.two": 40_500_000_000_000,
            },
        )

    def test_blank_rate_removes_an_expected_hashrate(self):
        self.assertEqual(server.parse_expected_fields(["miner.one"], [""]), {})

    def test_rejects_invalid_expected_hashrates(self):
        for value in ("0", "-1", "not-a-number", "nan", "inf"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                server.parse_expected_fields(["miner.one"], [value])

    def test_reads_persisted_expected_hashrates(self):
        with tempfile.TemporaryDirectory() as directory:
            expected_file = Path(directory) / "expected.json"
            expected_file.write_text('{"miner.one": 10000000000000}\n', encoding="utf-8")
            with patch.object(server, "EXPECTED_FILE", expected_file):
                self.assertEqual(
                    server.read_expected_rates(),
                    {"miner.one": 10_000_000_000_000.0},
                )

    def test_render_prompts_for_detected_worker_without_examples(self):
        telemetry = {
            "workers": [{"name": "garage-miner", "active": True, "last_share_at": 1}],
            "block_history": {},
            "accepted_shares": 0,
            "rejected_shares": 0,
            "current_hashrate_hs": 0,
            "connected_workers": 1,
        }
        with (
            patch.object(server, "read_text", return_value=""),
            patch.object(server, "read_expected_rates", return_value={}),
            patch.object(server, "chain_status", return_value=("ready", 1, 1.0)),
            patch.object(server, "auxpow_connected", return_value=True),
            patch.object(server, "read_telemetry", return_value=telemetry),
        ):
            page = server.render({}).decode("utf-8")

        self.assertIn("garage-miner", page)
        self.assertIn("Expected hashrate needed", page)
        self.assertIn("Fractal Bitcoin Core", page)
        self.assertIn("Fractal BTC Payout Address (optional)", page)
        self.assertIn("Fractal Blocks Found", page)
        self.assertIn('name="expected_rate"', page)
        self.assertNotIn("thor-p2", page)
        self.assertNotIn("magic-40t", page)

    def test_chain_status_distinguishes_ready_syncing_and_offline(self):
        self.assertEqual(
            server.chain_status(lambda _method: {
                "blocks": 248922,
                "initialblockdownload": True,
                "verificationprogress": 0.6655295,
            }),
            ("syncing", 248922, 0.6655295),
        )
        self.assertEqual(
            server.chain_status(lambda _method: {
                "blocks": 2000000,
                "initialblockdownload": False,
                "verificationprogress": 0.999999,
            }),
            ("ready", 2000000, 0.999999),
        )
        self.assertEqual(
            server.chain_status(lambda _method: (_ for _ in ()).throw(OSError("offline"))),
            ("offline", None, None),
        )

    def test_render_marks_a_syncing_fractal_node_as_warning(self):
        statuses = iter([
            ("ready", 100, 1.0),
            ("ready", 200, 1.0),
            ("syncing", 248922, 0.6655295),
        ])
        with (
            patch.object(server, "read_text", return_value=""),
            patch.object(server, "read_expected_rates", return_value={}),
            patch.object(server, "chain_status", side_effect=lambda _rpc: next(statuses)),
            patch.object(server, "auxpow_connected", return_value=True),
            patch.object(server, "read_telemetry", return_value=None),
        ):
            page = server.render({}).decode("utf-8")

        self.assertIn("Fractal Bitcoin Core", page)
        self.assertIn("Synchronizing 66.55% · Block 248,922", page)
        self.assertIn('service-dot warn', page)


if __name__ == "__main__":
    unittest.main()
