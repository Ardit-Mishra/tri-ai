# Tri-AI — state, 2026-09-29

This file is **tracked on `trunk`**, so it travels with the code. It used
to be untracked, which is how 178 KB of working notes came to exist on
exactly one laptop.

## One line, two repositories

`trunk` is the only line of development. Both machines are on it.

| | |
|---|---|
| `Ardit-Mishra/tri-ai` | **public** — the sealed demo, nothing else |
| `Ardit-Mishra/tri-ai-private` | **private** — real history, Cortex indexes, operator notes |

`RELEASE_SCOPE` declares a branch's audience; `scripts/release_guard.py`
enforces it at push time from `core.hooksPath` outside the repo, so it
applies on every branch. Fails closed in all three directions: unknown
scope reads private, unknown remote reads public, no declared private
remote means nothing private moves.

The three earlier lines are reconciled. The two `codex/freellm-router`
branches shared 273 commits and no files in common, so they merged
(`b8089c8`). `private/cortex` had an **unrelated history** — the scrubbed
release began a fresh root — but was a strict superset, so it became
`trunk`. Old branches are kept on the private remote as history.

## What runs, and what keeps it running

| | desktop `DESKTOP-JHQ7HJM` | laptop `Vivo-S14` |
|---|---|---|
| worker, supervisor, Telegram | scheduled task, boot + logon + 15 min | — |
| dashboard | `:8081`, scheduled task | Cortex `:3026`, scheduled task |
| OmniRoute | `:20129` | `:20128` |
| FreeLLMAPI | `:3001` | `:3001` |

Every service is loopback-only. The Cortex reaches a phone through
`tailscale serve`, behind a session token.

**A long-running daemon does not reload Python.** After changing `src/`,
stop the python processes and `Start-ScheduledTask`. A full evening's
fixes appeared not to work because of this.

## Cortex

870,777 indexed items across 9 reporting sources — desktop 486,925,
laptop 307,151, Obsidian 73,802, Drive 2,606, OmniRoute 51, Claude and
Codex 227, GitHub 10, Vercel and Render 3, Ollama 2. Sealed without a
session, verified over the real Tailnet URL.

## The build that proved it end to end

A stalled 17-task e-commerce graph on the desktop went from 3 done to
**15 done**, unattended, producing real files (`cart.html` 12,485 bytes,
`checkout.html` 14,467 bytes, backends, admin views) each past a
verifier's exit code.

Four defects had to be fixed to get there, and none were visible to any
test:

1. **A space in an account name.** `Ardit II` made the prompt's unquoted
   `cd` split in bash; every command failed and the agent stopped to ask a
   question nobody could answer. Fixed by `executor.shell_path()`.
2. **A stale daemon** running the pre-fix module from memory.
3. **`git diff --quiet` cannot see new files**, so any task whose job was
   to create something failed and had its work stashed away. Replaced with
   `python verify.py`, which attributes deliverables to *this run*.
4. **A cancelled parent strands its children silently.** `standstill.py`
   now reports it, and repeats while it persists.

## Open, needing Ardit rather than an agent

- **FreeLLMAPI has no API key.** Create one at `http://127.0.0.1:3001`,
  set `FREELLMAPI_API_KEY`. Not in the repo, a task prompt, or a chat
  message — that is the project's own rule.
- **Two tasks wait on a cancelled parent** and can never run. Cancel them
  or recreate the parent; it is a product decision, and they are now
  visible rather than silently parked.
- **Phone** is a declared Cortex source with no collector, because
  nothing runs on the phone yet.

## Deliberately not wired

`lane_select` is complete, tested, and off the execution path on purpose.
Measured selection compares lanes, and all 110 ledger runs used a single
lane, so there is nothing to compare. `model_routes` picks models today.
A test names the condition that reverses this: two or more lanes with
`MIN_SAMPLES` decided, non-environment runs for one role.

Environment failures no longer count against a lane — a lane is not bad
because the harness was broken.

## 2026-09-30 — the guidance was never in the prompt

The system had 752 skills installed and a designer that produced the
machine-made look anyway. Both halves of that turned out to be one
mechanism.

**A path is not guidance.** `capabilities.brief_block` wrote

    Skill instructions: <root>/frontend-design/SKILL.md

and trusted the agent to open it. Claude and Codex usually do. They are
not what this system runs on — the standing constraint is `$0 marginal
cost`, so the lanes carrying the volume are local Ollama models and free
API models behind OmniRoute and FreeLLMAPI, and a 7B model handed a
filesystem path mid-build does not stop to read it.

