from __future__ import annotations

from dataclasses import replace
import unittest

from mininet_ai.specification.models import AttachmentLayer, ResourceKind
from mininet_ai.substrates import (
    FakeSubstrateDriver,
    SubstrateRegistry,
    substrate_registry,
)
from tests.substrates.contract import SubstrateDriverContract


class FakeSubstrateContractTests(SubstrateDriverContract, unittest.TestCase):
    def make_driver(self) -> FakeSubstrateDriver:
        return FakeSubstrateDriver()


class FakeSubstrateTests(unittest.TestCase):
    def test_custom_layer_is_declared_in_manifest(self) -> None:
        driver = FakeSubstrateDriver(
            {
                "custom-layers": [
                    {
                        "name": "experimental",
                        "targets": ["switch"],
                        "runtimes": ["edge-runtime"],
                        "observations": ["experimental.metrics"],
                    }
                ]
            }
        )

        self.assertEqual(driver.validate_options(), ())
        support = driver.manifest.custom_layers["experimental"]
        self.assertEqual(support.targets, frozenset({ResourceKind.SWITCH}))
        self.assertEqual(support.runtimes, frozenset({"edge-runtime"}))
        self.assertEqual(
            driver.validate_observation(
                layer=AttachmentLayer.CUSTOM,
                custom_layer="experimental",
                name="experimental.metrics",
            ),
            (),
        )

    def test_invalid_options_return_paths_and_codes(self) -> None:
        driver = FakeSubstrateDriver(
            {
                "unknown": True,
                "custom-layers": [
                    {
                        "name": "experimental",
                        "targets": ["not-a-resource"],
                        "runtimes": [],
                    }
                ],
            }
        )

        issues = driver.validate_options()

        self.assertTrue(issues)
        self.assertTrue(all(issue.code.startswith("fake.options") for issue in issues))
        self.assertTrue(all(issue.path for issue in issues))

    def test_builtin_registry_constructs_fake_driver(self) -> None:
        driver = substrate_registry.create("fake")

        self.assertIsInstance(driver, FakeSubstrateDriver)
        self.assertEqual(substrate_registry.names, ("fake",))

    def test_registry_rejects_duplicates_and_unknown_names(self) -> None:
        registry = SubstrateRegistry()
        registry.register("fake", FakeSubstrateDriver)

        with self.assertRaisesRegex(ValueError, "already registered"):
            registry.register("fake", FakeSubstrateDriver)
        with self.assertRaisesRegex(LookupError, "available: fake"):
            registry.create("missing")

    def test_registry_rejects_incompatible_contract_version(self) -> None:
        def old_driver(options=None):
            driver = FakeSubstrateDriver(options)
            driver.manifest = replace(driver.manifest, contract_version="v0")
            return driver

        registry = SubstrateRegistry()
        registry.register("fake", old_driver)

        with self.assertRaisesRegex(ValueError, "unsupported contract version 'v0'"):
            registry.create("fake")


if __name__ == "__main__":
    unittest.main()
