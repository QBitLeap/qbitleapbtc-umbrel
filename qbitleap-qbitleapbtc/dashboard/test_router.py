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

    def test_preserves_partial_stratum_messages(self):
        remaining, output = router.permissionless_messages(b"", b'{"id":1')
        self.assertEqual(remaining, b'{"id":1')
        self.assertEqual(output, b"")


if __name__ == "__main__":
    unittest.main()
