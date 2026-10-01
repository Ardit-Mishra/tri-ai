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

## The model sweep

`model-sweep-2026-09-30.json` is a second kind of evidence, and it exists because the ledger
above could not answer a question put to it: *which model actually ran this?*

Every model the router listed — 574 of them — was called once with the same one-line prompt,
and the **served** model name in the response was compared against the one requested. Twenty
answered as themselves. Twenty-two answered at HTTP 200 as something else entirely, including
every `auto/*` alias.

[`MODEL_SWEEP.md`](MODEL_SWEEP.md) reads the file. Check it the same way:

```bash
python -c "import json;r=json.load(open('evidence/model-sweep-2026-09-30.json'));print(len(r))"
python -c "import json;r=json.load(open('evidence/model-sweep-2026-09-30.json'));print(sum(x['status']=='ok' for x in r))"
```

Provider error text is redacted for filesystem paths (`<home>`) and nothing else; no
credential, endpoint or address appears in the file. The consequence for `ledger.jsonl` is
stated in `MODEL_SWEEP.md` and worth repeating here: **the model names recorded in runs before
2026-09-30 are not evidence of which model ran**, because every one of them was routed through
an alias. The exit codes in that file are unaffected — a verify command's result does not
depend on knowing which model produced the work, which is the whole point of gating on it.
