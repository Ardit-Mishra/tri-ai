# Tri-AI Brain Architecture

Tri-AI and the dashboard are one product. Tri-AI is the governed execution
engine; the dashboard is its read-only control and evidence surface. The
visible `KAYA` label is temporary and does not name a second runtime.

## What the Brain is

The Brain is a local-first context system under `~/.tri-ai/brain/`. It stores
the minimum durable information needed to continue work across agents,
sessions, projects, and machines:

- an append-only inbox receipt for every explicit capture;
- deduplicated memory items with source, project, kind, trust, and time;
- full-text retrieval with bounded context packs;
- typed graph edges whose evidence is another stored item;
- existing citation-backed episodic, semantic, and procedural memory;
- no passwords, tokens, cookies, private keys, or execution authority.

Captured information begins as `unreviewed`. It may inform a prompt when its
trust label and citation remain visible, but it cannot become a procedural
rule, permission, deployment decision, or verifier result by itself.

## Storage and deployment

The authoritative store is local SQLite plus content-addressed files on the
machine that runs Tri-AI. SQLite is small, transactional, inspectable, and
works without a network service. A VPS can later host the same kernel behind
an authenticated private interface when continuous availability is worth the
additional operational and privacy surface.

Google Drive is a backup and archive target, not the live database. Drive's
resumable upload path is suitable for large snapshots, but concurrent agents
must not edit a SQLite database through a synced folder. Backups should be
closed, checksummed snapshots, encrypted before upload, and periodically
restored in a test directory.

## One inbox, several specialist indexes

| Component | Job | Authority |
|---|---|---|
| Brain inbox | Capture operator notes, agent handoffs, decisions, sources, and artifacts | Unreviewed context |
| Ledger / episodic memory | Record what actually ran and how verification ended | Evidence |
| Procedural memory | Propose bounded preflight checks from repeated verified failures | Operator-approved advice only |
| Codebase Memory MCP | Structural code search, call graphs, impact analysis | Read-only code intelligence |
| Graphify | Project planning and repository knowledge graph | Rebuildable derived index |
| Graft | Budgeted, secret-aware source reads | Context governor |
| Context7 | Current third-party library documentation | Ephemeral external reference |
| Obsidian | Human-readable notes and graph navigation | Review surface, not runtime state |
| AgentMemory | Optional semantic/vector enrichment and cross-harness recall | Derived adapter, never policy |

This avoids pretending that one database can answer every retrieval problem.
The Brain routes each question to the right index and assembles one bounded,
provenance-labeled context pack.

## Agent learning loop

1. Capture an observation in the inbox.
2. Deduplicate it and retain every receipt.
3. Link it to tasks, projects, people, artifacts, sources, or prior memories.
4. Retrieve it only when a task's declared scope matches.
5. Compare the task result with verifier and ledger evidence.
6. Propose a lesson when evidence repeats.
7. Replay the proposed lesson against held-out runs.
8. Require operator review before activation as procedural advice.

"Self-learning" therefore means improving retrieval and proposing evidence-
backed lessons. It never means silently rewriting its own permissions,
verification commands, or operating rules.

## Repository decisions

- `rohitg00/agentmemory`: useful optional enrichment. It provides BM25,
  local embeddings, graph recall, and broad MCP/client support. Native Windows
  adds an engine, hooks, four ports, and another lifecycle, so it should attach
  behind a scoped adapter after retention and ownership are decided.
- `flyingrobots/graft`: adopt for context governance and structural history,
  not as the durable memory store.
- `DeusData/codebase-memory-mcp`: adopt for code structure and impact queries,
  not personal or operational memory.
- `msitarzewski/agency-agents`: use selected specialist roles as task guidance;
  they receive only capability-scoped context and cannot accept their own work.
- Graphiti, Cognee, and Neo4j Agent Memory: evaluate later for temporal and
  semantic graph enrichment. They add useful hybrid retrieval, but also a
  database/model/runtime dependency that the local kernel does not need for
  its first reliable slice.
- TypeSafe Jev: optional decision adapter for typed classification, ranking,
  tool selection, risk scoring, and confidence-based escalation. It is not a
  generator, agent, memory system, or verifier. Every threshold needs a local
  labeled evaluation set and a deterministic fallback.

## Phone path

Telegram remains the primary phone interface. `/remember <note>` writes an
unreviewed Brain inbox item, and `/recall <query>` returns a bounded context
pack with trust and citations. Task creation remains confirmation-gated.

Tailscale is the private route for reaching the dashboard or a future mobile
command surface on this PC. It is not required for Telegram's Bot API traffic.
If Telegram is unavailable, the fallback should be an authenticated command
surface over Tailscale that calls the same intake adapter, never a second task
or memory system.

## Sources reviewed

- Graphiti: https://github.com/getzep/graphiti
- Cognee: https://github.com/topoteretes/cognee
- Neo4j Agent Memory: https://github.com/neo4j-labs/agent-memory
- AgentMemory: https://github.com/rohitg00/agentmemory
- Graft: https://github.com/flyingrobots/graft
- Codebase Memory MCP: https://github.com/DeusData/codebase-memory-mcp
- Google Drive resumable uploads:
  https://developers.google.com/workspace/drive/api/guides/manage-uploads
- Jev measured examples: https://github.com/WallerChen/jev-measured
- Jev confidence-gated use cases: https://github.com/kenhuangus/jev-usecases

