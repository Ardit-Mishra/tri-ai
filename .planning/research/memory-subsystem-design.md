# Agent Memory & Self-Evolution Subsystem — Design Specification

**Status:** design — not started
**Author:** Claude (this session)
**Date:** 2026-09-10
**Constraint:** $0 marginal cost, 12 GB VRAM, local-first, headless execution, Git-based provenance
**Depends on:** Phase 3 (board substrate, ledger, worker pipeline — all shipped)
**Drives:** Phase 5+ (self-evolution, advanced observability)

---

## 1. Why This Exists

Tri-AI's workers currently execute tasks with no memory of past runs. Each task is isolated — a
worker claims, executes, verifies, and reports. The ledger captures *what happened* but nothing
*learns* from it. The planner builds graphs from scratch every time with no access to historical
patterns.

This subsystem adds three memory tiers that serve the execution loop, not a chat interface:

- **Episodic:** what happened, when, to whom, with what outcome — queryable by failure type,
  task kind, or time range.
- **Semantic:** what depends on what, what fixes what, what pattern repeats — a knowledge graph
  extracted from execution history with zero LLM calls.
- **Procedural:** what to do differently next time — markdown rules with file citations, validated
  before injection, updated by failure analysis.

And one evolution mechanism:

- **Self-evolution:** a closed loop where failures produce rules, rules are validated before use,
  and repeated successful patterns crystallize into planner macros.

### Design Principles (drawn from reference implementations)

| Principle | Source | How it lands here |
|-----------|--------|-------------------|
| Markdown-in-Git as system of record | GBrain | Procedural rules are `.md` files in Git; Git diff is the audit trail |
| Compiled truth + timeline | GBrain | Episodic memory has a "latest state" snapshot AND a timestamped history |
| Zero-LLM knowledge extraction | GBrain | Relationships extracted from graph structure and exit codes, never by asking a model |
| Thin harness, fat skills | GBrain | The memory system is a small core; behaviors live in markdown instruction files |
| File-based atomic ledgers | agent-board | Episodic logs are append-only JSONL, atomically written |
| Strict review boundaries | agent-kanban | Workers cannot modify rules about their own task kind (self-evolution is operator-gated) |
| State transition tracking | Aiden | Every memory write records: Trigger → Analysis → Rule → Validation → Activation |
| Failure-driven skill evolution | MemSkill | Failures are the primary trigger for new rules, not successes |
| Context compaction | Open Multi-Agent | Lessons are injected at a budget, not dumped wholesale |

### What This Is NOT

