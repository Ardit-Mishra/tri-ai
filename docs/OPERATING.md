# Operating — the commands you actually type

Assumes the system is already built (see [SETUP.md](SETUP.md)).

---

## Run the queue

The single most useful command in the system. No daemon, no session, nothing to keep alive.

```bash
python src/run_queue.py
```

| flag | effect |
|---|---|
| `--dry-run` | print what would run and stop |
| `--only <id>` | run one task |

Re-running is safe. Anything already recorded `pass` in `ledger.jsonl` is skipped, so a second run
only retries what failed. Exit code is non-zero only if something failed, which makes it usable
directly as a scheduled task.

---

## Add a task

Append one JSON object per line to `queue.jsonl`:

```json
{"id":"gs-tests","title":"GenomeSight: suite must stay green","repo":"C:/path/to/repo","branch":"feature-branch","prompt":"Run the tests and report the final summary line verbatim. If any fail, fix the CODE and re-run until green.","verify":"pytest -q","verify_timeout":900,"timeout":1800}
```

| field | meaning |
|---|---|
| `id` | unique; used by `--only` and by skip-if-passed |
| `repo` | absolute path, forward slashes |
| `branch` | task is **skipped** if the repo is on a different branch |
| `prompt` | the instruction. `cd <repo> &&` is prepended for you |
| `verify` | **the whole safety mechanism** |
| `timeout` / `verify_timeout` | seconds |

### Writing a verify command

This is the only part that requires thought. The question is not "did the agent do something" but
"can a command prove it worked".

**Good:**

```bash
pytest -q
npx tsc --noEmit && npm run build
python build.py                          # refuses to emit on a contrast failure
node scripts/verify-parity.mjs           # asserts two implementations agree
! grep -rq "ClaimThatMustBeGone" src/    # proves a deletion
```

**Bad:** anything that exits 0 regardless; anything that only checks a file was *touched* rather
than that it is *correct*.

If you cannot write the command, the task does not belong in the queue.

### Changing what a task means

Give it a **new `id`**. The ledger skips by id, so editing a passed task in place means it never
re-runs. When one task's contract changed from "285 tests" to "333 tests plus a provenance
assertion", it was renamed `gc-checks` → `gc-checks-provenance` for exactly this reason.

---

## Read the ledger

```bash
python - <<'PY'
import json
rows=[json.loads(l) for l in open('ledger.jsonl',encoding='utf-8') if l.strip()]
ok=[r for r in rows if r.get('result')=='pass']
print(f"{len(ok)}/{len(rows)} passed, {round(sum(r.get('seconds',0) for r in ok))}s agent time")
for r in rows[-10:]:
    print(f"  {r.get('result','?'):5} {r['id']:24} {r.get('seconds','')}")
PY
```

Every attempt carries agent exit code, verify exit code, duration and the tail of both outputs.
This is what makes an autonomy claim citable instead of assertable — quote the ledger, never a
recollection.

---

## Dispatch a GPU job to the desktop

**Never launch a long job over SSH directly.** The child dies with the session, and a travelling
laptop drops connections constantly. Register it as a scheduled task and trigger that.

```bash
# 1. copy the script and only the data it needs
scp train.py desktop:C:/jobs/
scp data/training.csv.bz2 desktop:C:/jobs/data/

# 2. a .cmd wrapper that logs everything
cat > run.cmd <<'EOF'
@echo off
set PY="C:\path\to\python.exe"
cd /d C:\jobs
echo ==== started %DATE% %TIME% > C:\jobs\run.log
%PY% train.py --device cuda >> C:\jobs\run.log 2>&1
echo ==== exit %ERRORLEVEL% >> C:\jobs\run.log
EOF
scp run.cmd desktop:C:/jobs/

# 3. register and fire
ssh desktop 'schtasks /Create /TN "MyJob" /TR "C:\jobs\run.cmd" /SC ONCE /ST 23:59 /F /RL LIMITED'
ssh desktop 'schtasks /Run /TN "MyJob"'

# 4. watch
ssh desktop 'Get-Content C:\jobs\run.log -Tail 20'
```

Check VRAM first — Ollama holds a loaded chat model resident:

```bash
ssh desktop 'nvidia-smi --query-gpu=memory.used,memory.total --format=csv'
```

**The desktop's SSH shell is PowerShell.** `&`, `>nul` and bash conditionals will fail there. Use
`;`, `2>$null`, and PowerShell syntax — or put the complexity in a `.cmd` file and call that.

---

## Health check

```bash
hermes --version
hermes fallback list                                    # chain must be non-empty
curl -s -o /dev/null -w "%{http_code}\n" localhost:20128/models   # 200 = router up
ollama list | tail -n +2 | wc -l                        # local models
tailscale status                                        # nodes online
ssh desktop 'nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv'
ssh desktop 'Get-ScheduledTask | Where-Object {$_.State -eq "Running"} | Select TaskName'
```

Probe the router on **`/models`**, not `/health` — it 404s that path and a naive check will report
it down when it is fine.

---

## Talk to it from a phone

Message the Telegram bot. It reaches the Hermes gateway on the desktop and can run commands there.

Two rules that make its answers trustworthy, both learned the hard way:

1. **Ask for a computed value, not a reading.** "How many models are installed?" gets a guess.
   "Run `ollama list | tail -n +2 | wc -l` and paste the output" gets the number.
2. **Include a command you expect to fail**, occasionally, and check it reports the failure. A
   model that quietly omits failures is worse than one that errors.

---

## When the premium CLI runs out of quota

What actually works today:

1. Commit everything in progress, on a feature branch.
2. Write the remaining verifiable work into `queue.jsonl` — test suites, builds, typechecks,
   benchmarks, mechanical deletions with a `grep` oracle.
3. Run `python src/run_queue.py`. It uses the free lane and self-verifies.
4. When quota resets, read the ledger and review the diffs.

Judgment work — architecture, honesty calls, anything a command cannot judge — **waits**. That is
not a limitation to engineer around; it is the boundary that keeps the ledger meaningful.
