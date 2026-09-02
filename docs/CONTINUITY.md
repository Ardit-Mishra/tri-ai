# Continuity — working through a quota wall

The premium CLI has a usage window. It runs out mid-task, repeatedly. Model-level failover inside
the agent does not help, because the thing that stops is not the model — it is *the session*.

There are two answers, for two different kinds of work, and confusing them is the main reason this
was hard to operate.

|  | `claude-free` / `cf` | the queue |
|---|---|---|
| keeps going | **you, interactively** | **work, unattended** |
| runs | the same CLI, on a free model | Hermes one-shot, per task |
| good for | thinking, exploring, conversation | test suites, builds, benchmarks, mechanical edits |
| judged by | you, live | a verify command's exit code |
| needs | OmniRoute running | nothing running |

Use the first when *you* want to keep working. Use the second when *the work* should keep going
without you.

---

## 1. `claude-free` — the same CLI, a free brain

```powershell
claude-free              # interactive session on the free model
claude-free <args>       # any normal CLI args pass through
```

Defined as a function in `Documents\WindowsPowerShell\profile.ps1`, which calls
`~\claude-free.ps1`. What that script does, in order:

1. **Checks the router is alive** — three attempts against `/v1/models` with a generous timeout,
   because the router is slow for a few seconds after boot and a single tight check falsely
   reports it down. An HTTP *error* response (401 without a key) still counts as alive: it proves
   something is listening.
2. **Requires `OMNIROUTE_KEY`** to be set, and never prints it.
3. **Sets the Anthropic env vars in child scope only** — `ANTHROPIC_BASE_URL`,
   `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`. Your normal `claude` command in
   the parent shell is completely untouched and stays premium.
4. **Launches the CLI**, now talking to the router instead of the vendor.

Model defaults to `auto/best-coding`; override with `OMNIROUTE_MODEL`.

**One-time setup** (yours to do, in the router dashboard at `http://localhost:20128`):

```powershell
setx OMNIROUTE_KEY "sk-omni-..."        # then OPEN A NEW TERMINAL
setx OMNIROUTE_MODEL "auto/best-coding" # optional
```

Add only **free** providers. A metered per-token provider breaks the $0 rule and turns a
continuity lane into a bill.

### If it says the router is not responding

That is the expected message when the router simply is not running. Start it and leave it in its
own terminal:

```powershell
npx omniroute
```

This is the single most common reason `claude-free` "doesn't work". Check it first, every time.

---

## 2. `cf` — the managed switch

`cf` (`~\.claude\continuity\tools\cf.ps1`) is the deliberate version of the same move. Rather than
just pointing at a free model and hoping, it consults the continuity config and the route registry
first.

Configuration lives in one file, `~\.claude\continuity\config\continuity.config.yaml`, and every
tool reads from it — nothing is hard-coded across scripts:

```yaml
thresholds:
  prepare_percent: 85            # warn, refresh the handoff note, preflight the route
  recommend_switch_percent: 100  # recommend switching, even if paid credits could continue

continuity:
  primary_route: github/gpt-4o-2024-11-20   # pinned, verified
  dynamic_route: auto/coding:free           # kept, but NOT trusted blindly
  dynamic_route_requires_preflight: true
  manual_switch_command: cf
  session_record_max_age_minutes: 120       # a stale session record is rejected
```

Two decisions in there are worth understanding, because they are the difference between a
continuity system and a way to lose an afternoon.

**85% prepares, 100% switches.** At 85% the handoff note is refreshed and the alternate route is
preflighted *while the good model is still available to do it well*. Discovering your fallback is
broken at 100% means discovering it with the broken model.

**"Even if credits could continue."** Included quota and paid overage credits are different
things. The policy recommends switching at 100% of the included window rather than silently
sliding into spend. Credits are an emergency bridge, not a default.

### The route registry — why a route name is not an identity

`~\.claude\continuity\config\route-registry.json` records which routes have actually been proven
to work with this CLI. The key insight is in its own header:

> Compatibility identity is keyed by **actual model + adapter path**, not by route name.

A dynamic route like `auto/coding:free` resolves to a *different model* depending on what is up
that day — the registry has observed it landing on several. So a "verified `auto/coding:free`"
would be a meaningless record. What gets verified is the resolved model plus the adapter that
carried it, together with the CLI and router versions it was tested against.

The pinned route `github/gpt-4o-2024-11-20` is marked verified against a real capability list:
context recall, historical tool blocks, issuing a new tool call, editing a file, running tests,
and honouring a frozen decision. That list exists because a model can answer fluently and still be
unusable as an agent — it loses the tool-call format, or forgets a decision you already made.

**So: pinned by default, dynamic only after preflight.**

### Work classification while you are away

```yaml
permissions:
  auto_merge_green_write: false   # even safe writes need an explicit merge gate
  queue_yellow_while_absent: true
  defer_red_while_absent: true
```

Green work proceeds but still does not self-merge. Yellow queues. Red waits for a human. The free
lane never gets to decide that something risky was fine.

---

## 3. The queue — unattended work

Covered in [OPERATING.md](OPERATING.md). The short version:

```bash
python src/run_queue.py
```

Every task carries a `verify` command; its exit code decides pass or fail; a failure reverts. No
daemon, no session, nothing to keep alive. This is what runs while you sleep or fly.

---

## Which to reach for

```
Quota about to run out
        │
        ├─ Do I need to keep THINKING?  ──▶  claude-free  (or cf, for the managed switch)
        │                                     needs: npx omniroute running
        │
        └─ Does WORK need to continue without me?  ──▶  write verify-able tasks into
                                                        queue.jsonl, run run_queue.py
```

And the boundary that keeps both honest: work a command cannot judge does not go to the free lane.
It waits.