The evidence is `runs/t_4b46ec92/135/rejected/BRIEF.md` on the desktop.
Display face **Space Grotesk**, body face **JetBrains Mono**, background
**#09090b**, accent **#38bdf8** — the machine-made look almost exactly,
written confidently, while the one skill whose job is to forbid it sat in
the prompt as a path.

`brief_block` now inlines the skill text itself, frontmatter stripped,
under one shared byte budget (`DEFAULT_SKILL_BUDGET`, 24,000). Role-named
capabilities spend it first because they are a considered choice; catalog
keyword matches spend what is left. A skill that does not fit becomes a
reference carrying its own `description`, so the model can judge whether
opening it is worth a turn. A skill that does not exist is no longer
announced at all — a dead path reads as guidance the agent ignored.

Measured: designer 25,206 chars, 3 inlined, 6 deferred. Builder, which
names no capability and previously got twelve bare paths, now spends the
whole budget on its catalog matches.

**The gate asked "did you choose?" when the question is "did you choose
this?"** `_has_chosen_type` rejects the framework default stack, which
catches an agent that decided nothing. It said nothing about an agent
that decides the same thing every time — Space Grotesk is not in
`DEFAULT_FONT_NAMES`, so run 135 passed that check comfortably.

`taste.OVERUSED_FACES` names seventeen faces that are a decision, but
always the same decision. `reads_as_machine_made()` fires only when
*every* non-generic face on the page comes off that list: one of them
beside a face with a voice is an ordinary well-made page, and a gate that
condemned Inter outright would spend its credibility on an argument it
cannot win. `Standard.reject_default_pairing` switches it off for a task
whose house style genuinely is one of these.

The brief now names them and asks for one line on **why these two and not
the obvious pair**. Naming clichés without demanding a justification just
moves the agent to the next default.

### Desktop, brought level with the laptop

`.claude/skills-archive` 0 → **8,730**, `.claude/agents` 0 → **36**,
`.claude/commands/gsd` 0 → **67**, `.claude/gsd` → 14 trees. Text only:
43,338 files, 138.6 MB packed, 4,068 binaries and checkouts skipped.
Blocked for a day by a full C: drive; the drive now has ~31 GB.

### What the full disk had already broken

Found in the desktop logs, all predating the cleanup and none of it
self-healing, because a running Python process does not notice a disk
being emptied under it:

- `worker.log` — `learning pass failed: OperationalError: database or
  disk is full`, repeating.
- Nothing listening on **20128**; the `OmniRoute Router` task last exited
  **1**. Started by hand it comes up fine, so this is the boot path, not
  the binary.
- Nothing listening on **3026** although `Tri-AI Dashboard` reported
  *Running*.
- `freellmapi.stdout.log` — `[Health] Checking 0 keys...` on every pass.

The last one is not a disk problem and not an agent's to fix: the free
API lane serves nothing until a key exists.

### The alphabet was deciding what a specialist learned

Caught by measuring the deploy rather than trusting it. The first version
of the inlining spent its budget in `sorted(contract.capabilities)` order,
because `RoleSpec.allowed` is a frozenset and carries none. On the desktop,
the moment the catalog came back, a designer's brief held
`codebase-memory`, `marketing-competitor-profiling`,
`web-design-guidelines` and one keyword match — and neither
`frontend-design` nor `taste-skill`. `codebase_memory` sorts before
`frontend_engineering` and `taste`, and it is large.

`RoleSpec.emphasis` now names what defines each role and the budget is
spent in that order; every capability is still announced, in the
contract's own order, so scope is unchanged. Two further facts fell out
of fixing it:

- `taste-skill` is 21,366 bytes and ate 89% of a 24,000 budget on its
  own, so `frontend-design` still did not fit. `MAX_SHARE_PER_SKILL = 0.6`
  defers any skill that would take more than its share — deferring beats
  truncating, because half a rule reads as a whole one — and
  `DEFAULT_SKILL_BUDGET` rose to 40,000 to hold the designer's real set.
- A task that names capabilities explicitly has its order preserved by
  `resolve_contract`, so the operator's order is the budget order. Only
  the silent default sorts, and that path now goes through `emphasis`.

