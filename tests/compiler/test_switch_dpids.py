from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from pydantic import ValidationError

from mininet_ai.compiler import compile_experiment
from mininet_ai.compiler.models import PlannedSwitch
from mininet_ai.errors import CompilationError
from mininet_ai.specification.models import Experiment
from tests.compiler.helpers import switch_experiment


class SwitchDPIDTests(unittest.TestCase):
    def test_generated_collisions_skip_canonical_ids_in_stable_order(self) -> None:
        digest = Mock()
        digest.digest.return_value = (1).to_bytes(8, "big") + bytes(24)
        digest.hexdigest.return_value = "0" * 64
        with patch("mininet_ai.compiler.compiler.hashlib.sha256", return_value=digest):
            first = compile_experiment(switch_experiment("s1", "beta", "alpha"))
            second = compile_experiment(switch_experiment("alpha", "beta", "s1"))
        self.assertEqual(first.resources, second.resources)
        self.assertEqual(
            {
                item.name: item.dpid
                for item in first.resources
                if isinstance(item, PlannedSwitch)
            },
            {
                "s1": "0000000000000001",
                "alpha": "0000000000000002",
                "beta": "0000000000000003",
            },
        )

    def test_zero_hash_and_maximum_collision_allocate_nonzero_ids(self) -> None:
        for candidate in (0, (1 << 64) - 1):
            with self.subTest(candidate=candidate):
                snapshot = switch_experiment("alpha", "beta", "reserved").model_dump(
                    by_alias=True, mode="json"
                )
                snapshot["substrate"]["topology"]["resources"][2]["dpid"] = (
                    "ffffffffffffffff"
                )
                digest = Mock()
                digest.digest.return_value = candidate.to_bytes(8, "big") + bytes(24)
                digest.hexdigest.return_value = "0" * 64
                with patch(
                    "mininet_ai.compiler.compiler.hashlib.sha256", return_value=digest
                ):
                    first = compile_experiment(Experiment.model_validate(snapshot))
                    snapshot["substrate"]["topology"]["resources"].reverse()
                    second = compile_experiment(Experiment.model_validate(snapshot))
                self.assertEqual(first.resources, second.resources)
                self.assertEqual(
                    {
                        item.name: item.dpid
                        for item in first.resources
                        if isinstance(item, PlannedSwitch)
                    },
                    {
                        "alpha": "0000000000000001",
                        "beta": "0000000000000002",
                        "reserved": "ffffffffffffffff",
                    },
                )

    def test_canonical_and_noncanonical_switches_have_stable_dpids(self) -> None:
        def dpids(*names: str) -> dict[str, str]:
            return {
                switch.name: switch.dpid
                for switch in compile_experiment(switch_experiment(*names)).resources
                if isinstance(switch, PlannedSwitch)
            }

        result = dpids("s1", "edge-sw", "core")
        self.assertEqual(result["s1"], "0000000000000001")
        self.assertEqual(result, dpids("core", "edge-sw", "s1"))
        self.assertEqual(len(set(result.values())), 3)
        for value in result.values():
            self.assertRegex(value, r"^[0-9a-f]{16}$")
            self.assertNotEqual(int(value, 16), 0)

    def test_explicit_dpid_is_normalized_and_preserved(self) -> None:
        experiment = switch_experiment("edge-sw")
        snapshot = experiment.model_dump(by_alias=True, mode="json")
        snapshot["substrate"]["topology"]["resources"][0]["dpid"] = "AbC"
        plan = compile_experiment(Experiment.model_validate(snapshot))
        switch = next(
            item for item in plan.resources if isinstance(item, PlannedSwitch)
        )
        self.assertEqual(switch.dpid, "0000000000000abc")

    def test_invalid_and_zero_dpids_are_rejected(self) -> None:
        for value in ("", "0", "0000000000000000", "xyz", "0x1", "1" * 17, " 1", 1):
            with self.subTest(value=value):
                snapshot = switch_experiment("edge-sw").model_dump(
                    by_alias=True, mode="json"
                )
                snapshot["substrate"]["topology"]["resources"][0]["dpid"] = value
                with self.assertRaises(ValidationError):
                    Experiment.model_validate(snapshot)

    def test_normalized_duplicates_and_canonical_conflicts_are_rejected(self) -> None:
        for names, values in (
            (["edge", "core"], ["A", "000000000000000a"]),
            (["edge", "s1"], ["1", None]),
        ):
            snapshot = switch_experiment(*names).model_dump(by_alias=True, mode="json")
            for resource, value in zip(
                snapshot["substrate"]["topology"]["resources"], values
            ):
                resource["dpid"] = value
            with self.assertRaisesRegex(CompilationError, "share DPID"):
                compile_experiment(Experiment.model_validate(snapshot))

    def test_generated_dpid_skips_reserved_explicit_id_regardless_of_order(
        self,
    ) -> None:
        original = compile_experiment(switch_experiment("edge-sw")).resources[0]
        assert isinstance(original, PlannedSwitch)
        snapshot = switch_experiment("edge-sw", "reserved").model_dump(
            by_alias=True, mode="json"
        )
        resources = snapshot["substrate"]["topology"]["resources"]
        resources[1]["dpid"] = original.dpid
        first = compile_experiment(Experiment.model_validate(snapshot))
        resources.reverse()
        second = compile_experiment(Experiment.model_validate(snapshot))
        self.assertEqual(first.resources, second.resources)
        dpids = {
            item.name: item.dpid
            for item in first.resources
            if isinstance(item, PlannedSwitch)
        }
        self.assertEqual(dpids["reserved"], original.dpid)
        self.assertEqual(int(dpids["edge-sw"], 16), int(original.dpid, 16) + 1)
