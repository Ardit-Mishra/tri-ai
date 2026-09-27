"""Publish a redacted node heartbeat to the public status collector.

The local dashboard reads everything: task titles, the prompts that produced
them, workspace paths, log tails. None of that can leave the machine. So the
public payload is built from an **allowlist** - this module names the fields
that may be published rather than stripping the ones that may not. A field
added to ``DashboardSnapshot`` later is therefore excluded by default, instead
of leaking on the next deploy because nobody remembered to redact it.

The transport is push, not pull. The nodes sit behind Tailscale, so a hosted
collector cannot reach in to poll them; each node posts on its own schedule
over an outbound connection. That also makes absence the signal: a node is
shown as down because its heartbeat stopped arriving, not because some probe
claimed it was unreachable. There is nothing to trust except the clock.

Every request is signed with HMAC-SHA256 over the exact bytes sent. The secret
is read from the environment, never from a config file in the repo, and never
appears in the payload.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from dashboard import kaya_terminal
else:
    from .dashboard import kaya_terminal


SIGNATURE_HEADER = "X-Tri-AI-Signature"
NODE_HEADER = "X-Tri-AI-Node"
SECRET_ENV = "TRI_AI_BEACON_SECRET"

# A payload older than this is treated as a dead node by the collector and the
# page. It is deliberately a small multiple of the posting interval: long
# enough to survive one missed post, short enough that a wedged node does not
# keep showing green.
DEFAULT_STALE_AFTER_S = 150.0


class BeaconError(RuntimeError):
    """The heartbeat could not be built or delivered."""


def _task_counts(snapshot: kaya_terminal.DashboardSnapshot) -> dict[str, int]:
    """Count tasks by status.

    Counts only. A status string is a closed vocabulary the board controls; a
    title is free text a stranger wrote over Telegram.
    """
    return dict(sorted(Counter(task.status for task in snapshot.tasks).items()))


def _daemon_view(health: kaya_terminal.DaemonHealth) -> dict[str, Any]:
    """Process names and liveness, without the diagnostic string.

    ``diagnostic`` explains *why* a daemon is unhealthy and quotes paths and
    command lines to do it. That is exactly the useful part locally and exactly
    the part that cannot be public, so the page gets the state and not the
    reason.
    """
    return {
        "status": health.status,
        "processes": [{"name": name, "alive": bool(alive)} for name, alive in health.processes],
    }


def public_payload(
    snapshot: kaya_terminal.DashboardSnapshot,
    *,
    node: str,
    role: str,
    models: Sequence[str] = (),
    now: Optional[float] = None,
) -> dict[str, Any]:
    """Build the one dict this node is allowed to publish.

    Everything here is either a number, a fixed vocabulary string, or a name
    the operator chose (the node label, the model list). Nothing is free text
    that arrived from outside the machine.
    """
    if not node:
        raise BeaconError("a node label is required")
    return {
        "node": node,
        "role": role,
        "generated_at": float(now if now is not None else time.time()),
        "tasks": _task_counts(snapshot),
        "task_total": len(snapshot.tasks),
        "daemons": _daemon_view(snapshot.daemons),
        "ledger_entries": int(snapshot.ledger_entry_count),
        "activated_rules": int(snapshot.activated_rule_count),
        "source_errors": len(snapshot.ledger_errors) + len(snapshot.memory_errors),
        "models": [str(name) for name in models],
    }


def sign(body: bytes, secret: str) -> str:
    """HMAC-SHA256 over the exact bytes that go on the wire."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify(body: bytes, secret: str, signature: str) -> bool:
    """Constant-time signature check, for the collector side."""
    return hmac.compare_digest(sign(body, secret), (signature or "").strip())


def encode(payload: Mapping[str, Any]) -> bytes:
    """Serialize deterministically so the signature covers a stable encoding."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def post(
    url: str,
    payload: Mapping[str, Any],
    *,
    secret: str,
    timeout: float = 10.0,
    opener=urllib.request.urlopen,
) -> int:
    """Send one heartbeat. Returns the HTTP status code."""
    if not secret:
        raise BeaconError(f"{SECRET_ENV} is not set; refusing to post unsigned")
    body = encode(payload)
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            SIGNATURE_HEADER: sign(body, secret),
            NODE_HEADER: str(payload.get("node", "")),
        },
    )
    try:
        with opener(request, timeout=timeout) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        raise BeaconError(f"collector rejected the heartbeat: {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise BeaconError(f"collector unreachable: {exc.reason}") from exc


def _read_models(command: Optional[str]) -> list[str]:
    """Model names from a newline-separated file the node maintains.

    Reading a file rather than shelling out keeps this module free of process
    spawning, which the repo's AST test proves happens in exactly two audited
    functions elsewhere.
    """
    if not command:
        return []
    path = Path(command)
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Post a redacted Tri-AI node heartbeat.")
    parser.add_argument("--url", required=True, help="collector endpoint, e.g. https://host/api/beacon")
    parser.add_argument("--node", required=True, help="node label, e.g. desktop")
    parser.add_argument("--role", default="worker", help="router | worker | interface")
    parser.add_argument("--runtime-root", default=str(Path.home() / ".tri-ai"))
    parser.add_argument("--models-file", default=None, help="newline-separated model names")
    parser.add_argument("--dry-run", action="store_true", help="print the payload, post nothing")
    args = parser.parse_args(argv)

    paths = kaya_terminal.runtime_paths(args.runtime_root)
    try:
        snapshot = kaya_terminal.read_snapshot(
            board_path=paths["board_path"],
            ledger_path=paths["ledger_path"],
            daemon_state_path=paths["daemon_state_path"],
        )
    except kaya_terminal.DashboardSourceError as exc:
        print(f"beacon: cannot read local state: {exc}", file=sys.stderr)
        return 2

    payload = public_payload(
        snapshot,
        node=args.node,
        role=args.role,
        models=_read_models(args.models_file),
    )

    if args.dry_run:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    try:
        post(args.url, payload, secret=os.environ.get(SECRET_ENV, ""))
    except BeaconError as exc:
        print(f"beacon: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
