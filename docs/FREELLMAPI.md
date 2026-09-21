# FreeLLMAPI Route — Tri-AI Integration

Tri-AI can route a verifiable agent task through three independent local
OpenAI-compatible endpoints:

```text
OmniRoute :20128/v1 -> FreeLLMAPI :3001/v1 -> Ollama :11434/v1
```

The order is deliberate. OmniRoute remains the existing primary; FreeLLMAPI is
an opt-in router assembled from provider free tiers; local Ollama is the final
offline-capable fallback. A routing probe advances only after a rate limit,
timeout, or connection error. It records every attempt as diagnostic evidence.
It does not accept a task, alter a board row, or launch an agent.

## What the FreeLLMAPI claim means

At the reviewed upstream revision `b882473c3a23251be312a7270e2e0dc1eae1329d`,
FreeLLMAPI advertises roughly 7.4 billion monthly tokens across its listed free
tiers. That is a catalog estimate, not a Tri-AI guarantee: availability,
provider eligibility, rate limits, and model quality all vary. Tri-AI does not
store or report that number as an owned quota, and it never treats a successful
agent report as task success. The verifier's exit status remains the only
acceptance signal.

## Local installation

Run from the Tri-AI checkout:

```powershell
.\scripts\install-freellmapi.ps1
.\scripts\install-freellmapi.ps1 -Start
```

The installer pins the reviewed source revision, binds the router to loopback,
generates a local encryption key only when the service has no `.env`, and does
not add provider credentials. Open `http://127.0.0.1:3001`, add only accounts
you own, and create a unified API key there. Do not place that key in this
repository, a task prompt, or a routing policy.

After the router is healthy, store the unified key as the user-level
`FREELLMAPI_API_KEY` environment variable and preview client changes:

```powershell
.\scripts\configure-freellmapi-clients.ps1
```

Only apply after reviewing the generated changes:

```powershell
.\scripts\configure-freellmapi-clients.ps1 -Apply
```

The setup uses the upstream CLI's structural configuration generators for
Claude Code, Codex, and Hermes. Credentials remain in the user environment or
the router's encrypted local store; none are written into Tri-AI.

## Probe policy

Copy `config/routing-probe.example.json` outside the repository, fill in only
approved model identifiers, and probe through `src/routing_probe.py`. The
policy accepts loopback `/v1` endpoints only and rejects embedded credentials,
duplicate endpoints, and mixed legacy/chain fallback definitions.
