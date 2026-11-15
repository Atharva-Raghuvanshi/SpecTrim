/* Query Optimizer UI (WP9). Vanilla JS, no build step, hand-written SVG charts.
   All rendered text is escaped — specs are untrusted LLM output. */
"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  range: "24h", auto: false, mode: "balanced",
  sortKey: "time", sortDir: -1, page: 0, specCache: {},
};

/* ---------- utilities ---------- */
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
function num(n) { return Number(n || 0).toLocaleString(); }
function headers() {
  const t = sessionStorage.getItem("qo_token");
  return t ? { Authorization: "Bearer " + t } : {};
}
async function api(path) {
  const r = await fetch(path, { headers: headers() });
  if (!r.ok) throw new Error(path + " -> " + r.status);
  return r.json();
}
function copyText(id) {
  navigator.clipboard.writeText($(id).textContent.trim()).then(() => {
    const b = document.querySelector('[data-copy="' + id + '"]');
    if (b) { b.textContent = "Copied"; setTimeout(() => (b.textContent = "Copy"), 900); }
  });
}
function showError(msg) {
  const el = $("error-banner");
  el.textContent = msg; el.classList.remove("hidden");
  setTimeout(() => el.classList.add("hidden"), 6000);
}
function fmtDelta(v) {
  if (v == null) return "";
  return (v >= 0 ? "+" : "") + v.toFixed(1) + "%";
}

/* ---------- SVG chart helpers (hand-written, no libraries) ---------- */
const NS = "http://www.w3.org/2000/svg";
function svgEl(tag, attrs) {
  const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  return e;
}
function svgFrame(w, h) {
  const svg = svgEl("svg", { viewBox: `0 0 ${w} ${h}`, role: "img" });
  return svg;
}
function sparkline(values, w = 160, h = 40, color = "var(--accent)") {
  const svg = svgFrame(w, h);
  const max = Math.max(...values, 1), min = Math.min(...values, 0);
  const pts = values.map((v, i) => {
    const x = (i / Math.max(values.length - 1, 1)) * (w - 4) + 2;
    const y = h - 2 - ((v - min) / (max - min || 1)) * (h - 4);
    return [x, y];
  });
  if (pts.length > 1) {
    svg.appendChild(svgEl("polyline", {
      points: pts.map((p) => p.join(",")).join(" "), fill: "none",
      stroke: color, "stroke-width": 2, "stroke-linejoin": "round",
    }));
  }
  return svg;
}
function lineChart(series, w = 560, h = 180, colors = ["var(--accent)", "var(--accent2)"]) {
  const svg = svgFrame(w, h);
  const n = Math.max(...series.map((s) => s.values.length), 1);
  const max = Math.max(...series.flatMap((s) => s.values), 1);
  series.forEach((s, si) => {
    const pts = s.values.map((v, i) => {
      const x = (i / Math.max(n - 1, 1)) * (w - 50) + 40;
      const y = h - 24 - (v / max) * (h - 40);
      return `${x},${y}`;
    });
    if (s.area) {
      const base = `${40},${h - 24}`;
      svg.appendChild(svgEl("polygon", {
        points: `${base} ${pts.join(" ")} ${40 + (w - 50)},${h - 24}`,
        fill: colors[si], opacity: 0.12, stroke: "none",
      }));
    }
    svg.appendChild(svgEl("polyline", {
      points: pts.join(" "), fill: "none", stroke: colors[si],
      "stroke-width": 2, "stroke-linejoin": "round",
    }));
  });
  svg.appendChild(svgEl("line", { x1: 40, y1: h - 24, x2: w - 10, y2: h - 24, stroke: "var(--line)" }));
  series.forEach((s, si) => {
    svg.appendChild(svgEl("text", {
      x: w - 10, y: 14 + si * 14, "text-anchor": "end", fill: colors[si], "font-size": 11,
    })).textContent = s.label;
  });
  return svg;
}
function hBars(items, w = 560, h = 180, color = "var(--accent)") {
  const svg = svgFrame(w, h);
  const max = Math.max(...items.map((i) => i.value), 1);
  const bh = Math.min(24, (h - 10) / Math.max(items.length, 1) - 6);
  items.forEach((it, i) => {
    const y = 8 + i * (bh + 6);
    svg.appendChild(svgEl("rect", { x: 130, y, width: ((w - 260) * it.value) / max, height: bh,
      fill: color, rx: 3 }));
    const lbl = svgEl("text", { x: 124, y: y + bh / 2 + 4, "text-anchor": "end", fill: "var(--text)", "font-size": 11 });
    lbl.textContent = it.label; svg.appendChild(lbl);
    const val = svgEl("text", { x: 136 + ((w - 260) * it.value) / max, y: y + bh / 2 + 4, fill: "var(--muted)", "font-size": 11 });
    val.textContent = num(it.value); svg.appendChild(val);
  });
  return svg;
}
function donut(parts, w = 560, h = 180) {
  const svg = svgFrame(w, h);
  const total = parts.reduce((a, p) => a + p.value, 0) || 1;
  const cx = 90, cy = h / 2, r = 60;
  let angle = -Math.PI / 2;
  const colors = ["var(--accent)", "var(--accent2)", "var(--warn)", "var(--danger)"];
  parts.forEach((p, i) => {
    const a2 = angle + (p.value / total) * Math.PI * 2;
    const large = a2 - angle > Math.PI ? 1 : 0;
    const x1 = cx + r * Math.cos(angle), y1 = cy + r * Math.sin(angle);
    const x2 = cx + r * Math.cos(a2), y2 = cy + r * Math.sin(a2);
    if (a2 - angle > 0.001) {
      svg.appendChild(svgEl("path", {
        d: `M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${large} 1 ${x2} ${y2} Z`,
        fill: colors[i % colors.length], opacity: 0.85,
      }));
    }
    const lx = 190, ly = 20 + i * 20;
    svg.appendChild(svgEl("rect", { x: lx, y: ly - 10, width: 10, height: 10, fill: colors[i % colors.length], rx: 2 }));
    const t = svgEl("text", { x: lx + 16, y: ly, fill: "var(--text)", "font-size": 12 });
    t.textContent = `${p.label}: ${num(p.value)}`; svg.appendChild(t);
    angle = a2;
  });
  const tTot = svgEl("text", { x: 190, y: 30 + parts.length * 20, fill: "var(--muted)", "font-size": 12, "font-weight": "600" });
  tTot.textContent = `Total: ${num(total)}`; svg.appendChild(tTot);
  return svg;
}

