# Why this is built the way it is

Tri-AI exists because of a single observation, and almost every design choice in
it is downstream of that one event.

---

## The event

A delegated coding task came back reported as complete. It had **deleted 387 of
389 lines** and announced success.

Nothing in the agent's own output distinguished that from a real completion. The
report was confident, well-formed, and wrong. There is no prompt that reliably
fixes this, because the thing being asked to self-assess is the thing that
failed.

## The decision that follows

**Acceptance is never the agent's own report.** A step is accepted when its
verification command exits `0`, and on no other basis.

That is a smaller claim than it sounds, and a stricter one. The system does not
try to judge whether work is good. It runs a command the operator wrote, reads
one integer, and acts on that. Everything an agent says about its own work is
treated as narration.

The cost is real: every task has to arrive with a way of checking it. Work that
cannot be verified cannot be accepted, which rules out a class of jobs this
system will simply not do.

## Decomposition happens before the planner, on purpose

A build arrives as a **dependency graph** of smaller steps, topologically
ordered. The planner's job is narrow and deliberately so — from its own
docstring:

> The planner consumes a graph document produced by a strong model or an
> operator. It treats that document as data: validate every node before opening
> the board transaction, persist every node through `board.create_task`, then
> exit. **It never polls, dispatches, or invokes an agent.**

The graph is validated **whole, before a single node is written**. A graph that
cannot be safely persisted is refused outright rather than half-applied, so there
is no state in which half a plan exists and nobody knows which half.

Keeping planning out of the executor means the component that decides *what to
do* and the component that decides *whether it worked* share no code and cannot
quietly agree with each other.

## Routing: cheapest lane that can finish it

Each `task_kind` resolves to a primary lane with a **required distinct fallback** —
the config refuses a fallback identical to its primary, because a failover that
lands on the failed endpoint is not a failover. Lanes are probed, and work fails
over rather than stopping.

The economic point: local models and free lanes take the work they can handle,
and paid capacity is spent only where it is actually needed. The marginal cost of
a build is approximately zero.

**What this is not:** routing is a declared policy, not automatic capability
discovery. The system does not benchmark models and infer what each is good at.
It reads a table someone wrote. Saying otherwise would be the kind of claim this
project exists to refuse.

## Surviving failure, not just succeeding

An agent that hangs, crashes, or keeps writing after being told to stop is the
normal case, not the exception:

- **Leased claiming** — two workers cannot take the same job.
- **Heartbeats requiring observable progress**, not elapsed time. A process that
  is alive but achieving nothing is not healthy.
- **Process-tree kills** — a timed-out worker's children stop writing too.
- **Stash-based revert** — partial work is recoverable, not merged.
- **Watchdog self-heal**, verified by killing all four services and confirming
  unattended recovery.

## A route must prove itself, because a name proves nothing

The routing section above says work goes to the cheapest lane that can finish
it. That is only a decision if "the lane" means something, and for a long time
it did not.

Three roles were pinned to `auto/best-coding` and ran **29 recorded tasks**
there. The pin was chosen from the alias name. Calling all 574 models the router
advertised, once each, and comparing the **served** model name against the
requested one showed what the name was worth: `auto/best-coding` resolves to a
diffusion model, and all 22 `auto/*` aliases serve something other than what they
name — `auto/claude-opus` returns a 31B Gemma at HTTP 200.

A request can be substituted at three layers, each invisible to the one above:

1. **Listed but not entitled.** 517 of 574 refused outright.
2. **Answered as something else.** HTTP 200 proves a response arrived, not that
   the named model produced it.
3. **The pin was dropped in transit.** A bare Ollama tag is routed to the
   gateway, refused, and replaced by the first fallback — which is then written
   into the usage file the ledger reads. The request vanishes and the run
   succeeds.

So admission requires all three to hold: it answered, the response named what was
asked for, and the usage file recorded that same id. Twenty models clear that
bar. The rest are not routes, whatever the catalogue calls them.

The cost of having assumed otherwise is stated rather than quietly fixed:
**model attribution in ledger entries before 2026-09-30 is not evidence.** Exit
codes are unaffected, because a verify command's result does not depend on
knowing which model did the work. That is the strongest argument for the gate
this document opens with — the one fact that stayed true while the other one
turned out to be fiction.

Reading: [`evidence/MODEL_SWEEP.md`](evidence/MODEL_SWEEP.md).

## Evidence

`evidence/ledger.jsonl` holds one entry per verified task: the repository, the
verification command, its exit code and its output. Nine entries, nine passes —
the badge and the file agree, which is the point of keeping the file.

## What is still unknown

- The ledger is small. Nine verified tasks demonstrate the mechanism works; they
  do not establish a failure rate.
- No measurement exists of how often routing picks a lane that then cannot finish
  the task, or of the cost of that retry.
- Verification quality is entirely the operator's: the system enforces that a
  check ran and passed, never that the check was a good one. A weak test passes
  just as convincingly as a strong one.
- Three agent runtimes are implemented and nothing selects between them per
  task. Until something does, the two paid subscriptions the design reserves for
  judgement work are not reachable from the system at all.
- The sweep is one account on one day. A route admitted today can be withdrawn
  by a provider tomorrow, and nothing re-probes on a schedule.
