"""Bootstrap utilities: TLS root CA setup, token log redaction, dotenv, static resolution, and process watchdog."""

import asyncio
import logging
import os
import re
import signal
import socket
import sys
import threading
from pathlib import Path
from typing import Any, List, Optional

from fastapi.staticfiles import StaticFiles

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def init_certifi_ca_bundle() -> Optional[str]:
    """Initializes and verifies TLS root CA certificates from certifi.

    Guarantees that SSL_CERT_FILE and REQUESTS_CA_BUNDLE point to a valid, existing
    CA bundle on disk, repairing any stale or nonexistent inherited environment variables
    to prevent SSL/TLS connection failures during HTTPS requests to market data providers.
    """
    ca_bundle: Optional[str] = None
    try:
        import certifi
        bundle_candidate = certifi.where()
        if bundle_candidate and os.path.exists(bundle_candidate):
            ca_bundle = str(bundle_candidate)
    except Exception as _cert_err:
        logging.getLogger("tradingview.server").debug("Certifi import notice: %s", _cert_err)

    # Fallback search locations for PyInstaller frozen bundles
    if not ca_bundle:
        search_roots = []
        if getattr(sys, "frozen", False):
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                search_roots.append(Path(meipass))
            exec_dir = Path(sys.executable).resolve().parent
            search_roots.append(exec_dir)
            search_roots.append(exec_dir / "_internal")

        for root in search_roots:
            for sub in [Path("certifi") / "cacert.pem", Path("cacert.pem")]:
                candidate = root / sub
                if candidate.exists():
                    ca_bundle = str(candidate)
                    break
            if ca_bundle:
                break

    if ca_bundle and os.path.exists(ca_bundle):
        # Repair SSL_CERT_FILE if unset or pointing to a non-existent file
        current_ssl = os.environ.get("SSL_CERT_FILE")
        if not current_ssl or not os.path.exists(current_ssl):
            os.environ["SSL_CERT_FILE"] = ca_bundle

        # Repair REQUESTS_CA_BUNDLE if unset or pointing to a non-existent file
        current_req = os.environ.get("REQUESTS_CA_BUNDLE")
        if not current_req or not os.path.exists(current_req):
            os.environ["REQUESTS_CA_BUNDLE"] = ca_bundle

        return ca_bundle
    return None


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("tradingview.server")
CA_BUNDLE_PATH: Optional[str] = init_certifi_ca_bundle()


class RedactTokenFilter(logging.Filter):
    """Redacts session token parameter values from log records to prevent secret leakage in access logs."""

    _TOKEN_RE = re.compile(r"(token=)[^&\s]+", re.IGNORECASE)

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._TOKEN_RE.sub(r"\1[REDACTED]", record.msg)
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    self._TOKEN_RE.sub(r"\1[REDACTED]", a) if isinstance(a, str) else a
                    for a in record.args
                )
            elif isinstance(record.args, dict):
                record.args = {
                    k: (self._TOKEN_RE.sub(r"\1[REDACTED]", v) if isinstance(v, str) else v)
                    for k, v in record.args.items()
                }
        return True


def setup_log_redaction_filters() -> None:
    """Attaches RedactTokenFilter to uvicorn.access and uvicorn.error loggers."""
    flt = RedactTokenFilter()
    for name in ("uvicorn.access", "uvicorn.error", "uvicorn"):
        log_obj = logging.getLogger(name)
        if not any(isinstance(f, RedactTokenFilter) for f in log_obj.filters):
            log_obj.addFilter(flt)


setup_log_redaction_filters()

def load_dotenv_custom(paths: Optional[List[Path]] = None) -> List[str]:
    """Loads environment variables from .env files without external dependencies."""
    if paths is not None:
        candidate_paths = paths
    else:
        candidate_paths = [
            Path.cwd() / ".env",
            PROJECT_ROOT / ".env",
        ]
        if os.getenv("TERMINAL_LOAD_HOME_DOTENV") == "1":
            candidate_paths.extend([
                Path.home() / ".env",
                Path.home() / ".gemini" / ".env",
            ])
    loaded = []
    seen = set()
    for p in candidate_paths:
        try:
            resolved = p.resolve()
        except OSError:
            continue
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        try:
            content = resolved.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            if "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("\"'")
                if k:
                    # Prevent ambient home .env files from polluting ephemeral desktop runtime flags
                    if resolved in (Path.home() / ".env", Path.home() / ".gemini" / ".env") and k in ("DESKTOP_MODE", "DESKTOP_PORT", "DESKTOP_TOKEN"):
                        continue
                    # Never set nonexistent SSL CA file paths from .env
                    if k in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE") and not os.path.exists(v):
                        logger.warning("Ignoring nonexistent CA bundle path in %s for %s: %s", resolved, k, v)
                        continue
                    os.environ.setdefault(k, v)
        loaded.append(str(resolved))
    return loaded


