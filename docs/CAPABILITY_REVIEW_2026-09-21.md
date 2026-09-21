# Tri-AI Capability Review

This is a source-and-license review of the operator's requested capability
list. A repository being useful does not make it safe to run inside a worker.
Tri-AI preserves its verify gate and starts external capabilities only when an
operator explicitly grants the required scope.

## Activated

The following portable, permissively licensed source trees are pinned under
`~/.tri-ai/capabilities/sources` and selected skills are junction-linked into
Codex, Claude Code, and this repository's `.agents/skills`.

| Requested capability | Reviewed upstream | Tri-AI use | Status |
|---|---|---|---|
| Scientific agent skills | `K-Dense-AI/scientific-agent-skills` | bioinformatics, literature, cheminformatics, and molecular work | Active selected skills |
| Diagram design | `cathrynlavery/diagram-design` | architecture and evidence diagrams | Active |
| Anthropic cybersecurity skills | `disconnectuss/anthropic-cybersecurity-skills` | defensive reviews only: SBOM, cloud/IAM, Terraform, and threat modeling | Active selected skills |
| Agency agents | `msitarzewski/agency-agents` | role library for explicitly selected task personas | Source staged |
| Design motion principles | `kylezantos/design-motion-principles` | purposeful product motion and motion review | Active |
| Taste skill | `irfanfaraaz/taste-skill-plugin` | frontend visual critique | Active |
| UI UX Pro Max | `nextlevelbuilder/ui-ux-pro-max-skill` | already present for both coding clients | Already active |
| Archify | `tt-a1i/archify` | deterministic system-map rendering | Active |
| Agent skills | `SteveVitali/agent-skills` | plan, implementation, review, reconciliation | Active selected skills |
| Marketing skills | `coreyhaines31/marketingskills` | research, positioning, content, SEO, and launch work | Active selected skills |
| Browser Use | `browser-use/browser-use` | browser automation in a separate local Python runtime | Installed, capability-gated |
| Codebase Memory MCP | `DeusData/codebase-memory-mcp` | structural code retrieval through MCP | Installed; restart clients to load |

`scripts/activate-agent-capabilities.ps1` is idempotent: it refuses to
overwrite a pre-existing skill and only links the reviewed source directories.

## Installed, Not Yet Activated

| Requested capability | Reviewed upstream | Why it is not yet a worker dependency |
|---|---|---|
| AgentMemory | `rohitg00/agentmemory` | Its Windows engine and lifecycle hooks need an operator decision on retention, transcript capture, and hook ownership. Tri-AI already has durable task and ledger state. |
| Graft | `flyingrobots/graft` | Context governor/MCP must not override Tri-AI's worktree and verification rules without a scoped adapter. |
| Voicebox | `jamiepine/voicebox` | Requires microphone, voice-data retention, model-download, and voice-cloning consent. |
| Agent Reach | `Panniantong/Agent-Reach` | Web/social extraction must have an allowlist, terms review, and rate policy before it may act for a business. |
| Orca | `orca-cli/orca` | A second multi-agent worktree orchestrator; use only after deciding whether it replaces, rather than competes with, Tri-AI's board. |
| OpenHands | `OpenHands/software-agent-sdk` | A separate agent platform requiring a sandbox/runtime plan. It should be evaluated as an adapter, not spawned recursively. |

## Deliberately Not Embedded

| Requested capability | Reviewed upstream | Decision |
|---|---|---|
| OpenViking | `unitsvc/openviking` | AGPL-3.0: do not embed it in a commercial Tri-AI runtime without a licensing decision. |
| OpenMontage | `calesthio/OpenMontage` | AGPL-3.0 and a media-production system, not a task-worker library. |
| VoiceStudio | `niaodian/voicestudio` | AGPL-3.0 plus heavy local model/data considerations. |
| Harness Engineering | `ArtemisAI/Harness_Engineering` | No declared license at the reviewed revision; use as research only. |
| Remotion agent skills | `phamthanhnghia/remotion-agent-skills` | No declared license at the reviewed revision. Existing Remotion skills remain available. |
| Agent Canvas | `OpenHands/agent-canvas` | Archived upstream; do not build new operational state around it. |
| OpenMAIC | `THU-MAIC/OpenMAIC` | Interactive classroom product, not an execution capability for Tri-AI. |
| MiniMind | `jingyaogong/minimind` | Educational/training project, not a dependable production model lane. |
| Ponytail | `DietrichGebert/ponytail` | Pi-agent plugin, not compatible with Tri-AI's worker runtime without an adapter. |
| Awesome Claude Design | `VoltAgent/awesome-claude-design` | A design-reference catalog, not an executable dependency. |

## FreeLLMAPI

FreeLLMAPI is installed and listens loopback-only at `http://127.0.0.1:3001`.
Its router is deliberately not configured into Codex, Claude Code, or Hermes
until its local dashboard has an operator-created account, a unified API key,
and provider credentials. Pointing coding clients at an empty router would
break the working model lanes rather than add a fallback.

After dashboard setup, the safe sequence is:

```powershell
[Environment]::SetEnvironmentVariable("FREELLMAPI_API_KEY", "<unified-key>", "User")
cd C:\Users\ardit\tri-ai
.\scripts\configure-freellmapi-clients.ps1
.\scripts\configure-freellmapi-clients.ps1 -Apply
```

The first command is a dry run. The second alters client configuration only
after the operator has confirmed the router has a usable provider route.

## Browser-Use Boundary

Browser Use is installed in `~/.tri-ai/runtimes/browser-use`. Chromium launches
headlessly in a local smoke test. It has no imported cookies, no browser
profile, no model credential, and no permission to carry out purchases, account
changes, deployments, or external posting. Any future Tri-AI browser task must
declare its allowed domains and an independently verifiable outcome.
