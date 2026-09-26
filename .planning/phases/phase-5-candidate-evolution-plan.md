# Phase 5 Candidate Evolution Plan

**Status:** implemented and verified locally (2026-09-11).

Repeated cited logic failures may produce deterministic draft candidates. A
candidate has no activation path: it cannot alter prompts, verifier commands,
tasks, routes, rules, or schemas. It remains `draft` and requires explicit
operator review. Citation drift invalidates the candidate; replay only reports
supporting held-out evidence and never activates it. Environment and single
failures are deliberately excluded: this slice produces advice candidates, not
runtime recovery behavior.

Exit gate: `python -m unittest tests.test_candidate_evolution`, then
`python tests/run.py`, must exit 0 before the local commit.

## Evidence

- `python -m unittest tests.test_candidate_evolution` — 8 tests, exit 0,
  0.280s.
- `python tests/run.py` — 212 tests, exit 0, 171.113s.
- The adversarial boundary test rejects direct execution, task mutation,
  activation, file-mutation, and dynamic-code paths in the candidate module.