/* ---------- pages ---------- */
function showPage(name) {
  document.querySelectorAll(".page").forEach((p) => p.classList.add("hidden"));
  $("page-" + name).classList.remove("hidden");
  document.querySelectorAll(".nav-btn").forEach((b) =>
    b.classList.toggle("active", b.dataset.page === name));
  if (name === "overview") refreshOverview();
  if (name === "specs") refreshSpecs();
  if (name === "connect") renderConnect();
}

async function refreshOverview() {
  const rng = state.range;
  try {
    const [summary, series, passes, reqs, bench] = await Promise.all([
      api(`/ui/api/summary?range=${rng}`),
      api(`/ui/api/series?range=${rng}`),
      api(`/ui/api/passes?range=${rng}`),
      api(`/ui/api/requests?range=${rng}&limit=50&offset=${state.page * 50}`),
      api("/ui/api/benchmarks"),
    ]);
    $("demo-banner").classList.toggle("hidden", !summary.demo);
    window.__ledgerSavedEst = summary.tokens_saved_est;
    renderKpis(summary);
    renderCharts(series, passes, reqs, summary);
    renderRequests(reqs);
    renderBench(bench);
    const empty = summary.requests === 0;
    $("empty-state").classList.toggle("hidden", !empty);
    if (empty) renderConnect();
  } catch (e) { showError("Overview load failed: " + e.message); }
}

