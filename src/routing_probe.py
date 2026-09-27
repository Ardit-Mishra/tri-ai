"""Evidence-only probe for a local primary route and local fallback.

This module is deliberately not imported by the worker or executor.  It
measures a declared route policy through an injected transport; it does not
launch agents, read Hermes configuration, or accept board tasks.
"""

from __future__ import annotations

import ipaddress
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit


FALLBACK_REASONS = frozenset({"rate_limited", "timeout", "connection_error"})


@dataclass(frozen=True)
class Route:
    name: str
    endpoint: str
    model: str


@dataclass(frozen=True)
class RoutePolicy:
    version: str
    routes: Mapping[str, tuple[Route, ...]]


@dataclass(frozen=True)
class ProbeAttempt:
    route: str
    endpoint: str
    model: str
    result: str
    http_status: int | None
    fallback_reason: str | None = None


@dataclass(frozen=True)
class ProbeResult:
    policy_version: str
    task_kind: str
    attempts: tuple[ProbeAttempt, ...]

    def evidence(self) -> dict[str, Any]:
        return {
            "kind": "routing_probe",
            "accepted": False,
            "policy_version": self.policy_version,
            "task_kind": self.task_kind,
            "attempts": [asdict(attempt) for attempt in self.attempts],
        }


Transport = Callable[[Route], int]


def _is_loopback_endpoint(endpoint: str) -> bool:
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        return False
    if parsed.query or parsed.fragment or parsed.path.rstrip("/") != "/v1":
        return False
    host = parsed.hostname
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _route(raw: Mapping[str, Any], *, label: str) -> Route:
    name = raw.get("name")
    endpoint = raw.get("endpoint")
    model = raw.get("model")
    if not all(isinstance(value, str) and value.strip() for value in (name, endpoint, model)):
        raise ValueError(f"{label} route requires nonblank name, endpoint, and model")
    endpoint = endpoint.strip().rstrip("/")
    if not _is_loopback_endpoint(endpoint):
        raise ValueError(f"{label} endpoint must be a loopback OpenAI-compatible /v1 URL")
    return Route(name=name.strip(), endpoint=endpoint, model=model.strip())


def policy_from_mapping(raw: Mapping[str, Any]) -> RoutePolicy:
    version = raw.get("version")
    routes = raw.get("routes")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("routing policy requires a nonblank version")
    if not isinstance(routes, Mapping) or not routes:
        raise ValueError("routing policy requires at least one task-kind route")

    parsed: dict[str, tuple[Route, ...]] = {}
    for task_kind, spec in routes.items():
        if not isinstance(task_kind, str) or not task_kind.strip() or not isinstance(spec, Mapping):
            raise ValueError("routing policy has an invalid task-kind route")
        primary = spec.get("primary")
        legacy_fallback = spec.get("fallback")
        fallbacks = spec.get("fallbacks")
        if not isinstance(primary, Mapping):
            raise ValueError(f"{task_kind!r} requires a primary route")
        if legacy_fallback is not None and fallbacks is not None:
            raise ValueError(f"{task_kind!r} cannot mix fallback and fallbacks")
        if legacy_fallback is not None:
            if not isinstance(legacy_fallback, Mapping):
                raise ValueError(f"{task_kind!r} fallback must be a route object")
            fallback_specs: tuple[Mapping[str, Any], ...] = (legacy_fallback,)
        elif isinstance(fallbacks, list) and fallbacks and all(isinstance(item, Mapping) for item in fallbacks):
            fallback_specs = tuple(fallbacks)
        else:
            raise ValueError(f"{task_kind!r} requires one or more fallback routes")

        ordered = (_route(primary, label="primary"),) + tuple(
            _route(item, label=f"fallback {index}")
            for index, item in enumerate(fallback_specs, start=1)
        )
        endpoints = [route.endpoint for route in ordered]
        if len(set(endpoints)) != len(endpoints):
            raise ValueError(f"{task_kind!r} fallback endpoints must differ from every earlier route")
        parsed[task_kind.strip()] = ordered
    return RoutePolicy(version=version.strip(), routes=parsed)


def load_policy(path: Path | str) -> RoutePolicy:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"routing policy is not valid JSON: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ValueError("routing policy must be a JSON object")
    return policy_from_mapping(raw)


def _attempt(route: Route, transport: Transport, *, fallback_reason: str | None = None) -> ProbeAttempt:
    try:
        status = transport(route)
    except TimeoutError:
        return ProbeAttempt(route.name, route.endpoint, route.model, "timeout", None, fallback_reason)
    except OSError:
        return ProbeAttempt(route.name, route.endpoint, route.model, "connection_error", None, fallback_reason)
    if not isinstance(status, int):
        return ProbeAttempt(route.name, route.endpoint, route.model, "invalid_response", None, fallback_reason)
    if status == 429:
        return ProbeAttempt(route.name, route.endpoint, route.model, "rate_limited", status, fallback_reason)
    if 200 <= status < 300:
        return ProbeAttempt(route.name, route.endpoint, route.model, "responded", status, fallback_reason)
    return ProbeAttempt(route.name, route.endpoint, route.model, "http_error", status, fallback_reason)


def probe(policy: RoutePolicy, task_kind: str, transport: Transport) -> ProbeResult:
    """Probe a declared local route chain until one responds or a non-route fault stops it."""
    if task_kind not in policy.routes:
        raise ValueError(f"unknown task kind: {task_kind}")
    attempts: list[ProbeAttempt] = []
    previous: ProbeAttempt | None = None
    for route in policy.routes[task_kind]:
        attempt = _attempt(route, transport, fallback_reason=previous.result if previous else None)
        attempts.append(attempt)
        if attempt.result not in FALLBACK_REASONS:
            break
        previous = attempt
    return ProbeResult(policy.version, task_kind, tuple(attempts))


def record_evidence(result: ProbeResult, path: Path | str) -> Path:
    """Append diagnostic route evidence; it is not an acceptance ledger entry."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result.evidence(), sort_keys=True) + "\n")
    return target
