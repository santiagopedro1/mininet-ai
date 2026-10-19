from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from itertools import count

import pytest
from agno.models.message import Message
from ollama import AsyncClient, ChatResponse, Client

from mininet_ai.agents.agno.ollama_model import create_ollama_model
from mininet_ai.runtime import ContinuousAgentRuntime, TelemetryPipeline
from mininet_ai.runtime.instrumentation import (
    ExecutionContext,
    Operation,
    Phase,
    PhaseRecorder,
    RecordingBinding,
    Stamp,
    current_binding,
    interval,
    recording_scope,
)
from mininet_ai.sdk import AgentInvocationResult, InvocationStatus
from mininet_ai.specification.models import ModelConfiguration
from tests.agents import test_runtime as agent_tests
from tests.runtime.test_telemetry import (
    NOW,
    SequenceSubstrate,
    configured_plan,
    wait_until,
)

CONTEXT = ExecutionContext("run-1", "trial-1", "fault-1")


@pytest.mark.parametrize(
    ("start", "end", "reason", "seconds"),
    [
        (Stamp("local", "0"), Stamp("local", "0"), None, 0),
        (Stamp("local", "0"), Stamp("local", "1000000000"), None, 1),
        (None, Stamp("local", "1"), "incomplete_phase", None),
        (Stamp("local", None), Stamp("local", "1"), "missing_evidence", None),
        (Stamp("local", "NaN"), Stamp("local", "1"), "malformed_tick", None),
        (Stamp("local", "-1"), Stamp("local", "1"), "malformed_tick", None),
        (Stamp("local", "1.0"), Stamp("local", "1"), "malformed_tick", None),
        (Stamp("local", "00"), Stamp("local", "1"), "malformed_tick", None),
        (Stamp("local", "1"), Stamp("remote", "2"), "clock_domain_mismatch", None),
        (Stamp("local", "2"), Stamp("local", "1"), "timestamp_order_invalid", None),
    ],
)
def test_raw_intervals(start, end, reason, seconds) -> None:
    result = interval(start, end)
    assert result.value_seconds == seconds
    assert result.reason == reason
    assert result.availability == ("available" if reason is None else "unavailable")


@pytest.mark.parametrize("reason", ["recognition_precedes_confirmed_onset", "action_starts_after_confirmed_recovery"])
def test_cross_phase_anomalies_are_not_clamped(reason) -> None:
    start, end = Stamp("experiment", "200"), Stamp("experiment", "100")
    result = interval(start, end, reversed_reason=reason)
    assert result.reason == reason
    assert result.value_seconds is None
    assert (start.tick_ns, end.tick_ns) == ("200", "100")


def test_simultaneous_quality_issues_are_preserved() -> None:
    result = interval(Stamp("local", "2"), Stamp("remote", "1"), trustworthy=False)
    assert result.quality_flags == (
        "missing_evidence", "clock_domain_mismatch",
    )
    same_domain = interval(Stamp("local", "2"), Stamp("local", "1"), trustworthy=False)
    assert same_domain.quality_flags == ("missing_evidence", "timestamp_order_invalid")


def test_thread_safe_records_keep_immutable_context_and_unique_operations() -> None:
    recorder = PhaseRecorder("experiment")

    def work(number):
        context = replace(CONTEXT, invocation_id=f"invocation-{number}", proposal_id="same-id")
        with recording_scope(RecordingBinding(recorder, context)), Operation(Phase.EXECUTION):
            binding = current_binding()
            assert binding is not None and binding.context == context

    with ThreadPoolExecutor(max_workers=8) as workers:
        list(workers.map(work, range(50)))
    records = recorder.records
    assert len(records) == 100
    assert [r.collector_seq for r in records] == list(range(100))
    assert len({r.record_id for r in records}) == 100
    assert len({r.operation_id for r in records}) == 50
    assert len({(r.context.invocation_id, r.context.proposal_id) for r in records}) == 50
    with pytest.raises(FrozenInstanceError):
        CONTEXT.trial_id = "changed"  # type: ignore[misc]
    assert current_binding() is None


