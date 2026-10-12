"""Run from q1_api with uvicorn app.main:app."""
import json
import os
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Query, UploadFile, Response
from pydantic import BaseModel

from .db import case_dict, connect, initialize
from .states import STATUSES, transition
from .integration import Integration, initialize_exports
from .validation import COLUMNS, validate_csv

MAX_UPLOAD = 5 * 1024 * 1024


def create_app(db_path=None, integration_settings=None, transport=None):
    path = db_path or os.getenv('DATABASE_PATH', 'data/service.db')

    @asynccontextmanager
    async def lifespan(application):
        initialize(path)
        initialize_exports(path)
        application.state.integration = Integration(path, integration_settings, transport)
        try:
            yield
        finally:
            await application.state.integration.close()

    application = FastAPI(title='Identity Verification', lifespan=lifespan)
    application.state.db_path = path

    @application.get('/api/health')
    def health():
        return {'status': 'ok'}

    @application.get('/api/ready')
    def ready():
        try:
            with connect(path) as db:
                db.execute('SELECT count(*) FROM batches').fetchone()
        except Exception as exc:
            raise HTTPException(503, 'Database unavailable') from exc
        return {'ready': True}

    @application.post('/api/batches', status_code=201)
    async def import_batch(file: UploadFile = File(...)):
        raw = await file.read(MAX_UPLOAD + 1)
        if len(raw) > MAX_UPLOAD:
            raise HTTPException(413, 'CSV upload exceeds 5 MiB')
        try:
            rows, errors = validate_csv(raw)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        batch_id = str(uuid.uuid4())
        with connect(path) as db:
            db.execute('INSERT INTO batches VALUES (?, ?, ?, ?)',
                       (batch_id, len(rows), len(errors), json.dumps(errors)))
            db.executemany('INSERT INTO cases (batch_id,' + ','.join(COLUMNS) +
                           ') VALUES (?,?,?,?,?,?,?)',
                           [(batch_id, *(row[key] for key in COLUMNS)) for row in rows])
        return get_batch(batch_id)

    @application.get('/api/batches/{batch_id}')
    def get_batch(batch_id: str):
        with connect(path) as db:
            row = db.execute('SELECT * FROM batches WHERE id=?', (batch_id,)).fetchone()
            if row is None:
                raise HTTPException(404, 'Batch not found')
            result = dict(row)
            result['errors'] = json.loads(result['errors'])
            counts = dict.fromkeys(STATUSES, 0)
            for count in db.execute('SELECT status,count(*) AS n FROM cases WHERE batch_id=? GROUP BY status', (batch_id,)):
                counts[count['status']] = count['n']
            result['counts'] = counts
            result['processing'] = application.state.integration.active('ocr:' + batch_id)
            result['exports'] = application.state.integration.exports(batch_id)
            return result

    @application.get('/api/batches/{batch_id}/cases')
    def list_cases(batch_id: str, status: str | None = None,
                   page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100)):
        get_batch(batch_id)
        if status is not None and status not in STATUSES:
            raise HTTPException(422, 'Unknown status')
        where = 'batch_id=?' + (' AND status=?' if status else '')
        params = [batch_id] + ([status] if status else [])
        with connect(path) as db:
            total = db.execute('SELECT count(*) FROM cases WHERE ' + where, params).fetchone()[0]
            rows = db.execute('SELECT * FROM cases WHERE ' + where + ' ORDER BY id LIMIT ? OFFSET ?',
                              [*params, page_size, (page - 1) * page_size]).fetchall()
        return {'items': [case_dict(row) for row in rows], 'total': total, 'page': page, 'page_size': page_size}

    @application.post('/api/cases/{case_id}/review')
    def review(case_id: int, request: Review):
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM cases WHERE id=?', (case_id,)).fetchone()
            if row is None:
                raise HTTPException(404, 'Case not found')
            try:
                target = transition(row['status'], request.stage, request.decision)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            db.execute('UPDATE cases SET status=? WHERE id=?', (target, case_id))
            result = case_dict(row)
            result['status'] = target
        return result

    @application.post('/api/batches/{batch_id}/process', status_code=202)
    async def process_batch(batch_id: str, retry_failed: bool = False):
        get_batch(batch_id)
        service = application.state.integration
        if retry_failed:
            if service.active('ocr:' + batch_id):
                raise HTTPException(409, 'Batch is already processing')
            with connect(path) as db:
                db.execute("UPDATE cases SET status='pending',failure_reason=NULL WHERE batch_id=? AND status='ocr_failed'", (batch_id,))
        service.schedule('ocr:' + batch_id, lambda: service.process(batch_id))
        return get_batch(batch_id)

    @application.post('/api/batches/{batch_id}/export', status_code=202)
    async def export_batch(batch_id: str):
        get_batch(batch_id)
        service = application.state.integration
        export_id = service.create_export(batch_id)
        # Also resume committed exports whose response was lost or worker died.
        for item in service.exports(batch_id):
            if item['delivery_status'] != 'delivered':
                service.schedule('delivery:' + item['id'], lambda identifier=item['id']: service.deliver(identifier))
        return {'created_export_id': export_id, 'exports': service.exports(batch_id)}

    @application.get('/api/exports/{export_id}')
    def get_export(export_id: str):
        with connect(path) as db:
            row = db.execute('SELECT batch_id FROM exports WHERE id=?', (export_id,)).fetchone()
        if row is None:
            raise HTTPException(404, 'Export not found')
        return next(item for item in application.state.integration.exports(row['batch_id']) if item['id'] == export_id)

    @application.get('/api/exports/{export_id}/download')
    def download_export(export_id: str):
        get_export(export_id)
        with connect(path) as db:
            row = db.execute('SELECT body,filename FROM exports WHERE id=?', (export_id,)).fetchone()
        return Response(bytes(row['body']), media_type='text/csv',
                        headers={'Content-Disposition': f'attachment; filename="{row["filename"]}"'})

    @application.post('/api/exports/{export_id}/deliver', status_code=202)
    async def retry_delivery(export_id: str):
        item = get_export(export_id)
        if item['delivery_status'] != 'delivered':
            service = application.state.integration
            service.schedule('delivery:' + export_id, lambda: service.deliver(export_id))
        return get_export(export_id)

    return application


class Review(BaseModel):
    stage: Literal[1, 2]
    decision: Literal['approve', 'reject']


app = create_app()
