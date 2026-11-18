#!/usr/bin/env python3
"""Build script for packaging TradingView Real-Time Terminal Python backend sidecar.

Packages server.py and its dependencies (engine, FastAPI, Uvicorn, websockets, httpx, certifi)
into a standalone macOS binary bundle using PyInstaller with --onedir mode, ensuring low
startup latency, zero-runtime-decompression, and full Apple Hardened Runtime notarization
compatibility.
"""

import argparse
import logging
import os
import platform
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("build_sidecar")

REQUIRED_DEPENDENCIES = [
    "certifi",
    "fastapi",
    "uvicorn",
    "websockets",
    "httpx",
]

REQUIRED_HIDDEN_IMPORTS = [
    "uvicorn.logging",
    "uvicorn.loops.asyncio",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "websockets",
    "websockets.legacy",
    "websockets.legacy.server",
    "fastapi",
    "fastapi.staticfiles",
    "starlette.middleware.base",
    "starlette.middleware.cors",
    "engine.protocol",
    "engine.client",
    "engine.aggregator",
    "engine.storage",
    "engine.providers",
    "engine.screener",
    "engine.pine_engine",
    "sqlite3",
    "certifi",
    "httpx",
    "httpcore",
]


def get_target_triple(arch: Optional[str] = None, system: Optional[str] = None) -> str:
    """Computes the target architecture triple following Tauri and Rust platform conventions."""
    sys_name = (system or platform.system()).lower()
    machine = (arch or platform.machine()).lower()

    if sys_name == "darwin":
        if machine in ("arm64", "aarch64"):
            return "aarch64-apple-darwin"
        elif machine in ("x86_64", "amd64", "i386", "x86"):
            return "x86_64-apple-darwin"
        return f"{machine}-apple-darwin"
    elif sys_name == "linux":
        if machine in ("arm64", "aarch64"):
            return "aarch64-unknown-linux-gnu"
        return "x86_64-unknown-linux-gnu"
    elif sys_name == "windows":
        return "x86_64-pc-windows-msvc"
    return f"{machine}-unknown-{sys_name}"


def get_hidden_imports() -> List[str]:
    """Returns the comprehensive list of required hidden imports for PyInstaller packaging."""
    return list(REQUIRED_HIDDEN_IMPORTS)


def get_bundle_datas(project_root: Optional[Path] = None) -> List[Tuple[str, str]]:
    """Gathers all necessary data files including certifi TLS roots and static UI assets."""
    root = (project_root or Path.cwd()).resolve()
    datas: List[Tuple[str, str]] = []

    # Certifi TLS root certificate collection
    try:
        import certifi
        ca_path = certifi.where()
        if os.path.exists(ca_path):
            datas.append((ca_path, "certifi"))
    except ImportError:
        logger.warning("certifi is not installed in the current Python environment.")

    try:
        from PyInstaller.utils.hooks import collect_data_files
        for src, dst in collect_data_files("certifi"):
            if (src, dst) not in datas:
                datas.append((src, dst))
    except Exception as exc:
        logger.debug("PyInstaller hook collect_data_files notice: %s", exc)

    # Static assets directory
    static_dir = root / "static"
    if static_dir.exists():
        datas.append((str(static_dir), "static"))

    return datas


def validate_build_environment() -> Tuple[bool, List[str]]:
    """Checks that all essential libraries and PyInstaller are present in the environment."""
    missing: List[str] = []
    for dep in REQUIRED_DEPENDENCIES:
        try:
            __import__(dep)
        except ImportError:
            missing.append(dep)

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        missing.append("pyinstaller")

    return (len(missing) == 0, missing)


def is_macho_binary(file_path: Path, resolve_symlinks: bool = False) -> bool:
    """Returns True if the file exists, is regular, and has a Mach-O magic header."""
    target = file_path.resolve() if resolve_symlinks else file_path
    if not resolve_symlinks and file_path.is_symlink():
        return False
    if not target.is_file():
        return False
    try:
        with open(target, "rb") as f:
            magic = f.read(4)
        return magic in (
            b"\xfe\xed\xfa\xce",  # Mach-O 32-bit
            b"\xfe\xed\xfa\xcf",  # Mach-O 64-bit
            b"\xce\xfa\xed\xfe",  # Mach-O 32-bit little-endian
            b"\xcf\xfa\xed\xfe",  # Mach-O 64-bit little-endian
            b"\xca\xfe\xba\xbe",  # Mach-O universal/fat binary
            b"\xbe\xba\xfe\xca",
        )
    except Exception:
        return False


