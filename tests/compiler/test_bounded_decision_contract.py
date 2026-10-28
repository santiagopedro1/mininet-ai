import json
import shutil
from copy import deepcopy
from threading import Event
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from mininet_ai.compiler import (
    compile_experiment,
    load_deployment_plan,
    save_deployment_plan,
)
from mininet_ai.sdk import ExecutionCatalog
from mininet_ai.specification.models import AgentBlueprint, Experiment
from tests.specification_fixtures import COMPILER_MULTILAYER_SPECIFICATION


def bounded_blueprint():
    return {
        "apiVersion": "mininet-ai/v1alpha5",
        "kind": "AgentBlueprint",
        "metadata": {"name": "decision-maker"},
        "implementation": {"type": "declarative"},
        "execution": {"kind": "bounded-decision", "scenario": "diamond-policy"},
        "model": {
            "provider": "llamacpp-systemone",
            "name": "test-model",
            "parameters": {
                "endpoint": "http://127.0.0.1:8080",
                "expectedIdentity": {
                    "repository": "org/model",
                    "revision": "abc123",
                    "artifact": "model.gguf",
                    "sha256": "0" * 64,
                },
            },
        },
    }


def test_explicit_bounded_blueprint_requires_native_model_identity_and_scenario():
    blueprint = AgentBlueprint.model_validate(bounded_blueprint())

    assert blueprint.execution.kind == "bounded-decision"
    assert blueprint.execution.scenario == "diamond-policy"


@pytest.mark.parametrize(
    "parameters",
    [
        {"endpoint": "http://127.0.0.1:8080", "extra": "not-allowed"},
        {"endpoint": "http://user:secret@127.0.0.1:8080"},
        {"endpoint": "http://127.0.0.1:8080?query=bad"},
    ],
)
def test_bounded_model_parameters_are_allowlisted_and_endpoint_is_safe(parameters):
    document = bounded_blueprint()
    document["model"]["parameters"] = parameters

    with pytest.raises(ValidationError):
        AgentBlueprint.model_validate(document)


def test_v1alpha4_cannot_silently_admit_new_bounded_fields():
    document = bounded_blueprint()
    document["apiVersion"] = "mininet-ai/v1alpha4"
    with pytest.raises(ValidationError, match="requires mininet-ai/v1alpha5"):
        AgentBlueprint.model_validate(document)


def test_published_v1alpha5_and_evidence_schemas_are_strict_and_versioned():
    from pathlib import Path

    schema_root = Path(__file__).parents[2] / "mininet_ai" / "compiler" / "schemas"
    expected = {
        "v1alpha5-experiment.json": "urn:mininet-ai:schema:v1alpha5:experiment",
        "v1alpha5-agent-blueprint.json": "urn:mininet-ai:schema:v1alpha5:agent-blueprint",
        "v1alpha5-decision-scenario.json": "urn:mininet-ai:schema:v1alpha5:decision-scenario",
        "v1alpha5-deployment-plan.json": "urn:mininet-ai:schema:v1alpha5:deployment-plan",
        "bounded-decision-v1alpha1.json": "urn:mininet-ai:schema:bounded-decision:v1alpha1",
        "agent-execution-v1alpha1.json": "urn:mininet-ai:schema:agent-execution:v1alpha1",
    }
    for filename, schema_id in expected.items():
        schema = json.loads((schema_root / filename).read_text())
        Draft202012Validator.check_schema(schema)
        assert schema["$id"] == schema_id
        assert schema["additionalProperties"] is False


def test_bounded_model_requires_well_formed_full_identity():
    document = bounded_blueprint()
    document["model"]["parameters"]["expectedIdentity"]["sha256"] = "bad"
    with pytest.raises(ValidationError, match="SHA-256"):
        AgentBlueprint.model_validate(document)


def test_experiment_resolves_inline_decision_scenario_declaration():
    experiment = Experiment.model_validate(
        {
            "apiVersion": "mininet-ai/v1alpha5",
            "kind": "Experiment",
            "metadata": {"name": "bounded"},
            "substrate": {"driver": "fake", "topology": "topology.yaml"},
            "blueprints": [bounded_blueprint()],
            "decisionScenarios": [
                {
                    "apiVersion": "mininet-ai/v1alpha5",
                    "kind": "DecisionScenario",
                    "metadata": {"name": "diamond-policy"},
                    "instructions": "Choose exactly one declared choice.",
                    "choices": [
                        {"id": "no_action", "description": "Restrain", "proposals": []},
                        {
                            "id": "install_recovery",
                            "description": "Recover",
                            "proposals": [
                                {
                                    "capability": "openflow.flow.install",
                                    "target": "s1",
                                    "arguments": {"match": {}, "actions": []},
                                }
                            ],
                        },
                    ],
                    "paths": {"primary": ["h1", "s1"], "recovery": ["h1", "s2"]},
                    "observations": {
                        "pathProbes": "probes",
                        "linkStates": "links",
                        "policyState": "policy",
                        "actionOutcomes": "outcomes",
                        "historyRounds": 3,
                        "maxAge": "2s",
                    },
                    "timing": {"cadence": "1s", "decisionDeadline": "800ms"},
                    "policy": {"initial": {}, "recovery": {}, "probePolicy": {}},
                }
            ],
        }
    )

    assert experiment.api_version == "mininet-ai/v1alpha5"
    assert experiment.decision_scenarios[0].metadata.name == "diamond-policy"


def _source_for_compile(tmp_path):
    shutil.copytree(
        COMPILER_MULTILAYER_SPECIFICATION.parent / "capabilities",
        tmp_path / "capabilities",
    )
    source = yaml.safe_load(COMPILER_MULTILAYER_SPECIFICATION.read_text())
    source["apiVersion"] = "mininet-ai/v1alpha5"
    source["blueprints"] = [
        {
            **bounded_blueprint(),
            "execution": {"kind": "bounded-decision", "scenario": "diamond-policy"},
        }
    ]
    deployment = next(
        item for item in source["agents"] if item["name"] == "switch-router"
    )
    source["agents"] = [deployment]
    deployment["blueprint"] = "decision-maker"
    deployment["placement"] = {
        "layer": "data",
        "targets": {"kind": "switch", "names": ["s1"]},
        "cardinality": "singleton",
        "runtime": "device-sidecar",
    }
    deployment["observe"] = [
        "ovs.port-counters",
        "tc.queue-occupancy",
        "topology.neighbors",
    ]
    deployment["decisionInputs"] = {
        "pathProbes": "ovs.port-counters",
        "linkStates": "tc.queue-occupancy",
        "policyState": "topology.neighbors",
        "actionOutcomes": "tc.queue-occupancy",
    }
    deployment["triggers"] = [{"type": "interval", "name": "decision", "every": "1s"}]
    source["decisionScenarios"] = [
        {
            "apiVersion": "mininet-ai/v1alpha5",
            "kind": "DecisionScenario",
            "metadata": {"name": "diamond-policy"},
            "instructions": "Choose only the action supported by supplied evidence.",
            "choices": [
                {"id": "no_action", "description": "Preserve policy", "proposals": []},
                {
                    "id": "install_recovery",
                    "description": "Install fixed recovery policy",
                    "proposals": [
                        {
                            "capability": "openflow.flow.install",
                            "target": "s1",
                            "arguments": {"match": {"step": 1}, "actions": []},
                        },
                        {
                            "capability": "openflow.flow.install",
                            "target": "s1",
                            "arguments": {"match": {"step": 2}, "actions": []},
                        },
                    ],
                },
            ],
            "paths": {
                "primary": ["h1", "s1", "s2", "h2"],
                "recovery": ["h1", "s1", "s2", "h2"],
            },
            "observations": {
                "pathProbes": "probes",
                "linkStates": "links",
                "policyState": "policy",
                "actionOutcomes": "outcomes",
                "historyRounds": 3,
                "maxAge": "2s",
            },
            "timing": {"cadence": "1s", "decisionDeadline": "800ms"},
            "policy": {"initial": {}, "recovery": {}, "probePolicy": {}},
        }
    ]
    source["policies"]["conflicting-actions"] = "serialize"
    path = tmp_path / "experiment.yaml"
    path.write_text(yaml.safe_dump(source))
    return path


