# STATE — 2026-09-25

Verified on `Vivo-S14` (laptop). Desktop `desktop-jhq7hjm` (100.67.149.86) and
phone `ardits-s25-ultra` (100.77.102.2) are on the tailnet.

## Verification run

```
cd ~/worktrees/tri-ai-freellm-router && python tests/run.py
Ran 694 tests in 243.376s — OK — exit 0
```

## Blocking findings

**1. The Codex capability work is not committed.** Branch `codex/freellm-router`
has two commits (`ca5d6c6` FreeLLMAPI routing, `c591661` capability catalog),
but everything built after them is loose in the working tree:

- untracked: `src/capabilities.py`, `src/capability_catalog.py`,
  `src/technology_radar.py`, `src/memory/brain.py`, `src/dashboard/tri_space.js`,
  `src/dashboard/vendor/`, `config/capability-adapters.json`,
  `docs/BRAIN_ARCHITECTURE.md`, `docs/TECHNOLOGY_RADAR.md`,
  `tests/test_brain.py`, two radar scripts
- modified: `board.py`, `planner.py`, `worker.py`, both dashboard modules,
  `telegram_daemon.py`, `telegram_control.py`, and 8 test files

The 694 passing tests are passing *against the working tree*. A `git checkout .`
or a clean clone loses all of it. Commit before any further work.

**2. The branch is missing `DECISIONS.md`.** It forked before `4c8e454` on main.
`git diff main..HEAD` shows `DECISIONS.md | 94 ------`. Merge main in.

**3. There is no model inventory to route across.**

| source | reality | README claims |
|---|---|---|
| OmniRoute (`:20128`) | `omniroute models list` → **"No models found."** | the router |
| Ollama (`:11434`) | **1 model** — `gemma-4-12B-coder…Q4_K_M`, 7.4 GB | 17 desktop / 11 laptop |
| FreeLLMAPI | health loop reports **2 keys** | "many models" |

Capability-aware routing cannot be built on this. This is a configuration
problem, not a code problem, and it blocks the whole orchestration goal.

**4. The daemon fleet has been down since 2026-09-14.**
`~/.tri-ai/logs/daemons.json` → `"status": "stopped"`, `"exit_code": 1`.

**5. Telegram transport, root cause confirmed.** `telegram.log` shows
`URLError` / `ConnectionResetError` / `TimeoutError` exhausting the 10-retry
budget, supervisor restart, repeat. Codex checked the network: Telegram resolves
over ordinary Wi-Fi DNS and the same Python HTTPS stack reaches it fine —
**Tailscale is not carrying Telegram traffic** and is not the cause. The bug is
that a finite retry budget treats transient network loss as fatal. Network
failures need continuous retry; auth/config failures must still fail fast.

## Live ledger (`~/.tri-ai/ledger.jsonl`) — diverges from the committed one

28 entries, 2026-09-10 → 09-22: **14 passed, 10 failed**, 2 skipped,
1 quarantined, 1 blocked. 24,641 s of agent time. No metered API in any entry.
`evidence/ledger.jsonl` in the repo still shows the older 9/9.

The failures are the strongest evidence the project has and are currently
unpublished: `this run changed nothing in the workspace - no deliverable` (×3),
`declared artifact check failed: missing 'index.html'` (×4),
`page title is absent; result surface is absent`.

## README drift (both directions)

| README | actual |
|---|---|
| 22 tests | 694 |
| ledger 9/9 | 28 entries, 14/24 real outcomes |
| "phone executes work and reports back — **not built**" | most ledger rows are `Telegram: <workspace>` |
| "worker claims from board and runs the graph — **not built**" | `worker_daemon.py` under `daemon_supervisor.py` |

## Supply-chain note for the technology radar

A GitHub search for `maka` returns **nine** repos with byte-identical
descriptions; `brightbean-studio` returns **seven**. Fork-swarm/typosquat
patterns target exactly an automated "scan trending repos and test them" loop.
The radar must pin `owner/repo` **and commit SHA**, never resolve by name or
star count. Codex's quarantine + static-scan + container-only probe is the right
shape — it correctly held OpenMAIC (install hooks) and VoiceStudio
(download-and-execute patterns), and refused to run anything when Docker was off.

---

# SESSION 2026-09-25 (later) — capability + routing audit

## Verified this session

| check | result |
|---|---|
| `python tests/run.py` (codex branch worktree) | **694 pass, exit 0**, 243 s |
| OmniRoute after key registration | **2,545 models**, 38 semantic aliases, 8 live providers |
| FreeLLMAPI `/v1/models` | 306 catalogued, **31 available**, 216 blocked `no_key` |
| Desktop Ollama over Tailscale | **18 models**, OpenAI-compatible, verified with a completion |
| Laptop Ollama | 1 model |
| Skills installed this session | **106** (646 -> 752), 2 renamed, 0 skipped |
| Codex review | **UNAVAILABLE** — usage limit until 2026-09-26 11:21 |

