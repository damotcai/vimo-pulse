"""
VĨ MÔ PULSE × WYCKOFF ENGINE — Phương pháp Kỹ thuật Wyckoff & VSA Kết hợp Vĩ mô
Định lượng đầy đủ 3 Quy luật Wyckoff + Chu kỳ 4 Pha (Tích lũy / Đẩy giá / Phân phối / Đè giá)
+ Nhận diện tự động sự kiện Wyckoff & VSA (SC, AR, ST, SPRING, SOS, LPS, BC, UTAD, SOW, LPSY, NS, ND, ABS)
+ Đối chiếu Sức mạnh Tương đối (Comparative RS vs VN-Index)
+ Hợp nhất trực tiếp với La bàn Vĩ mô sống (US 10Y, FedWatch, VIX, DXY, PMI, Khối ngoại/Tự doanh)
  để lọc bẫy Bull Trap / Fakeout và xác định đúng điểm giải ngân kỷ luật.
"""

from __future__ import annotations

import math
from typing import Any

from bctc_macro_engine import (
    REPRESENTATIVE_UNIVERSE,
    extract_macro_context,
    fetch_price_history,
    list_cached_companies,
    load_company_bundle,
)
from security_utils import get_logger, validate_ticker

logger = get_logger("wyckoff_engine")


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _normalize_bars(raw_bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Chuẩn hóa OHLCV từ cả macro_fetcher ({o,h,l,c,v}) và bctc_macro_engine ({open,high,low,close,volume})."""
    out: list[dict[str, Any]] = []
    for b in raw_bars or []:
        date = b.get("date")
        c = b.get("c") if b.get("c") is not None else b.get("close")
        if not date or c is None:
            continue
        c_f = float(c)
        if c_f <= 0:
            continue
        o_f = float(b.get("o") if b.get("o") is not None else (b.get("open") if b.get("open") is not None else c_f))
        h_f = float(b.get("h") if b.get("h") is not None else (b.get("high") if b.get("high") is not None else max(o_f, c_f)))
        l_f = float(b.get("l") if b.get("l") is not None else (b.get("low") if b.get("low") is not None else min(o_f, c_f)))
        v_f = float(b.get("v") if b.get("v") is not None else (b.get("volume") if b.get("volume") is not None else 0.0))
        out.append({
            "date": str(date)[:10],
            "o": round(o_f, 2),
            "h": round(max(h_f, o_f, c_f), 2),
            "l": round(min(l_f, o_f, c_f), 2),
            "c": round(c_f, 2),
            "v": max(0.0, v_f),
        })
    out.sort(key=lambda x: x["date"])
    return out


def _sma(vals: list[float], n: int) -> list[float | None]:
    out: list[float | None] = []
    acc = 0.0
    for i, x in enumerate(vals):
        acc += x
        if i >= n:
            acc -= vals[i - n]
        out.append(round(acc / n, 2) if i >= n - 1 else None)
    return out


def _compute_atr(bars: list[dict[str, Any]], period: int = 14) -> list[float]:
    trs: list[float] = []
    for i, b in enumerate(bars):
        if i == 0:
            tr = b["h"] - b["l"]
        else:
            pc = bars[i - 1]["c"]
            tr = max(b["h"] - b["l"], abs(b["h"] - pc), abs(b["l"] - pc))
        trs.append(max(tr, b["c"] * 0.002))
    out: list[float] = []
    for i in range(len(trs)):
        w = trs[max(0, i - period + 1): i + 1]
        out.append(sum(w) / len(w))
    return out


def _compute_obv(bars: list[dict[str, Any]]) -> list[float]:
    obv = [0.0]
    for i in range(1, len(bars)):
        if bars[i]["c"] > bars[i - 1]["c"]:
            obv.append(obv[-1] + bars[i]["v"])
        elif bars[i]["c"] < bars[i - 1]["c"]:
            obv.append(obv[-1] - bars[i]["v"])
        else:
            obv.append(obv[-1])
    return obv


def _percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = _clamp(q * (len(sorted_vals) - 1), 0.0, float(len(sorted_vals) - 1))
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return sorted_vals[lo]
    frac = idx - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def _slope_pct(vals: list[float | None], lookback: int = 20) -> float:
    clean = [v for v in vals[-lookback:] if v is not None]
    if len(clean) < 2 or abs(clean[0]) < 1e-9:
        return 0.0
    return ((clean[-1] - clean[0]) / abs(clean[0])) * 100.0


def analyze_wyckoff(
    raw_bars: list[dict[str, Any]],
    symbol: str,
    name: str = "",
    macro_ctx: dict[str, Any] | None = None,
    vni_raw_bars: list[dict[str, Any]] | None = None,
    flow: dict[str, Any] | None = None,
    is_index: bool = False,
) -> dict[str, Any]:
    """
    Phân tích toàn diện Wyckoff (4 Pha + 3 Quy luật + Sự kiện VSA + Comparative RS)
    kết hợp trực tiếp với Bối cảnh Vĩ mô hiện tại từ VĨ MÔ PULSE.
    """
    bars = _normalize_bars(raw_bars)
    if len(bars) < 40:
        raise ValueError(f"Không đủ dữ liệu lịch sử OHLCV cho {symbol} (cần >= 40 phiên).")

    macro = macro_ctx or extract_macro_context(None)
    closes = [b["c"] for b in bars]
    vols = [b["v"] for b in bars]
    ma20 = _sma(closes, 20)
    ma50 = _sma(closes, 50)
    ma200 = _sma(closes, 200)
    vol_ma20 = _sma(vols, 20)
    atr14 = _compute_atr(bars, 14)
    obv = _compute_obv(bars)
    obv_ma20 = _sma(obv, 20)

    n = len(bars)
    last = bars[-1]
    cur_c = last["c"]
    cur_ma20 = ma20[-1] or cur_c
    cur_ma50 = ma50[-1] or cur_ma20
    cur_ma200 = ma200[-1] or cur_ma50
    slope_ma50 = _slope_pct(ma50, 20)
    slope_ma200 = _slope_pct(ma200, 30)

    # ── 1. Trading Range (TR: Ice Support & Creek Resistance over active 65-session structure) ──
    tr_win = bars[-min(65, n):]
    # Use 6th & 94th percentiles of lows/highs so single extreme panic wicks/springs stay visible outside Ice/Creek!
    tr_lows_sorted = sorted(b["l"] for b in tr_win)
    tr_highs_sorted = sorted(b["h"] for b in tr_win)
    tr_low = round(_percentile(tr_lows_sorted, 0.06), 2)
    tr_high = round(_percentile(tr_highs_sorted, 0.94), 2)
    if tr_high <= tr_low * 1.015:
        tr_low = round(min(b["l"] for b in tr_win), 2)
        tr_high = round(max(b["h"] for b in tr_win), 2)
    tr_mid = round((tr_low + tr_high) / 2.0, 2)
    tr_width = max(tr_high - tr_low, cur_c * 0.02)
    tr_width_pct = round((tr_width / tr_low) * 100.0, 1) if tr_low > 0 else 0.0
    pos_in_tr = round(((cur_c - tr_low) / tr_width) * 100.0, 1)

    # Count how many of the last 90 bars traded inside [tr_low * 0.985, tr_high * 1.015] (Wyckoff Cause!)
    cause_window = bars[-min(90, n):]
    cause_sessions = sum(1 for b in cause_window if tr_low * 0.98 <= b["c"] <= tr_high * 1.02)
    cause_factor = _clamp(0.75 + (cause_sessions / 65.0) * 0.65, 0.80, 1.55)
    markup_target = round(tr_high + tr_width * cause_factor, 2)
    markdown_risk = round(max(tr_low * 0.65, tr_low - tr_width * (cause_factor * 0.85)), 2)

    # ── 2. Law 1: Supply & Demand (Up/Down Volume Ratio + OBV Divergence) ──
    def _up_down_vol_ratio(window_bars: list[dict[str, Any]]) -> float:
        up_v, dn_v = 0.0, 0.0
        for idx in range(1, len(window_bars)):
            if window_bars[idx]["c"] >= window_bars[idx - 1]["c"]:
                up_v += window_bars[idx]["v"]
            else:
                dn_v += window_bars[idx]["v"]
        if dn_v <= 0:
            return 2.0 if up_v > 0 else 1.0
        return round(up_v / dn_v, 2)

    ud_vol_20 = _up_down_vol_ratio(bars[-min(21, n):])
    ud_vol_60 = _up_down_vol_ratio(bars[-min(61, n):])

    # OBV Trend & Price-OBV Divergence over last 25 sessions
    lb_div = min(25, n - 1)
    price_chg_25 = ((closes[-1] - closes[-1 - lb_div]) / closes[-1 - lb_div]) * 100.0 if closes[-1 - lb_div] > 0 else 0.0
    obv_delta_25 = obv[-1] - obv[-1 - lb_div]
    avg_vol_25 = (sum(vols[-lb_div:]) / lb_div) if lb_div > 0 else 1.0
    obv_norm_25 = obv_delta_25 / max(avg_vol_25 * 5.0, 1.0)  # normalized OBV move

    if obv_norm_25 > 0.35:
        obv_trend = "Tăng (Cầu gom ròng)"
    elif obv_norm_25 < -0.35:
        obv_trend = "Giảm (Cung xả ròng)"
    else:
        obv_trend = "Đi ngang (Cân bằng)"

    if price_chg_25 <= -2.5 and obv_norm_25 >= 0.25:
        obv_div = "Phân kỳ Dương (Tiền lớn gom âm thầm)"
        obv_div_tone = "positive"
    elif price_chg_25 >= 2.5 and obv_norm_25 <= -0.25:
        obv_div = "Phân kỳ Âm (Tiền lớn phân phối ngầm)"
        obv_div_tone = "negative"
    else:
        obv_div = "Đồng thuận Giá – Khối lượng"
        obv_div_tone = "neutral"

    # ── 3. Comparative Relative Strength (Wyckoff RS vs VN-Index) ──
    rs_20d: float | None = None
    rs_60d: float | None = None
    rs_series: list[float | None] = [None] * n
    if (not is_index) and vni_raw_bars:
        vni_bars = _normalize_bars(vni_raw_bars)
        vni_map = {b["date"]: b["c"] for b in vni_bars}
        ratios: list[float | None] = []
        base_ratio = None
        for b in bars:
            vc = vni_map.get(b["date"])
            if vc and vc > 0:
                rat = b["c"] / vc
                if base_ratio is None:
                    base_ratio = rat
                ratios.append(rat)
            else:
                ratios.append(ratios[-1] if ratios else None)
        if base_ratio and base_ratio > 0:
            rs_series = [round((r / base_ratio) * 100.0, 2) if r is not None else None for r in ratios]
            valid_idx = [i for i, r in enumerate(ratios) if r is not None]
            if len(valid_idx) > 20:
                r_now = ratios[valid_idx[-1]]
                r_20 = ratios[valid_idx[-21]]
                if r_now and r_20:
                    rs_20d = round(((r_now / r_20) - 1.0) * 100.0, 1)
            if len(valid_idx) > 60:
                r_now = ratios[valid_idx[-1]]
                r_60 = ratios[valid_idx[-61]]
                if r_now and r_60:
                    rs_60d = round(((r_now / r_60) - 1.0) * 100.0, 1)

    # ── 4. Law 3: Effort vs Result (VSA & Wyckoff Event Scanner over last 80 sessions) ──
    events: list[dict[str, Any]] = []
    scan_start = max(21, n - 80)
    last_event_by_code: dict[str, int] = {}

    for i in range(scan_start, n):
        b = bars[i]
        prev = bars[i - 1]
        vma = vol_ma20[i] or 1.0
        rvol = b["v"] / vma if vma > 0 else 1.0
        spread = max(b["h"] - b["l"], 1e-6)
        atr = atr14[i] or spread
        rel_spread = spread / atr
        close_pos = (b["c"] - b["l"]) / spread  # 0 = closed at low, 1 = closed at high
        body = abs(b["c"] - b["o"])
        is_up = b["c"] >= prev["c"]
        is_green = b["c"] >= b["o"]

        # Local 20-bar high/low context before bar i
        win20 = bars[max(0, i - 20): i]
        hi20 = max(x["h"] for x in win20)
        lo20 = min(x["l"] for x in win20)
        pos_tr_i = ((b["c"] - tr_low) / tr_width) if tr_width > 0 else 0.5

        def _add_ev(code: str, label: str, kind: str, desc: str, cooldown: int = 7):
            if code in last_event_by_code and (i - last_event_by_code[code]) < cooldown:
                return
            last_event_by_code[code] = i
            events.append({
                "index": i,
                "date": b["date"],
                "price": b["l"] if kind == "bull" else b["h"],
                "close": b["c"],
                "rvol": round(rvol, 2),
                "code": code,
                "label": label,
                "kind": kind,  # bull | bear | neutral
                "desc": desc,
            })

        # A) SPRING (Rũ bỏ giả dưới Ice/tr_low rồi rút chân đóng cửa trở lại trên tr_low!)
        if b["l"] < tr_low * 0.996 and b["c"] >= tr_low * 0.998 and close_pos >= 0.45:
            _add_ev(
                "SPRING", "Spring (Rũ bỏ giả)", "bull",
                f"Giá xuyên thủng hỗ trợ Ice ({tr_low:,.1f}) nhưng rút chân đóng cửa tại {b['c']:,.1f} (RVol {rvol:.1f}x) — tín hiệu Phase C kinh điển.",
                cooldown=12,
            )
            continue

        # B) UTAD / UPTHRUST (Phá đỉnh giả vượt Creek/tr_high rồi bị bán tụt lại dưới tr_high!)
        if b["h"] > tr_high * 1.004 and b["c"] <= tr_high * 1.002 and close_pos <= 0.48 and rvol >= 1.05:
            _add_ev(
                "UTAD", "Upthrust / UTAD (Bẫy tăng)", "bear",
                f"Giá vượt giả kháng cự Creek ({tr_high:,.1f}) rồi bị xả ngược đóng cửa {b['c']:,.1f} (RVol {rvol:.1f}x) — dấu hiệu phân phối.",
                cooldown=12,
            )
            continue

        # C) SC (Selling Climax — Cao trào bán ở vùng đáy với Vol đột biến)
        if b["l"] <= lo20 * 1.005 and pos_tr_i <= 0.35 and rvol >= 1.65 and rel_spread >= 1.15 and ((not is_up) or close_pos >= 0.38):
            _add_ev(
                "SC", "Selling Climax (Cao trào bán)", "bull",
                f"Khối lượng đột biến {rvol:.1f}x MA20 tại vùng thấp ({b['c']:,.1f}) — lực bán hoảng loạn gặp cầu tổ chức bắt đầu hấp thụ (Phase A).",
                cooldown=15,
            )
            continue

        # D) BC (Buying Climax — Cao trào mua ở vùng đỉnh với Vol đột biến & râu trên)
        if b["h"] >= hi20 * 0.995 and pos_tr_i >= 0.72 and rvol >= 1.65 and (close_pos <= 0.58 or body / spread < 0.45):
            _add_ev(
                "BC", "Buying Climax (Cao trào mua)", "bear",
                f"Vol nổ {rvol:.1f}x MA20 tại vùng đỉnh ({b['c']:,.1f}) nhưng lực tăng bị chặn lại — dấu hiệu cung tổ chức bắt đầu xả hàng.",
                cooldown=15,
            )
            continue

        # E) SOS (Sign of Strength — Bùng nổ cầu phá vỡ kèm Vol mạnh)
        if is_up and is_green and close_pos >= 0.68 and rel_spread >= 1.10 and rvol >= 1.38 and b["c"] >= tr_mid:
            _add_ev(
                "SOS", "Sign of Strength (Tín hiệu Mạnh)", "bull",
                f"Nến tăng lan tỏa mạnh ({rel_spread:.1f}x ATR), đóng cửa sát đỉnh kèm Vol {rvol:.1f}x MA20 — cầu chủ động áp đảo (Phase D).",
                cooldown=10,
            )
            continue

        # F) SOW (Sign of Weakness — Gãy vỡ cung kèm Vol mạnh)
        if (not is_up) and (not is_green) and close_pos <= 0.32 and rel_spread >= 1.10 and rvol >= 1.38 and b["c"] <= tr_mid:
            _add_ev(
                "SOW", "Sign of Weakness (Tín hiệu Yếu)", "bear",
                f"Nến giảm rộng ({rel_spread:.1f}x ATR), đóng cửa sát đáy kèm Vol {rvol:.1f}x MA20 — áp lực cung áp đảo cấu trúc giá.",
                cooldown=10,
            )
            continue

        # G) ST (Secondary Test — Test lại vùng hỗ trợ tr_low với Vol cạn)
        if i >= scan_start + 5 and pos_tr_i <= 0.25 and b["l"] <= tr_low * 1.03 and rvol <= 0.80 and rel_spread <= 0.95:
            _add_ev(
                "ST", "Secondary Test (Test đáy cạn cung)", "bull",
                f"Giá kiểm định lại vùng hỗ trợ {tr_low:,.1f} với Vol thu hẹp chỉ {rvol:.2f}x MA20 — lực bán suy yếu dần.",
                cooldown=12,
            )
            continue

        # H) LPS (Last Point of Support — Nhịp rũ nhẹ giữ trên MA20/tr_mid với Vol thấp sau tăng)
        if pos_tr_i >= 0.48 and pos_tr_i <= 0.82 and (not is_up) and rvol <= 0.72 and rel_spread <= 0.85 and cur_ma20 and b["c"] >= (ma20[i] or 0) * 0.99:
            _add_ev(
                "LPS", "LPS / No Supply (Cạn cung)", "bull",
                f"Phiên điều chỉnh biên độ hẹp, Vol cạn ({rvol:.2f}x MA20) giữ vững nền trên — không có áp lực xả hàng.",
                cooldown=10,
            )
            continue

        # I) ND / LPSY (No Demand — Hồi yếu vol cạn ở vùng kháng cự / dưới MA50)
        if is_up and rvol <= 0.72 and rel_spread <= 0.85 and (pos_tr_i >= 0.70 or (ma50[i] and b["c"] < ma50[i] and b["h"] >= (ma20[i] or 0))):
            _add_ev(
                "ND", "No Demand / LPSY (Cạn cầu)", "bear",
                f"Phiên tăng yếu, biên độ hẹp kèm Vol thấp ({rvol:.2f}x MA20) — thiếu dòng tiền tổ chức tham gia kéo giá.",
                cooldown=10,
            )
            continue

        # J) VSA Stopping Volume / Absorption (Effort high, downward progress halted)
        if rvol >= 1.65 and rel_spread <= 0.90 and pos_tr_i <= 0.32 and close_pos >= 0.50:
            _add_ev(
                "ABS", "Stopping Vol (Hấp thụ đáy)", "bull",
                f"Nỗ lực lớn (Vol {rvol:.1f}x MA20) nhưng giá ngừng rơi và đóng cửa nửa trên — dấu hiệu hấp thụ cung VSA.",
                cooldown=12,
            )

    # Sort events chronologically and keep last 14 cleanest events for chart readability
    events.sort(key=lambda e: e["index"])
    recent_events = events[-14:]
    recent_25_events = [e for e in events if e["index"] >= n - 25]
    bull_ev_25 = [e for e in recent_25_events if e["kind"] == "bull"]
    bear_ev_25 = [e for e in recent_25_events if e["kind"] == "bear"]

    # ── 5. Classify Official Wyckoff 4-Stage Cycle & Sub-Phase (A/B/C/D/E) ──
    above_ma20 = cur_c >= cur_ma20
    above_ma50 = cur_c >= cur_ma50
    above_ma200 = cur_c >= cur_ma200
    is_consolidating = (tr_width_pct <= 22.0 and cause_sessions >= 34) or (abs(slope_ma50) <= 2.2)

    recent_codes = {e["code"] for e in recent_25_events}
    # A Spring is ONLY valid if price has NOT broken decisively back below Ice Support!
    spring_intact = ("SPRING" in recent_codes) and (cur_c >= tr_low * 0.992)
    failed_spring = ("SPRING" in recent_codes) and (cur_c < tr_low * 0.988)
    has_spring = spring_intact and (ud_vol_20 >= 0.72 or obv_div_tone == "positive" or (rs_20d is not None and rs_20d > -4.0))
    has_sos = ("SOS" in recent_codes) and (cur_c >= tr_mid)
    has_sc_abs = bool({"SC", "ABS", "ST"} & recent_codes)
    has_utad = "UTAD" in recent_codes or "BC" in recent_codes
    has_sow = ("SOW" in recent_codes) or failed_spring

    # Determine Stage (4 Official Wyckoff Cycles)
    if above_ma50 and above_ma200 and slope_ma50 > 1.2 and pos_in_tr >= 55 and not (has_utad and ud_vol_20 < 0.85):
        stage_code = "MARKUP"
        stage_vi = "Pha 2 · Đẩy giá (Markup)"
        stage_tone = "emerald"
        if pos_in_tr >= 88 and (has_sos or above_ma20):
            sub_phase = "Phase E · Xu hướng tăng mở rộng"
        elif has_sos or "LPS" in recent_codes:
            sub_phase = "Phase D/E · Bứt phá & Tái tích lũy"
        else:
            sub_phase = "Tái tích lũy trong Uptrend"
    elif pos_in_tr < -2.0 or ((not above_ma50) and (not above_ma200) and slope_ma50 < -1.0 and pos_in_tr <= 42 and not (has_spring and obv_div_tone == "positive")):
        stage_code = "MARKDOWN"
        stage_vi = "Pha 4 · Đè giá (Markdown)"
        stage_tone = "rose"
        if failed_spring:
            sub_phase = "Thủng Ice (Failed Spring) · Tiếp diễn Đè giá"
        elif has_sc_abs and pos_in_tr <= 25:
            sub_phase = "Cuối Pha Đè giá · Dấu hiệu Chững đà (SC/ABS)"
        elif has_sow:
            sub_phase = "Phase E · Xu hướng giảm tăng tốc (SOW)"
        else:
            sub_phase = "Tái phân phối / Phá đáy Ice"
    elif (pos_in_tr >= 58 and (has_utad or ud_vol_20 < 0.92 or obv_div_tone == "negative" or (above_ma200 and not above_ma50 and slope_ma50 < 0))):
        stage_code = "DISTRIBUTION"
        stage_vi = "Pha 3 · Phân phối (Distribution)"
        stage_tone = "amber"
        if has_sow:
            sub_phase = "Phase D · Phá vỡ yếu (SOW / LPSY)"
        elif has_utad:
            sub_phase = "Phase C · Kiểm định đỉnh giả (UTAD)"
        else:
            sub_phase = "Phase B · Xây nguyên nhân Phân phối"
    elif (not above_ma50) and (slope_ma50 < -1.5 or ud_vol_20 < 0.65) and (has_sow or pos_in_tr <= 20):
        stage_code = "MARKDOWN"
        stage_vi = "Pha 4 · Đè giá (Markdown)"
        stage_tone = "rose"
        sub_phase = "Đà giảm ngắn–trung hạn (Cung áp đảo)"
    else:
        stage_code = "ACCUMULATION"
        stage_vi = "Pha 1 · Tích lũy (Accumulation)"
        stage_tone = "accent"
        if has_sos and pos_in_tr >= 55:
            sub_phase = "Phase D · Dấu hiệu Mạnh (SOS / LPS)"
        elif has_spring:
            sub_phase = "Phase C · Rũ bỏ Spring thành công"
        elif spring_intact:
            sub_phase = "Phase A/B · Rút chân thử đáy (Cần thêm Vol cầu)"
        elif has_sc_abs and cause_sessions < 35:
            sub_phase = "Phase A · Hấp thụ chững đà giảm (SC/ST)"
        else:
            sub_phase = "Phase B · Xây nền nguyên nhân (Cause)"

    # ── 6. Pure Wyckoff Technical Score (0..100) ──
    w_score = 50.0
    if stage_code == "MARKUP":
        w_score += 22.0
    elif stage_code == "ACCUMULATION":
        w_score += 10.0 if ("Phase C" in sub_phase or "Phase D" in sub_phase) else 3.0
    elif stage_code == "DISTRIBUTION":
        w_score -= 16.0
    else:  # MARKDOWN
        w_score -= 22.0

    # Law 1 Supply/Demand
    w_score += _clamp((ud_vol_20 - 1.0) * 16.0, -12.0, 12.0)
    if obv_div_tone == "positive":
        w_score += 8.0
    elif obv_div_tone == "negative":
        w_score -= 8.0

    # Law 3 VSA Events
    w_score += min(10.0, len(bull_ev_25) * 4.5)
    w_score -= min(12.0, len(bear_ev_25) * 5.0)
    if has_spring:
        w_score += 7.0
    if has_utad:
        w_score -= 9.0

    # Comparative RS vs VN-Index
    if rs_20d is not None:
        w_score += _clamp(rs_20d * 0.9, -8.0, 8.0)

    # MA Alignment
    if above_ma50:
        w_score += 4.0
    if above_ma200:
        w_score += 4.0

    wyckoff_score = round(_clamp(w_score, 8.0, 96.0), 1)

    # ── 7. WYCKOFF × LIVE MACRO FUSION ENGINE ──
    m_score = float(macro.get("macro_score", 50))
    vix = float(macro.get("vix", 18.0))
    us10y = float(macro.get("us_10y", 4.0))
    f5 = float(macro.get("foreign_5d_bn", 0.0))
    vn_pmi = float(macro.get("vn_pmi", 50.0))
    eq_budget = int(macro.get("equity_budget_pct", 35))

    # Smart Money Flow check (for VN-Index or general market mood)
    foreign_selling_heavy = f5 <= -1500
    macro_bullish = m_score >= 62
    macro_cautious = m_score < 52
    macro_risk_off = m_score < 40

    # Confluence Score: 60% Wyckoff Technical Structure + 40% Live Macro Environment
    confluence_score = round(_clamp(0.60 * wyckoff_score + 0.40 * m_score, 10.0, 96.0), 1)

    # Determine Alignment & Actionable Wyckoff × Macro Verdict
    if stage_code in ("MARKUP", "ACCUMULATION") and wyckoff_score >= 62 and macro_bullish:
        alignment = "ĐỒNG PHA TĂNG · XÁC NHẬN MẠNH"
        align_tone = "emerald"
        action_verdict = "GIẢI NGÂN THEO XU HƯỚNG (HIGH CONVICTION)"
        sizing_rule = f"Giải ngân chủ động (30/40/30%) theo điểm LPS / SOS — tối đa trong ngân sách CP {eq_budget}%."
        macro_filter_note = (
            f"Vĩ mô ủng hộ ({m_score:.0f}/100, PMI VN {vn_pmi:.1f}) xác nhận tín hiệu Wyckoff ({stage_vi}). "
            "Xác suất phá vỡ thành công (Breakout thật) cao."
        )
    elif stage_code == "MARKUP" and macro_cautious:
        alignment = "CỔ PHIẾU KHỎE NGƯỢC THỊ TRƯỜNG (CHỌN LỌC)"
        align_tone = "accent"
        action_verdict = "NẮM GIỮ / CHỈ MUA NHỊP TEST LPS (KHÔNG ĐUỔI)"
        sizing_rule = f"Chỉ giữ/gom nhỏ 20–30% vị thế tại hỗ trợ MA20/LPS, tuyệt đối không mua đuổi vượt Creek khi VIX = {vix:.1f}."
        macro_filter_note = (
            f"Cấu trúc Wyckoff rất khỏe ({wyckoff_score:.0f}/100) nhưng La bàn Vĩ mô đang thận trọng ({m_score:.0f}/100, US 10Y {us10y:.2f}%, VIX {vix:.1f}) "
            "— cảnh giác rung lắc theo thị trường chung, ưu tiên chặn lãi dời theo MA20."
        )
    elif stage_code == "ACCUMULATION" and macro_cautious:
        if has_spring or obv_div_tone == "positive" or (rs_20d is not None and rs_20d > 2.0):
            alignment = "TÍCH LŨY SỚM PHASE C · VĨ MÔ CÒN THẬN TRỌNG"
            align_tone = "accent"
            action_verdict = "THĂM DÒ NHỎ TẠI ĐÁY TR (15–20% · CHỜ SOS)"
            sizing_rule = "Chỉ giải ngân thăm dò 15–20% quanh vùng Ice/Spring; chỉ gia tăng khi xuất hiện SOS xác nhận kèm VIX hạ nhiệt."
            macro_filter_note = (
                f"Đã xuất hiện dấu hiệu gom hàng Wyckoff ({sub_phase}) nhưng Vĩ mô ({m_score:.0f}/100, Ngoại 5p {f5:+,.0f} tỷ) "
                "còn lực cản — Composite Operator thường cần thêm nhịp rũ kiểm định (ST) trước khi bước vào Pha 2 Đẩy giá."
            )
        else:
            alignment = "XÂY NỀN PHASE B · THIẾU XÚC TÁC VĨ MÔ"
            align_tone = "amber"
            action_verdict = "QUAN SÁT XÂY NỀN · CHỜ SPRING HOẶC SOS"
            sizing_rule = "Chưa vội mua sớm giữa Trading Range khi dòng tiền ngoại còn rút ròng; đặt cảnh báo giá tại Ice & Creek."
            macro_filter_note = (
                f"Giá đang đi ngang xây Nguyên nhân ({cause_sessions} phiên) nhưng vĩ mô chưa thuận ({m_score:.0f}/100, VIX {vix:.1f}) "
                "— kiên nhẫn chờ tín hiệu Spring/Test cạn cung."
            )
    elif stage_code == "DISTRIBUTION":
        if macro_cautious or foreign_selling_heavy:
            alignment = "ĐỒNG PHA PHÂN PHỐI · CẢNH BÁO CAO"
            align_tone = "rose"
            action_verdict = "CHỐT LỜI / HẠ TỶ TRỌNG · KHÔNG MUA MỚI"
            sizing_rule = "Hạ tỷ trọng trên các nhịp hồi yếu (LPSY/ND) về gần kháng cự Creek; cắt toàn bộ margin."
            macro_filter_note = (
                f"Cả Wyckoff ({sub_phase}) lẫn Vĩ mô ({m_score:.0f}/100, US 10Y {us10y:.2f}%, Ngoại 5p {f5:+,.0f} tỷ) cùng phát tín hiệu cung lớn — "
                "tránh tuyệt đối các bẫy vượt đỉnh giả (UTAD)."
            )
        else:
            alignment = "PHÂN PHỐI KỸ THUẬT · CHỜ XÁC NHẬN"
            align_tone = "amber"
            action_verdict = "SIẾT CHẶT QUẢN TRỊ RỦI RO / CHỜ TÁI TÍCH LŨY"
            sizing_rule = "Không mua mới sát vùng kháng cự Creek; giữ stop dưới vùng Ice."
            macro_filter_note = (
                f"Vĩ mô vẫn ở mức trung tính–tích cực ({m_score:.0f}/100) nhưng khối lượng trên đồ thị cho thấy áp lực chốt lời ngắn hạn."
            )
    else:  # MARKDOWN
        if has_sc_abs and pos_in_tr <= 25:
            alignment = "ĐÈ GIÁ CHẠM VÙNG HẤP THỤ (SC) · RỦI RO BẮT DAO"
            align_tone = "amber"
            action_verdict = "CHƯA BẮT ĐÁY VỘI · CHỜ TẠO AR & ST (PHASE A/B)"
            sizing_rule = "Theo kỷ luật Wyckoff: sau Cao trào bán (SC) KHÔNG mua ngay mà chờ Hồi phục tự động (AR) và Test đáy Vol cạn (ST)."
            macro_filter_note = (
                f"Có dấu hiệu lực cầu đỡ bước đầu nhưng Vĩ mô ({m_score:.0f}/100, VIX {vix:.1f}, Fed {macro.get('fed_bias')}) vẫn gây áp lực — "
                "cần ít nhất 1 nhịp Secondary Test giữ đáy với khối lượng cạn."
            )
        else:
            alignment = "ĐỒNG PHA GIẢM (MARKDOWN × VĨ MÔ YẾU)"
            align_tone = "rose"
            action_verdict = "ĐỨNG NGOÀI / PHÒNG THỦ TIỀN MẶT"
            sizing_rule = "Bảo toàn vốn, không bắt đáy trong Pha 4 Đè giá; chỉ đưa vào danh sách canh khi giá ngừng rơi (SC + AR)."
            macro_filter_note = (
                f"Đồ thị nằm trong Pha 4 Đè giá ({sub_phase}) cộng hưởng với Vĩ mô bất lợi ({m_score:.0f}/100, Ngoại 5p {f5:+,.0f} tỷ) — "
                "rủi ro thủng sâu về vùng mục tiêu Cause ({markdown_risk:,.1f})."
            )

    # Key Trigger Levels
    entry_trigger = (
        f"Mua xác nhận khi vượt Creek ({tr_high:,.1f}) kèm Vol ≥ 1.4x MA20 (SOS), hoặc rũ Spring giữ vững trên Ice ({tr_low:,.1f}) vol cạn."
        if stage_code in ("ACCUMULATION", "MARKDOWN")
        else f"Canh mua nhịp kiểm định LPS quanh MA20 ({cur_ma20:,.1f}) – {tr_mid:,.1f} với Vol thu hẹp (< 0.8x MA20)."
    )
    invalidation_level = round(tr_low * 0.965, 2)

    # Prepare chart payload (last 180 bars so 60/120/180 zoom works instantaneously on frontend!)
    chart_slice_start = max(0, n - 180)
    chart_bars = []
    for i in range(chart_slice_start, n):
        b = bars[i]
        vma = vol_ma20[i] or 1.0
        chart_bars.append({
            "date": b["date"],
            "o": b["o"],
            "h": b["h"],
            "l": b["l"],
            "c": b["c"],
            "v": b["v"],
            "ma20": ma20[i],
            "ma50": ma50[i],
            "ma200": ma200[i],
            "vol_ma20": round(vol_ma20[i], 0) if vol_ma20[i] is not None else None,
            "rvol": round(b["v"] / vma, 2) if vma > 0 else 1.0,
            "obv": round(obv[i], 0),
            "obv_ma20": round(obv_ma20[i], 0) if obv_ma20[i] is not None else None,
            "rs": rs_series[i],
        })

    # Keep only events visible inside chart_slice_start..n
    visible_events = [
        {k: v for k, v in e.items() if k != "index"}
        for e in recent_events
        if e["index"] >= chart_slice_start
    ]
    latest_event = visible_events[-1] if visible_events else None

    return {
        "symbol": symbol.upper(),
        "name": name or symbol.upper(),
        "is_index": is_index,
        "date": last["date"],
        "close": cur_c,
        "pct_1d": round(((cur_c - bars[-2]["c"]) / bars[-2]["c"]) * 100.0, 2) if n >= 2 and bars[-2]["c"] > 0 else 0.0,
        "stage": {
            "code": stage_code,      # ACCUMULATION | MARKUP | DISTRIBUTION | MARKDOWN
            "label": stage_vi,
            "sub_phase": sub_phase,
            "tone": stage_tone,
        },
        "trading_range": {
            "ice_support": tr_low,
            "creek_resistance": tr_high,
            "midline": tr_mid,
            "width_pct": tr_width_pct,
            "position_pct": pos_in_tr,
            "cause_sessions": cause_sessions,
            "markup_target": markup_target,
            "markdown_risk": markdown_risk,
        },
        "laws": {
            "supply_demand": {
                "up_down_vol_20": ud_vol_20,
                "up_down_vol_60": ud_vol_60,
                "obv_trend": obv_trend,
                "obv_divergence": obv_div,
                "obv_div_tone": obv_div_tone,
                "rs_20d": rs_20d,
                "rs_60d": rs_60d,
                "foreign_5d_bn": flow.get("foreign_5d_bn") if flow else None,
                "prop_5d_bn": flow.get("prop_5d_bn") if flow else None,
            },
            "cause_effect": {
                "cause_sessions": cause_sessions,
                "tr_width_pct": tr_width_pct,
                "markup_target": markup_target,
                "upside_to_target_pct": round(((markup_target - cur_c) / cur_c) * 100.0, 1) if cur_c > 0 else 0.0,
                "markdown_risk": markdown_risk,
                "downside_to_risk_pct": round(((markdown_risk - cur_c) / cur_c) * 100.0, 1) if cur_c > 0 else 0.0,
            },
            "effort_result": {
                "latest_rvol": round(last["v"] / (vol_ma20[-1] or 1.0), 2),
                "bull_events_25d": len(bull_ev_25),
                "bear_events_25d": len(bear_ev_25),
                "latest_event": latest_event,
                "events": list(reversed(visible_events)),  # newest first for event log
            },
        },
        "scores": {
            "wyckoff": wyckoff_score,
            "macro": round(m_score, 1),
            "confluence": confluence_score,
        },
        "fusion": {
            "alignment": alignment,
            "align_tone": align_tone,
            "action_verdict": action_verdict,
            "sizing_rule": sizing_rule,
            "macro_filter_note": macro_filter_note,
            "entry_trigger": entry_trigger,
            "invalidation_level": invalidation_level,
            "macro_snapshot": {
                "macro_score": int(m_score),
                "stance_short": macro.get("stance_short", ""),
                "us_10y": macro.get("us_10y"),
                "fed_bias": macro.get("fed_bias"),
                "vix": macro.get("vix"),
                "vn_pmi": macro.get("vn_pmi"),
                "dxy": macro.get("dxy"),
                "foreign_5d_bn": macro.get("foreign_5d_bn"),
                "equity_budget_pct": eq_budget,
            },
        },
        "chart": {
            "bars": chart_bars,
            "events": visible_events,
            "ma50": cur_ma50,
            "ma200": cur_ma200,
        },
    }


def analyze_symbol_wyckoff(symbol: str, dashboard: dict[str, Any] | None) -> dict[str, Any]:
    """Chạy Wyckoff × Vĩ mô cho bất kỳ chỉ số (VNINDEX/VN30/HNX/UPCOM) hoặc bất kỳ cổ phiếu VN nào."""
    sym = validate_ticker(symbol)
    macro_ctx = extract_macro_context(dashboard)
    vn_mkt = (dashboard or {}).get("vn_market") or {}
    indices = vn_mkt.get("indices") or {}
    flow = vn_mkt.get("flow") or {}
    vni_series = (indices.get("VNINDEX") or {}).get("series") or []

    # 1. Vietnam Index?
    if sym in ("VNINDEX", "VN30", "HNX", "UPCOM"):
        idx = indices.get(sym)
        if idx and idx.get("series"):
            return analyze_wyckoff(
                idx["series"],
                symbol=sym,
                name=idx.get("name", sym),
                macro_ctx=macro_ctx,
                vni_raw_bars=None,
                flow=flow if sym in ("VNINDEX", "VN30") else None,
                is_index=True,
            )

    # 2. Cached BCTC bundle?
    bundle = load_company_bundle(sym)
    if bundle and bundle.get("prices"):
        return analyze_wyckoff(
            bundle["prices"],
            symbol=sym,
            name=bundle.get("company_name") or REPRESENTATIVE_UNIVERSE.get(sym, {}).get("name") or sym,
            macro_ctx=macro_ctx,
            vni_raw_bars=vni_series,
            flow=None,
            is_index=False,
        )

    # 3. Live OHLCV fetch for ANY Vietnamese stock ticker (SSI iBoard / DNSE fallback)
    prices = fetch_price_history(sym, years=2)
    if not prices or len(prices) < 40:
        raise ValueError(f"Không lấy được lịch sử giá OHLCV đủ 40 phiên cho mã '{sym}'.")
    return analyze_wyckoff(
        prices,
        symbol=sym,
        name=REPRESENTATIVE_UNIVERSE.get(sym, {}).get("name") or f"Cổ phiếu {sym}",
        macro_ctx=macro_ctx,
        vni_raw_bars=vni_series,
        flow=None,
        is_index=False,
    )


def build_wyckoff_radar(dashboard: dict[str, Any] | None) -> dict[str, Any]:
    """Quét nhanh chu kỳ Wyckoff × Vĩ mô cho 4 chỉ số thị trường + toàn bộ rổ cổ phiếu BCTC."""
    macro_ctx = extract_macro_context(dashboard)
    vn_mkt = (dashboard or {}).get("vn_market") or {}
    indices = vn_mkt.get("indices") or {}
    flow = vn_mkt.get("flow") or {}
    vni_series = (indices.get("VNINDEX") or {}).get("series") or []

    def _summarize(res: dict[str, Any]) -> dict[str, Any]:
        le = res["laws"]["effort_result"].get("latest_event")
        return {
            "symbol": res["symbol"],
            "name": res["name"],
            "is_index": res["is_index"],
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

    index_items = []
    vni_full = None
    for code in ("VNINDEX", "VN30", "HNX", "UPCOM"):
        idx = indices.get(code)
        if idx and idx.get("series"):
            try:
                res = analyze_wyckoff(
                    idx["series"],
                    symbol=code,
                    name=idx.get("name", code),
                    macro_ctx=macro_ctx,
                    flow=flow if code in ("VNINDEX", "VN30") else None,
                    is_index=True,
                )
                index_items.append(_summarize(res))
                if code == "VNINDEX":
                    vni_full = res
            except Exception as exc:
                logger.warning("Wyckoff radar index %s failed: %s", code, exc)

    stock_items = []
    for c in list_cached_companies():
        b = load_company_bundle(c["ticker"])
        if b and b.get("prices"):
            try:
                res = analyze_wyckoff(
                    b["prices"],
                    symbol=c["ticker"],
                    name=b.get("company_name") or c["ticker"],
                    macro_ctx=macro_ctx,
                    vni_raw_bars=vni_series,
                    is_index=False,
                )
                stock_items.append(_summarize(res))
            except Exception as exc:
                logger.warning("Wyckoff radar stock %s failed: %s", c["ticker"], exc)

    stock_items.sort(key=lambda x: (x["confluence_score"], x["wyckoff_score"]), reverse=True)

    return {
        "macro_context": {
            "macro_score": macro_ctx["macro_score"],
            "stance_short": macro_ctx["stance_short"],
            "us_10y": macro_ctx["us_10y"],
            "fed_bias": macro_ctx["fed_bias"],
            "vix": macro_ctx["vix"],
            "vn_pmi": macro_ctx["vn_pmi"],
            "dxy": macro_ctx["dxy"],
            "foreign_5d_bn": macro_ctx["foreign_5d_bn"],
            "equity_budget_pct": macro_ctx["equity_budget_pct"],
            "updated_at_vn": macro_ctx["updated_at_vn"],
        },
        "indices": index_items,
        "stocks": stock_items,
        "default_detail": vni_full,
    }
