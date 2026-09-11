# Phase 5 Route Admission Plan

**Status:** implemented and locally verified (2026-09-11).

## Purpose

Promote a local route only from measured binary-verifier evidence. This is not
executor integration and does not launch Hermes, read a Hermes profile, read a
credential, contact an endpoint, or complete a board task.

## Contract

- An operator-owned policy names a version, task kind, exact route name and
  resolved model, maximum resident model count, and measured VRAM ceiling.
- A baseline and candidate submit the same unique fixture ids. The baseline
  itself must have verifier exit 0 for every fixture; otherwise no admission
  claim is valid.
- Candidate admission requires verifier exit 0 for every fixture, exactly the
  policy-owned resolved model, no unapproved loaded model, no excess resident
  model count, and no VRAM-cap breach. An agent success report is recorded but
  never consulted.
- Every admission or refusal becomes a durable local JSONL evidence record.
- The component imports neither the board, worker, executor, nor Hermes.

## Proofs

1. Unknown task kind and malformed/duplicate benchmark fixtures are refused
   before a candidate can be admitted.
2. A candidate with a non-zero verifier exit is refused even when its agent
   report claims success.
3. A wrong resolved model, an unapproved second model, excess residency, and a
   VRAM breach are each refused.
4. A fully matching candidate is admitted and its route, model, policy version,
   and per-fixture verifier evidence survive JSONL round-trip.
5. AST audit proves the component cannot complete a task or import execution
   modules.

## Exit Gate

`python -m unittest tests.test_route_admission`, then `python tests/run.py`,
must exit 0 before the local commit. A later executor-adoption plan needs a
real operator-owned Hermes profile and a real benchmark record accepted by this
gate; it must not turn this synthetic fixture into a claim that OmniRoute or
Ollama was live-tested.

**Implementation record:** `route_admission.py` evaluates only supplied
verification/resource evidence. The route and model must both exactly match
the policy, each fixture's verifier exit must be zero, and every loaded model
must be the one policy-owned model. It records both approval and refusal as
diagnostic JSONL and is structurally unable to reach board acceptance.
