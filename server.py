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

from bctc_macro_engine import (
    DEFAULT_TICKERS,
    REPRESENTATIVE_UNIVERSE,
    analyze_company_with_macro,
    build_fused_portfolio_strategy,
    build_statement_table,
    ensure_default_universe_loaded,
    export_to_excel,
    extract_macro_context,
    load_company_bundle,
    run_fused_ai_analysis,
    scrape_company,
    scrape_universe,
)
from macro_fetcher import build_dashboard, fetch_calendar_fast, fetch_watchlist_stocks, get_cached_dashboard
from wyckoff_engine import analyze_symbol_wyckoff, build_wyckoff_radar
from fusion_engine import (
    PEER_PRESETS,
    build_morning_brief,
    build_peer_comparison,
    build_triple_fusion_matrix,
    build_vn30_wyckoff_scanner,
    evaluate_portfolio_positions,
    send_telegram_alert,
)
from security_utils import SlidingWindowRateLimiter, get_logger, validate_ticker

logger = get_logger("server")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
STATIC_ROOT_RESOLVED = STATIC_DIR.resolve()

_refresh_lock = threading.Lock()
_last_live_ts = 0.0
_latest_payload: dict | None = None
FAST_MAX_AGE_SECONDS = 4 * 3600  # Never flash cached dashboard older than 4 hours!

MAX_BODY_BYTES = 262_144  # 256 KB max JSON request body (prevents OOM DoS on 512MB Render)
VALID_SECTIONS = {"INCOME_STATEMENT", "BALANCE_SHEET", "CASH_FLOW", "NOTE"}
VALID_SCENARIOS = {"live", "dovish", "shock"}

_rate_limiter = SlidingWindowRateLimiter(max_keys=2048)

_EXTRA_ALLOWED_ORIGINS = {
    o.strip().rstrip("/")
    for o in (os.environ.get("ALLOWED_ORIGINS") or "").split(",")
    if o.strip()
}


class PayloadTooLargeError(Exception):
    pass


class InvalidJsonError(ValueError):
    pass


class HardenedThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _is_fresh_snapshot(payload: dict | None, max_age: float = FAST_MAX_AGE_SECONDS) -> bool:
    if not payload or not isinstance(payload, dict):
        return False
    ts = (payload.get("meta") or {}).get("updated_ts")
    if not ts:
        return False
    return (time.time() - float(ts)) <= max_age


def get_dashboard_payload(mode: str = "live", force: bool = False) -> dict:
    global _latest_payload, _last_live_ts

    # Fast mode: only serve in-memory or disk-cached snapshot if it is genuinely FRESH (<= 4h)
    if mode == "fast" and not force:
        if _is_fresh_snapshot(_latest_payload):
            return {**_latest_payload, "_cached_fast": True}
        disk = get_cached_dashboard()
        if _is_fresh_snapshot(disk):
            _latest_payload = disk
            return {**disk, "_cached_fast": True}
        return {"_stale_skipped": True, "_cached_fast": True}

    with _refresh_lock:
        now = time.time()
        # Coalesce duplicate live calls within 15 seconds unless force=True
        if not force and _is_fresh_snapshot(_latest_payload, max_age=180) and (now - _last_live_ts) < 15:
            return {**_latest_payload, "_cached_fast": False}
        data = build_dashboard(force=force)
        _latest_payload = data
        _last_live_ts = time.time()
        return {**data, "_cached_fast": False}