def test_v1alpha5_source_compiles_saves_and_reloads_with_embedded_scenario(tmp_path):
    source = _source_for_compile(tmp_path)
    compiled = compile_experiment(source)
    saved = save_deployment_plan(compiled, tmp_path / "saved-plan.json")
    restored = load_deployment_plan(saved)

    assert restored.api_version == "mininet-ai/v1alpha5"
    assert restored.digest == compiled.digest
    assert (
        restored.snapshot["decisionScenarios"][0]["metadata"]["name"]
        == "diamond-policy"
    )
    definition = ExecutionCatalog(restored).resolve(restored.agents[0].id)
    assert definition.blueprint.execution.kind == "bounded-decision"
    assert restored.agents[0].decision_scenario["choices"][0]["id"] == "no_action"


def test_inline_and_document_scenario_references_compile_to_same_embedded_content(
    tmp_path,
):
    source = _source_for_compile(tmp_path)
    inline = compile_experiment(source)
    document = yaml.safe_load(source.read_text())
    scenario = document["decisionScenarios"][0]
    (tmp_path / "decision-scenario.yaml").write_text(yaml.safe_dump(scenario))
    document["decisionScenarios"] = ["decision-scenario.yaml"]
    source.write_text(yaml.safe_dump(document))

    referenced = compile_experiment(source)

    assert (
        referenced.snapshot["decisionScenarios"] == inline.snapshot["decisionScenarios"]
    )
    assert referenced.agents[0].decision_scenario == inline.agents[0].decision_scenario


def test_saved_plan_runs_native_no_action_through_experiment_lifecycle(
    tmp_path, monkeypatch
):
    import importlib

    from mininet_ai import experiment as experiment_module
    from mininet_ai.agents import register_builtin_providers
    from mininet_ai.audit import AuditEventType, AuditRecorder, MemoryAuditSink
    from mininet_ai.experiment import ExperimentRuntime
    from mininet_ai.plugins import ProviderRegistries
    from mininet_ai.substrates import FakeSubstrateRuntime

    class NativeTransport:
        def get_json(self, url, **kwargs):
            return {"status": "ok"} if url.endswith("/health") else {}

        def post_json(self, url, payload, **kwargs):
            assert url.endswith("/v1/systemone")
            assert tuple(payload["questions"]) == ("next_action",)
            return {
                "answers": {"next_action": {"type": "choice", "choice": "no_action"}}
            }

    source = _source_for_compile(tmp_path)
    plan = compile_experiment(source)
    saved = save_deployment_plan(plan, tmp_path / "saved-plan.json")
    restored = load_deployment_plan(saved)
    substrate = FakeSubstrateRuntime()
    registries = ProviderRegistries()
    register_builtin_providers(registries, substrate)
    audit_sink = MemoryAuditSink()
    completed = Event()
    results: list[Any] = []

    def record_result(record):
        results.append(record.result)
        completed.set()

    def forbidden_agno_factory(*args, **kwargs):
        raise AssertionError("bounded execution must not construct Agno")

    monkeypatch.setattr(experiment_module, "AgnoAgentFactory", forbidden_agno_factory)
    agent_runtime_module = importlib.import_module("mininet_ai.agents.runtime")
    monkeypatch.setattr(
        agent_runtime_module, "AgnoAgentFactory", forbidden_agno_factory
    )
    runtime = ExperimentRuntime(
        restored,
        substrate,
        registries,
        audit=AuditRecorder(audit_sink),
        decision_transport=NativeTransport(),
        invocation_listener=record_result,
    )
    runtime.start()
    try:
        assert completed.wait(3), "scheduled bounded no_action did not complete"
    finally:
        runtime.stop()

    result = results[0]
    assert result.status.value == "succeeded"
    assert result.response is not None and result.response.proposals == ()
    assert result.response.metadata["selectedChoice"] == "no_action"
    assert (
        result.response.metadata["executionRecord"]["executionKind"]
        == "bounded-decision"
    )
    evidence = result.response.metadata["executionRecord"]["runtime"]["boundedDecision"]
    assert evidence["selectedChoice"] == "no_action"
    assert evidence["request"]["url"].endswith("/v1/systemone")
    decision_context = json.loads(evidence["request"]["state"])
    assert (
        decision_context["apiVersion"] == "mininet-ai/bounded-decision-context/v1alpha1"
    )
    assert decision_context["history"]["requiredRounds"] == 3
    assert "intent" not in decision_context and "sharedState" not in decision_context
    completed_events = [
        event
        for event in audit_sink.events
        if event.type == AuditEventType.AGENT_COMPLETED
    ]
    assert len(completed_events) == 1
    assert (
        completed_events[0].data["runtime"]["runtime"]["boundedDecision"][
            "selectedChoice"
        ]
        == "no_action"
    )


def test_late_native_result_is_discarded_at_exact_deadline(tmp_path):
    from mininet_ai.agents import register_builtin_providers
    from mininet_ai.experiment import ExperimentRuntime
    from mininet_ai.plugins import ProviderRegistries
    from mininet_ai.substrates import FakeSubstrateRuntime

    class ControlledClock:
        now = 0.0

        def __call__(self):
            return self.now

    class LateTransport:
        def get_json(self, url, **kwargs):
            return {"status": "ok"} if url.endswith("/health") else {}

        def post_json(self, url, payload, **kwargs):
            if kwargs["timeout_seconds"] <= 0.8:
                self.timeout = kwargs["timeout_seconds"]
                clock.now = 0.8
            return {
                "answers": {"next_action": {"type": "choice", "choice": "no_action"}}
            }

    clock = ControlledClock()
    transport = LateTransport()
    plan = compile_experiment(_source_for_compile(tmp_path))
    substrate = FakeSubstrateRuntime()
    registries = ProviderRegistries()
    register_builtin_providers(registries, substrate)
    completed = Event()
    results = []

    def record_result(record):
        results.append(record.result)
        completed.set()

    runtime = ExperimentRuntime(
        plan,
        substrate,
        registries,
        decision_transport=transport,
        monotonic_clock=clock,
        invocation_listener=record_result,
    )
    runtime.start()
    try:
        assert completed.wait(3), "bounded decision did not complete"
    finally:
        runtime.stop()

    assert transport.timeout == 0.8
    assert results[0].status.value == "failed"
    assert results[0].issue.code == "agent.bounded-decision.timeout"
    assert results[0].response is not None
    evidence = results[0].response.metadata["executionRecord"]["runtime"][
        "boundedDecision"
    ]
    assert evidence["lateResultDiscarded"] is True
    assert evidence["selectedChoice"] == "unavailable"
    assert evidence["remoteTerminalStatus"] == "unknown"
    assert evidence["timeoutStatus"] == "expired"


