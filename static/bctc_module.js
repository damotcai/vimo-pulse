/* ==========================================================================
   VĨ MÔ PULSE × BCTC PRO — Top-Down Macro × Bottom-Up Financial Statement UI
   High-Contrast Dark & Light ECharts Module (Zero Chart.js needed)
   ========================================================================== */

(() => {
  "use strict";

  const bState = {
    initialized: false,
    scenario: "live", // live | dovish | shock
    portfolio: null,
    currentTicker: null,
    currentAnalysis: null,
    activeSubtab: "overview",
    chartPeriod: "yearly",
    stmtSection: "INCOME_STATEMENT",
    stmtPeriod: "years",
    stmtData: null,
    lastMemoMd: "",
    charts: {},
  };

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  function esc(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fmtPrice(v) {
    return v && Number(v) > 0 ? `${Math.round(Number(v)).toLocaleString("vi-VN")}₫` : "—";
  }

  function fmtNum(v, d = 1) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
    return Number(v).toLocaleString("vi-VN", { minimumFractionDigits: d, maximumFractionDigits: d });
  }

  function fmtPct(v, signed = false) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
    const n = Number(v);
    return `${signed && n > 0 ? "+" : ""}${n.toFixed(1)}%`;
  }

  function tokens() {
    const isLight = document.documentElement.getAttribute("data-theme") === "light";
    return {
      isLight,
      text: isLight ? "#0f172a" : "#f1f5f9",
      muted: isLight ? "#334155" : "#cbd5e1",
      subtle: isLight ? "#475569" : "#94a3b8",
      grid: isLight ? "rgba(15, 23, 42, 0.10)" : "rgba(148, 163, 184, 0.14)",
      tooltipBg: isLight ? "#ffffff" : "#0f172a",
      tooltipBorder: isLight ? "#94a3b8" : "rgba(148, 163, 184, 0.34)",
      accent: isLight ? "#0369a1" : "#38bdf8",
      emerald: isLight ? "#047857" : "#34d399",
      rose: isLight ? "#be123c" : "#fb7185",
      amber: isLight ? "#b45309" : "#fbbf24",
      violet: isLight ? "#6d28d9" : "#c084fc",
    };
  }

  function getChart(id) {
    if (typeof window.echarts === "undefined") return null;
    const el = document.getElementById(id);
    if (!el || el.offsetParent === null) return null;
    if (!bState.charts[id]) {
      bState.charts[id] = window.echarts.init(el);
    }
    return bState.charts[id];
  }

  function resizeCharts() {
    Object.values(bState.charts).forEach((ch) => {
      if (ch && ch.getDom() && ch.getDom().offsetParent !== null) {
        ch.resize();
      }
    });
  }

  function baseTip() {
    const t = tokens();
    return {
      trigger: "axis",
      backgroundColor: t.tooltipBg,
      borderColor: t.tooltipBorder,
      borderWidth: 1,
      textStyle: { color: t.text, fontSize: 12, fontFamily: "JetBrains Mono, Inter, sans-serif" },
    };
  }

  function showStatus(msg, isErr = false) {
    const el = $("#bctc-status-msg");
    if (!el) return;
    if (!msg) {
      el.classList.add("hidden");
      return;
    }
    el.className = `bctc-status-msg${isErr ? " err" : ""}`;
    el.textContent = msg;
  }

  async function apiGet(url) {
    const r = await fetch(url, { cache: "no-store" });
    const d = await r.json();
    if (!r.ok || d.error) throw new Error(d.error || `HTTP ${r.status}`);
    return d;
  }

  async function apiPost(url, body) {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (!r.ok || d.error) throw new Error(d.error || `HTTP ${r.status}`);
    return d;
  }

  function renderMacroBar(mc) {
    const bar = $("#bctc-macro-bar");
    if (!bar || !mc) return;
    const scTone = mc.macro_score >= 65 ? "emerald" : mc.macro_score >= 45 ? "amber" : "rose";
    const vixTone = mc.vix >= 25 ? "rose" : mc.vix <= 16 ? "emerald" : "amber";
    const pmiTone = mc.vn_pmi >= 50 ? "emerald" : "rose";
    bar.innerHTML = `
      <span class="bctc-macro-chip ${scTone}">La bàn Vĩ mô: ${mc.macro_score}/100 · ${esc(mc.stance_short)}</span>
      <span class="bctc-macro-chip accent">US 10Y: ${Number(mc.us_10y).toFixed(2)}%</span>
      <span class="bctc-macro-chip ${mc.fed_hike_prob >= 45 ? "rose" : mc.fed_cut_prob >= 50 ? "emerald" : "amber"}">Fed: ${esc(mc.fed_bias)}</span>
      <span class="bctc-macro-chip ${vixTone}">VIX: ${Number(mc.vix).toFixed(1)}</span>
      <span class="bctc-macro-chip ${pmiTone}">PMI VN: ${Number(mc.vn_pmi).toFixed(1)}</span>
      <span class="bctc-macro-chip">DXY: ${Number(mc.dxy).toFixed(1)} · Ngân sách CP: ${mc.equity_budget_pct}%</span>
    `;
  }

  // =========================================================================
  // VIEW A: PORTFOLIO FUSED MATRIX
  // =========================================================================

  async function loadPortfolio() {
    try {
      const pf = await apiGet(`/api/bctc/portfolio?scenario=${bState.scenario}`);
      bState.portfolio = pf;
      renderPortfolio(pf);
    } catch (err) {
      showStatus(`Không tải được Ma trận BCTC × Vĩ mô: ${err.message}`, true);
    }
  }

  function renderPortfolio(pf) {
    if (!pf) return;
    const t = tokens();
    const mc = pf.macro_context || {};
    renderMacroBar(mc);

    const sumEl = $("#bctc-pf-summary");
    if (sumEl) {
      const cls = mc.macro_score >= 65 ? "good" : mc.macro_score >= 45 ? "warn" : "bad";
      sumEl.className = `bctc-summary-callout ${cls}`;
      sumEl.innerHTML = `<strong>Tổng hợp Chiến lược BCTC × Vĩ mô:</strong> ${esc(pf.summary)}`;
    }
    const badge = $("#bctc-cached-badge");
    if (badge) badge.textContent = `${(pf.ranking || []).length} mã BCTC`;

    const tbody = $("#table-bctc-matrix tbody");
    if (tbody) {
      const rows = pf.ranking || [];
      if (!rows.length) {
        tbody.innerHTML = `<tr><td colspan="14" class="empty-state">Chưa có dữ liệu BCTC. Nhấn <strong>Cập nhật 8 ngành</strong> hoặc nhập mã cổ phiếu ở trên.</td></tr>`;
      } else {
        tbody.innerHTML = rows
          .map((r) => {
            const upCol = r.upside_pct >= 0 ? t.emerald : t.rose;
            const mfCol = r.macro_fit >= 65 ? t.emerald : r.macro_fit >= 48 ? t.amber : t.rose;
            const scCol = r.score >= 70 ? t.emerald : r.score >= 55 ? t.amber : t.rose;
            const rfHtml =
              r.red_flags_count > 0
                ? `<span class="bctc-verdict-pill AVOID">${r.red_flags_count} (${r.macro_flags_count} vĩ mô)</span>`
                : `<span class="bctc-verdict-pill BUY">Sạch</span>`;
            return `
              <tr data-ticker="${esc(r.ticker)}">
                <td><span class="ticker-chip">${esc(r.ticker)}</span></td>
                <td style="text-align:left;font-family:var(--font-sans)">${esc(r.sector_vi)}</td>
                <td>${esc(r.latest_report_period)}</td>
                <td><strong>${fmtPrice(r.current_price)}</strong></td>
                <td style="color:var(--text-subtle)">${fmtPrice(r.pure_fair_value)}</td>
                <td><strong style="color:${t.accent}">${fmtPrice(r.fair_value)}</strong></td>
                <td style="color:${upCol};font-weight:800">${fmtPct(r.upside_pct, true)}</td>
                <td>${r.pe ? r.pe.toFixed(1) + "x" : "—"} · ${r.pb ? r.pb.toFixed(2) + "x" : "—"}</td>
                <td>${fmtPct(r.roe)}</td>
                <td>${r.pure_score}</td>
                <td style="color:${mfCol};font-weight:800">${r.macro_fit}</td>
                <td style="color:${scCol};font-weight:800;font-size:13px">${r.score}</td>
                <td>${rfHtml}</td>
                <td style="text-align:left"><span class="bctc-verdict-pill ${esc(r.verdict_code)}">${esc(r.verdict)}</span></td>
              </tr>`;
          })
          .join("");
      }
    }

    // Allocation list synced with CIO Equity Budget
    const allocSub = $("#bctc-alloc-sub");
    if (allocSub) {
      allocSub.textContent = `Đồng bộ với ngân sách Cổ phiếu ${mc.equity_budget_pct ?? 35}% tổng tài sản từ La bàn Vĩ mô`;
    }
    const allocEl = $("#bctc-alloc-list");
    if (allocEl) {
      allocEl.innerHTML = (pf.allocation_suggestion || [])
        .map((a) => {
          const fillCol = a.weight_in_total_nav_pct > 0 ? t.emerald : t.subtle;
          return `
            <div class="bctc-alloc-item">
              <div class="bctc-alloc-top">
                <span><strong>${esc(a.ticker)}</strong> <span style="color:var(--text-muted)">(${esc(a.sector_vi)} · BCTC ${a.pure_score} / Vĩ mô ${a.macro_fit})</span></span>
                <span class="mono" style="font-weight:800;color:${fillCol}">${a.weight_in_total_nav_pct}% NAV <span style="font-weight:500;color:var(--text-subtle)">(${a.weight_in_equity_pct}% rổ CP)</span></span>
              </div>
              <div class="bctc-alloc-bar">
                <div class="bctc-alloc-fill" style="width:${Math.min(100, a.weight_in_equity_pct * 2.4)}%;background:${fillCol}"></div>
              </div>
            </div>`;
        })
        .join("");
    }

    // Scatter Quadrant Chart (ECharts)
    const ch = getChart("chart-bctc-scatter");
    if (ch && (pf.ranking || []).length) {
      const pts = (pf.ranking || []).map((r) => ({
        value: [r.score, r.upside_pct],
        name: r.ticker,
        sector: r.sector_vi,
        macroFit: r.macro_fit,
        verdict: r.verdict,
        itemStyle: {
          color: ["STRONG_BUY", "BUY"].includes(r.verdict_code)
            ? t.emerald
            : r.verdict_code === "ACCUMULATE"
            ? t.accent
            : r.verdict_code === "HOLD"
            ? t.amber
            : t.rose,
        },
      }));
      ch.setOption({
        tooltip: {
          trigger: "item",
          backgroundColor: t.tooltipBg,
          borderColor: t.tooltipBorder,
          borderWidth: 1,
          textStyle: { color: t.text, fontSize: 12 },
          formatter: (p) => {
            const d = p.data;
            return `<strong>${d.name}</strong> (${d.sector})<br/>Điểm hợp nhất: <strong>${d.value[0]}/100</strong><br/>Hợp Vĩ mô: <strong>${d.macroFit}/100</strong><br/>Upside chỉnh vĩ mô: <strong>${d.value[1] > 0 ? "+" : ""}${d.value[1]}%</strong><br/>${d.verdict}`;
          },
        },
        grid: { left: 50, right: 24, top: 22, bottom: 38 },
        xAxis: {
          type: "value",
          name: "Điểm Hợp nhất BCTC × Vĩ mô",
          nameLocation: "middle",
          nameGap: 24,
          nameTextStyle: { color: t.muted, fontSize: 11 },
          min: 25,
          max: 95,
          axisLabel: { color: t.muted },
          splitLine: { lineStyle: { color: t.grid } },
        },
        yAxis: {
          type: "value",
          name: "Upside thực %",
          nameTextStyle: { color: t.muted, fontSize: 11 },
          axisLabel: { color: t.muted, formatter: "{value}%" },
          splitLine: { lineStyle: { color: t.grid } },
        },
        series: [
          {
            type: "scatter",
            symbolSize: 18,
            data: pts,
            label: { show: true, position: "top", formatter: "{b}", color: t.text, fontWeight: 700, fontSize: 11.5 },
            markLine: {
              silent: true,
              symbol: "none",
              lineStyle: { type: "dashed", color: t.subtle, width: 1.2 },
              label: { show: false },
              data: [{ xAxis: 65 }, { yAxis: 0 }],
            },
          },
        ],
      }, true);

      ch.off("click");
      ch.on("click", (params) => {
        if (params?.data?.name) openTicker(params.data.name);
      });
    }

    // Populate quick ticker chips for Detail View
    const chips = $("#bctc-ticker-chips");
    if (chips) {
      chips.innerHTML = (pf.ranking || [])
        .map(
          (r) =>
            `<button type="button" class="pill-btn ${bState.currentTicker === r.ticker ? "active" : ""}" data-chip-tk="${esc(r.ticker)}">${esc(r.ticker)}</button>`
        )
        .join("");
    }
  }

  // =========================================================================
  // VIEW B: COMPANY DEEP-DIVE (BCTC × LIVE MACRO)
  // =========================================================================

  function setViewMode(mode) {
    $("#bctc-matrix-view")?.classList.toggle("hidden", mode !== "matrix");
    $("#bctc-detail-view")?.classList.toggle("hidden", mode !== "detail");
    requestAnimationFrame(resizeCharts);
  }

  function setSubtab(sub) {
    bState.activeSubtab = sub;
    $$("#bctc-subtabs .pill-btn").forEach((b) => b.classList.toggle("active", b.dataset.subtab === sub));
    $$(".bctc-subpane").forEach((p) => p.classList.toggle("active", p.id === `bctc-sub-${sub}`));
    requestAnimationFrame(() => {
      if (bState.currentAnalysis) {
        if (sub === "overview") renderRadarChart(bState.currentAnalysis);
        if (sub === "charts") renderDetailCharts(bState.currentAnalysis);
        if (sub === "valuation") renderValuationChart(bState.currentAnalysis);
      }
      resizeCharts();
    });
  }

  async function openTicker(ticker, customQuery = "") {
    const sym = String(ticker || "").trim().toUpperCase();
    if (!sym) return;
    bState.currentTicker = sym;
    showStatus(`Đang phân tích BCTC × Vĩ mô cho ${sym}...`);
    try {
      const sep = customQuery ? "&" : "?";
      const url = `/api/bctc/analyze/${encodeURIComponent(sym)}${customQuery}${sep}scenario=${bState.scenario}`;
      const analysis = await apiGet(url);
      bState.currentAnalysis = analysis;
      showStatus("");
      setViewMode("detail");
      renderDetail(analysis);
      loadStatements();
      loadOfflineMemo(sym);
      // Sync active ticker chip
      $$("#bctc-ticker-chips .pill-btn").forEach((b) => b.classList.toggle("active", b.dataset.chipTk === sym));
    } catch (err) {
      showStatus(`Lỗi phân tích ${sym}: ${err.message}`, true);
    }
  }

  function renderDetail(a) {
    const t = tokens();
    const m = a.market;
    const g = a.growth;
    const sc = a.scores;
    const v = a.valuation;
    const st = a.strategy;
    const pp = st.price_plan;
    const mf = a.macro_fusion;
    const pvm = mf.pure_vs_macro;
    const ov = mf.valuation_overlay;

    renderMacroBar(mf.macro_context);

    // Hero
    $("#bd-ticker").textContent = a.ticker;
    $("#bd-sector").textContent = a.sector_vi;
    $("#bd-model").textContent = `Chuẩn ${a.report_model}`;
    $("#bd-period").textContent = `BCTC: ${a.latest_report_period}`;
    $("#bd-name").textContent = a.company_name;
    $("#bd-price").textContent = fmtPrice(m.current_price);
    $("#bd-mcap").textContent = `Vốn hóa: ${fmtNum(m.market_cap_bn, 0)} tỷ · ${fmtNum(m.shares_m, 1)} tr CP`;

    const verd = $("#bd-verdict");
    verd.textContent = st.verdict;
    verd.className = `bctc-hero-verdict ${["STRONG_BUY", "BUY"].includes(st.verdict_code) ? "good" : ["HOLD", "ACCUMULATE"].includes(st.verdict_code) ? "warn" : "bad"}`;
    $("#bd-fair-line").textContent = `FV Chỉnh Vĩ mô: ${fmtPrice(v.fair_value)} (${fmtPct(v.upside_pct, true)})`;
    $("#bd-score").textContent = sc.total;
    $("#bd-score-split").textContent = `BCTC ${sc.pure_bctc} · Vĩ mô ${sc.macro_fit}`;

    // Should invest banner
    const inv = $("#bd-should-invest");
    const invCls = ["STRONG_BUY", "BUY"].includes(st.verdict_code) ? "good" : st.verdict_code === "AVOID" ? "bad" : "warn";
    inv.className = `bctc-summary-callout ${invCls}`;
    inv.innerHTML = `<strong>KẾT LUẬN HỢP NHẤT (${esc(a.ticker)}):</strong> ${esc(st.should_invest)} &nbsp;·&nbsp; <strong>Tỷ trọng:</strong> ${esc(st.allocation)}`;

    // Before vs After Macro Comparison
    $("#bd-adj-reason").textContent = pvm.adjustment_reason;
    $("#bd-ke-tag").textContent = `Ke động: ${(ov.live_ke * 100).toFixed(1)}% (${ov.ke_shift_bps >= 0 ? "+" : ""}${ov.ke_shift_bps}bps · Beta ${ov.sector_beta}x)`;
    $("#bd-compare-grid").innerHTML = `
      <div class="bctc-cmp-card">
        <div class="bctc-cmp-lbl">Điểm Tổng hợp</div>
        <div class="bctc-cmp-vals">
          <span class="bctc-cmp-old">${pvm.pure_score}</span><span>→</span>
          <strong style="color:${sc.total >= 70 ? t.emerald : sc.total >= 55 ? t.amber : t.rose}">${pvm.fused_score}/100</strong>
        </div>
        <div class="bctc-cmp-sub">Chênh lệch vĩ mô: ${pvm.score_delta >= 0 ? "+" : ""}${pvm.score_delta} điểm</div>
      </div>
      <div class="bctc-cmp-card">
        <div class="bctc-cmp-lbl">Giá trị Hợp lý (Fair Value)</div>
        <div class="bctc-cmp-vals">
          <span class="bctc-cmp-old">${fmtPrice(pvm.pure_fair_value)}</span><span>→</span>
          <strong style="color:${t.accent}">${fmtPrice(pvm.macro_fair_value)}</strong>
        </div>
        <div class="bctc-cmp-sub">Điều chỉnh: ${fmtPct(pvm.fv_delta_pct, true)} (Bội số ${ov.multiple_adj}x)</div>
      </div>
      <div class="bctc-cmp-card">
        <div class="bctc-cmp-lbl">Upside Thực tế</div>
        <div class="bctc-cmp-vals">
          <span class="bctc-cmp-old">${fmtPct(pvm.pure_upside_pct, true)}</span><span>→</span>
          <strong style="color:${pvm.macro_upside_pct >= 0 ? t.emerald : t.rose}">${fmtPct(pvm.macro_upside_pct, true)}</strong>
        </div>
        <div class="bctc-cmp-sub">Biên an toàn (MoS): ${fmtPct(v.margin_of_safety_pct, true)}</div>
      </div>
      <div class="bctc-cmp-card">
        <div class="bctc-cmp-lbl">Khuyến nghị (Tĩnh → Thực)</div>
        <div class="bctc-cmp-vals" style="font-size:12.5px">
          <strong style="color:${["STRONG_BUY", "BUY"].includes(st.verdict_code) ? t.emerald : st.verdict_code === "AVOID" ? t.rose : t.amber}">${esc(st.verdict)}</strong>
        </div>
        <div class="bctc-cmp-sub">BCTC thuần: ${esc(pvm.pure_verdict)}</div>
      </div>`;

    // 6 Macro Transmission Channels
    $("#bd-macro-impacts").innerHTML = (mf.impacts || [])
      .map(
        (imp) => `
        <div class="bctc-impact-card ${esc(imp.signal)}">
          <div class="bctc-imp-head">
            <span>${esc(imp.channel)}</span>
            <span class="bctc-imp-delta ${esc(imp.signal)}">${imp.delta > 0 ? "+" : ""}${imp.delta}đ</span>
          </div>
          <div class="bctc-imp-fact">Vĩ mô: ${esc(imp.macro_fact)}</div>
          <div class="bctc-imp-bctc">BCTC: ${esc(imp.bctc_link)}</div>
          <div class="bctc-imp-exp">${esc(imp.explanation)}</div>
        </div>`
      )
      .join("");

    // 8 Core KPIs
    const kpis = [
      ["P/E (TTM)", m.pe ? `${m.pe.toFixed(1)}x` : "—", `TB lịch sử: ${v.pe_bands.mean || "—"}x`],
      ["P/B (Hiện tại)", m.pb ? `${m.pb.toFixed(2)}x` : "—", `TB lịch sử: ${v.pb_bands.mean || "—"}x`],
      ["ROE / ROA", fmtPct(m.roe), `ROA: ${fmtPct(m.roa)}`],
      ["EPS / BVPS", fmtPrice(m.eps_ttm), `BVPS: ${fmtPrice(m.bvps)}`],
      ["Tăng trưởng DT 5Y", fmtPct(g.rev_cagr_5y, true), `Quý mới YoY: ${fmtPct(g.rev_yoy_latest_q, true)}`],
      ["Tăng trưởng LN 5Y", fmtPct(g.npat_cagr_5y, true), `Quý mới YoY: ${fmtPct(g.npat_yoy_latest_q, true)}`],
      ["Độ phù hợp Vĩ mô", `${sc.macro_fit}/100`, `Beta ngành: ${ov.sector_beta}x`],
      ["Cổ tức tiền mặt", fmtPct(m.dividend_yield), `Đỉnh 52T: ${fmtPrice(m.price_stats.high_52w)}`],
    ];
    $("#bd-kpi-strip").innerHTML = kpis
      .map(([l, val, sub]) => `<div class="bctc-kpi-box"><div class="bctc-kpi-lbl">${l}</div><div class="bctc-kpi-val">${val}</div><div class="bctc-kpi-sub">${sub}</div></div>`)
      .join("");

    // Price Plan & Strategy
    const tw = st.tranche_weights || [30, 40, 30];
    $("#bd-style-horizon").textContent = `${st.style} · ${st.horizon}`;
    $("#bd-alloc-tag").textContent = st.allocation;
    $("#bd-price-plan").innerHTML = `
      <div class="bctc-pp-box buy"><div class="bctc-pp-lbl">Đợt 1 (Thăm dò ${tw[0]}%)</div><div class="bctc-pp-val">${fmtPrice(pp.buy_tranche_1)}</div></div>
      <div class="bctc-pp-box buy"><div class="bctc-pp-lbl">Đợt 2 (Tích lũy ${tw[1]}%)</div><div class="bctc-pp-val">${fmtPrice(pp.buy_tranche_2)}</div></div>
      <div class="bctc-pp-box buy"><div class="bctc-pp-lbl">Đợt 3 (Chiết khấu ${tw[2]}%)</div><div class="bctc-pp-val">${fmtPrice(pp.buy_tranche_3)}</div></div>
      <div class="bctc-pp-box tp"><div class="bctc-pp-lbl">Fair Value (Chỉnh Vĩ mô)</div><div class="bctc-pp-val">${fmtPrice(pp.fair_value)}</div></div>
      <div class="bctc-pp-box tp"><div class="bctc-pp-lbl">Vùng chốt lời</div><div class="bctc-pp-val">${fmtPrice(pp.take_profit_1)} – ${fmtPrice(pp.take_profit_2)}</div></div>
      <div class="bctc-pp-box stop"><div class="bctc-pp-lbl">Rà soát rủi ro / Cắt lỗ</div><div class="bctc-pp-val">&lt; ${fmtPrice(pp.review_stop_level)}</div></div>`;
    $("#bd-tactics").innerHTML = (st.tactics || []).map((x) => `<li>${esc(x)}</li>`).join("");
    $("#bd-drivers").innerHTML = (st.key_drivers || []).map((x) => `<li>${esc(x)}</li>`).join("");
    $("#bd-invalidations").innerHTML = (st.invalidation_triggers || []).map((x) => `<li>${esc(x)}</li>`).join("");

    // 6-Pillar Bars & Radar
    const pillars = [
      ["Sinh lời (BCTC)", sc.profitability],
      ["Tăng trưởng (BCTC)", sc.growth],
      ["Sức khỏe TC (BCTC)", sc.health],
      ["Chất lượng tiền", sc.quality],
      ["Định giá (Chỉnh VM)", sc.valuation],
      ["Độ phù hợp Vĩ mô", sc.macro_fit],
    ];
    $("#bd-pillar-bars").innerHTML = pillars
      .map(([lbl, val]) => {
        const col = val >= 68 ? t.emerald : val >= 48 ? t.amber : t.rose;
        return `
          <div class="pillar-row">
            <div class="pillar-top"><span class="pillar-name">${lbl}</span><span class="pillar-score" style="color:${col}">${val}/100</span></div>
            <div class="pillar-track"><div class="pillar-fill" style="width:${val}%;background:${col}"></div></div>
          </div>`;
      })
      .join("");
    renderRadarChart(a);

    // Sector metrics
    $("#bd-sec-title").textContent = `Chỉ số Đặc thù Ngành — ${a.sector_metrics.model}`;
    $("#bd-sector-grid").innerHTML = (a.sector_metrics.cards || [])
      .map((c) => `<div class="bctc-sec-card ${esc(c.status || "")}"><div class="bctc-sec-lbl">${esc(c.label)}</div><div class="bctc-sec-val">${esc(c.value)}</div></div>`)
      .join("");

    // Strengths & Red Flags (BCTC + Macro tagged)
    $("#bd-strengths").innerHTML = (a.strengths || []).length
      ? a.strengths.map((s) => `<li>${esc(s)}</li>`).join("")
      : `<li style="color:var(--text-muted)">Hoạt động kinh doanh ổn định.</li>`;
    $("#bd-red-flags").innerHTML = (a.red_flags || []).length
      ? a.red_flags
          .map((rf) => {
            const src = rf.source === "macro" ? "VĨ MÔ" : "BCTC";
            return `<div class="bctc-flag-item ${esc(rf.severity)}"><div class="bctc-flag-title">[${src} · ${esc(rf.severity.toUpperCase())}] ${esc(rf.title)}</div><div class="bctc-flag-detail">${esc(rf.detail)}</div></div>`;
          })
          .join("")
      : `<div class="bctc-flag-item ok"><div class="bctc-flag-title">Không phát hiện Red Flag trọng yếu</div><div class="bctc-flag-detail">Các chỉ tiêu tài chính BCTC và bộ lọc vĩ mô đang trong vùng kiểm soát.</div></div>`;

    // Corporate Forensics
    const fPanel = $("#bd-forensics-panel");
    if (a.report_model === "CORPORATE" && a.piotroski?.details?.length) {
      fPanel.classList.remove("hidden");
      $("#bd-piotroski-box").innerHTML =
        `<div class="subcard-title" style="margin-bottom:8px;color:var(--accent)">Piotroski F-Score: ${a.piotroski.score}/9 — ${esc(a.piotroski.label)}</div>` +
        a.piotroski.details
          .map((d) => `<div class="bctc-pio-row"><span>${d.passed ? "✓" : "✕"} ${esc(d.criterion)}</span><strong class="mono">${esc(d.evidence)}</strong></div>`)
          .join("");
      const dp = a.dupont || {};
      const alt = a.altman || {};
      const ex = a.sector_metrics.extra || {};
      $("#bd-dupont-box").innerHTML = `
        <div class="subcard-title" style="margin-bottom:8px;color:var(--accent)">Phân rã DuPont &amp; Altman Z-Score</div>
        <div class="bctc-pio-row"><span>Biên lợi nhuận ròng (Net Margin)</span><strong class="mono">${dp.net_margin}%</strong></div>
        <div class="bctc-pio-row"><span>Vòng quay Tổng tài sản</span><strong class="mono">${dp.asset_turnover}x</strong></div>
        <div class="bctc-pio-row"><span>Đòn bẩy Tài chính (TTS/VCSH)</span><strong class="mono">${dp.financial_leverage}x</strong></div>
        <div class="bctc-pio-row"><span>ROE Quy đổi DuPont</span><strong class="mono" style="color:${t.emerald}">${dp.roe_dupont}%</strong></div>
        <div class="bctc-pio-row"><span>Altman Z-Score</span><strong class="mono">${alt.score ?? "N/A"} (${esc(alt.zone)})</strong></div>
        <div class="bctc-pio-row"><span>Tài sản dở dang dài hạn (CAPEX)</span><strong class="mono">${fmtNum(ex.wip_lt_bn, 1)} tỷ</strong></div>
        <div class="bctc-pio-row"><span>Chu kỳ tiền mặt CCC / Tồn kho DIO</span><strong class="mono">${fmtNum(ex.ccc, 0)}d / ${fmtNum(ex.dio, 0)}d</strong></div>`;
    } else {
      fPanel.classList.add("hidden");
    }

    // Sync valuation sliders to live macro defaults
    const kePct = Number((v.assumptions.wacc * 100).toFixed(1));
    const wInp = $("#inp-bd-wacc");
    if (wInp) {
      wInp.value = Math.min(19, Math.max(9, kePct));
      $("#lbl-bd-wacc").textContent = `${Number(wInp.value).toFixed(1)}%`;
    }
    const rInp = $("#inp-bd-roe");
    if (rInp && m.roe > 0) {
      rInp.value = Math.min(30, Math.max(6, Math.round(m.roe * 2) / 2));
      $("#lbl-bd-roe").textContent = `${Number(rInp.value).toFixed(1)}%`;
    }
    renderValuationTab(a);
    if (bState.activeSubtab === "charts") renderDetailCharts(a);
  }

  function renderRadarChart(a) {
    const ch = getChart("chart-bctc-radar");
    if (!ch) return;
    const t = tokens();
    const sc = a.scores;
    ch.setOption({
      radar: {
        radius: "66%",
        indicator: [
          { name: "Sinh lời", max: 100 },
          { name: "Tăng trưởng", max: 100 },
          { name: "Sức khỏe", max: 100 },
          { name: "Dòng tiền", max: 100 },
          { name: "Định giá", max: 100 },
          { name: "Hợp Vĩ mô", max: 100 },
        ],
        axisName: { color: t.muted, fontSize: 11, fontWeight: 700 },
        splitLine: { lineStyle: { color: t.grid } },
        splitArea: { show: false },
        axisLine: { lineStyle: { color: t.grid } },
      },
      series: [
        {
          type: "radar",
          data: [
            {
              value: [sc.profitability, sc.growth, sc.health, sc.quality, sc.valuation, sc.macro_fit],
              name: a.ticker,
              lineStyle: { color: t.accent, width: 2.2 },
              itemStyle: { color: t.accent },
              areaStyle: { color: t.accent + "30" },
            },
          ],
        },
      ],
    }, true);
  }

  function renderDetailCharts(a) {
    const t = tokens();
    const rows = bState.chartPeriod === "yearly" ? a.series.yearly : a.series.quarterly;
    const labels = rows.map((r) => r.period);

    // 1. Rev & NPAT
    const chRev = getChart("chart-bctc-rev");
    if (chRev) {
      chRev.setOption({
        tooltip: baseTip(),
        legend: { top: 0, textStyle: { color: t.muted, fontSize: 11.5 } },
        grid: { left: 56, right: 16, top: 34, bottom: 26 },
        xAxis: { type: "category", data: labels, axisLabel: { color: t.muted, fontSize: 10.5 }, axisLine: { lineStyle: { color: t.grid } } },
        yAxis: { type: "value", axisLabel: { color: t.muted, fontSize: 10.5 }, splitLine: { lineStyle: { color: t.grid } } },
        series: [
          { name: a.report_model === "BANK" ? "Tổng TN Hoạt động (TOI)" : "Doanh thu thuần", type: "bar", barMaxWidth: 22, itemStyle: { color: t.accent, borderRadius: [4, 4, 0, 0] }, data: rows.map((r) => r.revenue) },
          { name: "LNST Cổ đông mẹ", type: "bar", barMaxWidth: 22, itemStyle: { color: t.emerald, borderRadius: [4, 4, 0, 0] }, data: rows.map((r) => r.npat) },
        ],
      }, true);
    }

    // 2. Margins / Bank NIM-CASA-NPL
    const chMar = getChart("chart-bctc-margin");
    if (chMar) {
      if (a.report_model === "BANK") {
        $("#bd-margin-chart-title").textContent = "Chỉ tiêu Ngân hàng: NIM, NPL & CASA (%)";
        chMar.setOption({
          tooltip: baseTip(),
          legend: { top: 0, textStyle: { color: t.muted, fontSize: 11.5 } },
          grid: { left: 46, right: 44, top: 34, bottom: 26 },
          xAxis: { type: "category", data: labels, axisLabel: { color: t.muted, fontSize: 10.5 }, axisLine: { lineStyle: { color: t.grid } } },
          yAxis: [
            { type: "value", axisLabel: { color: t.muted, formatter: "{value}%" }, splitLine: { lineStyle: { color: t.grid } } },
            { type: "value", axisLabel: { color: t.emerald, formatter: "{value}%" }, splitLine: { show: false } },
          ],
          series: [
            { name: "NIM (%)", type: "line", smooth: 0.25, lineStyle: { width: 2.5, color: t.accent }, itemStyle: { color: t.accent }, data: rows.map((r) => r.nim) },
            { name: "Nợ xấu NPL (%)", type: "line", smooth: 0.25, lineStyle: { width: 2.5, color: t.rose }, itemStyle: { color: t.rose }, data: rows.map((r) => r.npl) },
            { name: "CASA (%)", type: "line", yAxisIndex: 1, smooth: 0.25, lineStyle: { width: 2.5, color: t.emerald }, itemStyle: { color: t.emerald }, data: rows.map((r) => r.casa) },
          ],
        }, true);
      } else {
        $("#bd-margin-chart-title").textContent = "Hiệu quả Sinh lời: ROE, Biên Gộp & Biên Ròng (%)";
        chMar.setOption({
          tooltip: baseTip(),
          legend: { top: 0, textStyle: { color: t.muted, fontSize: 11.5 } },
          grid: { left: 46, right: 16, top: 34, bottom: 26 },
          xAxis: { type: "category", data: labels, axisLabel: { color: t.muted, fontSize: 10.5 }, axisLine: { lineStyle: { color: t.grid } } },
          yAxis: { type: "value", axisLabel: { color: t.muted, formatter: "{value}%" }, splitLine: { lineStyle: { color: t.grid } } },
          series: [
            { name: "ROE (%)", type: "line", smooth: 0.25, lineStyle: { width: 2.5, color: t.emerald }, itemStyle: { color: t.emerald }, data: rows.map((r) => (bState.chartPeriod === "yearly" ? r.roe : r.roe_ttm)) },
            { name: "Biên Gộp (%)", type: "line", smooth: 0.25, lineStyle: { width: 2.2, color: t.accent }, itemStyle: { color: t.accent }, data: rows.map((r) => r.gross_margin) },
            { name: "Biên Ròng (%)", type: "line", smooth: 0.25, lineStyle: { width: 2.2, color: t.amber }, itemStyle: { color: t.amber }, data: rows.map((r) => r.net_margin) },
          ],
        }, true);
      }
    }

    // 3. Cash flow / Bank LLR-CIR
    const chCf = getChart("chart-bctc-cash");
    if (chCf) {
      const yr = a.series.yearly || [];
      const yLabels = yr.map((r) => r.period);
      if (a.report_model === "BANK") {
        $("#bd-cash-chart-title").textContent = "Bao phủ Nợ xấu (LLR %) & Chi phí / Thu nhập (CIR %)";
        chCf.setOption({
          tooltip: baseTip(),
          legend: { top: 0, textStyle: { color: t.muted, fontSize: 11.5 } },
          grid: { left: 48, right: 16, top: 34, bottom: 26 },
          xAxis: { type: "category", data: yLabels, axisLabel: { color: t.muted }, axisLine: { lineStyle: { color: t.grid } } },
          yAxis: { type: "value", axisLabel: { color: t.muted, formatter: "{value}%" }, splitLine: { lineStyle: { color: t.grid } } },
          series: [
            { name: "LLR Bao phủ nợ xấu (%)", type: "bar", barMaxWidth: 26, itemStyle: { color: t.emerald, borderRadius: [4, 4, 0, 0] }, data: yr.map((r) => r.llr) },
            { name: "CIR (%)", type: "line", smooth: 0.25, lineStyle: { width: 2.4, color: t.amber }, itemStyle: { color: t.amber }, data: yr.map((r) => r.cir) },
          ],
        }, true);
      } else {
        $("#bd-cash-chart-title").textContent = "Chất lượng Dòng tiền (Năm): OCF vs LNST vs FCF (Tỷ VND)";
        chCf.setOption({
          tooltip: baseTip(),
          legend: { top: 0, textStyle: { color: t.muted, fontSize: 11.5 } },
          grid: { left: 56, right: 16, top: 34, bottom: 26 },
          xAxis: { type: "category", data: yLabels, axisLabel: { color: t.muted }, axisLine: { lineStyle: { color: t.grid } } },
          yAxis: { type: "value", axisLabel: { color: t.muted }, splitLine: { lineStyle: { color: t.grid } } },
          series: [
            { name: "Dòng tiền KD (OCF)", type: "bar", barMaxWidth: 18, itemStyle: { color: t.accent, borderRadius: [3, 3, 0, 0] }, data: yr.map((r) => r.ocf) },
            { name: "LNST", type: "bar", barMaxWidth: 18, itemStyle: { color: t.violet, borderRadius: [3, 3, 0, 0] }, data: yr.map((r) => r.npat) },
            { name: "Dòng tiền Tự do (FCF)", type: "line", smooth: 0.25, lineStyle: { width: 2.5, color: t.emerald }, itemStyle: { color: t.emerald }, data: yr.map((r) => r.fcf) },
          ],
        }, true);
      }
    }

    // 4. Price 1Y + MA50/MA200 + Macro-Adjusted Fair Value Line
    const chPr = getChart("chart-bctc-price");
    if (chPr) {
      const prices = a.series.prices || [];
      const closes = prices.map((p) => p.close);
      const ma = (n) => closes.map((_, i) => (i < n - 1 ? null : Math.round(closes.slice(i - n + 1, i + 1).reduce((s, x) => s + x, 0) / n)));
      chPr.setOption({
        tooltip: baseTip(),
        legend: { top: 0, textStyle: { color: t.muted, fontSize: 11.5 } },
        grid: { left: 58, right: 16, top: 34, bottom: 26 },
        xAxis: { type: "category", data: prices.map((p) => p.date), axisLabel: { color: t.muted, fontSize: 10 }, axisLine: { lineStyle: { color: t.grid } } },
        yAxis: { type: "value", scale: true, axisLabel: { color: t.muted, fontSize: 10.5 }, splitLine: { lineStyle: { color: t.grid } } },
        series: [
          {
            name: `Giá ${a.ticker}`,
            type: "line",
            showSymbol: false,
            lineStyle: { width: 2.4, color: t.accent },
            itemStyle: { color: t.accent },
            areaStyle: { color: t.accent + "22" },
            data: closes,
            markLine: a.valuation.fair_value > 0
              ? {
                  silent: true,
                  symbol: "none",
                  lineStyle: { type: "dashed", color: t.emerald, width: 1.6 },
                  label: { formatter: `FV Chỉnh VM: ${Math.round(a.valuation.fair_value).toLocaleString("vi-VN")}`, color: t.emerald, fontSize: 10.5, position: "insideEndTop" },
                  data: [{ yAxis: a.valuation.fair_value }],
                }
              : undefined,
          },
          { name: "MA50", type: "line", showSymbol: false, lineStyle: { width: 1.6, color: t.amber }, itemStyle: { color: t.amber }, data: ma(50) },
          { name: "MA200", type: "line", showSymbol: false, lineStyle: { width: 1.8, type: "dashed", color: t.rose }, itemStyle: { color: t.rose }, data: ma(200) },
        ],
      }, true);
    }
  }

  // =========================================================================
  // SUBTAB 3: FINANCIAL STATEMENTS TABLE
  // =========================================================================

  async function loadStatements() {
    if (!bState.currentTicker) return;
    try {
      const data = await apiGet(`/api/bctc/statements/${encodeURIComponent(bState.currentTicker)}?section=${bState.stmtSection}&period=${bState.stmtPeriod}&limit=8`);
      bState.stmtData = data;
      renderStatements();
    } catch (_) {}
  }

  function renderStatements() {
    const data = bState.stmtData;
    if (!data) return;
    const t = tokens();
    const q = ($("#bctc-stmt-search")?.value || "").trim().toLowerCase();
    const thead = $("#table-bctc-stmt thead");
    const tbody = $("#table-bctc-stmt tbody");
    if (!thead || !tbody) return;

    thead.innerHTML = `<tr><th>Khoản mục (${esc(data.section)} · Tỷ VND)</th>${data.columns.map((c) => `<th>${esc(c)}</th>`).join("")}<th>YoY %</th></tr>`;
    const list = q
      ? data.items.filter((it) => `${it.title_vi} ${it.title_en} ${it.field}`.toLowerCase().includes(q))
      : data.items;

    tbody.innerHTML = list
      .map((it) => {
        const yCol = it.yoy_pct > 0 ? t.emerald : it.yoy_pct < 0 ? t.rose : t.muted;
        return `
          <tr class="lvl-${Math.min(4, it.level)}">
            <td style="font-family:var(--font-sans)" title="${esc(it.title_en)} [${esc(it.field)}]">${esc(it.title_vi)}</td>
            ${it.values.map((v) => `<td>${v === null ? "—" : fmtNum(v, it.unit === "VND" ? 0 : 1)}</td>`).join("")}
            <td style="color:${yCol};font-weight:700">${it.yoy_pct === null ? "—" : fmtPct(it.yoy_pct, true)}</td>
          </tr>`;
      })
      .join("");
  }

  // =========================================================================
  // SUBTAB 4: VALUATION MODELS & WHAT-IF
  // =========================================================================

  function renderValuationTab(a) {
    const t = tokens();
    const v = a.valuation;
    const m = a.market;

    $("#bd-val-strip").innerHTML = `
      <div class="bctc-kpi-box"><div class="bctc-kpi-lbl">Thị giá</div><div class="bctc-kpi-val">${fmtPrice(m.current_price)}</div></div>
      <div class="bctc-kpi-box"><div class="bctc-kpi-lbl">FV BCTC tĩnh</div><div class="bctc-kpi-val" style="color:var(--text-muted)">${fmtPrice(v.pure_fair_value)}</div></div>
      <div class="bctc-kpi-box"><div class="bctc-kpi-lbl">FV Chỉnh Vĩ mô</div><div class="bctc-kpi-val" style="color:${t.emerald}">${fmtPrice(v.fair_value)}</div></div>
      <div class="bctc-kpi-box"><div class="bctc-kpi-lbl">Upside thực</div><div class="bctc-kpi-val" style="color:${v.upside_pct >= 0 ? t.emerald : t.rose}">${fmtPct(v.upside_pct, true)}</div></div>`;

    const tbody = $("#table-bd-val-models tbody");
    if (tbody) {
      tbody.innerHTML = (v.models || [])
        .map(
          (mod) => `
          <tr>
            <td style="font-family:var(--font-sans)"><strong>${esc(mod.method)}</strong></td>
            <td>${Math.round(mod.weight * 100)}%</td>
            <td><strong style="color:${t.accent}">${fmtPrice(mod.fair_value)}</strong></td>
            <td style="text-align:left;font-family:var(--font-sans);color:var(--text-muted)">${esc(mod.formula)}</td>
          </tr>`
        )
        .join("");
    }

    const bands = $("#bd-val-bands");
    if (bands) {
      const ov = a.macro_fusion.valuation_overlay;
      bands.innerHTML = `
        <div class="subcard-title" style="margin-bottom:6px">Bối cảnh Lịch sử &amp; Điều chỉnh Vĩ mô</div>
        <div style="font-size:12px;color:var(--text-muted);line-height:1.6">
          • P/E hiện tại: <strong class="mono">${m.pe || "—"}x</strong> · TB 5N: <strong class="mono">${v.pe_bands.mean || "—"}x</strong> (Band ${v.pe_bands.low_band || "—"}x – ${v.pe_bands.high_band || "—"}x)<br/>
          • P/B hiện tại: <strong class="mono">${m.pb || "—"}x</strong> · TB 5N: <strong class="mono">${v.pb_bands.mean || "—"}x</strong> (Band ${v.pb_bands.low_band || "—"}x – ${v.pb_bands.high_band || "—"}x)<br/>
          • Chi phí vốn Ke: nền <strong class="mono">${(ov.base_ke * 100).toFixed(1)}%</strong> → thực tế <strong class="mono" style="color:${t.amber}">${(ov.live_ke * 100).toFixed(1)}%</strong> (${ov.ke_shift_bps >= 0 ? "+" : ""}${ov.ke_shift_bps}bps)
        </div>`;
    }
    renderValuationChart(a);
  }

  function renderValuationChart(a) {
    const ch = getChart("chart-bctc-val");
    if (!ch) return;
    const t = tokens();
    const v = a.valuation;
    const labels = ["Thị giá", "FV BCTC tĩnh", ...(v.models || []).map((x) => x.method), "FV Chỉnh Vĩ mô"];
    const vals = [a.market.current_price, v.pure_fair_value, ...(v.models || []).map((x) => x.fair_value), v.fair_value];
    const colors = [t.amber, t.subtle, ...(v.models || []).map(() => t.accent), t.emerald];

    ch.setOption({
      tooltip: { ...baseTip(), valueFormatter: (val) => fmtPrice(val) },
      grid: { left: 165, right: 24, top: 14, bottom: 26 },
      xAxis: { type: "value", axisLabel: { color: t.muted, fontSize: 10.5 }, splitLine: { lineStyle: { color: t.grid } } },
      yAxis: { type: "category", data: labels, axisLabel: { color: t.text, fontSize: 11 }, axisLine: { lineStyle: { color: t.grid } } },
      series: [
        {
          type: "bar",
          barMaxWidth: 20,
          data: vals.map((val, i) => ({ value: val, itemStyle: { color: colors[i], borderRadius: [0, 4, 4, 0] } })),
        },
      ],
    }, true);
  }

  // =========================================================================
  // SUBTAB 5: BUY-SIDE MEMO & AI Q&A
  // =========================================================================

  function renderMd(md) {
    let html = esc(md || "");
    html = html.replace(/((?:\|.*\|\n?)+)/g, (block) => {
      const lines = block.trim().split("\n").filter((l) => l.trim().startsWith("|"));
      if (lines.length < 2) return block;
      const head = lines[0].split("|").slice(1, -1).map((c) => `<th>${c.trim()}</th>`).join("");
      const body = lines.slice(2).map((row) => `<tr>${row.split("|").slice(1, -1).map((c) => `<td>${c.trim()}</td>`).join("")}</tr>`).join("");
      return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
    });
    html = html
      .replace(/^### (.*)$/gm, "<h3>$1</h3>")
      .replace(/^## (.*)$/gm, "<h2>$1</h2>")
      .replace(/^# (.*)$/gm, "<h1>$1</h1>")
      .replace(/^&gt; (.*)$/gm, "<blockquote>$1</blockquote>")
      .replace(/^---$/gm, "<hr style='border:0;border-top:1px solid var(--border);margin:12px 0'/>")
      .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
      .replace(/\*(.*?)\*/g, "<em>$1</em>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/^- (.*)$/gm, "<li>$1</li>")
      .replace(/^\d+\. (.*)$/gm, "<li>$1</li>");
    html = html.replace(/(<li>.*<\/li>\n?)+/g, (m) => `<ul>${m}</ul>`);
    return html.replace(/\n{2,}/g, "<br/>");
  }

  async function loadOfflineMemo(sym) {
    try {
      const res = await apiPost("/api/bctc/ai-analyze", { ticker: sym, provider: "offline", scenario: bState.scenario });
      bState.lastMemoMd = res.content;
      $("#bd-memo-title").textContent = `Báo cáo Hợp nhất ${sym} — ${res.model}`;
      $("#bd-memo-output").innerHTML = renderMd(res.content);
    } catch (_) {}
  }

  async function runAiMemo(question = null) {
    if (!bState.currentTicker) return;
    const provider = $("#bd-ai-provider")?.value || "offline";
    const model = ($("#bd-ai-model")?.value || "").trim();
    const apiKey = ($("#bd-ai-key")?.value || "").trim();
    const baseUrl = ($("#bd-ai-url")?.value || "").trim();
    localStorage.setItem("vimo_ai_prov", provider);
    localStorage.setItem("vimo_ai_model", model);
    localStorage.setItem("vimo_ai_url", baseUrl);
    if (apiKey) localStorage.setItem("vimo_ai_key", apiKey);

    const out = $("#bd-memo-output");
    if (out) out.innerHTML = `<div class="empty-state">Đang phân tích hợp nhất BCTC × Vĩ mô cho ${esc(bState.currentTicker)}...</div>`;
    try {
      const res = await apiPost("/api/bctc/ai-analyze", {
        ticker: bState.currentTicker,
        provider,
        api_key: apiKey,
        model,
        base_url: baseUrl,
        question,
        scenario: bState.scenario,
      });
      bState.lastMemoMd = res.content;
      $("#bd-memo-title").textContent = `Báo cáo Hợp nhất ${bState.currentTicker} — ${res.model}`;
      if (out) out.innerHTML = renderMd(res.content);
    } catch (err) {
      if (out) out.innerHTML = `<div class="bctc-flag-item high"><div class="bctc-flag-title">Lỗi AI API</div><div class="bctc-flag-detail">${esc(err.message)}</div></div>`;
    }
  }

  // =========================================================================
  // EVENT BINDING & LIFECYCLE
  // =========================================================================

  function bindOnce() {
    if (bState.initialized) return;
    bState.initialized = true;

    // Scenario switcher (Live / Dovish / Shock)
    $("#bctc-scenario-group")?.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-scenario]");
      if (!btn) return;
      $$("#bctc-scenario-group button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      bState.scenario = btn.dataset.scenario;
      loadPortfolio();
      if (bState.currentTicker && !$("#bctc-detail-view")?.classList.contains("hidden")) {
        openTicker(bState.currentTicker);
      }
    });

    // Click row in matrix -> deep dive
    $("#table-bctc-matrix tbody")?.addEventListener("click", (e) => {
      const tr = e.target.closest("tr[data-ticker]");
      if (tr) openTicker(tr.dataset.ticker);
    });

    // Quick ticker chips & back button
    $("#btn-bctc-back")?.addEventListener("click", () => {
      setViewMode("matrix");
      if (bState.portfolio) renderPortfolio(bState.portfolio);
    });
    $("#bctc-ticker-chips")?.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-chip-tk]");
      if (btn) openTicker(btn.dataset.chipTk);
    });

    // Scrape single ticker
    const doScrape = async () => {
      const inp = $("#bctc-scrape-input");
      const sym = (inp?.value || "").trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
      if (!sym) return;
      showStatus(`Đang cào BCTC chính thức từ Vietcap IQ & SSI cho ${sym}...`);
      try {
        await apiPost("/api/bctc/scrape", { ticker: sym, scenario: bState.scenario });
        if (inp) inp.value = "";
        showStatus(`Đã lưu & phân tích BCTC × Vĩ mô cho ${sym}!`);
        await loadPortfolio();
        await openTicker(sym);
      } catch (err) {
        showStatus(`Không cào được ${sym}: ${err.message}`, true);
      }
    };
    $("#btn-bctc-scrape")?.addEventListener("click", doScrape);
    $("#bctc-scrape-input")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); doScrape(); }
    });

    // Refresh 8 representative sectors
    $("#btn-bctc-refresh-all")?.addEventListener("click", async () => {
      showStatus("Đang cập nhật song song BCTC mới nhất cho 8 mã đại diện ngành (VCB, SSI, HPG, DPM, FPT, MWG, VNM, KDH)...");
      try {
        const res = await apiPost("/api/bctc/scrape", { tickers: ["VCB", "SSI", "HPG", "DPM", "FPT", "MWG", "VNM", "KDH"] });
        showStatus(`Đã cập nhật xong ${(res.success || []).length}/8 mã đại diện ngành!`);
        await loadPortfolio();
      } catch (err) {
        showStatus(`Lỗi cập nhật 8 ngành: ${err.message}`, true);
      }
    });

    // Sub-tabs
    $("#bctc-subtabs")?.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-subtab]");
      if (btn) setSubtab(btn.dataset.subtab);
    });

    // Chart period toggle
    $("#bctc-chart-period-group")?.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-period]");
      if (!btn) return;
      $$("#bctc-chart-period-group button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      bState.chartPeriod = btn.dataset.period;
      if (bState.currentAnalysis) renderDetailCharts(bState.currentAnalysis);
    });

    // Statement controls
    $("#bctc-stmt-sec-group")?.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-sec]");
      if (!btn) return;
      $$("#bctc-stmt-sec-group button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      bState.stmtSection = btn.dataset.sec;
      loadStatements();
    });
    $("#bctc-stmt-per-group")?.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-per]");
      if (!btn) return;
      $$("#bctc-stmt-per-group button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      bState.stmtPeriod = btn.dataset.per;
      loadStatements();
    });
    $("#bctc-stmt-search")?.addEventListener("input", renderStatements);
    $("#btn-bctc-export")?.addEventListener("click", () => {
      if (bState.currentTicker) {
        window.location.href = `/api/bctc/export/${encodeURIComponent(bState.currentTicker)}?period=${bState.stmtPeriod}`;
      }
    });

    // What-if valuation sliders
    const bindSl = (id, lbl, suf, zeroTxt = null) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.addEventListener("input", () => {
        const v = Number(el.value);
        document.getElementById(lbl).textContent = zeroTxt && v === 0 ? zeroTxt : `${v.toFixed(1)}${suf}`;
      });
    };
    bindSl("inp-bd-wacc", "lbl-bd-wacc", "%");
    bindSl("inp-bd-g5", "lbl-bd-g5", "%");
    bindSl("inp-bd-gt", "lbl-bd-gt", "%");
    bindSl("inp-bd-roe", "lbl-bd-roe", "%");
    bindSl("inp-bd-pe", "lbl-bd-pe", "x", "Tự động");

    $("#btn-bd-run-val")?.addEventListener("click", () => {
      if (!bState.currentTicker) return;
      const p = new URLSearchParams({
        wacc: Number($("#inp-bd-wacc").value) / 100,
        growth_5y: Number($("#inp-bd-g5").value) / 100,
        terminal_growth: Number($("#inp-bd-gt").value) / 100,
        target_roe: Number($("#inp-bd-roe").value) / 100,
      });
      const pe = Number($("#inp-bd-pe").value);
      if (pe > 0) p.set("target_pe", pe);
      openTicker(bState.currentTicker, `?${p.toString()}`);
    });
    $("#btn-bd-reset-val")?.addEventListener("click", () => {
      if (bState.currentTicker) openTicker(bState.currentTicker);
    });

    // AI config & buttons
    const prov = $("#bd-ai-provider");
    if (prov) {
      prov.value = localStorage.getItem("vimo_ai_prov") || "offline";
      $("#bd-ai-model").value = localStorage.getItem("vimo_ai_model") || "";
      $("#bd-ai-key").value = localStorage.getItem("vimo_ai_key") || "";
      $("#bd-ai-url").value = localStorage.getItem("vimo_ai_url") || "";
      const syncProv = () => $("#bd-ai-url-wrap")?.classList.toggle("hidden", prov.value !== "openai");
      syncProv();
      prov.addEventListener("change", syncProv);
    }
    $("#btn-bd-gen-memo")?.addEventListener("click", () => runAiMemo(null));
    $("#btn-bd-ask")?.addEventListener("click", () => {
      const q = ($("#bd-ai-question")?.value || "").trim();
      if (q) runAiMemo(q);
    });
    $("#bd-ai-question")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); $("#btn-bd-ask")?.click(); }
    });
    $$(".bd-quick-q").forEach((b) => {
      b.addEventListener("click", () => {
        if ($("#bd-ai-question")) $("#bd-ai-question").value = b.dataset.q;
        runAiMemo(b.dataset.q);
      });
    });
    $("#btn-bd-copy-memo")?.addEventListener("click", () => {
      if (!bState.lastMemoMd) return;
      navigator.clipboard?.writeText(bState.lastMemoMd);
      const btn = $("#btn-bd-copy-memo");
      if (btn) {
        btn.textContent = "Đã sao chép ✓";
        setTimeout(() => (btn.textContent = "Sao chép Markdown"), 1500);
      }
    });
  }

  window.BctcModule = {
    onShow() {
      bindOnce();
      if (!bState.portfolio) {
        loadPortfolio();
      } else {
        requestAnimationFrame(() => {
          if (!$("#bctc-detail-view")?.classList.contains("hidden") && bState.currentAnalysis) {
            renderDetail(bState.currentAnalysis);
          } else {
            renderPortfolio(bState.portfolio);
          }
          resizeCharts();
        });
      }
    },
    onThemeChange() {
      if (!bState.initialized) return;
      if (bState.portfolio && !$("#bctc-matrix-view")?.classList.contains("hidden")) {
        renderPortfolio(bState.portfolio);
      }
      if (bState.currentAnalysis && !$("#bctc-detail-view")?.classList.contains("hidden")) {
        renderDetail(bState.currentAnalysis);
        if (bState.activeSubtab === "charts") renderDetailCharts(bState.currentAnalysis);
        if (bState.activeSubtab === "statements") renderStatements();
      }
    },
    onResize() {
      resizeCharts();
    },
    async openFromOutside(ticker) {
      bindOnce();
      if (!bState.portfolio) await loadPortfolio();
      await openTicker(ticker);
    },
  };
})();