- Not a vector database or RAG system (that's gap #5, probe-gated, separate)
- Not a chat memory (Tri-AI has no chat-based workers)
- Not a new state store (the board and ledger remain the source of truth)
- Not an LLM-dependent reflection loop (every extraction is deterministic)
- Not mandatory for Phase 4 — this is a companion design that can be implemented incrementally

---

## 2. Folder Layout

```
C:\Users\ardit\tri-ai\
├── .tri-ai/                          # Memory root (Git-tracked)
│   ├── memory/
│   │   ├── episodic/
│   │   │   ├── runs.db               # SQLite index of all runs (queryable)
│   │   │   └── summaries/            # Git-tracked markdown summaries per run
│   │   │       └── {run_id}.md       # "compiled truth" snapshot
│   │   ├── semantic/
│   │   │   ├── knowledge.json        # Knowledge graph (nodes + typed edges)
│   │   │   └── patterns.json         # Crystallized execution patterns
│   │   └── procedural/
│   │       ├── rules.json            # Active rules with citations + confidence
│   │       └── TRILessons.md         # Human-readable compiled lessons (Git-tracked)
│   ├── skills/                       # Fat skill files (Git-tracked)
│   │   ├── _index.json               # Skill registry (name → file, tags, version)
│   │   ├── python-test-chore.md      # Skill: run Python tests
│   │   ├── typecheck-chore.md        # Skill: run type checker
│   │   ├── dependency-audit.md       # Skill: audit dependencies
│   │   └── {repo}-specific/          # Repo-specific skills
│   │       └── {skill-name}.md
│   └── context/                      # Context budget maps (per-task)
│       └── {task_id}.json            # Which files/skills this task needs
│
├── src/
│   ├── memory/
│   │   ├── __init__.py
│   │   ├── episodic.py               # SQLite + JSONL ledger bridge
│   │   ├── semantic.py               # Knowledge graph (extract, query, update)
│   │   ├── procedural.py             # Rules engine (load, validate, inject, update)
│   │   ├── evolution.py              # Self-evolution loop (post-mortem, crystallize)
│   │   ├── context_budget.py         # Context map builder (which files for this task)
│   │   └── skills.py                 # Skill loader (markdown → injectable content)
│   ├── dashboard/
│   │   ├── __init__.py
│   │   ├── jarvis_terminal.py        # Terminal TUI (curses/rich ASCII neural net)
│   │   └── jarvis_web.py             # Local web dashboard (canvas neural net)
│   └── ... (existing modules)
│
└── tests/
    ├── test_memory_episodic.py
    ├── test_memory_semantic.py
    ├── test_memory_procedural.py
    ├── test_memory_evolution.py
    ├── test_memory_context_budget.py
    ├── test_memory_skills.py
    ├── test_memory_safety.py        # Workers cannot modify rules about their own task kind
    └── test_dashboard.py
```

---

## 3. The 3-Tier Memory Stack

### 3.1 Episodic Memory — Run Ledgers & Git Timelines

**What it stores:** every task execution — who ran it, what happened, what the verify command
said, how long it took, what the stdout/stderr was.

**Dual format (compiled truth + timeline):**

1. **SQLite `runs.db`** — the queryable index. One row per attempt. Schema:

```sql
CREATE TABLE runs (
    run_id         TEXT PRIMARY KEY,
    task_id        TEXT NOT NULL,
    graph_id       TEXT,              -- which graph this task belonged to
    worker_host    TEXT NOT NULL,     -- e.g. "DESKTOP-GPU"
    worker_pid     INTEGER NOT NULL,
    attempt        INTEGER NOT NULL,  -- 1-based, increments on retry
    status         TEXT NOT NULL,     -- 'claimed' | 'running' | 'done' | 'failed' | 'blocked'
    verify_exit    INTEGER,           -- exit code (NULL if never reached verify)
    verify_outcome TEXT,              -- 'passed' | 'failed' | 'timeout' | NULL
    error_class    TEXT,              -- 'environment' | 'logic' | NULL (after Phase 4 Slice 3)
    agent_model    TEXT,              -- which local model ran the agent
    workspace_kind TEXT,              -- 'dir' | 'worktree'
    workspace_path TEXT,
    started_at     TEXT NOT NULL,     -- ISO 8601
    completed_at   TEXT,              -- ISO 8601 or NULL
    wall_seconds   REAL,              -- completed_at - started_at
    ledger_path    TEXT,              -- path to the JSONL ledger line
    log_path       TEXT,              -- path to captured stdout/stderr
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_runs_task ON runs(task_id);
CREATE INDEX idx_runs_status ON runs(status);
CREATE INDEX idx_runs_error_class ON runs(error_class);
CREATE INDEX idx_runs_graph ON runs(graph_id);
```

2. **Markdown summaries** (`memory/episodic/summaries/{run_id}.md`) — the Git-tracked "compiled
   truth" snapshot. Generated after each completed run. Human-readable, diffable:

```markdown
# Run {run_id}

- **Task:** {task_id} ({graph_id})
- **Worker:** {worker_host}:{worker_pid} attempt #{attempt}
- **Status:** {status}
- **Verify:** exit {verify_exit} → {verify_outcome}
- **Error class:** {error_class}
- **Duration:** {wall_seconds}s
- **Workspace:** {workspace_kind} {workspace_path}
- **Agent model:** {agent_model}

## Stderr (last 50 lines)
{truncated stderr}

## Verdict
{pass/fail summary}
```

**How it integrates with the existing ledger:** The JSONL ledger (`src/ledger.py`) is the
*append-only write path* — it stays exactly as it is. `memory/episodic.py` is a *read-side bridge*
that:
- On each ledger append (or on a periodic sync), inserts a row into `runs.db`
- After each completed run, generates the markdown summary
- Provides query functions: `recent_failures(task_kind)`, `runs_for_task(task_id)`,
  `error_rate(graph_id, window)`

The JSONL ledger is never modified. SQLite is derived from it. If `runs.db` is corrupted, it can
be rebuilt from the JSONL.

**Query examples (used by other memory tiers):**

```python
from memory.episodic import EpisodicMemory

mem = EpisodicMemory(".tri-ai/memory/episodic/runs.db")

# What failed in the last 24 hours?
failures = mem.recent_failures(since="24h")

# What's the error rate for this task kind?
rate = mem.error_rate(task_kind="python-test", window="7d")

# What did this task's last attempt look like?
history = mem.history_for_task("repo-tests-abc123")
```

### 3.2 Semantic Memory — Knowledge Graph & Context Maps

**What it stores:** typed relationships between tasks, files, errors, and rules. Extracted
deterministically from execution history — zero LLM calls.

**Schema (`knowledge.json`):**

```json
{
  "nodes": {
    "task:repo-tests-abc123": {
      "type": "task",
      "kind": "python-test",
      "repo": "tri-ai",
      "created": "2026-09-10T10:00:00Z"
    },
    "error:exit-7": {
      "type": "error",
      "class": "logic",
      "pattern": "verify_exit=7",
      "first_seen": "2026-09-10T10:05:00Z"
    },
    "file:src/worker.py": {
      "type": "file",
      "path": "src/worker.py",
      "last_modified": "2026-09-10T09:00:00Z"
    },
    "rule:avoid-7-on-worker": {
      "type": "rule",
      "rule_id": "avoid-7-on-worker",
      "confidence": 0.85,
      "citations": ["src/worker.py:485", "tests/test_phase3_failure_isolation.py:12"]
    }
  },
  "edges": [
    {
      "source": "task:repo-tests-abc123",
      "target": "task:repo-typecheck-def456",
      "relation": "depends_on",
      "extracted_from": "task_links",
      "created": "2026-09-10T10:00:00Z"
    },
    {
      "source": "task:repo-tests-abc123",
      "target": "error:exit-7",
      "relation": "produced",
      "run_id": "run-xyz",
      "created": "2026-09-10T10:05:00Z"
    },
    {
      "source": "error:exit-7",
      "target": "rule:avoid-7-on-worker",
      "relation": "resolved_by",
      "created": "2026-09-10T10:10:00Z"
    },
    {
      "source": "rule:avoid-7-on-worker",
      "target": "file:src/worker.py",
      "relation": "cites",
      "line": 485
    }
  ]
}
```

**Edge types (closed set, no LLM inference):**

| Relation | Meaning | Extraction source |
|----------|---------|-------------------|
| `depends_on` | Task A must complete before B | `task_links` (planner-written) |
| `produced` | Task produced this error | Ledger: task → error |
| `resolved_by` | Error was resolved by this rule | Evolution loop |
| `cites` | Rule references this file:line | Rule citation field |
| `same_pattern` | Two tasks share structure (same verify command, same repo, same skill) | Deterministic hash |
| `fixed_by` | File was modified to fix this error | Post-mortem diff |
| `retries_from` | This run is a retry of that run | Ledger: same task_id, incrementing attempt |

**Extraction rules (all zero-LLM):**

1. `depends_on` edges come from `task_links` at graph-creation time — the planner already writes
   these.
2. `produced` edges come from the episodic bridge — when a run fails, an edge is added from the
   task node to the error node.
3. `same_pattern` edges come from hashing (task_kind, repo, verify_command, skill_file) — if two
   tasks produce the same hash, they share a pattern.
4. `fixed_by` edges come from the post-mortem — when a rule is created after a failure and the
   failure's error class matches the rule's trigger, the edge is added.
5. `retries_from` edges come from the ledger — same task_id + attempt N → attempt N+1.

**Context maps (`context/{task_id}.json`):**

Before a worker claims a task, the context budget system builds a map of what this task needs:

```json
{
  "task_id": "repo-tests-abc123",
  "budget_tokens": 8000,
  "files": [
    {"path": "src/worker.py", "relevance": "direct", "lines": "480-490"},
    {"path": "tests/test_phase3_failure_isolation.py", "relevance": "pattern", "lines": "1-50"}
  ],
  "skills": ["python-test-chore"],
  "rules": ["avoid-7-on-worker"],
  "related_runs": ["run-xyz", "run-abc"],
  "compacted_context": "Worker testing Python suite on branch phase-2/worker-assign. Last failure: verify exit 7 (logic class, circuit breaker). Rule: check exit code format before claiming."
}
```

The `compacted_context` field is the context-compaction output — a 1-2 sentence summary that fits
within the budget. It's built by `context_budget.py` by:
1. Loading the task's graph position (parents, siblings, dependencies)
2. Querying the knowledge graph for related errors and rules
3. Loading relevant skill files
4. Truncating to fit within `budget_tokens`

**Context budget tiers (inspired by Open Multi-Agent's compaction):**

| Tier | Budget | Content |
|------|--------|---------|
| L0 — minimal | 2 KB | Task title + verify command + workspace path only |
| L1 — standard | 8 KB | L0 + relevant rules + last 3 run outcomes + skill file header |
| L2 — full | 32 KB | L1 + related file excerpts + error traces + knowledge graph neighbors |

The worker receives L1 by default. L0 if VRAM is tight (measured by a free-memory check before
claim). L2 only if explicitly requested by the task's `max_runtime_seconds` being over 30 minutes
(long-running tasks get more context).

### 3.3 Procedural Memory — Fat Skills & Dynamic Rulebooks

**What it stores:** instructions for workers — how to perform specific task kinds, what to watch
out for, what patterns have been learned.

**Two layers:**

1. **Rules** (`rules.json`) — machine-readable, structured, citation-backed:

```json
{
  "rules": [
    {
      "id": "avoid-7-on-worker",
      "trigger": {
        "error_class": "logic",
        "verify_exit": 7,
        "task_kind": "python-test"
      },
      "action": "check that pytest is installed and the test file exists before running",
      "citation": ["src/worker.py:485", "tests/test_phase3_failure_isolation.py:12"],
      "confidence": 0.85,
      "created_from": "run-xyz",
      "created_at": "2026-09-10T10:10:00Z",
      "validated_at": "2026-09-10T10:10:00Z",
      "active": true,
      "times_applied": 3,
      "success_rate": 0.67
    }
  ]
}
```

2. **Skill files** (`skills/{name}.md`) — human-readable markdown instructions that workers read:

```markdown
# Python Test Chore

## When to use
Task kind is `python-test` and the verify command runs pytest.

## Pre-flight
- Check that the target branch exists: `git rev-parse --verify {branch}`
- Check that pytest is available: `python -m pytest --version`
- If the workspace is a worktree, verify it's clean: `git status --porcelain`

## Execution
1. Run the verify command as written — do not modify it
2. Capture full stdout and stderr
3. If exit code is non-zero, check if it matches a known pattern:
   - Exit 1: test failure (logic class)
   - Exit 2: usage error (logic class)
   - Exit 137: OOM killed (environment class)
   - Exit timeout: timeout (environment class)

## Post-flight
- If passed: report success to the board
- If failed: do NOT retry automatically — the circuit breaker handles retry
- If environment failure: log it but do not count against the node

## Rules that apply
- `avoid-7-on-worker`: if exit 7, check pytest installation first
```

**How workers receive procedural memory:**

The worker's `execute_task` function (after claim, before agent execution) calls:

```python
from memory.procedural import load_worker_context

context = load_worker_context(
    task_id=task["id"],
    task_kind=task.get("task_kind", "unknown"),
    repo=task.get("repo", "unknown"),
    budget_tokens=8000
)

# context contains:
# - relevant_rules: list of active rules matching this task's error history
# - skill_content: the markdown skill file content (truncated to budget)
# - compacted_context: 1-2 sentence summary
# - last_run_summary: what happened last time this task ran
```

This context is injected into the agent's system prompt (a future Phase 5 change to
`executor.run_agent`). In Phase 4, it's available but not yet injected — the memory system
writes the context; the worker reads it when the injection is wired.

**Citation validation (before injection):**

Every rule has `citations` — file:line references. Before a rule is injected into a worker's
context, `procedural.py` validates:

```python
def validate_rule(rule: dict) -> bool:
    """Check that all citations point to existing files at the referenced lines."""
    for citation in rule["citations"]:
        path, line = parse_citation(citation)
        if not os.path.exists(path):
            return False  # cited file was deleted — rule is stale
        with open(path) as f:
            lines = f.readlines()
            if line > len(lines):
                return False  # cited line no longer exists
    return True
```

Stale rules are deactivated (`active: false`) and logged. They're not deleted — they're part of
the audit trail.

---

## 4. Self-Evolution Mechanisms

### 4.1 Post-Mortem Reflection & Error Crystallization

**Trigger:** a run completes with `verify_outcome != 'passed'`.

**The routine (all deterministic, zero-LLM):**

```
1. READ the run's stderr (last 200 lines)
2. CLASSIFY the error:
   - If error_class is 'environment' → log to episodic memory, STOP
     (environment failures don't produce rules)
   - If error_class is 'logic' → continue
3. EXTRACT a failure signature:
   - Hash of (task_kind, verify_exit, first 3 non-empty stderr lines)
   - This is the "hard case" identifier
4. CHECK if a rule already exists for this signature:
   - If yes → increment its times_applied, recalculate success_rate, STOP
   - If no → continue
5. CREATE a new rule:
   - trigger: {error_class, verify_exit, task_kind}
   - action: extracted from stderr pattern (e.g., "ModuleNotFoundError → install dependency")
   - citation: [file where the verify command is defined, line of the error in stderr]
   - confidence: 0.5 (start low, increased by successful applications)
   - active: true
6. VALIDATE the rule's citations (Section 3.3)
7. WRITE to rules.json
8. LOG the state transition: Trigger → Analysis → Rule → Validation → Activation
```

**The state transition log (Aiden-inspired):**

Every evolution event is recorded:

```json
{
  "event_id": "evo-001",
  "timestamp": "2026-09-10T10:10:00Z",
  "trigger": {
    "type": "failure",
    "run_id": "run-xyz",
    "task_id": "repo-tests-abc123",
    "error_class": "logic",
    "verify_exit": 7
  },
  "analysis": {
    "signature": "a1b2c3d4",
    "existing_rule": null,
    "stderr_pattern": "ModuleNotFoundError: No module named 'pytest'"
  },
  "action": {
    "type": "rule_created",
    "rule_id": "install-pytest-before-test",
    "confidence": 0.5
  },
  "validation": {
    "citations_valid": true,
    "stale_citations": []
  },
  "state": "active"
}
```

### 4.2 Skill & Graph Pattern Crystallization

**Trigger:** the knowledge graph has 3+ `same_pattern` edges for the same task structure.

**The routine:**

```
1. QUERY knowledge.json for clusters of same_pattern edges
2. IF a cluster has >= 3 members AND all members have verify_outcome='passed':
   a. EXTRACT the common structure: task_kind, verify_command template, skill file, repo
   b. CREATE a macro template:
      - name: "{task_kind}-{repo}-macro"
      - template: the common task structure with placeholders
      - provenance: [list of run_ids that formed the pattern]
      - confidence: len(cluster) / (len(cluster) + 1)  # Bayesian-ish, starts at 0.75 for 3
   c. WRITE to patterns.json
   d. OFFER to the planner: when the planner creates a new graph and encounters a subtask
      that matches a macro's trigger, suggest the macro as a template
```

**Macro template schema (`patterns.json`):**

```json
{
  "macros": [
    {
      "id": "python-test-tri-ai-macro",
      "trigger": {
        "task_kind": "python-test",
        "repo": "tri-ai",
        "verify_command_pattern": "python -m pytest*"
      },
      "template": {
        "task_kind": "python-test",
        "repo": "tri-ai",
        "verify_command": "python -m pytest {test_path} -v",
        "skill_file": "python-test-chore",
        "workspace_kind": "worktree",
        "timeout": 600
      },
      "provenance": ["run-001", "run-002", "run-003"],
      "confidence": 0.75,
      "created_at": "2026-09-10T12:00:00Z",
      "times_used": 0
    }
  ]
}
```

The planner (`src/planner.py`) can optionally load `patterns.json` and suggest macros when
building a graph. This is Phase 6+ territory — the pattern must be proven reliable before the
planner trusts it.

### 4.3 Citation-Backed Validation

Every stored memory carries code-line references. Before injection into a worker's prompt:

1. **File exists?** If not → deactivate the rule, log "stale citation (file deleted)"
2. **Line exists?** If the cited line number exceeds the file's line count → deactivate, log
   "stale citation (line removed)"
3. **Content matches?** Optionally, check if the cited line still contains the expected text
   (e.g., if a rule cites `src/worker.py:485` and says "circuit breaker limit", verify that
   line 485 still relates to the circuit breaker). This is a fuzzy check — substring match, not
   exact.

Citation validation runs:
- Before every rule injection (worker pre-check)
- On a daily cron (to catch drift proactively)
- After any `git pull` or merge (to detect upstream changes)

---

## 5. Integration Points with Existing Code

### 5.1 Ledger Bridge (`memory/episodic.py`)

The existing `src/ledger.py` appends JSONL lines. The episodic bridge hooks in at two points:

**Write side:** after `src/worker.py:_record_task_failure()` or `src/worker.py:accept_task()`,
call `episodic.sync_from_ledger()` which:
- Reads the latest JSONL line
- Inserts a row into `runs.db`
- If the run completed (done or failed), generates the markdown summary

**Read side:** `episodic.py` exposes query functions that the semantic and procedural tiers call.
The JSONL ledger is never modified — SQLite is a derived index.

### 5.2 Worker Integration (`src/worker.py`)

The worker's `execute_task` sequence gains one step between "claim" and "precheck":

```
claim → [LOAD CONTEXT] → precheck → gate → agent → verify → accept/revert → ledger
                          ↑
                          memory.procedural.load_worker_context()
                          memory.context_budget.build_context_map()
```

The loaded context is passed to `executor.run_agent()` as an additional parameter (a dict or
string that gets prepended to the system prompt). This is a Phase 5 change — the memory system
writes the context; the worker reads it when the injection is wired.

### 5.3 Planner Integration (`src/planner.py`)

The planner can optionally load `patterns.json` and `knowledge.json` to:
- Suggest macro templates for known task patterns
- Avoid repeating task structures that have high failure rates
- Insert dependency edges that the knowledge graph has learned (e.g., "tasks in this repo always
  depend on the dependency-audit task")

This is Phase 6+ — the planner must be proven reliable before it trusts learned patterns.

### 5.4 Dispatcher Integration (`src/dispatcher.py`)

The dispatcher can use the episodic memory to:
- Prioritize tasks with low historical failure rates
- Deprioritize tasks with high environment-failure rates (they'll waste worker time)
- Check if a task's error class is environment (skip retry, save worker capacity)

### 5.5 Board Integration (`src/board.py`)

The board's `create_task` can optionally accept a `task_kind` field that the memory system uses
for pattern matching. This is additive — existing tasks without `task_kind` still work; the
memory system treats them as `kind: unknown`.

---

## 6. JARVIS Dashboard — Visual Neural Network

### 6.1 Terminal Dashboard (`dashboard/jarvis_terminal.py`)

A `rich`-based TUI that renders the task graph as a live ASCII neural network:

```
┌─────────────────────────────────────────────────────────────────────┐
│  TRI-AI JARVIS — Live Neural Network                    14:32:05  │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│   ┌─────────┐                                                      │
│   │  root   │ ●━━━━━━━━━┓                                          │
│   │  DONE   │           ┃                                          │
│   └─────────┘           ┣━━━━━━━┓                                  │
│                         ┃       ┃                                  │
│                  ┌──────┴──┐ ┌──┴─────────┐                        │
│                  │ failing │ │  sibling-A  │                        │
│                  │ BLOCKED │ │  ● INFLIGHT │                        │
│                  └─────────┘ └─────────────┘                        │
│                              ┃                                      │
│                       ┌──────┴──────┐                               │
│                       │ sibling-B   │                               │
│                       │ ○ UNCLAIMED │                               │
│                       └─────────────┘                               │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│  LEDGER                                                             │
│  14:32:01 run-abc sibling-A CLAIMED (attempt 1)                    │
│  14:31:55 run-xyz failing  BLOCKED  (verify exit 7, circuit breaker)│
│  14:31:40 run-001 root     DONE     (verify exit 0, 12.3s)         │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│  STATS  active: 1  done: 1  blocked: 1  unclaimed: 1  total: 4    │
└─────────────────────────────────────────────────────────────────────┘
```

**Refresh:** polls the board every 2 seconds. Zero external services. Runs in any terminal.

**Node rendering:**
- `●` = done (green)
- `◆` = in-flight (yellow, animated pulse)
- `○` = unclaimed (grey)
- `■` = blocked (red)
- `✕` = failed (red, dim)

**Edge rendering:**
- `━━━` = resolved dependency (solid)
- `┈┈┈` = unresolved dependency (dashed)
- `▶` = data flowing (animated when parent completes and child starts)

### 6.2 Local Web Dashboard (`dashboard/jarvis_web.py`)

A single-file HTML+JS dashboard served by Python's built-in `http.server`:

```python
# Usage: python -m src.dashboard.jarvis_web --port 8080
# Opens browser to http://localhost:8080
```

**The visual:** a canvas-rendered force-directed graph (no external dependencies — pure Canvas API
or a bundled vis.js from CDN). Nodes are glowing circles:

- Done nodes: steady green glow
- In-flight nodes: pulsing yellow glow (animated)
- Failed nodes: red glow with a "crack" pattern
- Unclaimed nodes: dim grey
- Blocked nodes: solid red

**Edges:** animated particle flows — when a parent completes and a child starts, particles move
along the edge from parent to child, visualizing data flow.

**Sidebar:** live ledger entries scrolling, stats (active/done/blocked/failed), memory system
status (rules count, last evolution event, knowledge graph size).

**Bottom panel:** the JARVIS "thought stream" — a scrolling log of evolution events:
```
14:32:05 [EVOLUTION] Rule created: install-pytest-before-test (confidence: 0.5)
14:31:55 [PATTERN] Same-pattern cluster detected: python-test-tri-ai (3 members)
14:31:40 [CONTEXT] L1 context loaded for sibling-A: 3 rules, 1 skill, 847 tokens
```

**The neural network metaphor:** the force-directed layout naturally arranges the graph into a
neural-network-like structure — clusters of related tasks form "regions," high-fan-out tasks look
like "hubs," and the animation of particles flowing along edges gives the impression of signals
propagating through a network. It's not a simulation — it's the actual execution, visualized in
a way that makes the graph structure intuitive.

**Constraints:**
- Reads board + ledger only. Never writes.
- Serves on localhost only. No external access.
- Zero external services. The HTML is self-contained.
- Refresh: WebSocket to a Python server that tails the board DB, or polling every 2 seconds.

---

## 7. Safety Boundaries

The memory system inherits all Phase 3 safety boundaries plus:

1. **Workers cannot modify rules about their own task kind.** A Python-test worker can read
   Python-test rules but cannot write new ones. Rule creation is operator-gated — the evolution
   loop writes rules, but they require a confidence threshold (≥ 0.7) before activation, and
   the operator can review and deactivate any rule via the dashboard or CLI.

2. **Citation validation is mandatory.** No rule is injected without valid citations. Stale rules
   are deactivated, not deleted.

3. **The memory system never modifies the board or ledger.** It reads both. Writes go to
   `.tri-ai/memory/` only.

4. **The memory system never modifies the graph.** It can suggest patterns to the planner, but
   the planner decides whether to use them.

5. **Evolution events are auditable.** Every rule creation, activation, deactivation, and
   crystallization is logged in `rules.json` with a timestamp and provenance. The operator can
   trace any rule back to the exact failure that produced it.

6. **The dashboard is read-only.** It reads the board and ledger. It never writes to any of them.

7. **Context injection is opt-in per phase.** The memory system builds the context; the worker
   reads it only when the injection is wired (Phase 5+). Until then, the context is available
   but unused.

---

## 8. Implementation Phases

### Phase 5A — Episodic Memory (the foundation)
- `memory/episodic.py`: SQLite bridge, markdown summary generation
- `memory/context_budget.py`: basic context map builder
- Tests: `test_memory_episodic.py`, `test_memory_context_budget.py`
- Integration: worker calls `episodic.sync_from_ledger()` after each run
- **Proof:** a test that runs a task, queries `runs.db` for the result, and finds the correct
  row with all fields populated. A test that generates a markdown summary and asserts its content
  matches the ledger entry.

### Phase 5B — Procedural Memory (rules + skills)
- `memory/procedural.py`: rules engine, skill loader, citation validator
- `memory/skills.py`: markdown skill file parser
- `skills/_index.json` + 2-3 starter skill files
- Tests: `test_memory_procedural.py`, `test_memory_skills.py`, `test_memory_safety.py`
- Integration: worker loads context before agent execution (available but not yet injected)
- **Proof:** a test that creates a rule with a citation, validates it, then deletes the cited
  file and asserts the rule is deactivated. A test that loads a skill file and asserts its
  content matches the expected structure.

### Phase 5C — Semantic Memory (knowledge graph)
- `memory/semantic.py`: graph extraction, query, update
- `knowledge.json` initial population from existing task_links and ledger
- Tests: `test_memory_semantic.py`
- Integration: episodic writes feed the knowledge graph
- **Proof:** a test that creates two tasks with a dependency, runs both, and asserts the
  knowledge graph has the correct `depends_on` and `produced` edges.

### Phase 5D — Self-Evolution Loop
- `memory/evolution.py`: post-mortem analysis, rule creation, pattern crystallization
- Tests: `test_memory_evolution.py`
- Integration: evolution runs after each failed task
- **Proof:** a test that runs a task with a deliberate failure, asserts a rule is created,
  then runs the same task type again and asserts the rule was applied.

### Phase 6A — JARVIS Terminal Dashboard
- `dashboard/jarvis_terminal.py`: rich-based TUI
- Tests: `test_dashboard.py` (smoke test — renders without error)
- **Proof:** a test that creates a known graph state and asserts the dashboard renders it
  correctly (snapshot test or assertion on the rendered output).

### Phase 6B — JARVIS Web Dashboard
- `dashboard/jarvis_web.py`: local HTTP server + canvas visualization
- Tests: `test_dashboard.py` (HTTP server starts, serves HTML, WebSocket connects)
- **Proof:** a test that starts the server, fetches the page, and asserts it contains the
  expected graph structure.

### Phase 7 — Planner Integration + Macro Crystallization
- Planner loads `patterns.json` and suggests macros
- Crystallization logic activates when same_pattern clusters reach threshold
- Tests: integration tests with the planner
- **Proof:** a test that creates 3 tasks with the same pattern, asserts a macro is created,
  then creates a 4th task and asserts the planner suggests the macro.

---

## 9. Data Flow Diagram

```
                    ┌─────────────┐
                    │   Planner   │
                    │ (writes     │
                    │  task_links)│
                    └──────┬──────┘
                           │ graph
                           ▼
                    ┌─────────────┐
                    │   Board     │ ← existing substrate
                    │ (task_links,│
                    │  status)    │
                    └──────┬──────┘
                           │ ready tasks
                           ▼
                    ┌─────────────┐
                    │ Dispatcher  │ ← existing
                    │ (claims,    │
                    │  launches)  │
                    └──────┬──────┘
                           │ task_id
                           ▼
              ┌────────────────────────┐
              │      Worker            │
              │                        │
              │  1. CLAIM              │
              │  2. LOAD CONTEXT ←─────│──── memory.procedural
              │     (rules, skills,    │     memory.context_budget
              │      compacted context)│     memory.semantic
              │  3. PRECHECK           │
              │  4. AGENT EXECUTION    │
              │  5. VERIFY             │
              │  6. ACCEPT / REVERT    │
              │  7. LEDGER WRITE       │
              └────────┬───────────────┘
                       │ run result
                       ▼
              ┌────────────────────────┐
              │   Ledger (JSONL)       │ ← existing, append-only
              └────────┬───────────────┘
                       │ sync
                       ▼
              ┌────────────────────────┐
              │  Episodic Memory       │
              │  (runs.db + summaries) │
              └────────┬───────────────┘
                       │ feeds
                       ▼
              ┌────────────────────────┐
              │  Semantic Memory       │
              │  (knowledge.json)      │──── context_budget.py
              └────────┬───────────────┘     builds context maps
                       │ triggers
                       ▼
              ┌────────────────────────┐
              │  Self-Evolution Loop   │
              │  (evolution.py)        │
              │                        │
              │  - post-mortem on fail │
              │  - create rule         │
              │  - crystallize pattern │
              └────────┬───────────────┘
                       │ writes
                       ▼
              ┌────────────────────────┐
              │  Procedural Memory     │
              │  (rules.json, skills/) │──── next worker reads
              └────────────────────────┘

              ┌────────────────────────┐
              │  JARVIS Dashboard      │──── reads board + ledger
              │  (terminal / web)      │     + memory system status
              └────────────────────────┘
```

---

## 10. VRAM Budget

The memory system adds negligible VRAM overhead:

| Component | VRAM | Notes |
|-----------|------|-------|
| SQLite (`runs.db`) | 0 | CPU-only, file I/O |
| JSON files | 0 | CPU-only, loaded on demand |
| Markdown files | 0 | CPU-only, read by workers |
| Context injection | 0 | Text prepended to prompt, no model overhead |
| Dashboard (terminal) | 0 | TUI rendering |
| Dashboard (web) | 0 | Local HTTP server, no GPU |
| Knowledge graph | 0 | JSON in memory, <1 MB |

The only VRAM cost is the text added to the agent's prompt (context injection). At L1 budget
(8 KB), this is negligible compared to the model's KV-cache. At L2 (32 KB), it's still under
5% of a 64K context window.

**Total memory system VRAM: 0 GB.** All CPU, all file I/O. The $0 cost constraint holds.
