import hashlib
import json
import time

import httpx
from fastapi.testclient import TestClient

from app.db import connect
from app.integration import Settings
from app.main import create_app
from conftest import upload

FIELDS = {'id_number': 'TEST-ID-123', 'full_name': 'Test Person', 'dob': '2000-01-01', 'issue_date': '2020-01-01', 'confidence': 0.95}
SETTINGS = Settings('http://mock/ocr/parse', 'http://mock/ingest', 0.1, 3, 0.001)


def wait_batch(client, batch_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        batch = client.get(f'/api/batches/{batch_id}').json()
        if not batch['processing'] and batch['counts']['pending'] == 0:
            return batch
        time.sleep(0.005)
    raise AssertionError('OCR did not finish')


def wait_export(client, export_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        item = client.get(f'/api/exports/{export_id}').json()
        if item['delivery_status'] != 'pending':
            return item
        time.sleep(0.005)
    raise AssertionError('Delivery did not finish')


def test_full_lifecycle_retry_and_lost_receipt(tmp_path, csv_bytes):
    ocr_attempts, deliveries, keys = [], {}, []

    def mock(request):
        if request.url.path == '/ocr/parse':
            ocr_attempts.append(request)
            if len(ocr_attempts) == 1:
                return httpx.Response(500, json={'error': 'transient'})
            if len(ocr_attempts) == 2:
                raise httpx.ReadTimeout('lost OCR response', request=request)
            return httpx.Response(200, json=FIELDS)
        key = request.headers['Idempotency-Key']
        keys.append(key)
        if key not in deliveries:
            deliveries[key] = {'delivery_id': f'DLV-{len(deliveries)+1}', 'idempotency_key': key,
                               'sha256': hashlib.sha256(request.content).hexdigest(), 'file_name': request.headers['X-File-Name']}
            raise httpx.ReadTimeout('ingested, receipt lost', request=request)
        return httpx.Response(200, json={**deliveries[key], 'deduplicated': True})

    with TestClient(create_app(str(tmp_path / 'service.db'), SETTINGS, httpx.MockTransport(mock))) as client:
        batch_id = upload(client, csv_bytes).json()['id']
        response = client.post(f'/api/batches/{batch_id}/process')
        assert response.status_code == 202
        assert wait_batch(client, batch_id)['counts']['ocr_done'] == 1
        case = client.get(f'/api/batches/{batch_id}/cases').json()['items'][0]
        assert case['parsed_fields'] == FIELDS
        for stage in (1, 2):
            assert client.post(f"/api/cases/{case['id']}/review", json={'stage': stage, 'decision': 'approve'}).status_code == 200
        export_id = client.post(f'/api/batches/{batch_id}/export').json()['created_export_id']
        item = wait_export(client, export_id)
        assert item['delivery_status'] == 'delivered'
        assert item['receipt']['deduplicated'] is True
        assert len(deliveries) == 1 and len(keys) == 2 and len(set(keys)) == 1
        assert len(ocr_attempts) == 3
        assert client.get(f'/api/exports/{export_id}/download').text.count('A,Alice') == 1
        assert client.post(f'/api/batches/{batch_id}/export').json()['created_export_id'] is None
        assert client.get(f'/api/batches/{batch_id}').json()['counts']['exported'] == 1
        before = len(ocr_attempts)
        client.post(f'/api/batches/{batch_id}/process')
        assert wait_batch(client, batch_id)['counts']['exported'] == 1
        assert len(ocr_attempts) == before


def test_unreadable_and_exhaustion_and_staff_retry(tmp_path, csv_bytes):
    attempts = {'unreadable': 0, 'transient': 0}

    def mock(request):
        image = json.loads(request.content)['image_ref']
        if 'badscan' in image:
            attempts['unreadable'] += 1
            return httpx.Response(422, json={'error': 'scan quality'})
        attempts['transient'] += 1
        return httpx.Response(500, json={'error': 'temporarily unavailable'})

    raw = csv_bytes + b'B,Bob,TEST-ID-2,2000-01-01,2020-01-01,badscan.jpg\n'
    with TestClient(create_app(str(tmp_path / 'service.db'), SETTINGS, httpx.MockTransport(mock))) as client:
        batch_id = upload(client, raw).json()['id']
        client.post(f'/api/batches/{batch_id}/process')
        assert wait_batch(client, batch_id)['counts']['ocr_failed'] == 2
        cases = client.get(f'/api/batches/{batch_id}/cases?status=ocr_failed').json()['items']
        assert 'exhausted after 3' in cases[0]['failure_reason']
        assert 'scan quality' in cases[1]['failure_reason']
        assert attempts == {'unreadable': 1, 'transient': 3}
        assert client.post('/api/cases/1/review', json={'stage': 1, 'decision': 'approve'}).status_code == 409
        client.app.state.integration.transport = httpx.MockTransport(lambda request: httpx.Response(200, json=FIELDS))
        client.post(f'/api/batches/{batch_id}/process?retry_failed=true')
        assert wait_batch(client, batch_id)['counts']['ocr_done'] == 2


def test_recovery_of_pending_case_and_committed_export(tmp_path, csv_bytes):
    path = str(tmp_path / 'recovery.db')
    with TestClient(create_app(path, SETTINGS)) as client:
        batch_id = upload(client, csv_bytes).json()['id']
        # Persist an export but intentionally never start its delivery worker.
        with connect(path) as db:
            db.execute("UPDATE cases SET status='stage2_approved',parsed_fields=? WHERE id=1", (json.dumps(FIELDS),))
        export_id = client.app.state.integration.create_export(batch_id)
    receipt = {}

    def mock(request):
        receipt.update(delivery_id='DLV-1', idempotency_key=request.headers['Idempotency-Key'],
                       sha256=hashlib.sha256(request.content).hexdigest())
        return httpx.Response(200, json=receipt)

    with TestClient(create_app(path, SETTINGS, httpx.MockTransport(mock))) as client:
        assert client.post(f'/api/batches/{batch_id}/export').json()['created_export_id'] is None
        assert wait_export(client, export_id)['receipt'] == receipt
        assert len(client.get(f'/api/batches/{batch_id}').json()['exports']) == 1


def test_export_excludes_rejected_and_has_distinct_keys(tmp_path, csv_bytes):
    path = str(tmp_path / 'exports.db')
    with TestClient(create_app(path, SETTINGS)) as client:
        batch_id = upload(client, csv_bytes + b'B,Bob,TEST-ID-2,2000-01-01,2020-01-01,b.jpg\n').json()['id']
        with connect(path) as db:
            db.execute("UPDATE cases SET status='stage2_approved' WHERE id=1")
            db.execute("UPDATE cases SET status='stage2_rejected' WHERE id=2")
        service = client.app.state.integration
        first = service.create_export(batch_id)
        assert service.exports(batch_id)[0]['case_count'] == 1
        assert 'Bob' not in client.get(f'/api/exports/{first}/download').text
        assert service.create_export(batch_id) is None
        # A later independent approval creates a separate export identity.
        with connect(path) as db:
            db.execute("INSERT INTO cases (batch_id,case_ref,applicant_name,id_number,dob,issue_date,image_ref,status) VALUES (?,?,?,?,?,?,?,?)",
                       (batch_id, 'C', 'Carol', 'TEST-ID-3', '2000-01-01', '2020-01-01', 'c.jpg', 'stage2_approved'))
        second = service.create_export(batch_id)
        exports = service.exports(batch_id)
        assert first != second
        assert exports[0]['idempotency_key'] != exports[1]['idempotency_key']
        assert 'Alice' not in client.get(f'/api/exports/{second}/download').text


def test_readiness_checks_database(client):
    assert client.get('/api/ready').status_code == 200
    with connect(client.app.state.db_path) as db:
        db.execute('DROP TABLE exports')
        db.execute('DROP TABLE cases')
        db.execute('DROP TABLE batches')
    assert client.get('/api/health').status_code == 200
    assert client.get('/api/ready').status_code == 503
