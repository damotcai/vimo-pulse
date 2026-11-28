"""
VĨ MÔ PULSE × BCTC PRO — Top-Down Macro × Bottom-Up Financial Statement Fusion Engine
Kết hợp:
  1. Dữ liệu BCTC chính thức (Vietcap IQ API: KQKD, CĐKT, LCTT, Thuyết minh Năm/Quý + SSI iBoard/DNSE OHLCV)
     theo đủ 3 chuẩn kế toán Việt Nam: BANK (TT49), SECURITIES (TT210), CORPORATE (TT200).
  2. Bối cảnh Vĩ mô Trực tuyến từ VĨ MÔ PULSE (US 10Y, FedWatch, Core PCE/CPI, VN & US PMI,
     DXY/USD-VND/SBV, VIX, WTI Oil, VN-Index Trend, Thanh khoản HOSE, Khối ngoại & RS 8 Ngành HOSE).
  3. Động cơ Hợp nhất Định lượng (Macro × BCTC Fusion):
     - Chi phí vốn động (Dynamic Ke / WACC) & nén/mở hệ số định giá (Multiple Compression/Expansion) theo vĩ mô live.
     - Trụ cột thứ 6: Độ phù hợp Vĩ mô (Macro Fit 0–100) đo 6 kênh truyền dẫn trực tiếp từ Vĩ mô vào từng khoản mục BCTC.
     - Khuyến nghị & kế hoạch giải ngân kỷ luật đồng bộ theo La bàn Vĩ mô (so sánh minh bạch BCTC thuần vs BCTC × Vĩ mô).
     - Báo cáo Nhận định Buy-Side Offline (0đ) + hỗ trợ Gemini / OpenAI-compatible API + Xuất Excel (.xlsx).
"""

from __future__ import annotations

import concurrent.futures
import csv
import io
import json
import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from security_utils import (
    atomic_write_json,
    build_pooled_session,
    cleanup_old_files,
    get_logger,
    validate_ai_model,
    validate_outbound_https_url,
    validate_ticker,
)

logger = get_logger("bctc_macro_engine")
_HTTP = build_pooled_session(pool_connections=16, pool_maxsize=16)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
BCTC_DIR = DATA_DIR / "bctc"
EXPORT_DIR = DATA_DIR / "exports"

for _d in (DATA_DIR, BCTC_DIR, EXPORT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

VCI_BASE = "https://iq.vietcap.com.vn/api/iq-insight-service/v1/company"
VCI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Origin": "https://iq.vietcap.com.vn",
    "Referer": "https://iq.vietcap.com.vn/",
}
SSI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Origin": "https://iboard.ssi.com.vn",
    "Referer": "https://iboard.ssi.com.vn/",
}

# Tham số cơ sở (Neutral Macro Baseline — sẽ tự động điều chỉnh theo Vĩ mô live)
MACRO_DEFAULTS = {
    "risk_free_rate": 0.032,
    "equity_risk_premium": 0.088,
    "cost_of_equity_bank": 0.125,
    "cost_of_equity_sec": 0.135,
    "cost_of_equity_corp": 0.120,
    "terminal_growth": 0.035,
    "tax_rate": 0.20,
}

# 8 mã tiêu biểu đại diện 8 nhóm ngành trụ cột TTCK Việt Nam
REPRESENTATIVE_UNIVERSE: dict[str, dict[str, Any]] = {
    "VCB": {
        "name": "Ngân hàng TMCP Ngoại thương Việt Nam (Vietcombank)",
        "sector_vi": "Ngân hàng",
        "sector_code": "BANK",
        "hose_sector": "VNFIN",
        "style": "Phòng thủ chất lượng cao (Blue-chip Compounder)",
        "is_core": True,
        "peer_benchmarks": {"pe": 11.5, "pb": 2.0, "roe": 0.19, "nim": 0.031, "npl": 0.015, "llr": 1.20, "cir": 0.35, "casa": 0.28},
        "key_drivers": [
            "Tăng trưởng tín dụng & hạn mức NHNN cấp hàng năm",
            "Biên lãi ròng (NIM) và lợi thế vốn giá rẻ (CASA > 30%)",
            "Chất lượng tài sản dẫn đầu hệ thống (NPL thấp, LLR cao kỷ lục)",
            "Thu nhập ngoài lãi (ngoại hối, phí dịch vụ, thanh toán quốc tế)",
        ],
    },
    "SSI": {
        "name": "Công ty Cổ phần Chứng khoán SSI",
        "sector_vi": "Chứng khoán",
        "sector_code": "SECURITIES",
        "hose_sector": "VNFIN",
        "style": "Chu kỳ thanh khoản & Đòn bẩy thị trường (High-Beta Cyclical)",
        "is_core": True,
        "peer_benchmarks": {"pe": 15.0, "pb": 1.8, "roe": 0.14, "margin_to_equity": 1.2, "fvtpl_to_assets": 0.45},
        "key_drivers": [
            "Thanh khoản toàn thị trường HOSE/HNX & câu chuyện nâng hạng FTSE/MSCI",
            "Dư nợ cho vay Margin & dư địa cho vay (Margin / VCSH < trần 200%)",
            "Hiệu quả danh mục Tự doanh (FVTPL: trái phiếu, CCTG, cổ phiếu)",
            "Thị phần môi giới & thu nhập ngân hàng đầu tư (IB)",
        ],
    },
    "HPG": {
        "name": "Công ty Cổ phần Tập đoàn Hòa Phát",
        "sector_vi": "Thép & Vật liệu Xây dựng",
        "sector_code": "CYCLICAL_MFG",
        "hose_sector": "VNMAT",
        "style": "Sản xuất đầu ngành chu kỳ & Tăng trưởng công suất (Cyclical Growth)",
        "is_core": True,
        "peer_benchmarks": {"pe": 12.0, "pb": 1.6, "ev_ebitda": 8.0, "roe": 0.16, "gross_margin": 0.15},
        "key_drivers": [
            "Tiến độ & công suất vận hành Khu liên hợp Dung Quất 2 (HRC)",
            "Chênh lệch giá bán thép và giá nguyên liệu (quặng sắt, than cốc)",
            "Chính sách phòng vệ thương mại (thuế chống bán phá giá HRC nhập khẩu)",
            "Chu kỳ phục hồi đầu tư công & bất động sản nội địa",
        ],
    },
    "DPM": {
        "name": "Tổng Công ty Phân bón và Hóa chất Dầu khí (Đạm Phú Mỹ)",
        "sector_vi": "Phân bón & Hóa chất",
        "sector_code": "CHEMICALS",
        "hose_sector": "VNMAT",
        "style": "Chu kỳ hàng hóa & Cổ tức tiền mặt (Commodity Value / Cash Cow)",
        "is_core": True,
        "peer_benchmarks": {"pe": 11.0, "pb": 1.4, "ev_ebitda": 6.5, "roe": 0.14, "gross_margin": 0.18},
        "key_drivers": [
            "Giá Urê thế giới & chính sách xuất khẩu phân bón của Trung Quốc",
            "Chi phí khí đầu vào (giá dầu FO / Brent & cơ chế bao tiêu khí PVN)",
            "Lượng tiền mặt ròng (Net Cash) dồi dào & cổ tức tiền mặt cao",
            "Lợi thế thuế VAT 5% đầu ra giúp khấu trừ thuế đầu vào",
        ],
    },
    "FPT": {
        "name": "Công ty Cổ phần FPT",
        "sector_vi": "Công nghệ Thông tin",
        "sector_code": "TECH_GROWTH",
        "hose_sector": "VNIT",
        "style": "Tăng trưởng kép bền vững (Secular Growth Compounder)",
        "is_core": False,
        "peer_benchmarks": {"pe": 22.0, "pb": 4.5, "ev_ebitda": 13.0, "roe": 0.24, "gross_margin": 0.37},
        "key_drivers": [
            "Tăng trưởng doanh thu Xuất khẩu phần mềm toàn cầu (Nhật Bản, Mỹ, APAC, EU)",
            "Chuyển đổi số, AI Factory, Cloud & bán dẫn",
            "Mảng Viễn thông & Giáo dục tạo dòng tiền đều đặn tài trợ tăng trưởng",
        ],
    },
    "MWG": {
        "name": "Công ty Cổ phần Đầu tư Thế Giới Di Động",
        "sector_vi": "Bán lẻ Tiêu dùng",
        "sector_code": "RETAIL",
        "hose_sector": "VNCONS",
        "style": "Tái cấu trúc & Mở rộng chuỗi bán lẻ (Turnaround & Consumer Growth)",
        "is_core": False,
        "peer_benchmarks": {"pe": 18.0, "pb": 2.8, "ev_ebitda": 10.0, "roe": 0.16, "gross_margin": 0.21},
        "key_drivers": [
            "Biên lợi nhuận & doanh thu/cửa hàng của chuỗi Bách Hóa Xanh (BHX)",
            "Phục hồi sức mua ICT/Điện máy & tối ưu chi phí vận hành",
            "Vòng quay hàng tồn kho & quản trị dòng tiền ròng",
        ],
    },
    "VNM": {
        "name": "Công ty Cổ phần Sữa Việt Nam (Vinamilk)",
        "sector_vi": "Thực phẩm & Đồ uống (FMCG)",
        "sector_code": "CONSUMER",
        "hose_sector": "VNCONS",
        "style": "Phòng thủ cổ tức cao (Defensive Cash Cow)",
        "is_core": False,
        "peer_benchmarks": {"pe": 15.0, "pb": 3.5, "ev_ebitda": 9.5, "roe": 0.25, "gross_margin": 0.41},
        "key_drivers": [
            "Thị phần sữa nội địa & tăng trưởng xuất khẩu / công ty con nước ngoài",
            "Giá bột sữa nguyên liệu thế giới quyết định biên lợi nhuận gộp",
            "Dòng tiền tự do (FCF) cực mạnh & tỷ suất cổ tức tiền mặt hấp dẫn",
        ],
    },
    "KDH": {
        "name": "Công ty Cổ phần Đầu tư và Kinh doanh Nhà Khang Điền",
        "sector_vi": "Bất động sản",
        "sector_code": "REAL_ESTATE",
        "hose_sector": "VNREAL",
        "style": "BĐS quỹ đất sạch pháp lý chuẩn (Quality Real Estate)",
        "is_core": False,
        "peer_benchmarks": {"pe": 16.0, "pb": 1.6, "ev_ebitda": 11.0, "roe": 0.12, "gross_margin": 0.42},
        "key_drivers": [
            "Tiến độ bàn giao & người mua trả tiền trước các dự án TP.HCM",
            "Sức khỏe bảng cân đối kế toán (Nợ vay/VCSH an toàn, quỹ đất sạch)",
            "Mặt bằng lãi suất vay mua nhà & pháp lý Luật Đất đai mới",
        ],
    },
}

DEFAULT_TICKERS = list(REPRESENTATIVE_UNIVERSE.keys())
CORE_TICKERS = [k for k, v in REPRESENTATIVE_UNIVERSE.items() if v.get("is_core")]

DEFAULT_SECTOR_BENCHMARKS = {
    "BANK": {"pe": 10.5, "pb": 1.6, "roe": 0.17, "nim": 0.032, "npl": 0.020, "llr": 0.90, "cir": 0.36, "casa": 0.22},
    "SECURITIES": {"pe": 15.0, "pb": 1.7, "roe": 0.13, "margin_to_equity": 1.2, "fvtpl_to_assets": 0.40},
    "CORPORATE": {"pe": 13.5, "pb": 1.8, "ev_ebitda": 8.5, "roe": 0.15, "gross_margin": 0.20},
}

# Độ nhạy Beta Vĩ mô theo nhóm ngành (cao hơn = chịu ảnh hưởng lãi suất / VIX mạnh hơn)
SECTOR_MACRO_BETA = {
    "SECURITIES": 1.35,
    "REAL_ESTATE": 1.30,
    "CYCLICAL_MFG": 1.15,
    "RETAIL": 1.05,
    "INDUSTRIAL": 1.00,
    "BANK": 0.95,
    "CORPORATE": 1.00,
    "TECH_GROWTH": 0.85,
    "ENERGY": 0.85,
    "CHEMICALS": 0.80,
    "CONSUMER": 0.70,
    "HEALTHCARE": 0.65,
}

SECTOR_TO_HOSE = {
    "BANK": "VNFIN",
    "SECURITIES": "VNFIN",
    "REAL_ESTATE": "VNREAL",
    "TECH_GROWTH": "VNIT",
    "INDUSTRIAL": "VNIND",
    "CYCLICAL_MFG": "VNMAT",
    "CHEMICALS": "VNMAT",
    "ENERGY": "VNENE",
    "RETAIL": "VNCONS",
    "CONSUMER": "VNCONS",
    "HEALTHCARE": "VNHEAL",
    "CORPORATE": "VNIND",
}


# =============================================================================
# Helper utilities
# =============================================================================

def _num(v: Any, default: float = 0.0) -> float:
    if v is None:
        return default
    try:
        f = float(v)
        return default if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return default


def _safe_div(a: float, b: float, default: float | None = None) -> float | None:
    if b is None or abs(b) < 1e-9:
        return default
    return a / b


def _cagr(start: float, end: float, periods: int) -> float | None:
    if periods <= 0 or start <= 0 or end <= 0:
        return None
    try:
        return (end / start) ** (1.0 / periods) - 1.0
    except Exception:
        return None


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def _pct(val: float | None, digits: int = 1) -> str:
    if val is None:
        return "N/A"
    return f"{val * 100:.{digits}f}%"


def _fmt_bn(val: float | None) -> str:
    if val is None:
        return "N/A"
    return f"{val / 1e9:,.1f} tỷ"


def _fmt_price(val: float | None) -> str:
    if val is None or val <= 0:
        return "N/A"
    return f"{val:,.0f} đ"


def _infer_sector_code(report_model: str, industry_name: str, ticker: str) -> str:
    if ticker in REPRESENTATIVE_UNIVERSE:
        return REPRESENTATIVE_UNIVERSE[ticker]["sector_code"]
    if report_model in ("BANK", "SECURITIES"):
        return report_model
    ind = (industry_name or "").lower()
    if any(k in ind for k in ("bất động sản", "real estate", "địa ốc", "khu công nghiệp")):
        return "REAL_ESTATE"
    if any(k in ind for k in ("thép", "kim loại", "vật liệu", "xi măng", "xây dựng", "steel", "material")):
        return "CYCLICAL_MFG"
    if any(k in ind for k in ("hóa chất", "phân bón", "chemical", "cao su")):
        return "CHEMICALS"
    if any(k in ind for k in ("dầu khí", "năng lượng", "điện", "gas", "oil", "energy", "utility")):
        return "ENERGY"
    if any(k in ind for k in ("công nghệ", "phần mềm", "viễn thông", "tech", "telecom", "it")):
        return "TECH_GROWTH"
    if any(k in ind for k in ("bán lẻ", "retail", "thương mại")):
        return "RETAIL"
    if any(k in ind for k in ("thực phẩm", "đồ uống", "sữa", "tiêu dùng", "food", "beverage", "fmcg")):
        return "CONSUMER"
    if any(k in ind for k in ("dược", "y tế", "health", "pharma")):
        return "HEALTHCARE"
    if any(k in ind for k in ("cảng", "vận tải", "hàng không", "logistics", "công nghiệp", "industrial")):
        return "INDUSTRIAL"
    return "CORPORATE"


# =============================================================================
# 1. BCTC Data Scraper & Local JSON Cache (Zero Pandas Dependency)
# =============================================================================

