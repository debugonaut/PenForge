# 0014 — Retries, cancellation and monotonic fencing

Status: accepted for local Phase 06. Date: 2026-10-20.

Every worker write requires job ID, owner, holder, monotonic attempt, unexpired lease and
active owner/project. Existing candidate/export deferred publication guards remain. Shared
reaping changes state, writes an event and settles terminal reservations in one transaction.
Zero-row heartbeat/fenced operations stop publication; isolated children poll cancellation.

Infrastructure retries use bounded exponential backoff with injected random/clock seams.
Two identical coded failures at the same stage and immutable input identity stop as
poison_suspected. Invalid/security input and validation failures are permanent; unsupported
payloads remain queued for compatible workers. needs_better_input is still a successful
assessment with retake flags. Resource shortage never chooses a different quality policy.

Queued cancellation is immediately terminal. Running cancellation requests a safe stop;
the worker acknowledges at a boundary or the reaper completes it after lease expiry.
SIGTERM stops claims, completes/checkpoints the current bounded unit and requeues. Contrary
to the source's proposed attempts decrement, fencing counters never decrease: graceful
requeue grants one replacement attempt by increasing max_attempts. Thus a fixed process
holder cannot accidentally revive a previous attempt. No failed-attempt budget is charged.
