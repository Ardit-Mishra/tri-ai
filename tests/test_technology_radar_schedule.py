"""The technology radar is refreshable and schedulable without host execution."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TechnologyRadarScheduleTests(unittest.TestCase):
    def test_runner_refreshes_catalog_before_running_the_radar(self):
        text = (ROOT / "scripts" / "run-technology-radar.ps1").read_text(encoding="utf-8")
        self.assertLess(text.index("capability_catalog.py"), text.index("technology_radar.py"))
        self.assertIn("--evaluate-limit", text)

    def test_registration_is_weekly_bounded_and_single_instance(self):
        text = (ROOT / "scripts" / "register-technology-radar.ps1").read_text(encoding="utf-8")
        self.assertIn("New-ScheduledTaskTrigger -Weekly", text)
        self.assertIn("-MultipleInstances IgnoreNew", text)
        self.assertIn("-ExecutionTimeLimit", text)
        self.assertIn("run-technology-radar.ps1", text)


if __name__ == "__main__":
    unittest.main()
