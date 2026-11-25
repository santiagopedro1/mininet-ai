"""Direct System Two policy: bounded, forwarding-only observe–act–verify."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from examples.diamond_recovery import PORTS

from .supervision import record_inference, run_action

NS = 1_000_000_000
MAX_ATTEMPTS = 3
MODEL_SETTINGS = {
    "think": False,
    "keep_alive": -1,
    "options": {"temperature": 0.3, "num_predict": 1024},
}
PERMISSIONS = {
    "telemetry": "surviving_switches_network_wide",
    "forwarding": "surviving_switches_only",
    "fault_reversal": False,
    "resource_revival": False,
    "offered_traffic_changes": False,
    "evaluator_labels": False,
    "max_attempts": MAX_ATTEMPTS,
}
INSTRUCTIONS = (
    "Diagnose the directional h1-to-h2 service alarm using the shared initial local "
    "snapshot and current network-wide observations. Propose forwarding changes on "
    "surviving switches only. Trace the observed surviving path from h1 to h2 "
    "using the peer/port map, then inspect whether the active h1-to-h2 service "
    "has a forwarding rule at each switch on that path. "
    "Each action installs a fixed IPv4 h1-to-h2 rule for "
    "10.0.0.1 -> 10.0.0.2, matching in_port and outputting out_port. "
    "Never enable ports, revive resources, reverse outages, change traffic, or "
    "construct commands. Use only observed up ports. A repair may span switches. "
    'Return JSON only: {"status":"repair","actions":[{"switch":"s1",'
    '"in_port":1,"out_port":3}]} or {"status":"unable","actions":[]}.'
)
ROUTE_INSTRUCTIONS = (
    "You choose an ordered h1-to-h2 route for directional IPv4 service. "
    "The observation.surviving_links list contains individual links whose "
    "switch endpoints both have observed live ports; it is NOT a route. "
    "Select an ordered route from h1 to h2 using these links, without repeating "
    "nodes. Do not copy the old forwarding path: its links may be down. "
    "S1's local attempt was insufficient; its outcome does not determine "
    "whether a network-wide route exists. Decide from the current "
    "observation, specifically surviving_links and port states. "
    "The bounded executor will validate your route and install forwarding "
    "rules; you do not provide actions or port numbers. Return JSON with "
    'status "repair" and path as the list of node names if a route exists, '
    'or status "unable" with an empty path otherwise.'
)


class ForwardingAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    switch: str
    in_port: int = Field(gt=0)
    out_port: int = Field(gt=0)


class Repair(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["repair", "unable", "no_action"]
    actions: list[ForwardingAction] = Field(max_length=16)


class RouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["repair", "unable"]
    path: list[str] = Field(max_length=8)


def _s1_flow_cookies(handoff):
    """Extract cookie values from S1's attempted action flows for observation filtering."""
    cookies = set()
    for action in handoff.get("s1_actions", []):
        result = action.get("result")
        if isinstance(result, dict):
            flow = result.get("flow", "")
            match = re.search(r"cookie=(0x[0-9a-fA-F]+)", flow)
            if match:
                cookies.add(match.group(1))
    return cookies


def decision_view(observation, exclude_cookies=None):
    """Project operational telemetry for S2; raw dumps stay in event evidence.

    When exclude_cookies is provided, filter out flows matching those cookies.
    This is used to hide S1's local attempt from S2's observation.
    """
    switches = {}
    for name, record in observation.get("switches", {}).items():
        flows = record.get("flows", "")
        if isinstance(flows, str):
            entries = flows.splitlines()
        elif isinstance(flows, list):
            entries = flows
        else:
            entries = []
        # Text OVS dumps are filtered to h1-to-h2 service traffic; structured
        # entries (controlled/offline fixtures) are already scoped and pass
        # through unchanged.
        lines = [
            entry.strip()
            if isinstance(entry, str)
            else entry
            for entry in entries
            if not isinstance(entry, str)
            or (
                re.search(r"\bnw_src=10\.0\.0\.1\b", entry)
                and re.search(r"\bnw_dst=10\.0\.0\.2\b", entry)
            )
        ]
        if exclude_cookies:
            lines = [
                line
                for line in lines
                if not isinstance(line, str)
                or not any(cookie in line for cookie in exclude_cookies)
            ]
        switches[name] = {
            "ports": record.get("ports", {}),
            "peers": record.get("peers", {}),
            # Raw per-switch flows are retained for the actions-v1 contract; the
            # filtered projection is what route-v2 consumes.
            "flows": flows,
            "active_service_flows": lines,
        }
    return {"switches": switches, "observed_ns": observation.get("observed_ns")}


