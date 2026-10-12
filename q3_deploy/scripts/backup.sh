#!/usr/bin/env bash
# Local mode: DATABASE_PATH=/path/service.db bash scripts/backup.sh
# Default: stream a consistent SQLite backup out of the running api container.
set -euo pipefail
cd "$(dirname "$0")/.."
backup_dir="${BACKUP_DIR:-backups}"
mkdir -p "$backup_dir"
backup_file="$backup_dir/backup-$(date -u +%Y%m%dT%H%M%S)-$$.db"
backup_tmp="$backup_file.tmp"
trap 'rm -f "$backup_tmp"' EXIT
backup_code=$(cat <<'PY'
import os, sqlite3, sys, tempfile
from pathlib import Path
source = sqlite3.connect('file:' + os.environ['DATABASE_PATH'] + '?mode=ro', uri=True)
with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / 'backup.db'
    destination = sqlite3.connect(path)
    source.backup(destination)
    destination.execute('PRAGMA journal_mode=DELETE')  # Standalone snapshot, no WAL sidecars.
    assert destination.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    destination.close()
    source.close()
    sys.stdout.buffer.write(path.read_bytes())
PY
)
if [[ -n "${DATABASE_PATH:-}" ]]; then
    "${PYTHON:-python3}" -c "$backup_code" > "$backup_tmp"
else
    docker compose exec -T api python -c "$backup_code" > "$backup_tmp"
fi
mv "$backup_tmp" "$backup_file"
"${PYTHON:-python3}" - "$backup_dir" <<'PY'
import sys
from pathlib import Path
files = sorted(Path(sys.argv[1]).glob('backup-*.db'), key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
for path in files[7:]:
    path.unlink()
for path in sorted(files[:7]):
    print(path)
PY
