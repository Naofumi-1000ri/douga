"""Static sync check between migrations/versions/*.sql and run_migrations().

This test detects the class of bug described in Issue #265:
  "A new .sql file is added to migrations/versions/ but run_migrations() is
   never updated, so existing databases silently miss the schema change."

Design
------
- Each .sql file in migrations/versions/ has a canonical marker comment:
    ``-- Migration NNN:`` (e.g. ``-- Migration 010:``)
  This comment MUST appear verbatim in run_migrations() (src/models/database.py).

- The test scans both sides and asserts no .sql file is missing from
  run_migrations(), producing a clear failure message that names the missing
  migration(s).

- This is a pure static (import-free) text scan, so it runs in CI without any
  database connection and does NOT require the ``requires_db`` marker.

Adding a new migration
----------------------
1. Create ``migrations/versions/NNN_<description>.sql`` with the header line:
       ``-- Migration NNN: <one-line description>``
2. Add the corresponding DDL block to ``run_migrations()`` (src/models/database.py)
   with the SAME comment: ``# Migration NNN: <description>``
   The test verifies the *SQL file marker number* appears in run_migrations as
   either a ``-- Migration NNN:`` or ``# Migration NNN:`` comment.
"""

from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths (relative to this file's location: backend/tests/)
# ---------------------------------------------------------------------------

_BACKEND_DIR = Path(__file__).parent.parent
_VERSIONS_DIR = _BACKEND_DIR / "migrations" / "versions"
_DATABASE_PY = _BACKEND_DIR / "src" / "models" / "database.py"

# Pattern that the .sql file header MUST follow: "-- Migration NNN:"
_SQL_HEADER_RE = re.compile(r"^--\s+Migration\s+(\d+):", re.MULTILINE)

# Patterns recognised as "present" in run_migrations() for migration NNN:
#   ``-- Migration NNN:``  (SQL-style comment)
#   ``# Migration NNN:``   (Python comment)
_PY_MARKER_RE = re.compile(r"(?:--|#)\s+Migration\s+(\d+):", re.MULTILINE)


def _collect_sql_migrations() -> dict[int, Path]:
    """Return {migration_number: path} for every *.sql file that declares a
    ``-- Migration NNN:`` header.  Files without such a header are skipped
    (they are either old-style or purely informational).
    """
    result: dict[int, Path] = {}
    for sql_file in sorted(_VERSIONS_DIR.glob("*.sql")):
        text = sql_file.read_text(encoding="utf-8")
        m = _SQL_HEADER_RE.search(text)
        if m:
            result[int(m.group(1))] = sql_file
    return result


def _collect_run_migrations_numbers() -> set[int]:
    """Return the set of migration numbers mentioned in run_migrations()."""
    text = _DATABASE_PY.read_text(encoding="utf-8")
    return {int(m.group(1)) for m in _PY_MARKER_RE.finditer(text)}


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


def test_all_sql_migrations_present_in_run_migrations() -> None:
    """Every migrations/versions/*.sql with a Migration NNN header must have
    a corresponding marker in run_migrations() (src/models/database.py).

    Failure here means a .sql migration was added but the application-level
    run_migrations() function was never updated — existing databases will NOT
    receive the schema change on restart.
    """
    sql_migrations = _collect_sql_migrations()
    py_numbers = _collect_run_migrations_numbers()

    missing: list[str] = []
    for number, path in sorted(sql_migrations.items()):
        if number not in py_numbers:
            missing.append(f"  {path.name}  (Migration {number:03d})")

    assert not missing, (
        "The following SQL migrations are NOT registered in run_migrations():\n"
        + "\n".join(missing)
        + "\n\nTo fix: add a '# Migration NNN: ...' comment and the corresponding "
        "DDL block to backend/src/models/database.py:run_migrations()."
    )


def test_no_duplicate_migration_numbers() -> None:
    """Migration numbers in migrations/versions/*.sql must be unique."""
    seen: dict[int, list[Path]] = {}
    for sql_file in sorted(_VERSIONS_DIR.glob("*.sql")):
        text = sql_file.read_text(encoding="utf-8")
        m = _SQL_HEADER_RE.search(text)
        if m:
            n = int(m.group(1))
            seen.setdefault(n, []).append(sql_file)

    duplicates = {n: paths for n, paths in seen.items() if len(paths) > 1}
    assert not duplicates, (
        "Duplicate migration numbers found in migrations/versions/:\n"
        + "\n".join(
            f"  Migration {n:03d}: " + ", ".join(p.name for p in paths)
            for n, paths in sorted(duplicates.items())
        )
    )


def test_versions_dir_and_database_py_exist() -> None:
    """Guard: ensure the paths we're scanning actually exist."""
    assert _VERSIONS_DIR.is_dir(), f"migrations/versions/ not found at {_VERSIONS_DIR}"
    assert _DATABASE_PY.is_file(), f"src/models/database.py not found at {_DATABASE_PY}"
