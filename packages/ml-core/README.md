# ML core boundary

The runtime library is `packages/ml_core`, including Phase 08's guarded registry.
It must not import API/database/queue code, training frameworks or font compilation.
No recognizer or personal-font model is qualified for product inference.

The separate R1 research CLI (`../../research/LOCAL_RESEARCH_R1.md`; local record) prepares audited
word data and provides an owner-run diagnostic training script. Its weak recognition,
style geometry and authentic-grapheme reuse results do not enter runtime services.
Agents may prepare/check code and inspect artifacts; only the owner runs training.