function renderKpis(s) {
  const kpis = [
    (() => {
      const sv = s.tokens_saved_est;
      const neg = sv < 0;
      const label = (neg ? "Net increase (estimated)" : "Prompt tokens saved (estimated)") + " (Ledger, " + s.range + ")";
      const pctStr = Math.abs(s.tokens_saved_pct_est || 0) + "%";
      const val = neg ? num(-sv) + ` (+${pctStr} increase)` : num(sv) + ` (${pctStr})`;
      const tip = "baseline is an estimate from chars/4 of the original prompt";
      let d = `${num(s.completion_tokens || 0)} completion · ${num(s.reasoning_tokens || 0)} reasoning`;
      if (s.excluded_count) {
        d += ` (${num(s.excluded_count)} unmeasured older rows excluded)`;
      }
      return { l: label, v: val, d, tip };
    })(),
    { l: "Cost saved (Ledger, " + s.range + ")", v: s.cost_saved_usd == null ? "unpriced" : "$" + s.cost_saved_usd.toFixed(4),
      d: "provider did not report a price" },
    { l: "Cache hit rate (Ledger, " + s.range + ")", v: s.cache_hit_rate + "%" },
    { l: "Requests proxied (Ledger, " + s.range + ")", v: num(s.requests) },
    { l: "Passthrough (Ledger, " + s.range + ")", v: num(s.passthroughs) },
    { l: "Validity-guard (Ledger, " + s.range + ")", v: num(s.invalid_after_pass_events) + " / " + num(s.invalid_original_events),
      d: "reverted passes / invalid originals" },
    { l: "Added latency p95 (Ledger, " + s.range + ")", v: s.latency_p95_ms + " ms" },
  ];
  $("kpis").innerHTML = kpis.map((k) =>
    `<div class="kpi" title="${escapeHtml(k.tip || "")}"><div class="l">${escapeHtml(k.l)}</div>` +
    `<div class="v">${escapeHtml(String(k.v))}</div><div class="d">${escapeHtml(String(k.d || ""))}</div></div>`
  ).join("");
}

function renderCharts(series, passes, reqs, summary) {
  const pts = series.points || [];
  const base = pts.map((p) => p.prompt_tokens_baseline_est ?? p.tokens_baseline_est);
  const act = pts.map((p) => p.prompt_tokens_actual ?? p.tokens_actual);
  $("chart-tokens").replaceChildren(
    lineChart([
      { label: "baseline-est (prompt)", values: base, area: true },
      { label: "actual (prompt)", values: act },
    ], 560, 180));
  $("fb-tokens").textContent = pts.length
    ? "Fallback table: " + pts.map((p) => `${p.bucket.slice(11, 16)} b=${p.prompt_tokens_baseline_est ?? p.tokens_baseline_est} a=${p.prompt_tokens_actual ?? p.tokens_actual}`).join("; ")
    : "No data in range.";
  $("chart-passes").replaceChildren(
    hBars((passes.passes || []).map((p) => ({ label: p.pass, value: p.tokens_saved_est }))));
  $("fb-passes").textContent = (passes.passes || []).map((p) => `${p.pass}: ${p.tokens_saved_est}`).join("; ") || "No data.";
  const cachePts = pts.map((p) => (p.requests ? (100 * p.cache_hits) / p.requests : 0));
  $("chart-cache").replaceChildren(lineChart([{ label: "cache hit %", values: cachePts }], 560, 180, ["var(--accent2)"]));
  $("fb-cache").textContent = pts.length
    ? "avg " + (cachePts.reduce((a, b) => a + b, 0) / cachePts.length).toFixed(1) + "%"
    : "No data.";
  const byMode = (summary && summary.by_mode && Object.keys(summary.by_mode).length) ? summary.by_mode : {};
  if (Object.keys(byMode).length === 0) {
    (reqs.items || []).forEach((r) => { byMode[r.mode] = (byMode[r.mode] || 0) + 1; });
  }
  const totalModeReqs = (summary && summary.requests != null) ? summary.requests : Object.values(byMode).reduce((a, b) => a + b, 0);
  $("chart-modes").replaceChildren(donut(Object.entries(byMode).map(([label, value]) => ({ label, value }))));
  $("fb-modes").textContent = (Object.entries(byMode).map(([m, c]) => `${m}: ${c}`).join("; ") || "No data.") + ` (Total: ${num(totalModeReqs)})`;
  $("chart-latency").replaceChildren(lineChart([
    { label: "p50", values: pts.map((p) => p.latency_p50_ms) },
    { label: "p95", values: pts.map((p) => p.latency_p95_ms) },
  ], 560, 180, ["var(--accent)", "var(--warn)"]));
  $("fb-latency").textContent = pts.length
    ? "p95 range " + Math.max(...pts.map((p) => p.latency_p95_ms)) + " ms max"
    : "No data.";
}