def repair_framework_symlinks(bundle_dir: Path) -> int:
    """Repairs standard macOS Framework symlink structure if broken during packaging or copying."""
    if not bundle_dir.exists():
        return 0
    repaired = 0
    for fw in bundle_dir.rglob("*.framework"):
        if not fw.is_dir():
            continue
        versions_dir = fw / "Versions"
        if not versions_dir.is_dir():
            continue
        version_dirs = [
            d for d in versions_dir.iterdir()
            if d.is_dir() and not d.is_symlink() and d.name != "Current"
        ]
        if not version_dirs:
            continue
        target_version = sorted(version_dirs, key=lambda d: d.name)[-1]
        current_link = versions_dir / "Current"

        if current_link.exists() and not current_link.is_symlink():
            shutil.rmtree(current_link)
            current_link.symlink_to(target_version.name)
            repaired += 1
        elif not current_link.exists():
            current_link.symlink_to(target_version.name)
            repaired += 1

        fw_name = fw.stem
        top_bin = fw / fw_name
        if top_bin.exists() and not top_bin.is_symlink():
            if top_bin.is_dir():
                shutil.rmtree(top_bin)
            else:
                top_bin.unlink()
            top_bin.symlink_to(f"Versions/Current/{fw_name}")
            repaired += 1
        elif not top_bin.exists() and (target_version / fw_name).exists():
            top_bin.symlink_to(f"Versions/Current/{fw_name}")
            repaired += 1

        top_res = fw / "Resources"
        if top_res.exists() and not top_res.is_symlink():
            shutil.rmtree(top_res)
            top_res.symlink_to("Versions/Current/Resources")
            repaired += 1
        elif not top_res.exists() and (target_version / "Resources").exists():
            top_res.symlink_to("Versions/Current/Resources")
            repaired += 1

    return repaired


