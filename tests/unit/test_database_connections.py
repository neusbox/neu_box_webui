from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from neu_box_webui.database.migrations import migrate_database
from neu_box_webui.database import sqlite_runtime
from neu_box_webui.master.services.db import (
    Database,
    MIGRATIONS_PACKAGE,
    REQUIRED_COLUMNS,
    REQUIRED_INDEXES,
)


def test_request_connections_do_not_repeat_wal_mode_change(
    tmp_path,
    monkeypatch,
):
    database_path = tmp_path / "master.db"
    migrate_database(
        database_path,
        MIGRATIONS_PACKAGE,
        REQUIRED_COLUMNS,
        REQUIRED_INDEXES,
    )
    database = Database(str(database_path))

    statements = []

    class FakeConnection:
        def execute(self, statement):
            statements.append(statement)

        def close(self):
            return None

    def fake_connect(path, **kwargs):
        assert path == str(database_path)
        assert kwargs == {"timeout": 5}
        return FakeConnection()

    monkeypatch.setattr(sqlite_runtime.sqlite3, "connect", fake_connect)

    with database._conn():
        pass

    assert statements == [
        "PRAGMA busy_timeout=5000",
        "PRAGMA foreign_keys=ON",
    ]


def test_connection_open_and_close_are_serialized(monkeypatch):
    state_lock = threading.Lock()
    active_lifecycle_calls = 0
    max_active_lifecycle_calls = 0
    closed = 0

    def lifecycle_call():
        nonlocal active_lifecycle_calls, max_active_lifecycle_calls
        with state_lock:
            active_lifecycle_calls += 1
            max_active_lifecycle_calls = max(
                max_active_lifecycle_calls,
                active_lifecycle_calls,
            )
        time.sleep(0.005)
        with state_lock:
            active_lifecycle_calls -= 1

    class FakeConnection:
        def close(self):
            nonlocal closed
            lifecycle_call()
            with state_lock:
                closed += 1

    def fake_connect(*_args, **_kwargs):
        lifecycle_call()
        return FakeConnection()

    monkeypatch.setattr(sqlite_runtime.sqlite3, "connect", fake_connect)

    def use_connection():
        with sqlite_runtime.sqlite_connection("master.db"):
            time.sleep(0.001)

    with ThreadPoolExecutor(max_workers=16) as executor:
        list(executor.map(lambda _index: use_connection(), range(32)))

    assert max_active_lifecycle_calls == 1
    assert closed == 32
