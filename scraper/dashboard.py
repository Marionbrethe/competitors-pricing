"""
Generate a self-contained static HTML dashboard from scrape results.

Usage:
    from scraper.dashboard import generate_dashboard
    path = generate_dashboard(records, scrape_logs, city="London", output_dir="output")

scrape_logs: list of dicts with keys:
    company       str   — "Bounce" | "Stasher" | "Radical Storage"
    status        str   — "ok" | "no_results" | "error"
    records_count int
    duration_sec  float
    error_message str | None
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from scraper.models import PriceRecord

COMPANY_COLORS = {
    "Bounce": "#2563eb",        # blue
    "Stasher": "#16a34a",       # green
    "Radical Storage": "#ea580c",  # coral/orange
}

COMPANIES = ["Bounce", "Stasher", "Radical Storage"]


def _price_color_class(price: float, currency: str) -> str:
    """Cheap = green, mid = blue, expensive = amber."""
    # Rough thresholds normalised to EUR/GBP (similar scale)
    if price <= 3.50:
        return "price-cheap"
    if price <= 4.75:
        return "price-mid"
    return "price-high"


def generate_dashboard(
    records: list[PriceRecord],
    scrape_logs: list[dict],
    city: str,
    output_dir: str = "output",
) -> str:
    """
    Write a self-contained HTML file to *output_dir*.
    Returns the path to the written file.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    label = city.lower().replace(" ", "_") if city else "all_cities"
    filename = f"{label}_{timestamp}_dashboard.html"
    path = os.path.join(output_dir, filename)

    # Build JSON data blob for the page
    records_data = [
        {
            "company": r.company,
            "city": r.city,
            "location_name": r.location_name,
            "address": r.address,
            "size": r.size,
            "price": r.price,
            "currency": r.currency,
            "price_unit": r.price_unit,
        }
        for r in records
    ]

    all_prices = [r.price for r in records if r.price > 0]
    cheapest = min(all_prices) if all_prices else None
    average = round(sum(all_prices) / len(all_prices), 2) if all_prices else None
    most_expensive = max(all_prices) if all_prices else None

    cheapest_company = ""
    most_expensive_company = ""
    if cheapest is not None:
        cheapest_company = next((r.company for r in records if r.price == cheapest), "")
    if most_expensive is not None:
        most_expensive_company = next((r.company for r in records if r.price == most_expensive), "")

    dashboard_data = {
        "city": city,
        "generated_at": generated_at,
        "scrape_logs": scrape_logs,
        "records": records_data,
        "summary": {
            "cheapest": cheapest,
            "cheapest_company": cheapest_company,
            "average": average,
            "most_expensive": most_expensive,
            "most_expensive_company": most_expensive_company,
            "total_records": len(records),
        },
    }

    html = _render_html(dashboard_data, city, generated_at)

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Dashboard saved → {path}")
    return path


