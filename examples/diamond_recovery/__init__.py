"""Inspectable policy and probe contract for the diamond Maintained Example."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
from typing import Any

PATHS = {
    "primary": ("h1", "s1", "s2", "s4", "h2"),
    "recovery": ("h1", "s1", "s3", "s4", "h2"),
}
PORTS = {
    "s1": {"h1": 1, "s2": 2, "s3": 3},
    "s2": {"s1": 1, "s4": 2},
    "s3": {"s1": 1, "s4": 2},
    "s4": {"h2": 1, "s2": 2, "s3": 3},
}
LINK_ENDPOINTS = {
    "h1-s1": ("h1", "s1"),
    "s1-s2": ("s1", "s2"),
    "s2-s4": ("s2", "s4"),
    "s1-s3": ("s1", "s3"),
    "s3-s4": ("s3", "s4"),
    "s4-h2": ("s4", "h2"),
}
HOST_MACS = {"h1": "02:00:00:00:00:01", "h2": "02:00:00:00:00:02"}
HOST_ADDRESSES = {
    "h1": {
        "active": "10.0.0.1",
        "primaryProbe": "10.0.0.11",
        "recoveryProbe": "10.0.0.21",
    },
    "h2": {
        "active": "10.0.0.2",
        "primaryProbe": "10.0.0.12",
        "recoveryProbe": "10.0.0.22",
    },
}
INTER_SWITCH_LINKS = ("s1-s2", "s1-s3", "s2-s4", "s3-s4")


def _path_flows(
    path_name: str,
    path: tuple[str, ...],
    source: str,
    destination: str,
    *,
    cookie_start: int,
) -> list[dict[str, Any]]:
    flows = []
    switches = path[1:-1]
    for direction, src, dst, ordered_switches in (
        ("h1-to-h2", source, destination, switches),
        ("h2-to-h1", destination, source, tuple(reversed(switches))),
    ):
        for index, switch in enumerate(ordered_switches):
            previous = path[index] if direction == "h1-to-h2" else path[-1 - index]
            following = path[index + 2] if direction == "h1-to-h2" else path[-3 - index]
            flows.append(
                {
                    "path": path_name,
                    "direction": direction,
                    "switch": switch,
                    "match": {
                        "in_port": PORTS[switch][previous],
                        "dl_type": 2048,
                        "nw_src": src,
                        "nw_dst": dst,
                    },
                    "actions": [f"output:{PORTS[switch][following]}"],
                    "priority": 100,
                    "cookie": cookie_start + len(flows),
                    "idleTimeout": 0,
                    "hardTimeout": 0,
                }
            )
    return flows


def _initial_rules() -> tuple[dict[str, Any], ...]:
    rules = []
    cookie = 11101
    for source, destination, path_name in (
        ("10.0.0.1", "10.0.0.2", "primary"),
        ("10.0.0.11", "10.0.0.12", "primary"),
        ("10.0.0.21", "10.0.0.22", "recovery"),
    ):
        path_rules = _path_flows(
            path_name, PATHS[path_name], source, destination, cookie_start=cookie
        )
        rules.extend(path_rules)
        cookie += len(path_rules)
    return tuple(rules)


INITIAL_RULES = _initial_rules()

RECOVERY_RULES = (
    {
        "switch": "s3",
        "match": {
            "in_port": 1,
            "dl_type": 2048,
            "nw_src": "10.0.0.1",
            "nw_dst": "10.0.0.2",
        },
        "actions": "output:2",
        "priority": 200,
        "cookie": 12201,
        "idleTimeout": 0,
        "hardTimeout": 0,
    },
    {
        "switch": "s3",
        "match": {
            "in_port": 2,
            "dl_type": 2048,
            "nw_src": "10.0.0.2",
            "nw_dst": "10.0.0.1",
        },
        "actions": "output:1",
        "priority": 200,
        "cookie": 12202,
        "idleTimeout": 0,
        "hardTimeout": 0,
    },
    {
        "switch": "s4",
        "match": {
            "in_port": 3,
            "dl_type": 2048,
            "nw_src": "10.0.0.1",
            "nw_dst": "10.0.0.2",
        },
        "actions": "output:1",
        "priority": 200,
        "cookie": 12203,
        "idleTimeout": 0,
        "hardTimeout": 0,
    },
    {
        "switch": "s4",
        "match": {
            "in_port": 1,
            "dl_type": 2048,
            "nw_src": "10.0.0.2",
            "nw_dst": "10.0.0.1",
        },
        "actions": "output:3",
        "priority": 200,
        "cookie": 12204,
        "idleTimeout": 0,
        "hardTimeout": 0,
    },
    {
        "switch": "s1",
        "match": {
            "in_port": 1,
            "dl_type": 2048,
            "nw_src": "10.0.0.1",
            "nw_dst": "10.0.0.2",
        },
        "actions": "output:3",
        "priority": 200,
        "cookie": 12205,
        "idleTimeout": 0,
        "hardTimeout": 0,
    },
    {
        "switch": "s1",
        "match": {
            "in_port": 3,
            "dl_type": 2048,
            "nw_src": "10.0.0.2",
            "nw_dst": "10.0.0.1",
        },
        "actions": "output:1",
        "priority": 200,
        "cookie": 12206,
        "idleTimeout": 0,
        "hardTimeout": 0,
    },
    {
        "switch": "s2",
        "match": {
            "in_port": 1,
            "dl_type": 2048,
            "nw_src": "10.0.0.1",
            "nw_dst": "10.0.0.2",
        },
        "actions": "drop",
        "priority": 200,
        "cookie": 12207,
        "idleTimeout": 0,
        "hardTimeout": 0,
    },
    {
        "switch": "s2",
        "match": {
            "in_port": 2,
            "dl_type": 2048,
            "nw_src": "10.0.0.2",
            "nw_dst": "10.0.0.1",
        },
        "actions": "drop",
        "priority": 200,
        "cookie": 12208,
        "idleTimeout": 0,
        "hardTimeout": 0,
    },
)


def static_neighbors() -> dict[str, dict[str, str]]:
    """Map every remote alias to its peer MAC for installation on each host."""
    return {
        "h1": {address: HOST_MACS["h2"] for address in HOST_ADDRESSES["h2"].values()},
        "h2": {address: HOST_MACS["h1"] for address in HOST_ADDRESSES["h1"].values()},
    }


Probe = Callable[[str, str, str, str, float], Mapping[str, Any]]


def collect_observation_round(
    probe: Probe,
    *,
    observed_at: str,
    link_states: Mapping[str, str | None],
    policy_state: str | Mapping[str, Any],
    previous_outcomes: list[Mapping[str, Any]],
    previous_rounds: tuple[Mapping[str, Any], ...] = (),
    round_number: int = 1,
    scheduled_at: str | None = None,
    cadence_lateness_seconds: float = 0.0,
) -> dict[str, Any]:
    """Collect one independently measured bidirectional round without filling gaps."""
    specifications = []
    for path_name, source_key, destination_key in (
        ("primary", "primaryProbe", "primaryProbe"),
        ("recovery", "recoveryProbe", "recoveryProbe"),
    ):
        for source, destination in (("h1", "h2"), ("h2", "h1")):
            specifications.append(
                (
                    "path",
                    path_name,
                    source,
                    destination,
                    HOST_ADDRESSES[source][source_key],
                    HOST_ADDRESSES[destination][destination_key],
                )
            )
    for source, destination in (("h1", "h2"), ("h2", "h1")):
        specifications.append(
            (
                "active",
                "active",
                source,
                destination,
                HOST_ADDRESSES[source]["active"],
                HOST_ADDRESSES[destination]["active"],
            )
        )

    def execute(specification):
        return probe(
            specification[2], specification[3], specification[4], specification[5], 1.0
        )

    with ThreadPoolExecutor(max_workers=len(specifications)) as workers:
        measurements = list(workers.map(execute, specifications))
    path_probes = []
    active_confirmation = []
    for specification, result in zip(specifications, measurements, strict=True):
        kind, path_name, source, destination, source_ip, destination_ip = specification
        item = {
            "direction": f"{source}-to-{destination}",
            "sourceAddress": source_ip,
            "destinationAddress": destination_ip,
            "sent": result.get("sent"),
            "received": result.get("received"),
            "state": result.get("state", "missing"),
            "observedAt": result.get("observedAt", observed_at),
        }
        if kind == "path":
            path_probes.append({"path": path_name, **item})
        else:
            active_confirmation.append(item)
    current = {
        "observedAt": observed_at,
        "scheduledAt": scheduled_at or observed_at,
        "roundNumber": round_number,
        "cadenceLatenessSeconds": max(0.0, cadence_lateness_seconds),
        "paths": {name: list(nodes) for name, nodes in PATHS.items()},
        "pathProbes": path_probes,
        "activeConfirmation": active_confirmation,
        "linkStates": {
            name: {"state": value, "observedAt": observed_at}
            for name, value in link_states.items()
        },
        "policyState": policy_state,
        "previousOutcomes": list(previous_outcomes),
    }

    def complete(round_data: Mapping[str, Any]) -> bool:
        probes = round_data.get("pathProbes", ())
        expected = {
            (path, direction)
            for path in ("primary", "recovery")
            for direction in ("h1-to-h2", "h2-to-h1")
        }
        actual = {
            (item.get("path"), item.get("direction"))
            for item in probes
            if isinstance(item, Mapping)
            and item.get("sent") is not None
            and item.get("received") is not None
            and item.get("observedAt")
        }
        return (
            expected <= actual
            and round_data.get("cadenceLatenessSeconds", 0.0) <= 0.1
            and all(
                value.get("state") is not None
                for value in round_data.get("linkStates", {}).values()
                if isinstance(value, Mapping)
            )
            and len(round_data.get("linkStates", {})) == len(INTER_SWITCH_LINKS)
        )

    candidates = [*previous_rounds[-2:]]
    if complete(current):
        candidates.append(current)
    completed_reversed = []
    newer = None
    for round_data in reversed(candidates):
        if not complete(round_data):
            break
        if newer is not None:
            if newer.get("roundNumber") != round_data.get("roundNumber", 0) + 1:
                break
            try:
                interval = (
                    datetime.fromisoformat(newer["scheduledAt"])
                    - datetime.fromisoformat(round_data["scheduledAt"])
                ).total_seconds()
            except KeyError, AttributeError, TypeError, ValueError:
                break
            if not 0 < interval <= 1.5:
                break
        completed_reversed.append(
            {
                key: round_data.get(key)
                for key in (
                    "roundNumber",
                    "scheduledAt",
                    "cadenceLatenessSeconds",
                    "observedAt",
                    "paths",
                    "pathProbes",
                    "activeConfirmation",
                    "linkStates",
                    "policyState",
                    "previousOutcomes",
                    "gaps",
                )
            }
            | {"gaps": round_data.get("history", {}).get("gaps", [])}
        )
        newer = round_data
    completed = list(reversed(completed_reversed))
    gaps = []
    if len(completed) < 3:
        gaps.append(
            {
                "code": "history.insufficient-complete-rounds",
                "required": 3,
                "available": len(completed),
            }
        )
    if not complete(current):
        gaps.append({"code": "round.incomplete", "observedAt": observed_at})
    for round_data in previous_rounds[-2:]:
        if not complete(round_data):
            gaps.append(
                {
                    "code": "history.incomplete-round",
                    "roundNumber": round_data.get("roundNumber"),
                    "observedAt": round_data.get("observedAt"),
                }
            )
    current["history"] = {
        "requiredRounds": 3,
        "completedRounds": completed,
        "gaps": gaps,
    }
    current["invocationSnapshot"] = {
        "scenario": "diamond-recovery",
        "inputs": deepcopy(current),
    }
    return current
