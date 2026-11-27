/* ==========================================================================
   VĨ MÔ PULSE × WYCKOFF ENGINE — Interactive Wyckoff VSA & Macro Confluence UI
   3-Panel Synchronized ECharts (Candles+TR+Events / VSA Volume / OBV + RS)
   ========================================================================== */

(() => {
  "use strict";

  const wState = {
    initialized: false,
    radar: null,
    currentSymbol: "VNINDEX",
    currentDetail: null,
    barsCount: 120,
    chart: null,
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

  function fmtPx(v, isIndex = false) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
    const n = Number(v);
    if (isIndex || n < 5000) {
      return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    return Math.round(n).toLocaleString("vi-VN") + "₫";
  }

  function fmtSigned(v, d = 1, suf = "") {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
    const n = Number(v);
    return `${n > 0 ? "+" : ""}${n.toFixed(d)}${suf}`;
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
      trFill: isLight ? "rgba(3, 105, 161, 0.08)" : "rgba(56, 189, 248, 0.09)",
      accent: isLight ? "#0369a1" : "#38bdf8",
      emerald: isLight ? "#047857" : "#34d399",
      rose: isLight ? "#be123c" : "#fb7185",
      amber: isLight ? "#b45309" : "#fbbf24",
      violet: isLight ? "#6d28d9" : "#c084fc",
    };
  }

  function showStatus(msg, isErr = false) {
    const el = $("#wy-status-msg");
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

  function ensureChart() {
    if (typeof window.echarts === "undefined") return null;
    const el = document.getElementById("chart-wyckoff-main");
    if (!el || el.offsetParent === null) return null;
    if (!wState.chart) {
      wState.chart = window.echarts.init(el);
    }
    return wState.chart;
  }

  function resizeChart() {
    if (wState.chart && wState.chart.getDom() && wState.chart.getDom().offsetParent !== null) {
      wState.chart.resize();
    }
  }

  async function loadRadar() {
    showStatus("Đang quét chu kỳ Wyckoff × Vĩ mô cho 4 chỉ số & toàn rổ cổ phiếu...");
    try {
      const data = await apiGet("/api/wyckoff/radar");
      wState.radar = data;
      showStatus("");
      renderRadar(data);
      if (data.default_detail && !wState.currentDetail) {
        wState.currentSymbol = data.default_detail.symbol;
        wState.currentDetail = data.default_detail;
        renderDetail(data.default_detail);
      } else if (wState.currentSymbol) {
        await loadSymbol(wState.currentSymbol);
      }
    } catch (err) {
      showStatus(`Lỗi tải Radar Wyckoff: ${err.message}`, true);
    }
  }

  async function loadSymbol(sym) {
    const clean = String(sym || "").trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
    if (!clean) return;
    wState.currentSymbol = clean;
    showStatus(`Đang phân tích Wyckoff VSA × Vĩ mô cho ${clean}...`);
    try {
      const res = await apiGet(`/api/wyckoff/analyze/${encodeURIComponent(clean)}`);
      wState.currentDetail = res;
      showStatus("");
      renderDetail(res);
      syncActiveChips(clean);
    } catch (err) {
      showStatus(`Không phân tích được Wyckoff cho ${clean}: ${err.message}`, true);
    }
  }

  function syncActiveChips(sym) {
    $$("#wy-quick-chips button[data-wy-sym]").forEach((b) => {
      b.classList.toggle("active", b.dataset.wySym === sym);
    });
  }

  function renderRadar(data) {
    const t = tokens();
    const mc = data.macro_context || {};
    const tag = $("#wy-radar-macro-tag");
    if (tag) {
      tag.textContent = `La bàn Vĩ mô: ${mc.macro_score}/100 (${mc.stance_short}) · US10Y ${Number(mc.us_10y).toFixed(2)}% · VIX ${Number(mc.vix).toFixed(1)}`;
    }

    // Populate stock quick chips
    const stockChips = $("#wy-stock-chips");
    if (stockChips) {
      stockChips.innerHTML = (data.stocks || [])
        .map(
          (s) =>
            `<button type="button" class="pill-btn ${wState.currentSymbol === s.symbol ? "active" : ""}" data-wy-sym="${esc(s.symbol)}">${esc(s.symbol)}</button>`
        )
        .join("");
    }

    // Populate Scanner Table (Indices first, then stocks)
    const tbody = $("#table-wyckoff-radar tbody");
    if (!tbody) return;
    const all = [...(data.indices || []), ...(data.stocks || [])];
    tbody.innerHTML = all
      .map((r) => {
        const rsStr = r.rs_20d !== null && r.rs_20d !== undefined ? fmtSigned(r.rs_20d, 1, "%") : "—";
        const rsCol = r.rs_20d > 0 ? t.emerald : r.rs_20d < 0 ? t.rose : t.muted;
        const udCol = r.ud_vol_20 >= 1.15 ? t.emerald : r.ud_vol_20 < 0.85 ? t.rose : t.amber;
        const evCls = r.latest_event_kind === "bull" ? "emerald" : r.latest_event_kind === "bear" ? "rose" : "accent";
        const confCol = r.confluence_score >= 65 ? t.emerald : r.confluence_score >= 48 ? t.amber : t.rose;
        return `
          <tr data-wy-row="${esc(r.symbol)}">
            <td>
              <span class="ticker-chip">${esc(r.symbol)}</span>
              ${r.is_index ? `<span style="font-size:10px;color:var(--text-subtle);margin-left:4px">IDX</span>` : ""}
            </td>
            <td><strong>${fmtPx(r.close, r.is_index)}</strong></td>
            <td style="text-align:left"><span class="wy-stage-pill ${esc(r.stage_tone)}">${esc(r.stage_label)}</span></td>
            <td style="text-align:left;font-family:var(--font-sans);font-size:11.6px;color:var(--text-muted)">${esc(r.sub_phase)}</td>
            <td>${r.pos_in_tr.toFixed(0)}%</td>
            <td style="color:${udCol};font-weight:700">${r.ud_vol_20.toFixed(2)}x</td>
            <td style="color:${rsCol};font-weight:700">${rsStr}</td>
            <td><span class="wy-stage-pill ${evCls}">${esc(r.latest_event)}</span></td>
            <td>${r.wyckoff_score}</td>
            <td style="color:${confCol};font-weight:800;font-size:13px">${r.confluence_score}</td>
            <td style="text-align:left"><span class="wy-stage-pill ${esc(r.align_tone)}">${esc(r.action_verdict)}</span></td>
          </tr>`;
      })
      .join("");
  }

  function renderDetail(d) {
    if (!d) return;
    const t = tokens();
    const st = d.stage;
    const tr = d.trading_range;
    const f = d.fusion;
    const sc = d.scores;

    $("#wy-sym-title").textContent = d.symbol;
    $("#wy-sym-name").textContent = d.name;
    $("#wy-date").textContent = d.date;
    $("#wy-price").textContent = fmtPx(d.close, d.is_index);

    const pctEl = $("#wy-pct1d");
    pctEl.textContent = `1 phiên: ${fmtSigned(d.pct_1d, 2, "%")} · Vị trí trong hộp TR: ${tr.position_pct}%`;
    pctEl.style.color = d.pct_1d >= 0 ? t.emerald : t.rose;

    const stPill = $("#wy-stage-pill");
    stPill.textContent = st.label;
    stPill.className = `wy-stage-pill ${st.tone}`;
    $("#wy-subphase-pill").textContent = st.sub_phase;

    const confEl = $("#wy-conf-score");
    confEl.textContent = sc.confluence;
    confEl.style.color = sc.confluence >= 65 ? t.emerald : sc.confluence >= 48 ? t.amber : t.rose;
    $("#wy-score-split").textContent = `Wyckoff ${sc.wyckoff} · Vĩ mô ${sc.macro}`;

    // Highlight active stage in 4-Stage Cycle Progress Bar
    $$("#wy-cycle-bar .wy-stage-step").forEach((el) => {
      el.classList.toggle("active", el.dataset.stage === st.code);
    });

    // Confluence Action Callout
    const call = $("#wy-fusion-callout");
    const callCls = f.align_tone === "emerald" ? "good" : f.align_tone === "rose" ? "bad" : f.align_tone === "amber" ? "warn" : "";
    call.className = `bctc-summary-callout mt-sm ${callCls}`;
    call.innerHTML = `
      <strong>[${esc(f.alignment)}] → ${esc(f.action_verdict)}</strong><br/>
      <span><strong>Lọc Vĩ mô:</strong> ${esc(f.macro_filter_note)}</span><br/>
      <span><strong>Quản trị vị thế:</strong> ${esc(f.sizing_rule)}</span>
    `;

    // Chart Header Badge
    $("#wy-chart-title").textContent = `${d.symbol} — Đồ thị Wyckoff VSA · Trading Range · Volume · ${d.is_index ? "OBV" : "OBV & RS vs VN-Index"}`;
    $("#wy-tr-badge").textContent = `Ice (Hỗ trợ): ${fmtPx(tr.ice_support, d.is_index)} · Creek (Kháng cự): ${fmtPx(tr.creek_resistance, d.is_index)} (Biên ${tr.width_pct}%)`;

    // Law 1: Supply & Demand
    const l1 = d.laws.supply_demand;
    const ud20Col = l1.up_down_vol_20 >= 1.15 ? t.emerald : l1.up_down_vol_20 < 0.85 ? t.rose : t.amber;
    const divCol = l1.obv_div_tone === "positive" ? t.emerald : l1.obv_div_tone === "negative" ? t.rose : t.muted;
    const flowExtra =
      l1.foreign_5d_bn !== null && l1.foreign_5d_bn !== undefined
        ? `<div class="wy-law-row"><span>Khối ngoại 5 phiên (HOSE)</span><strong style="color:${l1.foreign_5d_bn >= 0 ? t.emerald : t.rose}">${l1.foreign_5d_bn >= 0 ? "+" : ""}${Math.round(l1.foreign_5d_bn).toLocaleString("vi-VN")} tỷ</strong></div>`
        : "";
    const rsExtra =
      l1.rs_20d !== null && l1.rs_20d !== undefined
        ? `<div class="wy-law-row"><span>Sức mạnh Tương đối RS vs VNI (20p / 60p)</span><strong style="color:${l1.rs_20d >= 0 ? t.emerald : t.rose}">${fmtSigned(l1.rs_20d, 1, "%")} / ${fmtSigned(l1.rs_60d, 1, "%")}</strong></div>`
        : "";
    $("#wy-law1-body").innerHTML = `
      <div class="wy-law-row"><span>Tỷ lệ Vol Tăng / Vol Giảm (20 phiên)</span><strong style="color:${ud20Col}">${l1.up_down_vol_20.toFixed(2)}x</strong></div>
      <div class="wy-law-row"><span>Tỷ lệ Vol Tăng / Vol Giảm (60 phiên)</span><strong>${l1.up_down_vol_60.toFixed(2)}x</strong></div>
      <div class="wy-law-row"><span>Xu hướng dòng tiền OBV</span><strong>${esc(l1.obv_trend)}</strong></div>
      <div class="wy-law-row"><span>Phân kỳ OBV vs Giá</span><strong style="color:${divCol}">${esc(l1.obv_divergence)}</strong></div>
      ${rsExtra}
      ${flowExtra}
    `;

    // Law 2: Cause & Effect
    const l2 = d.laws.cause_effect;
    $("#wy-law2-body").innerHTML = `
      <div class="wy-law-row"><span>Nguyên nhân (Số phiên tích lũy/phân phối)</span><strong>${l2.cause_sessions} phiên</strong></div>
      <div class="wy-law-row"><span>Độ rộng Hộp Trading Range (Ice ↔ Creek)</span><strong>${l2.tr_width_pct}%</strong></div>
      <div class="wy-law-row"><span>Cạnh dưới Ice (Hỗ trợ nền)</span><strong style="color:${t.emerald}">${fmtPx(tr.ice_support, d.is_index)}</strong></div>
      <div class="wy-law-row"><span>Đường giữa (Midline TR)</span><strong>${fmtPx(tr.midline, d.is_index)}</strong></div>
      <div class="wy-law-row"><span>Cạnh trên Creek (Kháng cự đột phá)</span><strong style="color:${t.rose}">${fmtPx(tr.creek_resistance, d.is_index)}</strong></div>
      <div class="wy-law-row"><span>Mục tiêu Đẩy giá (Markup Target)</span><strong style="color:${t.emerald}">${fmtPx(l2.markup_target, d.is_index)} (${fmtSigned(l2.upside_to_target_pct, 1, "%")})</strong></div>
      <div class="wy-law-row"><span>Rủi ro Đè giá (Markdown Risk)</span><strong style="color:${t.rose}">${fmtPx(l2.markdown_risk, d.is_index)} (${fmtSigned(l2.downside_to_risk_pct, 1, "%")})</strong></div>
    `;

    // Law 3 & Triggers + Event Log
    const l3 = d.laws.effort_result;
    $("#wy-trigger-box").innerHTML = `
      <div><strong style="color:${t.emerald}">Kích hoạt Mua:</strong> ${esc(f.entry_trigger)}</div>
      <div><strong style="color:${t.rose}">Vô hiệu hóa / Cắt rủi ro:</strong> Đóng cửa gãy sâu dưới <strong class="mono">${fmtPx(f.invalidation_level, d.is_index)}</strong> · Vol gần nhất <strong class="mono">${l3.latest_rvol}x MA20</strong> (25p: ${l3.bull_events_25d} cầu / ${l3.bear_events_25d} cung)</div>
    `;

    const evs = l3.events || [];
    $("#wy-events-log").innerHTML = evs.length
      ? evs
          .slice(0, 8)
          .map(
            (ev) => `
            <div class="wy-ev-item ${esc(ev.kind)}">
              <div class="wy-ev-top">
                <span class="wy-ev-code ${esc(ev.kind)}">${esc(ev.code)} · ${esc(ev.label)}</span>
                <span class="mono" style="font-size:10.8px;color:var(--text-subtle)">${esc(ev.date)} · ${ev.rvol}x Vol</span>
              </div>
              <div class="wy-ev-desc">${esc(ev.desc)}</div>
            </div>`
          )
          .join("")
      : `<div class="empty-state" style="padding:14px">Chưa phát hiện nến VSA đột biến trong vùng quan sát gần đây.</div>`;

    renderWyckoffChart(d);
  }

  function renderWyckoffChart(d) {
    const ch = ensureChart();
    if (!ch || !d?.chart?.bars?.length) return;
    const t = tokens();
    const bars = d.chart.bars.slice(-wState.barsCount);
    const dates = bars.map((b) => b.date);
    const dateSet = new Set(dates);
    const tr = d.trading_range;

    // Event MarkPoints inside active date range
    const markPts = (d.chart.events || [])
      .filter((e) => dateSet.has(e.date))
      .map((e) => {
        const isBull = e.kind === "bull";
        return {
          name: e.code,
          coord: [e.date, e.price],
          value: e.code,
          symbol: "pin",
          symbolSize: 34,
          symbolRotate: isBull ? 180 : 0,
          itemStyle: { color: isBull ? t.emerald : t.rose },
          label: {
            color: "#ffffff",
            fontSize: 9.5,
            fontWeight: 800,
            fontFamily: "JetBrains Mono, sans-serif",
            rotate: 0,
            offset: [0, isBull ? 4 : -4],
          },
        };
      });

    const hasRs = !d.is_index && bars.some((b) => b.rs !== null && b.rs !== undefined);

    const series = [
      {
        name: d.symbol,
        type: "candlestick",
        xAxisIndex: 0,
        yAxisIndex: 0,
        itemStyle: { color: t.emerald, color0: t.rose, borderColor: t.emerald, borderColor0: t.rose },
        data: bars.map((b) => [b.o, b.c, b.l, b.h]),
        markArea: {
          silent: true,
          itemStyle: { color: t.trFill },
          data: [[{ yAxis: tr.ice_support }, { yAxis: tr.creek_resistance }]],
        },
        markLine: {
          silent: true,
          symbol: "none",
          data: [
            {
              yAxis: tr.creek_resistance,
              lineStyle: { type: "dashed", color: t.rose, width: 1.4 },
              label: { formatter: `Creek ${fmtPx(tr.creek_resistance, d.is_index)}`, color: t.rose, fontSize: 10.5, position: "insideStartTop" },
            },
            {
              yAxis: tr.ice_support,
              lineStyle: { type: "dashed", color: t.emerald, width: 1.4 },
              label: { formatter: `Ice ${fmtPx(tr.ice_support, d.is_index)}`, color: t.emerald, fontSize: 10.5, position: "insideStartBottom" },
            },
          ],
        },
        markPoint: { data: markPts },
      },
      {
        name: "MA20",
        type: "line",
        xAxisIndex: 0,
        yAxisIndex: 0,
        showSymbol: false,
        lineStyle: { width: 1.4, color: t.accent },
        itemStyle: { color: t.accent },
        data: bars.map((b) => b.ma20),
      },
      {
        name: "MA50",
        type: "line",
        xAxisIndex: 0,
        yAxisIndex: 0,
        showSymbol: false,
        lineStyle: { width: 1.7, color: t.amber },
        itemStyle: { color: t.amber },
        data: bars.map((b) => b.ma50),
      },
      {
        name: "MA200",
        type: "line",
        xAxisIndex: 0,
        yAxisIndex: 0,
        showSymbol: false,
        lineStyle: { width: 1.8, type: "dotted", color: t.violet },
        itemStyle: { color: t.violet },
        data: bars.map((b) => b.ma200),
      },
      {
        name: "Khối lượng (VSA)",
        type: "bar",
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: bars.map((b) => {
          const up = b.c >= b.o;
          const isClimax = b.rvol >= 1.5;
          const isDry = b.rvol <= 0.72;
          let col = up ? t.emerald + "88" : t.rose + "88";
          if (isClimax) col = up ? t.emerald : t.rose;
          else if (isDry) col = t.subtle + "66";
          return { value: b.v, itemStyle: { color: col } };
        }),
      },
      {
        name: "Vol MA20",
        type: "line",
        xAxisIndex: 1,
        yAxisIndex: 1,
        showSymbol: false,
        lineStyle: { width: 1.4, color: t.amber },
        itemStyle: { color: t.amber },
        data: bars.map((b) => b.vol_ma20),
      },
      {
        name: "OBV (Cung-Cầu)",
        type: "line",
        xAxisIndex: 2,
        yAxisIndex: 2,
        showSymbol: false,
        lineStyle: { width: 2.0, color: t.accent },
        itemStyle: { color: t.accent },
        data: bars.map((b) => b.obv),
      },
    ];

    if (hasRs) {
      series.push({
        name: "RS vs VN-Index",
        type: "line",
        xAxisIndex: 2,
        yAxisIndex: 3,
        showSymbol: false,
        lineStyle: { width: 2.0, color: t.emerald },
        itemStyle: { color: t.emerald },
        data: bars.map((b) => b.rs),
      });
    } else {
      series.push({
        name: "OBV MA20",
        type: "line",
        xAxisIndex: 2,
        yAxisIndex: 2,
        showSymbol: false,
        lineStyle: { width: 1.4, type: "dashed", color: t.amber },
        itemStyle: { color: t.amber },
        data: bars.map((b) => b.obv_ma20),
      });
    }

    const yAxes = [
      { type: "value", gridIndex: 0, scale: true, axisLabel: { color: t.muted, fontSize: 10.5 }, splitLine: { lineStyle: { color: t.grid } } },
      { type: "value", gridIndex: 1, splitNumber: 2, axisLabel: { show: false }, splitLine: { show: false } },
      { type: "value", gridIndex: 2, scale: true, splitNumber: 2, axisLabel: { show: false }, splitLine: { lineStyle: { color: t.grid } } },
    ];
    if (hasRs) {
      yAxes.push({
        type: "value",
        gridIndex: 2,
        scale: true,
        splitNumber: 2,
        position: "right",
        axisLabel: { color: t.emerald, fontSize: 9.5 },
        splitLine: { show: false },
      });
    }

    ch.setOption({
      tooltip: {
        trigger: "axis",
        backgroundColor: t.tooltipBg,
        borderColor: t.tooltipBorder,
        borderWidth: 1,
        textStyle: { color: t.text, fontSize: 11.8, fontFamily: "JetBrains Mono, Inter, sans-serif" },
        axisPointer: { type: "cross", link: [{ xAxisIndex: "all" }] },
      },
      legend: { top: 0, textStyle: { color: t.muted, fontSize: 11 } },
      grid: [
        { left: 64, right: 44, top: 34, height: "50%" },
        { left: 64, right: 44, top: "61%", height: "15%" },
        { left: 64, right: 44, top: "80%", height: "15%" },
      ],
      xAxis: [
        { type: "category", gridIndex: 0, data: dates, axisLabel: { show: false }, axisLine: { lineStyle: { color: t.grid } }, axisTick: { show: false } },
        { type: "category", gridIndex: 1, data: dates, axisLabel: { show: false }, axisLine: { lineStyle: { color: t.grid } }, axisTick: { show: false } },
        { type: "category", gridIndex: 2, data: dates, axisLabel: { color: t.muted, fontSize: 10 }, axisLine: { lineStyle: { color: t.grid } } },
      ],
      yAxis: yAxes,
      series,
    }, true);
  }

  function bindOnce() {
    if (wState.initialized) return;
    wState.initialized = true;

    $("#wy-bars-group")?.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-bars]");
      if (!btn) return;
      $$("#wy-bars-group button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      wState.barsCount = Number(btn.dataset.bars) || 120;
      if (wState.currentDetail) renderWyckoffChart(wState.currentDetail);
    });

    $("#wy-quick-chips")?.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-wy-sym]");
      if (btn) loadSymbol(btn.dataset.wySym);
    });

    const runInput = () => {
      const inp = $("#wy-symbol-input");
      const sym = (inp?.value || "").trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
      if (!sym) return;
      if (inp) inp.value = "";
      loadSymbol(sym);
    };
    $("#btn-wy-analyze")?.addEventListener("click", runInput);
    $("#wy-symbol-input")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); runInput(); }
    });

    $("#table-wyckoff-radar tbody")?.addEventListener("click", (e) => {
      const tr = e.target.closest("tr[data-wy-row]");
      if (!tr) return;
      loadSymbol(tr.dataset.wyRow);
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }

  window.WyckoffModule = {
    onShow() {
      bindOnce();
      if (!wState.radar) {
        loadRadar();
      } else {
        requestAnimationFrame(() => {
          renderRadar(wState.radar);
          if (wState.currentDetail) renderDetail(wState.currentDetail);
          resizeChart();
        });
      }
    },
    onThemeChange() {
      if (!wState.initialized) return;
      if (wState.radar) renderRadar(wState.radar);
      if (wState.currentDetail) renderDetail(wState.currentDetail);
    },
    onResize() {
      resizeChart();
    },
    async openFromOutside(sym) {
      bindOnce();
      if (!wState.radar) await loadRadar();
      await loadSymbol(sym);
    },
  };
})();
