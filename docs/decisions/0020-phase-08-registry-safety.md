# 0020 — Controlled, verified model artifacts and live revocation

Status: accepted for local Phase 08. Date: 2026-10-20.

model_versions remains the registry authority. The read-only service maps existing
rows into explicit task/script/style/input support. Artifacts resolve only under
models/opaque-UUID keys through ObjectStorage. No filesystem path, URL, module or
framework loader is accepted. ONNX/safetensors remain opaque verified bytes; no
inference runtime is installed or handwriting model represented. JSON rules use one
bounded enum-map schema, with no executable expression or external reference.
Container checks reject disguised pickle bytes: ONNX requires its canonical ir_version
prefix; safetensors validates a bounded header, dtypes/shapes and exact contiguous tensor
payload. This is not full ONNX graph validation or an inference runtime. A future runtime
needs separate qualification and safe operator/resource validation before activation.

Each cache use resolves live approval, then verifies exact size/hash and requested
schemas, support, worker range and license/provenance. Promotion may require pinned
measured resources. Revocation fails cached access. The bounded in-memory LRU rechecks
bytes on every hit and supports explicit eviction. It stores no private user samples.
Worker and application grants cannot approve registry rows; independent offline release
review remains necessary. Local administrative credentials are not least privilege.

Synthetic enum maps test security/compatibility only. No model, benchmark, data license,
training job, production promotion or provider has been created.
