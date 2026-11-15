/* ToolGraph UI: plain JS, no build step. Every number shown comes from the API. */
"use strict";

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const fmt = (n, d = 0) => Number(n).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d });
const pct = (x) => `${Math.round(x * 100)}%`;
const short = (k) => String(k).replace(/^(tool|type|cap|server):/, "");

const S = { examples: [], types: {}, ex: null, given: [], cy: null, charts: {}, resultsLoaded: false };

async function api(path, body) {
  const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `${r.status} ${r.statusText}`);
  return data;
}

function busy(on) {
  document.querySelectorAll(".actions .btn, #btn-compare").forEach((b) => (b.disabled = on));
}

function toast(msg) {
  $("#cy-caption").innerHTML = `<span style="color:${css("--bad")}">${esc(msg)}</span>`;
}

/* ---------------- tabs ---------------- */
document.querySelectorAll(".tab").forEach((t) =>
  t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => { x.classList.toggle("active", x === t); x.setAttribute("aria-selected", x === t); });
    document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("active", p.id === `panel-${t.dataset.tab}`));
    $(".task-card").style.display = ["results", "how"].includes(t.dataset.tab) ? "none" : "";
    if (t.dataset.tab === "results") loadResults();
    if (t.dataset.tab === "how") loadHow();
    if (t.dataset.tab === "route" && S.cy) S.cy.resize();
  })
);

/* ---------------- status ---------------- */
async function loadStatus() {
  const el = $("#status");
  try {
    const st = await api("/api/graph/stats");
    const e = Object.values(st.edges).reduce((a, b) => a + b, 0);
    el.className = "status ok";
    const dn = $("#demo-n"); if (dn) dn.textContent = `${st.catalog.tools} demo tools across ${st.catalog.servers} services`;
    $("#status-text").textContent = `HydraDB · ${st.catalog.tools} tools · ${fmt(e)} edges · ${st.learned.intents} learned intents`;
  } catch {
    el.className = "status down";
    $("#status-text").textContent = "HydraDB unreachable";
  }
}

/* ---------------- task input ---------------- */
function renderGiven() {
  $("#given").innerHTML = S.given.map((g) => `<span class="chip" title="${esc(S.types[g] || "")}">${esc(short(g))}<button aria-label="remove ${esc(short(g))}" data-g="${esc(g)}">×</button></span>`).join("") || `<span class="muted">nothing beyond free text</span>`;
  $("#given").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { S.given = S.given.filter((x) => x !== b.dataset.g); renderGiven(); }));
}

function selectExample(ex, btn) {
  S.ex = ex;
  document.querySelectorAll(".ex").forEach((b) => b.classList.toggle("active", b === btn));
  $("#task").value = ex.text;
  S.given = [...ex.given];
  renderGiven();
  $("#goal").textContent = `goal: ${ex.goal.map(short).join(", ")}`;
}

async function loadExamples() {
  const d = await api("/api/examples");
  S.examples = d.examples;
  S.types = d.types;
  $("#examples").innerHTML = d.examples.map((e, i) => `<button class="ex" data-i="${i}">${esc(e.text)}<span class="d">${esc(e.family)} · ${e.difficulty}-step</span></button>`).join("");
  $("#examples").querySelectorAll(".ex").forEach((b) => b.addEventListener("click", () => selectExample(S.examples[+b.dataset.i], b)));
  $("#given-add").innerHTML = `<option value="">+ add input</option>` + Object.entries(d.types).sort().map(([k, v]) => `<option value="${esc(k)}">${esc(short(k))} — ${esc(v)}</option>`).join("");
  if (d.examples.length) selectExample(d.examples[0], $(".ex"));
}

$("#given-add").addEventListener("change", (e) => {
  const v = e.target.value;
  if (v && !S.given.includes(v)) { S.given.push(v); renderGiven(); }
  e.target.value = "";
});
$("#task").addEventListener("input", () => {
  if (S.ex && $("#task").value !== S.ex.text) {
    S.ex = null;
    document.querySelectorAll(".ex").forEach((b) => b.classList.remove("active"));
    $("#goal").textContent = "custom task: retrieval only (no goal to simulate)";
  }
});

