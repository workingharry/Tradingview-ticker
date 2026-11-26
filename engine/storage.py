"""Local SQLite Persistence Engine for Historical OHLCV K-Lines.

Manages persistent bar storage with SQLite WAL mode, concurrency busy timeout,
automatic directory creation, and retention pruning.
"""

import sys
import os
import math
import json
import shutil
import time
import sqlite3
import threading
import logging
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Dict, List, Any, Optional, Sequence, Union

logger = logging.getLogger("tradingview.storage")

LEGACY_STORAGE_DIR = Path.home() / "Library" / "Application Support" / "com.tradingview.terminal"
DEFAULT_STORAGE_DIR = Path.home() / "Library" / "Application Support" / "com.harryng.momentum-terminal"
DEFAULT_STORAGE_PATH = DEFAULT_STORAGE_DIR / "terminal.sqlite"


def migrate_legacy_storage(
    legacy_dir: Optional[Path] = None,
    new_dir: Optional[Path] = None,
) -> bool:
    """Copies legacy SQLite database files to the new identifier directory on first launch.

    Preserves all legacy files untouched. Returns True if migration performed a copy.
    """
    src_dir = Path(legacy_dir) if legacy_dir is not None else LEGACY_STORAGE_DIR
    dst_dir = Path(new_dir) if new_dir is not None else DEFAULT_STORAGE_DIR

    src_db = src_dir / "terminal.sqlite"
    dst_db = dst_dir / "terminal.sqlite"

    if not src_db.is_file() or dst_db.exists():
        return False

    try:
        dst_dir.mkdir(parents=True, exist_ok=True)
        for sidecar_suffix in ("-wal", "-shm"):
            sidecar_src = src_dir / f"terminal.sqlite{sidecar_suffix}"
            sidecar_dst = dst_dir / f"terminal.sqlite{sidecar_suffix}"
            if sidecar_src.is_file() and not sidecar_dst.exists():
                shutil.copy2(sidecar_src, sidecar_dst)

        tmp_dst = dst_dir / "terminal.sqlite.migrating"
        shutil.copy2(src_db, tmp_dst)
        os.replace(tmp_dst, dst_db)
        logger.info("Migrated legacy SQLite storage from %s to %s (legacy preserved)", src_db, dst_db)
        return True
    except OSError as exc:
        logger.warning("Failed to migrate legacy storage from %s to %s: %s", src_db, dst_db, exc)
        return False


def get_default_db_path() -> Path:
    """Resolves target SQLite database file path from environment or default app support dir."""
    env_path = os.environ.get("TERMINAL_DB_PATH")
    if env_path and env_path.strip():
        return Path(env_path.strip()).expanduser().resolve()
    if "pytest" in sys.modules or "unittest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST"):
        import tempfile
        return (Path(tempfile.gettempdir()) / "tradingview_test_terminal.sqlite").resolve()
    migrate_legacy_storage()
    return DEFAULT_STORAGE_PATH.resolve()


DEFAULT_WATCHLIST_SEEDS: List[Dict[str, Any]] = [
    {"id": "NASDAQ:NVDA", "name": "NVIDIA Corp.", "exchange": "NASDAQ", "pair": "NVDA", "type": "stock", "sort_order": 0, "sector": "Technology", "industry": "Semiconductors"},
    {"id": "NASDAQ:FAST", "name": "Fastenal Company", "exchange": "NASDAQ", "pair": "FAST", "type": "stock", "sort_order": 1, "sector": "Industrials", "industry": "Industrial Distribution"},
    {"id": "NASDAQ:PRCH", "name": "Porch Group Inc.", "exchange": "NASDAQ", "pair": "PRCH", "type": "stock", "sort_order": 2, "sector": "Technology", "industry": "Software - Application"},
    {"id": "AMEX:SPY", "name": "SPDR S&P 500 ETF Trust", "exchange": "AMEX", "pair": "SPY", "type": "etf", "sort_order": 3, "sector": "Index ETF", "industry": "Large Cap Blend"},
    {"id": "NASDAQ:QQQ", "name": "Invesco QQQ Trust", "exchange": "NASDAQ", "pair": "QQQ", "type": "etf", "sort_order": 4, "sector": "Index ETF", "industry": "Large Cap Growth"},
    {"id": "NASDAQ:AAPL", "name": "Apple Inc.", "exchange": "NASDAQ", "pair": "AAPL", "type": "stock", "sort_order": 5, "sector": "Technology", "industry": "Consumer Electronics"},
    {"id": "BINANCE:BTCUSDT", "name": "Bitcoin", "exchange": "Binance", "pair": "BTC/USDT", "type": "crypto", "sort_order": 6, "sector": "Cryptocurrency", "industry": "Layer 1"},
    {"id": "BINANCE:ETHUSDT", "name": "Ethereum", "exchange": "Binance", "pair": "ETH/USDT", "type": "crypto", "sort_order": 7, "sector": "Cryptocurrency", "industry": "Smart Contract"},
    {"id": "BINANCE:SOLUSDT", "name": "Solana", "exchange": "Binance", "pair": "SOL/USDT", "type": "crypto", "sort_order": 8, "sector": "Cryptocurrency", "industry": "Layer 1"},
]

