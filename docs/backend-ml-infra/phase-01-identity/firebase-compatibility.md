# Firebase compatibility review — Phase 01

Reviewed 2026-10-19 against official Firebase documentation and the installed Admin Python
SDK. This records compatibility and local evidence; no hosted project, provider setting,
public endpoint or production credentials were provisioned or tested.

## SDK and token verification

[Admin Python release notes](https://firebase.google.com/support/release-notes/admin/python)
identify **7.7.0**, released 2026-09-23. The API pins that release in `pyproject.toml`
and `uv.lock`. [Admin setup](https://firebase.google.com/docs/admin/setup) supports the
workspace's Python 3.12 and Application Default Credentials (ADC). Production credential
and service-identity policy remains Infra Q7; credentials never enter the browser bundle.

[ID-token verification](https://firebase.google.com/docs/auth/admin/verify-id-tokens)
requires Firebase **ID tokens**, rather than raw custom tokens or OAuth provider tokens.
Admin verifies the signature, algorithm/key, issuer, project audience and token validity.
Penforge supplies an explicit project ID, derives the UID/provider from verified claims,
and never accepts a body-supplied internal user ID or tier. It also validates required
claim types, timestamps and the configured single-project/non-tenant boundary.

[Session management](https://firebase.google.com/docs/auth/admin/manage-sessions) explains
that detecting revocation requires querying Firebase Auth. Penforge always uses
`check_revoked=True`, including `/me`, and maps invalid/expired/revoked/disabled/deleted
identities to a generic typed 401. Certificate retrieval, credential or service failures
return a safe 503. No SDK exception text, token, email or credential is logged or reflected.
This strengthens the plan's minimum sensitive-operation check; its added lookup latency
and hosted permissions remain unmeasured. Installed SDK 7.7.0 compares `iat` with the user
record's `tokens_valid_after_timestamp`; tests exercise that actual SDK path.

Offline tests use the real SDK and locally generated RSA signatures with a local public-key
fixture and user-record fixture. They verify cryptographic acceptance/rejection, expiry,
wrong audience/issuer, custom-token rejection, revocation and disabled-user behavior.
These checks do **not** prove Google certificate retrieval, hosted revocation propagation,
ADC permissions or hosted provider configuration.

## Emulator boundary

[Auth emulator guidance](https://firebase.google.com/docs/emulator-suite/connect_auth)
states that `FIREBASE_AUTH_EMULATOR_HOST` makes the Admin SDK accept unsigned tokens.
Penforge requires all of: local/test environment, an explicit allow flag, a `demo-` project,
an explicit loopback host/port and a matching process environment. Ambient emulator
configuration without that authorization fails startup. A changed emulator environment
also fails verification. No emulator path is permitted for preview/production.

The installed SDK's emulated token path also skips time validation. Penforge therefore
checks required `auth_time`, `iat` and `exp` validity after SDK verification in both modes.
The emulator provides local anonymous, email/password, linking and mocked Google flows;
its Google test credential is not proof of a real Google consent/redirect flow. Its
production anti-abuse and rate limiting are expressly absent. See the phase's acceptance
evidence for actual emulator execution, separate from offline verifier tests.

[Anonymous web authentication](https://firebase.google.com/docs/auth/web/anonymous-auth)
documents unchanged-UID credential linking. Identity Platform's optional automatic cleanup
can delete anonymous accounts after 30 days; it is not enabled by this implementation.
Do not select hosted cleanup until its interaction with Penforge ownership/deletion policy
is reviewed. [Auth limits](https://firebase.google.com/docs/auth/limits) distinguish ordinary
Auth from Identity Platform/Spark limits. Project-specific eligibility and actual quotas
remain hosted configuration checks; no unlimited/no-cost claim follows from emulator use.

## App Check assessment

The [App Check overview](https://firebase.google.com/docs/app-check) currently lists Firebase
Authentication support as **Preview** and distinguishes app/device attestation from user
authentication. [Auth FAQ](https://firebase.google.com/docs/auth/faq-and-troubleshooting)
requires upgrading to Firebase Authentication with Identity Platform to enforce App Check
for Authentication. That upgrade, console enforcement and hosted feature eligibility were
not exercised or authorized by local Phase 01 work.

[Custom backend verification](https://firebase.google.com/docs/app-check/custom-resource-backend)
supports Python Admin `app_check.verify_token()`, independently of protecting Firebase
Auth itself. A later dependency can validate `X-Firebase-AppCheck` beside the existing
Bearer verifier; it must not treat an App Check token as a user identity. SDK 7.6/7.7 release
notes also add/fix Python replay protection; Penforge has not enabled or tested it.

[Web App Check](https://firebase.google.com/docs/app-check/web/custom-resource) sends tokens
in a header rather than a URL. The provider choice, reCAPTCHA Enterprise assessment quota
and billing, allowed domains, enforcement rollout and hosted attestation tests remain open.
[Debug provider guidance](https://firebase.google.com/docs/app-check/web/debug-provider)
is a local development technique; debug credentials are not production abuse protection.
App Check does not replace server ownership checks, rate limits or resource quotas.

## Rate-limit deployment gate

Phase 01 implements an injected, bounded, concurrent process-local limiter for local/test
only. Identity endpoints consume independently scoped request/first-identity/claim windows
and return rate headers plus `Retry-After` on 429. Direct peer addresses are HMACed before
retention; forwarded headers are not trusted. Limits are configurable safety defaults,
not measured capacity. HMAC key versions support rotation; changing the key resets windows,
so deployment must plan overlap or controlled reset and never log raw IP addresses.

Before hosted identity can be enabled, implement and test a shared PostgreSQL limiter with
atomic window-key upsert/increment and bounded expiration cleanup. All API instances must
share that store and a stable HMAC rotation policy; ingress trust and global ceilings need
an explicit deployment decision. An in-process limiter cannot enforce multi-instance
abuse limits. Current configuration blocks hosted identity pending this gate.
