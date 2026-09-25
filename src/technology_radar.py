"""Evidence-producing technology discovery and isolated evaluation pipeline."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional, Sequence


REPORT_SCHEMA = "triai.technology-radar.v1"
DEFAULT_REPORT = Path.home() / ".tri-ai" / "radar" / "latest.json"
DEFAULT_ADAPTER_MANIFEST = Path(__file__).resolve().parents[1] / "config" / "capability-adapters.json"


@dataclass(frozen=True)
class Candidate:
    source: str
    name: str
    url: str
    description: str
    stars: int
    forks: int
    open_issues: int
    created_at: str
    updated_at: str
    license_id: Optional[str]
    query: str
    signals: tuple[str, ...]
    disposition: str


@dataclass(frozen=True)
class StaticFinding:
    rule_id: str
    severity: str
    path: str
    detail: str


@dataclass(frozen=True)
class StaticReport:
    verdict: str
    files_scanned: int
    findings: tuple[StaticFinding, ...]


@dataclass(frozen=True)
class DynamicProbe:
    status: str
    reason: str
    command: tuple[str, ...] = ()
    exit_code: Optional[int] = None
    output: str = ""


@dataclass(frozen=True)
class CandidateEvaluation:
    candidate: Candidate
    quarantine_path: str
    static: StaticReport
    dynamic: DynamicProbe
    disposition: str


def load_operator_seeds(manifest_path: Path) -> list[Candidate]:
    """Turn requested GitHub adapters into persistent radar candidates."""
    try:
        document = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if document.get("schema") != "triai.capability-adapters.v1":
        return []
    candidates: list[Candidate] = []
    seen: set[str] = set()
    for raw in document.get("adapters", []):
        if not isinstance(raw, Mapping):
            continue
        url = str(raw.get("upstream_url") or "")
        if not url or url in seen:
            continue
        seed = Candidate(
            source="github",
            name=str(raw.get("name") or raw.get("id") or url),
            url=url,
            description=str(raw.get("description") or "Operator-requested capability."),
            stars=0, forks=0, open_issues=0,
            created_at="", updated_at="", license_id=None,
            query="operator-adapter-manifest",
            signals=("operator-requested",),
            disposition="evaluate",
        )
        try:
            _github_coordinates(seed)
        except ValueError:
            continue
        seen.add(url)
        candidates.append(seed)
    return candidates


def select_evaluation_candidates(
    candidates: Sequence[Candidate],
    *,
    limit: int,
    rotation: int,
) -> list[Candidate]:
    """Prioritize requested tools and rotate them across bounded radar runs."""
    if limit <= 0:
        return []
    requested = [item for item in candidates if "operator-requested" in item.signals]
    discovered = [item for item in candidates if "operator-requested" not in item.signals]
    if requested:
        offset = rotation % len(requested)
        requested = requested[offset:] + requested[:offset]
    return (requested + discovered)[:limit]


def parse_github_results(payload: Mapping[str, object], *, query: str) -> list[Candidate]:
    candidates: list[Candidate] = []
    for raw in payload.get("items", []) if isinstance(payload.get("items", []), list) else []:
        if not isinstance(raw, Mapping) or raw.get("archived"):
            continue
        license_raw = raw.get("license")
        license_id = license_raw.get("spdx_id") if isinstance(license_raw, Mapping) else None
        stars = int(raw.get("stargazers_count") or 0)
        pushed = str(raw.get("pushed_at") or raw.get("updated_at") or "")
        signals = ["recency"]
        if stars >= 100:
            signals.append("adoption")
        if license_id and license_id != "NOASSERTION":
            signals.append("declared-license")
        candidates.append(Candidate(
            source="github",
            name=str(raw.get("full_name") or "unknown"),
            url=str(raw.get("html_url") or ""),
            description=str(raw.get("description") or ""),
            stars=stars,
            forks=int(raw.get("forks_count") or 0),
            open_issues=int(raw.get("open_issues_count") or 0),
            created_at=str(raw.get("created_at") or ""),
            updated_at=pushed,
            license_id=str(license_id) if license_id else None,
            query=query,
            signals=tuple(signals),
            disposition="evaluate",
        ))
    return candidates


def github_search(
    query: str,
    *,
    days: int = 14,
    limit: int = 20,
    token: Optional[str] = None,
    opener: Callable[..., object] = urllib.request.urlopen,
) -> list[Candidate]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    qualified = f"{query} pushed:>={since} archived:false"
    params = urllib.parse.urlencode({
        "q": qualified,
        "sort": "updated",
        "order": "desc",
        "per_page": max(1, min(limit, 100)),
    })
    request = urllib.request.Request(
        f"https://api.github.com/search/repositories?{params}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Tri-AI-Technology-Radar/1",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    with opener(request, timeout=30) as response:  # type: ignore[attr-defined]
        payload = json.loads(response.read().decode("utf-8"))
    return parse_github_results(payload, query=query)


def parse_youtube_results(payload: Mapping[str, object], *, query: str) -> list[Candidate]:
    candidates: list[Candidate] = []
    items = payload.get("items", [])
    for raw in items if isinstance(items, list) else []:
        if not isinstance(raw, Mapping):
            continue
        identity = raw.get("id")
        snippet = raw.get("snippet")
        if not isinstance(identity, Mapping) or not isinstance(snippet, Mapping):
            continue
        video_id = str(identity.get("videoId") or "")
        if not video_id:
            continue
        published = str(snippet.get("publishedAt") or "")
        channel = str(snippet.get("channelTitle") or "unknown channel")
        candidates.append(Candidate(
            source="youtube",
            name=f"{channel}: {snippet.get('title') or video_id}",
            url=f"https://www.youtube.com/watch?v={video_id}",
            description=str(snippet.get("description") or ""),
            stars=0, forks=0, open_issues=0,
            created_at=published, updated_at=published, license_id=None,
            query=query, signals=("recency", "video-demonstration"),
            disposition="research-lead",
        ))
    return candidates


def youtube_search(
    query: str,
    *,
    api_key: str,
    days: int = 14,
    limit: int = 10,
    opener: Callable[..., object] = urllib.request.urlopen,
) -> list[Candidate]:
    published_after = (
        datetime.now(timezone.utc) - timedelta(days=days)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    params = urllib.parse.urlencode({
        "part": "snippet", "type": "video", "order": "date", "q": query,
        "publishedAfter": published_after, "maxResults": max(1, min(limit, 50)),
        "key": api_key,
    })
    request = urllib.request.Request(
        f"https://www.googleapis.com/youtube/v3/search?{params}",
        headers={"User-Agent": "Tri-AI-Technology-Radar/1"},
    )
    with opener(request, timeout=30) as response:  # type: ignore[attr-defined]
        payload = json.loads(response.read().decode("utf-8"))
    return parse_youtube_results(payload, query=query)


def parse_hacker_news_results(payload: Mapping[str, object], *, query: str) -> list[Candidate]:
    candidates: list[Candidate] = []
    hits = payload.get("hits", [])
    for raw in hits if isinstance(hits, list) else []:
        if not isinstance(raw, Mapping):
            continue
        title = str(raw.get("title") or raw.get("story_title") or "")
        url = str(raw.get("url") or raw.get("story_url") or "")
        object_id = str(raw.get("objectID") or "")
        if not title or not object_id:
            continue
        created = str(raw.get("created_at") or "")
        candidates.append(Candidate(
            source="hacker_news", name=title,
            url=url or f"https://news.ycombinator.com/item?id={object_id}",
            description=f"Hacker News discussion with {int(raw.get('num_comments') or 0)} comments.",
            stars=int(raw.get("points") or 0), forks=0,
            open_issues=int(raw.get("num_comments") or 0),
            created_at=created, updated_at=created, license_id=None,
            query=query, signals=("recency", "community-discussion"),
            disposition="research-lead",
        ))
    return candidates


def hacker_news_search(
    query: str,
    *,
    days: int = 14,
    limit: int = 20,
    opener: Callable[..., object] = urllib.request.urlopen,
) -> list[Candidate]:
    after = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    params = urllib.parse.urlencode({
        "query": query, "tags": "story", "numericFilters": f"created_at_i>{after}",
        "hitsPerPage": max(1, min(limit, 100)),
    })
    request = urllib.request.Request(
        f"https://hn.algolia.com/api/v1/search_by_date?{params}",
        headers={"User-Agent": "Tri-AI-Technology-Radar/1"},
    )
    with opener(request, timeout=30) as response:  # type: ignore[attr-defined]
        payload = json.loads(response.read().decode("utf-8"))
    return parse_hacker_news_results(payload, query=query)


_EXECUTABLE_SUFFIXES = frozenset({".exe", ".dll", ".com", ".scr", ".msi", ".ps1", ".bat", ".cmd"})
_SHELL_DOWNLOAD = re.compile(r"(?:curl|wget|invoke-webrequest).{0,160}(?:\||;|&&)\s*(?:sh|bash|powershell|pwsh)", re.I | re.S)


def static_scan(root: Path) -> StaticReport:
    root = Path(root).resolve()
    findings: list[StaticFinding] = []
    files_scanned = 0
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        files_scanned += 1
        relative = str(path.relative_to(root))
        if path.suffix.lower() in _EXECUTABLE_SUFFIXES:
            findings.append(StaticFinding(
                "executable-artifact", "high", relative,
                "Repository contains an executable or command-script artifact; inspect before running.",
            ))
        if path.name == "package.json":
            try:
                package = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                findings.append(StaticFinding("invalid-manifest", "medium", relative, "package.json could not be parsed."))
                continue
            scripts = package.get("scripts", {}) if isinstance(package, dict) else {}
            for hook in ("preinstall", "install", "postinstall", "prepare"):
                if isinstance(scripts, dict) and hook in scripts:
                    command = str(scripts[hook])
                    severity = "critical" if _SHELL_DOWNLOAD.search(command) else "high"
                    findings.append(StaticFinding(
                        "package-lifecycle-script", severity, relative,
                        f"npm lifecycle hook {hook!r} requires manual review.",
                    ))
        if path.stat().st_size <= 1_000_000 and path.suffix.lower() in {".sh", ".ps1", ".cmd", ".bat", ".yml", ".yaml"}:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if _SHELL_DOWNLOAD.search(text):
                findings.append(StaticFinding(
                    "download-and-execute", "critical", relative,
                    "Script downloads content and pipes or chains it into a shell.",
                ))
    verdict = "manual_review" if findings else "static_clear"
    return StaticReport(verdict, files_scanned, tuple(findings))


def container_probe_command(
    root: Path,
    *,
    image: str,
    probe: Sequence[str],
) -> list[str]:
    mount = f"{Path(root).resolve()}:/workspace:ro"
    return [
        "docker", "run", "--rm", "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--pids-limit", "128", "--memory", "1g", "--cpus", "1",
        "--mount", "type=tmpfs,destination=/tmp,tmpfs-size=134217728",
        "-v", mount, "-w", "/workspace", image, *probe,
    ]


def _docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def select_container_probe(root: Path) -> tuple[str, tuple[str, ...], str]:
    """Choose a non-installing probe from the candidate's root manifests."""
    root = Path(root)
    if (root / "package.json").is_file():
        return (
            "node:22-alpine",
            (
                "node", "-e",
                "const fs=require('fs'); JSON.parse(fs.readFileSync('package.json','utf8'));",
            ),
            "Node manifest parse",
        )
    if any((root / name).is_file() for name in ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")):
        return (
            "python:3.13-alpine",
            ("python", "-m", "compileall", "-q", "/workspace"),
            "Python syntax compile",
        )
    return (
        "alpine:3.22",
        ("sh", "-c", "test -r README.md || test -r README || test -r LICENSE"),
        "Readable project metadata",
    )


def run_dynamic_probe(
    root: Path,
    *,
    docker_available: Callable[[], bool] = _docker_available,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> DynamicProbe:
    if not docker_available():
        return DynamicProbe("not_run", "Docker engine unavailable; host execution is forbidden for untrusted candidates.")
    image, probe, label = select_container_probe(root)
    command = container_probe_command(
        root,
        image=image,
        probe=probe,
    )
    try:
        result = runner(command, capture_output=True, text=True, timeout=180, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return DynamicProbe(
            "failed",
            f"{label} failed inside the container boundary: {type(exc).__name__}.",
            tuple(command),
        )
    output = ((result.stdout or "") + (result.stderr or ""))[-12000:]
    return DynamicProbe(
        "passed" if result.returncode == 0 else "failed",
        f"{label} completed in a read-only, no-network container.",
        tuple(command),
        result.returncode,
        output,
    )


def _github_coordinates(candidate: Candidate) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(candidate.url)
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if parsed.scheme != "https" or parsed.netloc.casefold() != "github.com" or len(parts) != 2:
        raise ValueError("only canonical HTTPS GitHub repository URLs may enter quarantine")
    owner, repo = parts
    return owner, repo.removesuffix(".git")


def fetch_github_archive(
    candidate: Candidate,
    destination: Path,
    *,
    opener: Callable[..., object] = urllib.request.urlopen,
    max_download_bytes: int = 100_000_000,
    max_unpacked_bytes: int = 250_000_000,
) -> Path:
    """Download and safely unpack source without running Git hooks or filters."""
    owner, repo = _github_coordinates(candidate)
    request = urllib.request.Request(
        f"https://api.github.com/repos/{owner}/{repo}/zipball",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Tri-AI-Technology-Radar/1",
            **({"Authorization": f"Bearer {os.environ['GH_TOKEN']}"} if os.environ.get("GH_TOKEN") else {}),
        },
    )
    with opener(request, timeout=60) as response:  # type: ignore[attr-defined]
        payload = response.read(max_download_bytes + 1)
    if len(payload) > max_download_bytes:
        raise ValueError("candidate archive exceeds the download limit")

    destination.mkdir(parents=True, exist_ok=False)
    total = 0
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for member in archive.infolist():
            parts = [part for part in member.filename.replace("\\", "/").split("/") if part]
            if len(parts) <= 1:
                continue
            relative = Path(*parts[1:])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("candidate archive contains an unsafe path")
            unix_mode = member.external_attr >> 16
            if (unix_mode & 0o170000) == 0o120000:
                raise ValueError("candidate archive contains a symbolic link")
            total += member.file_size
            if total > max_unpacked_bytes:
                raise ValueError("candidate archive exceeds the unpacked size limit")
            target = destination / relative
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink)
    return destination


def evaluate_candidate(
    candidate: Candidate,
    *,
    quarantine_root: Path,
    fetcher: Callable[[Candidate, Path], Path] = fetch_github_archive,
) -> CandidateEvaluation:
    """Fetch source into quarantine, statically inspect it, then probe in Docker."""
    root = Path(quarantine_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    prefix = hashlib.sha256(candidate.url.encode("utf-8")).hexdigest()[:12] + "-"
    destination = Path(tempfile.mkdtemp(prefix=prefix, dir=root))
    # Fetchers own directory creation. The default safe archive fetcher refuses
    # an existing destination, so remove only this freshly-created empty leaf.
    destination.rmdir()
    try:
        fetched = Path(fetcher(candidate, destination)).resolve()
        if root not in fetched.parents:
            raise ValueError("candidate fetch escaped the quarantine root")
        static = static_scan(fetched)
        if any(item.severity == "critical" for item in static.findings):
            dynamic = DynamicProbe(
                "blocked", "Critical static findings prevent any candidate execution."
            )
            disposition = "manual-security-review"
        else:
            dynamic = run_dynamic_probe(fetched)
            if static.findings:
                disposition = "manual-security-review"
            elif dynamic.status == "passed":
                disposition = "candidate-ready-for-adapter-review"
            else:
                disposition = "probe-incomplete"
        return CandidateEvaluation(candidate, str(fetched), static, dynamic, disposition)
    except Exception as exc:
        static = StaticReport("scan_failed", 0, ())
        dynamic = DynamicProbe("blocked", f"Evaluation failed safely: {type(exc).__name__}: {exc}")
        return CandidateEvaluation(candidate, str(destination), static, dynamic, "evaluation-failed")


def write_report(path: Path, candidates: Iterable[Candidate], **extra: object) -> None:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "schema": REPORT_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidates": [asdict(candidate) for candidate in candidates],
        **extra,
    }
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(target)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="technology_radar.py")
    parser.add_argument("--query", action="append", dest="queries")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--adapter-manifest", type=Path, default=DEFAULT_ADAPTER_MANIFEST)
    parser.add_argument("--evaluate-limit", type=int, default=0)
    parser.add_argument(
        "--quarantine-root", type=Path,
        default=Path.home() / ".tri-ai" / "radar" / "quarantine",
    )
    args = parser.parse_args(argv)
    queries = args.queries or [
        "AI agent framework", "agent memory MCP", "coding agent sandbox",
        "LLM evaluation observability", "generative UI agent",
    ]
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    youtube_key = os.environ.get("YOUTUBE_API_KEY")
    candidates: list[Candidate] = load_operator_seeds(args.adapter_manifest)
    errors: list[dict[str, str]] = []
    for query in queries:
        try:
            candidates.extend(github_search(query, days=args.days, limit=args.limit, token=token))
        except Exception as exc:  # network/API failure belongs in the evidence report
            errors.append({"source": "github", "query": query, "error": f"{type(exc).__name__}: {exc}"})
        try:
            candidates.extend(hacker_news_search(query, days=args.days, limit=args.limit))
        except Exception as exc:
            errors.append({"source": "hacker_news", "query": query, "error": f"{type(exc).__name__}: {exc}"})
        if youtube_key:
            try:
                candidates.extend(youtube_search(
                    query, api_key=youtube_key, days=args.days,
                    limit=min(args.limit, 10),
                ))
            except Exception as exc:
                errors.append({"source": "youtube", "query": query, "error": f"{type(exc).__name__}: {exc}"})
    unique: dict[str, Candidate] = {}
    for candidate in candidates:
        if candidate.url:
            unique.setdefault(candidate.url, candidate)
    evaluations: list[CandidateEvaluation] = []
    rotation = (datetime.now(timezone.utc).date().toordinal() // 7) * max(1, args.evaluate_limit)
    for candidate in select_evaluation_candidates(
        list(unique.values()), limit=max(0, args.evaluate_limit), rotation=rotation
    ):
        if candidate.source == "github":
            evaluations.append(evaluate_candidate(
                candidate, quarantine_root=args.quarantine_root
            ))
    write_report(
        args.report, unique.values(), errors=errors,
        evaluations=[asdict(item) for item in evaluations],
    )
    print(json.dumps({
        "report": str(args.report), "candidates": len(unique),
        "evaluated": len(evaluations), "errors": len(errors),
    }))
    return 0 if unique or not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
