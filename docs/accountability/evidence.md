# Service accountability — local evidence, 2026-10-24

Status: implemented and verified locally as a **pre-release draft**. The owner approved
mandatory accounts, Terms/handwriting-rights confirmations, private accountability and
misuse reporting. Jurisdiction: India. The owner has no company; no operator name or
contact was supplied. Neither was inferred. No legal acceptance, real account creation,
email delivery, suspension, provider setup, publication or deployment occurred.

## What changed

- `accountability` is the API domain for policy, access, immutable agreement receipts,
  minimal request admissions and private abuse reports. Other routers use its public
  `font_identity` dependency; domain ownership and quota checks still run afterwards.
- Creation, account edits, capture uploads, analysis, glyph mutations, font compilation
  requests, font/asset download grants and raw object transfers require a registered,
  email-verified principal and the current policy receipt. Missing services fail closed.
  The new default for `/create` is account mode. Demo use requires an explicit choice.
- The two confirmations start unchecked. The server rejects false, missing, coercible
  or extra fields; it binds the principal, server time, exact SHA-256 and complete policy
  text/operator details. Repeated acceptance returns the original receipt. Changing the
  text or operator details invalidates old access. Guest claims do not transfer receipts.
- Immutable receipt rows in migration `0014_service_accountability` can be removed only
  through the validated account-erasure context for that receipt's user. The existing
  deletion saga removes them and preserves another account's receipts.
- Audit admission records contain an HMAC subject reference, static operation name,
  optional resource UUID, request UUID and policy hash. The existing privacy HMAC format
  is shared without changing its bytes. A stable existing privacy key is required;
  there is no new key rotation/migration. Bodies, document text, bearer tokens, signed
  URLs and raw account identifiers are not copied into these new audit events.
- Admission means the account passed this guard. It does **not** mean later validation,
  ownership or processing succeeded. Existing private jobs/snapshots/exports preserve
  completed-action lineage. Nothing tracks use of a file after it leaves the service.
- Raw local object capabilities now additionally require their current account owner.
  Capture upload/preview, review evidence and font transports obtain a fresh owner-bound
  bearer only after checking the same-origin destination. External URLs remain rejected.
- Sign-in, identity/guest recovery, existing metadata/draft reads, cancellation, training
  withdrawal and deletion have no new Terms requirement. Existing authentication,
  recent-auth and account-state restrictions still apply. Training remains a separate,
  optional consent; accepting service terms never grants it.
- Public report intake needs no account or Terms acceptance. Inputs are bounded, rate
  limited and private, with an opaque receipt and no public report-detail route or
  automatic penalty. Existing suspension enforcement remains available to the operator;
  no new admin console or automated adjudication was introduced.

## Operator procedure and release gates

1. Establish the actual individual or organisation operating any hosted instance and
   publish its privacy/grievance and misuse contact. Open-source source code does not
   supply these details. No project code licence was selected by this change.
