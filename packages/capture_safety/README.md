# Isolated capture verification

Phase 03's worker-only package validates JPEG, PNG and WebP originals and produces a
bounded, oriented, metadata-free PNG thumbnail. It performs no quality assessment,
recognition, segmentation, extraction, training or font generation.

`verify_image(path, declared_mime=..., output_dir=..., expected_sha256=..., limits=...)`
stream-copies and hashes a pinned regular file, then parses only that private copy in
a fresh subprocess. The caller receives original dimensions/hash/size, derivative
path/hash/size and actual CPU/RSS/wall measurements. The caller owns removal of the
successful derivative. Failed/canceled invocations kill the process group and remove
their private temporary directory. Source bytes are never rewritten.

The child checks container boundaries before Pillow verification and full `load()`:
JPEG markers and exact EOI, PNG chunk CRCs and exact IEND, and WebP exact RIFF/chunk
boundaries. It rejects appended or concatenated polyglots, malformed/truncated input,
MIME/magic mismatch, multiple frames, excessive dimensions/pixels and checksum mismatch.
PNG IDAT validation additionally requires one complete zlib stream, no unused compressed
payload and exactly the declared filtered scanlines, including Adam7. Output is consumed
in at most64KiB blocks, so a bomb is rejected without a second full decoded-image buffer.
Strict JPEG validation uses the native codec linked by the pinned Pillow extension:
it reads every DCT coefficient and makes corruption warnings fatal before the normal
pixel decode. Pillow alone deliberately suppresses those warnings and accepts missing
entropy data when an EOI marker is restored.
Valid metadata inside a valid image container is accepted as original evidence; it is
not copied into the thumbnail. A fresh image carries only oriented thumbnail pixels.
Strict container validation is deliberately conservative; unsupported formats/chunks
require a separate compatibility/security review.

Configured defaults are provisional safety caps: 20MiB input, 50MP, 12,000px dimension,
768MiB process memory, 10CPU seconds, 20wall seconds, and 1,000px/4MiB thumbnail. The
API reserves the original declaration plus the thumbnail bound before upload issuance.
These are not measured ML thresholds, hosting feasibility or throughput promises.

The subprocess receives a minimal environment, no credentials, and no inherited
network descriptors. macOS Seatbelt denies all network operations before Python starts.
Linux seccomp enforces native architecture/ABI checks and denies socket operations,
network syscalls, io_uring and cross-process descriptor/ptrace paths; filters survive
child execution. A real IPv4/IPv6 permission-denial probe must pass before parsing.
Isolation failure or unsupported platforms fail closed, without a socket monkeypatch.
This reduces parser exposure; it is not a claim of an exploit-proof virtual machine.
JPEG's native adapter supports only libjpeg v6b LP64 on the listed64-bit architectures.
The native create function checks version62 and the632-byte decompressor size before
initializing its body; public error-callback types and layout are fixed. Missing symbols,
other codecs or an incompatible ABI fail closed as `strict_decoder_unavailable`; no
system-library search or permissive fallback is used. This adapter is locally executed
on the Pillow13.0.0 macOS arm64 wheel. Linux wheel ABI and all other hosts need their own
execution evidence before promotion.

CPU, file-size and wall limits apply. Linux also uses AS/DATA rlimits. This macOS
runtime rejects finite AS/DATA limits, so its required native RSS watchdog samples
every20ms and kills above the configured cap; allocation can overshoot between samples.
Reported metrics identify that distinction. Linux/container hard-memory execution and
provider/dependency review remain release gates. The local Codex outer sandbox prevents
nested Seatbelt application; the real decoder suite must run with permitted local
execution so the child can install its own sandbox. It never falls back to decoding
without that sandbox.
Decoder temporary directories are nested in the worker's per-attempt directory, so
lease-aware crash cleanup inventories all residue. The child has its own real-time wall
alarm even if its parent is killed. Linux also requests a parent-death SIGKILL, with a
race check. macOS relies on the child's wall/CPU limits after parent death; these do not
prove the later worker's deletion saga.

Run `python scripts/measure_capture_decoder.py` from the root with worker dependencies
and repository `PYTHONPATH`. Results in `artifacts/phase-03/decoder-resources.json` are
synthetic engineering measurements, separate from the unmeasured ML research records.
Fixtures include a full50MP oriented alpha PNG and an exact20MiB JPEG; all sources and
intermediate outputs are temporary.

Primary references checked2026-10-19: [Pillow13.0.0 release and license](https://pypi.org/project/pillow/),
[Pillow verify/load/pixel semantics](https://pillow.readthedocs.io/en/stable/reference/Image.html),
[Pillow's suppressed JPEG warnings](https://github.com/python-pillow/Pillow/blob/13.0.0/src/libImaging/JpegDecode.c),
[libjpeg public structs](https://github.com/libjpeg-turbo/libjpeg-turbo/blob/main/src/jpeglib.h),
[native version/size guard](https://github.com/libjpeg-turbo/libjpeg-turbo/blob/main/src/jdapimin.c),
[libjpeg warning callbacks and full coefficient reads](https://github.com/libjpeg-turbo/libjpeg-turbo/blob/main/doc/libjpeg.txt),
[Python resource limits](https://docs.python.org/3/library/resource.html),
[Linux seccomp filter/ABI requirements](https://docs.kernel.org/userspace-api/seccomp_filter.html).
