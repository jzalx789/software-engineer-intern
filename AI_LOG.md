# AI interaction record

## Interaction 1: Implementation, testing, and deployment support

| Field | Description |
|---|---|
| Tool or model | ChatGPT Codex |
| Prompt or record | Requested AI assistance with implementation and verification of the take-home exercise against the repository requirements and supplied API contracts. The scope included CSV validation, SQLite persistence, review-state enforcement, OCR retry and recovery, export delivery idempotency, and container deployment. Assistance also covered automated testing, failure diagnosis, backup restoration checks, and technical documentation. Selected Q1 logic was discussed through an interactive code walkthrough. |
| Affected files | - Application: q1_api/app/main.py, db.py, states.py, validation.py, integration.py<br>- Tests: q1_api/tests/conftest.py, test_app.py, test_integration.py; q1_api/pytest.ini<br>- Integration verification: q2_integration/verify.py; q2_integration/evidence/clean.json, interrupted.json, comparison.json<br>- Deployment: q3_deploy/Dockerfile, docker-compose.yml, nginx.conf, .env.example<br>- Operations: q3_deploy/scripts/backup.sh, smoke_test.sh; q3_deploy/RUNBOOK.md, backup-evidence.json, ci-evidence.txt<br>- CI and configuration: .github/workflows/ci.yml, requirements.txt, .gitignore, .dockerignore<br>- Documentation: q1_api/README.md, EXPLAIN.md; q2_integration/README.md; REFLECTION.md; TOOLS.md |

### Adopted output

AI-assisted code and revisions were incorporated into the FastAPI application, SQLite persistence layer, CSV validation, review transition table, background OCR worker, retry handling, durable exports, and idempotent delivery logic. Assistance also produced automated tests, a live-mock recovery verification script, Docker Compose and nginx configuration, backup and smoke-test scripts, CI, and supporting documentation. Selected Q1 validation and review behavior was explained through an interactive walkthrough; the candidate edited and ran tests during that walkthrough.

### Changes and rejected output

The implementation used SQLite, which the exercise explicitly accepts, to keep local startup and transaction handling simple. Background OCR was limited to one API process with sequential case processing; this limitation is documented rather than implying multi-worker support. Temporary diagnostic prints used during the walkthrough were removed from the application. Verification exposed two deployment issues that were corrected: the CI routing probe used an undocumented OCR health path, and read-only restoration of WAL-mode snapshots could require sidecar files. The probe now uses the documented parse endpoint; backups use DELETE journal mode and static restore sources use immutable reads. Observed retry exhaustion was retained as a recorded failure rather than reported as OCR success. AI_LOG wording and the affected-file list were edited by the candidate before the remaining record sections were completed with AI assistance.
