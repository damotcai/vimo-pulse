/* ==========================================================================
   VĨ MÔ PULSE — Investment Decision Terminal Frontend Engine
   Decision-First CIO Workspace · VN-Index / HOSE Sectors & Global Macro
   ========================================================================== */

(() => {
  "use strict";

  const DEFAULT_WATCHLIST = ["MBB", "SSI", "MWG", "KBC"];

  function loadStoredWatchlist() {
    try {
      const raw = JSON.parse(localStorage.getItem("vimo_watchlist") || "null");
      if (Array.isArray(raw) && raw.length) return raw;
    } catch (_) {}
    return [...DEFAULT_WATCHLIST];
  }

  function loadStoredRebal() {
    try {
      const raw = JSON.parse(localStorage.getItem("vimo_rebal") || "null");
      if (raw && typeof raw === "object") return raw;
    } catch (_) {}
    return { capital: 500, eq: 40, cash: 30, gold: 20, usd: 10 };
  }

  const TAB_TITLES = {
    strategy: "Chiến lược & VN-Index",
    fusion: "Hội tụ 3-in-1 & Danh mục",
    bctc: "BCTC × Vĩ mô Pro",
    wyckoff: "Wyckoff VSA × Vĩ mô",
    macro: "Lạm phát · PMI · Lao động",
    rates: "FedWatch & Lợi suất",
    fx: "Tỷ giá · Vàng SJC · SBV",
    news: "Lịch Mỹ ★★★ & Tin Chính sách VN",
    all: "Xem Toàn cảnh Terminal",
  };

  const state = {
    data: null,
    theme: localStorage.getItem("vimo_theme") || "dark",
    sidebarCollapsed: localStorage.getItem("vimo_sidebar") === "1",
    activeTab: "strategy", // strategy | macro | rates | fx | news | all
    riskProfile: localStorage.getItem("vimo_risk") || "balanced", // safe | balanced | aggressive
    rebal: loadStoredRebal(),
    watchlist: loadStoredWatchlist(),
    watchlistItems: [],
    flowMode: "foreign",   // foreign | prop | both
    vnIndex: "VNINDEX",    // VNINDEX | VN30 | HNX | UPCOM
    vnMode: "tech",        // tech | overlay | ytd
    vnOverlay: "usdvnd",   // usdvnd | dxy | us10y | sp500
    vnRange: 252,          // 65 | 130 | 252 | 756 | 1300
    infMode: "timeline",   // timeline | yearly | mom
    infRange: 60,          // 12 | 36 | 60 | 120
    infMetric: "all",      // all | us_cpi | us_core_cpi | us_core_pce | us_pce | us_ppi
    pmiMode: "timeline",   // timeline | yearly_vn | yearly_us
    laborMode: "dual",     // dual | yearly
    fedMode: "path",       // path | probs
    ycMode: "curve",       // curve | spread
    vnIbMode: "interbank", // interbank | spread_fed | deposit
    vnIbRange: 130,        // 22 | 65 | 130 | 252 | 600
    fxTierMode: "tiers",   // tiers | spread
    fxTierRange: 130,      // 22 | 65 | 130 | 252 | 650
    fxMode: "dual",        // dual | ytd
    fxRange: 260,          // 90 | 260 | 780 | 1400
    wbMetric: "gdp_growth",// gdp_growth | cpi_annual | unemployment
    calFilter: "all",      // all | upcoming | released
    newsCat: "ALL",
    newsQuery: "",
  };

  const charts = {};
  let calPollTimer = null;
  let calSyncBusy = false;
  const YEAR_COLORS = ["#64748b", "#6366f1", "#d97706", "#059669", "#0284c7", "#e11d48"];

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  function esc(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function safeHref(url) {
    const raw = String(url ?? "").trim();
    if (/^https?:\/\//i.test(raw)) return esc(raw);
    return "#";
  }

  function fmtNum(v, digits = 2, suffix = "") {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
    return Number(v).toLocaleString("en-US", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }) + suffix;
  }

  function fmtVnd(v) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
    return Math.round(Number(v)).toLocaleString("vi-VN") + "₫";
  }

  function fmtSigned(v, digits = 2, suffix = "") {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
    const n = Number(v);
    const sign = n > 0 ? "+" : "";
    return `${sign}${n.toFixed(digits)}${suffix}`;
  }

  function getThemeTokens() {
    const isLight = state.theme === "light";
    return {
      text: isLight ? "#0f172a" : "#f1f5f9",
      muted: isLight ? "#334155" : "#cbd5e1",
      subtle: isLight ? "#475569" : "#94a3b8",
      grid: isLight ? "rgba(15, 23, 42, 0.10)" : "rgba(148, 163, 184, 0.14)",
      tooltipBg: isLight ? "#ffffff" : "#0f172a",
      tooltipBorder: isLight ? "#94a3b8" : "rgba(148, 163, 184, 0.34)",
      cardBg: isLight ? "#ffffff" : "#111a2c",
      accent: isLight ? "#0369a1" : "#38bdf8",
      emerald: isLight ? "#047857" : "#34d399",
      rose: isLight ? "#be123c" : "#fb7185",
      amber: isLight ? "#b45309" : "#fbbf24",
      violet: isLight ? "#6d28d9" : "#c084fc",
    };
  }

  function getAllocColor(key, rawColor) {
    const t = getThemeTokens();
    const map = {
      vn_equity: t.emerald,
      vnd_cash: t.accent,
      gold: t.amber,
      usd_assets: t.violet,
    };
    return map[key] || rawColor;
  }

  function resizeVisibleCharts() {
    Object.values(charts).forEach((ch) => {
      if (ch && ch.getDom() && ch.getDom().offsetParent !== null) {
        ch.resize();
      }
    });
    window.BctcModule?.onResize();
    window.WyckoffModule?.onResize();
    window.ProModules?.onResize();
  }

  function getChart(id) {
    if (typeof window.echarts === "undefined") return null;
    const el = document.getElementById(id);
    if (!el) return null;
    if (el.offsetParent === null) return null; // Skip initializing hidden tab charts until visible
    if (charts[id] && charts[id].getDom() !== el) {
      try { charts[id].dispose(); } catch (_) {}
      delete charts[id];
    }
    if (!charts[id]) {
      charts[id] = window.echarts.init(el);
    }
    return charts[id];
  }

  function baseTooltip() {
    const t = getThemeTokens();
    return {
      trigger: "axis",
      backgroundColor: t.tooltipBg,
      borderColor: t.tooltipBorder,
      borderWidth: 1,
      textStyle: { color: t.text, fontSize: 12, fontFamily: "JetBrains Mono, Inter, sans-serif" },
      axisPointer: { type: "cross", lineStyle: { color: t.subtle, type: "dashed" } },
    };
  }

  function applyTheme(theme) {
    const valid = theme === "light" ? "light" : "dark";
    state.theme = valid;
    document.documentElement.setAttribute("data-theme", valid);
    localStorage.setItem("vimo_theme", valid);
    $$("#theme-switch .theme-opt").forEach((btn) => {
      const isAct = btn.dataset.setTheme === valid;
      btn.classList.toggle("active", isAct);
      btn.setAttribute("aria-pressed", isAct ? "true" : "false");
    });
    if (state.data) renderVisibleWorkspace();
    window.BctcModule?.onThemeChange();
    window.WyckoffModule?.onThemeChange();
    window.ProModules?.onThemeChange();
  }

  function applySidebarState(collapsed) {
    state.sidebarCollapsed = !!collapsed;
    localStorage.setItem("vimo_sidebar", state.sidebarCollapsed ? "1" : "0");
    const shell = $("#app-shell");
    if (shell) shell.classList.toggle("sidebar-collapsed", state.sidebarCollapsed);
    setTimeout(resizeVisibleCharts, 250);
  }

  function closeMobileDrawer() {
    $("#app-shell")?.classList.remove("mobile-open");
  }

  function setStatus(kind, text) {
    const dot = $("#status-dot");
    const lbl = $("#status-text");
    if (dot) dot.className = `status-dot ${kind}`;
    if (lbl) lbl.textContent = text;
  }

  function updateGreeting() {
    const hr = new Date().getHours();
    let greet = "Phương án Đầu tư & Toàn cảnh VN-Index";
    if (hr >= 5 && hr < 12) greet = "Chào buổi sáng · Phương án Đầu tư & VN-Index Hôm nay";
    else if (hr >= 12 && hr < 18) greet = "Cập nhật Buổi chiều · Chiến lược Đầu tư & VN-Index";
    else greet = "Tổng kết Phiên & Chuẩn bị Phương án Đầu tư";
    const el = $("#greeting-title");
    if (el) el.textContent = greet;
  }

  // =========================================================================
  // Workspace Tab Switcher
  // =========================================================================

  function switchTab(tab) {
    state.activeTab = tab;
    const isAll = tab === "all";
    document.body.classList.toggle("view-all", isAll);
    closeMobileDrawer();

    const titleEl = $("#active-view-title");
    if (titleEl) titleEl.textContent = TAB_TITLES[tab] || "VĨ MÔ PULSE";

    $$("#workspace-tabs .ws-tab").forEach((btn) => {
      const active = btn.dataset.tab === tab;
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
    });

    $$(".ws-pane").forEach((pane) => {
      pane.classList.toggle("active", isAll || pane.dataset.pane === tab);
    });

    window.scrollTo({ top: 0, behavior: "instant" });

    if (state.data) {
      requestAnimationFrame(() => {
        renderVisibleWorkspace();
        resizeVisibleCharts();
      });
    }

    // Whenever user opens the Calendar tab, trigger an ultra-fast live US 3-star sync!
    if (tab === "news" || tab === "all") {
      refreshCalendarOnly(false);
    }
    if (tab === "bctc" || tab === "all") {
      window.BctcModule?.onShow();
    }
    if (tab === "wyckoff" || tab === "all") {
      window.WyckoffModule?.onShow();
    }
    window.ProModules?.onShowTab(tab);
  }

  // =========================================================================
  // 1. Ticker Strip
  // =========================================================================

  function renderTicker(data) {
    const wrap = $("#ticker-strip");
    const tickers = data.fx?.tickers || [];
    if (!wrap || !tickers.length) return;
    const vnLabels = new Set(["VN-Index", "VN30", "HNX", "UPCoM", "P/E VNI", "Vàng SJC"]);
    wrap.innerHTML = tickers
      .map((tk) => {
        const pct = tk.pct;
        const cls = pct > 0 ? "up" : pct < 0 ? "down" : "flat";
        const vnCls = vnLabels.has(tk.label) ? "vn-flag" : "";
        const priceStr =
          tk.unit === "₫"
            ? Math.round(tk.price).toLocaleString("vi-VN") + "₫"
            : tk.unit === "$"
            ? "$" + tk.price.toLocaleString("en-US")
            : tk.price.toLocaleString("en-US") + (tk.unit || "");
        const pctStr = pct !== null && pct !== undefined ? fmtSigned(pct, 2, "%") : "—";
        return `
          <div class="ticker-item ${vnCls}">
            <span class="ticker-label">${esc(tk.label)}</span>
            <span class="ticker-price">${esc(priceStr)}</span>
            <span class="ticker-chg ${cls}">${esc(pctStr)}</span>
          </div>`;
      })
      .join("");
  }

  // =========================================================================
  // 2. INVESTMENT DECISION COCKPIT (Workspace 1 Hero)
  // =========================================================================

  function getAdjustedAllocation(baseAlloc) {
    if (!baseAlloc || !baseAlloc.length) return [];
    const shift = state.riskProfile === "safe" ? -10 : state.riskProfile === "aggressive" ? 15 : 0;
    const copy = baseAlloc.map((a) => ({ ...a }));
    const eq = copy.find((a) => a.key === "vn_equity");
    const cash = copy.find((a) => a.key === "vnd_cash");
    const gold = copy.find((a) => a.key === "gold");
    const usd = copy.find((a) => a.key === "usd_assets");
    if (eq && cash && gold && usd && shift !== 0) {
      const targetEq = Math.max(10, Math.min(80, eq.weight + shift));
      const actualDelta = targetEq - eq.weight;
      eq.weight = targetEq;
      if (actualDelta > 0) {
        // Take primarily from cash, then gold/usd
        let rem = actualDelta;
        const takeCash = Math.min(rem, Math.max(0, cash.weight - 10));
        cash.weight -= takeCash; rem -= takeCash;
        const takeGold = Math.min(rem, Math.max(0, gold.weight - 5));
        gold.weight -= takeGold; rem -= takeGold;
        const takeUsd = Math.min(rem, Math.max(0, usd.weight - 5));
        usd.weight -= takeUsd;
      } else {
        // Move freed equity weight into cash (+70%) and gold (+30%)
        const add = -actualDelta;
        const addCash = Math.round(add * 0.7);
        cash.weight += addCash;
        gold.weight += add - addCash;
      }
      // Guarantee exact 100%
      const sum = copy.reduce((s, x) => s + x.weight, 0);
      if (sum !== 100) cash.weight += 100 - sum;
    }
    return copy;
  }

  function renderRebalancer(alloc) {
    const out = $("#rebal-results");
    if (!out || !alloc || !alloc.length) return;
    const cap = Math.max(0, Number(state.rebal.capital) || 0);
    const curMap = {
      vn_equity: Number(state.rebal.eq) || 0,
      vnd_cash: Number(state.rebal.cash) || 0,
      gold: Number(state.rebal.gold) || 0,
      usd_assets: Number(state.rebal.usd) || 0,
    };
    const shortNames = {
      vn_equity: "Cổ phiếu VN",
      vnd_cash: "Tiền mặt / TP",
      gold: "Vàng",
      usd_assets: "Tài sản USD",
    };
    out.innerHTML = alloc
      .map((a) => {
        const curPct = curMap[a.key] ?? 0;
        const diffPct = a.weight - curPct;
        const targetAmt = Math.round((cap * a.weight) / 100);
        const diffAmt = Math.round((cap * diffPct) / 100);
        const cls = diffAmt > 2 ? "buy" : diffAmt < -2 ? "sell" : "keep";
        const actTxt =
          diffAmt > 2
            ? `Mua thêm +${diffAmt.toLocaleString("vi-VN")} tr`
            : diffAmt < -2
            ? `Hạ bớt ${diffAmt.toLocaleString("vi-VN")} tr`
            : "Giữ nguyên";
        return `
          <div class="rebal-res-row">
            <span><strong>${shortNames[a.key] || a.name}</strong> · Mục tiêu <span class="mono">${targetAmt.toLocaleString("vi-VN")} tr (${a.weight}%)</span></span>
            <span class="rebal-res-act ${cls}">${actTxt}</span>
          </div>`;
      })
      .join("");
  }

  function renderAllocationOnly() {
    if (!state.data?.strategy) return;
    const t = getThemeTokens();
    const alloc = getAdjustedAllocation(state.data.strategy.allocation || []);

    const chAlloc = getChart("chart-allocation");
    if (chAlloc && alloc.length) {
      const eqItem = alloc.find((a) => a.key === "vn_equity");
      chAlloc.setOption({
        tooltip: {
          trigger: "item",
          backgroundColor: t.tooltipBg,
          borderColor: t.tooltipBorder,
          textStyle: { color: t.text, fontSize: 12 },
          formatter: "{b}: <strong>{c}%</strong>",
        },
        title: {
          text: eqItem ? `${eqItem.weight}%` : "100%",
          subtext: eqItem ? "Cổ phiếu VN" : "Phân bổ",
          left: "center",
          top: "36%",
          itemGap: 2,
          textStyle: { color: t.text, fontSize: 20, fontWeight: 800, fontFamily: "JetBrains Mono, sans-serif" },
          subtextStyle: { color: t.muted, fontSize: 10.5, fontWeight: 600 },
        },
        series: [
          {
            type: "pie",
            radius: ["60%", "88%"],
            center: ["50%", "50%"],
            avoidLabelOverlap: false,
            itemStyle: { borderRadius: 5, borderColor: t.cardBg, borderWidth: 2 },
            label: { show: false },
            data: alloc.map((a) => ({
              value: a.weight,
              name: a.name,
              itemStyle: { color: getAllocColor(a.key, a.color) },
            })),
          },
        ],
      }, true);
    }

    const allocList = $("#allocation-list");
    if (allocList) {
      allocList.innerHTML = alloc
        .map((a) => {
          const col = getAllocColor(a.key, a.color);
          return `
          <div class="alloc-row">
            <div class="alloc-row-top">
              <span class="alloc-name">
                <span class="alloc-dot" style="background:${col}"></span>
                <span>${esc(a.name)}</span>
              </span>
              <span class="alloc-pct" style="color:${col}">${a.weight}%</span>
            </div>
            <div class="alloc-reason">${esc(a.reason)}</div>
          </div>`;
        })
        .join("");
    }
    renderRebalancer(alloc);
  }

  function renderStrategyCockpit(data) {
    const st = data.strategy;
    if (!st) return;
    const t = getThemeTokens();

    const stanceEl = $("#strategy-stance-badge");
    const stanceTxt = $("#strategy-stance-text");
    if (stanceEl && stanceTxt && st.stance) {
      stanceEl.className = `stance-pill ${st.stance.tone || "amber"}`;
      stanceTxt.textContent = st.stance.label;
    }
    const brief = data.briefing || {};
    if ($("#regime-text") && brief.regime?.name) {
      $("#regime-text").textContent = brief.regime.name;
    }
    $("#strategy-score-tag").textContent = `${st.total_score} / 100 điểm`;
    $("#strategy-summary-action").textContent = st.summary_action || "";

    // Sync Sidebar Footer Score Box
    const sbVal = $("#sb-score-val");
    const sbSub = $("#sb-score-sub");
    if (sbVal) {
      const sc = st.total_score ?? 50;
      sbVal.textContent = `${sc}/100`;
      sbVal.style.color = sc >= 70 ? t.emerald : sc >= 45 ? t.amber : t.rose;
    }
    if (sbSub) sbSub.textContent = st.stance?.short || "Sẵn sàng";

    // 1. Risk-On / Risk-Off Gauge
    const chGauge = getChart("chart-risk-gauge");
    if (chGauge) {
      const score = st.total_score ?? 50;
      const scoreColor = score >= 70 ? t.emerald : score >= 45 ? t.amber : t.rose;
      chGauge.setOption({
        series: [
          {
            type: "gauge",
            startAngle: 195,
            endAngle: -15,
            min: 0,
            max: 100,
            center: ["50%", "54%"],
            radius: "92%",
            axisLine: {
              lineStyle: {
                width: 13,
                color: [
                  [0.38, t.rose],
                  [0.55, t.amber],
                  [0.72, t.accent],
                  [1, t.emerald],
                ],
              },
            },
            pointer: { length: "52%", width: 4, itemStyle: { color: t.text } },
            anchor: {
              show: true,
              showAbove: true,
              size: 8,
              itemStyle: { color: t.text, borderWidth: 2, borderColor: t.cardBg },
            },
            axisTick: { show: false },
            splitLine: { length: 7, distance: -13, lineStyle: { color: t.tooltipBg, width: 2 } },
            axisLabel: {
              distance: -24,
              color: t.muted,
              fontSize: 10,
              formatter: (v) => (v === 0 ? "0" : v === 50 ? "50" : v === 100 ? "100" : ""),
            },
            title: { offsetCenter: [0, "62%"], fontSize: 11.5, fontWeight: 600, color: t.muted },
            detail: {
              valueAnimation: true,
              fontSize: 27,
              fontWeight: 800,
              fontFamily: "JetBrains Mono, sans-serif",
              offsetCenter: [0, "34%"],
              color: scoreColor,
              formatter: "{value}",
            },
            data: [{ value: score, name: st.stance?.short || "Điểm Vĩ mô" }],
          },
        ],
      }, true);
    }

    // 2. Five Score Pillars
    const pillarsEl = $("#strategy-pillars");
    if (pillarsEl) {
      pillarsEl.innerHTML = (st.pillars || [])
        .map((p) => {
          const pct = Math.round((p.score / p.max) * 100);
          const color = pct >= 65 ? t.emerald : pct >= 42 ? t.amber : t.rose;
          return `
            <div class="pillar-row">
              <div class="pillar-top">
                <div>
                  <span class="pillar-name">${esc(p.name)}</span>
                  <span class="pillar-note">${esc(p.note)}</span>
                </div>
                <span class="pillar-score" style="color:${color}">${p.score.toFixed(1)}/${p.max}</span>
              </div>
              <div class="pillar-track">
                <div class="pillar-fill" style="width:${pct}%;background:${color}"></div>
              </div>
            </div>`;
        })
        .join("");
    }

    // 3. Tactical Allocation Donut, 2x2 Cards & Rebalancer
    renderAllocationOnly();

    // 4. Quantitative Trigger Rules ("Nếu... Thì...")
    const trigEl = $("#strategy-triggers");
    if (trigEl) {
      trigEl.innerHTML = (st.triggers || [])
        .map(
          (tr) => `
          <div class="trigger-card ${esc(tr.type || "macro")}">
            <div class="trigger-title">${esc(tr.title)}</div>
            <div class="trigger-cond"><strong>Nếu:</strong> ${esc(tr.condition)}</div>
            <div class="trigger-act"><strong>Thì:</strong> ${esc(tr.action)}</div>
          </div>`
        )
        .join("");
    }
  }

  // =========================================================================
  // 3. VIETNAM INDICES HUB (VN-Index, VN30, HNX, UPCoM + Foreign Flow)
  // =========================================================================

  function renderSparkline(id, values, color, refLine = null) {
    const ch = getChart(id);
    if (!ch || !values || values.length < 2) return;
    const minV = Math.min(...values);
    const maxV = Math.max(...values);
    const pad = (maxV - minV) * 0.15 || 0.5;
    const seriesObj = {
      type: "line",
      data: values,
      smooth: 0.3,
      showSymbol: false,
      lineStyle: { width: 2, color },
      areaStyle: {
        color: {
          type: "linear", x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [{ offset: 0, color: color + "40" }, { offset: 1, color: color + "00" }],
        },
      },
    };
    if (refLine !== null) {
      seriesObj.markLine = {
        silent: true,
        symbol: "none",
        lineStyle: { type: "dashed", color: "#94a3b8", width: 1, opacity: 0.55 },
        label: { show: false },
        data: [{ yAxis: refLine }],
      };
    }
    ch.setOption({
      animation: false,
      grid: { left: 2, right: 2, top: 4, bottom: 4 },
      xAxis: { type: "category", show: false, data: values.map((_, i) => i) },
      yAxis: { type: "value", show: false, min: minV - pad, max: maxV + pad },
      series: [seriesObj],
    }, true);
  }

  function renderVnMarket(data) {
    const vn = data.vn_market || {};
    const indices = vn.indices || {};
    const flow = vn.flow || {};
    const vniPe = data.vn_valuation?.vnindex_pe || {};
    const t = getThemeTokens();

    // 1. Render 4 Clickable Vietnam Index Cards (with live VN-Index P/E on VNINDEX card!)
    const cardsWrap = $("#vn-idx-cards");
    const order = ["VNINDEX", "VN30", "HNX", "UPCOM"];
    if (cardsWrap) {
      cardsWrap.innerHTML = order
        .map((code) => {
          const idx = indices[code];
          if (!idx || !idx.available) return "";
          const activeCls = state.vnIndex === code ? "active" : "";
          const pctCls = idx.pct > 0 ? "down-good" : idx.pct < 0 ? "up-bad" : "";
          const ytdColor = idx.ytd_pct >= 0 ? t.emerald : t.rose;
          const thirdStat =
            code === "VNINDEX" && vniPe.available && vniPe.pe
              ? `<span>P/E: <strong style="color:${t.emerald}">${vniPe.pe.toFixed(2)}x</strong></span>`
              : `<span>MA50: <strong>${fmtNum(idx.ma50, 0)}</strong></span>`;
          return `
            <article class="vn-idx-card ${activeCls}" data-vn-code="${code}">
              <div class="vn-idx-head">
                <div>
                  <span class="vn-idx-name">${esc(idx.name)}</span>
                  <span class="vn-idx-mkt">· ${esc(idx.market)}</span>
                </div>
                <span class="delta-pill ${esc(idx.trend_tone || "info")}">${esc(idx.trend)}</span>
              </div>
              <div class="vn-idx-price-row">
                <span class="vn-idx-val">${fmtNum(idx.close, 2)}</span>
                <span class="delta-pill ${pctCls}">${fmtSigned(idx.pct, 2, "%")}</span>
              </div>
              <div class="vn-idx-spark" id="spark-vn-${code}"></div>
              <div class="vn-idx-foot mono">
                <span>YTD: <strong style="color:${ytdColor}">${fmtSigned(idx.ytd_pct, 1, "%")}</strong></span>
                <span>RSI: <strong>${fmtNum(idx.rsi14, 0)}</strong></span>
                ${thirdStat}
              </div>
            </article>`;
        })
        .join("");

      order.forEach((code) => {
        const idx = indices[code];
        if (!idx || !idx.available) return;
        const color = idx.ytd_pct >= 0 ? t.emerald : t.rose;
        renderSparkline(`spark-vn-${code}`, idx.spark, color, idx.ma50);
      });
    }

    // 1b. Render Live VN-Index P/E Valuation Card
    renderVnPeValuation(vniPe, data.rates?.yield_curve?.latest?.yields?.["10Y"]);

    // Toggle secondary controls visibility
    $("#vn-overlay-group")?.classList.toggle("hidden", state.vnMode !== "overlay");
    $("#vn-range-group")?.classList.toggle("hidden", state.vnMode === "ytd");
    $("#vn-ytd-table-wrap")?.classList.toggle("hidden", state.vnMode !== "ytd");

    // 2. Main VN Index Chart
    const activeIdx = indices[state.vnIndex] || indices.VNINDEX;
    if (activeIdx && activeIdx.available) {
      $("#vn-active-badge").textContent = activeIdx.code;
      $("#vn-levels-title").textContent = `Mốc Kỹ thuật Quan trọng — ${activeIdx.name}`;
      renderVnMainChart(activeIdx, data.fx?.overlay_series || {});
      renderVnTechnicalLevels(activeIdx, flow);
    }

    // 3. Foreign Flow Chart & Summary
    renderForeignFlow(flow);
  }

  function renderVnMainChart(idx, overlays) {
    const ch = getChart("chart-vn-index");
    if (!ch) return;
    const t = getThemeTokens();

    // MODE A: Multi-Year Seasonal YTD Comparison (2022–2026)
    if (state.vnMode === "ytd") {
      $("#vn-chart-title").textContent = `${idx.name} — So sánh Hiệu suất Các Năm (% YTD · T1 → T12)`;
      $("#vn-chart-sub").textContent = "Đặt các năm cạnh nhau để nhìn rõ chu kỳ mùa vụ và sức khỏe thị trường năm nay";
      const ytd = idx.ytd_comparison || { years: [], months: [], matrix: {}, annual_summary: [] };
      const yrs = ytd.years || [];

      const tbody = $("#table-vn-yearly tbody");
      if (tbody) {
        tbody.innerHTML = (ytd.annual_summary || [])
          .map((r) => {
            const c = r.ytd_pct >= 0 ? t.emerald : t.rose;
            return `
              <tr>
                <td><strong>${r.year}</strong></td>
                <td>${fmtNum(r.open, 2)}</td>
                <td>${fmtNum(r.low, 2)}</td>
                <td>${fmtNum(r.high, 2)}</td>
                <td><strong>${fmtNum(r.close, 2)}</strong></td>
                <td style="color:${c};font-weight:700">${fmtSigned(r.ytd_pct, 2, "%")}</td>
              </tr>`;
          })
          .join("");
      }

      const series = yrs.map((yr, i) => {
        const isLatest = i === yrs.length - 1;
        const color = isLatest ? t.emerald : YEAR_COLORS[i % YEAR_COLORS.length];
        return {
          name: yr,
          type: "line",
          smooth: 0.25,
          symbol: isLatest ? "circle" : "none",
          symbolSize: isLatest ? 7 : 4,
          lineStyle: { width: isLatest ? 3.2 : 1.9, opacity: isLatest ? 1 : 0.72, color },
          itemStyle: { color },
          data: (ytd.matrix || {})[yr] || [],
        };
      });
      if (series.length) {
        series[0].markLine = {
          silent: true,
          symbol: "none",
          lineStyle: { type: "dashed", color: t.muted, width: 1.2 },
          label: { show: false },
          data: [{ yAxis: 0 }],
        };
      }
      ch.setOption({
        tooltip: { ...baseTooltip(), valueFormatter: (v) => (v == null ? "—" : fmtSigned(v, 2, "%")) },
        legend: { top: 0, textStyle: { color: t.muted, fontSize: 12 } },
        grid: { left: 50, right: 18, top: 38, bottom: 28 },
        xAxis: { type: "category", data: ytd.months || [], axisLabel: { color: t.muted }, axisLine: { lineStyle: { color: t.grid } } },
        yAxis: { type: "value", axisLabel: { color: t.muted, formatter: "{value}%" }, splitLine: { lineStyle: { color: t.grid } } },
        series,
      }, true);
      return;
    }

    const slice = (idx.series || []).slice(-state.vnRange);
    const dates = slice.map((b) => b.date);

    // MODE B: Macro Overlay (VN-Index vs USD/VND | DXY | US 10Y | S&P 500)
    if (state.vnMode === "overlay") {
      const ovMeta = {
        usdvnd: { label: "USD/VND Spot", color: t.amber, unit: "₫" },
        dxy: { label: "Chỉ số DXY", color: t.rose, unit: "" },
        us10y: { label: "Lợi suất US 10Y (%)", color: t.violet, unit: "%" },
        sp500: { label: "S&P 500 (Mỹ)", color: t.accent, unit: "" },
      }[state.vnOverlay] || { label: "USD/VND", color: t.amber, unit: "" };

      $("#vn-chart-title").textContent = `Đối chiếu ${idx.name} vs ${ovMeta.label}`;
      $("#vn-chart-sub").textContent = "Nhận diện phân kỳ giữa chứng khoán Việt Nam và biến số vĩ mô toàn cầu";

      const rawOv = overlays[state.vnOverlay] || [];
      const ovMap = new Map(rawOv.map((p) => [p.date, p.v]));
      let lastV = null;
      const alignedOv = dates.map((d) => {
        if (ovMap.has(d)) lastV = ovMap.get(d);
        return lastV;
      });

      ch.setOption({
        tooltip: baseTooltip(),
        legend: { top: 0, textStyle: { color: t.muted, fontSize: 12 } },
        grid: { left: 58, right: 62, top: 38, bottom: 28 },
        xAxis: { type: "category", data: dates, axisLabel: { color: t.muted, fontSize: 11 }, axisLine: { lineStyle: { color: t.grid } } },
        yAxis: [
          {
            type: "value",
            name: idx.name,
            nameTextStyle: { color: t.emerald, fontSize: 11 },
            scale: true,
            axisLabel: { color: t.muted },
            splitLine: { lineStyle: { color: t.grid } },
          },
          {
            type: "value",
            name: ovMeta.label,
            nameTextStyle: { color: ovMeta.color, fontSize: 11 },
            scale: true,
            axisLabel: { color: t.muted },
            splitLine: { show: false },
          },
        ],
        series: [
          {
            name: idx.name,
            type: "line",
            yAxisIndex: 0,
            showSymbol: false,
            lineStyle: { width: 2.6, color: t.emerald },
            itemStyle: { color: t.emerald },
            areaStyle: {
              color: {
                type: "linear", x: 0, y: 0, x2: 0, y2: 1,
                colorStops: [{ offset: 0, color: t.emerald + "2c" }, { offset: 1, color: t.emerald + "00" }],
              },
            },
            data: slice.map((b) => b.c),
          },
          {
            name: ovMeta.label,
            type: "line",
            yAxisIndex: 1,
            showSymbol: false,
            connectNulls: true,
            lineStyle: { width: 2.1, color: ovMeta.color },
            itemStyle: { color: ovMeta.color },
            data: alignedOv,
          },
        ],
      }, true);
      return;
    }

    // MODE C: Technical Chart (Candlestick / Line + MA20 / MA50 / MA200 + Volume)
    const useCandle = slice.length <= 270;
    $("#vn-chart-title").textContent = `${idx.name} — Phân tích Kỹ thuật (${useCandle ? "Nến Nhật" : "Đường giá"} · MA20 / MA50 / MA200 · Khối lượng)`;
    $("#vn-chart-sub").textContent = `Cập nhật phiên ${idx.date} · Đóng cửa ${fmtNum(idx.close, 2)} (${fmtSigned(idx.pct, 2, "%")})`;

    const mainSeries = useCandle
      ? {
          name: idx.name,
          type: "candlestick",
          xAxisIndex: 0,
          yAxisIndex: 0,
          itemStyle: {
            color: t.emerald,
            color0: t.rose,
            borderColor: t.emerald,
            borderColor0: t.rose,
          },
          data: slice.map((b) => [b.o, b.c, b.l, b.h]),
        }
      : {
          name: idx.name,
          type: "line",
          xAxisIndex: 0,
          yAxisIndex: 0,
          showSymbol: false,
          lineStyle: { width: 2.4, color: t.emerald },
          itemStyle: { color: t.emerald },
          areaStyle: {
            color: {
              type: "linear", x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [{ offset: 0, color: t.emerald + "28" }, { offset: 1, color: t.emerald + "00" }],
            },
          },
          data: slice.map((b) => b.c),
        };

    ch.setOption({
      tooltip: {
        ...baseTooltip(),
        axisPointer: { type: "cross", link: [{ xAxisIndex: "all" }] },
      },
      legend: { top: 0, textStyle: { color: t.muted, fontSize: 11.5 } },
      grid: [
        { left: 56, right: 16, top: 36, height: "62%" },
        { left: 56, right: 16, top: "77%", height: "16%" },
      ],
      xAxis: [
        {
          type: "category",
          gridIndex: 0,
          data: dates,
          axisLabel: { show: false },
          axisLine: { lineStyle: { color: t.grid } },
          axisTick: { show: false },
        },
        {
          type: "category",
          gridIndex: 1,
          data: dates,
          axisLabel: { color: t.muted, fontSize: 10.5 },
          axisLine: { lineStyle: { color: t.grid } },
        },
      ],
      yAxis: [
        {
          type: "value",
          gridIndex: 0,
          scale: true,
          axisLabel: { color: t.muted, fontSize: 11 },
          splitLine: { lineStyle: { color: t.grid } },
        },
        {
          type: "value",
          gridIndex: 1,
          splitNumber: 2,
          axisLabel: { show: false },
          splitLine: { show: false },
        },
      ],
      series: [
        mainSeries,
        {
          name: "MA20",
          type: "line",
          xAxisIndex: 0,
          yAxisIndex: 0,
          showSymbol: false,
          lineStyle: { width: 1.5, color: t.accent },
          itemStyle: { color: t.accent },
          data: slice.map((b) => b.ma20),
        },
        {
          name: "MA50",
          type: "line",
          xAxisIndex: 0,
          yAxisIndex: 0,
          showSymbol: false,
          lineStyle: { width: 1.8, color: t.amber },
          itemStyle: { color: t.amber },
          data: slice.map((b) => b.ma50),
        },
        {
          name: "MA200",
          type: "line",
          xAxisIndex: 0,
          yAxisIndex: 0,
          showSymbol: false,
          lineStyle: { width: 2.0, type: "dashed", color: t.rose },
          itemStyle: { color: t.rose },
          data: slice.map((b) => b.ma200),
        },
        {
          name: "Khối lượng",
          type: "bar",
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: slice.map((b) => ({
            value: b.v,
            itemStyle: { color: b.c >= b.o ? t.emerald + "70" : t.rose + "70" },
          })),
        },
      ],
    }, true);
  }

  function renderForeignFlow(flow) {
    const t = getThemeTokens();
    const fDaily = flow.foreign_daily || [];
    const pDaily = flow.prop_daily || [];

    if (flow.liquidity_bn) {
      $("#vn-liq-sub").textContent =
        `Khớp lệnh HOSE (${flow.date || ""}): ${Math.round(flow.liquidity_bn).toLocaleString("vi-VN")} tỷ · Tổng: ${Math.round(flow.total_value_bn || 0).toLocaleString("vi-VN")} tỷ`;
    }

    const sumEl = $("#foreign-summary");
    const box = (lbl, val) => {
      const c = val == null ? t.muted : val >= 0 ? t.emerald : t.rose;
      return `
        <div class="mini-stat">
          <div class="mini-stat-label">${lbl}</div>
          <div class="mini-stat-val" style="color:${c}">${val != null ? `${val >= 0 ? "+" : ""}${Math.round(val).toLocaleString("vi-VN")} tỷ` : "—"}</div>
        </div>`;
    };

    if (sumEl) {
      if (state.flowMode === "prop") {
        sumEl.innerHTML =
          box("Tự doanh 1 phiên", flow.prop_1d_bn) +
          box("Tự doanh 5 phiên", flow.prop_5d_bn) +
          box("Tự doanh 15 phiên", flow.prop_15d_bn);
      } else if (state.flowMode === "both") {
        sumEl.innerHTML =
          box("Ngoại 5 phiên", flow.foreign_5d_bn) +
          box("Tự doanh 5 phiên", flow.prop_5d_bn) +
          box("Ngoại 20 phiên", flow.foreign_20d_bn);
      } else {
        sumEl.innerHTML =
          box("Ngoại phiên gần nhất", flow.foreign_1d_bn) +
          box("Ngoại 5 phiên", flow.foreign_5d_bn) +
          box("Ngoại 20 phiên", flow.foreign_20d_bn);
      }
    }

    const ch = getChart("chart-foreign-flow");
    if (!ch) return;

    if (state.flowMode === "both") {
      const dateSet = new Set([...fDaily.slice(-15).map((d) => d.date), ...pDaily.slice(-15).map((d) => d.date)]);
      const dates = Array.from(dateSet).sort();
      const fMap = new Map(fDaily.map((d) => [d.date, d]));
      const pMap = new Map(pDaily.map((d) => [d.date, d]));
      const labels = dates.map((d) => (fMap.get(d)?.label || pMap.get(d)?.label || d.slice(5)));

      ch.setOption({
        tooltip: {
          ...baseTooltip(),
          valueFormatter: (v) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${Number(v).toLocaleString("vi-VN")} tỷ`),
        },
        legend: { top: 0, textStyle: { color: t.muted, fontSize: 10.5 } },
        grid: { left: 48, right: 10, top: 26, bottom: 24 },
        xAxis: { type: "category", data: labels, axisLabel: { color: t.muted, fontSize: 10 }, axisLine: { lineStyle: { color: t.grid } } },
        yAxis: { type: "value", axisLabel: { color: t.muted, fontSize: 10 }, splitLine: { lineStyle: { color: t.grid } } },
        series: [
          { name: "Khối ngoại", type: "bar", barMaxWidth: 10, itemStyle: { color: t.accent, borderRadius: 2 }, data: dates.map((d) => fMap.get(d)?.net_bn ?? null) },
          { name: "Tự doanh", type: "bar", barMaxWidth: 10, itemStyle: { color: t.violet, borderRadius: 2 }, data: dates.map((d) => pMap.get(d)?.net_bn ?? null) },
        ],
      }, true);
      return;
    }

    const activeList = state.flowMode === "prop" ? pDaily : fDaily;
    if (!activeList.length) return;
    ch.setOption({
      tooltip: {
        ...baseTooltip(),
        valueFormatter: (v) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${Number(v).toLocaleString("vi-VN")} tỷ`),
      },
      legend: { show: false },
      grid: { left: 48, right: 10, top: 14, bottom: 24 },
      xAxis: {
        type: "category",
        data: activeList.map((d) => d.label),
        axisLabel: { color: t.muted, fontSize: 10 },
        axisLine: { lineStyle: { color: t.grid } },
      },
      yAxis: {
        type: "value",
        axisLabel: { color: t.muted, fontSize: 10 },
        splitLine: { lineStyle: { color: t.grid } },
      },
      series: [
        {
          name: state.flowMode === "prop" ? "Tự doanh ròng (tỷ VND)" : "Khối ngoại ròng (tỷ VND)",
          type: "bar",
          barMaxWidth: 16,
          data: activeList.map((d) => ({
            value: d.net_bn,
            itemStyle: {
              color: d.net_bn >= 0 ? t.emerald : t.rose,
              borderRadius: d.net_bn >= 0 ? [3, 3, 0, 0] : [0, 0, 3, 3],
            },
          })),
        },
      ],
    }, true);
  }

  function renderVnTechnicalLevels(idx, flow) {
    const t = getThemeTokens();
    const tbody = $("#table-vn-levels tbody");
    if (!tbody) return;
    const rsi = idx.rsi14;
    const rsiTag = rsi == null ? "—" : rsi >= 70 ? "Quá mua" : rsi <= 30 ? "Quá bán" : rsi >= 50 ? "Khỏe" : "Yếu";
    const rsiCol = rsi >= 70 || rsi <= 35 ? t.amber : rsi >= 50 ? t.emerald : t.rose;

    const distCell = (pct) => {
      if (pct == null) return "—";
      const c = pct >= 0 ? t.emerald : t.rose;
      return `<span style="color:${c}">${fmtSigned(pct, 2, "%")}</span>`;
    };

    const rows = [
      ["Đỉnh 52 tuần", `<strong>${fmtNum(idx.high_52w, 2)}</strong>`, `<span style="color:${t.rose}">${fmtSigned(idx.drawdown_52w, 2, "%")}</span>`],
      ["Đáy 52 tuần", `<strong>${fmtNum(idx.low_52w, 2)}</strong>`, "Hỗ trợ dài hạn"],
      ["MA20 (Ngắn hạn)", fmtNum(idx.ma20, 2), idx.close >= (idx.ma20 || 0) ? `<span style="color:${t.emerald}">Trên MA20</span>` : `<span style="color:${t.rose}">Dưới MA20</span>`],
      ["MA50 (Trung hạn)", fmtNum(idx.ma50, 2), distCell(idx.dist_ma50_pct)],
      ["MA200 (Xu hướng lớn)", fmtNum(idx.ma200, 2), distCell(idx.dist_ma200_pct)],
      ["RSI (14 ngày)", `<strong style="color:${rsiCol}">${fmtNum(rsi, 1)}</strong>`, `<span style="color:${rsiCol}">${rsiTag}</span>`],
      ["Hiệu suất 1M / 3M", `${fmtSigned(idx.chg_1m, 1, "%")} / ${fmtSigned(idx.chg_3m, 1, "%")}`, `YTD ${fmtSigned(idx.ytd_pct, 1, "%")}`],
    ];
    tbody.innerHTML = rows.map(([a, b, c]) => `<tr><td>${a}</td><td>${b}</td><td>${c}</td></tr>`).join("");
  }

  // =========================================================================
  // 4. HOSE SECTOR ROTATION PLAYBOOK (Live 8 Sector Indices)
  // =========================================================================

  function renderVnPeValuation(peObj, us10y) {
    const card = $("#vn-pe-card");
    if (!card) return;
    if (!peObj || !peObj.available || !peObj.pe) {
      card.classList.add("hidden");
      return;
    }
    card.classList.remove("hidden");
    const t = getThemeTokens();
    const pe = peObj.pe;
    const low = peObj.avg_5y_low || 12.9;
    const high = peObj.avg_5y_high || 16.7;
    const mid = peObj.avg_5y_mid || 14.8;

    $("#vn-pe-sub").textContent = `${peObj.date ? `Cập nhật ${peObj.date}` : "Định giá thị trường"} · ${peObj.source || ""}`;
    const valEl = $("#vn-pe-val");
    valEl.textContent = `${pe.toFixed(2)}x`;
    valEl.style.color = peObj.tone === "success" ? t.emerald : peObj.tone === "danger" ? t.rose : t.amber;

    const badge = $("#vn-pe-badge");
    badge.textContent = peObj.status || "—";
    badge.className = `rating-pill ${peObj.tone || "warning"}`;

    const erp = us10y != null && peObj.earnings_yield_pct != null ? (peObj.earnings_yield_pct - us10y).toFixed(2) : null;
    $("#vn-pe-stats").innerHTML = `
      <div>E/P (Lợi suất thu nhập): <strong style="color:${t.accent}">${fmtNum(peObj.earnings_yield_pct, 2, "%")}</strong></div>
      <div>So với TB 5 năm: <strong style="color:${peObj.vs_avg_pct <= 0 ? t.emerald : t.rose}">${fmtSigned(peObj.vs_avg_pct, 1, "%")}</strong></div>
      ${peObj.leaders_weighted_pb ? `<div>P/B nhóm đầu ngành: <strong>${peObj.leaders_weighted_pb.toFixed(2)}x</strong></div>` : ""}`;

    $("#pe-low").textContent = `${low.toFixed(1)}x`;
    $("#pe-mid").textContent = `${mid.toFixed(1)}x`;
    $("#pe-high").textContent = `${high.toFixed(1)}x`;

    // Map PE onto 8.0x -> 22.0x thermometer scale
    const pos = Math.max(4, Math.min(96, ((pe - 8.0) / (22.0 - 8.0)) * 100));
    $("#pe-marker").style.left = `${pos}%`;

    const erpTxt = erp != null ? ` · Chênh lệch E/P so với US 10Y: <strong class="mono">${Number(erp) >= 0 ? "+" : ""}${erp}%</strong>` : "";
    $("#vn-pe-note").innerHTML =
      `Vùng hợp lý 5 năm: <strong class="mono">${low.toFixed(2)}x – ${high.toFixed(2)}x</strong>${erpTxt}`;
  }

  function renderSectorPlaybook(data) {
    const sectors = data.strategy?.sector_playbook || [];
    const leaders = data.vn_valuation?.leaders || [];
    const t = getThemeTokens();

    const ch = getChart("chart-sectors");
    if (ch && sectors.length) {
      const sorted = [...sectors].reverse();
      ch.setOption({
        tooltip: {
          ...baseTooltip(),
          valueFormatter: (v) => (v == null ? "—" : fmtSigned(v, 2, "%")),
        },
        legend: { top: 0, textStyle: { color: t.muted, fontSize: 11.5 } },
        grid: { left: 108, right: 20, top: 34, bottom: 24 },
        xAxis: {
          type: "value",
          axisLabel: { color: t.muted, formatter: "{value}%" },
          splitLine: { lineStyle: { color: t.grid } },
        },
        yAxis: {
          type: "category",
          data: sorted.map((s) => `${s.short} (${s.code})`),
          axisLabel: { color: t.text, fontSize: 11.5, fontWeight: 600 },
          axisLine: { lineStyle: { color: t.grid } },
        },
        series: [
          {
            name: "Hiệu suất 1 Tháng (%)",
            type: "bar",
            barMaxWidth: 11,
            data: sorted.map((s) => ({
              value: s.chg_1m,
              itemStyle: { color: s.chg_1m >= 0 ? t.accent : t.muted + "88", borderRadius: 3 },
            })),
          },
          {
            name: "RS 1M vs VN-Index (%)",
            type: "bar",
            barMaxWidth: 11,
            data: sorted.map((s) => ({
              value: s.rs_1m,
              itemStyle: { color: s.rs_1m >= 0 ? t.emerald : t.rose, borderRadius: 3 },
            })),
          },
        ],
      }, true);
    }

    const tbody = $("#table-sectors tbody");
    if (tbody) {
      tbody.innerHTML = sectors
        .map((s) => {
          const c1m = s.chg_1m >= 0 ? t.emerald : t.rose;
          const crs = s.rs_1m >= 0 ? t.emerald : t.rose;
          const cytd = s.chg_ytd >= 0 ? t.emerald : t.rose;
          const ld = s.leader;
          const ldCell = ld
            ? `<span class="ticker-chip">${esc(ld.ticker)}</span><div class="mono" style="font-size:10.5px;color:var(--text-muted);margin-top:2px">${Math.round(ld.price).toLocaleString("vi-VN")} · PE ${ld.pe ? ld.pe.toFixed(1) + "x" : "—"}</div>`
            : "—";
          return `
            <tr>
              <td>
                <strong>${esc(s.name)}</strong>
                <div class="mono" style="font-size:10.5px;color:var(--text-subtle)">${esc(s.code)} · ${fmtNum(s.close, 1)}</div>
              </td>
              <td style="text-align:left">${ldCell}</td>
              <td><span class="rating-pill ${esc(s.tone)}">${esc(s.rating)}</span></td>
              <td class="mono"><strong>${s.score}</strong></td>
              <td class="mono" style="color:${c1m}">${fmtSigned(s.chg_1m, 1, "%")}</td>
              <td class="mono" style="color:${crs};font-weight:700">${fmtSigned(s.rs_1m, 1, "%")}</td>
              <td class="mono" style="color:${cytd}">${fmtSigned(s.chg_ytd, 1, "%")}</td>
              <td>
                <div class="sector-tactic-txt">${esc(s.tactic)}</div>
                <div class="sector-reason-txt">${esc(s.reason)}</div>
              </td>
            </tr>`;
        })
        .join("");
    }

    // Render 8 Representative Bellwether Stock Cards (1 Stock per Sector)
    const leadersGrid = $("#leaders-grid");
    if (leadersGrid) {
      const secByCode = new Map(sectors.map((s) => [s.code, s]));
      const ordered = sectors.map((s) => s.leader).filter(Boolean);
      const list = ordered.length ? ordered : leaders;

      leadersGrid.innerHTML = list
        .map((st) => {
          const sec = secByCode.get(st.sector_code);
          const d1Cls = st.pct_1d > 0 ? "down-good" : st.pct_1d < 0 ? "up-bad" : "";
          const ytdCol = st.pct_ytd >= 0 ? t.emerald : t.rose;
          const taCls =
            st.ta_raw?.includes("BUY") ? t.emerald : st.ta_raw?.includes("SELL") ? t.rose : t.muted;
          const thirdFoot =
            st.div_yield != null && st.div_yield > 0
              ? `<span>Cổ tức: <strong style="color:${t.amber}">${st.div_yield.toFixed(1)}%</strong></span>`
              : st.ni_growth != null
              ? `<span>LNST: <strong style="color:${st.ni_growth >= 0 ? t.emerald : t.rose}">${fmtSigned(st.ni_growth, 0, "%")}</strong></span>`
              : `<span style="color:${taCls};font-weight:700">KT: ${esc(st.ta_signal)}</span>`;
          return `
            <article class="leader-card">
              <div class="leader-top">
                <div>
                  <div class="leader-ticker-row">
                    <span class="leader-ticker">${esc(st.ticker)}</span>
                    <span class="leader-name">${esc(st.short_name)}</span>
                  </div>
                  <div class="leader-sector">${esc(st.sector_name)} (${esc(st.sector_code)})</div>
                </div>
                ${sec ? `<span class="rating-pill ${esc(sec.tone)}">${esc(sec.rating)}</span>` : ""}
              </div>
              <div class="leader-price-row">
                <span class="leader-price">${Math.round(st.price).toLocaleString("vi-VN")}₫</span>
                <span class="delta-pill ${d1Cls}">${fmtSigned(st.pct_1d, 2, "%")}</span>
              </div>
              <div class="leader-metrics">
                <div class="lm-item">
                  <span class="lm-lbl">P/E</span>
                  <span class="lm-val" style="color:${st.pe && st.pe < 12 ? t.emerald : t.text}">${st.pe ? st.pe.toFixed(1) + "x" : "—"}</span>
                </div>
                <div class="lm-item">
                  <span class="lm-lbl">P/B</span>
                  <span class="lm-val">${st.pb ? st.pb.toFixed(2) + "x" : "—"}</span>
                </div>
                <div class="lm-item">
                  <span class="lm-lbl">ROE</span>
                  <span class="lm-val" style="color:${st.roe && st.roe >= 18 ? t.accent : t.text}">${st.roe ? st.roe.toFixed(1) + "%" : "—"}</span>
                </div>
                <div class="lm-item">
                  <span class="lm-lbl">Vốn hóa</span>
                  <span class="lm-val">${st.market_cap_tn ? st.market_cap_tn.toFixed(0) + "N tỷ" : "—"}</span>
                </div>
              </div>
              <div class="leader-foot mono">
                <span>YTD: <strong style="color:${ytdCol}">${fmtSigned(st.pct_ytd, 1, "%")}</strong></span>
                <span style="display:inline-flex;gap:5px">
                  <button type="button" class="btn-open-bctc-mini" data-open-bctc="${esc(st.ticker)}">BCTC</button>
                  <button type="button" class="btn-open-bctc-mini" data-open-wyckoff="${esc(st.ticker)}">Wyckoff</button>
                </span>
              </div>
            </article>`;
        })
        .join("");
    }
  }

  // =========================================================================
  // 4b. Personal Stock Watchlist (Live Simplize + VNDirect Technicals)
  // =========================================================================

  function saveWatchlist() {
    try {
      localStorage.setItem("vimo_watchlist", JSON.stringify(state.watchlist));
    } catch (_) {}
  }

  async function loadWatchlist() {
    const grid = $("#watchlist-grid");
    if (!state.watchlist.length) {
      state.watchlistItems = [];
      if (grid) grid.innerHTML = `<div class="empty-state">Watchlist đang trống. Nhập mã cổ phiếu phía trên (vd: MBB, TCB, SSI, MWG) để bắt đầu theo dõi.</div>`;
      return;
    }
    try {
      const q = encodeURIComponent(state.watchlist.join(","));
      const r = await fetch(`/api/watchlist?tickers=${q}`, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      state.watchlistItems = j.items || [];
      renderWatchlist();
    } catch (err) {
      console.error("Watchlist error:", err);
      if (grid) grid.innerHTML = `<div class="empty-state">Không tải được dữ liệu Watchlist. Nhấn Làm mới để thử lại.</div>`;
    }
  }

  function renderWatchlist() {
    const grid = $("#watchlist-grid");
    if (!grid) return;
    const t = getThemeTokens();
    const items = state.watchlistItems || [];
    if (!items.length) {
      grid.innerHTML = `<div class="empty-state">Đang tải dữ liệu Watchlist cổ phiếu...</div>`;
      return;
    }
    grid.innerHTML = items
      .map((st) => {
        if (!st.found) {
          return `
            <article class="wl-card">
              <button class="wl-remove" data-rm="${esc(st.ticker)}" title="Xóa mã ${esc(st.ticker)}">&times;</button>
              <div class="leader-ticker-row"><span class="leader-ticker">${esc(st.ticker)}</span></div>
              <div class="empty-state" style="padding:14px 4px">Không tìm thấy mã cổ phiếu này trên HOSE/HNX/UPCoM.</div>
            </article>`;
        }
        const d1Cls = st.pct_1d > 0 ? "down-good" : st.pct_1d < 0 ? "up-bad" : "";
        const ytdCol = (st.pct_ytd ?? 0) >= 0 ? t.emerald : t.rose;
        const rsiCol = st.rsi14 >= 70 || st.rsi14 <= 32 ? t.amber : st.rsi14 >= 50 ? t.emerald : t.rose;
        return `
          <article class="wl-card">
            <button class="wl-remove" data-rm="${esc(st.ticker)}" title="Xóa mã ${esc(st.ticker)}">&times;</button>
            <div>
              <div class="leader-ticker-row">
                <span class="leader-ticker">${esc(st.ticker)}</span>
                <span class="leader-name" style="padding-right:22px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(st.name_vi)}</span>
              </div>
              <div style="margin-top:5px"><span class="wl-signal-pill ${esc(st.signal_tone || "info")}">${esc(st.signal)}</span></div>
            </div>
            <div class="leader-price-row">
              <span class="leader-price">${st.price ? Math.round(st.price).toLocaleString("vi-VN") + "₫" : "—"}</span>
              <span class="delta-pill ${d1Cls}">${fmtSigned(st.pct_1d, 2, "%")}</span>
            </div>
            <div class="wl-spark" id="spark-wl-${esc(st.ticker)}"></div>
            <div class="leader-metrics">
              <div class="lm-item"><span class="lm-lbl">P/E</span><span class="lm-val" style="color:${st.pe && st.pe < 12 ? t.emerald : t.text}">${st.pe ? st.pe.toFixed(1) + "x" : "—"}</span></div>
              <div class="lm-item"><span class="lm-lbl">ROE</span><span class="lm-val" style="color:${st.roe && st.roe >= 18 ? t.accent : t.text}">${st.roe ? st.roe.toFixed(1) + "%" : "—"}</span></div>
              <div class="lm-item"><span class="lm-lbl">RSI 14</span><span class="lm-val" style="color:${rsiCol}">${st.rsi14 != null ? st.rsi14.toFixed(0) : "—"}</span></div>
              <div class="lm-item"><span class="lm-lbl">vs MA50</span><span class="lm-val" style="color:${(st.dist_ma50_pct ?? 0) >= 0 ? t.emerald : t.rose}">${st.dist_ma50_pct != null ? fmtSigned(st.dist_ma50_pct, 1, "%") : "—"}</span></div>
            </div>
            <div class="leader-foot mono">
              <span>YTD: <strong style="color:${ytdCol}">${st.pct_ytd != null ? fmtSigned(st.pct_ytd, 1, "%") : "—"}</strong></span>
              <span style="display:inline-flex;gap:5px">
                <button type="button" class="btn-open-bctc-mini" data-open-bctc="${esc(st.ticker)}">BCTC</button>
                <button type="button" class="btn-open-bctc-mini" data-open-wyckoff="${esc(st.ticker)}">Wyckoff</button>
              </span>
            </div>
          </article>`;
      })
      .join("");

    items.forEach((st) => {
      if (st.found && st.spark && st.spark.length >= 2) {
        const col = (st.dist_ma50_pct ?? 0) >= 0 ? t.emerald : t.rose;
        renderSparkline(`spark-wl-${st.ticker}`, st.spark, col, st.ma50);
      }
    });
  }

  // =========================================================================
  // 4c. Vietnam Gold (SJC Bar & Ring 9999) vs World Gold Converted Spread
  // =========================================================================

  function renderVnGold(data) {
    const sec = $("#sec-gold-vn");
    const g = data.gold_vn || { available: false };
    if (!sec) return;
    if (!g.available) {
      sec.classList.add("hidden");
      return;
    }
    sec.classList.remove("hidden");
    const t = getThemeTokens();
    $("#gold-updated-tag").textContent = `${g.source || "SJC"} · ${g.updated_at || ""}`;

    const kpiGrid = $("#gold-kpi-grid");
    if (kpiGrid) {
      const bar = g.sjc_bar;
      const ring = g.sjc_ring;
      const spreadCol = g.bar_spread_m > 15 ? t.rose : g.bar_spread_m > 8 ? t.amber : t.emerald;
      kpiGrid.innerHTML = `
        <div class="gold-stat-card">
          <div class="gold-stat-lbl">Vàng miếng SJC (Bán ra)</div>
          <div class="gold-stat-val" style="color:${t.amber}">${bar ? bar.sell_m.toFixed(2) + " tr" : "—"}</div>
          <div class="gold-stat-sub">Mua: ${bar ? bar.buy_m.toFixed(2) + " tr" : "—"} · Chênh M-B: ${bar ? bar.spread_buy_sell_m.toFixed(1) + "tr" : "—"}</div>
        </div>
        <div class="gold-stat-card">
          <div class="gold-stat-lbl">Vàng nhẫn SJC 99,99%</div>
          <div class="gold-stat-val">${ring ? ring.sell_m.toFixed(2) + " tr" : "—"}</div>
          <div class="gold-stat-sub">Mua: ${ring ? ring.buy_m.toFixed(2) + " tr" : "—"} · Chênh TG: ${g.ring_spread_m != null ? fmtSigned(g.ring_spread_m, 1, "tr") : "—"}</div>
        </div>
        <div class="gold-stat-card">
          <div class="gold-stat-lbl">Vàng Thế giới Quy đổi</div>
          <div class="gold-stat-val" style="color:${t.accent}">${g.world_vnd_m ? g.world_vnd_m.toFixed(2) + " tr" : "—"}</div>
          <div class="gold-stat-sub">XAU: ${g.world_usd_oz ? "$" + g.world_usd_oz.toLocaleString("en-US") + "/oz" : "—"} (${g.world_pct != null ? fmtSigned(g.world_pct, 2, "%") : "—"})</div>
        </div>
        <div class="gold-stat-card highlight">
          <div class="gold-stat-lbl">Chênh lệch SJC vs Thế giới</div>
          <div class="gold-stat-val" style="color:${spreadCol}">${g.bar_spread_m != null ? fmtSigned(g.bar_spread_m, 2, " tr") : "—"}</div>
          <div class="gold-stat-sub">${g.bar_spread_pct != null ? `Cao hơn ${fmtSigned(g.bar_spread_pct, 1, "%")} so với thế giới` : "—"}</div>
        </div>`;
    }

    const adv = $("#gold-advice-box");
    if (adv) {
      adv.className = `gold-advice-box ${g.tone || ""}`;
      adv.textContent = `Góc nhìn phòng hộ Vàng: ${g.advice || ""}`;
    }

    const ch = getChart("chart-gold-vn");
    const hist = g.history_sjc || [];
    if (ch && hist.length) {
      ch.setOption({
        tooltip: { ...baseTooltip(), valueFormatter: (v) => (v == null ? "—" : `${Number(v).toFixed(2)} tr/lượng`) },
        legend: { top: 0, textStyle: { color: t.muted, fontSize: 11 } },
        grid: { left: 48, right: 14, top: 28, bottom: 24 },
        xAxis: { type: "category", data: hist.map((p) => p.date.slice(5)), axisLabel: { color: t.muted, fontSize: 10 }, axisLine: { lineStyle: { color: t.grid } } },
        yAxis: {
          type: "value",
          scale: true,
          axisLabel: { color: t.muted, fontSize: 10, formatter: "{value}tr" },
          splitLine: { lineStyle: { color: t.grid } },
        },
        series: [
          {
            name: "SJC Bán ra",
            type: "line",
            smooth: 0.25,
            showSymbol: false,
            lineStyle: { width: 2.5, color: t.amber },
            itemStyle: { color: t.amber },
            areaStyle: {
              color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: t.amber + "33" }, { offset: 1, color: t.amber + "00" }] },
            },
            data: hist.map((p) => p.sell_m),
            markLine: g.world_vnd_m
              ? {
                  silent: true,
                  symbol: "none",
                  lineStyle: { type: "dashed", color: t.accent, width: 1.4 },
                  label: { formatter: `TG quy đổi ${g.world_vnd_m.toFixed(1)}tr`, color: t.accent, fontSize: 10, position: "insideEndTop" },
                  data: [{ yAxis: g.world_vnd_m }],
                }
              : undefined,
          },
          {
            name: "SJC Mua vào",
            type: "line",
            smooth: 0.25,
            showSymbol: false,
            lineStyle: { width: 1.8, type: "dotted", color: t.muted },
            itemStyle: { color: t.muted },
            data: hist.map((p) => p.buy_m),
          },
        ],
      }, true);
    }
  }

  // =========================================================================
  // 5. Morning Briefing Bullets & 6 Core Macro KPI Cards
  // =========================================================================

  function renderBriefing(data) {
    const list = $("#briefing-bullets");
    const bullets = data.briefing?.bullets || [];
    if (!list) return;
    if (!bullets.length) {
      list.innerHTML = `<div class="empty-state">Đang tổng hợp tín hiệu kinh tế vĩ mô...</div>`;
      return;
    }
    list.innerHTML = bullets
      .map(
        (b) => `
        <div class="brief-item">
          <span class="brief-tag ${esc(b.tone || "info")}">${esc(b.tag)}</span>
          <span>${esc(b.text)}</span>
        </div>`
      )
      .join("");
  }

  function renderKpis(data) {
    const k = data.kpis || {};
    const t = getThemeTokens();

    const cpi = k.us_cpi || {};
    $("#kpi-cpi-period").textContent = cpi.period || "—";
    $("#kpi-cpi-val").textContent = cpi.value != null ? `${cpi.value.toFixed(2)}%` : "—";
    const cpiDelta = $("#kpi-cpi-delta");
    if (cpi.delta != null) {
      cpiDelta.textContent = `${fmtSigned(cpi.delta, 2)}đ%`;
      cpiDelta.className = `delta-pill ${cpi.delta > 0 ? "up-bad" : "down-good"}`;
    }
    const momNote = cpi.mom != null ? `MoM (${cpi.mom_period || ""}): ${fmtSigned(cpi.mom, 2, "%")}` : "";
    $("#kpi-cpi-sub").textContent = `Core CPI: ${fmtNum(cpi.core_cpi, 2, "%")} · ${momNote || `PPI: ${fmtNum(cpi.ppi, 2, "%")}`}`;
    renderSparkline("spark-cpi", cpi.spark, t.rose, 2.0);

    const pce = k.us_core_pce || {};
    $("#kpi-pce-period").textContent = pce.period || "—";
    $("#kpi-pce-val").textContent = pce.value != null ? `${pce.value.toFixed(2)}%` : "—";
    const pceDelta = $("#kpi-pce-delta");
    if (pce.delta != null) {
      pceDelta.textContent = `${fmtSigned(pce.delta, 2)}đ%`;
      pceDelta.className = `delta-pill ${pce.delta > 0 ? "up-bad" : "down-good"}`;
    }
    $("#kpi-pce-sub").textContent = `PCE toàn phần: ${fmtNum(pce.headline_pce, 2, "%")} · MoM: ${fmtSigned(pce.mom, 2, "%")}`;
    renderSparkline("spark-pce", pce.spark, t.amber, 2.0);

    const upmi = k.us_pmi || {};
    const vpmi = k.vn_pmi || {};
    $("#kpi-pmi-period").textContent = upmi.period || vpmi.period || "—";
    const usPmiEl = $("#kpi-us-pmi-val");
    const vnPmiEl = $("#kpi-vn-pmi-val");
    usPmiEl.textContent = fmtNum(upmi.value, 1);
    usPmiEl.style.color = upmi.value >= 50 ? t.emerald : t.rose;
    vnPmiEl.textContent = fmtNum(vpmi.value, 1);
    vnPmiEl.style.color = vpmi.value >= 50 ? t.emerald : t.rose;
    $("#kpi-pmi-sub").textContent = `ISM Dịch vụ: ${fmtNum(upmi.services, 1)} · VN Δ: ${fmtSigned(vpmi.delta, 1)} điểm`;
    renderSparkline("spark-pmi", vpmi.spark, t.emerald, 50.0);

    const un = k.us_unemployment || {};
    $("#kpi-unemp-period").textContent = un.period || "—";
    $("#kpi-unemp-val").textContent = un.value != null ? `${un.value.toFixed(1)}%` : "—";
    const unDelta = $("#kpi-unemp-delta");
    if (un.delta != null) {
      unDelta.textContent = `${fmtSigned(un.delta, 1)}%`;
      unDelta.className = `delta-pill ${un.delta > 0 ? "up-bad" : "down-good"}`;
    }
    $("#kpi-unemp-sub").textContent = `Việc làm NFP tháng mới: ${un.nfp_k != null ? `${fmtSigned(un.nfp_k, 0)}K` : "—"}`;
    renderSparkline("spark-unemp", un.spark, t.violet);

    const fed = k.fed_rate || {};
    $("#kpi-fed-period").textContent = fed.date ? fed.date.slice(5) : "Live";
    $("#kpi-fed-val").textContent = fed.target_range || "—";
    const fedProb = $("#kpi-fed-prob");
    const nxt = fed.next_meeting;
    if (nxt) {
      if (nxt.prob_hike >= 50) {
        fedProb.textContent = `${Math.round(nxt.prob_hike)}% Nâng`;
        fedProb.className = "delta-pill danger";
      } else if (nxt.prob_cut >= 50) {
        fedProb.textContent = `${Math.round(nxt.prob_cut)}% Hạ`;
        fedProb.className = "delta-pill success";
      } else {
        fedProb.textContent = `${Math.round(nxt.prob_hold)}% Giữ`;
        fedProb.className = "delta-pill info";
      }
    }
    $("#kpi-fed-sub").textContent = `EFFR: ${fmtNum(fed.effr, 2, "%")} · SOFR: ${fmtNum(fed.sofr, 2, "%")}`;
    renderSparkline("spark-fed", fed.spark, t.accent);

    const fx = k.usdvnd || {};
    $("#kpi-fx-val").textContent = fmtVnd(fx.vcb_sell || fx.spot);
    const dxyPill = $("#kpi-fx-dxy");
    dxyPill.textContent = fx.dxy != null ? `DXY ${fx.dxy.toFixed(2)}` : "Live";
    dxyPill.className = `delta-pill ${fx.dxy_pct > 0 ? "warn" : "info"}`;
    const sbvPart = fx.sbv_central ? ` · SBV TT: ${fmtVnd(fx.sbv_central)}` : ` · Spot: ${fmtVnd(fx.spot)}`;
    $("#kpi-fx-sub").textContent = `VCB CK: ${fmtVnd(fx.vcb_transfer)}${sbvPart}`;
    renderSparkline("spark-fx", fx.spark, t.amber);
  }

  // =========================================================================
  // 6. Workspace 2: Inflation, PMI & Labor Charts
  // =========================================================================

  const INF_META = {
    us_cpi: { key: "cpi", label: "CPI Mỹ", colorKey: "accent" },
    us_core_cpi: { key: "core_cpi", label: "Core CPI Mỹ", colorKey: "violet" },
    us_core_pce: { key: "core_pce", label: "Core PCE Mỹ", colorKey: "rose" },
    us_pce: { key: "pce", label: "PCE Mỹ", colorKey: "amber" },
    us_ppi: { key: "ppi", label: "PPI Mỹ", colorKey: "emerald" },
  };

  function renderInflation() {
    if (!state.data) return;
    const t = getThemeTokens();
    const inf = state.data.inflation || {};
    const yearly = state.data.yearly || {};

    const rangeGroup = $("#inf-range-group");
    if (rangeGroup) rangeGroup.style.display = state.infMode === "yearly" ? "none" : "inline-flex";

    const tableMetricKey = state.infMetric === "all" ? "us_cpi" : state.infMetric;
    const tableMeta = INF_META[tableMetricKey] || INF_META.us_cpi;
    $("#inf-table-title").textContent = `Thống kê theo năm — ${tableMeta.label} (YoY %)`;

    const yrData = yearly[tableMetricKey] || { summary: [], years: [], months: [], matrix: {} };
    const tbody = $("#table-inf-yearly tbody");
    if (tbody) {
      tbody.innerHTML = (yrData.summary || [])
        .map(
          (r) => `
          <tr>
            <td><strong>${r.year}</strong></td>
            <td>${r.avg.toFixed(2)}%</td>
            <td style="color:${t.rose}">${r.max.toFixed(2)}%</td>
            <td style="color:${t.emerald}">${r.min.toFixed(2)}%</td>
            <td><strong>${r.latest.toFixed(2)}%</strong></td>
          </tr>`
        )
        .join("");
    }

    const vnCpi = inf.vn_cpi_latest;
    if (vnCpi) {
      $("#vn-cpi-period").textContent = vnCpi.period || "2026";
      $("#vn-cpi-metrics").innerHTML = `
        <div class="mini-stat">
          <div class="mini-stat-label">CPI cùng kỳ (YoY)</div>
          <div class="mini-stat-val" style="color:${t.rose}">${fmtNum(vnCpi.yoy, 2, "%")}</div>
        </div>
        <div class="mini-stat">
          <div class="mini-stat-label">Bình quân lũy kế</div>
          <div class="mini-stat-val" style="color:${t.amber}">${fmtNum(vnCpi.ytd_avg, 2, "%")}</div>
        </div>
        <div class="mini-stat">
          <div class="mini-stat-label">Lạm phát cơ bản</div>
          <div class="mini-stat-val" style="color:${t.accent}">${fmtNum(vnCpi.core_ytd, 2, "%")}</div>
        </div>`;
    }

    const ch = getChart("chart-inflation");
    if (!ch) return;

    if (state.infMode === "yearly") {
      const targetKey = state.infMetric === "all" ? "us_cpi" : state.infMetric;
      const mat = yearly[targetKey] || { years: [], months: [], matrix: {} };
      const yrs = mat.years || [];
      const series = yrs.map((yr, idx) => {
        const isLatest = idx === yrs.length - 1;
        const color = isLatest ? t.rose : YEAR_COLORS[idx % YEAR_COLORS.length];
        return {
          name: yr,
          type: "line",
          smooth: 0.25,
          connectNulls: true,
          symbol: isLatest ? "circle" : "none",
          symbolSize: isLatest ? 7 : 4,
          lineStyle: { width: isLatest ? 3.2 : 1.9, opacity: isLatest ? 1 : 0.72, color },
          itemStyle: { color },
          data: mat.matrix[yr] || [],
        };
      });
      if (series.length) {
        series[0].markLine = {
          silent: true,
          symbol: "none",
          lineStyle: { type: "dashed", color: t.emerald, width: 1.4 },
          label: { formatter: "Mục tiêu Fed 2.0%", color: t.emerald, fontSize: 11, position: "insideEndTop" },
          data: [{ yAxis: 2.0 }],
        };
      }
      ch.setOption({
        tooltip: { ...baseTooltip(), valueFormatter: (v) => (v == null ? "—" : `${Number(v).toFixed(2)}%`) },
        legend: { top: 0, textStyle: { color: t.muted, fontSize: 12 } },
        grid: { left: 46, right: 20, top: 38, bottom: 28 },
        xAxis: { type: "category", data: mat.months, axisLine: { lineStyle: { color: t.grid } }, axisLabel: { color: t.muted } },
        yAxis: { type: "value", axisLabel: { color: t.muted, formatter: "{value}%" }, splitLine: { lineStyle: { color: t.grid } } },
        series,
      }, true);
      return;
    }

    if (state.infMode === "mom") {
      const targetKey = state.infMetric === "all" ? "us_cpi" : state.infMetric;
      const meta = INF_META[targetKey] || INF_META.us_cpi;
      const raw = (inf[meta.key] || []).filter((p) => p.mom !== null).slice(-Math.min(state.infRange, 48));
      ch.setOption({
        tooltip: { ...baseTooltip(), valueFormatter: (v) => (v == null ? "—" : fmtSigned(v, 2, "%")) },
        legend: { top: 0, textStyle: { color: t.muted } },
        grid: { left: 48, right: 18, top: 36, bottom: 32 },
        xAxis: { type: "category", data: raw.map((p) => p.date), axisLine: { lineStyle: { color: t.grid } }, axisLabel: { color: t.muted, fontSize: 11 } },
        yAxis: { type: "value", axisLabel: { color: t.muted, formatter: "{value}%" }, splitLine: { lineStyle: { color: t.grid } } },
        series: [
          {
            name: `${meta.label} MoM (%)`,
            type: "bar",
            barMaxWidth: 22,
            data: raw.map((p) => ({
              value: p.mom,
              itemStyle: {
                color: p.mom >= 0.35 ? t.rose : p.mom >= 0 ? t.amber : t.emerald,
                borderRadius: p.mom >= 0 ? [4, 4, 0, 0] : [0, 0, 4, 4],
              },
            })),
          },
        ],
      }, true);
      return;
    }

    const selectedKeys = state.infMetric === "all" ? ["us_cpi", "us_core_cpi", "us_core_pce"] : [state.infMetric];
    const dateSet = new Set();
    selectedKeys.forEach((mk) => {
      (inf[INF_META[mk].key] || []).filter((p) => p.yoy !== null).slice(-state.infRange).forEach((p) => dateSet.add(p.date));
    });
    const dates = Array.from(dateSet).sort();

    const series = selectedKeys.map((mk, idx) => {
      const meta = INF_META[mk];
      const map = new Map((inf[meta.key] || []).map((p) => [p.date, p.yoy]));
      const color = t[meta.colorKey];
      const obj = {
        name: `${meta.label} (YoY)`,
        type: "line",
        smooth: 0.2,
        connectNulls: true,
        showSymbol: false,
        lineStyle: { width: idx === 0 ? 2.8 : 2.1, color },
        itemStyle: { color },
        data: dates.map((d) => (map.has(d) ? map.get(d) : null)),
      };
      if (selectedKeys.length === 1) {
        obj.areaStyle = {
          color: {
            type: "linear", x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [{ offset: 0, color: color + "38" }, { offset: 1, color: color + "00" }],
          },
        };
      }
      if (idx === 0) {
        obj.markLine = {
          silent: true,
          symbol: "none",
          lineStyle: { type: "dashed", color: t.emerald, width: 1.4 },
          label: { formatter: "Mục tiêu Fed 2.0%", color: t.emerald, fontSize: 11, position: "insideEndTop" },
          data: [{ yAxis: 2.0 }],
        };
      }
      return obj;
    });

    ch.setOption({
      tooltip: { ...baseTooltip(), valueFormatter: (v) => (v == null ? "—" : `${Number(v).toFixed(2)}%`) },
      legend: { top: 0, textStyle: { color: t.muted, fontSize: 12 } },
      grid: { left: 48, right: 18, top: 38, bottom: 32 },
      xAxis: { type: "category", data: dates, axisLine: { lineStyle: { color: t.grid } }, axisLabel: { color: t.muted, fontSize: 11 } },
      yAxis: { type: "value", axisLabel: { color: t.muted, formatter: "{value}%" }, splitLine: { lineStyle: { color: t.grid } } },
      series,
    }, true);
  }

  function renderPmi() {
    if (!state.data) return;
    const t = getThemeTokens();
    const pmi = state.data.pmi || {};
    const yearly = state.data.yearly || {};

    const linksWrap = $("#pmi-official-links");
    if (linksWrap) {
      const links = pmi.official_links || [];
      if (links.length) {
        linksWrap.innerHTML =
          `<span>Báo cáo S&amp;P Global mới nhất:</span>` +
          links
            .slice(0, 3)
            .map((l) => `<a class="ext-pill" href="${safeHref(l.url)}" target="_blank" rel="noopener noreferrer">${esc(l.title.replace("S&P Global ", ""))} ↗</a>`)
            .join("");
      } else {
        linksWrap.textContent = "Nguồn: ISM (Mỹ) & S&P Global (Việt Nam)";
      }
    }

    const ch = getChart("chart-pmi");
    if (!ch) return;

    if (state.pmiMode === "yearly_vn" || state.pmiMode === "yearly_us") {
      const key = state.pmiMode === "yearly_vn" ? "vn_mfg_pmi" : "us_ism_mfg";
      const mat = yearly[key] || { years: [], months: [], matrix: {} };
      const yrs = mat.years || [];
      const series = yrs.map((yr, idx) => {
        const isLatest = idx === yrs.length - 1;
        const color = isLatest ? t.emerald : YEAR_COLORS[idx % YEAR_COLORS.length];
        return {
          name: yr,
          type: "line",
          smooth: 0.25,
          symbol: isLatest ? "circle" : "none",
          symbolSize: isLatest ? 7 : 4,
          lineStyle: { width: isLatest ? 3 : 1.9, opacity: isLatest ? 1 : 0.72, color },
          itemStyle: { color },
          data: mat.matrix[yr] || [],
        };
      });
      if (series.length) {
        series[0].markLine = {
          silent: true,
          symbol: "none",
          lineStyle: { type: "dashed", color: t.amber, width: 1.5 },
          label: { formatter: "Ngưỡng 50", color: t.amber, fontSize: 11, position: "insideEndTop" },
          data: [{ yAxis: 50 }],
        };
      }
      ch.setOption({
        tooltip: baseTooltip(),
        legend: { top: 0, textStyle: { color: t.muted, fontSize: 12 } },
        grid: { left: 42, right: 16, top: 36, bottom: 26 },
        xAxis: { type: "category", data: mat.months, axisLabel: { color: t.muted }, axisLine: { lineStyle: { color: t.grid } } },
        yAxis: { type: "value", min: 43, max: 60, axisLabel: { color: t.muted }, splitLine: { lineStyle: { color: t.grid } } },
        series,
      }, true);
      return;
    }

    const dateSet = new Set();
    ["us_ism_mfg", "us_ism_svc", "vn_mfg_pmi"].forEach((k) => {
      (pmi[k] || []).slice(-36).forEach((p) => dateSet.add(p.date));
    });
    const dates = Array.from(dateSet).sort();
    const mkMap = (arr) => new Map((arr || []).map((p) => [p.date, p.value]));
    const mUsM = mkMap(pmi.us_ism_mfg);
    const mUsS = mkMap(pmi.us_ism_svc);
    const mVn = mkMap(pmi.vn_mfg_pmi);

    ch.setOption({
      tooltip: baseTooltip(),
      legend: { top: 0, textStyle: { color: t.muted, fontSize: 11.5 } },
      grid: { left: 42, right: 16, top: 36, bottom: 26 },
      xAxis: { type: "category", data: dates, axisLabel: { color: t.muted, fontSize: 11 }, axisLine: { lineStyle: { color: t.grid } } },
      yAxis: { type: "value", min: 43, max: 60, axisLabel: { color: t.muted }, splitLine: { lineStyle: { color: t.grid } } },
      series: [
        {
          name: "VN PMI Sản xuất",
          type: "line",
          smooth: 0.25,
          connectNulls: true,
          showSymbol: false,
          lineStyle: { width: 2.8, color: t.emerald },
          itemStyle: { color: t.emerald },
          data: dates.map((d) => mVn.get(d) ?? null),
          markLine: {
            silent: true,
            symbol: "none",
            lineStyle: { type: "dashed", color: t.amber, width: 1.5 },
            label: { formatter: "50 (Mở rộng)", color: t.amber, fontSize: 11, position: "insideEndTop" },
            data: [{ yAxis: 50 }],
          },
        },
        {
          name: "Mỹ ISM Sản xuất",
          type: "line",
          smooth: 0.25,
          connectNulls: true,
          showSymbol: false,
          lineStyle: { width: 2.4, color: t.accent },
          itemStyle: { color: t.accent },
          data: dates.map((d) => mUsM.get(d) ?? null),
        },
        {
          name: "Mỹ ISM Dịch vụ",
          type: "line",
          smooth: 0.25,
          connectNulls: true,
          showSymbol: false,
          lineStyle: { width: 1.8, type: "dotted", color: t.violet },
          itemStyle: { color: t.violet },
          data: dates.map((d) => mUsS.get(d) ?? null),
        },
      ],
    }, true);
  }

  function renderLabor() {
    if (!state.data) return;
    const ch = getChart("chart-labor");
    if (!ch) return;
    const t = getThemeTokens();
    const labor = state.data.labor || {};
    const yearly = state.data.yearly || {};

    if (state.laborMode === "yearly") {
      const mat = yearly.us_unemployment || { years: [], months: [], matrix: {} };
      const yrs = mat.years || [];
      const series = yrs.map((yr, idx) => {
        const isLatest = idx === yrs.length - 1;
        const color = isLatest ? t.violet : YEAR_COLORS[idx % YEAR_COLORS.length];
        return {
          name: yr,
          type: "line",
          smooth: 0.25,
          symbol: isLatest ? "circle" : "none",
          symbolSize: isLatest ? 7 : 4,
          lineStyle: { width: isLatest ? 3 : 1.9, opacity: isLatest ? 1 : 0.72, color },
          itemStyle: { color },
          data: mat.matrix[yr] || [],
        };
      });
      ch.setOption({
        tooltip: { ...baseTooltip(), valueFormatter: (v) => (v == null ? "—" : `${v}%`) },
        legend: { top: 0, textStyle: { color: t.muted, fontSize: 12 } },
        grid: { left: 46, right: 16, top: 36, bottom: 26 },
        xAxis: { type: "category", data: mat.months, axisLabel: { color: t.muted }, axisLine: { lineStyle: { color: t.grid } } },
        yAxis: { type: "value", axisLabel: { color: t.muted, formatter: "{value}%" }, splitLine: { lineStyle: { color: t.grid } } },
        series,
      }, true);
      return;
    }

    const unemp = (labor.unemployment || []).slice(-36);
    const nfpMap = new Map((labor.nfp || []).map((p) => [p.date, p.change_k]));
    const dates = unemp.map((p) => p.date);

    ch.setOption({
      tooltip: baseTooltip(),
      legend: { top: 0, type: "scroll", textStyle: { color: t.muted, fontSize: 11.5 } },
      grid: { left: 46, right: 48, top: 52, bottom: 26 },
      xAxis: { type: "category", data: dates, axisLabel: { color: t.muted, fontSize: 11 }, axisLine: { lineStyle: { color: t.grid } } },
      yAxis: [
        {
          type: "value",
          name: "Thất nghiệp %",
          nameGap: 10,
          nameTextStyle: { color: t.muted, fontSize: 10 },
          min: (v) => Math.max(0, Math.floor(v.min - 0.6)),
          max: (v) => Math.ceil(v.max + 0.6),
          axisLabel: { color: t.muted, formatter: "{value}%" },
          splitLine: { lineStyle: { color: t.grid } },
        },
        {
          type: "value",
          name: "NFP (K)",
          nameGap: 10,
          nameTextStyle: { color: t.muted, fontSize: 10 },
          axisLabel: { color: t.muted, formatter: "{value}K" },
          splitLine: { show: false },
        },
      ],
      series: [
        {
          name: "Việc làm NFP (+K)",
          type: "bar",
          yAxisIndex: 1,
          barMaxWidth: 14,
          data: dates.map((d) => {
            const v = nfpMap.get(d) ?? null;
            return {
              value: v,
              itemStyle: { color: v != null && v < 0 ? t.rose + "99" : t.accent + "55", borderRadius: [3, 3, 0, 0] },
            };
          }),
        },
        {
          name: "Tỉ lệ Thất nghiệp (%)",
          type: "line",
          yAxisIndex: 0,
          smooth: 0.25,
          showSymbol: false,
          lineStyle: { width: 2.8, color: t.violet },
          itemStyle: { color: t.violet },
          data: unemp.map((p) => p.value),
        },
      ],
    }, true);
  }

  // =========================================================================
  // 7. Workspace 3: FedWatch & Yield Curve
  // =========================================================================

  function renderFedWatch() {
    if (!state.data) return;
    const t = getThemeTokens();
    const fw = state.data.fedwatch || {};
    const ny = state.data.rates?.nyfed || {};
    const latest = ny.latest || {};
    const contracts = fw.contracts || [];

    $("#fedwatch-subtitle").textContent =
      `Khung mục tiêu hiện tại: ${fmtNum(latest.target_low, 2)}%–${fmtNum(latest.target_high, 2)}% · ` +
      `EFFR: ${fmtNum(latest.effr, 2)}% · SOFR: ${fmtNum(latest.sofr, 2)}% (${latest.date || ""})`;

    const cardsEl = $("#fedwatch-cards");
    if (cardsEl) {
      const picks = contracts.length <= 4
        ? contracts
        : [contracts[0], contracts[1], contracts[Math.min(3, contracts.length - 1)], contracts[contracts.length - 1]];
      const uniquePicks = [];
      const seenYm = new Set();
      picks.forEach((c) => {
        if (c && !seenYm.has(c.ym)) { seenYm.add(c.ym); uniquePicks.push(c); }
      });

      cardsEl.innerHTML = uniquePicks
        .map((c) => {
          const biasCls = c.prob_hike >= 50 ? "hike" : c.prob_cut >= 50 ? "cut" : "hold";
          const bpsTxt = `${c.delta_bps >= 0 ? "+" : ""}${Math.round(c.delta_bps)}bps`;
          return `
            <div class="fw-card">
              <div class="fw-card-head">
                <span class="fw-month">${esc(c.label)}</span>
                <span class="fw-implied">${c.implied_rate.toFixed(2)}%</span>
              </div>
              <div class="fw-bias ${biasCls}">
                <span>${esc(c.bias)}</span>
                <span class="mono">${bpsTxt}</span>
              </div>
              <div class="prob-bar">
                <div class="prob-seg cut" style="width:${c.prob_cut}%"></div>
                <div class="prob-seg hold" style="width:${c.prob_hold}%"></div>
                <div class="prob-seg hike" style="width:${c.prob_hike}%"></div>
              </div>
              <div class="fw-legend">
                <span style="color:${t.emerald}">Hạ ${Math.round(c.prob_cut)}%</span>
                <span>Giữ ${Math.round(c.prob_hold)}%</span>
                <span style="color:${t.rose}">Nâng ${Math.round(c.prob_hike)}%</span>
              </div>
            </div>`;
        })
        .join("");
    }

    const tbody = $("#table-fed-changes tbody");
    if (tbody) {
      tbody.innerHTML = (ny.changes || [])
        .map((ch) => {
          const isHike = ch.delta_bps > 0;
          const color = isHike ? t.rose : t.emerald;
          return `
            <tr>
              <td>${esc(ch.date)}</td>
              <td style="color:${color};font-weight:600">${esc(ch.action)}</td>
              <td style="color:${color};font-weight:700">${isHike ? "+" : ""}${ch.delta_bps} bps</td>
              <td>${esc(ch.from_range)}</td>
              <td><strong>${esc(ch.to_range)}</strong></td>
            </tr>`;
        })
        .join("");
    }

    const ch = getChart("chart-fed-path");
    if (!ch) return;

    if (state.fedMode === "probs") {
      ch.setOption({
        tooltip: { ...baseTooltip(), valueFormatter: (v) => `${Number(v).toFixed(1)}%` },
        legend: { top: 0, textStyle: { color: t.muted, fontSize: 12 } },
        grid: { left: 46, right: 18, top: 36, bottom: 28 },
        xAxis: { type: "category", data: contracts.map((c) => c.label), axisLabel: { color: t.muted, fontSize: 11 }, axisLine: { lineStyle: { color: t.grid } } },
        yAxis: { type: "value", max: 100, axisLabel: { color: t.muted, formatter: "{value}%" }, splitLine: { lineStyle: { color: t.grid } } },
        series: [
          { name: "Xác suất Hạ lãi suất", type: "bar", stack: "prob", barMaxWidth: 34, itemStyle: { color: t.emerald }, data: contracts.map((c) => c.prob_cut) },
          { name: "Xác suất Giữ nguyên", type: "bar", stack: "prob", barMaxWidth: 34, itemStyle: { color: "#64748b" }, data: contracts.map((c) => c.prob_hold) },
          { name: "Xác suất Nâng lãi suất", type: "bar", stack: "prob", barMaxWidth: 34, itemStyle: { color: t.rose, borderRadius: [4, 4, 0, 0] }, data: contracts.map((c) => c.prob_hike) },
        ],
      }, true);
      return;
    }

    const hist = (ny.monthly || []).slice(-30);
    const labels = [...hist.map((p) => p.date), ...contracts.map((c) => c.ym)];
    const histData = hist.map((p) => p.effr);
    const forwardData = new Array(Math.max(0, hist.length - 1)).fill(null);
    if (hist.length) forwardData.push(hist[hist.length - 1].effr);
    contracts.forEach((c) => forwardData.push(c.implied_rate));

    ch.setOption({
      tooltip: { ...baseTooltip(), valueFormatter: (v) => (v == null ? "—" : `${Number(v).toFixed(2)}%`) },
      legend: { top: 0, textStyle: { color: t.muted, fontSize: 12 } },
      grid: { left: 46, right: 18, top: 36, bottom: 28 },
      xAxis: { type: "category", data: labels, axisLabel: { color: t.muted, fontSize: 11 }, axisLine: { lineStyle: { color: t.grid } } },
      yAxis: {
        type: "value",
        min: (v) => Math.max(0, Math.floor((v.min - 0.5) * 2) / 2),
        max: (v) => Math.ceil((v.max + 0.5) * 2) / 2,
        axisLabel: { color: t.muted, formatter: "{value}%" },
        splitLine: { lineStyle: { color: t.grid } },
      },
      series: [
        {
          name: "Lãi suất EFFR Thực tế",
          type: "line",
          step: "end",
          showSymbol: false,
          lineStyle: { width: 2.8, color: t.accent },
          itemStyle: { color: t.accent },
          areaStyle: {
            color: {
              type: "linear", x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [{ offset: 0, color: t.accent + "33" }, { offset: 1, color: t.accent + "00" }],
            },
          },
          data: [...histData, ...new Array(contracts.length).fill(null)],
        },
        {
          name: "Kỳ vọng Thị trường (Fed Futures ZQ)",
          type: "line",
          smooth: 0.2,
          symbol: "circle",
          symbolSize: 6,
          lineStyle: { width: 2.6, type: "dashed", color: t.amber },
          itemStyle: { color: t.amber },
          data: forwardData,
        },
      ],
    }, true);
  }

  function renderYieldCurve() {
    if (!state.data) return;
    const t = getThemeTokens();
    const yc = state.data.rates?.yield_curve || {};
    const latest = yc.latest || {};
    const yields = latest.yields || {};
    const spread102 = latest.spread_10y_2y;

    const metricsEl = $("#yc-metrics");
    if (metricsEl) {
      const spColor = spread102 != null && spread102 < 0 ? t.rose : t.emerald;
      const spStatus = spread102 != null && spread102 < 0 ? "Đảo ngược" : "Bình thường";
      metricsEl.innerHTML = `
        <div class="mini-stat"><div class="mini-stat-label">US 2Y Yield</div><div class="mini-stat-val">${fmtNum(yields["2Y"], 2, "%")}</div></div>
        <div class="mini-stat"><div class="mini-stat-label">US 10Y Yield</div><div class="mini-stat-val" style="color:${t.accent}">${fmtNum(yields["10Y"], 2, "%")}</div></div>
        <div class="mini-stat"><div class="mini-stat-label">Spread 10Y–2Y</div><div class="mini-stat-val" style="color:${spColor}">${fmtSigned(spread102, 2, "%")}</div></div>
        <div class="mini-stat"><div class="mini-stat-label">Trạng thái (${latest.date ? latest.date.slice(5) : "—"})</div><div class="mini-stat-val" style="font-size:13px;color:${spColor}">${spStatus}</div></div>`;
    }

    const ch = getChart("chart-yield-curve");
    if (!ch) return;

    if (state.ycMode === "spread") {
      const hist = yc.history || [];
      ch.setOption({
        tooltip: { ...baseTooltip(), valueFormatter: (v) => (v == null ? "—" : fmtSigned(v, 2, "%")) },
        legend: { top: 0, textStyle: { color: t.muted, fontSize: 12 } },
        grid: { left: 46, right: 16, top: 36, bottom: 26 },
        xAxis: { type: "category", data: hist.map((p) => p.date), axisLabel: { color: t.muted, fontSize: 11 }, axisLine: { lineStyle: { color: t.grid } } },
        yAxis: { type: "value", axisLabel: { color: t.muted, formatter: "{value}%" }, splitLine: { lineStyle: { color: t.grid } } },
        series: [
          {
            name: "Spread 10Y – 2Y",
            type: "line",
            showSymbol: false,
            lineStyle: { width: 2.5, color: t.accent },
            itemStyle: { color: t.accent },
            areaStyle: { color: t.accent + "22" },
            data: hist.map((p) => p.spread_10y_2y),
            markLine: {
              silent: true,
              symbol: "none",
              lineStyle: { type: "dashed", color: t.rose, width: 1.4 },
              label: { formatter: "0% (Đảo ngược)", color: t.rose, fontSize: 11, position: "insideEndTop" },
              data: [{ yAxis: 0 }],
            },
          },
          {
            name: "Spread 10Y – 3M",
            type: "line",
            showSymbol: false,
            lineStyle: { width: 1.8, type: "dashed", color: t.violet },
            itemStyle: { color: t.violet },
            data: hist.map((p) => p.spread_10y_3m),
          },
        ],
      }, true);
      return;
    }

    const tenors = yc.tenors || [];
    const oneM = yc.one_month_ago || {};
    const oneY = yc.one_year_ago || {};
    ch.setOption({
      tooltip: { ...baseTooltip(), valueFormatter: (v) => (v == null ? "—" : `${Number(v).toFixed(2)}%`) },
      legend: { top: 0, textStyle: { color: t.muted, fontSize: 11.5 } },
      grid: { left: 46, right: 18, top: 36, bottom: 26 },
      xAxis: { type: "category", data: tenors, axisLabel: { color: t.muted, fontSize: 11 }, axisLine: { lineStyle: { color: t.grid } } },
      yAxis: {
        type: "value",
        min: (v) => Math.max(0, Math.floor((v.min - 0.3) * 2) / 2),
        max: (v) => Math.ceil((v.max + 0.3) * 2) / 2,
        axisLabel: { color: t.muted, formatter: "{value}%" },
        splitLine: { lineStyle: { color: t.grid } },
      },
      series: [
        { name: `Hôm nay (${latest.date || ""})`, type: "line", smooth: 0.25, symbol: "circle", symbolSize: 6, lineStyle: { width: 3, color: t.accent }, itemStyle: { color: t.accent }, data: tenors.map((tn) => yields[tn] ?? null) },
        { name: `1 tháng trước (${oneM.date || ""})`, type: "line", smooth: 0.25, showSymbol: false, lineStyle: { width: 2, type: "dashed", color: t.amber }, itemStyle: { color: t.amber }, data: tenors.map((tn) => (oneM.yields || {})[tn] ?? null) },
        { name: `1 năm trước (${oneY.date || ""})`, type: "line", smooth: 0.25, showSymbol: false, lineStyle: { width: 1.8, type: "dotted", color: t.muted }, itemStyle: { color: t.muted }, data: tenors.map((tn) => (oneY.yields || {})[tn] ?? null) },
      ],
    }, true);
  }

  // =========================================================================
  // 7b. Vietnam Interbank Interest Rates (ON · 1W · 2W), Policy & Deposit Rates
  // =========================================================================

  function renderVnInterbank() {
    if (!state.data) return;
    const sec = $("#sec-vn-interbank");
    const ib = state.data.vn_money_fx?.interbank || { available: false };
    if (!sec) return;
    if (!ib.available) {
      sec.classList.add("hidden");
      return;
    }
    sec.classList.remove("hidden");

    const t = getThemeTokens();
    const lt = ib.latest || {};
    const effr = state.data.rates?.nyfed?.latest?.effr;
    const spreadUsd = lt.on != null && effr != null ? lt.on - effr : null;

    if ($("#vn-ib-updated-tag")) {
      $("#vn-ib-updated-tag").textContent = `${ib.updated_at || lt.date || ""} · WiData Live`;
    }

    const kpiEl = $("#vn-ib-kpis");
    if (kpiEl) {
      const onTone = lt.on >= 5.5 ? "rose" : lt.on >= 3.8 ? "amber" : "emerald";
      const onStatus = lt.on >= 5.5 ? "Thanh khoản căng" : lt.on >= 3.8 ? "Trung tính · Bảo vệ tỷ giá" : "Thanh khoản dồi dào";
      const spTone = spreadUsd == null ? "accent" : spreadUsd >= 0.5 ? "emerald" : spreadUsd <= -1.0 ? "rose" : "amber";
      const spDesc =
        spreadUsd == null
          ? "So với Fed EFFR"
          : spreadUsd >= 0
          ? "VND cao hơn USD · Giảm áp lực tỷ giá"
          : "VND thấp hơn USD · Tạo áp lực lên tỷ giá";

      kpiEl.innerHTML = `
        <div class="vn-tier-card ${onTone}">
          <div class="vn-tier-lbl">Qua đêm (ON)</div>
          <div class="vn-tier-val" style="color:${t[onTone]}">${fmtNum(lt.on, 2, "%")}</div>
          <div class="vn-tier-sub">${onStatus}${lt.delta_on_1d != null ? ` · 1p ${fmtSigned(lt.delta_on_1d, 2, "%")}` : ""}</div>
        </div>
        <div class="vn-tier-card accent">
          <div class="vn-tier-lbl">Kỳ hạn 1 Tuần (1W)</div>
          <div class="vn-tier-val" style="color:${t.accent}">${fmtNum(lt.w1, 2, "%")}</div>
          <div class="vn-tier-sub">${lt.delta_on_5d != null ? `ON 5 phiên: ${fmtSigned(lt.delta_on_5d, 2, "%")}` : "Thị trường liên ngân hàng"}</div>
        </div>
        <div class="vn-tier-card violet">
          <div class="vn-tier-lbl">Kỳ hạn 2 Tuần (2W)</div>
          <div class="vn-tier-val" style="color:${t.violet}">${fmtNum(lt.w2, 2, "%")}</div>
          <div class="vn-tier-sub">Kỳ hạn ngắn chuẩn OMO</div>
        </div>
        <div class="vn-tier-card ${spTone}">
          <div class="vn-tier-lbl">VND (ON) − USD (EFFR)</div>
          <div class="vn-tier-val" style="color:${t[spTone]}">${spreadUsd != null ? fmtSigned(spreadUsd, 2, "%") : "—"}</div>
          <div class="vn-tier-sub">${spDesc}</div>
        </div>
        <div class="vn-tier-card amber">
          <div class="vn-tier-lbl">NHNN &amp; Huy động 13T</div>
          <div class="vn-tier-val" style="font-size:15.5px;color:${t.amber}">TCV ${fmtNum(lt.policy_refinancing, 1, "%")} · CK ${fmtNum(lt.policy_discount, 1, "%")}</div>
          <div class="vn-tier-sub">Gửi NHTM lớn: 1-3T ${fmtNum(lt.deposit_1_3m, 2, "%")} · 13T ${fmtNum(lt.deposit_13m, 2, "%")}</div>
        </div>`;
    }

    const ch = getChart("chart-vn-interbank");
    if (!ch) return;
    const isNarrow = window.innerWidth < 560;

    if (state.vnIbMode === "deposit") {
      $("#vn-ib-chart-title").textContent = "Lãi suất Huy động NHTM Lớn (MBB · ACB · TCB · VPB)";
      $("#vn-ib-chart-sub").textContent = "Theo dõi xu hướng mặt bằng lãi suất đầu vào thị trường 1 (dân cư) ở 3 nhóm kỳ hạn chính";
      const dep = (ib.deposit_series || []).slice(-state.vnIbRange);
      ch.setOption(
        {
          tooltip: { ...baseTooltip(), valueFormatter: (v) => (v == null ? "—" : `${Number(v).toFixed(2)}%`) },
          legend: { top: 0, type: "scroll", textStyle: { color: t.muted, fontSize: isNarrow ? 11 : 12 } },
          grid: { left: 46, right: 18, top: 44, bottom: 28 },
          xAxis: { type: "category", data: dep.map((p) => p.date), axisLabel: { color: t.muted, fontSize: 11 }, axisLine: { lineStyle: { color: t.grid } } },
          yAxis: { type: "value", scale: true, axisLabel: { color: t.muted, formatter: "{value}%" }, splitLine: { lineStyle: { color: t.grid } } },
          series: [
            { name: "Kỳ hạn 13 tháng", type: "line", smooth: 0.2, showSymbol: false, lineStyle: { width: 2.6, color: t.rose }, itemStyle: { color: t.rose }, data: dep.map((p) => p.m13) },
            { name: "Kỳ hạn 6–9 tháng", type: "line", smooth: 0.2, showSymbol: false, lineStyle: { width: 2.3, color: t.amber }, itemStyle: { color: t.amber }, data: dep.map((p) => p.m6_9) },
            { name: "Kỳ hạn 1–3 tháng", type: "line", smooth: 0.2, showSymbol: false, lineStyle: { width: 2.2, color: t.accent }, itemStyle: { color: t.accent }, data: dep.map((p) => p.m1_3) },
          ],
        },
        true
      );
      return;
    }

    const hist = (ib.series || []).slice(-state.vnIbRange);
    const dates = hist.map((p) => p.date);

    if (state.vnIbMode === "spread_fed") {
      $("#vn-ib-chart-title").textContent = "Đối chiếu Lãi suất Qua đêm VN (ON) vs Fed (EFFR) & Chênh lệch VND–USD";
      $("#vn-ib-chart-sub").textContent = "Khi thanh cột chênh lệch (VND − USD) âm sâu, dòng vốn có xu hướng găm USD làm tăng áp lực lên tỷ giá USD/VND";
      const nyMonthly = state.data.rates?.nyfed?.monthly || [];
      const effrByYm = new Map(nyMonthly.map((p) => [p.date, p.effr]));
      const fallbackEffr = effr ?? (nyMonthly.length ? nyMonthly[nyMonthly.length - 1].effr : null);
      const effrAligned = dates.map((d) => effrByYm.get(d.slice(0, 7)) ?? fallbackEffr);
      const spreadData = hist.map((p, idx) => {
        const ef = effrAligned[idx];
        const v = p.on != null && ef != null ? Number((p.on - ef).toFixed(2)) : null;
        return { value: v, itemStyle: { color: v != null && v >= 0 ? t.emerald + "70" : t.rose + "70" } };
      });
      ch.setOption(
        {
          tooltip: { ...baseTooltip(), valueFormatter: (v) => (v == null ? "—" : `${Number(v).toFixed(2)}%`) },
          legend: { top: 0, type: "scroll", textStyle: { color: t.muted, fontSize: isNarrow ? 11 : 12 } },
          grid: { left: 48, right: 18, top: 44, bottom: 28 },
          xAxis: { type: "category", data: dates, axisLabel: { color: t.muted, fontSize: 11 }, axisLine: { lineStyle: { color: t.grid } } },
          yAxis: { type: "value", axisLabel: { color: t.muted, formatter: "{value}%" }, splitLine: { lineStyle: { color: t.grid } } },
          series: [
            { name: "Chênh lệch VND (ON) − USD (EFFR)", type: "bar", barMaxWidth: 10, data: spreadData },
            { name: "VN Qua đêm (ON)", type: "line", smooth: 0.15, showSymbol: false, lineStyle: { width: 2.6, color: t.accent }, itemStyle: { color: t.accent }, data: hist.map((p) => p.on) },
            { name: "Mỹ Fed EFFR", type: "line", step: "end", showSymbol: false, lineStyle: { width: 2.2, type: "dashed", color: t.amber }, itemStyle: { color: t.amber }, data: effrAligned },
          ],
        },
        true
      );
      return;
    }

    $("#vn-ib-chart-title").textContent = "Diễn biến Lãi suất Liên ngân hàng VND (Qua đêm · 1 Tuần · 2 Tuần)";
    $("#vn-ib-chart-sub").textContent = "Lãi suất qua đêm tăng đột biến thường phản ánh thanh khoản VND căng thẳng hoặc NHNN hút ròng bảo vệ tỷ giá";
    const refRate = lt.policy_refinancing || 4.5;
    ch.setOption(
      {
        tooltip: { ...baseTooltip(), valueFormatter: (v) => (v == null ? "—" : `${Number(v).toFixed(2)}%`) },
        legend: { top: 0, type: "scroll", textStyle: { color: t.muted, fontSize: isNarrow ? 11 : 12 } },
        grid: { left: 46, right: 18, top: 44, bottom: 28 },
        xAxis: { type: "category", data: dates, axisLabel: { color: t.muted, fontSize: 11 }, axisLine: { lineStyle: { color: t.grid } } },
        yAxis: { type: "value", min: 0, axisLabel: { color: t.muted, formatter: "{value}%" }, splitLine: { lineStyle: { color: t.grid } } },
        series: [
          {
            name: "Qua đêm (ON)",
            type: "line",
            smooth: 0.15,
            showSymbol: false,
            lineStyle: { width: 2.8, color: t.rose },
            itemStyle: { color: t.rose },
            areaStyle: {
              color: {
                type: "linear", x: 0, y: 0, x2: 0, y2: 1,
                colorStops: [{ offset: 0, color: t.rose + "2a" }, { offset: 1, color: t.rose + "00" }],
              },
            },
            data: hist.map((p) => p.on),
            markLine: {
              silent: true,
              symbol: "none",
              lineStyle: { type: "dashed", color: t.amber, width: 1.4 },
              label: { formatter: `Tái cấp vốn NHNN ${refRate}%`, color: t.amber, fontSize: 10.5, position: "insideEndTop" },
              data: [{ yAxis: refRate }],
            },
          },
          { name: "Kỳ hạn 1 Tuần (1W)", type: "line", smooth: 0.15, showSymbol: false, lineStyle: { width: 2.2, color: t.accent }, itemStyle: { color: t.accent }, data: hist.map((p) => p.w1) },
          { name: "Kỳ hạn 2 Tuần (2W)", type: "line", smooth: 0.15, showSymbol: false, lineStyle: { width: 1.9, type: "dashed", color: t.violet }, itemStyle: { color: t.violet }, data: hist.map((p) => p.w2) },
        ],
      },
      true
    );
  }

  // =========================================================================
  // 8a. 3-Tier Vietnam FX Chart: Central (SBV) · Commercial (Bank) · Free (Black Market)
  // =========================================================================

  function renderVnFxTiers() {
    if (!state.data) return;
    const sec = $("#sec-vn-fx-tiers");
    const ft = state.data.vn_money_fx?.fx_tiers || { available: false };
    if (!sec) return;
    if (!ft.available) {
      sec.classList.add("hidden");
      return;
    }
    sec.classList.remove("hidden");

    const t = getThemeTokens();
    const lt = ft.latest || {};
    if ($("#vn-fxtier-updated-tag")) {
      $("#vn-fxtier-updated-tag").textContent = `${ft.updated_at || lt.date || ""} · WiData & VCB Live`;
    }

    const kpiEl = $("#vn-fxtier-kpis");
    if (kpiEl) {
      const spPct = lt.spread_free_bank_pct;
      const spTone = spPct == null ? "amber" : spPct >= 2.5 ? "rose" : spPct >= 1.2 ? "amber" : "emerald";
      const spStatus = spPct == null ? "—" : spPct >= 2.5 ? "Căng thẳng đầu cơ" : spPct >= 1.2 ? "Nóng nhẹ · Theo dõi" : "Ổn định";
      kpiEl.innerHTML = `
        <div class="vn-tier-card emerald">
          <div class="vn-tier-lbl">Tỷ giá Trung tâm (SBV)</div>
          <div class="vn-tier-val" style="color:${t.emerald}">${fmtVnd(lt.central)}</div>
          <div class="vn-tier-sub">Trần +5%: ${fmtVnd(lt.ceiling)} · Sàn -5%: ${fmtVnd(lt.floor)}</div>
        </div>
        <div class="vn-tier-card accent">
          <div class="vn-tier-lbl">Thương mại (NHTM / VCB Bán)</div>
          <div class="vn-tier-val" style="color:${t.accent}">${fmtVnd(lt.commercial_sell)}</div>
          <div class="vn-tier-sub">${lt.gap_to_ceiling != null ? `Cách trần SBV: ${fmtVnd(lt.gap_to_ceiling)} (${lt.gap_to_ceiling_pct?.toFixed(2)}%)` : "Giá bán niêm yết NHTM"}</div>
        </div>
        <div class="vn-tier-card rose">
          <div class="vn-tier-lbl">Tự do / Chợ đen (Bán ra)</div>
          <div class="vn-tier-val" style="color:${t.rose}">${fmtVnd(lt.free_sell)}</div>
          <div class="vn-tier-sub">${lt.free_buy ? `Mua vào tự do: ${fmtVnd(lt.free_buy)}` : "Giá bán thị trường tự do"}</div>
        </div>
        <div class="vn-tier-card ${spTone}">
          <div class="vn-tier-lbl">Độ vênh Chợ đen − NHTM</div>
          <div class="vn-tier-val" style="color:${t[spTone]}">${lt.spread_free_bank != null ? `${lt.spread_free_bank >= 0 ? "+" : ""}${Math.round(lt.spread_free_bank).toLocaleString("vi-VN")}₫` : "—"}</div>
          <div class="vn-tier-sub">${spPct != null ? `${fmtSigned(spPct, 2, "%")} · ${spStatus}` : "—"}</div>
        </div>`;
    }

    const ch = getChart("chart-vn-fx-tiers");
    if (!ch) return;
    const isNarrow = window.innerWidth < 560;
    const hist = (ft.series || []).slice(-state.fxTierRange);
    const dates = hist.map((p) => p.date);

    if (state.fxTierMode === "spread") {
      $("#vn-fxtier-chart-title").textContent = "Độ vênh Tỷ giá Chợ đen (Tự do) so với Ngân hàng Thương mại (VND/USD)";
      $("#vn-fxtier-chart-sub").textContent = "Đo lường sức nóng găm giữ USD ngoài thị trường chính thức — ngưỡng cảnh báo khi chênh lệch vượt +500₫ (+2%)";
      ch.setOption(
        {
          tooltip: {
            ...baseTooltip(),
            valueFormatter: (v) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${Math.round(Number(v)).toLocaleString("vi-VN")}₫`),
          },
          legend: { top: 0, type: "scroll", textStyle: { color: t.muted, fontSize: isNarrow ? 11 : 12 } },
          grid: { left: isNarrow ? 54 : 62, right: 18, top: 44, bottom: 28 },
          xAxis: { type: "category", data: dates, axisLabel: { color: t.muted, fontSize: 11 }, axisLine: { lineStyle: { color: t.grid } } },
          yAxis: {
            type: "value",
            axisLabel: { color: t.muted, formatter: (v) => `${v >= 0 ? "+" : ""}${v.toLocaleString("vi-VN")}₫` },
            splitLine: { lineStyle: { color: t.grid } },
          },
          series: [
            {
              name: "Chênh lệch Chợ đen − NHTM (VND)",
              type: "bar",
              barMaxWidth: 10,
              data: hist.map((p) => ({
                value: p.spread_free_bank,
                itemStyle: {
                  color: p.spread_free_bank != null && p.spread_free_bank >= 500 ? t.rose : p.spread_free_bank >= 250 ? t.amber : t.emerald,
                  borderRadius: [3, 3, 0, 0],
                },
              })),
              markLine: {
                silent: true,
                symbol: "none",
                lineStyle: { type: "dashed", color: t.rose, width: 1.4 },
                label: { formatter: "+500₫ (Nóng)", color: t.rose, fontSize: 10.5, position: "insideEndTop" },
                data: [{ yAxis: 500 }],
              },
            },
          ],
        },
        true
      );
      return;
    }

    $("#vn-fxtier-chart-title").textContent = "So sánh 3 Tầng Tỷ giá USD/VND: Trung tâm · Thương mại · Tự do (Chợ đen)";
    $("#vn-fxtier-chart-sub").textContent = "Khi giá Chợ đen nới rộng cách biệt > 2% so với NHTM hoặc NHTM áp sát Trần +5% SBV, áp lực tỷ giá lên TTCK thường gia tăng";
    ch.setOption(
      {
        tooltip: {
          ...baseTooltip(),
          valueFormatter: (v) => (v == null ? "—" : `${Math.round(Number(v)).toLocaleString("vi-VN")}₫`),
        },
        legend: { top: 0, type: "scroll", textStyle: { color: t.muted, fontSize: isNarrow ? 11 : 12 } },
        grid: { left: isNarrow ? 58 : 66, right: 18, top: 44, bottom: 28 },
        xAxis: { type: "category", data: dates, axisLabel: { color: t.muted, fontSize: 11 }, axisLine: { lineStyle: { color: t.grid } } },
        yAxis: {
          type: "value",
          scale: true,
          min: (v) => Math.floor((v.min - 150) / 100) * 100,
          max: (v) => Math.ceil((v.max + 150) / 100) * 100,
          axisLabel: { color: t.muted, fontSize: isNarrow ? 10 : 11, formatter: (v) => v.toLocaleString("vi-VN") },
          splitLine: { lineStyle: { color: t.grid } },
        },
        series: [
          { name: "Chợ đen / Tự do (Bán)", type: "line", smooth: 0.15, showSymbol: false, lineStyle: { width: 2.8, color: t.rose }, itemStyle: { color: t.rose }, data: hist.map((p) => p.free) },
          { name: "Thương mại NHTM (Bán)", type: "line", smooth: 0.15, showSymbol: false, lineStyle: { width: 2.5, color: t.accent }, itemStyle: { color: t.accent }, data: hist.map((p) => p.commercial) },
          { name: "Trung tâm SBV", type: "line", smooth: 0.15, showSymbol: false, lineStyle: { width: 2.2, color: t.emerald }, itemStyle: { color: t.emerald }, data: hist.map((p) => p.central) },
          { name: "Trần SBV (+5%)", type: "line", smooth: 0.1, showSymbol: false, lineStyle: { width: 1.6, type: "dashed", color: t.amber }, itemStyle: { color: t.amber }, data: hist.map((p) => p.ceiling) },
        ],
      },
      true
    );
  }

  // =========================================================================
  // 8. Workspace 4: FX USD/VND, Conditional SBV (Auto-Skipped if Unavailable) & VN vs US
  // =========================================================================

  function renderFxAndSbv() {
    if (!state.data) return;
    const t = getThemeTokens();
    const fx = state.data.fx || {};
    const vcb = state.data.vcb || {};
    const sbv = state.data.sbv || { available: false };

    const fxRangeGroup = $("#fx-range-group");
    if (fxRangeGroup) fxRangeGroup.style.display = state.fxMode === "ytd" ? "none" : "inline-flex";

    const ytdObj = fx.usdvnd_ytd_comparison || {};
    const ytdBody = $("#table-fx-yearly tbody");
    if (ytdBody) {
      ytdBody.innerHTML = (ytdObj.annual_summary || [])
        .map((r) => {
          const color = r.ytd_pct > 2 ? t.rose : r.ytd_pct < 0 ? t.emerald : t.amber;
          return `
            <tr>
              <td><strong>${r.year}</strong></td>
              <td>${fmtVnd(r.open)}</td>
              <td>${fmtVnd(r.low)}</td>
              <td>${fmtVnd(r.high)}</td>
              <td><strong>${fmtVnd(r.close)}</strong></td>
              <td style="color:${color};font-weight:700">${fmtSigned(r.ytd_pct, 2, "%")}</td>
            </tr>`;
        })
        .join("");
    }

    // Conditional SBV Card: cleanly skipped if unavailable
    const sbvCard = $("#sbv-card");
    if (sbvCard) {
      if (sbv.available && sbv.central_rate) {
        sbvCard.classList.remove("hidden");
        $("#sbv-central").textContent = fmtVnd(sbv.central_rate);
        $("#sbv-date-sub").textContent = `Áp dụng ngày ${sbv.central_date || "mới nhất"} · Biên độ ±5%`;
        $("#sbv-floor").textContent = fmtVnd(sbv.floor);
        $("#sbv-ceiling").textContent = fmtVnd(sbv.ceiling);
        if (sbv.ref_buy && sbv.ref_sell) {
          $("#sbv-ref-box").classList.remove("hidden");
          $("#sbv-ref-vals").textContent = `${fmtVnd(sbv.ref_buy)} / ${fmtVnd(sbv.ref_sell)}`;
        } else {
          $("#sbv-ref-box").classList.add("hidden");
        }
        const vcbSell = vcb.usd?.sell || fx.usdvnd_spot?.price;
        if (vcbSell && sbv.ceiling > sbv.floor) {
          const pctPos = Math.max(2, Math.min(98, ((vcbSell - sbv.floor) / (sbv.ceiling - sbv.floor)) * 100));
          $("#sbv-vcb-marker").style.left = `${pctPos}%`;
          const gapVnd = sbv.ceiling - vcbSell;
          const gapPct = ((gapVnd / sbv.ceiling) * 100).toFixed(2);
          $("#sbv-gap-note").innerHTML = `Giá bán VCB (${fmtVnd(vcbSell)}) còn cách trần NHNN <strong class="mono">${fmtVnd(gapVnd)} (${gapPct}%)</strong>`;
        }
      } else {
        sbvCard.classList.add("hidden");
      }
    }

    $("#vcb-updated").textContent = vcb.updated_at ? `Cập nhật: ${vcb.updated_at}` : "Tỷ giá trực tuyến VCB";
    const usd = vcb.usd || {};
    const vcbHero = $("#vcb-usd-hero");
    if (vcbHero && usd.sell) {
      vcbHero.innerHTML = `
        <div class="mini-stat"><div class="mini-stat-label">USD Mua TM</div><div class="mini-stat-val">${fmtVnd(usd.buy)}</div></div>
        <div class="mini-stat"><div class="mini-stat-label">USD Chuyển khoản</div><div class="mini-stat-val" style="color:${t.accent}">${fmtVnd(usd.transfer)}</div></div>
        <div class="mini-stat"><div class="mini-stat-label">USD Bán ra</div><div class="mini-stat-val" style="color:${t.amber}">${fmtVnd(usd.sell)}</div></div>`;
    }

    const vcbBody = $("#table-vcb tbody");
    if (vcbBody) {
      vcbBody.innerHTML = (vcb.currencies || [])
        .map(
          (c) => `
          <tr>
            <td><strong>${esc(c.code)}</strong></td>
            <td>${c.buy ? Math.round(c.buy).toLocaleString("vi-VN") : "—"}</td>
            <td>${c.transfer ? Math.round(c.transfer).toLocaleString("vi-VN") : "—"}</td>
            <td><strong>${c.sell ? Math.round(c.sell).toLocaleString("vi-VN") : "—"}</strong></td>
          </tr>`
        )
        .join("");
    }

    const ch = getChart("chart-fx");
    if (!ch) return;

    if (state.fxMode === "ytd") {
      $("#fx-chart-title").textContent = "So sánh Biến động USD/VND Theo Từng Năm (% YTD)";
      $("#fx-chart-sub").textContent = "Đo lường tốc độ mất giá / tăng giá của VND từ đầu năm qua các năm (T1 → T12)";
      const yrs = ytdObj.years || [];
      const series = yrs.map((yr, idx) => {
        const isLatest = idx === yrs.length - 1;
        const color = isLatest ? t.amber : YEAR_COLORS[idx % YEAR_COLORS.length];
        return {
          name: yr,
          type: "line",
          smooth: 0.25,
          symbol: isLatest ? "circle" : "none",
          symbolSize: isLatest ? 7 : 4,
          lineStyle: { width: isLatest ? 3.2 : 1.9, opacity: isLatest ? 1 : 0.72, color },
          itemStyle: { color },
          data: (ytdObj.matrix || {})[yr] || [],
        };
      });
      if (series.length) {
        series[0].markLine = { silent: true, symbol: "none", lineStyle: { type: "dashed", color: t.muted, width: 1.2 }, label: { show: false }, data: [{ yAxis: 0 }] };
      }
      const isNarrowFx = window.innerWidth < 560;
      ch.setOption({
        tooltip: { ...baseTooltip(), valueFormatter: (v) => (v == null ? "—" : fmtSigned(v, 2, "%")) },
        legend: { top: 0, type: "scroll", textStyle: { color: t.muted, fontSize: isNarrowFx ? 11 : 12 } },
        grid: { left: 48, right: 18, top: 42, bottom: 26 },
        xAxis: { type: "category", data: ytdObj.months || [], axisLabel: { color: t.muted }, axisLine: { lineStyle: { color: t.grid } } },
        yAxis: { type: "value", axisLabel: { color: t.muted, formatter: "{value}%" }, splitLine: { lineStyle: { color: t.grid } } },
        series,
      }, true);
      return;
    }

    $("#fx-chart-title").textContent = "Diễn biến USD/VND Spot & Chỉ số DXY";
    $("#fx-chart-sub").textContent = "Đường kép 2 trục · Quan sát độ nhạy tỷ giá Việt Nam theo đồng USD toàn cầu";
    const hist = (fx.fx_dual_history || []).slice(-state.fxRange);
    const isNarrowFxDual = window.innerWidth < 560;

    ch.setOption({
      tooltip: baseTooltip(),
      legend: { top: 0, type: "scroll", textStyle: { color: t.muted, fontSize: isNarrowFxDual ? 11 : 12 } },
      grid: { left: isNarrowFxDual ? 54 : 62, right: isNarrowFxDual ? 40 : 48, top: isNarrowFxDual ? 56 : 50, bottom: 28 },
      xAxis: { type: "category", data: hist.map((p) => p.date), axisLabel: { color: t.muted, fontSize: 11 }, axisLine: { lineStyle: { color: t.grid } } },
      yAxis: [
        {
          type: "value",
          name: "USD/VND",
          nameGap: 10,
          nameTextStyle: { color: t.amber, fontSize: 10.5, align: "left" },
          min: (v) => Math.floor((v.min - 150) / 100) * 100,
          max: (v) => Math.ceil((v.max + 150) / 100) * 100,
          axisLabel: { color: t.muted, fontSize: isNarrowFxDual ? 10 : 11, formatter: (v) => v.toLocaleString("vi-VN") },
          splitLine: { lineStyle: { color: t.grid } },
        },
        {
          type: "value",
          name: "DXY",
          nameGap: 10,
          nameTextStyle: { color: t.accent, fontSize: 10.5, align: "right" },
          min: (v) => Math.floor(v.min - 1.5),
          max: (v) => Math.ceil(v.max + 1.5),
          axisLabel: { color: t.muted, fontSize: isNarrowFxDual ? 10 : 11 },
          splitLine: { show: false },
        },
      ],
      series: [
        {
          name: "USD/VND Spot",
          type: "line",
          yAxisIndex: 0,
          showSymbol: false,
          lineStyle: { width: 2.6, color: t.amber },
          itemStyle: { color: t.amber },
          areaStyle: {
            color: {
              type: "linear", x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [{ offset: 0, color: t.amber + "30" }, { offset: 1, color: t.amber + "00" }],
            },
          },
          data: hist.map((p) => p.usdvnd),
        },
        {
          name: "Chỉ số DXY (USD Index)",
          type: "line",
          yAxisIndex: 1,
          showSymbol: false,
          lineStyle: { width: 2, color: t.accent },
          itemStyle: { color: t.accent },
          data: hist.map((p) => p.dxy),
        },
      ],
    }, true);
  }

  function renderVnUsComparison() {
    if (!state.data) return;
    const t = getThemeTokens();
    const wb = state.data.worldbank?.metrics || {};
    const m = wb[state.wbMetric] || { years: [], VNM: [], USA: [] };

    const ch = getChart("chart-vn-us");
    if (ch) {
      const titles = {
        gdp_growth: "Tăng trưởng GDP thực (%)",
        cpi_annual: "Lạm phát CPI bình quân năm (%)",
        unemployment: "Tỉ lệ thất nghiệp hàng năm (%)",
      };
      ch.setOption({
        tooltip: { ...baseTooltip(), valueFormatter: (v) => (v == null ? "—" : `${Number(v).toFixed(2)}%`) },
        legend: { top: 0, textStyle: { color: t.muted, fontSize: 12 } },
        grid: { left: 46, right: 16, top: 38, bottom: 26 },
        xAxis: { type: "category", data: m.years || [], axisLabel: { color: t.muted }, axisLine: { lineStyle: { color: t.grid } } },
        yAxis: { type: "value", name: titles[state.wbMetric] || "%", nameTextStyle: { color: t.muted, fontSize: 11 }, axisLabel: { color: t.muted, formatter: "{value}%" }, splitLine: { lineStyle: { color: t.grid } } },
        series: [
          { name: "Việt Nam (VNM)", type: "bar", barMaxWidth: 26, itemStyle: { color: t.emerald, borderRadius: [4, 4, 0, 0] }, data: m.VNM || [] },
          { name: "Hoa Kỳ (USA)", type: "bar", barMaxWidth: 26, itemStyle: { color: t.accent, borderRadius: [4, 4, 0, 0] }, data: m.USA || [] },
        ],
      }, true);
    }

    const tbody = $("#table-scorecard tbody");
    if (tbody) {
      const k = state.data.kpis || {};
      const vnCpi = state.data.inflation?.vn_cpi_latest || {};
      const vni = state.data.vn_market?.indices?.VNINDEX || {};
      const lastValid = (arr) => {
        if (!arr) return null;
        for (let i = arr.length - 1; i >= 0; i--) { if (arr[i] != null) return arr[i]; }
        return null;
      };
      const rows = [
        ["Chỉ số Chứng khoán (YTD)", vni.ytd_pct != null ? `VN-Index ${fmtSigned(vni.ytd_pct, 1, "%")}` : "—", "S&P 500 (Live Ticker)"],
        ["Lạm phát CPI (YoY mới nhất)", fmtNum(vnCpi.yoy, 2, "%"), fmtNum(k.us_cpi?.value, 2, "%")],
        ["Lạm phát Lõi (Core)", fmtNum(vnCpi.core_ytd, 2, "%"), fmtNum(k.us_core_pce?.value, 2, "%")],
        ["PMI Sản xuất", fmtNum(k.vn_pmi?.value, 1), fmtNum(k.us_pmi?.value, 1)],
        ["Tỉ lệ Thất nghiệp", fmtNum(lastValid(wb.unemployment?.VNM), 2, "%"), fmtNum(k.us_unemployment?.value, 1, "%")],
        ["Tăng trưởng GDP (năm gần nhất)", fmtNum(lastValid(wb.gdp_growth?.VNM), 2, "%"), fmtNum(lastValid(wb.gdp_growth?.USA), 2, "%")],
      ];
      tbody.innerHTML = rows
        .map(([label, vn, us]) => `<tr><td>${esc(label)}</td><td style="color:${t.emerald};font-weight:700">${esc(vn)}</td><td style="color:${t.accent};font-weight:700">${esc(us)}</td></tr>`)
        .join("");
    }
  }

  // =========================================================================
  // 9. Workspace 5: Real-Time US 3-Star Economic Calendar (GMT+7) & News
  // =========================================================================

  function formatCountdownVi(eventTs) {
    const diff = Math.round(eventTs - Date.now() / 1000);
    if (diff <= 0) return "";
    const d = Math.floor(diff / 86400);
    const h = Math.floor((diff % 86400) / 3600);
    const m = Math.floor((diff % 3600) / 60);
    if (d > 0) return `Còn ${d} ngày ${h}g`;
    if (h > 0) return `Còn ${h}g ${m}p`;
    return `Còn ${Math.max(1, m)} phút`;
  }

  function renderUsCalStrip(data) {
    const strip = $("#us-cal-strip");
    if (!strip) return;
    const events = data?.calendar?.events || [];
    if (!events.length) {
      strip.classList.add("hidden");
      return;
    }
    // Highlight up to 2 most recent just-released (or today's actuals) + next 2 upcoming US 3-star events
    const released = events.filter((e) => e.has_actual).slice(-2).reverse();
    const upcoming = events.filter((e) => e.upcoming).slice(0, 2);
    const chips = [];

    released.forEach((ev) => {
      chips.push(`
        <span class="us-cal-chip released" title="${esc(ev.title)} (${esc(ev.date_vn)} ${esc(ev.time_vn)})">
          <span>🇺🇸 ★★★</span>
          <span>${esc(ev.title.split("·")[0].trim())}:</span>
          <strong>${esc(ev.actual)}</strong>
          <span style="opacity:.8">(DB: ${esc(ev.forecast)})</span>
        </span>`);
    });
    upcoming.forEach((ev) => {
      chips.push(`
        <span class="us-cal-chip upcoming" title="${esc(ev.title)}">
          <span>⏰ ${esc(ev.date_vn.slice(0, 5))} ${esc(ev.time_vn)}</span>
          <span>${esc(ev.title.split("·")[0].trim())}</span>
          ${ev.forecast !== "—" ? `<strong>(DB: ${esc(ev.forecast)})</strong>` : ""}
        </span>`);
    });

    if (!chips.length) {
      strip.classList.add("hidden");
      return;
    }
    strip.classList.remove("hidden");
    strip.innerHTML = `
      <div class="us-cal-strip-items">${chips.join("")}</div>
      <button type="button" class="us-cal-jump" id="btn-jump-cal">Lịch Mỹ ★★★ Live →</button>`;
    $("#btn-jump-cal")?.addEventListener("click", () => switchTab("news"));
  }

  function renderCalendar() {
    if (!state.data) return;
    const cal = state.data.calendar || {};
    const all = cal.events || [];

    // Update top meta bar & subtitle
    if ($("#cal-subtitle") && cal.source) {
      $("#cal-subtitle").textContent = `${cal.source} · Tự động cập nhật mỗi 30 giây`;
    }
    const metaTxt = $("#cal-meta-txt");
    if (metaTxt) {
      const upd = cal.updated_at_vn ? cal.updated_at_vn.split("·")[0].trim() : "—";
      metaTxt.textContent =
        `Đồng bộ ${upd} · Tổng ${all.length} tin 3★ Mỹ (${cal.today_count ?? 0} hôm nay · ${cal.released_count ?? 0} đã có số · ${cal.upcoming_count ?? 0} sắp công bố)`;
    }

    // Keep Tab 1 highlight strip updated too
    renderUsCalStrip(state.data);

    const list = $("#calendar-list");
    if (!list) return;

    const filtered = all.filter((ev) => {
      if (state.calFilter === "upcoming") return ev.upcoming;
      if (state.calFilter === "released") return ev.has_actual || !ev.upcoming;
      return true;
    });

    if (!filtered.length) {
      list.innerHTML = `<div class="empty-state">Chưa có sự kiện 3 sao Hoa Kỳ phù hợp bộ lọc này. Nhấn <strong>Cập nhật nhanh</strong> để đồng bộ ngay.</div>`;
      return;
    }

    // Group chronologically with clean date separator headers
    let htmlOut = "";
    let lastDate = "";
    filtered.forEach((ev) => {
      if (ev.date_vn !== lastDate) {
        lastDate = ev.date_vn;
        const dayTag = ev.is_today ? "HÔM NAY · " : "";
        htmlOut += `
          <div class="cal-day-sep ${ev.is_today ? "is-today" : ""}">
            <span>${dayTag}${esc(ev.weekday_vn)}, ${esc(ev.date_vn)}</span>
            <span>Giờ Việt Nam (GMT+7)</span>
          </div>`;
      }

      const rowCls = ev.just_released ? "just-released" : ev.upcoming ? "upcoming" : "past";
      const cd = ev.upcoming && ev.ts ? formatCountdownVi(ev.ts) : "";
      let statePill = "";
      if (ev.just_released && ev.has_actual) {
        statePill = `<span class="cal-state-pill live">VỪA CÔNG BỐ</span>`;
      } else if (ev.upcoming) {
        statePill = `<span class="cal-state-pill soon">${cd || "Sắp công bố"}</span>`;
      } else if (ev.has_actual) {
        statePill = `<span class="cal-state-pill done">Đã công bố</span>`;
      } else {
        statePill = `<span class="cal-state-pill done">Đã diễn ra</span>`;
      }

      const surpHtml = ev.surprise_label
        ? `<span class="cal-state-pill ${ev.surprise_tone === "success" ? "live" : ev.surprise_tone === "danger" ? "" : "soon"}" style="${ev.surprise_tone === "danger" ? "color:var(--rose);background:var(--rose-soft);border-color:var(--rose-border)" : ""}">${esc(ev.surprise_label)}</span>`
        : "";

      const actualToneCls = ev.has_actual ? (ev.surprise_tone || "info") : "";

      htmlOut += `
        <div class="cal-row ${rowCls}">
          <div class="cal-time">
            <strong>${esc(ev.time_vn)}</strong>
            <span class="cal-stars" title="3 sao — Tác động rất mạnh">★★★</span>
            <span class="cal-ccy">🇺🇸 USD</span>
          </div>
          <div class="cal-body">
            <div class="cal-title">${esc(ev.title)}</div>
            <div class="cal-meta-row">
              <span class="cal-cat-pill">${esc(ev.category || "Vĩ mô Mỹ")}</span>
              ${ev.period ? `<span>Kỳ: <strong>${esc(ev.period)}</strong></span>` : ""}
              ${statePill}
              ${surpHtml}
            </div>
          </div>
          <div class="cal-nums-grid">
            <div class="cal-num-col actual-box ${actualToneCls}">
              <span class="cal-num-lbl">Thực tế</span>
              <span class="cal-num-val">${esc(ev.actual)}</span>
            </div>
            <div class="cal-num-col">
              <span class="cal-num-lbl">Dự báo</span>
              <span class="cal-num-val">${esc(ev.forecast)}</span>
            </div>
            <div class="cal-num-col">
              <span class="cal-num-lbl">Kỳ trước</span>
              <span class="cal-num-val">${esc(ev.previous)}</span>
            </div>
          </div>
        </div>`;
    });

    list.innerHTML = htmlOut;
  }

  async function refreshCalendarOnly(force = true) {
    if (calSyncBusy) return;
    calSyncBusy = true;
    const btn = $("#btn-cal-refresh");
    if (btn) btn.classList.add("spinning");
    try {
      const r = await fetch(`/api/calendar?force=${force ? "1" : "0"}`, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const cal = await r.json();
      if (cal && !cal.error && Array.isArray(cal.events)) {
        if (!state.data) state.data = {};
        state.data.calendar = cal;
        renderCalendar();
      }
    } catch (err) {
      console.error("Fast calendar refresh error:", err);
    } finally {
      calSyncBusy = false;
      if (btn) btn.classList.remove("spinning");
    }
  }

  function renderNews() {
    if (!state.data) return;
    const list = $("#news-list");
    if (!list) return;
    const all = state.data.news || [];
    const q = state.newsQuery.trim().toLowerCase();

    const filtered = all.filter((item) => {
      if (state.newsCat !== "ALL" && item.category !== state.newsCat) return false;
      if (q) {
        const hay = `${item.title} ${item.summary} ${item.source} ${item.category}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });

    if (!filtered.length) {
      list.innerHTML = `<div class="empty-state">Không tìm thấy bài viết phù hợp. Hãy thử từ khóa hoặc chuyên mục khác.</div>`;
      return;
    }

    const catClass = (cat) => {
      if (cat.includes("SBV")) return "sbv";
      if (cat.includes("Tỷ giá")) return "fx";
      if (cat.includes("Lạm phát")) return "inf";
      if (cat.includes("Fed")) return "fed";
      return "";
    };

    list.innerHTML = filtered
      .map(
        (it) => `
        <a class="news-card" href="${safeHref(it.link)}" target="_blank" rel="noopener noreferrer">
          <div class="news-meta">
            <span class="news-cat ${catClass(it.category)}">${esc(it.category)}</span>
            <span class="news-source-time">${esc(it.source)} · ${esc(it.published_vn)}</span>
          </div>
          <h4 class="news-title">${esc(it.title)}</h4>
          ${it.summary ? `<p class="news-summary">${esc(it.summary)}</p>` : ""}
        </a>`
      )
      .join("");
  }

  function renderFooterSources(data) {
    const el = $("#footer-sources");
    if (!el) return;
    const srcs = data.meta?.sources || {};
    const names = {
      vn_market: "HOSE / VNDirect / CafeF",
      vn_valuation: "P/E VNI & Simplize",
      gold_vn: "Vàng SJC & CafeF",
      vn_money_fx: "LSLNH & Tỷ giá 3 Tầng",
      bls: "U.S. BLS",
      bea: "U.S. BEA",
      nyfed: "NY Fed",
      yield_curve: "Fed H.15",
      fedwatch: "CBOT ZQ",
      fx_markets: "Yahoo Markets",
      vcb: "Vietcombank",
      sbv: "SBV",
      worldbank: "World Bank",
      pmi_live: "ISM / S&P PMI",
      calendar: "US 3★ Live (TV+FXS)",
      news: "VN Policy RSS",
    };
    const okHtml = Object.entries(srcs)
      .filter(([, st]) => st && st.ok)
      .map(([k]) => `<span class="src-pill">${esc(names[k] || k)}</span>`)
      .join("");
    const degraded = data.meta?.degraded_sources || [];
    const degHtml = degraded.length
      ? `<span class="src-pill" style="border-color:rgba(245,158,11,0.5);color:#f59e0b" title="Nguồn tạm gián đoạn: ${esc(degraded.join(", "))}">⚠ Fallback (${degraded.length})</span>`
      : "";
    el.innerHTML = okHtml + degHtml;
  }

  // Render only visible workspace panes (or all panes in Toàn cảnh mode)
  function renderVisibleWorkspace() {
    if (!state.data) return;
    const tab = state.activeTab;
    const isAll = tab === "all";

    if (isAll || tab === "strategy") {
      renderStrategyCockpit(state.data);
      renderVnMarket(state.data);
      renderSectorPlaybook(state.data);
      renderWatchlist();
      renderBriefing(state.data);
      renderUsCalStrip(state.data);
      renderKpis(state.data);
    }
    if (isAll || tab === "macro") {
      renderInflation();
      renderPmi();
      renderLabor();
    }
    if (isAll || tab === "rates") {
      renderFedWatch();
      renderYieldCurve();
      renderVnInterbank();
    }
    if (isAll || tab === "fx") {
      renderVnFxTiers();
      renderFxAndSbv();
      renderVnGold(state.data);
      renderVnUsComparison();
    }
    if (isAll || tab === "news") {
      renderCalendar();
      renderNews();
    }
  }

  function renderDashboard(data) {
    state.data = data;
    updateGreeting();
    renderTicker(data);
    renderVisibleWorkspace();
    renderFooterSources(data);
  }

  // =========================================================================
  // Progressive Fast Paint -> Automatic Live Sync on Every Visit
  // =========================================================================

  async function fetchDashboard(mode = "live", force = false, retryCount = 0) {
    const btn = $("#btn-refresh");
    if (mode === "live") {
      setStatus("syncing", force ? "Đang làm mới toàn bộ..." : "Đang cập nhật dữ liệu mới nhất...");
      if (btn) btn.classList.add("spinning");
    }

    try {
      const url = `/api/dashboard?mode=${mode}${force ? "&force=1" : ""}`;
      const res = await fetch(url, { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.error) throw new Error(data.error);

      // If fast cache was stale (>4h old), skip painting old data and proceed cleanly to live sync!
      if (mode === "fast" && data._stale_skipped) {
        setStatus("syncing", "Đang đồng bộ dữ liệu trực tuyến hôm nay...");
        return true;
      }

      renderDashboard(data);

      const updated = data.meta?.updated_at_vn || "";
      const parts = updated.split("·").map((s) => s.trim());
      const hm = (parts[0] || "").slice(0, 5);
      const dm = (parts[1] || "").slice(0, 5);
      const stamp = hm && dm ? `${hm} · ${dm}` : (parts[0] || updated);
      if (mode === "live") {
        setStatus("ok", `Cập nhật ${stamp}`);
      } else {
        setStatus("syncing", "Đang đồng bộ mới nhất...");
      }
      return true;
    } catch (err) {
      console.error("Dashboard load error:", err);
      if (mode === "live" && retryCount < 1) {
        setStatus("syncing", "Máy chủ đang khởi động, thử lại...");
        await new Promise((r) => setTimeout(r, 2500));
        return fetchDashboard(mode, force, retryCount + 1);
      }
      if (mode === "live") setStatus("error", "Lỗi kết nối mạng");
      return false;
    } finally {
      if (mode === "live" && btn) btn.classList.remove("spinning");
    }
  }

  // =========================================================================
  // UI Event Bindings & Keyboard Shortcuts
  // =========================================================================

  function bindSegmented(containerId, attr, onChange) {
    const wrap = document.getElementById(containerId);
    if (!wrap) return;
    wrap.addEventListener("click", (e) => {
      const btn = e.target.closest("button");
      if (!btn || !wrap.contains(btn)) return;
      wrap.querySelectorAll("button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      onChange(btn.dataset[attr]);
    });
  }

  function initEvents() {
    // Workspace Tabs
    $("#workspace-tabs")?.addEventListener("click", (e) => {
      const btn = e.target.closest(".ws-tab");
      if (!btn) return;
      switchTab(btn.dataset.tab);
    });

    // Keyboard shortcuts 1..8 & 0 (when not typing in search input)
    window.addEventListener("keydown", (e) => {
      if (e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT")) return;
      const map = { "1": "strategy", "2": "macro", "3": "rates", "4": "fx", "5": "news", "6": "bctc", "7": "wyckoff", "8": "fusion", "0": "all" };
      if (map[e.key]) switchTab(map[e.key]);
    });

    // Global 1-click jump from any Leader Card, Watchlist, Fusion Matrix, Peers or Portfolio -> BCTC or Wyckoff Deep-Dive
    document.addEventListener("click", (e) => {
      const btnB = e.target.closest("[data-open-bctc]");
      if (btnB && btnB.dataset.openBctc) {
        switchTab("bctc");
        window.ProModules?.onOpenBctcExternal();
        window.BctcModule?.openFromOutside(btnB.dataset.openBctc);
        return;
      }
      const btnW = e.target.closest("[data-open-wyckoff]");
      if (btnW && btnW.dataset.openWyckoff) {
        switchTab("wyckoff");
        window.WyckoffModule?.openFromOutside(btnW.dataset.openWyckoff);
      }
    });

    // Explicit Dark / Light Theme Switcher
    $("#theme-switch")?.addEventListener("click", (e) => {
      const btn = e.target.closest(".theme-opt[data-set-theme]");
      if (!btn) return;
      applyTheme(btn.dataset.setTheme);
    });

    // Collapsible Left Sidebar & Tablet/Mobile Slide-out Drawer (<= 1200px)
    $("#btn-sidebar-toggle")?.addEventListener("click", () => {
      if (window.innerWidth <= 1200) {
        closeMobileDrawer();
      } else {
        applySidebarState(!state.sidebarCollapsed);
      }
    });
    $("#btn-mobile-menu")?.addEventListener("click", () => {
      $("#app-shell")?.classList.toggle("mobile-open");
    });
    $("#sidebar-backdrop")?.addEventListener("click", closeMobileDrawer);

    // Manual Full Refresh & Instant US 3-Star Calendar Refresh
    $("#btn-refresh")?.addEventListener("click", () => fetchDashboard("live", true));
    $("#btn-cal-refresh")?.addEventListener("click", () => refreshCalendarOnly(true));

    // KPI Cards quick jump to deep-dive workspace tab
    $$(".kpi-card[data-jump-tab]").forEach((card) => {
      card.addEventListener("click", () => switchTab(card.dataset.jumpTab));
    });

    // Clickable Vietnam Index Selector Cards (Event delegation)
    $("#vn-idx-cards")?.addEventListener("click", (e) => {
      const card = e.target.closest(".vn-idx-card[data-vn-code]");
      if (!card || !state.data) return;
      state.vnIndex = card.dataset.vnCode;
      renderVnMarket(state.data);
    });

    // Risk Profile selector (An toàn / Cân bằng / Tấn công)
    $$("#risk-profile-group .seg-btn").forEach((b) => {
      b.classList.toggle("active", b.dataset.risk === state.riskProfile);
    });
    bindSegmented("risk-profile-group", "risk", (v) => {
      state.riskProfile = v;
      localStorage.setItem("vimo_risk", v);
      renderAllocationOnly();
    });

    // Personal Portfolio Rebalancer inputs (saved in localStorage)
    const rebalInputs = [
      ["rebal-capital", "capital"],
      ["cur-eq", "eq"],
      ["cur-cash", "cash"],
      ["cur-gold", "gold"],
      ["cur-usd", "usd"],
    ];
    rebalInputs.forEach(([id, key]) => {
      const inp = document.getElementById(id);
      if (!inp) return;
      inp.value = state.rebal[key];
      inp.addEventListener("input", () => {
        state.rebal[key] = Number(inp.value) || 0;
        localStorage.setItem("vimo_rebal", JSON.stringify(state.rebal));
        renderAllocationOnly();
      });
    });

    // Flow Mode (Khối ngoại / Tự doanh / So sánh)
    bindSegmented("flow-mode-group", "flow", (v) => {
      state.flowMode = v;
      if (state.data?.vn_market?.flow) renderForeignFlow(state.data.vn_market.flow);
    });

    // Personal Stock Watchlist Add / Remove / Refresh
    const addTickersFromInput = () => {
      const inp = $("#wl-input");
      if (!inp) return;
      const parts = inp.value
        .toUpperCase()
        .split(/[\s,;]+/)
        .map((s) => s.replace(/[^A-Z0-9]/g, ""))
        .filter((s) => s.length >= 2 && s.length <= 10);
      if (!parts.length) return;
      parts.forEach((tk) => {
        if (!state.watchlist.includes(tk)) state.watchlist.push(tk);
      });
      if (state.watchlist.length > 16) state.watchlist = state.watchlist.slice(-16);
      inp.value = "";
      saveWatchlist();
      loadWatchlist();
    };
    $("#wl-add-btn")?.addEventListener("click", addTickersFromInput);
    $("#wl-input")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); addTickersFromInput(); }
    });
    $("#wl-refresh-btn")?.addEventListener("click", () => loadWatchlist());
    $("#watchlist-grid")?.addEventListener("click", (e) => {
      const btn = e.target.closest(".wl-remove[data-rm]");
      if (!btn) return;
      const tk = btn.dataset.rm;
      state.watchlist = state.watchlist.filter((x) => x !== tk);
      state.watchlistItems = state.watchlistItems.filter((x) => x.ticker !== tk);
      saveWatchlist();
      renderWatchlist();
    });

    // VN Market Controls
    bindSegmented("vn-mode-group", "mode", (v) => { state.vnMode = v; if (state.data) renderVnMarket(state.data); });
    bindSegmented("vn-overlay-group", "overlay", (v) => { state.vnOverlay = v; if (state.data) renderVnMarket(state.data); });
    bindSegmented("vn-range-group", "range", (v) => { state.vnRange = Number(v); if (state.data) renderVnMarket(state.data); });

    // Macro / Rates / FX / News Controls
    bindSegmented("inf-mode-group", "mode", (v) => { state.infMode = v; renderInflation(); });
    bindSegmented("inf-range-group", "range", (v) => { state.infRange = Number(v); renderInflation(); });
    bindSegmented("inf-metric-tabs", "metric", (v) => { state.infMetric = v; renderInflation(); });
    bindSegmented("pmi-mode-group", "mode", (v) => { state.pmiMode = v; renderPmi(); });
    bindSegmented("labor-mode-group", "mode", (v) => { state.laborMode = v; renderLabor(); });
    bindSegmented("fed-mode-group", "mode", (v) => { state.fedMode = v; renderFedWatch(); });
    bindSegmented("yc-mode-group", "mode", (v) => { state.ycMode = v; renderYieldCurve(); });
    bindSegmented("vn-ib-mode-group", "mode", (v) => { state.vnIbMode = v; renderVnInterbank(); });
    bindSegmented("vn-ib-range-group", "range", (v) => { state.vnIbRange = Number(v); renderVnInterbank(); });
    bindSegmented("vn-fxtier-mode-group", "mode", (v) => { state.fxTierMode = v; renderVnFxTiers(); });
    bindSegmented("vn-fxtier-range-group", "range", (v) => { state.fxTierRange = Number(v); renderVnFxTiers(); });
    bindSegmented("fx-mode-group", "mode", (v) => { state.fxMode = v; renderFxAndSbv(); });
    bindSegmented("fx-range-group", "range", (v) => { state.fxRange = Number(v); renderFxAndSbv(); });
    bindSegmented("wb-metric-group", "metric", (v) => { state.wbMetric = v; renderVnUsComparison(); });
    bindSegmented("cal-filter-group", "filter", (v) => { state.calFilter = v; renderCalendar(); });
    bindSegmented("news-cat-tabs", "cat", (v) => { state.newsCat = v; renderNews(); });
    $("#news-search")?.addEventListener("input", (e) => { state.newsQuery = e.target.value || ""; renderNews(); });

    let resizeTimer = null;
    let resizeSettleTimer = null;
    const handleViewportResize = () => {
      clearTimeout(resizeTimer);
      clearTimeout(resizeSettleTimer);
      resizeTimer = setTimeout(() => {
        resizeVisibleCharts();
        if (state.data && (state.activeTab === "fx" || state.activeTab === "all")) {
          renderFxAndSbv();
        }
      }, 120);
      resizeSettleTimer = setTimeout(() => resizeVisibleCharts(), 320);
    };
    window.addEventListener("resize", handleViewportResize);
    window.addEventListener("orientationchange", () => {
      setTimeout(() => {
        resizeVisibleCharts();
        if (state.data && (state.activeTab === "fx" || state.activeTab === "all")) {
          renderFxAndSbv();
        }
      }, 260);
    });
  }

  document.addEventListener("DOMContentLoaded", async () => {
    applyTheme(state.theme);
    applySidebarState(state.sidebarCollapsed);
    initEvents();
    loadWatchlist();
    await fetchDashboard("fast", false);
    await fetchDashboard("live", false);

    // Auto-poll live US 3-star economic calendar every 30s when page is visible
    calPollTimer = setInterval(() => {
      if (document.visibilityState === "visible") {
        refreshCalendarOnly(false);
      }
    }, 30000);
  });
})();