## Lane benchmark (warm, temp 0, correctness-checked)

`~/.tri-ai/lane-bench.json`. Correctness = returned code actually dedupes.

| lane | correct | median |
|---|---|---|
| local `qwen2.5-coder:7b` | 3/3 | **0.82 s** |
| local `qwen2.5-coder:14b` | 3/3 | 1.66 s |
| `auto/cheap` -> codestral-2508 | 3/3 | 4.12 s |
| local `devstral:24b` | 3/3 | 7.32 s |
| local `gpt-oss:20b` | 3/3 | 10.2 s |
| local `qwen3:14b` | **0/3** | 38.1 s |
| freellmapi `auto` | **2/3** | 37.6 s |
| local `gemma4:31b` | **0/3** | 100 s |

**Local is ~5x faster than the best cloud lane.** An earlier measurement said the
opposite; it was contending with a concurrent benchmark on the same GPU and
timing cold model loads. Do not re-derive that.

**Measured VRAM cliff on the 12 GB desktop: between 14.3 and 19.0 GB.**
`devstral:24b` (14.3 GB) works. `glm-4.7-flash` (19.0 GB) and `gemma4:31b`
(19.9 GB) both return HTTP 500. `gemma4:26b` (18.0 GB) untested, presumed dead.
`qwen3:14b` fails differently — `finish_reason=length` with **empty content**:
a reasoning model exhausting the token budget on internal thinking. Fixable by
raising max_tokens, not a bad model.

## The two real gaps in the orchestration story

1. **Nothing decomposes a request into a graph.** `src/planner.py:3` says it
   consumes "a graph document produced by a strong model or an operator."
   A human writes the graph JSON today.
2. **Nothing selects a model per node.** `src/executor.py:618-622` reads the
   model from `--usage-file` *after the fact*, for ledger honesty. It never
   chooses. Every node gets whatever Hermes is configured with.

Role assignment and skill matching DO exist (`src/capabilities.py`,
`src/capability_catalog.py`) — they are just uncommitted.

## Added this session

`src/roles_lifecycle.py` — 22 new roles + 21 new capabilities across 8 phases
(ideation, design, build, harden, ship, monetise, market, govern), taking the
roster from 7 to **29**. Additive: merges via `merged_roles()` / 
`merged_capabilities()`, mutates nothing. Validated: 0 roles cite an unknown
capability; **77 of 78 skill references resolve to installed skills**.

Three roles are marked `external=True` and must never auto-accept:
`payments_engineer`, `social_marketer`, `launch_producer`.

## Bug found in Codex's uncommitted work

`src/capabilities.py:107` — capability `media_generation` references skill
`remocn`, which does not exist. Intended target is almost certainly
`affaan-m-remotion-video-creation`, which is present but **archived only**.
That capability silently resolves to no skills today.

## Still blocking

- **34 files uncommitted** on `codex/freellm-router`, including all of
  `capabilities.py`, `capability_catalog.py`, `technology_radar.py`,
  `memory/brain.py`. 694 tests pass against the working tree only.
- **Desktop node registered but unroutable.** Node
  `openai-compatible-chat-f3a1556c…`, prefix `deskollama`, returns
  `No active credentials for provider`. Needs a connection with any dummy key
  added in the OmniRoute UI. The CLI cannot do it: `omniroute nodes add` has a
  **global `--base-url` flag that shadows the subcommand's own**, so the
  subcommand always reports it missing. Reproduced in Bash and PowerShell.

## Useful external feed found

`https://freeinference.dev/data/providers.json` — machine-readable, schema
validated, verified daily (2026-09-25 at time of writing). 33 providers, 107
free models, with RPM/TPM/RPD and daily token limits. **42.5 M tokens/day
aggregate.** This fills exactly the quota fields FreeLLMAPI's free snapshot
leaves blank, and is the right feed for the technology radar.

---

# SESSION 2026-09-25 (implementation) — Step 0 done, Step 1 spiked

## Step 0 COMPLETE — verified from a clean clone, not a working tree

All 34 files committed to `codex/freellm-router` in six coherent commits, then
`main` merged in (recovers `DECISIONS.md`). Head is `13b60a3`.

```
git clone --branch codex/freellm-router <worktree> /tmp/cleanclone
cd /tmp/cleanclone && python tests/run.py
694 passed in 241.8s, exit 0
```

143 files tracked. Every module that previously existed only as an untracked
file — `capabilities.py`, `capability_catalog.py`, `technology_radar.py`,
`memory/brain.py`, `roles_lifecycle.py`, `config/capability-adapters.json` —
is present in the clean clone. **The earlier risk is closed.**

