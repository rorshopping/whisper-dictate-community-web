"""Smoke test for the static community site.

This deliberately invokes the standard-library validator rather than adding a
web test dependency or making a network request.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = REPO_ROOT / "scripts" / "validate_site.py"
MANIFEST = REPO_ROOT / "releases.json"
APP_JS = REPO_ROOT / "app.js"
INDEX_HTML = REPO_ROOT / "index.html"
SIGNING_VALUES = {"unsigned-preview", "unsigned-portable", "developer-id-notarized"}


def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


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

    def test_published_artifacts_declare_a_known_signing_state(self) -> None:
        manifest = load_manifest()
        if manifest["release"]["status"] != "published":
            self.skipTest("no published release to check")
        published = [a for a in manifest["artifacts"] if a.get("url")]
        self.assertTrue(published, "a published release must advertise at least one artifact")
        for artifact in published:
            with self.subTest(artifact=artifact.get("id")):
                signing = artifact.get("signing")
                self.assertIsInstance(signing, str, "an available artifact must carry a signing value")
                self.assertIn(signing, SIGNING_VALUES)

    def test_unavailable_artifacts_are_explicitly_null(self) -> None:
        for artifact in load_manifest()["artifacts"]:
            if artifact.get("url"):
                continue
            with self.subTest(artifact=artifact.get("id")):
                for field in ("filename", "url", "sha256", "size_bytes", "signing"):
                    self.assertIsNone(artifact.get(field), f"{field} must be null while unavailable")

    def test_windows_artifact_is_labelled_unsigned(self) -> None:
        manifest = load_manifest()
        if manifest["release"]["status"] != "published":
            self.skipTest("no published release to check")
        for artifact in manifest["artifacts"]:
            if artifact.get("platform") != "windows-x64" or not artifact.get("url"):
                continue
            with self.subTest(artifact=artifact.get("id")):
                self.assertTrue(str(artifact["signing"]).startswith("unsigned-"))

    def test_app_js_renders_the_signing_label(self) -> None:
        source = APP_JS.read_text(encoding="utf-8")
        self.assertIn("SIGNING_LABELS", source)
        self.assertIn("dataset.signing", source)
        # The page must not claim a verified release for a release whose
        # Windows artifact is an unsigned preview.
        self.assertNotIn("Verified community release", source)
        self.assertIn("Published community preview", source)

    def test_index_html_does_not_claim_windows_is_signed(self) -> None:
        text = INDEX_HTML.read_text(encoding="utf-8")
        claim = re.search(
            r"windows[^.]{0,80}(?<!un)(?:signed|notarized)",
            text,
            re.IGNORECASE,
        )
        self.assertIsNone(
            claim,
            "the page must not imply the Windows build is signed: " + (claim.group(0) if claim else ""),
        )
        self.assertIn("unsigned", text.lower(), "the page must state the Windows preview is unsigned")


if __name__ == "__main__":
    unittest.main()
