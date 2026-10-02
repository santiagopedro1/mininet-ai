from unittest.mock import Mock

import pytest

from mininet_ai.substrates import FakeSubstrateRuntime
from mininet_ai.substrates.builtin_runtimes import register_builtin_runtimes
from mininet_ai.substrates.runtime_registry import RuntimeRegistry


def test_unreserved_factory_stays_zero_argument():
    registry = RuntimeRegistry()
    factory = Mock(return_value=FakeSubstrateRuntime())
    registry.register("fake", factory)
    run_id, runtime = registry.reserve("fake")
    assert run_id is None
    assert runtime is factory.return_value
    factory.assert_called_once_with()


def test_reserved_factory_is_validated_like_normal_construction():
    registry = RuntimeRegistry()
    registry.register(
        "wrong-name",
        FakeSubstrateRuntime,
        reserve=lambda: ("run-test", FakeSubstrateRuntime()),
    )
    with pytest.raises(ValueError, match="reports name"):
        registry.reserve("wrong-name")


def test_reservation_and_creation_share_unknown_adapter_diagnostics():
    registry = RuntimeRegistry()
    for operation in (registry.create, registry.reserve):
        with pytest.raises(
            LookupError, match="unknown substrate runtime.*available: none"
        ):
            operation("missing")


def test_all_builtin_registrations_support_creation_and_reservation():
    registry = RuntimeRegistry()
    register_builtin_runtimes(registry)
    for name in registry.names:
        ordinary = registry.create(name)
        run_id, reserved = registry.reserve(name)
        assert run_id is not None
        assert type(ordinary) is type(reserved)
        assert reserved.name == name


def test_default_builtin_identity_prefixes_are_preserved():
    registry = RuntimeRegistry()
    register_builtin_runtimes(registry)
    for name, prefix in (("fake", "run-"), ("mininet-ovs", "mn-")):
        run_id, _ = registry.reserve(name)
        assert run_id is not None and run_id.startswith(prefix)