Measured on the desktop, the user's own blocked portfolio task
(`t_4b46ec92`, role builder, capabilities named explicitly):
41,789 chars carrying `taste-skill`, `design-motion-principles`,
`frontend-design` and `web-design-guidelines` as text.

### A skill that contradicts the gate

`~/.claude/skills/taste-skill/SKILL.md` bans Inter under an **ANTI-SLOP**
heading (lines 40, 108) and then mandates `Geist`, `Outfit`,
`Cabinet Grotesk`, `Satoshi`, `Geist Mono` and `JetBrains Mono` instead
(lines 40, 41). Every one is on `OVERUSED_FACES`, and `Satoshi +
JetBrains Mono` is one step from what run 135 shipped.

So the agent was not ignoring its guidance. Where it followed the
guidance, the guidance named a fixed shortlist — and a shortlist applied
to every subject is a default however it is headed. The taste brief is
appended last and now says so explicitly; without that the gate would
reject work for obeying the prompt. The skill file itself is the
operator's and was left alone.

### Desktop state after this session

- `tri-ai` at the commit above, clean tree.
- Capability catalog rebuilt: **10,886 resources** (9,343
  archived-reference, 1,522 active), 9.77 MB. It was absent, and the
  Cortex read "0 active resources available to the planner."
- Daemons restarted after the disk was freed; supervisor, worker and
  telegram all up.
- Dashboard serves `127.0.0.1:8081`, proxied by `tailscale serve` to
  `https://desktop-jhq7hjm.tailc4ef4b.ts.net`. Port 3026 is the laptop's;
  checking it on the desktop reported a false outage twice in one session.
- OmniRoute serves **20129**, not 20128, and `hermes/config.yaml` already
  points there with `devstral:24b` and `qwen2.5-coder:14b` beneath it as
  fallbacks. `ensure_routers.ps1` matches it by process for this reason.
  A bare port probe of 20128 is not a health check.

## 2026-09-30 — pressure-testing the Cortex

Measured in the live console rather than looked at. What the measurements
found, and what was done:

**Two layouts for one markup.** The stylesheet declares a grid
(`.cortex-theater { display:grid; grid-template-rows:... }`) and then, 90
lines later, a "neural observatory" that re-declares the same selectors
as a full-viewport stack. The second wins. Any edit to the first is dead
on arrival — the first attempt at capping the graph resolved to
`minmax(480px,558px)` while the element was `display:block; height:100dvh`.
Corrections are now appended last, with a comment saying why.

**The header was painted on top of the canvas.** `header` was
`position:absolute; z-index:20` over a `1377x900` full-bleed canvas at
`y=0`, and `.core-topline` carried a 91px padding whose only job was to
dodge it. Four text layers floated on the live node field. The document
ran to 2784px, so 68% of the console sat below a field of moving dots.

**The lane tabs worked; I twice reported they did not.** The first check
used `Object.keys(el).filter(k=>k.startsWith('on'))`, which cannot see an
`addEventListener` handler. The second clicked a `ref` captured at a
different viewport. Both readings were wrong and the listener is on line
668. The real faults were that the tabs were 31px (below the 44px touch
minimum), sat mid-canvas, and said nothing about what distinguished them.

**Evidence was attributed to the wrong lane.** `renderModelLanes` built
one global list and rendered it inside the rail headed CLAUDE LANE. The
rows were byte-identical across all three tabs — so the console asserted
Claude had run `devstral:24b`, an Ollama tag that exists only on the
local box. That panel is the one an operator uses to answer "am I
burning a subscription or running free", and it was answering with
another lane's evidence. `laneForModel()` now classifies each route and
`renderAgentLens` redraws the rows with the lane.

**The headline was an inventory.** While idle the largest type on the
page — 35px — read "1522 active resources available to the planner. 9343
archived references remain searchable", while the task it had just
finished sat beneath it at 10px. Swapped.

**Rails overflowed once the theater became a band.** They are absolutely
positioned with `top:104px; bottom:72px`, sized against a 100dvh
container; in a 400-640px band their boxes were 272px and their content
was not. Measured: the boxes do not intersect, so the apparent collision
was an overflow. They now scroll inside the band, and return to flow
below 700px.

**Verified after the change**, at 375 / 1024 / 1440:
no horizontal overflow at any width; zero overlapping pairs among
header, core, both rails, the run panel, the readout, the metrics and
the tab band at 375 and 1440; at 1024 the only pairs are the rails over
the canvas, which is the observatory's intended layering. Core height
900 -> 504. Document 2784 -> 2474.

