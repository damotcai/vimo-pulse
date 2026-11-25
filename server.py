"""
VĨ MÔ PULSE — Local Web Server & Live API Orchestrator
Serves the Macro Morning Terminal UI and updates live macroeconomic data on every visit.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from macro_fetcher import build_dashboard, fetch_calendar_fast, fetch_watchlist_stocks, get_cached_dashboard

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

_refresh_lock = threading.Lock()
_last_live_ts = 0.0
_latest_payload: dict | None = None


def get_dashboard_payload(mode: str = "live", force: bool = False) -> dict:
    global _latest_payload, _last_live_ts

    # Fast mode: immediately serve in-memory or disk-cached snapshot if present
    if mode == "fast" and not force:
        if _latest_payload is not None:
            return {**_latest_payload, "_cached_fast": True}
        disk = get_cached_dashboard()
        if disk is not None:
            _latest_payload = disk
            return {**disk, "_cached_fast": True}

    with _refresh_lock:
        now = time.time()
        # Coalesce duplicate live calls within 15 seconds unless force=True
        if not force and _latest_payload is not None and (now - _last_live_ts) < 15:
            return {**_latest_payload, "_cached_fast": False}
        data = build_dashboard(force=force)
        _latest_payload = data
        _last_live_ts = time.time()
        return {**data, "_cached_fast": False}


class MacroHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args) -> None:
        # Keep console output clean; log API calls only
        if args and isinstance(args[0], str) and "/api/" in args[0]:
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] {args[0]} -> {args[1] if len(args) > 1 else ''}", flush=True)

    def _send_json(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, file_path: Path) -> None:
        if not file_path.exists() or not file_path.is_file():
            self.send_error(404, "Not Found")
            return
        mime, _ = mimetypes.guess_type(str(file_path))
        if file_path.suffix == ".js":
            mime = "application/javascript; charset=utf-8"
        elif file_path.suffix == ".css":
            mime = "text/css; charset=utf-8"
        elif file_path.suffix == ".html":
            mime = "text/html; charset=utf-8"
        elif not mime:
            mime = "application/octet-stream"

        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/api/health":
            self._send_json({"status": "ok", "ts": time.time()})
            return

        if path == "/api/calendar":
            force = qs.get("force", ["1"])[0] in ("1", "true", "yes")
            try:
                cal = fetch_calendar_fast(force=force)
                # Keep in-memory dashboard payload in sync with latest calendar!
                if _latest_payload is not None and isinstance(cal, dict):
                    _latest_payload["calendar"] = cal
                self._send_json(cal)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if path == "/api/watchlist":
            raw = (qs.get("tickers", [""])[0] or "").strip()
            tickers = [t.strip() for t in raw.split(",") if t.strip()]
            try:
                items = fetch_watchlist_stocks(tickers)
                self._send_json({"tickers": tickers, "items": items, "ts": time.time()})
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if path == "/api/dashboard":
            mode = (qs.get("mode", ["live"])[0] or "live").lower()
            force = qs.get("force", ["0"])[0] in ("1", "true", "yes")
            try:
                payload = get_dashboard_payload(mode=mode, force=force)
                self._send_json(payload)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if path in ("/", "/index.html"):
            self._send_file(STATIC_DIR / "index.html")
            return

        if path.startswith("/static/"):
            rel = path[len("/static/"):].lstrip("/")
            candidate = (STATIC_DIR / rel).resolve()
            if str(candidate).startswith(str(STATIC_DIR.resolve())):
                self._send_file(candidate)
                return

        if path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        self.send_error(404, "Not Found")


def run_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    # Warm up disk cache in memory immediately
    global _latest_payload
    _latest_payload = get_cached_dashboard()

    server = ThreadingHTTPServer((host, port), MacroHandler)
    print("=" * 64, flush=True)
    print("  VĨ MÔ PULSE — Bản tin Kinh tế Vĩ mô Sáng (US & Việt Nam)", flush=True)
    print(f"  Truy cập ngay: http://{host}:{port}", flush=True)
    print("  Dữ liệu tự động cập nhật mỗi lần bạn mở website.", flush=True)
    print("  Nhấn Ctrl+C để dừng máy chủ.", flush=True)
    print("=" * 64, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nĐang tắt máy chủ VĨ MÔ PULSE...", flush=True)
        server.server_close()


if __name__ == "__main__":
    default_host = os.environ.get("HOST") or ("0.0.0.0" if os.environ.get("PORT") or os.environ.get("RENDER") else "127.0.0.1")
    parser = argparse.ArgumentParser(description="VĨ MÔ PULSE Macro Terminal Server")
    parser.add_argument("--host", default=default_host)
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    args = parser.parse_args()
    run_server(host=args.host, port=args.port)