## Also done

- **`remocn` fixed** → `remotion-video-creation` (the *active* skill, better
  than the archived `affaan-m-` copy named in the plan). **78/78 skill
  references now resolve to active skills**; 0 roles cite an unknown capability.
  Note: the plan said 79/79 — arithmetic slip, the real total is 78.
- **One source tree.** My duplicate `~/.tri-ai/capability-sources/` merged into
  Codex's `~/.tri-ai/capabilities/sources/` — no name collisions, now **20
  repos**. Provenance (`installed-skills.json`, fetch/install scripts) preserved
  under `capabilities/provenance/`. Duplicate directory removed.
- **Catalog rebuilt** (old kept as `catalog.json.bak-2026-09-25`).
  **10,219 → 10,333.** Reconciles exactly: active skills 837 → **943** (+106,
  the skills installed this session), source repositories 14 → **22** (+8).

## Step 1 spike — `hermes kanban decompose` WORKS, with three gaps

Ran on a throwaway board `triai-spike` (the real `default` board untouched):

```
hermes kanban --board triai-spike decompose t_e3501584 --json
{"ok": true, "reason": "decomposed into 6 children", "fanout": true, ...}
```

Six sensible children from one sentence: Design the app UI/UX · Set up user
accounts and authentication · Develop goal setting · Implement notification
scheduler · Create adherence logging · Integrate all components and test.

**The hard part — a sentence becoming a sensible breakdown — works.** What the
adapter must add:

1. **No dependency edges.** `parents: None` on *every* child. It returns a flat
   list, not a graph. "Integrate all components" does not depend on the
   components. Tri-AI's planner needs a DAG; topological order is meaningless
   without edges.
2. **No specialist routing.** `assignee: 'default'` on all six, despite the help
   text promising *"routed to specialist profiles by description."* Cause found
   — see the blocker below. It is vacuous with one profile, not broken.
3. **No skills, no verify commands.** `skills: []`; verify is expected to be
   absent, Hermes has no such concept.

`auxiliary.kanban_decomposer` confirmed present (`provider: auto`, `model: ''`,
`timeout: 180`) — so pointing the decomposer at a strong lane really is one
config line, as planned.

**Bonus finding:** `hermes kanban create` takes `--model` and `--provider`
**per-card overrides**. Hermes can already pin a model per task, so Step 3's
lane selector can set that instead of building new plumbing.

## BLOCKER for specialist routing: `hermes profile list` crashes

```
FileNotFoundError: [WinError 3] ... 'AppData\Local\hermes\skills\ask-matt'
```

**39 dangling symlinks** in `~/AppData/Local/hermes/skills/` all point into
`~/.agents/skills/`, which still exists but now holds only 4 entries — the rest
were removed at some point. One dangling link aborts the whole profile listing,
so profiles cannot be managed, so no specialist profiles exist, so `decompose`
assigns everything to `default`.

Repairability, measured: **31 of 39** have a copy in `~/.claude/skills` or
`~/.claude/skills-archive` and can be repointed. **8 have no copy anywhere**:
`code-review`, `design-an-interface`, `edit-article`, `obsidian-vault`, `qa`,
`request-refactor-plan`, `ubiquitous-language`, `writing-great-skills`.

