"""Read-only inventory and task matching for Tri-AI capability resources.

The catalog indexes skills, cloned source repositories, and configured MCP
servers without moving, deleting, activating, or executing any of them.  It is
the lookup layer between a task's intent and the large tool estate on disk.
"""

from __future__ import annotations

import hashlib
import argparse
import json
import re
import shutil
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Sequence


CATALOG_SCHEMA = "triai.capability-catalog.v1"
DEFAULT_CATALOG_PATH = Path.home() / ".tri-ai" / "capabilities" / "catalog.json"
DEFAULT_ADAPTER_MANIFEST = Path(__file__).resolve().parents[1] / "config" / "capability-adapters.json"


@dataclass(frozen=True)
class CapabilityResource:
    resource_id: str
    name: str
    kind: str
    origin: str
    description: str
    path: Optional[Path]
    availability: str
    instruction_ready: bool
    license_hint: Optional[str] = None
    adapter_status: str = "unadapted"
    entrypoint: Optional[str] = None
    permission_scopes: tuple[str, ...] = ()
    risk_status: str = "unreviewed"
    tags: tuple[str, ...] = ()
    health_status: str = "not-checked"
    upstream_url: Optional[str] = None


@dataclass(frozen=True)
class ResourceMatch:
    resource: CapabilityResource
    score: int
    reason: str


_WORD = re.compile(r"[a-z0-9][a-z0-9+#._-]*")
_STOP = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "build", "by", "do",
    "for", "from", "in", "into", "is", "it", "of", "on", "or", "the",
    "then", "this", "to", "use", "with",
})


def default_skill_roots(repo_root: Optional[Path] = None) -> tuple[Path, ...]:
    roots = [
        Path.home() / ".claude" / "skills",
        Path.home() / ".claude" / "skills-archive",
        Path.home() / ".codex" / "skills",
        Path.home() / ".agents" / "skills",
    ]
    if repo_root is not None:
        roots.insert(0, Path(repo_root) / ".agents" / "skills")
    return tuple(roots)


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    result: dict[str, str] = {}
    section = ""
    for line in text[3:end].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0 and ":" in line:
            key, value = line.split(":", 1)
            section = key.strip()
            value = value.strip().strip("'\"")
            if value:
                result[section] = value
        elif indent and section == "metadata" and ":" in line:
            key, value = line.split(":", 1)
            result[f"metadata.{key.strip()}"] = value.strip().strip("'\"")
    return result


def _availability(path: Path) -> str:
    lowered = {part.lower() for part in path.parts}
    if "skills-archive" in lowered:
        return "archived-reference"
    if "skills" in lowered:
        return "active"
    return "available"


def _skill_entrypoints(root: Path) -> list[Path]:
    """Find registered skill roots without recursing through repos or junctions."""
    found: list[Path] = []
    direct = root / "SKILL.md"
    if direct.is_file():
        found.append(direct)
    try:
        first_level = [item for item in root.iterdir() if item.is_dir()]
    except OSError:
        return found
    for folder in first_level:
        skill = folder / "SKILL.md"
        if skill.is_file():
            found.append(skill)
            continue
        # Codex system skills are namespaced under `.system/<skill>`. Keep the
        # search bounded to this one additional level; a cloned repository can
        # contain hundreds of nested examples and junctions.
        try:
            second_level = [item for item in folder.iterdir() if item.is_dir()]
        except OSError:
            continue
        for nested in second_level:
            nested_skill = nested / "SKILL.md"
            if nested_skill.is_file():
                found.append(nested_skill)
    return sorted(found)


