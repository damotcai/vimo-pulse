"""
VĨ MÔ PULSE — Smart Incremental SQLite Database Store (WAL Mode)
Persists per-source snapshots, incremental date-keyed time-series points,
and assembled dashboard state so:
  1. Opening the website always serves complete verified data immediately.
  2. Upstream rate-limits or empty responses NEVER overwrite valid data.
  3. New dates/points are merged incrementally (append/upsert by date).
  4. Smart refresh inspects each source individually and only fetches missing/expired feeds.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from security_utils import get_logger

logger = get_logger("db_store")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_DB_PATH = DATA_DIR / "vimo_pulse.db"


def _canonical_hash(data: Any) -> str:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


class MacroDatabase:
    """Thread-safe SQLite WAL store for incremental macro & market data."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=15.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA temp_store=MEMORY;")
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS source_snapshots (
                        source_key TEXT PRIMARY KEY,
                        payload_json TEXT NOT NULL,
                        fetched_at REAL NOT NULL,
                        latest_date TEXT,
                        record_count INTEGER NOT NULL DEFAULT 0,
                        data_hash TEXT NOT NULL DEFAULT ''
                    );

                    CREATE TABLE IF NOT EXISTS series_points (
                        series_key TEXT NOT NULL,
                        point_date TEXT NOT NULL,
                        value_json TEXT NOT NULL,
                        updated_at REAL NOT NULL,
                        PRIMARY KEY (series_key, point_date)
                    );

                    CREATE INDEX IF NOT EXISTS idx_series_points_key
                        ON series_points (series_key, point_date);

                    CREATE TABLE IF NOT EXISTS dashboard_state (
                        state_key TEXT PRIMARY KEY,
                        payload_json TEXT NOT NULL,
                        updated_at REAL NOT NULL
                    );
                    """
                )

    def get_source_snapshot(self, source_key: str) -> dict[str, Any] | None:
        with self._lock:
            try:
                with self._connect() as conn:
                    row = conn.execute(
                        "SELECT payload_json, fetched_at, latest_date, record_count, data_hash "
                        "FROM source_snapshots WHERE source_key = ?",
                        (source_key,),
                    ).fetchone()
                    if not row:
                        return None
                    return {
                        "data": json.loads(row["payload_json"]),
                        "ts": float(row["fetched_at"]),
                        "latest_date": row["latest_date"],
                        "record_count": int(row["record_count"] or 0),
                        "data_hash": row["data_hash"] or "",
                    }
            except Exception as exc:
                logger.warning("SQLite get_source_snapshot(%s) error: %s", source_key, exc)
                return None

    def get_all_source_snapshots(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        with self._lock:
            try:
                with self._connect() as conn:
                    rows = conn.execute(
                        "SELECT source_key, payload_json, fetched_at, latest_date, record_count, data_hash "
                        "FROM source_snapshots"
                    ).fetchall()
                    for row in rows:
                        try:
                            out[row["source_key"]] = {
                                "data": json.loads(row["payload_json"]),
                                "ts": float(row["fetched_at"]),
                                "latest_date": row["latest_date"],
                                "record_count": int(row["record_count"] or 0),
                                "data_hash": row["data_hash"] or "",
                            }
                        except Exception:
                            continue
            except Exception as exc:
                logger.warning("SQLite get_all_source_snapshots error: %s", exc)
        return out

    def save_source_snapshot(
        self,
        source_key: str,
        data: Any,
        latest_date: str | None = None,
        record_count: int = 0,
        fetched_at: float | None = None,
    ) -> dict[str, Any]:
        ts = float(fetched_at if fetched_at is not None else time.time())
        new_hash = _canonical_hash(data)
        payload_json = json.dumps(data, ensure_ascii=False)
        with self._lock:
            try:
                with self._connect() as conn:
                    prev = conn.execute(
                        "SELECT data_hash, record_count FROM source_snapshots WHERE source_key = ?",
                        (source_key,),
                    ).fetchone()
                    changed = (prev is None) or (prev["data_hash"] != new_hash)
                    prev_count = int(prev["record_count"]) if prev else 0
                    conn.execute(
                        """
                        INSERT INTO source_snapshots (source_key, payload_json, fetched_at, latest_date, record_count, data_hash)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(source_key) DO UPDATE SET
                            payload_json = excluded.payload_json,
                            fetched_at = excluded.fetched_at,
                            latest_date = COALESCE(excluded.latest_date, source_snapshots.latest_date),
                            record_count = excluded.record_count,
                            data_hash = excluded.data_hash
                        """,
                        (source_key, payload_json, ts, latest_date, int(record_count), new_hash),
                    )
                    return {
                        "changed": changed,
                        "prev_count": prev_count,
                        "new_count": int(record_count),
                        "added_delta": max(0, int(record_count) - prev_count),
                    }
            except Exception as exc:
                logger.warning("SQLite save_source_snapshot(%s) error: %s", source_key, exc)
                return {"changed": False, "prev_count": 0, "new_count": int(record_count), "added_delta": 0}

    def touch_source_checked(self, source_key: str, checked_at: float | None = None) -> None:
        """Update fetched_at timestamp when upstream was checked and confirmed unchanged."""
        ts = float(checked_at if checked_at is not None else time.time())
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute(
                        "UPDATE source_snapshots SET fetched_at = ? WHERE source_key = ?",
                        (ts, source_key),
                    )
            except Exception as exc:
                logger.warning("SQLite touch_source_checked(%s) error: %s", source_key, exc)

    def upsert_series_points(
        self,
        series_key: str,
        points: list[dict[str, Any]],
        date_key: str = "date",
    ) -> tuple[list[dict[str, Any]], int]:
        """
        Incrementally merge incoming time-series points with existing SQLite history.
        Only inserts/updates dates that are new or changed; NEVER deletes older history.
        Returns (merged_sorted_points, newly_added_or_updated_count).
        """
        now = time.time()
        with self._lock:
            try:
                with self._connect() as conn:
                    existing_rows = conn.execute(
                        "SELECT point_date, value_json FROM series_points WHERE series_key = ? ORDER BY point_date ASC",
                        (series_key,),
                    ).fetchall()
                    existing_map: dict[str, str] = {r["point_date"]: r["value_json"] for r in existing_rows}

                    to_write: list[tuple[str, str, str, float]] = []
                    for pt in points or []:
                        if not isinstance(pt, dict):
                            continue
                        dt = str(pt.get(date_key) or "").strip()
                        if not dt:
                            continue
                        val_json = json.dumps(pt, ensure_ascii=False, sort_keys=True)
                        if existing_map.get(dt) != val_json:
                            existing_map[dt] = val_json
                            to_write.append((series_key, dt, val_json, now))

                    if to_write:
                        conn.executemany(
                            """
                            INSERT INTO series_points (series_key, point_date, value_json, updated_at)
                            VALUES (?, ?, ?, ?)
                            ON CONFLICT(series_key, point_date) DO UPDATE SET
                                value_json = excluded.value_json,
                                updated_at = excluded.updated_at
                            """,
                            to_write,
                        )

                    merged = [json.loads(existing_map[d]) for d in sorted(existing_map.keys())]
                    return merged, len(to_write)
            except Exception as exc:
                logger.warning("SQLite upsert_series_points(%s) error: %s", series_key, exc)
                # Graceful in-memory fallback
                dedup = {str(p.get(date_key)): p for p in (points or []) if isinstance(p, dict) and p.get(date_key)}
                return [dedup[d] for d in sorted(dedup.keys())], 0

    def load_series_points(self, series_key: str) -> list[dict[str, Any]]:
        with self._lock:
            try:
                with self._connect() as conn:
                    rows = conn.execute(
                        "SELECT value_json FROM series_points WHERE series_key = ? ORDER BY point_date ASC",
                        (series_key,),
                    ).fetchall()
                    return [json.loads(r["value_json"]) for r in rows]
            except Exception as exc:
                logger.warning("SQLite load_series_points(%s) error: %s", series_key, exc)
                return []

    def save_dashboard_snapshot(self, payload: dict[str, Any]) -> None:
        now = time.time()
        raw = json.dumps(payload, ensure_ascii=False)
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute(
                        """
                        INSERT INTO dashboard_state (state_key, payload_json, updated_at)
                        VALUES ('latest_dashboard', ?, ?)
                        ON CONFLICT(state_key) DO UPDATE SET
                            payload_json = excluded.payload_json,
                            updated_at = excluded.updated_at
                        """,
                        (raw, now),
                    )
            except Exception as exc:
                logger.warning("SQLite save_dashboard_snapshot error: %s", exc)

    def load_dashboard_snapshot(self) -> dict[str, Any] | None:
        with self._lock:
            try:
                with self._connect() as conn:
                    row = conn.execute(
                        "SELECT payload_json FROM dashboard_state WHERE state_key = 'latest_dashboard'"
                    ).fetchone()
                    if not row:
                        return None
                    return json.loads(row["payload_json"])
            except Exception as exc:
                logger.warning("SQLite load_dashboard_snapshot error: %s", exc)
                return None

    def get_db_stats(self) -> dict[str, Any]:
        with self._lock:
            try:
                now = time.time()
                with self._connect() as conn:
                    src_rows = conn.execute(
                        "SELECT source_key, fetched_at, latest_date, record_count FROM source_snapshots ORDER BY source_key"
                    ).fetchall()
                    pt_row = conn.execute("SELECT COUNT(*) AS cnt FROM series_points").fetchone()
                    total_points = int(pt_row["cnt"]) if pt_row else 0
                size_kb = round(self.db_path.stat().st_size / 1024.0, 1) if self.db_path.exists() else 0.0
                sources = {
                    r["source_key"]: {
                        "age_sec": round(max(0.0, now - float(r["fetched_at"])), 1),
                        "latest_date": r["latest_date"],
                        "records": int(r["record_count"] or 0),
                    }
                    for r in src_rows
                }
                return {
                    "engine": "SQLite WAL",
                    "db_file": self.db_path.name,
                    "size_kb": size_kb,
                    "sources_count": len(sources),
                    "total_series_points": total_points,
                    "sources": sources,
                }
            except Exception as exc:
                logger.warning("SQLite get_db_stats error: %s", exc)
                return {"engine": "SQLite WAL", "sources_count": 0, "total_series_points": 0, "sources": {}}


# Global singleton DB instance
macro_db = MacroDatabase(DEFAULT_DB_PATH)