function renderRequests(reqs) {
  const cols = ["time", "model", "mode", "tokens_baseline_est", "tokens_actual", "saved_pct", "passes_applied", "cache_hit", "passthrough"];
  const items = [...(reqs.items || [])].sort((a, b) => {
    const va = a[state.sortKey], vb = b[state.sortKey];
    return (va > vb ? 1 : va < vb ? -1 : 0) * state.sortDir;
  });
  const page = items.slice(state.page * 50, state.page * 50 + 50);
  const head = `<tr>${cols.map((c) => `<th data-sort="${c}">${c}</th>`).join("")}</tr>`;
  const body = page.map((r) =>
    `<tr>${cols.map((c) => `<td>${escapeHtml(String(r[c]))}</td>`).join("")}</tr>`).join("");
  $("requests-table").innerHTML =
    `<table><thead>${head}</thead><tbody>${body || '<tr><td colspan="9">No requests in range.</td></tr>'}</tbody></table>` +
    `<div class="muted">total ${reqs.total} · page ${state.page + 1}` +
    ` <button class="ghost" id="prev">‹</button><button class="ghost" id="next">›</button></div>`;
  document.querySelectorAll("#requests-table th").forEach((th) =>
    th.addEventListener("click", () => {
      if (state.sortKey === th.dataset.sort) state.sortDir *= -1;
      else { state.sortKey = th.dataset.sort; state.sortDir = -1; }
      refreshOverview();
    }));
  const prev = $("prev"), next = $("next");
  if (prev) prev.onclick = () => { if (state.page > 0) { state.page--; refreshOverview(); } };
  if (next) next.onclick = () => { state.page++; refreshOverview(); };
}

function renderBench(b) {
  const el = $("bench-body");
  if (!b.measured) {
    el.innerHTML = `<span class="muted">NOT MEASURED — no benchmark result files found yet.</span>`;
    return;
  }
  const one = (exp) => {
    const d = b[exp];
    if (!d || !d.summary) return `<p class="muted">${exp.toUpperCase()}: NOT MEASURED</p>`;
    const p = d.paired;
    const pairedHtml = p && p.paired_n
      ? `<p class="muted">Paired (all modes completed): n=${p.paired_n}, ` +
        Object.entries(p.per_config).map(([cfg, s]) =>
          `${escapeHtml(cfg)}: ${s.tokens_in_avg} tok-in` +
          (s.tokens_in_delta_pct != null ? ` (${s.tokens_in_delta_pct > 0 ? "+" : ""}${s.tokens_in_delta_pct}%)` : "")
        ).join(", ") +
        `, tasks with differing outcomes: ${p.tasks_with_different_outcomes}</p>`
      : `<p class="muted">${exp.toUpperCase()} paired: NOT MEASURED</p>`;
    const rows = Object.entries(d.summary).map(([cfg, s]) =>
      `<tr><td>${escapeHtml(cfg)}</td><td>${s.n}</td><td>${(100 * s.pass_rate).toFixed(1)}%</td>` +
      `<td>${num(s.tokens_in_avg)}</td><td>${num(s.tokens_out_avg)}</td><td>${s.iterations_avg}</td></tr>`);
    return `<p>${exp.toUpperCase()} — model: <code>${escapeHtml(d.builder_model || "")}</code>, cells: ${d.n_cells}</p>
      <table><thead><tr><th>config</th><th>n</th><th>pass rate</th><th>tokens in avg</th><th>tokens out avg</th><th>iterations</th></tr></thead>
      <tbody>${rows.join("")}</tbody></table>${pairedHtml}`;
  };
  let warning = "";
  const e4 = b.e4, ledgerSaved = (window.__ledgerSavedEst || 0) > 0;
  const balDelta = e4 && e4.paired && e4.paired.per_config &&
    e4.paired.per_config.balanced && e4.paired.per_config.balanced.tokens_in_delta_pct;
  if (ledgerSaved && balDelta != null && balDelta > 0) {
    warning = `<div class="banner">⚠ Ledger estimates savings while the paired E4 measurement shows balanced input tokens INCREASED ` +
      `(+${balDelta}% vs off) — the ledger baseline is an estimate; trust the paired measurement.</div>`;
  }
  el.innerHTML = warning + one("e4") + one("e5");
}

