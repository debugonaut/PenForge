# Font export dependency and license decision

Date: 2026-10-20. Accepted for local Phase 05. Provider/deployment distribution
review remains a separate gate. Installed versions and license-file hashes are in
the inventory (`../../../artifacts/phase05-acceptance/dependency-inventory.json`; local record).
The checked-in `uv.lock` pins transitive packages and wheel hashes.

| Dependency | Version | Local role / license evidence |
| --- | --- | --- |
| FontTools | 4.61.1 | FontBuilder, Cu2QuPen, T2CharStringPen, WOFF2; MIT |
| Brotli | 1.2.0 | Real WOFF2 compression; MIT |
| skia-pathops | 0.9.2 | Explicit even-odd resolution/winding; BSD-3-Clause |
| uharfbuzz | 0.53.3 | Actual HarfBuzz shaping; Apache-2.0 wrapper, bundled HarfBuzz notices retained |
| opentype-sanitizer | 9.2.0 | Installed wheel includes the actual `ots-sanitize` executable, verified as `ots 9.2.0`; BSD-style |
| FontBakery | 1.1.0 | Four-check local curated profile; Apache-2.0 |
| freetype-py | 2.3.0 | Validator dependency; BSD-style wrapper, bundled FreeType notices retained |
| Pillow | 13.0.0 | FreeType raster smoke; existing worker dependency and license decision retained |

No Potrace, ML framework, trained model or copied commercial glyph source is added.
The compiler fixtures use the project's authored analytic canonical outline, repeated
under explicit synthetic character assignments. They do not resemble a real alphabet
and establish no recognition, legibility or handwriting fidelity claim.

FontTools and validators live in the worker group. The API consumes contracts and
orchestration only. Docker recipes and CI paths are updated, but Docker/hosted CI have
not been run on this host. Preserve all dependency notices before distribution.

Packaging was checked against the primary [OTS wheel project](https://pypi.org/project/opentype-sanitizer/9.2.0/),
[FontTools documentation](https://fonttools.readthedocs.io/en/stable/fontBuilder.html) and
[FontBakery documentation](https://fontbakery.readthedocs.io/en/latest/).
The installed license files, binary hash and actual command execution are the local
evidence; this is not a legal opinion or public-release approval.
