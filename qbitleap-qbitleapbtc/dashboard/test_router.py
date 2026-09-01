import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import router


class PermissionlessRoutingTests(unittest.TestCase):
    def test_rewrites_worker_authorization_to_qbit_payout(self):
        with tempfile.TemporaryDirectory() as directory:
            address_file = Path(directory) / "qbt.txt"
            address_file.write_text("qb1validpayout\n", encoding="utf-8")
            payload = json.dumps({"id": 2, "method": "mining.authorize", "params": ["thor-p2", "x"]}).encode() + b"\n"
            with patch.object(router, "QBT_ADDRESS_FILE", address_file):
                remaining, output = router.permissionless_messages(b"", payload)
        self.assertEqual(remaining, b"")
        self.assertEqual(json.loads(output)["params"], ["qb1validpayout.thor-p2", "x"])

    def test_authorize_and_submit_use_identical_qualified_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            address_file = Path(directory) / "qbt.txt"
            address_file.write_text("qb1validpayout\n", encoding="utf-8")
            messages = [
                {"id": 2, "method": "mining.authorize", "params": ["thor-p2", "x"]},
                {"id": 3, "method": "mining.submit", "params": ["thor-p2", "1", "00000000", "65000000", "12345678"]},
            ]
            payload = b"".join(json.dumps(message).encode() + b"\n" for message in messages)
            with patch.object(router, "QBT_ADDRESS_FILE", address_file):
                remaining, output = router.permissionless_messages(b"", payload)
        rewritten = [json.loads(line) for line in output.splitlines()]
        self.assertEqual(remaining, b"")
        self.assertEqual(rewritten[0]["params"][0], "qb1validpayout.thor-p2")
        self.assertEqual(rewritten[1]["params"][0], "qb1validpayout.thor-p2")
        self.assertEqual(rewritten[1]["params"][1:], messages[1]["params"][1:])

    def test_preserves_partial_stratum_messages(self):
        remaining, output = router.permissionless_messages(b"", b'{"id":1')
        self.assertEqual(remaining, b'{"id":1')
        self.assertEqual(output, b"")


if __name__ == "__main__":
    unittest.main()
