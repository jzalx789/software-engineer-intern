# AI interaction record

Record interactions that influenced architecture, implementation decisions, or generated code. Include prompt excerpts, conversation links, or exported records. Routine lookups do not require entries.

Complete one entry per relevant interaction using the template below. Replace bracketed prompts and add entries as needed. If AI did not materially influence the code, state that and remove the blank entry.

## Interaction 1: [Topic]

| Field | Description |
|---|---|
| Tool or model | ChatGPT Codex |
| Prompt or record | Requested AI assistance with implementation and verification of the take-home exercise against the repository requirements and supplied API contracts. The scope included CSV validation, SQLite persistence, review-state enforcement, OCR retry and recovery, export delivery idempotency, and container deployment. Assistance also covered automated testing, failure diagnosis, backup restoration checks, and technical documentation. Selected Q1 logic was discussed through an interactive code walkthrough. |
| Affected files | - Application: q1_api/app/main.py, db.py, states.py, validation.py, integration.py<br>- Tests: q1_api/tests/conftest.py, test_app.py, test_integration.py; q1_api/pytest.ini<br>- Integration verification: q2_integration/verify.py; q2_integration/evidence/clean.json, interrupted.json, comparison.json<br>- Deployment: q3_deploy/Dockerfile, docker-compose.yml, nginx.conf, .env.example<br>- Operations: q3_deploy/scripts/backup.sh, smoke_test.sh; q3_deploy/RUNBOOK.md, backup-evidence.json, ci-evidence.txt<br>- CI and configuration: .github/workflows/ci.yml, requirements.txt, .gitignore, .dockerignore<br>- Documentation: q1_api/README.md, EXPLAIN.md; q2_integration/README.md; REFLECTION.md; TOOLS.md |

### Adopted output

[Describe the suggestion or generated code included in the implementation.]

### Changes and rejected output

[Describe what you changed or rejected and explain why. Enter `None` if applicable.]