def discover_skills(roots: Iterable[Path]) -> list[CapabilityResource]:
    resources: list[CapabilityResource] = []
    seen_paths: set[str] = set()
    seen_ids: set[str] = set()
    for root in (Path(item).expanduser() for item in roots):
        if not root.is_dir():
            continue
        for skill_file in _skill_entrypoints(root):
            canonical = str(skill_file.resolve()).casefold()
            if canonical in seen_paths:
                continue
            seen_paths.add(canonical)
            try:
                text = skill_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            meta = _frontmatter(text[:12000])
            name = meta.get("name") or skill_file.parent.name
            description = meta.get("description") or f"Skill instructions for {name}."
            base_id = f"skill:{name}"
            resource_id = base_id
            if resource_id in seen_ids:
                suffix = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:10]
                resource_id = f"{base_id}@{suffix}"
            seen_ids.add(resource_id)
            resources.append(CapabilityResource(
                resource_id=resource_id,
                name=name,
                kind="skill",
                origin=meta.get("metadata.origin", "installed"),
                description=" ".join(description.split()),
                path=skill_file,
                availability=_availability(skill_file),
                instruction_ready=True,
            ))
    return resources


def _license_hint(repo: Path) -> Optional[str]:
    for name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING"):
        path = repo / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")[:1000].lower()
        if "apache license" in text:
            return "Apache-2.0"
        if "mit license" in text:
            return "MIT"
        if "gnu affero" in text:
            return "AGPL"
        if "gnu general public" in text:
            return "GPL"
        return "declared"
    return None


def discover_source_repositories(root: Path) -> list[CapabilityResource]:
    root = Path(root).expanduser()
    if not root.is_dir():
        return []
    resources: list[CapabilityResource] = []
    for repo in sorted((path for path in root.iterdir() if path.is_dir()), key=lambda p: p.name.casefold()):
        readme = next((path for path in (repo / "README.md", repo / "README.rst", repo / "README") if path.is_file()), None)
        text = readme.read_text(encoding="utf-8", errors="replace")[:6000] if readme else ""
        description = " ".join(line.strip("# ") for line in text.splitlines()[:8] if line.strip())
        resources.append(CapabilityResource(
            resource_id=f"source:{repo.name}",
            name=repo.name,
            kind="source_repository",
            origin="cloned-source",
            description=description or f"Cloned capability source {repo.name}.",
            path=repo,
            availability="source-only",
            instruction_ready=False,
            license_hint=_license_hint(repo),
        ))
    return resources


def discover_mcp_servers(config_path: Path) -> list[CapabilityResource]:
    path = Path(config_path).expanduser()
    if not path.is_file():
        return []
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    servers = document.get("mcp_servers", {})
    if not isinstance(servers, dict):
        return []
    resources: list[CapabilityResource] = []
    for name, raw in sorted(servers.items()):
        if not isinstance(raw, dict):
            continue
        command = str(raw.get("command", "configured server"))
        args = raw.get("args", [])
        public_args = " ".join(str(item) for item in args if not str(item).startswith(("sk-", "ghp_")))
        resources.append(CapabilityResource(
            resource_id=f"mcp:{name}",
            name=name,
            kind="mcp_server",
            origin="codex-config",
            description=f"Configured MCP server via {command} {public_args}".strip(),
            path=path,
            availability="configured",
            instruction_ready=False,
            adapter_status="registered",
            entrypoint=command,
            permission_scopes=("mcp",),
            risk_status="configured-not-health-checked",
            tags=("mcp", name),
        ))
    return resources


def _adapter_health(
    *,
    path: Optional[Path],
    entrypoint: Optional[str],
    availability: str,
    instruction_ready: bool,
    opener: object,
) -> str:
    if entrypoint and "://" in entrypoint:
        parsed = urllib.parse.urlparse(entrypoint)
        if parsed.scheme not in {"http", "https"}:
            return "unsupported-service-scheme"
        if (parsed.hostname or "").casefold() not in {"127.0.0.1", "localhost", "::1"}:
            return "external-service-not-probed"
        try:
            request = urllib.request.Request(entrypoint, headers={"User-Agent": "Tri-AI-Capability-Health/1"})
            with opener(request, timeout=2) as response:  # type: ignore[operator]
                status = int(getattr(response, "status", 200))
            return "loopback-service-ready" if status < 500 else "loopback-service-degraded"
        except (OSError, urllib.error.URLError, TimeoutError):
            return "loopback-service-unreachable"
    if path is not None:
        if not path.exists():
            return "path-missing"
        if instruction_ready:
            return "instruction-ready"
        if availability == "source-only":
            return "source-present-not-adapted"
        return "path-present"
    if entrypoint:
        candidate = Path(entrypoint).expanduser()
        if candidate.is_absolute():
            return "entrypoint-present" if candidate.exists() else "entrypoint-missing"
        return "entrypoint-present" if shutil.which(entrypoint) else "entrypoint-missing"
    if availability == "missing":
        return "missing"
    return "not-probed"


