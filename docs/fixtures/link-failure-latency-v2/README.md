# Synthetic v2 conformance fixtures

See [the artifact contract](../../latency-artifacts-v2.md) for the record format,
offline validation API, fixture expectations, and regeneration command.

- `records-valid.json` and `records-invalid.json`: arrays of `{family, record}`
  cases for all five schema families and every discriminated event variant.
- `cases.json`: artifact directory names and expected persisted state, integrity,
  and optional diagnostic code. Each directory contains actual v2 filenames.
- `interrupted-started-trial` intentionally lacks finalized projections and a
  summary. `truncated-tail` intentionally ends with an incomplete JSONL record.
- The seed-0 replay graph and port evidence comes from the existing generator
  golden vector. No fixture is live Mininet or Provider acceptance evidence.

Do not normalize the torn tail or add fabricated records to an incomplete case.