def _get_json(url: str, headers: dict[str, str], timeout: int = 12) -> dict[str, Any] | None:
    try:
        r = _HTTP.get(url, headers=headers, timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception as exc:
        logger.debug("Upstream GET failed (%s): %s", url.split("?")[0], type(exc).__name__)
    return None


def detect_report_model(metrics: dict[str, Any], industry_name: str = "") -> str:
    is_fields = {item.get("field") for item in metrics.get("INCOME_STATEMENT", []) if item.get("field")}
    bs_fields = {item.get("field") for item in metrics.get("BALANCE_SHEET", []) if item.get("field")}
    if "isb27" in is_fields or "bsb103" in bs_fields or "bsb113" in bs_fields:
        return "BANK"
    if "iss115" in is_fields or "bss214" in bs_fields or "bss215" in bs_fields:
        return "SECURITIES"
    ind = (industry_name or "").lower()
    if "ngân hàng" in ind or "bank" in ind:
        return "BANK"
    if "chứng khoán" in ind or "securit" in ind or "môi giới" in ind:
        return "SECURITIES"
    return "CORPORATE"


def fetch_price_history(ticker: str, years: int = 3) -> list[dict[str, Any]]:
    now_ts = int(time.time())
    from_ts = now_ts - years * 365 * 86400

    # Source 1: SSI iBoard Charts API
    ssi_url = (
        f"https://iboard-api.ssi.com.vn/statistics/charts/history"
        f"?resolution=1D&symbol={ticker}&from={from_ts}&to={now_ts}"
    )
    res = _get_json(ssi_url, SSI_HEADERS, timeout=10)
    data = (res or {}).get("data") or {}
    if data.get("t") and data.get("c"):
        out = []
        ts_list = data["t"]
        o_list = data.get("o", data["c"])
        h_list = data.get("h", data["c"])
        l_list = data.get("l", data["c"])
        c_list = data["c"]
        v_list = data.get("v", [0] * len(ts_list))
        for i, ts in enumerate(ts_list):
            dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            c_val = float(c_list[i])
            mult = 1000.0 if c_val < 1000 else 1.0
            out.append({
                "date": dt,
                "open": round(float(o_list[i]) * mult, 2),
                "high": round(float(h_list[i]) * mult, 2),
                "low": round(float(l_list[i]) * mult, 2),
                "close": round(c_val * mult, 2),
                "volume": float(v_list[i]),
            })
        return out

    # Source 2: DNSE Entrade fallback
    dnse_url = (
        f"https://services.entrade.com.vn/chart-api/v2/ohlcs/stock"
        f"?from={from_ts}&to={now_ts}&symbol={ticker}&resolution=1D"
    )
    res2 = _get_json(dnse_url, {"User-Agent": "Mozilla/5.0"}, timeout=10)
    if res2 and res2.get("t") and res2.get("c"):
        out = []
        for i, ts in enumerate(res2["t"]):
            dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            c_val = float(res2["c"][i])
            mult = 1000.0 if c_val < 1000 else 1.0
            out.append({
                "date": dt,
                "open": round(float(res2["o"][i]) * mult, 2),
                "high": round(float(res2["h"][i]) * mult, 2),
                "low": round(float(res2["l"][i]) * mult, 2),
                "close": round(c_val * mult, 2),
                "volume": float(res2["v"][i]),
            })
        return out
    return []


def scrape_company(ticker: str, save: bool = True) -> dict[str, Any]:
    sym = validate_ticker(ticker)

    metrics_res = _get_json(f"{VCI_BASE}/{sym}/financial-statement/metrics", VCI_HEADERS)
    if not metrics_res or not metrics_res.get("successful") or not metrics_res.get("data"):
        raise RuntimeError(f"Không tìm thấy dữ liệu BCTC cho mã '{sym}' trên Vietcap IQ.")
    metrics = metrics_res["data"]

    sections = ["INCOME_STATEMENT", "BALANCE_SHEET", "CASH_FLOW", "NOTE"]
    statements: dict[str, dict[str, list[dict[str, Any]]]] = {}

    def _fetch_sec(sec: str):
        res = _get_json(f"{VCI_BASE}/{sym}/financial-statement?section={sec}", VCI_HEADERS)
        if res and res.get("successful") and res.get("data"):
            years = sorted(res["data"].get("years", []), key=lambda x: x.get("yearReport", 0))
            quarters = sorted(
                res["data"].get("quarters", []),
                key=lambda x: (x.get("yearReport", 0), x.get("lengthReport", 0)),
            )
            return sec, {"years": years, "quarters": quarters}
        return sec, {"years": [], "quarters": []}

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        f_secs = [ex.submit(_fetch_sec, s) for s in sections]
        f_stats = ex.submit(_get_json, f"{VCI_BASE}/{sym}/statistics-financial", VCI_HEADERS)
        f_prof = ex.submit(
            _get_json,
            f"https://iboard-api.ssi.com.vn/statistics/company/ssmi/company-profile?symbol={sym}&language=vn",
            SSI_HEADERS,
        )
        f_prices = ex.submit(fetch_price_history, sym, 3)

        for fut in f_secs:
            sec, payload = fut.result()
            statements[sec] = payload
        stats_financial = (f_stats.result() or {}).get("data") or []
        profile = (f_prof.result() or {}).get("data") or {}
        prices = f_prices.result() or []

    preset = REPRESENTATIVE_UNIVERSE.get(sym, {})
    industry_name = profile.get("industryName") or profile.get("superSector") or preset.get("sector_vi") or "Doanh nghiệp"
    report_model = detect_report_model(metrics, industry_name)
    sector_code = _infer_sector_code(report_model, industry_name, sym)
    sector_vi = preset.get("sector_vi") or industry_name
    company_name = preset.get("name") or profile.get("companyName") or sym

    latest_ttm = None
    for row in reversed(stats_financial):
        if row.get("ratioType") == "RATIO_TTM" or row.get("marketCap"):
            latest_ttm = row
            break

    shares = float((latest_ttm or {}).get("numberOfSharesMktCap") or 0)
    if not shares and profile.get("issueShare"):
        try:
            shares = float(str(profile["issueShare"]).replace(",", ""))
        except Exception:
            shares = 0.0

    current_price = float(prices[-1]["close"]) if prices else 0.0
    market_cap = float((latest_ttm or {}).get("marketCap") or 0)
    if current_price > 0 and shares > 0:
        market_cap = current_price * shares
    elif market_cap > 0 and shares > 0 and current_price == 0:
        current_price = market_cap / shares

    bundle = {
        "ticker": sym,
        "company_name": company_name,
        "sector_vi": sector_vi,
        "sector_code": sector_code,
        "report_model": report_model,
        "exchange": profile.get("exchange") or "HOSE",
        "current_price": round(current_price, 2),
        "market_cap": round(market_cap, 2),
        "shares_outstanding": shares,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "profile": profile,
        "metrics": metrics,
        "statements": statements,
        "statistics_financial": stats_financial,
        "prices": prices,
    }
    if save:
        save_company_bundle(bundle)
    return bundle


def save_company_bundle(bundle: dict[str, Any]) -> Path:
    sym = validate_ticker(bundle.get("ticker"))
    path = (BCTC_DIR / f"{sym}.json").resolve()
    if not path.is_relative_to(BCTC_DIR.resolve()):
        raise ValueError("Đường dẫn lưu BCTC không hợp lệ.")
    atomic_write_json(path, bundle)
    return path


_price_refresh_ts: dict[str, float] = {}
_price_refresh_lock = threading.Lock()


def _maybe_refresh_bundle_prices(bundle: dict[str, Any]) -> dict[str, Any]:
    """Tự động cập nhật lịch sử giá OHLCV mới nhất nếu cache BCTC cũ hơn 1 ngày giao dịch (chống kẹt giá cũ trên Render)."""
    sym = (bundle.get("ticker") or "").upper().strip()
    if not sym:
        return bundle
    now = time.time()
    # Cooldown 20 minutes per ticker in memory so repeated requests stay instant
    with _price_refresh_lock:
        if now - _price_refresh_ts.get(sym, 0.0) < 1200:
            return bundle

    prices = bundle.get("prices") or []
    last_date_str = str(prices[-1].get("date") or "")[:10] if prices else ""
    today_vn = datetime.now(timezone(timedelta(hours=7)))
    # Expected latest trading date (if Sat/Sun -> Friday, else today/yesterday)
    days_stale = 99
    if last_date_str:
        try:
            last_dt = datetime.strptime(last_date_str, "%Y-%m-%d").date()
            days_stale = (today_vn.date() - last_dt).days
        except Exception as exc:
            logger.debug("Invalid price date '%s' for %s: %s", last_date_str, sym, exc)
            days_stale = 99

    # On weekend (Sat=5 -> 1 day from Fri, Sun=6 -> 2 days from Fri), Friday data is already latest!
    max_ok_gap = 1 if today_vn.weekday() == 5 else (2 if today_vn.weekday() == 6 else 0)
    with _price_refresh_lock:
        if len(_price_refresh_ts) > 500:
            cutoff = now - 3600
            for k in [k for k, v in _price_refresh_ts.items() if v < cutoff]:
                del _price_refresh_ts[k]
        _price_refresh_ts[sym] = now

    if days_stale <= max_ok_gap and len(prices) >= 60:
        return bundle

    try:
        fresh_px = fetch_price_history(sym, years=2)
        if fresh_px and len(fresh_px) >= 20:
            bundle["prices"] = fresh_px
            bundle["current_price"] = float(fresh_px[-1]["close"])
            bundle["updated_at"] = today_vn.strftime("%Y-%m-%d %H:%M:%S")
            save_company_bundle(bundle)
    except Exception as exc:
        logger.warning("Failed to refresh OHLCV prices for %s: %s", sym, exc)
    return bundle


def load_company_bundle(ticker: str, refresh_price: bool = True) -> dict[str, Any] | None:
    try:
        sym = validate_ticker(ticker)
    except ValueError:
        return None
    path = (BCTC_DIR / f"{sym}.json").resolve()
    if not path.is_relative_to(BCTC_DIR.resolve()) or not path.exists():
        return None
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
        if refresh_price and isinstance(bundle, dict):
            return _maybe_refresh_bundle_prices(bundle)
        return bundle
    except Exception as exc:
        logger.warning("Failed to load BCTC bundle for %s: %s", sym, exc)
        return None


def list_cached_companies() -> list[dict[str, Any]]:
    rows = []
    for p in sorted(BCTC_DIR.glob("*.json")):
        try:
            b = json.loads(p.read_text(encoding="utf-8"))
            rows.append({
                "ticker": b.get("ticker", p.stem).upper(),
                "company_name": b.get("company_name", p.stem),
                "sector_vi": b.get("sector_vi", ""),
                "sector_code": b.get("sector_code", "CORPORATE"),
                "report_model": b.get("report_model", "CORPORATE"),
                "exchange": b.get("exchange", "HOSE"),
                "current_price": b.get("current_price", 0),
                "market_cap": b.get("market_cap", 0),
                "updated_at": b.get("updated_at", ""),
            })
        except Exception:
            continue
    # Sort 4 core tickers first, then default universe, then alphabetically
    order_map = {t: i for i, t in enumerate(DEFAULT_TICKERS)}
    rows.sort(key=lambda r: (order_map.get(r["ticker"], 999), r["ticker"]))
    return rows


def scrape_universe(tickers: list[str] | None = None) -> dict[str, Any]:
    targets = [t.strip().upper() for t in (tickers or DEFAULT_TICKERS) if t.strip()]
    results: dict[str, list[dict[str, Any]]] = {"success": [], "failed": []}

    def _one(sym: str):
        b = scrape_company(sym, save=True)
        return {
            "ticker": b["ticker"],
            "sector_vi": b["sector_vi"],
            "report_model": b["report_model"],
            "current_price": b["current_price"],
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(_one, s): s for s in targets}
        for fut in concurrent.futures.as_completed(futs):
            sym = futs[fut]
            try:
                results["success"].append(fut.result())
            except Exception as exc:
                results["failed"].append({"ticker": sym, "error": str(exc)})
    return results


def ensure_default_universe_loaded() -> list[dict[str, Any]]:
    """Nếu kho BCTC đang trống (VD lần đầu chạy trên cloud Render), tự động cào song song 8 ngành chuẩn."""
    cached = list_cached_companies()
    if not cached:
        scrape_universe(DEFAULT_TICKERS)
        cached = list_cached_companies()
    return cached


def export_to_excel(ticker: str, period: str = "years") -> tuple[Path, str]:
    """Xuất toàn bộ BCTC ra .xlsx bằng openpyxl nhẹ (hoặc .csv dự phòng)."""
    sym_clean = validate_ticker(ticker)
    if period not in ("years", "quarters"):
        period = "years"
    cleanup_old_files(EXPORT_DIR, max_age_seconds=86400)
    bundle = load_company_bundle(sym_clean)
    if not bundle:
        raise FileNotFoundError(f"Chưa có dữ liệu BCTC cho mã {sym_clean}.")
    sym = validate_ticker(bundle.get("ticker") or sym_clean)
    stamp = datetime.now().strftime("%Y%m%d")
    metrics = bundle.get("metrics", {})
    statements = bundle.get("statements", {})

    section_titles = [
        ("INCOME_STATEMENT", "KQKD"),
        ("BALANCE_SHEET", "CDKT"),
        ("CASH_FLOW", "LCTT"),
        ("NOTE", "ThuyetMinh"),
    ]

    try:
        import openpyxl
        from openpyxl.styles import Font

        wb = openpyxl.Workbook()
        # Remove default blank sheet once we add real sheets
        default_ws = wb.active
        added_any = False

        for sec, sheet_name in section_titles:
            rows = statements.get(sec, {}).get(period, [])
            sec_metrics = metrics.get(sec, [])
            if not rows or not sec_metrics:
                continue
            recent = rows[-10:]
            col_labels = [
                f"{r.get('yearReport')}" if period == "years" else f"Q{r.get('lengthReport')}/{r.get('yearReport')}"
                for r in recent
            ]
            ws = wb.create_sheet(title=sheet_name)
            added_any = True
            header = ["Mã chỉ tiêu", "Khoản mục (Tiếng Việt)", "Item (English)", *col_labels]
            ws.append(header)
            for cell in ws[1]:
                cell.font = Font(bold=True)

            for m in sec_metrics:
                field = m.get("field")
                lvl = int(m.get("level") or 1)
                indent = "  " * max(0, lvl - 1)
                vals = []
                has_val = False
                is_eps = "eps" in (m.get("titleEn") or "").lower()
                for r in recent:
                    v = r.get(field) if field else None
                    if v is not None:
                        has_val = True
                        if isinstance(v, (int, float)) and abs(v) >= 1e6 and not is_eps:
                            vals.append(round(v / 1e9, 2))
                        else:
                            vals.append(v)
                    else:
                        vals.append(None)
                if has_val or lvl == 1:
                    ws.append([field or "", f"{indent}{m.get('titleVi') or ''}", m.get("titleEn") or "", *vals])
                    if lvl == 1:
                        for cell in ws[ws.max_row]:
                            cell.font = Font(bold=True)
            ws.column_dimensions["A"].width = 14
            ws.column_dimensions["B"].width = 48
            ws.column_dimensions["C"].width = 36

        if added_any:
            wb.remove(default_ws)
        out_path = EXPORT_DIR / f"BCTC_{sym}_{period}_{stamp}.xlsx"
        wb.save(out_path)
        return out_path, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    except ImportError:
        # Fallback UTF-8 BOM CSV if openpyxl is missing
        out_path = EXPORT_DIR / f"BCTC_{sym}_{period}_{stamp}.csv"
        buf = io.StringIO()
        w = csv.writer(buf)
        for sec, sheet_name in section_titles[:3]:
            rows = statements.get(sec, {}).get(period, [])
            sec_metrics = metrics.get(sec, [])
            if not rows or not sec_metrics:
                continue
            recent = rows[-8:]
            col_labels = [
                f"{r.get('yearReport')}" if period == "years" else f"Q{r.get('lengthReport')}/{r.get('yearReport')}"
                for r in recent
            ]
            w.writerow([f"=== {sheet_name} ({sym}) ==="])
            w.writerow(["Field", "Khoản mục", *col_labels])
            for m in sec_metrics:
                field = m.get("field")
                vals = [
                    round(r.get(field) / 1e9, 2) if isinstance(r.get(field), (int, float)) and abs(r.get(field)) >= 1e6 else r.get(field)
                    for r in recent
                ]
                if any(v is not None for v in vals):
                    w.writerow([field or "", m.get("titleVi") or "", *vals])
            w.writerow([])
        out_path.write_bytes(b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8"))
        return out_path, "text/csv; charset=utf-8"


# =============================================================================
# 2. Live Macro Context Extractor (Bridge from VĨ MÔ PULSE)
# =============================================================================

def extract_macro_context(dashboard: dict[str, Any] | None) -> dict[str, Any]:
    """Rút trích bộ biến vĩ mô sống mới nhất từ VĨ MÔ PULSE để bơm trực tiếp vào mô hình BCTC."""
    if not dashboard or not isinstance(dashboard, dict):
        return {
            "available": False,
            "degraded": True,
            "updated_at_vn": "Chưa đồng bộ",
            "macro_score": 55,
            "stance_label": "TRUNG LẬP (Mặc định)",
            "stance_short": "Trung lập",
            "stance_tone": "amber",
            "regime_name": "Bối cảnh cơ sở",
            "equity_budget_pct": 40,
            "us_10y": 3.8,
            "spread_10y_2y": 0.2,
            "fed_effr": 4.0,
            "fed_hike_prob": 15.0,
            "fed_cut_prob": 25.0,
            "fed_bias": "Giữ nguyên",
            "us_cpi": 2.6,
            "us_core_pce": 2.5,
            "vn_cpi": 3.5,
            "vn_pmi": 50.5,
            "us_ism_mfg": 50.5,
            "us_ism_svc": 52.0,
            "dxy": 102.0,
            "usdvnd": 25400.0,
            "sbv_gap_pct": None,
            "vix": 18.0,
            "wti": 72.0,
            "vni_close": None,
            "vni_trend": "Trung tính",
            "vni_rsi": 50.0,
            "vni_above_ma50": True,
            "vni_above_ma200": True,
            "vni_pe": None,
            "foreign_5d_bn": 0.0,
            "foreign_20d_bn": 0.0,
            "hose_liq_bn": 16000.0,
            "sector_rs_map": {},
        }

    st = dashboard.get("strategy") or {}
    kpis = dashboard.get("kpis") or {}
    yc = (dashboard.get("rates") or {}).get("yield_curve") or {}
    yc_latest = yc.get("latest") or {}
    fw = dashboard.get("fedwatch") or {}
    nxt = (fw.get("contracts") or [{}])[0] if fw.get("contracts") else {}
    fx = dashboard.get("fx") or {}
    vcb = dashboard.get("vcb") or {}
    sbv = dashboard.get("sbv") or {}
    vn_mkt = dashboard.get("vn_market") or {}
    vni = (vn_mkt.get("indices") or {}).get("VNINDEX") or {}
    flow = vn_mkt.get("flow") or {}
    vni_pe = (dashboard.get("vn_valuation") or {}).get("vnindex_pe") or {}
    vn_cpi_obj = (dashboard.get("inflation") or {}).get("vn_cpi_latest") or {}
    brief = dashboard.get("briefing") or {}

    eq_alloc = next((a for a in (st.get("allocation") or []) if a.get("key") == "vn_equity"), {})
    vcb_sell = (vcb.get("usd") or {}).get("sell") or (fx.get("usdvnd_spot") or {}).get("price") or 25500.0
    sbv_gap = None
    if sbv.get("available") and sbv.get("ceiling"):
        sbv_gap = round(((sbv["ceiling"] - vcb_sell) / sbv["ceiling"]) * 100.0, 2)

    sector_rs_map: dict[str, dict[str, Any]] = {}
    for sec in st.get("sector_playbook") or vn_mkt.get("sectors") or []:
        code = sec.get("code")
        if code:
            sector_rs_map[code] = {
                "name": sec.get("name", code),
                "rs_1m": _num(sec.get("rs_1m"), 0.0),
                "chg_1m": _num(sec.get("chg_1m"), 0.0),
                "above_ma50": bool(sec.get("above_ma50")),
                "score": sec.get("score"),
                "rating": sec.get("rating", "Theo dõi"),
            }

    vni_close = vni.get("close")
    vni_ma50 = vni.get("ma50")
    vni_ma200 = vni.get("ma200")

    return {
        "available": True,
        "updated_at_vn": (dashboard.get("meta") or {}).get("updated_at_vn", ""),
        "macro_score": int(st.get("total_score") or 50),
        "stance_label": (st.get("stance") or {}).get("label", "TRUNG LẬP"),
        "stance_short": (st.get("stance") or {}).get("short", "Trung lập"),
        "stance_tone": (st.get("stance") or {}).get("tone", "amber"),
        "regime_name": (brief.get("regime") or {}).get("name", "Chuyển pha Vĩ mô"),
        "equity_budget_pct": int(eq_alloc.get("weight") or 35),
        "us_10y": _num((yc_latest.get("yields") or {}).get("10Y"), 4.2),
        "spread_10y_2y": yc_latest.get("spread_10y_2y"),
        "fed_effr": _num((kpis.get("fed_rate") or {}).get("effr"), 4.0),
        "fed_hike_prob": _num(nxt.get("prob_hike"), 0.0),
        "fed_cut_prob": _num(nxt.get("prob_cut"), 0.0),
        "fed_bias": nxt.get("bias", "Giữ nguyên"),
        "us_cpi": _num((kpis.get("us_cpi") or {}).get("value"), 2.8),
        "us_core_pce": _num((kpis.get("us_core_pce") or {}).get("value"), 2.6),
        "vn_cpi": _num(vn_cpi_obj.get("yoy"), 3.5),
        "vn_pmi": _num((kpis.get("vn_pmi") or {}).get("value"), 50.0),
        "us_ism_mfg": _num((kpis.get("us_pmi") or {}).get("value"), 50.0),
        "us_ism_svc": _num((kpis.get("us_pmi") or {}).get("services"), 51.0),
        "dxy": _num((fx.get("dxy") or {}).get("price"), 102.0),
        "usdvnd": float(vcb_sell),
        "sbv_gap_pct": sbv_gap,
        "vix": _num(fx.get("vix"), 18.0),
        "wti": _num(fx.get("wti_price"), 72.0),
        "vni_close": vni_close,
        "vni_trend": vni.get("trend", "Trung tính"),
        "vni_rsi": _num(vni.get("rsi14"), 50.0),
        "vni_above_ma50": bool(vni_close and vni_ma50 and vni_close >= vni_ma50),
        "vni_above_ma200": bool(vni_close and vni_ma200 and vni_close >= vni_ma200),
        "vni_pe": vni_pe.get("pe") if vni_pe.get("available") else None,
        "foreign_5d_bn": _num(flow.get("foreign_5d_bn"), 0.0),
        "foreign_20d_bn": _num(flow.get("foreign_20d_bn"), 0.0),
        "hose_liq_bn": _num(flow.get("liquidity_bn"), 15000.0),
        "sector_rs_map": sector_rs_map,
    }


def _compute_macro_valuation_overlay(macro: dict[str, Any], sector_code: str, report_model: str) -> dict[str, Any]:
    """
    Tính độ lệch Chi phí vốn (Ke/WACC) và hệ số nén/mở định giá (Multiple Multiplier)
    dựa trực tiếp vào lợi suất US 10Y, FedWatch, VIX, DXY và dòng vốn khối ngoại thực tế.
    """
    beta = SECTOR_MACRO_BETA.get(sector_code, 1.0)
    us10y = macro.get("us_10y", 3.8)
    vix = macro.get("vix", 18.0)
    hike_p = macro.get("fed_hike_prob", 0.0)
    cut_p = macro.get("fed_cut_prob", 0.0)
    dxy = macro.get("dxy", 102.0)
    f20 = macro.get("foreign_20d_bn", 0.0)
    sbv_gap = macro.get("sbv_gap_pct")

    # 1. Rate shock vs neutral 3.50% US 10Y
    rate_delta = _clamp((us10y - 3.50) * 0.0085, -0.008, 0.022)
    # 2. Volatility / Equity Risk Premium shock vs neutral VIX 18
    vix_delta = _clamp((vix - 18.0) * 0.0008, -0.004, 0.016)
    # 3. Fed policy bias shift
    fed_delta = _clamp((hike_p - cut_p) * 0.00007, -0.005, 0.007)
    # 4. Vietnam FX & Foreign Capital flow risk premium
    fx_delta = 0.0
    if dxy >= 103.5:
        fx_delta += 0.0025
    if f20 <= -3500:
        fx_delta += 0.0025
    if sbv_gap is not None and sbv_gap < 1.2:
        fx_delta += 0.0030

    raw_shift = rate_delta + vix_delta + fed_delta + fx_delta
    ke_shift = _clamp(raw_shift * beta, -0.015, 0.045)

    base_ke_key = (
        "cost_of_equity_bank" if report_model == "BANK"
        else ("cost_of_equity_sec" if report_model == "SECURITIES" else "cost_of_equity_corp")
    )
    base_ke = MACRO_DEFAULTS[base_ke_key]
    live_ke = round(_clamp(base_ke + ke_shift, 0.105, 0.185), 4)

    # Multiple compression/expansion (P/E, EV/EBITDA, P/B)
    # Khi Ke tăng +2.5% -> hệ số định giá hợp lý nén xuống ~18% (nhân ~0.82)
    mult_adj = round(_clamp(1.0 - ke_shift * 7.2, 0.72, 1.12), 3)

    # Growth damping/boost for 5Y DCF growth based on PMI & macro score
    vn_pmi = macro.get("vn_pmi", 50.0)
    g_shift = 0.0
    if vn_pmi >= 52.0:
        g_shift += 0.008
    elif vn_pmi < 49.0:
        g_shift -= 0.015
    if macro.get("macro_score", 55) < 45:
        g_shift -= 0.010

    return {
        "sector_beta": beta,
        "base_ke": base_ke,
        "live_ke": live_ke,
        "ke_shift_bps": round((live_ke - base_ke) * 10000),
        "multiple_adj": mult_adj,
        "multiple_adj_pct": round((mult_adj - 1.0) * 100, 1),
        "growth_shift": round(g_shift, 4),
        "components": {
            "us10y_bps": round(rate_delta * beta * 10000),
            "vix_bps": round(vix_delta * beta * 10000),
            "fed_bps": round(fed_delta * beta * 10000),
            "fx_flow_bps": round(fx_delta * beta * 10000),
        },
    }


def _compute_macro_fit_channels(
    macro: dict[str, Any],
    sector_code: str,
    report_model: str,
    bctc_facts: dict[str, Any],
) -> tuple[float, list[dict[str, Any]], list[dict[str, str]]]:
    """
    Đánh giá 6 kênh truyền dẫn thực tế từ Vĩ mô hiện tại vào đúng cấu trúc BCTC của doanh nghiệp.
    Trả về: (macro_fit_score 0..100, macro_impacts list, macro_red_flags list)
    """
    score = 55.0
    impacts: list[dict[str, Any]] = []
    macro_flags: list[dict[str, str]] = []

    us10y = macro["us_10y"]
    hike_p = macro["fed_hike_prob"]
    cut_p = macro["fed_cut_prob"]
    vix = macro["vix"]
    dxy = macro["dxy"]
    usdvnd = macro["usdvnd"]
    vn_pmi = macro["vn_pmi"]
    us_pmi = macro["us_ism_mfg"]
    core_pce = macro["us_core_pce"]
    vn_cpi = macro["vn_cpi"]
    wti = macro["wti"]
    f5 = macro["foreign_5d_bn"]
    f20 = macro["foreign_20d_bn"]
    liq = macro["hose_liq_bn"]
    vni_above_50 = macro["vni_above_ma50"]
    macro_score = macro["macro_score"]

    hawkish = (hike_p >= 45) or (us10y >= 4.5)
    dovish = (cut_p >= 50) and (us10y <= 3.9)

    net_cash_mcap = bctc_facts.get("net_cash_to_mcap", 0.0)
    debt_to_eq = bctc_facts.get("debt_to_eq", 0.0)
    int_cov = bctc_facts.get("interest_cov", 10.0)
    casa = bctc_facts.get("casa", 0.0)
    llr = bctc_facts.get("llr", 0.0)
    npl = bctc_facts.get("npl", 0.0)
    margin_to_eq = bctc_facts.get("margin_to_eq", 0.0)
    fvtpl_to_eq = bctc_facts.get("fvtpl_to_eq", 0.0)
    wip_to_assets = bctc_facts.get("wip_to_assets", 0.0)
    gross_margin = bctc_facts.get("gross_margin", 0.0)
    div_yield = bctc_facts.get("dividend_yield", 0.0)

    # ── Kênh 1: Lãi suất (US 10Y + FedWatch) × Cấu trúc Bảng CĐKT trên BCTC ──
    if report_model == "BANK":
        if hawkish:
            if casa >= 0.28 and llr >= 1.20:
                d = 8.0
                impacts.append({
                    "channel": "Lãi suất & Chi phí vốn (COF)",
                    "signal": "positive",
                    "delta": d,
                    "macro_fact": f"US 10Y {us10y:.2f}% · Fed {macro['fed_bias']} ({max(hike_p, cut_p):.0f}%)",
                    "bctc_link": f"CASA {casa*100:.1f}% · LLR {llr*100:.0f}% · NPL {npl*100:.2f}%",
                    "explanation": "Lợi thế CASA cao & bộ đệm dự phòng LLR dày giúp ngân hàng giữ NIM vượt trội so với ngành khi mặt bằng lãi suất chịu áp lực tăng.",
                })
            elif casa < 0.18 or npl > 0.022:
                d = -14.0
                impacts.append({
                    "channel": "Lãi suất & Nợ xấu hệ thống",
                    "signal": "negative",
                    "delta": d,
                    "macro_fact": f"US 10Y {us10y:.2f}% · Fed {macro['fed_bias']} ({hike_p:.0f}%)",
                    "bctc_link": f"CASA {casa*100:.1f}% · NPL {npl*100:.2f}%",
                    "explanation": "Lãi suất neo cao kéo chi phí huy động tăng và đè áp lực nợ xấu lên các ngân hàng có CASA thấp hoặc NPL cao.",
                })
                macro_flags.append({
                    "severity": "high",
                    "source": "macro",
                    "title": "Rủi ro co NIM & nợ xấu trong môi trường lãi suất cao",
                    "detail": f"US 10Y tại {us10y:.2f}% & Fed nghiêng diều hâu trong khi CASA ({casa*100:.1f}%) chưa đủ dày để phòng thủ chi phí vốn.",
                })
            else:
                d = -3.0
                impacts.append({
                    "channel": "Lãi suất & Biên NIM",
                    "signal": "neutral",
                    "delta": d,
                    "macro_fact": f"US 10Y {us10y:.2f}% · Fed {macro['fed_bias']}",
                    "bctc_link": f"CASA {casa*100:.1f}% · LLR {llr*100:.0f}%",
                    "explanation": "Áp lực nhẹ lên chi phí huy động nhưng bộ đệm chất lượng tài sản vẫn nằm trong vùng kiểm soát.",
                })
        elif dovish:
            d = 10.0
            impacts.append({
                "channel": "Lãi suất & Tín dụng",
                "signal": "positive",
                "delta": d,
                "macro_fact": f"Fed nghiêng Hạ lãi suất ({cut_p:.0f}%) · US 10Y {us10y:.2f}%",
                "bctc_link": f"CASA {casa*100:.1f}% · LLR {llr*100:.0f}%",
                "explanation": "Chu kỳ nới lỏng giảm chi phí huy động, hỗ trợ mở rộng NIM và giảm áp lực trích lập dự phòng.",
            })
        else:
            d = 2.0
            impacts.append({
                "channel": "Lãi suất & Biên NIM",
                "signal": "neutral",
                "delta": d,
                "macro_fact": f"US 10Y {us10y:.2f}% · Fed {macro['fed_bias']}",
                "bctc_link": f"CASA {casa*100:.1f}%",
                "explanation": "Mặt bằng lãi suất ổn định giúp NIM và chi phí vốn của ngân hàng duy trì cân bằng.",
            })
        score += d

    elif report_model == "SECURITIES":
        if hawkish:
            d = -15.0
            impacts.append({
                "channel": "Lãi suất & Chi phí vay Margin",
                "signal": "negative",
                "delta": d,
                "macro_fact": f"US 10Y {us10y:.2f}% · Fed {macro['fed_bias']} ({hike_p:.0f}%)",
                "bctc_link": f"Margin/VCSH {margin_to_eq*100:.0f}% · Nợ/VCSH {debt_to_eq:.2f}x",
                "explanation": "Lãi suất cao làm tăng chi phí vốn đi vay của CTCK, nén biên lãi cho vay margin và hút dòng tiền cá nhân khỏi chứng khoán.",
            })
            macro_flags.append({
                "severity": "high",
                "source": "macro",
                "title": "Chứng khoán ngược pha chu kỳ lãi suất diều hâu",
                "detail": f"FedWatch định giá {hike_p:.0f}% nâng lãi suất & US 10Y {us10y:.2f}% gây bất lợi trực tiếp cho nhóm cổ phiếu High-Beta chứng khoán.",
            })
        elif dovish:
            d = 15.0
            impacts.append({
                "channel": "Lãi suất & Dòng tiền rẻ",
                "signal": "positive",
                "delta": d,
                "macro_fact": f"Fed nghiêng Hạ lãi suất ({cut_p:.0f}%) · US 10Y {us10y:.2f}%",
                "bctc_link": f"Margin/VCSH {margin_to_eq*100:.0f}% (còn dư địa trần 200%)",
                "explanation": "Lãi suất hạ là chất xúc tác mạnh nhất cho dòng tiền margin và định giá lại danh mục tự doanh FVTPL.",
            })
        else:
            d = 0.0
            impacts.append({
                "channel": "Lãi suất & Margin",
                "signal": "neutral",
                "delta": d,
                "macro_fact": f"US 10Y {us10y:.2f}% · Fed {macro['fed_bias']}",
                "bctc_link": f"Margin/VCSH {margin_to_eq*100:.0f}%",
                "explanation": "Lãi suất trung tính; động lực chính của CTCK phụ thuộc trực tiếp vào thanh khoản khớp lệnh HOSE.",
            })
        score += d

    else:  # CORPORATE
        if hawkish:
            if sector_code == "REAL_ESTATE":
                d = -20.0 if debt_to_eq > 0.45 else -14.0
                impacts.append({
                    "channel": "Lãi suất × Bất động sản",
                    "signal": "negative",
                    "delta": d,
                    "macro_fact": f"US 10Y {us10y:.2f}% · Fed {macro['fed_bias']} ({hike_p:.0f}%)",
                    "bctc_link": f"D/E {debt_to_eq:.2f}x · EBIT/Lãi vay {int_cov:.1f}x",
                    "explanation": "Bất động sản nhạy cảm nhất với lãi suất cao: lãi suất vay mua nhà khó giảm sâu và chi phí tài chính dự án tăng.",
                })
                macro_flags.append({
                    "severity": "high",
                    "source": "macro",
                    "title": "Áp lực Vĩ mô mạnh lên nhóm Bất động sản",
                    "detail": f"Mặt bằng lãi suất toàn cầu cao (US 10Y {us10y:.2f}%, Fed {hike_p:.0f}% nâng lãi) kìm hãm thanh khoản và định giá nhóm BĐS.",
                })
            elif net_cash_mcap >= 0.12:
                d = 13.0
                impacts.append({
                    "channel": "Lãi suất × Tiền mặt ròng (Net Cash)",
                    "signal": "positive",
                    "delta": d,
                    "macro_fact": f"US 10Y {us10y:.2f}% · Môi trường lãi suất cao",
                    "bctc_link": f"Tiền mặt ròng = {net_cash_mcap*100:.1f}% vốn hóa · D/E {debt_to_eq:.2f}x",
                    "explanation": "Cấu trúc bảng cân đối 'sạch nợ, thừa tiền' giúp doanh nghiệp miễn nhiễm rủi ro lãi suất cao và tăng thu nhập lãi tiền gửi.",
                })
            elif debt_to_eq >= 0.85 or int_cov < 4.0:
                d = -13.0
                impacts.append({
                    "channel": "Lãi suất × Đòn bẩy nợ BCTC",
                    "signal": "negative",
                    "delta": d,
                    "macro_fact": f"US 10Y {us10y:.2f}% · Fed {macro['fed_bias']} ({hike_p:.0f}%)",
                    "bctc_link": f"Nợ vay/VCSH {debt_to_eq:.2f}x · EBIT/Lãi vay {int_cov:.1f}x",
                    "explanation": "Tỷ lệ đòn bẩy nợ cao gặp bối cảnh lãi suất neo cao làm tăng rủi ro chi phí tài chính ăn mòn biên lợi nhuận ròng.",
                })
                macro_flags.append({
                    "severity": "medium",
                    "source": "macro",
                    "title": "Đòn bẩy nợ nhạy cảm với lãi suất neo cao",
                    "detail": f"D/E đạt {debt_to_eq:.2f}x khi US 10Y ở {us10y:.2f}% — cần theo dõi chi phí lãi vay trên KQKD các quý tới.",
                })
            else:
                d = -3.0
                impacts.append({
                    "channel": "Lãi suất & Chi phí vốn (WACC)",
                    "signal": "neutral",
                    "delta": d,
                    "macro_fact": f"US 10Y {us10y:.2f}% · Fed {macro['fed_bias']}",
                    "bctc_link": f"D/E {debt_to_eq:.2f}x · Trả lãi {int_cov:.1f}x",
                    "explanation": "Sức khỏe nợ vay ở mức an toàn nhưng lãi suất cao vẫn gây hiệu ứng nén bội số P/E định giá chung.",
                })
        elif dovish:
            d = 12.0 if sector_code in ("REAL_ESTATE", "CYCLICAL_MFG", "RETAIL") else 7.0
            impacts.append({
                "channel": "Lãi suất hạ nhiệt × Chi phí vốn",
                "signal": "positive",
                "delta": d,
                "macro_fact": f"Fed nghiêng Hạ lãi suất ({cut_p:.0f}%) · US 10Y {us10y:.2f}%",
                "bctc_link": f"D/E {debt_to_eq:.2f}x",
                "explanation": "Chi phí vốn WACC giảm trực tiếp giúp mở rộng định giá DCF/P/E và kích thích cầu tiêu dùng/đầu tư.",
            })
        else:
            d = 2.0 if net_cash_mcap > 0 else 0.0
            impacts.append({
                "channel": "Lãi suất & Bảng cân đối",
                "signal": "neutral",
                "delta": d,
                "macro_fact": f"US 10Y {us10y:.2f}% · Fed {macro['fed_bias']}",
                "bctc_link": f"D/E {debt_to_eq:.2f}x",
                "explanation": "Lãi suất ổn định không tạo áp lực đột biến lên chi phí tài chính.",
            })
        score += d

    # ── Kênh 2: Chu kỳ Sản xuất & Tăng trưởng (VN PMI & US ISM) × Ngành & CAPEX ──
    both_pmi_exp = vn_pmi >= 50.5 and us_pmi >= 50.5
    pmi_weak = vn_pmi < 49.5
    if sector_code in ("CYCLICAL_MFG", "INDUSTRIAL", "TECH_GROWTH"):
        if both_pmi_exp:
            d = 12.0 + (3.0 if wip_to_assets >= 0.12 else 0.0)
            impacts.append({
                "channel": "PMI Sản xuất Toàn cầu × Đơn hàng / Công suất",
                "signal": "positive",
                "delta": d,
                "macro_fact": f"PMI VN {vn_pmi:.1f} & ISM Mỹ {us_pmi:.1f} (đồng pha mở rộng > 50)",
                "bctc_link": f"TS dở dang/TTS {wip_to_assets*100:.1f}%" if wip_to_assets > 0.05 else "Nhóm xuất khẩu / sản xuất đầu ngành",
                "explanation": "Kinh tế thực Mỹ & Việt Nam cùng mở rộng tạo lực cầu đơn hàng mạnh, đặc biệt khuếch đại tăng trưởng cho doanh nghiệp sắp đưa công suất mới vào vận hành.",
            })
        elif pmi_weak:
            d = -10.0
            impacts.append({
                "channel": "PMI Sản xuất Thu hẹp",
                "signal": "negative",
                "delta": d,
                "macro_fact": f"PMI VN {vn_pmi:.1f} (< 50) · ISM Mỹ {us_pmi:.1f}",
                "bctc_link": "Doanh nghiệp chu kỳ sản xuất",
                "explanation": "Sản xuất suy yếu làm chậm vòng quay tồn kho và gây áp lực lên biên lợi nhuận gộp.",
            })
        else:
            d = 3.0
            impacts.append({
                "channel": "PMI Sản xuất",
                "signal": "neutral",
                "delta": d,
                "macro_fact": f"PMI VN {vn_pmi:.1f} · ISM Mỹ {us_pmi:.1f}",
                "bctc_link": "Hoạt động sản xuất duy trì ổn định",
                "explanation": "Nhu cầu sản xuất duy trì trạng thái cân bằng.",
            })
        score += d
    elif report_model == "BANK":
        d = 6.0 if vn_pmi >= 51.0 else (-6.0 if pmi_weak else 1.0)
        impacts.append({
            "channel": "PMI Việt Nam × Cầu Tín dụng",
            "signal": "positive" if d > 2 else ("negative" if d < 0 else "neutral"),
            "delta": d,
            "macro_fact": f"PMI VN {vn_pmi:.1f}",
            "bctc_link": "Động lực giải ngân tín dụng doanh nghiệp",
            "explanation": "PMI sản xuất VN mở rộng (>51) kéo cầu vốn lưu động & đầu tư doanh nghiệp tăng, hỗ trợ tăng trưởng tín dụng thực chất." if d > 2 else "Cầu tín dụng doanh nghiệp ở mức vừa phải.",
        })
        score += d

    # ── Kênh 3: Lạm phát (Core PCE / CPI VN) & Hàng hóa (WTI Oil) × Biên gộp BCTC ──
    hot_infl = core_pce >= 2.8 or vn_cpi >= 4.2
    high_oil = wti >= 78.0
    if sector_code in ("CHEMICALS", "ENERGY"):
        if hot_infl or high_oil:
            d = 10.0
            impacts.append({
                "channel": "Chu kỳ Hàng hóa / Reflation × Biên gộp",
                "signal": "positive",
                "delta": d,
                "macro_fact": f"Dầu WTI ${wti:.1f}/thùng · Core PCE {core_pce:.2f}% · CPI VN {vn_cpi:.2f}%",
                "bctc_link": f"Biên gộp BCTC {gross_margin*100:.1f}%",
                "explanation": "Pha lạm phát hàng hóa & giá năng lượng neo cao thường đi cùng mặt bằng giá bán phân bón/hóa chất/năng lượng thuận lợi, bảo vệ dòng tiền phòng hộ lạm phát.",
            })
        else:
            d = -3.0
            impacts.append({
                "channel": "Giá Hàng hóa hạ nhiệt",
                "signal": "neutral",
                "delta": d,
                "macro_fact": f"WTI ${wti:.1f}/thùng · Core PCE {core_pce:.2f}%",
                "bctc_link": f"Biên gộp {gross_margin*100:.1f}%",
                "explanation": "Mặt bằng hàng hóa dịu lại khiến động lực đột biến giá bán ngắn hạn giảm bớt.",
            })
        score += d
    elif sector_code in ("RETAIL", "CONSUMER"):
        if vn_cpi >= 4.5:
            if gross_margin >= 0.35:
                d = -2.0
                impacts.append({
                    "channel": "Lạm phát CPI VN × Sức mạnh định giá",
                    "signal": "neutral",
                    "delta": d,
                    "macro_fact": f"CPI VN {vn_cpi:.2f}% (cao hơn mục tiêu 4.5%)",
                    "bctc_link": f"Biên gộp rất cao ({gross_margin*100:.1f}%)",
                    "explanation": "Lạm phát nội địa cao tạo áp lực nhẹ lên sức mua nhưng biên gộp >35% chứng tỏ thương hiệu đủ sức chuyển giá và giữ lợi nhuận.",
                })
            else:
                d = -7.0
                impacts.append({
                    "channel": "Lạm phát CPI VN × Sức mua tiêu dùng",
                    "signal": "negative",
                    "delta": d,
                    "macro_fact": f"CPI VN {vn_cpi:.2f}% · Dầu WTI ${wti:.1f}",
                    "bctc_link": f"Biên gộp {gross_margin*100:.1f}%",
                    "explanation": "CPI nội địa tăng cao dễ khiến người tiêu dùng thắt chặt chi tiêu hàng không thiết yếu và tăng chi phí logistics.",
                })
            score += d

    # ── Kênh 4: Tỷ giá (DXY & USD/VND) × Xuất khẩu / Ngoại tệ ──
    strong_usd = dxy >= 103.0 or usdvnd >= 25600
    if sector_code == "TECH_GROWTH":
        d = 9.0 if strong_usd else 2.0
        impacts.append({
            "channel": "Tỷ giá USD/VND × Doanh thu Xuất khẩu",
            "signal": "positive" if strong_usd else "neutral",
            "delta": d,
            "macro_fact": f"DXY {dxy:.1f} · USD bán VCB {usdvnd:,.0f}₫",
            "bctc_link": "Doanh thu xuất khẩu phần mềm thu ngoại tệ",
            "explanation": "Đồng USD khỏe hỗ trợ trực tiếp doanh thu quy đổi VND cho doanh nghiệp công nghệ xuất khẩu (natural FX hedge)." if strong_usd else "Tỷ giá ổn định, tác động trung tính.",
        })
        score += d
    elif strong_usd and sector_code in ("REAL_ESTATE", "SECURITIES"):
        d = -6.0
        impacts.append({
            "channel": "Áp lực Tỷ giá & Chính sách SBV",
            "signal": "negative",
            "delta": d,
            "macro_fact": f"DXY {dxy:.1f} · USD VCB {usdvnd:,.0f}₫",
            "bctc_link": "Nhóm cổ phiếu nhạy cảm thanh khoản VND",
            "explanation": "Tỷ giá USD/VND neo cao thu hẹp dư địa nới lỏng tiền tệ của NHNN (SBV), tạo lực cản định giá lên nhóm nhạy thanh khoản.",
        })
        score += d

    # ── Kênh 5: Dòng tiền Thị trường (VN-Index, VIX, Thanh khoản HOSE, Khối ngoại) ──
    if report_model == "SECURITIES":
        if (not vni_above_50) and (liq < 13000 or vix >= 25 or f20 < -3000):
            d = -18.0
            impacts.append({
                "channel": "Thanh khoản HOSE & VIX × Doanh thu CTCK & FVTPL",
                "signal": "negative",
                "delta": d,
                "macro_fact": f"Khớp lệnh HOSE {liq:,.0f} tỷ · VIX {vix:.1f} · Ngoại 20p {f20:+,.0f} tỷ",
                "bctc_link": f"FVTPL/VCSH {fvtpl_to_eq:.2f}x · Margin/VCSH {margin_to_eq*100:.0f}%",
                "explanation": "Thanh khoản toàn sàn sụt giảm mạnh trong pha VN-Index giảm & VIX > 25 sẽ kéo doanh thu môi giới đi xuống và tăng rủi ro lỗ đánh giá lại FVTPL.",
            })
            macro_flags.append({
                "severity": "high",
                "source": "macro",
                "title": "Thanh khoản HOSE suy yếu & biến động VIX cao",
                "detail": f"Thanh khoản khớp lệnh chỉ {liq:,.0f} tỷ kèm khối ngoại bán ròng {f20:+,.0f} tỷ (20 phiên) — chưa có điểm kích hoạt chu kỳ sóng Chứng khoán.",
            })
        elif vni_above_50 and liq >= 18000:
            d = 14.0
            impacts.append({
                "channel": "Thanh khoản HOSE bùng nổ",
                "signal": "positive",
                "delta": d,
                "macro_fact": f"Khớp lệnh HOSE {liq:,.0f} tỷ · VN-Index trên MA50",
                "bctc_link": f"Dư địa Margin/VCSH {margin_to_eq*100:.0f}%",
                "explanation": "Thanh khoản cao & xu hướng tăng là môi trường vàng để CTCK khuếch đại lợi nhuận môi giới, margin và tự doanh.",
            })
        else:
            d = -2.0
            impacts.append({
                "channel": "Thanh khoản HOSE & Dòng vốn ngoại",
                "signal": "neutral",
                "delta": d,
                "macro_fact": f"Khớp lệnh HOSE {liq:,.0f} tỷ · Ngoại 5p {f5:+,.0f} tỷ",
                "bctc_link": "Độ nhạy cao với vòng quay thị trường",
                "explanation": "Thanh khoản trung bình, cần quan sát thêm tín hiệu dòng tiền lớn trở lại.",
            })
        score += d
    else:
        # Chế độ Risk-Off / VIX cao -> thưởng cho phòng thủ cổ tức cao / Net Cash, phạt nhẹ nhóm rủi ro cao
        if vix >= 25 or macro_score < 48:
            if sector_code in ("CONSUMER", "HEALTHCARE", "CHEMICALS") or (report_model == "BANK" and casa >= 0.28 and llr >= 1.2) or div_yield >= 0.045:
                d = 8.0
                impacts.append({
                    "channel": "Chế độ Risk-Off (VIX cao) × Tài sản Phòng thủ",
                    "signal": "positive",
                    "delta": d,
                    "macro_fact": f"La bàn Vĩ mô {macro_score}/100 · VIX {vix:.1f} · Ngoại 5p {f5:+,.0f} tỷ",
                    "bctc_link": f"Cổ tức {div_yield*100:.1f}% · Nền tảng phòng thủ đầu ngành",
                    "explanation": "Trong giai đoạn thị trường biến động mạnh & khối ngoại rút ròng, doanh nghiệp đầu ngành giàu tiền mặt / cổ tức cao giữ giá tốt nhất.",
                })
            else:
                d = -6.0
                impacts.append({
                    "channel": "Tâm lý Thị trường & Khối ngoại",
                    "signal": "negative",
                    "delta": d,
                    "macro_fact": f"VN-Index {macro['vni_trend']} · VIX {vix:.1f} · Ngoại 20p {f20:+,.0f} tỷ",
                    "bctc_link": "Rủi ro thị trường chung (Systemic Risk)",
                    "explanation": "Dòng vốn ngoại bán ròng & VIX cao có thể kéo giá cổ phiếu ngắn hạn xuống sâu hơn giá trị nội tại — cần chia nhỏ lệnh thay vì mua đuổi.",
                })
            score += d
        elif macro_score >= 65 and vni_above_50:
            d = 7.0
            impacts.append({
                "channel": "Tâm lý Risk-On & Xu hướng VN-Index",
                "signal": "positive",
                "delta": d,
                "macro_fact": f"La bàn Vĩ mô {macro_score}/100 · VN-Index trên MA50",
                "bctc_link": "Thuận pha thị trường chung",
                "explanation": "Xu hướng chung tích cực giúp định giá cổ phiếu cơ bản tốt dễ dàng hội tụ nhanh về Fair Value.",
            })
            score += d

    # ── Kênh 6: Sức mạnh Tương đối Nhóm ngành HOSE (Live RS 1M vs VN-Index) ──
    hose_code = SECTOR_TO_HOSE.get(sector_code)
    sec_live = (macro.get("sector_rs_map") or {}).get(hose_code) if hose_code else None
    if sec_live:
        rs1m = sec_live.get("rs_1m", 0.0)
        d = round(_clamp(rs1m * 1.1, -7.0, 7.0), 1)
        if abs(d) >= 1.0:
            impacts.append({
                "channel": f"Dòng tiền Ngành HOSE ({hose_code})",
                "signal": "positive" if d > 0 else "negative",
                "delta": d,
                "macro_fact": f"RS 1T ngành {hose_code}: {rs1m:+.1f}% vs VN-Index (1T: {sec_live.get('chg_1m', 0):+.1f}%)",
                "bctc_link": f"Xếp hạng vĩ mô ngành: {sec_live.get('rating', 'Theo dõi')}",
                "explanation": "Dòng tiền thông minh trên sàn đang ưu tiên nhóm ngành này mạnh hơn VN-Index." if d > 0 else "Nhóm ngành đang bị dòng tiền ngắn hạn thận trọng hơn mặt bằng VN-Index.",
            })
            score += d

    return round(_clamp(score, 15.0, 96.0), 1), impacts, macro_flags


# =============================================================================
# 3. Core BCTC Analyzer + Macro Fusion (Full 3 Vietnamese Accounting Models)
# =============================================================================

def _extract_stats_series(stats: list[dict[str, Any]]):
    yearly, ttm_list = [], []
    for row in stats:
        rtype = row.get("ratioType") or ""
        if rtype == "RATIO_YEAR" or row.get("quarter") in (None, 0, 5):
            yearly.append(row)
        else:
            ttm_list.append(row)
    yearly.sort(key=lambda x: int(x.get("yearReport") or x.get("year") or 0))
    ttm_list.sort(key=lambda x: (int(x.get("yearReport") or x.get("year") or 0), int(x.get("quarter") or 0)))
    seen_y = {int(y.get("yearReport") or y.get("year") or 0): y for y in yearly}
    yearly_clean = [seen_y[k] for k in sorted(seen_y.keys()) if k > 0]
    latest = ttm_list[-1] if ttm_list else (yearly_clean[-1] if yearly_clean else {})
    return yearly_clean, ttm_list, latest


def _sum_ttm(quarters: list[dict[str, Any]], field: str) -> float:
    return sum(_num(q.get(field)) for q in quarters[-4:]) if quarters else 0.0


def _compute_price_stats(prices: list[dict[str, Any]]) -> dict[str, Any]:
    if not prices:
        return {
            "current": 0.0, "high_52w": 0.0, "low_52w": 0.0, "pct_from_52w_high": None,
            "ret_3m": None, "ret_1y": None, "volatility_1y": None, "ma50": None, "ma200": None,
            "trend": "Chưa có dữ liệu giá",
        }
    closes = [float(p["close"]) for p in prices if p.get("close")]
    cur = closes[-1]
    w52 = closes[-250:] if len(closes) >= 250 else closes
    hi52, lo52 = max(w52), min(w52)
    ret_3m = _safe_div(cur - closes[-63], closes[-63]) if len(closes) > 63 else None
    ret_1y = _safe_div(cur - closes[-250], closes[-250]) if len(closes) > 250 else None
    ma50 = sum(closes[-50:]) / 50.0 if len(closes) >= 50 else None
    ma200 = sum(closes[-200:]) / 200.0 if len(closes) >= 200 else None
    vol = None
    if len(w52) > 20:
        rets = [(w52[i] / w52[i - 1] - 1.0) for i in range(1, len(w52)) if w52[i - 1] > 0]
        if rets:
            mean_r = sum(rets) / len(rets)
            vol = math.sqrt(sum((r - mean_r) ** 2 for r in rets) / len(rets)) * math.sqrt(252)
    if ma50 and ma200:
        if cur > ma50 > ma200:
            trend = "Xu hướng tăng (Giá > MA50 > MA200)"
        elif cur > ma200 and cur <= ma50:
            trend = "Điều chỉnh trong xu hướng tăng (Giá > MA200)"
        elif cur < ma200 and cur > ma50:
            trend = "Phục hồi kỹ thuật (Giá > MA50, dưới MA200)"
        else:
            trend = "Xu hướng giảm / tích lũy đáy (Giá < MA50 & MA200)"
    else:
        trend = "Trung tính"
    return {
        "current": round(cur, 2),
        "high_52w": round(hi52, 2),
        "low_52w": round(lo52, 2),
        "pct_from_52w_high": _safe_div(cur - hi52, hi52),
        "ret_3m": ret_3m,
        "ret_1y": ret_1y,
        "volatility_1y": vol,
        "ma50": round(ma50, 2) if ma50 else None,
        "ma200": round(ma200, 2) if ma200 else None,
        "trend": trend,
    }


def _compute_piotroski_f_score(is_y, bs_y, cf_y) -> dict[str, Any]:
    if len(is_y) < 2 or len(bs_y) < 2 or len(cf_y) < 2:
        return {"score": None, "max": 9, "label": "Không đủ dữ liệu", "details": []}
    is1, is0 = is_y[-1], is_y[-2]
    bs1, bs0 = bs_y[-1], bs_y[-2]
    cf1 = cf_y[-1]
    ni1, ni0 = _num(is1.get("isa20")), _num(is0.get("isa20"))
    ta1, ta0 = _num(bs1.get("bsa53")), _num(bs0.get("bsa53"))
    ocf1 = _num(cf1.get("cfa18"))
    roa1 = _safe_div(ni1, ta1, 0.0) or 0.0
    roa0 = _safe_div(ni0, ta0, 0.0) or 0.0
    lev1 = _safe_div(_num(bs1.get("bsa71")), ta1, 0.0) or 0.0
    lev0 = _safe_div(_num(bs0.get("bsa71")), ta0, 0.0) or 0.0
    cr1 = _safe_div(_num(bs1.get("bsa1")), _num(bs1.get("bsa55")), 0.0) or 0.0
    cr0 = _safe_div(_num(bs0.get("bsa1")), _num(bs0.get("bsa55")), 0.0) or 0.0
    cap1, cap0 = _num(bs1.get("bsa80")), _num(bs0.get("bsa80"))
    rev1, rev0 = _num(is1.get("isa3")), _num(is0.get("isa3"))
    gm1 = _safe_div(_num(is1.get("isa5")), rev1, 0.0) or 0.0
    gm0 = _safe_div(_num(is0.get("isa5")), rev0, 0.0) or 0.0
    at1 = _safe_div(rev1, ta1, 0.0) or 0.0
    at0 = _safe_div(rev0, ta0, 0.0) or 0.0

    checks = [
        ("ROA dương (> 0)", roa1 > 0, f"{roa1*100:.1f}%"),
        ("Dòng tiền kinh doanh (OCF) dương", ocf1 > 0, _fmt_bn(ocf1)),
        ("ROA cải thiện so với năm trước", roa1 > roa0, f"{roa0*100:.1f}% → {roa1*100:.1f}%"),
        ("Chất lượng lợi nhuận (OCF > LNST)", ocf1 > ni1, f"OCF/LNST = {(_safe_div(ocf1, ni1) or 0):.2f}x"),
        ("Giảm tỷ lệ nợ dài hạn / Tổng TS", lev1 <= lev0 + 0.005, f"{lev0*100:.1f}% → {lev1*100:.1f}%"),
        ("Hệ số thanh toán hiện hành cải thiện", cr1 >= cr0 - 0.02, f"{cr0:.2f}x → {cr1:.2f}x"),
        ("Không pha loãng vốn góp", cap1 <= cap0 * 1.02 if cap0 > 0 else True, f"Vốn góp {(_safe_div(cap1-cap0, cap0) or 0)*100:+.1f}%"),
        ("Biên lợi nhuận gộp mở rộng", gm1 >= gm0 - 0.002, f"{gm0*100:.1f}% → {gm1*100:.1f}%"),
        ("Vòng quay tổng tài sản tăng", at1 >= at0 - 0.01, f"{at0:.2f}x → {at1:.2f}x"),
    ]
    score = sum(1 for _, p, _ in checks if p)
    label = (
        "Rất mạnh (Xuất sắc)" if score >= 7
        else ("Ổn định / Khá tốt" if score >= 5 else ("Trung bình — theo dõi" if score >= 3 else "Yếu — suy giảm"))
    )
    return {
        "score": score,
        "max": 9,
        "label": label,
        "details": [{"criterion": c, "passed": p, "evidence": e} for c, p, e in checks],
    }


def _compute_altman_z_score(is_row, bs_row, market_cap: float) -> dict[str, Any]:
    ta, tl = _num(bs_row.get("bsa53")), _num(bs_row.get("bsa54"))
    if ta <= 0 or tl <= 0:
        return {"score": None, "zone": "N/A"}
    wc = _num(bs_row.get("bsa1")) - _num(bs_row.get("bsa55"))
    re_val = _num(bs_row.get("bsa90"))
    ebit = _num(is_row.get("isa16")) + abs(_num(is_row.get("isa8")))
    sales = _num(is_row.get("isa3"))
    z = 1.2 * (wc / ta) + 1.4 * (re_val / ta) + 3.3 * (ebit / ta) + 0.6 * (market_cap / tl) + 1.0 * (sales / ta)
    zone = "Vùng An toàn (Safe)" if z >= 2.99 else ("Vùng Xám (Grey)" if z >= 1.81 else "Vùng Rủi ro (Distress)")
    return {"score": round(z, 2), "zone": zone}


def _historical_valuation_bands(stats_series: list[dict[str, Any]], key: str) -> dict[str, float | None]:
    vals = [float(r[key]) for r in stats_series if r.get(key) is not None and 0 < float(r[key]) < 80]
    if len(vals) < 3:
        return {"mean": None, "min": None, "max": None, "std": None, "low_band": None, "high_band": None}
    mean = sum(vals) / len(vals)
    std = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals))
    return {
        "mean": round(mean, 2),
        "min": round(min(vals), 2),
        "max": round(max(vals), 2),
        "std": round(std, 2),
        "low_band": round(max(min(vals), mean - std), 2),
        "high_band": round(mean + std, 2),
    }


