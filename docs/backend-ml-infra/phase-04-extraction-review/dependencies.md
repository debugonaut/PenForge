# Phase 04 local extraction dependencies

Date checked: 2026-10-19. Scope: local deterministic CV and synthetic evaluation only.

The worker group pins OpenCV headless 4.13.0.92 and NumPy 2.4.2. OpenCV's library
license for this version is Apache 2.0, per its [official license page](https://opencv.org/LICENSE/).
The installed Python wrapper has a separate MIT notice and includes a full third-party
binary notice file. NumPy's main license is BSD 3-clause, per its
[official license](https://numpy.org/doc/stable/license.html); preserve the installed
wheel's additional component notices as well.

The checked local files and their hashes are recorded in
dependency-license-evidence.v1.json (`../../../artifacts/phase04-core/dependency-license-evidence.v1.json`; local record).
Potrace is not bundled or invoked. The existing bundled Caveat font and OFL notice are
byte-pinned for development fixtures and research evaluation, with attribution retained.
FontTools 4.61.1 is isolated to the research group for decoding that source font into an
in-memory fixture renderer. It does not compile a user's font or introduce Phase 05.

Every Caveat weight and augmentation belongs to the same global font-family surrogate,
assigned to validation only. Train and test are empty. This prevents presenting the same
font as independent evaluation writers. No real/private handwriting is admitted and no
training is authorized. These synthetic renders cannot establish writer fidelity or
real-handwriting production accuracy.

The API group does not depend on NumPy, OpenCV, FontTools or a PDF renderer. Full binary
redistribution/dependency/provider review, container execution and hosted operation
remain release gates. Local license evidence does not waive those gates.

Development/CI `make setup` includes the research group so licensed-source fixture tests
can use FontTools. The API and worker Docker dependency commands continue to exclude
that group. The current research group contains FontTools only; no training framework
or training recipe is installed by this change. PDF authoring/verification uses the
separate bundled document runtime.
