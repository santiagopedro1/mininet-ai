# Retained bounded-decision evaluation evidence

`diagnosis-runtime-native-20261029.tar.gz` retains the complete failed
four-agent native runtime check described in the live-model report:

SHA-256: `70e72ebb8254d1489def008db77e06f94b853be4f2b127d7071aa2512111c935`.

- constructed healthy local observations, not live OVS telemetry;
- actual `ExperimentRuntime`, coordination, cadence and 5-second deadlines;
- existing native service, one slot, context 8192;
- two correct healthy choices and four timeout failures, no proposed actions;
- Fake Substrate cleanup only, not host/network verification;
- a later independent idle-slot observation after local stop initially left
  remote work active; no inference retries.

The archive contains the source configuration, compiled plan, normalized
invocations, SQLite ledger, full runtime report, summary, remote-slot status,
and post-run service properties. Exact supplied inputs/responses and failures
are retained, including startup probes. Hardware, backend, launch details and
a freshly verified artifact hash are not established by this evidence.

Extract into a **new** directory for inspection. Never execute/replay inference
automatically from evidence; the archive is a failed workload record, not a
model support baseline or an OVS acceptance result.
