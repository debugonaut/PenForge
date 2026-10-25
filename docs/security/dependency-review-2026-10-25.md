# Dependency security review — 2026-10-25

## Compatible frontend repair

Scoped overrides replace Firestore's gRPC dependency with `@grpc/grpc-js` 1.13.6
and Next's PostCSS with 8.5.23. Firebase 13.0.0 and Next 15.5.28 stay unchanged.
The only added transitive package is MIT-licensed `@js-sdsl/ordered-map` 4.4.2.
Both upstream packages still pin older dependencies, so removing these overrides
requires a fresh audit rather than an assumed framework upgrade.

The gRPC [certificate advisory](https://github.com/advisories/GHSA-m9gg-hp2v-232j)
and PostCSS [source-map advisory](https://github.com/advisories/GHSA-fxqj-rqcc-2cmp)
document the patched versions. A clean locked install and npm audit report zero
findings. On Node 24.22.1, all 180 frontend tests, typecheck, lint, production build
and Firebase Auth/Firestore initialization checks pass. These initialization checks
do not sign in or access documents.

Firebase's installed packages require Node 24.12 or newer. The repository now uses
Node 24 LTS with a 24.22.1 minimum, aligning the package engine, `.nvmrc`, setup guide
and CI. This [Node security release](https://nodejs.org/en/blog/release/v24.22.1)
also backports the Brotli decoder fix; its reported Brotli version alone does not
describe that backport. CI runs a locked moderate-or-higher npm audit as a gate.

## Image findings still under review

Stable Debian trixie does not yet supply fixes for the reported
[mount](https://security-tracker.debian.org/tracker/CVE-2026-76642),
[nsenter](https://security-tracker.debian.org/tracker/CVE-2026-78408) and
[ACL](https://security-tracker.debian.org/tracker/CVE-2026-54369) issues.
Installed affected code must remain blocking until safely patched, removed or
resolved by measured runtime evidence.

Some source-package reports concern different components: ncurses `infocmp`,
systemd-homed/homework and Perl Archive::Tar. Pip's msgpack C decoder and setuptools
PackageIndex also require component-specific inspection. Upgrading standalone
libraries does not replace pip's vendored urllib3. Pip 26.2.2 is still the latest
stable release and retains older bundled code; it must not be arbitrarily downgraded
or debundled to change scanner output.

`scripts/image_security_inventory.py` records dpkg/package versions and scoped
component paths, hashes, permissions and selected Python distribution/vendor data.
It does not import installers or read environment/credential values. Missing,
unsafe, inaccessible or bounded-out evidence fails with exit 2; exit 0 means only
that inventory completed. Positive and failure controls cover those distinctions.

CI executes this probe inside the exact API/worker images without network, writes,
capabilities or new privileges, and publishes its JSON alongside image config IDs.
The HIGH/CRITICAL vulnerability scans remain separate and blocking. No VEX or
scanner suppression is introduced by this repair. Actual image evidence is required
before any component-specific disposition; this document is not an all-green or
deployment-readiness claim.

The initial [Linux inventory run](https://github.com/debugonaut/PenForge/actions/runs/40202887884)
confirmed `infocmp`, libacl, libmount, mount and nsenter are present; mount is root-owned
and SUID. None receives an absence disposition. Systemd-homed/homework, Archive::Tar,
pip's native msgpack decoder and setuptools PackageIndex were absent in the scoped
searches. These are candidates for narrow review, not blanket package exemptions.

The probe also found global pip 25.0.1 and its older vendored libraries in both images.
The Dockerfiles now remove global pip, ensurepip's bundled installer and uv after the
locked project environment is built. Worker venv pip/pip-api remain installed, keeping
FontBakery's declared dependencies complete. CI requires the global installer payloads
to be absent, then exercises the unchanged validators and capped font pipeline.
This removes unnecessary runtime code without treating scanner silence as proof of
security. Current Debian libraries and worker-vendored urllib3 still need resolution.

## Local validator boundary follow-up — 2026-10-26

The validator now launches a Python child in isolated mode (`-I`) before importing
FontBakery. The child fixes the existing four-check profile and denies `pip` and
`pip_api` imports, including submodules. Preloaded installer modules refuse before
FontBakery loads; an attempted import still fails the child if a dependency catches
the rejection. The parent retains exact check-ID/result checks and all five validator
layers. FontBakery's declared dependency graph and compiler toolchain are unchanged.

Real subprocess controls exercise rejected imports, preloaded modules, swallowed
rejections, late module injection, fixed arguments and exit statuses. A positive
control executes the fake installer without the guard. Actual synthetic full/partial
fonts still pass every validation layer in TTF/OTF/WOFF2; their six output hashes
match the pre-change compiler baseline. No private font or model was rebuilt.

This is local application-path evidence only. Docker is unavailable on the current
host, so this follow-up has not rebuilt or rescanned Linux images. It does not clear
the existing image-security gate, establish a VEX disposition, or implement the
separate proposed minimal-runtime filesystem. Raw findings remain blocking.
