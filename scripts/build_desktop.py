#!/usr/bin/env python3
"""Desktop build orchestrator for Momentum Swing Terminal.

Derives application version from package.json and bundle product name from
src-tauri/tauri.conf.json so release .app and .dmg artifacts never rely on
hardcoded version strings.
"""

import argparse
import json
import platform
import subprocess
import sys
from pathlib import Path
from typing import Tuple


def get_dmg_arch(machine: str = "") -> str:
    """Maps host machine architecture to the Tauri macOS DMG suffix."""
    raw = (machine or platform.machine()).strip().lower()
    if raw in ("arm64", "aarch64"):
        return "aarch64"
    if raw in ("x86_64", "amd64"):
        return "x86_64"
    return raw


def derive_desktop_paths(project_root: Path) -> Tuple[str, str, Path, Path]:
    """Computes version, product name, .app bundle path, and .dmg path."""
    pkg = json.loads((project_root / "package.json").read_text(encoding="utf-8"))
    tauri_conf = json.loads(
        (project_root / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")
    )
    version = str(pkg["version"]).strip()
    product_name = str(tauri_conf["productName"]).strip()
    arch = get_dmg_arch()
    bundle_root = project_root / "src-tauri" / "target" / "release" / "bundle"
    app_path = bundle_root / "macos" / f"{product_name}.app"
    dmg_path = bundle_root / "dmg" / f"{product_name}_{version}_{arch}.dmg"
    return version, product_name, app_path, dmg_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Momentum Swing Terminal Desktop App & DMG")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print derived version and artifact paths without invoking builds",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    version, product_name, app_path, dmg_path = derive_desktop_paths(project_root)

    if args.dry_run:
        print(
            json.dumps(
                {
                    "version": version,
                    "product_name": product_name,
                    "app_path": str(app_path),
                    "dmg_path": str(dmg_path),
                }
            )
        )
        return 0

    subprocess.run(
        [
            sys.executable,
            str(project_root / "scripts" / "build_sidecar.py"),
            "--tauri-bin-dir",
            str(project_root / "src-tauri" / "bin"),
        ],
        cwd=str(project_root),
        check=True,
    )
    subprocess.run(["npx", "tauri", "build", "--bundles", "app"], cwd=str(project_root), check=True)
    subprocess.run(
        [
            sys.executable,
            str(project_root / "scripts" / "build_sidecar.py"),
            "--sign-app",
            str(app_path),
            "--package-dmg",
            str(dmg_path),
        ],
        cwd=str(project_root),
        check=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