/* ---------------- graph view ---------------- */
function kindStyle(k, sel) {
  return { Capability: css("--cap"), Intent: css("--intent"), DataType: css("--type"), Tool: sel ? css("--graph") : css("--muted") }[k] || css("--muted");
}

function drawGraph(sub) {
  const nodes = sub.nodes.map((n) => ({
    data: { id: n.id, label: n.kind === "Intent" ? "learned intent" : short(n.id), kind: n.kind, color: kindStyle(n.kind, n.selected) },
    classes: n.kind,
  }));
  const ids = new Set(nodes.map((n) => n.data.id));
  const edges = sub.edges.filter((e) => ids.has(e.source) && ids.has(e.target)).map((e, i) => ({ data: { id: `e${i}`, source: e.source, target: e.target, rel: e.rel } }));
  if (S.cy) S.cy.destroy();
  S.cy = cytoscape({
    container: $("#cy"),
    elements: [...nodes, ...edges],
    style: [
      { selector: "node", style: { "background-color": "data(color)", label: "data(label)", color: css("--text-2"), "font-size": 10, "font-family": "JetBrains Mono, monospace", "text-valign": "bottom", "text-margin-y": 5, width: 18, height: 18, "border-width": 2, "border-color": css("--bg"), "text-wrap": "ellipsis", "text-max-width": 150 } },
      { selector: "node.Tool", style: { width: 24, height: 24, color: css("--text") } },
      { selector: "node.DataType", style: { shape: "round-rectangle", width: 14, height: 14 } },
      { selector: "node.Intent", style: { shape: "diamond", width: 22, height: 22 } },
      { selector: "edge", style: { width: 1.5, "line-color": css("--border"), "target-arrow-color": css("--border"), "target-arrow-shape": "triangle", "arrow-scale": 0.8, "curve-style": "bezier", "font-size": 9, color: css("--muted"), "text-rotation": "autorotate", "text-background-color": css("--bg"), "text-background-opacity": 1, "text-background-padding": 1 } },
      { selector: 'edge[rel = "SOLVED_WITH"]', style: { "line-color": css("--intent"), "target-arrow-color": css("--intent"), width: 2 } },
      { selector: 'edge[rel = "NEXT"]', style: { "line-color": css("--graph"), "target-arrow-color": css("--graph"), "line-style": "dashed" } },
      { selector: "edge.hover", style: { label: "data(rel)" } },
      { selector: "node:selected", style: { "border-color": css("--text"), "border-width": 2 } },
    ],
    layout: { name: "cose", animate: false, nodeRepulsion: 12000, idealEdgeLength: 80, padding: 30, fit: true, randomize: false, componentSpacing: 60 },
  });
  S.cy.on("mouseover", "edge", (e) => e.target.addClass("hover"));
  S.cy.on("mouseout", "edge", (e) => e.target.removeClass("hover"));
  S.cy.on("mouseover", "node", (e) => e.target.connectedEdges().addClass("hover"));
  S.cy.on("mouseout", "node", (e) => e.target.connectedEdges().removeClass("hover"));
  S.cy.fit(undefined, 30);
}

function renderRetrieval(r, allTok) {
  drawGraph(r.subgraph);
  const caps = (r.debug?.caps || []).map((c) => short(c[0])).join(", ");
  const nIntents = (r.debug?.intents || []).length;
  $("#cy-caption").textContent = `Entry capabilities: ${caps || "—"}. ${nIntents ? `${nIntents} learned intent(s) matched.` : "No learned intent matched."} ${r.debug?.servers?.length ? `Servers named: ${r.debug.servers.join(", ")}.` : ""}`;
  $("#t-tokens").textContent = fmt(r.approx_tokens);
  $("#t-tokens-sub").textContent = allTok ? `vs ${fmt(allTok)} with all tools (−${Math.round((1 - r.approx_tokens / allTok) * 100)}%)` : "";
  $("#t-lat").textContent = `${fmt(r.latency_ms, 1)} ms`;
  $("#t-lat-sub").textContent = `${r.db_round_trips} HydraDB round trips · ${fmt(r.db_ms, 1)} ms in DB`;
  $("#tools").innerHTML = r.tools.map((t) => { const [srv, name] = short(t).split("."); return `<li><span class="srv">${esc(srv)}.</span>${esc(name)}</li>`; }).join("");
}

