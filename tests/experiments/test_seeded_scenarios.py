import json
from itertools import pairwise
from pathlib import Path

import pytest

from mininet_ai.experiments.scenarios import (
    CounterStream,
    FailurePasses,
    generate_topology,
)
from tests.experiments import example

VECTOR = json.loads(
    Path("docs/fixtures/link-failure-latency-seed-v1-0.json").read_text()
)


def test_counter_stream_matches_version_one_golden_words_and_keys():
    for label, key in VECTOR["stream_keys_hex"].items():
        assert CounterStream(0, label).key.hex() == key
    stream = CounterStream(0, "topology")
    assert [f"{stream.word():016x}" for _ in range(4)] == VECTOR[
        "first_topology_words_hex"
    ]


def test_bounded_draw_rejects_bias_and_shuffle_is_descending_fisher_yates():
    class Words(CounterStream):
        def __init__(self):
            self.words = iter([(1 << 64) - 1, 7, 0, 1, 0])

        def word(self):
            return next(self.words)

    stream = Words()
    assert stream.draw(3) == 1  # max-u64 is rejected, then 7 % 3
    items = ["a", "b", "c", "d"]
    stream.shuffle(items)
    assert items == ["c", "d", "b", "a"]


@pytest.mark.parametrize("seed", [-1, 1 << 64, True, 1.5, "0"])
def test_seed_requires_explicit_unsigned_64_bit_integer(seed):
    with pytest.raises(ValueError, match="seed"):
        CounterStream(seed, "topology")


def test_stream_bounds_and_counter_exhaustion_never_wrap():
    stream = CounterStream((1 << 64) - 1, "topology", counter=(1 << 64) - 1)
    assert 0 <= stream.word() < 1 << 64
    with pytest.raises(OverflowError, match="exhausted"):
        stream.word()
    with pytest.raises(OverflowError):
        stream.word()
    for n in (0, -1, (1 << 64) + 1, True):
        with pytest.raises(ValueError):
            CounterStream(0, "topology").draw(n)
    assert CounterStream(0, "topology").draw(1) == 0
    assert CounterStream(0, "topology").draw(1 << 64) == int(
        VECTOR["first_topology_words_hex"][0], 16
    )
    for counter in (-1, (1 << 64) + 1, True):
        with pytest.raises(ValueError, match="counter"):
            CounterStream(0, "topology", counter=counter)


def test_seed_zero_topology_matches_every_golden_graph_and_placement_field():
    topology = generate_topology(0)
    assert list(topology.switches) == VECTOR["canonical_switch_ids"]
    assert list(topology.cycle) == VECTOR["cycle"]
    assert len(topology.chords) == VECTOR["chord_count"]
    assert [list(edge) for edge in topology.chords] == VECTOR["chords"]
    assert [list(edge) for edge in topology.edges] == VECTOR["edges"]
    assert list(topology.placement_order) == VECTOR["host_placement_order"]
    assert topology.attachments == VECTOR["host_attachments"]
    assert {
        switch: [
            {"neighbor": neighbor, "port": port}
            for (owner, neighbor), port in topology.ports.items()
            if owner == switch
        ]
        for switch in topology.switches
    } == VECTOR["ports_by_switch"]


def test_failure_passes_match_golden_and_are_independent_without_replacement():
    eligible = [tuple(pair) for pair in VECTOR["eligible_failure_links"]]
    measured = FailurePasses(0, eligible, kind="measured")
    warmup = FailurePasses(0, eligible, kind="warmup")
    expected = [tuple(pair) for pair in VECTOR["measured_failure_order"]]
    for _ in range(17):
        next(warmup)
    assert [next(measured) for _ in eligible] == expected
    # Each subsequent attempt consumes a slot regardless of its eventual outcome.
    for _ in range(5):
        assert {next(measured) for _ in eligible} == set(eligible)
    reversed_input = FailurePasses(
        0, [pair[::-1] for pair in eligible[::-1]], kind="measured"
    )
    assert [next(reversed_input) for _ in eligible] == expected
    with pytest.raises(ValueError):
        FailurePasses(0, [], kind="measured")
    with pytest.raises(ValueError):
        FailurePasses(0, eligible, kind="other")  # pyright: ignore[reportArgumentType]


def test_generated_scenario_paths_policies_and_eligibility_match_seed_zero():
    scenario = example.build_scenario(seed=0)
    assert scenario.generator_version == VECTOR["generator_version"]
    for source, destination in (("h1", "h2"), ("h2", "h1")):
        assert [source, *scenario.route(source, destination), destination] == VECTOR[
            "healthy_paths"
        ][f"{source}_to_{destination}"]
    assert [list(pair) for pair in scenario.eligible_failures] == VECTOR[
        "eligible_failure_links"
    ]
    assert scenario.failed_link == "s3-s9"
    assert scenario.route("h1", "h2", failed=True) == ("s7", "s9", "s10", "s6", "s2")
    assert scenario.route("h2", "h1", failed=True) == ("s2", "s6", "s10", "s9", "s7")
    assert scenario.rules("s9", failed=True) == (
        {
            "priority": 100,
            "match": {"dl_dst": "02:00:00:00:00:01"},
            "actions": "output:3",
        },
        {
            "priority": 100,
            "match": {"arp": True, "arp_tpa": "10.0.0.1"},
            "actions": "output:3",
        },
        {
            "priority": 100,
            "match": {"dl_dst": "02:00:00:00:00:02"},
            "actions": "output:1",
        },
        {
            "priority": 100,
            "match": {"arp": True, "arp_tpa": "10.0.0.2"},
            "actions": "output:1",
        },
    )
    # Two five-hop routes from s1 to h2's switch: s4 wins over s5.
    assert scenario.rules("s1")[2]["actions"] == "output:1"


