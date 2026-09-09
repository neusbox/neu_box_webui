from __future__ import annotations

from neu_box_webui.database.migrations import migrate_database
from neu_box_webui.master.services import db as db_module
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

    monkeypatch.setattr(db_module.sqlite3, "connect", fake_connect)

    with database._conn():
        pass

    assert statements == [
        "PRAGMA busy_timeout=5000",
        "PRAGMA foreign_keys=ON",
    ]
