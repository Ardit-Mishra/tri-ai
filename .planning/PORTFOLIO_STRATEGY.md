# Portfolio Strategy

**Audited: 2026-09-13.** Every claim below is checked against the machine. Where
a project was named but not found, that is recorded as not found rather than
described from the brief.

## What exists

### 1. Tri-AI — autonomous agentic kernel
`C:\Users\ardit\tri-ai` · branch `phase-2/worker-assign` · 79 commits

The supervisor, worker, and Telegram control plane that runs verify-gated tasks,
plus a strictly read-only observability surface.

- Phases 1–5 accepted. Mobile intake, natural-language staging, and `/confirm`
  execution are live and have been exercised end to end from a phone.
- Phase 6 (JARVIS dashboard) implemented through the spatial HUD: read-only
  terminal and web surfaces, run telemetry, lifecycle phases derived from
  recorded evidence, bounded log tails, artifact capture, completion delivery
  to Telegram, and artifact serving over Tailscale.
- Verification gate: `python tests/run.py` → **359 tests, exit 0, 204.436s**.

**Recruiter-facing demo:** send a plain-language request from a phone, watch the
task move through claim → build → verify on a live HUD, receive the finished
artifact as a tappable link, and reply to that message to queue a linked
revision. The interesting part is not that an agent writes code — it is that
nothing is called done without an exit code, and the observability surface
cannot mutate what it observes.

### 2. Genclarus — grounded gene & variant explainer
`C:\Users\ardit\projects\genelens` · branch `evals/provenance-surface` · 78 commits
· repo `Ardit-Mishra/genclarus` · live at **genclarus.com**

Type a gene (`BRCA1`, `TP53`, `CFTR`) or an rsID (`rs6025`) and get a cited,
plain-language explanation assembled from ClinVar, dbSNP, gnomAD and MyGene,
with every sentence traceable to the record it came from.

- Stack: Next.js 16, TypeScript, Tailwind, Vercel, NVIDIA NIM (treated as
  replaceable — the app still answers source-only without it).
- Shipped: v1.1 plus hardening phases 0–1, hybrid retrieval with a retrieval
  eval, per-instance rate limiting, and a published provenance eval surface that
  audits every shipped claim.
- The most recent work moved the provenance audit onto a schedule rather than
  running it on every push.
- Documented in depth: ADRs, validation reports (grounding, retrieval), QA
  founder-acceptance walkthroughs, a logged incident
  (`INCIDENT-2026-07-28-grounding.md`), and GTM material.

**Recruiter-facing demo:** a live URL, a stated anti-hallucination thesis, and
an eval surface that measures whether the thesis holds. The incident write-up is
an asset, not a blemish — it shows grounding failures were found and closed.

**Relationship to Tri-AI:** registered as a governed workspace under the alias
`genclarus`, with `npm test` as its verify profile, so Tri-AI can be given work
that targets it.

## What was named but not found

`peptidemhc`, `genomesight`, and `biostudio` were described as flagship
portfolio projects. A scan of `~/tri-ai`, `~/projects`, `~/Documents`,
`~/Desktop`, `~/source`, `~/repos`, `~/dev` and `~/code` across every `.md` and
`.json` file returned **zero mentions of any of the three**, and no directory by
those names exists.

They are therefore **intended, not started**. Nothing here describes them as
though they exist, because no evidence was found that they do. If they live on
another machine or under different names, point at them and this document gets
corrected.

## Workspaces registered with Tri-AI

| Alias | Path | Verifier |
|---|---|---|
| `sandbox` *(default)* | `~/tri-ai-sandbox` | `python verify.py` |
| `tri-ai` | `~/tri-ai` | `python tests/run.py` |
| `genclarus` | `~/projects/genelens` | `npm test` |

`sandbox` became the default on 2026-09-13. Ad-hoc work used to run in the
kernel repo, where each delivered file left the tree dirty and the next task
skipped at the clean-tree precheck. It also gives ad-hoc work an honest
verifier: `verify.py` fails when a run produced no file and when produced HTML
does not parse, instead of running the kernel's suite, which proves nothing
about a requested page.

## How the work is actually produced

Tri-AI is built by two agent seats with different jobs, and the separation is
deliberate rather than incidental. `~/CODEX-REVIEWER-BRIEF.md` states it: Claude
writes, Codex reviews, and the reviewer is instructed not to implement. A review
that says "looks good" is treated as worthless, because that is the same failure
mode the project exists to prevent — every finding must carry a command and its
exit code, a file and line, or a concrete failure scenario.

It earns its keep. The reviewer's first pass caught a test whose *name* claimed
it proved a schema collision was refused while its body only inspected a
throwaway table and never called `migrate()`. On 2026-09-13 it caught delivery
documented as exactly-once that was in fact at-least-once, with a repro showing
the duplicate window. Both are the same class of defect: a claim that outruns
its evidence, which self-review does not reliably catch.

The commit history shows the two seats interleaved rather than sequential —
of 86 commits, 26 carry a Claude co-author trailer and the rest do not, spread
throughout the project's life.

This is worth stating plainly because the alternative reads as a single author
producing 9,000 lines unaided, and that would be the same overclaim the system
is built to refuse.

## How these combine

Tri-AI is the kernel; Genclarus is the first governed child. The pairing is the
pitch: a system that decomposes and verifies work, and a live product built
under it whose central claim — every sentence traceable to a source — is itself
measured by an eval surface.

The shared discipline across both is the actual through-line: **nothing is
accepted without evidence.** Tri-AI refuses to mark a task done without an exit
code; Genclarus refuses to state a fact without a citation, and audits that it
held. That is one engineering conviction expressed twice.

## Honest gaps

- **Three of the five named projects do not exist.** The portfolio is two
  projects, not five.
- **"Verified" is weaker than it sounds for ad-hoc tasks.** Until the sandbox
  workspace existed, a task's verify command was the kernel's own test suite —
  so a page could be marked verified on evidence unrelated to it. `sandbox`
  fixes this going forward; historical rows carry the weaker meaning.
- **Artifact capture attributes concurrent edits to the run.** It diffs the
  working tree, so anything another writer changed mid-run is recorded as that
  run's output. A tree snapshot taken at claim would fix it.
- **Genclarus has not been driven by Tri-AI yet.** The workspace is registered
  and the verify profile is configured, but no task has been executed against
  it. That is the next thing to prove, and it is what makes the "kernel
  governing a child project" claim real rather than aspirational.
