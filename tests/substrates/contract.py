"""Conformance mixin for every Phase 1 substrate driver.

Future driver suites should inherit this mixin and ``unittest.TestCase``, then
implement ``make_driver``. This keeps compiler-facing behavior consistent.
"""

from __future__ import annotations

from mininet_ai.specification.models import AttachmentLayer, ResourceKind
from mininet_ai.substrates import (
    SUBSTRATE_CONTRACT_VERSION,
    SubstrateDriver,
    SubstrateIssue,
)


class SubstrateDriverContract:
    def make_driver(self) -> SubstrateDriver:
        raise NotImplementedError

    def test_driver_satisfies_runtime_protocol(self) -> None:
        driver = self.make_driver()

        self.assertIsInstance(driver, SubstrateDriver)
        self.assertEqual(driver.name, driver.manifest.name)
        self.assertEqual(
            driver.manifest.contract_version, SUBSTRATE_CONTRACT_VERSION
        )

    def test_manifest_has_usable_capabilities(self) -> None:
        manifest = self.make_driver().manifest

        self.assertTrue(manifest.resource_kinds)
        self.assertTrue(manifest.layers)
        self.assertNotIn(AttachmentLayer.CUSTOM, manifest.layers)
        for support in (*manifest.layers.values(), *manifest.custom_layers.values()):
            self.assertTrue(support.targets)
            self.assertTrue(support.runtimes)

    def test_every_advertised_attachment_is_accepted(self) -> None:
        driver = self.make_driver()
        for layer, support in driver.manifest.layers.items():
            for target in support.targets:
                for runtime in support.runtimes:
                    self.assertEqual(
                        driver.validate_attachment(
                            layer=layer,
                            custom_layer=None,
                            target_kind=target,
                            runtime=runtime,
                        ),
                        (),
                    )
        for name, support in driver.manifest.custom_layers.items():
            for target in support.targets:
                for runtime in support.runtimes:
                    self.assertEqual(
                        driver.validate_attachment(
                            layer=AttachmentLayer.CUSTOM,
                            custom_layer=name,
                            target_kind=target,
                            runtime=runtime,
                        ),
                        (),
                    )

    def test_every_advertised_observation_is_accepted(self) -> None:
        driver = self.make_driver()
        for layer, support in driver.manifest.layers.items():
            for observation in support.observations:
                self.assertEqual(
                    driver.validate_observation(
                        layer=layer,
                        custom_layer=None,
                        name=observation,
                    ),
                    (),
                )
        for name, support in driver.manifest.custom_layers.items():
            for observation in support.observations:
                self.assertEqual(
                    driver.validate_observation(
                        layer=AttachmentLayer.CUSTOM,
                        custom_layer=name,
                        name=observation,
                    ),
                    (),
                )

    def test_unadvertised_bindings_return_typed_issues(self) -> None:
        driver = self.make_driver()
        layer, support = next(iter(driver.manifest.layers.items()))
        issues = driver.validate_attachment(
            layer=layer,
            custom_layer=None,
            target_kind=next(iter(support.targets)),
            runtime="contract-unsupported-runtime",
        )
        observation_issues = driver.validate_observation(
            layer=layer,
            custom_layer=None,
            name="contract.unknown-observation",
        )

        self.assertTrue(issues)
        self.assertTrue(observation_issues)
        self.assertTrue(all(isinstance(issue, SubstrateIssue) for issue in issues))
        self.assertTrue(
            all(isinstance(issue, SubstrateIssue) for issue in observation_issues)
        )

    def test_empty_resource_graph_and_default_options_are_valid(self) -> None:
        driver = self.make_driver()

        self.assertEqual(driver.validate_options(), ())
        self.assertEqual(driver.validate_resources(()), ())