def discover_adapters(
    manifest_path: Path,
    *,
    opener: object = urllib.request.urlopen,
) -> list[CapabilityResource]:
    """Load operator-reviewed adapters without importing credentials or code."""
    path = Path(manifest_path).expanduser()
    if not path.is_file():
        return []
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if document.get("schema") != "triai.capability-adapters.v1":
        return []
    resources: list[CapabilityResource] = []
    for raw in document.get("adapters", []):
        if not isinstance(raw, dict):
            continue
        adapter_id = str(raw.get("id") or "").strip()
        name = str(raw.get("name") or adapter_id).strip()
        if not adapter_id or not name:
            continue
        raw_path = raw.get("path")
        local_path = Path(str(raw_path)).expanduser() if raw_path else None
        availability = str(raw.get("availability") or "candidate-only")
        entrypoint = str(raw["entrypoint"]) if raw.get("entrypoint") else None
        instruction_ready = bool(raw.get("instruction_ready", False))
        health = _adapter_health(
            path=local_path,
            entrypoint=entrypoint,
            availability=availability,
            instruction_ready=instruction_ready,
            opener=opener,
        )
        resources.append(CapabilityResource(
            resource_id=f"adapter:{adapter_id}",
            name=name,
            kind=str(raw.get("kind") or "adapter"),
            origin="operator-manifest",
            description=" ".join(str(raw.get("description") or "").split()),
            path=local_path,
            availability=availability,
            instruction_ready=instruction_ready,
            license_hint=(str(raw["license_hint"]) if raw.get("license_hint") else None),
            adapter_status=str(raw.get("adapter_status") or "unadapted"),
            entrypoint=entrypoint,
            permission_scopes=tuple(str(item) for item in raw.get("permission_scopes", [])),
            risk_status=str(raw.get("risk_status") or "unreviewed"),
            tags=tuple(str(item) for item in raw.get("tags", [])),
            health_status=health,
            upstream_url=(str(raw["upstream_url"]) if raw.get("upstream_url") else None),
        ))
    return resources


def build_catalog(
    *,
    repo_root: Optional[Path] = None,
    adapter_manifest: Path = DEFAULT_ADAPTER_MANIFEST,
) -> list[CapabilityResource]:
    resources = discover_skills(default_skill_roots(repo_root))
    resources.extend(discover_source_repositories(Path.home() / ".tri-ai" / "capabilities" / "sources"))
    resources.extend(discover_mcp_servers(Path.home() / ".codex" / "config.toml"))
    resources.extend(discover_adapters(adapter_manifest))
    return resources


def write_catalog(path: Path, resources: Sequence[CapabilityResource]) -> None:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": CATALOG_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "resources": [
            {**asdict(item), "path": str(item.path) if item.path is not None else None}
            for item in resources
        ],
    }
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(target)


def load_catalog(path: Path = DEFAULT_CATALOG_PATH) -> list[CapabilityResource]:
    document = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if document.get("schema") != CATALOG_SCHEMA:
        raise ValueError("unsupported capability catalog schema")
    return [
        CapabilityResource(
            **{
                **raw,
                "path": Path(raw["path"]) if raw.get("path") else None,
                "permission_scopes": tuple(raw.get("permission_scopes", [])),
                "tags": tuple(raw.get("tags", [])),
            }
        )
        for raw in document.get("resources", [])
    ]


def _tokens(text: str) -> set[str]:
    return {word for word in _WORD.findall(text.lower()) if len(word) > 2 and word not in _STOP}


