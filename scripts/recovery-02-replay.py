"""Replay a retained S2 request offline; never executes forwarding actions."""

import argparse
import copy
import json
import time
import urllib.request
from pathlib import Path

from examples.noms27.recovery_campaign.s2 import validate_repair

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
    if args.mode.startswith("port-map") or args.mode == "algorithm":
        context = json.loads(payload["messages"][1]["content"])
        for switch in context["observation"]["switches"].values():
            switch["port_to_peer"] = {
                str(port): neighbor for neighbor, port in switch.pop("peers").items()
            }
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
        permitted = validate_repair(repair, observation)
        switches = {action["switch"] for action in permitted["actions"]}
        summary = {
            "mode": args.mode,
            "elapsed_seconds": elapsed,
            "input_tokens": result.get("prompt_eval_count"),
            "output_tokens": result.get("eval_count"),
            "status": permitted["status"],
            "switches": sorted(switches),
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
