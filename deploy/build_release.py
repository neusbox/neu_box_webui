"""Build the Neu Box WebUI (master) release package.

产物: dist/neu_box_webui-<version>-linux-<arch>.tar.gz + .sha256

包内容:
    master/      PyInstaller 可执行目录（含 static 前端）
    config/      master.env.example, nodes.json.example
    systemd/     neu-box-webui.service
    install.sh   安装脚本（sudo ./install.sh）
    VERSION
    README.md / LICENSE
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _version() -> str:
    init = ROOT / "src" / "neu_box_webui" / "__init__.py"
    match = re.search(r'__version__\s*=\s*"([^"]+)"', init.read_text(encoding="utf-8"))
    return match.group(1) if match else ""


def _architecture() -> str:
    machine = os.uname().machine
    return {"aarch64": "arm64", "x86_64": "x64"}.get(machine, machine)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(command: list[str], env: dict | None = None, cwd: Path | None = None) -> None:
    subprocess.run(command, check=True, env=env, cwd=cwd)


def _copy_tree(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, dirs_exist_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=str(ROOT / "dist"))
    args = parser.parse_args()

    version = _version()
    if not version:
        parser.error("未能从 src/neu_box_webui/__init__.py 读取版本号")
    architecture = _architecture()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    work_root = ROOT / "build" / "release"
    pyi_dist = work_root / "pyinstaller-dist"
    pyi_work = work_root / "pyinstaller-work"
    for path in (pyi_dist, pyi_work):
        shutil.rmtree(path, ignore_errors=True)
    pyi_dist.mkdir(parents=True)

    _run([
        sys.executable,
        "-m",
        "PyInstaller",
        "--log-level=WARN",
        "--clean",
        "--noconfirm",
        "--distpath",
        str(pyi_dist),
        "--workpath",
        str(pyi_work),
        str(ROOT / "deploy" / "pyinstaller" / "webui.spec"),
    ])

    master_dir = pyi_dist / "neu-box-webui"
    if not master_dir.is_dir():
        raise SystemExit("缺少 PyInstaller 输出: neu-box-webui")

    archive_name = f"neu_box_webui-{version}-linux-{architecture}"
    with tempfile.TemporaryDirectory(prefix="neu-box-webui-release-") as raw_temp:
        staging = Path(raw_temp) / archive_name
        staging.mkdir()
        _copy_tree(master_dir, staging / "master")
        _copy_tree(ROOT / "deploy" / "config", staging / "config")
        _copy_tree(ROOT / "deploy" / "systemd", staging / "systemd")
        shutil.copy2(ROOT / "deploy" / "install.sh", staging / "install.sh")
        os.chmod(staging / "install.sh", 0o755)
        (staging / "VERSION").write_text(version + "\n", encoding="utf-8")
        shutil.copy2(ROOT / "README.md", staging / "README.md")
        shutil.copy2(ROOT / "LICENSE", staging / "LICENSE")

        tar_path = output_dir / f"{archive_name}.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(staging, arcname=archive_name)
        checksum = _sha256(tar_path)
        (output_dir / f"{tar_path.name}.sha256").write_text(
            f"{checksum}  {tar_path.name}\n", encoding="utf-8"
        )
        print(f"built {tar_path} ({tar_path.stat().st_size} bytes)")
        print(checksum)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