`tests/test_lane_attribution.py` executes the shipped classifier under
node rather than substring-matching it. Both mutations — classifier
always returns `claude`, rows never redrawn — are caught.

### A deploy trap worth remembering

Two `-m dashboard.kaya_web` processes were alive at once. The first held
8081 and kept serving the template it had imported before the deploy; the
second could not bind and idled. `Stop-ScheduledTask` ends the task, not
the python child, so restarting produced the spare rather than replacing
the original. Three separate "the fix did not land" rounds came from
this. Killing every process whose command line matches the module, then
starting one, is the only reliable restart.

Also: probing `http://127.0.0.1:8081/` from the desktop returns the
**login page** (1,437 bytes), not the board. A deploy check that greps
that response for board markup will report failure whatever is deployed.

## 2026-09-30 — the lane you run on is now a thing you choose

`model_routes` parses an operator registry, `board.py:1407` writes
`model_override`/`provider_override` from a resolved route, and
`TelegramControl` accepts a `model_routes_config`. All three were built
and tested, and **nothing supplied the config** — no daemon declared a
flag, so `self._model_routing` was None on every run. The laptop has
carried `~/.tri-ai/model-routes.json` since 2026-09-26 and the running
system never read a byte of it; the desktop had no such file.

Fifth instance of written, tested, called by nothing. Now wired:

    daemon_supervisor --model-routes -> telegram_daemon --model-routes
      -> load_model_routes()   validates at start, fails closed
      -> TelegramControl(model_routes_config=...)

Verified on the running desktop, which is the only proof that counts:

    telegram child running, --model-routes present: True
      C:\Users\Ardit II\.tri-ai\model-routes.json

### The registry, and why each line is there

Every admission cites `lane-bench.json` or a ledger run; a test asserts
no entry is declared without evidence.

| admitted | evidence |
|---|---|
| `qwen2.5-coder:14b` | 3/3, 1.66s, 27.6 tok/s, 128k ctx for the 40k brief |
| `qwen2.5-coder:7b` | 3/3, 0.82s, 41.6 tok/s — fastest correct lane |
| `devstral:24b` | 3/3, and one completed ledger run (proven through Hermes) |
| `auto/best-coding` | 3/3, 4.21s, codestral-2508, **29 ledger runs** |
| `auto/best-free` | 3/3, 0.33s, Llama-3.2-3B — fast but 3B |

Refused: `qwen3:14b`, `gemma4:31b` (0/3); `freellmapi auto` (2/3 — held
on accuracy, not cost). Not admitted: both subscription routes, per the
operator's decision that nothing escalates until a build completes on
the free tier alone.

Default `local-qwen-coder-14b`. Roles: builder/designer/reviewer →
14b, operator → 7b, researcher/lead/product_strategist →
`auto/best-coding`. Every one of those is free.

### What the subscription actually is

Not two subscriptions. `hermes auth.json` holds a credential pool of
`copilot` and `openrouter`, and OmniRoute exposes **112 models** over
three providers on those credentials:

- **`gh`** (Copilot): Claude Fable 5, Opus 5 / 4.8 / 4.7 / 4.6 / 4.5,
  Sonnet 5 / 4.6 / 4.5, Haiku 4.5; GPT-5.6 Sol/Terra/Luna, 5.5, 5.4,
  5.4 mini/nano, 5.3-Codex, 4o; Gemini 3.1 Pro and 3.x Flash; Grok 4.6.
- **`groq`**: Llama 4 Scout, Llama 3.3 70B, GPT-OSS 120B/20B, Qwen3 32B.
- **`nvidia`**: GLM 5.2, MiniMax M2.7, Mistral Large 3 675B,
  **Devstral 2 123B**, Qwen3.5-397B-A17B.

So the Codex CLI not being installed on the desktop does not block
anything — `GPT-5.3-Codex` is routable through OmniRoute. And `groq` and
`nvidia` are two free lanes that were never counted, carrying models far
larger than a 3060 can hold.

### Two blockers that need the operator

- **Exact model IDs are unavailable to an agent.** `omniroute models
  --json` is accepted and ignored by v3.8.50 — it prints the table
  regardless — and `/v1/models` on 20129 returns 401 without the API
  key. The table's "Model ID" column shows display names
  (`Claude Opus 5`), which may not be the literal API string. Resolve by
  setting `OMNIROUTE_API_KEY` from the OmniRoute UI. Probing by firing a
  completion was not done: it would spend a metered credit.
