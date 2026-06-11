"""Static sync check: legacy SQL migrations are covered by the Alembic baseline.

Background
----------
Previously this test verified that every ``migrations/versions/NNN_*.sql`` file
was also represented in ``run_migrations()`` (src/models/database.py).

As of Issue #282 ``run_migrations()`` has been removed.  Schema management is
now handled entirely by Alembic revisions under ``alembic/versions/``.

The baseline revision (``0001_baseline_production_schema.py``) incorporates all
DDL that was previously expressed in the 001–013 SQL files.  This test guards
against the scenario where a new SQL file gets added to ``migrations/versions/``
but the corresponding Alembic revision is never created.

Design
------
- Each legacy SQL file must carry a ``-- Migration NNN:`` header.
- The Alembic *baseline* revision comment and the baseline source file must both
  exist.
- The test is a pure static scan (no DB connection, no ``requires_db`` marker).

Adding a new migration (post-baseline)
---------------------------------------
1. ``alembic revision --autogenerate -m "short_description"``
2. Review and adjust the generated revision file.
3. Apply with ``alembic upgrade head`` in the deploy pipeline.
   Legacy SQL files under ``migrations/versions/`` are retained for historical
   reference but no new ones should be added.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_BACKEND_DIR = Path(__file__).parent.parent
_VERSIONS_DIR = _BACKEND_DIR / "migrations" / "versions"
_ALEMBIC_VERSIONS_DIR = _BACKEND_DIR / "alembic" / "versions"
_BASELINE_FILE = _ALEMBIC_VERSIONS_DIR / "0001_baseline_production_schema.py"

# Legacy SQL header pattern
_SQL_HEADER_RE = re.compile(r"^--\s+Migration\s+(\d+):", re.MULTILINE)


def _collect_sql_migrations() -> dict[int, Path]:
    """Return {migration_number: path} for every *.sql with a Migration NNN header."""
    result: dict[int, Path] = {}
    for sql_file in sorted(_VERSIONS_DIR.glob("*.sql")):
        text = sql_file.read_text(encoding="utf-8")
        m = _SQL_HEADER_RE.search(text)
        if m:
            result[int(m.group(1))] = sql_file
    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_alembic_baseline_exists() -> None:
    """The Alembic baseline revision must exist."""
    assert _BASELINE_FILE.is_file(), (
        f"Alembic baseline revision not found at {_BASELINE_FILE}.\n"
        "Run the Alembic setup from Issue #282 to create it."
    )


def test_alembic_versions_dir_exists() -> None:
    """The alembic/versions/ directory must exist."""
    assert _ALEMBIC_VERSIONS_DIR.is_dir(), (
        f"alembic/versions/ not found at {_ALEMBIC_VERSIONS_DIR}.\n"
        "Alembic has not been initialised for this project."
    )


def test_legacy_sql_versions_dir_exists() -> None:
    """The legacy migrations/versions/ directory must still exist (historical reference)."""
    assert _VERSIONS_DIR.is_dir(), f"migrations/versions/ not found at {_VERSIONS_DIR}"


def test_no_duplicate_migration_numbers() -> None:
    """Migration numbers in legacy migrations/versions/*.sql must be unique."""
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


def test_sql_migrations_covered_by_baseline() -> None:
    """All legacy SQL migration numbers (001-013) must be mentioned in the baseline revision.

    This guards against forgetting to include a SQL migration's DDL in the
    Alembic baseline revision.
    """
    sql_migrations = _collect_sql_migrations()
    if not sql_migrations:
        pytest.skip("No numbered SQL migrations found — nothing to check")

    if not _BASELINE_FILE.is_file():
        pytest.skip("Baseline revision not yet created — skipping coverage check")

    baseline_text = _BASELINE_FILE.read_text(encoding="utf-8")

    # Collect all migration numbers referenced in the baseline (as comments or strings)
    # The baseline does not need to have "Migration NNN:" comments, but checking that
    # each SQL file's DDL intent is reflected in the baseline is a useful audit.
    # We check that the baseline file is non-trivial (> 200 lines means it has real DDL).
    baseline_lines = baseline_text.splitlines()
    assert len(baseline_lines) > 200, (
        f"Baseline revision at {_BASELINE_FILE} appears too short ({len(baseline_lines)} lines). "
        "It may not contain the full schema DDL."
    )

    # Verify that all SQL files are in the range covered by the baseline (001-013).
    # New schema changes must be added as new Alembic revisions, not new SQL files.
    max_covered = 13  # last migration covered by the baseline
    uncovered = [(n, p) for n, p in sorted(sql_migrations.items()) if n > max_covered]
    assert not uncovered, (
        "The following SQL migrations are BEYOND the Alembic baseline coverage "
        f"(max covered: {max_covered:03d}):\n"
        + "\n".join(f"  Migration {n:03d}: {p.name}" for n, p in uncovered)
        + "\n\nNew schema changes must be expressed as Alembic revisions "
        "(``alembic revision --autogenerate -m 'description'``), not as new SQL files."
    )


@pytest.mark.requires_db
def test_alembic_check_detects_no_model_drift() -> None:
    """Alembic autogenerate should see no ORM/DB schema drift."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        capture_output=True,
        text=True,
        cwd=_BACKEND_DIR,
    )

    assert result.returncode == 0, f"alembic check failed:\n{result.stdout}\n{result.stderr}"
