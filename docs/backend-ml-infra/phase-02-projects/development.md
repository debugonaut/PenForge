# Phase 02 local development

Read [Phase 00 setup](../phase-00-foundations/development.md) and [Phase 01 identity setup](../phase-01-identity/development.md) first. Phase 02 adds authenticated project metadata and a cloud panel that users open deliberately. Local demo projects, handwriting, imported fonts, and Writing Desk drafts remain in browser storage. No provider provisioning, remote upload, or migration is required for the default demo.

## Database and migrations

Use the Phase 00 Docker Compose or native PostgreSQL setup. Create and export a disposable `TEST_DATABASE_URL`; Phase 02 integration tests require real PostgreSQL and create a temporary schema. They do not silently use SQLite. The current migration head is `0003_project_cursor`, which adds the deterministic owner-list cursor index.

```sh
export DATABASE_URL='the private local PostgreSQL URL printed by scripts/local_postgres.py'
export TEST_DATABASE_URL='the disposable local PostgreSQL test URL printed by scripts/local_postgres.py'
make migrate
```

The server defaults idempotency receipts to 24 hours. `IDEMPOTENCY_RETENTION_SECONDS` can be set from 60 seconds through seven days. After a receipt expires, a retry with the same key is a new operation. The browser stores the exact request body and identity-scoped key before sending each create, under an independent owner/operation key. In **Unconfirmed project creations**, inspect **Original request details and retry key**, choose **Restore original creation**, then **Retry original creation**. The restored fields are read-only and the saved body/key are sent unchanged. **Start an independent creation** retains the original evidence and starts a separate operation. Receipt expiry still applies; an old key does not guarantee replay after server retention ends.

## API and opt-in browser client

For a local Firebase Auth emulator, follow the explicit `demo-` project instructions in Phase 01. Start the API with `APP_ENV=local`, `AUTH_BACKEND=firebase`, the emulator host and `CORS_ALLOWED_ORIGINS` set to the actual local frontend origin. Start Next in a separate shell using the Phase 01 public emulator settings. Account configuration alone does not open cloud project APIs; on `/projects`, select **Connect cloud projects** and sign in or explicitly start a guest identity.

The cloud panel supports create/list/get/update of metadata: project name, script code, and five bounded style controls. It does not send handwriting, glyphs, imported font bytes, or local drafts. Cloud create uses an identity-scoped idempotency key. Local retry/conflict recovery is stored in localStorage and tied to the Firebase UID; another identity cannot replay it. Each open editor retains the project/ETag from the moment it was opened, even after a list refresh. Each update stores its partial patch before sending, so an interrupted response remains recoverable. A successful mutation requires the latest strong ETag. If two tabs save the same row version, one receives a 412 and can keep its recovered edit against the latest version or keep the cloud version. A rebase writes only fields changed in the local tab, preserving unrelated newer fields; if the request already committed before its response was lost, recovery reads the row and avoids a duplicate update.

Deletion currently returns the typed `capability_unavailable` response after owner and ETag checks. No row is hard-deleted; a user-visible deletion saga and physical purge evidence belong to Phase 07. Cloud import and duplication are disabled because Phase 02 has no upload/snapshot/lineage transfer to preserve. Local demo resume, duplicate, and confirmed delete remain available.

Useful API calls after a local identity is connected:

```sh
curl -H "Authorization: Bearer $ID_TOKEN" \
  -H 'Idempotency-Key: project-create-1' \
  -H 'Content-Type: application/json' \
  -d '{"name":"Practice hand","script_code":"Latn","style_controls":{"thickness":50}}' \
  http://127.0.0.1:8000/api/v1/projects

curl -H "Authorization: Bearer $ID_TOKEN" http://127.0.0.1:8000/api/v1/projects
curl -H "Authorization: Bearer $ID_TOKEN" -H 'If-Match: "v1"' \
  -H 'Content-Type: application/json' -d '{"name":"A new title"}' \
  -X PATCH http://127.0.0.1:8000/api/v1/projects/PROJECT_UUID
```

The placeholder token must come from the local emulator. Do not use a hosted token or configure a real Firebase project for this local acceptance path.

## Browser recovery storage and identity changes

New recovery records use `penforge-cloud-operation-v2:` keys, with one key per owner/operation. Legacy `penforge-cloud-create-recovery-v1` and `penforge-cloud-change-recovery-v1` bytes are read without rewriting or deleting them. Resolving a legacy operation adds only its own resolution marker. Keep these records when investigating unknown commits; do not clear all site storage as a retry strategy.

Changing identities clears the old Workbench service/editor and fences late results; explicitly load the new identity's projects. Another identity's original create details remain inspectable, but retry requires its original UID. If a guest was claimed into another account, compare destination projects while retaining the old request evidence. The destination never replays the guest key as its own operation and can create independently. No local handwriting moves during these actions.

## Acceptance commands

```sh
make lint
make contracts-check
make test-api
# requires a disposable real PostgreSQL TEST_DATABASE_URL
make test-integration
npm run typecheck
npm run lint
npm test
npm run build
```

`make contracts` intentionally regenerates OpenAPI registry artifacts, generated TypeScript, and the research schema audit. `make contracts-check` verifies drift, retained-v1 compatibility, standalone TS, and research metadata. For browser acceptance use the standard `/projects` UI at 1440, 768, 390, and 320 CSS pixels, including short viewport height and keyboard operation. Confirm that opening the local Workbench sends no cloud request; only the explicit cloud action may initialize Firebase and list server metadata.

## Rollback and remaining gates

Disable `NEXT_PUBLIC_PENFORGE_AUTH_BACKEND` and rebuild the frontend to return to the local-only display. This leaves local drafts and cloud rows intact. Stop new cloud writes by disabling the API feature/configuration; do not downgrade `0003` after writes or delete project rows as a rollback. The additive cursor index is readable by the previous API schema.

Hosted Firebase ADC/project/OAuth/certificate/revocation checks, App Check, shared rate limiting, and dependency/provider gates remain unresolved. This local path does not prove public security, service limits, hosted cost, or ML quality. Phase 03 has separate explicit authorization; later phases retain their own boundaries.
