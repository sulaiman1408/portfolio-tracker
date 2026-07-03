// ── helpers ──────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

function fmtSAR(v) {
  if (v == null) return "—";
  return "SAR " + Math.round(v).toLocaleString();
}
function fmtNum(v, d = 2) {
  if (v == null) return "—";
  return (+v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
}
function fmtPct(v) {
  if (v == null) return "—";
  return (v >= 0 ? "+" : "") + (+v).toFixed(2) + "%";
}
function signClass(v) {
  if (v == null) return "";
  return v >= 0 ? "positive" : "negative";
}

function card(label, value, sub, cls) {
  return `<div class="card">
    <div class="label">${label}</div>
    <div class="value ${cls || ""}">${value}</div>
    ${sub ? `<div class="sub">${sub}</div>` : ""}
  </div>`;
}

// ── state ─────────────────────────────────────────────────────────────────────
let sessionId = null;
let pieChart = null, plChart = null, histChart = null;

// ── portfolio selector ────────────────────────────────────────────────────────
async function initPortfolioSelector() {
  const container = $("portfolio-options");
  try {
    const res  = await fetch("/api/portfolios");
    const data = await res.json();
    container.innerHTML = data.portfolios.map(name => `
      <button class="portfolio-btn" data-name="${name}">${name}</button>
    `).join("");
    container.querySelectorAll(".portfolio-btn").forEach(btn => {
      btn.addEventListener("click", () => handleLoad(btn.dataset.name));
    });
  } catch (e) {
    container.innerHTML = `<p style="color:red">Failed to load portfolios: ${e.message}</p>`;
  }
}

async function handleLoad(name) {
  const errEl  = $("upload-error");
  const loadEl = $("upload-loading");
  errEl.classList.add("hidden");
  loadEl.classList.remove("hidden");

  // Highlight selected button
  document.querySelectorAll(".portfolio-btn").forEach(b => b.classList.remove("active"));
  document.querySelector(`.portfolio-btn[data-name="${name}"]`)?.classList.add("active");

  try {
    const res  = await fetch("/api/load", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const data = await res.json();
    if (!res.ok) { showUploadError(data.error); return; }
    sessionId = data.session_id;
    showDashboard(name);
  } catch (e) {
    showUploadError("Failed to load: " + e.message);
  } finally {
    loadEl.classList.add("hidden");
  }
}

initPortfolioSelector();

function showUploadError(msg) {
  const el = $("upload-error");
  el.textContent = msg;
  el.classList.remove("hidden");
}

// ── dashboard ──────────────────────────────────────────────────────────────────
function showDashboard(name) {
  $("upload-view").classList.add("hidden");
  $("dashboard-view").classList.remove("hidden");
  if (name) $("nav-brand").textContent = "📈 " + name;
  loadSummary();
  loadHistory();
  initPerfTable();
  loadPerfTable();
}

$("new-portfolio-btn").addEventListener("click", () => {
  sessionId = null;
  [pieChart, plChart, histChart].forEach(c => c && c.destroy());
  pieChart = plChart = histChart = null;
  $("upload-view").classList.remove("hidden");
  $("dashboard-view").classList.add("hidden");
  fileInput.value = "";
});

$("refresh-btn").addEventListener("click", () => { loadSummary(); loadHistory(); });

// ── summary ───────────────────────────────────────────────────────────────────
async function loadSummary() {
  try {
    const res = await fetch(`/api/summary?session_id=${sessionId}`);
    const data = await res.json();
    if (!res.ok) { alert(data.error); return; }
    renderSummary(data);
  } catch (e) {
    alert("Failed to load summary: " + e.message);
  }
}

function renderSummary(data) {
  const { summary: s, tasi, charts, holdings, generated_at } = data;
  $("nav-timestamp").textContent = "Updated: " + generated_at;

  // Summary cards
  $("summary-section").innerHTML = [
    card("Cost Basis",   fmtSAR(s.total_cost)),
    card("Current Value", fmtSAR(s.total_value)),
    card("Unrealized P&L", fmtSAR(s.total_unrealized_pl), fmtPct(s.total_unrealized_pl_pct), signClass(s.total_unrealized_pl)),
    card("Realized P&L",  fmtSAR(s.total_realized_pl), null, signClass(s.total_realized_pl)),
    card("Total Return",  fmtPct(s.total_return_pct), "realized + unrealized / cost", signClass(s.total_return_pct)),
  ].join("");

  // TASI cards
  const vt = tasi.vs_portfolio;
  $("tasi-section").innerHTML = [
    card("TASI Index",  fmtNum(tasi.current)),
    card("TASI Since " + tasi.baseline_date, fmtPct(tasi.pct_change), null, signClass(tasi.pct_change)),
    card("Portfolio vs TASI", vt != null ? (vt >= 0 ? "+" : "") + vt.toFixed(2) + "%" : "—",
         "your return minus TASI return", signClass(vt)),
  ].join("");

  // Pie chart
  renderPie(charts.pie);

  // P&L bar chart
  renderPlBars(charts.pl_bars);

  // Holdings table
  renderTable(holdings);
}

// ── pie chart ─────────────────────────────────────────────────────────────────
function renderPie(pie) {
  const ctx = $("pie-chart").getContext("2d");
  if (pieChart) pieChart.destroy();

  const COLORS = [
    "#2563eb","#7c3aed","#059669","#d97706","#dc2626","#0891b2",
    "#be185d","#16a34a","#ea580c","#4338ca","#0284c7","#b45309",
    "#15803d","#9333ea","#e11d48","#0369a1","#ca8a04","#166534",
  ];

  pieChart = new Chart(ctx, {
    type: "doughnut",
    data: {
      labels: pie.map(p => p.label),
      datasets: [{
        data: pie.map(p => p.value),
        backgroundColor: COLORS.slice(0, pie.length),
        borderWidth: 2,
        borderColor: "#fff",
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "60%",
      plugins: {
        legend: { position: "right", labels: { font: { size: 11 }, boxWidth: 12, padding: 10 } },
        tooltip: {
          callbacks: {
            label: ctx => {
              const total = ctx.dataset.data.reduce((a, b) => a + b, 0);
              const pct = (ctx.raw / total * 100).toFixed(1);
              return ` ${fmtSAR(ctx.raw)}  (${pct}%)`;
            },
          },
        },
      },
    },
  });
}

// ── P&L bar chart ─────────────────────────────────────────────────────────────
function renderPlBars(bars) {
  const ctx = $("pl-chart").getContext("2d");
  if (plChart) plChart.destroy();

  // Dynamic height based on number of bars
  const h = Math.max(280, bars.length * 30);
  $("pl-chart").style.height = h + "px";
  $("pl-chart").parentElement.style.height = h + "px";

  plChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels: bars.map(b => b.ticker),
      datasets: [{
        label: "Unrealized P&L (SAR)",
        data: bars.map(b => b.unrealized_pl),
        backgroundColor: bars.map(b => b.unrealized_pl >= 0 ? "rgba(5,150,105,.8)" : "rgba(220,38,38,.8)"),
        borderRadius: 4,
      }],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => ` ${fmtSAR(ctx.raw)}  (${bars[ctx.dataIndex].pct >= 0 ? "+" : ""}${bars[ctx.dataIndex].pct}%)`,
          },
        },
      },
      scales: {
        x: {
          grid: { color: "#f1f5f9" },
          ticks: { callback: v => "SAR " + Math.round(v / 1000) + "k", font: { size: 11 } },
        },
        y: { ticks: { font: { size: 11 } }, grid: { display: false } },
      },
    },
  });
}

