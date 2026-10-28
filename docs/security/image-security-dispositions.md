# Image security dispositions

The [image security gate](../../scripts/image_security_gate.py) preserves raw Trivy
findings and separately records narrow, evidence-backed dispositions. A successful
effective gate does not mean the raw scan has zero findings. Both reports remain
reviewable. Unknown advisories, package/version/path tuples, ambiguous origins and
incomplete evidence block the gate; no package-wide ignore, severity reduction or
`ignore-unfixed` option is used.

Run `python -m scripts.image_security_gate` from the repository root, with `--scan`,
`--inventory`, `--runtime-manifest`, `--smoke`, `--sbom-inventory`, `--image-id` and
`--output`. The tool
uses only Python's standard library and the existing image inventory policy. Exit 0
means every reported finding has a reviewed disposition; exit 1 means unresolved
findings or invalid evidence remain. CLI errors also fail; callers must check exit
status and retain the input files.

## Required evidence

- Trivy's exact `Metadata.ImageID` must equal the supplied SHA-256 image config ID,
  inventory ID and smoke ID. The scan must identify a Debian container image, include
  the OS result and have an aware timestamp no older than 24 hours or more than five
  minutes into the future. Its original bytes are hashed into the effective report.
- Inventory must be complete, cover the unchanged inventory scopes and pass the
  minimal-runtime policy. Its original dpkg status hash must match the runtime
  manifest. Finding package versions must match the recorded builder package set;
  retaining that metadata does not assert the corresponding code is installed.
- Smoke must pass imports for the same runtime role and report the SHA-256 of the
  exact runtime manifest read inside the image. Worker smoke must also match all
  three pinned synthetic font hashes, execute the four installer-import rejection
  probes and identify the installed child hash. The installed validator parent,
  child and fixed profile hashes must match reviewed checkout sources.
- The scanner version is pinned to 0.70.0. A complete SBOM-input export inventory
  must bind the same image and runtime-manifest hash. Declared image volumes refuse
  because exports can otherwise hide image paths. Nonempty secret, configuration
  or license findings also block; their contents are not echoed in coded errors.
- Malformed or oversized JSON, duplicate keys, changed source, substituted manifests,
  failed probes, unknown versions, privileged files and conflicting presence records
  fail closed. Component absence must agree with both inventory and copied files;
  cached PackageIndex bytecode is treated as code presence too.

The inputs are collected by trusted CI directly from the image; this verifier is not
an attestation service for arbitrary uploaded reports. No network lookup or installer
import occurs in the gate.

## Reviewed scope and limits

The exact Debian binary/version allowlists cover eight advisories: ncurses
[`infocmp`](https://security-tracker.debian.org/tracker/CVE-2025-69720),
[`systemd-homed`](https://security-tracker.debian.org/tracker/CVE-2026-16742),
[`libacl`](https://security-tracker.debian.org/tracker/CVE-2026-54369),
[`mount`/`libmount`](https://security-tracker.debian.org/tracker/CVE-2026-76642),
[`nsenter`](https://security-tracker.debian.org/tracker/CVE-2026-78408),
the additional mount advisories
[`CVE-2026-78409`](https://security-tracker.debian.org/tracker/CVE-2026-78409) and
[`CVE-2026-78410`](https://security-tracker.debian.org/tracker/CVE-2026-78410), and
Perl [`Archive::Tar`](https://security-tracker.debian.org/tracker/CVE-2026-9538).
These receive a code-absence disposition only when their exact affected component
is absent. Other advisories in those packages remain blocking.

Pip-vendored [msgpack's native decoder](https://github.com/msgpack/msgpack-python/commit/2c56ddb5d0025ed481d962c0f5d62d19dec7476d)
and the additional native-only
[`CVE-2026-114613`](https://github.com/msgpack/msgpack-python/security/advisories/GHSA-j586-36cw-2gc2),
and setuptools' [PackageIndex](https://github.com/pypa/setuptools/security/advisories/GHSA-5rjg-fvgr-3xxf)
have separate absence rules. Two exact urllib3 advisories concern
[HTTPS proxy TLS](https://github.com/urllib3/urllib3/security/advisories/GHSA-8988-9cw3-xx77)
and [chunk-response reading](https://github.com/urllib3/urllib3/security/advisories/GHSA-vxq7-64xx-v4gw).
Their code remains installed. A disposition is limited to the verified worker's
fixed FontBakery profile, whose child rejects installer imports before discovery;
this is an application call-path invariant, not an OS sandbox or permission to use
pip/other FontBakery profiles at runtime.

Every Python rule requires the exact pip vendor origin, pinned versions and matching
metadata hashes. Trivy can aggregate pip's embedded SBOM into a `Python` result
without `PkgPath`, as described by the [scanner project](https://github.com/aquasecurity/trivy/discussions/11031).
Names, versions and BOM references alone never establish origin. A different
installed copy is not covered.

## Exclusive scanner-input provenance

The [SBOM input collector](../../scripts/image_sbom_inventory.py) reads an entire
plain tar stream from a nonstarted container: `docker export` feeds
`python -m scripts.image_sbom_inventory --image-id SHA --runtime-manifest FILE
--output FILE`. CI verifies that container's exact image ID and empty mount list
before export and removes it afterward. The collector neither extracts nor executes
files. It caps raw tar bytes at 2 GB, members at 100,000, extended metadata headers
at 64 KiB and each SBOM at 1 MiB. Duplicate/unsafe paths, incomplete members, missing
end markers, nonzero trailing data and nonregular or extra SBOM inputs refuse.

Every copied manifest path must occur with the expected kind, file size or symlink
target. This is coverage verification, **not a rehash of ordinary exported files**.
Recognized SBOM bytes are independently hashed against the copied-file manifest,
retained in the proof and parsed again by the gate. Generated files are included in
the exhaustive pathname scan too. The current reviewed pip document is CycloneDX
1.4; its root component has no version. Pip's own distribution metadata supplies
the separately checked root version.

The collector mirrors the pinned scanner's
[`Required()` filter](https://github.com/aquasecurity/trivy/blob/v0.70.0/pkg/fanal/analyzer/sbom/sbom.go):
case-sensitive `.spdx`, `.spdx.json`, `.cdx` and `.cdx.json` suffixes, excluding any
path containing `.dist-info/sboms/`. The proof records this reviewed source hash and
filter. CI does not configure custom analyzer file patterns or external SBOM sources.

A missing or empty `PkgPath` may receive a **separately recorded derived origin**
only when the exhaustive export has exactly one recognized input, the bound pip
vendor SBOM. The raw finding must join uniquely to a raw package row by package ID,
UID, name/version, complete PURL/BOM reference and layer; that layer must belong to
the scanned image. The row must have the exact analyzer string `sbom`, with no
conflicting file path. Its identity must occur exactly once in the independently
parsed document. This establishes origin through an exclusive parser input plus
scanner attribution, rather than guessing from a matching package name. Raw findings
remain unchanged. Missing, ambiguous, combined-analyzer or conflicting provenance
continues to block, as do unreviewed advisories after origin is established.

Source tests demonstrate failure behavior using synthetic evidence. They do not
establish that a new Linux image builds, passes smoke or receives clearance. Actual
image reports and the remaining application/security gates determine PR acceptance.
Private handwriting/fonts, model quality, merging and deployment are outside this
review.