def test_cancellation_revokes_action_authority_and_retains_remote_unknown(tmp_path):
    from mininet_ai.agents import register_builtin_providers
    from mininet_ai.experiment import ExperimentRuntime
    from mininet_ai.plugins import ProviderRegistries
    from mininet_ai.substrates import FakeSubstrateRuntime

    entered, release = Event(), Event()

    class BlockingTransport:
        def get_json(self, url, **kwargs):
            return {"status": "ok"} if url.endswith("/health") else {}

        def post_json(self, url, payload, **kwargs):
            if kwargs["timeout_seconds"] <= 0.8:
                entered.set()
                assert release.wait(2)
            return {
                "answers": {
                    "next_action": {
                        "type": "choice",
                        "choice": "install_recovery",
                    }
                }
            }

    plan = compile_experiment(_source_for_compile(tmp_path))

    class RecordingSubstrate(FakeSubstrateRuntime):
        def __init__(self):
            super().__init__()
            self.mutations = []

        def execute(self, run_id, request):
            self.mutations.append(request)
            return super().execute(run_id, request)

    substrate = RecordingSubstrate()
    registries = ProviderRegistries()
    register_builtin_providers(registries, substrate)
    completed = Event()
    results = []

    def record_result(record):
        results.append(record.result)
        completed.set()

    runtime = ExperimentRuntime(
        plan,
        substrate,
        registries,
        decision_transport=BlockingTransport(),
        invocation_listener=record_result,
    )
    runtime.start()
    try:
        assert entered.wait(2), "native inference did not start"
        assert runtime.cancel_bounded_decision(plan.agents[0].id)
        release.set()
        assert completed.wait(2), "cancelled inference did not complete locally"
    finally:
        release.set()
        runtime.stop()

    result = results[0]
    assert result.status.value == "failed"
    assert result.response is not None
    evidence = result.response.metadata["executionRecord"]["runtime"][
        "boundedDecision"
    ]
    assert evidence["cancellationStatus"] == "cancelled"
    assert evidence["lateResultDiscarded"] is True
    assert evidence["remoteTerminalStatus"] == "unknown"
    assert evidence["localCancellationStatus"] == "local-authority-revoked"
    assert evidence["admission"]["status"] == "cancelled"
    assert result.action_results == ()
    assert substrate.mutations == []


def test_recovery_selection_executes_fixed_bundle_and_retains_completion_evidence(
    tmp_path,
):
    from mininet_ai.agents import register_builtin_providers
    from mininet_ai.experiment import ExperimentRuntime
    from mininet_ai.plugins import ProviderRegistries
    from mininet_ai.substrates import FakeSubstrateRuntime

    class RecoveryTransport:
        def get_json(self, url, **kwargs):
            return {"status": "ok"} if url.endswith("/health") else {}

        def post_json(self, url, payload, **kwargs):
            return {
                "answers": {
                    "next_action": {
                        "type": "choice",
                        "choice": "install_recovery",
                        "probability": 0.01,
                        "confidence": 0.02,
                    }
                }
            }

    class RecordingSubstrate(FakeSubstrateRuntime):
        def __init__(self):
            super().__init__()
            self.mutations = []

        def execute(self, run_id, request):
            self.mutations.append(request)
            return super().execute(run_id, request)

    source = _source_for_compile(tmp_path)
    plan = load_deployment_plan(
        save_deployment_plan(compile_experiment(source), tmp_path / "saved-plan.json")
    )
    substrate = RecordingSubstrate()
    registries = ProviderRegistries()
    register_builtin_providers(registries, substrate)
    completed = Event()
    results = []

    runtime = ExperimentRuntime(
        plan,
        substrate,
        registries,
        decision_transport=RecoveryTransport(),
        invocation_listener=lambda record: (
            results.append(record.result),
            completed.set(),
        ),
    )
    runtime.start()
    try:
        assert completed.wait(3), "recovery decision did not complete"
    finally:
        runtime.stop()

    assert results[0].status.value == "succeeded", results[0].issue
    assert [item.parameters["match"]["step"] for item in substrate.mutations] == [1, 2]
    assert [item.target for item in substrate.mutations] == ["s1", "s1"]
    assert all(item.name == "openflow.flow.install" for item in substrate.mutations)
    evidence = results[0].response.metadata["executionRecord"]["runtime"][
        "boundedDecision"
    ]
    assert evidence["selectedChoice"] == "install_recovery"
    assert evidence["proposalMapping"]["proposals"][1]["arguments"] == {
        "match": {"step": 2},
        "actions": [],
    }
    assert [item["status"] for item in evidence["actionOutcomes"]] == [
        "succeeded",
        "succeeded",
    ]
    assert evidence["authorization"]["status"] == "authorized"
    assert evidence["admission"]["status"] == "completed"
    assert evidence["response"]["answers"]["next_action"]["confidence"] == 0.02


def test_bounded_decision_records_trusted_primary_policy_inspection(tmp_path):
    from mininet_ai.agents import register_builtin_providers
    from mininet_ai.experiment import ExperimentRuntime
    from mininet_ai.plugins import ProviderRegistries
    from mininet_ai.substrates import FakeSubstrateRuntime

    class NoActionTransport:
        def get_json(self, url, **kwargs):
            return {"status": "ok"} if url.endswith("/health") else {}

        def post_json(self, url, payload, **kwargs):
            return {"answers": {"next_action": {"type": "choice", "choice": "no_action"}}}

    plan = compile_experiment(_source_for_compile(tmp_path))
    substrate = FakeSubstrateRuntime()
    registries = ProviderRegistries()
    register_builtin_providers(registries, substrate)
    completed = Event()
    results = []

    def record_result(invocation):
        results.append(invocation.result)
        completed.set()

    runtime = ExperimentRuntime(
        plan,
        substrate,
        registries,
        decision_transport=NoActionTransport(),
        invocation_listener=record_result,
    )
    runtime.start()
    try:
        assert completed.wait(3)
    finally:
        runtime.stop()

    decision = results[0].response.metadata["executionRecord"]["runtime"]["boundedDecision"]
    assert decision["reconciliation"]["policy"] == "verified-primary"
    assert decision["reconciliation"]["stepStates"] == ["missing", "missing"]