def test_general_graph_replay_preserves_recorded_fault_ports_routes_and_policies(
    monkeypatch,
):
    scenario = example.build_scenario(seed=0).select_failure(("s2", "s3"))
    record = json.loads(json.dumps(scenario.to_record()))
    assert record["generator_configuration"] == {
        "switch_count": 10,
        "chord_count_range": [1, 5],
        "bandwidth_mbps": 100,
        "delay": "1ms",
        "loss_percent": 0,
        "controllers": [],
        "fail_mode": "secure",
        "protocols": ["OpenFlow13"],
    }

    def forbidden(*args, **kwargs):
        raise AssertionError("replay must not regenerate or recompute policy/path data")

    monkeypatch.setattr(example, "generate_topology", forbidden)
    replayed = example.LinkFailureScenario.from_record(record)
    monkeypatch.setattr(example.LinkFailureScenario, "_path", forbidden)
    assert replayed.to_record() == record
    assert replayed.failed_link == scenario.failed_link
    assert replayed.ports == scenario.ports
    assert replayed.attachments == scenario.attachments
    assert replayed.select_failure(("s2", "s3")) is replayed
    with pytest.raises(ValueError, match="recorded"):
        replayed.select_failure(("s3", "s9"))
    for failed in (False, True):
        assert replayed.route("h1", "h2", failed=failed) == tuple(
            record["paths"]["recovery" if failed else "healthy"]["h1_to_h2"]
        )
        for switch in scenario.switches:
            assert (
                list(replayed.rules(switch, failed=failed))
                == record["policies"]["recovery" if failed else "healthy"][switch]
            )


@pytest.mark.parametrize("seed", [0, 1, 7, 8, 42, (1 << 64) - 1])
def test_generated_plans_have_fixed_network_configuration_and_loop_free_policies(seed):
    scenario = example.build_scenario(seed=seed)
    topology = scenario.plan.snapshot["substrate"]["topology"]
    switches = [r for r in topology["resources"] if r["kind"] == "switch"]
    assert len(switches) == len(scenario.plan.agents) == 10
    assert 11 <= len(scenario.edges) <= 15
    assert len(scenario.edges) == len(set(scenario.edges))
    assert all(left < right for left, right in scenario.edges)
    assert len(set(scenario.attachments.values())) == 2
    for switch in switches:
        assert switch["controllers"] == []
        assert switch["failMode"] == "secure"
        assert switch["protocols"] == ["OpenFlow13"]
    for link in topology["links"]:
        assert (link["bandwidth"], link["delay"], link["loss"]) == (100, "1ms", 0)
    for host, addresses in VECTOR["host_addresses"].items():
        resource = next(r for r in topology["resources"] if r["name"] == host)
        assert resource["interfaces"][0]["ipv4"] == addresses["ipv4"]
        assert resource["interfaces"][0]["mac"] == addresses["mac"]
    assert scenario.plan.snapshot["agents"][0]["capabilities"] == [
        "openflow.flow.install"
    ]
    used = set()
    for source, destination in (("h1", "h2"), ("h2", "h1")):
        path = scenario.route(source, destination)
        used.update(tuple(sorted(pair)) for pair in pairwise(path))
    assert set(scenario.eligible_failures) == used
    for pair in scenario.eligible_failures:
        selected = scenario.select_failure(pair)
        for failed in (False, True):
            for host_index, host in enumerate(("h1", "h2")):
                for start in scenario.switches:
                    node = start
                    visited = set()
                    while node != host:
                        assert node not in visited
                        visited.add(node)
                        rules = selected.rules(node, failed=failed)
                        assert len(rules) == 4
                        traffic, arp = rules[host_index * 2 : host_index * 2 + 2]
                        assert traffic["actions"] == arp["actions"]
                        port = int(traffic["actions"].removeprefix("output:"))
                        neighbor = next(
                            peer
                            for (owner, peer), number in selected.ports.items()
                            if owner == node and number == port
                        )
                        assert not failed or {node, neighbor} != set(pair)
                        node = neighbor
    with pytest.raises(ValueError, match="eligible"):
        scenario.select_failure(("h1", scenario.attachments["h1"]))


def test_checked_in_seed_zero_is_the_version_one_generated_plan():
    assert (
        example.build_scenario().plan.digest
        == example.build_scenario(seed=0).plan.digest
    )


def test_saved_general_graph_uses_actual_noncanonical_ports_and_link_names(tmp_path):
    snapshot = example.build_scenario(seed=0).plan.snapshot
    for resource in snapshot["substrate"]["topology"]["resources"]:
        for port in resource.get("ports", []):
            port["number"] += 10
    for link in snapshot["substrate"]["topology"]["links"]:
        link["name"] = "saved-" + link["name"]
    specification = tmp_path / "saved.json"
    specification.write_text(json.dumps(snapshot))
    saved = example.build_scenario(specification=specification)
    assert saved.failed_link == "saved-s3-s9"
    assert saved.rules("s9", failed=True)[2]["actions"] == "output:11"
    assert saved.attachments == VECTOR["host_attachments"]
