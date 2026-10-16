"""Throwaway regression checks at the offline prototype's CLI/report boundary."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest


@pytest.fixture(scope="module")
def observations(tmp_path_factory):
    root = tmp_path_factory.mktemp("PROTOTYPE-wipe-me-integrated")
    report = root / "observations.json"
    result = subprocess.run(
        [sys.executable, "-P", "mininet_ai/agents/agno/integrated_learning_prototype.py",
         "--scratch-parent", str(root), "--report", str(report)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(report.read_text())


def test_one_agent_survives_route_changes(observations):
    replies = [r for r in observations["groups"]["mixed"]["replies"] if "id" in r]
    assert len(replies) >= 2
    assert all(r["success"] for r in replies)
    assert len({r["agent_object"] for r in replies}) == 1


def test_mixed_routes_retain_history_and_action_outcome(observations):
    replies = [r for r in observations["groups"]["mixed"]["replies"] if "id" in r]
    final = replies[-1]
    assert "unique A history" in json.dumps(final.get("model_messages", []))
    assert final.get("session_state") == {"action_result": "installed"}


@pytest.mark.parametrize("group_name", ["mixed", "agentic", "automatic-agent-scope", "agentic-agent-scope", "memory-free"])
def test_subsequent_reasoning_receives_recorded_action_outcome(observations, group_name):
    replies = {r["id"]: r for r in observations["groups"][group_name]["replies"] if "id" in r}
    final = replies["back-A" if group_name == "memory-free" else "back"]
    model_input = json.dumps(final["model_messages"])
    assert "action_result" in model_input
    assert "installed" in model_input


def test_fixed_profiles_exercise_their_actual_learning_paths(observations):
    automatic = {r["id"]: r for r in observations["groups"]["mixed"]["replies"] if "id" in r}
    agentic = {r["id"]: r for r in observations["groups"]["agentic"]["replies"] if "id" in r}
    assert automatic["automatic"]["owned_futures"] > 0
    assert "fact-automatic" in json.dumps(automatic["automatic"]["memories"])
    assert agentic["agentic"]["owned_futures"] == 0
    assert "fact-agentic" in json.dumps(agentic["agentic"]["memories"])
    assert "update_user_memory" in json.dumps(agentic["agentic"]["model_messages"])


def test_background_learning_keeps_gate_until_drained(observations):
    group = observations["groups"]["mixed"]
    assert group["background_gate"] == {
        "controller": "denied while background active",
        "thread": "denied while background active",
    }
    terminal = next(r for r in group["replies"] if r.get("id") == "automatic")
    assert "fact-automatic" in json.dumps(terminal["memories"])


@pytest.mark.parametrize("group_name", ["mixed", "agentic", "automatic-agent-scope", "agentic-agent-scope"])
def test_mixed_route_effects_and_scope_isolation(observations, group_name):
    group = observations["groups"][group_name]
    replies = {r["id"]: r for r in group["replies"] if "id" in r}
    assert all(r["success"] for r in replies.values())
    for field in ("agent_object", "manager_object", "executor_object", "adapter_object",
                  "foreground_model_object", "memory_model_object", "owner"):
        assert len({r[field] for r in replies.values()}) == 1
    assert "direct mixed fact" in json.dumps(replies["add"]["memories"])
    assert "replaced mixed fact" in replies["read"]["response"]
    assert "replaced mixed fact" in replies["search"]["response"]
    assert "hook-pre-hooks" in json.dumps(replies["hooks"]["memories"])
    assert "hook-post-hooks" in json.dumps(replies["hooks"]["memories"])
    assert "fact-tool" in json.dumps(replies["tool"]["memories"])
    assert not any(m["memory_id"] == "mixed-direct" for m in replies["delete"]["memories"])
    assert [m["memory"] for m in replies["optimize"]["memories"]] == ["optimized offline"]
    assert replies["clear-user"]["memories"] == []
    assert "unique A history" in json.dumps(replies["back"]["model_messages"])
    assert replies["back"]["session_state"] == {"action_result": "installed"}
    user = "A" if group_name.endswith("agent-scope") else "trial:A"
    assert {r["user"] for r in replies.values()} == {user}
    assert {r["session"] for r in replies.values()} == {"trial:A"}
    assert [m["memory"] for m in group["scopes"]["foreign"]] == ["seed"]
    other_scope = "trial:A" if user == "A" else "A"
    assert [m["memory"] for m in group["scopes"][other_scope]] == ["seed"]


def test_memory_free_sessions_overlap_without_sharing_history(observations):
    group = observations["groups"]["memory-free"]
    replies = {r["id"]: r for r in group["replies"] if "id" in r}
    assert all(r["success"] for r in replies.values())
    assert replies["overlap-A"]["agent_object"] != replies["overlap-B"]["agent_object"]
    assert replies["overlap-A"]["owner"] != replies["overlap-B"]["owner"]
    assert "unique A history" in json.dumps(replies["back-A"]["model_messages"])
    assert "unique A history" not in json.dumps(replies["back-B"]["model_messages"])
    assert replies["back-A"]["session_state"] == {"action_result": "installed"}
    assert replies["back-B"]["session_state"] == {}
    assert not any(e["kind"] == "gate-acquired" for e in group["events"])
    assert not any(e["payload"].get("method", "").endswith("memory")
                   or e["payload"].get("method") == "get_user_memories" for e in group["events"])


def test_report_contains_checks_and_reproducible_provenance(observations):
    assert observations["checks"]
    assert all(observations["checks"].values())
    assert observations["provenance"]["packages"]["agno"] == "3.0.11"
    assert observations["provenance"]["sources"]


def test_success_replies_follow_required_writes_and_drain(observations):
    for group in observations["groups"].values():
        assert group["exitcode"] == 0
        for reply in group["replies"]:
            if "id" not in reply:
                continue
            events = [e for e in group["events"] if e["id"] == reply["id"]]
            kinds = [e["kind"] for e in events]
            assert kinds.index("supervisor-admission") < kinds.index("admitted")
            assert kinds.index("drained") < kinds.index("terminal")
            assert events[-1]["kind"] == "terminal"
            assert events[-1]["payload"] == reply
            assert next(e["payload"] for e in events if e["kind"] == "drained")["pending"] == 0
            assert not any(e["kind"] == "db-error" for e in events)
            if reply["mode"] != "session-only":
                assert kinds.index("gate-acquired") < kinds.index("admitted")
            if reply["id"] == "tool":
                assert "custom-tool" in kinds
            if reply["id"] == "hooks":
                assert [e["payload"]["stage"] for e in events if e["kind"] == "hook"] == ["pre", "post"]
