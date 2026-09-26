"""Detect a workspace's stack, and give its nodes a gate that can actually fail.

`decomposer` marks every node `verify_generic: true` because a stack-blind
command only proves the workspace changed. That is a real gate but a weak one:
it cannot tell a working Django app from one whose models no longer match its
migrations.

Two consumers, deliberately kept apart:

  * the ``-verification`` skills are instruction documents. They teach an
    *agent* how to verify a stack, in phases, with prose and examples. They are
    named here so the capability brief can hand the right one to the right
    specialist.
  * the ``verify_command`` below is what the *gate* runs. It is a curated shell
    command, not something parsed out of that prose. Turning an instruction
    document into a shell command by pattern-matching is the kind of cleverness
    that fails quietly, and a gate that fails quietly is worse than none.

Detection reads repository evidence only — manifests and marker files — never
the request. A request that claims to be a Django app does not make the gate
run Django's checks; `manage.py` plus a Django dependency does.

Specific beats general. A Django repo is also a Python repo, so ``django`` is
tested before ``python`` and the first match wins. Evidence is recorded on the
result, so a wrong gate is traceable to the file that caused it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

# Applied when nothing is recognised. Generic, not fake: a workspace the agent
# never changed is a failed task under any stack. It is always labelled, so a
# blind gate can never be mistaken for a considered one.
GENERIC_VERIFY = "git -C . diff --quiet && exit 1 || exit 0"


@dataclass(frozen=True)
class StackSpec:
    label: str
    verify_command: str
    # Skills that teach an agent this stack. Verified present on this machine
    # by tests/test_stack_profile.py, because a dangling reference resolves to
    # nothing and teaches nobody.
    skills: tuple[str, ...] = ()


@dataclass
class Profile:
    stack: str
    label: str
    verify_command: str
    generic: bool
    evidence: list[str] = field(default_factory=list)
    skills: tuple[str, ...] = ()


def _has(root: Path, *names: str) -> bool:
    return any((root / n).exists() for n in names)


def _text(root: Path, name: str) -> str:
    p = root / name
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _dep(root: Path, needle: str, *files: str) -> bool:
    """A dependency named in any of `files`, case-insensitively."""
    return any(needle.lower() in _text(root, f).lower() for f in files)


# Ordered: the first match wins, so a specific stack must precede the general
# one it is built on.
DETECTORS: tuple[tuple[str, Callable[[Path], list[str]]], ...] = (
    ("django", lambda r: (
        ["manage.py", "django dependency"]
        if (root_has := _has(r, "manage.py")) and _dep(
            r, "django", "requirements.txt", "pyproject.toml", "setup.py", "Pipfile")
        else [])),
    ("laravel", lambda r: (
        ["artisan", "composer.json"]
        if _has(r, "artisan") and _has(r, "composer.json") else [])),
    ("springboot", lambda r: (
        ["pom.xml/build.gradle", "spring dependency"]
        if _has(r, "pom.xml", "build.gradle", "build.gradle.kts")
        and _dep(r, "springframework", "pom.xml", "build.gradle", "build.gradle.kts")
        else [])),
    ("quarkus", lambda r: (
        ["pom.xml/build.gradle", "quarkus dependency"]
        if _has(r, "pom.xml", "build.gradle", "build.gradle.kts")
        and _dep(r, "quarkus", "pom.xml", "build.gradle", "build.gradle.kts")
        else [])),
    ("rust", lambda r: ["Cargo.toml"] if _has(r, "Cargo.toml") else []),
    ("go", lambda r: ["go.mod"] if _has(r, "go.mod") else []),
    ("node", lambda r: ["package.json"] if _has(r, "package.json") else []),
    ("python", lambda r: [
        n for n in ("pyproject.toml", "requirements.txt", "setup.py") if (r / n).exists()
    ]),
    # Last, so any manifest wins: a project that ships its own runner and
    # declares no dependencies. Tri-AI is one — 88 .py files, stdlib only, no
    # pyproject, and `python tests/run.py` is the gate its CLAUDE.md names.
    # Running pytest here would collect nothing and exit 0, which is the worst
    # possible gate: green because it checked nothing.
    ("python-runner", lambda r: (
        ["tests/run.py"] if (r / "tests" / "run.py").exists() else [])),
)

STACKS: dict[str, StackSpec] = {
    "django": StackSpec(
        "Django",
        # `makemigrations --check` is the one that matters: a model change with
        # no migration passes every test and breaks the deploy.
        "python manage.py check && python manage.py makemigrations --check --dry-run "
        "&& python -m pytest -q",
        ("django-verification", "django-tdd", "django-patterns"),
    ),
    "laravel": StackSpec(
        "Laravel",
        "php artisan test",
        ("laravel-verification", "laravel-tdd", "laravel-patterns"),
    ),
    "springboot": StackSpec(
        "Spring Boot",
        "./mvnw -q -B test",
        ("springboot-verification", "springboot-tdd", "springboot-patterns"),
    ),
    "quarkus": StackSpec(
        "Quarkus",
        "./mvnw -q -B test",
        ("quarkus-verification", "quarkus-tdd", "quarkus-patterns"),
    ),
    "rust": StackSpec("Rust", "cargo test --quiet",
                      ("rust-testing", "rust-patterns")),
    "go": StackSpec("Go", "go test ./...",
                    ("golang-testing", "golang-patterns")),
    "node": StackSpec("Node", "npm test --silent",
                      ("e2e-testing", "frontend-patterns")),
    "python": StackSpec(
        "Python",
        "python -m pytest -q",
        ("python-testing", "python-patterns", "test-driven-development"),
    ),
    "python-runner": StackSpec(
        "Python (self-hosted runner)",
        "python tests/run.py",
        ("python-testing", "test-driven-development", "verification-before-completion"),
    ),
}


def detect(root: str | Path) -> Profile:
    """Identify the stack from repository evidence alone."""
    path = Path(root)
    for name, probe in DETECTORS:
        try:
            evidence = probe(path)
        except OSError:
            evidence = []
        if evidence:
            spec = STACKS[name]
            return Profile(
                stack=name, label=spec.label, verify_command=spec.verify_command,
                generic=False, evidence=evidence, skills=spec.skills,
            )
    return Profile(
        stack="unknown", label="unknown", verify_command=GENERIC_VERIFY,
        generic=True, evidence=[], skills=(),
    )


def apply(graph: Mapping[str, Any], root: str | Path) -> Profile:
    """Give every node in `graph` the detected gate. Mutates the nodes.

    An unknown workspace keeps `verify_generic: true`, so the weak gate stays
    visible rather than being quietly promoted by having been through here.

    A detection that detected *nothing* does not overwrite a command the node
    already carries. Measured on the first fan-out to run to completion: the
    operator's intake policy sets `python verify.py` for the sandbox, this
    function replaced it on every node with the generic fallback because a
    workspace with no manifest detects as "unknown", and three specialists
    were judged by a gate the operator never chose. Detecting a stack is a
    reason to set the gate; detecting nothing is not.
    """
    profile = detect(root)
    for node in graph.get("nodes", []):
        existing = str(node.get("verify_command") or "").strip()
        if not profile.generic or not existing:
            node["verify_command"] = profile.verify_command
            node["verify_generic"] = profile.generic
        node["verify_stack"] = profile.stack
    return profile


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Detect a workspace's stack and gate.")
    ap.add_argument("root")
    ap.add_argument("--graph", help="graph JSON to stamp in place")
    args = ap.parse_args(argv)

    if args.graph:
        p = Path(args.graph)
        graph = json.loads(p.read_text(encoding="utf-8"))
        profile = apply(graph, args.root)
        p.write_text(json.dumps(graph, indent=2), encoding="utf-8")
        print(f"{len(graph.get('nodes', []))} nodes stamped -> {p}")
    else:
        profile = detect(args.root)

    print(f"stack   : {profile.stack} ({profile.label})")
    print(f"evidence: {', '.join(profile.evidence) or '(none)'}")
    print(f"generic : {profile.generic}")
    print(f"verify  : {profile.verify_command}")
    print(f"skills  : {', '.join(profile.skills) or '(none)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
