# Minimal runtime packaging

The API and worker keep the locked Python 3.12/Debian builder and complete project
venv. Their final stages use a separately assembled filesystem rather than inheriting
the builder's operating-system utilities. This is a packaging change, not model
training, a validator downgrade or a vulnerability exemption.

[The assembler](../../infrastructure/docker/assemble_runtime.py) copies CPython,
the role's application/venv, CA/locale/time-zone data, package metadata and license
notices. Every installed native extension/executable is inspected in the **trusted
builder** with `ldd`; its dependency closure is copied recursively. NSS, crypto and
native rendering resources loaded at runtime receive explicit inclusion. Source
paths, symlinks, content hashes, ownership, limits and forbidden payloads are checked.
Do not apply this builder tool to uploaded binaries or untrusted filesystem trees.

Some wheel libraries inherit the importing extension's RPATH and cannot resolve
their siblings when inspected alone. Such a missing-library result is deferred only
until every ELF is inspected. It is accepted only when a separate successful complete
`ldd` trace includes that exact resolved ELF; the manifest's `native_context` records
the parent trace. That trace's full dependency paths are copied and checked normally.
An unresolved orphan, a same-named different file or any other inspection error
still fails. The assembler never widens `LD_LIBRARY_PATH` to manufacture success.

The runtime manifest records the files actually copied. Unmodified dpkg metadata
still describes the builder's package set; its presence does not assert that every
builder file exists in the runtime. Keeping it and the redistribution notices avoids
hiding package versions from scanners. Python distribution metadata and licenses
remain beside the complete venv. The runtime runs as UID/GID 65532 and has a writable
temporary directory, with no SUID/SGID payloads, global installers, mount/nsenter,
infocmp, libacl or libmount. If the native dependency graph requires a forbidden
component, assembly fails rather than silently omitting it.

The fixed [FontBakery child](../../packages/font-engine/penforge_font_engine/_fontbakery_child.py)
blocks pip/pip_api imports for the supported four-check profile. This is a Python
call-path invariant, not an OS sandbox. Declared dependencies remain installed;
installer code is not represented as absent. All five validation layers still run.

## Acceptance and failure behavior

Filesystem/parser tests cover native dependency recursion, missing evidence,
privileged modes, path/symlink safety, licenses, package records, immutable output
and unexpected `ldd` results. These tests do **not** establish a valid Linux image.
The final images must also pass the existing CI gates:

- Native imports plus CA loading, UTF-8 locale and localhost name resolution.
- Networkless, memory/CPU-capped verification, extraction and TTF/OTF/WOFF2 validation,
  including the init-parent isolation requirement and image-size ceilings.
- Exact synthetic font hashes pinned to compiler 1.0.1, spacing policy v2 and the
  existing dependency versions. A mismatch fails instead of regenerating the fixture.
- Read-only inventory bound to each exact image config ID, with forbidden components
  absent and all evidence complete.
- Raw HIGH/CRITICAL scans and the normal application, database and identity suites.

No image has passed merely because source tests pass. On a build/import/validation
failure, preserve its evidence and repair the dependency closure; do not disable a
validator or broaden an advisory exclusion. No VEX or scan filter is supplied here.
Unresolved findings keep the security gate red even if the runtime functions.

Existing ready font exports remain immutable. This packaging change does not alter
the compiler version, approve a model, publish private font assets, deploy a service
or resolve the remaining hosted release gates.
