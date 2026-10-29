"""Current-only diagnosis evaluation through the real experiment lifecycle.

The label oracle is evidence analysis only. It never selects the model's answer
or gates production inference.
"""

from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
from threading import Condition
from typing import Any

from examples.diamond_recovery.cleanup import DiagnosisCleanupObserver
from mininet_ai.agents import register_builtin_providers
from mininet_ai.compiler import DeploymentPlan, save_deployment_plan
from mininet_ai.experiment import ExperimentRuntime
from mininet_ai.plugins import ProviderRegistries
from mininet_ai.runtime.ledger import SQLiteRunLedger
from mininet_ai.substrates import SubstrateRuntime


def expected_diagnosis(context: dict[str, Any]) -> str:
    """Label the exact supplied current local inputs, independently of inference."""
    switch = context["switch"]
    (target,) = switch["targets"]
    expected = switch.get("expectations", {})
    diagnosis = switch["diagnosis"]
    if not expected or not expected.get("ports") or not expected.get("flows"):
        return "degraded"
    values = {}
    for name in ("flowState", "portState"):
        observation = diagnosis.get(name, {})
        value = observation.get("value")
        if observation.get("availability") != "available" or not isinstance(
            value, dict
        ):
            return "degraded"
        if not isinstance(value.get(target), dict):
            return "degraded"
        values[name] = value[target]
    ports = values["portState"].get("ports", [])
    if not ports or any(port.get("state") != "up" for port in ports):
        return "degraded"
    if not set(expected["ports"]).issubset({port.get("name") for port in ports}):
        return "degraded"
    switches = values["flowState"].get("switches", [])
    local = [item for item in switches if item.get("name") == target]
    if len(local) != 1:
        return "degraded"
    actual = local[0].get("flows", [])
    if any(
        not any(
            all(flow.get(key) == value for key, value in required.items())
            for flow in actual
        )
        for required in expected["flows"]
    ):
        return "degraded"
    return "healthy"


def evaluate_runtime(
    plan: DeploymentPlan,
    substrate: SubstrateRuntime,
    evidence: Path,
    *,
    timeout_seconds: float = 30,
    decision_transport: Any = None,
    cleanup_observer: DiagnosisCleanupObserver | None = None,
) -> dict[str, Any]:
    """Run all compiled agents on cadence, retain normalized records, then drain."""
    evidence.mkdir(parents=True, exist_ok=False)
    save_deployment_plan(plan, evidence / "plan.json")
    registries = ProviderRegistries()
    register_builtin_providers(registries, substrate)
    completed: set[str] = set()
    condition = Condition()
    evaluations: list[dict[str, Any]] = []

    def retain(record) -> None:
        serialized = record.model_dump(mode="json", by_alias=True)
        result = record.result
        evaluation: dict[str, Any] = {
            "agentId": record.agent_id,
            "invocationId": result.invocation_id,
            "status": result.status.value,
            "correct": False,
            "warmth": "startup-probed; cold inference excluded",
        }
        if result.response is not None:
            execution = result.response.metadata.get("executionRecord", {})
            decision = execution.get("runtime", {}).get("boundedDecision", {})
            if decision.get("request", {}).get("state"):
                context = json.loads(decision["request"]["state"])
                evaluation["expected"] = expected_diagnosis(context)
                evaluation["observed"] = decision.get("selectedChoice")
                evaluation["correct"] = (
                    result.status.value == "succeeded"
                    and evaluation["expected"] == evaluation["observed"]
                )
        with condition:
            with (evidence / "invocations.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(serialized, sort_keys=True) + "\n")
            evaluations.append(evaluation)
            completed.add(record.agent_id)
            condition.notify_all()

    with closing(SQLiteRunLedger(evidence / "ledger.sqlite3")) as ledger:
        runtime = ExperimentRuntime(
            plan,
            substrate,
            registries,
            ledger=ledger,
            decision_transport=decision_transport,
            invocation_listener=retain,
        )
        try:
            runtime.start()
        except BaseException as error:
            causes = []
            cause: BaseException | None = error
            while cause is not None:
                detail = {
                    "type": type(cause).__name__,
                    "code": getattr(cause, "code", None),
                    "message": str(cause),
                }
                if hasattr(cause, "native_response"):
                    detail["nativeResponse"] = getattr(cause, "native_response", None)
                if getattr(cause, "status_code", None) is not None:
                    detail["httpStatus"] = getattr(cause, "status_code", None)
                    detail["bodyBase64"] = getattr(cause, "response_body_base64", None)
                    detail["bodyUtf8"] = getattr(cause, "response_body", None)
                causes.append(detail)
                cause = cause.__cause__
            (evidence / "startup-failure.json").write_text(
                json.dumps(
                    {
                        "error": f"{type(error).__name__}: {error}",
                        "causes": causes,
                        "cleanup": runtime.cleanup.model_dump(
                            mode="json", by_alias=True
                        ),
                    },
                    indent=2,
                )
                + "\n"
            )
            raise
        try:
            with condition:
                all_completed = condition.wait_for(
                    lambda: set(runtime.agent_ids).issubset(completed), timeout_seconds
                )
        finally:
            try:
                if cleanup_observer is not None:
                    cleanup_observer.capture()
            finally:
                report = runtime.stop(timeout_seconds=30)
            (evidence / "runtime-report.json").write_text(
                report.model_dump_json(by_alias=True, indent=2) + "\n"
            )
        cleanup = (
            cleanup_observer.verify()
            if cleanup_observer is not None
            else report.cleanup
        )
        summary = {
            "allAgentsCompleted": all_completed,
            "samples": len(evaluations),
            "correct": sum(item["correct"] for item in evaluations),
            "evaluations": evaluations,
            "operation": report.operation.value,
            "cleanup": cleanup.model_dump(mode="json", by_alias=True),
            "runtimeCleanup": report.cleanup.model_dump(mode="json", by_alias=True),
            "cleanupSource": "independent trusted-diagnosis observer"
            if cleanup_observer is not None
            else "Substrate runtime",
            "passed": all_completed
            and bool(evaluations)
            and all(item["correct"] for item in evaluations)
            and report.operation.value == "succeeded"
            and all(
                issue.phase == "cleanup"
                and issue.code == "experiment.cleanup.unverified"
                for issue in report.issues
            )
            and cleanup.verified,
        }
        (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        return summary
