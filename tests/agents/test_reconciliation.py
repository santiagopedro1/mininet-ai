from __future__ import annotations

import pytest

from mininet_ai.agents.reconciliation import reconcile_bundle


@pytest.mark.parametrize("failed_index", range(4))
def test_partial_bundle_state_preserves_present_effects_and_remaining_steps(
    failed_index: int,
) -> None:
    states = tuple(
        "present" if index < failed_index else "missing" for index in range(4)
    )

    result = reconcile_bundle(states)

    assert result.policy == ("partial" if failed_index else "verified-primary")
    assert result.present == tuple(range(failed_index))
    assert result.missing == tuple(range(failed_index, 4))


def test_complete_policy_is_verified_recovery() -> None:
    result = reconcile_bundle(("present",) * 4)

    assert result.policy == "verified-recovery"
    assert result.missing == ()


def test_non_prefix_policy_cannot_be_safely_continued() -> None:
    result = reconcile_bundle(("present", "missing", "present", "missing"))

    assert result.policy == "unknown"
    assert result.present == ()


@pytest.mark.parametrize("state", ["wrong", "unreadable", "unknown", "conflicting"])
def test_unestablished_bundle_state_is_explicitly_unknown(state: str) -> None:
    result = reconcile_bundle(("present", state, "missing"))

    assert result.policy == "unknown"
    assert result.present == ()
    assert result.missing == (0, 1, 2)
