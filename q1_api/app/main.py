"""Run from q1_api with uvicorn app.main:app."""
import json
import os
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

from .db import case_dict, connect, initialize
from .states import STATUSES, transition
from .validation import COLUMNS, validate_csv

MAX_UPLOAD = 5 * 1024 * 1024


def create_app(db_path=None):
    path = db_path or os.getenv('DATABASE_PATH', 'data/service.db')

    @asynccontextmanager
    async def lifespan(application):
        initialize(path)
        yield

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

    return application


class Review(BaseModel):
    stage: Literal[1, 2]
    decision: Literal['approve', 'reject']


app = create_app()