def test_delayed_sink_does_not_timestamp_at_receipt() -> None:
    tick = 5

    def delayed_sink(record):
        nonlocal tick
        tick = 900
        assert record.stamp.tick_ns == "5"

    recorder = PhaseRecorder("experiment", clock=lambda: tick, sink=delayed_sink)
    recorder.record(CONTEXT, "op", Phase.OBSERVATION, "operation_started")
    assert recorder.records[0].stamp.tick_ns == "5"


@pytest.mark.parametrize("failure", ["sink", "clock", "reported_loss"])
def test_recorder_failure_is_sticky_and_measurements_unavailable(failure) -> None:
    def broken_sink(record):
        raise OSError("disk full")

    recorder = PhaseRecorder(
        "experiment", clock=(lambda: -1) if failure == "clock" else (lambda: 0),
        sink=broken_sink if failure == "sink" else None,
    )
    with recording_scope(RecordingBinding(recorder, CONTEXT)), Operation(Phase.OBSERVATION):
        pass
    if failure == "reported_loss":
        recorder.report_loss(CONTEXT, "lost required observation")
    assert recorder.losses
    result = recorder.measurement(CONTEXT, Stamp("experiment", "0"), Stamp("experiment", "0"))
    assert result.reason == "missing_evidence"
    assert result.value_seconds is None
    assert not recorder.requests.contained


def test_failed_cancelled_and_incomplete_phases_are_distinct() -> None:
    ticks = count()
    recorder = PhaseRecorder("experiment", clock=lambda: next(ticks))
    with recording_scope(RecordingBinding(recorder, CONTEXT)):
        with pytest.raises(RuntimeError), Operation(Phase.REASONING):
            raise RuntimeError("provider failed")
        with pytest.raises(asyncio.CancelledError), Operation(Phase.EXECUTION):
            raise asyncio.CancelledError()
        Operation(Phase.CONTEXT)  # Cleanup may finalize this as incomplete, not completed.
    assert [r.transition for r in recorder.records] == [
        "operation_started", "operation_failed", "operation_started", "operation_cancelled", "operation_started",
    ]
    assert interval(recorder.records[0].stamp, recorder.records[1].stamp).value_seconds == 1e-9


def test_preparation_context_survives_different_action_worker_scope() -> None:
    response = {"proposals": [
        {"id": f"rule-{n}", "capability": "openflow.flow.install", "target": "s1",
         "arguments": {"match": "ip", "actions": "normal"}} for n in range(2)
    ]}
    runtime, run, _ = agent_tests.OneShotAgentRuntimeTests().runtime(response)
    recorder = PhaseRecorder("experiment")
    with recording_scope(RecordingBinding(recorder, CONTEXT)):
        prepared = runtime.prepare(run.id, "switch-router@s1", "repair")
    assert not isinstance(prepared, AgentInvocationResult)
    with recording_scope(RecordingBinding(recorder, replace(CONTEXT, trial_id="later-trial"))):
        results = tuple(runtime.execute(prepared, p) for p in prepared.response.proposals)
    result = runtime.complete(prepared, results)
    assert result.status == InvocationStatus.SUCCEEDED
    assert all(r.context.trial_id == "trial-1" for r in recorder.records)
    actions = [r for r in recorder.records if r.phase == Phase.EXECUTION and r.transition == "operation_started"]
    assert {r.context.proposal_id for r in actions} == {"rule-0", "rule-1"}
    assert {r.context.invocation_id for r in actions} == {"invoke-1"}
    assert sum(r.phase == Phase.DECISION_TO_ACTION and r.transition == "operation_completed" for r in recorder.records) == 1
    assert result.timings.reasoning_seconds is not None


def test_rejected_proposals_do_not_become_action_attempts() -> None:
    runtime, run, _ = agent_tests.OneShotAgentRuntimeTests().runtime({"proposals": [
        {"id": "rejected", "capability": "not.assigned", "target": "s1", "arguments": {}}
    ]})
    recorder = PhaseRecorder("experiment")
    with recording_scope(RecordingBinding(recorder, CONTEXT)):
        result = runtime.invoke(run.id, "switch-router@s1", "inspect")
    assert result.status == InvocationStatus.REJECTED
    assert not any(r.phase in {Phase.ACTION_ATTEMPT, Phase.EXECUTION} for r in recorder.records)
    assert any(r.phase == Phase.ADMISSION and r.transition == "operation_failed" for r in recorder.records)