@pytest.mark.parametrize("failed_step", ["bounded-1", "bounded-2"])
def test_partial_recovery_is_reconciled_and_only_fresh_remaining_steps_retry(
    tmp_path, failed_step
):
    from threading import Event

    from mininet_ai.agents import register_builtin_providers
    from mininet_ai.errors import RuntimeOperationError
    from mininet_ai.experiment import ExperimentRuntime
    from mininet_ai.plugins import ProviderRegistries
    from mininet_ai.substrates import FakeSubstrateRuntime

    source = _source_for_compile(tmp_path)
    plan = compile_experiment(source)

    class RetryTransport:
        def __init__(self):
            self.requests = []

        def get_json(self, url, **kwargs):
            return {"status": "ok"} if url.endswith("/health") else {}

        def post_json(self, url, payload, **kwargs):
            if payload.get("state") == "{}":
                choice = "no_action"  # non-mutating startup interface probe
            else:
                self.requests.append(payload)
                state = json.loads(payload["state"])
                probe_state = state["latestObservations"]["pathProbes"]["value"]["s1"]
                choice = (
                    "install_recovery"
                    if probe_state["eligible"]
                    else "no_action"
                )
            return {"answers": {"next_action": {"type": "choice", "choice": choice}}}

    class FailingSubstrate(FakeSubstrateRuntime):
        def __init__(self):
            super().__init__()
            self.failed = False
            self.mutations = []
            self.observation_round = 0

        def observe(self, run_id, query):
            result = super().observe(run_id, query)
            if query.name != "ovs.port-counters":
                return result
            self.observation_round += 1
            eligible = self.observation_round != 2
            return result.model_copy(
                update={
                    "values": {
                        target: {"eligible": eligible}
                        for target in query.targets
                    }
                }
            )

        def execute(self, run_id, request):
            if request.id == failed_step and not self.failed:
                self.failed = True
                raise RuntimeOperationError("injected step failure", code="test.injected")
            self.mutations.append(request)
            return super().execute(run_id, request)

    substrate = FailingSubstrate()
    registries = ProviderRegistries()
    register_builtin_providers(registries, substrate)
    results: list[Any] = []
    completed = Event()

    def record(invocation) -> None:
        results.append(invocation.result)
        completed.set()

    transport = RetryTransport()
    runtime = ExperimentRuntime(
        plan,
        substrate,
        registries,
        decision_transport=transport,
        invocation_listener=record,
    )
    runtime.start()
    try:
        for expected_count in (1, 2, 3):
            assert completed.wait(4), f"invocation {expected_count} did not complete"
            completed.clear()
    finally:
        runtime.stop()

    first, restraint, retry = results
    assert first.status.value == "failed"
    assert [action.request_id for action in first.action_results] == (
        ["bounded-1", "bounded-2"]
    )
    assert [action.status.value for action in first.action_results] == (
        ["failed", "rejected"]
        if failed_step == "bounded-1"
        else ["succeeded", "failed"]
    )
    assert restraint.status.value == "succeeded"
    assert retry.status.value == "succeeded", (retry.issue, retry.response.metadata)
    if failed_step == "bounded-1":
        expected_retry_ids = ["bounded-1", "bounded-2"]
        expected_partial = "verified-primary"
    else:
        expected_retry_ids = ["bounded-2"]
        expected_partial = "partial"
    retry_record = retry.response.metadata["executionRecord"]["runtime"]["boundedDecision"]
    assert [item["id"] for item in retry_record["proposalMapping"]["proposals"]] == expected_retry_ids
    assert retry_record["reconciliation"]["policy"] == expected_partial
    assert retry_record["reconciliation"]["afterActions"]["policy"] == "verified-recovery"
    assert retry_record["authorization"]["status"] == "authorized"
    assert retry_record["admission"]["status"] == "completed"
    assert retry.shared_state_changes == ()
    assert [item.id for item in substrate.mutations] == [
        "bounded-1",
        *expected_retry_ids,
    ] if failed_step == "bounded-2" else expected_retry_ids

    # The second invocation is a fresh model decision while recovery is partial;
    # its no-action choice proves eligibility loss doesn't trigger an auto-retry.
    restraint_state = json.loads(transport.requests[1]["state"])
    assert restraint_state["policy"]["current"]["policy"] == expected_partial
    assert restraint_state["latestObservations"]["pathProbes"]["value"]["s1"][
        "eligible"
    ] is False
    assert restraint_state["policy"]["current"]["stepStates"] == (
        ["missing", "missing"]
        if failed_step == "bounded-1"
        else ["present", "missing"]
    )
    assert restraint.action_results == ()


@pytest.mark.parametrize("inspection", ["wrong", "conflicting", "unreadable"])
def test_unknown_or_wrong_scenario_rules_refuse_retry_with_correlated_evidence(
    tmp_path, inspection
):
    from mininet_ai.agents import OneShotAgentRuntime, register_builtin_providers
    from mininet_ai.plugins import ProviderRegistries
    from mininet_ai.substrates import FakeSubstrateRuntime

    class InstallTransport:
        def get_json(self, url, **kwargs):
            return {"status": "ok"} if url.endswith("/health") else {}

        def post_json(self, url, payload, **kwargs):
            return {"answers": {"next_action": {"type": "choice", "choice": "install_recovery"}}}

    class UntrustedInspectionSubstrate(FakeSubstrateRuntime):
        def __init__(self):
            super().__init__()
            self.mutations = []

        def inspect_scenario_actions(
            self, run_id, expected, *, timeout_seconds=None
        ):
            if inspection == "unreadable":
                raise OSError("injected inspection read failure")
            return ("present", inspection)

        def execute(self, run_id, request):
            self.mutations.append(request)
            return super().execute(run_id, request)

    plan = compile_experiment(_source_for_compile(tmp_path))
    substrate = UntrustedInspectionSubstrate()
    run = substrate.deploy(plan)
    registries = ProviderRegistries()
    register_builtin_providers(registries, substrate)
    runtime = OneShotAgentRuntime(
        plan,
        substrate,
        registries,
        decision_transport=InstallTransport(),
    )

    result = runtime.invoke(run.id, plan.agents[0].id, "evaluate recovery")

    assert result.status.value == "failed"
    assert result.issue is not None
    assert result.response is not None
    assert result.issue.code == "agent.bounded-decision.reconciliation-failed"
    assert substrate.mutations == []
    decision = result.response.metadata["executionRecord"]["runtime"]["boundedDecision"]
    assert decision["selectedChoice"] == "install_recovery"
    assert decision["reconciliation"]["policy"] == "unknown"
    assert decision["runId"] == run.id
    assert decision["correlationId"] == result.invocation_id


def test_redundant_recovery_after_positive_verification_is_failure(tmp_path):
    from mininet_ai.agents import OneShotAgentRuntime, register_builtin_providers
    from mininet_ai.plugins import ProviderRegistries
    from mininet_ai.substrates import FakeSubstrateRuntime

    class InstallTransport:
        def get_json(self, url, **kwargs):
            return {"status": "ok"} if url.endswith("/health") else {}

        def post_json(self, url, payload, **kwargs):
            return {"answers": {"next_action": {"type": "choice", "choice": "install_recovery"}}}

    plan = compile_experiment(_source_for_compile(tmp_path))
    substrate = FakeSubstrateRuntime()
    run = substrate.deploy(plan)
    registries = ProviderRegistries()
    register_builtin_providers(registries, substrate)
    runtime = OneShotAgentRuntime(
        plan,
        substrate,
        registries,
        decision_transport=InstallTransport(),
    )

    recovered = runtime.invoke(run.id, plan.agents[0].id, "recover")
    redundant = runtime.invoke(run.id, plan.agents[0].id, "recover again")

    assert recovered.status.value == "succeeded"
    assert recovered.response.metadata["executionRecord"]["runtime"]["boundedDecision"][
        "reconciliation"
    ]["afterActions"]["policy"] == "verified-recovery"
    assert redundant.status.value == "failed"
    assert redundant.issue is not None
    assert redundant.issue.code == "agent.bounded-decision.redundant-recovery"
    assert len(substrate._scenario_actions[run.id]) == 2