def route_view(observation, exclude_cookies=None):
    """Expose observed links, never candidate paths or evaluator fault labels."""
    view = decision_view(observation, exclude_cookies=exclude_cookies)
    switches = view["switches"]
    links = set()
    for node, record in switches.items():
        record["port_to_peer"] = {
            str(port): peer for peer, port in record.pop("peers").items()
        }
        for port, peer in record["port_to_peer"].items():
            if record["ports"].get(port) is not True:
                continue
            if peer in switches:
                other = observation["switches"][peer]
                reciprocal = other.get("peers", {}).get(node)
                if (
                    reciprocal is None
                    or other.get("ports", {}).get(str(reciprocal)) is not True
                ):
                    continue
            links.add(tuple(sorted((node, peer))))
    view["surviving_links"] = [list(link) for link in sorted(links)]
    return view


def initial_route_view(initial):
    """Project the shared local snapshot without altering the retained raw record."""
    from .live import parse_port_liveness

    switch = initial["switch"]
    projected = decision_view(
        {
            "switches": {
                switch: {
                    "ports": parse_port_liveness(initial["ports"]),
                    "flows": initial["flows"],
                }
            }
        }
    )["switches"][switch]
    return {"switch": switch, **projected}


def route_handoff_view(handoff):
    """Present only the escalation cause and remaining budget; omit S1 specifics."""
    return {
        "cause": handoff["cause"],
        "remaining_seconds_at_handoff": handoff["remaining_seconds"],
    }


def compile_route(value, observation, port_authority=None):
    """Validate the model-selected path and compile only that path into actions."""
    decision = RouteDecision.model_validate(value)
    if decision.status == "unable":
        if decision.path:
            raise ValueError("inability forbids a path")
        return {"status": "unable", "actions": []}
    path = decision.path
    if (
        len(path) < 3
        or len(path) != len(set(path))
        or path[0] != "h1"
        or path[-1] != "h2"
    ):
        raise ValueError("repair requires a simple h1-to-h2 path")
    switches = observation.get("switches", {})
    authority = PORTS if port_authority is None else port_authority
    actions = []
    for before, node, after in zip(path, path[1:], path[2:], strict=False):
        record = switches.get(node, {})
        peers = record.get("peers", {})
        if node not in authority or before not in peers or after not in peers:
            raise ValueError("route includes unknown or nonadjacent switch")
        incoming, outgoing = peers[before], peers[after]
        if any(
            record.get("ports", {}).get(str(port)) is not True
            for port in (incoming, outgoing)
        ):
            raise ValueError("route requires surviving observed ports")
        if after in switches:
            other = switches[after]
            reciprocal = other.get("peers", {}).get(node)
            if (
                reciprocal is None
                or other.get("ports", {}).get(str(reciprocal)) is not True
            ):
                raise ValueError("route requires a reciprocal surviving link")
        actions.append({"switch": node, "in_port": incoming, "out_port": outgoing})
    return validate_repair(
        {"status": "repair", "actions": actions}, observation, port_authority
    )


def decision_history(history):
    """Keep prior outcomes without repeating raw admission telemetry in requests."""
    entries = []
    for item in history:
        entry = dict(item)
        if isinstance(entry.get("result"), dict):
            result = dict(entry["result"])
            result.pop("admission_observation", None)
            entry["result"] = result
        entries.append(entry)
    return entries


