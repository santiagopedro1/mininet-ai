"""Non-incident replay of a retained combined S2 request; never executes actions."""

import argparse
import copy
import json
import time
import urllib.request
from pathlib import Path

from examples.noms27.recovery_campaign.s2 import compile_route, route_handoff_view


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run", action="store_true", help="send one inference only")
    parser.add_argument(
        "--clarify-handoff",
        action="store_true",
        help="probe whether S1 failure is being confused with missing network-wide routes",
    )
    args = parser.parse_args()
    evidence = json.loads((args.evidence / "evidence.json").read_text())
    if evidence["config"]["treatment"] != "combined_ungated":
        raise ValueError("expected combined-treatment evidence")
    s2_call = next(call for call in evidence["model_calls"] if call["stage"] == "s2")
    payload = copy.deepcopy(s2_call["request"])
    context = json.loads(payload["messages"][1]["content"])
    if context["s2_decision_interface"] != "route-v2":
        raise ValueError("expected a route-v2 request")
    if context["handoff"] != evidence["handoffs"][0]:
        raise ValueError("retained wire request does not match full handoff")
    context["handoff"] = route_handoff_view(evidence["handoffs"][0])
    payload["messages"][1]["content"] = json.dumps(context)
    if args.clarify_handoff:
        payload["messages"][0]["content"] += (
            " S1's failed local verification does not mean no network-wide route "
            "exists. Decide whether a route exists from the current "
            "observation.surviving_links, not from S1's outcome."
        )
    if not args.run:
        print(
            json.dumps(
                {"endpoint": evidence["config"]["s2_endpoint"], "context": context}
            )
        )
        return
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "request.json").write_text(json.dumps(payload, indent=2) + "\n")
    started = time.monotonic()
    try:
        request = urllib.request.Request(
            evidence["config"]["s2_endpoint"].rstrip("/") + "/api/chat",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            reply = json.load(response)
        (args.output / "response.json").write_text(json.dumps(reply, indent=2) + "\n")
        decision = json.loads(reply["message"]["content"])
        observation = next(
            event["result"]
            for event in (
                json.loads(line)
                for line in (args.evidence / "events.jsonl").read_text().splitlines()
            )
            if event["kind"] == "telemetry_complete" and "switches" in event["result"]
        )
        compiled = compile_route(decision, observation)
        summary = {
            "status": compiled["status"],
            "route": decision["path"],
            "compiled_actions": compiled["actions"],
            "elapsed_seconds": time.monotonic() - started,
            "input_tokens": reply.get("prompt_eval_count"),
            "output_tokens": reply.get("eval_count"),
            "not_service_verification": True,
        }
    except Exception as error:
        summary = {"error": str(error), "elapsed_seconds": time.monotonic() - started}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
