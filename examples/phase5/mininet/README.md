# Phase 5 Mininet/OVS coordination experiment

This is the live-network counterpart of the rootless Phase 5 example. It keeps
the same deterministic coordinator, two switch remediators, centralized graph,
and `reject` conflict policy, but deploys a real Mininet topology with one OVS
switch, two hosts, and the built-in controller. The winning proposal executes
`ovs-ofctl add-flow`; the conflicting proposal is rejected before a second
network mutation occurs.

Compile it without root privileges:

```bash
uv run mininet-ai validate examples/phase5/mininet/experiment.yaml
uv run mininet-ai run examples/phase5/mininet/experiment.yaml --dry-run
```

Run it only inside the disposable Vagrant environment used for Mininet tests:

```bash
vagrant up
vagrant ssh
cd /vagrant
sudo scripts/vm-run.sh mininet-ai run \
  examples/phase5/mininet/experiment.yaml \
  --intent 'primary-remediator@s1=Coordinate a safe forwarding repair for s1.' \
  --format json
```

Wait for the intent to complete, then press Ctrl+C. The final JSON report
contains the versioned coordination outcome under
`continuous.invocations[0].coordination`. It should contain three messages,
three invocations, one `execute` arbitration decision, and one `reject`
decision with issue code `coordination.conflict.rejected`.

The live run writes ownership state under `/run/mininet-ai` and must retain its
foreground owner until teardown. If a normal stop fails in the disposable VM,
follow the repository's Mininet cleanup procedure; do not use emergency cleanup
on a shared machine.