function task() {
  const t = $("#task").value.trim();
  if (t.length < 3) throw new Error("write a task first");
  return t;
}

async function doRetrieve() {
  busy(true);
  try {
    const r = await api("/api/retrieve", { task: task(), given: S.given, strategy: "graph" });
    renderRetrieval(r, r.all_tools_tokens);
    $("#run-card").hidden = true;
  } catch (e) { toast(e.message); } finally { busy(false); }
}

async function doRun(strategy, learn) {
  busy(true);
  try {
    const body = S.ex && $("#task").value === S.ex.text ? { task_id: S.ex.id, strategy, learn } : { task: task(), given: S.given, strategy, learn };
    const [r, all] = await Promise.all([api("/api/run", body), api("/api/retrieve", { task: task(), given: S.given, strategy: "all_tools" })]);
    renderRetrieval({ ...r.retrieval, debug: r.retrieval.debug }, all.approx_tokens);
    const o = r.outcome;
    $("#run-card").hidden = false;
    $("#outcome").innerHTML = o
      ? `<span class="badge ${o.success ? "ok" : "fail"}">${o.success ? "✓ task completed" : "✗ task failed"}</span> <span class="muted">${esc(strategy)} · planner: ${esc(r.planner)}</span>${o.success ? "" : `<div class="reason">${esc(o.failure_reason)}</div>`}`
      : `<span class="muted">custom task: no goal types, so the plan is not simulated · planner: ${esc(r.planner)}</span>`;
    $("#plan").innerHTML = r.plan.map((s) => `<li>${esc(short(s.tool))}${s.why && s.why !== "solver" ? `<span class="why">${esc(s.why)}</span>` : ""}</li>`).join("") || `<li class="muted">empty plan</li>`;
    const L = r.learned;
    $("#learned").innerHTML = L
      ? `<div class="learned"><b>Graph updated.</b> ${Object.keys(L.solved).length ? `SOLVED_WITH weights: ${Object.entries(L.solved).map(([k, w]) => `<code>${esc(short(k))}</code> → ${fmt(w, 2)}`).join(", ")}. ` : ""}${L.gap ? `Gap-driven: graph says <code>${esc(short(L.gap.producer))}</code> produces the missing <code>${esc(short(L.gap.type))}</code>. ` : ""}${Object.keys(L.next).length ? `${Object.keys(L.next).length} NEXT edge(s) updated. ` : ""}Run again to see the effect.</div>`
      : "";
    loadStatus();
  } catch (e) { toast(e.message); } finally { busy(false); }
}

$("#btn-retrieve").addEventListener("click", doRetrieve);
$("#btn-run").addEventListener("click", () => doRun("graph", true));
$("#btn-static").addEventListener("click", () => doRun("graph_static", false));

/* ---------------- compare ---------------- */
const STRATS = [
  ["all_tools", "every tool in the catalog"],
  ["vector_k8", "cosine top-8"],
  ["vector_k15", "cosine top-15"],
  ["graph_static", "graph, no learned edges"],
  ["graph", "ToolGraph (learned)"],
];
async function doCompare() {
  busy(true);
  const box = $("#compare");
  try {
    const t = task();
    const c = await api("/api/compare", { task: t, given: S.given });
    const maxTok = Math.max(...Object.values(c.strategies).map((s) => s.approx_tokens));
    const draw = (runs) => {
      box.innerHTML = STRATS.map(([k, label]) => {
        const s = c.strategies[k];
        const run = runs?.[k];
        const res = run ? (run.outcome ? `<div class="res"><span class="badge ${run.outcome.success ? "ok" : "fail"}">${run.outcome.success ? "✓ completed" : "✗ failed"}</span></div>` : "") : runs === undefined ? "" : `<div class="res muted">…</div>`;
        return `<div class="strat ${k === "graph" ? "hl" : ""}"><h3>${esc(k)}</h3><div class="muted" style="font-size:12px">${esc(label)}</div>
          <div class="num">${fmt(s.approx_tokens)} <span class="muted" style="font-size:12px;font-weight:400">tokens</span></div>
          <div class="bar"><i style="width:${Math.max(2, (100 * s.approx_tokens) / maxTok)}%"></i></div>
          <div class="muted" style="font-size:12px">${s.tools.length} tools · ${fmt(s.latency_ms, 1)} ms</div>${res}
          <ul>${s.tools.slice(0, 15).map((x) => `<li>${esc(short(x))}</li>`).join("")}${s.tools.length > 15 ? `<li class="muted">+ ${s.tools.length - 15} more</li>` : ""}</ul></div>`;
      }).join("");
    };
    draw();
    if (S.ex && t === S.ex.text) {
      draw({});
      const runs = {};
      for (const [k] of STRATS) {
        try { runs[k] = await api("/api/run", { task_id: S.ex.id, strategy: k }); } catch (e) { runs[k] = null; }
      }
      draw(runs);
    }
  } catch (e) { box.innerHTML = `<p style="color:${css("--bad")}">${esc(e.message)}</p>`; } finally { busy(false); }
}
$("#btn-compare").addEventListener("click", doCompare);