Proposed fix (NOT yet applied — touches the operator's Hermes install):
repoint the 31, move the 8 to a quarantine directory rather than deleting them,
and record the mapping. Then create profiles matching the 29-role roster so
`decompose` can route for real.

## Specialist routing now WORKS — measured before and after

### The blocker, diagnosed correctly on the second attempt

The 39 dangling entries are **Windows directory junctions**, not symlinks.
`Path.is_symlink()` returns **False**, `lstat` reports a directory,
`os.readlink()` succeeds and returns a `\?\` path, and removal is `os.rmdir`
not `unlink`. Git Bash reports them as symlinks, which is why a first repair
script written against symlinks found **zero** where bash had found 39. Worth
remembering for anything that walks these directories.

### Repair applied — `scripts/repair-hermes-skill-links.py`

```
repointed 31 · removed (no copy) 8 · failed 0
dangling junctions remaining: 0
hermes profile list -> works
```

Nothing deleted. The 8 with no copy anywhere had their original target strings
written to `~/.tri-ai/ops/hermes-skill-link-repair.json` before removal;
`mklink /J` restores any of them. Names: `code-review`, `design-an-interface`,
`edit-article`, `obsidian-vault`, `qa`, `request-refactor-plan`,
`ubiquitous-language`, `writing-great-skills`.

### Profiles created — `scripts/sync-hermes-profiles.py`

29 Hermes profiles, one per role, described from the mission each role already
carries. `hermes profile create --description` states the text is *"used by the
kanban decomposer to route tasks based on role instead of profile name alone"*,
so the role missions feed it directly. Lean: `--no-alias`, `--no-skills` —
Tri-AI supplies skills through the capability contract.

### The result, same request both times

| | before | after |
|---|---|---|
| children | 6 | 6 |
| distinct assignees | **1** (`default`) | **5** |

After: `designer` (UI/UX) · `backend_builder` (infrastructure, scheduler) ·
`frontend_builder` · **`payments_engineer` ("paid subscription tier")** ·
`accessibility_auditor`.

It routed monetisation work to a role that did not exist this morning, purely
from the description. **The role roster is doing real work.**

### Still missing — the adapter's remaining job

`parents` is unset on every child. The decomposer returns a **list, not a
graph**. Dependency edges, verify commands and stack profiles are what
`src/decomposer.py` must add on top.

Head: `11d60ef`. Spike board `triai-spike` is throwaway; the `default` board
was never touched.

## Step 1 COMPLETE — a sentence now becomes a validated graph

`src/decomposer.py` + `tests/test_decomposer.py` (14 tests). Head `a53beef`.
**708 tests pass, exit 0** (694 baseline + 14).

### Measured end to end

```
python src/decomposer.py "Build an app that sends discipline notifications
  for workouts and diet" --board triai-spike --workspace-kind dir ...
8 nodes -> graph.json
planner.validate_graph(graph)  ->  ACCEPTED, 8 nodes topologically ordered
```

| # | node | phase | depends on |
|---|---|---|---|
| 1 | designer | design | — |
| 2 | ux_writer | design | — |
| 3-7 | backend_builder, data_engineer, frontend_builder, integrations_engineer, backend_builder | build | 1, 2 |
| 8 | payments_engineer | monetise | 3, 4, 5, 6, 7 |

Seven distinct specialists. Parallelism preserved inside the build phase.
Money last. Accepted by `validate_graph` **unmodified**.

### Design decision: edges from phase order, not semantics

A node depends on every node in the nearest earlier *occupied* phase. It cannot
cycle (edges only point backwards through a fixed sequence), a wrong edge is a
wrong phase mapping rather than a model's opinion, and it over-constrains
rather than under-constrains — costing wall-clock, never correctness. Semantic
edges would be tighter but need a second model call and fail in the expensive
direction: a missed edge runs a task before its input exists.

### Integration fix: resolve_contract only knew seven roles

`capabilities.resolve_contract` checked `role not in ROLES` — the original
seven — so a node naming `payments_engineer` was **refused outright**. It now
merges `roles_lifecycle` lazily (module-scope import would be circular) and
caches. Without that module the original seven behave exactly as before.
29 roles, 39 capabilities now resolvable; the role/capability boundary still
holds (`payments_engineer` still cannot take `web_research`).

### Three defects the process caught, not the reading

1. **TDD violation, self-inflicted.** Wrote `decomposer.py` before its tests,
   deleted it, rewrote test-first. The rewrite is better: `build_graph` is a
   pure function over a `Decomposition`, so 12 of 14 tests need no subprocess.
2. **AST boundary refused the import.** `capabilities.py` importing an
   unaudited `roles_lifecycle.py`. Correct refusal — both it and `decomposer`
   shape what the agent is told and what the gate accepts, so both are now
   declared in `CLOSURE_MODULES`.
3. **Process containment.** `_hermes` called `subprocess.run` directly.
   Process creation is confined to `executor.spawn_contained` precisely so a
   timed-out child cannot leave a detached grandchild writing to the
   workspace. Now routed through the gateway, with `terminate_tree()` on
   timeout.

### Bug found only by running it

`hermes kanban create --json` **pretty-prints** across many lines while
`kanban decompose --json` prints one object on one line. A line-at-a-time
reader handles the second and silently fails the first — the first live run
died on exactly that. `_last_json_object` now scans for the last *top-level*
object with `raw_decode`, skipping nested braces. A naive backwards scan finds
the innermost nested object instead, which the test now pins.

### Still open for Step 2

Every node carries `verify_generic: true`. The gate is real but stack-blind —
it only proves the workspace changed. Per-node commands from
`{django,laravel,springboot,quarkus}-verification` are `stack_profile`'s job.

## Defects closed (Steps 6 and 7) — head `3552bec`, 708 tests green

### deskollama now routes — and it did NOT need the UI

I said twice that the OmniRoute UI was required. The **CLI** genuinely cannot do
it (a global `--base-url` shadows the subcommand's own), but the REST API can:

```
POST /api/providers  {provider: <nodeId>, name, authType: apikey, apiKey, isActive}
-> connection 64b86a80-9432-4f0a-8bc5-1f324771a505
POST /v1/chat/completions  model=deskollama/qwen2.5-coder:7b  -> "OK", 36 tokens
```

The 0.82 s local lane is live through OmniRoute.

### SECURITY: the router was on every interface, not loopback

**I was wrong about this and stated it repeatedly.** I read the *client* config
(`baseURL: http://127.0.0.1:3001`) and called it the bind address.

`install-freellmapi.ps1` wrote `HOST_BIND=127.0.0.1` and printed "Created a
localhost-only FreeLLMAPI configuration". Upstream docs, verbatim: `HOST_BIND`
is *"Docker only: which host interface the container's port is published on"*
and is *"distinct from HOST"*. A native node process ignores it, so the server
sat on its default `::`.

| | before | after |
|---|---|---|
| listeners | `0.0.0.0:3001`, `[::]:3001` | **`127.0.0.1:3001`** |
| tailnet TCP connect | **succeeded** | refused |
| models on loopback | 31 available | 31 available |

Fixed by adding `HOST=127.0.0.1` to `~/.tri-ai/freellmapi/.env` and to the
install script, then restarting via the new `scripts/start-freellmapi.ps1`
(which refuses to start a second router on an occupied port).

**The desktop runs its own instance on 3001 and answered a tailnet connect
earlier. It needs the same change.**

### Dependencies: 13 -> 0

`npm audit fix` without `--force` cleared all 6 high and the 1 low. Net change
to the FreeLLMAPI tree is **`package-lock.json` only** — all three
`package.json` files are back at their committed state.

I briefly bumped vitest 3 -> 5 to clear the last 3 moderates, then reverted it.
Two reasons, both measured:

- the remaining 3 are **test-only devDependencies** (path traversal in a test
  runner), not in the serving path;
- after `npm audit fix` the lockfile alone reports **0 vulnerabilities**, so the
  bump bought nothing.

Two failures investigated and both cleared as **not caused by the change**:

- `cli/src/tools.test.ts`, 29 snapshot failures. The test hardcodes
  `homeDir: '/home/tester'` and the committed snapshots carry **27 POSIX
  paths**; production joins with the platform separator, so Windows yields
  `\home\tester\...`. These cannot pass on Windows at any vitest version.
- `client` build, 31 `TS2591` errors — `tsconfig.app.json` sets
  `types: ["vite/client"]` while `include: ["src"]` pulls in `*.test.ts` that
  import `node:fs`. **Reproduced with the original `package.json`**, so
  pre-existing. `client/dist` dates from 2026-09-21.
- The one server-workspace failure seen in the first run was flaky: a clean run
  gives **283 files, 3,378 tests, 0 failures**.

### Registry accuracy

OmniRoute added (it was absent despite being the primary router: 2,545 models,
38 aliases, 8 providers). The `freellmapi` row no longer reads
`service-running-provider-unconfigured`. 32 adapters.

## Step 2 COMPLETE — `stack_profile.py`, head `22904ca`, 726 tests green

Every node used to carry `verify_generic: true` — a real gate but a weak one.
It proves the agent changed something; it cannot tell a working Django app from
one whose models no longer match its migrations.

### Detection reads evidence, never the request

`manage.py` **plus** a Django dependency is Django; `manage.py` alone is not,
because plenty of projects have one. Specific beats general (django before
python, first match wins) and the evidence is recorded on the result, so a
wrong gate is traceable to the file that caused it.

Checked against four real repositories:

| repo | detected | from | gate |
|---|---|---|---|
| freellmapi | node | package.json | `npm test --silent` |
| arditmishracom | node | package.json | `npm test --silent` |
| biostudio-backend-readiness | python | pyproject.toml, requirements.txt | `python -m pytest -q` |
| **tri-ai itself** | **python-runner** | tests/run.py | **`python tests/run.py`** |

### Two consumers, kept apart deliberately

The `-verification` skills are **instruction documents** — phases, prose,
examples — written to teach an *agent*. They are named per stack so the
capability brief hands the right one to the right specialist. The **gate
command is curated separately**, not parsed out of that prose: turning an
instruction document into a shell command by pattern-matching fails quietly,
and a gate that fails quietly is worse than no gate.

### Two judgement calls worth keeping

- **Django gates on `makemigrations --check`**, not only tests. A model change
  with no migration passes every test and breaks the deploy.
- **`python-runner`** is a last-resort detector for a project that ships its own
  runner and declares no dependencies. Tri-AI is one. `pytest` there would
  collect nothing and exit 0 — *green because it checked nothing*, the worst
  possible gate. **Tri-AI can now verify work on itself.**

An unrecognised workspace keeps `verify_generic: true` rather than being
quietly promoted by having passed through detection.

### Process note

The heredoc mangled `\n` escapes while adding tests — the exact trap recorded
in memory as `windows-heredoc-mangles-backslashes`. Use the Write/Edit tools
for anything containing a backslash. I hit it anyway; the note is right.

## Steps 3 and 4 COMPLETE — head `70c3022`, 764 tests green

### `lane_select.py` — three tiers, measured

Local is the floor, free is volume, subscription is an accelerator that may
vanish mid-run. A task declares the lowest tier it needs and never names a
subscription.

Live result on the 8-node graph: **6 of 8 nodes chose local `deskollama` lanes**,
`ux_writer` took `auto/best-free`, `payments_engineer` took `auto/best-coding`.

**A test caught the obvious version being wrong.** Ranking by pass rate alone
selected a lane that had failed **20 of 20** — it was the only candidate with
enough samples, so "best of a bad lot" read as "best". A measured lane must now
clear `MIN_PASS_RATE` before it can win. Evidence a lane never works is a
reason to avoid it.

Exhaustion is recorded, never retried into. Codex's own reset text is parsed;
an unparseable message still benches the lane on a cooldown. With every
subscription benched, a build still gets a local lane — the floor holds.

### `cartographer.py` — the renderer was the reviewer

**A lane is an agent, a column is time.** Three earlier designs were rejected
by archify, each correctly:

1. `variant` on a workflow node — does not exist there (`additionalProperties:
   false`; variant belongs to lanes and edges). Run state moved to `sublabel`.
2. **Lanes as phases, siblings stacked with `yOffset`.** A lane frame is 104px
   tall with a 30px title strip: it holds *one row*, stacking is unavailable.
   Lanes became agents and phases became columns.
3. **Every parent edge drawn** — phase-derived parents are a complete bipartite
   mesh (2 design × 5 build = 10 identical-meaning lines), flagged as ambiguous
   corridors. Now one edge per phase transition, labelled "5 in parallel". The
   full dependency set stays in the graph JSON, which is what the planner reads.

Labels clip at 20 chars (~6.8px each against a 150px box) and columns come from
a well-spaced set, because archify's centres are 132/80/130/70/125 apart —
columns 1-2 and 3-4 cannot both hold a readable node.

```
archify validate workflow --quality showcase -> ok: true, 0 errors, 0 warnings
archify deliver -> 807KB self-contained HTML, 0 diagnostics
```

A sublabel cannot hold a shell command; trying produced 213px against a 150px
node. The gate stays in the graph JSON. My own test asserted otherwise and had
to be corrected, not the code.

### Tailscale SSH is not an option here

`tailscale set --ssh` on this machine: **"The Tailscale SSH server is not
supported on windows."** Both nodes are Windows, so the desktop cannot host it.
OpenSSH on port 22 is open; the host key fingerprints offered by
`100.67.149.86` are:

```
RSA     SHA256:lDKCHyCn1UAT9u/2hB6VFtzn/3PTEAhxJGvWBBzoSDM
ED25519 SHA256:kgpdpUCrWzgMWCXtlkEzUiXYp+RQdnJNpUBBllYUCNI
```

**Operator must verify these against the desktop before they are trusted.**
That is the entire point of host-key verification and is not mine to skip.

---

# CORRECTION 2026-09-25 — the desktop HAS been running Tri-AI

**Two claims earlier in this file are wrong. Do not act on them.**

## Wrong claim 1: "the always-on desktop has never executed a Tri-AI task"

I read only the laptop's local `~/.tri-ai/ledger.jsonl` (28 entries, 24 from
`Vivo-S14`) and generalised. **Each machine keeps its own ledger.** The
desktop's, read over SSH:

