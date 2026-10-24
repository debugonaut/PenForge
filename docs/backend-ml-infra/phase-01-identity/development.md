# Phase 01 local development

Read [Phase 00 setup](../phase-00-foundations/development.md) first. The existing lockfiles
now pin Firebase Admin Python 7.7.0 and Firebase JS 13.0.0. No Firebase project, cloud
resource or credentials are required for default local demos. `/account` explicitly shows
unavailable until configured. It never changes the local project storage service.

## Local Auth emulator

Use a `demo-` project only. The pinned CLI can run temporarily without adding its broad
CLI dependencies to the application lockfile:

```sh
npx --yes --package firebase-tools@15.34.0 firebase emulators:start \
  --only auth --project demo-penforge-identity \
  --config infrastructure/firebase/firebase.json
```

Auth binds `127.0.0.1:9099`; UI is disabled. This CLI Auth emulator ran locally without a
Java-based database emulator. The CLI and local tooling dependencies are not API runtime
packages. Do not capture its debug logs as application evidence: emulator logs may contain
test credentials. Use only disposable synthetic accounts.

Start PostgreSQL with the existing Compose or native helper and export the exact local
`DATABASE_URL` it prints, then run `make migrate`. Head is now `0002_identity`.
Run the independent API with explicit emulator authorization:

```sh
export APP_ENV=local AUTH_BACKEND=firebase STORAGE_BACKEND=disabled
export FIREBASE_PROJECT_ID=demo-penforge-identity
export FIREBASE_ALLOW_EMULATOR=true
export FIREBASE_AUTH_EMULATOR_HOST=127.0.0.1:9099
export CORS_ALLOWED_ORIGINS='["http://localhost:3000"]'
make api
```

The API does not automatically read `.env`. Firebase Admin uses the matching process
variable; Penforge refuses an ambient emulator host without all explicit local guards.
`AUTH_BACKEND=fake` is a test adapter and cannot serve `/me` or claims.

In a separate frontend shell:

```sh
export NEXT_PUBLIC_PENFORGE_AUTH_BACKEND=firebase
export NEXT_PUBLIC_PENFORGE_ENV=local
export NEXT_PUBLIC_PENFORGE_API_ORIGIN=http://127.0.0.1:8000
export NEXT_PUBLIC_FIREBASE_API_KEY=demo-key
export NEXT_PUBLIC_FIREBASE_PROJECT_ID=demo-penforge-identity
export NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=localhost
export NEXT_PUBLIC_FIREBASE_APP_ID=demo-app
export NEXT_PUBLIC_FIREBASE_AUTH_EMULATOR_URL=http://127.0.0.1:9099
npm run dev
```

Visit `/account`. If Next chooses a different port, update API `CORS_ALLOWED_ORIGINS`
explicitly and restart the API. Public Firebase client configuration is not an Admin
credential. Never put ADC/service-account secrets in any `NEXT_PUBLIC_` variable.
Emulator frontend configuration is rejected in a production build even on loopback.

Choose guest, link a new email/Google identity, or sign in to an existing account and
separately approve the pending claim. Google uses the local emulator's mocked popup;
no real provider credentials are needed. The screen also offers GitHub through the same
popup/in-place-link path, but the emulator does not mock it: enable the GitHub provider in
a real Firebase project and add its OAuth app to exercise that button. Only identity
requests reach the API. Local handwriting, drafts, imported fonts and document exports
keep their existing behavior.
The old guest proof is memory only; reload/tab close loses it. Firebase token refresh
does not refresh `auth_time`; a guest session older than the configured claim window
fails securely. No supported anonymous reauthentication/recovery protocol is invented.

## Acceptance commands

```sh
make lint contracts-check
make test-api
# Use a disposable real PostgreSQL TEST_DATABASE_URL from the setup instructions:
make test-integration
export PENFORGE_TEST_AUTH_EMULATOR_HOST=127.0.0.1:9099
make test-identity-emulator
npm run typecheck
npm run lint
npm test
npm run build
npm start
```

The emulator target requires both environment variables and tests actual Firebase Admin,
local Auth REST and PostgreSQL through the public identity routes. Normal integration
checks exclude emulator cases; absence of an emulator is never recorded as a passing
emulator run. CI adds a pinned `emulators:exec` step, authored but not hosted-executed here.
Offline signature tests use actual Admin SDK verification with local RSA fixtures and
controlled cert/user lookups; this is separate from Google's hosted verification.

## Production gates and rollback

Production Firebase uses ADC and an explicit project, with no emulator variables or fake
auth. Current settings block hosted identity until a shared PostgreSQL limiter is delivered
and verified. App Check provider, Identity Platform eligibility, actual OAuth domains,
credential/service permissions and provider costs remain explicit configuration/hosting
gates. No production credentials or hosted feature checks were exercised.

Set frontend auth backend to disabled (and rebuild public configuration) to roll back
identity presentation. This leaves local drafts and server identities/work intact. Do not
delete Firebase users or reverse a transfer as a rollback. Pre-claim schema downgrade is
for disposable databases only; after a receipt exists use forward repair. Phase 07 needs
an explicit privacy-safe treatment of immutable receipt/origin history before account purge.
