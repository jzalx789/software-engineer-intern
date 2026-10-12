import csv
import io
import re
from datetime import date

COLUMNS = ('case_ref', 'applicant_name', 'id_number', 'dob', 'issue_date', 'image_ref')


def validate_csv(raw):
    try:
        reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')), strict=True)
        if reader.fieldnames != list(COLUMNS):
            raise ValueError('CSV header must be: ' + ','.join(COLUMNS))
        accepted, rejected, seen = [], [], set()
        for row_number, row in enumerate(reader, start=2):
            problems = []
            if None in row or any(value is None for value in row.values()):
                rejected.append({'row': row_number, 'reasons': ['Wrong column count']})
                continue
            row = {key: value.strip() for key, value in row.items()}
            for field in COLUMNS:
                if not row[field]:
                    problems.append(f'{field} is required')
            if row['case_ref'] in seen:
                problems.append('Duplicate case_ref: ' + row['case_ref'])
            seen.add(row['case_ref'])
            if not row['id_number'].startswith('TEST-ID-'):
                problems.append('id_number must start with TEST-ID-')
            dates = {}
            for field in ('dob', 'issue_date'):
                try:
                    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', row[field]):
                        raise ValueError()
                    dates[field] = date.fromisoformat(row[field])
                except ValueError:
                    problems.append(f'{field} must be a valid YYYY-MM-DD date')
            if len(dates) == 2 and dates['issue_date'] < dates['dob']:
                problems.append('issue_date cannot precede dob')
            if problems:
                rejected.append({'row': row_number, 'case_ref': row['case_ref'], 'reasons': problems})
            else:
                accepted.append(row)
        return accepted, rejected
    except (UnicodeError, csv.Error) as exc:
        raise ValueError(f'Invalid UTF-8 CSV: {exc}') from exc