/* ---------------- results ---------------- */
function chartDefaults() {
  Chart.defaults.color = css("--text-2");
  Chart.defaults.borderColor = css("--border");
  Chart.defaults.font.family = "Inter, system-ui, sans-serif";
}

async function loadResults() {
  if (S.resultsLoaded) return;
  S.resultsLoaded = true;
  chartDefaults();
  let R;
  try { R = await api("/api/results"); } catch (e) { $("#res-source").textContent = e.message; return; }
  const B = R.benchmark || R.sufficiency;
  const A = R.ablations || R.ablations_sufficiency;
  const SC = R.scale;
  const label = (d, llm) => (d ? (llm ? `planner ${d.config.planner}` : "context sufficiency (solver planner: shortest valid plan using only the retrieved tools)") : "not run yet");
  $("#res-source").textContent = `Benchmark: ${label(B, !!R.benchmark)} · ${B ? `${B.config.seeds} seeds × ${B.config.iters} training batches, ${B.config.n_test} held-out tasks` : ""}`;

  const G = colors();
  if (B) {
    const gl = B.aggregate.graph_learning, bl = B.aggregate.baselines;
    const s0 = gl.success_rate[0].mean, sN = gl.success_rate.at(-1).mean;
    const tokG = gl.context_tokens.at(-1).mean, tokAll = bl.all_tools.context_tokens.mean;
    const kpis = [
      [pct(sN), `task success after learning`, `${pct(s0)} static · vector_k15 ${pct(bl.vector_k15.success_rate.mean)}`],
      [`−${Math.round((1 - tokG / tokAll) * 100)}%`, "tool tokens vs all tools", `${fmt(tokG)} vs ${fmt(tokAll)} per task`],
      [`${fmt(gl.latency_p50_ms.at(-1).mean, 1)} ms`, "retrieval p50", `p95 ${fmt(gl.latency_p95_ms.at(-1).mean, 1)} ms · ${fmt(gl.db_round_trips.at(-1).mean, 1)} round trips`],
    ];
    if (SC) { const big = SC.sizes.at(-1); kpis.push([pct(big.graph.tool_recall), `recall at ${fmt(big.n_tools)} tools`, `vector_k15 ${pct(big.vector_k15.tool_recall)} · p50 ${fmt(big.graph.latency_p50_ms)} ms`]); }
    $("#kpis").innerHTML = kpis.map(([v, l, s]) => `<div class="kpi"><span class="tl">${esc(l)}</span><div class="tv">${esc(v)}</div><span class="ts">${esc(s)}</span></div>`).join("");

    const its = gl.success_rate.map((_, i) => (i ? `after ${i}` : "static"));
    const band = (sign) => gl.success_rate.map((a) => a.mean + sign * a.std);
    const flat = (v) => its.map(() => v);
    $("#lc-sub").textContent = `held-out success, mean ± std over ${B.config.seeds} seeds; dashed = baselines`;
    S.charts.learn = new Chart($("#c-learn"), {
      type: "line",
      data: { labels: its, datasets: [
        { label: "ToolGraph", data: gl.success_rate.map((a) => a.mean), borderColor: G.graph, backgroundColor: G.graph, borderWidth: 2, pointRadius: 4 },
        { label: "+std", data: band(1), borderWidth: 0, pointRadius: 0, fill: "+1", backgroundColor: G.graphA },
        { label: "-std", data: band(-1), borderWidth: 0, pointRadius: 0, fill: false },
        { label: "vector_k15", data: flat(bl.vector_k15.success_rate.mean), borderColor: G.vector, borderDash: [5, 4], borderWidth: 1.5, pointRadius: 0 },
        { label: "all_tools", data: flat(bl.all_tools.success_rate.mean), borderColor: G.muted, borderDash: [5, 4], borderWidth: 1.5, pointRadius: 0 },
        { label: "vector_k8", data: flat(bl.vector_k8.success_rate.mean), borderColor: G.muted, borderDash: [2, 3], borderWidth: 1.5, pointRadius: 0 },
      ] },
      options: lineOpts({ pctY: true, legendFilter: (l) => !l.text.endsWith("std") }),
    });
  }
  if (A) {
    const order = Object.keys(A.aggregate).sort((a, b) => A.aggregate[b].success_rate.mean - A.aggregate[a].success_rate.mean);
    $("#ab-sub").textContent = "held-out success after training, one component off at a time";
    S.charts.ab = new Chart($("#c-ablate"), {
      type: "bar",
      data: { labels: order, datasets: [{ data: order.map((k) => A.aggregate[k].success_rate.mean), backgroundColor: order.map((k) => (k === "graph_full" ? G.graph : k.startsWith("vector") ? G.vector : G.muted)), borderRadius: 4, barPercentage: 0.7 }] },
      options: { indexAxis: "y", maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => `${pct(c.raw)} · ${fmt(A.aggregate[c.label].context_tokens.mean)} tokens` } } },
        scales: { x: { min: 0, max: 1, ticks: { callback: (v) => pct(v) }, grid: { color: G.grid } }, y: { grid: { display: false }, ticks: { font: { family: "JetBrains Mono" } } } } },
    });
  }
  if (SC) {
    const n = SC.sizes.map((r) => fmt(r.n_tools));
    S.charts.scale = new Chart($("#c-scale"), {
      type: "line",
      data: { labels: n, datasets: [
        { label: "ToolGraph (static)", data: SC.sizes.map((r) => r.graph.tool_recall), borderColor: G.graph, backgroundColor: G.graph, borderWidth: 2, pointRadius: 4 },
        { label: "vector_k15", data: SC.sizes.map((r) => r.vector_k15.tool_recall), borderColor: G.vector, backgroundColor: G.vector, borderWidth: 2, pointRadius: 4 },
      ] },
      options: lineOpts({ pctY: true, xTitle: "tools in catalog" }),
    });
    S.charts.lat = new Chart($("#c-lat"), {
      type: "line",
      data: { labels: n, datasets: [
        { label: "p50", data: SC.sizes.map((r) => r.graph.latency_p50_ms), borderColor: G.graph, backgroundColor: G.graph, borderWidth: 2, pointRadius: 4 },
        { label: "p95", data: SC.sizes.map((r) => r.graph.latency_p95_ms), borderColor: G.graph, backgroundColor: G.graph, borderDash: [5, 4], borderWidth: 2, pointRadius: 4 },
      ] },
      options: lineOpts({ yTitle: "ms", xTitle: "tools in catalog", unit: " ms" }),
    });
  }
  renderTable(B, A, SC);
}

