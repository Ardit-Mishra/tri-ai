"""The ledger's documented schema and its producer must not drift apart.

Twice this session a consumer read a field the producer never wrote, and both
times every test on both sides stayed green because each built its own
fixture:

  * `_board_failure` wrote `answered_without_tools` under a typo'd key and all
    17 renderer tests passed, because they construct the completion row.
  * `lane_select._outcomes` joined on `agent_role` and matched nothing for
    every role, because `tests/test_lane_select.py` writes its own ledger
    lines and so agreed with the consumer about a field `_entry` did not emit.

The end-to-end tests pin those two paths. This pins the contract itself: the
schema block in `ledger.py` is what a reader of this codebase treats as the
list of available fields, and a consumer written against a documented-but-
unwritten field fails silently — `.get()` returns None and a filter matches
nothing. Cheap to check, and it fails in both directions.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import ledger  # noqa: E402
import worker  # noqa: E402


class _Claimed:
    id = "t_example"
    title = "Build the thing"


def _documented() -> set[str]:
    """Field names from the schema block in ledger.py's module docstring."""
    doc = ledger.__doc__ or ""
    start = doc.find("Entry schema")
    self_check = doc.find("Outcome vocabulary", start)
    assert start != -1 and self_check != -1, "ledger.py's schema block moved"
    names = set()
    # "    field_name   type  description". The columns are hand-aligned, so
    # a long name leaves only ONE space before its type - matching on the
    # type keyword rather than on whitespace is what makes this robust.
    # (A first version required two spaces and silently missed
    # `verify_outcome` and `failure_class`, which is the same class of bug
    # this file exists to catch.)
    TYPES = r"(?:str|int|bool|float|dict|list)\b"
    for line in doc[start:self_check].splitlines():
        match = re.match(rf"^ {{4}}([a-z_][a-z0-9_]*)\s+{TYPES}", line)
        if match:
            names.add(match.group(1))
            continue
        # A name too long for the column wraps, with its type on the next line.
        solo = re.match(r"^ {4}([a-z_][a-z0-9_]*)\s*$", line)
        if solo:
            names.add(solo.group(1))
    return names


def _emitted() -> set[str]:
    return set(worker._entry(
        _Claimed(), run_id=1, repo="/tmp/x", branch=None, outcome="failed"))


class LedgerSchemaIsHonestTest(unittest.TestCase):
    def test_the_schema_block_is_found_and_not_empty(self):
        """Guard the guard: a moved docstring must fail loudly, not vacuously
        pass by finding nothing to compare."""
        self.assertGreater(len(_documented()), 10)

    def test_every_emitted_field_is_documented(self):
        undocumented = sorted(_emitted() - _documented())
        self.assertEqual(
            undocumented, [],
            "fields written to the ledger but absent from ledger.py's schema "
            f"block: {undocumented}",
        )

    def test_every_documented_field_is_emitted(self):
        """The direction that bit us. A documented field nobody writes is one
        a consumer will read as None for ever."""
        unwritten = sorted(_documented() - _emitted())
        self.assertEqual(
            unwritten, [],
            "fields documented in ledger.py but never written by worker."
            f"_entry: {unwritten}",
        )

    def test_the_fields_lane_select_joins_on_are_written(self):
        """`_outcomes` filters on these three. All must exist or the join
        silently matches nothing, which is exactly what `agent_role` did."""
        for field in ("agent_role", "model", "outcome"):
            self.assertIn(field, _emitted())


if __name__ == "__main__":
    unittest.main()
