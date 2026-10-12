# Module 2: OCR integration and export delivery

Add OCR, export, and delivery modules to `q1_api/app/`. Register the new endpoints on the existing `app.main:app` application. Keep the [fixed repository layout](../README.md#2-fixed-structure-and-naming).

Read the [Module 2 requirements](../README.md#4-module-2-ocr-integration-and-export-delivery-35-points), [OCR contract](../mock_ocr/README.md), and [account-opening contract](../mock_onboarding/README.md) before implementing the clients.

## Run without Docker

Start each service in a separate terminal from the repository root:

```bash
python3 mock_ocr/server.py
```

```bash
python3 mock_onboarding/server.py
```

```bash
cd q1_api
uvicorn app.main:app --reload
```

Configure the API to use `http://localhost:9100/ocr/parse` and `http://localhost:9200/ingest`. Document the API configuration method after implementation.

Verify one batch through import, background OCR, both review stages, export, and submission to the account-opening service. Record retry and recovery evidence in [REFLECTION.md](../REFLECTION.md).

## Implemented workflow and configuration

Start the mocks as above. From the root, install requirements and run:

```bash
cd q1_api
DATABASE_PATH=./data/service.db uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Environment variables are `DATABASE_PATH`, `OCR_URL`, `ONBOARDING_URL`, `REQUEST_TIMEOUT` (2 seconds), `MAX_ATTEMPTS` (5 total attempts), and `RETRY_BACKOFF` (0.2 seconds, doubled between attempts). Local defaults use the localhost mock addresses. Environment variables must be exported before startup; local non-Docker mode does not automatically read `.env`.

1. `POST /api/batches` with multipart field `file`.
2. `POST /api/batches/{id}/process` returns 202 immediately; poll batch counts and `processing`. Unfinished work stays `pending` across crashes, so call `/process` again after restart. Repeated calls while a worker is active reuse that worker. Completed cases are skipped. To deliberately retry failures after fixing the upstream/input, use `/process?retry_failed=true` (409 while already active). Unreadable scans cannot be repaired by retry alone; upload corrected applicant/image references in a new batch.
3. Review `ocr_done` cases with `POST /api/cases/{id}/review`; approve stages 1 and 2 in order.
4. `POST /api/batches/{id}/export` returns `created_export_id` plus all batch export records. It commits the CSV bytes and the `exported` transitions atomically, then delivers asynchronously. No newly approved cases means no new export. The same call also resumes outstanding deliveries.
5. `GET /api/exports/{id}` shows delivery state, receipt, last error and `delivery_runs`. Download CSV with `GET /api/exports/{id}/download`. Retry a failed or interrupted delivery using `POST /api/exports/{id}/deliver`; bytes and key remain unchanged. `delivery_runs` counts completed worker runs, not upstream HTTP attempts; receipts expose ingestion attempts.

Verify live mocks with `python q2_integration/verify.py` from the root. It starts isolated processes, captures `/debug/requests`, kills the API during a recorded delayed OCR request, resumes against the same database/mocks, and compares every final status/parsed field against a clean run. It also tests lost onboarding responses, the shared smoke script, and online backups. Evidence is stored in `evidence/` and `../q3_deploy/backup-evidence.json`. Test data is entirely synthetic.

For a live local smoke test, with all three services running, use `BASE_URL=http://localhost:8000 bash scripts/smoke_test.sh` from `q3_deploy/`.
