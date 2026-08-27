from __future__ import annotations

import os

from neu_box_webui.config import (
    load_role_environment,
    user_config_dir,
    user_data_dir,
    user_log_dir,
)
from neu_box_webui.master.paths import (
    experiment_logs_dir,
    nodes_config_path,
    uploads_dir,
)
from neu_box_webui.master.services.db import database_path


def test_default_paths_live_under_project_runtime(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("NEU_BOX_RUNTIME_DIR", str(runtime))
    for name in (
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "NEU_BOX_DB_PATH",
        "NEU_BOX_NODES_CONFIG",
        "NEU_BOX_UPLOAD_DIR",
        "NEU_BOX_EXPERIMENT_LOG_DIR",
        "EXP_LOG_DIR",
        "db_dir",
    ):
        monkeypatch.delenv(name, raising=False)

    assert user_config_dir() == runtime / "config"
    assert user_data_dir("master") == runtime / "data" / "master"
    assert user_log_dir() == runtime / "logs"
    assert nodes_config_path() == runtime / "config" / "nodes.json"
    assert database_path() == str(runtime / "data" / "master" / "master.db")
    assert uploads_dir() == runtime / "data" / "master" / "uploads"
    assert experiment_logs_dir() == (
        runtime / "data" / "master" / "experiment-logs"
    )


def test_default_environment_file_is_project_local(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    config = runtime / "config" / "webui.env"
    config.parent.mkdir(parents=True)
    config.write_text("NEU_BOX_TEST_LOCAL_CONFIG=loaded\n", encoding="utf-8")

    monkeypatch.setenv("NEU_BOX_RUNTIME_DIR", str(runtime))
    monkeypatch.delenv("NEU_BOX_CONFIG", raising=False)
    monkeypatch.delenv("NEU_BOX_TEST_LOCAL_CONFIG", raising=False)

    assert load_role_environment("webui") == config
    assert os.environ["NEU_BOX_TEST_LOCAL_CONFIG"] == "loaded"