```
total: 66
  39  DESKTOP-JHQ7HJM     <- the majority of all work
  23  Vivo-S14
   3  test-host
   1  laptop
first: 2026-09-10   last: 2026-09-24
```

**The desktop has run more Tri-AI work than the laptop.** Step 0b as planned —
"relocate the runtime to the desktop" — is largely unnecessary.

## Wrong claim 2: "hermes MISSING on the desktop"

A PATH artifact. Windows SSH sessions get a minimal PATH. Hermes is installed
at `C:\Users\Ardit II\AppData\Local\hermes\hermes-agent`, same layout as the
laptop. Always check the install path, not `Get-Command`, over SSH.

## The real pass rate is worse than reported

Combined desktop ledger, 66 entries: **21 passed, 40 failed**, 2 skipped, 1
environment_backoff, 1 blocked, 1 quarantined. That is **34% of decided runs
passing**, not the 58% the laptop-only view suggested. Any portfolio claim must
use 66/21/40, not 28/14/10.

## Desktop state, measured over SSH

| | |
|---|---|
| OS | **Windows 10 Pro** (why RDP works; laptop is Home) |
| repo | `C:\Users\Ardit II\tri-ai`, branch `phase-2/worker-assign` @ `cd37c3a`, clean |
| python | 3.14.3 (same as laptop) |
| git / node / ollama / omniroute | present |
| hermes | present, not on the SSH PATH |
| hermes kanban boards | **none** |
| runtime | `~/.tri-ai/` with board.db, ledger.jsonl (66), runs/, logs/ |

