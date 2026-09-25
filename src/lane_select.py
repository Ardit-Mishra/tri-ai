"""Choose the model lane for a node, and never depend on a paid one.

Three tiers, and the order between them is not a preference but a guarantee:

  local         deskollama/* on the always-on desktop. Uncapped, free, and the
                fastest lane measured (qwen2.5-coder:7b at 0.82s median against
                4.12s for the best cloud lane). This is the floor: a whole
                build must complete on it alone.
  free          OmniRoute and FreeLLMAPI aliases. Rate-limited, no money.
  subscription  claude, codex, gemini, grok. Highest capability, hard caps,
                and never load-bearing.

A task declares the lowest tier it needs; it never names a subscription. That
is what keeps "supercharge when you have one" from becoming "broken when you
do not".

Preference is declared per role, then overridden by what the ledger recorded.
The ledger stores *verify outcomes*, not transport results, so a lane that
returned HTTP 200 and produced an empty file counts as a failure here. That is
the signal no commercial router has: FreeLLMAPI's bandit learns from whether
the request succeeded, this learns from whether the work did.

Exhaustion is recorded, never retried into. Codex states its own reset time in
plain text - "try again at Sep 26th, 2026 11:21 AM" - so it is parsed and
stored. An unparseable message still benches the lane, because blind retry
against an exhausted subscription is how an unattended run spends an hour
achieving nothing.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

DEFAULT_LEDGER = Path.home() / ".tri-ai" / "ledger.jsonl"
DEFAULT_STATE = Path.home() / ".tri-ai" / "lane-state.json"

# Below this, a pass rate is noise rather than evidence.
MIN_SAMPLES = 5

# A measured lane must clear this to be chosen. Without it, a lane that failed
# 20 times out of 20 still wins for being the only one with enough samples -
# "best of a bad lot" read as "best". Evidence that a lane never works is a
# reason to avoid it, not to select it.
MIN_PASS_RATE = 0.5

TIER_ORDER = ("subscription", "free", "local")

# Lanes named on a CLI subscription rather than an API key. OmniRoute's
# provider_connections already draws the same line with auth_type=oauth.
SUBSCRIPTION_LANES = frozenset({
    "claude", "claude-code", "codex", "gemini", "gemini-cli", "grok",
})

LOCAL_PREFIXES = ("deskollama/", "ollama/", "local/")

# The floor, in preference order. Local last so it is the fallback of last
# resort rather than the first choice - but always present, so there is always
# something left.
DEFAULT_PERMITTED = (
    "auto/best-coding",
    "auto/cheap",
    "deskollama/qwen2.5-coder:14b",
    "deskollama/qwen2.5-coder:7b",
)

LAST_RESORT = "deskollama/qwen2.5-coder:7b"


@dataclass(frozen=True)
class Choice:
    lane: str
    tier: str
    reason: str
    samples: int = 0
    pass_rate: Optional[float] = None


def tier_of(lane: str) -> str:
    """Which tier a lane belongs to.

    An unrecognised lane is treated as free, never local. Guessing local would
    claim an uncapped lane the system does not actually have, and the cost of
    that error is a build that stalls waiting on nothing.
    """
    name = (lane or "").strip()
    if any(name.startswith(p) for p in LOCAL_PREFIXES):
        return "local"
    if name.casefold() in SUBSCRIPTION_LANES:
        return "subscription"
    return "free"


def declared_lane(role: str) -> str:
    """The lane a role prefers before any evidence."""
    try:
        import roles_lifecycle
        lane = roles_lifecycle.default_lane(role)
        if lane:
            return lane
    except ImportError:
        pass
    return LAST_RESORT


# --- exhaustion -------------------------------------------------------------

_RESET_PATTERNS = (
    # "try again at Sep 26th, 2026 11:21 AM"
    r"try again at\s+([A-Z][a-z]{2}\s+\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\s+"
    r"(\d{1,2}):(\d{2})\s*([AP]M)",
)


def parse_reset(message: str, *, now: Optional[float] = None) -> Optional[float]:
    """A provider's stated reset time as an epoch, or None if it gave none."""
    for pattern in _RESET_PATTERNS:
        m = re.search(pattern, message or "", re.IGNORECASE)
        if not m:
            continue
        day, year, hour, minute, meridiem = m.groups()
        hour = int(hour) % 12 + (12 if meridiem.upper() == "PM" else 0)
        try:
            when = datetime.strptime(f"{day} {year} {hour}:{minute}", "%b %d %Y %H:%M")
        except ValueError:
            continue
        return when.timestamp()
    return None


