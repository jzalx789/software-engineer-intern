from pathlib import Path
import pytest
from app.db import connect
from conftest import upload


def set_status(client, case_id, status):
    with connect(client.app.state.db_path) as db:
        db.execute('UPDATE cases SET status=? WHERE id=?', (status, case_id))


def test_dirty_csv(client, csv_bytes):
    raw = csv_bytes + b'B,,TEST-ID-2,2000-13-01,2020-01-01,b.jpg\nA,Bob,TEST-ID-3,2000-01-01,2020-01-01,c.jpg\n'
    result = upload(client, raw)
    assert result.status_code == 201
    batch = result.json()
    assert (batch['accepted'], batch['rejected']) == (1, 2)
    assert batch['errors'][0]['row'] == 3
    assert any('applicant_name' in reason for reason in batch['errors'][0]['reasons'])
    assert any('dob' in reason for reason in batch['errors'][0]['reasons'])
    assert 'Duplicate' in batch['errors'][1]['reasons'][0]


def test_sample_counts(client):
    raw = (Path(__file__).parents[2] / 'data/account_applications.csv').read_bytes()
    batch = upload(client, raw).json()
    assert batch['accepted'] + batch['rejected'] == 50
    assert batch['rejected'] > 0
    assert batch['counts']['pending'] == batch['accepted']
    response = client.get(f"/api/batches/{batch['id']}/cases")
    result = response.json()

    assert result["total"] == batch["accepted"]


def test_review_lifecycle(client, csv_bytes):
    batch = upload(client, csv_bytes).json()
    case_id = client.get(f"/api/batches/{batch['id']}/cases").json()['items'][0]['id']
    set_status(client, case_id, 'ocr_done')  # OCR boundary supplied by Q2.
    for stage, expected in [(1, 'stage1_approved'), (2, 'stage2_approved')]:
        response = client.post(f'/api/cases/{case_id}/review', json={'stage': stage, 'decision': 'approve'})
        assert response.status_code == 200
        assert response.json()['status'] == expected


@pytest.mark.parametrize('status,stage', [('pending', 2), ('ocr_failed', 1), ('stage1_approved', 1), ('stage1_rejected', 2), ('stage2_rejected', 2), ('exported', 2)])
def test_invalid_transitions(client, csv_bytes, status, stage):
    upload(client, csv_bytes)
    set_status(client, 1, status)
    response = client.post('/api/cases/1/review', json={'stage': stage, 'decision': 'approve'})
    assert response.status_code == 409
    assert status in response.json()['detail']


@pytest.mark.parametrize('stage,initial,terminal', [(1, 'ocr_done', 'stage1_rejected'), (2, 'stage1_approved', 'stage2_rejected')])
def test_terminal_rejection(client, csv_bytes, stage, initial, terminal):
    upload(client, csv_bytes)
    set_status(client, 1, initial)
    assert client.post('/api/cases/1/review', json={'stage': stage, 'decision': 'reject'}).json()['status'] == terminal
    assert client.post('/api/cases/1/review', json={'stage': stage, 'decision': 'approve'}).status_code == 409


def test_invalid_header_and_pagination(client, csv_bytes):
    assert upload(client, b'wrong\n').status_code == 422
    batch = upload(client, csv_bytes).json()
    assert client.get(f"/api/batches/{batch['id']}/cases?page=0").status_code == 422
    assert client.get('/api/batches/missing').status_code == 404
    assert client.get('/api/ready').json() == {'ready': True}

def test_pending_cannot_be_approved(client, csv_bytes):
    upload(client, csv_bytes)

    response = client.post(
        "/api/cases/1/review",
        json={"stage": 1, "decision": "approve"}
    )

    assert response.status_code == 409
    assert "pending" in response.json()["detail"]

def test_stage1_approval(client, csv_bytes):
    upload(client, csv_bytes)
    set_status(client, 1, "ocr_done")

    response = client.post(
        "/api/cases/1/review",
        json={"stage": 1, "decision": "approve"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "stage1_approved"
    repeated = client.post(
        "/api/cases/1/review",
        json={"stage": 1, "decision": "approve"}
    )

    assert repeated.status_code == 409
