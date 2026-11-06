# Ticket 05 independent review

## Scope

Comparison: `git diff 1b0ea1a...93ddbc8`, ticket 05 only, with the user's
explicit offline-only authorization. Reviewed commit:
`93ddbc8530d9a2bb4ee1d7032dbfdacbe7e4570a`.

The originating paper tracker is
`/home/pedro/projects/papers/mininet-ai/noms27/.scratch/four-treatment-recovery/`:
`spec.md` and `issues/05-run-six-class-matched-blocks.md`.
Live integration and scientific acceptance are deferred, not waived.

## Standards

Independent reviewer: `ses_e4dd63e18ffe0AywP7gm9UW4Op`.

No documented-standard violations or actionable heuristic smells in the ten
changed files. The separate structural and packet-delivery walks intentionally
preserve an independent-verification boundary.

## Spec

Independent reviewer: `ses_e4dd63e13ffeCkJSgZTgLOy495`.

One P2 finding: controlled S2 execution discarded `in_port`, installing a
switch-wide next hop. Packet delivery likewise ignored ingress. A rule matching
the wrong ingress could pass authority validation and falsely appear to recover
service. This violates the configuration-to-evidence mechanism-validation
contract and the S2 ingress-matched forwarding semantics.

Requested correction: model ingress-matched forwarding and exercise a
wrong-ingress repair through emitted incident evidence. Correction and final
validation are pending.

## Parent validation before correction

On integration `93ddbc8`:

```bash
/home/pedro/projects/mininet-ai/mininet-ai/.venv/bin/python -m pytest -q \
  --basetemp=/tmp/opencode/r05-review-tests
```

**1,466 passed, 3 skipped, 179 subtests passed**, 124 existing warnings,
205.77 seconds. Passing these tests did not disprove the review finding.

Summary: Standards **0** findings; Spec **1** finding, ingress matching.