def test_completed_bundle_without_positive_full_verification_fails(tmp_path):
    from mininet_ai.agents import OneShotAgentRuntime, register_builtin_providers
    from mininet_ai.plugins import ProviderRegistries
    from mininet_ai.substrates import FakeSubstrateRuntime

    class InstallTransport:
        def get_json(self, url, **kwargs):
            return {"status": "ok"} if url.endswith("/health") else {}

        def post_json(self, url, payload, **kwargs):
            return {"answers": {"next_action": {"type": "choice", "choice": "install_recovery"}}}

    class VerificationLostSubstrate(FakeSubstrateRuntime):
        def __init__(self):
            super().__init__()
            self.inspections = 0

        def inspect_scenario_actions(
            self, run_id, expected, *, timeout_seconds=None
        ):
            self.inspections += 1
            if self.inspections == 1:
                return ("missing", "missing")
            return ("present", "missing")

    plan = compile_experiment(_source_for_compile(tmp_path))
    substrate = VerificationLostSubstrate()
    run = substrate.deploy(plan)
    registries = ProviderRegistries()
    register_builtin_providers(registries, substrate)
    runtime = OneShotAgentRuntime(
        plan,
        substrate,
        registries,
        decision_transport=InstallTransport(),
    )

    result = runtime.invoke(run.id, plan.agents[0].id, "recover")

    assert result.status.value == "failed"
    assert result.issue is not None
    assert result.response is not None
    assert result.issue.code == "agent.bounded-decision.recovery-verification-failed"
    decision = result.response.metadata["executionRecord"]["runtime"]["boundedDecision"]
    assert [action["stepStatus"] for action in decision["actionOutcomes"]] == [
        "completed",
        "completed",
    ]
    assert decision["reconciliation"]["afterActions"]["policy"] == "partial"
    assert result.shared_state_changes == ()


def test_failed_recovery_step_stops_bundle_and_retains_partial_effects(tmp_path):
    from mininet_ai.agents import register_builtin_providers
    from mininet_ai.errors import RuntimeOperationError
    from mininet_ai.experiment import ExperimentRuntime
    from mininet_ai.plugins import ProviderRegistries
    from mininet_ai.substrates import FakeSubstrateRuntime

    class RecoveryTransport:
        def get_json(self, url, **kwargs):
            return {"status": "ok"} if url.endswith("/health") else {}

        def post_json(self, url, payload, **kwargs):
            return {
                "answers": {
                    "next_action": {"type": "choice", "choice": "install_recovery"}
                }
            }

    class FailingSubstrate(FakeSubstrateRuntime):
        def __init__(self):
            super().__init__()
            self.mutations = []

        def execute(self, run_id, request):
            self.mutations.append(request)
            if request.parameters["match"]["step"] == 1:
                raise RuntimeOperationError(
                    "simulated partial mutation failure", code="test.step.failed"
                )
            return super().execute(run_id, request)

    source = _source_for_compile(tmp_path)
    plan = compile_experiment(source)
    substrate = FailingSubstrate()
    registries = ProviderRegistries()
    register_builtin_providers(registries, substrate)
    completed = Event()
    results = []
    runtime = ExperimentRuntime(
        plan,
        substrate,
        registries,
        decision_transport=RecoveryTransport(),
        invocation_listener=lambda record: (
            results.append(record.result),
            completed.set(),
        ),
    )
    runtime.start()
    try:
        assert completed.wait(3), "failed recovery decision did not complete"
    finally:
        runtime.stop()

    result = results[0]
    assert result.status.value == "failed"
    assert len(substrate.mutations) == 1
    evidence = result.response.metadata["executionRecord"]["runtime"]["boundedDecision"]
    assert [item["stepStatus"] for item in evidence["actionOutcomes"]] == [
        "failed",
        "not-started",
    ]
    assert evidence["actionOutcomes"][0]["effects"] == "unknown"
    assert evidence["authorization"]["status"] == "authorized"
    assert evidence["admission"]["status"] == "failed"
    assert [
        phase["phase"]
        for phase in evidence["timings"]["phaseTransitions"]
    ].count("action-start") == 1
    assert evidence["incompleteEffects"] == [
        {
            "requestId": "bounded-1",
            "status": "failed",
            "effects": "unknown",
        },
        {
            "requestId": "bounded-2",
            "status": "rejected",
            "effects": "unknown",
        },
    ]
    assert evidence["remoteTerminalStatus"] == "unknown"


def test_deadline_between_recovery_steps_preserves_started_effect_and_stops_next(
    tmp_path,
):
    from mininet_ai.agents import register_builtin_providers
    from mininet_ai.experiment import ExperimentRuntime
    from mininet_ai.plugins import ProviderRegistries
    from mininet_ai.substrates import FakeSubstrateRuntime

    class ControlledClock:
        now = 0.0

        def __call__(self):
            return self.now

    class RecoveryTransport:
        def get_json(self, url, **kwargs):
            return {"status": "ok"} if url.endswith("/health") else {}

        def post_json(self, url, payload, **kwargs):
            return {"answers": {"next_action": {"type": "choice", "choice": "install_recovery"}}}

    clock = ControlledClock()

    class AdvancingSubstrate(FakeSubstrateRuntime):
        def __init__(self):
            super().__init__()
            self.mutations = []

        def execute(self, run_id, request):
            self.mutations.append(request)
            result = super().execute(run_id, request)
            clock.now = 0.8
            return result

    plan = compile_experiment(_source_for_compile(tmp_path))
    substrate = AdvancingSubstrate()
    registries = ProviderRegistries()
    register_builtin_providers(registries, substrate)
    completed = Event()
    results = []

    def record_result(record):
        results.append(record.result)
        completed.set()

    runtime = ExperimentRuntime(
        plan,
        substrate,
        registries,
        decision_transport=RecoveryTransport(),
        monotonic_clock=clock,
        invocation_listener=record_result,
    )
    runtime.start()
    completed_on_time = completed.wait(3)
    report = runtime.stop()
    assert completed_on_time, f"bounded recovery did not complete: {report}"

    result = results[0]
    assert result.status.value == "failed"
    assert len(substrate.mutations) == 1
    evidence = result.response.metadata["executionRecord"]["runtime"][
        "boundedDecision"
    ]
    assert [step["stepStatus"] for step in evidence["actionOutcomes"]] == [
        "completed",
        "not-started",
    ]
    assert evidence["actionOutcomes"][0]["effects"] == "changed"
    assert evidence["actionOutcomes"][1]["effects"] == "unknown"
    assert evidence["timeoutStatus"] == "expired"
    assert evidence["admission"]["status"] == "expired"
    assert evidence["incompleteEffects"][0]["effects"] == "unknown"


