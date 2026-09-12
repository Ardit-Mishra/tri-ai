# Portfolio Strategy

**Written:** 2026-09-12 · **Source:** machine-wide discovery under `C:\Users\ardit`,
git state read per repository, live endpoints probed, verify gates read from each
project's own CI definition.

Tri-AI is the kernel. The other four are the work it governs, and together they are
the case for an AI Engineer hire: a model trained and shipped, a bioinformatics
service with a measured native accelerator, an ADMET platform that separates trained
models from heuristics, a grounded explainer with no LLM in its factual path, and an
autonomous orchestrator that will not mark work done without an exit code.

## What was actually verified for this document

| Claim | How it was established |
|---|---|
| Repository paths, branches, dirty state | `git -C <path> status/log/remote`, 2026-09-12 |
| `tri-ai` suite | `python tests/run.py` run here |
| `genclarus` suite | `npm test` run here: 36 files, 333 tests, exit 0, 4.17s |
| `peptidemhc` / `genomesight` / `biostudio` gates | Read from each `.github/workflows/ci.yml`. **Not run locally in this session** |
| Live deployments | HTTP GET, all four returned 200 |
| Model metrics | Quoted from each project's own README/BENCHMARKS. Not re-derived here |

Anything below that was not measured in this session says so. That distinction is the
same one the projects themselves make, and it is the point.

---

## 1. tri-ai — the autonomous kernel

| | |
|---|---|
| **Path** | `C:\Users\ardit\tri-ai` |
| **Branch** | `phase-2/worker-assign` (the canonical checkout; see the guard in STATE.md) |
| **Remote** | `github.com/Ardit-Mishra/tri-ai` (public) |
| **Verify gate** | `python tests/run.py` |
| **State** | Phases 1-6 complete. Working tree clean |

A task assigned once is decomposed, executed in parallel by free local models, and
accepted or reverted **strictly by exit code** — without the expensive model staying
in the loop. The board is the Hermes kanban kernel, used and never edited; the only
board-level addition is the `verify_command` column that turns it into a verify-gated
executor.

**Competencies it evidences**

- *Agentic orchestration.* A real scheduler: atomic claim, lease and heartbeat,
  dependency-gated DAG promotion, per-branch failure isolation, a circuit breaker, and
  bounded concurrency taken from measurement rather than optimism.
