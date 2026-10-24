# Experiment records

The `r1-*` records describe the 2026-10-21 data audit, baseline comparison, one small
ridge fit and held-out evaluation completed before the latest owner instruction.
There are four completed lifecycle records, not four trained models. The saved
`r1-attempt-01.json` records an earlier failure before training. See the
owner-run handoff (`../LOCAL_RESEARCH_R1.md`; local record) and observed lineage (`../results/local-r1/lineage.json`; local record).
No R1 model is qualified or installed in Penforge. **Only the owner runs training**;
agents must not launch or schedule the scripts. Metadata import reads saved JSON only.

The original Latin-print and Devanagari-mixed planning records still reserve later
comparison work; their null fields are not overwritten with R1 results. Other
script/style/input partitions need separate evidence before any promotion.

Follow the complete [evidence lifecycle](../evaluation/PROTOCOL.md). Create a new `ExperimentRecord` for each dataset-research, baseline-comparison, justified-training, held-out-evaluation, candidate-registration, staged-deployment and monitoring step. Pin parent records and output artifacts by digest. A baseline comparison needs at least two pinned methods; completed stages require reproducible environment/config/code/seed/command/output evidence. Training requires its baseline parent, approved training datasets, rationale and pinned recipe. Held-out evaluation needs test-split benchmark evidence and training lineage where training was required. Registration, staged deployment and monitoring preserve that chain.

`research.validate` verifies metadata without executing stages or granting scientific/
production approval. Raw inputs stay external; large artifacts remain ignored. Every
experiment is `research_only`; R1 completed stages retain their measured failures.
