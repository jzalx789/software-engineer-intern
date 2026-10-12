import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(str(tmp_path / 'test.db'))) as client:
        yield client


@pytest.fixture
def csv_bytes():
    return b'case_ref,applicant_name,id_number,dob,issue_date,image_ref\nA,Alice,TEST-ID-1,2000-01-01,2020-01-01,id_scan_0001.jpg\n'


def upload(client, raw):
    return client.post('/api/batches', files={'file': ('batch.csv', raw, 'text/csv')})
