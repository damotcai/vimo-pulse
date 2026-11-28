"""Integration & unit tests for server.py HTTP hardening and core financial engines."""

from __future__ import annotations

import http.client
import json
import threading
import unittest
from unittest.mock import patch

import requests

import server
from bctc_macro_engine import extract_macro_context, run_fused_ai_analysis
from fusion_engine import _classify_triple_convergence, build_cio_market_diagnosis, send_telegram_alert
from wyckoff_engine import analyze_wyckoff


def _make_synthetic_bars(n: int = 120, start_price: float = 50000.0) -> list[dict]:
    bars = []
    px = start_price
    for i in range(n):
        # Mild accumulation range then gentle markup
        delta = 120.0 if i > 85 else ((i % 7) - 3) * 140.0
        o = max(1000.0, px)
        c = max(1000.0, o + delta)
        h = max(o, c) + 250.0
        l = max(500.0, min(o, c) - 250.0)
        v = 1_500_000.0 if i > 85 else 900_000.0
        month = 1 + (i // 28) % 12
        day = 1 + (i % 28)
        bars.append({
            "date": f"2026-{month:02d}-{day:02d}",
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
            "volume": v,
        })
        px = c
    return bars


class TestCoreEngines(unittest.TestCase):
    def test_extract_macro_context_flags_degraded_when_missing(self) -> None:
        ctx = extract_macro_context(None)
        self.assertFalse(ctx["available"])
        self.assertTrue(ctx["degraded"])
        self.assertEqual(ctx["macro_score"], 55)

    def test_wyckoff_engine_synthetic_bars(self) -> None:
        bars = _make_synthetic_bars(120)
        ctx = extract_macro_context(None)
        res = analyze_wyckoff(bars, symbol="VCB", name="Vietcombank", macro_ctx=ctx, is_index=False)
        self.assertEqual(res["symbol"], "VCB")
        self.assertIn(res["stage"]["code"], ("ACCUMULATION", "MARKUP", "DISTRIBUTION", "MARKDOWN"))
        self.assertGreaterEqual(res["scores"]["wyckoff"], 0)
        self.assertLessEqual(res["scores"]["wyckoff"], 100)

    def test_triple_fusion_classification(self) -> None:
        diamond = _classify_triple_convergence(
            triple_score=74.0, bctc_score=70.0, macro_fit=72.0, upside_pct=15.0,
            wy_stage="ACCUMULATION", wy_score=72.0, macro_score=65.0, latest_ev_kind="bullish",
        )
        self.assertEqual(diamond["tier_code"], "DIAMOND")

    def test_cio_diagnosis_detects_index_distortion_and_picks(self) -> None:
        dash = {
            "vn_market": {
                "indices": {"VNINDEX": {"close": 1650.0, "ytd_pct": -7.0}, "VN30": {"close": 1750.0, "ytd_pct": -13.5}},
                "flow": {"foreign_5d_bn": -6000.0, "foreign_20d_bn": -11000.0},
            },
            "vn_money_fx": {
                "interbank": {"latest": {"on": 6.1, "w1": 6.2}},
                "fx_tiers": {"latest": {"spread_free_bank": 530, "spread_free_bank_pct": 2.04}},
            },
            "strategy": {"sector_playbook": [{"code": "VNFIN", "above_ma50": False}, {"code": "VNIT", "above_ma50": False}]},
        }
        macro_ctx = {"us_cpi": 3.7, "us_core_pce": 3.2, "us_10y": 4.4, "dxy": 104.2, "vix": 21.0, "foreign_5d_bn": -6000.0}
        rows = [
            {"ticker": "SAB", "company_name": "Sabeco", "sector_vi": "Tiêu dùng", "tier_code": "DIAMOND", "triple_score": 76.5, "bctc_score": 72.1, "pure_bctc_score": 76.0, "wyckoff_score": 92.6, "wyckoff_stage": "ACCUMULATION", "wyckoff_stage_label": "Tích lũy", "latest_event": "SPRING", "price": 43600, "fair_value_macro": 48400, "upside_pct": 11.1, "pe": 12.0, "pb": 2.4, "roe": 22.1, "dividend_yield": 11.6, "red_flags_count": 0, "rs_20d": 9.4, "buy_zone_1": 43000, "stop_loss": 40000},
            {"ticker": "VIC", "company_name": "Vingroup", "sector_vi": "BĐS", "tier_code": "AVOID", "triple_score": 59.0, "bctc_score": 49.0, "pure_bctc_score": 54.0, "wyckoff_score": 85.0, "wyckoff_stage": "ACCUMULATION", "wyckoff_stage_label": "Tích lũy", "latest_event": "LPS", "price": 235000, "fair_value_macro": 78000, "upside_pct": -66.6, "pe": 42.4, "pb": 7.7, "roe": 26.0, "dividend_yield": 0.4, "red_flags_count": 5, "rs_20d": 3.5, "buy_zone_1": 0, "stop_loss": 0},
            {"ticker": "MBB", "company_name": "MBBank", "sector_vi": "Ngân hàng", "tier_code": "VALUE_TRAP", "triple_score": 58.7, "bctc_score": 83.3, "pure_bctc_score": 89.8, "wyckoff_score": 32.6, "wyckoff_stage": "MARKDOWN", "wyckoff_stage_label": "Đè giá", "latest_event": "SPRING", "price": 18200, "fair_value_macro": 22880, "upside_pct": 25.7, "pe": 5.6, "pb": 1.1, "roe": 21.2, "dividend_yield": 4.6, "red_flags_count": 0, "rs_20d": -1.9, "buy_zone_1": 18000, "stop_loss": 16500},
            {"ticker": "VCB", "company_name": "Vietcombank", "sector_vi": "Ngân hàng", "tier_code": "VALUE_TRAP", "triple_score": 54.0, "bctc_score": 78.0, "pure_bctc_score": 85.0, "wyckoff_score": 20.0, "wyckoff_stage": "MARKDOWN", "wyckoff_stage_label": "Đè giá", "latest_event": "SPRING", "price": 49200, "fair_value_macro": 53200, "upside_pct": 8.2, "pe": 9.8, "pb": 1.6, "roe": 17.3, "dividend_yield": 1.8, "red_flags_count": 0, "rs_20d": -8.7, "buy_zone_1": 48500, "stop_loss": 46000},
            {"ticker": "FPT", "company_name": "FPT", "sector_vi": "Công nghệ", "tier_code": "VALUE_TRAP", "triple_score": 53.0, "bctc_score": 78.0, "pure_bctc_score": 79.0, "wyckoff_score": 18.0, "wyckoff_stage": "MARKDOWN", "wyckoff_stage_label": "Đè giá", "latest_event": "SPRING", "price": 44600, "fair_value_macro": 74800, "upside_pct": 67.7, "pe": 8.1, "pb": 1.9, "roe": 26.0, "dividend_yield": 4.0, "red_flags_count": 1, "rs_20d": -8.1, "buy_zone_1": 44000, "stop_loss": 40000},
        ]
        diag = build_cio_market_diagnosis(dash, rows, macro_ctx)
        self.assertLess(diag["external_us"]["score"], 50)
        self.assertLess(diag["internal_vn"]["score"], 50)
        self.assertEqual(diag["index_distortion"]["level"], "HIGH")
        self.assertIn("VIC", diag["index_distortion"]["distorter_tickers"])
        self.assertEqual([p["ticker"] for p in diag["vn30_conviction"]["core_buy"]], ["SAB"])
        self.assertIn("MBB", [p["ticker"] for p in diag["vn30_conviction"]["accumulate_watch"]])
        self.assertIn("VIC", [p["ticker"] for p in diag["vn30_conviction"]["avoid_distortion"]])

    def test_telegram_alert_never_leaks_token_on_network_error(self) -> None:
        secret_tok = "987654321:SECRET_TOKEN_SHOULD_NEVER_LEAK_1234567"
        with patch("fusion_engine._HTTP.post", side_effect=requests.ConnectionError(f"Failed url https://api.telegram.org/bot{secret_tok}/sendMessage")):
            with self.assertRaises(RuntimeError) as cm:
                send_telegram_alert(secret_tok, "123456789", "Hello")
            self.assertNotIn(secret_tok, str(cm.exception))

    def test_ai_analysis_blocks_ssrf_and_uses_header_for_gemini(self) -> None:
        dummy_analysis = {
            "ticker": "VCB",
            "company_name": "Vietcombank",
            "sector_vi": "Ngân hàng",
            "report_model": "BANK",
            "latest_report_period": "Q3/2026",
            "market": {"current_price": 62000, "pe": 12.0, "pb": 2.1, "roe": 19.0},
            "growth": {"rev_yoy": 10.5, "npat_yoy": 12.0},
            "valuation": {"fair_value": 72000, "pure_fair_value": 70000, "upside_pct": 16.0},
            "scores": {"total": 75, "pure_bctc": 78, "macro_fit": 70},
            "macro_fusion": {"macro_context": {"macro_score": 55}, "channels": [], "valuation_overlay": {}},
            "sector_metrics": {},
            "piotroski": {"score": 7, "label": "Mạnh"},
            "altman": {"score": None, "label": "N/A"},
            "dupont": {},
            "strengths": ["CASA cao"],
            "strategy": {"verdict": "MUA TÍCH LŨY", "verdict_code": "BUY", "price_plan": {}},
            "red_flags": [],
        }
        with self.assertRaises(ValueError):
            run_fused_ai_analysis(
                analysis=dummy_analysis,
                provider="openai",
                api_key="sk-test-key",
                model="gpt-4o-mini",
                base_url="http://169.254.169.254/latest/meta-data",
            )

        with patch("bctc_macro_engine._HTTP.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {
                "candidates": [{"content": {"parts": [{"text": "OK"}]}}]
            }
            out = run_fused_ai_analysis(
                analysis=dummy_analysis,
                provider="gemini",
                api_key="AIzaFakeKeyForHeaderTest12345",
                model="gemini-2.5-flash",
            )
            self.assertEqual(out["content"], "OK")
            called_url = mock_post.call_args.args[0]
            called_headers = mock_post.call_args.kwargs["headers"]
            self.assertNotIn("AIzaFakeKeyForHeaderTest12345", called_url)
            self.assertEqual(called_headers.get("x-goog-api-key"), "AIzaFakeKeyForHeaderTest12345")


class TestHardenedHTTPServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.httpd = server.HardenedThreadingHTTPServer(("127.0.0.1", 0), server.MacroHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def _request(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        hdrs = headers or {}
        if body is not None and "Content-Type" not in hdrs:
            hdrs["Content-Type"] = "application/json"
        conn.request(method, path, body=body, headers=hdrs)
        resp = conn.getresponse()
        raw = resp.read()
        hdr_map = {k.lower(): v for k, v in resp.getheaders()}
        conn.close()
        return resp.status, hdr_map, raw

    def test_health_and_security_headers(self) -> None:
        status, headers, raw = self._request("GET", "/api/health", headers={"Origin": "https://evil.example.com"})
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("x-content-type-options"), "nosniff")
        self.assertEqual(headers.get("x-frame-options"), "SAMEORIGIN")
        self.assertEqual(headers.get("referrer-policy"), "strict-origin-when-cross-origin")
        # Wildcard CORS must NOT be returned for untrusted cross-site origins
        self.assertIsNone(headers.get("access-control-allow-origin"))
        self.assertEqual(json.loads(raw)["status"], "ok")

    def test_oversized_post_body_returns_413(self) -> None:
        big = b"{" + b"a" * (server.MAX_BODY_BYTES + 100) + b"}"
        status, _, _ = self._request("POST", "/api/alerts/telegram", body=big)
        self.assertEqual(status, 413)

    def test_invalid_json_returns_400(self) -> None:
        status, _, _ = self._request("POST", "/api/alerts/telegram", body=b"{not-json")
        self.assertEqual(status, 400)

    def test_path_traversal_tickers_and_static_blocked(self) -> None:
        for bad_path in (
            "/api/bctc/analyze/..%2F..%2Fserver",
            "/api/bctc/statements/../../etc",
            "/api/bctc/export/..%5Cserver",
            "/api/wyckoff/analyze/../../server",
        ):
            status, _, _ = self._request("GET", bad_path)
            self.assertEqual(status, 400, f"Expected 400 for {bad_path}, got {status}")

        status, _, _ = self._request("GET", "/static/../server.py")
        self.assertEqual(status, 404)

    def test_telegram_rate_limit_and_validation(self) -> None:
        # Reset bucket for test isolation
        server._rate_limiter._buckets.clear()
        payload = json.dumps({"bot_token": "bad", "chat_id": "bad", "text": "hi"}).encode("utf-8")
        statuses = []
        for _ in range(6):
            st, _, _ = self._request("POST", "/api/alerts/telegram", body=payload)
            statuses.append(st)
        self.assertEqual(statuses[:5], [400, 400, 400, 400, 400])
        self.assertEqual(statuses[5], 429)


if __name__ == "__main__":
    unittest.main()