def test_detector_tick_and_context_precede_delayed_bus_delivery() -> None:
    plan = configured_plan(detectors=[{
        "type": "threshold", "name": "high", "path": "depth", "operator": "gt",
        "value": 10, "event": "queue.high", "cooldown": "0s",
    }])
    substrate = SequenceSubstrate([{"s1": {"depth": 90}}], [NOW])
    tick = 1
    recorder = PhaseRecorder("experiment", clock=lambda: tick)
    captured = []

    class Invoker:
        def invoke(self, run_id, agent_id, intent):
            captured.append(current_binding())
            return AgentInvocationResult(
                invocationId="worker-invocation", runId=run_id, agentId=agent_id,
                status=InvocationStatus.SUCCEEDED, startedAt=NOW, completedAt=NOW,
            )

    with recording_scope(RecordingBinding(recorder, CONTEXT)):
        continuous = ContinuousAgentRuntime(plan, "run-1", Invoker())

        class DelayedPublisher:
            def publish(self, event):
                nonlocal tick
                tick = 1000
                continuous.publish(event)

        pipeline = TelemetryPipeline(plan, "run-1", substrate, DelayedPublisher(), clock=lambda: NOW)
    continuous.start()
    try:
        with recording_scope(RecordingBinding(recorder, replace(CONTEXT, trial_id="new-trial"))):
            events = pipeline.sample_once("switch-router@s1", "tc.queue-occupancy")
        wait_until(lambda: bool(captured))
    finally:
        continuous.stop()
    recognition = next(r for r in recorder.records if r.outcome == "recognized")
    assert recognition.stamp.tick_ns == "1"
    assert recognition.context.trial_id == "trial-1"
    assert recognition.context.observation_source == "global:telemetry"
    assert captured[0].context.trial_id == "trial-1"
    assert captured[0].context.event_id == events[1].event_id
    assert captured[0].context.observation_id == recognition.context.observation_id
    assert events[1].causation_id == events[0].event_id
    assert events[1].execution_context == recognition.context


def ollama_model():
    return create_ollama_model(ModelConfiguration(provider="ollama", name="test"))


def terminal_response():
    return ChatResponse.model_validate({"done": True, "message": {"role": "assistant", "content": "ok"}})


def test_real_ollama_adapter_accepts_protocol_terminal_response(monkeypatch) -> None:
    monkeypatch.setattr(Client, "chat", lambda *a, **kw: terminal_response())
    recorder = PhaseRecorder("experiment")
    with recording_scope(RecordingBinding(recorder, CONTEXT)):
        response = ollama_model().invoke([], Message(role="assistant"))
    assert response.content == "ok"
    assert recorder.requests.contained
    request, = recorder.requests.requests
    assert request.terminal_evidence == "normal_response"
    assert request.provider_request_id is None
    assert request.context == CONTEXT


@pytest.mark.parametrize("error", [TimeoutError(), ConnectionError(), RuntimeError(), asyncio.CancelledError()])
def test_actual_async_adapter_local_interruption_is_unknown(monkeypatch, error) -> None:
    async def interrupted(*args, **kwargs):
        raise error

    monkeypatch.setattr(AsyncClient, "chat", interrupted)
    recorder = PhaseRecorder("experiment")
    with recording_scope(RecordingBinding(recorder, CONTEXT)), pytest.raises(type(error)):
        asyncio.run(ollama_model().ainvoke([], Message(role="assistant")))
    request, = recorder.requests.outstanding
    assert request.local_error == type(error).__name__
    assert request.terminal_evidence is None
    assert request.interrupted_at is not None
    assert not recorder.requests.contained


def test_terminal_evidence_survives_local_parse_failure(monkeypatch) -> None:
    monkeypatch.setattr(Client, "chat", lambda *a, **kw: {"done": True})
    recorder = PhaseRecorder("experiment")
    with recording_scope(RecordingBinding(recorder, CONTEXT)), pytest.raises(AttributeError):
        ollama_model().invoke([], Message(role="assistant"))
    assert recorder.requests.contained  # Parsing failure does not undo remote completion.