def _run_valuation_models(
    *,
    report_model: str,
    bvps: float,
    eps_ttm: float,
    roe_ttm: float,
    npat_ttm: float,
    fcf_ttm: float,
    npat_cagr_5y: float | None,
    shares: float,
    ebitda_val: float,
    cash_st: float,
    tot_debt: float,
    pe_bands: dict[str, Any],
    pb_bands: dict[str, Any],
    benchmarks: dict[str, Any],
    ke: float,
    wacc: float,
    g_term: float,
    multiple_adj: float = 1.0,
    growth_shift: float = 0.0,
    custom_assumptions: dict[str, float] | None = None,
) -> tuple[list[dict[str, Any]], float, float, float]:
    cust = custom_assumptions or {}
    models: list[dict[str, Any]] = []

    # 1. Justified P/B
    if bvps > 0 and roe_ttm > 0:
        roe_sust = _clamp(cust.get("target_roe", roe_ttm), 0.06, 0.28)
        g_pb = min(g_term, roe_sust * 0.4)
        justified_pb = _clamp((roe_sust - g_pb) / max(0.035, ke - g_pb), 0.55, 4.5)
        weight = 0.50 if report_model in ("BANK", "SECURITIES") else 0.15
        models.append({
            "method": "Justified P/B (Gordon Growth)",
            "fair_value": round(justified_pb * bvps, 0),
            "weight": weight,
            "formula": f"P/B = (ROE {_pct(roe_sust,1)} - g {_pct(g_pb,1)}) / (Ke {_pct(ke,1)} - g) = {justified_pb:.2f}x × BVPS {_fmt_price(bvps)}",
        })

    # 2. Historical P/B (Macro-adjusted multiple)
    if bvps > 0 and pb_bands.get("mean"):
        base_pb = _clamp(pb_bands["mean"], 0.7, 5.0)
        adj_pb = _clamp(base_pb * (1.0 if cust else multiple_adj), 0.55, 5.0)
        weight = 0.30 if report_model in ("BANK", "SECURITIES") else 0.15
        note_m = f" × hệ số vĩ mô {multiple_adj:.2f}x" if (not cust and abs(multiple_adj - 1.0) >= 0.02) else ""
        models.append({
            "method": "P/B Lịch sử (Điều chỉnh Vĩ mô)" if note_m else "P/B Trung bình lịch sử",
            "fair_value": round(adj_pb * bvps, 0),
            "weight": weight,
            "formula": f"P/B TB {base_pb:.2f}x{note_m} = {adj_pb:.2f}x × BVPS {_fmt_price(bvps)}",
        })

    # 3. Relative P/E (Macro-adjusted multiple)
    if eps_ttm > 0:
        peer_pe = benchmarks.get("pe", 13.0)
        hist_pe = pe_bands.get("mean") or peer_pe
        raw_pe = _clamp(0.5 * peer_pe + 0.5 * min(hist_pe, peer_pe * 1.4), 6.0, 26.0)
        if npat_cagr_5y and npat_cagr_5y > 0.15 and report_model == "CORPORATE":
            raw_pe = min(28.0, raw_pe * 1.08)
        if cust.get("target_pe"):
            target_pe = cust["target_pe"]
            note_m = " (tùy chỉnh)"
        else:
            target_pe = _clamp(raw_pe * multiple_adj, 5.5, 28.0)
            note_m = f" (nền {raw_pe:.1f}x × vĩ mô {multiple_adj:.2f}x)" if abs(multiple_adj - 1.0) >= 0.02 else ""
        weight = 0.20 if report_model in ("BANK", "SECURITIES") else 0.35
        models.append({
            "method": "P/E Tương đối (Chỉnh Vĩ mô)" if (not cust and abs(multiple_adj - 1.0) >= 0.02) else "P/E Tương đối",
            "fair_value": round(target_pe * eps_ttm, 0),
            "weight": weight,
            "formula": f"P/E mục tiêu {target_pe:.1f}x{note_m} × EPS TTM {_fmt_price(eps_ttm)}",
        })

    # 4. EV/EBITDA (Corporate)
    if report_model == "CORPORATE" and shares > 0 and ebitda_val > 0:
        base_ev = benchmarks.get("ev_ebitda", 8.5)
        adj_ev = _clamp(base_ev * (1.0 if cust else multiple_adj), 4.5, 16.0)
        fair_eq = adj_ev * ebitda_val - tot_debt + cash_st
        if fair_eq > 0:
            note_m = f" (nền {base_ev:.1f}x × vĩ mô {multiple_adj:.2f}x)" if (not cust and abs(multiple_adj - 1.0) >= 0.02) else ""
            models.append({
                "method": "EV / EBITDA Ngành",
                "fair_value": round(fair_eq / shares, 0),
                "weight": 0.25,
                "formula": f"EV/EBITDA {adj_ev:.1f}x{note_m} × EBITDA {_fmt_bn(ebitda_val)} + Net Cash {_fmt_bn(cash_st - tot_debt)}",
            })

    # 5. 2-Stage DCF (Corporate)
    if report_model == "CORPORATE" and shares > 0:
        base_cf = fcf_ttm if fcf_ttm > 0.35 * max(npat_ttm, 0) else (0.70 * npat_ttm if npat_ttm > 0 else 0)
        if base_cf > 0:
            if cust.get("growth_5y") is not None:
                g_5y = cust["growth_5y"]
            else:
                raw_g = npat_cagr_5y if npat_cagr_5y is not None else 0.08
                g_5y = _clamp(raw_g + growth_shift, 0.03, 0.16)
            w_eff = _clamp(wacc, 0.085, 0.19)
            pv_sum = 0.0
            cf = base_cf
            for yr in range(1, 6):
                cf = cf * (1.0 + g_5y)
                pv_sum += cf / ((1.0 + w_eff) ** yr)
            tv = (cf * (1.0 + g_term)) / max(0.035, w_eff - g_term)
            pv_tv = tv / ((1.0 + w_eff) ** 5)
            excess_cash = max(0.0, (cash_st - tot_debt) * 0.5)
            dcf_ps = (pv_sum + pv_tv + excess_cash) / shares
            models.append({
                "method": "DCF 2 Giai đoạn (WACC Động)",
                "fair_value": round(dcf_ps, 0),
                "weight": 0.25,
                "formula": f"CF nền {_fmt_bn(base_cf)}, g(5Y)={_pct(g_5y,1)}, WACC={_pct(w_eff,1)}, g_term={_pct(g_term,1)}",
            })

    valid = [m for m in models if m["fair_value"] > 0]
    if valid:
        tot_w = sum(m["weight"] for m in valid)
        fv = sum(m["fair_value"] * m["weight"] for m in valid) / tot_w
        lo = min(m["fair_value"] for m in valid) * 0.92
        hi = max(m["fair_value"] for m in valid) * 1.05
    else:
        fv, lo, hi = 0.0, 0.0, 0.0
    return models, fv, lo, hi