function colors() {
  return { graph: css("--graph"), graphA: "rgba(57,135,229,0.18)", vector: css("--vector"), muted: css("--muted"), grid: css("--border") };
}

function lineOpts({ pctY = false, xTitle, yTitle, unit = "", legendFilter } = {}) {
  return {
    maintainAspectRatio: false,
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: { labels: { boxWidth: 12, boxHeight: 2, filter: legendFilter } },
      tooltip: { filter: (i) => !i.dataset.label.endsWith("std"), callbacks: { label: (c) => `${c.dataset.label}: ${pctY ? pct(c.raw) : fmt(c.raw, 1) + unit}` } },
    },
    scales: {
      y: { min: pctY ? 0 : undefined, max: pctY ? 1 : undefined, ticks: { callback: (v) => (pctY ? pct(v) : v) }, title: { display: !!yTitle, text: yTitle }, grid: { color: css("--border") } },
      x: { title: { display: !!xTitle, text: xTitle }, grid: { display: false } },
    },
  };
}

function renderTable(B, A, SC) {
  const rows = [];
  if (B) {
    const bl = B.aggregate.baselines, gl = B.aggregate.graph_learning;
    const ms = (o) => (o ? `${fmt(o.mean * 100, 1)}% ± ${fmt(o.std * 100, 1)}` : "—");
    rows.push(["<b>Benchmark</b>", "success", "tool recall", "tokens / task", "$ / 1k tasks (input)", "retrieval p50"]);
    for (const k of ["all_tools", "vector_k8", "vector_k15"]) rows.push([k, ms(bl[k].success_rate), ms(bl[k].tool_recall), fmt(bl[k].context_tokens.mean), `$${fmt(bl[k].cost_per_1k_tasks_usd.mean, 2)}`, `${fmt(bl[k].latency_p50_ms.mean, 1)} ms`]);
    const price = bl.all_tools.cost_per_1k_tasks_usd.mean / bl.all_tools.context_tokens.mean;
    for (const [i, name] of [[0, "graph static"], [gl.success_rate.length - 1, "graph learned"]])
      rows.push([name, ms(gl.success_rate[i]), ms(gl.tool_recall[i]), fmt(gl.context_tokens[i].mean), `$${fmt(gl.context_tokens[i].mean * price, 2)}`, `${fmt(gl.latency_p50_ms[i].mean, 1)} ms`, "hl"]);
  }
  if (SC) {
    rows.push(["<b>Scale</b>", "graph recall", "vector_k15 recall", "graph p50 / p95", "ingest", "graph sufficiency"]);
    for (const r of SC.sizes) rows.push([`${fmt(r.n_tools)} tools`, pct(r.graph.tool_recall), pct(r.vector_k15.tool_recall), `${fmt(r.graph.latency_p50_ms, 1)} / ${fmt(r.graph.latency_p95_ms, 1)} ms`, `${fmt(r.ingest_ms / 1000, 1)} s`, pct(r.graph.success_rate)]);
  }
  $("#res-table").innerHTML = rows.map((r) => (r[0].startsWith("<b>") ? `<tr>${r.map((c, i) => `<th>${i ? esc(c) : c}</th>`).join("")}</tr>` : `<tr class="${r[6] || ""}">${r.slice(0, 6).map((c) => `<td>${esc(c)}</td>`).join("")}</tr>`)).join("");
}

