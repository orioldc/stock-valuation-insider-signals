import os
import sqlite3
import sys

import pytest

TRACKER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (TRACKER, os.path.join(TRACKER, "data_ingestion"), os.path.join(TRACKER, "db")):
    if p not in sys.path:
        sys.path.insert(0, p)

from init_db import SCHEMA  # noqa: E402


@pytest.fixture
def db_path(tmp_path):
    """An empty database with the current schema, on disk so modules can reopen it."""
    path = str(tmp_path / "insider_signals.db")
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.close()
    return path
