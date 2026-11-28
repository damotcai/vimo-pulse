/* ==========================================================================
   VĨ MÔ PULSE v8 — Pro Modules Engine
   1. Siêu Ma trận Hội tụ 3-in-1 (Vĩ mô × BCTC × Wyckoff VSA)
   2. Quản trị Danh mục Thực tế (Portfolio Tracker + Risk Guard + JSON Backup)
   3. So sánh Đối thủ Cùng Ngành (Peer Comparison + Multi-Peer Radar Chart)
   4. Trung tâm Cảnh báo Realtime (Browser Notification + Telegram Bot) & PWA
   5. Báo cáo Sáng CIO 1-Click (Modal + In PDF + Gửi Telegram)
   ========================================================================== */

(() => {
  "use strict";

  const STORAGE_PF = "vimo_portfolio_v1";
  const STORAGE_TG = "vimo_telegram_v1";
  const STORAGE_ALERTS = "vimo_alert_prefs_v1";

  const pState = {
    initialized: false,
    fusionScenario: "live",
    fusionTier: "ALL",
    fusionData: null,
    positions: loadJson(STORAGE_PF, []),
    pfEval: null,
    peerPreset: "BANK",
    peerData: null,
    briefData: null,
    charts: {},
    deferredInstallPrompt: null,
    notifiedKeys: new Set(),
    watchdogTimer: null,
  };

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  function loadJson(key, fallback) {
    try {
      const raw = JSON.parse(localStorage.getItem(key) || "null");
      if (raw !== null && raw !== undefined) return raw;
    } catch (_) {}
    return fallback;
  }

  function saveJson(key, val) {
    try {
      localStorage.setItem(key, JSON.stringify(val));
    } catch (_) {}
  }

  function esc(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fmtVnd(v) {
    if (v === null || v === undefined || Number.isNaN(Number(v)) || Number(v) === 0) return "—";
    return Math.round(Number(v)).toLocaleString("vi-VN") + "₫";
  }

  function fmtMillion(v) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
    const m = Number(v) / 1e6;
    return `${m.toLocaleString("vi-VN", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} tr`;
  }

  function fmtSigned(v, d = 1, suf = "") {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
    const n = Number(v);
    return `${n > 0 ? "+" : ""}${n.toFixed(d)}${suf}`;
  }

  function fmtNum(v, d = 1, suf = "") {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
    return Number(v).toLocaleString("vi-VN", { minimumFractionDigits: d, maximumFractionDigits: d }) + suf;
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
      cardBg: isLight ? "#ffffff" : "#111a2c",
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
    if (pState.charts[id] && pState.charts[id].getDom() !== el) {
      try { pState.charts[id].dispose(); } catch (_) {}
      delete pState.charts[id];
    }
    if (!pState.charts[id]) {
      pState.charts[id] = window.echarts.init(el);
    }
    return pState.charts[id];
  }

  function resizeCharts() {
    Object.values(pState.charts).forEach((ch) => {
      if (ch && ch.getDom() && ch.getDom().offsetParent !== null) {
        ch.resize();
      }
    });
  }

  async function apiGet(url) {
    const r = await fetch(url, { cache: "no-store" });
    const d = await r.json();
    if (!r.ok || d.error) throw new Error(d.error || `HTTP ${r.status}`);
    return d;
  }

  async function apiPost(url, payload) {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}),
    });
    const d = await r.json();
    if (!r.ok || d.error) throw new Error(d.error || `HTTP ${r.status}`);
    return d;
  }

  function setStatus(elId, msg, isErr = false) {
    const el = document.getElementById(elId);
    if (!el) return;
    if (!msg) {
      el.classList.add("hidden");
      return;
    }
    el.className = `bctc-status-msg${isErr ? " err" : ""}`;
    el.textContent = msg;
  }

  // =========================================================================
  // 1. UNIFIED 3-IN-1 CONVERGENCE SCREENER (VĨ MÔ × BCTC × WYCKOFF)
  // =========================================================================

  async function loadFusionMatrix(force = false) {
    setStatus("fusion-status-msg", "Đang hợp nhất 3 trụ cột (Vĩ mô × BCTC × Wyckoff VSA) cho toàn bộ rổ cổ phiếu...");
    try {
      const data = await apiGet(`/api/fusion/matrix?scenario=${encodeURIComponent(pState.fusionScenario)}&force=${force ? "1" : "0"}`);
      pState.fusionData = data;
      setStatus("fusion-status-msg", "");
      renderFusionMatrix(data);
    } catch (err) {
      setStatus("fusion-status-msg", `Lỗi tải Siêu Ma trận 3-in-1: ${err.message}`, true);
    }
  }

  function renderFusionMatrix(data) {
    if (!data) return;
    const t = tokens();
    const mc = data.macro_context || {};
    const c = data.counts || {};
    const rows = data.rows || [];

    const kpiStrip = $("#fusion-kpi-strip");
    if (kpiStrip) {
      kpiStrip.innerHTML = `
        <div class="bctc-kpi-box">
          <div class="bctc-kpi-lbl">La bàn Vĩ mô (25%)</div>
          <div class="bctc-kpi-val" style="color:${mc.macro_score >= 60 ? t.emerald : mc.macro_score < 45 ? t.rose : t.amber}">${mc.macro_score ?? "—"}/100</div>
          <div class="bctc-kpi-sub">${esc(mc.stance_short || "—")} · CP ${mc.equity_budget_pct ?? "—"}%</div>
        </div>
        <div class="bctc-kpi-box">
          <div class="bctc-kpi-lbl">💎 Hội tụ Kim cương</div>
          <div class="bctc-kpi-val" style="color:${t.emerald}">${c.DIAMOND ?? 0} mã</div>
          <div class="bctc-kpi-sub">Đủ 3 trụ: BCTC + Rẻ + Wyckoff khỏe</div>
        </div>
        <div class="bctc-kpi-box">
          <div class="bctc-kpi-lbl">🎯 Canh gom Hội tụ</div>
          <div class="bctc-kpi-val" style="color:${t.accent}">${c.ACCUMULATE ?? 0} mã</div>
          <div class="bctc-kpi-sub">Nền tích lũy / Đẩy giá · Chờ điểm đẹp</div>
        </div>
        <div class="bctc-kpi-box">
          <div class="bctc-kpi-lbl">⚠️ Cảnh báo Bẫy Giá trị</div>
          <div class="bctc-kpi-val" style="color:${t.amber}">${c.VALUE_TRAP ?? 0} mã</div>
          <div class="bctc-kpi-sub">BCTC rẻ nhưng Wyckoff Đè/Phân phối</div>
        </div>`;
    }

    const sumEl = $("#fusion-summary");
    if (sumEl) {
      const cls = (c.DIAMOND || 0) > 0 ? "good" : (c.VALUE_TRAP || 0) >= 3 ? "warn" : "";
      sumEl.className = `bctc-summary-callout ${cls}`;
      sumEl.textContent = data.summary || "";
    }

    // Top highlights & Value Trap alert box
    const hlEl = $("#fusion-highlights");
    if (hlEl) {
      const top3 = rows.slice(0, 3);
      const traps = rows.filter((r) => r.tier_code === "VALUE_TRAP").slice(0, 3);
      hlEl.innerHTML = `
        <div class="fusion-hl-group">
          <div class="fusion-hl-head">🏆 Top 3 Hội tụ Mạnh nhất Hiện tại</div>
          ${top3
            .map(
              (r) => `
            <div class="fusion-hl-item">
              <div>
                <strong class="ticker-chip">${esc(r.ticker)}</strong>
                <span class="wy-stage-pill ${esc(r.tier_tone)}" style="margin-left:6px">${esc(r.tier_label)}</span>
                <div class="fusion-hl-sub">${esc(r.sector_vi)} · Upside ${fmtSigned(r.upside_pct, 1, "%")} · ${esc(r.wyckoff_stage_label)}</div>
              </div>
              <div class="mono" style="text-align:right">
                <strong style="font-size:15px;color:${r.triple_score >= 65 ? t.emerald : t.accent}">${r.triple_score.toFixed(1)}</strong>
                <div style="font-size:10px;color:var(--text-subtle)">3-in-1</div>
              </div>
            </div>`
            )
            .join("")}
        </div>
        ${
          traps.length
            ? `<div class="fusion-hl-group mt-sm">
                <div class="fusion-hl-head" style="color:${t.amber}">⚠️ Bẫy Giá trị cần kiên nhẫn (Chưa bắt dao rơi)</div>
                ${traps
                  .map(
                    (r) => `
                  <div class="fusion-hl-item warn">
                    <div>
                      <strong class="ticker-chip">${esc(r.ticker)}</strong>
                      <span style="margin-left:6px;font-size:11.6px;font-weight:700">BCTC ${r.bctc_score.toFixed(0)}đ (${fmtSigned(r.upside_pct, 0, "%")})</span>
                      <div class="fusion-hl-sub">Nhưng Wyckoff: <strong>${esc(r.wyckoff_stage_label)}</strong> · Chờ SC/AR/ST</div>
                    </div>
                    <button type="button" class="btn-open-bctc-mini" data-open-wyckoff="${esc(r.ticker)}">Soi KT</button>
                  </div>`
                  )
                  .join("")}
              </div>`
            : ""
        }`;
    }

    // Filtered rows for table
    const filtered = rows.filter((r) => pState.fusionTier === "ALL" || r.tier_code === pState.fusionTier);
    const tbody = $("#table-fusion-matrix tbody");
    if (tbody) {
      if (!filtered.length) {
        tbody.innerHTML = `<tr><td colspan="10" class="empty-state">Không có cổ phiếu nào thuộc nhóm bộ lọc này ở bối cảnh vĩ mô hiện tại.</td></tr>`;
      } else {
        tbody.innerHTML = filtered
          .map((r) => {
            const upCol = r.upside_pct >= 10 ? t.emerald : r.upside_pct < 0 ? t.rose : t.amber;
            const rsCol = r.rs_20d > 0 ? t.emerald : r.rs_20d < 0 ? t.rose : t.muted;
            const scCol = r.triple_score >= 68 ? t.emerald : r.triple_score >= 54 ? t.accent : r.triple_score >= 45 ? t.amber : t.rose;
            return `
              <tr data-fusion-tk="${esc(r.ticker)}">
                <td><strong>${esc(r.ticker)}</strong><div style="font-size:10.5px;color:var(--text-subtle)">${esc(r.sector_vi)}</div></td>
                <td><span class="wy-stage-pill ${esc(r.tier_tone)}">${esc(r.tier_label)}</span></td>
                <td style="font-weight:800;font-size:13.5px;color:${scCol}">${r.triple_score.toFixed(1)}</td>
                <td>${fmtVnd(r.price)}<div style="font-size:10.5px;color:var(--text-subtle)">FV: ${fmtVnd(r.fair_value_macro)}</div></td>
                <td style="font-weight:700;color:${upCol}">${fmtSigned(r.upside_pct, 1, "%")}</td>
                <td><strong>${r.bctc_score.toFixed(0)}</strong><div style="font-size:10.5px;color:var(--text-subtle)">Vĩ mô ${r.macro_fit.toFixed(0)}</div></td>
                <td><span class="wy-stage-pill ${esc(r.wyckoff_tone)}">${esc(r.wyckoff_stage_label)}</span><div style="font-size:10.5px;color:var(--text-subtle)">${r.wyckoff_score.toFixed(0)}đ · ${esc(r.latest_event)}</div></td>
                <td style="color:${rsCol};font-weight:700">${r.rs_20d != null ? fmtSigned(r.rs_20d, 1, "%") : "—"}</td>
                <td style="font-size:11.2px">Mua: <strong>${fmtVnd(r.buy_zone_1)}</strong><div style="font-size:10.5px;color:var(--text-subtle)">TP: ${fmtVnd(r.take_profit_1)} · SL: ${fmtVnd(r.stop_loss)}</div></td>
                <td>
                  <div style="display:flex;gap:4px;justify-content:flex-end">
                    <button type="button" class="btn-open-bctc-mini" data-open-bctc="${esc(r.ticker)}">BCTC</button>
                    <button type="button" class="btn-open-bctc-mini" data-open-wyckoff="${esc(r.ticker)}">Wyckoff</button>
                  </div>
                </td>
              </tr>`;
          })
          .join("");
      }
    }

    renderFusionQuadrantChart(rows);
  }

  function renderFusionQuadrantChart(rows) {
    const ch = getChart("chart-fusion-quadrant");
    if (!ch || !rows.length) return;
    const t = tokens();
    const toneMap = {
      emerald: t.emerald,
      accent: t.accent,
      amber: t.amber,
      violet: t.violet,
      rose: t.rose,
    };

    ch.setOption(
      {
        tooltip: {
          trigger: "item",
          backgroundColor: t.tooltipBg,
          borderColor: t.tooltipBorder,
          borderWidth: 1,
          textStyle: { color: t.text, fontSize: 12 },
          formatter: (p) => {
            const r = p.data.raw;
            return (
              `<strong>${esc(r.ticker)}</strong> · ${esc(r.tier_label)}<br/>` +
              `Điểm 3-in-1: <strong>${r.triple_score.toFixed(1)}/100</strong><br/>` +
              `BCTC × Vĩ mô: ${r.bctc_score.toFixed(1)} · Wyckoff: ${r.wyckoff_score.toFixed(1)} (${esc(r.wyckoff_stage_label)})<br/>` +
              `Upside thực: ${fmtSigned(r.upside_pct, 1, "%")} · RS 20p: ${r.rs_20d != null ? fmtSigned(r.rs_20d, 1, "%") : "—"}`
            );
          },
        },
        grid: { left: 48, right: 24, top: 28, bottom: 42 },
        xAxis: {
          type: "value",
          name: "Điểm BCTC × Vĩ mô",
          nameLocation: "middle",
          nameGap: 25,
          min: 20,
          max: 100,
          nameTextStyle: { color: t.muted, fontSize: 11 },
          axisLabel: { color: t.muted },
          splitLine: { lineStyle: { color: t.grid } },
        },
        yAxis: {
          type: "value",
          name: "Wyckoff VSA",
          min: 15,
          max: 100,
          nameTextStyle: { color: t.muted, fontSize: 11 },
          axisLabel: { color: t.muted },
          splitLine: { lineStyle: { color: t.grid } },
        },
        series: [
          {
            type: "scatter",
            symbolSize: (val, params) => {
              const up = Math.max(0, Math.min(45, params.data.raw.upside_pct || 0));
              return Math.round(16 + up * 0.48);
            },
            label: {
              show: true,
              position: "top",
              formatter: (p) => p.data.raw.ticker,
              color: t.text,
              fontWeight: 700,
              fontSize: 11,
              fontFamily: "JetBrains Mono, sans-serif",
            },
            labelLayout: { moveOverlap: "shiftY" },
            markLine: {
              silent: true,
              symbol: "none",
              lineStyle: { type: "dashed", color: t.subtle, width: 1.2 },
              data: [
                { xAxis: 60, label: { formatter: "BCTC ≥ 60", color: t.subtle, fontSize: 10, position: "insideEndTop", distance: 6 } },
                { yAxis: 55, label: { formatter: "Wyckoff ≥ 55", color: t.subtle, fontSize: 10, position: "insideEndTop", distance: 4 } },
              ],
            },
            markArea: {
              silent: true,
              data: [
                [
                  { xAxis: 60, yAxis: 55, itemStyle: { color: t.emerald + "14" } },
                  { xAxis: 100, yAxis: 100 },
                ],
                [
                  { xAxis: 60, yAxis: 15, itemStyle: { color: t.amber + "12" } },
                  { xAxis: 100, yAxis: 55 },
                ],
              ],
            },
            data: rows.map((r) => ({
              value: [r.bctc_score, r.wyckoff_score],
              raw: r,
              itemStyle: { color: toneMap[r.tier_tone] || t.accent, borderColor: t.cardBg, borderWidth: 1.5 },
            })),
          },
        ],
      },
      true
    );
  }

  // =========================================================================
  // 2. PORTFOLIO TRACKER & AUTOMATED RISK GUARD (LOCALSTORAGE + LIVE P&L)
  // =========================================================================

  function savePositions() {
    saveJson(STORAGE_PF, pState.positions);
  }

  async function evaluatePortfolio() {
    if (!pState.positions.length) {
      pState.pfEval = null;
      renderPortfolio(null);
      return;
    }
    setStatus("pf-status-msg", `Đang định giá danh mục thực tế (${pState.positions.length} vị thế) & quét cảnh báo Ice/TP...`);
    try {
      const data = await apiPost("/api/portfolio/evaluate", {
        positions: pState.positions,
        scenario: pState.fusionScenario,
      });
      pState.pfEval = data;
      setStatus("pf-status-msg", "");
      renderPortfolio(data);
      checkPortfolioAlerts(data);
    } catch (err) {
      setStatus("pf-status-msg", `Lỗi tính P&L danh mục: ${err.message}`, true);
    }
  }

  function renderPortfolio(evalData) {
    const t = tokens();
    const totalsEl = $("#pf-totals-strip");
    const alertsEl = $("#pf-alerts-list");
    const tbody = $("#table-portfolio tbody");

    if (!pState.positions.length) {
      if (totalsEl) {
        totalsEl.innerHTML = `
          <div class="bctc-kpi-box"><div class="bctc-kpi-lbl">Tổng Vốn Đầu tư</div><div class="bctc-kpi-val">0₫</div><div class="bctc-kpi-sub">Nhập mã đang nắm giữ ở trên</div></div>
          <div class="bctc-kpi-box"><div class="bctc-kpi-lbl">Giá trị Thị trường (NAV)</div><div class="bctc-kpi-val">0₫</div><div class="bctc-kpi-sub">Tự đồng bộ giá Live</div></div>
          <div class="bctc-kpi-box"><div class="bctc-kpi-lbl">Lãi / Lỗ Tạm tính (P&amp;L)</div><div class="bctc-kpi-val">—</div><div class="bctc-kpi-sub">Cảnh báo Stop-loss &amp; TP tự động</div></div>
          <div class="bctc-kpi-box"><div class="bctc-kpi-lbl">Cảnh báo Quản trị Rủi ro</div><div class="bctc-kpi-val">0</div><div class="bctc-kpi-sub">Kiểm soát Ice Wyckoff &amp; Ngành</div></div>`;
      }
      if (alertsEl) alertsEl.innerHTML = "";
      if (tbody) {
        tbody.innerHTML = `<tr><td colspan="9" class="empty-state">Danh mục thực tế đang trống. Hãy nhập cổ phiếu bạn đang cầm ở form trên (lưu riêng tư trên máy của bạn) hoặc nhấn <strong>Tải mẫu thử (FPT, VCB, HPG)</strong>.</td></tr>`;
      }
      const ch = pState.charts["chart-pf-sectors"];
      if (ch) ch.clear();
      return;
    }

    if (!evalData) return;
    const tot = evalData.totals || {};
    const pnlCol = tot.pnl_vnd > 0 ? t.emerald : tot.pnl_vnd < 0 ? t.rose : t.muted;
    const alerts = evalData.alerts || [];

    if (totalsEl) {
      totalsEl.innerHTML = `
        <div class="bctc-kpi-box">
          <div class="bctc-kpi-lbl">Tổng Vốn Đầu tư</div>
          <div class="bctc-kpi-val">${fmtMillion(tot.cost_vnd)}</div>
          <div class="bctc-kpi-sub">${tot.positions_count || 0} vị thế đang giữ</div>
        </div>
        <div class="bctc-kpi-box">
          <div class="bctc-kpi-lbl">Giá trị Thị trường (NAV)</div>
          <div class="bctc-kpi-val" style="color:${t.accent}">${fmtMillion(tot.market_vnd)}</div>
          <div class="bctc-kpi-sub">Cập nhật ${esc(evalData.updated_at_vn?.split("·")[0] || "")}</div>
        </div>
        <div class="bctc-kpi-box">
          <div class="bctc-kpi-lbl">Lãi / Lỗ Realtime (P&amp;L)</div>
          <div class="bctc-kpi-val" style="color:${pnlCol}">${tot.pnl_vnd >= 0 ? "+" : ""}${fmtMillion(tot.pnl_vnd)}</div>
          <div class="bctc-kpi-sub" style="color:${pnlCol};font-weight:700">${fmtSigned(tot.pnl_pct, 2, "%")}</div>
        </div>
        <div class="bctc-kpi-box">
          <div class="bctc-kpi-lbl">Cảnh báo Risk Guard</div>
          <div class="bctc-kpi-val" style="color:${alerts.length ? t.rose : t.emerald}">${alerts.length} cảnh báo</div>
          <div class="bctc-kpi-sub">${alerts.length ? "Xem chi tiết ngay bên dưới" : "Danh mục an toàn, kỷ luật tốt"}</div>
        </div>`;
    }

    if (alertsEl) {
      if (!alerts.length) {
        alertsEl.innerHTML = `<div class="bctc-flag-item ok"><div class="bctc-flag-title">✅ Không có cảnh báo rủi ro nghiêm trọng</div><div class="bctc-flag-detail">Các vị thế trong danh mục chưa vi phạm hỗ trợ Ice Wyckoff và chưa vượt giới hạn tập trung ngành (&gt;45%).</div></div>`;
      } else {
        alertsEl.innerHTML = alerts
          .map(
            (a) => `
          <div class="bctc-flag-item ${a.severity === "high" ? "high" : ""}">
            <div class="bctc-flag-title">${esc(a.title)}</div>
            <div class="bctc-flag-detail">${esc(a.detail)}</div>
          </div>`
          )
          .join("");
      }
    }

    if (tbody) {
      tbody.innerHTML = (evalData.items || [])
        .map((it) => {
          const pCol = it.pnl_vnd > 0 ? t.emerald : it.pnl_vnd < 0 ? t.rose : t.muted;
          return `
            <tr>
              <td><strong>${esc(it.ticker)}</strong><div style="font-size:10.5px;color:var(--text-subtle)">${esc(it.sector_vi)}</div></td>
              <td>${Number(it.qty).toLocaleString("vi-VN")}<div style="font-size:10.5px;color:var(--text-subtle)">${it.weight_pct.toFixed(1)}% NAV</div></td>
              <td>${fmtVnd(it.avg_price)}<div style="font-size:10.5px;color:var(--text-subtle)">Vốn ${fmtMillion(it.cost_vnd)}</div></td>
              <td><strong>${fmtVnd(it.current_price)}</strong><div style="font-size:10.5px;color:var(--text-subtle)">${it.pct_1d != null ? fmtSigned(it.pct_1d, 2, "%") : ""}</div></td>
              <td style="color:${pCol};font-weight:800">${it.pnl_vnd >= 0 ? "+" : ""}${fmtMillion(it.pnl_vnd)}<div style="font-size:10.5px">${fmtSigned(it.pnl_pct, 2, "%")}</div></td>
              <td><span class="wy-stage-pill ${esc(it.wyckoff_tone)}">${esc(it.wyckoff_stage_label)}</span><div style="font-size:10.5px;color:var(--text-subtle)">${esc(it.latest_event)}</div></td>
              <td style="font-size:11px">SL: <strong style="color:${t.rose}">${fmtVnd(it.stop_ref)}</strong><div style="font-size:10.5px;color:var(--text-subtle)">TP1: ${fmtVnd(it.take_profit_1)}</div></td>
              <td><span class="wy-stage-pill ${esc(it.guard_tone)}" title="${esc(it.guard_detail)}">${esc(it.guard_label)}</span></td>
              <td>
                <div style="display:flex;gap:4px;justify-content:flex-end">
                  <button type="button" class="btn-open-bctc-mini" data-open-bctc="${esc(it.ticker)}">BCTC</button>
                  <button type="button" class="btn-open-bctc-mini" data-open-wyckoff="${esc(it.ticker)}">KT</button>
                  <button type="button" class="btn-pf-del" data-pf-del="${esc(it.ticker)}" title="Xóa vị thế">&times;</button>
                </div>
              </td>
            </tr>`;
        })
        .join("");
    }

    // Sector Concentration Donut Chart
    const ch = getChart("chart-pf-sectors");
    const sw = evalData.sector_weights || [];
    if (ch && sw.length) {
      const palette = [t.accent, t.emerald, t.amber, t.violet, t.rose, "#06b6d4", "#84cc16"];
      ch.setOption(
        {
          tooltip: {
            trigger: "item",
            backgroundColor: t.tooltipBg,
            borderColor: t.tooltipBorder,
            textStyle: { color: t.text, fontSize: 12 },
            formatter: (p) => `<strong>${esc(p.name)}</strong>: ${p.value}% (${fmtMillion(p.data.valVnd)})`,
          },
          legend: { bottom: 0, type: "scroll", textStyle: { color: t.muted, fontSize: 11 } },
          series: [
            {
              type: "pie",
              radius: ["48%", "74%"],
              center: ["50%", "44%"],
              itemStyle: { borderRadius: 5, borderColor: t.cardBg, borderWidth: 2 },
              label: { show: true, formatter: "{b}\n{d}%", color: t.text, fontSize: 10.5 },
              data: sw.map((s, idx) => ({
                name: s.sector,
                value: s.weight_pct,
                valVnd: s.value_vnd,
                itemStyle: { color: palette[idx % palette.length] },
              })),
            },
          ],
        },
        true
      );
    }
  }

  // =========================================================================
  // 3. PEER COMPARISON ENGINE (SO SÁNH ĐỐI THỦ CÙNG NGÀNH TRONG TAB BCTC)
  // =========================================================================

  async function loadPeerComparison({ preset = "", tickers = "" } = {}) {
    setStatus("peer-status-msg", "Đang tải & đồng bộ BCTC + Định giá chỉnh Vĩ mô + Wyckoff cho nhóm đối thủ (lần đầu cào mã mới có thể mất 5–10 giây)...");
    try {
      const scenario = document.querySelector("#bctc-scenario-group .seg-btn.active")?.dataset.scenario || "live";
      const qs = preset
        ? `preset=${encodeURIComponent(preset)}&scenario=${encodeURIComponent(scenario)}`
        : `tickers=${encodeURIComponent(tickers)}&scenario=${encodeURIComponent(scenario)}`;
      const data = await apiGet(`/api/bctc/peers?${qs}`);
      pState.peerData = data;
      setStatus("peer-status-msg", "");
      renderPeerComparison(data);
    } catch (err) {
      setStatus("peer-status-msg", `Lỗi so sánh cùng ngành: ${err.message}`, true);
    }
  }

  function renderPeerComparison(data) {
    if (!data) return;
    const t = tokens();
    const peers = (data.peers || []).filter((p) => p.ok);
    const failed = (data.peers || []).filter((p) => !p.ok);
    if (failed.length) {
      setStatus("peer-status-msg", `Lưu ý: Không tải được dữ liệu cho ${failed.map((f) => f.ticker).join(", ")}.`, true);
    }

    const ld = data.leaders || {};
    const ldGrid = $("#peer-leaders-grid");
    if (ldGrid) {
      ldGrid.innerHTML = `
        <div class="bctc-kpi-box">
          <div class="bctc-kpi-lbl">🏆 Dẫn đầu Tổng lực 3-in-1</div>
          <div class="bctc-kpi-val" style="color:${t.emerald}">${esc(ld.overall?.ticker || "—")}</div>
          <div class="bctc-kpi-sub">${esc(ld.overall?.value || "—")}</div>
        </div>
        <div class="bctc-kpi-box">
          <div class="bctc-kpi-lbl">💰 Biên an toàn Rẻ nhất</div>
          <div class="bctc-kpi-val" style="color:${t.accent}">${esc(ld.value?.ticker || "—")}</div>
          <div class="bctc-kpi-sub">Upside ${esc(ld.value?.value || "—")}</div>
        </div>
        <div class="bctc-kpi-box">
          <div class="bctc-kpi-lbl">⭐ Sinh lời ROE Cao nhất</div>
          <div class="bctc-kpi-val" style="color:${t.amber}">${esc(ld.quality?.ticker || "—")}</div>
          <div class="bctc-kpi-sub">${esc(ld.quality?.value || "—")}</div>
        </div>
        <div class="bctc-kpi-box">
          <div class="bctc-kpi-lbl">📈 Dòng tiền Wyckoff Khỏe nhất</div>
          <div class="bctc-kpi-val" style="color:${t.violet}">${esc(ld.wyckoff?.ticker || "—")}</div>
          <div class="bctc-kpi-sub">${esc(ld.wyckoff?.value || "—")}</div>
        </div>`;
    }

    const tbl = $("#table-peer-compare");
    if (tbl && peers.length) {
      const thead = tbl.querySelector("thead");
      const tbody = tbl.querySelector("tbody");
      thead.innerHTML = `<tr><th>Chỉ tiêu So sánh</th>${peers
        .map(
          (p) =>
            `<th><div style="font-size:13px;color:var(--accent)">${esc(p.ticker)}</div><div style="font-size:10px;font-weight:600;color:var(--text-subtle)">${esc(p.latest_period)}</div></th>`
        )
        .join("")}</tr>`;

      const rowSpecs = [
        { label: "Phân loại Hội tụ 3-in-1", render: (p) => `<span class="wy-stage-pill ${esc(p.tier_tone)}">${esc(p.tier_label)}</span>` },
        { label: "Điểm Tổng lực 3-in-1 (/100)", key: "triple_score", higherBetter: true, fmt: (v) => `${Number(v).toFixed(1)}` },
        { label: "Thị giá hiện tại", render: (p) => fmtVnd(p.price) },
        { label: "Giá trị hợp lý (Chỉnh Vĩ mô)", render: (p) => `<strong>${fmtVnd(p.fair_value_macro)}</strong>` },
        { label: "Biên an toàn / Upside thực", key: "upside_pct", higherBetter: true, fmt: (v) => fmtSigned(v, 1, "%") },
        { label: "Vốn hóa (Tỷ VND)", key: "market_cap_bn", higherBetter: true, fmt: (v) => `${Math.round(v).toLocaleString("vi-VN")}` },
        { label: "P/E (TTM)", key: "pe", higherBetter: false, positiveOnly: true, fmt: (v) => `${Number(v).toFixed(1)}x` },
        { label: "P/B", key: "pb", higherBetter: false, positiveOnly: true, fmt: (v) => `${Number(v).toFixed(2)}x` },
        { label: "ROE (%)", key: "roe", higherBetter: true, fmt: (v) => `${Number(v).toFixed(1)}%` },
        { label: "ROA (%)", key: "roa", higherBetter: true, fmt: (v) => `${Number(v).toFixed(2)}%` },
        { label: "Cổ tức Tiền mặt (%)", key: "dividend_yield", higherBetter: true, fmt: (v) => `${Number(v).toFixed(1)}%` },
        { label: "LNST Quý mới nhất (YoY)", key: "npat_yoy_q", higherBetter: true, fmt: (v) => fmtSigned(v, 1, "%") },
        { label: "CAGR Doanh thu 5N", key: "rev_cagr_5y", higherBetter: true, fmt: (v) => fmtSigned(v, 1, "%") },
        { label: "Điểm BCTC Chỉnh Vĩ mô", key: "bctc_score", higherBetter: true, fmt: (v) => `${Number(v).toFixed(1)}/100` },
        { label: "Độ phù hợp Vĩ mô", key: "macro_fit", higherBetter: true, fmt: (v) => `${Number(v).toFixed(0)}/100` },
        { label: "Pha Wyckoff VSA", render: (p) => `<span class="wy-stage-pill ${esc(p.wyckoff_tone)}">${esc(p.wyckoff_stage_label)}</span>` },
        { label: "Điểm Wyckoff & RS 20p", key: "wyckoff_score", higherBetter: true, fmt: (v, p) => `${Number(v).toFixed(0)} (${p.rs_20d != null ? fmtSigned(p.rs_20d, 1, "%") : "—"})` },
        { label: "Số cảnh báo Red Flags", key: "red_flags_count", higherBetter: false, fmt: (v) => `${v}` },
        {
          label: "Chỉ số Đặc thù Ngành (Top 2)",
          render: (p) =>
            (p.sector_highlights || [])
              .slice(0, 2)
              .map((h) => `<div style="font-size:10.8px">${esc(h.label.split("(")[0].trim())}: <strong>${esc(h.value)}</strong></div>`)
              .join("") || "—",
        },
        {
          label: "Mở phân tích chi tiết",
          render: (p) =>
            `<button type="button" class="btn-open-bctc-mini" data-open-bctc="${esc(p.ticker)}">BCTC</button> <button type="button" class="btn-open-bctc-mini" data-open-wyckoff="${esc(p.ticker)}">Wyckoff</button>`,
        },
      ];

      tbody.innerHTML = rowSpecs
        .map((spec) => {
          let bestVal = null;
          let worstVal = null;
          if (spec.key) {
            const nums = peers
              .map((p) => p[spec.key])
              .filter((v) => v !== null && v !== undefined && !Number.isNaN(Number(v)) && (!spec.positiveOnly || Number(v) > 0))
              .map(Number);
            if (nums.length >= 2) {
              bestVal = spec.higherBetter ? Math.max(...nums) : Math.min(...nums);
              worstVal = spec.higherBetter ? Math.min(...nums) : Math.max(...nums);
            }
          }
          return `<tr><td><strong>${esc(spec.label)}</strong></td>${peers
            .map((p) => {
              if (spec.render) return `<td>${spec.render(p)}</td>`;
              const raw = p[spec.key];
              if (raw === null || raw === undefined || Number.isNaN(Number(raw))) return `<td>—</td>`;
              const n = Number(raw);
              let style = "";
              if (bestVal !== null && n === bestVal && bestVal !== worstVal) {
                style = `color:${t.emerald};font-weight:800;background:var(--emerald-soft)`;
              } else if (worstVal !== null && n === worstVal && bestVal !== worstVal) {
                style = `color:${t.rose};font-weight:700`;
              }
              return `<td style="${style}">${spec.fmt(n, p)}</td>`;
            })
            .join("")}</tr>`;
        })
        .join("");
    }

    // Multi-Peer 6-Axis Radar Chart
    const ch = getChart("chart-peer-radar");
    if (ch && peers.length) {
      const palette = [t.accent, t.emerald, t.amber, t.violet, t.rose];
      ch.setOption(
        {
          tooltip: {
            backgroundColor: t.tooltipBg,
            borderColor: t.tooltipBorder,
            textStyle: { color: t.text, fontSize: 12 },
          },
          legend: { top: 0, type: "scroll", textStyle: { color: t.muted, fontSize: 11.5 } },
          radar: {
            center: ["50%", "56%"],
            radius: "65%",
            indicator: [
              { name: "Sinh lời", max: 100 },
              { name: "Tăng trưởng", max: 100 },
              { name: "An toàn TC", max: 100 },
              { name: "Định giá Vĩ mô", max: 100 },
              { name: "Hợp Vĩ mô", max: 100 },
              { name: "Wyckoff VSA", max: 100 },
            ],
            axisName: { color: t.muted, fontSize: 11, fontWeight: 700 },
            splitArea: { areaStyle: { color: ["transparent", t.grid] } },
            splitLine: { lineStyle: { color: t.grid } },
          },
          series: [
            {
              type: "radar",
              data: peers.map((p, idx) => {
                const sc = p.scores || {};
                const col = palette[idx % palette.length];
                return {
                  name: p.ticker,
                  value: [
                    sc.profitability || 50,
                    sc.growth || 50,
                    sc.health || 50,
                    sc.valuation || 50,
                    sc.macro_fit || 50,
                    p.wyckoff_score || 50,
                  ],
                  lineStyle: { width: 2.2, color: col },
                  itemStyle: { color: col },
                  areaStyle: { color: col + "18" },
                };
              }),
            },
          ],
        },
        true
      );
    }
  }

  // =========================================================================
  // 4. REALTIME ALERTS (BROWSER NOTIFICATIONS + TELEGRAM BOT) & PWA
  // =========================================================================

  function loadAlertPrefs() {
    return loadJson(STORAGE_ALERTS, { cal15: true, portfolio: true });
  }

  function updateNotifStatusUi() {
    const lbl = $("#notif-status");
    const btn = $("#btn-notif-enable");
    if (!lbl) return;
    if (!("Notification" in window)) {
      lbl.textContent = "Trình duyệt này chưa hỗ trợ Web Notification (vẫn dùng Telegram Bot bình thường).";
      if (btn) btn.disabled = true;
      return;
    }
    const perm = Notification.permission;
    if (perm === "granted") {
      lbl.textContent = "✅ Đã bật Thông báo Trình duyệt (Tự nhắc trước 15p tin Mỹ ★★★ & cảnh báo Stop-loss).";
      if (btn) btn.textContent = "Đã bật thông báo ✓";
    } else if (perm === "denied") {
      lbl.textContent = "⚠️ Thông báo đang bị chặn trong cài đặt trình duyệt — hãy cho phép để nhận cảnh báo.";
    } else {
      lbl.textContent = "Chưa bật thông báo trình duyệt — nhấn nút bên dưới để kích hoạt.";
    }
  }

  function fireBrowserNotification(title, body, uniqueKey) {
    if (uniqueKey && pState.notifiedKeys.has(uniqueKey)) return;
    if (uniqueKey) pState.notifiedKeys.add(uniqueKey);
    if ("Notification" in window && Notification.permission === "granted") {
      try {
        new Notification(title, { body, icon: "/static/icon-192.png" });
      } catch (_) {}
    }
  }

  function checkPortfolioAlerts(evalData) {
    const prefs = loadAlertPrefs();
    if (!prefs.portfolio || !evalData?.items) return;
    evalData.items.forEach((it) => {
      if (it.guard_code === "ALERT_STOP" || it.guard_code === "TAKE_PROFIT") {
        fireBrowserNotification(
          `VĨ MÔ PULSE · ${it.ticker}: ${it.guard_label}`,
          it.guard_detail,
          `pf:${it.ticker}:${it.guard_code}`
        );
      }
    });
  }

  async function runCalendarAlertCheck() {
    const prefs = loadAlertPrefs();
    if (!prefs.cal15) return;
    try {
      const cal = await apiGet("/api/calendar?force=0");
      const nowSec = Date.now() / 1000;
      (cal.events || []).forEach((ev) => {
        if (ev.upcoming && ev.ts) {
          const mins = (ev.ts - nowSec) / 60;
          if (mins > 0 && mins <= 16) {
            fireBrowserNotification(
              `⏰ Sắp ra tin Mỹ ★★★ (${Math.round(mins)} phút nữa)`,
              `${ev.title} (${ev.time_vn} GMT+7) · Dự báo: ${ev.forecast}`,
              `cal_soon:${ev.id || ev.title}:${ev.ts}`
            );
          }
        }
        if (ev.just_released && ev.has_actual) {
          fireBrowserNotification(
            `🚨 TIN MỸ ★★★ VỪA CÔNG BỐ: ${ev.title}`,
            `Thực tế: ${ev.actual} | Dự báo: ${ev.forecast} (${ev.surprise_label || ""})`,
            `cal_live:${ev.id || ev.title}:${ev.actual}`
          );
        }
      });
    } catch (_) {}
  }

  function initTelegramUi() {
    const tg = loadJson(STORAGE_TG, { bot_token: "", chat_id: "" });
    if ($("#tg-bot-token")) $("#tg-bot-token").value = tg.bot_token || "";
    if ($("#tg-chat-id")) $("#tg-chat-id").value = tg.chat_id || "";
  }

  function getTelegramCreds() {
    return {
      bot_token: ($("#tg-bot-token")?.value || loadJson(STORAGE_TG, {}).bot_token || "").trim(),
      chat_id: ($("#tg-chat-id")?.value || loadJson(STORAGE_TG, {}).chat_id || "").trim(),
    };
  }

  async function sendTelegramText(text, statusId = "tg-status-msg") {
    const { bot_token, chat_id } = getTelegramCreds();
    if (!bot_token || !chat_id) {
      setStatus(statusId, "Vui lòng nhập Telegram Bot Token và Chat ID trước (tạo miễn phí qua @BotFather & @userinfobot).", true);
      return false;
    }
    setStatus(statusId, "Đang gửi tin nhắn tới Telegram Bot của bạn...");
    try {
      const res = await apiPost("/api/alerts/telegram", { bot_token, chat_id, text });
      setStatus(statusId, `✅ Đã gửi thành công tới Telegram lúc ${res.sent_at_vn}!`);
      return true;
    } catch (err) {
      setStatus(statusId, `❌ ${err.message}`, true);
      return false;
    }
  }

  // =========================================================================
  // 5. 1-CLICK EXECUTIVE MORNING BRIEF MODAL & PRINT PDF
  // =========================================================================

  async function openMorningBriefModal() {
    const modal = $("#brief-modal");
    if (!modal) return;
    modal.classList.remove("hidden");
    document.body.classList.add("modal-open");
    const sheet = $("#brief-print-sheet");
    if (sheet) sheet.innerHTML = `<div class="empty-state">Đang tổng hợp Bản tin Sáng CIO (Vĩ mô · VN-Index · Tin Mỹ ★★★ · Hội tụ 3-in-1 · Danh mục)...</div>`;
    try {
      const data = await apiPost("/api/briefing/morning", {
        scenario: pState.fusionScenario,
        positions: pState.positions,
      });
      pState.briefData = data;
      renderMorningBriefSheet(data);
    } catch (err) {
      if (sheet) sheet.innerHTML = `<div class="empty-state">Lỗi tạo báo cáo: ${esc(err.message)}</div>`;
    }
  }

  function closeMorningBriefModal() {
    $("#brief-modal")?.classList.add("hidden");
    document.body.classList.remove("modal-open");
  }

  function renderMorningBriefSheet(d) {
    const sheet = $("#brief-print-sheet");
    if (!sheet || !d) return;
    const mc = d.macro_context || {};
    const ms = d.market_snapshot || {};
    const pf = d.portfolio;

    sheet.innerHTML = `
      <div class="brief-doc-head">
        <div>
          <div class="brief-doc-eyebrow">VĨ MÔ PULSE × BCTC × WYCKOFF PRO · CIO EXECUTIVE BRIEF</div>
          <h2 class="brief-doc-title">Bản tin Điều hành Sáng &amp; Kế hoạch Giao dịch</h2>
        </div>
        <div class="brief-doc-meta mono">
          <div>${esc(d.generated_at_vn)} (GMT+7)</div>
          <div>La bàn Vĩ mô: <strong>${mc.macro_score ?? "—"}/100</strong> (${esc(mc.stance_short || "—")})</div>
        </div>
      </div>

      <div class="brief-doc-grid">
        <div class="brief-doc-box">
          <h4>1. La bàn Vĩ mô &amp; Thị trường Việt Nam</h4>
          <ul>
            <li><strong>Chu kỳ:</strong> ${esc(mc.regime_name || "—")} · <strong>Ngân sách CP:</strong> ${mc.equity_budget_pct ?? "—"}%</li>
            <li><strong>VN-Index:</strong> ${ms.vni ? `${Number(ms.vni).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} (${fmtSigned(ms.vni_pct, 2, "%")})` : "—"} · <strong>P/E VNI:</strong> ${ms.pe_vni ? `${ms.pe_vni}x` : "—"}</li>
            <li><strong>Toàn cầu:</strong> US 10Y ${fmtNum(mc.us_10y, 2, "%")} · VIX ${fmtNum(mc.vix, 1)} · DXY ${fmtNum(mc.dxy, 1)} · Fed: ${esc(mc.fed_bias || "—")}</li>
            <li><strong>Việt Nam:</strong> PMI ${fmtNum(mc.vn_pmi, 1)} · Ngoại 5p ${fmtSigned(mc.foreign_5d_bn, 0, " tỷ")} · VCB USD ${ms.vcb_usd_sell ? fmtVnd(ms.vcb_usd_sell) : "—"}${ms.sjc_sell_m ? ` · SJC ${ms.sjc_sell_m.toFixed(1)}tr` : ""}</li>
          </ul>
          ${d.strategy_summary ? `<p class="brief-doc-callout">${esc(d.strategy_summary)}</p>` : ""}
        </div>

        <div class="brief-doc-box">
          <h4>2. Lịch Kinh tế Mỹ ★★★ (Tác động Mạnh · GMT+7)</h4>
          ${
            (d.upcoming_calendar || []).length
              ? `<ul>${d.upcoming_calendar
                  .map(
                    (ev) =>
                      `<li><span class="mono"><strong>${esc(ev.date_vn?.slice(0, 5))} ${esc(ev.time_vn)}</strong></span> — ${esc(ev.title)} <em>(DB: ${esc(ev.forecast)})</em></li>`
                  )
                  .join("")}</ul>`
              : `<p>Không có sự kiện Mỹ 3★ sắp công bố trong khung ngắn hạn.</p>`
          }
          ${
            (d.recent_calendar || []).length
              ? `<div style="margin-top:6px;font-size:11.5px"><strong>Mới công bố gần nhất:</strong> ${d.recent_calendar
                  .map((e) => `${esc(e.title.split("·")[0].trim())}: <strong>${esc(e.actual)}</strong> (DB ${esc(e.forecast)})`)
                  .join(" · ")}</div>`
              : ""
          }
        </div>
      </div>

      <div class="brief-doc-box mt-sm">
        <h4>3. Top Cổ phiếu Hội tụ 3-trong-1 (Vĩ mô × BCTC × Wyckoff VSA)</h4>
        <div class="table-wrap">
          <table class="data-table mono-nums">
            <thead>
              <tr>
                <th>Mã</th><th>Phân loại</th><th>Điểm 3-in-1</th><th>Thị giá</th><th>FV Chỉnh Vĩ mô</th><th>Upside</th><th>Wyckoff</th><th>Vùng Mua / TP1 / SL</th>
              </tr>
            </thead>
            <tbody>
              ${(d.fusion_top || [])
                .map(
                  (r) => `
                <tr>
                  <td><strong>${esc(r.ticker)}</strong></td>
                  <td>${esc(r.tier_label)}</td>
                  <td><strong>${r.triple_score.toFixed(1)}</strong></td>
                  <td>${fmtVnd(r.price)}</td>
                  <td>${fmtVnd(r.fair_value_macro)}</td>
                  <td>${fmtSigned(r.upside_pct, 1, "%")}</td>
                  <td>${esc(r.wyckoff_stage_label)}</td>
                  <td>${fmtVnd(r.buy_zone_1)} / ${fmtVnd(r.take_profit_1)} / ${fmtVnd(r.stop_loss)}</td>
                </tr>`
                )
                .join("")}
            </tbody>
          </table>
        </div>
        ${
          (d.value_traps || []).length
            ? `<div style="margin-top:8px;font-size:12px"><strong>⚠️ Cảnh báo Bẫy Giá trị (BCTC tốt nhưng Wyckoff chưa tạo đáy):</strong> ${d.value_traps
                .map((r) => `<strong>${esc(r.ticker)}</strong> (${esc(r.wyckoff_stage_label)})`)
                .join(", ")}</div>`
            : ""
        }
      </div>

      ${
        pf && pf.totals?.positions_count > 0
          ? `<div class="brief-doc-box mt-sm">
              <h4>4. Danh mục Thực tế của Bạn (${pf.totals.positions_count} mã · NAV ${fmtMillion(pf.totals.market_vnd)} · P&amp;L ${pf.totals.pnl_vnd >= 0 ? "+" : ""}${fmtMillion(pf.totals.pnl_vnd)} / ${fmtSigned(pf.totals.pnl_pct, 2, "%")})</h4>
              <ul>${(pf.items || [])
                .map(
                  (it) =>
                    `<li><strong>${esc(it.ticker)}</strong> (${it.weight_pct.toFixed(1)}%): P&amp;L <strong>${fmtSigned(it.pnl_pct, 2, "%")}</strong> · ${esc(it.wyckoff_stage_label)} · ${esc(it.guard_label)}</li>`
                )
                .join("")}</ul>
            </div>`
          : ""
      }`;
  }

  // =========================================================================
  // EVENT BINDINGS & PWA SERVICE WORKER REGISTRATION
  // =========================================================================

  function bindOnce() {
    if (pState.initialized) return;
    pState.initialized = true;

    // 1. Register PWA Service Worker
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(() => {});
    }
    window.addEventListener("beforeinstallprompt", (e) => {
      e.preventDefault();
      pState.deferredInstallPrompt = e;
      const btn = $("#btn-pwa-install");
      if (btn) btn.classList.remove("hidden");
    });
    $("#btn-pwa-install")?.addEventListener("click", async () => {
      if (!pState.deferredInstallPrompt) return;
      pState.deferredInstallPrompt.prompt();
      await pState.deferredInstallPrompt.userChoice;
      pState.deferredInstallPrompt = null;
      $("#btn-pwa-install")?.classList.add("hidden");
    });

    // 2. Tab 8 Fusion controls
    $("#fusion-scenario-group")?.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-scenario]");
      if (!btn) return;
      $$("#fusion-scenario-group button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      pState.fusionScenario = btn.dataset.scenario;
      loadFusionMatrix(false);
    });
    $("#btn-fusion-refresh")?.addEventListener("click", () => loadFusionMatrix(true));
    $("#fusion-tier-tabs")?.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-tier]");
      if (!btn) return;
      $$("#fusion-tier-tabs button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      pState.fusionTier = btn.dataset.tier;
      renderFusionMatrix(pState.fusionData);
    });

    // 3. Portfolio Tracker controls
    const addPosition = () => {
      const tk = ($("#pf-inp-ticker")?.value || "").trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
      const qty = Number($("#pf-inp-qty")?.value || 0);
      const avg = Number($("#pf-inp-avg")?.value || 0);
      const note = ($("#pf-inp-note")?.value || "").trim();
      if (!tk || tk.length < 2 || qty <= 0 || avg <= 0) {
        setStatus("pf-status-msg", "Vui lòng nhập hợp lệ: Mã CP (VD: FPT), Số lượng (>0) và Giá vốn VND (>0).", true);
        return;
      }
      const existing = pState.positions.find((p) => p.ticker === tk);
      if (existing) {
        existing.qty = qty;
        existing.avg_price = avg;
        existing.note = note;
      } else {
        pState.positions.push({ ticker: tk, qty, avg_price: avg, note });
      }
      savePositions();
      if ($("#pf-inp-ticker")) $("#pf-inp-ticker").value = "";
      if ($("#pf-inp-qty")) $("#pf-inp-qty").value = "";
      if ($("#pf-inp-avg")) $("#pf-inp-avg").value = "";
      if ($("#pf-inp-note")) $("#pf-inp-note").value = "";
      evaluatePortfolio();
    };
    $("#btn-pf-add")?.addEventListener("click", addPosition);
    $("#btn-pf-eval")?.addEventListener("click", evaluatePortfolio);
    $("#btn-pf-sample")?.addEventListener("click", () => {
      pState.positions = [
        { ticker: "FPT", qty: 1000, avg_price: 46000, note: "Nòng cốt công nghệ" },
        { ticker: "VCB", qty: 1500, avg_price: 48500, note: "Phòng thủ ngân hàng" },
        { ticker: "HPG", qty: 3000, avg_price: 19200, note: "Chu kỳ thép Dung Quất 2" },
      ];
      savePositions();
      evaluatePortfolio();
    });
    $("#table-portfolio")?.addEventListener("click", (e) => {
      const btn = e.target.closest(".btn-pf-del[data-pf-del]");
      if (!btn) return;
      const tk = btn.dataset.pfDel;
      pState.positions = pState.positions.filter((p) => p.ticker !== tk);
      savePositions();
      evaluatePortfolio();
    });
    $("#btn-pf-export")?.addEventListener("click", () => {
      const blob = new Blob([JSON.stringify(pState.positions, null, 2)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `vimo_portfolio_${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    });
    $("#btn-pf-import")?.addEventListener("click", () => $("#pf-file-input")?.click());
    $("#pf-file-input")?.addEventListener("change", (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => {
        try {
          const parsed = JSON.parse(String(reader.result || "[]"));
          if (!Array.isArray(parsed)) throw new Error("Định dạng file JSON danh mục không hợp lệ.");
          pState.positions = parsed.filter((x) => x && x.ticker && Number(x.qty) > 0 && Number(x.avg_price) > 0);
          savePositions();
          evaluatePortfolio();
        } catch (err) {
          setStatus("pf-status-msg", `Lỗi nhập file JSON: ${err.message}`, true);
        }
      };
      reader.readAsText(file);
      e.target.value = "";
    });

    // 4. BCTC Peer Comparison View Switch & Presets
    $("#bctc-view-mode-group")?.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-bctc-mode]");
      if (!btn) return;
      $$("#bctc-view-mode-group button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const mode = btn.dataset.bctcMode;
      const isPeers = mode === "peers";
      $("#bctc-peers-view")?.classList.toggle("hidden", !isPeers);
      if (isPeers) {
        $("#bctc-matrix-view")?.classList.add("hidden");
        $("#bctc-detail-view")?.classList.add("hidden");
        if (!pState.peerData) loadPeerComparison({ preset: pState.peerPreset });
        else requestAnimationFrame(resizeCharts);
      } else {
        $("#bctc-matrix-view")?.classList.remove("hidden");
        window.BctcModule?.onResize();
      }
    });
    $("#peer-preset-tabs")?.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-peer-preset]");
      if (!btn) return;
      $$("#peer-preset-tabs button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      pState.peerPreset = btn.dataset.peerPreset;
      loadPeerComparison({ preset: pState.peerPreset });
    });
    const runCustomPeers = () => {
      const raw = ($("#peer-custom-input")?.value || "").trim();
      if (!raw) return;
      $$("#peer-preset-tabs button").forEach((b) => b.classList.remove("active"));
      loadPeerComparison({ tickers: raw });
    };
    $("#btn-peer-run")?.addEventListener("click", runCustomPeers);
    $("#peer-custom-input")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); runCustomPeers(); }
    });

    // 5. Alerts & Telegram controls
    const prefs = loadAlertPrefs();
    if ($("#chk-alert-cal15")) $("#chk-alert-cal15").checked = !!prefs.cal15;
    if ($("#chk-alert-pf")) $("#chk-alert-pf").checked = !!prefs.portfolio;
    ["chk-alert-cal15", "chk-alert-pf"].forEach((id) => {
      document.getElementById(id)?.addEventListener("change", () => {
        saveJson(STORAGE_ALERTS, {
          cal15: !!$("#chk-alert-cal15")?.checked,
          portfolio: !!$("#chk-alert-pf")?.checked,
        });
      });
    });
    $("#btn-notif-enable")?.addEventListener("click", async () => {
      if (!("Notification" in window)) return;
      try {
        await Notification.requestPermission();
        updateNotifStatusUi();
        if (Notification.permission === "granted") {
          fireBrowserNotification("VĨ MÔ PULSE", "Đã kích hoạt thông báo realtime cho Lịch Mỹ ★★★ & Danh mục!", "init_test");
        }
      } catch (_) {}
    });
    updateNotifStatusUi();
    initTelegramUi();

    $("#btn-tg-save")?.addEventListener("click", () => {
      const creds = getTelegramCreds();
      saveJson(STORAGE_TG, creds);
      setStatus("tg-status-msg", "✅ Đã lưu cấu hình Telegram Bot cục bộ trên trình duyệt của bạn.");
    });
    $("#btn-tg-test")?.addEventListener("click", () => {
      const creds = getTelegramCreds();
      saveJson(STORAGE_TG, creds);
      sendTelegramText("✅ <b>VĨ MÔ PULSE</b> kết nối Telegram thành công! Bạn có thể gửi Bản tin Sáng CIO bất cứ lúc nào.");
    });
    $("#btn-tg-send-brief")?.addEventListener("click", async () => {
      const creds = getTelegramCreds();
      saveJson(STORAGE_TG, creds);
      setStatus("tg-status-msg", "Đang tạo Bản tin Sáng CIO mới nhất để gửi Telegram...");
      try {
        const d = await apiPost("/api/briefing/morning", {
          scenario: pState.fusionScenario,
          positions: pState.positions,
        });
        pState.briefData = d;
        await sendTelegramText(d.telegram_html, "tg-status-msg");
      } catch (err) {
        setStatus("tg-status-msg", `❌ ${err.message}`, true);
      }
    });

    // 6. Morning Brief Modal triggers
    $("#btn-open-brief")?.addEventListener("click", openMorningBriefModal);
    $("#btn-open-brief-card")?.addEventListener("click", openMorningBriefModal);
    $("#btn-brief-close")?.addEventListener("click", closeMorningBriefModal);
    $("#brief-modal")?.addEventListener("click", (e) => {
      if (e.target && e.target.id === "brief-modal") closeMorningBriefModal();
    });
    $("#btn-brief-print")?.addEventListener("click", () => window.print());
    $("#btn-brief-copy")?.addEventListener("click", () => {
      if (!pState.briefData?.telegram_html) return;
      const plain = pState.briefData.telegram_html.replace(/<\/?b>/g, "").replace(/<\/?i>/g, "");
      navigator.clipboard?.writeText(plain);
      const btn = $("#btn-brief-copy");
      if (btn) {
        btn.textContent = "Đã sao chép ✓";
        setTimeout(() => (btn.textContent = "Sao chép bản tin"), 1600);
      }
    });
    $("#btn-brief-tg")?.addEventListener("click", async () => {
      if (!pState.briefData?.telegram_html) return;
      const ok = await sendTelegramText(pState.briefData.telegram_html, "brief-tg-msg");
      if (ok) setTimeout(() => setStatus("brief-tg-msg", ""), 4000);
    });

    // Background 60s calendar alert watchdog when Notifications granted
    pState.watchdogTimer = setInterval(() => {
      if (document.visibilityState === "visible" && "Notification" in window && Notification.permission === "granted") {
        runCalendarAlertCheck();
      }
    }, 60000);
  }

  document.addEventListener("DOMContentLoaded", () => {
    bindOnce();
  });

  window.ProModules = {
    onShowTab(tab) {
      bindOnce();
      if (tab === "fusion" || tab === "all") {
        if (!pState.fusionData) loadFusionMatrix(false);
        else requestAnimationFrame(() => { renderFusionMatrix(pState.fusionData); resizeCharts(); });
        if (pState.positions.length && !pState.pfEval) evaluatePortfolio();
        else renderPortfolio(pState.pfEval);
      }
      if (tab === "bctc") {
        // Ensure Peer Comparison resets cleanly when switching back from external ticker jump
        if (!$("#bctc-peers-view")?.classList.contains("hidden") && pState.peerData) {
          requestAnimationFrame(resizeCharts);
        }
      }
    },
    onOpenBctcExternal() {
      // Switch BCTC tab back to normal view when user clicks a ticker's BCTC button
      $("#bctc-peers-view")?.classList.add("hidden");
      $$("#bctc-view-mode-group button").forEach((b) => b.classList.toggle("active", b.dataset.bctcMode === "matrix"));
    },
    onThemeChange() {
      if (pState.fusionData) renderFusionMatrix(pState.fusionData);
      if (pState.pfEval) renderPortfolio(pState.pfEval);
      if (pState.peerData) renderPeerComparison(pState.peerData);
    },
    onResize() {
      resizeCharts();
    },
  };
})();
