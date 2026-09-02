#!/usr/bin/env python3
"""
Continuity runner — execute a queue of work on the free lane when Claude is out.

THE PROBLEM THIS SOLVES
-----------------------
Claude Code's usage window runs out mid-program, repeatedly. Model-level failover
inside Hermes does not help: the work itself stops. What continues the work is a
QUEUE of tasks that a weaker agent can both execute and check.

THE RULE THAT MAKES IT SAFE
---------------------------
Every task carries a `verify` command whose exit code decides pass/fail. Nothing
is accepted on the agent's say-so. This is not bureaucracy — it is the observed
failure mode:

  * Asked to delete two project entries, verified by `grep -> empty`, the free
    lane did it perfectly.
  * Asked to rewrite three prose strings, with no mechanical oracle, the SAME
    lane deleted 387 of 389 lines and reported success.

So: if you cannot write a command that proves the task worked, the task does not
belong in this queue. Leave it for Claude.

GUARANTEES
----------
* Work happens on a feature branch. Never pushes, never merges, never deploys.
* A task that fails verification is REVERTED (`git checkout -- .`) so a broken
  attempt cannot pile onto the next task.
* Every attempt is appended to a ledger, so the whole run is reviewable after.

USAGE
-----
    python run_queue.py                 # run every pending task
    python run_queue.py --dry-run       # show what would run
    python run_queue.py --only <id>     # single task
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
QUEUE = HERE / "queue.jsonl"
LEDGER = HERE / "ledger.jsonl"

HERMES = pathlib.Path(os.environ.get(
    "HERMES_BIN",
    pathlib.Path.home() / "AppData/Local/hermes/hermes-agent/venv/Scripts/hermes.exe",
))

# Hermes' terminal starts in the user's home directory and `cd` does NOT persist
# between its commands. Every command it runs must carry its own cd. This is not
# optional — without it the agent reports "not a git repository" from the wrong
# directory and the task looks broken when it is merely lost.
CD_PREAMBLE = (
    "Your terminal starts in {home} and `cd` does NOT persist between commands. "
    "Prefix EVERY command with: cd {repo} && \n\n"
)

HARD_RULES = """
NON-NEGOTIABLE:
- Never run: git push, git merge, git rebase, any deploy command.
- Never modify .env, credentials, tokens, or keys.
- Touch ONLY the files this task names.
- If you cannot complete the task, say so plainly. Do NOT partially edit a file
  and report success — a half-finished edit is worse than an untouched one.
"""


def sh(cmd: str, cwd: str | None = None, timeout: int = 900) -> tuple[int, str]:
    """Run a shell command, returning (exit_code, combined_output)."""
    try:
        p = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True,
                           text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"TIMEOUT after {timeout}s"
    except Exception as exc:  # noqa: BLE001
        return 1, f"{type(exc).__name__}: {exc}"


def load_queue() -> list[dict]:
    if not QUEUE.exists():
        sys.exit(f"no queue at {QUEUE}")
    tasks = []
    for line in QUEUE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("//"):
            tasks.append(json.loads(line))
    return tasks


def done_ids() -> set[str]:
    if not LEDGER.exists():
        return set()
    out = set()
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("result") == "pass":
            out.add(e["id"])
    return out


def record(entry: dict) -> None:
    with LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def run_task(t: dict, dry: bool) -> str:
    repo = t["repo"]
    tid = t["id"]
    print(f"\n=== {tid} :: {t['title']}")
    print(f"    repo   : {repo}")
    print(f"    verify : {t['verify']}")

    if dry:
        return "dry"

    # Guard: the branch must be what the task expects, or we are editing the
    # wrong thing entirely.
    code, branch = sh("git branch --show-current", cwd=repo, timeout=60)
    branch = branch.strip()
    if code != 0 or branch != t["branch"]:
        msg = f"branch is {branch!r}, expected {t['branch']!r}"
        print(f"    SKIP   : {msg}")
        record({"id": tid, "result": "skip", "why": msg, "ts": time.time()})
        return "skip"

    prompt = (CD_PREAMBLE.format(home=str(pathlib.Path.home()).replace("\\", "/"), repo=repo)
              + t["prompt"] + HARD_RULES)

    t0 = time.time()
    code, out = sh(f'"{HERMES}" -z {json.dumps(prompt)}', timeout=t.get("timeout", 1800))
    elapsed = round(time.time() - t0, 1)
    print(f"    agent  : exit={code} in {elapsed}s")

    # The agent's own report is NOT evidence. The verify command decides.
    vcode, vout = sh(t["verify"], cwd=repo, timeout=t.get("verify_timeout", 900))
    passed = vcode == 0
    print(f"    verify : exit={vcode} -> {'PASS' if passed else 'FAIL'}")

    if not passed:
        # Revert so a broken attempt cannot contaminate the next task.
        sh("git checkout -- .", cwd=repo, timeout=120)
        print("    revert : working tree restored")

    record({
        "id": tid, "title": t["title"], "repo": repo,
        "result": "pass" if passed else "fail",
        "agent_exit": code, "verify_exit": vcode,
        "verify_output": vout[-800:], "agent_tail": out[-800:],
        "seconds": elapsed, "ts": time.time(),
    })
    return "pass" if passed else "fail"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only")
    args = ap.parse_args()

    tasks = load_queue()
    already = done_ids()
    counts = {"pass": 0, "fail": 0, "skip": 0, "dry": 0}

    for t in tasks:
        if args.only and t["id"] != args.only:
            continue
        if t["id"] in already:
            print(f"=== {t['id']} :: already passed, skipping")
            continue
        counts[run_task(t, args.dry_run)] += 1

    print(f"\n{'='*60}\n  pass={counts['pass']}  fail={counts['fail']}  "
          f"skip={counts['skip']}\n  ledger: {LEDGER}")
    # Non-zero only on failure, so this is usable from a scheduled task.
    return 1 if counts["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