def test_local_cancel_and_factory_close_cannot_clear_requests() -> None:
    from mininet_ai.agents.agno import AgnoAgentFactory

    recorder = PhaseRecorder("experiment")
    request = recorder.requests.start(CONTEXT, "ollama")
    recorder.requests.cancel(request)
    recorder.requests.interrupted(request, "closed_connection")
    AgnoAgentFactory().close()
    assert recorder.requests.outstanding
    assert not recorder.requests.contained
    recorder.requests.terminal(request, evidence="remote_termination", source="test-authoritative-adapter")
    assert recorder.requests.contained
    assert len(recorder.requests.history) == 4
    assert recorder.requests.history[0].terminal_evidence is None
    assert recorder.requests.history[-1].terminal_evidence == "remote_termination"


def test_disabled_adapter_is_ordinary_ollama_client() -> None:
    model = ollama_model()
    assert type(model.get_client()) is Client
    assert type(model.get_async_client()) is AsyncClient


def test_async_terminal_response_and_multiple_requests_are_accounted(monkeypatch) -> None:
    async def respond(*args, **kwargs):
        return terminal_response()

    monkeypatch.setattr(AsyncClient, "chat", respond)
    recorder = PhaseRecorder("experiment")

    async def run():
        model = ollama_model()
        await model.ainvoke([], Message(role="assistant"))
        await model.ainvoke([], Message(role="assistant"))

    with recording_scope(RecordingBinding(recorder, CONTEXT)):
        asyncio.run(run())
    assert recorder.requests.contained
    assert len(recorder.requests.requests) == 2
    assert len({r.request_id for r in recorder.requests.requests}) == 2


@pytest.mark.parametrize("terminal", [True, False])
def test_stream_only_completes_with_terminal_protocol_marker(monkeypatch, terminal) -> None:
    def stream(*args, **kwargs):
        yield ChatResponse.model_validate({"done": False, "message": {"role": "assistant", "content": "chunk"}})
        if terminal:
            yield terminal_response()

    monkeypatch.setattr(Client, "chat", stream)
    recorder = PhaseRecorder("experiment")
    with recording_scope(RecordingBinding(recorder, CONTEXT)):
        list(ollama_model().invoke_stream([], Message(role="assistant")))
    assert recorder.requests.contained is terminal
    assert bool(recorder.requests.outstanding) is not terminal


def test_failed_provider_retains_raw_reasoning_interval() -> None:
    runtime, run, _ = agent_tests.OneShotAgentRuntimeTests().runtime({"unexpected": "response"})
    ticks = count()
    recorder = PhaseRecorder("experiment", clock=lambda: next(ticks))
    with recording_scope(RecordingBinding(recorder, CONTEXT)):
        result = runtime.invoke(run.id, "switch-router@s1", "inspect")
    assert result.status == InvocationStatus.FAILED
    reasoning = [r for r in recorder.records if r.phase == Phase.REASONING]
    assert [r.transition for r in reasoning] == ["operation_started", "operation_failed"]
    assert recorder.measurement(CONTEXT, reasoning[0].stamp, reasoning[1].stamp).availability == "available"


def test_sdk_metrics_use_their_original_clock_not_experiment_ticks() -> None:
    ticks = iter(float(n) for n in range(8))
    runtime, run, _ = agent_tests.OneShotAgentRuntimeTests().runtime(
        {"message": "ok"}, monotonic_clock=lambda: next(ticks),
    )
    recorder = PhaseRecorder("experiment", clock=lambda: 0)
    with recording_scope(RecordingBinding(recorder, CONTEXT)):
        result = runtime.invoke(run.id, "switch-router@s1", "inspect")
    assert result.timings.context_build_seconds == 1
    assert result.timings.reasoning_seconds == 1
    assert result.timings.total_seconds == 7
    reasoning = [r for r in recorder.records if r.phase == Phase.REASONING]
    assert recorder.measurement(CONTEXT, reasoning[0].stamp, reasoning[1].stamp).value_seconds == 0


