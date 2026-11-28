"""
VĨ MÔ PULSE v8 — Super-Fusion Engine (Vĩ mô × BCTC × Wyckoff 3-in-1)
Bao gồm:
  1. Siêu Ma trận Hội tụ 3 Trụ cột (Vĩ mô 25% × BCTC 40% × Wyckoff VSA 35%) + lọc Cổ phiếu Kim cương & Bẫy Giá trị
  2. So sánh Đối thủ Cùng Ngành (Peer Comparison) cho 8 nhóm ngành chuẩn & tùy chọn 2-5 mã
  3. Bộ quét Wyckoff VSA toàn rổ VN30 (30 Bluechips) song song kèm Độ rộng thị trường (Market Breadth)
  4. Bộ đánh giá Danh mục Thực tế (Real-time P&L + Cảnh báo Stop-loss Ice / Chốt lời BCTC / Tỷ trọng ngành)
  5. Bộ tạo Báo cáo Sáng CIO (In PDF + Telegram Bot Dispatcher)
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from bctc_macro_engine import (
    DEFAULT_TICKERS,
    REPRESENTATIVE_UNIVERSE,
    analyze_company_with_macro,
    ensure_default_universe_loaded,
    extract_macro_context,
    fetch_price_history,
    list_cached_companies,
    load_company_bundle,
    scrape_company,
)
from macro_fetcher import fetch_watchlist_stocks
from security_utils import build_pooled_session, get_logger, validate_telegram_creds
from wyckoff_engine import analyze_wyckoff

logger = get_logger("fusion_engine")
_HTTP = build_pooled_session(pool_connections=12, pool_maxsize=12)
_FUSION_CACHE_MAX = 16

VN_TZ = timezone(timedelta(hours=7))

VN30_TICKERS: list[str] = [
    "ACB", "BCM", "BID", "BVH", "CTG", "FPT", "GAS", "GVR", "HDB", "HPG",
    "MBB", "MSN", "MWG", "PLX", "POW", "SAB", "SHB", "SSB", "SSI", "STB",
    "TCB", "TPB", "VCB", "VHM", "VIB", "VIC", "VJC", "VNM", "VPB", "VRE",
]

VN30_NAMES: dict[str, str] = {
    "ACB": "Ngân hàng Á Châu (ACB)",
    "BCM": "Becamex IDC",
    "BID": "Ngân hàng BIDV",
    "BVH": "Tập đoàn Bảo Việt",
    "CTG": "Ngân hàng VietinBank",
    "FPT": "Tập đoàn FPT",
    "GAS": "PV Gas",
    "GVR": "Tập đoàn Cao su VN",
    "HDB": "Ngân hàng HDBank",
    "HPG": "Tập đoàn Hòa Phát",
    "MBB": "Ngân hàng Quân đội (MB)",
    "MSN": "Tập đoàn Masan",
    "MWG": "Thế Giới Di Động",
    "PLX": "Petrolimex",
    "POW": "PV Power",
    "SAB": "Sabeco",
    "SHB": "Ngân hàng SHB",
    "SSB": "Ngân hàng SeABank",
    "SSI": "Chứng khoán SSI",
    "STB": "Ngân hàng Sacombank",
    "TCB": "Ngân hàng Techcombank",
    "TPB": "Ngân hàng TPBank",
    "VCB": "Ngân hàng Vietcombank",
    "VHM": "Vinhomes",
    "VIB": "Ngân hàng VIB",
    "VIC": "Tập đoàn Vingroup",
    "VJC": "Vietjet Air",
    "VNM": "Vinamilk",
    "VPB": "Ngân hàng VPBank",
    "VRE": "Vincom Retail",
}

PEER_PRESETS: dict[str, dict[str, Any]] = {
    "BANK": {"label": "Ngân hàng", "tickers": ["VCB", "BID", "CTG", "MBB", "TCB"]},
    "SECURITIES": {"label": "Chứng khoán", "tickers": ["SSI", "VCI", "HCM", "VND"]},
    "STEEL": {"label": "Thép & VLXD", "tickers": ["HPG", "HSG", "NKG"]},
    "RETAIL": {"label": "Bán lẻ", "tickers": ["MWG", "FRT", "PNJ", "DGW"]},
    "REAL_ESTATE": {"label": "Bất động sản", "tickers": ["VHM", "KDH", "NLG", "PDR"]},
    "TECH": {"label": "Công nghệ", "tickers": ["FPT", "CMG", "CTR"]},
    "OIL_GAS": {"label": "Dầu khí", "tickers": ["GAS", "PVD", "PVS", "BSR"]},
    "FERTILIZER": {"label": "Phân bón · Hóa chất", "tickers": ["DPM", "DCM", "DGC"]},
}

_fusion_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_fusion_lock = threading.Lock()

_vn30_cache: tuple[float, dict[str, Any]] | None = None
_vn30_lock = threading.Lock()


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _now_vn_str() -> str:
    return datetime.now(VN_TZ).strftime("%H:%M:%S · %d/%m/%Y")


def _classify_triple_convergence(
    triple_score: float,
    bctc_score: float,
    macro_fit: float,
    upside_pct: float,
    wy_stage: str,
    wy_score: float,
    macro_score: float,
    latest_ev_kind: str,
) -> dict[str, str]:
    """Phân loại cổ phiếu theo Hội tụ 3 Trụ cột: Vĩ mô × BCTC × Wyckoff VSA."""
    if (
        triple_score >= 66.0
        and bctc_score >= 60.0
        and wy_stage in ("ACCUMULATION", "MARKUP")
        and upside_pct >= 0.0
    ):
        return {
            "tier_code": "DIAMOND",
            "tier_label": "💎 Hội tụ Kim cương",
            "tier_tone": "emerald",
            "action": "ƯU TIÊN GIẢI NGÂN TRỌNG TÂM — Đủ 3 trụ: BCTC tốt + Định giá hấp dẫn + Wyckoff dòng tiền vào.",
        }
    if (
        wy_stage in ("ACCUMULATION", "MARKUP")
        and (bctc_score >= 54.0 or upside_pct >= 8.0)
        and triple_score >= 52.0
    ):
        return {
            "tier_code": "ACCUMULATE",
            "tier_label": "🎯 Canh gom Hội tụ",
            "tier_tone": "accent",
            "action": "GOM TỪNG PHẦN (30/40/30%) — Canh nhịp test LPS / Spring giữ hỗ trợ Ice hoặc vùng mua BCTC.",
        }
    if (bctc_score >= 60.0 or upside_pct >= 12.0) and wy_stage in ("DISTRIBUTION", "MARKDOWN"):
        return {
            "tier_code": "VALUE_TRAP",
            "tier_label": "⚠️ Bẫy Giá trị (Chờ đáy KT)",
            "tier_tone": "amber",
            "action": "CHƯA BẮT DAO RƠI — BCTC đẹp/rẻ nhưng Wyckoff đang Phân phối/Đè giá. Chờ tạo SC + AR + ST cạn cung.",
        }
    if wy_stage == "MARKUP" and (bctc_score < 54.0 or upside_pct < 0.0):
        return {
            "tier_code": "MOMENTUM",
            "tier_label": "⚡ Sóng KT (Định giá cao)",
            "tier_tone": "violet",
            "action": "CHỈ GIAO DỊCH NGẮN HẠN — Dòng tiền Wyckoff khỏe nhưng biên an toàn BCTC mỏng, đặt Stop-loss chặt theo MA20.",
        }
    if wy_stage == "ACCUMULATION" and latest_ev_kind == "bull" and macro_score >= 45:
        return {
            "tier_code": "ACCUMULATE",
            "tier_label": "🎯 Theo dõi Tích lũy",
            "tier_tone": "accent",
            "action": "ĐƯA VÀO TẦM NGẮM — Xuất hiện tín hiệu cầu VSA trong vùng nền, chờ bứt phá Creek (SOS).",
        }
    return {
        "tier_code": "AVOID",
        "tier_label": "🛑 Phòng thủ / Đứng ngoài",
        "tier_tone": "rose",
        "action": "ĐỨNG NGOÀI / HẠ TỶ TRỌNG — Thiếu đồng thuận cả cơ bản lẫn dòng tiền tạo lập Wyckoff.",
    }


def _fuse_single_ticker(
    ticker: str,
    macro_ctx: dict[str, Any],
    vni_series: list[dict[str, Any]],
) -> dict[str, Any] | None:
    sym = ticker.upper().strip()
    bundle = load_company_bundle(sym)
    if not bundle:
        return None
    bctc = analyze_company_with_macro(bundle, macro_ctx=macro_ctx)

    prices = bundle.get("prices") or []
    if len(prices) < 40:
        try:
            prices = fetch_price_history(sym, years=1)
        except Exception:
            prices = []

    wy = None
    if len(prices) >= 40:
        try:
            wy = analyze_wyckoff(
                prices,
                symbol=sym,
                name=bctc["company_name"],
                macro_ctx=macro_ctx,
                vni_raw_bars=vni_series,
                is_index=False,
            )
        except Exception:
            wy = None

    m_global = float(macro_ctx.get("macro_score", 50.0))
    b_total = float(bctc["scores"]["total"])
    b_pure = float(bctc["scores"]["pure_bctc"])
    m_fit = float(bctc["scores"]["macro_fit"])
    w_score = float(wy["scores"]["wyckoff"]) if wy else 50.0
    w_stage = wy["stage"]["code"] if wy else "ACCUMULATION"
    w_stage_lbl = wy["stage"]["label"] if wy else "Chưa đủ dữ liệu giá"
    w_sub = wy["stage"]["sub_phase"] if wy else "—"
    w_tone = wy["stage"]["tone"] if wy else "accent"
    w_tr = wy["trading_range"] if wy else {}
    w_sd = wy["laws"]["supply_demand"] if wy else {}
    le = wy["laws"]["effort_result"].get("latest_event") if wy else None

    # 3-Pillar Score: 25% Macro (Fit + Global) + 40% BCTC Macro-Adjusted + 35% Wyckoff VSA
    macro_pillar = round(0.60 * m_fit + 0.40 * m_global, 1)
    triple_score = round(_clamp(0.25 * macro_pillar + 0.40 * b_total + 0.35 * w_score, 8.0, 97.0), 1)

    upside_pct = float(bctc["valuation"]["upside_pct"])
    tier = _classify_triple_convergence(
        triple_score=triple_score,
        bctc_score=b_total,
        macro_fit=m_fit,
        upside_pct=upside_pct,
        wy_stage=w_stage,
        wy_score=w_score,
        macro_score=m_global,
        latest_ev_kind=le["kind"] if le else "neutral",
    )

    pp = bctc["strategy"]["price_plan"]
    ice_val = w_tr.get("ice_support")
    creek_val = w_tr.get("creek_resistance")
    stop_bctc = float(pp.get("review_stop_level") or 0)
    stop_wy = round(float(ice_val) * 0.97, -2) if ice_val else 0
    # Use the tighter (higher) protective stop-loss if both exist and below current price
    cur_px = float(bctc["market"]["current_price"] or 0)
    valid_stops = [s for s in (stop_bctc, stop_wy) if 0 < s < cur_px] if cur_px > 0 else []
    smart_stop = max(valid_stops) if valid_stops else (stop_bctc or stop_wy)

    return {
        "ticker": sym,
        "company_name": bctc["company_name"],
        "sector_vi": bctc["sector_vi"],
        "hose_sector": bctc["hose_sector"],
        "report_model": bctc["report_model"],
        "latest_period": bctc["latest_report_period"],
        "price": cur_px,
        "fair_value_macro": bctc["valuation"]["fair_value"],
        "fair_value_pure": bctc["valuation"]["pure_fair_value"],
        "upside_pct": upside_pct,
        "pe": bctc["market"]["pe"],
        "pb": bctc["market"]["pb"],
        "roe": bctc["market"]["roe"],
        "dividend_yield": bctc["market"]["dividend_yield"],
        "npat_yoy_q": bctc["growth"]["npat_yoy_latest_q"],
        "red_flags_count": len(bctc["red_flags"]),
        "bctc_score": b_total,
        "pure_bctc_score": b_pure,
        "macro_fit": m_fit,
        "macro_pillar": macro_pillar,
        "bctc_verdict": bctc["strategy"]["verdict"],
        "bctc_verdict_code": bctc["strategy"]["verdict_code"],
        "wyckoff_score": w_score,
        "wyckoff_stage": w_stage,
        "wyckoff_stage_label": w_stage_lbl,
        "wyckoff_sub_phase": w_sub,
        "wyckoff_tone": w_tone,
        "pos_in_tr": w_tr.get("position_pct"),
        "ice": ice_val,
        "creek": creek_val,
        "rs_20d": w_sd.get("rs_20d"),
        "ud_vol_20": w_sd.get("up_down_vol_20"),
        "latest_event": f"{le['code']} ({le['date'][5:]})" if le else "—",
        "latest_event_kind": le["kind"] if le else "neutral",
        "triple_score": triple_score,
        "tier_code": tier["tier_code"],
        "tier_label": tier["tier_label"],
        "tier_tone": tier["tier_tone"],
        "action_plan": tier["action"],
        "buy_zone_1": pp.get("buy_tranche_1", 0),
        "buy_zone_2": pp.get("buy_tranche_2", 0),
        "take_profit_1": pp.get("take_profit_1", 0),
        "stop_loss": smart_stop,
    }


def build_triple_fusion_matrix(
    dashboard: dict[str, Any] | None,
    macro_ctx: dict[str, Any],
    scenario: str = "live",
    force: bool = False,
) -> dict[str, Any]:
    """Tạo Siêu Ma trận Hội tụ 3-trong-1 (Vĩ mô × BCTC × Wyckoff VSA) cho toàn bộ rổ cổ phiếu."""
    cached = ensure_default_universe_loaded()
    tickers = [c["ticker"] for c in cached]
    cache_key = f"{scenario}:{','.join(sorted(tickers))}:{int(macro_ctx.get('macro_score', 50))}"

    with _fusion_lock:
        if not force and cache_key in _fusion_cache:
            ts, payload = _fusion_cache[cache_key]
            if time.time() - ts < 90:
                return payload

    vn_mkt = (dashboard or {}).get("vn_market") or {}
    vni_series = ((vn_mkt.get("indices") or {}).get("VNINDEX") or {}).get("series") or []

    rows: list[dict[str, Any]] = []
    failed_tickers: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        fut_map = {ex.submit(_fuse_single_ticker, tk, macro_ctx, vni_series): tk for tk in tickers}
        for fut in concurrent.futures.as_completed(fut_map):
            tk = fut_map[fut]
            try:
                item = fut.result()
                if item:
                    rows.append(item)
            except Exception as exc:
                failed_tickers.append(tk)
                logger.warning("Triple fusion failed for %s: %s", tk, exc)

    rows.sort(key=lambda r: (r["triple_score"], r["upside_pct"]), reverse=True)

    counts = {"DIAMOND": 0, "ACCUMULATE": 0, "VALUE_TRAP": 0, "MOMENTUM": 0, "AVOID": 0}
    for r in rows:
        counts[r["tier_code"]] = counts.get(r["tier_code"], 0) + 1

    top_pick = rows[0] if rows else None
    summary_text = (
        f"Quét hợp nhất {len(rows)} mã theo 3 trụ cột (Vĩ mô {macro_ctx.get('macro_score')}/100 · BCTC Chỉnh Vĩ mô · Wyckoff VSA): "
        f"phát hiện {counts['DIAMOND']} mã Hội tụ Kim cương, {counts['ACCUMULATE']} mã Canh gom, "
        f"và cảnh báo {counts['VALUE_TRAP']} mã Bẫy Giá trị (BCTC tốt nhưng Wyckoff chưa tạo đáy)."
    )
    if top_pick:
        summary_text += (
            f" Dẫn đầu hiện tại: {top_pick['ticker']} ({top_pick['triple_score']:.1f}/100 · "
            f"{top_pick['tier_label']} · Upside {top_pick['upside_pct']:+.1f}%)."
        )

    payload = {
        "updated_at_vn": _now_vn_str(),
        "scenario": scenario,
        "macro_context": {
            "macro_score": macro_ctx.get("macro_score"),
            "stance_short": macro_ctx.get("stance_short"),
            "stance_tone": macro_ctx.get("stance_tone"),
            "regime_name": macro_ctx.get("regime_name"),
            "equity_budget_pct": macro_ctx.get("equity_budget_pct"),
            "us_10y": macro_ctx.get("us_10y"),
            "vix": macro_ctx.get("vix"),
            "vn_pmi": macro_ctx.get("vn_pmi"),
            "foreign_5d_bn": macro_ctx.get("foreign_5d_bn"),
        },
        "counts": counts,
        "summary": summary_text,
        "rows": rows,
        "failed_tickers": sorted(failed_tickers),
        "cio_diagnosis": build_cio_market_diagnosis(dashboard, rows, macro_ctx),
    }

    with _fusion_lock:
        if len(_fusion_cache) >= _FUSION_CACHE_MAX and cache_key not in _fusion_cache:
            oldest_k = min(_fusion_cache.items(), key=lambda kv: kv[1][0])[0]
            del _fusion_cache[oldest_k]
        _fusion_cache[cache_key] = (time.time(), payload)
    return payload


def _build_pick_thesis(row: dict[str, Any], bucket: str) -> str:
    """Sinh luận điểm đầu tư CIO súc tích theo dữ liệu thực tế BCTC × Wyckoff × Vĩ mô."""
    pe = row.get("pe")
    pb = row.get("pb")
    roe = row.get("roe")
    dy = row.get("dividend_yield") or 0
    up = row.get("upside_pct") or 0
    rs = row.get("rs_20d")
    ev = row.get("latest_event") or "—"

    if bucket == "CORE_BUY":
        parts = []
        if dy and dy >= 6.0:
            parts.append(f"Cổ tức tiền mặt {dy:.1f}%/năm (phòng thủ lãi suất cực tốt)")
        if roe and roe >= 18.0:
            parts.append(f"ROE {roe:.1f}% · P/E {pe:.1f}x")
        if up >= 8.0:
            parts.append(f"Upside chỉnh Vĩ mô {up:+.1f}%")
        parts.append(f"Wyckoff {row.get('wyckoff_score', 0):.0f}/100 ({row.get('wyckoff_stage_label')})")
        if rs is not None and rs > 0:
            parts.append(f"RS20 {rs:+.1f}% khỏe hơn VNI")
        return " · ".join(parts)

    if bucket == "ACCUMULATE_WATCH":
        if row.get("wyckoff_stage") == "ACCUMULATION":
            return (
                f"Đang xây nền Tích lũy · Định giá chiết khấu (P/E {pe or '—'}x, P/B {pb or '—'}x, Upside {up:+.1f}%) "
                f"→ Chia lệnh gom từng phần quanh vùng hỗ trợ."
            )
        return (
            f"BCTC siêu hạng ({row.get('pure_bctc_score', 0):.0f}/100, ROE {roe or '—'}%, Upside {up:+.1f}%) "
            f"nhưng đang Pha Đè giá (tín hiệu {ev}) → CHỜ xác nhận giữ đáy Spring/LPS trước khi giải ngân."
        )

    # AVOID_DISTORTION
    return (
        f"Cảnh báo kéo chỉ số / định giá quá nóng: P/E {pe or '—'}x, P/B {pb or '—'}x, "
        f"Upside {up:+.1f}%, {row.get('red_flags_count', 0)} cờ đỏ BCTC → Không đuổi theo nhịp kéo trụ!"
    )


def build_cio_market_diagnosis(
    dashboard: dict[str, Any] | None,
    fusion_rows: list[dict[str, Any]],
    macro_ctx: dict[str, Any],
) -> dict[str, Any]:
    """
    Chẩn đoán CIO chuyên sâu:
      1. Bóc tách 2 động cơ: Vĩ mô Ngoại sinh (Mỹ/Toàn cầu) vs Áp lực Nội tại VN (LSLNH, Tỷ giá Chợ đen, Khối ngoại, Độ rộng)
      2. Radar phát hiện 'Xanh vỏ Đỏ lòng' (Index Distortion): VN-Index vs Độ rộng thực tế VN30 & các mã trụ kéo ảo
      3. Tuyển chọn cổ phiếu VN30 đáng đầu tư nhất (Core Buy / Canh Gom & Chờ Spring / Tránh Kéo Trụ Ảo)
    """
    dash = dashboard or {}
    st = dash.get("strategy") or {}
    vn_mkt = dash.get("vn_market") or {}
    indices = vn_mkt.get("indices") or {}
    vni = indices.get("VNINDEX") or {}
    vn30_idx = indices.get("VN30") or {}
    flow = vn_mkt.get("flow") or {}
    vnmfx = dash.get("vn_money_fx") or {}
    ib_latest = (vnmfx.get("interbank") or {}).get("latest") or {}
    fx_latest = (vnmfx.get("fx_tiers") or {}).get("latest") or {}
    sectors = st.get("sector_playbook") or vn_mkt.get("sectors") or []

    # --- 1. External US/Global Macro Score (0-100) ---
    us_cpi = float(macro_ctx.get("us_cpi") or 2.8)
    us_pce = float(macro_ctx.get("us_core_pce") or 2.6)
    us_10y = float(macro_ctx.get("us_10y") or 4.0)
    dxy = float(macro_ctx.get("dxy") or 102.0)
    vix = float(macro_ctx.get("vix") or 18.0)

    ext_score = 100.0
    ext_reasons: list[str] = []
    if us_cpi >= 3.2:
        ext_score -= 22
        ext_reasons.append(f"US CPI nóng ({us_cpi:.2f}%)")
    elif us_cpi >= 2.7:
        ext_score -= 10
        ext_reasons.append(f"US CPI dai dẳng ({us_cpi:.2f}%)")
    if us_pce >= 3.0:
        ext_score -= 18
        ext_reasons.append(f"Core PCE cao ({us_pce:.2f}%)")
    elif us_pce >= 2.6:
        ext_score -= 8
    if us_10y >= 4.5:
        ext_score -= 20
        ext_reasons.append(f"US 10Y cao ({us_10y:.2f}%)")
    elif us_10y >= 4.0:
        ext_score -= 10
        ext_reasons.append(f"US 10Y neo cao ({us_10y:.2f}%)")
    if dxy >= 104.0:
        ext_score -= 15
        ext_reasons.append(f"DXY mạnh ({dxy:.1f})")
    elif dxy >= 101.5:
        ext_score -= 7
    if vix >= 25.0:
        ext_score -= 15
        ext_reasons.append(f"VIX hoảng loạn ({vix:.1f})")
    elif vix >= 20.0:
        ext_score -= 8
    ext_score = round(_clamp(ext_score, 5.0, 98.0), 1)
    ext_tone = "emerald" if ext_score >= 65 else ("amber" if ext_score >= 45 else "rose")
    ext_label = "Thuận lợi (Mỹ nới lỏng)" if ext_score >= 65 else ("Trung tính / Giằng co" if ext_score >= 45 else "Áp lực Thắt chặt từ Mỹ")

    # --- 2. Vietnam Internal Liquidity & Flow Score (0-100) ---
    ib_on = ib_latest.get("on")
    ib_1w = ib_latest.get("w1")
    fx_spread_pct = fx_latest.get("spread_free_bank_pct")
    fx_spread_vnd = fx_latest.get("spread_free_bank")
    f5 = float(flow.get("foreign_5d_bn") if flow.get("foreign_5d_bn") is not None else (macro_ctx.get("foreign_5d_bn") or 0.0))
    f20 = float(flow.get("foreign_20d_bn") if flow.get("foreign_20d_bn") is not None else (macro_ctx.get("foreign_20d_bn") or 0.0))

    int_score = 100.0
    int_reasons: list[str] = []
    if ib_on is not None:
        if float(ib_on) >= 5.5:
            int_score -= 24
            int_reasons.append(f"LSLNH ON căng ({float(ib_on):.2f}%)")
        elif float(ib_on) >= 4.2:
            int_score -= 12
            int_reasons.append(f"LSLNH ON tăng ({float(ib_on):.2f}%)")
    if fx_spread_pct is not None:
        if float(fx_spread_pct) >= 2.0:
            int_score -= 20
            int_reasons.append(f"Chợ đen vênh NHTM +{float(fx_spread_pct):.2f}%")
        elif float(fx_spread_pct) >= 1.2:
            int_score -= 10
            int_reasons.append(f"Chợ đen nóng nhẹ (+{float(fx_spread_pct):.2f}%)")
    if f20 <= -5000 or f5 <= -2500:
        int_score -= 24
        int_reasons.append(f"Khối ngoại xả mạnh (5p {f5:+,.0f} tỷ)")
    elif f20 < -1500:
        int_score -= 12
        int_reasons.append(f"Khối ngoại bán ròng (20p {f20:+,.0f} tỷ)")

    # Breadth from cached VN30 scan if already computed, else infer from fusion VN30 subset
    vn30_breadth = None
    with _vn30_lock:
        if _vn30_cache is not None:
            vn30_breadth = (_vn30_cache[1] or {}).get("breadth")

    vn30_set = set(VN30_TICKERS)
    vn30_fusion = [r for r in fusion_rows if r.get("ticker") in vn30_set]
    if vn30_breadth:
        total_b = max(1, int(vn30_breadth.get("total") or 30))
        md_cnt = int(vn30_breadth.get("MARKDOWN") or 0)
        bull_pct = float(vn30_breadth.get("bullish_pct") or 0.0)
        md_pct = round(md_cnt / total_b * 100.0, 1)
    else:
        total_b = max(1, len(vn30_fusion))
        md_cnt = sum(1 for r in vn30_fusion if r.get("wyckoff_stage") == "MARKDOWN")
        bull_cnt = sum(1 for r in vn30_fusion if r.get("wyckoff_stage") in ("ACCUMULATION", "MARKUP"))
        md_pct = round(md_cnt / total_b * 100.0, 1)
        bull_pct = round(bull_cnt / total_b * 100.0, 1)

    sec_above_ma50 = sum(1 for s in sectors if s.get("above_ma50"))
    sec_total = len(sectors) or 8

    if md_pct >= 65.0:
        int_score -= 22
        int_reasons.append(f"{md_pct:.0f}% VN30 Đè giá")
    elif md_pct >= 45.0:
        int_score -= 12
    if sec_above_ma50 == 0 and sec_total > 0:
        int_score -= 10
        int_reasons.append(f"0/{sec_total} ngành > MA50")

    int_score = round(_clamp(int_score, 5.0, 98.0), 1)
    int_tone = "emerald" if int_score >= 65 else ("amber" if int_score >= 45 else "rose")
    int_label = "Thanh khoản & Độ rộng Khỏe" if int_score >= 65 else ("Phân hóa Nội địa" if int_score >= 45 else "Thanh khoản Căng & Độ rộng Yếu")

    # --- 3. Index Distortion ("Xanh vỏ Đỏ lòng") Detector ---
    vni_ytd = vni.get("ytd_pct")
    vn30_ytd = vn30_idx.get("ytd_pct")
    ytd_gap = round(float(vni_ytd) - float(vn30_ytd), 2) if (vni_ytd is not None and vn30_ytd is not None) else None

    distorters = [
        r for r in vn30_fusion
        if (r.get("upside_pct", 0) <= -25.0 and r.get("red_flags_count", 0) >= 3)
        and (r.get("wyckoff_stage") in ("ACCUMULATION", "MARKUP") or (r.get("rs_20d") or 0) >= 3.0)
    ]
    distorters.sort(key=lambda r: r.get("upside_pct", 0))

    if md_pct >= 60.0 and (distorters or (ytd_gap is not None and ytd_gap >= 3.0) or (vni_ytd is not None and float(vni_ytd) > -10.0)):
        dist_level = "HIGH"
        dist_tone = "rose"
        dist_label = "XANH VỎ ĐỎ LÒNG NẶNG (Index Distortion)"
    elif md_pct >= 45.0 or sec_above_ma50 <= 2:
        dist_level = "MODERATE"
        dist_tone = "amber"
        dist_label = "PHÂN HÓA HẸP (Cần lọc mã khắt khe)"
    else:
        dist_level = "HEALTHY"
        dist_tone = "emerald"
        dist_label = "ĐỒNG THUẬN LAN TỎA (Thị trường lành mạnh)"

    dist_names = [f"{r['ticker']} (P/E {r.get('pe')}x · {r.get('upside_pct'):+.0f}%)" for r in distorters[:3]]
    dist_detail = (
        f"VN-Index ({vni.get('close', '—')}, YTD {vni_ytd:+.1f}%) giữ điểm tốt hơn hẳn thực chất thị trường: "
        f"{md_cnt}/{total_b} mã VN30 ({md_pct:.0f}%) đang Pha 4 Đè giá và chỉ {sec_above_ma50}/{sec_total} ngành giữ trên MA50."
        if vni_ytd is not None
        else f"{md_cnt}/{total_b} mã VN30 ({md_pct:.0f}%) đang Pha 4 Đè giá · {sec_above_ma50}/{sec_total} ngành trên MA50."
    )
    if dist_names:
        dist_detail += f" Trụ kéo chỉ số nhưng định giá rất cao: {', '.join(dist_names)}."

    # --- 4. Transmission Verdict ---
    if ext_score < 50 and int_score < 50:
        transmission = (
            "KẾT LUẬN CIO: Mỹ (Ngoại sinh) là van áp suất đầu vào khiến khối ngoại rút ròng, "
            "còn Lãi suất LNH VN & Vênh tỷ giá chợ đen (Nội sinh) là bộ khuếch đại hút thanh khoản nội địa. "
            "KHÔNG đánh theo điểm số VN-Index — chỉ giải ngân có chọn lọc vào mã Tiền mặt/Cổ tức cao hoặc Big4 đang Tích lũy đáy."
        )
    elif ext_score < 50 <= int_score:
        transmission = (
            "KẾT LUẬN CIO: Áp lực Mỹ còn cao nhưng nội tại VN đang giữ nhịp tốt — ưu tiên cổ phiếu nội địa dẫn dắt dòng tiền (RS dương)."
        )
    elif int_score < 50 <= ext_score:
        transmission = (
            "KẾT LUẬN CIO: Vĩ mô Mỹ đã dịu nhưng thanh khoản VND & độ rộng nội địa chưa hồi — chưa vội nâng margin, gom dần nền tích lũy."
        )
    else:
        transmission = (
            "KẾT LUẬN CIO: Cả Vĩ mô Mỹ (Ngoại sinh) và Thanh khoản VN (Nội sinh) cùng đồng thuận — ưu tiên nắm giữ cổ phiếu đầu ngành VN30."
        )

    # --- 5. Curated VN30 CIO Conviction Picks ---
    def _pack_pick(r: dict[str, Any], bucket: str) -> dict[str, Any]:
        return {
            "ticker": r["ticker"],
            "company_name": r["company_name"],
            "sector_vi": r["sector_vi"],
            "bucket": bucket,
            "triple_score": r["triple_score"],
            "bctc_score": r["bctc_score"],
            "pure_bctc_score": r["pure_bctc_score"],
            "wyckoff_score": r["wyckoff_score"],
            "wyckoff_stage": r["wyckoff_stage"],
            "wyckoff_stage_label": r["wyckoff_stage_label"],
            "latest_event": r["latest_event"],
            "price": r["price"],
            "fair_value_macro": r["fair_value_macro"],
            "upside_pct": r["upside_pct"],
            "pe": r["pe"],
            "pb": r["pb"],
            "roe": r["roe"],
            "dividend_yield": r["dividend_yield"],
            "red_flags_count": r["red_flags_count"],
            "rs_20d": r["rs_20d"],
            "buy_zone_1": r["buy_zone_1"],
            "stop_loss": r["stop_loss"],
            "thesis": _build_pick_thesis(r, bucket),
        }

    core_buy = [
        _pack_pick(r, "CORE_BUY")
        for r in vn30_fusion
        if r.get("tier_code") == "DIAMOND" and r.get("red_flags_count", 0) <= 1
    ][:4]

    core_set = {x["ticker"] for x in core_buy}
    acc_watch_raw = [
        r for r in vn30_fusion
        if r["ticker"] not in core_set
        and (
            r.get("tier_code") == "ACCUMULATE"
            or (
                r.get("pure_bctc_score", 0) >= 75.0
                and r.get("red_flags_count", 0) <= 1
                and r.get("upside_pct", 0) >= 8.0
            )
        )
    ]
    acc_watch_raw.sort(
        key=lambda r: (
            1 if r.get("wyckoff_stage") == "ACCUMULATION" else 0,
            r.get("triple_score", 0),
            r.get("pure_bctc_score", 0),
        ),
        reverse=True,
    )
    accumulate_watch = [_pack_pick(r, "ACCUMULATE_WATCH") for r in acc_watch_raw[:5]]

    avoid_raw = [
        r for r in vn30_fusion
        if r["ticker"] not in core_set
        and (
            (r.get("upside_pct", 0) <= -25.0 and r.get("red_flags_count", 0) >= 3)
            or (r.get("tier_code") == "AVOID" and r.get("red_flags_count", 0) >= 4)
        )
    ]
    avoid_raw.sort(key=lambda r: (r.get("upside_pct", 0), -r.get("red_flags_count", 0)))
    avoid_distortion = [_pack_pick(r, "AVOID_DISTORTION") for r in avoid_raw[:4]]

    return {
        "updated_at_vn": _now_vn_str(),
        "external_us": {
            "score": ext_score,
            "tone": ext_tone,
            "label": ext_label,
            "reasons": ext_reasons or ["Mỹ ổn định · Lạm phát & DXY trong tầm kiểm soát"],
            "metrics": {"us_cpi": us_cpi, "us_core_pce": us_pce, "us_10y": us_10y, "dxy": dxy, "vix": vix},
        },
        "internal_vn": {
            "score": int_score,
            "tone": int_tone,
            "label": int_label,
            "reasons": int_reasons or ["Thanh khoản VND & Dòng vốn ổn định"],
            "metrics": {
                "ib_on": ib_on,
                "ib_1w": ib_1w,
                "fx_spread_vnd": fx_spread_vnd,
                "fx_spread_pct": fx_spread_pct,
                "foreign_5d_bn": round(f5, 1),
                "foreign_20d_bn": round(f20, 1),
            },
        },
        "index_distortion": {
            "level": dist_level,
            "tone": dist_tone,
            "label": dist_label,
            "detail": dist_detail,
            "vni_close": vni.get("close"),
            "vni_ytd_pct": vni_ytd,
            "vn30_ytd_pct": vn30_ytd,
            "vn30_markdown_cnt": md_cnt,
            "vn30_total": total_b,
            "vn30_markdown_pct": md_pct,
            "vn30_bullish_pct": bull_pct,
            "sectors_above_ma50": sec_above_ma50,
            "sectors_total": sec_total,
            "distorter_tickers": [r["ticker"] for r in distorters[:4]],
        },
        "transmission_verdict": transmission,
        "vn30_conviction": {
            "core_buy": core_buy,
            "accumulate_watch": accumulate_watch,
            "avoid_distortion": avoid_distortion,
        },
    }


def build_peer_comparison(
    tickers: list[str],
    dashboard: dict[str, Any] | None,
    macro_ctx: dict[str, Any],
    scenario: str = "live",
) -> dict[str, Any]:
    """So sánh trực tiếp 2–5 cổ phiếu cùng ngành trên đủ 3 lớp: BCTC + Định giá chỉnh Vĩ mô + Wyckoff VSA."""
    clean_tickers: list[str] = []
    for raw in tickers:
        tk = "".join(ch for ch in str(raw or "").upper().strip() if ch.isalnum())
        if 2 <= len(tk) <= 10 and tk not in clean_tickers:
            clean_tickers.append(tk)
    clean_tickers = clean_tickers[:5]
    if len(clean_tickers) < 2:
        raise ValueError("Vui lòng chọn ít nhất 2 mã cổ phiếu để so sánh cùng ngành (tối đa 5 mã).")

    vn_mkt = (dashboard or {}).get("vn_market") or {}
    vni_series = ((vn_mkt.get("indices") or {}).get("VNINDEX") or {}).get("series") or []

    def _analyze_peer(tk: str) -> dict[str, Any]:
        bundle = load_company_bundle(tk)
        scraped_now = False
        if not bundle:
            try:
                bundle = scrape_company(tk, save=True)
                scraped_now = True
            except Exception as exc:
                return {"ticker": tk, "ok": False, "error": str(exc)}

        bctc = analyze_company_with_macro(bundle, macro_ctx=macro_ctx)
        prices = bundle.get("prices") or []
        wy = None
        if len(prices) >= 40:
            try:
                wy = analyze_wyckoff(
                    prices,
                    symbol=tk,
                    name=bctc["company_name"],
                    macro_ctx=macro_ctx,
                    vni_raw_bars=vni_series,
                    is_index=False,
                )
            except Exception:
                wy = None

        m_global = float(macro_ctx.get("macro_score", 50.0))
        b_total = float(bctc["scores"]["total"])
        m_fit = float(bctc["scores"]["macro_fit"])
        w_score = float(wy["scores"]["wyckoff"]) if wy else 50.0
        w_stage = wy["stage"]["code"] if wy else "ACCUMULATION"
        w_stage_lbl = wy["stage"]["label"] if wy else "—"
        w_tone = wy["stage"]["tone"] if wy else "accent"
        w_sd = wy["laws"]["supply_demand"] if wy else {}
        le = wy["laws"]["effort_result"].get("latest_event") if wy else None

        macro_pillar = round(0.60 * m_fit + 0.40 * m_global, 1)
        triple_score = round(_clamp(0.25 * macro_pillar + 0.40 * b_total + 0.35 * w_score, 8.0, 97.0), 1)
        tier = _classify_triple_convergence(
            triple_score=triple_score,
            bctc_score=b_total,
            macro_fit=m_fit,
            upside_pct=float(bctc["valuation"]["upside_pct"]),
            wy_stage=w_stage,
            wy_score=w_score,
            macro_score=m_global,
            latest_ev_kind=le["kind"] if le else "neutral",
        )

        # Extract top 3 sector-specific cards for display (e.g. NIM/CASA/NPL for Banks)
        sec_cards = (bctc.get("sector_metrics") or {}).get("cards") or []
        sec_highlights = [{"label": c["label"], "value": c["value"], "status": c.get("status", "neutral")} for c in sec_cards[:4]]

        return {
            "ticker": tk,
            "ok": True,
            "scraped_now": scraped_now,
            "company_name": bctc["company_name"],
            "sector_vi": bctc["sector_vi"],
            "report_model": bctc["report_model"],
            "latest_period": bctc["latest_report_period"],
            "price": bctc["market"]["current_price"],
            "market_cap_bn": bctc["market"]["market_cap_bn"],
            "pe": bctc["market"]["pe"],
            "pb": bctc["market"]["pb"],
            "roe": bctc["market"]["roe"],
            "roa": bctc["market"]["roa"],
            "dividend_yield": bctc["market"]["dividend_yield"],
            "rev_cagr_5y": bctc["growth"]["rev_cagr_5y"],
            "npat_cagr_5y": bctc["growth"]["npat_cagr_5y"],
            "npat_yoy_q": bctc["growth"]["npat_yoy_latest_q"],
            "fair_value_macro": bctc["valuation"]["fair_value"],
            "upside_pct": bctc["valuation"]["upside_pct"],
            "bctc_score": b_total,
            "macro_fit": m_fit,
            "scores": bctc["scores"],
            "bctc_verdict": bctc["strategy"]["verdict"],
            "bctc_verdict_code": bctc["strategy"]["verdict_code"],
            "red_flags_count": len(bctc["red_flags"]),
            "wyckoff_score": w_score,
            "wyckoff_stage": w_stage,
            "wyckoff_stage_label": w_stage_lbl,
            "wyckoff_tone": w_tone,
            "rs_20d": w_sd.get("rs_20d"),
            "latest_event": f"{le['code']} ({le['date'][5:]})" if le else "—",
            "triple_score": triple_score,
            "tier_code": tier["tier_code"],
            "tier_label": tier["tier_label"],
            "tier_tone": tier["tier_tone"],
            "sector_highlights": sec_highlights,
        }

    peers_map: dict[str, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(5, len(clean_tickers))) as ex:
        futs = {ex.submit(_analyze_peer, tk): tk for tk in clean_tickers}
        for fut in concurrent.futures.as_completed(futs):
            tk = futs[fut]
            try:
                peers_map[tk] = fut.result()
            except Exception as exc:
                peers_map[tk] = {"ticker": tk, "ok": False, "error": str(exc)}

    peers = [peers_map[tk] for tk in clean_tickers if tk in peers_map]
    valid = [p for p in peers if p.get("ok")]
    if not valid:
        raise ValueError("Không tải được dữ liệu BCTC cho các mã đã chọn.")

    best_overall = max(valid, key=lambda x: x["triple_score"])
    best_value = max(valid, key=lambda x: x["upside_pct"])
    best_roe = max(valid, key=lambda x: x["roe"] or -999)
    best_wy = max(valid, key=lambda x: x["wyckoff_score"])

    return {
        "updated_at_vn": _now_vn_str(),
        "scenario": scenario,
        "tickers": clean_tickers,
        "presets": PEER_PRESETS,
        "peers": peers,
        "leaders": {
            "overall": {"ticker": best_overall["ticker"], "value": f"{best_overall['triple_score']:.1f}/100"},
            "value": {"ticker": best_value["ticker"], "value": f"{best_value['upside_pct']:+.1f}%"},
            "quality": {"ticker": best_roe["ticker"], "value": f"ROE {best_roe['roe']:.1f}%"},
            "wyckoff": {"ticker": best_wy["ticker"], "value": f"{best_wy['wyckoff_score']:.1f} ({best_wy['wyckoff_stage_label']})"},
        },
        "macro_score": macro_ctx.get("macro_score"),
    }


def build_vn30_wyckoff_scanner(
    dashboard: dict[str, Any] | None,
    force: bool = False,
) -> dict[str, Any]:
    """Quét Wyckoff VSA × Vĩ mô song song cho toàn bộ 30 cổ phiếu rổ VN30 + đo độ rộng chu kỳ."""
    global _vn30_cache
    with _vn30_lock:
        if not force and _vn30_cache is not None:
            ts, payload = _vn30_cache
            if time.time() - ts < 600:
                return payload

    macro_ctx = extract_macro_context(dashboard)
    vn_mkt = (dashboard or {}).get("vn_market") or {}
    vni_series = ((vn_mkt.get("indices") or {}).get("VNINDEX") or {}).get("series") or []
    cached_set = {c["ticker"] for c in list_cached_companies()}

    def _scan_one(sym: str) -> dict[str, Any] | None:
        prices = []
        name = VN30_NAMES.get(sym, sym)
        bundle = load_company_bundle(sym)
        if bundle and bundle.get("prices"):
            prices = bundle["prices"]
            name = bundle.get("company_name") or name
        else:
            try:
                prices = fetch_price_history(sym, years=1)
            except Exception:
                prices = []
        if not prices or len(prices) < 40:
            return None
        res = analyze_wyckoff(
            prices,
            symbol=sym,
            name=name,
            macro_ctx=macro_ctx,
            vni_raw_bars=vni_series,
            is_index=False,
        )
        le = res["laws"]["effort_result"].get("latest_event")
        return {
            "symbol": res["symbol"],
            "name": VN30_NAMES.get(sym, res["name"]),
            "is_index": False,
            "in_bctc_cache": sym in cached_set,
            "close": res["close"],
            "pct_1d": res["pct_1d"],
            "stage_code": res["stage"]["code"],
            "stage_label": res["stage"]["label"],
            "sub_phase": res["stage"]["sub_phase"],
            "stage_tone": res["stage"]["tone"],
            "pos_in_tr": res["trading_range"]["position_pct"],
            "ice": res["trading_range"]["ice_support"],
            "creek": res["trading_range"]["creek_resistance"],
            "ud_vol_20": res["laws"]["supply_demand"]["up_down_vol_20"],
            "obv_divergence": res["laws"]["supply_demand"]["obv_divergence"],
            "rs_20d": res["laws"]["supply_demand"]["rs_20d"],
            "latest_event": f"{le['code']} ({le['date'][5:]})" if le else "—",
            "latest_event_kind": le["kind"] if le else "neutral",
            "wyckoff_score": res["scores"]["wyckoff"],
            "confluence_score": res["scores"]["confluence"],
            "alignment": res["fusion"]["alignment"],
            "align_tone": res["fusion"]["align_tone"],
            "action_verdict": res["fusion"]["action_verdict"],
        }

    items: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(_scan_one, sym) for sym in VN30_TICKERS]
        for fut in concurrent.futures.as_completed(futs):
            try:
                row = fut.result()
                if row:
                    items.append(row)
            except Exception:
                pass

    items.sort(key=lambda x: (x["confluence_score"], x["wyckoff_score"]), reverse=True)

    counts = {"ACCUMULATION": 0, "MARKUP": 0, "DISTRIBUTION": 0, "MARKDOWN": 0}
    vsa_active = 0
    rs_pos = 0
    for it in items:
        counts[it["stage_code"]] = counts.get(it["stage_code"], 0) + 1
        if it["latest_event"] != "—":
            vsa_active += 1
        if it.get("rs_20d") is not None and it["rs_20d"] > 0:
            rs_pos += 1

    total = len(items)
    bull_pct = round(((counts["ACCUMULATION"] + counts["MARKUP"]) / total) * 100, 1) if total else 0.0

    payload = {
        "updated_at_vn": _now_vn_str(),
        "total": total,
        "breadth": {
            **counts,
            "total": total,
            "bullish_pct": bull_pct,
            "vsa_signals_count": vsa_active,
            "rs_positive_count": rs_pos,
        },
        "macro_context": {
            "macro_score": macro_ctx["macro_score"],
            "stance_short": macro_ctx["stance_short"],
            "us_10y": macro_ctx["us_10y"],
            "vix": macro_ctx["vix"],
        },
        "stocks": items,
    }

    with _vn30_lock:
        _vn30_cache = (time.time(), payload)
    return payload


def evaluate_portfolio_positions(
    positions: list[dict[str, Any]],
    dashboard: dict[str, Any] | None,
    macro_ctx: dict[str, Any],
) -> dict[str, Any]:
    """Tính P&L realtime + cảnh báo rủi ro tự động (Stop-loss Ice Wyckoff, vùng Chốt lời BCTC, tập trung ngành)."""
    clean_pos: list[dict[str, Any]] = []
    for p in positions or []:
        tk = "".join(ch for ch in str(p.get("ticker") or "").upper().strip() if ch.isalnum())
        qty = float(p.get("qty") or 0)
        avg = float(p.get("avg_price") or 0)
        if 2 <= len(tk) <= 10 and qty > 0 and avg > 0:
            clean_pos.append({"ticker": tk, "qty": qty, "avg_price": avg, "note": str(p.get("note") or "")[:120]})

    if not clean_pos:
        return {
            "updated_at_vn": _now_vn_str(),
            "totals": {"cost_vnd": 0, "market_vnd": 0, "pnl_vnd": 0, "pnl_pct": 0, "positions_count": 0},
            "sector_weights": [],
            "alerts": [],
            "items": [],
        }

    tickers = list(dict.fromkeys(p["ticker"] for p in clean_pos))
    live_map: dict[str, dict[str, Any]] = {}
    try:
        wl_items = fetch_watchlist_stocks(tickers)
        for it in wl_items:
            if it.get("ticker"):
                live_map[it["ticker"].upper()] = it
    except Exception:
        pass

    vn_mkt = (dashboard or {}).get("vn_market") or {}
    vni_series = ((vn_mkt.get("indices") or {}).get("VNINDEX") or {}).get("series") or []

    def _enrich(pos: dict[str, Any]) -> dict[str, Any]:
        tk = pos["ticker"]
        wl = live_map.get(tk) or {}
        bundle = load_company_bundle(tk)
        bctc = analyze_company_with_macro(bundle, macro_ctx=macro_ctx) if bundle else None

        cur_price = float(wl.get("price") or (bctc["market"]["current_price"] if bctc else 0) or pos["avg_price"])
        prices = (bundle.get("prices") if bundle else None) or []
        if len(prices) < 40:
            try:
                prices = fetch_price_history(tk, years=1)
            except Exception:
                prices = []

        wy = None
        if len(prices) >= 40:
            try:
                wy = analyze_wyckoff(
                    prices,
                    symbol=tk,
                    name=(bctc["company_name"] if bctc else wl.get("name_vi") or tk),
                    macro_ctx=macro_ctx,
                    vni_raw_bars=vni_series,
                    is_index=False,
                )
            except Exception:
                wy = None

        cost_vnd = pos["qty"] * pos["avg_price"]
        mkt_vnd = pos["qty"] * cur_price
        pnl_vnd = mkt_vnd - cost_vnd
        pnl_pct = ((cur_price - pos["avg_price"]) / pos["avg_price"]) * 100.0 if pos["avg_price"] > 0 else 0.0

        pp = bctc["strategy"]["price_plan"] if bctc else {}
        fv = float(bctc["valuation"]["fair_value"]) if bctc else None
        tp1 = float(pp.get("take_profit_1") or 0) or None
        buy1 = float(pp.get("buy_tranche_1") or 0) or None
        stop_bctc = float(pp.get("review_stop_level") or 0) or None

        ice = float(wy["trading_range"]["ice_support"]) if wy else None
        creek = float(wy["trading_range"]["creek_resistance"]) if wy else None
        stage_code = wy["stage"]["code"] if wy else "UNKNOWN"
        stage_label = wy["stage"]["label"] if wy else "—"
        stage_tone = wy["stage"]["tone"] if wy else "accent"
        le = wy["laws"]["effort_result"].get("latest_event") if wy else None

        # Smart stop reference = Ice * 0.97 or -8% from avg_price
        stop_ref = round(ice * 0.97, -2) if ice else (stop_bctc or round(pos["avg_price"] * 0.92, -2))

        # Risk Guard Logic
        guard_code = "HOLD_OK"
        guard_label = "🟢 Nắm giữ ổn định"
        guard_tone = "emerald"
        guard_detail = "Vị thế nằm trong vùng kiểm soát."

        if (ice and cur_price < ice * 0.985) or pnl_pct <= -8.0:
            guard_code = "ALERT_STOP"
            guard_label = "🔴 Cảnh báo Stop-loss / Thủng Ice"
            guard_tone = "rose"
            guard_detail = f"Giá ({cur_price:,.0f}₫) vi phạm ngưỡng hỗ trợ Ice ({ice:,.0f}₫) hoặc lỗ {pnl_pct:.1f}% — cần rà soát cắt lỗ kỷ luật!" if ice else f"Vị thế lỗ {pnl_pct:.1f}% (vượt -8%) — rà soát kỷ luật quản trị vốn."
        elif stage_code == "DISTRIBUTION" or (le and le["code"] in ("UTAD", "SOW", "BC")):
            guard_code = "WARN_DIST"
            guard_label = "⚠️ Wyckoff Phân phối — Canh khóa lãi"
            guard_tone = "amber"
            guard_detail = f"Cấu trúc Wyckoff chuyển {stage_label} ({le['code'] if le else 'cung lớn'}) — cân nhắc chốt lời từng phần."
        elif tp1 and cur_price >= tp1 * 0.97:
            guard_code = "TAKE_PROFIT"
            guard_label = "🎯 Chạm vùng Chốt lời BCTC"
            guard_tone = "accent"
            guard_detail = f"Thị giá đã tiệm cận/vượt mục tiêu TP1 ({tp1:,.0f}₫) theo định giá chỉnh Vĩ mô."
        elif stage_code == "MARKDOWN":
            guard_code = "WARN_MD"
            guard_label = "⚠️ Pha 4 Đè giá — Không bình quân giá"
            guard_tone = "rose"
            guard_detail = "Cổ phiếu đang ở Pha 4 Đè giá Wyckoff — tuyệt đối không mua trung bình giá xuống."
        elif buy1 and cur_price <= buy1 * 1.02 and stage_code in ("ACCUMULATION", "MARKUP"):
            guard_code = "BUY_ZONE"
            guard_label = "💎 Trong vùng gom thêm"
            guard_tone = "emerald"
            guard_detail = f"Giá nằm trong vùng mua BCTC ({buy1:,.0f}₫) & Wyckoff {stage_label}."

        sector_vi = (
            (bctc["sector_vi"] if bctc else None)
            or REPRESENTATIVE_UNIVERSE.get(tk, {}).get("sector_vi")
            or "Khác"
        )

        return {
            "ticker": tk,
            "name": (bctc["company_name"] if bctc else wl.get("name_vi") or VN30_NAMES.get(tk, tk)),
            "sector_vi": sector_vi,
            "qty": pos["qty"],
            "avg_price": pos["avg_price"],
            "current_price": cur_price,
            "pct_1d": wl.get("pct_1d"),
            "cost_vnd": round(cost_vnd, 0),
            "market_vnd": round(mkt_vnd, 0),
            "pnl_vnd": round(pnl_vnd, 0),
            "pnl_pct": round(pnl_pct, 2),
            "fair_value": fv,
            "take_profit_1": tp1,
            "ice": ice,
            "creek": creek,
            "stop_ref": stop_ref,
            "wyckoff_stage": stage_code,
            "wyckoff_stage_label": stage_label,
            "wyckoff_tone": stage_tone,
            "latest_event": f"{le['code']} ({le['date'][5:]})" if le else "—",
            "guard_code": guard_code,
            "guard_label": guard_label,
            "guard_tone": guard_tone,
            "guard_detail": guard_detail,
            "note": pos["note"],
        }

    items: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(_enrich, p) for p in clean_pos]
        for fut in concurrent.futures.as_completed(futs):
            try:
                items.append(fut.result())
            except Exception:
                pass

    # Preserve input order
    order_idx = {p["ticker"]: i for i, p in enumerate(clean_pos)}
    items.sort(key=lambda x: order_idx.get(x["ticker"], 999))

    tot_cost = sum(x["cost_vnd"] for x in items)
    tot_mkt = sum(x["market_vnd"] for x in items)
    tot_pnl = tot_mkt - tot_cost
    tot_pnl_pct = (tot_pnl / tot_cost * 100.0) if tot_cost > 0 else 0.0

    sector_map: dict[str, float] = {}
    for it in items:
        it["weight_pct"] = round((it["market_vnd"] / tot_mkt) * 100.0, 1) if tot_mkt > 0 else 0.0
        sec = it["sector_vi"]
        sector_map[sec] = sector_map.get(sec, 0.0) + it["market_vnd"]

    sector_weights = [
        {
            "sector": sec,
            "value_vnd": round(val, 0),
            "weight_pct": round((val / tot_mkt) * 100.0, 1) if tot_mkt > 0 else 0.0,
        }
        for sec, val in sorted(sector_map.items(), key=lambda kv: kv[1], reverse=True)
    ]

    alerts: list[dict[str, str]] = []
    for sw in sector_weights:
        if sw["weight_pct"] >= 45.0 and len(items) >= 2:
            alerts.append({
                "severity": "warn",
                "title": f"Cảnh báo tập trung ngành: {sw['sector']} chiếm {sw['weight_pct']:.1f}% danh mục",
                "detail": "Tỷ trọng vượt 45% làm tăng rủi ro đặc thù ngành — cân nhắc đa dạng hóa khi tái cân bằng.",
            })
    for it in items:
        if it["guard_code"] in ("ALERT_STOP", "WARN_DIST", "WARN_MD", "TAKE_PROFIT"):
            alerts.append({
                "severity": "high" if it["guard_code"] == "ALERT_STOP" else "warn",
                "title": f"{it['ticker']}: {it['guard_label']}",
                "detail": it["guard_detail"],
            })

    return {
        "updated_at_vn": _now_vn_str(),
        "totals": {
            "cost_vnd": round(tot_cost, 0),
            "market_vnd": round(tot_mkt, 0),
            "pnl_vnd": round(tot_pnl, 0),
            "pnl_pct": round(tot_pnl_pct, 2),
            "positions_count": len(items),
        },
        "sector_weights": sector_weights,
        "alerts": alerts,
        "items": items,
    }


def build_morning_brief(
    dashboard: dict[str, Any] | None,
    macro_ctx: dict[str, Any],
    scenario: str = "live",
    positions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Tổng hợp Bản tin Sáng CIO 1 trang (đẹp cho In PDF + gửi Telegram 1-Click)."""
    dash = dashboard or {}
    strat = dash.get("strategy") or {}
    vni = ((dash.get("vn_market") or {}).get("indices") or {}).get("VNINDEX") or {}
    pe_obj = (dash.get("vn_valuation") or {}).get("vnindex_pe") or {}
    pe_val = pe_obj.get("pe")
    gold = dash.get("gold_vn") or {}
    vcb = (dash.get("vcb") or {}).get("usd") or {}
    cal_events = (dash.get("calendar") or {}).get("events") or []

    fusion = build_triple_fusion_matrix(dash, macro_ctx, scenario=scenario, force=False)
    pf_eval = evaluate_portfolio_positions(positions or [], dash, macro_ctx) if positions else None

    upcoming_cal = [e for e in cal_events if e.get("upcoming")][:4]
    recent_cal = [e for e in cal_events if e.get("has_actual")][-3:]
    top_stocks = fusion["rows"][:5]
    traps = [r for r in fusion["rows"] if r["tier_code"] == "VALUE_TRAP"][:3]

    now_str = _now_vn_str()
    vni_val = vni.get("close") or vni.get("latest") or vni.get("value")
    vni_pct = vni.get("pct_1d")
    if vni_pct is None:
        vni_pct = vni.get("change_pct")
    if vni_pct is None:
        vni_pct = vni.get("pct")
    sjc_sell = (gold.get("sjc_bar") or {}).get("sell_m")

    def _f(v: Any, d: int = 1, signed: bool = False) -> str:
        if v is None:
            return "—"
        try:
            n = float(v)
            return f"{n:+.{d}f}" if signed else f"{n:.{d}f}"
        except Exception:
            return "—"

    # Build clean HTML Telegram message
    tg_lines = [
        "📊 <b>BẢN TIN SÁNG VĨ MÔ PULSE × BCTC × WYCKOFF</b>",
        f"🕒 {now_str} (GMT+7)",
        "",
        f"🧭 <b>1. LA BÀN VĨ MÔ: {macro_ctx.get('macro_score', '—')}/100</b> — {macro_ctx.get('stance_short', '—')}",
        f"• Chu kỳ: {macro_ctx.get('regime_name', '—')}",
        f"• Ngân sách CP đề xuất: <b>{macro_ctx.get('equity_budget_pct', '—')}%</b> | US 10Y: {_f(macro_ctx.get('us_10y'), 2)}% | VIX: {_f(macro_ctx.get('vix'), 1)} | DXY: {_f(macro_ctx.get('dxy'), 1)}",
        f"• PMI VN: {_f(macro_ctx.get('vn_pmi'), 1)} | Ngoại 5p: {float(macro_ctx.get('foreign_5d_bn') or 0):+,.0f} tỷ | Thanh khoản HOSE: {float(macro_ctx.get('hose_liq_bn') or 0):,.0f} tỷ",
    ]
    if vni_val is not None:
        pct_part = f" ({_f(vni_pct, 2, signed=True)}%)" if vni_pct is not None else ""
        sjc_part = f" | Vàng SJC: {_f(sjc_sell, 1)}tr" if sjc_sell is not None else ""
        tg_lines.append(
            f"• VN-Index: <b>{float(vni_val):,.2f}</b>{pct_part} | P/E VNI: {_f(pe_val, 2)}x | VCB USD: {int(vcb.get('sell') or 0):,}₫{sjc_part}"
        )

    if upcoming_cal:
        tg_lines.append("")
        tg_lines.append("🇺🇸 <b>2. LỊCH TIN MỸ ★★★ SẮP CÔNG BỐ (GMT+7)</b>")
        for ev in upcoming_cal:
            tg_lines.append(f"• {ev.get('date_vn', '')[:5]} {ev.get('time_vn', '')} — {ev.get('title', '')} (DB: {ev.get('forecast', '—')})")

    if top_stocks:
        tg_lines.append("")
        tg_lines.append("💎 <b>3. TOP HỘI TỤ 3-TRONG-1 (VĨ MÔ × BCTC × WYCKOFF)</b>")
        for r in top_stocks:
            tg_lines.append(
                f"• <b>{r['ticker']}</b> ({r['triple_score']:.1f}đ · {r['tier_label']}): Giá {r['price']:,.0f}₫ → FV {r['fair_value_macro']:,.0f}₫ ({r['upside_pct']:+.1f}%) | {r['wyckoff_stage_label']}"
            )

    if traps:
        tg_lines.append("")
        tg_lines.append("⚠️ <b>4. CẢNH BÁO BẪY GIÁ TRỊ (BCTC đẹp nhưng KT yếu)</b>")
        for r in traps:
            tg_lines.append(f"• <b>{r['ticker']}</b>: BCTC {r['bctc_score']:.0f}đ nhưng Wyckoff {r['wyckoff_stage_label']} — chờ tạo đáy ST/Spring.")

    if pf_eval and pf_eval["totals"]["positions_count"] > 0:
        t_pf = pf_eval["totals"]
        tg_lines.append("")
        tg_lines.append(
            f"💼 <b>5. DANH MỤC CỦA BẠN ({t_pf['positions_count']} mã)</b>: NAV {t_pf['market_vnd']/1e6:,.1f}tr | P&L <b>{t_pf['pnl_vnd']/1e6:+,.1f}tr ({t_pf['pnl_pct']:+.2f}%)</b>"
        )
        for al in pf_eval["alerts"][:4]:
            tg_lines.append(f"• {al['title']}")

    return {
        "generated_at_vn": now_str,
        "macro_context": macro_ctx,
        "strategy_summary": strat.get("summary_action") or "",
        "allocation": strat.get("allocation") or [],
        "market_snapshot": {
            "vni": vni_val,
            "vni_pct": vni_pct,
            "pe_vni": pe_val,
            "vcb_usd_sell": vcb.get("sell"),
            "sjc_sell_m": sjc_sell,
        },
        "upcoming_calendar": upcoming_cal,
        "recent_calendar": recent_cal,
        "fusion_top": top_stocks,
        "value_traps": traps,
        "fusion_counts": fusion["counts"],
        "portfolio": pf_eval,
        "telegram_html": "\n".join(tg_lines),
    }


def send_telegram_alert(bot_token: str, chat_id: str, text: str) -> dict[str, Any]:
    """Gửi tin nhắn cảnh báo / Bản tin Sáng tới Telegram Bot của người dùng (server không lưu token)."""
    tok, cid = validate_telegram_creds(bot_token, chat_id)
    msg = (text or "").strip()
    if not msg:
        raise ValueError("Nội dung tin nhắn đang trống.")
    if len(msg) > 4000:
        msg = msg[:3980] + "\n..."

    url = f"https://api.telegram.org/bot{tok}/sendMessage"
    try:
        resp = _HTTP.post(
            url,
            json={"chat_id": cid, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        logger.warning("Telegram API network error (%s)", type(exc).__name__)
        raise RuntimeError("Không thể kết nối tới Telegram API (timeout hoặc lỗi mạng).") from exc
    data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    if not resp.ok or not data.get("ok"):
        desc = str(data.get("description") or f"HTTP {resp.status_code}")[:160]
        raise RuntimeError(f"Telegram từ chối gửi tin: {desc}")
    return {"ok": True, "message_id": (data.get("result") or {}).get("message_id"), "sent_at_vn": _now_vn_str()}
