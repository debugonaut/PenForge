# Phase 07 authoritative data inventory

Date: 2026-10-20. Database identity/ownership determines scope, never a current UID prefix.

| Records | Private material / cleanup rule |
| --- | --- |
| users, guest_usage, identity_claims | Firebase UID, merged ancestry, IP HMAC, claim receipts; account erasure removes ancestry after working data |
| projects | Names, style controls, active snapshot; deleting state revokes access before cascade |
| assets | Originals, raw/derived strokes, normalized images, thumbnails, crops/masks, vectors, result manifests, checkpoints, staged objects, fonts, reports, embeddings; inventory every row including historical keys and expired ledgers |
| samples | Source/template hashes, capture metadata, quality; cascade after contribution withdrawal |
| glyphs, glyph_revisions, glyph_candidates, ml_result_artifacts | Inline geometry, source lineage, review, hypotheses; project cascade preserves ordinary immutability |
| project_snapshots, style_profiles, font_exports | Frozen geometry/mapping, styles, font names, reports; retained until authorized project deletion |
| jobs, job_events, job_checkpoints, job_streams | Payloads, source/result/staging IDs, dependency manifests, progress and stream slots; cancel/wait, then cascade |
| quota_balances, quota_reservations | Release reserved/physically removed storage accounting once; remove scoped reservations; account balances then cascade |
| idempotency_keys | May contain resource metadata/capabilities; purge project-related receipts conservatively for owner; all on account erasure |
| training_consents, dataset_contributions | Immutable grants/closed withdrawals, source/consent/transformation/hash lineage and separate dataset copies; withdraw before working purge |
| dataset_versions | Immutable contribution membership/hash manifests; historical selection lineage remains content-free after contribution withdrawal |
| system_settings | Versioned privacy/retention policies; no raw writing or identity permitted |
| audit_events | Historic actor/resource IDs need erasure minimization; scoped purge retains HMAC/counts/codes only |
| deletion_requests | Raw work targets exist only while needed; completion clears user/project/Firebase identifiers and retains HMAC/counts/timestamps/states |
| ObjectStorage privacy/erasures journal | Integrity-checked HMAC targets, scope, opaque operation ID and timestamp; retained independently of database restore, never raw UID or content |
| local recovery | Browser local demo drafts and explicit upload backups are separate from cloud deletion; privacy settings do not upload or erase them automatically |
| model_versions | No Phase 07 trained model; provenance can reference dataset versions. Withdrawal cannot promise removal of learned influence |
| Phase 08 style_profiles | Snapshot/revision/sample hashes, descriptors and optional embedding asset; retained as immutable private project lineage, cascaded on project/account erasure. Personal measurement needs no training consent. Contribution withdrawal preserves the working profile. |
| Phase 08 alternate evidence | Existing glyph variants/revisions/candidates and source samples, frozen snapshot membership; same access, retention and erasure scope as the primary glyph. No extra base coverage. |
| Phase 08 alignment / synthetic proposals | Experiments are unavailable in the normal path. Any future persisted private hypothesis/proposal must use the existing asset/result/candidate ledgers, never logs or a detached cache. No generated model assets exist in this release. |

Known verifier orphan intermediates are enumerated from immutable original key + asset
ID + persisted attempt budget. Analysis/export/checkpoint publication uses authoritative
asset ledgers before storage. Unknown objects remain in dry-run reconciliation, never
indiscriminately deleted. Local temporary attempt trees are reaped only after the job
attempt is inactive. Cross-project aliases are preserved before canonical deletion.

Retention uses a versioned policy and a dry-run report before apply. Referenced sources,
outlines, snapshots, exports and immutable result lineage remain until scope deletion;
TTL does not authorize breaking those references. Provider backup expiry, hosted Firebase
and remote caches remain unverified Phase 11 gates.

Phase 09 additions use the same authoritative inventory: raw sequence/normalization
and capture context in immutable job/result/outline evidence; contextual/half/reph/rakar
forms, anchor records and provenance in glyph revision geometry; required-set/Unicode
pins in projects and snapshots; generated feature identity, HarfBuzz traces and renders
in private export/report/checkpoint assets. Project/account erasure cascades these rows
and deletes every linked object through existing ledgers. Template manifests/reference
fonts under repository public engineering resources contain no user writing. No private
Devanagari cache or new dataset copy path is introduced. Training withdrawal preserves
personal font work while removing the separately consented contribution copies.
