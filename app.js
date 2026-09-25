(() => {
  "use strict";

  const manifestUrl = "./releases.json";
  const statusElement = document.querySelector("#release-status");
  const checksumElement = document.querySelector("#checksum-list");

  function isHttpsUrl(value) {
    if (typeof value !== "string" || value.length === 0) {
      return false;
    }
    try {
      const url = new URL(value, window.location.href);
      const host = url.hostname.toLowerCase();
      const placeholderHost = ["example.com", "example.org", "localhost", "127.0.0.1"].some((part) => host.includes(part));
      return url.protocol === "https:" && !placeholderHost;
    } catch (_error) {
      return false;
    }
  }

  const SIGNING_LABELS = {
    "unsigned-preview": "Unsigned preview",
    "unsigned-portable": "Unsigned portable preview",
    "developer-id-notarized": "Developer ID signed and notarized",
  };

  function signingLabel(artifact) {
    const value = artifact && typeof artifact.signing === "string" ? artifact.signing.trim() : "";
    if (!value) {
      return null;
    }
    return SIGNING_LABELS[value] || value;
  }

  function isUsableArtifact(manifest, artifact) {
    const safeDigest = artifact && typeof artifact.sha256 === "string" && /^[a-f0-9]{64}$/i.test(artifact.sha256);
    const safeFilename = artifact && typeof artifact.filename === "string" && artifact.filename.trim().length > 0;
    const safeZip = !artifact || artifact.kind !== "portable_zip" || /\.zip$/i.test(artifact.filename || "");
    const labelled = signingLabel(artifact) !== null;
    return Boolean(
      manifest &&
      manifest.release &&
      manifest.release.status === "published" &&
      artifact &&
      isHttpsUrl(artifact.url) &&
      safeFilename &&
      safeDigest &&
      safeZip &&
      labelled &&
      Number.isInteger(artifact.size_bytes) &&
      artifact.size_bytes > 0
    );
  }

  function setUnavailable(button, label) {
    button.disabled = true;
    button.setAttribute("aria-disabled", "true");
    button.textContent = label || "Not published yet";
  }

  function makeDownloadLink(button, artifact, platformLabel) {
    const link = document.createElement("a");
    const signed = signingLabel(artifact);
    link.className = button.className;
    link.href = artifact.url;
    link.textContent = `Download ${artifact.label}`;
    link.setAttribute(
      "aria-label",
      `Download ${artifact.label} for ${platformLabel}: ${artifact.filename} (${signed})`,
    );
    link.setAttribute("rel", "noopener noreferrer");
    link.dataset.download = "enabled";
    link.dataset.platform = button.dataset.platform;
    link.dataset.artifact = button.dataset.artifact;
    link.dataset.signing = artifact.signing;
    link.title = signed;
    return link;
  }

  function renderDownloads(manifest) {
    const artifacts = Array.isArray(manifest.artifacts) ? manifest.artifacts : [];
    const artifactMap = new Map(artifacts.map((artifact) => [artifact.id, artifact]));

    document.querySelectorAll("[data-download]").forEach((button) => {
      const id = `${button.dataset.platform}-${button.dataset.artifact.replace(/_/g, "-")}`;
      const artifact = artifactMap.get(id);
      const platformLabel = artifact ? artifact.platform_label : "this platform";

      if (isUsableArtifact(manifest, artifact)) {
        button.replaceWith(makeDownloadLink(button, artifact, platformLabel));
      } else {
        setUnavailable(button, "Not published yet");
      }
    });
  }

  function appendChecksumRow(filename, digest, kind) {
    const row = document.createElement("div");
    row.className = "checksum-row";

    const file = document.createElement("code");
    file.className = "checksum-file";
    file.textContent = filename;

    const hash = document.createElement("code");
    hash.className = "checksum-hash";
    hash.textContent = digest;

    const type = document.createElement("span");
    type.className = "checksum-kind";
    type.textContent = kind;

    row.append(file, hash, type);
    checksumElement.append(row);
  }

  function renderChecksums(manifest) {
    checksumElement.replaceChildren();
    const artifacts = Array.isArray(manifest.artifacts) ? manifest.artifacts : [];
    const published = artifacts.filter(
      (artifact) =>
        isUsableArtifact(manifest, artifact) &&
        typeof artifact.sha256 === "string" &&
        /^[a-f0-9]{64}$/i.test(artifact.sha256)
    );

    if (published.length === 0) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = "SHA-256 checksums will appear here when a canonical release is published.";
      checksumElement.append(empty);
      return;
    }

    published.forEach((artifact) => {
      const signed = signingLabel(artifact);
      appendChecksumRow(artifact.filename, artifact.sha256, `${artifact.label} · ${signed}`);
    });
  }

  function renderStatus(manifest) {
    const release = manifest && manifest.release ? manifest.release : {};
    const isPublished = release.status === "published";
    statusElement.classList.toggle("status-published", isPublished);
    statusElement.classList.toggle("status-placeholder", !isPublished);

    if (isPublished) {
      const version = release.version ? ` ${release.version}` : "";
      statusElement.textContent = `Published community preview${version}. Windows is an explicitly unsigned preview; the macOS artifact is Developer ID signed and notarized. Check each artifact’s signing label and checksum before running it.`;
    } else {
      statusElement.textContent = "No community release is published yet. Download buttons remain disabled until the manifest points to canonical artifacts.";
    }
  }

  async function loadManifest() {
    try {
      const response = await fetch(manifestUrl, { cache: "no-store", credentials: "same-origin" });
      if (!response.ok) {
        throw new Error(`manifest request returned ${response.status}`);
      }
      const manifest = await response.json();
      renderStatus(manifest);
      renderDownloads(manifest);
      renderChecksums(manifest);
    } catch (_error) {
      statusElement.classList.add("status-placeholder");
      statusElement.textContent = "The release manifest could not be loaded. Downloads remain disabled; inspect releases.json before using a build.";
      document.querySelectorAll("[data-download]").forEach((button) => setUnavailable(button, "Manifest unavailable"));
      checksumElement.replaceChildren();
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = "Checksums are unavailable because releases.json could not be loaded.";
      checksumElement.append(empty);
    }
  }

  loadManifest();
})();