## SSH access established

Host key verified by the operator against the desktop console:
`SHA256:kgpdpUCrWzgMWCXtlkEzUiXYp+RQdnJNpUBBllYUCNI` — matched, pinned in
`~/.ssh/known_hosts`. Key `~/.ssh/triai_desktop` was already authorised from an
earlier session. `ssh "Ardit II@100.67.149.86"` returns exit 0.

Note the username contains a space (`Ardit II`) — quote every path.

## Blocked on an operator decision: the GitHub remote is PUBLIC

`github.com/Ardit-Mishra/tri-ai` is `"isPrivate": false`. Both machines share
it. My branch `codex/freellm-router` @ `70c3022` is **not** on the remote.

Scanned before proposing anything: my commits introduce **no** IPs, hostnames,
usernames or secrets, and `scripts/add-desktop-node.ps1` (which does contain a
tailnet IP) was never committed. `.planning/` is untracked, so this file would
not be published either.

Two ways to move the code to the desktop:

- **`git bundle` over SSH** — private, no GitHub involvement. Default choice.
- **push the branch** — convenient, but publishes to a public repo.

Pre-existing note, not caused by this session: the README claims the repo
"contains no hostnames, IP addresses, tokens or keys", but
`scripts/run_dashboard.ps1` and `src/dashboard/jarvis_web.py` already carry
real tailnet addresses.