def validate_repair(value, observation, port_authority=None):
    repair = Repair.model_validate(value)
    if (repair.status == "repair") != bool(repair.actions):
        raise ValueError("repair requires actions; inability/no_action forbids actions")
    switches = observation.get("switches", {})
    authority = PORTS if port_authority is None else port_authority
    for action in repair.actions:
        ports = switches.get(action.switch, {}).get("ports", {})
        if action.switch not in authority or action.in_port == action.out_port:
            raise ValueError("unknown switch or non-forwarding action")
        allowed = set(authority[action.switch].values())
        if any(
            port not in allowed or ports.get(str(port)) is not True
            for port in (action.in_port, action.out_port)
        ):
            raise ValueError("repair requires surviving observed ports")
    return repair.model_dump()


async def run_direct(
    runtime,
    clock,
    deadline,
    initial,
    bounded,
    event,
    calls,
    actions,
    interface="actions-v1",
    *,
    handoff=None,
    port_authority=None,
):
    history = []
    exclude_cookies = _s1_flow_cookies(handoff) if handoff else None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        event("s2_attempt_start", attempt=attempt, deadline_ns=deadline)
        observation = await bounded("telemetry", runtime.observe_network(), deadline)
        view = (
            route_view(observation, exclude_cookies=exclude_cookies)
            if interface == "route-v2"
            else decision_view(observation)
        )
        context = {
            "alarm": "directional_service_disruption",
            "initial_snapshot": initial_route_view(initial)
            if interface == "route-v2"
            else initial,
            "observation": view,
            "permissions": PERMISSIONS,
            "history": decision_history(history),
            "s2_decision_interface": interface,
        }
        if handoff is not None:
            context["handoff"] = (
                route_handoff_view(handoff) if interface == "route-v2" else handoff
            )
        call = {
            "stage": "s2",
            "attempt": attempt,
            "started_ns": clock.now_ns(),
            "request": context,
            "response": None,
            "tokens": {"input": None, "output": None, "reasoning": None},
            "latency_seconds": None,
            "late": False,
            "status": "pending",
            "retry": 0,
        }
        calls.append(call)

        async def infer(call=call, context=context):
            # Bind the current record: late completions must never mutate a later call.
            value = await record_inference(
                lambda: runtime.decide_s2(context, (deadline - clock.now_ns()) / NS),
                call,
                clock,
                deadline,
            )
            if value.get("remote_terminal") == "unconfirmed":
                call["status"] = "remote_terminal_unknown"
            return value

        try:
            decision = await bounded("inference", infer(), deadline)
            repair = (
                compile_route(decision.get("repair"), observation, port_authority)
                if interface == "route-v2"
                else validate_repair(
                    decision.get("repair"), observation, port_authority
                )
            )
        except TimeoutError:
            raise
        except Exception as error:
            event("s2_decision_rejected", attempt=attempt, error=str(error))
            history.append(
                {
                    "attempt": attempt,
                    "observation": view,
                    "status": "unusable_decision",
                }
            )
            if call["response"] is None or call["status"] == "remote_terminal_unknown":
                # Do not overlap further requests with a possibly outstanding
                # provider operation whose terminal response was never observed.
                return "treatment_error"
            continue
        if repair["status"] != "repair":
            return "explicit_inability" if repair["status"] == "unable" else "no_action"
        selected_route = decision["repair"]["path"] if interface == "route-v2" else None
        if selected_route is not None:
            event(
                "s2_route_selected",
                path=selected_route,
                compiled_actions=repair["actions"],
            )
        action = {
            "stage": "s2",
            "attempt": attempt,
            "repair": repair,
            "selected_route": selected_route,
            "started_ns": clock.now_ns(),
            "result": None,
        }
        actions.append(action)

        result = await run_action(
            lambda repair=repair: runtime.execute_s2(repair),
            action,
            clock,
            deadline,
            bounded,
        )
        signal = await bounded(
            "verification", runtime.service_signal(clock.now_ns()), deadline
        )
        status = (
            "verified" if signal.get("recovered") is True else "verification_failed"
        )
        history.append(
            {
                "attempt": attempt,
                "observation": view,
                "repair": repair,
                "result": result,
                "verification": signal,
                "status": status,
            }
        )
        event("s2_attempt_complete", **history[-1])
        if status == "verified":
            return status
    return "attempts_exhausted"