def _get_live_macro_context(scenario: str = "live") -> dict:
    """Lấy bối cảnh vĩ mô hiện tại từ VĨ MÔ PULSE (hoặc giả lập kịch bản dovish / shock để stress-test)."""
    dash = _latest_payload or get_cached_dashboard()
    if dash is None:
        try:
            dash = get_dashboard_payload(mode="live", force=False)
        except Exception:
            dash = None
    ctx = extract_macro_context(dash)
    sc = (scenario or "live").strip().lower()
    if sc == "dovish":
        ctx = {
            **ctx,
            "macro_score": 74,
            "stance_short": "Risk-On · Nới lỏng (Giả lập)",
            "stance_tone": "emerald",
            "regime_name": "Kịch bản Giả lập: Fed Hạ lãi suất & Goldilocks",
            "equity_budget_pct": 58,
            "us_10y": 3.55,
            "fed_bias": "Hạ lãi suất",
            "fed_hike_prob": 0.0,
            "fed_cut_prob": 78.0,
            "vix": 15.0,
            "dxy": 99.5,
            "vn_pmi": 53.0,
            "us_ism_mfg": 52.5,
            "vni_above_ma50": True,
            "vni_trend": "Xu hướng Tăng",
            "foreign_5d_bn": 1800.0,
            "foreign_20d_bn": 4500.0,
            "hose_liq_bn": 22500.0,
        }
    elif sc == "shock":
        ctx = {
            **ctx,
            "macro_score": 28,
            "stance_short": "Risk-Off · Sốc Lãi suất (Giả lập)",
            "stance_tone": "rose",
            "regime_name": "Kịch bản Giả lập: Lạm phát sốc & Fed Thắt chặt",
            "equity_budget_pct": 20,
            "us_10y": 5.45,
            "fed_bias": "Nâng lãi suất",
            "fed_hike_prob": 85.0,
            "fed_cut_prob": 0.0,
            "vix": 38.0,
            "dxy": 107.0,
            "vn_pmi": 48.2,
            "us_ism_mfg": 48.0,
            "vni_above_ma50": False,
            "vni_trend": "Xu hướng Giảm mạnh",
            "foreign_5d_bn": -5500.0,
            "foreign_20d_bn": -12000.0,
            "hose_liq_bn": 7500.0,
        }
    return ctx


class MacroHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        super().setup()
        try:
            self.connection.settimeout(30.0)
        except OSError:
            pass

    def log_message(self, format: str, *args) -> None:
        # Keep console output clean; log API calls only
        if args and isinstance(args[0], str) and "/api/" in args[0]:
            logger.info("%s -> %s", args[0], args[1] if len(args) > 1 else "")

    def _client_ip(self) -> str:
        xff = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        if xff:
            return xff[:64]
        return (self.client_address[0] if self.client_address else "unknown")[:64]

    def _check_rate_limit(self, bucket: str, limit: int, window_seconds: float) -> bool:
        key = f"{bucket}:{self._client_ip()}"
        allowed, retry_after = _rate_limiter.allow(key, limit=limit, window_seconds=window_seconds)
        if not allowed:
            self._send_json(
                {"error": f"Quá nhiều yêu cầu. Vui lòng thử lại sau {retry_after} giây."},
                status=429,
                extra_headers={"Retry-After": str(retry_after)},
            )
            return False
        return True

    def _allowed_cors_origin(self) -> str | None:
        origin = (self.headers.get("Origin") or "").strip().rstrip("/")
        if not origin:
            return None
        parsed = urlparse(origin)
        host = (parsed.hostname or "").lower()
        if origin in _EXTRA_ALLOWED_ORIGINS:
            return origin
        if host in ("127.0.0.1", "localhost", "damotcai.onrender.com"):
            return origin
        req_host = (self.headers.get("Host") or "").split(":")[0].lower()
        if req_host and host == req_host:
            return origin
        return None

    def _send_security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "geolocation=(), microphone=(), camera=()")

    def _read_json_body(self) -> dict:
        raw_len = (self.headers.get("Content-Length") or "0").strip()
        try:
            length = int(raw_len)
        except ValueError as exc:
            raise InvalidJsonError("Header Content-Length không hợp lệ.") from exc
        if length < 0:
            raise InvalidJsonError("Header Content-Length không hợp lệ.")
        if length > MAX_BODY_BYTES:
            raise PayloadTooLargeError(f"Dung lượng yêu cầu vượt giới hạn tối đa ({MAX_BODY_BYTES // 1024}KB).")
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8", errors="strict")
        if not raw.strip():
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise InvalidJsonError("Nội dung JSON không hợp lệ.") from exc
        if not isinstance(parsed, dict):
            raise InvalidJsonError("JSON body phải là một object.")
        return parsed

    def _send_json(self, obj: dict, status: int = 200, extra_headers: dict[str, str] | None = None) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self._send_security_headers()
        cors_origin = self._allowed_cors_origin()
        if cors_origin:
            self.send_header("Access-Control-Allow-Origin", cors_origin)
            self.send_header("Vary", "Origin")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_internal_error(self, context: str, exc: Exception) -> None:
        logger.exception("Unhandled error in %s: %s", context, exc)
        self._send_json({"error": "Lỗi xử lý máy chủ tạm thời. Vui lòng thử lại sau."}, status=500)

    def _send_file(self, file_path: Path, download_name: str = "", override_mime: str = "") -> None:
        if not file_path.exists() or not file_path.is_file():
            self.send_error(404, "Not Found")
            return
        mime = override_mime
        if not mime:
            mime, _ = mimetypes.guess_type(str(file_path))
            if file_path.suffix == ".js":
                mime = "application/javascript; charset=utf-8"
            elif file_path.suffix == ".css":
                mime = "text/css; charset=utf-8"
            elif file_path.suffix == ".html":
                mime = "text/html; charset=utf-8"
            elif file_path.suffix == ".xlsx":
                mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            elif not mime:
                mime = "application/octet-stream"

        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self._send_security_headers()
        if download_name:
            safe_name = "".join(ch for ch in download_name if ch.isalnum() or ch in "._-")
            self.send_header("Content-Disposition", f'attachment; filename="{safe_name}"')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        global _latest_payload
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
                # Keep in-memory dashboard payload in sync with latest calendar (thread-safe copy swap)
                if isinstance(cal, dict):
                    with _refresh_lock:
                        if _latest_payload is not None:
                            _latest_payload = {**_latest_payload, "calendar": cal}
                self._send_json(cal)
            except Exception as exc:
                self._send_internal_error("GET /api/calendar", exc)
            return

        if path == "/api/watchlist":
            raw = (qs.get("tickers", [""])[0] or "").strip()
            tickers = []
            for t in raw.split(","):
                try:
                    tickers.append(validate_ticker(t))
                except ValueError:
                    pass
            tickers = tickers[:20]
            try:
                items = fetch_watchlist_stocks(tickers)
                self._send_json({"tickers": tickers, "items": items, "ts": time.time()})
            except Exception as exc:
                self._send_internal_error("GET /api/watchlist", exc)
            return

        if path == "/api/dashboard":
            mode = (qs.get("mode", ["live"])[0] or "live").lower()
            force = qs.get("force", ["0"])[0] in ("1", "true", "yes")
            try:
                payload = get_dashboard_payload(mode=mode, force=force)
                self._send_json(payload)
            except Exception as exc:
                self._send_internal_error("GET /api/dashboard", exc)
            return

        # --- BCTC × Macro Fusion API Endpoints ---
        if path == "/api/bctc/portfolio":
            try:
                scenario = (qs.get("scenario", ["live"])[0] or "live").lower()
                if scenario not in VALID_SCENARIOS:
                    scenario = "live"
                macro_ctx = _get_live_macro_context(scenario=scenario)
                cached = ensure_default_universe_loaded()
                analyses = []
                for c in cached:
                    b = load_company_bundle(c["ticker"])
                    if b:
                        analyses.append(analyze_company_with_macro(b, macro_ctx=macro_ctx))
                out = build_fused_portfolio_strategy(analyses, macro_ctx)
                out["cached_count"] = len(cached)
                out["default_tickers"] = DEFAULT_TICKERS
                out["scenario"] = scenario
                self._send_json(out)
            except Exception as exc:
                self._send_internal_error("GET /api/bctc/portfolio", exc)
            return

        if path.startswith("/api/bctc/analyze/"):
            raw_ticker = path.split("/api/bctc/analyze/", 1)[1].strip("/")
            try:
                ticker = validate_ticker(raw_ticker)
                bundle = load_company_bundle(ticker)
                if not bundle:
                    bundle = scrape_company(ticker, save=True)
                scenario = (qs.get("scenario", ["live"])[0] or "live").lower()
                if scenario not in VALID_SCENARIOS:
                    scenario = "live"
                macro_ctx = _get_live_macro_context(scenario=scenario)
                custom = {}
                for k in ("wacc", "growth_5y", "terminal_growth", "target_roe", "target_pe"):
                    if k in qs and qs[k][0] != "":
                        try:
                            custom[k] = float(qs[k][0])
                        except ValueError:
                            pass
                res = analyze_company_with_macro(bundle, macro_ctx=macro_ctx, custom_assumptions=custom or None)
                res["scenario"] = scenario
                self._send_json(res)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
            except RuntimeError as exc:
                self._send_json({"error": str(exc)}, status=404 if "Không tìm thấy" in str(exc) else 502)
            except Exception as exc:
                self._send_internal_error("GET /api/bctc/analyze", exc)
            return

        if path.startswith("/api/bctc/statements/"):
            raw_ticker = path.split("/api/bctc/statements/", 1)[1].strip("/")
            try:
                ticker = validate_ticker(raw_ticker)
                bundle = load_company_bundle(ticker)
                if not bundle:
                    bundle = scrape_company(ticker, save=True)
                section = (qs.get("section", ["INCOME_STATEMENT"])[0] or "INCOME_STATEMENT").upper()
                if section not in VALID_SECTIONS:
                    section = "INCOME_STATEMENT"
                period = (qs.get("period", ["years"])[0] or "years").lower()
                if period not in ("years", "quarters"):
                    period = "years"
                try:
                    limit = max(1, min(40, int(qs.get("limit", ["8"])[0] or 8)))
                except ValueError:
                    limit = 8
                self._send_json(build_statement_table(bundle, section=section, period=period, limit=limit))
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
            except RuntimeError as exc:
                self._send_json({"error": str(exc)}, status=404 if "Không tìm thấy" in str(exc) else 502)
            except Exception as exc:
                self._send_internal_error("GET /api/bctc/statements", exc)
            return

        if path.startswith("/api/bctc/export/"):
            raw_ticker = path.split("/api/bctc/export/", 1)[1].strip("/")
            period = (qs.get("period", ["years"])[0] or "years").lower()
            if period not in ("years", "quarters"):
                period = "years"
            try:
                ticker = validate_ticker(raw_ticker)
                if not load_company_bundle(ticker):
                    scrape_company(ticker, save=True)
                out_path, mime = export_to_excel(ticker, period=period)
                self._send_file(out_path, download_name=out_path.name, override_mime=mime)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
            except FileNotFoundError as exc:
                self._send_json({"error": str(exc)}, status=404)
            except Exception as exc:
                self._send_internal_error("GET /api/bctc/export", exc)
            return

        # --- Wyckoff VSA × Macro API Endpoints ---
        if path == "/api/wyckoff/radar":
            try:
                dash = _latest_payload or get_cached_dashboard() or get_dashboard_payload(mode="live", force=False)
                ensure_default_universe_loaded()
                self._send_json(build_wyckoff_radar(dash))
            except Exception as exc:
                self._send_internal_error("GET /api/wyckoff/radar", exc)
            return

        if path == "/api/wyckoff/vn30":
            try:
                force = qs.get("force", ["0"])[0] in ("1", "true", "yes")
                if force and not self._check_rate_limit("vn30_force", limit=6, window_seconds=60.0):
                    return
                dash = _latest_payload or get_cached_dashboard() or get_dashboard_payload(mode="live", force=False)
                self._send_json(build_vn30_wyckoff_scanner(dash, force=force))
            except Exception as exc:
                self._send_internal_error("GET /api/wyckoff/vn30", exc)
            return

        if path.startswith("/api/wyckoff/analyze/"):
            raw_symbol = path.split("/api/wyckoff/analyze/", 1)[1].strip("/")
            try:
                symbol = validate_ticker(raw_symbol)
                dash = _latest_payload or get_cached_dashboard() or get_dashboard_payload(mode="live", force=False)
                res = analyze_symbol_wyckoff(symbol, dash)
                self._send_json(res)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=404 if "Không lấy được" in str(exc) else 400)
            except Exception as exc:
                self._send_internal_error("GET /api/wyckoff/analyze", exc)
            return

        # --- v8 Super-Fusion 3-in-1, Peer Comparison & Morning Brief Endpoints ---
        if path == "/api/fusion/matrix":
            try:
                scenario = (qs.get("scenario", ["live"])[0] or "live").lower()
                if scenario not in VALID_SCENARIOS:
                    scenario = "live"
                force = qs.get("force", ["0"])[0] in ("1", "true", "yes")
                dash = _latest_payload or get_cached_dashboard() or get_dashboard_payload(mode="live", force=False)
                macro_ctx = _get_live_macro_context(scenario=scenario)
                self._send_json(build_triple_fusion_matrix(dash, macro_ctx, scenario=scenario, force=force))
            except Exception as exc:
                self._send_internal_error("GET /api/fusion/matrix", exc)
            return

        if path == "/api/cio/vn30-conviction":
            try:
                scenario = (qs.get("scenario", ["live"])[0] or "live").lower()
                if scenario not in VALID_SCENARIOS:
                    scenario = "live"
                force = qs.get("force", ["0"])[0] in ("1", "true", "yes")
                dash = _latest_payload or get_cached_dashboard() or get_dashboard_payload(mode="live", force=False)
                macro_ctx = _get_live_macro_context(scenario=scenario)
                mat = build_triple_fusion_matrix(dash, macro_ctx, scenario=scenario, force=force)
                self._send_json(mat.get("cio_diagnosis") or {})
            except Exception as exc:
                self._send_internal_error("GET /api/cio/vn30-conviction", exc)
            return

        if path == "/api/bctc/peers":
            try:
                raw_t = (qs.get("tickers", [""])[0] or "").strip()
                preset_key = (qs.get("preset", [""])[0] or "").strip().upper()
                scenario = (qs.get("scenario", ["live"])[0] or "live").lower()
                if scenario not in VALID_SCENARIOS:
                    scenario = "live"
                if preset_key and preset_key in PEER_PRESETS:
                    tickers = PEER_PRESETS[preset_key]["tickers"]
                else:
                    tickers = [t.strip() for t in raw_t.split(",") if t.strip()]
                if not tickers:
                    tickers = PEER_PRESETS["BANK"]["tickers"]
                dash = _latest_payload or get_cached_dashboard() or get_dashboard_payload(mode="live", force=False)
                macro_ctx = _get_live_macro_context(scenario=scenario)
                self._send_json(build_peer_comparison(tickers, dash, macro_ctx, scenario=scenario))
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
            except Exception as exc:
                self._send_internal_error("GET /api/bctc/peers", exc)
            return

        if path == "/api/briefing/morning":
            try:
                scenario = (qs.get("scenario", ["live"])[0] or "live").lower()
                if scenario not in VALID_SCENARIOS:
                    scenario = "live"
                dash = _latest_payload or get_cached_dashboard() or get_dashboard_payload(mode="live", force=False)
                macro_ctx = _get_live_macro_context(scenario=scenario)
                self._send_json(build_morning_brief(dash, macro_ctx, scenario=scenario))
            except Exception as exc:
                self._send_internal_error("GET /api/briefing/morning", exc)
            return

        if path == "/manifest.json":
            self._send_file(STATIC_DIR / "manifest.json", override_mime="application/manifest+json; charset=utf-8")
            return

        if path == "/sw.js":
            self._send_file(STATIC_DIR / "sw.js", override_mime="application/javascript; charset=utf-8")
            return

        if path in ("/", "/index.html"):
            self._send_file(STATIC_DIR / "index.html")
            return

        if path.startswith("/static/"):
            rel = path[len("/static/"):].lstrip("/")
            candidate = (STATIC_DIR / rel).resolve()
            if candidate.is_relative_to(STATIC_ROOT_RESOLVED):
                self._send_file(candidate)
                return

        if path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        self.send_error(404, "Not Found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            body = self._read_json_body()

            if path == "/api/bctc/scrape":
                if not self._check_rate_limit("bctc_scrape", limit=10, window_seconds=60.0):
                    return
                tickers = body.get("tickers")
                if tickers and isinstance(tickers, list):
                    valid_list = []
                    for t in tickers[:12]:
                        valid_list.append(validate_ticker(str(t)))
                    res = scrape_universe(valid_list)
                    self._send_json(res)
                    return
                ticker = validate_ticker(body.get("ticker"))
                bundle = scrape_company(ticker, save=True)
                scenario = (body.get("scenario") or "live").lower()
                if scenario not in VALID_SCENARIOS:
                    scenario = "live"
                macro_ctx = _get_live_macro_context(scenario=scenario)
                analysis = analyze_company_with_macro(bundle, macro_ctx=macro_ctx)
                self._send_json({"status": "ok", "ticker": ticker, "analysis": analysis})
                return

            if path == "/api/bctc/ai-analyze":
                if not self._check_rate_limit("bctc_ai", limit=12, window_seconds=60.0):
                    return
                ticker = validate_ticker(body.get("ticker"))
                bundle = load_company_bundle(ticker) or scrape_company(ticker, save=True)
                scenario = (body.get("scenario") or "live").lower()
                if scenario not in VALID_SCENARIOS:
                    scenario = "live"
                macro_ctx = _get_live_macro_context(scenario=scenario)
                analysis = analyze_company_with_macro(bundle, macro_ctx=macro_ctx)
                out = run_fused_ai_analysis(
                    analysis=analysis,
                    provider=body.get("provider", "offline"),
                    api_key=body.get("api_key", ""),
                    model=body.get("model", ""),
                    base_url=body.get("base_url", ""),
                    question=body.get("question"),
                )
                self._send_json(out)
                return

            if path == "/api/portfolio/evaluate":
                positions = body.get("positions") or []
                if not isinstance(positions, list) or len(positions) > 100:
                    raise ValueError("Danh sách vị thế danh mục không hợp lệ (tối đa 100 mã).")
                scenario = (body.get("scenario") or "live").lower()
                if scenario not in VALID_SCENARIOS:
                    scenario = "live"
                dash = _latest_payload or get_cached_dashboard() or get_dashboard_payload(mode="live", force=False)
                macro_ctx = _get_live_macro_context(scenario=scenario)
                self._send_json(evaluate_portfolio_positions(positions, dash, macro_ctx))
                return

            if path == "/api/briefing/morning":
                positions = body.get("positions") or []
                if not isinstance(positions, list) or len(positions) > 100:
                    raise ValueError("Danh sách vị thế danh mục không hợp lệ.")
                scenario = (body.get("scenario") or "live").lower()
                if scenario not in VALID_SCENARIOS:
                    scenario = "live"
                dash = _latest_payload or get_cached_dashboard() or get_dashboard_payload(mode="live", force=False)
                macro_ctx = _get_live_macro_context(scenario=scenario)
                self._send_json(build_morning_brief(dash, macro_ctx, scenario=scenario, positions=positions))
                return

            if path == "/api/alerts/telegram":
                if not self._check_rate_limit("telegram_alert", limit=5, window_seconds=60.0):
                    return
                res = send_telegram_alert(
                    bot_token=body.get("bot_token", ""),
                    chat_id=body.get("chat_id", ""),
                    text=body.get("text", ""),
                )
                self._send_json(res)
                return

            self._send_json({"error": "Endpoint not found"}, status=404)
        except PayloadTooLargeError as exc:
            self.close_connection = True
            self._send_json({"error": str(exc)}, status=413, extra_headers={"Connection": "close"})
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except RuntimeError as exc:
            self._send_json({"error": str(exc)}, status=502)
        except Exception as exc:
            self._send_internal_error(f"POST {path}", exc)


def run_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    # Only warm up disk cache if it is genuinely fresh (<= 4 hours); never serve stale days-old cache!
    global _latest_payload
    disk = get_cached_dashboard()
    if _is_fresh_snapshot(disk):
        _latest_payload = disk
    else:
        _latest_payload = None

    # Kick off immediate background live data sync on server startup (critical for Render cold starts!)
    def _boot_warmup() -> None:
        try:
            get_dashboard_payload(mode="live", force=not _is_fresh_snapshot(_latest_payload, max_age=600))
        except Exception as exc:
            logger.warning("Startup dashboard warmup failed: %s", exc)

    threading.Thread(target=_boot_warmup, daemon=True).start()

    server = HardenedThreadingHTTPServer((host, port), MacroHandler)
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
