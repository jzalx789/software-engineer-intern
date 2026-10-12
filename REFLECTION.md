# Implementation reflection

## 1. Design decisions

| Decision | Rationale |
|---|---|
| Database | SQLite, accepted by the exercise. Transactions fit a small, single-process batch service, avoid an extra database service, and make clean local startup simple. WAL permits reads during writes; each operation opens/closes its own connection. A Docker named volume persists the file. |
| CSV import | Multipart field `file`; no arbitrary server file paths. Validate headers, required values, synthetic ID prefix, calendar dates, chronology and duplicate references. Sample: 43 accepted, 7 rejected. Errors include CSV row numbers. |
| Background OCR | One asyncio task per batch, sequential cases, one uvicorn worker. `/process` returns 202 without waiting. Successful/failed results commit per case; unfinished work stays `pending`, avoiding a stranded intermediate state. Active-task ownership is in memory; after a crash, staff call `/process` again. No Celery/queue is needed for the exercise scale. Multiple API processes are not supported. |
| Review state machine | `q1_api/app/states.py` owns the review transition table. Invalid transitions return 409. SQLite `BEGIN IMMEDIATE` serializes competing decisions. Rejections at either stage are terminal. |
| Export/delivery | Persist CSV bytes and mark selected stage-2 approvals exported in one transaction, before calling onboarding. The key is SHA-256 of a unique export filename, a separator, and the exact CSV bytes. Retries reuse persisted bytes/key; a new export has a new filename and key. A lost response can therefore recover the original receipt. Exporting and delivering are distinct states: staff can inspect and retry a failed delivery without re-exporting the cases. |

## 2. Mock observations and retry configuration

Live evidence was obtained with unmodified mocks, seeded OCR 42/onboarding 7, configured OCR 500/hang probabilities 20%/10%, original hang durations 600/120 seconds. `q2_integration/verify.py` starts isolated localhost services, sends the actual CSV, and reads `/debug/requests` and `/debug/deliveries`. No failures were disabled.

| Item | Measurement or decision |
|---|---|
| Observed OCR outcomes | Clean run: 66 recorded requests, 37 `ok` (56.06%), 14 `error_500` (21.21%), 10 `hang` (15.15%), 5 `unreadable` (7.58%). Among 61 readable attempts, 500/hang rates were 22.95%/16.39%. These are observed request rates, not configured probabilities or final case failure rates. |
| Final case outcomes | 43 valid cases: 37 `ocr_done`, 6 `ocr_failed`. Five unreadable cases (11.63% of accepted cases) and one exhausted transient sequence, APPL-2026-0038. The separate smoke batch completed with 38 done and 5 unreadable failures because the random request sequence continued. |
| Timeouts | HTTP read/connect defaults 2s/1s. Initial contract probes: normal OCR 200 about 45ms, unreadable 422 about 19ms, onboarding lost response timed out at 2.015s; retry recovered a receipt in about 33ms. Two seconds provides substantial local-response margin while bounding injected 600s/120s delays. This is mock-specific, not a production latency claim. |
| Retry limit | 5 total HTTP attempts; exponential pauses 0.2, 0.4, 0.8, 1.6 seconds. Retry transport/timeouts, 5xx and 429. Do not retry 422/400. Every case commits success or failure; a process crash leaves uncommitted cases pending. |
| Staff-visible failure | `ocr_failed` with vendor reason or `Retries exhausted after 5 attempts`. List using the status filter. Replace unreadable input in a new batch. For temporary failures, restore the upstream then invoke `/process?retry_failed=true`. Staff cannot review failed cases. |

## 3. Verification evidence

### Tests and live local workflow

`python -m pytest q1_api/`: 19 passed on Python 3.13.1. One third-party Starlette/AnyIO deprecation warning remains; it does not indicate test failure. The tests cover CSV rejection counts/reasons, review lifecycle and terminal rejections, six invalid transitions, OCR retry exhaustion, explicit staff retries, export exclusion/unique keys, lost receipts, durable export recovery, and readiness failure with a live API.

The actual API and both mocks also ran without Docker. `q2_integration/evidence/clean.json` contains the smoke output, ending `SMOKE TEST PASSED`: 43 accepted, 7 rejected, 38 OCR successes reviewed through both stages, 38 exported, receipt hash matched downloaded bytes, and a repeated export created no new file. `/process` returned in about 0.002 seconds.