/* ---------- specs ---------- */
async function refreshSpecs() {
  try {
    const d = await api("/ui/api/sessions");
    const list = $("specs-list");
    list.innerHTML = `<table><thead><tr><th>session</th><th>status</th><th>v</th><th>idea</th><th></th></tr></thead><tbody>` +
      (d.sessions || []).map((s) =>
        `<tr><td><code>${escapeHtml(s.id)}</code></td><td>${escapeHtml(s.status)}</td>` +
        `<td>${s.current_version ?? "-"}</td><td>${escapeHtml(s.idea)}</td>` +
        `<td><button class="ghost" data-view="${escapeHtml(s.id)}">View</button></td></tr>`).join("") +
      `</tbody></table>`;
    list.querySelectorAll("[data-view]").forEach((b) =>
      b.addEventListener("click", () => viewSpec(b.dataset.view)));
    if (!(d.sessions || []).length) {
      list.innerHTML = `<div class="muted">No sessions yet — create one under “New spec”.</div>`;
    }
  } catch (e) { showError("Specs load failed: " + e.message); }
}

async function viewSpec(sid) {
  try {
    const d = await api("/ui/api/sessions/" + encodeURIComponent(sid));
    state.specCache[sid] = d;
    const v = $("spec-viewer");
    v.classList.remove("hidden");
    const tabs = ["master_spec", "assumptions", "sub_specs", "contract"];
    v.innerHTML =
      `<div class="card"><h2>Session ${escapeHtml(sid)} — v${d.version} (${escapeHtml(d.status)})</h2>
       <div class="row">${tabs.map((t) => `<button class="ghost" data-tab="${t}">${t}</button>`).join("")}
       <a class="ghost" href="/sessions/${encodeURIComponent(sid)}/export?format=markdown" target="_blank">Markdown</a>
       <a class="ghost" href="/sessions/${encodeURIComponent(sid)}/export?format=openapi" target="_blank">openapi.yaml</a></div>
       <div id="tab-body"></div>
       <div class="muted" id="diff-line"></div></div>`;
    v.querySelectorAll("[data-tab]").forEach((b) =>
      b.addEventListener("click", () => renderSpecTab(sid, b.dataset.tab)));
    if (d.diff_from_previous) {
      $("diff-line").textContent =
        `diff v${d.diff_from_previous.from}→v${d.diff_from_previous.to}: ` +
        (d.diff_from_previous.breaking ? "BREAKING CHANGES: " : "") +
        d.diff_from_previous.changes.map((c) => c.kind).join(", ");
    }
    renderSpecTab(sid, "master_spec");
  } catch (e) { showError("Spec view failed: " + e.message); }
}

