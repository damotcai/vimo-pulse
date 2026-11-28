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

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

_refresh_lock = threading.Lock()
_last_live_ts = 0.0
_latest_payload: dict | None = None
FAST_MAX_AGE_SECONDS = 4 * 3600  # Never flash cached dashboard older than 4 hours!


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

    def log_message(self, format: str, *args) -> None:
        # Keep console output clean; log API calls only
        if args and isinstance(args[0], str) and "/api/" in args[0]:
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] {args[0]} -> {args[1] if len(args) > 1 else ''}", flush=True)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw.strip() else {}

    def _send_json(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

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
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
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

        # --- BCTC × Macro Fusion API Endpoints ---
        if path == "/api/bctc/portfolio":
            try:
                scenario = (qs.get("scenario", ["live"])[0] or "live").lower()
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
                self._send_json({"error": str(exc)}, status=500)
            return

        if path.startswith("/api/bctc/analyze/"):
            ticker = path.split("/api/bctc/analyze/", 1)[1].strip("/").upper()
            try:
                bundle = load_company_bundle(ticker)
                if not bundle:
                    bundle = scrape_company(ticker, save=True)
                scenario = (qs.get("scenario", ["live"])[0] or "live").lower()
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
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=404 if "Không tìm thấy" in str(exc) else 500)
            return

        if path.startswith("/api/bctc/statements/"):
            ticker = path.split("/api/bctc/statements/", 1)[1].strip("/").upper()
            try:
                bundle = load_company_bundle(ticker)
                if not bundle:
                    bundle = scrape_company(ticker, save=True)
                section = (qs.get("section", ["INCOME_STATEMENT"])[0] or "INCOME_STATEMENT").upper()
                period = (qs.get("period", ["years"])[0] or "years").lower()
                limit = int(qs.get("limit", ["8"])[0] or 8)
                self._send_json(build_statement_table(bundle, section=section, period=period, limit=limit))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if path.startswith("/api/bctc/export/"):
            ticker = path.split("/api/bctc/export/", 1)[1].strip("/").upper()
            period = (qs.get("period", ["years"])[0] or "years").lower()
            try:
                if not load_company_bundle(ticker):
                    scrape_company(ticker, save=True)
                out_path, mime = export_to_excel(ticker, period=period)
                self._send_file(out_path, download_name=out_path.name, override_mime=mime)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        # --- Wyckoff VSA × Macro API Endpoints ---
        if path == "/api/wyckoff/radar":
            try:
                dash = _latest_payload or get_cached_dashboard() or get_dashboard_payload(mode="live", force=False)
                ensure_default_universe_loaded()
                self._send_json(build_wyckoff_radar(dash))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if path == "/api/wyckoff/vn30":
            try:
                force = qs.get("force", ["0"])[0] in ("1", "true", "yes")
                dash = _latest_payload or get_cached_dashboard() or get_dashboard_payload(mode="live", force=False)
                self._send_json(build_vn30_wyckoff_scanner(dash, force=force))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if path.startswith("/api/wyckoff/analyze/"):
            symbol = path.split("/api/wyckoff/analyze/", 1)[1].strip("/").upper()
            try:
                dash = _latest_payload or get_cached_dashboard() or get_dashboard_payload(mode="live", force=False)
                res = analyze_symbol_wyckoff(symbol, dash)
                self._send_json(res)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=404 if "Không lấy được" in str(exc) else 500)
            return

        # --- v8 Super-Fusion 3-in-1, Peer Comparison & Morning Brief Endpoints ---
        if path == "/api/fusion/matrix":
            try:
                scenario = (qs.get("scenario", ["live"])[0] or "live").lower()
                force = qs.get("force", ["0"])[0] in ("1", "true", "yes")
                dash = _latest_payload or get_cached_dashboard() or get_dashboard_payload(mode="live", force=False)
                macro_ctx = _get_live_macro_context(scenario=scenario)
                self._send_json(build_triple_fusion_matrix(dash, macro_ctx, scenario=scenario, force=force))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if path == "/api/bctc/peers":
            try:
                raw_t = (qs.get("tickers", [""])[0] or "").strip()
                preset_key = (qs.get("preset", [""])[0] or "").strip().upper()
                scenario = (qs.get("scenario", ["live"])[0] or "live").lower()
                if preset_key and preset_key in PEER_PRESETS:
                    tickers = PEER_PRESETS[preset_key]["tickers"]
                else:
                    tickers = [t.strip() for t in raw_t.split(",") if t.strip()]
                if not tickers:
                    tickers = PEER_PRESETS["BANK"]["tickers"]
                dash = _latest_payload or get_cached_dashboard() or get_dashboard_payload(mode="live", force=False)
                macro_ctx = _get_live_macro_context(scenario=scenario)
                self._send_json(build_peer_comparison(tickers, dash, macro_ctx, scenario=scenario))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400 if "ít nhất" in str(exc) else 500)
            return

        if path == "/api/briefing/morning":
            try:
                scenario = (qs.get("scenario", ["live"])[0] or "live").lower()
                dash = _latest_payload or get_cached_dashboard() or get_dashboard_payload(mode="live", force=False)
                macro_ctx = _get_live_macro_context(scenario=scenario)
                self._send_json(build_morning_brief(dash, macro_ctx, scenario=scenario))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
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
            if str(candidate).startswith(str(STATIC_DIR.resolve())):
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
                tickers = body.get("tickers")
                if tickers and isinstance(tickers, list):
                    res = scrape_universe([str(t).strip().upper() for t in tickers if str(t).strip()])
                    self._send_json(res)
                    return
                ticker = (body.get("ticker") or "").strip().upper()
                if not ticker:
                    self._send_json({"error": "Vui lòng nhập mã cổ phiếu hợp lệ (VD: MBB, TCB, GAS...)."}, status=400)
                    return
                bundle = scrape_company(ticker, save=True)
                macro_ctx = _get_live_macro_context(scenario=body.get("scenario", "live"))
                analysis = analyze_company_with_macro(bundle, macro_ctx=macro_ctx)
                self._send_json({"status": "ok", "ticker": ticker, "analysis": analysis})
                return

            if path == "/api/bctc/ai-analyze":
                ticker = (body.get("ticker") or "").strip().upper()
                if not ticker:
                    self._send_json({"error": "Thiếu mã cổ phiếu."}, status=400)
                    return
                bundle = load_company_bundle(ticker) or scrape_company(ticker, save=True)
                macro_ctx = _get_live_macro_context(scenario=body.get("scenario", "live"))
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
                scenario = (body.get("scenario") or "live").lower()
                dash = _latest_payload or get_cached_dashboard() or get_dashboard_payload(mode="live", force=False)
                macro_ctx = _get_live_macro_context(scenario=scenario)
                self._send_json(evaluate_portfolio_positions(positions, dash, macro_ctx))
                return

            if path == "/api/briefing/morning":
                positions = body.get("positions") or []
                scenario = (body.get("scenario") or "live").lower()
                dash = _latest_payload or get_cached_dashboard() or get_dashboard_payload(mode="live", force=False)
                macro_ctx = _get_live_macro_context(scenario=scenario)
                self._send_json(build_morning_brief(dash, macro_ctx, scenario=scenario, positions=positions))
                return

            if path == "/api/alerts/telegram":
                res = send_telegram_alert(
                    bot_token=body.get("bot_token", ""),
                    chat_id=body.get("chat_id", ""),
                    text=body.get("text", ""),
                )
                self._send_json(res)
                return

            self._send_json({"error": "Endpoint not found"}, status=404)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=500)


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
        except Exception:
            pass

    threading.Thread(target=_boot_warmup, daemon=True).start()

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
