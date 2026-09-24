"""Smoke test for the static community site.

This deliberately invokes the standard-library validator rather than adding a
web test dependency or making a network request.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = REPO_ROOT / "scripts" / "validate_site.py"


class CommunitySiteTests(unittest.TestCase):
    def test_html_links_manifest_and_headers(self) -> None:
        result = subprocess.run(
            [sys.executable, str(VALIDATOR)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=(result.stdout + result.stderr).strip(),
        )
        self.assertIn("validation passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
