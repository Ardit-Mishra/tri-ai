# Portfolio Strategy — superseded pointer

**This file is not the portfolio strategy.** The authoritative comparative audit is:

> `C:\Users\ardit\agent-os\PORTFOLIO-DELIVERABLE-D-PROJECT-AUDIT.md`

An earlier version of this file was written on 2026-09-12 without reading the
established portfolio process in `agent-os/`, and it was wrong in ways worth
recording so the mistake is not repeated:

- It implied Tri-AI runs or produced the other four projects. The handoff forbids
  exactly that — the three independent tools are Jan–Aug 2025 work, predating Tri-AI
  by a year. Genclarus is the only product built through Tri-AI.
- It reported "Live — HTTP 200" for peptidemhc, genomesight and biostudio. Those
  custom domains resolve to `34.111.179.208` and are served by **Replit**, from builds
  dated 2025-11 to 2026-02. A 200 proves something answers, not that it is your code.
- It invented a competency framing instead of using the seven opportunity types
  already fixed in `PORTFOLIO-REDESIGN-HANDOFF.md` §4, and dropped the revenue half of
  the dual goal recorded in `ARDIT-PROFILE.md`.
- It omitted the portfolio site and `ml-training` entirely.
- It described BioStudio's stack from a stale assumption. BioStudio is still Streamlit;
  the Streamlit→FastAPI+React migration was **GenomeSight** (`84a2459`, `13d0a07`).

`agent-os/` holds the single source of truth for portfolio positioning: locked
Deliverable A (brand), verified B (chronology), draft C (relationship map), D (this
audit), the résumé alignment check, and the hosting/routing audit. Do not maintain a
competing copy here — `ARDIT-PROFILE.md` warns that divergent copies drift, and this
file is the proof.

## What belongs in this repository

Only the Tri-AI-side operational facts:

- **`genclarus` is a registered governed workspace.** `~/.tri-ai/intake_policy.json`
  maps alias `genclarus` → `C:\Users\ardit\projects\genelens` with verify profile
  `genclarus-vitest` (`npm test`, 600s), measured before registration at 36 files /
  333 tests / exit 0 / 4.17s. `default_workspace` remains `tri-ai`.
- **The canonical local copies** are `C:\Users\ardit\Downloads\live-projects\*` and
  `C:\Users\ardit\projects\genelens`. `agent-os/PORTFOLIO-INVENTORY.md` still points at
  `Downloads\Portoflio Management\…`, which last committed Feb–Mar 2026; the
  live-projects copies are Sep 2026 and are what the operator's own `queue.jsonl`
  targets. That inventory needs correcting at source.
- **Clean-tree precheck.** Any Tri-AI run against a workspace fails its precheck while
  that workspace's tree is dirty — the same precheck that stranded `t_69cc6245`.
