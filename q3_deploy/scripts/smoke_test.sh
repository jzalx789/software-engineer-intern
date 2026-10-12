#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export BASE_URL="${BASE_URL:-http://localhost:8080}"
"${PYTHON:-python3}" - <<'PY'
import csv, hashlib, io, json, os, time, urllib.request, uuid
from pathlib import Path

base = os.environ['BASE_URL'].rstrip('/')

def request(path, data=None, headers=None, json_result=True):
    req = urllib.request.Request(base + path, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=10) as response:
        body = response.read()
    return json.loads(body) if json_result else body

def post(path, body=None):
    return request(path, json.dumps(body or {}).encode(), {'Content-Type': 'application/json'})

def poll(fetch, complete):
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        item = fetch()
        if complete(item):
            return item
        time.sleep(0.2)
    raise RuntimeError('Workflow did not finish within 300 seconds')

assert request('/api/ready')['ready']
boundary = 'batch-' + uuid.uuid4().hex
raw = Path('../data/account_applications.csv').read_bytes()
body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="applications.csv"\r\nContent-Type: text/csv\r\n\r\n'.encode()
        + raw + f'\r\n--{boundary}--\r\n'.encode())
batch = request('/api/batches', body, {'Content-Type': f'multipart/form-data; boundary={boundary}'})
assert (batch['accepted'], batch['rejected']) == (43, 7), batch
identifier = batch['id']
start = time.monotonic()
post(f'/api/batches/{identifier}/process')
returned_in = time.monotonic() - start
assert returned_in < 2, returned_in
batch = poll(lambda: request(f'/api/batches/{identifier}'),
             lambda item: not item['processing'] and item['counts']['pending'] == 0)
cases = request(f'/api/batches/{identifier}/cases?page_size=100')['items']
approved = [item for item in cases if item['status'] == 'ocr_done']
assert approved
for item in approved:
    for stage in (1, 2):
        result = post(f"/api/cases/{item['id']}/review", {'stage': stage, 'decision': 'approve'})
        assert result['status'] == f'stage{stage}_approved'
export = post(f'/api/batches/{identifier}/export')
export_id = export['created_export_id']
assert export_id
item = poll(lambda: request(f'/api/exports/{export_id}'), lambda value: value['delivery_status'] != 'pending')
assert item['delivery_status'] == 'delivered', item
content = request(f'/api/exports/{export_id}/download', json_result=False)
exported = list(csv.DictReader(io.StringIO(content.decode())))
assert len(exported) == len(approved)
assert {row['case_ref'] for row in exported} == {row['case_ref'] for row in approved}
assert item['receipt']['sha256'] == hashlib.sha256(content).hexdigest()
assert post(f'/api/batches/{identifier}/export')['created_export_id'] is None
summary = {'batch_id': identifier, 'accepted': batch['accepted'], 'rejected': batch['rejected'],
           'ocr_done': len(approved), 'ocr_failed': batch['counts']['ocr_failed'],
           'process_return_seconds': round(returned_in, 4), 'export_id': export_id,
           'export_count': len(exported), 'receipt': item['receipt']}
print(json.dumps(summary, indent=2))
print('SMOKE TEST PASSED')
PY