## Desktop audit (proper one) — the two machines have DIVERGED, not drifted

Pushed `codex/freellm-router` @ `70c3022` to GitHub (operator approved; scanned
first — no IPs, hostnames, usernames or secrets in the diff).

### The desktop is running an OLD Tri-AI, and its daemons are live

`phase-2/worker-assign @ cd37c3a`, clean tree, **daemons running** (supervisor
5160, telegram 14016, worker 20024). **None** of the capability/orchestration
modules exist there:

```
NO  capabilities.py  capability_catalog.py  technology_radar.py
NO  roles_lifecycle.py  decomposer.py  stack_profile.py
NO  lane_select.py  cartographer.py  memory/brain.py
yes planner.py  worker.py  board.py      <- old core only
```

So the 39 desktop ledger entries came from the **pre-Codex engine**: no
capability contracts, no roles, no brain.

### Divergence, measured

```
commits mine has, desktop lacks : 99   (24 genuinely unique by message)
commits desktop has, mine lacks : 120  (45 genuinely unique by message)
```

Many share a message with a different SHA — a rebase/cherry-pick split, the
same work in two histories. **A checkout would destroy real work.**

Desktop-only changes touch **48 tests, 36 src, 38 `.planning`**, plus
CLAUDE.md, README.md, .gitignore. A trial merge conflicts in `.gitignore`,
`config/routing-probe.example.json` and `src/board.py`. This needs a real
merge, not a fast-forward.

### What only the desktop has: the entire planning tree

`.planning/` — `PROJECT.md`, `REQUIREMENTS.md`, `ROADMAP.md`,
`PORTFOLIO_STRATEGY.md`, `AUTONOMOUS-RUNBOOK.md`, its own `STATE.md`, and
`phases/phase-1..5` including **`phase-4.5-telegram-transport-plan.md`**.

CLAUDE.md calls `.planning/` "the project's durable memory". My branch has none
of it. **Neither branch is "the proper one" — they are two halves.**

## Why Telegram behaves the way the operator hates — it is by design

From `phase-4.5-telegram-transport-plan.md`, verbatim:

- *"Make /status, /task, /logs reachable from the operator's phone ... without
  ... adding any board mutation"*
- *"The local read surface stays pure: no ... board mutation"*
- *"service installation, auto-start, and **Telegram write commands remain out
  of scope**"*
- *"A transport/API/configuration failure **hard-stops the daemon**; it is not
  retried invisibly."*

**Telegram was built as a read-only status surface with three commands.** The
task intake (`_pending_run`) was bolted on later. Every complaint follows:

| complaint | cause |
|---|---|
| "it's not a chatbot" | correct — never was one |
| "just spits out stuff" | it is a status reporter, answering on demand |
| "doesn't tell me what's happening" | no proactive progress by design |
| "no storage for the artifacts" | a read surface was never meant to deliver files |
| daemon kept dying | the hard-stop rule, working as written |

Codex's continuous-retry change (network retries forever, auth fails fast) is
therefore a **deliberate revision of this plan**, not merely a bug fix. It is
committed.

## The 40 failures are NOT Telegram

27 of 40 are titled `Telegram: sandbox`, so Telegram was the intake path. The
reasons are not transport:

