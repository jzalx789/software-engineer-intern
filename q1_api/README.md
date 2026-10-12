# Module 1: Core API

Implement the API in `app/` and tests in `tests/`. Keep the entry point importable as `app.main:app`. Organize database access, validation, and transition rules into modules within `app/`.

The [Module 1 requirements](../README.md#3-module-1-core-api-35-points) define endpoints, review behavior, and test coverage. The [repository layout](../README.md#2-fixed-structure-and-naming) defines fixed paths and names.

## Start the API

From the repository root:

```bash
python -m pip install -r requirements.txt
cd q1_api
uvicorn app.main:app --reload
```

The scaffold exposes `GET /api/health`. Complete the remaining endpoints before testing the application workflow.

## Run tests

From the repository root:

```bash
python -m pytest q1_api/
```

Replace the scaffold import test with tests covering the required behavior. The suite must pass from a clean checkout.

## Implemented interface

Upload using multipart field `file`, for example from the root:

```bash
curl -F file=@data/account_applications.csv http://localhost:8000/api/batches
```

The sample accepts 43 rows and rejects 7; rejection records use physical CSV line numbers starting at 2. Headers must match the supplied column names and order. Duplicate references are scoped to one batch. The API caps CSV bytes at 5 MiB and accepts UTF-8 (with or without BOM). Missing headers or malformed input return 422; individual invalid rows are reported within the created batch (201).

`GET /api/batches/{id}` includes accepted/rejected counts, errors, per-status counts, `processing`, and export delivery records. The cases endpoint returns `items`, `total`, `page`, and `page_size` (1..100); `status` accepts the states in `app/states.py`. Each case exposes original applicant metadata, separate `parsed_fields`, and any `failure_reason`. Reviews return 200 or 409 with a reason; invalid request stage/decision values return 422. Rejections at either stage are terminal. `GET /api/health` is liveness; `/api/ready` checks SQLite reachability.

Set `DATABASE_PATH` to select the SQLite file. Parent directories are created on startup; do not share a file across independent submissions. Run one API worker for background processing ownership. Full integration instructions are in `../q2_integration/README.md`.
