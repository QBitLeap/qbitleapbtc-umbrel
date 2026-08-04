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
            patch.object(server, "chain_status", return_value=(True, 1)),
            patch.object(server, "auxpow_connected", return_value=True),
            patch.object(server, "read_telemetry", return_value=telemetry),
        ):
            page = server.render({}).decode("utf-8")

        self.assertIn("garage-miner", page)
        self.assertIn("Expected hashrate needed", page)
        self.assertIn('name="expected_rate"', page)
        self.assertNotIn("thor-p2", page)
        self.assertNotIn("magic-40t", page)


if __name__ == "__main__":
    unittest.main()
