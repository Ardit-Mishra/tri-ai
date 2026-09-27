"""Verifier-evidence admission for a declared local model route.

The component is intentionally outside the worker execution closure. It
evaluates supplied measurement records only; it cannot launch an agent,
contact an endpoint, read Hermes configuration, or accept a board task.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class RouteRequirement:
    route: str
    model: str
    max_resident_models: int
    max_vram_mib: int


@dataclass(frozen=True)
class AdmissionPolicy:
    version: str
    task_kinds: Mapping[str, RouteRequirement]


@dataclass(frozen=True)
class VerificationEvidence:
    fixture_id: str
    verify_exit: int
    seconds: float
    resolved_route: str
    resolved_model: str
    loaded_models: tuple[str, ...]
    peak_vram_mib: int
    agent_reported_success: bool = False


@dataclass(frozen=True)
class AdmissionResult:
    policy_version: str
    task_kind: str
    route: str | None
    model: str | None
    admitted: bool
    reason: str | None
    baseline: tuple[VerificationEvidence, ...]
    candidate: tuple[VerificationEvidence, ...]

    def evidence(self) -> dict[str, Any]:
        return {
            "kind": "route_admission",
            "policy_version": self.policy_version,
            "task_kind": self.task_kind,
            "route": self.route,
            "model": self.model,
            "admitted": self.admitted,
            "reason": self.reason,
            "baseline": [asdict(item) for item in self.baseline],
            "candidate": [asdict(item) for item in self.candidate],
        }


def policy_from_mapping(raw: Mapping[str, Any]) -> AdmissionPolicy:
    version = raw.get("version")
    task_kinds = raw.get("task_kinds")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("admission policy requires a nonblank version")
    if not isinstance(task_kinds, Mapping) or not task_kinds:
        raise ValueError("admission policy requires task kinds")

    parsed: dict[str, RouteRequirement] = {}
    for kind, item in task_kinds.items():
        if not isinstance(kind, str) or not kind.strip() or not isinstance(item, Mapping):
            raise ValueError("admission policy has an invalid task kind")
        route, model = item.get("route"), item.get("model")
        resident, vram = item.get("max_resident_models"), item.get("max_vram_mib")
        if not all(isinstance(value, str) and value.strip() for value in (route, model)):
            raise ValueError(f"{kind!r} requires a route and model")
        if not isinstance(resident, int) or resident < 1:
            raise ValueError(f"{kind!r} requires a positive resident-model cap")
        if not isinstance(vram, int) or vram < 1:
            raise ValueError(f"{kind!r} requires a positive VRAM cap")
        parsed[kind.strip()] = RouteRequirement(
            route=route.strip(),
            model=model.strip(),
            max_resident_models=resident,
            max_vram_mib=vram,
        )
    return AdmissionPolicy(version=version.strip(), task_kinds=parsed)


def _materialize(items: Iterable[VerificationEvidence], *, label: str) -> tuple[VerificationEvidence, ...]:
    records = tuple(items)
    fixture_ids = [item.fixture_id for item in records]
    if not records or any(not isinstance(value, str) or not value.strip() for value in fixture_ids):
        raise ValueError(f"{label} requires nonblank fixture evidence")
    if len(set(fixture_ids)) != len(fixture_ids):
        raise ValueError(f"{label} fixture ids must be unique")
    if any(item.seconds < 0 or item.peak_vram_mib < 0 for item in records):
        raise ValueError(f"{label} evidence has an invalid measurement")
    return records


def _refusal(
    policy: AdmissionPolicy,
    task_kind: str,
    requirement: RouteRequirement,
    baseline: tuple[VerificationEvidence, ...],
    candidate: tuple[VerificationEvidence, ...],
    reason: str,
) -> AdmissionResult:
    return AdmissionResult(
        policy.version,
        task_kind,
        requirement.route,
        requirement.model,
        False,
        reason,
        baseline,
        candidate,
    )


def admit(
    policy: AdmissionPolicy,
    task_kind: str,
    baseline: Iterable[VerificationEvidence],
    candidate: Iterable[VerificationEvidence],
) -> AdmissionResult:
    """Return an evidence-backed admission or an explicit refusal."""
    if task_kind not in policy.task_kinds:
        raise ValueError(f"unknown task kind: {task_kind}")
    requirement = policy.task_kinds[task_kind]
    baseline_records = _materialize(baseline, label="baseline")
    candidate_records = _materialize(candidate, label="candidate")
    baseline_ids = {item.fixture_id for item in baseline_records}
    candidate_ids = {item.fixture_id for item in candidate_records}
    if baseline_ids != candidate_ids:
        return _refusal(
            policy, task_kind, requirement, baseline_records, candidate_records,
            "candidate fixtures differ from baseline",
        )
    if any(item.verify_exit != 0 for item in baseline_records):
        return _refusal(
            policy, task_kind, requirement, baseline_records, candidate_records,
            "baseline verifier did not pass every fixture",
        )
    for item in candidate_records:
        if item.resolved_route != requirement.route:
            return _refusal(
                policy, task_kind, requirement, baseline_records, candidate_records,
                "candidate resolved an unapproved route",
            )
        if item.resolved_model != requirement.model:
            return _refusal(
                policy, task_kind, requirement, baseline_records, candidate_records,
                "candidate resolved an unapproved model",
            )
        if set(item.loaded_models) != {requirement.model}:
            return _refusal(
                policy, task_kind, requirement, baseline_records, candidate_records,
                "candidate loaded an unapproved model",
            )
        if len(item.loaded_models) > requirement.max_resident_models:
            return _refusal(
                policy, task_kind, requirement, baseline_records, candidate_records,
                "candidate exceeded the resident-model cap",
            )
        if item.peak_vram_mib > requirement.max_vram_mib:
            return _refusal(
                policy, task_kind, requirement, baseline_records, candidate_records,
                "candidate exceeded the VRAM cap",
            )
        if item.verify_exit != 0:
            return _refusal(
                policy, task_kind, requirement, baseline_records, candidate_records,
                "candidate verifier did not pass every fixture",
            )
    return AdmissionResult(
        policy.version,
        task_kind,
        requirement.route,
        requirement.model,
        True,
        None,
        baseline_records,
        candidate_records,
    )


def record_evidence(result: AdmissionResult, path: Path | str) -> Path:
    """Append route-admission evidence; it is not a board acceptance record."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result.evidence(), sort_keys=True) + "\n")
    return target
