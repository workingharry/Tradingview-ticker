"""Modular FastAPI routers and shared dependencies for Momentum Swing Terminal."""

import sys
from typing import Any


def get_server_module() -> Any:
    """Returns the live `server` module when loaded so test patches on server.* propagate cleanly."""
    return sys.modules.get("server")
