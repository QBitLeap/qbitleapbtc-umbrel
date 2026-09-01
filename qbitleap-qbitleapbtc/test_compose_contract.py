import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
COMPOSE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")


class ComposeContractTests(unittest.TestCase):
    def test_permissionless_loads_address_before_ckpool_preflight(self):
        permissionless = COMPOSE.split("  permissionless:\n", 1)[1].split(
            "  stratum_router:\n", 1
        )[0]
        self.assertIn('export QBIT_MINER_ADDRESS=', permissionless)
        self.assertIn("exec /usr/local/bin/start-ckpool.sh", permissionless)
        self.assertNotIn("QBIT_MINER_ADDRESS_FILE:", permissionless)

    def test_router_does_not_wait_for_backend_health(self):
        router = COMPOSE.split("  stratum_router:\n", 1)[1]
        dependencies = router.split("    depends_on:\n", 1)[1].split(
            "    healthcheck:\n", 1
        )[0]
        self.assertEqual(dependencies.count("condition: service_started"), 2)
        self.assertNotIn("condition: service_healthy", dependencies)

    def test_all_release_images_use_manifest_version(self):
        manifest = (ROOT / "umbrel-app.yml").read_text(encoding="utf-8")
        version = re.search(r'^version: "([^"]+)"$', manifest, re.MULTILINE).group(1)
        image_versions = re.findall(r"image: ghcr\.io/qbitleap/[^:]+:([^\s]+)", COMPOSE)
        self.assertTrue(image_versions)
        self.assertEqual(set(image_versions), {version})


if __name__ == "__main__":
    unittest.main()