### OCR recovery

The API was killed with SIGKILL while `/debug/requests` showed a `hang`; the mock and database were retained. After restart there were 42 pending cases and one committed OCR success. Calling `/process` again completed the remaining work. `evidence/comparison.json` records `identical: true`, comparing the final status and parsed fields for all 43 cases. `clean.json` and `interrupted.json` retain the raw request records. One case exhausted its bounded retry limit in both runs; this is disclosed rather than counted as OCR success.

### Account-opening submission

Clean export receipt `DLV-0001`: 37 cases, exactly one `/debug/deliveries` record, `attempts: 2`, and recovered response `deduplicated: true`. Seed 7 delays the first receipt after ingestion; the retry used the same persisted key and returned the original receipt. Complete keys/hashes/records are in `evidence/clean.json`. No duplicate ingestion occurred. A later export with no approvals returned `created_export_id: null`.

### Backup retention

Online local verification kept a real uvicorn API running while creating 10 SQLite backups. Only the seven most recent remained. Restoring the newest backup returned `PRAGMA integrity_check = ok` and 43 cases. `q3_deploy/backup-evidence.json` contains the full evidence. Before the final three backups:

```text
backup-20261012T123623-55155.db
backup-20261012T123623-55165.db
backup-20261012T123624-55175.db
backup-20261012T123624-55185.db
backup-20261012T123624-55195.db
backup-20261012T123624-55205.db
backup-20261012T123624-55215.db
```

After:

```text
backup-20261012T123624-55185.db
backup-20261012T123624-55195.db
backup-20261012T123624-55205.db
backup-20261012T123624-55215.db
backup-20261012T123624-55225.db
backup-20261012T123624-55235.db
backup-20261012T123624-55245.db
```

### Container verification

Docker is not installed on the local Mac. The provided GitHub Actions workflow runs pytest, `docker compose build`, health-dependent startup from its clean checkout, nginx configuration validation, the complete smoke workflow through nginx, `/ocr/parse` routing, a 413 upload-size check, and an online container backup. The first run built containers and passed the full smoke test, but the additional routing check incorrectly requested `/ocr/online` (the mock health path is `/online`). That check was corrected to POST the documented `/ocr/parse` with an unreadable image and require its 422 reason. The next run also checks seven-backup retention, stopped-service restoration, and readiness after restart. The second run passed routing, upload limits, and 10-backup retention, but restoration from a read-only mount failed because a WAL-mode snapshot could attempt to create SQLite sidecar files. Backups now explicitly switch their destination to DELETE journal mode before serialization; static restore sources also use `immutable=1`. The corrected container run [38251081158](https://github.com/jzalx789/software-engineer-intern/actions/runs/38251081158) passed on commit `ef6873ac59e3175ba3a41f5b290e5c6f40b096cd`. All 19 tests, container builds, startup health checks, nginx validation, the full smoke workflow, documented OCR routing, 413 upload limit, ten online backups retained as seven, stopped-service restoration (integrity ok and 43 cases), and readiness after restart passed. Log excerpts are in `q3_deploy/ci-evidence.txt`; container commands have not been claimed as locally verified.

## 4. AI-generated code requiring further review

Codex materially assisted application code, tests, integration verification, deployment files, and documentation. The candidate stated that they understand the code; this statement has not been independently assessed. The candidate will complete the retained `AI_LOG.md` template before submission. A short architecture guide is in `q1_api/EXPLAIN.md`.

## 5. Incomplete work and limitations

- `AI_LOG.md` is intentionally left for the candidate to complete; the submission documentation is incomplete until then.
- Container deployment and restore passed in GitHub Actions from a clean checkout. The Mac cannot perform local Docker startup without a Docker engine; use the tested runbook on a Docker-enabled machine.
- Background-task ownership assumes one API process. With another day, use a durable queue/lease for multiple workers and add authenticated staff accounts; these are beyond the required mock exercise.
- Both mock debug/deduplication stores are in memory. A mock-onboarding restart erases its keys. An unknown external outcome across that restart requires operational reconciliation, not blind redelivery.
- No recruitment submission email has been sent. The recipient/address and final approved documentation must be available before submission.