def codesign_bundle(
    bundle_dir: Path,
    identity: str = "-",
    entitlements_path: Optional[Path] = None,
) -> bool:
    """Recursively signs dynamic libraries, Mach-O binaries, and the main bundle with Hardened Runtime."""
    if not bundle_dir.exists():
        logger.error("Cannot sign nonexistent bundle directory: %s", bundle_dir)
        return False

    if platform.system() != "Darwin":
        logger.info("Codesigning is only applicable on macOS; skipping.")
        return True

    repair_framework_symlinks(bundle_dir)
    logger.info("Codesigning bundle %s with identity: %s", bundle_dir, identity)

    def is_inside_framework(path: Path) -> bool:
        return any(parent.suffix.lower() == ".framework" for parent in path.parents)

    main_executables: List[Path] = []
    if bundle_dir.suffix.lower() == ".app":
        macos_dir = bundle_dir / "Contents" / "MacOS"
        if macos_dir.exists():
            for f in macos_dir.iterdir():
                if f.is_file() and is_macho_binary(f):
                    main_executables.append(f)
        for s in bundle_dir.rglob("server"):
            if s.is_file() and is_macho_binary(s) and not any(s.resolve() == m.resolve() for m in main_executables):
                main_executables.append(s)
    else:
        for s in bundle_dir.rglob("server"):
            if s.is_file() and is_macho_binary(s):
                main_executables.append(s)

    # 1. Sign shared libraries (.dylib, .so) and Mach-O binaries inside bundle first
    candidate_files: List[Path] = []
    for p in bundle_dir.rglob("*"):
        if is_macho_binary(p) and not is_inside_framework(p):
            if not any(p.resolve() == m.resolve() for m in main_executables):
                candidate_files.append(p)

    signed_count = 0
    for lib in candidate_files:
        try:
            mode = lib.stat().st_mode
            if not (mode & stat.S_IWUSR):
                lib.chmod(mode | stat.S_IWUSR)
        except Exception as perm_err:
            logger.debug("Could not modify permissions for %s: %s", lib, perm_err)

        cmd = [
            "codesign",
            "--force",
            "--options", "runtime",
            "--sign", identity,
            str(lib),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            logger.error("Failed to sign component %s: %s", lib.name, res.stderr.strip())
            return False
        signed_count += 1

    # 2. Sign embedded frameworks (e.g. Python3.framework)
    for fw in bundle_dir.rglob("*.framework"):
        if fw.is_dir():
            target_fw = fw / "Versions" / "Current" if (fw / "Versions" / "Current").exists() else fw
            cmd = [
                "codesign",
                "--force",
                "--deep",
                "--options", "runtime",
                "--sign", identity,
                str(target_fw),
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode != 0:
                logger.error("Failed to sign framework %s: %s", fw.name, res.stderr.strip())
                return False
            signed_count += 1

    # 3. Sign main executables with Hardened Runtime and entitlements
    for main_exe in main_executables:
        try:
            mode = main_exe.stat().st_mode
            if not (mode & stat.S_IWUSR):
                main_exe.chmod(mode | stat.S_IWUSR)
        except Exception as perm_err:
            logger.debug("Could not modify permissions for main executable %s: %s", main_exe, perm_err)

        cmd = [
            "codesign",
            "--force",
            "--options", "runtime",
        ]
        if entitlements_path and entitlements_path.exists():
            cmd.extend(["--entitlements", str(entitlements_path)])
        cmd.extend(["--sign", identity, str(main_exe)])

        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            logger.error("Failed to sign executable %s: %s", main_exe, res.stderr.strip())
            return False
        signed_count += 1

        # Verify signature on main executable
        verify_cmd = ["codesign", "--verify", "--verbose=2", str(main_exe)]
        v_res = subprocess.run(verify_cmd, capture_output=True, text=True)
        if v_res.returncode != 0:
            logger.error("Codesign verification failed for %s: %s", main_exe, v_res.stderr.strip())
            return False

    # 4. If bundle_dir itself is an .app or .framework bundle, execute top-level sign without --deep (to preserve inner entitlements)
    if bundle_dir.suffix.lower() in (".app", ".framework"):
        cmd = [
            "codesign",
            "--force",
            "--options", "runtime",
        ]
        if entitlements_path and entitlements_path.exists():
            cmd.extend(["--entitlements", str(entitlements_path)])
        cmd.extend(["--sign", identity, str(bundle_dir)])
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            logger.error("Failed to sign app bundle %s: %s", bundle_dir, res.stderr.strip())
            return False

    logger.info("Successfully codesigned bundle %s (%d binaries/libraries signed and verified).", bundle_dir.name, signed_count)
    return True


def build_sidecar(
    project_root: Optional[Path] = None,
    dist_dir: Optional[Path] = None,
    bundle_name: Optional[str] = None,
    target_triple: Optional[str] = None,
    clean: bool = False,
    codesign_identity: Optional[str] = None,
    spec_path: Optional[Path] = None,
    dry_run: bool = False,
    tauri_compat: bool = False,
    tauri_bin_dir: Optional[Path] = None,
) -> Path:
    """Executes the PyInstaller standalone onedir packaging workflow."""
    root = (project_root or Path.cwd()).resolve()
    effective_triple = target_triple or get_target_triple()
    if bundle_name:
        effective_bundle_name = bundle_name
    elif tauri_compat:
        effective_bundle_name = f"server-{effective_triple}"
    else:
        effective_bundle_name = "server-sidecar"

    effective_dist_dir = (dist_dir or root / "dist").resolve()
    effective_spec = (spec_path or root / "server.spec").resolve()

    logger.info("Initiating sidecar packaging for target: %s", effective_triple)
    logger.info("Project Root: %s", root)
    logger.info("Output Directory: %s", effective_dist_dir / effective_bundle_name)

    env_ok, missing = validate_build_environment()
    if not env_ok:
        raise RuntimeError(f"Missing required build dependencies: {', '.join(missing)}")

    if not effective_spec.exists():
        raise FileNotFoundError(f"PyInstaller spec file not found at: {effective_spec}")

    bundle_output_path = effective_dist_dir / effective_bundle_name

    if dry_run:
        logger.info("Dry run requested; packaging configuration verified.")
        return bundle_output_path

    if clean:
        logger.info("Cleaning previous build artifacts...")
        build_dir = root / "build"
        if build_dir.exists():
            shutil.rmtree(build_dir, ignore_errors=True)
        if bundle_output_path.exists():
            shutil.rmtree(bundle_output_path, ignore_errors=True)

    # Prepare environment for spec execution
    build_env = os.environ.copy()
    build_env["SIDECAR_BUNDLE_NAME"] = effective_bundle_name
    build_env["TARGET_TRIPLE"] = effective_triple
    build_env["PYTHONUNBUFFERED"] = "1"

    pyinstaller_cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        str(effective_spec),
        "--distpath", str(effective_dist_dir),
        "--workpath", str(root / "build"),
        "--noconfirm",
    ]

    logger.info("Executing PyInstaller command: %s", " ".join(pyinstaller_cmd))
    result = subprocess.run(
        pyinstaller_cmd,
        cwd=str(root),
        env=build_env,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        logger.error("PyInstaller build failed with code %d:\n%s\n%s", result.returncode, result.stdout, result.stderr)
        raise RuntimeError(f"PyInstaller build failed with exit code {result.returncode}: {result.stderr.strip() or result.stdout.strip()}")

    if not bundle_output_path.exists():
        raise FileNotFoundError(f"Expected bundle output was not created at: {bundle_output_path}")

    # Verify that the main executable is present
    server_bin = bundle_output_path / "server"
    if not server_bin.exists():
        raise FileNotFoundError(f"Main binary 'server' not found in bundle: {bundle_output_path}")

    # Verify TLS root certificate bundle inclusion
    certifi_found = False
    for candidate in [
        bundle_output_path / "certifi" / "cacert.pem",
        bundle_output_path / "_internal" / "certifi" / "cacert.pem",
        bundle_output_path / "cacert.pem",
    ]:
        if candidate.exists():
            certifi_found = True
            logger.info("Verified certifi TLS root bundle at: %s", candidate)
            break

    if not certifi_found:
        for p in bundle_output_path.rglob("cacert.pem"):
            logger.info("Discovered TLS root bundle at: %s", p)
            certifi_found = True
            break

    if not certifi_found:
        raise RuntimeError(f"TLS root certificate bundle was not found within {bundle_output_path}")

    # Ensure static UI assets are fully up to date with project root
    static_src = root / "static"
    if static_src.is_dir():
        for internal_target in [
            bundle_output_path / "_internal" / "static",
            bundle_output_path / "static",
        ]:
            if internal_target.exists():
                shutil.rmtree(internal_target)
            shutil.copytree(static_src, internal_target)

    # Provide architecture-tagged symlink inside bundle for direct Tauri invocation
    triple_bin = bundle_output_path / f"server-{effective_triple}"
    if not triple_bin.exists():
        try:
            triple_bin.symlink_to("server")
        except OSError:
            shutil.copy2(server_bin, triple_bin)

    # Execute code signing with Hardened Runtime and entitlements on macOS
    entitlements_file = (root / "entitlements.plist").resolve()
    effective_entitlements = entitlements_file if entitlements_file.exists() else None

    if codesign_identity:
        ok = codesign_bundle(bundle_output_path, identity=codesign_identity, entitlements_path=effective_entitlements)
        if not ok:
            raise RuntimeError(f"Codesigning bundle failed with identity: {codesign_identity}")
    elif platform.system() == "Darwin":
        # macOS requires ad-hoc signing and entitlements to load internal Python dynamic framework
        ok = codesign_bundle(bundle_output_path, identity="-", entitlements_path=effective_entitlements)
        if not ok:
            raise RuntimeError("Ad-hoc codesigning bundle failed on macOS")

    # Tauri v2 sidecar layout support: populate src-tauri/bin if requested
    if tauri_bin_dir:
        tauri_bin_dir.mkdir(parents=True, exist_ok=True)
        tauri_bundle_dir = tauri_bin_dir / "server-sidecar"
        logger.info("Synchronizing sidecar bundle to Tauri directory: %s", tauri_bundle_dir)
        if tauri_bundle_dir.exists():
            shutil.rmtree(tauri_bundle_dir)
        shutil.copytree(bundle_output_path, tauri_bundle_dir, symlinks=True)

        # Tauri v2 externalBin expects an executable file at src-tauri/bin/server-<target-triple>
        launcher_file = tauri_bin_dir / f"server-{effective_triple}"
        if launcher_file.exists():
            if launcher_file.is_dir():
                shutil.rmtree(launcher_file)
            else:
                launcher_file.unlink()

        built_native = False
        if platform.system() == "Darwin" and shutil.which("clang"):
            c_source = f"""#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>
#include <limits.h>
#include <libgen.h>
#include <mach-o/dyld.h>
#include <string.h>

int main(int argc, char *argv[]) {{
    char path[PATH_MAX];
    char resolved[PATH_MAX];
    uint32_t size = sizeof(path);
    if (_NSGetExecutablePath(path, &size) != 0) {{
        return 1;
    }}
    char *base_path = realpath(path, resolved) ? resolved : path;
    char *dir = dirname(base_path);
    char target[PATH_MAX];

    // 1. Direct local bundle layout
    snprintf(target, sizeof(target), "%s/server-sidecar/server", dir);
    if (access(target, X_OK) == 0) {{
        argv[0] = target;
        execv(target, argv);
    }}

    // 2. Bundled macOS app layout: Contents/MacOS -> Contents/Resources/bin/server-sidecar/server
    snprintf(target, sizeof(target), "%s/../Resources/bin/server-sidecar/server", dir);
    if (access(target, X_OK) == 0) {{
        argv[0] = target;
        execv(target, argv);
    }}

    // 3. Alternative Resources layout
    snprintf(target, sizeof(target), "%s/../Resources/server-sidecar/server", dir);
    if (access(target, X_OK) == 0) {{
        argv[0] = target;
        execv(target, argv);
    }}

    // 4. Tauri dev layout from target/debug: ../../bin/server-sidecar/server
    snprintf(target, sizeof(target), "%s/../../bin/server-sidecar/server", dir);
    if (access(target, X_OK) == 0) {{
        argv[0] = target;
        execv(target, argv);
    }}

    // 5. Development layout from target/debug to dist/server-sidecar
    snprintf(target, sizeof(target), "%s/../../../dist/server-sidecar/server", dir);
    if (access(target, X_OK) == 0) {{
        argv[0] = target;
        execv(target, argv);
    }}

    // 6. Development layout from target/debug to src-tauri/bin
    snprintf(target, sizeof(target), "%s/../../../src-tauri/bin/server-sidecar/server", dir);
    if (access(target, X_OK) == 0) {{
        argv[0] = target;
        execv(target, argv);
    }}

    fprintf(stderr, "Error: Sidecar binary 'server' not found relative to %s\\n", dir);
    return 1;
}}
"""
            import tempfile
            with tempfile.NamedTemporaryFile("w", suffix=".c", delete=False) as tf:
                tf.write(c_source)
                tmp_c = Path(tf.name)
            try:
                arch_flag = "arm64" if "aarch64" in effective_triple or "arm64" in effective_triple else "x86_64"
                clang_cmd = ["clang", "-O2", "-arch", arch_flag, str(tmp_c), "-o", str(launcher_file)]
                c_res = subprocess.run(clang_cmd, capture_output=True, text=True)
                if c_res.returncode == 0:
                    launcher_file.chmod(0o755)
                    built_native = True
                    sign_cmd = ["codesign", "--force", "--options", "runtime"]
                    if effective_entitlements:
                        sign_cmd.extend(["--entitlements", str(effective_entitlements)])
                    sign_cmd.extend(["--sign", codesign_identity or "-", str(launcher_file)])
                    subprocess.run(sign_cmd, capture_output=True, text=True)
                    logger.info("Compiled native Mach-O sidecar launcher at: %s", launcher_file)
            finally:
                tmp_c.unlink(missing_ok=True)

        if not built_native:
            launcher_content = f"""#!/bin/sh
# Tauri v2 Sidecar Launcher for {effective_triple}
DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT_NAME="$(basename "$0")"

if [ -x "$DIR/server-sidecar/server" ]; then
    exec "$DIR/server-sidecar/server" "$@"
elif [ -x "$DIR/../Resources/bin/server-sidecar/server" ]; then
    exec "$DIR/../Resources/bin/server-sidecar/server" "$@"
elif [ -x "$DIR/../Resources/server-sidecar/server" ]; then
    exec "$DIR/../Resources/server-sidecar/server" "$@"
elif [ -x "$DIR/../../bin/server-sidecar/server" ]; then
    exec "$DIR/../../bin/server-sidecar/server" "$@"
elif [ -x "$DIR/../../../dist/server-sidecar/server" ]; then
    exec "$DIR/../../../dist/server-sidecar/server" "$@"
elif [ -x "$DIR/../../../src-tauri/bin/server-sidecar/server" ]; then
    exec "$DIR/../../../src-tauri/bin/server-sidecar/server" "$@"
elif [ -x "$DIR/server.bin" ]; then
    exec "$DIR/server.bin" "$@"
elif [ "$SCRIPT_NAME" != "server" ] && [ -x "$DIR/server" ]; then
    exec "$DIR/server" "$@"
else
    echo "Error: Sidecar binary 'server' not found relative to $DIR" >&2
    exit 1
fi
"""
            launcher_file.write_text(launcher_content, encoding="utf-8")
            launcher_file.chmod(0o755)
            logger.info("Created fallback shell sidecar launcher script at: %s", launcher_file)

        # In dev environments, also ensure target/debug and target/release have sidecar symlinks
        for mode_dir in ("debug", "release"):
            target_mode_dir = root / "src-tauri" / "target" / mode_dir
            target_mode_dir.mkdir(parents=True, exist_ok=True)
            target_symlink = target_mode_dir / "server-sidecar"
            if not target_symlink.exists():
                try:
                    target_symlink.symlink_to(tauri_bundle_dir.resolve())
                    logger.info("Created development symlink: %s -> %s", target_symlink, tauri_bundle_dir)
                except OSError as symlink_err:
                    logger.debug("Notice: Development symlink could not be established: %s", symlink_err)

    logger.info("Sidecar standalone packaging completed successfully: %s", bundle_output_path)
    return bundle_output_path


def package_dmg(
    app_path: Path,
    output_dmg: Path,
    volname: str = "TradingView Terminal",
    icon_path: Optional[Path] = None,
) -> bool:
    """Packages a signed macOS .app bundle into a DMG disk image with an /Applications symlink."""
    if not app_path.exists():
        logger.error("App bundle does not exist: %s", app_path)
        return False

    output_dmg.parent.mkdir(parents=True, exist_ok=True)
    if output_dmg.exists():
        output_dmg.unlink()

    import tempfile
    with tempfile.TemporaryDirectory() as staging_dir:
        staging_path = Path(staging_dir)
        dest_app = staging_path / app_path.name
        subprocess.run(["cp", "-a", str(app_path), str(dest_app)], check=True)
        apps_link = staging_path / "Applications"
        os.symlink("/Applications", str(apps_link))

        if icon_path and icon_path.exists():
            shutil.copy2(icon_path, staging_path / ".VolumeIcon.icns")

        cmd = [
            "hdiutil",
            "create",
            "-volname", volname,
            "-srcfolder", str(staging_path),
            "-ov",
            "-format", "UDZO",
            str(output_dmg),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            logger.error("Failed to create DMG with hdiutil: %s", res.stderr)
            return False

    logger.info("Successfully packaged DMG at: %s", output_dmg)
    return True


def main() -> int:
    """CLI entry point for sidecar packaging script."""
    parser = argparse.ArgumentParser(description="Package TradingView Terminal Backend Sidecar")
    parser.add_argument("--dist-dir", type=Path, default=None, help="Directory for build output (default: dist/)")
    parser.add_argument("--bundle-name", type=str, default=None, help="Output bundle folder name")
    parser.add_argument("--target-triple", type=str, default=None, help="Explicit target triple (e.g. aarch64-apple-darwin)")
    parser.add_argument("--clean", action="store_true", help="Clean build directories before packaging")
    parser.add_argument("--sign", "--codesign-identity", dest="codesign_identity", type=str, default=None, help="macOS codesign identity")
    parser.add_argument("--sign-app", dest="sign_app_path", type=Path, default=None, help="Sign an existing macOS .app bundle with Hardened Runtime and entitlements")
    parser.add_argument("--package-dmg", dest="package_dmg_path", type=Path, default=None, help="Package a signed macOS .app bundle into a distributable DMG")
    parser.add_argument("--spec", dest="spec_path", type=Path, default=None, help="Path to PyInstaller spec file")
    parser.add_argument("--tauri-compat", action="store_true", help="Name bundle according to Tauri convention")
    parser.add_argument("--tauri-bin-dir", type=Path, default=None, help="Copy bundle to Tauri binary directory (e.g. src-tauri/bin)")
    parser.add_argument("--dry-run", action="store_true", help="Validate configuration without running PyInstaller")

    args = parser.parse_args()
    try:
        if args.sign_app_path:
            app_path = args.sign_app_path.resolve()
            if not app_path.exists():
                raise FileNotFoundError(f"App bundle to sign not found: {app_path}")

            # 1. Synchronize pristine sidecar into app bundle using ditto to preserve all symlinks
            source_candidates = [
                Path.cwd() / "dist" / "server-sidecar",
                Path.cwd() / "src-tauri" / "bin" / "server-sidecar",
            ]
            source_sidecar = next((p for p in source_candidates if p.exists() and (p / "server").is_file()), None)
            if source_sidecar:
                dest_res_bin = app_path / "Contents" / "Resources" / "bin" / "server-sidecar"
                dest_res_bin.parent.mkdir(parents=True, exist_ok=True)
                if dest_res_bin.exists():
                    shutil.rmtree(dest_res_bin)
                logger.info("Synchronizing sidecar bundle to .app with ditto: %s -> %s", source_sidecar, dest_res_bin)
                subprocess.run(["ditto", str(source_sidecar), str(dest_res_bin)], check=True)

                # Ensure static UI assets inside app bundle are completely up to date
                static_src = Path.cwd() / "static"
                if static_src.is_dir():
                    for static_target in [
                        dest_res_bin / "_internal" / "static",
                        dest_res_bin / "static",
                    ]:
                        if static_target.exists():
                            shutil.rmtree(static_target)
                        shutil.copytree(static_src, static_target)

            # 2. Repair any flattened or broken framework symlinks
            repaired = repair_framework_symlinks(app_path)
            if repaired > 0:
                logger.info("Repaired %d framework symlinks in app bundle", repaired)

            entitlements_file = (Path.cwd() / "entitlements.plist").resolve()
            effective_entitlements = entitlements_file if entitlements_file.exists() else None
            identity = args.codesign_identity or "-"
            logger.info("Codesigning app bundle: %s with identity: %s", app_path, identity)
            ok = codesign_bundle(app_path, identity=identity, entitlements_path=effective_entitlements)
            if not ok:
                raise RuntimeError(f"Codesigning app bundle failed: {app_path}")
            print(f"SUCCESS: App bundle signed and verified at: {app_path}")

            if args.package_dmg_path:
                dmg_out = args.package_dmg_path.resolve()
                icon = (Path.cwd() / "src-tauri" / "icons" / "icon.icns").resolve()
                dmg_ok = package_dmg(app_path, dmg_out, icon_path=icon)
                if not dmg_ok:
                    raise RuntimeError(f"Packaging DMG failed at: {dmg_out}")
                print(f"SUCCESS: Packaged DMG ready at: {dmg_out}")
            return 0

        output = build_sidecar(
            dist_dir=args.dist_dir,
            bundle_name=args.bundle_name,
            target_triple=args.target_triple,
            clean=args.clean,
            codesign_identity=args.codesign_identity,
            spec_path=args.spec_path,
            dry_run=args.dry_run,
            tauri_compat=args.tauri_compat,
            tauri_bin_dir=args.tauri_bin_dir,
        )
        print(f"SUCCESS: Sidecar bundle ready at: {output}")
        return 0
    except Exception as exc:
        logger.error("Build failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