@pytest.mark.parametrize("satisfied", [True, False])
def test_verification_timing_remains_separate_from_execution(satisfied) -> None:
    import copy

    response = {"proposals": [{
        "id": "rule", "capability": "openflow.flow.install", "target": "s1",
        "arguments": {"match": "ip", "actions": "normal"},
    }]}
    plan = agent_tests.configured_plan(response)
    snapshot = copy.deepcopy(plan.snapshot)
    capability = next(c for c in snapshot["capabilityDefinitions"] if c["metadata"]["name"] == "openflow.flow.install")
    capability["postconditions"] = [{
        "observation": "openflow.flows", "path": "observation", "operator": "eq",
        "expected": "openflow.flows" if satisfied else "not-present", "timeout": "20ms", "interval": "10ms",
    }]
    runtime, run, _ = agent_tests.OneShotAgentRuntimeTests().runtime(
        response, plan=plan.model_copy(update={"snapshot": snapshot}),
    )
    ticks = count()
    recorder = PhaseRecorder("experiment", clock=lambda: next(ticks))
    with recording_scope(RecordingBinding(recorder, CONTEXT)):
        result = runtime.invoke(run.id, "switch-router@s1", "install")
    assert (result.status == InvocationStatus.SUCCEEDED) is satisfied
    execution = [r for r in recorder.records if r.phase == Phase.EXECUTION]
    verification = [r for r in recorder.records if r.phase == Phase.VERIFICATION]
    assert len(execution) == len(verification) == 2
    execution_end = execution[1].stamp.tick_ns
    verification_start = verification[0].stamp.tick_ns
    assert execution_end is not None and verification_start is not None
    assert int(execution_end) < int(verification_start)
    assert {r.context.proposal_id for r in verification} == {"rule"}
    assert verification[-1].transition == ("operation_completed" if satisfied else "operation_failed")
    assert result.timings.action_effect_seconds is not None


@pytest.mark.parametrize("interrupted", [False, True])
def test_request_evidence_through_agno_provider_and_factory_close(monkeypatch, interrupted) -> None:
    from mininet_ai.agents.agno import AgnoAgentFactory

    def chat(*args, **kwargs):
        if interrupted:
            raise TimeoutError("local timeout")
        return ChatResponse.model_validate({
            "done": True, "message": {"role": "assistant", "content": '{"message":"ok"}'},
        })

    monkeypatch.setattr(Client, "chat", chat)
    async def async_chat(*args, **kwargs):
        return chat(*args, **kwargs)

    monkeypatch.setattr(AsyncClient, "chat", async_chat)
    factory = AgnoAgentFactory(model_resolver=lambda configuration: ollama_model())
    runtime, run, _ = agent_tests.OneShotAgentRuntimeTests().runtime({"message": "unused"})
    monkeypatch.setattr(runtime, "_agent_factory", factory)
    recorder = PhaseRecorder("experiment")
    with recording_scope(RecordingBinding(recorder, CONTEXT)):
        result = runtime.invoke(run.id, "switch-router@s1", "inspect")
    factory.close()
    assert (result.status == InvocationStatus.FAILED) is interrupted
    assert recorder.requests.contained is not interrupted
    assert recorder.requests.requests
    assert all(r.context.invocation_id == result.invocation_id for r in recorder.requests.requests)


def test_failed_capability_retains_execution_not_just_attempt(monkeypatch) -> None:
    from mininet_ai.substrates import FakeSubstrateRuntime

    def fail(*args, **kwargs):
        raise ConnectionError("execution failed")

    monkeypatch.setattr(FakeSubstrateRuntime, "execute", fail)
    response = {"proposals": [{
        "id": "rule", "capability": "openflow.flow.install", "target": "s1",
        "arguments": {"match": "ip", "actions": "normal"},
    }]}
    runtime, run, _ = agent_tests.OneShotAgentRuntimeTests().runtime(response)
    recorder = PhaseRecorder("experiment")
    with recording_scope(RecordingBinding(recorder, CONTEXT)):
        result = runtime.invoke(run.id, "switch-router@s1", "repair")
    assert result.status == InvocationStatus.FAILED
    execution = [r for r in recorder.records if r.phase == Phase.EXECUTION]
    assert execution[-1].transition == "operation_failed"
    assert recorder.measurement(CONTEXT, execution[0].stamp, execution[-1].stamp).availability == "available"