CANONICAL_TIMEFRAMES: Dict[str, str] = {
    "1m": "1m", "1M": "1m",
    "5m": "5m", "5M": "5m",
    "15m": "15m", "15M": "15m",
    "1h": "1h", "1H": "1h", "60m": "1h", "60M": "1h",
    "1d": "1D", "1D": "1D",
    "1w": "1W", "1W": "1W", "1wk": "1W", "1WK": "1W",
}


def normalize_storage_timeframe(timeframe: Optional[str], default: str = "1D") -> str:
    """Normalizes timeframe to canonical format (e.g. 1m, 5m, 1h, 1D, 1W)."""
    if not timeframe or not isinstance(timeframe, str):
        return default
    cleaned = timeframe.strip()
    return CANONICAL_TIMEFRAMES.get(cleaned, CANONICAL_TIMEFRAMES.get(cleaned.upper(), cleaned))


class Storage:
    """SQLite-backed persistent store for historical OHLCV candlestick bars."""

    def __init__(self, db_path: Optional[Union[str, Path]] = None) -> None:
        if db_path and str(db_path) == ":memory:":
            self.db_path = Path(":memory:")
            self._is_memory = True
        else:
            self.db_path = Path(db_path).expanduser().resolve() if db_path else get_default_db_path()
            self._is_memory = False
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._closed = False

        self._conn = sqlite3.connect(
            ":memory:" if self._is_memory else str(self.db_path),
            timeout=5.0,
            check_same_thread=False
        )

        with self._lock:
            if not self._is_memory:
                self._conn.execute("PRAGMA journal_mode=WAL;")
                self._conn.execute("PRAGMA busy_timeout=5000;")
                self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bars (
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    time INTEGER NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL,
                    PRIMARY KEY (symbol, timeframe, time)
                );
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_bars_sym_tf_time ON bars(symbol, timeframe, time DESC);"
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS watchlist (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    pair TEXT NOT NULL,
                    type TEXT NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    sector TEXT DEFAULT '',
                    industry TEXT DEFAULT ''
                );
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_watchlist_sort_order ON watchlist(sort_order ASC);"
            )
            # Idempotent migration for existing SQLite databases lacking sector/industry
            cursor = self._conn.cursor()
            cursor.execute("PRAGMA table_info(watchlist);")
            existing_cols = {str(row[1]).lower() for row in cursor.fetchall()}
            if "sector" not in existing_cols:
                self._conn.execute("ALTER TABLE watchlist ADD COLUMN sector TEXT DEFAULT '';")
            if "industry" not in existing_cols:
                self._conn.execute("ALTER TABLE watchlist ADD COLUMN industry TEXT DEFAULT '';")
            # Idempotent migration: fix PRCH exchange prefix from NYSE to NASDAQ
            try:
                self._conn.execute("UPDATE OR IGNORE watchlist SET id='NASDAQ:PRCH', exchange='NASDAQ' WHERE id='NYSE:PRCH';")
                self._conn.execute("DELETE FROM watchlist WHERE id='NYSE:PRCH';")
                self._conn.execute("UPDATE OR IGNORE bars SET symbol='NASDAQ:PRCH' WHERE symbol='NYSE:PRCH';")
                self._conn.execute("DELETE FROM bars WHERE symbol='NYSE:PRCH';")
            except Exception as exc:
                logger.warning("PRCH exchange prefix migration notice: %s", exc)
            self._conn.commit()
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS fundamental_reports (
                    symbol TEXT PRIMARY KEY,
                    metrics_json TEXT NOT NULL,
                    ai_summary TEXT NOT NULL,
                    model_used TEXT,
                    updated_at INTEGER NOT NULL
                );
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_agent_lens_reports (
                    symbol TEXT,
                    timeframe TEXT,
                    bar_time INTEGER NOT NULL,
                    lens_json TEXT NOT NULL,
                    ai_decision_json TEXT NOT NULL,
                    model_used TEXT,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (symbol, timeframe)
                );
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS preferences (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS drawings (
                    id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    pane_id TEXT NOT NULL DEFAULT 'candle',
                    tool_type TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    timeframe_scope TEXT NOT NULL DEFAULT 'ALL',
                    revision INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    is_deleted INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_drawings_symbol ON drawings(symbol, is_deleted);"
            )
            # Idempotent migration for existing SQLite databases lacking revision
            cursor = self._conn.cursor()
            cursor.execute("PRAGMA table_info(drawings);")
            drawing_cols = {str(row[1]).lower() for row in cursor.fetchall()}
            if "revision" not in drawing_cols:
                self._conn.execute("ALTER TABLE drawings ADD COLUMN revision INTEGER NOT NULL DEFAULT 1;")
            self._conn.commit()

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS screener_snapshots (
                    scan_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    filter_count INTEGER NOT NULL,
                    frameworks TEXT NOT NULL,
                    tier TEXT NOT NULL,
                    close REAL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (scan_date, symbol)
                );
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_screener_snapshots_sym_date ON screener_snapshots(symbol, scan_date DESC);"
            )
            self._conn.commit()

    @property
    def is_closed(self) -> bool:
        return self._closed

    def save_bars(
        self,
        symbol: str,
        timeframe: str,
        bars: Sequence[Any]
    ) -> int:
        """Persists a list of bars with upsert semantics on primary key conflict.

        Accepts dictionaries or object models providing time, open, high, low, close, volume.
        Returns the count of successfully persisted bar records.
        """
        if self._closed:
            raise RuntimeError("Cannot write to closed storage")
        if not symbol or not timeframe or not bars:
            return 0

        clean_symbol = str(symbol).strip()
        clean_tf = normalize_storage_timeframe(timeframe, default=str(timeframe).strip())
        if not clean_symbol or not clean_tf:
            return 0

        records = []
        for b in bars:
            if isinstance(b, dict):
                t = b.get("time")
                o = b.get("open")
                h = b.get("high")
                l = b.get("low")
                c = b.get("close")
                v = b.get("volume", 0.0)
            elif isinstance(b, (list, tuple)) and len(b) >= 6:
                t = b[0]
                o = b[1]
                h = b[2]
                l = b[3]
                c = b[4]
                v = b[5]
            else:
                t = getattr(b, "time", None)
                o = getattr(b, "open", None)
                h = getattr(b, "high", None)
                l = getattr(b, "low", None)
                c = getattr(b, "close", None)
                v = getattr(b, "volume", 0.0)

            if t is None or o is None or h is None or l is None or c is None:
                continue

            try:
                numeric_t = int(t)
                if numeric_t > 100000000000:
                    numeric_t //= 1000
                f_o = float(o)
                f_h = float(h)
                f_l = float(l)
                f_c = float(c)
                f_v = float(v) if v is not None else 0.0

                if not (math.isfinite(f_o) and math.isfinite(f_h) and math.isfinite(f_l) and math.isfinite(f_c) and math.isfinite(f_v)):
                    continue
                if f_o <= 0.0 or f_c <= 0.0 or f_h <= 0.0 or f_l <= 0.0:
                    continue

                norm_h = max(f_h, f_o, f_c)
                norm_l = min(f_l, f_o, f_c)
                norm_v = max(0.0, f_v)

                records.append((
                    clean_symbol,
                    clean_tf,
                    numeric_t,
                    f_o,
                    norm_h,
                    norm_l,
                    f_c,
                    norm_v
                ))
            except (ValueError, TypeError):
                continue

        if not records:
            return 0

        sql = """
            INSERT INTO bars (symbol, timeframe, time, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, timeframe, time) DO UPDATE SET
                open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                volume = excluded.volume;
        """

        with self._lock:
            self._conn.executemany(sql, records)
            self._conn.commit()

        return len(records)

    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 300
    ) -> List[Dict[str, Any]]:
        """Retrieves historical bars ordered chronologically ascending up to the specified limit."""
        if self._closed:
            raise RuntimeError("Cannot read from closed storage")
        if not symbol or not timeframe or limit <= 0:
            return []

        clean_symbol = str(symbol).strip()
        clean_tf = normalize_storage_timeframe(timeframe, default=str(timeframe).strip())

        sql = """
            SELECT time, open, high, low, close, volume
            FROM (
                SELECT time, open, high, low, close, volume
                FROM bars
                WHERE symbol = ? AND timeframe = ?
                ORDER BY time DESC
                LIMIT ?
            )
            ORDER BY time ASC;
        """

        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(sql, (clean_symbol, clean_tf, int(limit)))
            rows = cursor.fetchall()

        return [
            {
                "time": int(r[0]),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[5]),
            }
            for r in rows
        ]

    def prune_bars(self, keep_per_series: int = 2000) -> int:
        """Prunes historical bars exceeding keep_per_series for every symbol/timeframe series.

        Returns total count of deleted bar rows.
        """
        if self._closed:
            raise RuntimeError("Cannot prune closed storage")
        if keep_per_series < 0:
            raise ValueError("keep_per_series must be non-negative")

        sql = """
            DELETE FROM bars
            WHERE rowid IN (
                SELECT rowid FROM (
                    SELECT rowid,
                           ROW_NUMBER() OVER (PARTITION BY symbol, timeframe ORDER BY time DESC) as rn
                    FROM bars
                )
                WHERE rn > ?
            );
        """

        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(sql, (int(keep_per_series),))
            deleted = cursor.rowcount
            self._conn.commit()

        return max(0, deleted)

    def get_watchlist(self) -> List[Dict[str, Any]]:
        """Returns the persisted watchlist ordered by sort_order. Auto-seeds defaults on first initialization only."""
        if self._closed:
            raise RuntimeError("Cannot read from closed storage")
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT id, name, exchange, pair, type, sort_order, sector, industry FROM watchlist ORDER BY sort_order ASC, rowid ASC;")
            rows = cursor.fetchall()
            if not rows:
                cursor.execute("SELECT value FROM preferences WHERE key = 'watchlist_seeded';")
                seeded_row = cursor.fetchone()
                if not seeded_row:
                    for item in DEFAULT_WATCHLIST_SEEDS:
                        cursor.execute(
                            "INSERT OR IGNORE INTO watchlist (id, name, exchange, pair, type, sort_order, sector, industry) VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
                            (
                                item["id"],
                                item["name"],
                                item["exchange"],
                                item["pair"],
                                item["type"],
                                item.get("sort_order", 0),
                                item.get("sector", ""),
                                item.get("industry", ""),
                            )
                        )
                    import time
                    now = int(time.time())
                    cursor.execute(
                        "INSERT INTO preferences (key, value, updated_at) VALUES ('watchlist_seeded', 'true', ?) ON CONFLICT(key) DO UPDATE SET value='true', updated_at=excluded.updated_at;",
                        (now,)
                    )
                    self._conn.commit()
                    cursor.execute("SELECT id, name, exchange, pair, type, sort_order, sector, industry FROM watchlist ORDER BY sort_order ASC, rowid ASC;")
                    rows = cursor.fetchall()
            else:
                cursor.execute("SELECT value FROM preferences WHERE key = 'watchlist_seeded';")
                if not cursor.fetchone():
                    import time
                    now = int(time.time())
                    cursor.execute(
                        "INSERT INTO preferences (key, value, updated_at) VALUES ('watchlist_seeded', 'true', ?) ON CONFLICT(key) DO NOTHING;",
                        (now,)
                    )
                    self._conn.commit()
            return [
                {
                    "id": str(r[0]),
                    "name": str(r[1]),
                    "exchange": str(r[2]),
                    "pair": str(r[3]),
                    "type": str(r[4]),
                    "sort_order": int(r[5]),
                    "sector": str(r[6] or "") if len(r) > 6 else "",
                    "industry": str(r[7] or "") if len(r) > 7 else "",
                }
                for r in rows
            ]

    def add_watchlist_item(self, item: Dict[str, Any]) -> bool:
        """Persists or updates an item in the watchlist table."""
        if self._closed:
            raise RuntimeError("Cannot write to closed storage")
        if not item or not item.get("id"):
            return False
        sym_id = str(item["id"]).strip().upper()
        name = str(item.get("name") or sym_id.split(":")[-1]).strip()[:64]
        exchange = str(item.get("exchange") or (sym_id.split(":")[0] if ":" in sym_id else "US")).strip()[:20]
        pair = str(item.get("pair") or (sym_id.split(":")[-1] if ":" in sym_id else sym_id)).strip()[:24]
        sym_type = str(item.get("type") or "stock").strip().lower()
        sector = str(item.get("sector") or "").strip()[:64]
        industry = str(item.get("industry") or "").strip()[:64]
        with self._lock:
            if "sort_order" in item and item["sort_order"] is not None:
                sort_order = int(item["sort_order"])
            else:
                cursor = self._conn.cursor()
                cursor.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM watchlist;")
                row = cursor.fetchone()
                sort_order = int(row[0]) if row and row[0] is not None else 0
            self._conn.execute(
                """
                INSERT INTO watchlist (id, name, exchange, pair, type, sort_order, sector, industry)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    exchange=excluded.exchange,
                    pair=excluded.pair,
                    type=excluded.type,
                    sort_order=excluded.sort_order,
                    sector=excluded.sector,
                    industry=excluded.industry;
                """,
                (sym_id, name, exchange, pair, sym_type, sort_order, sector, industry)
            )
            self._conn.commit()
            return True

    def remove_watchlist_item(self, symbol_id: str) -> bool:
        """Removes a symbol from the persistent watchlist table."""
        if self._closed:
            raise RuntimeError("Cannot write to closed storage")
        if not symbol_id:
            return False
        clean_id = str(symbol_id).strip().upper()
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("DELETE FROM watchlist WHERE id = ?;", (clean_id,))
            deleted = cursor.rowcount > 0
            self._conn.commit()
            return deleted

    def reorder_watchlist(self, ordered_ids: List[str]) -> bool:
        """Updates sort_order for symbols in the watchlist table based on the provided ordered list."""
        if self._closed:
            raise RuntimeError("Cannot write to closed storage")
        if not ordered_ids or not isinstance(ordered_ids, list):
            return False
        with self._lock:
            cursor = self._conn.cursor()
            for idx, raw_id in enumerate(ordered_ids):
                clean_id = str(raw_id).strip().upper()
                cursor.execute(
                    "UPDATE watchlist SET sort_order = ? WHERE id = ?;",
                    (idx, clean_id)
                )
            self._conn.commit()
            return True

    def get_fundamental_report(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Retrieves cached fundamental analysis report for a symbol."""
        if self._closed:
            raise RuntimeError("Cannot read from closed storage")
        if not symbol:
            return None
        clean_sym = str(symbol).strip().upper()
        import json
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT metrics_json, ai_summary, model_used, updated_at FROM fundamental_reports WHERE symbol = ?;",
                (clean_sym,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            try:
                metrics = json.loads(row[0])
            except Exception:
                metrics = {}
            return {
                "symbol": clean_sym,
                "metrics": metrics,
                "ai_summary": row[1],
                "model_used": row[2] or "gemini",
                "updated_at": int(row[3]),
            }

    def save_fundamental_report(
        self,
        symbol: str,
        metrics: Union[str, Dict[str, Any]],
        ai_summary: str,
        model_used: Optional[str] = "gemini",
        updated_at: Optional[int] = None
    ) -> bool:
        """Persists or updates fundamental analysis report for a symbol."""
        if self._closed:
            raise RuntimeError("Cannot write to closed storage")
        if not symbol:
            return False
        clean_sym = str(symbol).strip().upper()
        import json, time
        metrics_str = metrics if isinstance(metrics, str) else json.dumps(metrics, ensure_ascii=False)
        ts = int(updated_at or time.time())
        model = str(model_used or "gemini")
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO fundamental_reports (symbol, metrics_json, ai_summary, model_used, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    metrics_json=excluded.metrics_json,
                    ai_summary=excluded.ai_summary,
                    model_used=excluded.model_used,
                    updated_at=excluded.updated_at;
                """,
                (clean_sym, metrics_str, str(ai_summary or ""), model, ts)
            )
            self._conn.commit()
            return True

    def get_ai_agent_lens_report(self, symbol: str, timeframe: str = "1D") -> Optional[Dict[str, Any]]:
        """Retrieves cached non-repainting AI Agent Lens report for a symbol and timeframe."""
        if self._closed:
            raise RuntimeError("Cannot read from closed storage")
        if not symbol:
            return None
        clean_sym = str(symbol).strip().upper()
        clean_tf = normalize_storage_timeframe(timeframe, default="1D")
        import json
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT bar_time, lens_json, ai_decision_json, model_used, updated_at "
                "FROM ai_agent_lens_reports WHERE symbol = ? AND timeframe = ?;",
                (clean_sym, clean_tf)
            )
            row = cursor.fetchone()
            if not row:
                return None
            try:
                lens_data = json.loads(row[1])
            except Exception:
                lens_data = {}
            try:
                decision_data = json.loads(row[2])
            except Exception:
                decision_data = {}
            return {
                "symbol": clean_sym,
                "timeframe": clean_tf,
                "bar_time": int(row[0]),
                "lens": lens_data,
                "decision": decision_data,
                "model_used": row[3] or "gemini-2.5-flash",
                "updated_at": int(row[4]),
            }

    def save_ai_agent_lens_report(
        self,
        symbol: str,
        timeframe: str,
        bar_time: int,
        lens_data: Union[str, Dict[str, Any]],
        decision_data: Union[str, Dict[str, Any]],
        model_used: Optional[str] = "gemini-2.5-flash",
        updated_at: Optional[int] = None
    ) -> bool:
        """Persists or updates non-repainting AI Agent Lens report for a symbol and timeframe."""
        if self._closed:
            raise RuntimeError("Cannot write to closed storage")
        if not symbol or not timeframe:
            return False
        clean_sym = str(symbol).strip().upper()
        clean_tf = normalize_storage_timeframe(timeframe, default="1D")
        import json, time
        lens_str = lens_data if isinstance(lens_data, str) else json.dumps(lens_data, ensure_ascii=False)
        decision_str = decision_data if isinstance(decision_data, str) else json.dumps(decision_data, ensure_ascii=False)
        ts = int(updated_at or time.time())
        model = str(model_used or "gemini-2.5-flash")
        bt = int(bar_time)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO ai_agent_lens_reports (symbol, timeframe, bar_time, lens_json, ai_decision_json, model_used, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, timeframe) DO UPDATE SET
                    bar_time=excluded.bar_time,
                    lens_json=excluded.lens_json,
                    ai_decision_json=excluded.ai_decision_json,
                    model_used=excluded.model_used,
                    updated_at=excluded.updated_at;
                """,
                (clean_sym, clean_tf, bt, lens_str, decision_str, model, ts)
            )
            self._conn.commit()
            return True

    def get_preferences(self) -> Dict[str, Any]:
        """Retrieves all stored application/terminal preferences."""
        if self._closed:
            raise RuntimeError("Cannot read from closed storage")
        import json
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT key, value FROM preferences;")
            prefs: Dict[str, Any] = {}
            for k, v in cursor.fetchall():
                try:
                    prefs[k] = json.loads(v)
                except Exception:
                    prefs[k] = v
            return prefs

    def save_preferences(self, prefs: Dict[str, Any]) -> bool:
        """Persists or updates application/terminal preferences."""
        if self._closed:
            raise RuntimeError("Cannot write to closed storage")
        if not isinstance(prefs, dict):
            return False
        import json, time
        now = int(time.time())
        with self._lock:
            for k, v in prefs.items():
                v_str = json.dumps(v, ensure_ascii=False) if not isinstance(v, (str, int, float, bool)) else json.dumps(v)
                self._conn.execute(
                    """
                    INSERT INTO preferences (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value=excluded.value,
                        updated_at=excluded.updated_at;
                    """,
                    (str(k), v_str, now)
                )
            self._conn.commit()
            return True

    def save_drawing(self, drawing: Dict[str, Any]) -> Dict[str, Any]:
        """Persists or updates a user drawing keyed by symbol with revision control."""
        if self._closed:
            raise RuntimeError("Cannot write to closed storage")
        if not isinstance(drawing, dict) or not drawing.get("id") or not drawing.get("symbol"):
            raise ValueError("Drawing must include non-empty 'id' and 'symbol'")

        drawing_id = str(drawing["id"]).strip()
        symbol = str(drawing["symbol"]).strip().upper()
        if not drawing_id or not symbol:
            raise ValueError("Drawing 'id' and 'symbol' cannot be blank")

        pane_id = str(drawing.get("pane_id", "candle")).strip()
        tool_type = str(drawing.get("tool_type", "risk_reward")).strip()
        timeframe_scope = str(drawing.get("timeframe_scope", "ALL")).strip()

        raw_data = drawing.get("data") if "data" in drawing else drawing.get("data_json", {})
        data_json = json.dumps(raw_data, ensure_ascii=False) if isinstance(raw_data, (dict, list)) else str(raw_data or "{}")

        now = int(time.time())
        created_at = int(drawing.get("created_at") or now)
        client_revision = int(drawing["revision"]) if "revision" in drawing and drawing["revision"] is not None else None
        is_restore = bool(drawing.get("is_restore", False))

        with self._lock:
            # Check existing record for symbol immutability, tombstone protection, and revision concurrency
            cur = self._conn.cursor()
            cur.execute("SELECT symbol, revision, is_deleted FROM drawings WHERE id = ?;", (drawing_id,))
            row = cur.fetchone()

            if row:
                existing_sym, existing_rev, existing_deleted = row[0], int(row[1]), int(row[2])
                if existing_sym != symbol:
                    raise ValueError(f"Drawing '{drawing_id}' is owned by symbol '{existing_sym}', cannot reassign to '{symbol}'")

                if existing_deleted and not is_restore:
                    raise ValueError(f"Drawing '{drawing_id}' is deleted (tombstone) and cannot be modified by standard save")

                if client_revision is not None and client_revision < existing_rev:
                    raise ValueError(f"Revision conflict for drawing '{drawing_id}': client={client_revision}, current={existing_rev}")

                new_revision = existing_rev + 1
                self._conn.execute(
                    """
                    UPDATE drawings
                    SET pane_id = ?, tool_type = ?, data_json = ?, timeframe_scope = ?, revision = ?, updated_at = ?, is_deleted = 0
                    WHERE id = ? AND symbol = ?;
                    """,
                    (pane_id, tool_type, data_json, timeframe_scope, new_revision, now, drawing_id, symbol)
                )
            else:
                new_revision = client_revision if client_revision and client_revision > 0 else 1
                self._conn.execute(
                    """
                    INSERT INTO drawings (id, symbol, pane_id, tool_type, data_json, timeframe_scope, revision, created_at, updated_at, is_deleted)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0);
                    """,
                    (drawing_id, symbol, pane_id, tool_type, data_json, timeframe_scope, new_revision, created_at, now)
                )

            self._conn.commit()
            return {
                "id": drawing_id,
                "symbol": symbol,
                "pane_id": pane_id,
                "tool_type": tool_type,
                "timeframe_scope": timeframe_scope,
                "revision": new_revision,
                "created_at": created_at,
                "updated_at": now,
                "is_deleted": False
            }

    def get_drawings(self, symbol: str, include_deleted: bool = False) -> List[Dict[str, Any]]:
        """Retrieves drawings for a given symbol."""
        if self._closed:
            raise RuntimeError("Cannot read from closed storage")
        if not symbol:
            return []

        clean_symbol = symbol.strip().upper()
        with self._lock:
            cursor = self._conn.cursor()
            if include_deleted:
                cursor.execute(
                    """
                    SELECT id, symbol, pane_id, tool_type, data_json, timeframe_scope, revision, created_at, updated_at, is_deleted
                    FROM drawings
                    WHERE symbol = ?
                    ORDER BY created_at ASC;
                    """,
                    (clean_symbol,)
                )
            else:
                cursor.execute(
                    """
                    SELECT id, symbol, pane_id, tool_type, data_json, timeframe_scope, revision, created_at, updated_at, is_deleted
                    FROM drawings
                    WHERE symbol = ? AND is_deleted = 0
                    ORDER BY created_at ASC;
                    """,
                    (clean_symbol,)
                )
            rows = cursor.fetchall()

        results = []
        for r in rows:
            try:
                parsed_data = json.loads(r[4])
            except Exception:
                parsed_data = {}
            results.append({
                "id": r[0],
                "symbol": r[1],
                "pane_id": r[2],
                "tool_type": r[3],
                "data": parsed_data,
                "timeframe_scope": r[5],
                "revision": r[6],
                "created_at": r[7],
                "updated_at": r[8],
                "is_deleted": bool(r[9])
            })
        return results

    def delete_drawing(self, drawing_id: str, soft_delete: bool = True, symbol: Optional[str] = None) -> bool:
        """Deletes a drawing by ID (soft-delete tombstone with revision bump by default)."""
        if self._closed:
            raise RuntimeError("Cannot write to closed storage")
        if not drawing_id:
            return False

        clean_id = str(drawing_id).strip()
        clean_sym = symbol.strip().upper() if symbol else None
        now = int(time.time())
        with self._lock:
            if soft_delete:
                if clean_sym:
                    cursor = self._conn.execute(
                        "UPDATE drawings SET is_deleted = 1, revision = revision + 1, updated_at = ? WHERE id = ? AND symbol = ? AND is_deleted = 0;",
                        (now, clean_id, clean_sym)
                    )
                else:
                    cursor = self._conn.execute(
                        "UPDATE drawings SET is_deleted = 1, revision = revision + 1, updated_at = ? WHERE id = ? AND is_deleted = 0;",
                        (now, clean_id)
                    )
            else:
                if clean_sym:
                    cursor = self._conn.execute("DELETE FROM drawings WHERE id = ? AND symbol = ?;", (clean_id, clean_sym))
                else:
                    cursor = self._conn.execute("DELETE FROM drawings WHERE id = ?;", (clean_id,))
            self._conn.commit()
            return cursor.rowcount > 0

    def clear_drawings(self, symbol: str) -> int:
        """Removes or tombstones all drawings for a symbol."""
        if self._closed:
            raise RuntimeError("Cannot write to closed storage")
        if not symbol:
            return 0
        clean_symbol = symbol.strip().upper()
        now = int(time.time())
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE drawings SET is_deleted = 1, updated_at = ? WHERE symbol = ? AND is_deleted = 0;",
                (now, clean_symbol)
            )
            self._conn.commit()
            return cursor.rowcount

    def record_screener_snapshot(self, scan_date: str, items: Sequence[Dict[str, Any]]) -> int:
        """Records daily screener snapshot items for streak tracking."""
        if self._closed:
            raise RuntimeError("Cannot write to closed storage")
        if not items:
            return 0
        now_iso = datetime.now(timezone.utc).isoformat()
        records = []
        for item in items:
            sym = str(item.get("symbol") or "").strip().upper()
            if not sym:
                continue
            fc = int(item.get("filter_count") or 0)
            fw = item.get("frameworks") or []
            if isinstance(fw, (list, tuple, set)):
                fw_str = json.dumps(list(fw))
            else:
                fw_str = str(fw)
            tier = str(item.get("tier") or "Watch")
            close = float(item.get("close") or 0.0)
            records.append((scan_date, sym, fc, fw_str, tier, close, now_iso))

        with self._lock:
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO screener_snapshots (
                    scan_date, symbol, filter_count, frameworks, tier, close, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                records,
            )
            self._conn.commit()
        return len(records)

    def get_screener_streaks(
        self,
        symbols: Sequence[str],
        current_date: Optional[str] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Computes consecutive streak days and filter count delta for given symbols."""
        if self._closed:
            raise RuntimeError("Cannot read from closed storage")
        if not symbols:
            return {}

        sym_list = [str(s).strip().upper() for s in symbols if str(s).strip()]
        if not sym_list:
            return {}

        from engine.market_time import current_session_date
        c_date = current_date or current_session_date().isoformat()

        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT DISTINCT scan_date FROM screener_snapshots WHERE scan_date <= ? ORDER BY scan_date DESC;",
                (c_date,),
            )
            distinct_dates = [r[0] for r in cursor.fetchall()]

            if not distinct_dates:
                return {sym: {"streak_days": 0, "filter_delta": "NEW"} for sym in sym_list}

            placeholders = ",".join("?" for _ in sym_list)
            query = f"""
                SELECT symbol, scan_date, filter_count, tier
                FROM screener_snapshots
                WHERE symbol IN ({placeholders}) AND scan_date <= ?
                ORDER BY scan_date DESC;
            """
            cursor.execute(query, (*sym_list, c_date))
            rows = cursor.fetchall()

        sym_records: Dict[str, Dict[str, int]] = {}
        for sym, s_date, fc, _ in rows:
            if sym not in sym_records:
                sym_records[sym] = {}
            sym_records[sym][s_date] = int(fc)

        results: Dict[str, Dict[str, Any]] = {}
        target_date = distinct_dates[0]
        prev_date = distinct_dates[1] if len(distinct_dates) > 1 else None

        for sym in sym_list:
            history = sym_records.get(sym, {})
            if target_date in history:
                curr_fc = history[target_date]
                if prev_date is not None and prev_date in history:
                    prev_fc = history[prev_date]
                    diff = curr_fc - prev_fc
                    delta_str = f"+{diff}" if diff > 0 else (str(diff) if diff < 0 else "0")
                else:
                    delta_str = "NEW"

                if curr_fc < 2:
                    streak = 0
                else:
                    streak = 1
                    for past_d in distinct_dates[1:]:
                        if past_d in history and history[past_d] >= 2:
                            streak += 1
                        else:
                            break
                results[sym] = {"streak_days": streak, "filter_delta": delta_str}
            else:
                results[sym] = {"streak_days": 0, "filter_delta": "NEW"}

        return results

    def close(self) -> None:
        """Closes the underlying SQLite connection idempotently with WAL flush."""
        with self._lock:
            if not self._closed:
                self._closed = True
                try:
                    self._conn.commit()
                    if not self._is_memory:
                        self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                except Exception as commit_err:
                    logger.debug("Failed to commit and checkpoint SQLite storage on close: %s", commit_err)
                self._conn.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