def analyze_company_with_macro(
    bundle: dict[str, Any],
    macro_ctx: dict[str, Any] | None = None,
    custom_assumptions: dict[str, float] | None = None,
) -> dict[str, Any]:
    """
    Phân tích hợp nhất BCTC chuyên sâu × Vĩ mô hiện tại.
    Tính song song kết quả BCTC thuần (baseline) và BCTC × Vĩ mô để so sánh minh bạch.
    """
    macro = macro_ctx or extract_macro_context(None)
    cust = custom_assumptions or {}
    sym = bundle["ticker"].upper()
    report_model = bundle.get("report_model", "CORPORATE")
    sector_code = _infer_sector_code(report_model, bundle.get("sector_vi", ""), sym)
    preset = REPRESENTATIVE_UNIVERSE.get(sym, {})
    benchmarks = preset.get("peer_benchmarks") or DEFAULT_SECTOR_BENCHMARKS.get(report_model, DEFAULT_SECTOR_BENCHMARKS["CORPORATE"])

    stmts = bundle.get("statements", {})
    is_y = stmts.get("INCOME_STATEMENT", {}).get("years", [])
    is_q = stmts.get("INCOME_STATEMENT", {}).get("quarters", [])
    bs_y = stmts.get("BALANCE_SHEET", {}).get("years", [])
    bs_q = stmts.get("BALANCE_SHEET", {}).get("quarters", [])
    cf_y = stmts.get("CASH_FLOW", {}).get("years", [])
    cf_q = stmts.get("CASH_FLOW", {}).get("quarters", [])

    stats_y, stats_ttm, latest_stat = _extract_stats_series(bundle.get("statistics_financial", []))
    price_stats = _compute_price_stats(bundle.get("prices", []))

    current_price = price_stats["current"] or _num(bundle.get("current_price"))
    shares = _num(bundle.get("shares_outstanding")) or _num(latest_stat.get("numberOfSharesMktCap"))
    market_cap = (current_price * shares) if (current_price > 0 and shares > 0) else _num(latest_stat.get("marketCap"))

    latest_bs = bs_q[-1] if bs_q else (bs_y[-1] if bs_y else {})
    latest_is_y = is_y[-1] if is_y else {}
    latest_cf_y = cf_y[-1] if cf_y else {}

    equity = _num(latest_bs.get("bsa78"))
    total_assets = _num(latest_bs.get("bsa53"))
    bvps = _safe_div(equity, shares, 0.0) or 0.0

    npat_ttm = _sum_ttm(is_q, "isa22") or _sum_ttm(is_q, "isa20") or _num(latest_is_y.get("isa22")) or _num(latest_is_y.get("isa20"))
    eps_ttm = _safe_div(npat_ttm, shares, 0.0) or 0.0

    pe_now = _safe_div(current_price, eps_ttm) if (current_price > 0 and eps_ttm > 0) else (_num(latest_stat.get("pe")) or None)
    pb_now = _safe_div(current_price, bvps) if (current_price > 0 and bvps > 0) else (_num(latest_stat.get("pb")) or None)
    roe_ttm = _num(latest_stat.get("roe"))
    roa_ttm = _num(latest_stat.get("roa"))
    if roe_ttm == 0 and equity > 0 and npat_ttm != 0:
        roe_ttm = npat_ttm / equity

    rev_field = "isb38" if report_model == "BANK" else ("isa1" if report_model == "SECURITIES" else "isa3")
    yearly_series = []
    for row in is_y:
        yr = row.get("yearReport")
        bs_m = next((b for b in bs_y if b.get("yearReport") == yr), {})
        cf_m = next((c for c in cf_y if c.get("yearReport") == yr), {})
        st_m = next((s for s in stats_y if int(s.get("yearReport") or s.get("year") or 0) == yr), {})
        rev = _num(row.get(rev_field)) or _num(row.get("isa3")) or _num(row.get("isa1"))
        npat = _num(row.get("isa22")) or _num(row.get("isa20"))
        gp = _num(row.get("isa5"))
        ocf = _num(cf_m.get("cfa18"))
        capex = abs(_num(cf_m.get("cfa19")))
        eq_y = _num(bs_m.get("bsa78"))
        ta_y = _num(bs_m.get("bsa53"))
        yearly_series.append({
            "period": str(yr),
            "year": yr,
            "revenue": round(rev / 1e9, 2),
            "npat": round(npat / 1e9, 2),
            "ocf": round(ocf / 1e9, 2),
            "capex": round(capex / 1e9, 2),
            "fcf": round((ocf - capex) / 1e9, 2),
            "roe": round(_num(st_m.get("roe")) * 100, 2) if st_m.get("roe") else (round((npat / eq_y) * 100, 2) if eq_y > 0 else None),
            "gross_margin": round(_num(st_m.get("grossMargin")) * 100, 2) if st_m.get("grossMargin") else (round((gp / rev) * 100, 2) if rev > 0 and gp > 0 else None),
            "net_margin": round((npat / rev) * 100, 2) if rev > 0 else None,
            "nim": round(_num(st_m.get("netInterestMargin")) * 100, 2) if st_m.get("netInterestMargin") else None,
            "casa": round(abs(_num(st_m.get("casaRatio"))) * 100, 2) if st_m.get("casaRatio") else None,
            "cir": round(abs(_num(st_m.get("cir") or st_m.get("costToIncome"))) * 100, 2) if (st_m.get("cir") or st_m.get("costToIncome")) else None,
            "npl": round(abs(_num(st_m.get("npl"))) * 100, 2) if st_m.get("npl") else None,
            "llr": round(abs(_num(st_m.get("loansLossReservesToNPLs"))) * 100, 2) if st_m.get("loansLossReservesToNPLs") else None,
        })

    quarterly_series = []
    for row in is_q[-12:]:
        yr, qtr = row.get("yearReport"), row.get("lengthReport")
        rev = _num(row.get(rev_field)) or _num(row.get("isa3")) or _num(row.get("isa1"))
        npat = _num(row.get("isa22")) or _num(row.get("isa20"))
        gp = _num(row.get("isa5"))
        st_m = next((s for s in stats_ttm if int(s.get("yearReport") or s.get("year") or 0) == yr and int(s.get("quarter") or 0) == qtr), {})
        quarterly_series.append({
            "period": f"Q{qtr}/{yr}",
            "revenue": round(rev / 1e9, 2),
            "npat": round(npat / 1e9, 2),
            "gross_margin": round((gp / rev) * 100, 2) if rev > 0 and gp > 0 else None,
            "net_margin": round((npat / rev) * 100, 2) if rev > 0 else None,
            "roe_ttm": round(_num(st_m.get("roe")) * 100, 2) if st_m.get("roe") else None,
            "nim": round(_num(st_m.get("netInterestMargin")) * 100, 2) if st_m.get("netInterestMargin") else None,
            "npl": round(abs(_num(st_m.get("npl"))) * 100, 2) if st_m.get("npl") else None,
            "casa": round(abs(_num(st_m.get("casaRatio"))) * 100, 2) if st_m.get("casaRatio") else None,
        })

    rev_cagr_5y = (
        _cagr(yearly_series[-6]["revenue"], yearly_series[-1]["revenue"], 5) if len(yearly_series) >= 6
        else (_cagr(yearly_series[0]["revenue"], yearly_series[-1]["revenue"], len(yearly_series) - 1) if len(yearly_series) >= 2 else None)
    )
    npat_cagr_5y = (
        _cagr(yearly_series[-6]["npat"], yearly_series[-1]["npat"], 5) if len(yearly_series) >= 6
        else (_cagr(yearly_series[0]["npat"], yearly_series[-1]["npat"], len(yearly_series) - 1) if len(yearly_series) >= 2 else None)
    )

    rev_yoy_q, npat_yoy_q = None, None
    if len(is_q) >= 5:
        q_now, q_prev = is_q[-1], is_q[-5]
        r_now = _num(q_now.get(rev_field)) or _num(q_now.get("isa3")) or _num(q_now.get("isa1"))
        r_prev = _num(q_prev.get(rev_field)) or _num(q_prev.get("isa3")) or _num(q_prev.get("isa1"))
        p_now = _num(q_now.get("isa22")) or _num(q_now.get("isa20"))
        p_prev = _num(q_prev.get("isa22")) or _num(q_prev.get("isa20"))
        rev_yoy_q = _safe_div(r_now - r_prev, abs(r_prev)) if abs(r_prev) > 0 else None
        npat_yoy_q = _safe_div(p_now - p_prev, abs(p_prev)) if abs(p_prev) > 0 else None

    rev_ttm = _sum_ttm(is_q, rev_field) or _sum_ttm(is_q, "isa3") or _sum_ttm(is_q, "isa1")
    ocf_ttm = _sum_ttm(cf_q, "cfa18") or _num(latest_cf_y.get("cfa18"))
    capex_ttm = abs(_sum_ttm(cf_q, "cfa19") or _num(latest_cf_y.get("cfa19")))
    fcf_ttm = ocf_ttm - capex_ttm
    div_paid_ttm = abs(_sum_ttm(cf_q, "cfa32") or _num(latest_cf_y.get("cfa32")))
    div_yield_calc = _safe_div(div_paid_ttm, market_cap, 0.0) or _num(latest_stat.get("dividendYield"))

    # ── Sector-Specific Forensic Metrics ──
    sector_metrics: dict[str, Any] = {}
    red_flags: list[dict[str, str]] = []
    strengths: list[str] = []
    piotroski: dict[str, Any] = {"score": None, "max": 9, "label": "Không áp dụng cho ngành Tài chính", "details": []}
    altman: dict[str, Any] = {"score": None, "zone": "Không áp dụng cho ngành Tài chính"}
    dupont: dict[str, Any] = {}
    bctc_facts: dict[str, Any] = {"dividend_yield": div_yield_calc}

    cash_st = _num(latest_bs.get("bsa2")) + _num(latest_bs.get("bsa5"))
    tot_debt = _num(latest_bs.get("bsa56")) + _num(latest_bs.get("bsa71"))
    ebitda_ttm = _num(latest_stat.get("ebitda")) or (_sum_ttm(is_q, "isa16") + abs(_sum_ttm(is_q, "isa8")) + abs(_sum_ttm(cf_q, "cfa2")))

    if report_model == "BANK":
        nim = _num(latest_stat.get("netInterestMargin"))
        casa = abs(_num(latest_stat.get("casaRatio")))
        cir = abs(_num(latest_stat.get("cir") or latest_stat.get("costToIncome")))
        npl = abs(_num(latest_stat.get("npl")))
        llr = abs(_num(latest_stat.get("loansLossReservesToNPLs")))
        ldr = abs(_num(latest_stat.get("ldrLoanDepositRatio")))
        eq_to_ta = _safe_div(equity, total_assets, 0.0) or 0.0
        nii_ttm = _sum_ttm(is_q, "isb27")
        toi_ttm = _sum_ttm(is_q, "isb38") or rev_ttm
        prov_ttm = abs(_sum_ttm(is_q, "isb41"))
        ppop_ttm = _sum_ttm(is_q, "isb40")
        non_ii_ratio = _safe_div(toi_ttm - nii_ttm, toi_ttm, 0.0) or 0.0
        credit_cost_ppop = _safe_div(prov_ttm, ppop_ttm, 0.0) or 0.0
        bctc_facts.update({"casa": casa, "llr": llr, "npl": npl, "nim": nim})

        sector_metrics = {
            "model": "BANK (Thông tư 49 · CAMELS)",
            "cards": [
                {"label": "NIM (Biên lãi thuần)", "value": _pct(nim, 2), "status": "good" if nim >= 0.032 else ("warn" if nim >= 0.025 else "bad")},
                {"label": "CASA (Tiền gửi KKH)", "value": _pct(casa, 1), "status": "good" if casa >= 0.28 else ("warn" if casa >= 0.18 else "bad")},
                {"label": "NPL (Tỷ lệ nợ xấu)", "value": _pct(npl, 2), "status": "good" if npl <= 0.015 else ("warn" if npl <= 0.025 else "bad")},
                {"label": "LLR (Bao phủ nợ xấu)", "value": _pct(llr, 1), "status": "good" if llr >= 1.20 else ("warn" if llr >= 0.75 else "bad")},
                {"label": "CIR (Chi phí / Thu nhập)", "value": _pct(cir, 1), "status": "good" if cir <= 0.35 else ("warn" if cir <= 0.45 else "bad")},
                {"label": "LDR (Cho vay / Huy động)", "value": _pct(ldr, 1), "status": "good" if ldr <= 0.85 else ("warn" if ldr <= 1.0 else "bad")},
                {"label": "Thu nhập ngoài lãi / TOI", "value": _pct(non_ii_ratio, 1), "status": "good" if non_ii_ratio >= 0.22 else "neutral"},
                {"label": "Dự phòng / LN trước DP", "value": _pct(credit_cost_ppop, 1), "status": "good" if credit_cost_ppop <= 0.25 else ("warn" if credit_cost_ppop <= 0.45 else "bad")},
            ],
            "extra": {"equity_to_assets": eq_to_ta},
        }
        if npl <= 0.013 and llr >= 1.50:
            strengths.append(f"Chất lượng tài sản hạng nhất: NPL chỉ {_pct(npl,2)}, bao phủ nợ xấu LLR đạt {_pct(llr,0)}.")
        if casa >= 0.30:
            strengths.append(f"Lợi thế chi phí vốn vượt trội nhờ CASA đạt {_pct(casa,1)}, bảo vệ biên NIM.")
        if cir <= 0.34:
            strengths.append(f"Vận hành tinh gọn với CIR chỉ {_pct(cir,1)}.")
        if roe_ttm >= 0.17:
            strengths.append(f"ROE duy trì cao bền vững ở mức {_pct(roe_ttm,1)}.")
        if npl > 0.025:
            red_flags.append({"severity": "high", "source": "bctc", "title": "Nợ xấu (NPL) vượt ngưỡng cảnh báo", "detail": f"NPL đạt {_pct(npl,2)} (> 2.5%), gây áp lực trích lập."})
        if llr < 0.75 and npl > 0:
            red_flags.append({"severity": "high", "source": "bctc", "title": "Bộ đệm dự phòng LLR mỏng", "detail": f"LLR chỉ {_pct(llr,0)} (< 75%)."})
        if nim < 0.026 and nim > 0:
            red_flags.append({"severity": "medium", "source": "bctc", "title": "NIM thu hẹp", "detail": f"NIM ở mức {_pct(nim,2)}, chịu cạnh tranh lãi suất cho vay."})
        if eq_to_ta < 0.07:
            red_flags.append({"severity": "medium", "source": "bctc", "title": "VCSH / Tổng tài sản mỏng", "detail": f"VCSH/TTS chỉ {_pct(eq_to_ta,1)}."})

    elif report_model == "SECURITIES":
        margin_loans = _num(latest_bs.get("bss215"))
        fvtpl = _num(latest_bs.get("bsa6"))
        margin_to_eq = _safe_div(margin_loans, equity, 0.0) or 0.0
        fvtpl_to_eq = _safe_div(fvtpl, equity, 0.0) or 0.0
        fvtpl_to_ta = _safe_div(fvtpl, total_assets, 0.0) or 0.0
        debt_to_eq = _safe_div(tot_debt, equity, 0.0) or 0.0
        fvtpl_net = _sum_ttm(is_q, "iss115") - abs(_sum_ttm(is_q, "iss124"))
        margin_inc = _sum_ttm(is_q, "iss120")
        brok_rev = _sum_ttm(is_q, "iss42")
        brok_net = brok_rev - abs(_sum_ttm(is_q, "iss133"))
        brok_margin = _safe_div(brok_net, brok_rev, 0.0) or 0.0
        realized_pbt = _sum_ttm(is_q, "iss153")
        unrealized_pbt = _sum_ttm(is_q, "iss154")
        pbt_ttm = _sum_ttm(is_q, "isa16")
        unrealized_share = _safe_div(abs(unrealized_pbt), abs(pbt_ttm), 0.0) or 0.0
        op_sales = _sum_ttm(is_q, "isa1") or 1.0
        bctc_facts.update({"margin_to_eq": margin_to_eq, "fvtpl_to_eq": fvtpl_to_eq, "debt_to_eq": debt_to_eq})

        sector_metrics = {
            "model": "SECURITIES (Thông tư 210 · CTCK)",
            "cards": [
                {"label": "Dư nợ Margin / VCSH (Trần 200%)", "value": _pct(margin_to_eq, 1), "status": "good" if margin_to_eq <= 1.4 else ("warn" if margin_to_eq <= 1.75 else "bad")},
                {"label": "Danh mục FVTPL / Tổng TS", "value": _pct(fvtpl_to_ta, 1), "status": "good" if fvtpl_to_ta <= 0.45 else ("warn" if fvtpl_to_ta <= 0.60 else "bad")},
                {"label": "Biên gộp Môi giới", "value": _pct(brok_margin, 1), "status": "good" if brok_margin >= 0.25 else ("warn" if brok_margin >= 0.10 else "bad")},
                {"label": "Tỷ trọng DT Margin", "value": _pct(margin_inc / op_sales, 1), "status": "good" if margin_inc / op_sales >= 0.25 else "neutral"},
                {"label": "Tỷ trọng DT Môi giới", "value": _pct(brok_rev / op_sales, 1), "status": "neutral"},
                {"label": "Lãi thuần Tự doanh (TTM)", "value": _fmt_bn(fvtpl_net), "status": "good" if fvtpl_net > 0 else "bad"},
                {"label": "LN Chưa thực hiện / LNTT", "value": _pct(unrealized_share, 1), "status": "good" if unrealized_share <= 0.15 else ("warn" if unrealized_share <= 0.30 else "bad")},
                {"label": "Nợ vay / VCSH", "value": f"{debt_to_eq:.2f}x", "status": "good" if debt_to_eq <= 1.5 else ("warn" if debt_to_eq <= 2.2 else "bad")},
            ],
            "extra": {"margin_loans_bn": round(margin_loans / 1e9, 1), "fvtpl_bn": round(fvtpl / 1e9, 1)},
        }
        if margin_to_eq < 1.5:
            strengths.append(f"Dư địa cho vay Margin dồi dào (Margin/VCSH = {_pct(margin_to_eq,1)} so với trần 200%).")
        if margin_inc / op_sales >= 0.25:
            strengths.append(f"Thu lãi Margin ổn định, chiếm {_pct(margin_inc/op_sales,1)} doanh thu ({_fmt_bn(margin_inc)}).")
        if unrealized_share < 0.15:
            strengths.append(f"Chất lượng lợi nhuận thực chất: chủ yếu là lãi đã thực hiện ({_fmt_bn(realized_pbt)}).")
        if margin_to_eq > 1.75:
            red_flags.append({"severity": "high", "source": "bctc", "title": "Margin/VCSH sát trần 200%", "detail": f"Đạt {_pct(margin_to_eq,1)}, cần tăng vốn để mở rộng dư nợ."})
        if fvtpl_to_eq > 1.3:
            red_flags.append({"severity": "medium", "source": "bctc", "title": "Danh mục Tự doanh (FVTPL) lớn", "detail": f"FVTPL đạt {_fmt_bn(fvtpl)} ({fvtpl_to_eq:.2f}x VCSH)."})
        if unrealized_share > 0.30:
            red_flags.append({"severity": "medium", "source": "bctc", "title": "Tỷ trọng lãi chưa thực hiện cao", "detail": f"Chiếm {_pct(unrealized_share,1)} LNTT, nhạy biến động thị trường."})
        if roe_ttm < 0.12:
            red_flags.append({"severity": "low", "source": "bctc", "title": "ROE pha loãng sau tăng vốn", "detail": f"ROE TTM đạt {_pct(roe_ttm,1)}."})

    else:  # CORPORATE
        piotroski = _compute_piotroski_f_score(is_y, bs_y, cf_y)
        altman = _compute_altman_z_score(latest_is_y, latest_bs, market_cap)
        net_cash = cash_st - tot_debt
        net_cash_mcap = _safe_div(net_cash, market_cap, 0.0) or 0.0
        debt_to_eq = _safe_div(tot_debt, equity, 0.0) or 0.0
        net_debt_to_eq = _safe_div(-net_cash, equity, 0.0) or 0.0
        receivables = _num(latest_bs.get("bsa8"))
        wip_lt = _num(latest_bs.get("bsa163")) or _num(latest_bs.get("bsa188"))
        adv_cust = _num(latest_bs.get("bsa58")) + _num(latest_bs.get("bsa170"))
        gp_ttm = _sum_ttm(is_q, "isa5")
        gross_margin = _safe_div(gp_ttm, rev_ttm, 0.0) or _num(latest_stat.get("grossMargin"))
        net_margin = _safe_div(npat_ttm, rev_ttm, 0.0) or _num(latest_stat.get("afterTaxProfitMargin"))
        ebit_ttm = _num(latest_stat.get("ebit")) or (_sum_ttm(is_q, "isa16") + abs(_sum_ttm(is_q, "isa8")))
        int_exp = abs(_sum_ttm(is_q, "isa8"))
        int_cov = _safe_div(ebit_ttm, int_exp, 99.0) if int_exp > 0 else 99.0
        ocf_to_npat = _safe_div(ocf_ttm, npat_ttm) if npat_ttm > 0 else None
        ev = market_cap + tot_debt - cash_st
        ev_ebitda = _safe_div(ev, ebitda_ttm) if ebitda_ttm > 0 else (_num(latest_stat.get("evToEbitda")) or None)
        roic = _num(latest_stat.get("roic"))
        cur_ratio = _safe_div(_num(latest_bs.get("bsa1")), _num(latest_bs.get("bsa55")), 0.0) or _num(latest_stat.get("currentRatio"))
        asset_turnover = _safe_div(rev_ttm, total_assets, 0.0) or 0.0
        fin_lev = _safe_div(total_assets, equity, 0.0) or 0.0
        dupont = {
            "net_margin": round(net_margin * 100, 2),
            "asset_turnover": round(asset_turnover, 2),
            "financial_leverage": round(fin_lev, 2),
            "roe_dupont": round(net_margin * asset_turnover * fin_lev * 100, 2),
        }
        bctc_facts.update({
            "net_cash_to_mcap": net_cash_mcap,
            "debt_to_eq": debt_to_eq,
            "interest_cov": int_cov,
            "wip_to_assets": _safe_div(wip_lt, total_assets, 0.0) or 0.0,
            "gross_margin": gross_margin,
        })

        sector_metrics = {
            "model": f"CORPORATE (Thông tư 200 · {sector_code})",
            "cards": [
                {"label": "Biên lợi nhuận gộp (TTM)", "value": _pct(gross_margin, 1), "status": "good" if gross_margin >= benchmarks.get("gross_margin", 0.18) else ("warn" if gross_margin >= 0.10 else "bad")},
                {"label": "ROIC (Sinh lời vốn ĐT)", "value": _pct(roic, 1), "status": "good" if roic >= 0.12 else ("warn" if roic >= 0.07 else "bad")},
                {"label": "Chất lượng tiền (OCF/LNST)", "value": f"{ocf_to_npat:.2f}x" if ocf_to_npat is not None else "N/A", "status": "good" if (ocf_to_npat or 0) >= 0.95 else ("warn" if (ocf_to_npat or 0) >= 0.65 else "bad")},
                {"label": "Tiền mặt ròng (Net Cash)", "value": _fmt_bn(net_cash), "status": "good" if net_cash > 0 else ("warn" if net_debt_to_eq < 0.7 else "bad")},
                {"label": "Nợ vay / VCSH (D/E)", "value": f"{debt_to_eq:.2f}x", "status": "good" if debt_to_eq <= 0.5 else ("warn" if debt_to_eq <= 1.0 else "bad")},
                {"label": "EV / EBITDA", "value": f"{ev_ebitda:.1f}x" if ev_ebitda else "N/A", "status": "good" if (ev_ebitda and ev_ebitda <= benchmarks.get("ev_ebitda", 8.5)) else ("warn" if (ev_ebitda and ev_ebitda <= 13) else "bad")},
                {"label": "Trả lãi (EBIT / Lãi vay)", "value": f"{int_cov:.1f}x" if int_cov < 90 else ">90x (Sạch lãi)", "status": "good" if int_cov >= 5.0 else ("warn" if int_cov >= 2.5 else "bad")},
                {"label": "Piotroski F / Altman Z", "value": f"F: {piotroski['score']}/9 · Z: {altman['score']}", "status": "good" if (piotroski["score"] or 0) >= 6 else ("warn" if (piotroski["score"] or 0) >= 4 else "bad")},
            ],
            "extra": {
                "net_cash_bn": round(net_cash / 1e9, 1),
                "wip_lt_bn": round(wip_lt / 1e9, 1),
                "ccc": _num(latest_stat.get("cashCycle")),
                "dio": _num(latest_stat.get("daysInventoryOutstanding")),
            },
        }
        if net_cash > 0 and net_cash_mcap >= 0.20:
            strengths.append(f"Bảng cân đối cực sạch: Tiền mặt ròng {_fmt_bn(net_cash)} ({_pct(net_cash_mcap,1)} vốn hóa).")
        elif net_cash > 0:
            strengths.append(f"Tiền mặt vượt tổng nợ vay ({_fmt_bn(net_cash)} tiền ròng).")
        if ocf_to_npat is not None and ocf_to_npat >= 1.0:
            strengths.append(f"Lợi nhuận tiền thật: OCF đạt {_fmt_bn(ocf_ttm)} ({ocf_to_npat:.2f}x LNST).")
        if (piotroski["score"] or 0) >= 7:
            strengths.append(f"Piotroski F-Score xuất sắc ({piotroski['score']}/9 điểm).")
        if wip_lt > 0 and total_assets > 0 and (wip_lt / total_assets) >= 0.15:
            strengths.append(f"Động lực tăng công suất lớn: Tài sản dở dang dài hạn {_fmt_bn(wip_lt)} ({_pct(wip_lt/total_assets,1)} TTS).")
        if adv_cust > 0 and sector_code == "REAL_ESTATE":
            strengths.append(f"Của để dành BĐS (Người mua trả trước): {_fmt_bn(adv_cust)}.")

        if ocf_to_npat is not None and ocf_to_npat < 0.65 and npat_ttm > 0:
            red_flags.append({"severity": "high", "source": "bctc", "title": "OCF/LNST thấp (tiền kẹt phải thu/HTK)", "detail": f"OCF/LNST chỉ {ocf_to_npat:.2f}x."})
        if debt_to_eq > 1.0:
            red_flags.append({"severity": "high", "source": "bctc", "title": "Đòn bẩy nợ vay cao (D/E > 1.0x)", "detail": f"D/E = {debt_to_eq:.2f}x ({_fmt_bn(tot_debt)})."})
        elif debt_to_eq > 0.65 and fcf_ttm < 0:
            red_flags.append({"severity": "medium", "source": "bctc", "title": "Chu kỳ CAPEX nặng (FCF âm)", "detail": f"FCF TTM {_fmt_bn(fcf_ttm)}, D/E {debt_to_eq:.2f}x."})
        if int_cov < 3.0 and tot_debt > 0:
            red_flags.append({"severity": "high", "source": "bctc", "title": "Áp lực lãi vay lớn", "detail": f"EBIT/Lãi vay chỉ {int_cov:.1f}x."})
        if 0 < cur_ratio < 1.0:
            red_flags.append({"severity": "medium", "source": "bctc", "title": "Thanh khoản ngắn hạn chặt", "detail": f"Current Ratio = {cur_ratio:.2f}x (< 1.0x)."})
        if altman["score"] is not None and altman["score"] < 1.81:
            red_flags.append({"severity": "high", "source": "bctc", "title": "Altman Z-Score vùng rủi ro", "detail": f"Z-Score = {altman['score']} (< 1.81)."})
        if rev_ttm > 0 and (receivables / rev_ttm) > 0.35 and sector_code != "REAL_ESTATE":
            red_flags.append({"severity": "medium", "source": "bctc", "title": "Phải thu chiếm tỷ trọng cao", "detail": f"Phải thu = {_pct(receivables/rev_ttm,1)} doanh thu TTM."})

    # ── Dual Valuation: Pure Static BCTC vs Macro-Adjusted Dynamic Valuation ──
    pe_bands = _historical_valuation_bands(stats_ttm or stats_y, "pe")
    pb_bands = _historical_valuation_bands(stats_ttm or stats_y, "pb")
    val_overlay = _compute_macro_valuation_overlay(macro, sector_code, report_model)

    # A) Pure Static BCTC baseline (for transparent before/after comparison)
    pure_models, pure_fv, _, _ = _run_valuation_models(
        report_model=report_model, bvps=bvps, eps_ttm=eps_ttm, roe_ttm=roe_ttm,
        npat_ttm=npat_ttm, fcf_ttm=fcf_ttm, npat_cagr_5y=npat_cagr_5y, shares=shares,
        ebitda_val=ebitda_ttm, cash_st=cash_st, tot_debt=tot_debt,
        pe_bands=pe_bands, pb_bands=pb_bands, benchmarks=benchmarks,
        ke=val_overlay["base_ke"], wacc=val_overlay["base_ke"], g_term=MACRO_DEFAULTS["terminal_growth"],
        multiple_adj=1.0, growth_shift=0.0, custom_assumptions=None,
    )
    if pure_fv <= 0:
        pure_fv = current_price
    pure_upside = _safe_div(pure_fv - current_price, current_price, 0.0) or 0.0

    # B) Macro-Adjusted Valuation (or user What-If overrides)
    eff_ke = cust.get("wacc") or val_overlay["live_ke"]
    eff_wacc = cust.get("wacc") or val_overlay["live_ke"]
    eff_g_term = cust.get("terminal_growth", MACRO_DEFAULTS["terminal_growth"])
    val_models, fair_value, fair_low, fair_high = _run_valuation_models(
        report_model=report_model, bvps=bvps, eps_ttm=eps_ttm, roe_ttm=roe_ttm,
        npat_ttm=npat_ttm, fcf_ttm=fcf_ttm, npat_cagr_5y=npat_cagr_5y, shares=shares,
        ebitda_val=ebitda_ttm, cash_st=cash_st, tot_debt=tot_debt,
        pe_bands=pe_bands, pb_bands=pb_bands, benchmarks=benchmarks,
        ke=eff_ke, wacc=eff_wacc, g_term=eff_g_term,
        multiple_adj=val_overlay["multiple_adj"],
        growth_shift=val_overlay["growth_shift"],
        custom_assumptions=cust or None,
    )
    if fair_value <= 0:
        fair_value, fair_low, fair_high = current_price, current_price * 0.85, current_price * 1.15

    upside = _safe_div(fair_value - current_price, current_price, 0.0) or 0.0
    margin_of_safety = _safe_div(fair_value - current_price, fair_value, 0.0) or 0.0

    # ── 5 BCTC Pillars + 6th Macro Fit Pillar ──
    if report_model == "BANK":
        nim_v = _num(latest_stat.get("netInterestMargin"))
        cir_v = abs(_num(latest_stat.get("cir") or latest_stat.get("costToIncome")))
        score_prof = _clamp((roe_ttm / 0.20) * 55 + (nim_v / 0.035) * 25 + (max(0, 0.55 - cir_v) / 0.25) * 20, 15, 98)
        npl_v = abs(_num(latest_stat.get("npl")))
        llr_v = abs(_num(latest_stat.get("loansLossReservesToNPLs")))
        casa_v = abs(_num(latest_stat.get("casaRatio")))
        s_npl = 40 if npl_v <= 0.012 else (32 if npl_v <= 0.018 else (20 if npl_v <= 0.025 else 8))
        score_health = _clamp(s_npl + _clamp((llr_v / 1.50) * 35, 5, 35) + _clamp((casa_v / 0.30) * 25, 5, 25), 15, 98)
        nii_q, toi_q = _sum_ttm(is_q, "isb27"), (_sum_ttm(is_q, "isb38") or 1.0)
        score_quality = _clamp(55 + ((toi_q - nii_q) / toi_q) * 90 + (15 if llr_v >= 1.0 else 0), 30, 96)
    elif report_model == "SECURITIES":
        score_prof = _clamp((roe_ttm / 0.16) * 65 + (roa_ttm / 0.06) * 35, 20, 95)
        m_eq = _safe_div(_num(latest_bs.get("bss215")), equity, 1.0) or 1.0
        f_ta = _safe_div(_num(latest_bs.get("bsa6")), total_assets, 0.4) or 0.4
        score_health = _clamp((45 if m_eq <= 1.3 else (32 if m_eq <= 1.65 else 15)) + (35 if f_ta <= 0.45 else (22 if f_ta <= 0.60 else 10)) + 15, 20, 95)
        pbt_q = _sum_ttm(is_q, "isa16") or 1.0
        unr_q = abs(_sum_ttm(is_q, "iss154")) / abs(pbt_q)
        score_quality = _clamp(88 - unr_q * 90, 25, 95)
    else:
        roic_v = _num(latest_stat.get("roic")) or roe_ttm * 0.75
        gm_v = _num(latest_stat.get("grossMargin"))
        score_prof = _clamp((roe_ttm / 0.20) * 45 + (roic_v / 0.15) * 35 + (gm_v / max(0.15, benchmarks.get("gross_margin", 0.2))) * 20, 15, 98)
        f_sc = piotroski["score"] if piotroski["score"] is not None else 5
        d_eq = _safe_div(tot_debt, equity, 0.5) or 0.5
        s_d = 30 if d_eq <= 0.3 else (24 if d_eq <= 0.7 else (14 if d_eq <= 1.1 else 5))
        s_z = 20 if (altman["score"] or 2.5) >= 2.99 else (12 if (altman["score"] or 2.5) >= 1.81 else 4)
        score_health = _clamp((f_sc / 9.0) * 50 + s_d + s_z, 15, 98)
        ocf_r = _safe_div(ocf_ttm, npat_ttm, 0.8) if npat_ttm > 0 else 0.3
        score_quality = _clamp(_clamp((ocf_r or 0) * 55, 10, 65) + (20 if div_yield_calc >= 0.04 else (12 if div_yield_calc >= 0.015 else 6)) + (15 if fcf_ttm > 0 else 5), 15, 96)

    score_growth = _clamp(
        _clamp(((rev_cagr_5y or 0.05) + 0.05) / 0.25 * 50, 5, 50)
        + _clamp(((npat_cagr_5y or 0.05) + 0.05) / 0.25 * 35, 5, 35)
        + _clamp(((npat_yoy_q or 0.0) + 0.15) / 0.45 * 15, 0, 15),
        15, 98,
    )

    score_val_pure = _clamp(55 + pure_upside * 130, 12, 98)
    score_val_macro = _clamp(55 + upside * 130, 12, 98)

    bctc_high_flags = sum(1 for r in red_flags if r["severity"] == "high")
    pure_bctc_score = round(_clamp(
        0.25 * score_prof + 0.20 * score_growth + 0.20 * score_health + 0.15 * score_quality + 0.20 * score_val_pure
        - bctc_high_flags * 4.5, 10, 98
    ), 1)

    # 6th Pillar: Macro Fit Score + Macro Red Flags
    macro_fit_score, macro_impacts, macro_flags = _compute_macro_fit_channels(macro, sector_code, report_model, bctc_facts)
    all_red_flags = red_flags + macro_flags
    macro_high_flags = sum(1 for r in macro_flags if r["severity"] == "high")

    # Fused 6-Pillar Score (80% BCTC với định giá chỉnh vĩ mô + 20% Độ phù hợp Vĩ mô)
    fused_score = round(_clamp(
        0.20 * score_prof
        + 0.16 * score_growth
        + 0.16 * score_health
        + 0.12 * score_quality
        + 0.16 * score_val_macro
        + 0.20 * macro_fit_score
        - bctc_high_flags * 4.0
        - macro_high_flags * 3.0,
        10, 98,
    ), 1)

    # Pure BCTC Verdict (Baseline for comparison)
    if pure_bctc_score >= 76 and pure_upside >= 0.18:
        pure_verdict = "MUA MẠNH (BCTC thuần)"
    elif pure_bctc_score >= 66 and pure_upside >= 0.08:
        pure_verdict = "TÍCH LŨY (BCTC thuần)"
    elif pure_bctc_score >= 65:
        pure_verdict = "THEO DÕI (BCTC thuần)"
    elif pure_bctc_score >= 52 and pure_upside >= 0.15:
        pure_verdict = "ĐẦU CƠ GIÁ TRỊ (BCTC thuần)"
    else:
        pure_verdict = "QUAN SÁT / TRÁNH (BCTC thuần)"

    # ── Disciplined Macro-Gated Fused Verdict ──
    m_sc = macro["macro_score"]
    eq_budget = macro["equity_budget_pct"]
    cautious_macro = m_sc < 52
    risk_off_macro = m_sc < 40

    if not cautious_macro:
        # Risk-On / Selective Macro
        if fused_score >= 76 and upside >= 0.15 and macro_fit_score >= 60:
            verdict, verdict_code = "MUA MẠNH · THUẬN VĨ MÔ (STRONG BUY)", "STRONG_BUY"
            should_invest = "NÊN ĐẦU TƯ MẠNH — Hội tụ cả BCTC xuất sắc, biên an toàn tốt và vĩ mô hậu thuẫn trực tiếp."
            alloc_range = f"12% – 20% danh mục (trong ngân sách CP {eq_budget}%)"
        elif fused_score >= 66 and upside >= 0.08 and macro_fit_score >= 50:
            verdict, verdict_code = "TÍCH LŨY / MUA (BUY)", "BUY"
            should_invest = "NÊN ĐẦU TƯ — Nền tảng tài chính tốt, định giá chỉnh vĩ mô vẫn còn dư địa tăng."
            alloc_range = f"8% – 14% danh mục (trong ngân sách CP {eq_budget}%)"
        elif fused_score >= 64 and upside < 0.08:
            verdict, verdict_code = "NẮM GIỮ · CHỜ CHIẾT KHẤU (HOLD)", "HOLD"
            should_invest = "DOANH NGHIỆP TỐT NHƯNG GIÁ ĐÃ GẦN FAIR VALUE — Chờ rung lắc kỹ thuật để giải ngân."
            alloc_range = "5% – 8% danh mục"
        elif fused_score >= 54 and upside >= 0.15:
            verdict, verdict_code = "MUA CHỌN LỌC CHU KỲ (ACCUMULATE)", "ACCUMULATE"
            should_invest = "CHỌN LỌC TỪNG PHẦN — Định giá hấp dẫn nhưng vĩ mô/chất lượng cần theo dõi sát."
            alloc_range = "4% – 8% danh mục"
        else:
            verdict, verdict_code = "QUAN SÁT / THẬN TRỌNG (WATCH)", "WATCH"
            should_invest = "CHƯA NÊN VỘI MUA — Cần chờ thêm tín hiệu xác nhận từ cả BCTC lẫn vĩ mô."
            alloc_range = "0% – 3% danh mục"
    else:
        # Cautious / Risk-Off Macro (như hiện tại: Điểm Vĩ mô < 52, VIX cao, Fed diều hâu)
        if (not risk_off_macro) and fused_score >= 76 and upside >= 0.22 and macro_fit_score >= 62:
            verdict, verdict_code = "TÍCH SẢN PHÒNG THỦ · ƯU TIÊN SỐ 1 (BUY)", "BUY"
            should_invest = (
                f"ƯU TIÊN GOM TÍCH SẢN TỪNG PHẦN — BCTC vượt trội & hợp pha vĩ mô hiện tại ({macro_fit_score:.0f}/100), "
                f"định giá sau khi cộng thêm chi phí vốn +{val_overlay['ke_shift_bps']}bps vẫn còn upside {upside*100:+.1f}%. "
                "Giải ngân chậm 3 đợt 20–30–50% do La bàn Vĩ mô đang thận trọng."
            )
            alloc_range = f"8% – 12% tài sản (trụ cột trong ngân sách CP {eq_budget}%)"
        elif fused_score >= 66 and upside >= 0.14 and macro_fit_score >= 52:
            verdict, verdict_code = "GOM TỪNG PHẦN THẬN TRỌNG (ACCUMULATE)", "ACCUMULATE"
            should_invest = (
                f"CHỈ GOM RẢI LỆNH KHI RUNG LẮC — Nền tảng BCTC tốt nhưng bối cảnh vĩ mô ({m_sc}/100, VIX {macro['vix']:.1f}) "
                "chưa thuận để mua đuổi. Ưu tiên chia 3 đợt tại các vùng hỗ trợ sâu, tuyệt đối không dùng margin."
            )
            alloc_range = f"5% – 8% tài sản (không margin)"
        elif fused_score >= 58:
            verdict, verdict_code = "THEO DÕI · CHỜ VĨ MÔ DỊU LẠI (HOLD / WAIT)", "HOLD"
            should_invest = (
                f"CHƯA VỘI GIẢI NGÂN MỚI — Dù BCTC cơ bản khá ({pure_bctc_score:.0f}/100), áp lực vĩ mô hiện tại "
                f"(US 10Y {macro['us_10y']:.2f}%, Fed {macro['fed_bias']}) đang nén định giá & sức mua ngành này."
            )
            alloc_range = "0% – 4% tài sản (chỉ giữ vị thế nhỏ)"
        elif fused_score >= 48:
            verdict, verdict_code = "QUAN SÁT · ĐỨNG NGOÀI NGẮN HẠN (WATCH)", "WATCH"
            should_invest = (
                f"NÊN ĐỨNG NGOÀI QUAN SÁT — Ngành/mã này ngược pha vĩ mô hiện tại (Hợp Vĩ mô {macro_fit_score:.0f}/100). "
                "Chỉ quay lại khi VN-Index vượt MA50 hoặc áp lực lãi suất Fed hạ nhiệt."
            )
            alloc_range = "0% danh mục"
        else:
            verdict, verdict_code = "TRÁNH / HẠ TỶ TRỌNG (AVOID)", "AVOID"
            should_invest = (
                f"KHÔNG NÊN ĐẦU TƯ LÚC NÀY — Rủi ro vĩ mô (Hợp Vĩ mô {macro_fit_score:.0f}/100) cộng hưởng với điểm yếu BCTC/định giá."
            )
            alloc_range = "0% danh mục"

    # Build transparent before-vs-after explanation
    fv_diff_pct = _safe_div(fair_value - pure_fv, pure_fv, 0.0) or 0.0
    score_diff = round(fused_score - pure_bctc_score, 1)
    if val_overlay["ke_shift_bps"] > 50:
        adj_reason = (
            f"Vĩ mô hiện tại (US 10Y {macro['us_10y']:.2f}%, Fed {macro['fed_bias']}, VIX {macro['vix']:.1f}) đẩy Chi phí vốn Ke lên "
            f"{eff_ke*100:.1f}% (+{val_overlay['ke_shift_bps']}bps) và nén hệ số định giá {val_overlay['multiple_adj_pct']:+.1f}%, "
            f"đưa Giá trị hợp lý từ {_fmt_price(pure_fv)} về mức thực tế hơn là {_fmt_price(fair_value)} ({fv_diff_pct*100:+.1f}%). "
            f"Điểm Hợp Vĩ mô đạt {macro_fit_score:.0f}/100 → Điểm hợp nhất thay đổi {score_diff:+.1f} điểm."
        )
    elif val_overlay["ke_shift_bps"] < -30:
        adj_reason = (
            f"Vĩ mô thuận lợi hạ Chi phí vốn Ke xuống {eff_ke*100:.1f}% ({val_overlay['ke_shift_bps']}bps), "
            f"mở rộng Fair Value lên {_fmt_price(fair_value)} ({fv_diff_pct*100:+.1f}%). Điểm Hợp Vĩ mô đạt {macro_fit_score:.0f}/100."
        )
    else:
        adj_reason = (
            f"Vĩ mô ở trạng thái cân bằng (Ke {eff_ke*100:.1f}%), Giá trị hợp lý duy trì sát nền BCTC ({_fmt_price(fair_value)}) "
            f"với điểm Hợp Vĩ mô {macro_fit_score:.0f}/100."
        )

    # ── Macro-Aware 3-Tranche Entry Plan ──
    if cautious_macro:
        tranche_weights = [20, 30, 50]
        buy_1 = round(min(current_price * 0.98, fair_value * 0.85), -2) if current_price > 0 else 0
        buy_2 = round(min(current_price * 0.90, fair_value * 0.76), -2) if current_price > 0 else 0
        buy_3 = round(min(current_price * 0.82, fair_value * 0.68), -2) if current_price > 0 else 0
    else:
        tranche_weights = [30, 40, 30]
        buy_1 = round(min(current_price, fair_value * 0.92), -2) if current_price > 0 else 0
        buy_2 = round(min(current_price * 0.93, fair_value * 0.83), -2) if current_price > 0 else 0
        buy_3 = round(min(current_price * 0.86, fair_value * 0.75), -2) if current_price > 0 else 0

    tp_1 = round(fair_value * 1.04, -2) if fair_value > 0 else 0
    tp_2 = round(fair_value * 1.20, -2) if fair_value > 0 else 0
    stop_review = round(min(current_price, buy_1) * 0.86, -2) if current_price > 0 else 0

    tactics = [
        f"Phân bổ giải ngân 3 đợt theo chế độ vĩ mô ({'/'.join(map(str, tranche_weights))}%): Đợt 1 quanh {_fmt_price(buy_1)}, Đợt 2 {_fmt_price(buy_2)}, Đợt 3 chiết khấu sâu {_fmt_price(buy_3)}.",
        f"Cập nhật định giá động: Giá trị hợp lý đã tự động trừ rủi ro lãi suất/VIX (Ke={eff_ke*100:.1f}%), giúp tránh bẫy định giá ảo khi thị trường biến động.",
    ]
    if cautious_macro:
        tactics.append(f"Kỷ luật vĩ mô ({m_sc}/100): Không dùng margin; chỉ giải ngân tăng tốc khi VN-Index lấy lại MA50 hoặc VIX hạ dưới 22.")
    else:
        tactics.append("Giữ vị thế nòng cốt khi ROIC/ROE duy trì trên chi phí vốn và BCTC quý tới không phát sinh Red Flag mới.")

    invalidation = [
        f"Vĩ mô xấu đi đột ngột: US 10Y vượt {max(5.2, round(macro['us_10y'] + 0.4, 1)):.1f}%, DXY vượt 106 hoặc VN-Index thủng sâu MA200 kèm khối ngoại bán ròng kéo dài.",
    ]
    if report_model == "BANK":
        invalidation.append("NPL vượt 2.5% trong 2 quý liên tiếp kèm LLR sụt dưới 80% hoặc NIM giảm mạnh.")
    elif report_model == "SECURITIES":
        invalidation.append("Thanh khoản HOSE kéo dài dưới 10,000 tỷ/phiên hoặc danh mục FVTPL ghi nhận lỗ lớn.")
    else:
        invalidation.append("OCF/LNST rơi dưới 0.6x kéo dài 2 quý hoặc biên gộp thu hẹp mạnh kèm đòn bẩy nợ D/E tăng vọt.")

    latest_period = (
        f"Q{is_q[-1].get('lengthReport')}/{is_q[-1].get('yearReport')}" if is_q
        else (str(is_y[-1].get("yearReport")) if is_y else "N/A")
    )

    return {
        "ticker": sym,
        "company_name": bundle.get("company_name", sym),
        "sector_vi": bundle.get("sector_vi", ""),
        "sector_code": sector_code,
        "hose_sector": SECTOR_TO_HOSE.get(sector_code, "VNIND"),
        "report_model": report_model,
        "exchange": bundle.get("exchange", "HOSE"),
        "updated_at": bundle.get("updated_at", ""),
        "latest_report_period": latest_period,
        "market": {
            "current_price": round(current_price, 0),
            "market_cap_bn": round(market_cap / 1e9, 1),
            "shares_m": round(shares / 1e6, 2),
            "pe": round(pe_now, 2) if pe_now else None,
            "pb": round(pb_now, 2) if pb_now else None,
            "eps_ttm": round(eps_ttm, 0),
            "bvps": round(bvps, 0),
            "roe": round(roe_ttm * 100, 2),
            "roa": round(roa_ttm * 100, 2),
            "dividend_yield": round(div_yield_calc * 100, 2),
            "price_stats": price_stats,
        },
        "growth": {
            "rev_cagr_5y": round(rev_cagr_5y * 100, 2) if rev_cagr_5y is not None else None,
            "npat_cagr_5y": round(npat_cagr_5y * 100, 2) if npat_cagr_5y is not None else None,
            "rev_yoy_latest_q": round(rev_yoy_q * 100, 2) if rev_yoy_q is not None else None,
            "npat_yoy_latest_q": round(npat_yoy_q * 100, 2) if npat_yoy_q is not None else None,
            "rev_ttm_bn": round(rev_ttm / 1e9, 1),
            "npat_ttm_bn": round(npat_ttm / 1e9, 1),
            "ocf_ttm_bn": round(ocf_ttm / 1e9, 1),
            "fcf_ttm_bn": round(fcf_ttm / 1e9, 1),
        },
        "scores": {
            "total": fused_score,
            "pure_bctc": pure_bctc_score,
            "macro_fit": macro_fit_score,
            "profitability": round(score_prof, 1),
            "growth": round(score_growth, 1),
            "health": round(score_health, 1),
            "quality": round(score_quality, 1),
            "valuation": round(score_val_macro, 1),
        },
        "valuation": {
            "fair_value": round(fair_value, 0),
            "fair_low": round(fair_low, 0),
            "fair_high": round(fair_high, 0),
            "upside_pct": round(upside * 100, 1),
            "margin_of_safety_pct": round(margin_of_safety * 100, 1),
            "pure_fair_value": round(pure_fv, 0),
            "pure_upside_pct": round(pure_upside * 100, 1),
            "models": val_models,
            "pe_bands": pe_bands,
            "pb_bands": pb_bands,
            "assumptions": {
                "wacc": eff_wacc,
                "ke": eff_ke,
                "base_ke": val_overlay["base_ke"],
                "terminal_growth": eff_g_term,
                "multiple_adj": val_overlay["multiple_adj"],
            },
        },
        "macro_fusion": {
            "macro_context": {
                "updated_at_vn": macro["updated_at_vn"],
                "macro_score": macro["macro_score"],
                "stance_short": macro["stance_short"],
                "stance_tone": macro["stance_tone"],
                "regime_name": macro["regime_name"],
                "equity_budget_pct": macro["equity_budget_pct"],
                "us_10y": macro["us_10y"],
                "fed_bias": macro["fed_bias"],
                "fed_hike_prob": macro["fed_hike_prob"],
                "fed_cut_prob": macro["fed_cut_prob"],
                "vix": macro["vix"],
                "dxy": macro["dxy"],
                "usdvnd": macro["usdvnd"],
                "vn_pmi": macro["vn_pmi"],
                "us_ism_mfg": macro["us_ism_mfg"],
                "us_core_pce": macro["us_core_pce"],
                "vn_cpi": macro["vn_cpi"],
                "wti": macro["wti"],
                "vni_trend": macro["vni_trend"],
                "foreign_5d_bn": macro["foreign_5d_bn"],
                "hose_liq_bn": macro["hose_liq_bn"],
            },
            "valuation_overlay": val_overlay,
            "impacts": macro_impacts,
            "pure_vs_macro": {
                "pure_score": pure_bctc_score,
                "fused_score": fused_score,
                "score_delta": score_diff,
                "pure_fair_value": round(pure_fv, 0),
                "macro_fair_value": round(fair_value, 0),
                "fv_delta_pct": round(fv_diff_pct * 100, 1),
                "pure_upside_pct": round(pure_upside * 100, 1),
                "macro_upside_pct": round(upside * 100, 1),
                "pure_verdict": pure_verdict,
                "fused_verdict": verdict,
                "adjustment_reason": adj_reason,
            },
        },
        "sector_metrics": sector_metrics,
        "piotroski": piotroski,
        "altman": altman,
        "dupont": dupont,
        "strengths": strengths,
        "red_flags": all_red_flags,
        "strategy": {
            "verdict": verdict,
            "verdict_code": verdict_code,
            "should_invest": should_invest,
            "style": preset.get("style") or sector_code,
            "horizon": "Trung – Dài hạn (12 – 36 tháng)" if report_model != "SECURITIES" else "Theo chu kỳ thanh khoản (6 – 18 tháng)",
            "allocation": alloc_range,
            "tranche_weights": tranche_weights,
            "price_plan": {
                "current_price": round(current_price, 0),
                "fair_value": round(fair_value, 0),
                "buy_tranche_1": buy_1,
                "buy_tranche_2": buy_2,
                "buy_tranche_3": buy_3,
                "take_profit_1": tp_1,
                "take_profit_2": tp_2,
                "review_stop_level": stop_review,
            },
            "tactics": tactics,
            "key_drivers": preset.get("key_drivers") or [
                "Tăng trưởng doanh thu & biên lợi nhuận cốt lõi theo quý",
                "Hiệu quả sử dụng vốn (ROE/ROIC) & chất lượng dòng tiền",
                "Sự đồng pha giữa chu kỳ vĩ mô (lãi suất, PMI, tỷ giá) và định giá",
            ],
            "invalidation_triggers": invalidation,
        },
        "series": {
            "yearly": yearly_series,
            "quarterly": quarterly_series,
            "prices": bundle.get("prices", [])[-260:],
        },
    }