def _render_html(data: dict, city: str, generated_at: str) -> str:
    data_json = json.dumps(data, ensure_ascii=False, indent=2)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Pricing Dashboard — {city}</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #f8fafc; color: #1e293b; font-size: 14px; }}
    .container {{ max-width: 1200px; margin: 0 auto; padding: 24px 16px; }}

    /* Header */
    .header {{ display: flex; justify-content: space-between; align-items: flex-end;
               margin-bottom: 28px; border-bottom: 2px solid #e2e8f0; padding-bottom: 16px; }}
    .header h1 {{ font-size: 22px; font-weight: 700; color: #0f172a; }}
    .header .meta {{ font-size: 12px; color: #64748b; text-align: right; }}

    /* Section titles */
    h2 {{ font-size: 15px; font-weight: 600; color: #374151; margin-bottom: 12px;
          text-transform: uppercase; letter-spacing: 0.05em; }}

    /* Status cards */
    .status-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
                    gap: 14px; margin-bottom: 28px; }}
    .status-card {{ background: #fff; border-radius: 10px; padding: 16px 20px;
                    border-left: 5px solid #94a3b8; box-shadow: 0 1px 4px rgba(0,0,0,.07); }}
    .status-card.ok    {{ border-left-color: #16a34a; }}
    .status-card.no_results {{ border-left-color: #f59e0b; }}
    .status-card.error {{ border-left-color: #dc2626; }}
    .status-card .company-name {{ font-weight: 700; font-size: 15px; margin-bottom: 6px; }}
    .status-card .status-badge {{ display: inline-block; padding: 2px 8px; border-radius: 20px;
                                   font-size: 11px; font-weight: 600; margin-bottom: 8px; }}
    .badge-ok         {{ background: #dcfce7; color: #15803d; }}
    .badge-no_results {{ background: #fef9c3; color: #a16207; }}
    .badge-error      {{ background: #fee2e2; color: #991b1b; }}
    .status-card .detail {{ font-size: 12px; color: #64748b; line-height: 1.6; }}
    .status-card .error-msg {{ font-size: 12px; color: #dc2626; margin-top: 4px;
                                font-family: monospace; word-break: break-word; }}

    /* Metrics */
    .metrics-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
                     gap: 14px; margin-bottom: 28px; }}
    .metric-card {{ background: #fff; border-radius: 10px; padding: 16px 20px;
                    box-shadow: 0 1px 4px rgba(0,0,0,.07); text-align: center; }}
    .metric-card .label {{ font-size: 11px; text-transform: uppercase; letter-spacing: .05em;
                           color: #94a3b8; margin-bottom: 6px; }}
    .metric-card .value {{ font-size: 26px; font-weight: 700; color: #0f172a; }}
    .metric-card .sub   {{ font-size: 12px; color: #64748b; margin-top: 4px; }}

    /* Filters */
    .filter-row {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }}
    .filter-btn {{ padding: 6px 14px; border: 1px solid #d1d5db; border-radius: 20px;
                   background: #fff; cursor: pointer; font-size: 13px; transition: all .15s; }}
    .filter-btn:hover {{ border-color: #6b7280; }}
    .filter-btn.active {{ background: #1e293b; color: #fff; border-color: #1e293b; }}

    /* Table */
    .table-wrap {{ background: #fff; border-radius: 10px; overflow: auto;
                   box-shadow: 0 1px 4px rgba(0,0,0,.07); margin-bottom: 28px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    thead th {{ background: #f1f5f9; text-align: left; padding: 10px 14px; font-size: 12px;
                text-transform: uppercase; letter-spacing: .05em; color: #64748b;
                cursor: pointer; user-select: none; white-space: nowrap; }}
    thead th:hover {{ background: #e2e8f0; }}
    thead th .sort-arrow {{ margin-left: 4px; opacity: .4; }}
    thead th.sorted .sort-arrow {{ opacity: 1; }}
    tbody tr {{ border-top: 1px solid #f1f5f9; }}
    tbody tr:hover {{ background: #f8fafc; }}
    tbody td {{ padding: 9px 14px; vertical-align: middle; }}
    .company-dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%;
                    margin-right: 6px; vertical-align: middle; }}
    .price-cheap {{ color: #15803d; font-weight: 600; }}
    .price-mid   {{ color: #1d4ed8; font-weight: 600; }}
    .price-high  {{ color: #b45309; font-weight: 600; }}
    .no-data {{ text-align: center; padding: 40px; color: #94a3b8; font-style: italic; }}

    /* Error log */
    .log-section {{ background: #fff; border-radius: 10px; padding: 20px;
                    box-shadow: 0 1px 4px rgba(0,0,0,.07); margin-bottom: 28px; }}
    .log-entry {{ border-left: 3px solid #e2e8f0; padding: 8px 12px; margin-bottom: 10px;
                  font-size: 12px; font-family: monospace; }}
    .log-entry.ok {{ border-left-color: #16a34a; }}
    .log-entry.no_results {{ border-left-color: #f59e0b; }}
    .log-entry.error {{ border-left-color: #dc2626; background: #fff5f5; }}
    .log-company {{ font-weight: 700; font-size: 13px; font-family: sans-serif; }}
    .log-time {{ color: #64748b; }}

    /* Flat-rate note */
    .info-banner {{ background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 8px;
                    padding: 10px 16px; font-size: 12px; color: #1d4ed8; margin-bottom: 28px; }}

    @media (max-width: 640px) {{
      .header {{ flex-direction: column; gap: 8px; }}
    }}
  </style>
</head>
<body>
<div class="container">

  <div class="header">
    <div>
      <h1>Competitor Pricing Dashboard</h1>
      <div style="font-size:13px;color:#64748b;margin-top:4px;">City: <strong>{city}</strong></div>
    </div>
    <div class="meta">Generated {generated_at}<br>Bounce · Stasher · Radical Storage</div>
  </div>

  <!-- Status cards -->
  <h2>Scrape Status</h2>
  <div class="status-grid" id="statusGrid"></div>

  <!-- Metrics -->
  <h2>Summary</h2>
  <div class="metrics-grid" id="metricsGrid"></div>

  <!-- Flat-rate note -->
  <div class="info-banner">
    ℹ All three companies use <strong>flat-rate pricing</strong> — small, medium and large bags are charged the same daily rate.
  </div>

  <!-- Results table -->
  <h2>All Locations</h2>
  <div class="filter-row" id="filterRow"></div>
  <div class="table-wrap">
    <table id="resultsTable">
      <thead>
        <tr>
          <th data-col="company">Company <span class="sort-arrow">↕</span></th>
          <th data-col="location_name">Location <span class="sort-arrow">↕</span></th>
          <th data-col="address">Address <span class="sort-arrow">↕</span></th>
          <th data-col="size">Size type <span class="sort-arrow">↕</span></th>
          <th data-col="price">Price/day <span class="sort-arrow">↕</span></th>
        </tr>
      </thead>
      <tbody id="tableBody"></tbody>
    </table>
  </div>

  <!-- Error log -->
  <h2>Scrape Log</h2>
  <div class="log-section" id="logSection"></div>

</div>

<script>
const DATA = {data_json};

const COLORS = {{
  "Bounce": "#2563eb",
  "Stasher": "#16a34a",
  "Radical Storage": "#ea580c",
}};

let activeFilter = "all";
let sortCol = "price";
let sortAsc = true;

// ---------- Status cards ----------
function renderStatus() {{
  const grid = document.getElementById("statusGrid");
  grid.innerHTML = "";
  const companies = ["Bounce", "Stasher", "Radical Storage"];
  companies.forEach(co => {{
    const log = DATA.scrape_logs.find(l => l.company === co) || {{
      company: co, status: "error", records_count: 0,
      duration_sec: 0, error_message: "Scraper did not run"
    }};
    const badgeClass = `badge-${{log.status}}`;
    const cardClass  = log.status;
    const statusLabel = {{ok:"OK", no_results:"No results", error:"Error"}}[log.status] || log.status;
    const dot = `<span class="company-dot" style="background:${{COLORS[co]||'#94a3b8'}}"></span>`;
    const errorLine = log.error_message
      ? `<div class="error-msg">⚠ ${{escHtml(log.error_message)}}</div>` : "";
    grid.innerHTML += `
      <div class="status-card ${{cardClass}}">
        <div class="company-name">${{dot}}${{co}}</div>
        <span class="status-badge ${{badgeClass}}">${{statusLabel}}</span>
        <div class="detail">
          ${{log.records_count}} record(s) collected<br>
          Completed in ${{log.duration_sec.toFixed(1)}}s
        </div>
        ${{errorLine}}
      </div>`;
  }});
}}

// ---------- Metrics ----------
function renderMetrics() {{
  const s = DATA.summary;
  const grid = document.getElementById("metricsGrid");
  if (!s.total_records) {{
    grid.innerHTML = `<div class="no-data">No data collected yet.</div>`;
    return;
  }}
  const currency = DATA.records.length ? DATA.records[0].currency : "";
  grid.innerHTML = `
    <div class="metric-card">
      <div class="label">Cheapest / day</div>
      <div class="value price-cheap">${{currency}}${{s.cheapest != null ? s.cheapest.toFixed(2) : "—"}}</div>
      <div class="sub">${{s.cheapest_company}}</div>
    </div>
    <div class="metric-card">
      <div class="label">Market average</div>
      <div class="value">${{currency}}${{s.average != null ? s.average.toFixed(2) : "—"}}</div>
      <div class="sub">across ${{s.total_records}} record(s)</div>
    </div>
    <div class="metric-card">
      <div class="label">Most expensive / day</div>
      <div class="value price-high">${{currency}}${{s.most_expensive != null ? s.most_expensive.toFixed(2) : "—"}}</div>
      <div class="sub">${{s.most_expensive_company}}</div>
    </div>`;
}}

// ---------- Filter buttons ----------
function renderFilters() {{
  const companies = ["all", ...new Set(DATA.records.map(r => r.company))];
  const row = document.getElementById("filterRow");
  row.innerHTML = "";
  companies.forEach(co => {{
    const btn = document.createElement("button");
    btn.className = "filter-btn" + (co === activeFilter ? " active" : "");
    btn.textContent = co === "all" ? "All companies" : co;
    btn.addEventListener("click", () => {{
      activeFilter = co;
      renderFilters();
      renderTable();
    }});
    row.appendChild(btn);
  }});
}}

// ---------- Table ----------
function renderTable() {{
  const body = document.getElementById("tableBody");
  let rows = activeFilter === "all"
    ? [...DATA.records]
    : DATA.records.filter(r => r.company === activeFilter);

  rows.sort((a, b) => {{
    let va = a[sortCol], vb = b[sortCol];
    if (typeof va === "string") va = va.toLowerCase();
    if (typeof vb === "string") vb = vb.toLowerCase();
    if (va < vb) return sortAsc ? -1 : 1;
    if (va > vb) return sortAsc ?  1 : -1;
    return 0;
  }});

  if (!rows.length) {{
    body.innerHTML = `<tr><td colspan="5" class="no-data">No results for this filter.</td></tr>`;
    return;
  }}

  body.innerHTML = rows.map(r => {{
    const dot = `<span class="company-dot" style="background:${{COLORS[r.company]||'#94a3b8'}}"></span>`;
    const priceClass = r.price <= 3.5 ? "price-cheap" : r.price <= 4.75 ? "price-mid" : "price-high";
    return `<tr>
      <td>${{dot}}${{escHtml(r.company)}}</td>
      <td>${{escHtml(r.location_name)}}</td>
      <td style="color:#64748b">${{escHtml(r.address)}}</td>
      <td style="color:#64748b">${{escHtml(r.size)}}</td>
      <td class="${{priceClass}}">${{r.currency}}${{r.price.toFixed(2)}}/${{r.price_unit}}</td>
    </tr>`;
  }}).join("");
}}

// ---------- Sort headers ----------
document.querySelectorAll("thead th[data-col]").forEach(th => {{
  th.addEventListener("click", () => {{
    const col = th.getAttribute("data-col");
    if (sortCol === col) sortAsc = !sortAsc;
    else {{ sortCol = col; sortAsc = true; }}
    document.querySelectorAll("thead th").forEach(t => t.classList.remove("sorted"));
    th.classList.add("sorted");
    th.querySelector(".sort-arrow").textContent = sortAsc ? "↑" : "↓";
    renderTable();
  }});
}});

// ---------- Log section ----------
function renderLog() {{
  const sec = document.getElementById("logSection");
  if (!DATA.scrape_logs.length) {{
    sec.innerHTML = `<div style="color:#94a3b8;font-style:italic">No log entries.</div>`;
    return;
  }}
  sec.innerHTML = DATA.scrape_logs.map(l => {{
    const statusLabel = {{ok:"✅ OK", no_results:"⚠ No results", error:"❌ Error"}}[l.status] || l.status;
    const errLine = l.error_message ? `<br><span style="color:#dc2626">Error: ${{escHtml(l.error_message)}}</span>` : "";
    return `<div class="log-entry ${{l.status}}">
      <span class="log-company">${{l.company}}</span> — ${{statusLabel}}
      &nbsp;·&nbsp; ${{l.records_count}} record(s)
      &nbsp;·&nbsp; <span class="log-time">${{l.duration_sec.toFixed(1)}}s</span>
      ${{errLine}}
    </div>`;
  }}).join("");
}}

function escHtml(s) {{
  return String(s || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}}

// ---------- Boot ----------
renderStatus();
renderMetrics();
renderFilters();
renderTable();
renderLog();
</script>
</body>
</html>"""
