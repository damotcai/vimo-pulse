"""
VĨ MÔ PULSE — Live Macroeconomic & Vietnam Market Strategy Engine (US & Vietnam)
Fetches live official macro data from BLS, BEA (DBnomics), NY Fed, Federal Reserve H.15,
CBOT Fed Funds Futures (Yahoo), Vietcombank XML, World Bank, CafeF, VNDirect/DNSE/SSI
Vietnam Stock Indices & Sector Indices, ForexFactory Calendar, Vietnamese Policy/SBV RSS feeds,
and optional SBV official portal (gracefully skipped if unavailable).
Includes quantitative Macro-Market Scoring & Tactical Investment Strategy Engine.
"""

from __future__ import annotations

import concurrent.futures
import csv
import email.utils
import html
import io
import json
import math
import re
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

BASELINE_FILE = DATA_DIR / "pmi_vn_baseline.json"
HISTORY_STORE_FILE = DATA_DIR / "history_store.json"
SOURCE_CACHE_FILE = DATA_DIR / "source_cache.json"
DASHBOARD_CACHE_FILE = DATA_DIR / "dashboard_cache.json"

VN_TZ = timezone(timedelta(hours=7))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}

# Per-source cache TTLs (seconds) to guarantee live updates while respecting upstream rate limits
SOURCE_TTL = {
    "bls": 1800,          # Monthly official US CPI/PPI/Unemp/NFP (BLS v1 daily quota safe)
    "bea": 1800,          # Monthly official US PCE/Core PCE (DBnomics BEA)
    "nyfed": 600,         # Daily EFFR & SOFR
    "yield_curve": 600,   # Daily Federal Reserve H.15 Treasury curve
    "fedwatch": 180,      # Live CBOT 30-Day Fed Funds Futures (ZQ)
    "fx_markets": 120,    # Live USD/VND, DXY, Gold, Oil, S&P500, VIX
    "vn_market": 120,     # Live VN-Index, VN30, HNX, UPCoM, 8 HOSE Sectors & Foreign/Prop Flow
    "vn_valuation": 300,  # Live VN-Index P/E & 8 Sector Representative Bellwether Stocks
    "gold_vn": 180,       # Live SJC Bar, SJC Ring 9999 vs World Gold Converted Spread
    "vcb": 310,           # Vietcombank XML explicitly asks <= 1 req per 5 min
    "sbv": 600,           # Optional SBV portal (short timeout, skipped cleanly if unavailable)
    "worldbank": 3600,    # Annual VN vs US comparison
    "pmi_live": 900,      # PMI & VN CPI baseline + live RSS/S&P Global updates
    "calendar": 30,       # Live US 3-Star Economic Calendar (TradingView + FXStreet real-time)
    "news": 300,          # Vietnam macro & SBV policy RSS feeds
}

_cache_lock = threading.Lock()
_source_cache: dict[str, dict[str, Any]] = {}


def _load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _save_json(path: Path, data: Any) -> None:
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def _init_source_cache() -> None:
    global _source_cache
    with _cache_lock:
        if not _source_cache:
            loaded = _load_json(SOURCE_CACHE_FILE, {})
            if isinstance(loaded, dict):
                _source_cache = loaded


def _get_cached_or_fetch(key: str, fn, force: bool = False, respect_strict_cooldown: bool = False):
    """Fetch source with per-source TTL caching & automatic fallback to last valid cached value."""
    _init_source_cache()
    now = time.time()
    ttl = SOURCE_TTL.get(key, 300)

    with _cache_lock:
        entry = _source_cache.get(key)

    if entry is not None:
        age = now - entry.get("ts", 0)
        if respect_strict_cooldown and age < 305 and entry.get("data"):
            return entry["data"], False
        if not force and age < ttl and entry.get("data") is not None:
            return entry["data"], False

    try:
        fresh = fn()
        if fresh is not None:
            with _cache_lock:
                _source_cache[key] = {"ts": now, "data": fresh}
                _save_json(SOURCE_CACHE_FILE, _source_cache)
            return fresh, True
    except Exception:
        pass

    if entry and entry.get("data") is not None:
        return entry["data"], False
    return None, False


def _now_vn() -> datetime:
    return datetime.now(VN_TZ)


def _safe_float(val: Any) -> float | None:
    try:
        if val is None or val == "" or val == "." or val == "ND":
            return None
        s = str(val).strip().replace(",", "")
        return float(s)
    except Exception:
        return None


def _parse_vn_number(text: str) -> float | None:
    try:
        s = text.strip().replace(" ", "")
        if "," in s and "." in s:
            s = s.replace(".", "").replace(",", ".")
        elif "." in s:
            parts = s.split(".")
            if len(parts[-1]) == 3:
                s = s.replace(".", "")
        elif "," in s:
            s = s.replace(",", ".")
        return float(s)
    except Exception:
        return None


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# =============================================================================
# Time-Series Helpers (YoY, MoM, Multi-Year Seasonal Matrix, MA, RSI)
# =============================================================================

