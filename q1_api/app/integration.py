"""One asyncio worker per batch/export in one API process.

Cases stay pending until a terminal OCR result is committed. A killed process
therefore leaves unfinished cases eligible for another /process call. Export
bytes and delivery keys are committed before any external submission.
"""
import asyncio
import csv
import hashlib
import io
import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import date

import httpx
from pydantic import BaseModel, Field, ValidationError

from .db import connect
from .validation import COLUMNS

log = logging.getLogger('uvicorn.error')


@dataclass(frozen=True)
class Settings:
    ocr_url: str
    onboarding_url: str
    timeout: float
    attempts: int
    backoff: float

    @classmethod
    def from_env(cls):
        result = cls(
            os.getenv('OCR_URL', 'http://localhost:9100/ocr/parse'),
            os.getenv('ONBOARDING_URL', 'http://localhost:9200/ingest'),
            float(os.getenv('REQUEST_TIMEOUT', '2')),
            int(os.getenv('MAX_ATTEMPTS', '5')),
            float(os.getenv('RETRY_BACKOFF', '0.2')),
        )
        if result.timeout <= 0 or not 1 <= result.attempts <= 10 or result.backoff < 0:
            raise ValueError('Timeout must be positive, attempts 1..10, backoff non-negative')
        return result


class ParsedFields(BaseModel):
    id_number: str = Field(pattern=r'^TEST-ID-')
    full_name: str = Field(min_length=1)
    dob: date
    issue_date: date
    confidence: float = Field(ge=0, le=1)