function renderSpecTab(sid, tab) {
  const d = state.specCache[sid];
  const el = $("tab-body");
  if (tab === "master_spec") {
    const ms = d.master_spec || {};
    el.innerHTML = `<p>${escapeHtml(ms.summary || "")}</p>` +
      (ms.features || []).map((f) =>
        `<div class="card"><b>${escapeHtml(f.id)}: ${escapeHtml(f.title)}</b>` +
        `<ul>${(f.acceptance || []).map((a) => `<li>${escapeHtml(a)}</li>`).join("")}</ul></div>`).join("");
  } else if (tab === "assumptions") {
    el.innerHTML = `<table><tbody>${(d.assumptions || []).map((a) =>
      `<tr><td><code>${escapeHtml(a.id)}</code></td><td>${escapeHtml(a.text)}</td>` +
      `<td>${escapeHtml(a.category)}</td><td>${a.overridable ? "overridable" : "flagged"}</td></tr>`).join("")}</tbody></table>`;
  } else if (tab === "sub_specs") {
    el.innerHTML = Object.entries(d.sub_specs || {}).map(([k, s]) =>
      `<div class="card"><b>${escapeHtml(k)}</b> <span class="muted">contract v${escapeHtml(String(s.contract_version))}</span>` +
      `<div class="muted">endpoints: ${(s.endpoints || []).map((e) => escapeHtml(e.op_id)).join(", ") || "none"}</div>` +
      `<div class="muted">pages: ${(s.pages || []).map((p) => escapeHtml(p.name)).join(", ") || "none"}</div></div>`).join("");
  } else if (tab === "contract") {
    const ops = [];
    const paths = (d.contract?.openapi?.paths) || {};
    for (const [path, item] of Object.entries(paths))
      for (const m of ["get", "post", "put", "patch", "delete"])
        if (item[m]) ops.push({ method: m.toUpperCase(), path, op: item[m].operationId });
    el.innerHTML = `<table><thead><tr><th>method</th><th>path</th><th>operationId</th></tr></thead><tbody>` +
      ops.map((o) => `<tr><td>${escapeHtml(o.method)}</td><td>${escapeHtml(o.path)}</td><td>${escapeHtml(o.op)}</td></tr>`).join("") +
      `</tbody></table>
      <div class="row"><a class="ghost" href="/sessions/${encodeURIComponent(sid)}/sessions/${encodeURIComponent(sid)}/openapi.yaml?format=openapi" hidden></a>
      <a class="ghost" href="/ui/api/sessions/${encodeURIComponent(sid)}/openapi.yaml" target="_blank">openapi.yaml</a></div>`;
  }
}

/* ---------- new spec ---------- */
let sse = null;
function newSpec() {
  const idea = $("idea-input").value.trim();
  if (!idea) { showError("Enter an idea first."); return; }
  const idem = $("idem-input").value.trim() || "ui-" + Date.now();
  fetch("/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": idem, ...headers() },
    body: JSON.stringify({ idea }),
  }).then((r) => r.json()).then((s) => {
    $("spec-progress").textContent = "session " + s.id + " (" + s.status + ")";
    listenEvents(s.id);
  }).catch((e) => showError("Create failed: " + e.message));
}

function listenEvents(sid) {
  if (sse) sse.close();
  let lastId = sessionStorage.getItem("qo_last_seq_" + sid) || 0;
  sse = new EventSource("/sessions/" + encodeURIComponent(sid) + "/events", {
    withCredentials: false,
  });
  sse.onopen = () => { $("spec-progress").textContent = "streaming events…"; };
  sse.onmessage = (ev) => {
    if (ev.lastEventId) sessionStorage.setItem("qo_last_seq_" + sid, ev.lastEventId);
    const data = JSON.parse(ev.data);
    $("spec-progress").textContent =
      `last event: ${data.type} (seq ${ev.lastEventId})`;
    if (data.type === "version_created") {
      $("spec-progress").textContent = "ready: version " + data.payload.version + " created";
      sse.close();
    }
  };
  sse.onerror = () => {
    // Last-Event-ID reconnect: EventSource replays from last id automatically
    $("spec-progress").textContent = "reconnecting…";
  };
  fetch(`/sessions/${encodeURIComponent(sid)}/questions`, { headers: headers() })
    .then((r) => r.json()).then((q) => {
      if (!(q.questions || []).length) return;
      const box = $("questions-box");
      box.classList.remove("hidden");
      box.innerHTML = `<h3>Blocking questions</h3>` + q.questions.map((qq) =>
        `<div class="row"><label>${escapeHtml(qq.id)}: ${escapeHtml(qq.text)}</label>` +
        `<input data-q="${escapeHtml(qq.id)}" placeholder="your answer"></div>`).join("") +
        `<button class="primary" id="send-answers">Send answers</button>`;
      $("send-answers").onclick = () => {
        const answers = [...box.querySelectorAll("[data-q]")]
          .map((inp) => ({ id: inp.dataset.q, answer: inp.value }))
          .filter((a) => a.answer);
        fetch(`/sessions/${encodeURIComponent(sid)}/answers`, {
          method: "POST", headers: { "Content-Type": "application/json", ...headers() },
          body: JSON.stringify({ answers }),
        }).then((r) => r.json()).then(() => {
          box.classList.add("hidden");
          listenEvents(sid);
        });
      };
    });
}

