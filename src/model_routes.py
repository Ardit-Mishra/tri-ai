"""Operator-owned registry for exact, admitted Hermes model pins.

The registry carries no endpoint or credential. Hermes configuration owns both;
Tri-AI only records the tested model/provider identity selected for a task.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ModelRoute:
    name: str
    model: str
    provider: str | None
    admitted: bool


@dataclass(frozen=True)
class ModelRouteRegistry:
    version: str
    routes: Mapping[str, ModelRoute]


@dataclass(frozen=True)
class ModelRoutingPolicy:
    """Operator-owned selection policy over admitted named routes."""

    registry: ModelRouteRegistry
    default_route: str | None
    role_routes: Mapping[str, str]


def registry_from_mapping(raw: Mapping[str, Any]) -> ModelRouteRegistry:
    """Validate an operator-owned route registry without touching a runtime."""
    version = raw.get("version")
    entries = raw.get("routes")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("model routes require a nonblank version")
    if not isinstance(entries, Mapping) or not entries:
        raise ValueError("model routes require at least one named route")

    parsed: dict[str, ModelRoute] = {}
    for name, entry in entries.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(entry, Mapping):
            raise ValueError("model routes contain an invalid route")
        model = entry.get("model")
        provider = entry.get("provider")
        admitted = entry.get("admitted")
        if not isinstance(model, str) or not model.strip():
            raise ValueError(f"model route {name!r} requires a nonblank model")
        if provider is not None and (not isinstance(provider, str) or not provider.strip()):
            raise ValueError(f"model route {name!r} has an invalid provider")
        if not isinstance(admitted, bool):
            raise ValueError(f"model route {name!r} must declare admitted true or false")
        key = name.strip()
        if key in parsed:
            raise ValueError(f"duplicate model route {key!r}")
        parsed[key] = ModelRoute(key, model.strip(), provider.strip() if provider else None,
                                 admitted)
    return ModelRouteRegistry(version.strip(), parsed)


def resolve(registry: ModelRouteRegistry, name: str) -> ModelRoute:
    """Return an admitted route, refusing unknown or unproven identities."""
    route = registry.routes.get((name or "").strip())
    if route is None:
        raise ValueError(f"unknown model route {name!r}")
    if not route.admitted:
        raise ValueError(f"model route {route.name!r} is not admitted")
    return route


def policy_from_mapping(raw: Mapping[str, Any]) -> ModelRoutingPolicy:
    """Validate an optional default and per-role route selection policy."""
    registry = registry_from_mapping(raw)
    default_route = raw.get("default_route")
    if default_route is not None:
        if not isinstance(default_route, str) or not default_route.strip():
            raise ValueError("default_route must be nonblank text when configured")
        default_route = default_route.strip()
        resolve(registry, default_route)

    raw_roles = raw.get("role_routes", {})
    if not isinstance(raw_roles, Mapping):
        raise ValueError("role_routes must be an object")
    role_routes: dict[str, str] = {}
    for role, route_name in raw_roles.items():
        if not isinstance(role, str) or not role.strip():
            raise ValueError("role_routes contains an invalid role")
        if not isinstance(route_name, str) or not route_name.strip():
            raise ValueError(f"role route {role!r} must be nonblank text")
        key = role.strip()
        if key in role_routes:
            raise ValueError(f"duplicate role route {key!r}")
        route_name = route_name.strip()
        resolve(registry, route_name)
        role_routes[key] = route_name
    return ModelRoutingPolicy(registry, default_route, role_routes)


def select(policy: ModelRoutingPolicy, role: str | None) -> ModelRoute | None:
    """Choose an admitted route for a role, or preserve the default runtime."""
    route_name = policy.role_routes.get((role or "").strip()) or policy.default_route
    return resolve(policy.registry, route_name) if route_name else None