- **`source_collectors.omniroute_catalog` is dead.** It runs
  `omniroute models --output json`, which is not a valid flag, falls back
  to HTTP, and gets 401. This is why the desktop Cortex reports **1
  source cluster** where the laptop reports 9. Parsing the table needs no
  credential and would fix it.

## 2026-09-30 — a catalog is an advertisement

The operator said plainly: *"I feel like we have a lot of models available,
but I don't think we particularly have access to them."* Correct, and the
probe found two separate failures that no catalog listing could reveal.

**`auto/*` serves whatever it likes.** Asked for `auto/best-coding`,
the gateway returned HTTP 200 in 31.1s and reported served model
`google/diffusiongemma-26b-a4b-it` — a diffusion model — whose reply
ignored a two-word instruction. An `auto/` alias resolves downstream at
request time, so it can never name a tested identity, which is the one
thing the route registry exists to record.

The consequence reaches backwards: **29 ledger runs recorded
`auto/best-coding`**, meaning they recorded the alias and not what served
them. That history says nothing about any model, and some of the output
quality complaints may simply be this. `taste.py` was rejecting work that
a diffusion model may have produced.

Both `auto/` routes withdrawn. A test now refuses to admit any route whose
model begins `auto/`, permanently.

**All 110 `github/*` models return HTTP 466:**

    This version of the Copilot CLI is no longer supported.
    Please upgrade to the latest version.

A version gate, not an entitlement wall — the Copilot subscription is
fine. `@github/copilot` on the laptop was 1.0.83, upgraded to 1.0.90.
That alone did not clear it; OmniRoute holds the binary, so it was
restarted too (up after 100s). OmniRoute itself is one patch behind,
3.8.50 against 3.8.51, which is the next lever.

**`cxa/*` returns HTTP 503:** `Codex app-server transport is not
configured (missing url or token)`. Installing the CLI is necessary and
not sufficient — the transport is an OmniRoute setting.

### What the probe technique was, and why it matters

Each probe asks for one token and records the model named in the
**response**, not the request. A gateway that silently downgrades returns
HTTP 200 either way; `served` is the only field that distinguishes a
working lane from a substituted one. Without it, `auto/best-coding` looks
like a healthy route with 29 successful runs.

Probes are `max_tokens: 4` with a two-word prompt, because a
premium-request-metered provider charges per request and a sweep should
cost the smallest unit that exists. `openrouter` (1,215 models) and
`aihorde` (207) are excluded from the default sweep: neither is a lane
this system would route a build to.

### Verified state of every lane, 2026-09-30

| lane | result |
|---|---|
| `deskollama/qwen2.5-coder:14b` | **ok**, 22.2s, served as itself, replied correctly |
| `auto/best-coding` | 200, served a diffusion model — withdrawn |
| `auto/best-free` | withdrawn by the same reasoning |
| every `github/*` | HTTP 466, Copilot CLI version gate |
| `cxa/*` | HTTP 503, transport not configured |
| `freellmapi` | 0 keys configured, serves nothing |

**Three routes admitted, all local, all of which answered as themselves.**
All seven roles pinned to one of them. Nothing metered is admitted,
because nothing metered answered.

### What the two subscriptions actually are

Neither a Claude nor a ChatGPT subscription includes API access; they
ship CLIs. `hermes auth.json` holds a credential pool of `copilot` and
`openrouter` only — there is no `anthropic` or `openai` provider.

- **Claude** — no OmniRoute bridge exists (all 21 providers enumerated).
  Usable only by running the `claude` CLI as an agent, which Hermes
  already does. Not routable as a model.
- **ChatGPT** — reachable through OmniRoute's `codex-app-server` bridge
  as `cxa/*`. Codex CLI 0.159.2 installed on the desktop, reports
  *Logged in using ChatGPT*. Blocked only on the transport setting.
- **Copilot** — the one that actually exposes Opus 5 and GPT-5.6 as
  routable models, currently behind the 466 gate.

### Needs the operator

- **Desktop OmniRoute gateway key.** Each instance mints its own inside
  encrypted `storage.sqlite`; the laptop's key returns 401 there and was
  removed rather than left lying about. Generate one in the desktop UI,
  then `python ~/.tri-ai/ops/set-omniroute-key.py`.
- **Codex app-server transport** url and token, in the OmniRoute UI.
