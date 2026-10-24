# 0004 — Research starts with governed evidence and independent gates

Status: accepted. Date: 2026-10-19. Scope: Phase 00 research foundations.

## Context and decision

[v3 Part A §§8/13/14 and Part B §§8.9–8.12](../specifications/PENFORGE_CODEX_BACKEND_ML_INFRA_CONTEXT_v3.md)
require writer-disjoint, licensed, consent-aware research from S0. The latest user request
makes the entire research→training→evaluation→registry→deployment→monitoring path explicit.

Version dataset/license/consent manifests, global pseudonymous writer groups, split and
augmentation/duplicate lineage, benchmark records, experiment plans and shaping corpora.
Validate all records together: writer and source overlap across train/validation/test is
rejected even across datasets. Unresolved identities remain quarantined. Synthetic font
families count as writer groups and cannot prove performance on real handwriting.

Public dataset pages are research leads, not permission to train a product model. IAM and
DHCD remain blocked candidates with source-linked findings; current bundled Caveat has
pinned OFL provenance but no synthetic benchmark is claimed. No raw handwriting/model
weights are added. Metadata fixtures exist to test governance only.

Compare classical/pretrained baselines before justified training or fine-tuning. Keep
per-user fine-tuning research-only. Pin dataset/config/code/tool versions/seeds, preserve
held-out test writers, compare recognition/segmentation/writer fidelity separately, and
require independent script shaping. Numeric thresholds, recipes and resources remain
unmeasured. A better OCR score cannot waive a writer-fidelity regression.

Experiment records preserve later gates: baseline evidence → justification/training →
held-out multi-dimensional comparison → candidate registry entry → independent approval →
staged rollout/rollback → monitoring. They are governance records, not an implemented
trainer, registry loader or deployed model. Current benchmark values and resources are
null. Devanagari starter strings derive from the source and are unexecuted; the production
conjunct inventory and 200–500 word corpus still require licensed corpus research.

## Consequences and evidence

See [research workflow](../../research/README.md), generated research schemas,
[research tests](../../tests/backend/test_research.py) and Phase 00 evidence.
Consent for making a personal font is never reused for global training. Withdrawal
excludes future datasets and preserves lineage; no model-unlearning promise is made.
All current models/capabilities remain unvalidated. Promotion and deployment require
later authorized phases and their measured gates.
