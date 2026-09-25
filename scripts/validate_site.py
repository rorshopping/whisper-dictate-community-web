#!/usr/bin/env python3
"""Offline validation for the Whisper Dictate community site.

The validator intentionally uses only Python's standard library. It checks the
static files, local links and fragments, the release manifest shape, and the
security-header configuration without downloading anything.
"""

from __future__ import annotations

import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
HTML_FILES = (ROOT / "index.html", ROOT / "privacy.html", ROOT / "licenses.html")
REQUIRED_FILES = (
    *HTML_FILES,
    ROOT / "styles.css",
    ROOT / "app.js",
    ROOT / "releases.json",
    ROOT / "vercel.json",
    ROOT / "README.md",
)

FAKE_HOST_PARTS = ("example.com", "example.org", "localhost", "127.0.0.1")
FAKE_URL_PARTS = ("replace-me", "your-release", "path/to/", "coming-soon")
# Only these signing states may be published. Anything else is a wording bug:
# a build that is not signed must say so, and a signed build must name the gate.
SIGNING_VALUES = {
    "unsigned-preview",
    "unsigned-portable",
    "developer-id-notarized",
}
SECRET_PATTERNS = (
    re.compile(r"sk_live_[A-Za-z0-9]+"),
    re.compile(r"sk_test_[A-Za-z0-9]+"),
    re.compile(r"whsec_[A-Za-z0-9]+"),
    re.compile(r"AWS_SECRET_ACCESS_KEY\s*[:=]", re.IGNORECASE),
    re.compile(r"STRIPE_SECRET_KEY\s*[:=]", re.IGNORECASE),
    re.compile(r"BEGIN (?:RSA|OPENSSH|EC) PRIVATE KEY"),
)
PAID_MARKERS = (
    re.compile(r"stripe\.com", re.IGNORECASE),
    re.compile(r"buy\.stripe", re.IGNORECASE),
    re.compile(r"€\s*40", re.IGNORECASE),
    re.compile(r"14[- ]day", re.IGNORECASE),
    re.compile(r"money[- ]back", re.IGNORECASE),
    re.compile(r"free trial", re.IGNORECASE),
)


class DocumentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.duplicate_ids: set[str] = set()
        self.links: list[tuple[str, str, int]] = []
        self.sources: list[tuple[str, int]] = []
        self.h1_count = 0
        self.main_count = 0
        self.lang = ""
        self.title_count = 0
        self.inline_event_attributes: list[tuple[str, int]] = []
        self.aria_refs: list[tuple[str, str, int]] = []
        self.has_skip_link = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if "id" in values:
            if values["id"] in self.ids:
                self.duplicate_ids.add(values["id"])
            self.ids.add(values["id"])
        if tag == "h1":
            self.h1_count += 1
        if tag == "main":
            self.main_count += 1
        if tag == "html":
            self.lang = values.get("lang", "")
        if tag == "title":
            self.title_count += 1
        if "href" in values:
            self.links.append((values["href"], "href", self.getpos()[0]))
        if "src" in values:
            self.sources.append((values["src"], self.getpos()[0]))
        for key in values:
            if key.lower().startswith("on"):
                self.inline_event_attributes.append((key, self.getpos()[0]))
        for attr in ("aria-describedby", "aria-labelledby", "aria-controls"):
            if attr in values:
                for target in values[attr].split():
                    self.aria_refs.append((attr, target, self.getpos()[0]))
        classes = values.get("class", "").split()
        if tag == "a" and "skip-link" in classes:
            self.has_skip_link = True

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def parse_document(path: Path, errors: list[str]) -> DocumentParser:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        fail(errors, f"{path.relative_to(ROOT)}: cannot read ({exc})")
        return DocumentParser()

    parser = DocumentParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:  # HTMLParser can still raise on malformed markup.
        fail(errors, f"{path.relative_to(ROOT)}: HTML parse error ({exc})")

    if parser.lang != "en":
        fail(errors, f"{path.relative_to(ROOT)}: <html> must declare lang=\"en\"")
    if parser.h1_count != 1:
        fail(errors, f"{path.relative_to(ROOT)}: expected exactly one h1, found {parser.h1_count}")
    if parser.main_count != 1:
        fail(errors, f"{path.relative_to(ROOT)}: expected exactly one main, found {parser.main_count}")
    if parser.title_count != 1:
        fail(errors, f"{path.relative_to(ROOT)}: expected exactly one title, found {parser.title_count}")
    if parser.duplicate_ids:
        fail(errors, f"{path.relative_to(ROOT)}: duplicate ids: {sorted(parser.duplicate_ids)}")
    if not parser.has_skip_link:
        fail(errors, f"{path.relative_to(ROOT)}: missing skip link")
    if parser.inline_event_attributes:
        fail(errors, f"{path.relative_to(ROOT)}: inline event attributes are not allowed")
    for attr, target, line in parser.aria_refs:
        if target not in parser.ids:
            fail(errors, f"{path.relative_to(ROOT)}:{line}: {attr} references missing id {target!r}")
    return parser


