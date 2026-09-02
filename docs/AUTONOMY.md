# Autonomy — what runs without a human, and what deliberately does not

The interesting claim is not "I built an agent system". It is "I know exactly which parts of it
run unattended and which parts I refuse to let run unattended, and I can show you the ledger."

---

## The honest ceiling

> Free and local agents do chores, test suites, builds, training runs and mechanically verifiable
> work. Feature work stays human-driven with review.

Not modesty — the observed failure mode. The same free model, on the same day: given a task with a
`grep` oracle it performed perfectly; given a prose-rewriting task with no oracle it deleted 387 of
389 lines of a working file and reported success.

The variable was never difficulty. It was whether a command could judge the result.

So the system's own description of itself is *supervised for features, autonomous for verifiable
work* — and it is stated that way rather than more grandly because the ledger only supports that.

---

## Scheduling: Windows Task Scheduler, not `hermes cron`

`hermes cron` exists and works. This system does not use it. `hermes cron list` returns
"No scheduled jobs", and that is accurate, not an oversight waiting to be fixed.

The reason is a lesson that cost real work:

> **An SSH-launched process dies with the session.**

A training run started over SSH from a travelling laptop is killed by the first network drop —
which, travelling, is constant. Worse, one long-running helper was deleted from disk while still
running, so killing it made it unrecoverable on that machine.

Windows Task Scheduler survives disconnects, survives reboots, and is independent of whether any
agent is alive. It is the durability layer. `hermes cron` runs *inside the gateway process*, which
is exactly the thing that might not be running.

### The dispatch pattern

```bash
# 1. copy in the script and only the data it needs
scp train.py desktop:C:/jobs/
scp data/training.csv.bz2 desktop:C:/jobs/data/

# 2. a .cmd wrapper — logging is not optional, it is the only way to see inside
cat > run.cmd <<'EOF'
@echo off
set PY="C:\path\to\python.exe"
cd /d C:\jobs
echo ==== started %DATE% %TIME% > C:\jobs\run.log
%PY% train.py --device cuda >> C:\jobs\run.log 2>&1
echo ==== exit %ERRORLEVEL% at %DATE% %TIME% >> C:\jobs\run.log
EOF
scp run.cmd desktop:C:/jobs/

# 3. register once, then trigger
ssh desktop 'schtasks /Create /TN "MyJob" /TR "C:\jobs\run.cmd" /SC ONCE /ST 23:59 /F /RL LIMITED'
ssh desktop 'schtasks /Run /TN "MyJob"'

# 4. watch from anywhere
ssh desktop 'Get-Content C:\jobs\run.log -Tail 20'
```

The `/SC ONCE /ST 23:59` is a convention: the schedule never fires on its own, so the task exists
purely as a durable, triggerable container. `/RL LIMITED` keeps it out of elevated privileges.

Currently registered on the desktop:

| task | role |
|---|---|
| `AgentGateway` | the Telegram gateway — **enabled**, so the bot survives a reboot |
| `DeskRun` | the command runner |
| `PmhcArchTrain`, `PmhcEsmProper`, `PmhcEsmCnnBiLstm`, `PmhcSeedSweep` | training jobs, triggered on demand |

`AgentGateway` was previously **disabled and pointing at a deprecated script**, which meant the
bot only ran because a process happened to still be up from a manual start. It would not have
survived a power cut. Check this kind of thing; do not assume that something currently running is
something that will come back.

### Recurring work

For genuinely recurring jobs — nightly test suites, dependency audits — use a real schedule
instead of the `ONCE` convention:

```bash
schtasks /Create /TN "NightlyQueue" /TR "C:\path\run_queue.cmd" /SC DAILY /ST 03:00 /F /RL LIMITED
```

`run_queue.py` is built for this: it exits non-zero only on failure, and it skips anything already
recorded as passed, so a nightly run retries only what is broken.

A second, cleaner option for repo-level checks is the repository's own CI on a `schedule` trigger.
One production service in this system runs its grounding audit nightly in CI rather than on every
push, precisely because that audit calls live upstream APIs and an upstream blip should fail a cron
run, not a build. That placement decision matters more than the scheduler you pick.

---

## What is deliberately not wired

**The phone → execute → report loop is open.** Messaging the bot works, and `/capture` writes to an
inbox file. Nothing reads that inbox back out and executes it. The dispatcher is the missing link,
and until it exists this is a *chat* interface to the desktop, not a remote execution loop. It is
described that way everywhere rather than implied to be more.

**`hermes kanban` swarm is not wired.** The board exists and is empty. The parallel
workers → verifier → synthesizer pattern has been run, but through the orchestrator on the laptop,
not through the gateway.

Both are honest gaps, not hidden ones. The rule when documenting this system: if the ledger does
not show it, the site does not claim it.

---

## The ledger

Every queued attempt appends one line to `ledger.jsonl`: id, result, agent exit code, verify exit
code, duration, and the tail of both outputs.

Latest state: **9 of 9 passed across 6 repositories, ~1,216 seconds of agent time, $0.** Its most
recent run independently re-verified work that had just been completed by hand — including an
assertion that no explanation a production service serves is ungrounded.

This is what makes the autonomy claim citable. When describing the system anywhere public, quote
the ledger. Never quote a recollection.
