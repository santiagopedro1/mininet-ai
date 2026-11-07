"""Replay a retained S2 request offline; never executes forwarding actions."""

import argparse
import copy
import json
import time
import urllib.request
from pathlib import Path

from examples.noms27.recovery_campaign.live import parse_port_liveness
from examples.noms27.recovery_campaign.s2 import decision_view, validate_repair

NEUTRAL_INSTRUCTIONS = (
    "Diagnose directional h1-to-h2 service using the shared initial snapshot and "
    "current network-wide observations. Trace surviving ports and peers from h1 "
    "to h2; compare active h1-to-h2 forwarding flows at each switch along that "
    "path. Propose all needed forwarding changes on surviving switches only. "
    "Each action installs a fixed IPv4 h1-to-h2 rule matching in_port and "
    "outputting out_port. Never enable ports, revive resources, reverse outages, "
    "change traffic or construct commands. Use only observed up ports. "
    'Return JSON only: status "repair" with a nonempty actions list of '
    '{"switch": string, "in_port": integer, "out_port": integer}, or status '
    '"unable" with an empty actions list. Do not claim recovery from a single '
    "action unless forwarding exists along the whole path."
)
ALGORITHM_INSTRUCTIONS = (
    "Restore directional IPv4 service 10.0.0.1 (h1) to 10.0.0.2 (h2) by "
    "installing forwarding rules on surviving switches only. In the current "
    "observation, port_to_peer maps EACH NUMERIC PORT to its connected neighbor; "
    "use this mapping, never infer peers from interface names or port numbers. "
    "Find a path from h1 to h2 using only ports with value true. For every switch "
    "on that path, determine its incoming and outgoing port. Inspect its "
    "active_service_flows and include an action if no existing h1-to-h2 flow "
    "for that incoming port already outputs on the needed outgoing port. "
    "Include all missing or incorrect rules in ONE actions array; the action "
    "adapter fixes the IPv4 addresses and match fields. Never enable ports, "
    "reverse outages, revive resources or change traffic. Return only JSON "
    'with status "repair" and actions containing switch, integer in_port and '
    'integer out_port when a path exists and needs repairs. Return status "unable" '
    "with an empty actions list only if no surviving path exists."
)
ROUTE_INSTRUCTIONS = (
    "You choose the h1-to-h2 route for directional IPv4 service; a separate "
    "bounded executor will install forwarding rules only for your chosen route. "
    "Use ONLY current observation.port_to_peer to determine which node each "
    "numeric port connects to. A link is usable only when the ports at both "
    "endpoints are true in the current observation. Never infer neighbors from "
    "interface names or the original failed forwarding flows. Select an ordered "
    "route beginning at h1 and ending at h2, with no repeated nodes, using "
    "surviving ports only. Do not return actions or port numbers. Return JSON "
    'with status "repair" and path as the ordered list of node names if such '
    'a route exists; otherwise status "unable" with an empty path.'
)
ROUTE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["repair", "unable"]},
        "path": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
    },
    "required": ["status", "path"],
}