def is_external(url: str) -> bool:
    parsed = urlparse(url)
    return bool(parsed.scheme or parsed.netloc)


def validate_external_url(value: object, location: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not value:
        fail(errors, f"{location}: URL must be a non-empty string")
        return
    if value.startswith("data:") or value.startswith("mailto:"):
        return
    parsed = urlparse(value)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        fail(errors, f"{location}: external URL must use https ({value!r})")
        return
    host = parsed.netloc.lower()
    if any(part in host for part in FAKE_HOST_PARTS):
        fail(errors, f"{location}: placeholder host is not publishable ({value!r})")
    if any(part in value.lower() for part in FAKE_URL_PARTS):
        fail(errors, f"{location}: placeholder URL fragment ({value!r})")


def local_target(base: Path, value: str) -> Path:
    path_part = unquote(value.split("#", 1)[0].split("?", 1)[0])
    if path_part.startswith("/"):
        candidate = ROOT / path_part.lstrip("/")
    else:
        candidate = base.parent / path_part
    if candidate.is_dir():
        candidate = candidate / "index.html"
    if not candidate.suffix and not candidate.exists():
        candidate = candidate.with_suffix(".html")
    return candidate.resolve()


def validate_local_links(path: Path, parser: DocumentParser, errors: list[str]) -> None:
    for value, attribute, line in [*parser.links, *[(src, "src", src_line) for src, src_line in parser.sources]]:
        if not value:
            fail(errors, f"{path.relative_to(ROOT)}:{line}: empty {attribute} link")
            continue
        if value.startswith("#"):
            fragment = unquote(value[1:])
            if not fragment or fragment not in parser.ids:
                fail(errors, f"{path.relative_to(ROOT)}:{line}: fragment #{fragment!r} not found")
            continue
        if value.startswith("data:"):
            continue
        if is_external(value):
            validate_external_url(value, f"{path.relative_to(ROOT)}:{line} ({attribute})", errors)
            continue
        target = local_target(path, value)
        try:
            target.relative_to(ROOT)
        except ValueError:
            fail(errors, f"{path.relative_to(ROOT)}:{line}: link escapes site root ({value!r})")
            continue
        if not target.exists():
            fail(errors, f"{path.relative_to(ROOT)}:{line}: missing local target {value!r}")
            continue
        if "#" in value:
            fragment = unquote(value.split("#", 1)[1])
            if target.suffix.lower() == ".html":
                target_parser = parse_document(target, errors)
                if fragment and fragment not in target_parser.ids:
                    fail(errors, f"{path.relative_to(ROOT)}:{line}: fragment #{fragment} not found in {target.name}")


def validate_manifest(errors: list[str]) -> None:
    path = ROOT / "releases.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(errors, f"releases.json: cannot parse ({exc})")
        return

    if data.get("schema_version") != 1:
        fail(errors, "releases.json: schema_version must be 1")
    project = data.get("project")
    if not isinstance(project, dict):
        fail(errors, "releases.json: project must be an object")
    else:
        for field in ("name", "edition", "repository"):
            if not isinstance(project.get(field), str) or not project[field].strip():
                fail(errors, f"releases.json: project.{field} is required")

    release = data.get("release")
    if not isinstance(release, dict):
        fail(errors, "releases.json: release must be an object")
        release = {}
    status = release.get("status")
    if status not in {"placeholder", "published"}:
        fail(errors, "releases.json: release.status must be placeholder or published")
    if status == "published":
        for field in ("version", "published_at", "release_page"):
            if not isinstance(release.get(field), str) or not release[field].strip():
                fail(errors, f"releases.json: published release needs {field}")
        validate_external_url(release["release_page"], "releases.json release.release_page", errors)
    else:
        for field in ("version", "published_at", "release_page"):
            if release.get(field) is not None:
                fail(errors, f"releases.json: placeholder release.{field} must be null")

    source = data.get("source")
    if not isinstance(source, dict):
        fail(errors, "releases.json: source must be an object")
    else:
        for field in ("repository", "readme", "license", "third_party_notices"):
            validate_external_url(source.get(field, ""), f"releases.json source.{field}", errors)

    artifacts = data.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != 4:
        fail(errors, "releases.json: artifacts must contain exactly four entries")
        artifacts = []
    expected = {
        ("windows-x64", "portable_zip"),
        ("windows-x64", "installer"),
        ("macos-apple-silicon", "portable_zip"),
        ("macos-apple-silicon", "installer"),
    }
    actual: set[tuple[str, str]] = set()
    for index, artifact in enumerate(artifacts):
        location = f"releases.json artifacts[{index}]"
        if not isinstance(artifact, dict):
            fail(errors, f"{location}: must be an object")
            continue
        platform = artifact.get("platform")
        kind = artifact.get("kind")
        actual.add((platform, kind))
        for field in ("id", "platform", "platform_label", "kind", "label"):
            if not isinstance(artifact.get(field), str) or not artifact[field].strip():
                fail(errors, f"{location}: {field} is required")
        if kind == "portable_zip" and not str(artifact.get("label", "")).lower().endswith("zip"):
            fail(errors, f"{location}: portable_zip label must identify a ZIP")
        url = artifact.get("url")
        filename = artifact.get("filename")
        digest = artifact.get("sha256")
        size = artifact.get("size_bytes")
        signing = artifact.get("signing")
        if url is None:
            for field in ("filename", "sha256", "size_bytes", "signing"):
                if artifact.get(field) is not None:
                    fail(errors, f"{location}: unavailable artifact must have {field}=null")
            continue
        if not isinstance(url, str) or not isinstance(filename, str) or not filename.strip():
            fail(errors, f"{location}: an available artifact needs filename and url")
            continue
        if not isinstance(signing, str) or not signing.strip():
            fail(errors, f"{location}: an available artifact needs an explicit signing value")
        elif signing not in SIGNING_VALUES:
            fail(errors, f"{location}: unknown signing value {signing!r}, expected one of {sorted(SIGNING_VALUES)!r}")
        validate_external_url(url, f"{location}.url", errors)
        if not re.fullmatch(r"[a-f0-9]{64}", str(digest or ""), re.IGNORECASE):
            fail(errors, f"{location}: available artifact needs a 64-character SHA-256")
        if not isinstance(size, int) or size <= 0:
            fail(errors, f"{location}: available artifact needs a positive size_bytes")
        if kind == "portable_zip" and not filename.lower().endswith(".zip"):
            fail(errors, f"{location}: portable_zip filename must end in .zip")
    if actual != expected:
        fail(errors, f"releases.json: artifact platform/kind matrix must be {sorted(expected)!r}")

    models = data.get("models")
    if not isinstance(models, list) or len(models) != 2:
        fail(errors, "releases.json: models must contain the two documented profiles")
    else:
        for index, model in enumerate(models):
            location = f"releases.json models[{index}]"
            if not isinstance(model, dict):
                fail(errors, f"{location}: must be an object")
                continue
            for field in ("id", "language", "size", "page", "license_name", "license"):
                if not isinstance(model.get(field), str) or not model[field].strip():
                    fail(errors, f"{location}: {field} is required")
            validate_external_url(model.get("page", ""), f"{location}.page", errors)
            validate_external_url(model.get("license", ""), f"{location}.license", errors)


def validate_vercel(errors: list[str]) -> None:
    path = ROOT / "vercel.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(errors, f"vercel.json: cannot parse ({exc})")
        return
    if data.get("cleanUrls") is not True or data.get("trailingSlash") is not False:
        fail(errors, "vercel.json: expected cleanUrls=true and trailingSlash=false")
    headers = data.get("headers")
    if not isinstance(headers, list):
        fail(errors, "vercel.json: headers must be an array")
        return
    by_source = {entry.get("source"): entry.get("headers", []) for entry in headers if isinstance(entry, dict)}
    required = {
        "Content-Security-Policy",
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
        "Permissions-Policy",
    }
    found = {
        item.get("key")
        for source, entries in by_source.items()
        if source == "/(.*)"
        for item in entries
        if isinstance(item, dict)
    }
    missing = required - found
    if missing:
        fail(errors, f"vercel.json: missing security headers: {sorted(missing)}")
    csp = next(
        (item.get("value", "") for item in by_source.get("/(.*)", []) if item.get("key") == "Content-Security-Policy"),
        "",
    )
    for directive in ("default-src 'self'", "object-src 'none'", "frame-ancestors 'none'", "connect-src 'self'"):
        if directive not in csp:
            fail(errors, f"vercel.json: CSP missing {directive!r}")


def scan_safety(errors: list[str]) -> None:
    public_files = [
        path
        for path in ROOT.rglob("*")
        if path.is_file() and path.name not in {".DS_Store"}
    ]
    for path in public_files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        relative = path.relative_to(ROOT)
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                fail(errors, f"{relative}: possible secret material ({pattern.pattern})")
        if path.suffix.lower() in {".html", ".css", ".js", ".json"}:
            for marker in PAID_MARKERS:
                if marker.search(text):
                    fail(errors, f"{relative}: paid-site wording or payment endpoint found ({marker.pattern})")


def main() -> int:
    errors: list[str] = []
    for path in REQUIRED_FILES:
        if not path.is_file():
            fail(errors, f"missing required file: {path.relative_to(ROOT)}")

    parsers: dict[Path, DocumentParser] = {}
    for path in HTML_FILES:
        if path.is_file():
            parser = parse_document(path, errors)
            parsers[path] = parser
            validate_local_links(path, parser, errors)

    validate_manifest(errors)
    validate_vercel(errors)
    scan_safety(errors)

    if errors:
        print("Community site validation FAILED:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"Community site validation passed: {len(parsers)} HTML pages, 4 artifact entries, local links, manifest, and security headers checked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
