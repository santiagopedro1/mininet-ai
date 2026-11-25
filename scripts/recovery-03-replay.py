"""Non-incident replay of a retained combined S2 request; never executes actions.

Applies the production observation filtering (removes S1's installed flows) and
the simplified handoff view (cause + remaining time only). The strengthened system
prompt is inherited from the production ROUTE_INSTRUCTIONS.
"""

import argparse
import copy
import json
import re
import time
import urllib.request
from pathlib import Path

from examples.noms27.recovery_campaign.s2 import compile_route, route_handoff_view


def _extract_s1_cookies(handoff):
    """Extract cookie values from S1's attempted action flows."""
    cookies = set()
    for action in handoff.get("s1_actions", []):
        result = action.get("result")
        if isinstance(result, dict):
            flow = result.get("flow", "")
            match = re.search(r"cookie=(0x[0-9a-fA-F]+)", flow)
            if match:
                cookies.add(match.group(1))
    return cookies


def _filter_observation(context, cookies):
    """Remove flows matching the given cookies from the observation."""
    if not cookies:
        return
    for switch_data in context.get("observation", {}).get("switches", {}).values():
        flows = switch_data.get("active_service_flows", [])
        filtered = [
            flow for flow in flows if not any(cookie in flow for cookie in cookies)
        ]
        switch_data["active_service_flows"] = filtered


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run", action="store_true", help="send one inference only")
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
    # Apply production changes: simplified handoff, filtered observation, strengthened prompt
    handoff = evidence["handoffs"][0]
    context["handoff"] = route_handoff_view(handoff)
    s1_cookies = _extract_s1_cookies(handoff)
    _filter_observation(context, s1_cookies)
    # Also apply the strengthened system prompt from production
    from examples.noms27.recovery_campaign.s2 import ROUTE_INSTRUCTIONS

    payload["messages"][0]["content"] = ROUTE_INSTRUCTIONS
    payload["messages"][1]["content"] = json.dumps(context)
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
