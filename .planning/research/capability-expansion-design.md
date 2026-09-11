# Capability Expansion Design

**Status:** approved design, not yet an implementation plan
**Date:** 2026-09-11
**Decision:** local verified promotion is permitted only within the boundary below. RAG, local model routing, semantic memory, and constrained self-evolution are intended product capabilities, not rejected ideas.

## Purpose

Tri-AI must become more capable across runs without weakening the rule that a task is accepted only by a verifier's recorded exit code. These additions make past evidence usable, select local models from measured evidence, and promote reviewed work locally after a fresh integration verification. They never grant a worker authority to push, deploy, read credentials, edit its verifier, or declare its own result accepted.

## Global Invariants

1. The board and ledger remain sources of truth. Memory, routing, and RAG are advisory inputs, never acceptance authorities.
2. Each automated decision records inputs, policy version, and output in a durable local artifact with an adversarial test.
3. All inference and embeddings are local. No metered per-token API, account, credential, or automatic network request is introduced.
4. Worker execution remains unable to push, deploy, or merge protected/remote branches. Promotion is a separate, narrow local process.
5. A later phase may use an accepted lower-phase artifact, never a candidate branch or agent report.

## A. Local Verified Promotion

### Allowed scope

An explicit, review-approved promotion request may merge a local source branch into a dedicated local integration branch. It may never target `main`, a protected branch, a remote ref, or an arbitrary ref supplied by a worker. The process never pushes and never reads credentials.

The first implementation accepts only static, operator-owned allowlists of integration targets and verifier commands. A request binds an immutable source commit SHA, allowlisted local target, reviewer-attested approval artifact for that SHA, operator-owned integration verifier and timeout, and unique promotion id.

### State machine

`requested -> staged -> verified -> locally_integrated` is the only success path. `conflict`, `verify_failed`, `dirty`, `approval_mismatch`, or unknown Git/process state hard-stop and retain the staging worktree and logs as evidence. No retry renames, deletes, force-updates, or fallback targets.

A clean disposable staging worktree starts from the allowlisted target. The fixed source SHA is merged there without contacting a remote. The operator-owned integration verifier runs in a contained process. Only exit 0, a clean post-verify assertion, and matching review artifact permit a local integration commit. The ledger records source SHA, target, verifier result, review-artifact digest, staging path, and resulting local commit.

Raw worker diffs are not promotion inputs. A worker neither commits nor promotes its own output; a reviewed local branch is the minimum promotion input.

### Required proofs

- A `main`, remote, or non-allowlisted target is refused before Git mutation.
- A forged or stale approval artifact is refused before staging.
- A conflict, failed verifier, dirty post-verify tree, and containment failure create no integration commit and retain evidence.
- A deliberate verifier break returns non-zero and prevents integration.
- A good reviewed source produces a local integration commit only after fresh verifier exit 0; the test reads the local ref and ledger.
- Structural audit proves the worker/executor closure still has no merge or remote-capable Git form; promotion owns the only narrowly allowlisted local merge form.

## B. Local RAG Probe and Retrieval Layer

RAG is a bounded context selector, not a source of acceptance. The initial local-only corpus permits repository documentation, planning records, and task-owned workspace files. It excludes credentials, environment files, VCS internals, dependency trees, binaries, and paths outside the task workspace.

Every returned chunk carries canonical path, content digest, source commit when available, and line range. Changed, missing, outside-workspace, or unallowlisted sources invalidate the citation. Context has a fixed budget and deterministic ordering/tie-breaker.

### Probe before shipping retrieval

The fixture contains known-answer contexts, stale counterparts, poisoned high-similarity text, and forbidden paths. It records retrieval recall, wrong selections, runtime, and local resource use against no-retrieval baseline. It graduates only when every expected citation is returned, no forbidden/stale/poisoned citation is injected, and verifier pass rate does not regress on the same fixture. Otherwise it remains diagnostic evidence, not worker input.

### Required proofs