load_dotenv_custom()

# Ensure CA bundle remains valid if .env declared invalid or missing paths
if CA_BUNDLE_PATH and os.path.exists(CA_BUNDLE_PATH):
    current_ssl = os.environ.get("SSL_CERT_FILE")
    if not current_ssl or not os.path.exists(current_ssl):
        os.environ["SSL_CERT_FILE"] = CA_BUNDLE_PATH
    current_req = os.environ.get("REQUESTS_CA_BUNDLE")
    if not current_req or not os.path.exists(current_req):
        os.environ["REQUESTS_CA_BUNDLE"] = CA_BUNDLE_PATH

def bind_loopback_socket(host: str = "127.0.0.1", port: int = 0) -> tuple[socket.socket, int]:
    """Binds a TCP loopback socket, returning the socket object and OS-assigned port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(128)
    assigned_port = sock.getsockname()[1]
    return sock, assigned_port


def is_parent_alive(initial_ppid: int) -> bool:
    """Checks whether the original parent process is still alive to prevent orphaned sidecars."""
    if initial_ppid <= 1:
        return True
    current_ppid = os.getppid()
    if current_ppid == 1 or current_ppid != initial_ppid:
        return False
    try:
        os.kill(initial_ppid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


async def _parent_watchdog_loop(initial_ppid: int, interval: float = 2.0) -> None:
    """Background task in desktop mode that gracefully stops the server if the parent Tauri process dies."""
    try:
        while True:
            await asyncio.sleep(interval)
            if not is_parent_alive(initial_ppid):
                logger.warning("Parent process (PID=%d) no longer alive; shutting down sidecar.", initial_ppid)
                os.kill(os.getpid(), signal.SIGTERM)
                break
    except asyncio.CancelledError:
        logger.debug("Parent watchdog task cancelled during shutdown.")

def resolve_static_dir() -> Path:
    """Resolves the static assets directory for both development and frozen PyInstaller bundles."""
    if getattr(sys, "frozen", False):
        candidates = []
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            meipass_path = Path(meipass)
            candidates.append(meipass_path / "static")
            candidates.append(meipass_path / "_internal" / "static")

        exec_dir = Path(sys.executable).resolve().parent
        candidates.append(exec_dir / "_internal" / "static")
        candidates.append(exec_dir / "static")

        for candidate in candidates:
            if candidate.exists():
                return candidate

        return candidates[0] if candidates else PROJECT_ROOT / "static"
    return PROJECT_ROOT / "static"


STATIC_DIR = resolve_static_dir()

class NoCacheStaticFiles(StaticFiles):
    """回傳的靜態檔加上 Cache-Control: no-cache（仍可用 ETag 重新驗證），避免 WebView 使用舊版前端。"""

    async def get_response(self, path: str, scope: Any):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response

def start_parent_watchdog(server: Any) -> None:
    """Monitors standard input and signals uvicorn server to exit on EOF (parent process killed)."""
    if not sys.stdin or not hasattr(sys.stdin, "read"):
        logger.debug("sys.stdin is not available; parent watchdog disabled.")
        return
    def _watch() -> None:
        try:
            content = sys.stdin.read()
            if content == "" and not getattr(server, "started", True):
                logger.debug("Parent watchdog observed EOF before server completed startup.")
        except Exception as err:
            logger.debug("Parent watchdog stdin channel closed: %s", err)
        logger.info("Parent process EOF detected on stdin. Triggering server shutdown.")
        server.should_exit = True
    threading.Thread(target=_watch, name="parent-eof-watchdog", daemon=True).start()
