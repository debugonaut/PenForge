# Third-party materials

The root MIT License covers original PenForge code. It does not replace the licenses
or copyright notices of third-party materials.

| Material | Location and notice |
| --- | --- |
| shadcn/ui accordion | Adapted component in `src/components/ui/`; retain the [MIT copyright and license](src/components/ui/SHADCN-LICENSE.txt) and [source/adaptation notes](src/components/ui/README.md). Its Radix dependency and transitive versions are pinned in the npm lockfile. |
| Caveat Variable | Bundled web font in `public/fonts/`; retain [SIL OFL 1.1 and copyright](public/fonts/OFL-Caveat.txt). |
| Noto Sans Devanagari | Engineering reference fixture in `research/fixtures/fonts/noto-deva/`; retain [SIL OFL 1.1](research/fixtures/fonts/noto-deva/OFL.txt) and [pinned provenance](research/fixtures/fonts/noto-deva/provenance.json). It is not a generated personal font. |
| Unicode 17.0.0 data | Pinned files in `packages/contracts/unicode/17.0.0/`; retain the [Unicode License v3](packages/contracts/unicode/17.0.0/LICENSE.txt). |
| DM Sans, Fraunces and Caveat packages | Installed from the pinned Fontsource dependencies. Their package licenses accompany the installed fonts. |
| JavaScript/Python dependencies | Versions and upstream packages are pinned in `package-lock.json` and `uv.lock`; retain their applicable notices when distributing dependencies or build output. |

The mascots are repository-authored SVGs. The writing-hand cutout has its retained
PNG, lossless WebP and [generation provenance/prompt](public/illustrations/writing-hand-v2.prompt.txt).
The paper textures are original procedural SVGs covered by the project MIT License.
The earlier third-party parchment image was removed at the owner's request and is
not distributed. Private handwriting, data archives, permission records, model weights
and generated private fonts are not included in the public code license.