- A relevant known chunk is cited with exact digest and line range.
- Traversal, symlink escape, `.env`, credential-like file, stale digest, and poison fixture are rejected.
- Re-running the same query/corpus yields byte-identical context ordering.
- RAG cannot alter a verify command, artifacts, task target, or acceptance result.

## C. Measured Local Model Routing

Routes are selected by a declared task-kind enum, never by an LLM classifying itself. The route manifest names only installed local models and records a policy version. Its data comes from a task-kind benchmark whose outcomes are existing binary verifiers, not prose.

Each candidate is compared to the fixed baseline on the same code/prose/markup fixture. The probe records verifier outcome, timeout, duration, resource measurement where available, route policy, and resolved model. A route ships only with no verifier-pass regression and within configured residency/concurrency budget. The initial table is static; no task silently falls back to another model. An unavailable route is a recorded bounded failure.

### Required proofs

- Invalid task kind or unknown/non-local model is rejected before agent launch.
- A deliberately regressed candidate fails the promotion gate despite an agent success report.
- The ledger records selected route and policy version.
- A fixture proves a route cannot load an unapproved second model or bypass the measured residency cap.

## D. Semantic Memory and Constrained Self-Evolution

### Semantic memory

Semantic memory is a provenance graph derived deterministically from board links, ledger outcomes, artifact metadata, and accepted procedural rules. It does not ask an LLM to invent edges. Every node/edge retains its source record and becomes invalid when that source changes or disappears.

**Proof:** create dependent tasks and runs, derive exact `depends_on`, `produced`, and outcome edges, then mutate/remove a cited source and assert the fact is invalidated.

### Evolution

Evolution creates candidate procedural rules from repeated classified verified failures. It runs citation validation and replay against held-out ledger fixtures. It may automatically activate only `preflight_advice`: workspace-scoped, expiring, additive advice that cannot change a command, model route, verifier, expected artifact, task graph, source/target branch, or Git/network capability.

Prompt rewrites, model-policy changes, task kinds, graph mutations, and code changes remain proposals for operator or premium-planner approval. A worker can never activate a rule about its own running task.

**Proof:** repeated deliberate failures create a cited candidate; invalid citation or failed replay prevents activation; permitted advice applies only within scope/expiry; a forbidden class never activates, even with a passing candidate report.

### Bounded graph healing

The allowed form is template-based remediation: a known error fingerprint may enqueue an operator-approved, additive template with a predeclared verifier. It cannot accept a local model's arbitrary patch, rewrite an existing task, or merge code. The template has the standard board, verify, retry-cap, and ledger path.

## Delivery Order

1. Finish and independently review Phase 4; Slice 2 adversarial tests remain the current atomic task.
2. Add Phase 4.1 local verified promotion as a separate plan and review gate.
3. Phase 5A: episodic and procedural memory.
4. Phase 5B: RAG and model-routing probes. Failed probes produce evidence, not forced features or hidden retries.
5. Phase 5C: semantic memory using accepted episodic/procedural artifacts.
6. Phase 5D: constrained self-evolution and template-based healing.
7. Phase 5 Telegram control/intake, then Phase 6 dashboards, Phase 7 planner integration plus trusted remote delegation, and Phase 8 offload/phone control.

This resolves the prior ambiguity: semantic memory and evolution are Phase 5C/5D. Phase 6 is dashboards; Phase 7 is planner integration and trusted delegation. The Phase 5 implementation plan must turn these into atomic, reviewable tasks before source changes.

## Independent Review Packets

The subagent service was unavailable during this design (`Transport closed`), so no independent review is claimed. Before implementation, preserve read-only reports in `.planning/reviews/` answering:

1. **Promotion:** find a path from unreviewed worker diff or failed verifier to integration commit; test target/ref and approval substitution.
2. **Retrieval:** find containment, stale-citation, deterministic-ordering, corpus-poisoning, and context-budget bypasses.
3. **Routing:** find LLM-controlled routing, unmeasured fallback, baseline-comparison weakness, and multi-model residency gaps.
4. **Memory:** find a fact/rule that outlives evidence, changes activation class, or affects its generating task.

No implementation may claim independent review until those reports exist.
