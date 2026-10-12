"""Small SQLite transactions; each request/worker owns its connection."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def connect(path):
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript('''
            CREATE TABLE IF NOT EXISTS batches (
                id TEXT PRIMARY KEY, accepted INTEGER NOT NULL,
                rejected INTEGER NOT NULL, errors TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS cases (
                id INTEGER PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id),
                case_ref TEXT NOT NULL, applicant_name TEXT NOT NULL,
                id_number TEXT NOT NULL, dob TEXT NOT NULL, issue_date TEXT NOT NULL,
                image_ref TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                parsed_fields TEXT, failure_reason TEXT,
                UNIQUE(batch_id, case_ref));
            CREATE INDEX IF NOT EXISTS cases_batch_status ON cases(batch_id, status);
        ''')


def case_dict(row):
    result = dict(row)
    if result['parsed_fields']:
        result['parsed_fields'] = json.loads(result['parsed_fields'])
    return result