/* ---------------- how it works ---------------- */
function cypher(q) {
  const pretty = q.replace(/ UNION ALL /g, "\nUNION ALL\n").replace(/ (OPTIONAL MATCH|WHERE|RETURN|MERGE|SET) /g, "\n  $1 ");
  return esc(pretty).replace(/\b(MATCH|OPTIONAL|UNION ALL|WHERE|RETURN|MERGE|SET|UNWIND|AS|AND|NOT|IN)\b/g, '<span class="kw">$1</span>');
}
async function loadHow() {
  if (S.howLoaded) return;
  S.howLoaded = true;
  const h = await api("/api/how");
  $("#q-seed").innerHTML = cypher(h.seed_query);
  $("#q-hop").innerHTML = cypher(h.hop_query);
  $("#q-union").innerHTML = cypher(h.union_query_depth2);
  $("#q-learn").innerHTML = Object.entries(h.learn_queries).map(([k, q]) => `// ${esc(k)}\n${cypher(q)}`).join("\n\n");
  const r = h.rules;
  $("#rules").innerHTML = [
    `Success: NEXT ${esc(r.success.NEXT)} between consecutive plan steps, SOLVED_WITH ${esc(r.success.SOLVED_WITH)} intent → each tool used`,
    `Failure with a missing input: ${esc(r.gap_driven)} (the graph is asked who produces it)`,
    `Failure: ${esc(r.failure_penalty)}; weights clamped to [${r.clamp.join(", ")}]`,
    `Intent match: masked task text + servers named + inputs supplied, threshold ${fmt(r.intent_threshold, 3)} (calibrated on train only)`,
    `Learning uses simulator outcomes only, never gold labels; the held-out test set never updates the graph`,
  ].map((x) => `<li>${x}</li>`).join("");
}

/* ---------------- boot ---------------- */
loadStatus();
loadExamples().then(doRetrieve).catch((e) => toast(e.message));