def build_statement_table(bundle: dict[str, Any], section: str = "INCOME_STATEMENT", period: str = "years", limit: int = 8) -> dict[str, Any]:
    metrics = bundle.get("metrics", {}).get(section, [])
    rows = bundle.get("statements", {}).get(section, {}).get(period, [])
    recent = rows[-limit:] if limit > 0 else rows
    columns = [f"{r.get('yearReport')}" if period == "years" else f"Q{r.get('lengthReport')}/{r.get('yearReport')}" for r in recent]

    items = []
    for m in metrics:
        field = m.get("field")
        level = int(m.get("level") or 1)
        title_vi = m.get("titleVi") or ""
        title_en = m.get("titleEn") or ""
        is_eps = "eps" in title_en.lower() or ("cổ phiếu" in title_vi.lower() and "vnd" in title_vi.lower())

        values = []
        has_nonzero = False
        for r in recent:
            v = r.get(field) if field else None
            if v is not None:
                if abs(_num(v)) > 0:
                    has_nonzero = True
                values.append(round(_num(v), 0) if is_eps else round(_num(v) / 1e9, 2))
            else:
                values.append(None)

        yoy = None
        step = 1 if period == "years" else 4
        if len(values) > step and values[-1] is not None and values[-1 - step] not in (None, 0):
            yoy = round(((values[-1] - values[-1 - step]) / abs(values[-1 - step])) * 100, 1)

        if has_nonzero or (level == 1 and not field):
            items.append({
                "field": field or "",
                "level": level,
                "title_vi": title_vi,
                "title_en": title_en,
                "unit": "VND" if is_eps else "Tỷ VND",
                "values": values,
                "yoy_pct": yoy,
            })

    return {"ticker": bundle["ticker"], "section": section, "period": period, "columns": columns, "items": items}