def test_cancellation_racing_started_action_revokes_only_future_steps(tmp_path):
    from threading import Thread

    from mininet_ai.agents import register_builtin_providers
    from mininet_ai.experiment import ExperimentRuntime
    from mininet_ai.plugins import ProviderRegistries
    from mininet_ai.substrates import FakeSubstrateRuntime

    action_started, finish_action, cancel_returned = Event(), Event(), Event()

    class RecoveryTransport:
        def get_json(self, url, **kwargs):
            return {"status": "ok"} if url.endswith("/health") else {}

        def post_json(self, url, payload, **kwargs):
            return {"answers": {"next_action": {"type": "choice", "choice": "install_recovery"}}}

    class BlockingActionSubstrate(FakeSubstrateRuntime):
        def __init__(self):
            super().__init__()
            self.mutations = []

        def execute(self, run_id, request):
            self.mutations.append(request)
            if request.parameters["match"]["step"] == 1:
                action_started.set()
                assert finish_action.wait(2)
            return super().execute(run_id, request)

    plan = compile_experiment(_source_for_compile(tmp_path))
    substrate = BlockingActionSubstrate()
    registries = ProviderRegistries()
    register_builtin_providers(registries, substrate)
    completed = Event()
    results = []

    def record_result(record):
        results.append(record.result)
        completed.set()

    runtime = ExperimentRuntime(
        plan,
        substrate,
        registries,
        decision_transport=RecoveryTransport(),
        invocation_listener=record_result,
    )
    runtime.start()
    try:
        assert action_started.wait(2), "first bundle action did not start"

        def cancel():
            runtime.cancel_bounded_decision(plan.agents[0].id)
            cancel_returned.set()

        cancellation = Thread(target=cancel)
        cancellation.start()
        assert cancel_returned.wait(1), "local cancellation waited on a started action"
        finish_action.set()
        cancellation.join(2)
        assert completed.wait(2), "cancelled bundle did not complete"
    finally:
        finish_action.set()
        runtime.stop()

    result = results[0]
    assert result.status.value == "failed"
    assert len(substrate.mutations) == 1
    evidence = result.response.metadata["executionRecord"]["runtime"][
        "boundedDecision"
    ]
    assert [item["stepStatus"] for item in evidence["actionOutcomes"]] == [
        "completed",
        "not-started",
    ]
    assert evidence["actionOutcomes"][0]["effects"] == "changed"
    assert evidence["cancellationStatus"] == "cancelled"
    assert evidence["remoteTerminalStatus"] == "unknown"


def test_active_bounded_tick_is_coalesced_and_next_work_waits_for_next_cadence(
    tmp_path,
):
    from threading import Lock

    from mininet_ai.agents import register_builtin_providers
    from mininet_ai.experiment import ExperimentRuntime
    from mininet_ai.plugins import ProviderRegistries
    from mininet_ai.substrates import FakeSubstrateRuntime

    first_entered, release_first, coalesced, second_entered = (
        Event(),
        Event(),
        Event(),
        Event(),
    )
    calls_lock = Lock()

    class ControlledClock:
        now = 0.0

        def __call__(self):
            return self.now

    clock = ControlledClock()

    class CadenceTransport:
        decision_calls = 0

        def get_json(self, url, **kwargs):
            return {"status": "ok"} if url.endswith("/health") else {}

        def post_json(self, url, payload, **kwargs):
            if kwargs["timeout_seconds"] > 0.8:
                return {"answers": {"next_action": {"type": "choice", "choice": "no_action"}}}
            with calls_lock:
                self.decision_calls += 1
                call_number = self.decision_calls
            if call_number == 1:
                first_entered.set()
                assert release_first.wait(4)
            else:
                second_entered.set()
            return {"answers": {"next_action": {"type": "choice", "choice": "no_action"}}}

    plan = compile_experiment(_source_for_compile(tmp_path))
    substrate = FakeSubstrateRuntime()
    registries = ProviderRegistries()
    register_builtin_providers(registries, substrate)
    transport = CadenceTransport()

    def on_issue(issue):
        if issue.code == "runtime.bounded-decision.tick-coalesced":
            coalesced.set()

    runtime = ExperimentRuntime(
        plan,
        substrate,
        registries,
        decision_transport=transport,
        runtime_issue_listener=on_issue,
        monotonic_clock=clock,
    )
    runtime.start()
    try:
        assert first_entered.wait(2), "first scheduled inference did not start"
        assert coalesced.wait(2), "cadence tick was not dropped while busy"
        assert transport.decision_calls == 1
        release_first.set()
        clock.now = 0.1
        assert not second_entered.wait(0.1), "coalesced tick caused an immediate catch-up invocation"
        assert second_entered.wait(2), "fresh work did not start on a later cadence tick"
    finally:
        release_first.set()
        runtime.stop()

    assert transport.decision_calls == 2


def test_queued_bounded_tick_keeps_scheduled_deadline_while_capacity_is_occupied(
    tmp_path,
):
    import asyncio

    from agno.agent import Agent
    from agno.run.agent import RunOutput

    from mininet_ai.agents import register_builtin_providers
    from mininet_ai.agents.agno import AgnoAgentFactory
    from mininet_ai.experiment import ExperimentRuntime
    from mininet_ai.plugins import ProviderRegistries
    from mininet_ai.sdk import AgentResponse
    from mininet_ai.substrates import FakeSubstrateRuntime

    occupied, release, coalesced, bounded_failed = Event(), Event(), Event(), Event()

    class ControlledClock:
        now = 30.0

        def __call__(self):
            return self.now

    clock = ControlledClock()

    class CountingTransport:
        inference_calls = 0

        def get_json(self, url, **kwargs):
            return {"status": "ok"} if url.endswith("/health") else {}

        def post_json(self, url, payload, **kwargs):
            if kwargs["timeout_seconds"] <= 0.8:
                self.inference_calls += 1
            return {"answers": {"next_action": {"type": "choice", "choice": "no_action"}}}

    class WaitingAgent(Agent):
        async def arun(self, input=None, **options):
            return await asyncio.to_thread(self.run, input, **options)

        def run(self, input=None, **options):
            occupied.set()
            assert release.wait(6)
            return RunOutput(
                run_id=options["run_id"],
                session_id=options["session_id"],
                user_id=options["user_id"],
                content=AgentResponse(message="capacity released"),
            )

    class WaitingFactory(AgnoAgentFactory):
        def _declarative(self, definition):
            return WaitingAgent()

    source_path = _source_for_compile(tmp_path)
    source = yaml.safe_load(source_path.read_text())
    source["resourceLimits"]["max-concurrent-invocations"] = 1
    source["agents"][0]["triggers"][0]["every"] = "1s"
    source["agents"][0]["triggers"][0]["initialDelay"] = "100ms"
    source["blueprints"].append(
        {
            "apiVersion": "mininet-ai/v1alpha5",
            "kind": "AgentBlueprint",
            "metadata": {"name": "capacity-holder"},
            "implementation": {"type": "declarative"},
            "model": {
                "provider": "mock",
                "name": "unused",
                "parameters": {"response": {"message": "unused"}},
            },
        }
    )
    holder = deepcopy(source["agents"][0])
    holder["name"] = "capacity-holder"
    holder["blueprint"] = "capacity-holder"
    holder["placement"]["targets"]["names"] = ["s2"]
    holder["capabilities"] = []
    holder["observe"] = []
    holder.pop("decisionInputs", None)
    holder["triggers"] = [{"type": "manual", "name": "operator"}]
    source["agents"].append(holder)
    mixed_path = tmp_path / "capacity.yaml"
    mixed_path.write_text(yaml.safe_dump(source))
    plan = compile_experiment(mixed_path)
    assert plan.resource_limits.max_concurrent_invocations == 1
    holder_id = next(agent.id for agent in plan.agents if agent.decision_scenario is None)
    bounded_id = next(agent.id for agent in plan.agents if agent.decision_scenario is not None)
    substrate = FakeSubstrateRuntime()
    registries = ProviderRegistries()
    register_builtin_providers(registries, substrate)
    transport = CountingTransport()
    results = []

    def on_issue(issue):
        if issue.code == "runtime.bounded-decision.tick-coalesced":
            coalesced.set()

    def on_invocation(record):
        results.append(record.result)
        if record.result.agent_id == bounded_id:
            bounded_failed.set()

    runtime = ExperimentRuntime(
        plan,
        substrate,
        registries,
        agent_factory=WaitingFactory(),
        decision_transport=transport,
        monotonic_clock=clock,
        runtime_issue_listener=on_issue,
        invocation_listener=on_invocation,
    )
    runtime.start()
    try:
        runtime.submit_intent(holder_id, "occupy the only invocation slot")
        assert occupied.wait(2), "capacity-holder did not acquire invocation capacity"
        assert coalesced.wait(3), "bounded tick was not observed queued behind capacity"
        assert transport.inference_calls == 0, "queued bounded tick started inference while capacity was held"
        clock.now = 30.8
        release.set()
        assert bounded_failed.wait(3), "expired queued bounded work did not complete"
    finally:
        release.set()
        runtime.stop()

    bounded_result = next(result for result in results if result.agent_id == bounded_id)
    assert bounded_result.status.value == "failed"
    evidence = bounded_result.response.metadata["executionRecord"]["runtime"][
        "boundedDecision"
    ]
    assert evidence["timings"]["startedTick"] == 30.0
    assert evidence["timings"]["deadlineTick"] == 30.8
    assert transport.inference_calls == 0
    assert evidence["timeoutStatus"] == "expired"