def _load_state(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


# A provider that gives no reset time is benched for this long. Long enough
# that an unattended run stops hammering it, short enough to recover the lane
# within a working day.
DEFAULT_COOLDOWN_SECONDS = 3600.0


def mark_exhausted(
    lane: str, message: str, *, state_path: Path = DEFAULT_STATE,
    available_after: Optional[float] = None, now: Optional[float] = None,
) -> float:
    """Bench a lane until its stated reset, or a default cooldown."""
    current = now if now is not None else time.time()
    if available_after is None:
        available_after = parse_reset(message, now=current)
    if available_after is None:
        available_after = current + DEFAULT_COOLDOWN_SECONDS

    state = _load_state(state_path)
    state.setdefault("lanes", {})[lane] = {
        "available_after": available_after,
        "recorded_at": current,
        "message": (message or "")[:200],
    }
    _save_state(state_path, state)
    return available_after


def is_available(lane: str, *, state_path: Path = DEFAULT_STATE,
                 now: Optional[float] = None) -> bool:
    entry = _load_state(state_path).get("lanes", {}).get(lane)
    if not entry:
        return True
    return (now if now is not None else time.time()) >= entry.get("available_after", 0)


# --- measured preference ----------------------------------------------------

def _outcomes(ledger_path: Path, role: str) -> dict[str, tuple[int, int]]:
    """{lane: (passes, attempts)} for one role, from verify outcomes."""
    stats: dict[str, list[int]] = {}
    try:
        text = ledger_path.read_text(encoding="utf-8")
    except OSError:
        return {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("agent_role") != role:
            continue
        lane = row.get("model")
        outcome = row.get("outcome")
        # Only decided runs count. skipped/blocked/quarantined say nothing
        # about the lane.
        if not lane or outcome not in ("passed", "failed"):
            continue
        entry = stats.setdefault(lane, [0, 0])
        entry[1] += 1
        if outcome == "passed":
            entry[0] += 1
    return {k: (v[0], v[1]) for k, v in stats.items()}


def choose(
    role: str,
    *,
    ledger_path: Path = DEFAULT_LEDGER,
    state_path: Path = DEFAULT_STATE,
    permitted: Optional[Sequence[str]] = None,
    min_samples: int = MIN_SAMPLES,
    min_pass_rate: float = MIN_PASS_RATE,
    now: Optional[float] = None,
) -> Choice:
    """Pick a lane for `role`: measured where there is evidence, declared where not."""
    allowed = list(permitted or DEFAULT_PERMITTED)
    usable = [l for l in allowed if is_available(l, state_path=state_path, now=now)]

    stats = _outcomes(Path(ledger_path), role)
    ranked = [
        (lane, passes / attempts, attempts)
        for lane, (passes, attempts) in stats.items()
        if attempts >= min_samples
        and passes / attempts >= min_pass_rate
        and is_available(lane, state_path=state_path, now=now)
    ]
    if ranked:
        # Best pass rate; ties broken by the larger sample, then by cheaper tier.
        ranked.sort(key=lambda r: (-r[1], -r[2], TIER_ORDER.index(tier_of(r[0]))))
        lane, rate, attempts = ranked[0]
        return Choice(lane, tier_of(lane), "measured", attempts, rate)

    declared = declared_lane(role)
    if is_available(declared, state_path=state_path, now=now):
        return Choice(declared, tier_of(declared), "declared default")

    if usable:
        # Degrade rather than block: prefer the cheapest tier still standing.
        usable.sort(key=lambda l: TIER_ORDER.index(tier_of(l)), reverse=True)
        lane = usable[0]
        return Choice(lane, tier_of(lane), f"degraded - {declared} exhausted")

    # Everything is benched. Return the local floor anyway and say so: a lane
    # that might be rate-limited beats returning nothing and stalling the graph.
    return Choice(LAST_RESORT, tier_of(LAST_RESORT),
                  "all permitted lanes exhausted - falling back to local")


def apply(graph: dict[str, Any], **kwargs: Any) -> dict[str, str]:
    """Stamp each node with its chosen lane. Returns {node_key: lane}."""
    chosen: dict[str, str] = {}
    for node in graph.get("nodes", []):
        pick = choose(node.get("agent_role", "builder"), **kwargs)
        node["lane"] = pick.lane
        node["lane_tier"] = pick.tier
        node["lane_reason"] = pick.reason
        chosen[node.get("node_key", "?")] = pick.lane
    return chosen


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Choose model lanes for graph nodes.")
    ap.add_argument("--role", help="report the lane for one role")
    ap.add_argument("--graph", help="stamp a graph JSON in place")
    ap.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    args = ap.parse_args(argv)

    if args.graph:
        p = Path(args.graph)
        graph = json.loads(p.read_text(encoding="utf-8"))
        picked = apply(graph, ledger_path=Path(args.ledger))
        p.write_text(json.dumps(graph, indent=2), encoding="utf-8")
        for key, lane in picked.items():
            print(f"  {key:<28} {lane}")
        print(f"{len(picked)} nodes stamped -> {p}")
        return 0

    role = args.role or "builder"
    pick = choose(role, ledger_path=Path(args.ledger))
    print(f"role    : {role}")
    print(f"lane    : {pick.lane}  ({pick.tier})")
    print(f"reason  : {pick.reason}")
    if pick.pass_rate is not None:
        print(f"evidence: {pick.pass_rate:.0%} over {pick.samples} runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
