"""THROWAWAY presentation preview; no Mininet, providers, or runtime changes.

Question: do source labels and visibility make these synthetic runs readable?
Run: python mininet_ai/prototype_log_output.py
Options: --scenario normal|failure --view default|verbose|saved|json
         --layout timeline|source-first|blocks --no-color

Timeline is the agreed baseline. Source-first and blocks are alternative
terminal layouts for comparison, not approved changes. JSON is a synthetic
command-result fixture, not a proposed schema. All data is in memory.
"""

import argparse
import json
import os
import sys


def events(failure):
    # Synthetic safe summaries, not recordings or new production payloads.
    rows = [
        (
            "14:32:00",
            "INFO",
            "Run",
            "Starting experiment",
            True,
            {"event": "run.starting"},
        ),
        (
            "14:32:01",
            "INFO",
            "Mininet",
            "Starting switches",
            False,
            {"event": "native.diagnostic", "dependency": "mininet"},
        ),
        ("14:32:02", "INFO", "Run", "Network ready", True, {"event": "run.ready"}),
        (
            "14:32:03",
            "INFO",
            "Agent:monitor-1",
            "Invocation started",
            False,
            {
                "event": "agent.invocation.started",
                "agent": "monitor-1",
                "invocation": "inv-1",
            },
        ),
        (
            "14:32:04",
            "INFO",
            "Agent:monitor-1",
            "Model request started",
            False,
            {
                "event": "model.request.started",
                "agent": "monitor-1",
                "invocation": "inv-1",
            },
        ),
        (
            "14:32:05",
            "INFO",
            "Agent:monitor-1",
            "Model request completed",
            False,
            {
                "event": "model.request.completed",
                "agent": "monitor-1",
                "invocation": "inv-1",
            },
        ),
        (
            "14:32:06",
            "INFO",
            "Agent:monitor-1",
            "Congestion detected; proposed a link update",
            True,
            {
                "event": "agent.invocation.completed",
                "agent": "monitor-1",
                "invocation": "inv-1",
                "proposals": 1,
            },
        ),
        (
            "14:32:07",
            "INFO",
            "Mininet",
            "Executing link update",
            False,
            {
                "event": "capability.execution.started",
                "agent": "monitor-1",
                "invocation": "inv-1",
                "id": "request-1",
            },
        ),
    ]
    if failure:
        rows += [
            (
                "14:32:08",
                "ERROR",
                "Mininet",
                "Link update failed",
                True,
                {
                    "event": "capability.execution.completed",
                    "agent": "monitor-1",
                    "invocation": "inv-1",
                    "request_id": "request-1",
                    "status": "failed",
                    "changed": False,
                },
            ),
            (
                "14:32:09",
                "WARNING",
                "Run",
                "Provider diagnostic; agent identity unavailable",
                True,
                {
                    "event": "dependency.warning",
                    "dependency": "agno",
                    "agent": "unknown",
                },
            ),
            (
                "14:32:10",
                "INFO",
                "Run",
                "Stopping after runtime failure",
                True,
                {"event": "run.stopping"},
            ),
            (
                "14:32:11",
                "INFO",
                "Mininet",
                "Stopping switches",
                False,
                {"event": "native.diagnostic", "dependency": "mininet"},
            ),
            (
                "14:32:12",
                "ERROR",
                "Run",
                "Cleanup failed; experiment not finalized",
                True,
                {"event": "run.cleanup.failed"},
            ),
        ]
    else:
        rows += [
            (
                "14:32:08",
                "INFO",
                "Mininet",
                "Link updated",
                True,
                {
                    "event": "capability.execution.completed",
                    "agent": "monitor-1",
                    "invocation": "inv-1",
                    "request_id": "request-1",
                    "status": "succeeded",
                    "changed": True,
                },
            ),
            (
                "14:32:09",
                "INFO",
                "Agent:monitor-2",
                "Invocation started",
                False,
                {
                    "event": "agent.invocation.started",
                    "agent": "monitor-2",
                    "invocation": "inv-2",
                },
            ),
            (
                "14:32:10",
                "INFO",
                "Agent:monitor-2",
                "No congestion detected\n[bold]No further changes proposed[/bold]",
                True,
                {
                    "event": "agent.invocation.completed",
                    "agent": "monitor-2",
                    "invocation": "inv-2",
                    "proposals": 0,
                },
            ),
            (
                "14:32:11",
                "INFO",
                "Agent:monitor-2",
                "Shared state updated",
                False,
                {
                    "event": "shared-state.updated",
                    "agent": "monitor-2",
                    "invocation": "inv-2",
                },
            ),
            (
                "14:32:12",
                "INFO",
                "Mininet",
                "Link already configured",
                False,
                {
                    "event": "capability.execution.completed",
                    "agent": "monitor-2",
                    "invocation": "inv-2",
                    "request_id": "request-2",
                    "status": "succeeded",
                    "changed": False,
                },
            ),
            (
                "14:32:13",
                "INFO",
                "Run",
                "Stopping experiment",
                True,
                {"event": "run.stopping"},
            ),
            (
                "14:32:14",
                "INFO",
                "Mininet",
                "Stopping switches",
                False,
                {"event": "native.diagnostic", "dependency": "mininet"},
            ),
            (
                "14:32:15",
                "INFO",
                "Run",
                "Experiment completed",
                True,
                {"event": "run.completed"},
            ),
        ]
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=["normal", "failure"], default="normal")
    parser.add_argument(
        "--view", choices=["default", "verbose", "saved", "json"], default="default"
    )
    parser.add_argument(
        "--layout", choices=["timeline", "source-first", "blocks"], default="timeline"
    )
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args()
    rows = events(args.scenario == "failure")
    terminal = args.view in ("default", "verbose", "json")
    stream = sys.stderr if terminal else sys.stdout
    color = (
        terminal
        and stream.isatty()
        and not args.no_color
        and "NO_COLOR" not in os.environ
    )

    def style(text, code):
        return f"\033[{code}m{text}\033[0m" if color else text

    # Preview status goes to stderr; JSON stdout contains only the fixture.
    print(
        f"PROTOTYPE ONLY | scenario={args.scenario} view={args.view} "
        f"layout={args.layout} color={color} | synthetic data, no runtime capture",
        file=sys.stderr,
    )
    previous = None
    for time, level, source, message, visible, metadata in rows:
        if args.view in ("default", "json") and not visible:
            continue
        if args.view == "saved":
            fields = {**metadata, "message": message}
            detail = " ".join(
                f"{key}={json.dumps(value)}" for key, value in fields.items()
            )
            # Keep run_id immediately after severity for existing export compatibility.
            print(
                f'{"2026-10-08"}T{time}+00:00 {level} run_id="preview-run" [{source}] {detail}'
            )
            continue
        label = style(
            f"[{source}]",
            "36" if source == "Run" else "34" if source == "Mininet" else "35",
        )
        severity = style(
            f"{level:<7}",
            "31" if level == "ERROR" else "33" if level == "WARNING" else "0",
        )
        # Terminal identifiers only where needed to connect an action or warning.
        keys = (
            ("agent", "request_id")
            if source == "Mininet"
            else ("dependency", "agent")
            if source == "Run"
            else ()
        )
        context = "".join(f" {key}={metadata[key]}" for key in keys if key in metadata)
        if args.view == "verbose":
            context += " " + " ".join(
                f"{key}={json.dumps(value)}"
                for key, value in metadata.items()
                if key not in keys
            )
        if args.layout == "blocks" and source != previous:
            print(f"--- {label} ---", file=stream)
        previous = source
        for line in message.split("\n"):
            if args.layout == "source-first":
                text = f"{label} {severity} {time} {line}{context}"
            elif args.layout == "blocks":
                text = f"{time} {label} {severity}\n  {label} {line}{context}"
            else:
                text = f"{time} {severity} {label} {line}{context}"
            print(text, file=stream)
    if args.view == "json":
        print(
            json.dumps(
                {
                    "prototype": True,
                    "run_id": "preview-run",
                    "status": "failed" if args.scenario == "failure" else "completed",
                }
            )
        )


if __name__ == "__main__":
    main()
