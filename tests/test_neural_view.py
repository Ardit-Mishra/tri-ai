"""The topology reads as a brain, and reads that way on a phone.

Two things were wrong with the spatial view as shipped.

The first was that a phone never saw it. `setView(window.innerWidth > 767 ...)`
chose the flat 2D ring below 768px, and the phone is the surface this system is
actually operated from - the operator's own screenshot of the live dashboard is
the 2D ring, on a device whose browser runs WebGL perfectly well. The 3D view
existed and was simply gated off from the only place it mattered.

The second was the layout. A Fibonacci sphere is an even scatter on a ball;
the operator asked for something that reads as a brain - lobes, a fissure, a
folded surface - with neurons firing across it. That is a different shape and a
different set of connections, not a styling change.

These are source assertions plus a real `node --check`, which is the same
contract `test_dashboard_template_syntax` holds the inline script to. They
cannot prove the thing looks right; nothing here can. They pin the decisions
that were made on purpose so a later edit has to argue with them.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

SOURCE = Path(__file__).resolve().parents[1] / "src" / "dashboard" / "tri_space.js"


class SyntaxTest(unittest.TestCase):
    def test_the_module_parses_as_javascript(self):
        """A syntax error here is a blank panel with a console warning, and the
        try/catch around the whole view would report it as 'unavailable'."""
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed on this machine")
        proc = subprocess.run(
            [node, "--input-type=module", "--check"],
            input=SOURCE.read_text(encoding="utf-8"),
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)


class _Source(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text(encoding="utf-8")


class PhoneTest(_Source):
    def test_a_phone_is_no_longer_sent_to_the_flat_ring(self):
        """The dashboard is read from a phone. Gating the view on viewport
        width meant the operator had never seen it."""
        self.assertNotIn('window.innerWidth > 767 ? "3d" : "2d"', self.source)

    def test_the_three_dimensional_view_is_the_default(self):
        self.assertIn('setView("3d")', self.source)

    def test_the_flat_view_is_still_reachable_by_choice(self):
        """Removing the fallback would strand anyone whose GPU refuses."""
        self.assertIn('setView("2d")', self.source)
        self.assertIn("button2d", self.source)


class BrainShapeTest(_Source):
    def test_positions_pass_through_a_named_brain_deformation(self):
        self.assertIn("function brainShape", self.source)
        self.assertIn("brainShape(", self.source)

    def test_the_hemispheres_are_separated_by_a_fissure(self):
        """Two lobes with a gap is the single feature that makes the
        silhouette read as a brain rather than as a ball."""
        self.assertIn("FISSURE", self.source)

    def test_the_surface_is_folded_rather_than_smooth(self):
        self.assertIn("GYRI", self.source)


class SynapseTest(_Source):
    def test_nodes_are_wired_to_their_nearest_neighbours(self):
        """Real task edges are sparse - most nodes would sit unconnected, and
        an unconnected scatter is not a brain."""
        self.assertIn("buildSynapses", self.source)

    def test_the_synapses_are_one_object_rather_than_one_per_edge(self):
        """A few hundred separate Line objects is a draw call each, on a
        phone, every frame."""
        self.assertIn("LineSegments", self.source)


class FiringTest(_Source):
    def test_signals_travel_along_the_synapses(self):
        self.assertIn("signals", self.source)
        self.assertIn("advanceSignals", self.source)

    def test_firing_stops_when_motion_is_paused(self):
        """The pause control has to mean it, or it is decoration."""
        body = self.source.split("function advanceSignals")[1].split("\n    }")[0]
        self.assertIn("motionPaused", body)

    def test_reduced_motion_is_still_honoured(self):
        self.assertIn("prefers-reduced-motion", self.source)
        self.assertIn("motionPaused = true", self.source)


class BudgetTest(_Source):
    def test_the_signal_count_is_bounded(self):
        """This runs on a phone GPU beside everything else it is doing."""
        self.assertIn("MAX_SIGNALS", self.source)

    def test_the_pixel_ratio_is_capped(self):
        """An uncapped devicePixelRatio renders 3x area on a modern phone."""
        self.assertIn("setPixelRatio", self.source)


if __name__ == "__main__":
    unittest.main()