def _compute_yoy_mom(monthly_points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_date = {p["date"]: p["index"] for p in monthly_points}
    out = []
    for p in monthly_points:
        d = p["date"]
        val = p["index"]
        y, m = int(d[:4]), int(d[5:7])
        prev_y_key = f"{y - 1:04d}-{m:02d}"
        pm = m - 1
        py = y
        if pm == 0:
            pm = 12
            py -= 1
        prev_m_key = f"{py:04d}-{pm:02d}"
        yoy = None
        mom = None
        if prev_y_key in by_date and by_date[prev_y_key]:
            yoy = round(((val / by_date[prev_y_key]) - 1.0) * 100.0, 2)
        if prev_m_key in by_date and by_date[prev_m_key]:
            mom = round(((val / by_date[prev_m_key]) - 1.0) * 100.0, 2)
        out.append({"date": d, "index": round(val, 3), "yoy": yoy, "mom": mom})
    return out


def _build_yearly_matrix(series: list[dict[str, Any]], value_key: str = "yoy", years_back: int = 5) -> dict[str, Any]:
    valid = [p for p in series if p.get(value_key) is not None]
    if not valid:
        return {"years": [], "months": [f"T{i}" for i in range(1, 13)], "matrix": {}, "summary": []}

    all_years = sorted({int(p["date"][:4]) for p in valid})
    target_years = all_years[-years_back:]
    matrix: dict[str, list[float | None]] = {}
    summary = []
    for y in target_years:
        row: list[float | None] = [None] * 12
        vals = []
        for p in valid:
            if int(p["date"][:4]) == y:
                m_idx = int(p["date"][5:7]) - 1
                if 0 <= m_idx < 12:
                    v = round(float(p[value_key]), 2)
                    row[m_idx] = v
                    vals.append(v)
        matrix[str(y)] = row
        if vals:
            summary.append({
                "year": y,
                "avg": round(sum(vals) / len(vals), 2),
                "min": round(min(vals), 2),
                "max": round(max(vals), 2),
                "latest": vals[-1],
                "months_count": len(vals),
            })
    summary.sort(key=lambda x: x["year"], reverse=True)
    return {
        "years": [str(y) for y in target_years],
        "months": [f"T{i}" for i in range(1, 13)],
        "matrix": matrix,
        "summary": summary,
    }


def _sma(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = []
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= period:
            running -= values[i - period]
        if i >= period - 1:
            out.append(round(running / period, 2))
        else:
            out.append(None)
    return out


def _rsi14(values: list[float], period: int = 14) -> float | None:
    if len(values) <= period:
        return None
    gains = []
    losses = []
    for i in range(1, period + 1):
        diff = values[i] - values[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for i in range(period + 1, len(values)):
        diff = values[i] - values[i - 1]
        gain = max(diff, 0.0)
        loss = max(-diff, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 1)


def _compute_daily_ytd_comparison(daily_series: list[dict[str, Any]], val_key: str = "close", digits: int = 2) -> dict[str, Any]:
    if not daily_series:
        return {"years": [], "months": [f"T{i}" for i in range(1, 13)], "matrix": {}, "annual_summary": []}

    by_year: dict[int, list[dict[str, Any]]] = {}
    for pt in daily_series:
        yr = int(pt["date"][:4])
        by_year.setdefault(yr, []).append(pt)

    years = sorted(by_year.keys())[-5:]
    matrix: dict[str, list[float | None]] = {}
    annual_summary = []

    for y in years:
        pts = by_year[y]
        if not pts:
            continue
        base = pts[0][val_key]
        if (y - 1) in by_year and by_year[y - 1]:
            base = by_year[y - 1][-1][val_key]
        row: list[float | None] = [None] * 12
        for p in pts:
            m_idx = int(p["date"][5:7]) - 1
            if 0 <= m_idx < 12 and base:
                row[m_idx] = round(((p[val_key] / base) - 1.0) * 100.0, 2)
        matrix[str(y)] = row
        vals = [p[val_key] for p in pts]
        end_val = vals[-1]
        ytd_pct = round(((end_val / base) - 1.0) * 100.0, 2) if base else 0.0
        annual_summary.append({
            "year": y,
            "open": round(base, digits),
            "high": round(max(vals), digits),
            "low": round(min(vals), digits),
            "close": round(end_val, digits),
            "ytd_pct": ytd_pct,
        })

    annual_summary.sort(key=lambda x: x["year"], reverse=True)
    return {
        "years": [str(y) for y in years],
        "months": [f"T{i}" for i in range(1, 13)],
        "matrix": matrix,
        "annual_summary": annual_summary,
    }


# =============================================================================
# 1. US BLS Official API (CPI, Core CPI, PPI, Unemployment Rate, Nonfarm Payrolls)
# =============================================================================

def fetch_bls_data() -> dict[str, Any]:
    cur_year = _now_vn().year
    start_year = max(2016, cur_year - 9)
    series_ids = [
        "CUSR0000SA0",
        "CUSR0000SA0L1E",
        "WPSFD4",
        "LNS14000000",
        "CES0000000001",
    ]
    resp = requests.post(
        "https://api.bls.gov/publicAPI/v1/timeseries/data/",
        json={"seriesid": series_ids, "startyear": str(start_year), "endyear": str(cur_year)},
        headers={"Content-Type": "application/json", **HEADERS},
        timeout=15,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"BLS API status: {payload.get('status')}")

    raw: dict[str, list[dict[str, Any]]] = {}
    for s in payload.get("Results", {}).get("series", []):
        sid = s["seriesID"]
        pts = []
        for item in s.get("data", []):
            period = item.get("period", "")
            if not period.startswith("M") or period == "M13":
                continue
            val = _safe_float(item.get("value"))
            if val is None:
                continue
            dt = f"{item['year']}-{period[1:]:0>2}"
            pts.append({"date": dt, "index": val})
        pts.sort(key=lambda x: x["date"])
        raw[sid] = pts

    cpi = _compute_yoy_mom(raw.get("CUSR0000SA0", []))
    core_cpi = _compute_yoy_mom(raw.get("CUSR0000SA0L1E", []))
    ppi = _compute_yoy_mom(raw.get("WPSFD4", []))
    unemp = [{"date": p["date"], "value": round(p["index"], 1)} for p in raw.get("LNS14000000", [])]

    nfp_raw = raw.get("CES0000000001", [])
    nfp = []
    for i in range(1, len(nfp_raw)):
        chg = round(nfp_raw[i]["index"] - nfp_raw[i - 1]["index"], 1)
        nfp.append({"date": nfp_raw[i]["date"], "total": nfp_raw[i]["index"], "change_k": chg})

    return {
        "source": "U.S. Bureau of Labor Statistics (BLS)",
        "cpi": cpi,
        "core_cpi": core_cpi,
        "ppi": ppi,
        "unemployment": unemp,
        "nfp": nfp,
    }


# =============================================================================
# 2. US BEA Official PCE & Core PCE via DBnomics
# =============================================================================

def fetch_bea_pce_data() -> dict[str, Any]:
    out = {"source": "U.S. Bureau of Economic Analysis (BEA / DBnomics)", "pce": [], "core_pce": []}
    for field, code in [("pce", "DPCERG-M"), ("core_pce", "DPCCRG-M")]:
        url = f"https://api.db.nomics.world/v22/series/BEA/NIPA-T20804/{code}?observations=1"
        r = requests.get(url, headers=HEADERS, timeout=12)
        r.raise_for_status()
        doc = r.json()["series"]["docs"][0]
        periods = doc.get("period", [])
        values = doc.get("value", [])
        pts = []
        for p, v in zip(periods, values):
            if p >= "2016-01" and isinstance(v, (int, float)):
                pts.append({"date": p, "index": float(v)})
        pts.sort(key=lambda x: x["date"])
        out[field] = _compute_yoy_mom(pts)
    return out


# =============================================================================
# 3. NY Fed Official Rates (EFFR, Target Range, Policy Hike/Cut History, SOFR)
# =============================================================================

def fetch_nyfed_rates() -> dict[str, Any]:
    r_effr = requests.get(
        "https://markets.newyorkfed.org/api/rates/unsecured/effr/search.json?startDate=2020-01-01",
        headers=HEADERS,
        timeout=12,
    )
    r_effr.raise_for_status()
    rates = r_effr.json().get("refRates", [])
    rates.sort(key=lambda x: x["effectiveDate"])

    sofr_latest = None
    try:
        r_sofr = requests.get(
            "https://markets.newyorkfed.org/api/rates/secured/sofr/last/5.json",
            headers=HEADERS,
            timeout=8,
        )
        if r_sofr.status_code == 200:
            s_list = r_sofr.json().get("refRates", [])
            if s_list:
                sofr_latest = {"date": s_list[0]["effectiveDate"], "rate": s_list[0]["percentRate"]}
    except Exception:
        pass

    monthly_map: dict[str, dict[str, Any]] = {}
    rate_changes = []
    prev_target = None

    for row in rates:
        dt = row["effectiveDate"]
        effr = _safe_float(row.get("percentRate"))
        t_low = _safe_float(row.get("targetRateFrom"))
        t_high = _safe_float(row.get("targetRateTo"))
        if effr is None:
            continue
        ym = dt[:7]
        monthly_map[ym] = {
            "date": ym,
            "exact_date": dt,
            "effr": effr,
            "target_low": t_low,
            "target_high": t_high,
        }
        if t_low is not None and t_high is not None:
            cur_pair = (t_low, t_high)
            if prev_target is not None and cur_pair != prev_target:
                delta_bps = round((t_high - prev_target[1]) * 100)
                rate_changes.append({
                    "date": dt,
                    "from_range": f"{prev_target[0]:.2f}% – {prev_target[1]:.2f}%",
                    "to_range": f"{t_low:.2f}% – {t_high:.2f}%",
                    "delta_bps": delta_bps,
                    "action": "Nâng lãi suất" if delta_bps > 0 else "Hạ lãi suất",
                })
            prev_target = cur_pair

    monthly = [monthly_map[k] for k in sorted(monthly_map.keys())]
    latest_row = rates[-1] if rates else {}

    return {
        "source": "Federal Reserve Bank of New York",
        "latest": {
            "date": latest_row.get("effectiveDate"),
            "effr": _safe_float(latest_row.get("percentRate")),
            "target_low": _safe_float(latest_row.get("targetRateFrom")),
            "target_high": _safe_float(latest_row.get("targetRateTo")),
            "volume_bn": _safe_float(latest_row.get("volumeInBillions")),
            "sofr": sofr_latest["rate"] if sofr_latest else None,
            "sofr_date": sofr_latest["date"] if sofr_latest else None,
        },
        "monthly": monthly,
        "changes": list(reversed(rate_changes[-15:])),
    }


# =============================================================================
# 4. Federal Reserve H.15 Official Treasury Yield Curve (1M -> 30Y & 10Y-2Y Spread)
# =============================================================================

def fetch_fed_yield_curve() -> dict[str, Any]:
    url = (
        "https://www.federalreserve.gov/datadownload/Output.aspx?"
        "rel=H15&series=bf17364827e38702b42a58cf8eaa3f78&lastobs=400"
        "&from=&to=&filetype=csv&label=include&layout=seriescolumn"
    )
    r = requests.get(url, headers=HEADERS, timeout=14)
    r.raise_for_status()

    tenors = ["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"]
    rows = []
    reader = csv.reader(io.StringIO(r.text))
    for row in reader:
        if not row or not re.match(r"^\d{4}-\d{2}-\d{2}$", row[0].strip()):
            continue
        vals = [_safe_float(x) for x in row[1 : 1 + len(tenors)]]
        if len(vals) == len(tenors) and vals[4] is not None and vals[8] is not None:
            rows.append({
                "date": row[0].strip(),
                "yields": dict(zip(tenors, vals)),
                "spread_10y_2y": round(vals[8] - vals[4], 2),
                "spread_10y_3m": round(vals[8] - vals[1], 2) if vals[1] is not None else None,
            })

    if not rows:
        raise RuntimeError("No valid H.15 Treasury rows parsed")

    return {
        "source": "Federal Reserve Board (H.15)",
        "tenors": tenors,
        "latest": rows[-1],
        "one_month_ago": rows[max(0, len(rows) - 22)],
        "one_year_ago": rows[max(0, len(rows) - 252)],
        "history": [
            {
                "date": x["date"],
                "y2": x["yields"]["2Y"],
                "y10": x["yields"]["10Y"],
                "y3m": x["yields"]["3M"],
                "spread_10y_2y": x["spread_10y_2y"],
                "spread_10y_3m": x["spread_10y_3m"],
            }
            for x in rows[-260:]
        ],
    }


# =============================================================================
# 5. Live CME FedWatch-Style Rate Hike / Cut Probabilities (CBOT ZQ Futures)
# =============================================================================

MONTH_CODES = {
    1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
    7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z",
}
MONTH_NAMES_VI = {
    1: "Thg 1", 2: "Thg 2", 3: "Thg 3", 4: "Thg 4", 5: "Thg 5", 6: "Thg 6",
    7: "Thg 7", 8: "Thg 8", 9: "Thg 9", 10: "Thg 10", 11: "Thg 11", 12: "Thg 12",
}


def _fetch_zq_contract(ticker: str) -> float | None:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=5d&interval=1d"
    r = requests.get(url, headers=HEADERS, timeout=7)
    if r.status_code != 200:
        return None
    res = r.json().get("chart", {}).get("result")
    if not res:
        return None
    price = _safe_float(res[0].get("meta", {}).get("regularMarketPrice"))
    if price and 80.0 < price < 100.5:
        return price
    return None


def _compute_rate_probabilities(implied_rate: float, current_effr: float, target_low: float, target_high: float) -> dict[str, Any]:
    diff = implied_rate - current_effr
    delta_bps = round(diff * 100.0, 1)
    steps = diff / 0.25

    lower_step = math.floor(steps)
    upper_step = lower_step + 1
    p_upper = max(0.0, min(1.0, steps - lower_step))
    p_lower = 1.0 - p_upper

    buckets = []
    for step, prob in [(lower_step, p_lower), (upper_step, p_upper)]:
        pct = round(prob * 100.0, 1)
        if pct < 0.5:
            continue
        low_b = round(target_low + step * 0.25, 2)
        high_b = round(target_high + step * 0.25, 2)
        if step == 0:
            label = f"Giữ nguyên ({low_b:.2f}–{high_b:.2f}%)"
            kind = "hold"
        elif step > 0:
            label = f"Nâng +{step * 25}bps ({low_b:.2f}–{high_b:.2f}%)"
            kind = "hike"
        else:
            label = f"Hạ {step * 25}bps ({low_b:.2f}–{high_b:.2f}%)"
            kind = "cut"
        buckets.append({"step": step, "label": label, "range": f"{low_b:.2f}%–{high_b:.2f}%", "prob": pct, "kind": kind})

    prob_cut = round(sum(b["prob"] for b in buckets if b["kind"] == "cut"), 1)
    prob_hold = round(sum(b["prob"] for b in buckets if b["kind"] == "hold"), 1)
    prob_hike = round(sum(b["prob"] for b in buckets if b["kind"] == "hike"), 1)

    total = prob_cut + prob_hold + prob_hike
    if total > 0 and abs(total - 100.0) > 0.05:
        if prob_hike >= prob_hold and prob_hike >= prob_cut:
            prob_hike = round(100.0 - prob_cut - prob_hold, 1)
        elif prob_cut >= prob_hold:
            prob_cut = round(100.0 - prob_hike - prob_hold, 1)
        else:
            prob_hold = round(100.0 - prob_cut - prob_hike, 1)

    bias = "Nâng lãi suất" if prob_hike >= 50 else ("Hạ lãi suất" if prob_cut >= 50 else "Giữ nguyên")
    return {
        "implied_rate": round(implied_rate, 3),
        "delta_bps": delta_bps,
        "prob_cut": prob_cut,
        "prob_hold": prob_hold,
        "prob_hike": prob_hike,
        "bias": bias,
        "buckets": sorted(buckets, key=lambda x: x["prob"], reverse=True),
    }


def fetch_fedwatch_futures(current_effr: float = 3.88, target_low: float = 3.75, target_high: float = 4.00) -> dict[str, Any]:
    now = _now_vn()
    offsets = [1, 2, 3, 4, 5, 6, 7, 9, 10, 12]
    targets = []
    seen = set()
    for off in offsets:
        m_total = (now.year * 12 + (now.month - 1)) + off
        yr = m_total // 12
        mo = (m_total % 12) + 1
        ym = f"{yr:04d}-{mo:02d}"
        if ym in seen:
            continue
        seen.add(ym)
        ticker = f"ZQ{MONTH_CODES[mo]}{str(yr)[-2:]}.CBT"
        targets.append((ym, yr, mo, ticker))

    contracts = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        fut_map = {ex.submit(_fetch_zq_contract, t[3]): t for t in targets}
        for fut in concurrent.futures.as_completed(fut_map):
            ym, yr, mo, ticker = fut_map[fut]
            try:
                price = fut.result()
                if price is not None:
                    implied = 100.0 - price
                    prob_info = _compute_rate_probabilities(implied, current_effr, target_low, target_high)
                    contracts.append({
                        "ym": ym,
                        "label": f"{MONTH_NAMES_VI[mo]} {yr}",
                        "ticker": ticker,
                        "futures_price": round(price, 3),
                        **prob_info,
                    })
            except Exception:
                pass

    contracts.sort(key=lambda x: x["ym"])
    return {
        "source": "CBOT 30-Day Fed Funds Futures (ZQ · Market-Implied)",
        "current_effr": current_effr,
        "target_low": target_low,
        "target_high": target_high,
        "contracts": contracts,
    }


# =============================================================================
# 6. Vietnam Stock Market Indices (VN-Index, VN30, HNX, UPCoM, 8 HOSE Sectors & Foreign Flow)
# =============================================================================

VN_MAJOR_INDICES = [
    ("VNINDEX", "VN-Index", "Sàn HOSE"),
    ("VN30", "VN30", "Top 30 Vốn hóa"),
    ("HNX", "HNX-Index", "Sàn Hà Nội"),
    ("UPCOM", "UPCoM", "Thị trường UPCoM"),
]

VN_SECTOR_INDICES = [
    ("VNFIN", "Ngân hàng & Tài chính", "Tài chính"),
    ("VNREAL", "Bất động sản", "Bất động sản"),
    ("VNIT", "Công nghệ Thông tin", "Công nghệ"),
    ("VNIND", "Công nghiệp & KCN", "Công nghiệp"),
    ("VNMAT", "Vật liệu & Thép", "Vật liệu"),
    ("VNENE", "Năng lượng & Dầu khí", "Năng lượng"),
    ("VNCONS", "Tiêu dùng Thiết yếu", "Tiêu dùng"),
    ("VNHEAL", "Y tế & Dược phẩm", "Y tế"),
]

# 1 Iconic Representative Stock per HOSE Sector (Bellwethers)
VN_SECTOR_LEADERS = [
    ("VNFIN", "VCB", "Vietcombank", "Ngân hàng & Tài chính"),
    ("VNREAL", "VHM", "Vinhomes", "Bất động sản"),
    ("VNIT", "FPT", "FPT Corp", "Công nghệ Thông tin"),
    ("VNIND", "GMD", "Gemadept", "Công nghiệp & Cảng biển"),
    ("VNMAT", "HPG", "Hòa Phát", "Vật liệu & Thép"),
    ("VNENE", "GAS", "PV Gas", "Năng lượng & Dầu khí"),
    ("VNCONS", "VNM", "Vinamilk", "Tiêu dùng Thiết yếu"),
    ("VNHEAL", "DHG", "Dược Hậu Giang", "Y tế & Dược phẩm"),
]


def _fetch_vnindex_pe() -> dict[str, Any]:
    """Fetch live VN-Index / Vietnam Market P/E Ratio & 5Y valuation interval from WorldPERatio."""
    try:
        r = requests.get("https://worldperatio.com/area/vietnam/", headers=HEADERS, timeout=(3, 5))
        if r.status_code != 200:
            return {"available": False}
        clean = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", r.text, flags=re.S | re.I)
        clean = re.sub(r"<[^>]+>", " ", clean)
        clean = re.sub(r"\s+", " ", clean)

        m_pe = re.search(
            r"Price-to-Earnings\s*\(P/E\)\s*Ratio\s+for\s+Vietnam\s+Stock\s+Market\s+is\s+(\d{1,2}\.\d{1,2})\s*,?\s*calculated\s+on\s+([A-Za-z0-9\s]+?)\.",
            clean,
            re.I,
        )
        pe_val = float(m_pe.group(1)) if m_pe else None
        pe_date = m_pe.group(2).strip() if m_pe else None

        m_band = re.search(r"average\s+P/E\s+interval\s+is\s*\[\s*(\d{1,2}\.\d{1,2})\s*,\s*(\d{1,2}\.\d{1,2})\s*\]", clean, re.I)
        low_5y = float(m_band.group(1)) if m_band else 12.9
        high_5y = float(m_band.group(2)) if m_band else 16.7
        mid_5y = round((low_5y + high_5y) / 2.0, 2)

        if pe_val is None or not (4.0 <= pe_val <= 45.0):
            return {"available": False}

        if pe_val < low_5y:
            status_vi = "Định giá Rẻ (Hấp dẫn)"
            tone = "success"
        elif pe_val <= high_5y:
            status_vi = "Định giá Hợp lý"
            tone = "warning"
        else:
            status_vi = "Định giá Cao (Thận trọng)"
            tone = "danger"

        earnings_yield = round(100.0 / pe_val, 2)
        vs_avg_pct = round(((pe_val / mid_5y) - 1.0) * 100.0, 1)

        return {
            "available": True,
            "pe": round(pe_val, 2),
            "date": pe_date,
            "avg_5y_low": round(low_5y, 2),
            "avg_5y_high": round(high_5y, 2),
            "avg_5y_mid": mid_5y,
            "earnings_yield_pct": earnings_yield,
            "vs_avg_pct": vs_avg_pct,
            "status": status_vi,
            "tone": tone,
            "source": "WorldPERatio (Vietnam Market)",
        }
    except Exception:
        return {"available": False}


def _fetch_simplize_stock(sector_code: str, ticker: str, short_name: str, sector_name: str) -> dict[str, Any] | None:
    for attempt in range(2):
        try:
            r = requests.get(f"https://api.simplize.vn/api/company/summary/{ticker}", headers=HEADERS, timeout=8)
            if r.status_code != 200:
                if attempt == 0:
                    time.sleep(0.25)
                    continue
                return None
            d = r.json().get("data") or {}
            price = _safe_float(d.get("priceClose"))
            if not price:
                return None
            mcap_tn = round((_safe_float(d.get("marketCap")) or 0.0) / 1e12, 1)
            ta_raw = (d.get("taSignal1d") or "").upper()
            ta_vi = {
                "STRONG_BUY": "Mua mạnh",
                "BUY": "Mua",
                "NEUTRAL": "Trung tính",
                "SELL": "Bán",
                "STRONG_SELL": "Bán mạnh",
            }.get(ta_raw, "Trung tính")
            ni_growth = _safe_float(d.get("netIncomeLtmGrowth"))
            return {
                "sector_code": sector_code,
                "sector_name": sector_name,
                "ticker": ticker,
                "short_name": short_name,
                "name_vi": d.get("nameVi") or short_name,
                "price": round(price, 0),
                "pct_1d": round(_safe_float(d.get("pctChange")) or 0.0, 2),
                "pct_7d": round(_safe_float(d.get("pricePctChg7d")) or 0.0, 2),
                "pct_30d": round(_safe_float(d.get("pricePctChg30d")) or 0.0, 2),
                "pct_ytd": round(_safe_float(d.get("pricePctChgYtd")) or 0.0, 2),
                "pe": round(_safe_float(d.get("peRatio")), 2) if _safe_float(d.get("peRatio")) else None,
                "pb": round(_safe_float(d.get("pbRatio")), 2) if _safe_float(d.get("pbRatio")) else None,
                "roe": round(_safe_float(d.get("roe")), 1) if _safe_float(d.get("roe")) else None,
                "eps": round(_safe_float(d.get("epsRatio")), 0) if _safe_float(d.get("epsRatio")) else None,
                "div_yield": round(_safe_float(d.get("dividendYieldCurrent")), 2) if _safe_float(d.get("dividendYieldCurrent")) is not None else None,
                "ni_growth": round(ni_growth, 1) if ni_growth is not None else None,
                "market_cap_tn": mcap_tn,
                "ta_signal": ta_vi,
                "ta_raw": ta_raw,
            }
        except Exception:
            if attempt == 0:
                time.sleep(0.25)
                continue
    return None


def fetch_vn_valuation_and_leaders() -> dict[str, Any]:
    """Fetch live VN-Index P/E & 8 Representative Sector Leader Stocks in parallel."""
    leaders_map: dict[str, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        f_pe = ex.submit(_fetch_vnindex_pe)
        f_stocks = {
            ex.submit(_fetch_simplize_stock, sc, tk, sn, secn): sc
            for sc, tk, sn, secn in VN_SECTOR_LEADERS
        }
        try:
            vn_pe = f_pe.result()
        except Exception:
            vn_pe = {"available": False}
        for fut in concurrent.futures.as_completed(f_stocks):
            sc = f_stocks[fut]
            try:
                res = fut.result()
                if res:
                    leaders_map[sc] = res
            except Exception:
                pass

    ordered_leaders = [leaders_map[sc] for sc, _, _, _ in VN_SECTOR_LEADERS if sc in leaders_map]

    # Compute weighted P/E & P/B of the 8 bellwether leaders as extra institutional reference
    valid_pe = [x for x in ordered_leaders if x.get("pe") and x["pe"] > 0 and x.get("market_cap_tn")]
    weighted_pe = None
    weighted_pb = None
    if valid_pe:
        tot_cap = sum(x["market_cap_tn"] for x in valid_pe)
        tot_earn = sum(x["market_cap_tn"] / x["pe"] for x in valid_pe)
        if tot_earn > 0:
            weighted_pe = round(tot_cap / tot_earn, 2)
        valid_pb = [x for x in ordered_leaders if x.get("pb") and x["pb"] > 0 and x.get("market_cap_tn")]
        if valid_pb:
            tot_cap_b = sum(x["market_cap_tn"] for x in valid_pb)
            tot_book = sum(x["market_cap_tn"] / x["pb"] for x in valid_pb)
            if tot_book > 0:
                weighted_pb = round(tot_cap_b / tot_book, 2)

    if not vn_pe.get("available") and weighted_pe:
        vn_pe = {
            "available": True,
            "pe": weighted_pe,
            "date": _now_vn().strftime("%d/%m/%Y"),
            "avg_5y_low": 12.9,
            "avg_5y_high": 16.7,
            "avg_5y_mid": 14.8,
            "earnings_yield_pct": round(100.0 / weighted_pe, 2),
            "vs_avg_pct": round(((weighted_pe / 14.8) - 1.0) * 100.0, 1),
            "status": "Định giá Rẻ (Hấp dẫn)" if weighted_pe < 12.9 else "Định giá Hợp lý",
            "tone": "success" if weighted_pe < 12.9 else "warning",
            "source": "Ước tính nhóm đầu ngành HOSE (Simplize)",
        }

    vn_pe["leaders_weighted_pe"] = weighted_pe
    vn_pe["leaders_weighted_pb"] = weighted_pb

    return {
        "vnindex_pe": vn_pe,
        "leaders": ordered_leaders,
        "leaders_by_sector": leaders_map,
    }


def _fetch_vn_ohlcv(symbol: str, years: int = 5) -> list[dict[str, Any]]:
    """Fetch daily OHLCV for a Vietnam index via VNDirect DChart (fallback DNSE Entrade)."""
    now_ts = int(time.time()) + 86400
    from_ts = now_ts - years * 366 * 86400
    providers = [
        f"https://dchart-api.vndirect.com.vn/dchart/history?resolution=D&symbol={symbol}&from={from_ts}&to={now_ts}",
        f"https://services.entrade.com.vn/chart-api/v2/ohlcs/index?from={from_ts}&to={now_ts}&symbol={symbol}&resolution=1D",
    ]
    for url in providers:
        try:
            r = requests.get(url, headers=HEADERS, timeout=9)
            if r.status_code != 200:
                continue
            j = r.json()
            ts_list = j.get("t") or []
            o_list = j.get("o") or []
            h_list = j.get("h") or []
            l_list = j.get("l") or []
            c_list = j.get("c") or []
            v_list = j.get("v") or []
            if len(ts_list) < 10:
                continue
            bars = []
            seen = set()
            for i in range(len(ts_list)):
                c = _safe_float(c_list[i]) if i < len(c_list) else None
                if c is None or c <= 0:
                    continue
                dt = datetime.fromtimestamp(ts_list[i], VN_TZ).strftime("%Y-%m-%d")
                o = _safe_float(o_list[i]) if i < len(o_list) else c
                h = _safe_float(h_list[i]) if i < len(h_list) else c
                lo = _safe_float(l_list[i]) if i < len(l_list) else c
                vol = _safe_float(v_list[i]) if i < len(v_list) else 0.0
                row = {
                    "date": dt,
                    "open": round(o or c, 2),
                    "high": round(h or c, 2),
                    "low": round(lo or c, 2),
                    "close": round(c, 2),
                    "vol": round(vol or 0.0, 0),
                }
                if dt in seen:
                    bars[-1] = row
                else:
                    seen.add(dt)
                    bars.append(row)
            bars.sort(key=lambda x: x["date"])
            if bars:
                return bars
        except Exception:
            continue
    return []


def _fetch_cafef_foreign_and_liquidity() -> dict[str, Any]:
    """Fetch 20-day foreign net flow, 15-day proprietary (Tự doanh) net flow & HOSE liquidity."""
    out: dict[str, Any] = {
        "foreign_daily": [],
        "prop_daily": [],
        "liquidity_bn": None,
        "total_value_bn": None,
        "date": None,
    }

    def _get_foreign():
        url = (
            "https://cafef.vn/du-lieu/Ajax/PageNew/DataHistory/GDKhoiNgoai.ashx"
            "?Symbol=VNINDEX&StartDate=&EndDate=&PageIndex=1&PageSize=20"
        )
        r = requests.get(url, headers=HEADERS, timeout=8)
        if r.status_code != 200:
            return []
        rows = (r.json().get("Data") or {}).get("Data") or []
        parsed = []
        for item in rows:
            ngay = item.get("Ngay", "")
            parts = ngay.split("/")
            iso = f"{parts[2]}-{parts[1]}-{parts[0]}" if len(parts) == 3 else ngay
            net_val = (_safe_float(item.get("GTDGRong")) or 0.0) / 1e9
            buy_val = (_safe_float(item.get("GtMua")) or 0.0) / 1e9
            sell_val = (_safe_float(item.get("GtBan")) or 0.0) / 1e9
            parsed.append({
                "date": iso,
                "label": ngay[:5] if len(ngay) >= 5 else ngay,
                "net_bn": round(net_val, 1),
                "buy_bn": round(buy_val, 1),
                "sell_bn": round(sell_val, 1),
            })
        parsed.sort(key=lambda x: x["date"])
        return parsed

    def _get_prop():
        url = (
            "https://cafef.vn/du-lieu/Ajax/PageNew/DataHistory/GDTuDoanh.ashx"
            "?Symbol=VNINDEX&StartDate=&EndDate=&PageIndex=1&PageSize=20"
        )
        r = requests.get(url, headers=HEADERS, timeout=8)
        if r.status_code != 200:
            return []
        rows = ((r.json().get("Data") or {}).get("Data") or {}).get("ListDataTudoanh") or []
        parsed = []
        for item in rows:
            ngay = item.get("Date", "")
            parts = ngay.split("/")
            iso = f"{parts[2]}-{parts[1]}-{parts[0]}" if len(parts) == 3 else ngay
            buy_val = (_safe_float(item.get("GtMua")) or 0.0) / 1e9
            sell_val = (_safe_float(item.get("GtBan")) or 0.0) / 1e9
            net_val = buy_val - sell_val
            parsed.append({
                "date": iso,
                "label": ngay[:5] if len(ngay) >= 5 else ngay,
                "net_bn": round(net_val, 1),
                "buy_bn": round(buy_val, 1),
                "sell_bn": round(sell_val, 1),
            })
        parsed.sort(key=lambda x: x["date"])
        return parsed

    def _get_liquidity():
        url = (
            "https://cafef.vn/du-lieu/Ajax/PageNew/DataHistory/PriceHistory.ashx"
            "?Symbol=VNINDEX&StartDate=&EndDate=&PageIndex=1&PageSize=5"
        )
        r = requests.get(url, headers=HEADERS, timeout=8)
        if r.status_code != 200:
            return None
        rows = (r.json().get("Data") or {}).get("Data") or []
        if not rows:
            return None
        top = rows[0]
        matched = _safe_float(top.get("GiaTriKhopLenh")) or 0.0
        put_through = _safe_float(top.get("GtThoaThuan")) or 0.0
        return {
            "date": top.get("Ngay"),
            "matched_bn": round(matched, 1),
            "put_through_bn": round(put_through, 1),
            "total_bn": round(matched + put_through, 1),
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        f1 = ex.submit(_get_foreign)
        f2 = ex.submit(_get_prop)
        f3 = ex.submit(_get_liquidity)
        try:
            out["foreign_daily"] = f1.result()
        except Exception:
            pass
        try:
            out["prop_daily"] = f2.result()
        except Exception:
            pass
        try:
            liq = f3.result()
            if liq:
                out["liquidity_bn"] = liq["matched_bn"]
                out["total_value_bn"] = liq["total_bn"]
                out["date"] = liq["date"]
        except Exception:
            pass

    fd = out["foreign_daily"]
    out["foreign_1d_bn"] = fd[-1]["net_bn"] if fd else None
    out["foreign_5d_bn"] = round(sum(x["net_bn"] for x in fd[-5:]), 1) if fd else None
    out["foreign_20d_bn"] = round(sum(x["net_bn"] for x in fd[-20:]), 1) if fd else None

    pd = out["prop_daily"]
    out["prop_1d_bn"] = pd[-1]["net_bn"] if pd else None
    out["prop_5d_bn"] = round(sum(x["net_bn"] for x in pd[-5:]), 1) if pd else None
    out["prop_15d_bn"] = round(sum(x["net_bn"] for x in pd[-15:]), 1) if pd else None
    return out


# =============================================================================
# 6b. Vietnam Gold (SJC Bar & Ring 9999) vs World Gold Converted Spread
# =============================================================================

def fetch_vn_gold_prices(vcb_sell_usd: float | None = None) -> dict[str, Any]:
    """Fetch live SJC bar & SJC 9999 ring prices + CafeF world gold converted & 1M history."""
    sjc_bar = None
    sjc_ring = None
    sjc_updated = None
    world_usd = None
    world_vnd_m = None
    world_pct = None
    history_sjc: list[dict[str, Any]] = []

    def _get_sjc():
        r = requests.get("https://sjc.com.vn/GoldPrice/Services/PriceService.ashx", headers=HEADERS, timeout=7)
        if r.status_code != 200:
            return None
        return r.json()

    def _get_cafef_gold():
        r = requests.get("https://cafef.vn/du-lieu/Ajax/ajaxgoldpricehistory.ashx?index=1m", headers=HEADERS, timeout=7)
        if r.status_code != 200:
            return None
        return r.json()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f_sjc = ex.submit(_get_sjc)
        f_cf = ex.submit(_get_cafef_gold)
        try:
            sjc_j = f_sjc.result()
            if sjc_j and sjc_j.get("success"):
                sjc_updated = sjc_j.get("latestDate")
                for row in sjc_j.get("data") or []:
                    branch = row.get("BranchName") or ""
                    tname = row.get("TypeName") or ""
                    if "Hồ Chí Minh" not in branch:
                        continue
                    buy_m = round((_safe_float(row.get("BuyValue")) or 0.0) / 1e6, 2)
                    sell_m = round((_safe_float(row.get("SellValue")) or 0.0) / 1e6, 2)
                    if buy_m <= 0 or sell_m <= 0:
                        continue
                    if "Vàng SJC 1L" in tname and sjc_bar is None:
                        sjc_bar = {
                            "name": "Vàng miếng SJC 1L (HCM)",
                            "buy_m": buy_m,
                            "sell_m": sell_m,
                            "spread_buy_sell_m": round(sell_m - buy_m, 2),
                        }
                    elif "nhẫn sjc 99,99% 1 chỉ" in tname.lower() and sjc_ring is None:
                        sjc_ring = {
                            "name": "Vàng nhẫn SJC 99,99%",
                            "buy_m": buy_m,
                            "sell_m": sell_m,
                            "spread_buy_sell_m": round(sell_m - buy_m, 2),
                        }
        except Exception:
            pass

        try:
            cf_j = f_cf.result()
            d = (cf_j or {}).get("Data") or {}
            w = d.get("goldPriceWorlds") or {}
            world_usd = _safe_float(w.get("price"))
            world_pct = _safe_float(w.get("changePricePercent"))
            wv = _safe_float(w.get("goldPriceWorldVND"))
            if wv and wv > 20e6:
                world_vnd_m = round(wv / 1e6, 2)
            # Parse CafeF 1M SJC history
            seen_dt = set()
            for h in d.get("goldPriceWorldHistories") or []:
                cr = (h.get("createdAt") or "")[:10]
                bp = _safe_float(h.get("buyPrice"))
                sp = _safe_float(h.get("sellPrice"))
                if cr and sp and sp > 50 and cr not in seen_dt:
                    seen_dt.add(cr)
                    history_sjc.append({"date": cr, "buy_m": round(bp or sp, 2), "sell_m": round(sp, 2)})
            history_sjc.sort(key=lambda x: x["date"])
        except Exception:
            pass

    # Fallback world gold converted calculation if CafeF didn't return goldPriceWorldVND
    if world_vnd_m is None and world_usd and vcb_sell_usd:
        # 1 lượng = 37.5g = 1.205653 troy oz
        world_vnd_m = round((world_usd * 1.205653 * vcb_sell_usd) / 1e6, 2)

    if sjc_bar is None and history_sjc:
        last_h = history_sjc[-1]
        sjc_bar = {
            "name": "Vàng miếng SJC (CafeF)",
            "buy_m": last_h["buy_m"],
            "sell_m": last_h["sell_m"],
            "spread_buy_sell_m": round(last_h["sell_m"] - last_h["buy_m"], 2),
        }

    if not sjc_bar and not world_usd:
        return {"available": False}

    bar_spread_m = round(sjc_bar["sell_m"] - world_vnd_m, 2) if (sjc_bar and world_vnd_m) else None
    bar_spread_pct = round(((sjc_bar["sell_m"] / world_vnd_m) - 1.0) * 100.0, 1) if (sjc_bar and world_vnd_m) else None
    ring_spread_m = round(sjc_ring["sell_m"] - world_vnd_m, 2) if (sjc_ring and world_vnd_m) else None
    ring_spread_pct = round(((sjc_ring["sell_m"] / world_vnd_m) - 1.0) * 100.0, 1) if (sjc_ring and world_vnd_m) else None

    # Investor guidance on domestic gold premium
    if bar_spread_m is not None:
        if bar_spread_m <= 8.0:
            advice = "Chênh lệch thấp so với thế giới · Thuận lợi tích lũy vàng phòng hộ"
            tone = "success"
        elif bar_spread_m <= 15.0:
            advice = "Chênh lệch trung bình · Nên chia nhỏ từng đợt, ưu tiên vàng nhẫn 9999 nếu chênh thấp hơn"
            tone = "warning"
        else:
            advice = f"Chênh lệch cao (+{bar_spread_m:.1f} tr/lượng) · Hạn chế mua đuổi SJC giá cao, canh nhịp điều chỉnh"
            tone = "danger"
    else:
        advice = "Theo dõi diễn biến giá vàng thế giới & tỷ giá USD/VND"
        tone = "info"

    return {
        "available": True,
        "updated_at": sjc_updated or _now_vn().strftime("%H:%M %d/%m/%Y"),
        "sjc_bar": sjc_bar,
        "sjc_ring": sjc_ring,
        "world_usd_oz": round(world_usd, 1) if world_usd else None,
        "world_pct": round(world_pct, 2) if world_pct is not None else None,
        "world_vnd_m": world_vnd_m,
        "bar_spread_m": bar_spread_m,
        "bar_spread_pct": bar_spread_pct,
        "ring_spread_m": ring_spread_m,
        "ring_spread_pct": ring_spread_pct,
        "advice": advice,
        "tone": tone,
        "history_sjc": history_sjc[-30:],
        "source": "SJC Official & CafeF",
    }


# =============================================================================
# 6c. Dynamic Personal Watchlist Analyzer (Any Vietnam Stock Ticker Live!)
# =============================================================================

_watchlist_cache: dict[str, dict[str, Any]] = {}
_watchlist_lock = threading.Lock()


def _analyze_single_watchlist_stock(ticker: str) -> dict[str, Any] | None:
    tk = re.sub(r"[^A-Z0-9]", "", ticker.upper().strip())[:10]
    if not tk:
        return None

    now = time.time()
    with _watchlist_lock:
        cached = _watchlist_cache.get(tk)
        if cached and (now - cached["ts"]) < 180:
            return cached["data"]

    # Fetch Simplize fundamentals + VNDirect 1.5-year technical OHLCV in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f_fund = ex.submit(_fetch_simplize_stock, "CUSTOM", tk, tk, "Watchlist")
        f_bars = ex.submit(_fetch_vn_ohlcv, tk, 2)
        fund = f_fund.result()
        bars = f_bars.result()

    if not fund and not bars:
        return {"ticker": tk, "found": False}

    # Technical indicators from OHLCV
    closes = [b["close"] for b in bars] if bars else []
    ma20 = _sma(closes, 20)[-1] if len(closes) >= 20 else None
    ma50 = _sma(closes, 50)[-1] if len(closes) >= 50 else None
    ma200 = _sma(closes, 200)[-1] if len(closes) >= 200 else None
    rsi14 = _rsi14(closes, 14) if len(closes) >= 16 else None

    # Normalize VNDirect stock price scale (VNDirect returns in thousands VND like 24.5 -> 24,500 if < 1000)
    price = (fund or {}).get("price")
    scale = 1.0
    if bars:
        last_bar_c = bars[-1]["close"]
        if price and last_bar_c > 0 and price / last_bar_c > 500:
            scale = 1000.0
        elif not price:
            price = round(last_bar_c * 1000.0 if last_bar_c < 500 else last_bar_c, 0)
            if last_bar_c < 500:
                scale = 1000.0

    def _sc(v):
        return round(v * scale, 0) if v is not None else None

    ma20_v, ma50_v, ma200_v = _sc(ma20), _sc(ma50), _sc(ma200)
    recent_52w = bars[-252:] if bars else []
    high_52w = _sc(max(b["high"] for b in recent_52w)) if recent_52w else None
    low_52w = _sc(min(b["low"] for b in recent_52w)) if recent_52w else None
    dd_52w = round(((price / high_52w) - 1.0) * 100.0, 1) if (price and high_52w) else None
    dist_ma50 = round(((price / ma50_v) - 1.0) * 100.0, 1) if (price and ma50_v) else None

    pe = (fund or {}).get("pe")
    roe = (fund or {}).get("roe")

    # Actionable Signal Classification
    if rsi14 is not None and rsi14 >= 72:
        signal, sig_tone = "Quá mua · Chốt lời dần", "warning"
    elif rsi14 is not None and rsi14 <= 32:
        if pe and pe < 13 and (roe or 0) >= 12:
            signal, sig_tone = "Quá bán · Định giá rẻ", "success"
        else:
            signal, sig_tone = "Quá bán · Chờ tạo đáy", "warning"
    elif price and ma50_v and ma200_v and price > ma50_v and ma50_v >= ma200_v:
        signal, sig_tone = "Xu hướng Tăng · Nắm giữ", "success"
    elif price and ma50_v and price < ma50_v and (pe and pe < 12 and (roe or 0) >= 15):
        signal, sig_tone = "Chiết khấu · Canh gom dần", "info"
    elif price and ma200_v and price < ma200_v:
        signal, sig_tone = "Dưới MA200 · Thận trọng", "danger"
    else:
        signal, sig_tone = "Tích lũy · Theo dõi", "info"

    result = {
        "found": True,
        "ticker": tk,
        "name_vi": (fund or {}).get("name_vi") or tk,
        "industry": (fund or {}).get("sector_name") if fund and fund.get("sector_name") != "Watchlist" else "",
        "price": price,
        "pct_1d": (fund or {}).get("pct_1d") or 0.0,
        "pct_30d": (fund or {}).get("pct_30d"),
        "pct_ytd": (fund or {}).get("pct_ytd"),
        "pe": pe,
        "pb": (fund or {}).get("pb"),
        "roe": roe,
        "div_yield": (fund or {}).get("div_yield"),
        "market_cap_tn": (fund or {}).get("market_cap_tn"),
        "ma20": ma20_v,
        "ma50": ma50_v,
        "ma200": ma200_v,
        "dist_ma50_pct": dist_ma50,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "drawdown_52w": dd_52w,
        "rsi14": rsi14,
        "signal": signal,
        "signal_tone": sig_tone,
        "spark": [round(b["close"] * scale, 0) for b in bars[-35:]] if bars else [],
    }
    with _watchlist_lock:
        _watchlist_cache[tk] = {"ts": now, "data": result}
    return result


def fetch_watchlist_stocks(tickers: list[str]) -> list[dict[str, Any]]:
    cleaned = []
    seen = set()
    for t in tickers[:16]:
        tk = re.sub(r"[^A-Z0-9]", "", str(t).upper().strip())[:10]
        if tk and tk not in seen:
            seen.add(tk)
            cleaned.append(tk)
    if not cleaned:
        return []

    results_map: dict[str, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(_analyze_single_watchlist_stock, tk): tk for tk in cleaned}
        for fut in concurrent.futures.as_completed(futs):
            tk = futs[fut]
            try:
                res = fut.result()
                if res:
                    results_map[tk] = res
            except Exception:
                results_map[tk] = {"ticker": tk, "found": False}
    return [results_map[tk] for tk in cleaned if tk in results_map]


def _analyze_index_bars(code: str, name: str, market: str, bars: list[dict[str, Any]]) -> dict[str, Any]:
    if not bars:
        return {"code": code, "name": name, "market": market, "available": False}

    closes = [b["close"] for b in bars]
    ma20 = _sma(closes, 20)
    ma50 = _sma(closes, 50)
    ma200 = _sma(closes, 200)

    latest = bars[-1]
    prev = bars[-2] if len(bars) >= 2 else latest
    chg = round(latest["close"] - prev["close"], 2)
    pct = round(((latest["close"] / prev["close"]) - 1.0) * 100.0, 2) if prev["close"] else 0.0

    # YTD %
    cur_yr = latest["date"][:4]
    prev_yr_bars = [b for b in bars if b["date"][:4] < cur_yr]
    ytd_base = prev_yr_bars[-1]["close"] if prev_yr_bars else bars[0]["close"]
    ytd_pct = round(((latest["close"] / ytd_base) - 1.0) * 100.0, 2) if ytd_base else 0.0

    # 1M (~21 sessions), 3M (~63 sessions)
    c_1m = bars[-22]["close"] if len(bars) >= 22 else bars[0]["close"]
    c_3m = bars[-64]["close"] if len(bars) >= 64 else bars[0]["close"]
    chg_1m = round(((latest["close"] / c_1m) - 1.0) * 100.0, 2) if c_1m else 0.0
    chg_3m = round(((latest["close"] / c_3m) - 1.0) * 100.0, 2) if c_3m else 0.0

    # 52W High / Low
    recent_52w = bars[-252:]
    high_52w = max(b["high"] for b in recent_52w)
    low_52w = min(b["low"] for b in recent_52w)
    dd_52w = round(((latest["close"] / high_52w) - 1.0) * 100.0, 2) if high_52w else 0.0

    m20 = ma20[-1]
    m50 = ma50[-1]
    m200 = ma200[-1]
    rsi = _rsi14(closes, 14)

    # Trend label
    if m50 and m200:
        if latest["close"] > m50 and m50 > m200:
            trend = "Xu hướng Tăng"
            trend_tone = "success"
        elif latest["close"] < m50 and latest["close"] < m200:
            trend = "Xu hướng Giảm"
            trend_tone = "danger"
        elif latest["close"] >= m200:
            trend = "Tích lũy / Rung lắc"
            trend_tone = "warning"
        else:
            trend = "Hồi phục kỹ thuật"
            trend_tone = "info"
    else:
        trend = "Trung tính"
        trend_tone = "info"

    # Attach MA20/50/200 to series (keep up to 1300 bars ~5 years)
    series = []
    start_idx = max(0, len(bars) - 1300)
    for i in range(start_idx, len(bars)):
        b = bars[i]
        series.append({
            "date": b["date"],
            "o": b["open"],
            "h": b["high"],
            "l": b["low"],
            "c": b["close"],
            "v": b["vol"],
            "ma20": ma20[i],
            "ma50": ma50[i],
            "ma200": ma200[i],
        })

    return {
        "code": code,
        "name": name,
        "market": market,
        "available": True,
        "date": latest["date"],
        "close": latest["close"],
        "change": chg,
        "pct": pct,
        "chg_1m": chg_1m,
        "chg_3m": chg_3m,
        "ytd_pct": ytd_pct,
        "high_52w": round(high_52w, 2),
        "low_52w": round(low_52w, 2),
        "drawdown_52w": dd_52w,
        "ma20": m20,
        "ma50": m50,
        "ma200": m200,
        "dist_ma50_pct": round(((latest["close"] / m50) - 1.0) * 100.0, 2) if m50 else None,
        "dist_ma200_pct": round(((latest["close"] / m200) - 1.0) * 100.0, 2) if m200 else None,
        "rsi14": rsi,
        "trend": trend,
        "trend_tone": trend_tone,
        "spark": [b["close"] for b in bars[-45:]],
        "series": series,
        "ytd_comparison": _compute_daily_ytd_comparison(bars, "close", 2),
    }


def fetch_vn_market_indices() -> dict[str, Any]:
    major_results: dict[str, Any] = {}
    sector_bars: dict[str, list[dict[str, Any]]] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
        major_futs = {
            ex.submit(_fetch_vn_ohlcv, code, 5): (code, name, mkt)
            for code, name, mkt in VN_MAJOR_INDICES
        }
        sector_futs = {
            ex.submit(_fetch_vn_ohlcv, code, 2): (code, name, short)
            for code, name, short in VN_SECTOR_INDICES
        }
        flow_fut = ex.submit(_fetch_cafef_foreign_and_liquidity)

        for fut in concurrent.futures.as_completed(major_futs):
            code, name, mkt = major_futs[fut]
            try:
                bars = fut.result()
                major_results[code] = _analyze_index_bars(code, name, mkt, bars)
            except Exception:
                major_results[code] = {"code": code, "name": name, "market": mkt, "available": False}

        for fut in concurrent.futures.as_completed(sector_futs):
            code, _, _ = sector_futs[fut]
            try:
                sector_bars[code] = fut.result()
            except Exception:
                sector_bars[code] = []

        try:
            flow = flow_fut.result()
        except Exception:
            flow = {"foreign_daily": [], "liquidity_bn": None, "total_value_bn": None}

    vni = major_results.get("VNINDEX") or {}
    vni_1m = vni.get("chg_1m") or 0.0
    vni_ytd = vni.get("ytd_pct") or 0.0

    sectors = []
    for code, name_vi, short_name in VN_SECTOR_INDICES:
        bars = sector_bars.get(code) or []
        if len(bars) < 25:
            continue
        closes = [b["close"] for b in bars]
        latest = bars[-1]
        prev = bars[-2]
        c_1w = bars[-6]["close"] if len(bars) >= 6 else prev["close"]
        c_1m = bars[-22]["close"] if len(bars) >= 22 else bars[0]["close"]
        c_3m = bars[-64]["close"] if len(bars) >= 64 else bars[0]["close"]

        cur_yr = latest["date"][:4]
        prev_yr = [b for b in bars if b["date"][:4] < cur_yr]
        ytd_base = prev_yr[-1]["close"] if prev_yr else bars[0]["close"]

        chg_1d = round(((latest["close"] / prev["close"]) - 1.0) * 100.0, 2)
        chg_1w = round(((latest["close"] / c_1w) - 1.0) * 100.0, 2)
        chg_1m = round(((latest["close"] / c_1m) - 1.0) * 100.0, 2)
        chg_3m = round(((latest["close"] / c_3m) - 1.0) * 100.0, 2)
        chg_ytd = round(((latest["close"] / ytd_base) - 1.0) * 100.0, 2)
        ma50 = _sma(closes, 50)[-1]
        above_ma50 = bool(ma50 and latest["close"] >= ma50)

        sectors.append({
            "code": code,
            "name": name_vi,
            "short": short_name,
            "close": latest["close"],
            "chg_1d": chg_1d,
            "chg_1w": chg_1w,
            "chg_1m": chg_1m,
            "chg_3m": chg_3m,
            "chg_ytd": chg_ytd,
            "rs_1m": round(chg_1m - vni_1m, 2),
            "rs_ytd": round(chg_ytd - vni_ytd, 2),
            "above_ma50": above_ma50,
            "rsi14": _rsi14(closes, 14),
        })

    return {
        "source": "VNDirect DChart / DNSE & CafeF (HOSE · HNX · UPCoM)",
        "indices": major_results,
        "sectors": sectors,
        "flow": flow,
    }


# =============================================================================
# 7. FX (USD/VND & DXY 5-Year History) & Global Markets Ticker Strip
# =============================================================================

def _fetch_yahoo_chart(symbol: str, range_str: str = "5y", interval: str = "1d") -> dict[str, Any] | None:
    enc = urllib.parse.quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{enc}?range={range_str}&interval={interval}"
    r = requests.get(url, headers=HEADERS, timeout=10)
    if r.status_code != 200:
        return None
    res_list = r.json().get("chart", {}).get("result")
    if not res_list:
        return None
    res = res_list[0]
    meta = res.get("meta", {})
    timestamps = res.get("timestamp") or []
    closes = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []

    series = []
    seen_dates = set()
    for ts, c in zip(timestamps, closes):
        if c is None or (isinstance(c, float) and math.isnan(c)):
            continue
        dt = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
        if dt in seen_dates:
            series[-1]["value"] = round(float(c), 4)
        else:
            seen_dates.add(dt)
            series.append({"date": dt, "value": round(float(c), 4)})

    price = _safe_float(meta.get("regularMarketPrice")) or (series[-1]["value"] if series else None)
    prev = _safe_float(meta.get("chartPreviousClose")) or _safe_float(meta.get("previousClose"))
    if len(series) >= 2 and (prev is None or range_str != "5d"):
        prev = series[-2]["value"]
    change = round(price - prev, 4) if (price is not None and prev) else None
    pct = round(((price / prev) - 1.0) * 100.0, 2) if (price is not None and prev) else None

    return {
        "symbol": symbol,
        "price": round(price, 4) if price is not None else None,
        "prev_close": round(prev, 4) if prev is not None else None,
        "change": change,
        "pct": pct,
        "series": series,
    }


def fetch_fx_and_markets() -> dict[str, Any]:
    tasks = {
        "usdvnd": ("VND=X", "5y", "1d"),
        "dxy": ("DX-Y.NYB", "5y", "1d"),
        "gold": ("GC=F", "1y", "1d"),
        "wti": ("CL=F", "1y", "1d"),
        "sp500": ("^GSPC", "5y", "1d"),
        "vix": ("^VIX", "3mo", "1d"),
        "us10y": ("^TNX", "5y", "1d"),
    }

    results: dict[str, Any] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(_fetch_yahoo_chart, sym, rng, itv): k for k, (sym, rng, itv) in tasks.items()}
        er_fut = ex.submit(lambda: requests.get("https://open.er-api.com/v6/latest/USD", headers=HEADERS, timeout=7).json())

        for fut in concurrent.futures.as_completed(futs):
            k = futs[fut]
            try:
                results[k] = fut.result()
            except Exception:
                results[k] = None
        try:
            er_json = er_fut.result()
            results["open_er_vnd"] = er_json.get("rates", {}).get("VND")
        except Exception:
            results["open_er_vnd"] = None

    usdvnd_series = [p for p in ((results.get("usdvnd") or {}).get("series") or []) if 18000 <= p["value"] <= 35000]
    dxy_series = (results.get("dxy") or {}).get("series") or []
    sp500_series = (results.get("sp500") or {}).get("series") or []
    us10y_series = (results.get("us10y") or {}).get("series") or []

    dxy_map = {p["date"]: p["value"] for p in dxy_series}
    fx_dual = []
    last_dxy = None
    for p in usdvnd_series:
        if p["date"] in dxy_map:
            last_dxy = dxy_map[p["date"]]
        fx_dual.append({"date": p["date"], "usdvnd": round(p["value"], 1), "dxy": round(last_dxy, 2) if last_dxy else None})

    spot_price = (results.get("usdvnd") or {}).get("price")
    if not spot_price or not (18000 <= spot_price <= 35000):
        spot_price = results.get("open_er_vnd") or (usdvnd_series[-1]["value"] if usdvnd_series else None)

    tickers = []

    def add_ticker(label: str, key: str, unit: str = "", digits: int = 2):
        obj = results.get(key)
        if obj and obj.get("price") is not None:
            tickers.append({
                "label": label,
                "price": round(obj["price"], digits),
                "change": round(obj["change"], digits) if obj.get("change") is not None else None,
                "pct": obj.get("pct"),
                "unit": unit,
            })

    if spot_price:
        tickers.append({
            "label": "USD/VND Spot",
            "price": round(spot_price, 0),
            "change": round((results.get("usdvnd") or {}).get("change") or 0, 0),
            "pct": (results.get("usdvnd") or {}).get("pct"),
            "unit": "₫",
        })
    add_ticker("DXY", "dxy", "", 2)
    add_ticker("US 10Y", "us10y", "%", 2)
    add_ticker("S&P 500", "sp500", "", 1)
    add_ticker("Vàng (XAU)", "gold", "$", 1)
    add_ticker("Dầu WTI", "wti", "$", 2)
    add_ticker("VIX", "vix", "", 2)

    return {
        "usdvnd_spot": {
            "price": round(spot_price, 1) if spot_price else None,
            "pct": (results.get("usdvnd") or {}).get("pct"),
            "open_er_ref": round(results["open_er_vnd"], 1) if results.get("open_er_vnd") else None,
        },
        "dxy": {
            "price": (results.get("dxy") or {}).get("price"),
            "change": (results.get("dxy") or {}).get("change"),
            "pct": (results.get("dxy") or {}).get("pct"),
        },
        "vix": (results.get("vix") or {}).get("price"),
        "gold_price": (results.get("gold") or {}).get("price"),
        "wti_price": (results.get("wti") or {}).get("price"),
        "fx_dual_history": fx_dual,
        "overlay_series": {
            "usdvnd": [{"date": p["date"], "v": round(p["value"], 1)} for p in usdvnd_series],
            "dxy": [{"date": p["date"], "v": round(p["value"], 2)} for p in dxy_series],
            "us10y": [{"date": p["date"], "v": round(p["value"], 2)} for p in us10y_series],
            "sp500": [{"date": p["date"], "v": round(p["value"], 1)} for p in sp500_series],
        },
        "usdvnd_ytd_comparison": _compute_daily_ytd_comparison(usdvnd_series, "value", 0),
        "tickers": tickers,
    }


# =============================================================================
# 8. Vietcombank Official Exchange Rate XML & Optional SBV (Auto-Skipped if Down)
# =============================================================================

def fetch_vietcombank_xml() -> dict[str, Any]:
    url = "https://portal.vietcombank.com.vn/Usercontrols/TVPortal.TyGia/pXML.aspx"
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    updated = root.findtext("DateTime", "").strip()

    wanted = ["USD", "EUR", "JPY", "GBP", "CNY", "AUD", "SGD", "KRW", "CHF", "THB"]
    rates_map: dict[str, dict[str, Any]] = {}
    for ex in root.findall("Exrate"):
        code = (ex.attrib.get("CurrencyCode") or "").strip().upper()
        if code not in wanted:
            continue
        buy = _safe_float(ex.attrib.get("Buy"))
        transfer = _safe_float(ex.attrib.get("Transfer"))
        sell = _safe_float(ex.attrib.get("Sell"))
        rates_map[code] = {
            "code": code,
            "name": (ex.attrib.get("CurrencyName") or "").strip().title(),
            "buy": buy,
            "transfer": transfer,
            "sell": sell,
            "spread": round(sell - buy, 2) if (sell and buy) else None,
        }

    ordered = [rates_map[c] for c in wanted if c in rates_map]
    return {
        "source": "Vietcombank (VCB XML)",
        "updated_at": updated,
        "usd": rates_map.get("USD"),
        "currencies": ordered,
    }


def fetch_sbv_optional() -> dict[str, Any]:
    try:
        t_start = time.time()
        r = requests.get("https://sbv.gov.vn/t%E1%BB%B7-gi%C3%A1", headers=HEADERS, timeout=(2.5, 3.0), stream=True)
        if r.status_code != 200:
            return {"available": False}
        chunks = []
        for chunk in r.iter_content(chunk_size=16384):
            if chunk:
                chunks.append(chunk)
            if time.time() - t_start > 4.5:
                r.close()
                return {"available": False}
        raw_html = b"".join(chunks).decode("utf-8", errors="ignore")
        if len(raw_html) < 2000:
            return {"available": False}

        clean = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw_html, flags=re.S | re.I)
        clean = re.sub(r"<[^>]+>", " | ", clean)
        clean = re.sub(r"\s+", " ", clean)

        m_central = re.search(r"1\s*Đô la Mỹ\s*=\s*(?:\|\s*)*([\d.,]+)\s*VND", clean, re.I)
        m_cdate = re.search(r"áp dụng cho ngày\s*(?:\|\s*)*(\d{1,2}/\d{1,2}/\d{4})", clean, re.I)
        central_rate = _parse_vn_number(m_central.group(1)) if m_central else None

        m_ref = re.search(r"USD\s*\|\s*Đô la Mỹ\s*\|\s*([\d.,]+)\s*\|\s*([\d.,]+)", clean, re.I)
        ref_buy = _parse_vn_number(m_ref.group(1)) if m_ref else None
        ref_sell = _parse_vn_number(m_ref.group(2)) if m_ref else None
        m_rdate = re.search(r"Tỷ giá áp dụng cho ngày\s*(?:\|\s*)*(\d{1,2}/\d{1,2}/\d{4})", clean, re.I)

        if central_rate is None or not (18000 <= central_rate <= 35000):
            return {"available": False}

        return {
            "available": True,
            "source": "Ngân hàng Nhà nước Việt Nam (SBV)",
            "central_rate": round(central_rate),
            "central_date": m_cdate.group(1) if m_cdate else None,
            "ceiling": round(central_rate * 1.05),
            "floor": round(central_rate * 0.95),
            "band_pct": 5.0,
            "ref_buy": round(ref_buy) if ref_buy and 18000 <= ref_buy <= 35000 else None,
            "ref_sell": round(ref_sell) if ref_sell and 18000 <= ref_sell <= 35000 else None,
            "ref_date": m_rdate.group(1) if m_rdate else None,
        }
    except Exception:
        return {"available": False}


# =============================================================================
# 9. World Bank Annual Comparison (Vietnam vs US: GDP, CPI, Unemployment)
# =============================================================================

def fetch_worldbank_comparison() -> dict[str, Any]:
    indicators = {
        "gdp_growth": "NY.GDP.MKTP.KD.ZG",
        "cpi_annual": "FP.CPI.TOTL.ZG",
        "unemployment": "SL.UEM.TOTL.ZS",
    }
    cur_year = _now_vn().year
    start_year = cur_year - 8
    out: dict[str, Any] = {"source": "World Bank Open Data (WDI)", "metrics": {}}

    def _get_ind(code: str):
        url = (
            f"https://api.worldbank.org/v2/country/VNM;USA/indicator/{code}"
            f"?format=json&per_page=40&date={start_year}:{cur_year}"
        )
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        j = r.json()
        rows = j[1] if isinstance(j, list) and len(j) > 1 and isinstance(j[1], list) else []
        by_country: dict[str, dict[str, float]] = {"VNM": {}, "USA": {}}
        for item in rows:
            iso = item.get("countryiso3code")
            yr = item.get("date")
            val = item.get("value")
            if iso in by_country and yr and val is not None:
                by_country[iso][yr] = round(float(val), 2)
        years = sorted(set(by_country["VNM"].keys()) | set(by_country["USA"].keys()))
        return {
            "years": years,
            "VNM": [by_country["VNM"].get(y) for y in years],
            "USA": [by_country["USA"].get(y) for y in years],
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(_get_ind, code): k for k, code in indicators.items()}
        for fut in concurrent.futures.as_completed(futs):
            k = futs[fut]
            try:
                out["metrics"][k] = fut.result()
            except Exception:
                out["metrics"][k] = {"years": [], "VNM": [], "USA": []}

    return out


# =============================================================================
# 10. PMI & Vietnam Monthly CPI (Verified Archive + Live Auto-Appending RSS)
# =============================================================================

ENG_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def _merge_series(base: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_d = {p["date"]: round(float(p["value"]), 1) for p in base if p.get("value") is not None}
    for p in extra:
        if p.get("date") and p.get("value") is not None:
            by_d[p["date"]] = round(float(p["value"]), 1)
    return [{"date": d, "value": by_d[d]} for d in sorted(by_d.keys())]


def fetch_pmi_and_vn_macro() -> dict[str, Any]:
    baseline = _load_json(BASELINE_FILE, {})
    store = _load_json(HISTORY_STORE_FILE, {"us_ism_mfg": [], "us_ism_svc": [], "vn_mfg_pmi": [], "vn_cpi_latest": None})

    us_mfg_extra = list(store.get("us_ism_mfg") or [])
    us_svc_extra = list(store.get("us_ism_svc") or [])
    vn_pmi_extra = list(store.get("vn_mfg_pmi") or [])
    vn_cpi_latest = store.get("vn_cpi_latest") or baseline.get("vn_cpi_latest")
    sp_releases: list[dict[str, str]] = []

    def _scan_ism_rss():
        url = (
            "https://news.google.com/rss/search?"
            "q=%22Manufacturing+PMI%22+OR+%22Services+PMI%22+ISM+%22PR+Newswire%22&hl=en-US&gl=US&ceid=US:en"
        )
        r = requests.get(url, headers=HEADERS, timeout=8)
        if r.status_code != 200:
            return
        root = ET.fromstring(r.text)
        for it in root.findall(".//item")[:20]:
            title = html.unescape(it.findtext("title", ""))
            m = re.search(
                r"(Manufacturing|Services)\s+PMI[®]?\s+at\s+(\d{2}\.\d)%?;\s+([A-Za-z]+)\s+(202\d)",
                title,
                re.I,
            )
            if m:
                kind = m.group(1).lower()
                val = float(m.group(2))
                mo = ENG_MONTHS.get(m.group(3).lower())
                yr = int(m.group(4))
                if mo and 30.0 <= val <= 75.0:
                    pt = {"date": f"{yr:04d}-{mo:02d}", "value": val}
                    if kind == "manufacturing":
                        us_mfg_extra.append(pt)
                    else:
                        us_svc_extra.append(pt)

    def _scan_vn_rss():
        url_pmi = (
            "https://news.google.com/rss/search?"
            "q=%22PMI%22+%22Vi%E1%BB%87t+Nam%22+%22S%26P+Global%22&hl=vi&gl=VN&ceid=VN:vi"
        )
        r = requests.get(url_pmi, headers=HEADERS, timeout=8)
        if r.status_code == 200:
            root = ET.fromstring(r.text)
            for it in root.findall(".//item")[:15]:
                title = html.unescape(it.findtext("title", ""))
                pub = it.findtext("pubDate", "")
                m_val = re.search(r"(?:đạt|lên|xuống|giảm về|tăng lên|ở mức)\s*(\d{2}[.,]\d)", title, re.I)
                m_mo = re.search(r"tháng\s*(\d{1,2})(?:/(202\d))?", title, re.I)
                if m_val and m_mo:
                    val = float(m_val.group(1).replace(",", "."))
                    mo = int(m_mo.group(1))
                    yr = int(m_mo.group(2)) if m_mo.group(2) else None
                    if not yr and pub:
                        try:
                            yr = email.utils.parsedate_to_datetime(pub).year
                        except Exception:
                            yr = _now_vn().year
                    if yr and 1 <= mo <= 12 and 35.0 <= val <= 70.0:
                        vn_pmi_extra.append({"date": f"{yr:04d}-{mo:02d}", "value": val})

        url_cpi = (
            "https://news.google.com/rss/search?"
            "q=%22CPI%22+%22Vi%E1%BB%87t+Nam%22+%22th%C3%A1ng%22+%22t%C4%83ng%22&hl=vi&gl=VN&ceid=VN:vi"
        )
        r2 = requests.get(url_cpi, headers=HEADERS, timeout=8)
        if r2.status_code == 200:
            root2 = ET.fromstring(r2.text)
            nonlocal vn_cpi_latest
            for it in root2.findall(".//item")[:10]:
                title = html.unescape(it.findtext("title", ""))
                m = re.search(r"(\d{1,2})\s*tháng\s*(?:năm\s*)?(202\d).*?CPI\s*tăng\s*(\d+[.,]\d+)%", title, re.I)
                if m:
                    mo = int(m.group(1))
                    yr = int(m.group(2))
                    ytd = float(m.group(3).replace(",", "."))
                    new_period = f"{yr:04d}-{mo:02d}"
                    cur_period = (vn_cpi_latest or {}).get("period", "2020-01")
                    if new_period > cur_period:
                        vn_cpi_latest = {
                            "period": new_period,
                            "yoy": ytd,
                            "ytd_avg": ytd,
                            "core_ytd": (vn_cpi_latest or {}).get("core_ytd"),
                            "mom": None,
                            "source": "GSO / Tin chính thức mới nhất",
                        }

    def _scan_spglobal_links():
        r = requests.get("https://www.pmi.spglobal.com/Public/Release/PressReleases?language=en", headers=HEADERS, timeout=7)
        if r.status_code != 200:
            return
        blocks = re.findall(r'<div class="listItem">(.*?)</div>', r.text, re.S)
        for b in blocks:
            ti = re.search(r"releaseTitle[^>]*>(.*?)</span>", b, re.S)
            dt = re.search(r"releaseDate[^>]*>(.*?)</span>", b, re.S)
            lk = re.search(r'href="([^"]+)"', b)
            if ti and lk:
                title = html.unescape(re.sub(r"\s+", " ", ti.group(1)).strip())
                date_str = re.sub(r"&nbsp;|\s+", " ", dt.group(1)).strip() if dt else ""
                if any(k in title for k in [
                    "Vietnam Manufacturing PMI",
                    "US Manufacturing PMI",
                    "US Services PMI",
                    "Eurozone Manufacturing PMI",
                ]) and "(" not in title:
                    sp_releases.append({"title": title, "date": date_str, "url": lk.group(1)})
                    if len(sp_releases) >= 6:
                        break

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        futs = [ex.submit(_scan_ism_rss), ex.submit(_scan_vn_rss), ex.submit(_scan_spglobal_links)]
        for f in futs:
            try:
                f.result()
            except Exception:
                pass

    us_mfg = _merge_series(baseline.get("us_ism_mfg", []), us_mfg_extra)
    us_svc = _merge_series(baseline.get("us_ism_svc", []), us_svc_extra)
    vn_pmi = _merge_series(baseline.get("vn_mfg_pmi", []), vn_pmi_extra)

    _save_json(HISTORY_STORE_FILE, {
        "us_ism_mfg": us_mfg,
        "us_ism_svc": us_svc,
        "vn_mfg_pmi": vn_pmi,
        "vn_cpi_latest": vn_cpi_latest,
    })

    return {
        "source": "ISM (US) & S&P Global (Vietnam) + Auto-RSS",
        "us_ism_mfg": us_mfg,
        "us_ism_svc": us_svc,
        "vn_mfg_pmi": vn_pmi,
        "vn_cpi_latest": vn_cpi_latest,
        "official_links": sp_releases,
    }


# =============================================================================
# 11. Live US 3-Star High-Impact Economic Calendar & Vietnam Policy News
# =============================================================================

MACRO_KEYWORDS = (
    "cpi", "pce", "pmi", "fomc", "fed", "unemployment", "payroll", "non-farm", "nonfarm",
    "nfp", "gdp", "ppi", "interest rate", "rate statement", "jobless", "retail sales",
    "powell", "inflation", "jolts", "ism", "adp", "durable goods", "personal spending",
    "personal income", "consumer sentiment", "hourly earnings",
)

# Drop noisy FOMC dot-plot sub-rows from FXStreet while keeping core FOMC decision/statement/conference
_CAL_EXCLUDE_PATTERNS = (
    "interest rate projections - 1st",
    "interest rate projections - 2nd",
    "interest rate projections - 3rd",
    "interest rate projections - current",
    "interest rate projections - longer",
)

# Canonical normalization to deduplicate & merge TradingView + FXStreet seamlessly
def _canonical_us_event(title: str) -> tuple[str, str, str, str]:
    """
    Returns (canonical_key, display_title, category_vi, polarity)
    polarity: 'higher_hot' (inflation), 'lower_good' (unemployment), 'higher_good' (growth/jobs/PMI), 'neutral'
    """
    t = re.sub(r"\s+", " ", (title or "").strip())
    tl = t.lower()

    if "core" in tl and ("pce" in tl or "personal consumption expenditures" in tl):
        if "yoy" in tl or "y/y" in tl:
            return "core_pce_yoy", "Core PCE Mỹ (YoY) · Lạm phát Lõi Fed", "Lạm phát", "higher_hot"
        return "core_pce_mom", "Core PCE Mỹ (MoM) · Lạm phát Lõi Fed", "Lạm phát", "higher_hot"
    if ("pce" in tl or "personal consumption expenditures" in tl) and "price" in tl:
        if "yoy" in tl or "y/y" in tl:
            return "pce_yoy", "PCE Mỹ (YoY)", "Lạm phát", "higher_hot"
        return "pce_mom", "PCE Mỹ (MoM)", "Lạm phát", "higher_hot"

    if ("core inflation" in tl) or ("consumer price index ex food" in tl) or ("core cpi" in tl):
        if "yoy" in tl or "y/y" in tl:
            return "core_cpi_yoy", "Core CPI Mỹ (YoY) · Lạm phát Lõi", "Lạm phát", "higher_hot"
        return "core_cpi_mom", "Core CPI Mỹ (MoM) · Lạm phát Lõi", "Lạm phát", "higher_hot"
    if ("inflation rate" in tl) or ("consumer price index" in tl) or (re.search(r"\bcpi\b", tl)):
        if "yoy" in tl or "y/y" in tl:
            return "cpi_yoy", "CPI Mỹ (YoY) · Lạm phát Toàn phần", "Lạm phát", "higher_hot"
        return "cpi_mom", "CPI Mỹ (MoM) · Lạm phát Toàn phần", "Lạm phát", "higher_hot"

    if ("producer price" in tl or re.search(r"\bppi\b", tl)):
        is_core = "core" in tl or "ex food" in tl
        is_yoy = "yoy" in tl or "y/y" in tl
        if is_core and is_yoy:
            return "core_ppi_yoy", "Core PPI Mỹ (YoY) · Giá Sản xuất Lõi", "Lạm phát", "higher_hot"
        if is_core:
            return "core_ppi_mom", "Core PPI Mỹ (MoM) · Giá Sản xuất Lõi", "Lạm phát", "higher_hot"
        if is_yoy:
            return "ppi_yoy", "PPI Mỹ (YoY) · Giá Sản xuất", "Lạm phát", "higher_hot"
        return "ppi_mom", "PPI Mỹ (MoM) · Giá Sản xuất", "Lạm phát", "higher_hot"

    if "non farm payroll" in tl or "nonfarm payroll" in tl or "non-farm" in tl:
        return "nfp", "Bảng lương Phi nông nghiệp Mỹ (NFP)", "Việc làm", "higher_good"
    if "adp" in tl and "employment" in tl:
        return "adp", "Việc làm Khu vực Tư nhân Mỹ (ADP)", "Việc làm", "higher_good"
    if "unemployment rate" in tl:
        return "unemp", "Tỉ lệ Thất nghiệp Mỹ (UNRATE)", "Việc làm", "lower_good"
    if "jolts" in tl or "job openings" in tl:
        return "jolts", "Việc làm Đang tuyển dụng Mỹ (JOLTs)", "Việc làm", "higher_good"
    if "initial jobless" in tl or "unemployment claims" in tl:
        return "jobless_claims", "Đơn xin Trợ cấp Thất nghiệp Tuần (Mỹ)", "Việc làm", "lower_good"
    if "average hourly earnings" in tl:
        if "yoy" in tl or "y/y" in tl:
            return "ahe_yoy", "Thu nhập Bình quân Theo giờ (YoY)", "Lạm phát", "higher_hot"
        return "ahe_mom", "Thu nhập Bình quân Theo giờ (MoM)", "Lạm phát", "higher_hot"

    if "ism manufacturing" in tl:
        return "ism_mfg", "PMI Sản xuất Mỹ (ISM Manufacturing)", "PMI", "higher_good"
    if "ism services" in tl or "ism non-manufacturing" in tl:
        return "ism_svc", "PMI Dịch vụ Mỹ (ISM Services)", "PMI", "higher_good"
    if "s&p global manufacturing pmi" in tl or "flash manufacturing pmi" in tl:
        return "sp_mfg_pmi", "PMI Sản xuất Mỹ (S&P Global)", "PMI", "higher_good"
    if "s&p global services pmi" in tl or "flash services pmi" in tl:
        return "sp_svc_pmi", "PMI Dịch vụ Mỹ (S&P Global)", "PMI", "higher_good"

    if "gdp" in tl:
        return "gdp", f"Tăng trưởng GDP Mỹ ({t})", "Tăng trưởng", "higher_good"
    if tl in ("interest rate", "fed interest rate decision", "federal funds rate") or "interest rate decision" in tl:
        return "fomc_rate", "Quyết định Lãi suất Fed (FOMC)", "Fed / FOMC", "neutral"
    if "fomc press conference" in tl:
        return "fomc_press", "Họp báo Chủ tịch Fed (FOMC)", "Fed / FOMC", "neutral"
    if "monetary policy statement" in tl or "fomc statement" in tl:
        return "fomc_stmt", "Tuyên bố Chính sách Tiền tệ Fed", "Fed / FOMC", "neutral"
    if "fomc economic projections" in tl:
        return "fomc_proj", "Dự báo Kinh tế & Dot-Plot FOMC", "Fed / FOMC", "neutral"
    if "fomc minutes" in tl:
        return "fomc_min", "Biên bản Họp FOMC (Fed Minutes)", "Fed / FOMC", "neutral"
    if "powell" in tl:
        return "powell", f"Phát biểu Chủ tịch Fed ({t})", "Fed / FOMC", "neutral"

    if "retail sales control" in tl:
        return "retail_ctrl", "Doanh số Bán lẻ Nhóm Kiểm soát Mỹ", "Tiêu dùng", "higher_good"
    if "core retail sales" in tl:
        return "core_retail", "Doanh số Bán lẻ Lõi Mỹ (MoM)", "Tiêu dùng", "higher_good"
    if "retail sales" in tl:
        return "retail_mom", "Doanh số Bán lẻ Mỹ (MoM)", "Tiêu dùng", "higher_good"
    if "personal spending" in tl:
        return "personal_spending", "Chi tiêu Cá nhân Mỹ (MoM)", "Tiêu dùng", "higher_good"
    if "personal income" in tl:
        return "personal_income", "Thu nhập Cá nhân Mỹ (MoM)", "Tiêu dùng", "higher_good"
    if "durable goods" in tl:
        return "durable_goods", "Đơn đặt hàng Lâu bền Mỹ (MoM)", "Sản xuất", "higher_good"
    if "michigan consumer sentiment" in tl:
        return "umich", "Tâm lý Người tiêu dùng Mỹ (Michigan)", "Tiêu dùng", "higher_good"
    if "cb consumer confidence" in tl:
        return "cb_conf", "Niềm tin Người tiêu dùng Mỹ (CB)", "Tiêu dùng", "higher_good"
    if "existing home sales" in tl:
        return "home_sales", "Doanh số Bán nhà Hiện hữu Mỹ", "BĐS Mỹ", "higher_good"

    norm_key = re.sub(r"[^a-z0-9]+", "_", tl).strip("_")[:36]
    return norm_key, t, "Vĩ mô Mỹ", "neutral"


def _fmt_cal_val(val: Any, unit: str | None = None, scale: str | None = None) -> str:
    if val is None or val == "":
        return "—"
    if isinstance(val, str):
        s = val.strip()
        return s if s else "—"
    try:
        num = float(val)
    except Exception:
        return str(val)
    u = (unit or "").strip()
    sc = (scale or "").strip().upper()
    if sc == "ZERO":
        sc = ""
    if abs(num - round(num)) < 1e-6 and sc in ("K", "M", "B") and abs(num) >= 10:
        num_str = f"{int(round(num)):,}"
    elif abs(num) >= 100 and u != "%":
        num_str = f"{num:,.1f}".rstrip("0").rstrip(".")
    else:
        num_str = f"{num:.2f}".rstrip("0").rstrip(".")
        if "." not in num_str and u == "%":
            num_str = f"{num:.1f}"
    suffix = f"{sc}{u}" if sc else u
    return f"{num_str}{suffix}"


def _fetch_tv_us_3star(frm_iso: str, to_iso: str) -> list[dict[str, Any]]:
    r = requests.get(
        "https://economic-calendar.tradingview.com/events",
        params={"from": frm_iso, "to": to_iso, "countries": "US"},
        headers={
            **HEADERS,
            "Origin": "https://www.tradingview.com",
            "Referer": "https://www.tradingview.com/",
        },
        timeout=6,
    )
    r.raise_for_status()
    rows = r.json().get("result") or []
    out = []
    for item in rows:
        if item.get("importance") != 1:
            continue
        if (item.get("country") or "").upper() != "US":
            continue
        title = (item.get("title") or "").strip()
        date_raw = item.get("date") or ""
        if not title or not date_raw:
            continue
        try:
            dt_utc = datetime.fromisoformat(date_raw.replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            continue
        unit = item.get("unit") or ""
        scale = item.get("scale") or ""
        out.append({
            "src": "TradingView",
            "dt_utc": dt_utc,
            "raw_title": title,
            "period": (item.get("period") or "").strip(),
            "actual_num": _safe_float(item.get("actual")),
            "forecast_num": _safe_float(item.get("forecast")),
            "previous_num": _safe_float(item.get("previous")),
            "actual": _fmt_cal_val(item.get("actual"), unit, scale),
            "forecast": _fmt_cal_val(item.get("forecast"), unit, scale),
            "previous": _fmt_cal_val(item.get("previous"), unit, scale),
            "unit": unit,
            "scale": scale,
            "official_source": (item.get("source") or "").strip(),
        })
    return out


def _fetch_fxstreet_us_3star(frm_iso: str, to_iso: str) -> list[dict[str, Any]]:
    url = f"https://calendar-api.fxstreet.com/en/api/v1/eventDates/{frm_iso}/{to_iso}"
    r = requests.get(
        url,
        params={"volatilities": "HIGH", "countries": "US"},
        headers={
            **HEADERS,
            "Origin": "https://www.fxstreet.com",
            "Referer": "https://www.fxstreet.com/",
        },
        timeout=6,
    )
    r.raise_for_status()
    rows = r.json()
    if not isinstance(rows, list):
        return []
    out = []
    for item in rows:
        if (item.get("countryCode") or "").upper() != "US":
            continue
        if (item.get("volatility") or "").upper() != "HIGH":
            continue
        title = (item.get("name") or "").strip()
        if not title or any(p in title.lower() for p in _CAL_EXCLUDE_PATTERNS):
            continue
        date_raw = item.get("dateUtc") or ""
        if not date_raw:
            continue
        try:
            dt_utc = datetime.fromisoformat(date_raw.replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            continue
        unit = item.get("unit") or ""
        pot = item.get("potency") or ""
        scale = "" if pot.upper() in ("ZERO", "NONE", "") else pot.upper()
        out.append({
            "src": "FXStreet",
            "dt_utc": dt_utc,
            "raw_title": title,
            "period": "",
            "actual_num": _safe_float(item.get("actual")),
            "forecast_num": _safe_float(item.get("consensus")),
            "previous_num": _safe_float(item.get("previous")),
            "actual": _fmt_cal_val(item.get("actual"), unit, scale),
            "forecast": _fmt_cal_val(item.get("consensus"), unit, scale),
            "previous": _fmt_cal_val(item.get("previous"), unit, scale),
            "unit": unit,
            "scale": scale,
            "official_source": "",
        })
    return out


def _fetch_forexfactory_us_3star_fallback() -> list[dict[str, Any]]:
    r = requests.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", headers=HEADERS, timeout=6)
    r.raise_for_status()
    raw = r.json()
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if (item.get("country") or "").strip().upper() != "USD":
            continue
        if (item.get("impact") or "").strip().lower() != "high":
            continue
        title = (item.get("title") or "").strip()
        date_raw = item.get("date") or ""
        if not title or not date_raw:
            continue
        try:
            dt_utc = datetime.fromisoformat(date_raw).astimezone(timezone.utc)
        except Exception:
            continue
        out.append({
            "src": "ForexFactory",
            "dt_utc": dt_utc,
            "raw_title": title,
            "period": "",
            "actual_num": None,
            "forecast_num": None,
            "previous_num": None,
            "actual": "—",
            "forecast": (item.get("forecast") or "—").strip() or "—",
            "previous": (item.get("previous") or "—").strip() or "—",
            "unit": "",
            "scale": "",
            "official_source": "",
        })
    return out


def fetch_economic_calendar() -> dict[str, Any]:
    """
    Real-time US 3-Star (High Impact) ONLY Economic Calendar.
    Queries TradingView + FXStreet in parallel for sub-minute Actual value updates
    (with ForexFactory fallback), deduplicates & merges best Actual/Forecast/Previous,
    and converts all timestamps to Vietnam Time (GMT+7).
    """
    now_utc = datetime.now(timezone.utc)
    now_vn = now_utc.astimezone(VN_TZ)
    frm_iso = (now_utc - timedelta(days=4)).strftime("%Y-%m-%dT00:00:00.000Z")
    to_iso = (now_utc + timedelta(days=18)).strftime("%Y-%m-%dT23:59:59.000Z")

    tv_rows: list[dict[str, Any]] = []
    fxs_rows: list[dict[str, Any]] = []
    active_sources: list[str] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        fut_tv = ex.submit(_fetch_tv_us_3star, frm_iso, to_iso)
        fut_fxs = ex.submit(_fetch_fxstreet_us_3star, frm_iso, to_iso)
        try:
            tv_rows = fut_tv.result()
            if tv_rows:
                active_sources.append("TradingView")
        except Exception:
            pass
        try:
            fxs_rows = fut_fxs.result()
            if fxs_rows:
                active_sources.append("FXStreet")
        except Exception:
            pass

    all_raw = tv_rows + fxs_rows
    if not all_raw:
        try:
            all_raw = _fetch_forexfactory_us_3star_fallback()
            if all_raw:
                active_sources.append("ForexFactory")
        except Exception:
            pass

    if not all_raw:
        raise RuntimeError("No US 3-star calendar feeds responded")

    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for row in all_raw:
        dt_utc: datetime = row["dt_utc"]
        ckey, disp_title, cat_vi, polarity = _canonical_us_event(row["raw_title"])
        # Bucket key: UTC date+hour+minute + canonical event key
        bkey = (dt_utc.strftime("%Y-%m-%dT%H:%M"), ckey)

        if bkey not in merged:
            dt_vn = dt_utc.astimezone(VN_TZ)
            merged[bkey] = {
                "key": ckey,
                "title": disp_title,
                "raw_title": row["raw_title"],
                "category": cat_vi,
                "polarity": polarity,
                "country": "USD",
                "stars": 3,
                "impact": "High",
                "period": row["period"],
                "official_source": row["official_source"],
                "date_vn": dt_vn.strftime("%d/%m/%Y"),
                "date_ymd": dt_vn.strftime("%Y-%m-%d"),
                "weekday_vn": ["T2", "T3", "T4", "T5", "T6", "T7", "CN"][dt_vn.weekday()],
                "time_vn": dt_vn.strftime("%H:%M"),
                "iso": dt_vn.isoformat(),
                "ts": dt_utc.timestamp(),
                "actual": row["actual"],
                "forecast": row["forecast"],
                "previous": row["previous"],
                "actual_num": row["actual_num"],
                "forecast_num": row["forecast_num"],
                "previous_num": row["previous_num"],
                "sources": [row["src"]],
                "is_key_macro": True,
            }
        else:
            ev = merged[bkey]
            if row["src"] not in ev["sources"]:
                ev["sources"].append(row["src"])
            # Fill missing Actual / Forecast / Previous from the complementary real-time feed
            if ev["actual"] == "—" and row["actual"] != "—":
                ev["actual"] = row["actual"]
                ev["actual_num"] = row["actual_num"]
            if ev["forecast"] == "—" and row["forecast"] != "—":
                ev["forecast"] = row["forecast"]
                ev["forecast_num"] = row["forecast_num"]
            if ev["previous"] == "—" and row["previous"] != "—":
                ev["previous"] = row["previous"]
                ev["previous_num"] = row["previous_num"]
            if not ev["period"] and row["period"]:
                ev["period"] = row["period"]
            if not ev["official_source"] and row["official_source"]:
                ev["official_source"] = row["official_source"]

    today_ymd = now_vn.strftime("%Y-%m-%d")
    now_ts = now_utc.timestamp()
    events: list[dict[str, Any]] = []

    for ev in merged.values():
        diff_sec = ev["ts"] - now_ts
        has_actual = ev["actual"] != "—"
        is_today = ev["date_ymd"] == today_ymd
        upcoming = diff_sec > 0 and not has_actual
        just_released = (not upcoming) and (diff_sec >= -21600)  # Within last 6 hours

        # Determine surprise direction & market tone vs forecast
        surprise = "none"       # beat | miss | inline | none
        surprise_tone = "neutral"  # success | danger | neutral
        surprise_label = ""
        an, fn = ev["actual_num"], ev["forecast_num"]
        if an is not None and fn is not None:
            diff_v = an - fn
            if abs(diff_v) < 1e-6:
                surprise = "inline"
                surprise_tone = "info"
                surprise_label = "Đúng dự báo"
            elif diff_v > 0:
                surprise = "above"
                surprise_label = "Cao hơn dự báo"
                if ev["polarity"] == "higher_hot" or ev["polarity"] == "lower_good":
                    surprise_tone = "danger"
                elif ev["polarity"] == "higher_good":
                    surprise_tone = "success"
                else:
                    surprise_tone = "warning"
            else:
                surprise = "below"
                surprise_label = "Thấp hơn dự báo"
                if ev["polarity"] == "higher_hot" or ev["polarity"] == "lower_good":
                    surprise_tone = "success"
                elif ev["polarity"] == "higher_good":
                    surprise_tone = "danger"
                else:
                    surprise_tone = "warning"

        ev["upcoming"] = upcoming
        ev["is_today"] = is_today
        ev["just_released"] = just_released
        ev["has_actual"] = has_actual
        ev["surprise"] = surprise
        ev["surprise_tone"] = surprise_tone
        ev["surprise_label"] = surprise_label
        # Clean internal numeric helper fields not needed by UI
        ev.pop("actual_num", None)
        ev.pop("forecast_num", None)
        ev.pop("previous_num", None)
        events.append(ev)

    events.sort(key=lambda x: x["ts"])

    today_count = sum(1 for e in events if e["is_today"])
    upcoming_count = sum(1 for e in events if e["upcoming"])
    released_count = sum(1 for e in events if e["has_actual"])

    return {
        "source": f"{' + '.join(active_sources)} Live (Chỉ Mỹ ★★★ · GMT+7)",
        "updated_at_vn": now_vn.strftime("%H:%M:%S · %d/%m/%Y"),
        "updated_ts": now_ts,
        "today_count": today_count,
        "upcoming_count": upcoming_count,
        "released_count": released_count,
        "events": events,
    }


def fetch_calendar_fast(force: bool = True) -> dict[str, Any]:
    """Dedicated ultra-fast US 3-star calendar fetch (15s micro-cache unless forced)."""
    data, _ = _get_cached_or_fetch("calendar", fetch_economic_calendar, force=force, respect_strict_cooldown=False)
    return data or {"source": "Live US 3-Star", "updated_at_vn": _now_vn().strftime("%H:%M:%S · %d/%m/%Y"), "events": []}


NEWS_FEEDS = [
    ("CafeF Vĩ mô", "https://cafef.vn/vi-mo-dau-tu.rss", "Chính sách VN"),
    ("CafeF Ngân hàng", "https://cafef.vn/tai-chinh-ngan-hang.rss", "SBV & Tiền tệ"),
    ("VnEconomy", "https://vneconomy.vn/tai-chinh.rss", "SBV & Tiền tệ"),
    ("VnExpress", "https://vnexpress.net/rss/kinh-doanh.rss", "Chính sách VN"),
    (
        "SBV & Chính sách",
        "https://news.google.com/rss/search?q=%22Ng%C3%A2n+h%C3%A0ng+Nh%C3%A0+n%C6%B0%E1%BB%9Bc%22+OR+%22SBV%22+l%C3%A3i+su%E1%BA%A5t+OR+t%E1%BB%B7+gi%C3%A1+OR+OMO+OR+t%C3%ADn+d%E1%BB%A5ng&hl=vi&gl=VN&ceid=VN:vi",
        "SBV & Tiền tệ",
    ),
    (
        "Vĩ mô & Fed",
        "https://news.google.com/rss/search?q=(Fed+l%C3%A3i+su%E1%BA%A5t+OR+CPI+M%E1%BB%B9+OR+PCE+OR+PMI+Vi%E1%BB%87t+Nam)&hl=vi&gl=VN&ceid=VN:vi",
        "Fed & Toàn cầu",
    ),
]


def _classify_news(title: str, default_cat: str) -> str:
    t = title.lower()
    if any(k in t for k in ("sbv", "ngân hàng nhà nước", "nhnn", "omo", "tín phiếu", "liên ngân hàng", "điều hành lãi suất", "tái cấp vốn", "tăng trưởng tín dụng")):
        return "SBV & Tiền tệ"
    if any(k in t for k in ("tỷ giá", "usd/vnd", "usd", "đô la", "ngoại hối", "dxy", "vàng")):
        return "Tỷ giá & Ngoại hối"
    if any(k in t for k in ("cpi", "pce", "pmi", "lạm phát", "thất nghiệp", "gdp", "sản xuất")):
        return "Lạm phát & PMI"
    if any(k in t for k in ("fed", "fomc", "powell", "mỹ", "trái phiếu mỹ", "wall street", "ecb", "boj")):
        return "Fed & Toàn cầu"
    return default_cat


def _is_macro_relevant(title: str) -> bool:
    t = title.lower()
    keywords = (
        "sbv", "ngân hàng nhà nước", "nhnn", "lãi suất", "tỷ giá", "usd", "vnd",
        "cpi", "pce", "pmi", "gdp", "lạm phát", "thất nghiệp", "fed", "fomc",
        "vĩ mô", "chính sách", "tiền tệ", "tài khóa", "tín dụng", "omo", "tín phiếu",
        "xuất khẩu", "nhập khẩu", "fdi", "đầu tư công", "trái phiếu", "ngân hàng",
        "kinh tế", "thuế", "dxy", "vàng", "dầu", "chứng khoán", "vn-index", "nghị định",
        "chính phủ", "quốc hội", "bộ tài chính", "thủ tướng",
    )
    return any(k in t for k in keywords)


def _parse_rss_feed(source_name: str, url: str, default_cat: str) -> list[dict[str, Any]]:
    r = requests.get(url, headers=HEADERS, timeout=8)
    if r.status_code != 200:
        return []
    root = ET.fromstring(r.content)
    items = []
    for it in root.findall(".//item")[:20]:
        raw_title = html.unescape((it.findtext("title") or "").strip())
        link = (it.findtext("link") or "").strip()
        pub_raw = (it.findtext("pubDate") or "").strip()
        desc_raw = html.unescape(re.sub(r"<[^>]+>", " ", it.findtext("description") or "")).strip()
        desc = re.sub(r"\s+", " ", desc_raw)[:180]

        if not raw_title or not link:
            continue
        if source_name in ("VnExpress", "CafeF Vĩ mô", "CafeF Ngân hàng", "VnEconomy") and not _is_macro_relevant(raw_title):
            continue

        display_source = source_name
        title = raw_title
        if "news.google.com" in url and " - " in raw_title:
            parts = raw_title.rsplit(" - ", 1)
            title = parts[0].strip()
            display_source = parts[1].strip()

        ts = 0.0
        pub_str = ""
        if pub_raw:
            try:
                dt = email.utils.parsedate_to_datetime(pub_raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                dt_vn = dt.astimezone(VN_TZ)
                ts = dt_vn.timestamp()
                pub_str = dt_vn.strftime("%d/%m %H:%M")
            except Exception:
                pub_str = pub_raw[:16]

        items.append({
            "title": title,
            "summary": desc if "news.google.com" not in url else "",
            "link": link,
            "source": display_source,
            "category": _classify_news(title, default_cat),
            "published_vn": pub_str,
            "ts": ts,
        })
    return items


def fetch_vietnam_policy_news() -> list[dict[str, Any]]:
    all_items: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(_parse_rss_feed, name, url, cat) for name, url, cat in NEWS_FEEDS]
        for fut in concurrent.futures.as_completed(futs):
            try:
                all_items.extend(fut.result())
            except Exception:
                pass

    all_items.sort(key=lambda x: x["ts"], reverse=True)
    seen = set()
    deduped = []
    for it in all_items:
        norm = re.sub(r"[^a-z0-9à-ỹ]+", "", it["title"].lower())[:48]
        if norm in seen:
            continue
        seen.add(norm)
        deduped.append(it)
        if len(deduped) >= 48:
            break
    return deduped


# =============================================================================
# 12. Quantitative Investment Strategy & Sector Rotation Engine (CIO Playbook)
# =============================================================================

def compute_investment_strategy(data: dict[str, Any]) -> dict[str, Any]:
    """
    Transparent, rule-based quantitative macro-market scoring (0–100) across 5 pillars:
      1. Tăng trưởng & PMI (0–20)
      2. Áp lực Lạm phát (0–20)
      3. Chính sách Fed & Đường cong Lợi suất (0–20)
      4. Tỷ giá USD/VND & Khối ngoại (0–20)
      5. Xu hướng Kỹ thuật VN-Index (0–20)
    Outputs: Composite Risk-On Score, Tactical Asset Allocation (%), Sector Rotation Playbook,
    and Concrete Quantitative Trigger Rules ("Nếu... Thì...").
    """
    kpis = data.get("kpis") or {}
    vn_mkt = data.get("vn_market") or {}
    vni = (vn_mkt.get("indices") or {}).get("VNINDEX") or {}
    vn30 = (vn_mkt.get("indices") or {}).get("VN30") or {}
    flow = vn_mkt.get("flow") or {}
    fw = data.get("fedwatch") or {}
    yc = (data.get("rates") or {}).get("yield_curve") or {}
    fx = data.get("fx") or {}
    sbv = data.get("sbv") or {}
    vcb = data.get("vcb") or {}
    vn_cpi = (data.get("inflation") or {}).get("vn_cpi_latest") or {}

    # --- Pillar 1: Growth & PMI (0..20) ---
    vn_pmi = (kpis.get("vn_pmi") or {}).get("value") or 50.0
    us_pmi = (kpis.get("us_pmi") or {}).get("value") or 50.0
    us_svc = (kpis.get("us_pmi") or {}).get("services") or 50.0
    s_growth = 10.0
    s_growth += _clamp((vn_pmi - 50.0) * 1.5, -6.0, 6.0)
    s_growth += _clamp((us_pmi - 50.0) * 0.7, -3.0, 3.0)
    s_growth += 1.0 if us_svc >= 50 else -1.5
    s_growth = round(_clamp(s_growth, 0.0, 20.0), 1)
    growth_note = f"PMI VN {vn_pmi:.1f} · ISM Mỹ {us_pmi:.1f}"

    # --- Pillar 2: Inflation Pressure (0..20, higher = calmer inflation) ---
    us_cpi = (kpis.get("us_cpi") or {}).get("value") or 3.0
    us_cpce = (kpis.get("us_core_pce") or {}).get("value") or 2.8
    vn_cpi_yoy = vn_cpi.get("yoy") or 3.5
    s_infl = 20.0
    s_infl -= max(0.0, (us_cpce - 2.0) * 4.5)
    s_infl -= max(0.0, (us_cpi - 2.2) * 2.5)
    s_infl -= max(0.0, (vn_cpi_yoy - 4.0) * 2.2)
    s_infl = round(_clamp(s_infl, 2.0, 20.0), 1)
    infl_note = f"Core PCE {us_cpce:.2f}% · CPI VN {vn_cpi_yoy:.2f}%"

    # --- Pillar 3: Monetary Policy, FedWatch & Yield Curve (0..20) ---
    contracts = fw.get("contracts") or []
    nxt = contracts[0] if contracts else {}
    six_m = contracts[min(4, len(contracts) - 1)] if contracts else {}
    prob_hike = nxt.get("prob_hike", 0.0)
    prob_cut = nxt.get("prob_cut", 0.0)
    delta_6m = six_m.get("delta_bps", 0.0)
    spread_10_2 = (yc.get("latest") or {}).get("spread_10y_2y")
    vix = fx.get("vix") or 18.0

    s_mon = 11.0
    s_mon += (prob_cut - prob_hike) * 0.055
    s_mon += _clamp(-delta_6m * 0.06, -4.5, 4.5)
    if spread_10_2 is not None:
        s_mon += 2.0 if spread_10_2 > 0.1 else (-2.5 if spread_10_2 < -0.1 else 0.0)
    if vix > 25:
        s_mon -= 2.5
    elif vix < 16:
        s_mon += 1.5
    s_mon = round(_clamp(s_mon, 2.0, 20.0), 1)
    mon_bias = nxt.get("bias", "Giữ nguyên")
    mon_note = f"Fed {mon_bias} ({round(max(prob_hike, prob_cut, nxt.get('prob_hold', 0)))}%) · VIX {vix:.1f}"

    # --- Pillar 4: FX USD/VND & Foreign Flow (0..20) ---
    dxy = (fx.get("dxy") or {}).get("price") or 103.0
    vcb_sell = (vcb.get("usd") or {}).get("sell") or (fx.get("usdvnd_spot") or {}).get("price") or 25500
    f5d = flow.get("foreign_5d_bn")
    f20d = flow.get("foreign_20d_bn")

    s_fx = 12.0
    s_fx += _clamp((103.0 - dxy) * 0.7, -4.0, 4.0)
    gap_pct = None
    if sbv.get("available") and sbv.get("ceiling"):
        gap_pct = round(((sbv["ceiling"] - vcb_sell) / sbv["ceiling"]) * 100.0, 2)
        if gap_pct < 1.0:
            s_fx -= 4.0
        elif gap_pct < 2.2:
            s_fx -= 1.5
        else:
            s_fx += 2.0
    if f5d is not None:
        s_fx += _clamp(f5d / 600.0, -4.0, 4.0)
    s_fx = round(_clamp(s_fx, 2.0, 20.0), 1)
    f5_txt = f"Ngoại 5p: {f5d:+.0f} tỷ" if f5d is not None else "Ngoại: —"
    fx_note = f"DXY {dxy:.1f} · {f5_txt}"

    # --- Pillar 5: VN-Index Technical Trend (0..20) ---
    vni_close = vni.get("close")
    vni_ma20 = vni.get("ma20")
    vni_ma50 = vni.get("ma50")
    vni_ma200 = vni.get("ma200")
    vni_rsi = vni.get("rsi14") or 50.0

    s_tech = 10.0
    if vni_close:
        if vni_ma200:
            s_tech += 4.0 if vni_close > vni_ma200 else -4.5
        if vni_ma50:
            s_tech += 3.5 if vni_close > vni_ma50 else -3.0
        if vni_ma20:
            s_tech += 1.5 if vni_close > vni_ma20 else -1.5
        if 45 <= vni_rsi <= 68:
            s_tech += 1.5
        elif vni_rsi > 75:
            s_tech -= 1.5  # Overbought caution
        elif vni_rsi < 32:
            s_tech += 0.5  # Oversold bounce zone
    s_tech = round(_clamp(s_tech, 2.0, 20.0), 1)
    tech_note = f"{vni.get('trend', '—')} · RSI {vni_rsi:.0f}"

    total_score = round(s_growth + s_infl + s_mon + s_fx + s_tech)

    # --- Overall Posture ---
    if total_score >= 70:
        stance = {"label": "TÍCH CỰC · ƯU TIÊN CỔ PHIẾU (RISK-ON)", "short": "Risk-On · Tấn công", "tone": "emerald"}
        summary_action = (
            "Vĩ mô ủng hộ tăng trưởng và xu hướng VN-Index tích cực. Ưu tiên nắm giữ tỷ trọng cổ phiếu cao ở các ngành dẫn dắt, "
            "có thể giải ngân gia tăng trong các nhịp điều chỉnh về vùng hỗ trợ MA20/MA50."
        )
    elif total_score >= 54:
        stance = {"label": "CHỌN LỌC · CÂN BẰNG RỦI RO (SELECTIVE)", "short": "Chọn lọc · Kỷ luật", "tone": "amber"}
        summary_action = (
            "Kinh tế thực mở rộng (PMI > 50) nhưng vẫn còn lực cản từ lạm phát/lãi suất hoặc dòng vốn ngoại. "
            "Duy trì tỷ trọng cổ phiếu mức trung bình–khá, tập trung nhóm ngành có động lực riêng & định giá hợp lý, hạn chế dùng margin cao và giữ tỷ trọng phòng hộ Vàng/Tiền mặt."
        )
    elif total_score >= 40:
        stance = {"label": "THẬN TRỌNG · PHÒNG THỦ CHỦ ĐỘNG (CAUTIOUS)", "short": "Thận trọng · Phòng thủ", "tone": "amber"}
        summary_action = (
            "Áp lực từ lãi suất Fed, lạm phát dai dẳng hoặc rung lắc kỹ thuật ngắn hạn đang chi phối thị trường. "
            "Ưu tiên bảo toàn vốn, hạ đòn bẩy, giữ lượng tiền mặt/trái phiếu và vàng đủ lớn để chủ động gom cổ phiếu chất lượng khi chiết khấu sâu."
        )
    else:
        stance = {"label": "PHÒNG THỦ TỐI ĐA · BẢO TOÀN VỐN (RISK-OFF)", "short": "Risk-Off · Bảo toàn", "tone": "rose"}
        summary_action = (
            "Rủi ro vĩ mô và xu hướng kỹ thuật cùng suy yếu. Ưu tiên tiền mặt, tiền gửi kỳ hạn ngắn và vàng phòng hộ; "
            "chỉ giải ngân thăm dò từng phần khi có tín hiệu tạo đáy và áp lực tỷ giá/lãi suất hạ nhiệt."
        )

    # --- Dynamic Tactical Asset Allocation (sums strictly to 100%) ---
    # Base allocation adjusted smoothly by live scores
    eq_w = int(round(_clamp(22 + (total_score - 30) * 0.72 + (s_tech - 10) * 0.8, 20, 65)))
    gold_w = int(round(_clamp(12 + (20 - s_infl) * 0.65 + (10 - s_mon) * 0.4, 10, 28)))
    usd_w = int(round(_clamp(8 + (20 - s_fx) * 0.55 + (prob_hike * 0.05), 5, 22)))
    cash_w = 100 - eq_w - gold_w - usd_w
    if cash_w < 10:
        diff = 10 - cash_w
        eq_w = max(20, eq_w - diff)
        cash_w = 100 - eq_w - gold_w - usd_w

    # Enrich Gold allocation reason with live SJC spread if available
    gold_vn = data.get("gold_vn") or {}
    gold_extra = ""
    if gold_vn.get("available") and gold_vn.get("bar_spread_m") is not None:
        gold_extra = f" · SJC {gold_vn['sjc_bar']['sell_m']:.1f}tr (chênh TG {gold_vn['bar_spread_m']:+.1f}tr)"

    allocation = [
        {
            "key": "vn_equity",
            "name": "Cổ phiếu Việt Nam (VN-Index / VN30)",
            "weight": eq_w,
            "color": "#10b981",
            "stance": "Tăng tỷ trọng" if eq_w >= 50 else ("Chọn lọc" if eq_w >= 35 else "Phòng thủ"),
            "reason": f"VN-Index {vni_close:,.1f} ({vni.get('trend', '—')}), PMI VN {vn_pmi:.1f}" if vni_close else "Theo dõi xu hướng VN-Index",
        },
        {
            "key": "vnd_cash",
            "name": "Tiền mặt / Tiền gửi / Trái phiếu ngắn hạn",
            "weight": cash_w,
            "color": "#38bdf8",
            "stance": "Đệm thanh khoản",
            "reason": "Sẵn sức mua đón nhịp chiết khấu & giảm biến động danh mục",
        },
        {
            "key": "gold",
            "name": "Vàng (XAU / SJC · Phòng hộ Lạm phát)",
            "weight": gold_w,
            "color": "#f59e0b",
            "stance": "Phòng hộ cao" if gold_w >= 18 else "Nắm giữ nền",
            "reason": f"Core PCE {us_cpce:.2f}% & CPI VN {vn_cpi_yoy:.2f}%{gold_extra}",
        },
        {
            "key": "usd_assets",
            "name": "Tài sản USD / Hedge Tỷ giá",
            "weight": usd_w,
            "color": "#a78bfa",
            "stance": "Phòng hộ tỷ giá" if usd_w >= 14 else "Đa dạng hóa",
            "reason": f"DXY {dxy:.2f} · Fed kỳ vọng {mon_bias.lower()} ({nxt.get('implied_rate', 0):.2f}%)",
        },
    ]

    # --- Live HOSE Sector Rotation Playbook (Macro Fit + Real Technical RS!) ---
    hot_inflation = us_cpce >= 2.8 or us_cpi >= 3.2
    hawkish_fed = prob_hike >= 45 or delta_6m >= 20
    strong_pmi = vn_pmi >= 51.0 and us_pmi >= 51.0
    high_oil = (fx.get("wti_price") or 70) >= 75

    sector_macro_map = {
        "VNIND": (
            2.5 if strong_pmi else -1.0,
            f"Hưởng lợi trực tiếp từ PMI VN ({vn_pmi:.1f}) & ISM Mỹ ({us_pmi:.1f}) mở rộng đơn hàng xuất khẩu",
        ),
        "VNENE": (
            2.2 if (hot_inflation or high_oil) else 0.5,
            f"Phòng hộ lạm phát trong pha Reflation; dầu WTI tại ${(fx.get('wti_price') or 0):.1f}/thùng",
        ),
        "VNIT": (
            1.6 if strong_pmi else 0.5,
            "Tăng trưởng doanh thu xuất khẩu phần mềm hưởng lợi tỷ giá USD khỏe & ít chịu áp lực nợ vay",
        ),
        "VNFIN": (
            0.8 if (not hawkish_fed) else -0.4,
            "Tín dụng phục hồi theo sản xuất nhưng cần theo dõi áp lực lãi suất/tỷ giá và bán ròng khối ngoại",
        ),
        "VNMAT": (
            1.5 if (strong_pmi and hot_inflation) else 0.0,
            "Hưởng lợi chu kỳ hàng hóa & đầu tư công, nhưng nhạy cảm với biến động giá nguyên liệu",
        ),
        "VNCONS": (
            1.0 if total_score < 55 else 0.4,
            "Nhóm phòng thủ dòng tiền ổn định khi thị trường rung lắc, ít nhạy cảm chu kỳ lãi suất",
        ),
        "VNHEAL": (
            1.0 if total_score < 55 else 0.2,
            "Nhóm phòng thủ biến động thấp, giữ nhịp danh mục trong giai đoạn vĩ mô thận trọng",
        ),
        "VNREAL": (
            -2.0 if (hawkish_fed or hot_inflation) else 1.5,
            "Nhạy cảm cao với mặt bằng lãi suất neo cao & áp lực trái phiếu; ưu tiên chọn lọc doanh nghiệp sạch nợ",
        ),
    }

    sector_playbook = []
    leaders_by_sector = (data.get("vn_valuation") or {}).get("leaders_by_sector") or {}
    for sec in vn_mkt.get("sectors") or []:
        code = sec["code"]
        macro_adj, macro_reason = sector_macro_map.get(code, (0.0, "Diễn biến theo mặt bằng chung thị trường"))
        # Composite sector score combining live technical RS & macro fit
        tech_pts = (
            (1.5 if sec["above_ma50"] else -1.5)
            + _clamp(sec["rs_1m"] * 0.35, -2.5, 2.5)
            + _clamp(sec["rs_ytd"] * 0.08, -1.5, 1.5)
        )
        sec_score = round(50 + (macro_adj * 9.0) + (tech_pts * 6.5))
        sec_score = int(_clamp(sec_score, 15, 95))

        if sec_score >= 62:
            rating = "Ưu tiên"
            tone = "success"
            tactic = "Tích lũy nhịp rung lắc"
        elif sec_score >= 45:
            rating = "Theo dõi"
            tone = "warning"
            tactic = "Chọn lọc đầu ngành"
        else:
            rating = "Thận trọng"
            tone = "danger"
            tactic = "Hạn chế tỷ trọng / chờ đáy"

        rs_txt = f"RS 1T {'vượt trội' if sec['rs_1m'] >= 0 else 'yếu hơn'} VNI ({sec['rs_1m']:+.1f}%)"
        ma_txt = "trên MA50" if sec["above_ma50"] else "dưới MA50"
        combined_reason = f"{rs_txt}, {ma_txt} · {macro_reason}"

        sector_playbook.append({
            **sec,
            "score": sec_score,
            "rating": rating,
            "tone": tone,
            "tactic": tactic,
            "reason": combined_reason,
            "leader": leaders_by_sector.get(code),
        })

    sector_playbook.sort(key=lambda x: x["score"], reverse=True)

    # Enrich summary_action with live VN-Index P/E valuation context if cheap/expensive
    vni_pe = (data.get("vn_valuation") or {}).get("vnindex_pe") or {}
    if vni_pe.get("available") and vni_pe.get("pe"):
        pe_v = vni_pe["pe"]
        ep_v = vni_pe.get("earnings_yield_pct")
        if pe_v < vni_pe.get("avg_5y_low", 12.9):
            summary_action += (
                f" Điểm sáng định giá: P/E VN-Index hiện ở vùng Rẻ ({pe_v:.2f}x, lợi suất thu nhập E/P {ep_v:.1f}%), "
                "tạo biên an toàn tốt để gom từng phần cổ phiếu đầu ngành cho tầm nhìn trung–dài hạn."
            )
        elif pe_v > vni_pe.get("avg_5y_high", 16.7):
            summary_action += f" Lưu ý định giá: P/E VN-Index ở mức cao ({pe_v:.2f}x), cần kiểm soát chặt giá mua."

    # --- Concrete "NẾU... THÌ..." Action Trigger Rules ---
    triggers = []
    if vni_close and vni_ma50 and vni_ma200:
        if vni_close < vni_ma50:
            triggers.append({
                "type": "bullish",
                "title": f"Kích hoạt TĂNG tỷ trọng cổ phiếu",
                "condition": f"VN-Index lấy lại mốc MA50 ({vni_ma50:,.0f}) kèm thanh khoản > 18,000 tỷ & khối ngoại giảm bán ròng",
                "action": "Nâng tỷ trọng Cổ phiếu VN thêm +10%–15%, ưu tiên các nhóm ngành dẫn dắt (RS > 0).",
            })
        else:
            triggers.append({
                "type": "bullish",
                "title": f"Duy trì xu hướng TĂNG",
                "condition": f"VN-Index giữ vững trên MA50 ({vni_ma50:,.0f}) & PMI Việt Nam duy trì > 50",
                "action": "Tiếp tục nắm giữ cổ phiếu đầu ngành, dời điểm chặn lãi theo MA20.",
            })
        triggers.append({
            "type": "defensive",
            "title": "Ngưỡng QUẢN TRỊ RỦI RO (Cắt margin)",
            "condition": f"VN-Index đóng cửa thủng MA200 ({vni_ma200:,.0f}) hoặc VIX vượt 25 điểm",
            "action": "Hạ toàn bộ margin, đưa tỷ trọng cổ phiếu về ≤ 30% và tăng Tiền mặt / Vàng phòng hộ.",
        })

    if sbv.get("available") and sbv.get("ceiling"):
        triggers.append({
            "type": "macro",
            "title": "Cảnh báo Áp lực Tỷ giá & SBV",
            "condition": f"Giá bán USD VCB áp sát trần SBV {sbv['ceiling']:,}₫ (cách < 1.0%) hoặc DXY vượt 106",
            "action": "Cảnh giác động thái hút ròng OMO/tín phiếu; hạn chế cổ phiếu nhạy cảm lãi suất (BĐS, đòn bẩy cao).",
        })
    else:
        triggers.append({
            "type": "macro",
            "title": "Cảnh báo Tỷ giá USD/VND & DXY",
            "condition": "DXY vượt 106 hoặc USD/VND tăng nóng > 1.5% trong tháng",
            "action": "Tăng tỷ trọng phòng hộ Vàng/USD và ưu tiên doanh nghiệp xuất khẩu thu ngoại tệ.",
        })

    triggers.append({
        "type": "macro",
        "title": "Tín hiệu Đảo chiều Chính sách Fed",
        "condition": f"Xác suất Hạ lãi suất trên FedWatch vượt > 60% & Core PCE Mỹ hạ về dưới 2.5%",
        "action": "Mở rộng chu kỳ đầu tư sang nhóm Bất động sản, Chứng khoán và Ngân hàng.",
    })

    pillars = [
        {"key": "growth", "name": "Tăng trưởng & PMI", "score": s_growth, "max": 20, "note": growth_note},
        {"key": "inflation", "name": "Kiểm soát Lạm phát", "score": s_infl, "max": 20, "note": infl_note},
        {"key": "monetary", "name": "Fed & Lợi suất", "score": s_mon, "max": 20, "note": mon_note},
        {"key": "fx", "name": "Tỷ giá & Khối ngoại", "score": s_fx, "max": 20, "note": fx_note},
        {"key": "tech", "name": "Xu hướng VN-Index", "score": s_tech, "max": 20, "note": tech_note},
    ]

    return {
        "total_score": total_score,
        "stance": stance,
        "summary_action": summary_action,
        "pillars": pillars,
        "allocation": allocation,
        "sector_playbook": sector_playbook,
        "triggers": triggers,
    }


# =============================================================================
# 13. Morning Briefing Executive Digest Generator
# =============================================================================

def generate_morning_briefing(data: dict[str, Any]) -> dict[str, Any]:
    bullets = []
    signals = {"inflation": "neutral", "growth": "neutral"}

    vni = ((data.get("vn_market") or {}).get("indices") or {}).get("VNINDEX") or {}
    flow = (data.get("vn_market") or {}).get("flow") or {}
    if vni.get("close"):
        f1 = flow.get("foreign_1d_bn")
        f_txt = f" · Khối ngoại {'mua ròng' if (f1 or 0) >= 0 else 'bán ròng'} {abs(f1):,.0f} tỷ" if f1 is not None else ""
        liq_txt = f" · KL khớp lệnh {flow['liquidity_bn']:,.0f} tỷ" if flow.get("liquidity_bn") else ""
        bullets.append({
            "tag": "VN-Index",
            "tone": "success" if vni.get("trend_tone") == "success" else ("danger" if vni.get("trend_tone") == "danger" else "warning"),
            "text": (
                f"VN-Index tại {vni['close']:,.2f} ({vni['pct']:+.2f}%, YTD {vni['ytd_pct']:+.1f}%) — "
                f"{vni.get('trend')} (MA50: {vni.get('ma50', 0):,.0f}, RSI: {vni.get('rsi14', 0):.0f}){liq_txt}{f_txt}."
            ),
        })

    cpi_latest = data.get("kpis", {}).get("us_cpi")
    pce_latest = data.get("kpis", {}).get("us_core_pce")
    if cpi_latest and pce_latest:
        cpi_v, pce_v = cpi_latest.get("value"), pce_latest.get("value")
        if cpi_v is not None and pce_v is not None:
            if pce_v >= 2.8 or cpi_v >= 3.0:
                signals["inflation"] = "hot"
                bullets.append({
                    "tag": "Lạm phát Mỹ",
                    "tone": "danger",
                    "text": f"CPI Mỹ {cpi_v:.2f}% YoY ({cpi_latest.get('period')}) và Core PCE {pce_v:.2f}% YoY — cao hơn mục tiêu 2.0% của Fed, giữ áp lực diều hâu.",
                })
            elif pce_v <= 2.3:
                signals["inflation"] = "cool"
                bullets.append({
                    "tag": "Lạm phát Mỹ",
                    "tone": "success",
                    "text": f"Lạm phát hạ nhiệt gần mục tiêu 2%: CPI Mỹ {cpi_v:.2f}% YoY, Core PCE {pce_v:.2f}% YoY — ủng hộ nới lỏng.",
                })
            else:
                signals["inflation"] = "watch"
                bullets.append({
                    "tag": "Lạm phát Mỹ",
                    "tone": "warning",
                    "text": f"CPI Mỹ {cpi_v:.2f}% YoY và Core PCE {pce_v:.2f}% YoY — nhỉnh hơn mục tiêu 2.0%, cần theo dõi sát.",
                })

    fw = data.get("fedwatch") or {}
    contracts = fw.get("contracts") or []
    if contracts:
        nxt = contracts[0]
        six_m = contracts[min(4, len(contracts) - 1)]
        if nxt["prob_hike"] >= 50:
            bullets.append({
                "tag": "Lãi suất Fed",
                "tone": "danger",
                "text": f"HĐTL Fed Funds ({nxt['label']}) định giá {nxt['prob_hike']:.0f}% xác suất NÂNG lãi suất (ngầm định {nxt['implied_rate']:.2f}%); kỳ hạn {six_m['label']} tại {six_m['implied_rate']:.2f}%.",
            })
        elif nxt["prob_cut"] >= 50:
            bullets.append({
                "tag": "Lãi suất Fed",
                "tone": "success",
                "text": f"HĐTL ({nxt['label']}) định giá {nxt['prob_cut']:.0f}% xác suất HẠ lãi suất (ngầm định {nxt['implied_rate']:.2f}%); kỳ hạn {six_m['label']} về {six_m['implied_rate']:.2f}%.",
            })
        else:
            bullets.append({
                "tag": "Lãi suất Fed",
                "tone": "info",
                "text": f"Thị trường nghiêng về GIỮ NGUYÊN lãi suất ({nxt['prob_hold']:.0f}%) trong {nxt['label']} (ngầm định {nxt['implied_rate']:.2f}%).",
            })

    us_pmi = data.get("kpis", {}).get("us_pmi")
    vn_pmi = data.get("kpis", {}).get("vn_pmi")
    if us_pmi and vn_pmi and us_pmi.get("value") and vn_pmi.get("value"):
        uv, vv = us_pmi["value"], vn_pmi["value"]
        both_exp = uv >= 50 and vv >= 50
        signals["growth"] = "expansion" if both_exp else ("contraction" if uv < 50 and vv < 50 else "mixed")
        vcb_usd = (data.get("vcb") or {}).get("usd") or {}
        sbv_info = data.get("sbv") or {}
        sbv_txt = f" · SBV TT {sbv_info['central_rate']:,}₫" if sbv_info.get("available") else ""
        bullets.append({
            "tag": "PMI & Tỷ giá",
            "tone": "success" if both_exp else "warning",
            "text": (
                f"PMI VN đạt {vv:.1f} & ISM Mỹ {uv:.1f} ({'đồng pha mở rộng >50' if both_exp else 'phân hóa'}). "
                f"USD bán VCB {vcb_usd.get('sell', 0):,.0f}₫{sbv_txt}."
            ),
        })

    if signals["growth"] == "expansion" and signals["inflation"] in ("hot", "watch"):
        regime = {"name": "Tăng trưởng nóng · Lạm phát dai dẳng (Reflation)", "color": "amber"}
    elif signals["growth"] == "expansion" and signals["inflation"] == "cool":
        regime = {"name": "Tăng trưởng cân bằng · Lạm phát dịu (Goldilocks)", "color": "emerald"}
    elif signals["growth"] == "contraction" and signals["inflation"] == "hot":
        regime = {"name": "Đình lạm · Áp lực chi phí (Stagflation Risk)", "color": "rose"}
    else:
        regime = {"name": "Chuyển pha Vĩ mô · Theo dõi sát Fed & Tỷ giá", "color": "cyan"}

    return {
        "generated_at": _now_vn().strftime("%H:%M:%S · %d/%m/%Y"),
        "regime": regime,
        "bullets": bullets,
    }


# =============================================================================
# Master Dashboard Aggregator
# =============================================================================

def _latest_valid(series: list[dict[str, Any]], key: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    valid = [p for p in series if p.get(key) is not None]
    if not valid:
        return None, None
    return valid[-1], (valid[-2] if len(valid) >= 2 else None)


def build_dashboard(force: bool = False) -> dict[str, Any]:
    t0 = time.time()
    source_status: dict[str, dict[str, Any]] = {}

    def run_src(key: str, fn, strict_cooldown: bool = False):
        data, refreshed = _get_cached_or_fetch(key, fn, force=force, respect_strict_cooldown=strict_cooldown)
        source_status[key] = {"ok": data is not None, "fresh": refreshed}
        return data

    with concurrent.futures.ThreadPoolExecutor(max_workers=14) as ex:
        f_bls = ex.submit(run_src, "bls", fetch_bls_data)
        f_bea = ex.submit(run_src, "bea", fetch_bea_pce_data)
        f_nyfed = ex.submit(run_src, "nyfed", fetch_nyfed_rates)
        f_yc = ex.submit(run_src, "yield_curve", fetch_fed_yield_curve)
        f_fx = ex.submit(run_src, "fx_markets", fetch_fx_and_markets)
        f_vn = ex.submit(run_src, "vn_market", fetch_vn_market_indices)
        f_val = ex.submit(run_src, "vn_valuation", fetch_vn_valuation_and_leaders)
        f_gold = ex.submit(run_src, "gold_vn", fetch_vn_gold_prices)
        f_vcb = ex.submit(run_src, "vcb", fetch_vietcombank_xml, True)
        f_sbv = ex.submit(run_src, "sbv", fetch_sbv_optional)
        f_wb = ex.submit(run_src, "worldbank", fetch_worldbank_comparison)
        f_pmi = ex.submit(run_src, "pmi_live", fetch_pmi_and_vn_macro)
        f_cal = ex.submit(run_src, "calendar", fetch_economic_calendar, False)
        f_news = ex.submit(run_src, "news", fetch_vietnam_policy_news)

        bls = f_bls.result() or {}
        bea = f_bea.result() or {}
        nyfed = f_nyfed.result() or {}
        yc = f_yc.result() or {}
        fx = f_fx.result() or {}
        vn_mkt = f_vn.result() or {}
        vn_val = f_val.result() or {}
        gold_vn = f_gold.result() or {"available": False}
        vcb = f_vcb.result() or {}
        try:
            sbv = f_sbv.result(timeout=4.5) or {"available": False}
        except Exception:
            sbv = {"available": False}
        wb = f_wb.result() or {}
        pmi = f_pmi.result() or {}
        cal = f_cal.result() or {"events": []}
        news = f_news.result() or []

    # Prepend live Vietnam stock indices + VN-Index P/E + SJC Gold to top ticker strip!
    vn_tickers = []
    vn_indices = vn_mkt.get("indices") or {}
    for code, label in [("VNINDEX", "VN-Index"), ("VN30", "VN30"), ("HNX", "HNX"), ("UPCOM", "UPCoM")]:
        idx_obj = vn_indices.get(code)
        if idx_obj and idx_obj.get("available"):
            vn_tickers.append({
                "label": label,
                "price": idx_obj["close"],
                "change": idx_obj["change"],
                "pct": idx_obj["pct"],
                "unit": "",
            })
    vni_pe_info = vn_val.get("vnindex_pe") or {}
    if vni_pe_info.get("available") and vni_pe_info.get("pe"):
        vn_tickers.append({
            "label": "P/E VNI",
            "price": vni_pe_info["pe"],
            "change": None,
            "pct": vni_pe_info.get("vs_avg_pct"),
            "unit": "x",
        })
    if gold_vn.get("available") and gold_vn.get("sjc_bar"):
        vn_tickers.append({
            "label": "Vàng SJC",
            "price": gold_vn["sjc_bar"]["sell_m"],
            "change": None,
            "pct": gold_vn.get("bar_spread_pct"),
            "unit": "tr",
        })
    fx["tickers"] = vn_tickers + (fx.get("tickers") or [])

    ny_latest = nyfed.get("latest") or {}
    cur_effr = ny_latest.get("effr") or 3.88
    cur_tlow = ny_latest.get("target_low") or 3.75
    cur_thigh = ny_latest.get("target_high") or 4.00
    fedwatch = run_src("fedwatch", lambda: fetch_fedwatch_futures(cur_effr, cur_tlow, cur_thigh)) or {}

    cpi_s = bls.get("cpi") or []
    core_cpi_s = bls.get("core_cpi") or []
    ppi_s = bls.get("ppi") or []
    unemp_s = bls.get("unemployment") or []
    nfp_s = bls.get("nfp") or []
    pce_s = bea.get("pce") or []
    core_pce_s = bea.get("core_pce") or []
    us_mfg_pmi_s = pmi.get("us_ism_mfg") or []
    us_svc_pmi_s = pmi.get("us_ism_svc") or []
    vn_pmi_s = pmi.get("vn_mfg_pmi") or []

    cpi_now, cpi_prev = _latest_valid(cpi_s, "yoy")
    cpi_mom_now, _ = _latest_valid(cpi_s, "mom")
    ccpi_now, _ = _latest_valid(core_cpi_s, "yoy")
    ccpi_mom_now, _ = _latest_valid(core_cpi_s, "mom")
    ppi_now, _ = _latest_valid(ppi_s, "yoy")
    pce_now, _ = _latest_valid(pce_s, "yoy")
    cpce_now, cpce_prev = _latest_valid(core_pce_s, "yoy")
    un_now, un_prev = _latest_valid(unemp_s, "value")
    nfp_now, _ = _latest_valid(nfp_s, "change_k")
    upmi_now, upmi_prev = _latest_valid(us_mfg_pmi_s, "value")
    usvc_now, _ = _latest_valid(us_svc_pmi_s, "value")
    vpmi_now, vpmi_prev = _latest_valid(vn_pmi_s, "value")

    kpis = {
        "us_cpi": {
            "value": cpi_now["yoy"] if cpi_now else None,
            "prev": cpi_prev["yoy"] if cpi_prev else None,
            "delta": round(cpi_now["yoy"] - cpi_prev["yoy"], 2) if (cpi_now and cpi_prev) else None,
            "mom": cpi_mom_now["mom"] if cpi_mom_now else (cpi_now["mom"] if cpi_now else None),
            "mom_period": cpi_mom_now["date"] if cpi_mom_now else (cpi_now["date"] if cpi_now else None),
            "period": cpi_now["date"] if cpi_now else None,
            "core_cpi": ccpi_now["yoy"] if ccpi_now else None,
            "core_cpi_mom": ccpi_mom_now["mom"] if ccpi_mom_now else None,
            "ppi": ppi_now["yoy"] if ppi_now else None,
            "spark": [p["yoy"] for p in cpi_s[-18:] if p.get("yoy") is not None],
        },
        "us_core_pce": {
            "value": cpce_now["yoy"] if cpce_now else None,
            "prev": cpce_prev["yoy"] if cpce_prev else None,
            "delta": round(cpce_now["yoy"] - cpce_prev["yoy"], 2) if (cpce_now and cpce_prev) else None,
            "mom": cpce_now["mom"] if cpce_now else None,
            "headline_pce": pce_now["yoy"] if pce_now else None,
            "period": cpce_now["date"] if cpce_now else None,
            "spark": [p["yoy"] for p in core_pce_s[-18:] if p.get("yoy") is not None],
        },
        "us_pmi": {
            "value": upmi_now["value"] if upmi_now else None,
            "prev": upmi_prev["value"] if upmi_prev else None,
            "delta": round(upmi_now["value"] - upmi_prev["value"], 1) if (upmi_now and upmi_prev) else None,
            "services": usvc_now["value"] if usvc_now else None,
            "period": upmi_now["date"] if upmi_now else None,
            "spark": [p["value"] for p in us_mfg_pmi_s[-18:]],
        },
        "vn_pmi": {
            "value": vpmi_now["value"] if vpmi_now else None,
            "prev": vpmi_prev["value"] if vpmi_prev else None,
            "delta": round(vpmi_now["value"] - vpmi_prev["value"], 1) if (vpmi_now and vpmi_prev) else None,
            "period": vpmi_now["date"] if vpmi_now else None,
            "spark": [p["value"] for p in vn_pmi_s[-18:]],
        },
        "us_unemployment": {
            "value": un_now["value"] if un_now else None,
            "prev": un_prev["value"] if un_prev else None,
            "delta": round(un_now["value"] - un_prev["value"], 1) if (un_now and un_prev) else None,
            "nfp_k": nfp_now["change_k"] if nfp_now else None,
            "period": un_now["date"] if un_now else None,
            "spark": [p["value"] for p in unemp_s[-18:]],
        },
        "fed_rate": {
            "target_range": f"{cur_tlow:.2f}% – {cur_thigh:.2f}%",
            "effr": cur_effr,
            "sofr": ny_latest.get("sofr"),
            "date": ny_latest.get("date"),
            "next_meeting": (fedwatch.get("contracts") or [None])[0],
            "spark": [p["effr"] for p in (nyfed.get("monthly") or [])[-24:]],
        },
        "usdvnd": {
            "vcb_sell": (vcb.get("usd") or {}).get("sell"),
            "vcb_buy": (vcb.get("usd") or {}).get("buy"),
            "vcb_transfer": (vcb.get("usd") or {}).get("transfer"),
            "spot": (fx.get("usdvnd_spot") or {}).get("price"),
            "pct": (fx.get("usdvnd_spot") or {}).get("pct"),
            "dxy": (fx.get("dxy") or {}).get("price"),
            "dxy_pct": (fx.get("dxy") or {}).get("pct"),
            "sbv_central": sbv.get("central_rate") if sbv.get("available") else None,
            "spark": [p["usdvnd"] for p in (fx.get("fx_dual_history") or [])[-60:]],
        },
    }

    yearly_comparisons = {
        "us_cpi": _build_yearly_matrix(cpi_s, "yoy", 5),
        "us_core_cpi": _build_yearly_matrix(core_cpi_s, "yoy", 5),
        "us_pce": _build_yearly_matrix(pce_s, "yoy", 5),
        "us_core_pce": _build_yearly_matrix(core_pce_s, "yoy", 5),
        "us_ppi": _build_yearly_matrix(ppi_s, "yoy", 5),
        "us_unemployment": _build_yearly_matrix(unemp_s, "value", 5),
        "us_ism_mfg": _build_yearly_matrix(us_mfg_pmi_s, "value", 4),
        "us_ism_svc": _build_yearly_matrix(us_svc_pmi_s, "value", 4),
        "vn_mfg_pmi": _build_yearly_matrix(vn_pmi_s, "value", 4),
    }

    sbv_clean = sbv if sbv.get("available") else {"available": False}
    if not sbv_clean.get("available"):
        source_status.pop("sbv", None)

    payload = {
        "meta": {
            "updated_at_vn": _now_vn().strftime("%H:%M:%S · %d/%m/%Y"),
            "updated_ts": time.time(),
            "fetch_ms": round((time.time() - t0) * 1000),
            "sources": source_status,
        },
        "kpis": kpis,
        "vn_market": vn_mkt,
        "vn_valuation": vn_val,
        "gold_vn": gold_vn,
        "inflation": {
            "cpi": cpi_s[-120:],
            "core_cpi": core_cpi_s[-120:],
            "pce": pce_s[-120:],
            "core_pce": core_pce_s[-120:],
            "ppi": ppi_s[-120:],
            "vn_cpi_latest": pmi.get("vn_cpi_latest"),
        },
        "pmi": {
            "us_ism_mfg": us_mfg_pmi_s,
            "us_ism_svc": us_svc_pmi_s,
            "vn_mfg_pmi": vn_pmi_s,
            "official_links": pmi.get("official_links") or [],
        },
        "labor": {
            "unemployment": unemp_s[-120:],
            "nfp": nfp_s[-60:],
        },
        "rates": {
            "nyfed": nyfed,
            "yield_curve": yc,
        },
        "fedwatch": fedwatch,
        "fx": fx,
        "vcb": vcb,
        "sbv": sbv_clean,
        "worldbank": wb,
        "yearly": yearly_comparisons,
        "calendar": cal,
        "news": news,
    }
    payload["strategy"] = compute_investment_strategy(payload)
    payload["briefing"] = generate_morning_briefing(payload)

    _save_json(DASHBOARD_CACHE_FILE, payload)
    return payload


def get_cached_dashboard() -> dict[str, Any] | None:
    return _load_json(DASHBOARD_CACHE_FILE, None)


if __name__ == "__main__":
    print("Testing enhanced build_dashboard(force=True)...")
    data = build_dashboard(force=True)
    print("Fetch ms:", data["meta"]["fetch_ms"])
    vni = data["vn_market"]["indices"]["VNINDEX"]
    print(f"VNINDEX: {vni['close']} ({vni['pct']:+.2f}%, YTD {vni['ytd_pct']:+.2f}%, MA50={vni['ma50']}, MA200={vni['ma200']}, RSI={vni['rsi14']}, bars={len(vni['series'])})")
    print("VN Sectors count:", len(data["vn_market"]["sectors"]))
    print("Foreign 1D / 5D / 20D (tỷ):", data["vn_market"]["flow"]["foreign_1d_bn"], data["vn_market"]["flow"]["foreign_5d_bn"], data["vn_market"]["flow"]["foreign_20d_bn"])
    st = data["strategy"]
    print(f"Strategy Score: {st['total_score']}/100 | {st['stance']['label']}")
    print("Allocation:", [(a["name"], a["weight"]) for a in st["allocation"]])
    print("Top Sectors:", [(s["code"], s["score"], s["rating"], s["rs_1m"]) for s in st["sector_playbook"][:4]])