def test_bounded_admission_wait_consumes_original_deadline_before_action_start(
    tmp_path,
):
    import asyncio

    from agno.agent import Agent
    from agno.run.agent import RunOutput

    from mininet_ai.agents import register_builtin_providers
    from mininet_ai.agents.agno import AgnoAgentFactory
    from mininet_ai.experiment import ExperimentRuntime
    from mininet_ai.plugins import ProviderRegistries
    from mininet_ai.sdk import ActionProposal, AgentResponse
    from mininet_ai.substrates import FakeSubstrateRuntime

    holder_action_started, release_holder, bounded_response = Event(), Event(), Event()

    class ControlledClock:
        now = 40.0

        def __call__(self):
            return self.now

    clock = ControlledClock()

    class RecoveryTransport:
        def get_json(self, url, **kwargs):
            return {"status": "ok"} if url.endswith("/health") else {}

        def post_json(self, url, payload, **kwargs):
            if kwargs["timeout_seconds"] > 0.8:
                return {"answers": {"next_action": {"type": "choice", "choice": "no_action"}}}
            bounded_response.set()
            return {"answers": {"next_action": {"type": "choice", "choice": "install_recovery"}}}

    class BlockingSubstrate(FakeSubstrateRuntime):
        def __init__(self):
            super().__init__()
            self.mutations = []

        def execute(self, run_id, request):
            self.mutations.append(request)
            if request.id == "hold-flow":
                holder_action_started.set()
                assert release_holder.wait(4)
            return super().execute(run_id, request)

    class ActionAgent(Agent):
        async def arun(self, input=None, **options):
            return await asyncio.to_thread(self.run, input, **options)

        def run(self, input=None, **options):
            return RunOutput(
                run_id=options["run_id"],
                session_id=options["session_id"],
                user_id=options["user_id"],
                content=AgentResponse(
                    proposals=(
                        ActionProposal(
                            id="hold-flow",
                            capability="openflow.flow.install",
                            target="s1",
                            arguments={"match": {"step": 1}, "actions": []},
                        ),
                    )
                ),
            )

    class ActionFactory(AgnoAgentFactory):
        def _declarative(self, definition):
            return ActionAgent()

    source_path = _source_for_compile(tmp_path)
    source = yaml.safe_load(source_path.read_text())
    source["agents"][0]["triggers"][0]["initialDelay"] = "100ms"
    source["blueprints"].append(
        {
            "apiVersion": "mininet-ai/v1alpha5",
            "kind": "AgentBlueprint",
            "metadata": {"name": "admission-holder"},
            "implementation": {"type": "declarative"},
            "model": {
                "provider": "mock",
                "name": "unused",
                "parameters": {"response": {"message": "unused"}},
            },
        }
    )
    holder = deepcopy(source["agents"][0])
    holder["name"] = "admission-holder"
    holder["blueprint"] = "admission-holder"
    holder["placement"]["targets"]["names"] = ["s1"]
    holder["capabilities"] = ["openflow.flow.install"]
    holder["triggers"] = [{"type": "manual", "name": "operator"}]
    holder.pop("decisionInputs", None)
    source["agents"].append(holder)
    mixed_path = tmp_path / "admission.yaml"
    mixed_path.write_text(yaml.safe_dump(source))
    plan = compile_experiment(mixed_path)
    holder_id = next(agent.id for agent in plan.agents if agent.decision_scenario is None)
    bounded_id = next(agent.id for agent in plan.agents if agent.decision_scenario is not None)
    substrate = BlockingSubstrate()
    registries = ProviderRegistries()
    register_builtin_providers(registries, substrate)
    results = []
    bounded_completed = Event()

    def on_invocation(record):
        results.append(record.result)
        if record.result.agent_id == bounded_id:
            bounded_completed.set()

    runtime = ExperimentRuntime(
        plan,
        substrate,
        registries,
        agent_factory=ActionFactory(),
        decision_transport=RecoveryTransport(),
        monotonic_clock=clock,
        invocation_listener=on_invocation,
    )
    runtime.start()
    try:
        runtime.submit_intent(holder_id, "hold the conflicting flow admission")
        assert holder_action_started.wait(2), f"conflicting action did not start: {results}"
        assert bounded_response.wait(2), "bounded decision did not return its choice"
        clock.now = 40.8
        release_holder.set()
        assert bounded_completed.wait(2), "bounded admission wait did not complete"
    finally:
        release_holder.set()
        runtime.stop()

    bounded_result = next(result for result in results if result.agent_id == bounded_id)
    assert bounded_result.status.value == "failed"
    assert len(substrate.mutations) == 1
    evidence = bounded_result.response.metadata["executionRecord"]["runtime"][
        "boundedDecision"
    ]
    assert evidence["timings"]["deadlineTick"] == 40.8
    assert evidence["timeoutStatus"] == "expired"
    assert evidence["admission"]["status"] == "expired"
    assert evidence["actionOutcomes"][0]["stepStatus"] == "not-started"
    assert "action-start" not in {
        transition["phase"] for transition in evidence["timings"]["phaseTransitions"]
    }


