"""Configuration loading and stable runtime path helpers."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    """Configuration cannot be loaded safely."""


def project_runtime_dir() -> Path:
    """Return the project-local runtime root used by source deployments."""
    raw_path = os.getenv("NEU_BOX_RUNTIME_DIR", "").strip()
    if raw_path:
        return Path(raw_path).expanduser().resolve()
    return (Path(__file__).resolve().parents[2] / "runtime").resolve()


def load_role_environment(
    role: str,
    explicit_path: str | os.PathLike[str] | None = None,
) -> Path | None:
    """Load one explicit role environment file without cwd discovery."""
    raw_path = explicit_path or os.getenv("NEU_BOX_CONFIG", "").strip()
    if raw_path:
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            raise ConfigError(f"配置文件不存在: {path}")
        load_dotenv(path, override=False)
        return path

    local_path = project_runtime_dir() / "config" / f"{role}.env"
    if local_path.is_file():
        load_dotenv(local_path, override=False)
        return local_path

    # Compatibility fallback for existing system-wide deployments.
    system_path = Path(f"/etc/neu-box/{role}.env")
    if system_path.is_file():
        load_dotenv(system_path, override=False)
        return system_path
    return None


def env_text(name: str, default: str = "", legacy: str | None = None) -> str:
    value = os.getenv(name)
    if value is None and legacy:
        value = os.getenv(legacy)
    if value is None:
        value = default
    return value.strip().strip('"').strip("'")


def env_int(name: str, default: int, legacy: str | None = None) -> int:
    value = env_text(name, str(default), legacy)
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} 必须是整数，实际为 {value!r}") from exc


def user_data_dir(role: str) -> Path:
    root = os.getenv("XDG_DATA_HOME", "").strip()
    if root:
        return (Path(root).expanduser() / "neu-box" / role).resolve()
    return (project_runtime_dir() / "data" / role).resolve()


def user_config_dir() -> Path:
    root = os.getenv("XDG_CONFIG_HOME", "").strip()
    if root:
        return (Path(root).expanduser() / "neu-box").resolve()
    return (project_runtime_dir() / "config").resolve()


def user_log_dir() -> Path:
    root = os.getenv("XDG_STATE_HOME", "").strip()
    if root:
        return (Path(root).expanduser() / "neu-box" / "logs").resolve()
    return (project_runtime_dir() / "logs").resolve()


def configured_path(
    name: str,
    default: Path,
    legacy: str | None = None,
) -> Path:
    value = env_text(name, legacy=legacy)
    return Path(value).expanduser().resolve() if value else default.resolve()