```
22  verify: this run changed nothing in the workspace - no deliverable
 4  declared artifact check failed: missing 'index.html'; 'style.css'
 4  no image, svg or background-image anywhere
 2  missing 'devstral-proof.html'
 ~4 promised but not visible on the page: '500 g', 'Uttar Pradesh', ...
```

The agent produced nothing and the gate caught it. **Three separate problems:**
transport (fixed), UX (unfixed, real), and agents producing nothing (22 of 40,
the largest failure mode, unrelated to Telegram).

---

# 2026-09-28 - The Cortex session boundary

## A reverse proxy makes every caller look local

`tailscale serve` proxies the tailnet to `http://127.0.0.1:3026`, so every
remote visitor arrives at the dashboard with `client_address == 127.0.0.1`.
The first sealed-private boundary (`b8df28d`) used the rule "loopback is the
operator", which therefore authorized the whole tailnet. **1,260 tests were
green and the live deployment was exactly as open as before**: this is a fact
about the topology, not the code, so no unit test could have caught it.

It was found by reading `tailscale serve status` before restarting the
server - not by testing. The same trap applies to Cloudflare Tunnel, which is
the next thing planned in front of this.

The fix is not a better guess at who is behind the proxy; a local process can
forge anything a proxy sends. `kaya_web.session_config(env)` returns the token
and the loopback decision **together**, so "token configured but loopback
still trusted" cannot be expressed. Cost, accepted deliberately: the
operator's own browser signs in too, and local and remote become one path.

## The boundary needed a door

A phone cannot send `X-Kaya-Token` and nothing set the cookie, so sealing
every route made the dashboard unreachable rather than private. Added
`GET/POST /login`, `POST /logout`, and a redirect from `/` when sealed.
Cookie is `HttpOnly; SameSite=Lax`, plus `Secure` when `X-Forwarded-Proto`
says TLS - Tailscale terminates TLS and forwards in clear, so without that
the key travels back in the open. Confirmed empirically: Serve does send
that header.

`LoginThrottle` bounds guessing globally. Per-address limits are useless
(everyone is 127.0.0.1 behind the proxy) and delaying a reply throttles
nothing on a `ThreadingHTTPServer` (parallel guesses overlap their delays),
so it refuses immediately without examining the key. Four free attempts, then
doubling waits capped at 5 minutes. That is what makes a 12-character
phone-typeable key safe.

Commits: `f279dc9`, `966cf83`, `bb10e08` in `~/worktrees/tri-ai-public-release`
on `public-main`. **Unpushed** - that branch indexes real Drive and laptop
paths and `origin` is public.

Verification (real tailnet URL, not tests):

```
/                 no session -> 303 /login    with session -> dashboard
/api/snapshot     no session -> sealed:true, 0 items, 0 sources
                  with session -> 796,684 items, 11 sources
/api/file-graph/scene  no session -> 404       with session -> 200
6 wrong guesses   -> 401 x5 then 429; the correct key is refused too
                     while the gate is shut, with Retry-After
```

## The public repo leaked through its own scrub test

`tests/test_public_release_boundary.py` held a list of the real machine name,
home directory and both Tailnet addresses so it could search for them, and
skipped `tests/` because that list had to live somewhere - so it never
inspected the file doing the leaking. Dropping the exemption exposed three
more sites, including `C:\Users\<user>\worktrees\private-client-work` in the
beacon fixtures that stand in for "secrets that must not leak".

Now matched by shape: hostname as a pattern, the Tailscale range as a rule,
home directory **derived from `getpass.getuser()`** so it is never written
down. No allowlist - two drafts tripped their own check (an example address,
then a comment naming the range). Failures name the file and the kind of
finding, never the value.

Fixed in `52fc072`, pushed to **public** `origin/main` with the operator's
approval. History still contains the old strings; a rewrite was judged
disproportionate for non-routable addresses and an already-public username.

## Still open

- **The Cortex shows a desktop that is not there.** `remote-desktop` is
  `unavailable`, 0 items - "file index agent is not authorized yet". The
  796,684 figure is Drive + laptop only, while the UI lists the desktop as a
  source. Widest gap between what it looks like and what it is.
- Runtime still on `vivo-s14` (the laptop). The desktop runs the models and
  has never executed a task.
- 32 commits unpushed on `public-main`.
- `model_aliases:` still unproven, so `lane_select` stays unwired.

> **Corrected 2026-09-28 after an SSH survey of the desktop.** The claims that the desktop has never run a task, that it contributes 0 items, and that no SSH channel exists are all FALSE. The desktop holds 83 ledger entries (~56 its own), reports 486,925 nodes in the live Cortex, and runs Tri-AI as scheduled tasks serving a dashboard on :8081. Each machine has its own board, ledger and memory; the laptop's ledger was mistaken for the system's. Full correction: the CORRECTION section of docs/HANDOFF_2026-09-28.md in the primary checkout.