SQLiteStorage = Storage

_storage_instance: Optional[Storage] = None
_storage_singleton_lock = threading.Lock()


_storage_requested_path: Optional[Path] = None


def get_storage(db_path: Optional[Union[str, Path]] = None) -> Storage:
    """Returns thread-safe shared Storage instance."""
    global _storage_instance, _storage_requested_path
    with _storage_singleton_lock:
        if db_path and str(db_path) == ":memory:":
            target_path = Path(":memory:")
        else:
            target_path = Path(db_path).expanduser().resolve() if db_path else get_default_db_path().resolve()
        if _storage_instance is None or _storage_instance.is_closed or _storage_requested_path != target_path:
            if _storage_instance is not None and not _storage_instance.is_closed:
                _storage_instance.close()
            try:
                _storage_instance = Storage(db_path=target_path)
            except (sqlite3.DatabaseError, sqlite3.OperationalError, sqlite3.IntegrityError) as db_err:
                logger.error("Failed to initialize SQLite storage at %s: %s; falling back to in-memory database", target_path, db_err)
                _storage_instance = Storage(db_path=":memory:")
            _storage_requested_path = target_path
        return _storage_instance


def close_storage() -> None:
    """Closes shared storage singleton if initialized."""
    global _storage_instance
    with _storage_singleton_lock:
        if _storage_instance is not None:
            _storage_instance.close()
            _storage_instance = None