def test_unrelated_agno_agent_progresses_while_bounded_inference_is_in_flight(
    tmp_path,
):
    import asyncio

    from agno.agent import Agent
    from agno.run.agent import RunOutput

    from mininet_ai.agents import register_builtin_providers
    from mininet_ai.agents.agno import AgnoAgentFactory
    from mininet_ai.experiment import ExperimentRuntime
    from mininet_ai.plugins import ProviderRegistries
    from mininet_ai.sdk import AgentResponse
    from mininet_ai.substrates import FakeSubstrateRuntime

    inference_started, release_inference, unrelated_completed = Event(), Event(), Event()

    class BlockingTransport:
        def get_json(self, url, **kwargs):
            return {"status": "ok"} if url.endswith("/health") else {}

        def post_json(self, url, payload, **kwargs):
            if kwargs["timeout_seconds"] <= 0.8:
                inference_started.set()
                assert release_inference.wait(3)
            return {"answers": {"next_action": {"type": "choice", "choice": "no_action"}}}

    class StaticAgent(Agent):
        async def arun(self, input=None, **options):
            return await asyncio.to_thread(self.run, input, **options)

        def run(self, input=None, **options):
            return RunOutput(
                run_id=options["run_id"],
                session_id=options["session_id"],
                user_id=options["user_id"],
                content=AgentResponse(message="unrelated completed"),
            )

    class StaticFactory(AgnoAgentFactory):
        def _declarative(self, definition):
            return StaticAgent()

    source_path = _source_for_compile(tmp_path)
    source = yaml.safe_load(source_path.read_text())
    source["resourceLimits"]["max-concurrent-invocations"] = 2
    source["blueprints"].append(
        {
            "apiVersion": "mininet-ai/v1alpha5",
            "kind": "AgentBlueprint",
            "metadata": {"name": "unrelated"},
            "implementation": {"type": "declarative"},
            "model": {
                "provider": "mock",
                "name": "unused",
                "parameters": {"response": {"message": "ignored by factory"}},
            },
        }
    )
    unrelated = deepcopy(source["agents"][0])
    unrelated["name"] = "unrelated"
    unrelated["blueprint"] = "unrelated"
    unrelated["placement"]["targets"]["names"] = ["s2"]
    unrelated["capabilities"] = []
    unrelated["observe"] = []
    unrelated.pop("decisionInputs", None)
    unrelated["triggers"] = [{"type": "manual", "name": "operator"}]
    source["agents"].append(unrelated)
    mixed_path = tmp_path / "mixed.yaml"
    mixed_path.write_text(yaml.safe_dump(source))
    plan = compile_experiment(mixed_path)
    unrelated_id = next(agent.id for agent in plan.agents if agent.decision_scenario is None)
    substrate = FakeSubstrateRuntime()
    registries = ProviderRegistries()
    register_builtin_providers(registries, substrate)

    def record_invocation(record):
        if record.result.agent_id == unrelated_id:
            unrelated_completed.set()

    runtime = ExperimentRuntime(
        plan,
        substrate,
        registries,
        agent_factory=StaticFactory(),
        decision_transport=BlockingTransport(),
        invocation_listener=record_invocation,
    )
    runtime.start()
    try:
        assert inference_started.wait(2), "bounded inference did not start"
        runtime.submit_intent(unrelated_id, "make unrelated progress")
        assert unrelated_completed.wait(1), "unrelated agent was blocked by bounded inference"
    finally:
        release_inference.set()
        runtime.stop()


def test_compiler_rejects_fixed_recovery_arguments_that_fail_capability_schema(
    tmp_path,
):
    from mininet_ai.errors import CompilationError

    source = _source_for_compile(tmp_path)
    document = yaml.safe_load(source.read_text())
    document["decisionScenarios"][0]["choices"][1]["proposals"][0]["arguments"] = {}
    source.write_text(yaml.safe_dump(document))
    with pytest.raises(CompilationError, match="invalid arguments"):
        compile_experiment(source)


def test_runtime_conflict_rejection_is_retained_as_distinct_evidence(tmp_path):
    from mininet_ai.agents import register_builtin_providers
    from mininet_ai.experiment import ExperimentRuntime
    from mininet_ai.plugins import ProviderRegistries
    from mininet_ai.substrates import FakeSubstrateRuntime

    class RecoveryTransport:
        def __init__(self):
            self.inference_requests = 0

        def get_json(self, url, **kwargs):
            return {"status": "ok"} if url.endswith("/health") else {}

        def post_json(self, url, payload, **kwargs):
            self.inference_requests += 1
            return {
                "answers": {
                    "next_action": {
                        "type": "choice",
                        "choice": "install_recovery",
                        "confidence": 0.01,
                    }
                }
            }

    class RecordingSubstrate(FakeSubstrateRuntime):
        def __init__(self):
            super().__init__()
            self.mutations = []

        def execute(self, run_id, request):
            self.mutations.append(request)
            return super().execute(run_id, request)

    source = _source_for_compile(tmp_path)
    document = yaml.safe_load(source.read_text())
    document["policies"]["conflicting-actions"] = "reject"
    source.write_text(yaml.safe_dump(document))
    plan = compile_experiment(source)
    transport = RecoveryTransport()
    substrate = RecordingSubstrate()
    registries = ProviderRegistries()
    register_builtin_providers(registries, substrate)
    completed = Event()
    results = []
    runtime = ExperimentRuntime(
        plan,
        substrate,
        registries,
        decision_transport=transport,
        invocation_listener=lambda record: (
            results.append(record.result),
            completed.set(),
        ),
    )
    runtime.start()
    try:
        assert completed.wait(3), "conflicting recovery decision did not complete"
    finally:
        runtime.stop()

    result = results[0]
    serialized = result.model_dump(mode="json", by_alias=True)
    evidence = serialized["response"]["metadata"]["executionRecord"]["runtime"][
        "boundedDecision"
    ]
    assert transport.inference_requests == 2  # startup interface check plus decision
    assert [request.parameters["match"]["step"] for request in substrate.mutations] == [
        1
    ]
    assert result.status.value == "rejected"
    assert result.issue.code == "coordination.conflict.rejected"
    assert [step["stepStatus"] for step in evidence["actionOutcomes"]] == [
        "completed",
        "rejected",
    ]
    assert evidence["actionOutcomes"][1]["issue"]["code"] == (
        "coordination.conflict.rejected"
    )
    assert evidence["authorization"]["status"] == "authorized"
    assert evidence["admission"]["status"] == "rejected"


def test_bounded_service_startup_failure_precedes_substrate_deploy(tmp_path):
    from mininet_ai.agents import register_builtin_providers
    from mininet_ai.errors import AgentRuntimeError
    from mininet_ai.experiment import ExperimentRuntime
    from mininet_ai.plugins import ProviderRegistries
    from mininet_ai.substrates import FakeSubstrateRuntime

    class UnavailableTransport:
        def get_json(self, url, **kwargs):
            raise ConnectionError("server is down")

        def post_json(self, url, payload, **kwargs):
            raise AssertionError("native probe must follow availability checks")

    class RecordingSubstrate(FakeSubstrateRuntime):
        deployed = False

        def deploy(self, plan):
            self.deployed = True
            return super().deploy(plan)

    source = _source_for_compile(tmp_path)
    plan = compile_experiment(source)
    substrate = RecordingSubstrate()
    registries = ProviderRegistries()
    register_builtin_providers(registries, substrate)
    runtime = ExperimentRuntime(
        plan,
        substrate,
        registries,
        decision_transport=UnavailableTransport(),
    )
    with pytest.raises(AgentRuntimeError) as error:
        runtime.start()
    assert error.value.code == "agent.bounded-decision.startup-unavailable"
    assert not substrate.deployed
