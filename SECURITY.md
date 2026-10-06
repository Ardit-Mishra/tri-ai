# Security Policy

## Public release boundary

Tri-AI is designed local-first. The public repository and public dashboard demo
must never contain or expose:

- credentials, API keys, cookies, tokens, or private keys;
- a live task board, ledger, worktree, run log, artifact, or memory database;
- personal filesystem paths, machine names, private addresses, or network topology;
- an endpoint that can enqueue, execute, approve, deploy, or otherwise control work.

The hosted dashboard runs with `--demo`. It serves a fixed, visibly labeled
scenario only. It does not open the local runtime database, call a model
provider, receive an agent task, or use deployment credentials.

## How the boundary is enforced

That is enforced, not promised. `tests/test_public_release_boundary.py` reads the Git index and
fails on any tracked file carrying an operator identity, matched by shape: a machine-name pattern,
a private address range, and a home-directory path derived from whoever runs the check. No real
value is ever written down to search for. `scripts/release_guard.py` runs on `pre-push` (installed
from `scripts/hooks/pre-push`) and refuses to push a branch to a public remote unless its
`RELEASE_SCOPE` file declares it public.

## Reporting a vulnerability

Do not open a public issue containing a secret, exploit payload, private URL,
or personal data. Use GitHub's private security advisory flow for this
repository and include a minimal reproduction and affected revision.

## Operational guidance

Run the real system from an environment you control. Keep runtime state outside
the clone, use environment variables for credentials, and review public diffs
before every push. The production engine requires an explicit human approval
before public actions such as deploying or changing DNS.