- *Systems correctness under an adversarial reading.* The interesting content is the
  defect record: a Windows reclaim path that deferred forever, a `_kill_tree` that
  reported success while an orphan kept writing (replaced with a Job Object, so "we
  killed it" became evidence), an idempotency key that refreshed a verify gate onto a
  stale workspace, a claim that omitted its own PID and let a live run be reclaimed,
  and two opposite Windows liveness defects — a false-down `os.kill(pid, 0)` and a
  false-up bare `OpenProcess`.
- *Operational engineering.* A supervisor that restarts children with bounded backoff
  and a restart budget, a long-poll Telegram control surface whose writes are
  confirmation-gated, and a strictly read-only dashboard whose boundary is enforced by
  AST tests rather than by convention.
- *Honest instrumentation.* An append-only ledger that records the model name only
  when the run's usage file contains it, and renders `not recorded` otherwise.

**What remains to make it a demo**

1. The story is currently only legible by reading `.planning/`. It needs a README that
   opens with the 90-second version: the verify gate, the ledger, one screenshot of
   the HUD.
2. The most compelling artefact is the defect record, and it is buried in STATE.md.
   Three of those — the Job Object, the false-up liveness probe, the stale-workspace
   idempotency bug — are a hiring conversation on their own and deserve short writeups.
3. Phase 7's remote-delegation candidate violates the verify gate and must not merge;
   that boundary is itself worth stating publicly.

---

## 2. genclarus — the governed child project

| | |
|---|---|
| **Path** | `C:\Users\ardit\projects\genelens` — **the directory was never renamed**; `genelens` is Genclarus |
| **Branch** | `evals/provenance-surface` · 1 uncommitted entry |
| **Remote** | `github.com/Ardit-Mishra/genclarus` (public) · **Live:** https://genclarus.com (HTTP 200) |
| **Verify gate** | `npm test` → **36 files, 333 tests, exit 0, 4.17s** (run 2026-09-12) |
| **Stack** | Next.js 16 App Router, TypeScript, Tailwind, Vercel, $0 marginal cost |

A deterministic, grounded gene and variant explainer. Enter `BRCA1` or `rs6025` and
get a cited plain-language explanation built from public bioinformatics records, every
sentence bound to the source it came from. 173 precomputed provenance-stamped pages
plus live lookup for any valid gene or rsID.

**The load-bearing design decision: no LLM sits in the factual path.** Clinical and
identity claims render deterministically from typed facts through a hardened
validator; `aiAvailable` is always false. The NIM and prompt infrastructure exists in
the codebase and is deliberately unconsulted for factual content.

**Competencies it evidences**

- *Knowing when not to use a model.* The most valuable judgement an AI engineer shows
  is refusing the model where a wrong answer is a clinical claim. This is that, shipped.
- *Provenance and evaluation infrastructure.* The current branch is literally an eval
  surface: `measure:grounding`, `measure:retrieval`, `measure:provenance` and a
  scheduled provenance audit — evaluation as a product surface, not a notebook.
- *Modern frontend.* App Router, embeddable widgets, a versioned read API
  (`/api/v1`, `/api/v1/batch`), SEO panel pages, a 3D AlphaFold/RCSB structure viewer
  with pLDDT confidence and UniProt domain colouring.
- *Incident discipline.* A grounding incident on 2026-07-28 was found, closed, and
  re-audited to CLEAR at Stage 5 round 3 — with the log kept.

**Now bound to Tri-AI.** Registered in `~/.tri-ai/intake_policy.json` as workspace
alias `genclarus` → `C:\Users\ardit\projects\genelens`, verify profile
`genclarus-vitest` (`npm test`, 600s). `default_workspace` remains `tri-ai`, so this
adds a target without changing existing behaviour.

**What remains**

1. **Commit or stash the one dirty entry.** Tri-AI's clean-tree precheck will skip
   every run against this workspace until the tree is clean — the exact failure that
   stranded `t_69cc6245`.
2. `evals/provenance-surface` is ahead of `main`; the eval surface should land.
3. The planned bounded AI layer (precomputed structural narration plus a closed-book
   summary) is specified and awaiting sign-off. Shipping it *without* touching the
   factual path would be the strongest possible demonstration of the boundary.

---

## 3. peptidemhc — the trained model

| | |
|---|---|
| **Path** | `C:\Users\ardit\Downloads\live-projects\PeptideMHC` |
| **Branch** | `main` · clean · last commit 2026-09-04 |
| **Remote** | `github.com/Ardit-Mishra/peptide-mhc-binding-predictor` · **Live:** https://peptide.arditmishra.com (HTTP 200) |
| **Verify gate (CI)** | `npx tsc --noEmit` · `node --test scripts/tests/*.test.mjs` · `node scripts/verify-parity.mjs` · `npm run build` · model-asset check. *Not run locally in this session* |

Predicts whether a short peptide binds a given human MHC class I molecule — the step
that decides which protein fragments get displayed to T cells. **The trained model runs
entirely in the browser**: no backend, no database, nothing to wake up.

**Competencies it evidences**

- *Deep learning end to end.* Trained model, exported, and served client-side. The
  allele is encoded as its 39-residue pseudo-sequence (binding-groove contact residues,
  following NetMHCpan's idea), which is real domain modelling rather than a generic
  embedding.
- *Calibration literacy — the strongest single signal in this portfolio.* The README
  states ROC-AUC 0.919 with a 10-bin ECE of 0.093, says Platt scaling would cut that to
  0.008 at no ROC-AUC cost, and then **deliberately does not apply it in production**,
  instructing the reader to treat the output as a ranking score and not a probability
  they can act on. Most candidates report AUC and stop.
- *MLOps rigour.* Cross-repo metric checks run in CI, a browser/Python parity check
  proves the two inference paths agree, and metric fixtures are stored verbatim after
  CRLF normalisation was found to be rewriting them.

**What remains**

1. The calibration decision is the differentiator and is currently a section in
   BENCHMARKS.md. It should be the README's headline.
2. Show the number of training measurements backing each allele in the UI as a
   confidence cue — the data is already returned.
3. The repo lives under `Downloads/live-projects`, which reads as scratch space. Move
   it beside the other work.

---

## 4. genomesight — the bioinformatics service

| | |
|---|---|
| **Path** | `C:\Users\ardit\Downloads\live-projects\GenomeSight` |
| **Branch** | `main` · clean · last commit 2026-09-03 |
| **Remote** | `github.com/Ardit-Mishra/genomesight` · **Live:** https://genomesight-frontend.vercel.app (HTTP 200) · API on Render |
| **Verify gate (CI)** | Backend: build the native k-mer accelerator, report which engine will run, `pytest tests/ -q`. Frontend: `npx tsc --noEmit`, `npm run build`. *Not run locally in this session* |

A sequence analysis workbench — FastAPI service plus React front end. Composition and
GC content, k-mer profiling, six-frame ORF detection, IUPAC motif and restriction-site
search, codon usage with RSCU, translation, pairwise alignment. No model in the factual
path; the same input always gives the same result.

**Competencies it evidences**

- *Bioinformatics pipelines.* The actual daily operations, in one place, with input
  format detected by content rather than by file extension.
- *Performance engineering with numbers.* A Cython k-mer accelerator measured at
  **5.27×-11.04×** over the pure-Python path — and, more tellingly, the API *reports
  which engine produced the counts*, so the speedup claim is checkable at runtime
  rather than asserted in a README.
- *Full-stack service delivery.* FastAPI plus React, deployed across two providers,
  with the API stating the commit it was built from.
- *Defining your terms.* Alignment identity is defined over aligned columns. That one
  sentence separates people who have debugged a bioinformatics result from people who
  have not.

**What remains**

1. Render's free tier cold-starts; the front end should show a warming state rather
   than appearing broken on first load. A `keep-alive.yml` workflow already exists —
   confirm it is doing the job.
2. CI found two defects a local checkout hid (commit `061fe89`) — a good CI-value story
   worth one paragraph in the README.
3. A worked example on the landing page ("paste this, get this") would demonstrate
   value before a recruiter has to think of a sequence.

---

## 5. biostudio — the ADMET platform

| | |
|---|---|
| **Path** | `C:\Users\ardit\Downloads\live-projects\Ardit-BioStudio` |
| **Branch** | `ui/toxicity-radar-clarity` · clean · last commit 2026-09-05 |
| **Remote** | `github.com/Ardit-Mishra/biostudio` · **Live:** https://biostudio.arditmishra.com (HTTP 200) |
| **Verify gate (CI)** | ADMET model test suite via `uv`. *Not run locally in this session* |
| **Stack** | Python 3.11, RDKit, scikit-learn, XGBoost, Streamlit, FastAPI, Docker |

Computational drug discovery platform: molecular descriptors, drug-likeness, ADME/PK,
toxicity modelling, target class prediction, knowledge graph exploration.

**Competencies it evidences**

- *The distinction most portfolios blur.* Seven ADMET endpoints (DILI, hERG, Ames, BBB,
  P-gp, CYP3A4, Caco-2) are gradient-boosted models trained on Therapeutics Data Commons
  and evaluated once on a held-out **scaffold** split — with metrics, thresholds and
  test-set sizes generated from a manifest rather than typed into a table. Everything
  else — ADME/PK scoring, structural-alert screens, target-class likelihood — is
  rule-based, carries no held-out metrics because it was never fitted, and is **labelled
  heuristic at the point of display**.
- *Stating the limits of your own work.* The README says what would be required for
  production use: replacing the heuristics with validated QSAR models, and prospective
  validation well beyond a single split.
- *Reproducibility.* `METHODOLOGY.md`, `REFERENCES.md`, `VALIDATION.md`, `CITATION.cff`,
  a Dockerfile, and a single-sourced dependency contract guarded in CI.

**What remains**

1. `ui/toxicity-radar-clarity` is unmerged; the head commit makes Toxicity Radar say
   which molecule and which method it ran — exactly the honesty this project is built
   on. Merge it.
2. Scaffold-split metrics belong in the UI beside each trained prediction, not only in
   `VALIDATION.md`.
3. Streamlit cold start is the first impression; a prepared example molecule that loads
   instantly would carry it.

---

## How they combine

The portfolio reads as one argument if it is presented in this order:

1. **peptidemhc** — I can train, evaluate, calibrate and ship a model, and I know when
   not to apply a calibration.
2. **biostudio** — I can build a platform around models and be precise, in the product
   itself, about which outputs are earned and which are heuristics.
3. **genomesight** — I can build and deploy the deterministic service layer underneath,
   and make its performance claims checkable at runtime.
4. **genclarus** — I can decide a model does not belong in the factual path at all, and
   build the provenance and eval infrastructure that proves the boundary holds.
5. **tri-ai** — and I can build the agentic system that runs the other four, where
   nothing is accepted without an exit code.

The through-line is **verified rather than asserted**. Every project refuses to claim
something it has not measured, and Tri-AI enforces that mechanically rather than by
good intentions.

## Immediate actions

| Priority | Action | Project |
|---|---|---|
| 1 | Commit or stash the dirty entry so Tri-AI runs can pass the clean-tree precheck | genclarus |
| 2 | Merge `ui/toxicity-radar-clarity` | biostudio |
| 3 | Land `evals/provenance-surface` on `main` | genclarus |
| 4 | Lead the README with the calibration decision | peptidemhc |
| 5 | Write the README that makes the verify gate and ledger legible in 90 seconds | tri-ai |
| 6 | Move the three repos out of `Downloads/live-projects` | pmhc, gsight, biostudio |
| 7 | Run each project's CI gate locally and record the real numbers here | all |

Item 7 matters for this document specifically: three of the five gates above are read
from CI definitions rather than executed. Until they are run, their pass status is
reported, not verified — and this file should not pretend otherwise.
