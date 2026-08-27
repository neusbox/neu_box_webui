"""Neu Box Master application factory and command-line entry point."""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import secrets
import sys
from datetime import timedelta

import flask
from waitress import serve as waitress_serve

from neu_box_webui import API_VERSION, __version__
from neu_box_webui.config import (
    ConfigError,
    env_int,
    env_text,
    load_role_environment,
)
from neu_box_webui.database.cli import add_database_commands, run_database_command
from neu_box_webui.database.migrations import MigrationError, schema_status
from neu_box_webui.logging_config import configure_logging
from neu_box_webui.master.paths import nodes_config_path, static_dir, uploads_dir
from neu_box_webui.master.services.db import (
    MIGRATIONS_PACKAGE,
    REQUIRED_COLUMNS,
    REQUIRED_INDEXES,
    Database,
    database_path,
)


logger = logging.getLogger("master")
DEFAULT_ADMIN_PASSWORD = "231415926@qq.com"


def create_app() -> flask.Flask:
    """Create the Flask app after configuration and schema validation."""
    nodes_path = nodes_config_path()
    if not nodes_path.is_file():
        raise ConfigError(
            f"节点配置不存在: {nodes_path}；请从 nodes.json.example 初始化"
        )
    Database.get_instance()

    app = flask.Flask(
        "neu_box_webui.master",
        static_folder=str(static_dir()),
        static_url_path="/static",
    )
    raw_secret = env_text("SECRET_KEY")
    app.secret_key = raw_secret if raw_secret else secrets.token_hex(32)
    if not raw_secret:
        logger.warning("未设置 SECRET_KEY，重启后所有用户需要重新登录")
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    # Import blueprints only after environment loading and schema validation.
    from neu_box_webui.master.api.auth import auth_bp
    from neu_box_webui.master.api.command import command_bp
    from neu_box_webui.master.api.experiment import experiment_bp
    from neu_box_webui.master.api.nodes import nodes_bp

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(command_bp, url_prefix="/tasks")
    app.register_blueprint(nodes_bp, url_prefix="/nodes")
    app.register_blueprint(experiment_bp, url_prefix="/experiments")

    @app.get("/")
    def home():
        return flask.send_from_directory(app.static_folder, "index.html")

    @app.get("/static/uploads/<path:filename>")
    def uploaded_file(filename: str):
        return flask.send_from_directory(str(uploads_dir()), filename)

    @app.get("/healthz")
    def health():
        status = schema_status(database_path(), MIGRATIONS_PACKAGE)
        return {
            "status": "ok",
            "role": "master",
            "api_version": API_VERSION,
            "version": __version__,
            "schema_version": status.current,
        }, 200

    return app


def _init_admin() -> None:
    db = Database.get_instance()
    admin_user = env_text("ADMIN_USER", "admin")
    admin_pass = env_text("ADMIN_PASS", DEFAULT_ADMIN_PASSWORD)
    if db.get_user_by_username(admin_user):
        logger.info("管理员账号已存在: %s", admin_user)
        return
    uid = db.create_user(admin_user, admin_pass, role="admin")
    if uid:
        logger.info(
            "已创建管理员: %s (%s)",
            admin_user,
            "密码来自 ADMIN_PASS" if os.getenv("ADMIN_PASS") else "使用默认密码",
        )
    else:
        logger.warning("创建管理员失败（可能已存在）")


def _reset_admin_password(username: str, password: str) -> str:
    """显式创建管理员或重置其密码；启动服务时不会自动覆盖已有密码。"""
    username = username.strip()
    if not username:
        raise ValueError("管理员用户名不能为空")
    if len(password) < 4:
        raise ValueError("管理员密码至少 4 位")

    db = Database.get_instance()
    user = db.get_user_by_username(username)
    if user:
        if not db.update_password(user["id"], password):
            raise ValueError(f"管理员密码重置失败: {username}")
        return "updated"

    uid = db.create_user(username, password, role="admin")
    if not uid:
        raise ValueError(f"管理员创建失败: {username}")
    return "created"


def _run_admin_command(args: argparse.Namespace) -> int:
    username = (args.username or env_text("ADMIN_USER", "admin")).strip()
    if args.from_config:
        password = env_text("ADMIN_PASS")
        if not password:
            raise ValueError("配置中的 ADMIN_PASS 为空")
    elif args.password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
        if not password:
            raise ValueError("stdin 中没有管理员密码")
    else:
        if not sys.stdin.isatty():
            raise ValueError(
                "非交互重置必须使用 --from-config 或 --password-stdin"
            )
        password = getpass.getpass("新管理员密码: ")
        confirmation = getpass.getpass("再次输入新密码: ")
        if password != confirmation:
            raise ValueError("两次输入的密码不一致")

    action = _reset_admin_password(username, password)
    verb = "已创建" if action == "created" else "密码已重置"
    print(f"管理员{verb}: {username}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="neu-box-webui",
        description="Neu Box WebUI 服务与数据库管理",
    )
    parser.add_argument(
        "--config",
        help=(
            "环境配置文件；默认使用 NEU_BOX_CONFIG 或 "
            "项目 runtime/config/webui.env"
        ),
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="启动 Master HTTP 服务")
    serve.add_argument("--listen", help="覆盖配置中的监听地址")
    serve.add_argument("--port", type=int, help="覆盖配置中的监听端口")
    add_database_commands(commands)
    admin = commands.add_parser("admin", help="管理员账号恢复")
    admin_commands = admin.add_subparsers(dest="admin_command", required=True)
    reset = admin_commands.add_parser(
        "reset-password",
        help="显式创建管理员或重置管理员密码",
    )
    reset.add_argument(
        "--username",
        help="管理员用户名；默认读取 ADMIN_USER",
    )
    password_source = reset.add_mutually_exclusive_group()
    password_source.add_argument(
        "--from-config",
        action="store_true",
        help="从配置文件的 ADMIN_PASS 读取密码",
    )
    password_source.add_argument(
        "--password-stdin",
        action="store_true",
        help="从 stdin 读取一行密码，避免出现在进程参数中",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        load_role_environment("webui", args.config)
        if args.command == "db":
            return run_database_command(
                args,
                role="master",
                database=database_path(),
                migrations_package=MIGRATIONS_PACKAGE,
                required_columns=REQUIRED_COLUMNS,
                required_indexes=REQUIRED_INDEXES,
            )

        if args.command == "admin":
            configure_logging("master")
            return _run_admin_command(args)

        configure_logging("master")
        app = create_app()
        _init_admin()
        from neu_box_webui.master.services.nodes_pool import Nodes_Pool

        pool = Nodes_Pool.get_nodes_pool()
        pool.start_polling(interval=env_int("NEU_BOX_POLL_INTERVAL", 15, "poll_interval"))
        listen = args.listen or env_text("NEU_BOX_LISTEN", "0.0.0.0", "listen")
        port = args.port or env_int("NEU_BOX_PORT", 25565, "port")
        threads = env_int("NEU_BOX_HTTP_THREADS", 8)
        logger.info("Master 正在监听 %s:%s", listen, port)
        try:
            waitress_serve(app, host=listen, port=port, threads=threads)
        finally:
            pool.stop_polling()
        return 0
    except (ConfigError, MigrationError, OSError, ValueError) as exc:
        print(f"neu-box-webui: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
