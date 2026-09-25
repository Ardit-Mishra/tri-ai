# Tri-AI Technology Radar

The radar keeps Tri-AI aware of useful new tools without granting newly found
code ambient authority.

## Discovery

- GitHub repository search supplies code candidates and repository metadata.
- Hacker News supplies community and launch signals.
- YouTube supplies demonstration and research leads when `YOUTUBE_API_KEY` is
  configured. A video is never itself a software admission candidate.
- Low adoption does not disqualify a candidate. Recency, license, activity,
  adoption, and discussion are evidence fields for evaluation.
- Exact `upstream_url` values in `config/capability-adapters.json` are permanent
  operator seeds. They enter every report even when generic search would miss
  them, and bounded evaluation rotates through that requested backlog weekly.

## Evaluation Boundary

1. Download a canonical HTTPS GitHub archive without Git hooks or filters.
2. Reject traversal paths, symlinks, and archives over configured limits.
3. Scan manifests, lifecycle hooks, scripts, and executable artifacts.
4. Block dynamic evaluation on critical static findings.
5. Probe only in Docker with no network, a read-only root and workspace,
   dropped capabilities, no new privileges, bounded memory/CPU/PIDs, and a
   temporary filesystem.
6. Never fall back to host execution when Docker is unavailable.

Node candidates receive a non-installing `package.json` parse. Python
candidates receive syntax compilation. Unknown project types receive only a
readable-metadata check. Passing this probe means “candidate ready for adapter
review,” not “trusted” and not “installed.”

## States

- `research-lead`: useful external signal, not executable source.
- `manual-security-review`: static findings need a person to inspect them.
- `probe-incomplete`: isolation was unavailable or the bounded probe failed.
- `candidate-ready-for-adapter-review`: static and isolated checks passed.
- `evaluation-failed`: acquisition or inspection failed safely.

Only an operator-reviewed entry in `config/capability-adapters.json` makes a
candidate routable. Permissions and external actions remain independently
gated.

## Running

```powershell
.\scripts\run-technology-radar.ps1
```

The runner refreshes the dynamic capability catalog first and writes the
machine-readable report to `~/.tri-ai/radar/latest.json`.

The weekly scheduler can be registered after the branch is placed at its
durable checkout path:

```powershell
.\scripts\register-technology-radar.ps1
```

The default schedule is Sunday at 06:00, starts missed runs when possible,
ignores overlapping instances, runs with limited user privileges, and stops
after four hours. Telegram `/radar` shows the latest evaluation summary.