2. Obtain Indian legal review of the terms, age/contract eligibility, privacy notice,
   provider disclosures, retention, grievance handling, liability and lawful-request
   procedures. This draft promises no blanket immunity or legal compliance. Review the
   phased applicability of the [DPDP Act](https://www.meity.gov.in/static/uploads/2024/06/2bf1f0e9f04e6fb4f8fef35e82c42aa5.pdf)
   and [Rules](https://www.meity.gov.in/static/uploads/2025/11/53450e6e5dc0bfa85ebd78686cadad39.pdf).
3. Publish the reviewed pages, change the policy version/status to reviewed, then set
   `SERVICE_OPERATOR_NAME`, `SERVICE_OPERATOR_CONTACT` and `SERVICE_TERMS_REVIEWED`.
   A draft policy stays unavailable outside local/test even if these settings are set.
   These settings do not override the existing hosted auth, storage, shared rate-limit,
   provider, quota, deployment or publication gates.
4. Pin `PRIVACY_HMAC_SECRET` to the existing key before general HMAC rotation. Preserve
   old journal/receipt compatibility. Apply migration 0014 through the normal reviewed
   migration process; this task migrated disposable test schemas only.
5. Review reports privately through authorised database access. Corroborate account and
   immutable resource lineage; an account is not proof of real-world identity. Use the
   existing `users.account_state = 'suspended'` control only after an operator decision.
   Reports neither identify an offender by themselves nor trigger automatic suspension.
6. Keep minimal audit records under the existing audit-pruning policy (default one year).
   Agreement receipts last with the account. Reports have a fixed **90-day draft default**:
   use the content-free dry run below, inspect its count, then apply it through the
   operator's authorised maintenance workflow. No recurring job was created here.
   Legal preservation exceptions require a separately reviewed process; this code does
   not introduce indefinite holds or weaken account erasure.

```sh
PYTHONPATH=apps/api:packages/contracts .venv/bin/python scripts/prune_abuse_reports.py
# After reviewing the dry-run count for the intended database:
PYTHONPATH=apps/api:packages/contracts .venv/bin/python scripts/prune_abuse_reports.py --apply
```

The command uses the operator's explicit environment/database credentials. Never point
it at an unreviewed database. Stored report contact/details must not be copied into
public issues, logs or this open-source repository. Review retention and intake limits
before launch; the current in-memory limiter is local/test only.

## Verification

- **166 frontend tests passed**, including user-requested verification, stale-account
  refusal, owner-bound acceptance, policy mismatch, late account replies and all
  existing document/capture/export/recovery tests. TypeScript and ESLint passed.
- **103 focused backend unit/contract/boundary tests passed; 2 existing checks skipped**.
  The selected files were accountability, projects HTTP, identity HTTP, privacy HTTP,
  foundation, boundary checker and privacy contracts. This is not a full backend pass.
- **17 real PostgreSQL integration tests passed** against isolated migrated schemas:
  accountability, uploads HTTP, account workspace, extraction HTTP and exports HTTP.
  They cover idempotence, policy changes, immutability, strict confirmations, guest/
  unverified refusals, direct/cross-owner capabilities, minimal HMAC audit references,
  resource links, report limits, report pruning and account-erasure compatibility.
- OpenAPI export drift, generated TypeScript drift, contract TypeScript, Ruff, mypy and
  the architecture boundary checker passed. Preserved specification hashes and local
  documentation links were checked separately.
- Production build passed with existing mixed-support CSS warnings in the capture/review
  modules and a cache warning. A loopback production server was started and inspected,
  then stopped. The temporary synthetic visual route was removed before the final build.
- Browser layouts: real Terms, Privacy, Account, Report abuse and default Create plus
  the actual acceptance view in an inert fixture at **1440×900, 768×1000, 390×844,
  320×640 and 768×480**. No horizontal overflow in the 30 recorded views. Keyboard
  Space/Enter required both boxes; changed policy cleared both; the inert acceptance
  reached ready. Missing reporting configuration said no report was sent. The explicit
  demo still opened separately; no console errors were observed in that smoke check.
- Browser acceptance used synthetic state with no network, provider, real receipt or
  binding agreement. Live Firebase verification/email, live end-to-end account UI,
  OS-gentle media emulation, physical touch and hosted acceptance remain unexecuted.
  No OS preference was changed. All earlier phase gates remain open as recorded.

An initial broad backend attempt was interrupted at an extraction fixture. It reported
26 decoder isolation failures (`isolation_unavailable` in the nested macOS sandbox)
and one route-inventory assertion caused by the added routes; that assertion was fixed
and the focused foundation checks pass. Do not interpret that attempt as a full-suite
pass or repeat research/model runs to close it.

Local-only screenshots, fixture source, layout/interactions JSON and final check logs
are under `artifacts/accountability-20261024/`. The preserved fixture source ends in
`.tsx.fixture` and is not application code. No tests or fixtures accept terms for a real
person. The owner's original Writing Desk tab/document was preserved. Completed
handwriting datasets, training/evaluation and frozen font packages were not rerun.
