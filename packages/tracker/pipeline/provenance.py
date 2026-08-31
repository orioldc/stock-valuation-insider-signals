"""
Pipeline provenance tracking — record what was refreshed, when, and how completely.

This module provides a simple API for pipeline jobs to record their execution.
Provenance is stored inside the SQLite artifact so it travels with the data.

Usage:
    from pipeline.provenance import record_run, get_provenance

    with record_run(db_path, 'prices') as run:
        rows = fetch_and_write_prices()
        run.rows_written = rows
        run.coverage(companies_covered, total_companies)
        run.permanent_failures = failed_count

    # Read back
    info = get_provenance(db_path, 'prices')
    print(f"Last success: {info['last_success_at']}, age: {info['age_days']} days")
"""

import sqlite3
import json
import logging
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Optional, Dict, List, Any

logger = logging.getLogger(__name__)


# Schema version — increment when making breaking changes
PROVENANCE_VERSION = "1.0"


def _ensure_tables(conn: sqlite3.Connection) -> None:
    """Create provenance tables if they don't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS data_sources (
            source              TEXT PRIMARY KEY,
            last_attempt_at     TEXT,
            last_success_at     TEXT,
            status              TEXT,
            rows_written        INTEGER,
            coverage_num        INTEGER,
            coverage_den        INTEGER,
            permanent_failures  INTEGER,
            detail              TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS pipeline_meta (
            key     TEXT PRIMARY KEY,
            value   TEXT
        )
    """)

    # Record schema version on first creation
    conn.execute("""
        INSERT OR IGNORE INTO pipeline_meta (key, value)
        VALUES ('provenance_version', ?)
    """, (PROVENANCE_VERSION,))

    conn.commit()


class RunRecorder:
    """
    Records a pipeline run's outcome.

    Set attributes before exiting the context:
        run.rows_written = 1234
        run.coverage(500, 600)
        run.permanent_failures = 10
        run.detail = {"batch_id": "xyz"}

    On normal exit, status is 'ok' (or 'partial' if coverage_num < coverage_den).
    On exception, status is 'failed' and the exception is re-raised.
    """

    def __init__(self, conn: sqlite3.Connection, source: str):
        self.conn = conn
        self.source = source
        self.rows_written: Optional[int] = None
        self.coverage_num: Optional[int] = None
        self.coverage_den: Optional[int] = None
        self.permanent_failures: Optional[int] = None
        self.detail: Optional[Dict[str, Any]] = None
        self._failed = False

    def coverage(self, num: int, den: int) -> None:
        """Set coverage fraction (e.g., 480 companies covered out of 500 total)."""
        self.coverage_num = num
        self.coverage_den = den

    def _record_attempt(self) -> None:
        """Mark that the run started."""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute("""
            INSERT INTO data_sources (source, last_attempt_at, status)
            VALUES (?, ?, 'running')
            ON CONFLICT(source) DO UPDATE SET
                last_attempt_at = excluded.last_attempt_at,
                status = 'running'
        """, (self.source, now))
        self.conn.commit()

    def _record_outcome(self) -> None:
        """Record the final outcome."""
        now = datetime.now(timezone.utc).isoformat()

        if self._failed:
            status = 'failed'
            success_at = None
        else:
            # Determine status: ok if full coverage or no coverage info, partial otherwise
            if self.coverage_num is not None and self.coverage_den is not None:
                status = 'ok' if self.coverage_num >= self.coverage_den else 'partial'
            else:
                status = 'ok'
            success_at = now

        detail_json = json.dumps(self.detail) if self.detail else None

        self.conn.execute("""
            UPDATE data_sources
            SET last_attempt_at = ?,
                last_success_at = COALESCE(?, last_success_at),
                status = ?,
                rows_written = ?,
                coverage_num = ?,
                coverage_den = ?,
                permanent_failures = ?,
                detail = ?
            WHERE source = ?
        """, (
            now,
            success_at,
            status,
            self.rows_written,
            self.coverage_num,
            self.coverage_den,
            self.permanent_failures,
            detail_json,
            self.source
        ))
        self.conn.commit()


@contextmanager
def record_run(db_path: str, source: str):
    """
    Context manager for recording a pipeline run.

    Args:
        db_path: Path to SQLite database
        source: Source identifier (e.g., 'prices', 'insider_transactions')

    Yields:
        RunRecorder instance to record run details

    Example:
        with record_run(DB_PATH, 'prices') as run:
            rows = fetch_prices()
            run.rows_written = rows
            run.coverage(480, 500)
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA busy_timeout = 60000")

    try:
        _ensure_tables(conn)
        recorder = RunRecorder(conn, source)
        recorder._record_attempt()

        try:
            yield recorder
        except Exception as e:
            recorder._failed = True
            recorder._record_outcome()
            raise
        else:
            recorder._record_outcome()

    finally:
        conn.close()


def get_provenance(db_path: str, source: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Read provenance for one or all sources.

    Args:
        db_path: Path to SQLite database
        source: Optional source identifier; if None, returns all sources

    Returns:
        Dict with provenance info (single source) or list of dicts (all sources).
        Each dict includes computed 'age_days' field.
        Returns None if source not found or tables don't exist yet.

    Example:
        info = get_provenance(DB_PATH, 'prices')
        if info:
            print(f"Last success: {info['last_success_at']}")
            print(f"Age: {info['age_days']} days")
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        # Check if tables exist
        cursor = conn.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='data_sources'
        """)
        if not cursor.fetchone():
            return None

        if source is not None:
            cursor = conn.execute("""
                SELECT * FROM data_sources WHERE source = ?
            """, (source,))
            row = cursor.fetchone()
            if not row:
                return None
            return _enrich_row(dict(row))
        else:
            cursor = conn.execute("SELECT * FROM data_sources ORDER BY source")
            rows = [_enrich_row(dict(row)) for row in cursor.fetchall()]
            return rows

    finally:
        conn.close()


def get_pipeline_meta(db_path: str) -> Dict[str, str]:
    """
    Read pipeline metadata (version, etc.).

    Returns:
        Dict mapping key to value
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        # Check if table exists
        cursor = conn.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='pipeline_meta'
        """)
        if not cursor.fetchone():
            return {}

        cursor = conn.execute("SELECT key, value FROM pipeline_meta")
        return {row['key']: row['value'] for row in cursor.fetchall()}

    finally:
        conn.close()


def set_pipeline_meta(db_path: str, key: str, value: str) -> None:
    """
    Set a pipeline metadata value.

    Args:
        db_path: Path to SQLite database
        key: Metadata key
        value: Metadata value
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA busy_timeout = 60000")

    try:
        _ensure_tables(conn)
        conn.execute("""
            INSERT INTO pipeline_meta (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (key, value))
        conn.commit()

    finally:
        conn.close()


def _enrich_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Add computed fields to a provenance row."""
    # Compute age in days from last_success_at
    if row.get('last_success_at'):
        try:
            last_success = datetime.fromisoformat(row['last_success_at'])
            now = datetime.now(timezone.utc)
            # Handle timezone-naive datetimes from old data
            if last_success.tzinfo is None:
                last_success = last_success.replace(tzinfo=timezone.utc)
            age_delta = now - last_success
            row['age_days'] = age_delta.days
        except (ValueError, TypeError):
            row['age_days'] = None
    else:
        row['age_days'] = None

    # Parse detail JSON if present
    if row.get('detail'):
        try:
            row['detail'] = json.loads(row['detail'])
        except json.JSONDecodeError:
            pass

    return row
