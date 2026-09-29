"""A source with several roots must still appear as one region.

`_source_region_id` folds technical root identifiers into the operator's
declared source regions, so `laptop-downloads` counts toward `Laptop` rather
than appearing as a source nobody declared. Until now it knew only about
`desktop`, `laptop` and `ollama`.

Three of the seven pending sources have more than one root: Obsidian has a
vault per location, Claude and Codex are two separate session stores, and
Vercel and Render are two providers. Without folding, each root becomes its
own unrecognised source and the declared region stays at zero - which is
precisely the reading that made the desktop look absent while it held
486,925 indexed nodes.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dashboard import kaya_web  # noqa: E402


class SourceRegionTest(unittest.TestCase):
    def test_the_regions_it_already_knew_are_unchanged(self):
        for root, region in (
            ("desktop-profile", "desktop"),
            ("laptop-downloads", "laptop"),
            ("ollama-desktop", "ollama"),
            ("google-drive", "drive"),
        ):
            with self.subTest(root=root):
                self.assertEqual(kaya_web._source_region_id(root), region)

    def test_a_vault_per_location_still_counts_as_obsidian(self):
        for root in ("obsidian-thestart", "obsidian-projects", "obsidian-claude"):
            with self.subTest(root=root):
                self.assertEqual(kaya_web._source_region_id(root), "obsidian")

    def test_two_session_stores_are_one_region(self):
        """The id contains a hyphen itself, so a naive prefix rule splits it."""
        for root in ("claude-codex-claude", "claude-codex-codex"):
            with self.subTest(root=root):
                self.assertEqual(kaya_web._source_region_id(root), "claude-codex")

    def test_two_hosting_providers_are_one_region(self):
        for root in ("vercel-render-vercel", "vercel-render-render"):
            with self.subTest(root=root):
                self.assertEqual(kaya_web._source_region_id(root), "vercel-render")

    def test_a_region_id_on_its_own_is_left_alone(self):
        for region in ("obsidian", "claude-codex", "vercel-render", "github",
                       "phone", "omniroute", "freellmapi"):
            with self.subTest(region=region):
                self.assertEqual(kaya_web._source_region_id(region), region)

    def test_an_unrelated_id_is_not_swallowed_by_a_region(self):
        """`claude-*` must not be captured by the `claude-codex` region, and a
        source that merely starts with the same letters is its own thing."""
        for unrelated in ("claudia", "obsidiana", "githubbing", "phonebook"):
            with self.subTest(unrelated=unrelated):
                self.assertEqual(kaya_web._source_region_id(unrelated), unrelated)

    def test_the_longest_matching_region_wins(self):
        """`claude-codex-x` must fold to `claude-codex`, never to a shorter
        region that happens to also prefix it."""
        self.assertEqual(
            kaya_web._source_region_id("vercel-render-vercel"), "vercel-render")


class RegionsReachTheSnapshotTest(unittest.TestCase):
    """Folding correctly is not the same as the merge using it."""

    def test_several_roots_add_up_into_one_declared_source(self):
        merged = kaya_web._merge_declared_sources(
            [
                {"id": "obsidian-thestart", "node_count": 120, "authorized": True},
                {"id": "obsidian-projects", "node_count": 30, "authorized": True},
                {"id": "obsidian-claude", "node_count": 8, "authorized": True},
            ],
            ["obsidian", "phone"],
        )
        by_id = {row["id"]: row for row in merged}
        self.assertEqual(by_id["obsidian"]["node_count"], 158)
        self.assertEqual(by_id["obsidian"]["label"], "Obsidian")
        self.assertEqual(by_id["phone"]["node_count"], 0,
                         "a declared source with no index stays visible at zero")


if __name__ == "__main__":
    unittest.main()