def build_fused_portfolio_strategy(analyses: list[dict[str, Any]], macro_ctx: dict[str, Any]) -> dict[str, Any]:
    """Xếp hạng danh mục hợp nhất BCTC × Vĩ mô & phân bổ tỷ trọng đồng bộ theo ngân sách Cổ phiếu của La bàn Vĩ mô."""
    if not analyses:
        return {"macro_context": macro_ctx, "ranking": [], "allocation_suggestion": [], "summary": "Chưa có dữ liệu BCTC."}

    ranked = sorted(analyses, key=lambda x: (x["scores"]["total"], x["scores"]["macro_fit"], x["valuation"]["upside_pct"]), reverse=True)
    eq_budget = macro_ctx.get("equity_budget_pct", 35)

    weights = []
    for a in ranked:
        sc = a["scores"]["total"]
        mf = a["scores"]["macro_fit"]
        up = a["valuation"]["upside_pct"]
        vc = a["strategy"]["verdict_code"]
        if vc in ("STRONG_BUY", "BUY", "ACCUMULATE") and sc >= 62 and mf >= 48 and up > 0:
            raw_w = max(6.0, (sc - 50) * 0.75 + (mf - 45) * 0.45 + max(0.0, up) * 0.35)
        elif vc == "HOLD" and sc >= 62 and mf >= 50:
            raw_w = 5.0
        else:
            raw_w = 0.0
        weights.append((a, raw_w))

    tot = sum(w for _, w in weights) or 1.0
    alloc = []
    for a, w in weights:
        in_eq_pct = round((w / tot) * 100, 1) if w > 0 else 0.0
        in_nav_pct = round((in_eq_pct / 100.0) * eq_budget, 1) if w > 0 else 0.0
        alloc.append({
            "ticker": a["ticker"],
            "sector_vi": a["sector_vi"],
            "score": a["scores"]["total"],
            "pure_score": a["scores"]["pure_bctc"],
            "macro_fit": a["scores"]["macro_fit"],
            "upside_pct": a["valuation"]["upside_pct"],
            "verdict": a["strategy"]["verdict"],
            "verdict_code": a["strategy"]["verdict_code"],
            "weight_in_equity_pct": in_eq_pct,
            "weight_in_total_nav_pct": in_nav_pct,
            "role": a["strategy"]["style"],
        })

    top_picks = [a["ticker"] for a in ranked if a["strategy"]["verdict_code"] in ("STRONG_BUY", "BUY")][:3]
    avoid_picks = [a["ticker"] for a in ranked if a["strategy"]["verdict_code"] in ("WATCH", "AVOID")][:3]
    summary = (
        f"La bàn Vĩ mô hiện ở mức {macro_ctx['macro_score']}/100 ({macro_ctx['stance_short']}) — "
        f"Ngân sách Cổ phiếu mục tiêu {eq_budget}% tổng tài sản (US 10Y {macro_ctx['us_10y']:.2f}%, VIX {macro_ctx['vix']:.1f}, PMI VN {macro_ctx['vn_pmi']:.1f}). "
        + (f"Ưu tiên giải ngân chọn lọc: {', '.join(top_picks)}. " if top_picks else "Thị trường đang phân hóa mạnh — ưu tiên gom từng phần vào các mã đầu ngành hợp pha vĩ mô. ")
        + (f"Nhóm ngược pha vĩ mô nên đứng ngoài/chờ đợi: {', '.join(avoid_picks)}." if avoid_picks else "")
    )

    return {
        "macro_context": macro_ctx,
        "ranking": [
            {
                "ticker": a["ticker"],
                "company_name": a["company_name"],
                "sector_vi": a["sector_vi"],
                "report_model": a["report_model"],
                "latest_report_period": a["latest_report_period"],
                "current_price": a["market"]["current_price"],
                "pure_fair_value": a["valuation"]["pure_fair_value"],
                "fair_value": a["valuation"]["fair_value"],
                "upside_pct": a["valuation"]["upside_pct"],
                "pure_upside_pct": a["valuation"]["pure_upside_pct"],
                "pe": a["market"]["pe"],
                "pb": a["market"]["pb"],
                "roe": a["market"]["roe"],
                "dividend_yield": a["market"]["dividend_yield"],
                "pure_score": a["scores"]["pure_bctc"],
                "macro_fit": a["scores"]["macro_fit"],
                "score": a["scores"]["total"],
                "red_flags_count": len(a["red_flags"]),
                "macro_flags_count": sum(1 for r in a["red_flags"] if r.get("source") == "macro"),
                "verdict": a["strategy"]["verdict"],
                "verdict_code": a["strategy"]["verdict_code"],
            }
            for a in ranked
        ],
        "allocation_suggestion": alloc,
        "summary": summary,
    }


