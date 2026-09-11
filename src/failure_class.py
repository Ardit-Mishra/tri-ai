"""Deterministic classification of a verifier's non-pass outcome.

Classification consumes only the executor's discriminated outcome, exit code,
and captured verifier output.  It does not inspect an agent report and it does
not call a model.  Unknown signals fail closed as logic failures at the caller.
"""

from __future__ import annotations

import re
from enum import Enum


class FailureClass(Enum):
    ENVIRONMENT = "environment"
    LOGIC = "logic"


_ENVIRONMENT_EXIT_CODES = frozenset({-15, -11, -9, -6, -4, 132, 134, 136, 137, 139, 143})
_ENVIRONMENT_PATTERNS = (
    re.compile(r"\b(?:MemoryError|ENOMEM|out of memory|cannot allocate memory)\b", re.I),
    re.compile(r"\b(?:connection reset by peer|connection timed out|read timed out|"
               r"could not resolve host|network is unreachable|no route to host)\b", re.I),
    re.compile(r"\bHTTP\s+429\b|\b(?:rate limit|quota|usage limit|resource exhausted)\b", re.I),
    re.compile(r"\b(?:no space left on device|disk full|too many open files)\b", re.I),
    re.compile(r"\b(?:segmentation fault|core dumped|bus error)\b", re.I),
)


def classify_failure(*, verify_outcome: str, verify_exit: int | None,
                     verify_output: str = "") -> FailureClass:
    """Return the deterministic class for one verifier non-pass result.

    ``timeout`` and ``spawn_error`` are environment outcomes by construction:
    the scheduler killed the former and never started the latter. A normal
    non-zero verifier exit is logic unless the recorded process signals or
    output provide a narrow environment signature.
    """
    if verify_outcome == "passed":
        raise ValueError("a passed verify outcome is not a failure")
    if verify_outcome in {"timeout", "spawn_error"}:
        return FailureClass.ENVIRONMENT
    if verify_outcome != "failed":
        raise ValueError(f"unknown verifier outcome {verify_outcome!r}")
    if verify_exit in _ENVIRONMENT_EXIT_CODES:
        return FailureClass.ENVIRONMENT
    if any(pattern.search(verify_output) for pattern in _ENVIRONMENT_PATTERNS):
        return FailureClass.ENVIRONMENT
    return FailureClass.LOGIC