/* ---------- connect ---------- */
function renderConnect() {
  const host = location.origin;
  const mcp = host + "/mcp";
  $("openai-url").textContent = host + "/v1";
  $("anthropic-url").textContent = host;
  $("mcp-url").textContent = mcp;
  $("empty-snippet").textContent =
    `curl ${host}/v1/chat/completions -H "Content-Type: application/json" ` +
    `-H "x-optimizer-mode: ${state.mode}" ` +
    `-d '{"model":"groq/openai/gpt-oss-20b","messages":[{"role":"user","content":"hi"}]}'`;
  const s = JSON.stringify({
    mcpServers: { "query-optimizer": { url: mcp } },
  }, null, 2);
  $("snip-claude").textContent = "claude_desktop_config.json:\n" + s;
  $("snip-cursor").textContent = ".cursor/mcp.json:\n" + s;
  $("snip-claudecode").textContent =
    `claude mcp add query-optimizer --transport http ${mcp}`;
  $("snip-opencode").textContent =
    `opencode config: { "mcp": { "query-optimizer": { "url": "${mcp}" } } }`;
}

/* ---------- wiring ---------- */
document.querySelectorAll(".nav-btn").forEach((b) =>
  b.addEventListener("click", () => {
    showPage(b.dataset.page);
    location.hash = b.dataset.page;
  }));
// hash navigation: show the right page on load and on back/forward
function showPageFromHash() {
  const h = location.hash.replace("#", "");
  if (["overview", "specs", "newspec", "connect"].includes(h)) showPage(h);
}
window.addEventListener("hashchange", showPageFromHash);
showPageFromHash();
$("range-select").addEventListener("change", (e) => {
  state.range = e.target.value; state.page = 0; refreshOverview();
});
$("mode-select").addEventListener("change", (e) => { state.mode = e.target.value; renderConnect(); });
$("auto-refresh").addEventListener("change", (e) => {
  state.auto = e.target.checked;
  if (state.auto) state.timer = setInterval(refreshOverview, 10000);
  else clearInterval(state.timer);
});
$("create-spec").addEventListener("click", newSpec);
$("theme-toggle").addEventListener("click", () => {
  const cur = document.documentElement.dataset.theme;
  document.documentElement.dataset.theme = cur === "dark" ? "light" : "dark";
});
$("token-save").addEventListener("click", () => {
  sessionStorage.setItem("qo_token", $("token-input").value.trim());
  $("token-input").value = "";
  refreshOverview();
});
document.querySelectorAll("[data-copy]").forEach((b) =>
  b.addEventListener("click", () => copyText(b.dataset.copy)));
if (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches) {
  document.documentElement.dataset.theme = "light";
}
if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
  document.querySelectorAll(".skeleton").forEach((e) => (e.style.animation = "none"));
}
$("health").textContent = "checking…";
api("/ui/api/health").then((h) => {
  $("health").textContent = h.ok ? "healthy" : "DB down";
  $("health").className = "health " + (h.ok ? "ok" : "bad");
}).catch(() => {
  $("health").textContent = "unreachable"; $("health").className = "health bad";
});
showPage("overview");