_ALIASES: dict[str, frozenset[str]] = {
    "frontend": frozenset({"website", "web", "ui", "ux", "interface", "dashboard", "react", "next.js"}),
    "design": frozenset({"visual", "polished", "taste", "motion", "diagram", "layout", "brand"}),
    "research": frozenset({"research", "compare", "competitor", "sources", "evidence", "market"}),
    "science": frozenset({"bioinformatics", "biology", "drug", "genomics", "protein", "rna", "molecule"}),
    "security": frozenset({"security", "threat", "malware", "sbom", "vulnerability", "audit"}),
    "browser": frozenset({"browser", "playwright", "click", "page", "mobile", "e2e", "qa"}),
    "memory": frozenset({"memory", "context", "knowledge", "graph", "recall", "obsidian"}),
    "agents": frozenset({"agent", "agents", "orchestration", "multi-agent", "autonomous", "worker"}),
    "cloud": frozenset({"aws", "gcp", "azure", "cloud", "docker", "kubernetes", "terraform", "deploy"}),
    "media": frozenset({"video", "audio", "voice", "image", "remotion", "montage"}),
    "business": frozenset({"business", "marketing", "sales", "seo", "launch", "revenue"}),
}


def match_resources(
    task: str,
    resources: Sequence[CapabilityResource],
    *,
    limit: int = 12,
    include_archived: bool = True,
) -> list[ResourceMatch]:
    task_tokens = _tokens(task)
    expanded = set(task_tokens)
    for anchor, aliases in _ALIASES.items():
        if task_tokens & aliases or anchor in task_tokens:
            expanded.add(anchor)
            expanded.update(aliases)

    scored: list[ResourceMatch] = []
    for resource in resources:
        if resource.availability == "archived-reference" and not include_archived:
            continue
        name_tokens = _tokens(resource.name.replace("-", " "))
        body_tokens = name_tokens | _tokens(resource.description) | {
            token for tag in resource.tags for token in _tokens(tag)
        }
        direct = task_tokens & body_tokens
        semantic = expanded & body_tokens
        score = len(direct) * 5 + len(semantic) * 2 + len(task_tokens & name_tokens) * 4
        if score <= 0:
            continue
        score += {
            "executable": 8, "registered": 8, "active": 5,
            "configured": 4, "available": 2, "gated": 1,
            "source-only": 0, "candidate-only": -2, "missing": -4,
        }.get(resource.availability, 0)
        if resource.resource_id.startswith("adapter:"):
            score += 12
        reason_terms = sorted((direct or semantic))[:5]
        scored.append(ResourceMatch(resource, score, "matched " + ", ".join(reason_terms)))

    availability_rank = {
        "executable": 0, "registered": 0, "active": 1, "configured": 2,
        "available": 3, "gated": 4, "candidate-only": 5,
        "archived-reference": 6, "source-only": 7, "missing": 8,
    }
    scored.sort(key=lambda item: (
        -item.score,
        availability_rank.get(item.resource.availability, 9),
        item.resource.name.casefold(),
    ))

    # One canonical name in a task brief. Duplicates remain represented in the
    # catalog counts and can be inspected, but do not consume the whole context.
    selected: list[ResourceMatch] = []
    seen_names: set[str] = set()
    for item in scored:
        key = item.resource.name.casefold()
        if key in seen_names:
            continue
        seen_names.add(key)
        selected.append(item)
        if len(selected) >= limit:
            break
    return selected


def catalog_summary(resources: Sequence[CapabilityResource]) -> dict[str, int]:
    summary: dict[str, int] = {"total": len(resources)}
    for resource in resources:
        summary[resource.kind] = summary.get(resource.kind, 0) + 1
        key = f"availability:{resource.availability}"
        summary[key] = summary.get(key, 0) + 1
    return summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="capability_catalog.py")
    parser.add_argument("--output", type=Path, default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    resources = build_catalog(repo_root=args.repo_root)
    write_catalog(args.output, resources)
    print(json.dumps({"catalog": str(args.output), **catalog_summary(resources)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
