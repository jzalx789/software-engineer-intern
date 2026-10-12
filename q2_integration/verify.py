"""Reproducible live-mock evidence; run from root with the project Python."""
import collections
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'q2_integration/evidence'
EVIDENCE.mkdir(exist_ok=True)


def port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def wait(fetch, predicate=lambda item: True, limit=180):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        try:
            item = fetch()
            if predicate(item):
                return item
        except (httpx.TransportError, httpx.HTTPStatusError):
            pass
        time.sleep(0.05)
    raise RuntimeError('Wait exceeded deadline')


def phase(interrupt=False):
    processes = []
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary)
        ocr_port, onboarding_port, api_port = port(), port(), port()
        api_url = f'http://127.0.0.1:{api_port}'
        ocr_url = f'http://127.0.0.1:{ocr_port}'
        onboarding_url = f'http://127.0.0.1:{onboarding_port}'
        env = dict(os.environ, DATABASE_PATH=str(work / 'service.db'),
                   OCR_URL=ocr_url + '/ocr/parse', ONBOARDING_URL=onboarding_url + '/ingest',
                   REQUEST_TIMEOUT='2', MAX_ATTEMPTS='5', RETRY_BACKOFF='0.2',
                   OCR_FAIL_RATE='0.20', OCR_HANG_RATE='0.10', OCR_HANG_SECONDS='600',
                   ONBOARDING_FAIL_RATE='0.20', ONBOARDING_LOST_RATE='0.15', ONBOARDING_LOST_SECONDS='120')
        handles = []

        def start(command, cwd, name):
            handle = open(work / (name + '.log'), 'ab')
            handles.append(handle)
            process = subprocess.Popen(command, cwd=cwd, env=env, stdout=handle, stderr=subprocess.STDOUT)
            processes.append(process)
            return process

        def start_api():
            return start([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(api_port)], ROOT / 'q1_api', 'api')

        try:
            start([sys.executable, 'mock_ocr/server.py', '--port', str(ocr_port), '--seed', '42'], ROOT, 'ocr')
            start([sys.executable, 'mock_onboarding/server.py', '--port', str(onboarding_port), '--seed', '7'], ROOT, 'onboarding')
            api = start_api()
            with httpx.Client(timeout=5, trust_env=False) as client:
                def get(url):
                    response = client.get(url)
                    response.raise_for_status()
                    return response.json()

                def post(path, **kwargs):
                    response = client.post(api_url + path, **kwargs)
                    response.raise_for_status()
                    return response.json()

                wait(lambda: get(ocr_url + '/online'))
                wait(lambda: get(onboarding_url + '/online'))
                wait(lambda: get(api_url + '/api/ready'))
                raw = (ROOT / 'data/account_applications.csv').read_bytes()
                batch = post('/api/batches', files={'file': ('sample.csv', raw, 'text/csv')})
                batch_id = batch['id']
                before = time.monotonic()
                post(f'/api/batches/{batch_id}/process')
                returned_in = time.monotonic() - before
                interruption = None
                if interrupt:
                    recorded = wait(lambda: get(ocr_url + '/debug/requests'), lambda rows: any(row['outcome'] == 'hang' for row in rows))
                    api.kill()
                    api.wait(timeout=5)
                    interruption = {'requests_at_kill': recorded}
                    api = start_api()
                    wait(lambda: get(api_url + '/api/ready'))
                    interruption['batch_after_restart'] = get(api_url + f'/api/batches/{batch_id}')
                    post(f'/api/batches/{batch_id}/process')
                final = wait(lambda: get(api_url + f'/api/batches/{batch_id}'),
                             lambda item: not item['processing'] and item['counts']['pending'] == 0)
                cases = get(api_url + f'/api/batches/{batch_id}/cases?page_size=100')['items']
                requests = get(ocr_url + '/debug/requests')
                outcomes = dict(collections.Counter(row['outcome'] for row in requests))
                report = {'configuration': {'ocr_seed': 42, 'onboarding_seed': 7, 'ocr_fail_rate': 0.20,
                          'ocr_hang_rate': 0.10, 'timeout': 2, 'max_attempts': 5, 'backoff': 0.2},
                          'process_return_seconds': round(returned_in, 4), 'batch': final, 'cases': cases,
                          'requests': requests, 'outcomes': outcomes, 'request_count': len(requests),
                          'outcome_rates': {key: round(count / len(requests), 6) for key, count in outcomes.items()},
                          'interruption': interruption}
                if not interrupt:
                    for case in cases:
                        if case['status'] == 'ocr_done':
                            for stage in (1, 2):
                                post(f"/api/cases/{case['id']}/review", json={'stage': stage, 'decision': 'approve'})
                    export_id = post(f'/api/batches/{batch_id}/export')['created_export_id']
                    report['export'] = wait(lambda: get(api_url + f'/api/exports/{export_id}'),
                                            lambda item: item['delivery_status'] != 'pending')
                    assert report['export']['delivery_status'] == 'delivered'
                    report['deliveries'] = get(onboarding_url + '/debug/deliveries')
                    assert len(report['deliveries']) == 1
                    assert report['deliveries'][0]['attempts'] >= 2  # Seed 7 loses first response.
                    assert post(f'/api/batches/{batch_id}/export')['created_export_id'] is None
                    report['repeated_export_created'] = False
                    # Smoke script independently drives another batch through real services.
                    smoke = subprocess.run(['bash', 'scripts/smoke_test.sh'], cwd=ROOT / 'q3_deploy',
                                           env=dict(env, BASE_URL=api_url, PYTHON=sys.executable), capture_output=True, text=True, timeout=180)
                    report['smoke_exit_code'] = smoke.returncode
                    report['smoke_output'] = smoke.stdout + smoke.stderr
                    assert smoke.returncode == 0, report['smoke_output']
                    backup_dir = work / 'backups'
                    before_files = []
                    for index in range(10):
                        subprocess.run(['bash', 'scripts/backup.sh'], cwd=ROOT / 'q3_deploy',
                                       env=dict(env, BACKUP_DIR=str(backup_dir), PYTHON=sys.executable),
                                       check=True, capture_output=True, text=True)
                        if index == 6:
                            before_files = sorted(path.name for path in backup_dir.glob('*.db'))
                    after_files = sorted(path.name for path in backup_dir.glob('*.db'))
                    assert len(after_files) == 7
                    assert len(set(before_files) - set(after_files)) == 3
                    import sqlite3
                    newest = max(backup_dir.glob('*.db'), key=lambda path: path.stat().st_mtime_ns)
                    with sqlite3.connect(newest) as source, sqlite3.connect(work / 'restored.db') as destination:
                        source.backup(destination)
                        integrity = destination.execute('PRAGMA integrity_check').fetchone()[0]
                        restored_count = destination.execute('SELECT count(*) FROM cases').fetchone()[0]
                    assert integrity == 'ok' and restored_count == 86
                    report['backup'] = {'before': before_files, 'after': after_files,
                                        'created': 10, 'retained': 7, 'restore_integrity': integrity,
                                        'restored_cases': restored_count}
                    (ROOT / 'q3_deploy/backup-evidence.json').write_text(json.dumps(report['backup'], indent=2) + '\n')
                name = 'interrupted' if interrupt else 'clean'
                (EVIDENCE / f'{name}.json').write_text(json.dumps(report, indent=2) + '\n')
                print(name, final['counts'], outcomes, 'process return', returned_in, flush=True)
                return report
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            for handle in handles:
                handle.close()


if __name__ == '__main__':
    clean = phase()
    interrupted = phase(True)
    def state(report):
        return {item['case_ref']: {'status': item['status'], 'parsed_fields': item['parsed_fields']}
                for item in report['cases']}
    comparison = {'identical': state(clean) == state(interrupted), 'compared_cases': len(clean['cases']),
                  'clean': state(clean), 'interrupted': state(interrupted)}
    (EVIDENCE / 'comparison.json').write_text(json.dumps(comparison, indent=2) + '\n')
    assert comparison['identical'], 'Clean/interrupted state mismatch; inspect comparison.json'
    print('RECOVERY COMPARISON PASSED', comparison['compared_cases'], flush=True)
