"""Process-wide SQLite connection lifecycle helpers.

SQLite 3.51.1 can deadlock when one thread opens a WAL database while another
thread closes a connection to it.  Serializing only connection creation and
destruction avoids that runtime bug without serializing database queries.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from os import PathLike
from typing import Any, Iterator


_connection_lifecycle_lock = threading.Lock()


def open_connection(
    database: str | bytes | PathLike[str] | PathLike[bytes],
    **kwargs: Any,
) -> sqlite3.Connection:
    """Open a connection without racing another thread's close operation."""
    with _connection_lifecycle_lock:
        return sqlite3.connect(database, **kwargs)


def close_connection(conn: sqlite3.Connection) -> None:
    """Close a connection without racing another thread's open operation."""
    with _connection_lifecycle_lock:
        conn.close()


@contextmanager
def sqlite_connection(
    database: str | bytes | PathLike[str] | PathLike[bytes],
    **kwargs: Any,
) -> Iterator[sqlite3.Connection]:
    """Yield a SQLite connection and always close it through the guard."""
    conn = open_connection(database, **kwargs)
    try:
        yield conn
    finally:
        close_connection(conn)
