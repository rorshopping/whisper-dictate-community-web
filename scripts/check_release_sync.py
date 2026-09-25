#!/usr/bin/env python3
"""Compare releases.json with the artifacts actually published on GitHub.

`validate_site.py` is offline on purpose. This script is the complementary
online check: it downloads only the small release metadata, never the payload
binaries, and fails when the site advertises a filename, size, or checksum that
the published release does not carry.

Usage:
    python scripts/check_release_sync.py [--release community-v0.1.0]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "releases.json"
TIMEOUT_SECONDS = 30
USER_AGENT = "whisper-dictate-community-release-check"


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310 - fixed https URL
        if response.status != 200:
            raise RuntimeError(f"{url} returned HTTP {response.status}")
        return json.loads(response.read().decode("utf-8"))


def release_tag(manifest: dict, override: str | None) -> str:
    if override:
        return override
    page = (manifest.get("release") or {}).get("release_page")
    if isinstance(page, str) and page:
        match = re.search(r"/releases/tag/([^/?#]+)$", page)
        if match:
            return match.group(1)
    raise RuntimeError("cannot determine the release tag; pass --release")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", help="release tag, for example community-v0.1.0")
    args = parser.parse_args()

    site = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if (site.get("release") or {}).get("status") != "published":
        print("releases.json is not published; nothing to compare against the release.")
        return 0

    errors: list[str] = []
    tag = release_tag(site, args.release)
    repo = urlparse((site.get("project") or {}).get("repository", "")).path.strip("/")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        print(f"releases.json project.repository is not a GitHub slug: {repo!r}")
        return 1

    try:
        release = fetch_json(f"https://api.github.com/repos/{repo}/releases/tags/{tag}")
    except (urllib.error.URLError, RuntimeError, ValueError) as exc:
        print(f"could not read the published release: {exc}")
        return 1

    if release.get("tag_name") != tag:
        errors.append(f"published tag is {release.get('tag_name')!r}, expected {tag!r}")
    if release.get("draft"):
        errors.append("published release is a draft")
    if release.get("prerelease") is not True:
        errors.append("published release is not marked as a prerelease")

    assets = {asset["name"]: asset for asset in release.get("assets", []) if isinstance(asset, dict)}
    for name, asset in assets.items():
        if asset.get("state") != "uploaded":
            errors.append(f"release asset {name} is in state {asset.get('state')!r}")

    checked = 0
    for artifact in site.get("artifacts", []):
        if not isinstance(artifact, dict) or not artifact.get("url"):
            continue
        filename = artifact["filename"]
        asset = assets.get(filename)
        if asset is None:
            errors.append(f"site advertises {filename}, which the release does not contain")
            continue
        if asset.get("size") != artifact.get("size_bytes"):
            errors.append(
                f"{filename}: site size {artifact.get('size_bytes')} != released size {asset.get('size')}"
            )
        checked += 1

    # Cross-check the small machine-readable manifest the release publishes, so
    # the checksums the site shows are the ones that were published.
    if "release-manifest.json" in assets:
        try:
            payload = fetch_json(assets["release-manifest.json"]["browser_download_url"])
        except (urllib.error.URLError, RuntimeError, ValueError) as exc:
            errors.append(f"could not read the published release-manifest.json: {exc}")
        else:
            published = {entry["name"]: entry for entry in payload.get("assets", []) if isinstance(entry, dict)}
            for artifact in site.get("artifacts", []):
                if not isinstance(artifact, dict) or not artifact.get("url"):
                    continue
                entry = published.get(artifact["filename"])
                if entry is None:
                    errors.append(f"{artifact['filename']} is missing from the published release manifest")
                    continue
                if entry.get("sha256") != artifact.get("sha256"):
                    errors.append(
                        f"{artifact['filename']}: site sha256 {artifact.get('sha256')} != published {entry.get('sha256')}"
                    )
                if entry.get("size_bytes") != artifact.get("size_bytes"):
                    errors.append(
                        f"{artifact['filename']}: site size {artifact.get('size_bytes')} != published manifest {entry.get('size_bytes')}"
                    )

    if errors:
        print("Release sync check FAILED:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"Release sync check passed: {checked} advertised artifact(s) match {tag} and its published checksums.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