# =============================================================================
# 4. Buy-Side Top-Down × Bottom-Up Equity Research Memo (Offline + AI API)
# =============================================================================

def generate_fused_offline_memo(analysis: dict[str, Any], question: str | None = None) -> str:
    sym = analysis["ticker"]
    name = analysis["company_name"]
    m = analysis["market"]
    g = analysis["growth"]
    sc = analysis["scores"]
    val = analysis["valuation"]
    st = analysis["strategy"]
    sm = analysis["sector_metrics"]
    pp = st["price_plan"]
    mf = analysis["macro_fusion"]
    mc = mf["macro_context"]
    pvm = mf["pure_vs_macro"]
    ov = mf["valuation_overlay"]

    if question and question.strip():
        q = question.strip().lower()
        lines = [f"### Trả lời nhanh BCTC × Vĩ mô cho **{sym}** ({analysis['latest_report_period']})\n"]
        if any(k in q for k in ("vĩ mô", "lãi suất", "fed", "tỷ giá", "pmi", "vix", "macro")):
            lines.append(f"- **Bối cảnh Vĩ mô sống ({mc['updated_at_vn']}):** La bàn **{mc['macro_score']}/100 ({mc['stance_short']})** | US 10Y `{mc['us_10y']:.2f}%` | Fed `{mc['fed_bias']}` | VIX `{mc['vix']:.1f}` | PMI VN `{mc['vn_pmi']:.1f}` | DXY `{mc['dxy']:.1f}`.")
            lines.append(f"- **Điểm Hợp Vĩ mô của {sym}:** **{sc['macro_fit']}/100** (Beta ngành `{ov['sector_beta']}x`, Ke động `{ov['live_ke']*100:.1f}%`, hệ số định giá `{ov['multiple_adj']:.2f}x`).")
            lines.append(f"- **Tác động lên Giá trị hợp lý:** Từ `{_fmt_price(pvm['pure_fair_value'])}` (BCTC tĩnh) → **`{_fmt_price(pvm['macro_fair_value'])}`** ({pvm['fv_delta_pct']:+.1f}%).")
            for imp in mf["impacts"]:
                ico = "🟢" if imp["signal"] == "positive" else ("🔴" if imp["signal"] == "negative" else "⚪")
                lines.append(f"  - {ico} **{imp['channel']} ({imp['delta']:+.0f}đ):** {imp['explanation']}")
        elif any(k in q for k in ("mua", "đầu tư", "chiến lược", "giá", "bán")):
            lines.append(f"- **Nhận định Hợp nhất:** **{st['verdict']}** (Điểm hợp nhất **{sc['total']}/100** = BCTC `{sc['pure_bctc']}` × Vĩ mô `{sc['macro_fit']}`).")
            lines.append(f"- **Kết luận:** {st['should_invest']}")
            lines.append(f"- **Định giá thực tế (đã chỉnh Ke={ov['live_ke']*100:.1f}%):** Thị giá `{_fmt_price(m['current_price'])}` vs Fair Value **`{_fmt_price(val['fair_value'])}`** (Upside `{val['upside_pct']:+.1f}%`).")
            tw = st["tranche_weights"]
            lines.append(f"- **Kế hoạch 3 đợt ({tw[0]}% / {tw[1]}% / {tw[2]}%):** `{_fmt_price(pp['buy_tranche_1'])}` → `{_fmt_price(pp['buy_tranche_2'])}` → `{_fmt_price(pp['buy_tranche_3'])}`.")
        elif any(k in q for k in ("rủi ro", "red flag", "cảnh báo", "xấu", "nợ")):
            lines.append(f"**Tổng hợp Red Flags (BCTC + Vĩ mô) cho {sym}:**")
            for rf in analysis["red_flags"] or [{"severity": "low", "source": "bctc", "title": "Không có cảnh báo trọng yếu", "detail": "BCTC & vĩ mô nằm trong vùng kiểm soát."}]:
                tag = "VĨ MÔ" if rf.get("source") == "macro" else "BCTC"
                lines.append(f"- **[{tag} · {rf['severity'].upper()}] {rf['title']}:** {rf['detail']}")
        else:
            lines.append(f"- **{name}** ({analysis['sector_vi']} · `{analysis['report_model']}`)")
            lines.append(f"- **Điểm Hợp nhất:** **{sc['total']}/100** (BCTC `{sc['pure_bctc']}` · Hợp Vĩ mô `{sc['macro_fit']}`) → **{st['verdict']}**.")
            lines.append(f"- **So sánh định giá:** BCTC tĩnh `{_fmt_price(pvm['pure_fair_value'])}` → Chỉnh vĩ mô **`{_fmt_price(pvm['macro_fair_value'])}`** (`{val['upside_pct']:+.1f}%`).")
            lines.append(f"- **Hiệu quả:** ROE `{m['roe']}%`, P/E `{m['pe']}x`, P/B `{m['pb']}x`, LNST TTM `{g['npat_ttm_bn']:,.1f} tỷ`.")
        return "\n".join(lines)

    impacts_md = "\n".join(
        f"| {imp['channel']} | {'🟢 Thuận' if imp['signal']=='positive' else ('🔴 Bất lợi' if imp['signal']=='negative' else '⚪ Trung tính')} ({imp['delta']:+.0f}) | {imp['macro_fact']} | {imp['bctc_link']} | {imp['explanation']} |"
        for imp in mf["impacts"]
    )
    val_md = "\n".join(
        f"| {mod['method']} | {int(mod['weight']*100)}% | **{_fmt_price(mod['fair_value'])}** | {mod['formula']} |"
        for mod in val["models"]
    )
    strengths_md = "\n".join(f"- {s}" for s in analysis["strengths"]) or "- Hoạt động ổn định."
    flags_md = "\n".join(
        f"- **[{'VĨ MÔ' if r.get('source')=='macro' else 'BCTC'} · {r['severity'].upper()}] {r['title']}:** {r['detail']}"
        for r in analysis["red_flags"]
    ) or "- Không phát hiện Red Flag trọng yếu trên cả BCTC lẫn Vĩ mô."
    tw = st["tranche_weights"]

    return f"""# BÁO CÁO NHẬN ĐỊNH HỢP NHẤT BCTC × VĨ MÔ: {sym}
**{name}** | Ngành: **{analysis['sector_vi']}** (`{analysis['report_model']}`) | Kỳ BCTC: `{analysis['latest_report_period']}` | Vĩ mô: `{mc['updated_at_vn']}`

---

## 1. KẾT LUẬN ĐẦU TƯ TOP-DOWN × BOTTOM-UP
| Tiêu chí | BCTC Thuần (Tĩnh) | **Hợp nhất BCTC × Vĩ mô (Thực tế)** |
|---|---:|---:|
| **Điểm Tổng hợp** | {pvm['pure_score']} / 100 | **{pvm['fused_score']} / 100** (Hợp Vĩ mô: **{sc['macro_fit']}/100**) |
| **Chi phí vốn (Ke / WACC)** | {ov['base_ke']*100:.1f}% | **{ov['live_ke']*100:.1f}%** ({ov['ke_shift_bps']:+d} bps) |
| **Giá trị Nội tại (Fair Value)** | {_fmt_price(pvm['pure_fair_value'])} | **{_fmt_price(pvm['macro_fair_value'])}** ({pvm['fv_delta_pct']:+.1f}%) |
| **Upside so với Thị giá ({_fmt_price(m['current_price'])})** | {pvm['pure_upside_pct']:+.1f}% | **{pvm['macro_upside_pct']:+.1f}%** |
| **Khuyến nghị** | {pvm['pure_verdict']} | **{st['verdict']}** |

> **Nhận định dứt khoát:** {st['should_invest']}
> **Lý do điều chỉnh theo vĩ mô:** {pvm['adjustment_reason']}

---

## 2. TRUYỀN DẪN VĨ MÔ HIỆN TẠI VÀO BCTC CỦA {sym}
- **Bức tranh Vĩ mô sống:** La bàn **{mc['macro_score']}/100 ({mc['stance_short']})** · Chu kỳ *{mc['regime_name']}* · US 10Y `{mc['us_10y']:.2f}%` · Fed `{mc['fed_bias']}` · Core PCE `{mc['us_core_pce']:.2f}%` · PMI VN `{mc['vn_pmi']:.1f}` · VIX `{mc['vix']:.1f}` · DXY `{mc['dxy']:.1f}` · Ngoại 5p `{mc['foreign_5d_bn']:+,.0f} tỷ`.

| Kênh truyền dẫn | Chiều tác động | Dữ liệu Vĩ mô sống | Đối chiếu BCTC {sym} | Nhận định chuyên sâu |
|---|---|---|---|---|
{impacts_md}

---

## 3. CHẤT LƯỢNG BCTC & ĐỊNH GIÁ ĐỘNG
- **Tăng trưởng & Sinh lời:** DT TTM `{g['rev_ttm_bn']:,.1f} tỷ` (CAGR 5Y `{g['rev_cagr_5y']}%`) · LNST TTM `{g['npat_ttm_bn']:,.1f} tỷ` (YoY quý mới `{g['npat_yoy_latest_q']}%`) · ROE `{m['roe']}%` · P/E `{m['pe']}x` · P/B `{m['pb']}x` · Cổ tức `{m['dividend_yield']}%`.

| Phương pháp Định giá | Trọng số | Giá trị hợp lý | Cơ sở tính toán (Đã gắn Vĩ mô) |
|---|---:|---:|---|
{val_md}

### Điểm sáng (Bull Case)
{strengths_md}

### Cảnh báo Rủi ro (BCTC & Vĩ mô)
{flags_md}

---

## 4. KẾ HOẠCH HÀNH ĐỘNG & QUẢN TRỊ VỐN ({st['allocation']})
- **Tỷ lệ chia lệnh theo chế độ Vĩ mô ({tw[0]}% / {tw[1]}% / {tw[2]}%):**
  - Đợt 1 ({tw[0]}%): Quanh **{_fmt_price(pp['buy_tranche_1'])}**
  - Đợt 2 ({tw[1]}%): Quanh **{_fmt_price(pp['buy_tranche_2'])}**
  - Đợt 3 ({tw[2]}%): Chiết khấu sâu quanh **{_fmt_price(pp['buy_tranche_3'])}**
- **Vùng chốt lời / Tái cơ cấu:** **{_fmt_price(pp['take_profit_1'])}** – **{_fmt_price(pp['take_profit_2'])}** | **Mốc rà soát rủi ro:** `< {_fmt_price(pp['review_stop_level'])}`
""".strip()


