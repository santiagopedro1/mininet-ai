# Plan to solve noncanonical names
1. specification/models.py — SwitchResource: add optional dpid: str | None = None.
	- Validate ^[0-9a-fA-F]{1,16}$, normalize to 16-char lower-zero-padded, reject 0.

2. compiler/models.py — PlannedSwitch: add required dpid: str.

3. compiler/compiler.py — _planned_node/_build_resources:
	- If explicit, use normalized value, check uniqueness.
	- Else auto-generate deterministic stable DPID, e.g. sha256(name) truncated to 64-bit, non-zero, collision-resolved by increment. Decision needed: always set explicit (even for s1→000...0001) vs. only for non-sN to preserve Mininet default. I recommend always set for determinism.

4. substrates/mininet_ovs/driver.py — validate_resources: validate DPID format/uniqueness. Keep existing 15-byte Linux interface name check for switch/port names.

5. substrates/mininet_ovs/runtime.py — _build_network: pass dpid=switch.dpid to addSwitch. No changes to observations.py/actions.py — they keep using switch.name as bridge.

6. Tests: compiler passthrough + auto-stable/unique + duplicate/invalid reject; driver DPID tests; runtime fake-bindings assert dpid passed.

7. Docs: update iperf-throughput/README.md caveat, add edge-sw example. Backward compatible — optional field, v1alpha2 unchanged. Golden plans get new dpid field.