def validate_route(reply, observation):
    if reply.get("status") != "repair":
        if reply.get("status") == "unable" and reply.get("path") == []:
            return []
        raise ValueError("invalid route status or nonempty inability path")
    path = reply["path"]
    if not isinstance(path, list) or len(path) < 3 or len(path) != len(set(path)):
        raise ValueError("route requires distinct nodes")
    if path[0] != "h1" or path[-1] != "h2":
        raise ValueError("route must connect h1 and h2")
    switches = observation["switches"]
    for before, node, after in zip(path, path[1:], path[2:], strict=False):
        record = switches.get(node)
        if not record:
            raise ValueError("unknown route switch")
        ports = record["ports"]
        peers = record["peers"]
        if (
            before not in peers
            or after not in peers
            or ports.get(str(peers[before])) is not True
            or ports.get(str(peers[after])) is not True
        ):
            raise ValueError("route uses missing or down switch port")
        if after in switches:
            next_record = switches[after]
            back_port = next_record["peers"].get(node)
            if (
                back_port is None
                or next_record["ports"].get(str(back_port)) is not True
            ):
                raise ValueError("route uses a non-reciprocal surviving link")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--mode",
        choices=(
            "baseline",
            "neutral",
            "neutral-thinking",
            "port-map",
            "port-map-thinking",
            "port-map-thinking-long",
            "algorithm",
            "route-choice",
            "route-choice-compact-initial",
        ),
        required=True,
    )
    parser.add_argument("--run", action="store_true", help="send one model inference")
    args = parser.parse_args()
    evidence = json.loads((args.evidence / "evidence.json").read_text())
    calls = evidence["model_calls"]
    payload = copy.deepcopy(calls[0]["request"])
    observation = next(
        row["result"]
        for row in (
            json.loads(line)
            for line in (args.evidence / "events.jsonl").read_text().splitlines()
        )
        if row["kind"] == "telemetry_complete" and "switches" in row["result"]
    )
    if args.mode != "baseline":
        payload["messages"][0]["content"] = NEUTRAL_INSTRUCTIONS
    if args.mode == "algorithm":
        payload["messages"][0]["content"] = ALGORITHM_INSTRUCTIONS
    if args.mode.startswith("route-choice"):
        payload["messages"][0]["content"] = ROUTE_INSTRUCTIONS
        payload["format"] = ROUTE_SCHEMA
    if args.mode.startswith("port-map") or args.mode in (
        "algorithm",
        "route-choice",
        "route-choice-compact-initial",
    ):
        context = json.loads(payload["messages"][1]["content"])
        for switch in context["observation"]["switches"].values():
            switch["port_to_peer"] = {
                str(port): neighbor for neighbor, port in switch.pop("peers").items()
            }
        if args.mode == "route-choice-compact-initial":
            initial = context["initial_snapshot"]
            projected = decision_view(
                {
                    "switches": {
                        initial["switch"]: {
                            "ports": parse_port_liveness(initial["ports"]),
                            "flows": initial["flows"],
                        }
                    }
                }
            )["switches"][initial["switch"]]
            context["initial_snapshot"] = {"switch": initial["switch"], **projected}
        payload["messages"][1]["content"] = json.dumps(context)
    if args.mode in ("neutral-thinking", "port-map-thinking", "port-map-thinking-long"):
        payload["think"] = True
    if args.mode == "port-map-thinking-long":
        payload["options"]["num_predict"] = 2048
    if not args.run:
        print(
            json.dumps(
                {
                    "mode": args.mode,
                    "model": payload["model"],
                    "think": payload["think"],
                    "prompt": payload["messages"][0]["content"],
                }
            )
        )
        return
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "request.json").write_text(json.dumps(payload, indent=2) + "\n")
    started = time.monotonic()
    try:
        request = urllib.request.Request(
            evidence["config"]["endpoint"].rstrip("/") + "/api/chat",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
        elapsed = time.monotonic() - started
        (args.output / "response.json").write_text(json.dumps(result, indent=2) + "\n")
        repair = json.loads(result["message"]["content"])
        if args.mode.startswith("route-choice"):
            path = validate_route(repair, observation)
            switches = set(path[1:-1])
            status = repair["status"]
        else:
            permitted = validate_repair(repair, observation)
            switches = {action["switch"] for action in permitted["actions"]}
            status = permitted["status"]
        summary = {
            "mode": args.mode,
            "elapsed_seconds": elapsed,
            "input_tokens": result.get("prompt_eval_count"),
            "output_tokens": result.get("eval_count"),
            "status": status,
            "switches": sorted(switches),
            "route": path if args.mode.startswith("route-choice") else None,
            "three_switch_candidate": {"s1", "s3", "s4"} <= switches,
            "not_service_verification": True,
        }
    except Exception as error:
        summary = {
            "mode": args.mode,
            "elapsed_seconds": time.monotonic() - started,
            "error": str(error),
        }
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
