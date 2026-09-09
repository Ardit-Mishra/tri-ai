# Evidence

`ledger.jsonl` is the run record behind the claim in the top-level README:

> **9 of 9** queued tasks passed across **6 repositories**, **~1,216 seconds** of agent time, **$0**

Those numbers are derived from this file and nothing else. Nine lines, nine `"result": "pass"`,
six distinct repositories, and `seconds` summing to 1216.2.

```bash
wc -l evidence/ledger.jsonl                                     # 9
grep -c '"result": "pass"' evidence/ledger.jsonl                # 9
python -c "import json;print(round(sum(json.loads(l)['seconds'] for l in open('evidence/ledger.jsonl')),1))"
```

Publishing this matters more than it might look. A project whose whole argument is *the agent's
report is never evidence* has no business asking anyone to take its own headline number on trust.

## What was changed from the original

Absolute paths only. `repo` fields, and any path appearing inside captured output, were replaced
with `<repos>/<name>`; one Hermes cache path became `<hermes-cache>/out-<id>.log`. Everything else —
exit codes, durations, timestamps, captured stdout, and the agents' own reports — is verbatim.

The live ledger stays gitignored because it carries absolute local paths; this is its redacted twin,
not a summary of it.

## What is worth noticing in it

**The agent's report and the verify output are separate runs, and they disagree.** In `gs-tests` the
verify command recorded `99 passed in 2.55s` while the agent reported `99 passed in 7.65s`. Same
suite, same result, different timings — because the agent ran the tests itself and the verifier then
ran them again independently. Only the second one decided the outcome. This is the mechanism working
exactly as intended, visible in the data.

**A task can pass while something inside it fails.** `gc-checks` and `gc-checks-provenance` both
record the agent reporting lint failures — 290+ errors, later 296 errors and 6,380 warnings — and
both are marked `pass`, because the verify command was typecheck-and-test, not lint. The gate is
exactly as wide as the command you wrote and no wider. That is a property to design around, not a
bug: if lint mattered, it belonged in the verify command.

**Every entry here is a chore with a mechanical oracle** — test suites, typechecks, builds, a parity
harness, a token regeneration proving no drift. No prose, no judgement calls. That is the boundary
described in the top-level README, and this file is what it looks like when it is respected.

## What this is not

Nine tasks is a small sample from a single operator's machine. It demonstrates that the
verify-gated loop works and produces an auditable trail. It does not establish reliability at
scale, across models, or on work without a mechanical oracle — and the README's "honest ceiling"
section says so.