// ── history chart ──────────────────────────────────────────────────────────────
async function loadHistory() {
  $("history-status").textContent = "Loading…";
  try {
    const res = await fetch(`/api/history?session_id=${sessionId}`);
    const data = await res.json();
    if (!res.ok) { $("history-status").textContent = "Failed: " + data.error; return; }
    renderHistory(data);
    $("history-status").textContent = "";
  } catch (e) {
    $("history-status").textContent = "Failed: " + e.message;
  }
}

function renderHistory(data) {
  const ctx = $("history-chart").getContext("2d");
  if (histChart) histChart.destroy();

  const datasets = [
    {
      label: "My Portfolio",
      data: data.portfolio_return,
      borderColor: "#2563eb",
      backgroundColor: "rgba(37,99,235,.08)",
      fill: true,
      tension: 0.3,
      pointRadius: 0,
      borderWidth: 2.5,
    },
  ];

  if (data.tasi_return) {
    datasets.push({
      label: "TASI Index",
      data: data.tasi_return,
      borderColor: "#f59e0b",
      backgroundColor: "transparent",
      fill: false,
      tension: 0.3,
      pointRadius: 0,
      borderWidth: 2,
      borderDash: [5, 4],
    });
  }

  histChart = new Chart(ctx, {
    type: "line",
    data: { labels: data.dates, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { labels: { font: { size: 12 }, boxWidth: 20 } },
        tooltip: {
          callbacks: {
            label: ctx => ` ${ctx.dataset.label}: ${ctx.raw >= 0 ? "+" : ""}${(+ctx.raw).toFixed(2)}%`,
          },
        },
      },
      scales: {
        x: {
          ticks: {
            maxTicksLimit: 12,
            font: { size: 11 },
            callback: (_, i) => data.dates[i]?.slice(0, 7) ?? "",
          },
          grid: { color: "#f1f5f9" },
        },
        y: {
          ticks: { callback: v => v + "%", font: { size: 11 } },
          grid: { color: "#f1f5f9" },
        },
      },
    },
  });
}

