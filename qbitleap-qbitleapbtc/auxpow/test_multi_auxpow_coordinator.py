import unittest

import multi_auxpow_coordinator as multi
from lab.auxpow import auxpow_coordinator as base


class SharedAuxPowTreeTests(unittest.TestCase):
    def setUp(self):
        self.templates = {
            "qbit": {
                "hash": "01" * 32,
                "chainid": 47,
                "commitmentorder": "display",
            },
            "fractal": {
                "hash": "02" * 32,
                "chainid": 8228,
            },
        }

    def test_chain_ids_use_distinct_deterministic_slots(self):
        root, indices, branches = multi.build_aux_merkle_tree(self.templates, size=16, nonce=0)

        self.assertEqual(indices, {"qbit": 1, "fractal": 2})
        self.assertEqual(len(branches["qbit"]), 4)
        self.assertEqual(len(branches["fractal"]), 4)
        for name in self.templates:
            computed = base.check_merkle_branch(
                leaf=int(self.templates[name]["hash"], 16),
                branch=list(branches[name]),
                index=indices[name],
            )
            self.assertEqual(computed, root)

    def test_commitment_contains_standard_header_root_size_and_nonce(self):
        commitment, _, _ = multi.shared_commitment(self.templates, size=16, nonce=0)
        root, _, _ = multi.build_aux_merkle_tree(self.templates, size=16, nonce=0)

        self.assertEqual(commitment[:4], base.MERGED_MINING_HEADER)
        self.assertEqual(commitment[4:36], base.ser_uint256(root)[::-1])
        self.assertEqual(commitment[36:40], (16).to_bytes(4, "little"))
        self.assertEqual(commitment[40:44], (0).to_bytes(4, "little"))

    def test_rejects_non_display_qbit_commitment_order(self):
        self.templates["qbit"]["commitmentorder"] = "internal"
        with self.assertRaisesRegex(RuntimeError, "commitmentorder=display"):
            multi.shared_commitment(self.templates)

    def test_rejects_non_power_of_two_tree_size(self):
        with self.assertRaisesRegex(RuntimeError, "power of two"):
            multi.build_aux_merkle_tree(self.templates, size=15)


if __name__ == "__main__":
    unittest.main()