def run_fused_ai_analysis(
    analysis: dict[str, Any],
    provider: str = "offline",
    api_key: str = "",
    model: str = "",
    base_url: str = "",
    question: str | None = None,
) -> dict[str, Any]:
    prov = (provider or "offline").strip().lower()
    if prov != "offline" and not api_key.strip():
        api_key = os.getenv("GEMINI_API_KEY", "") if prov == "gemini" else os.getenv("OPENAI_API_KEY", "")
    if prov == "offline" or not api_key.strip():
        return {
            "mode": "offline",
            "provider": "offline",
            "model": "VĨ MÔ PULSE × BCTC Quantitative Engine (Offline)",
            "content": generate_fused_offline_memo(analysis, question=question),
        }

    compact = {
        "ticker": analysis["ticker"],
        "company_name": analysis["company_name"],
        "sector_vi": analysis["sector_vi"],
        "report_model": analysis["report_model"],
        "latest_report_period": analysis["latest_report_period"],
        "market": analysis["market"],
        "growth": analysis["growth"],
        "scores": analysis["scores"],
        "valuation": analysis["valuation"],
        "macro_fusion": analysis["macro_fusion"],
        "sector_metrics": analysis["sector_metrics"],
        "piotroski": {"score": analysis["piotroski"].get("score"), "label": analysis["piotroski"].get("label")},
        "altman": analysis["altman"],
        "dupont": analysis["dupont"],
        "strengths": analysis["strengths"],
        "red_flags": analysis["red_flags"],
        "strategy": analysis["strategy"],
    }
    ctx_json = json.dumps(compact, ensure_ascii=False)
    sys_prompt = (
        "Bạn là Giám đốc Đầu tư (CIO / CFA Charterholder) chuyên kết hợp Phân tích Vĩ mô Top-Down và Báo cáo Tài chính Bottom-Up tại TTCK Việt Nam. "
        "Hãy dựa 100% vào dữ liệu BCTC thực tế VÀ bối cảnh Vĩ mô sống trong JSON (KHÔNG bịa số), phân tích sắc bén sự tương tác giữa "
        "US 10Y, FedWatch, Core PCE, PMI, DXY/USD-VND, VIX, dòng vốn khối ngoại với cấu trúc tài chính thực tế trên BCTC mã này. "
        "Trả lời thẳng thắn: NÊN ĐẦU TƯ HAY KHÔNG lúc này, định giá sau khi điều chỉnh lãi suất/VIX đã đủ hấp dẫn chưa, và chiến lược chia lệnh cụ thể."
    )
    user_prompt = (
        f"Dữ liệu hợp nhất BCTC × Vĩ mô live của {analysis['ticker']}:\n```json\n{ctx_json}\n```\n\n"
        + (f"Câu hỏi của nhà đầu tư: {question.strip()}" if (question and question.strip()) else
           "Hãy viết Báo cáo Nhận định Đầu tư Hợp nhất (Top-Down Vĩ mô × Bottom-Up BCTC) súc tích, sắc bén, có kịch bản Bull/Base/Bear và hành động cụ thể.")
    )

    if prov == "gemini":
        chosen = validate_ai_model(model, "gemini-2.5-flash")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{chosen}:generateContent"
        try:
            resp = _HTTP.post(
                url,
                headers={"x-goog-api-key": api_key.strip(), "Content-Type": "application/json"},
                json={
                    "systemInstruction": {"parts": [{"text": sys_prompt}]},
                    "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                    "generationConfig": {"temperature": 0.35, "maxOutputTokens": 4096},
                },
                timeout=60,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            logger.warning("Gemini API connection error: %s", type(exc).__name__)
            raise RuntimeError("Không thể kết nối tới Gemini API (timeout hoặc lỗi mạng).") from exc
        if resp.status_code != 200:
            err_msg = ""
            try:
                err_msg = str((resp.json().get("error") or {}).get("message") or "")[:180]
            except Exception:
                err_msg = ""
            raise RuntimeError(f"Gemini API lỗi ({resp.status_code}){(': ' + err_msg) if err_msg else ''}")
        text = resp.json().get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        return {"mode": "online", "provider": "gemini", "model": chosen, "content": text}

    chosen = validate_ai_model(model, "gpt-4o-mini")
    safe_base = validate_outbound_https_url(base_url, default_url="https://api.openai.com/v1").rstrip("/")
    endpoint = safe_base + "/chat/completions"
    try:
        resp = _HTTP.post(
            endpoint,
            headers={"Authorization": f"Bearer {api_key.strip()}", "Content-Type": "application/json"},
            json={
                "model": chosen,
                "temperature": 0.35,
                "messages": [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_prompt}],
            },
            timeout=60,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        logger.warning("OpenAI-compatible API connection error: %s", type(exc).__name__)
        raise RuntimeError("Không thể kết nối tới AI API (timeout hoặc lỗi mạng).") from exc
    if resp.status_code != 200:
        err_msg = ""
        try:
            err_msg = str((resp.json().get("error") or {}).get("message") or "")[:180]
        except Exception:
            err_msg = ""
        raise RuntimeError(f"AI API lỗi ({resp.status_code}){(': ' + err_msg) if err_msg else ''}")
    text = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
    return {"mode": "online", "provider": "openai", "model": chosen, "content": text}