// ── periodic performance table ────────────────────────────────────────────────
function initPerfTable() {
  // Set default date range: 1 year ago → today
  const today = new Date();
  const oneYearAgo = new Date(today);
  oneYearAgo.setFullYear(today.getFullYear() - 1);
  $("perf-to").value   = today.toISOString().slice(0, 10);
  $("perf-from").value = oneYearAgo.toISOString().slice(0, 10);

  $("perf-apply").addEventListener("click", loadPerfTable);
  $("perf-freq").addEventListener("change", loadPerfTable);
}

async function loadPerfTable() {
  const freq = $("perf-freq").value;
  const from = $("perf-from").value;
  const to   = $("perf-to").value;
  const status = $("perf-status");
  status.textContent = "Loading…";

  try {
    const res  = await fetch(`/api/performance?session_id=${sessionId}&freq=${freq}&from=${from}&to=${to}`);
    const data = await res.json();
    if (!res.ok) { status.textContent = "Error: " + data.error; return; }
    renderPerfTable(data.rows);
    status.textContent = "";
  } catch (e) {
    status.textContent = "Failed: " + e.message;
  }
}

function renderPerfTable(rows) {
  // Show newest first
  const reversed = [...rows].reverse();
  document.querySelector("#perf-table tbody").innerHTML = reversed.map(r => `
    <tr>
      <td style="text-align:left">${r.period}</td>
      <td>${fmtSAR(r.start_value)}</td>
      <td>${fmtSAR(r.end_value)}</td>
      <td class="${signClass(r.pl)}">${fmtSAR(r.pl)}</td>
      <td class="${signClass(r.pl_pct)}">${fmtPct(r.pl_pct)}</td>
    </tr>`).join("");
}

// ── holdings table ────────────────────────────────────────────────────────────
let _allHoldings = [];

function renderTable(holdings) {
  _allHoldings = holdings;
  applyTableFilters();

  // Wire up controls once
  $("sort-select").onchange = applyTableFilters;
  $("search-box").oninput   = applyTableFilters;
}

function applyTableFilters() {
  const query  = ($("search-box").value || "").trim().toLowerCase();
  const [field, dir] = ($("sort-select").value || "unrealized_pl:desc").split(":");

  let rows = _allHoldings.filter(h =>
    !query ||
    h.ticker.toLowerCase().includes(query) ||
    h.company.toLowerCase().includes(query)
  );

  rows.sort((a, b) => {
    const av = a[field] ?? -Infinity;
    const bv = b[field] ?? -Infinity;
    return dir === "asc" ? av - bv : bv - av;
  });

  const noResults = $("no-results");
  if (rows.length === 0) {
    noResults.classList.remove("hidden");
    document.querySelector("#holdings-table tbody").innerHTML = "";
    return;
  }
  noResults.classList.add("hidden");

  document.querySelector("#holdings-table tbody").innerHTML = rows.map(h => `
    <tr>
      <td><strong>${h.ticker}</strong></td>
      <td>${h.company}</td>
      <td>${fmtNum(h.quantity, 0)}</td>
      <td>${fmtNum(h.average_cost)}</td>
      <td>${fmtNum(h.current_price)}</td>
      <td>${fmtSAR(h.cost_basis)}</td>
      <td>${fmtSAR(h.current_value)}</td>
      <td class="${signClass(h.unrealized_pl)}">${fmtSAR(h.unrealized_pl)}</td>
      <td class="${signClass(h.unrealized_pl_pct)}">${fmtPct(h.unrealized_pl_pct)}</td>
      <td class="${signClass(h.realized_pl)}">${fmtSAR(h.realized_pl)}</td>
    </tr>`).join("");
}