def initialize_exports(path):
    with connect(path) as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS exports (
                id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id),
                filename TEXT NOT NULL, body BLOB NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
                case_count INTEGER NOT NULL, delivery_status TEXT NOT NULL DEFAULT 'pending',
                receipt TEXT, last_error TEXT, attempts INTEGER NOT NULL DEFAULT 0);
        ''')


class Integration:
    def __init__(self, path, settings=None, transport=None):
        self.path = path
        self.settings = settings or Settings.from_env()
        self.transport = transport
        self.tasks = {}

    def active(self, key):
        return key in self.tasks and not self.tasks[key].done()

    def schedule(self, key, work):
        if self.active(key):
            return False
        task = asyncio.create_task(work())
        self.tasks[key] = task
        task.add_done_callback(lambda finished: self._finished(key, finished))
        return True

    def _finished(self, key, task):
        if self.tasks.get(key) is task:
            self.tasks.pop(key, None)
        if not task.cancelled() and task.exception():
            log.error('Background job %s failed: %s; restart using process/deliver endpoint', key, task.exception())

    async def close(self):
        pending = list(self.tasks.values())
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    def client(self):
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self.settings.timeout, connect=min(1, self.settings.timeout)),
            trust_env=False, transport=self.transport,
        )

    async def request(self, client, url, **kwargs):
        reason = 'Unknown error'
        for attempt in range(1, self.settings.attempts + 1):
            try:
                response = await client.post(url, **kwargs)
                if response.status_code == 200:
                    return response.json()
                reason = f'HTTP {response.status_code}: {response.text[:500]}'
                if response.status_code < 500 and response.status_code != 429:
                    raise ValueError(reason)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                reason = f'{type(exc).__name__}: {str(exc) or "response not received"}'
            log.warning('Upstream attempt %d/%d: %s', attempt, self.settings.attempts, reason)
            if attempt < self.settings.attempts:
                await asyncio.sleep(self.settings.backoff * 2 ** (attempt - 1))
        raise ValueError(f'Retries exhausted after {self.settings.attempts} attempts: {reason}')

    async def process(self, batch_id):
        with connect(self.path) as db:
            rows = db.execute("SELECT * FROM cases WHERE batch_id=? AND status='pending' ORDER BY id", (batch_id,)).fetchall()
        async with self.client() as client:
            for row in rows:
                try:
                    result = await self.request(client, self.settings.ocr_url,
                                                json={'case_id': row['case_ref'], 'image_ref': row['image_ref']})
                    parsed = ParsedFields.model_validate(result).model_dump(mode='json')
                    status, fields, reason = 'ocr_done', json.dumps(parsed, sort_keys=True), None
                except (ValueError, ValidationError) as exc:
                    status, fields, reason = 'ocr_failed', None, str(exc)
                # Conditional update protects an already-completed/reviewed case.
                with connect(self.path) as db:
                    db.execute("UPDATE cases SET status=?,parsed_fields=?,failure_reason=? WHERE id=? AND status='pending'",
                               (status, fields, reason, row['id']))
                log.info('OCR case %s -> %s', row['case_ref'], status)

    def create_export(self, batch_id):
        with connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute("SELECT * FROM cases WHERE batch_id=? AND status='stage2_approved' ORDER BY id", (batch_id,)).fetchall()
            if not rows:
                return None
            export_id = str(uuid.uuid4())
            filename = f'EXPORT-{export_id}.csv'
            stream = io.StringIO(newline='')
            writer = csv.DictWriter(stream, fieldnames=[*COLUMNS, 'ocr_id_number', 'ocr_full_name', 'ocr_dob', 'ocr_issue_date', 'ocr_confidence'])
            writer.writeheader()
            for row in rows:
                record = {column: row[column] for column in COLUMNS}
                parsed = json.loads(row['parsed_fields'] or '{}')
                record.update({'ocr_' + key: parsed.get(key, '') for key in ('id_number', 'full_name', 'dob', 'issue_date', 'confidence')})
                writer.writerow(record)
            body = stream.getvalue().encode('utf-8')
            key = hashlib.sha256(filename.encode() + b'\0' + body).hexdigest()
            db.execute('INSERT INTO exports (id,batch_id,filename,body,idempotency_key,case_count) VALUES (?,?,?,?,?,?)',
                       (export_id, batch_id, filename, body, key, len(rows)))
            db.executemany("UPDATE cases SET status='exported' WHERE id=?", [(row['id'],) for row in rows])
        return export_id

    async def deliver(self, export_id):
        with connect(self.path) as db:
            row = db.execute('SELECT * FROM exports WHERE id=?', (export_id,)).fetchone()
        if row is None or row['delivery_status'] == 'delivered':
            return
        try:
            async with self.client() as client:
                receipt = await self.request(client, self.settings.onboarding_url,
                                             content=row['body'], headers={
                                                 'Content-Type': 'text/csv',
                                                 'Idempotency-Key': row['idempotency_key'],
                                                 'X-File-Name': row['filename'],
                                             })
            if (receipt.get('idempotency_key') != row['idempotency_key'] or
                    receipt.get('sha256') != hashlib.sha256(row['body']).hexdigest() or
                    not receipt.get('delivery_id')):
                raise ValueError('Receipt key/hash/delivery_id does not match export')
            with connect(self.path) as db:
                db.execute("UPDATE exports SET delivery_status='delivered',receipt=?,last_error=NULL,attempts=attempts+1 WHERE id=?",
                           (json.dumps(receipt), export_id))
            log.info('Export %s delivered', export_id)
        except ValueError as exc:
            with connect(self.path) as db:
                db.execute("UPDATE exports SET delivery_status='failed',last_error=?,attempts=attempts+1 WHERE id=?", (str(exc), export_id))
            log.warning('Export %s requires delivery retry: %s', export_id, exc)

    def exports(self, batch_id):
        with connect(self.path) as db:
            rows = db.execute('SELECT id,batch_id,filename,idempotency_key,case_count,delivery_status,receipt,last_error,attempts FROM exports WHERE batch_id=? ORDER BY rowid', (batch_id,)).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item['receipt'] = json.loads(item['receipt']) if item['receipt'] else None
            item['delivery_runs'] = item.pop('attempts')
            items.append(item)
        return items
