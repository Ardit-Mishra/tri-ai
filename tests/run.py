"""Run the Tri-AI board tests. Exit code 0 = pass, 1 = fail.

Must run under an interpreter that can import the Hermes kernel — by default
the Hermes install's own venv. `tests/run.ps1` picks it for you.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover(str(HERE), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
