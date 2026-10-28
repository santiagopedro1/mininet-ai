import json
import shutil
from threading import Event

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
    results = []

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
    assert evidence["admission"]["status"] == "completed"


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
