"""
VĨ MÔ PULSE — Security, Validation, Concurrency & Reliability Utilities
Shared hardening helpers for SSRF prevention, strict input validation,
atomic file writes, rate limiting, connection pooling, and structured logging.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import socket
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter

VALID_TICKER_RE = re.compile(r"^[A-Z0-9]{2,10}$")
VALID_MODEL_RE = re.compile(r"^[a-zA-Z0-9._:/-]{2,80}$")
VALID_TG_TOKEN_RE = re.compile(r"^\d{6,15}:[A-Za-z0-9_-]{30,60}$")
VALID_TG_CHAT_ID_RE = re.compile(r"^(?:-?\d{4,20}|@[A-Za-z0-9_]{5,32})$")

BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata",
    "instance-data",
}


def get_logger(name: str) -> logging.Logger:
    """Return a clean structured logger without duplicating handlers."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s", datefmt="%H:%M:%S")
        )
        logger.addHandler(handler)
        logger.propagate = False
    if logger.level == logging.NOTSET:
        logger.setLevel(logging.INFO)
    return logger


def validate_ticker(raw: str | None) -> str:
    """Strictly validate Vietnamese stock ticker or index code (prevents path traversal & injection)."""
    sym = (raw or "").strip().upper()
    if not VALID_TICKER_RE.match(sym):
        raise ValueError("Mã cổ phiếu không hợp lệ (chỉ chấp nhận 2–10 ký tự chữ/số A-Z, 0-9).")
    return sym


def validate_ai_model(raw: str | None, default: str) -> str:
    """Validate LLM model identifier before URL or payload interpolation."""
    chosen = (raw or "").strip() or default
    if ".." in chosen or not VALID_MODEL_RE.match(chosen):
        raise ValueError("Tên model AI không hợp lệ.")
    return chosen


def validate_telegram_creds(bot_token: str | None, chat_id: str | None) -> tuple[str, str]:
    """Validate Telegram Bot Token and Chat ID format before outbound request."""
    tok = (bot_token or "").strip()
    cid = (chat_id or "").strip()
    if not tok or not cid:
        raise ValueError("Vui lòng nhập đầy đủ Telegram Bot Token và Chat ID.")
    if not VALID_TG_TOKEN_RE.match(tok):
        raise ValueError("Định dạng Telegram Bot Token không hợp lệ (dạng 123456789:ABCdef...).")
    if not VALID_TG_CHAT_ID_RE.match(cid):
        raise ValueError("Định dạng Telegram Chat ID không hợp lệ (số nguyên hoặc @username).")
    return tok, cid


def _is_public_ip(ip_str: str) -> bool:
    try:
        ip_obj = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    # Handle IPv4-mapped IPv6 addresses like ::ffff:127.0.0.1
    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped is not None:
        ip_obj = ip_obj.ipv4_mapped
    return bool(ip_obj.is_global and not (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
    ))


def validate_outbound_https_url(raw_url: str | None, default_url: str = "") -> str:
    """
    Validate user-supplied outbound API base URL against SSRF:
      - Requires https:// scheme
      - Rejects credentials in URL
      - Rejects localhost / internal / metadata hostnames
      - Resolves DNS and ensures ALL target IPs are globally routable public IPs
    """
    url = (raw_url or "").strip() or default_url
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        raise ValueError("Base URL phải sử dụng giao thức HTTPS an toàn (https://...).")
    if parsed.username or parsed.password:
        raise ValueError("Base URL không được chứa thông tin đăng nhập trực tiếp.")
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise ValueError("Base URL thiếu tên miền hợp lệ.")
    if host in BLOCKED_HOSTNAMES or host.endswith((".local", ".internal", ".localhost", ".lan", ".home")):
        raise ValueError("Base URL không được trỏ tới địa chỉ nội bộ hoặc metadata cloud.")

    # Direct IP literal check first
    try:
        ipaddress.ip_address(host)
        if not _is_public_ip(host):
            raise ValueError("Base URL không được trỏ tới địa chỉ IP nội bộ/loopback.")
        return url
    except ValueError as exc:
        if "Base URL không được" in str(exc):
            raise

    # DNS resolution check (defends against domains pointing to 127.0.0.1 / 169.254.169.254 / RFC1918)
    port = parsed.port or 443
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"Không thể phân giải tên miền Base URL '{host}'.") from exc

    resolved_ips = {info[4][0] for info in infos if info and info[4]}
    if not resolved_ips:
        raise ValueError(f"Tên miền Base URL '{host}' không có địa chỉ IP hợp lệ.")
    for ip_str in resolved_ips:
        if not _is_public_ip(ip_str):
            raise ValueError("Base URL phân giải sang địa chỉ IP nội bộ và đã bị chặn để bảo vệ hệ thống.")

    return url


_FILE_LOCKS_GUARD = threading.Lock()
_FILE_LOCKS: dict[str, threading.Lock] = {}


def _get_file_lock(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _FILE_LOCKS_GUARD:
        lock = _FILE_LOCKS.get(key)
        if lock is None:
            if len(_FILE_LOCKS) > 512:
                _FILE_LOCKS.clear()
            lock = threading.Lock()
            _FILE_LOCKS[key] = lock
        return lock


def atomic_write_json(path: Path, data: Any, indent: int | None = None) -> None:
    """Thread-safe atomic JSON write using a per-file lock and unique temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = _get_file_lock(path)
    with lock:
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(path.parent),
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as tf:
                tmp_path = Path(tf.name)
                json.dump(data, tf, ensure_ascii=False, indent=indent)
                tf.flush()
            for attempt in range(5):
                try:
                    os.replace(tmp_path, path)
                    tmp_path = None
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.02 * (attempt + 1))
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass


def cleanup_old_files(directory: Path, max_age_seconds: int = 86400, pattern: str = "*") -> int:
    """Delete files older than max_age_seconds in directory; returns count removed."""
    removed = 0
    if not directory.exists():
        return 0
    cutoff = time.time() - max_age_seconds
    for entry in directory.glob(pattern):
        try:
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                entry.unlink(missing_ok=True)
                removed += 1
        except OSError:
            pass
    return removed


def build_pooled_session(pool_connections: int = 20, pool_maxsize: int = 20) -> requests.Session:
    """Create a thread-safe requests.Session with connection pooling for Render efficiency."""
    sess = requests.Session()
    adapter = HTTPAdapter(pool_connections=pool_connections, pool_maxsize=pool_maxsize)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    return sess


class SlidingWindowRateLimiter:
    """Bounded in-memory per-key sliding-window rate limiter (Render 512MB safe)."""

    def __init__(self, max_keys: int = 2048) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, deque[float]] = {}
        self._max_keys = max_keys

    def allow(self, key: str, limit: int, window_seconds: float) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds)."""
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            if len(self._buckets) >= self._max_keys and key not in self._buckets:
                # Evict stale keys first to keep memory strictly bounded
                stale = [k for k, q in self._buckets.items() if not q or q[-1] < cutoff]
                for k in stale:
                    del self._buckets[k]
                if len(self._buckets) >= self._max_keys:
                    oldest_key = next(iter(self._buckets))
                    del self._buckets[oldest_key]

            dq = self._buckets.get(key)
            if dq is None:
                dq = deque()
                self._buckets[key] = dq
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if len(dq) >= limit:
                retry_after = max(1, int(window_seconds - (now - dq[0])) + 1)
                return False, retry_after
            dq.append(now)
            return True, 0
