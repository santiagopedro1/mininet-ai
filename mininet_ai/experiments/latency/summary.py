"""Descriptive measured populations derived exclusively from retained evidence."""

from collections import Counter
from statistics import mean, median

OUTCOMES = (
    "recovered",
    "timeout",
    "invalid",
    "setup_failed",
    "injection_failed",
    "no_observed_disruption",
    "error",
)
METRICS = (
    "recovery",
    "detection",
    "reasoning",
    "action_effect",
    "execution",
    "verification",
)


def derive_summary(records):
    """Return a summary payload (no envelope); incomplete prefixes stay incomplete."""
    manifest = records[0]
    events = [r for r in records if "type" in r]
    starts = {r["trial_id"]: r for r in events if r["type"] == "trial_started"}
    ends = {r["trial_id"]: r for r in events if r["type"] == "trial_ended"}
    cleanups = {r["trial_id"]: r for r in events if r["type"] == "cleanup_completed"}
    trials = [r for r in records if "cleanup_outcome" in r]
    agents = [r for r in records if "switch_id" in r]
    by_id = {r["record_id"]: r for r in records}
    terminals = [r for r in events if r["type"] in {"run_finished", "run_aborted"}]
    complete = bool(terminals) and all(
        i in ends
        and i in cleanups
        and any(t["trial_id"] == i for t in trials)
        and sum(a["trial_id"] == i for a in agents) == 10
        for i in starts
    )
    state = (
        ("aborted" if terminals[-1]["type"] == "run_aborted" else "completed")
        if complete
        else "incomplete"
    )
    counts = {}
    for kind in ("warmup", "measured"):
        ids = {i for i, r in starts.items() if r["data"]["trial_kind"] == kind}
        outcomes = Counter(ends[i]["data"]["outcome"] for i in ids if i in ends)
        requested = manifest["configuration"][f"{kind}_attempts"]
        counts[kind] = {
            "requested": requested,
            "started": len(ids),
            "finalized": len(ids & ends.keys()),
            "cleanup_completed": len(ids & cleanups.keys()),
            "unknown": len(ids - ends.keys()),
            "unstarted": requested - len(ids),
            "outcomes": {key: outcomes[key] for key in OUTCOMES},
        }
    measured = [t for t in trials if t["trial_kind"] == "measured"]

    def group(**values):
        return (
            dict.fromkeys(
                ("agent_id", "failed_link", "trial_outcome", "operation_outcome")
            )
            | values
        )

    def groups(metric):
        yield group()
        for link in manifest["scenario"]["eligible_failure_links"]:
            yield group(failed_link=link)
        if metric != "recovery":
            for a in manifest["agents"]:
                yield group(agent_id=a["agent_id"])
        for outcome in OUTCOMES:
            yield group(trial_outcome=outcome)
        if metric in {"execution", "verification", "reasoning"}:
            outcomes = sorted(
                {
                    op["outcome"]
                    for a in agents
                    for op in a["operations"]
                    if op["phase"] == metric and op["outcome"] is not None
                }
            )
            for outcome in outcomes:
                yield group(operation_outcome=outcome)

    def matches(g, t, a=None, op=None):
        if op is None and a is not None and g["operation_outcome"] is not None:
            op = next(
                (
                    p
                    for p in a["operations"]
                    if p["phase"] == "reasoning"
                    and p["duration"] == a["measurements"]["reasoning"]
                ),
                None,
            )
        actual = {
            "agent_id": a["agent_id"] if a else None,
            "failed_link": t["failed_link"],
            "trial_outcome": t["outcome"],
            "operation_outcome": op["outcome"] if op else None,
        }
        return all(value is None or value == actual[key] for key, value in g.items())

    distributions = []
    for metric in METRICS:
        for g in groups(metric):
            sources = []
            for trial in measured:
                if metric == "recovery":
                    if matches(g, trial):
                        sources.append((trial["measurements"][metric], False))
                    continue
                for agent in agents:
                    if agent["trial_id"] != trial["trial_id"]:
                        continue
                    if metric in {"execution", "verification"}:
                        sources.extend(
                            (op["duration"], op["late"])
                            for op in agent["operations"]
                            if op["phase"] == metric and matches(g, trial, agent, op)
                        )
                    elif matches(g, trial, agent):
                        d = agent["measurements"][metric]
                        end = by_id.get(d["end_record_id"])
                        sources.append(
                            (
                                d,
                                bool(
                                    end
                                    and int(end["tick_ns"])
                                    > int(ends[trial["trial_id"]]["tick_ns"])
                                ),
                            )
                        )
            availability = Counter(d["availability"] for d, _ in sources)
            values = [
                d["value_seconds"]
                for d, late in sources
                if d["availability"] == "available" and not late
            ]
            distributions.append(
                dict(
                    metric=metric,
                    population="trial"
                    if metric == "recovery"
                    else "action"
                    if metric in {"execution", "verification"}
                    else "agent_trial",
                    unit="seconds",
                    trial_kind="measured",
                    group=g,
                    complete=complete,
                    **{
                        k: availability[k]
                        for k in ("available", "unavailable", "not_applicable")
                    },
                    **{
                        f"{k}_reasons": dict(
                            Counter(
                                d["reason"]
                                for d, _ in sources
                                if d["availability"] == k
                            )
                        )
                        for k in ("unavailable", "not_applicable")
                    },
                    included=len(values),
                    late_excluded=sum(
                        d["availability"] == "available" and late for d, late in sources
                    ),
                    count=len(values),
                    mean_seconds=mean(values) if values else None,
                    median_seconds=median(values) if values else None,
                    min_seconds=min(values) if values else None,
                    max_seconds=max(values) if values else None,
                )
            )
    rates = []
    for metric in (
        "recovery_all_started",
        "recovery_valid_disrupted",
        "detection_participation",
        "action_participation",
    ):
        gs = [
            group(),
            *(
                group(failed_link=p)
                for p in manifest["scenario"]["eligible_failure_links"]
            ),
        ]
        if metric.endswith("participation"):
            gs.extend(group(agent_id=a["agent_id"]) for a in manifest["agents"])
        for g in gs:
            eligible = [t for t in measured if matches(g, t)]
            numerator = sum(t["outcome"] == "recovered" for t in eligible)
            if metric == "recovery_all_started":
                denominator = sum(
                    r["data"]["trial_kind"] == "measured"
                    and (
                        g["failed_link"] is None
                        or r["data"]["failed_link"] == g["failed_link"]
                    )
                    for r in starts.values()
                )
            elif metric == "recovery_valid_disrupted":
                denominator = sum(
                    t["outcome"] in {"recovered", "timeout"} for t in eligible
                )
            else:
                opportunities = [
                    (t, a)
                    for t in measured
                    if t["fault_record_id"] is not None
                    for a in agents
                    if a["trial_id"] == t["trial_id"] and matches(g, t, a)
                ]
                denominator = len(opportunities)
                numerator = sum(
                    any(
                        e["trial_id"] == t["trial_id"]
                        and e["data"].get("agent_id") == a["agent_id"]
                        and (
                            e["type"] == "recognition"
                            if metric == "detection_participation"
                            else e["type"] == "operation_started"
                            and e["data"]["phase"] == "action_attempt"
                        )
                        for e in events
                    )
                    for t, a in opportunities
                )
            rates.append(
                {
                    "metric": metric,
                    "trial_kind": "measured",
                    "group": g,
                    "complete": complete,
                    "numerator": numerator,
                    "denominator": denominator,
                    "value": numerator / denominator if denominator else None,
                }
            )
    return {
        "state": state,
        "terminal_reason": terminals[-1]["data"]["reason"] if terminals else None,
        "counts": counts,
        "distributions": distributions,
        "rates": rates,
    }
