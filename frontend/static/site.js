/* ToolGraph site: shared helpers (window.TG) + the landing page.
   Every number on the landing page is fetched at runtime: /v1/find_tools, /api/results, /v1/usage. */
(function () {
  "use strict";

  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n, d = 0) => (n == null || isNaN(n) ? "–" : Number(n).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d }));
  const pct = (x, d = 0) => fmt(100 * x, d) + "%";
  const stripId = (id) => String(id || "").replace(/^(tool|type|cap|intent|server):/, "");
  const reducedMotion = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  async function api(path, opts = {}) {
    const headers = Object.assign({}, opts.body ? { "content-type": "application/json" } : {}, opts.key ? { Authorization: "Bearer " + opts.key } : {});
    let res;
    try {
      res = await fetch(path, { method: opts.method || (opts.body ? "POST" : "GET"), headers, body: opts.body ? JSON.stringify(opts.body) : undefined });
    } catch (e) {
      throw new Error("Can't reach the ToolGraph API. Check your connection and try again.");
    }
    let data = null;
    try { data = await res.json(); } catch (e) { /* non-JSON */ }
    if (!res.ok) {
      let msg = data && data.detail;
      if (Array.isArray(msg)) msg = msg.map((d) => (d.loc ? d.loc.slice(1).join(".") + ": " : "") + d.msg).join("; ");
      const err = new Error(msg || `Request failed (HTTP ${res.status}).`);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  const store = {
    get(k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { window.localStorage.setItem(k, v); return true; } catch (e) { return false; } },
    del(k) { try { window.localStorage.removeItem(k); } catch (e) { /* storage blocked */ } },
  };

  async function copyText(text) {
    try {
      if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); return true; }
    } catch (e) { /* fall through */ }
    const ta = document.createElement("textarea");
    ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    ta.remove();
    return ok;
  }

  /* Any [data-copy-from="#id"] or [data-copy="text"] button copies; label flips to "Copied". */
  function wireCopy(root = document) {
    root.addEventListener("click", async (ev) => {
      const b = ev.target.closest("[data-copy-from],[data-copy]");
      if (!b) return;
      const src = b.dataset.copyFrom ? $(b.dataset.copyFrom) : null;
      const text = src ? src.textContent : b.dataset.copy;
      const ok = await copyText(text || "");
      const old = b.dataset.label || b.textContent;
      b.dataset.label = old;
      b.textContent = ok ? "Copied" : "Press Ctrl+C";
      b.classList.toggle("done", ok);
      clearTimeout(b._t);
      b._t = setTimeout(() => { b.textContent = old; b.classList.remove("done"); }, 1600);
    });
  }

  /* role="tablist" containers with buttons[aria-controls]. */
  function wireTabs(root = document) {
    $$("[role=tablist]", root).forEach((list) => {
      const tabs = $$("[role=tab]", list);
      const select = (t, focus) => {
        tabs.forEach((x) => {
          const on = x === t;
          x.setAttribute("aria-selected", on ? "true" : "false");
          x.tabIndex = on ? 0 : -1;
          const p = document.getElementById(x.getAttribute("aria-controls"));
          if (p) p.classList.toggle("active", on);
        });
        if (focus) t.focus();
        list.dispatchEvent(new CustomEvent("tabchange", { detail: t }));
      };
      tabs.forEach((t, i) => {
        t.addEventListener("click", () => select(t));
        t.addEventListener("keydown", (e) => {
          const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
          if (d) { e.preventDefault(); select(tabs[(i + d + tabs.length) % tabs.length], true); }
        });
      });
    });
  }

  /* ---------------------------------------------------------------- find_tools rendering */
  const ROLE_LABEL = { match: "match", prerequisite: "prerequisite", learned_next: "learned" };

  function whyText(w) {
    if (!w) return "";
    if (w.role === "prerequisite" && w.produces) {
      return `Produces <code>${esc(stripId(w.produces))}</code>${w.for ? ` for <code>${esc(stripId(w.for))}</code>` : ""}`;
    }
    if (w.role === "learned_next" && w.after) return `Agents that succeeded called this after <code>${esc(stripId(w.after))}</code>`;
    if (w.role === "match") return "Matches the task";
    return "";
  }

  function renderResults(el, r) {
    if (!r.tools || !r.tools.length) {
      el.innerHTML = `<p class="results-empty">${esc(r.note || "No tools matched this task. Try naming the app, like Slack or GitHub.")}</p>`;
      return;
    }
    el.innerHTML = `<ol class="results">${r.tools.map((t) => {
      const role = (t.why && t.why.role) || "other";
      const cls = ROLE_LABEL[role] ? role : "other";
      return `<li class="${esc(cls)}">
        <div class="head"><span class="name"><span class="srv">${esc(t.server)}.</span>${esc(t.name)}</span><span class="role ${esc(cls)}">${esc(ROLE_LABEL[role] || role)}</span></div>
        ${t.description ? `<p class="desc">${esc(t.description)}</p>` : ""}
        ${role !== "match" && whyText(t.why) ? `<p class="why">${whyText(t.why)}</p>` : ""}
      </li>`;
    }).join("")}</ol>`;
  }

  function renderMeter(el, r) {
    const all = r.all_tools_tokens || 0, ours = r.tool_tokens || 0;
    const w = (x) => (all ? Math.max(1.5, (100 * x) / all) : 0).toFixed(1) + "%";
    el.innerHTML = `<div class="meter">
      <div class="row"><span>Every tool in the catalog (${fmt(r.catalog_size)})</span><b>${fmt(all)} tokens</b><div class="track"><i data-w="100%"></i></div></div>
      <div class="row ours"><span>What ToolGraph returns (${fmt(r.tools.length)})</span><b>${fmt(ours)} tokens</b><div class="track"><i data-w="${w(ours)}"></i></div></div>
      <p class="saved"><strong>${fmt(r.tokens_saved_pct, 1)}% fewer</strong> tool-definition tokens in the agent's context for this task.</p>
      <dl class="facts">
        <dt>Routing time</dt><dd>${fmt(r.retrieval_ms, 0)} ms</dd>
        <dt>HydraDB round trips</dt><dd>${fmt(r.hydradb_round_trips)}</dd>
        <dt>LLM used to route</dt><dd>None</dd>
      </dl>
    </div>`;
    requestAnimationFrame(() => requestAnimationFrame(() => $$(".track i", el).forEach((i) => { i.style.width = i.dataset.w; })));
  }

  window.TG = { $, $$, esc, fmt, pct, api, store, copyText, wireCopy, wireTabs, renderResults, renderMeter, stripId, reducedMotion };

  /* ================================================================= LANDING */
  if (document.body.dataset.page !== "landing") return;

  wireCopy();
  wireTabs();

  const nav = $(".nav");
  const onScroll = () => nav.classList.toggle("scrolled", window.scrollY > 8);
  window.addEventListener("scroll", onScroll, { passive: true }); onScroll();

  /* ---------- the catalog field: one dot per tool in the catalog, the returned ones lit */
  const field = { n: 0, cols: 0, cells: [] };
  const SVGNS = "http://www.w3.org/2000/svg";

  function hash(s) { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; }

  function buildField(n) {
    const svg = $("#field-svg");
    if (!svg || n === field.n) return;
    field.n = n;
    const narrow = window.innerWidth < 600;
    field.cols = narrow ? 17 : Math.max(10, Math.round(Math.sqrt(n * 1.5)));
    const rows = Math.ceil(n / field.cols), gap = 22, pad = 12;
    const W = pad * 2 + gap * (field.cols - 1), H = pad * 2 + gap * (rows - 1);
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.innerHTML = `<g class="edges"></g><g class="dots"></g>`;
    const g = svg.querySelector(".dots");
    field.cells = [];
    for (let i = 0; i < n; i++) {
      const x = pad + (i % field.cols) * gap, y = pad + Math.floor(i / field.cols) * gap;
      const c = document.createElementNS(SVGNS, "circle");
      c.setAttribute("cx", x); c.setAttribute("cy", y); c.setAttribute("r", 3.2); c.setAttribute("class", "dot");
      g.appendChild(c);
      field.cells.push({ x, y, el: c });
    }
  }

  function lightField(r) {
    buildField(r.catalog_size || 0);
    const svg = $("#field-svg");
    if (!svg || !field.n) return;
    field.cells.forEach((c) => { c.el.setAttribute("class", "dot"); c.el.style.transitionDelay = "0ms"; });
    const taken = new Set(), pos = {};
    r.tools.forEach((t) => {
      const id = `${t.server}.${t.name}`;
      let i = hash(id) % field.n;
      while (taken.has(i)) i = (i + 1) % field.n;
      taken.add(i); pos[id] = i;
    });
    const edges = svg.querySelector(".edges");
    edges.innerHTML = "";
    const slow = !reducedMotion();
    r.tools.forEach((t, k) => {
      const id = `${t.server}.${t.name}`, cell = field.cells[pos[id]];
      const role = (t.why && t.why.role) || "match";
      cell.el.style.transitionDelay = slow ? `${80 + k * 55}ms` : "0ms";
      requestAnimationFrame(() => cell.el.setAttribute("class", `dot lit ${role}`));
      const other = t.why && (t.why.for || t.why.after);
      if (other && pos[stripId(other)] != null) {
        const o = field.cells[pos[stripId(other)]];
        const p = document.createElementNS(SVGNS, "path");
        const mx = (cell.x + o.x) / 2, my = (cell.y + o.y) / 2 - 18;
        p.setAttribute("d", `M${cell.x},${cell.y} Q${mx},${my} ${o.x},${o.y}`);
        p.setAttribute("class", `edge ${role}`);
        edges.appendChild(p);
        requestAnimationFrame(() => requestAnimationFrame(() => p.classList.add("on")));
      }
    });
    const prereq = r.tools.filter((t) => t.why && t.why.role === "prerequisite").length;
    $("#field-caption").innerHTML =
      `For <q>${esc(r._task)}</q>, the agent gets <b>${fmt(r.tools.length)}</b> of <b>${fmt(r.catalog_size)}</b> tools` +
      (prereq ? `, including <b>${fmt(prereq)}</b> ${prereq === 1 ? "prerequisite" : "prerequisites"} that look up inputs it doesn't have yet` : "") +
      `. That's <b>${fmt(r.tool_tokens)}</b> tokens of tool definitions instead of <b>${fmt(r.all_tools_tokens)}</b>.`;
  }

  /* ---------- try box */
  const form = $("#try-form"), input = $("#try-task"), out = $("#try-results"), meter = $("#try-meter");
  let inflight = 0;

  async function runTask(task) {
    task = (task || "").trim();
    if (task.length < 3) { out.innerHTML = `<p class="err">Describe the task in at least a few words.</p>`; return; }
    const my = ++inflight;
    const btn = $("#try-go");
    btn.disabled = true; btn.classList.add("busy"); btn.textContent = "Finding…";
    $$(".chipbtn").forEach((c) => c.setAttribute("aria-pressed", c.dataset.task === task ? "true" : "false"));
    try {
      const r = await api("/v1/find_tools", { body: { task } });
      if (my !== inflight) return;
      r._task = task;
      renderResults(out, r);
      renderMeter(meter, r);
      lightField(r);
    } catch (e) {
      if (my !== inflight) return;
      out.innerHTML = `<p class="err">${esc(e.message)}</p>`;
      meter.innerHTML = "";
      if (!field.n) $("#field-caption").textContent = "The live demo is unavailable right now. Try the box below in a minute.";
    } finally {
      if (my === inflight) { btn.disabled = false; btn.classList.remove("busy"); btn.textContent = "Find tools"; }
    }
  }

  form.addEventListener("submit", (e) => { e.preventDefault(); runTask(input.value); });
  $$(".chipbtn").forEach((c) => c.addEventListener("click", () => { input.value = c.dataset.task; runTask(c.dataset.task); }));
  input.value = $(".chipbtn").dataset.task;
  runTask(input.value);

  /* ---------- connect snippets use this deployment's origin */
  const origin = window.location.origin;
  $$("[data-origin]").forEach((el) => { el.textContent = el.textContent.split("{origin}").join(origin); });

  /* ---------- proof: measured results */
  const mean = (x) => (x && typeof x === "object" ? x.mean : x);

  function lineChart(series, base) {
    const W = 560, H = 230, L = 40, R = 120, T = 14, B = 34;
    const n = series.length;
    const vals = series.concat(base == null ? [] : [base]);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    lo = Math.max(0, Math.floor((lo - 0.05) * 10) / 10); hi = Math.min(1, Math.ceil((hi + 0.02) * 10) / 10);
    const x = (i) => L + (i * (W - L - R)) / Math.max(1, n - 1);
    const y = (v) => T + (1 - (v - lo) / (hi - lo || 1)) * (H - T - B);
    const ticks = []; for (let v = lo; v <= hi + 1e-9; v += 0.1) ticks.push(+v.toFixed(2));
    const pts = series.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
    const last = series[n - 1];
    return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Success rate on held-out tasks by number of training batches: ToolGraph from ${pct(series[0])} to ${pct(last)}${base != null ? `; vector top-15 baseline ${pct(base)}` : ""}">
      <g class="grid">${ticks.map((v) => `<line x1="${L}" x2="${W - R + 8}" y1="${y(v)}" y2="${y(v)}"/>`).join("")}</g>
      <g class="axis">${ticks.map((v) => `<text x="${L - 8}" y="${y(v) + 4}" text-anchor="end">${Math.round(v * 100)}%</text>`).join("")}
        ${series.map((_, i) => `<text x="${x(i)}" y="${H - 12}" text-anchor="middle">${i}</text>`).join("")}</g>
      ${base != null ? `<line class="base" x1="${L}" x2="${x(n - 1)}" y1="${y(base)}" y2="${y(base)}"/><text class="lbl base" x="${x(n - 1) + 8}" y="${y(base) + 4}">Vector ${pct(base)}</text>` : ""}
      <polyline class="ours" points="${pts}"/>
      ${series.map((v, i) => `<circle class="ours-pt" cx="${x(i)}" cy="${y(v)}" r="4"/>`).join("")}
      <text class="lbl ours" x="${x(n - 1) + 8}" y="${y(last) + 4}">ToolGraph ${pct(last)}</text>
    </svg>`;
  }

  function bars(rows) {
    const max = Math.max(...rows.map((r) => r.v));
    return `<div class="bars">${rows.map((r) => `<div class="row"><span>${esc(r.label)}</span><div class="track"><i style="width:${Math.max(1.2, (100 * r.v) / max).toFixed(1)}%;background:${r.color}"></i></div><b>${esc(r.text)}</b></div>`).join("")}</div>`;
  }

  async function loadProof() {
    const sec = $("#proof");
    let R;
    try { R = await api("/api/results"); } catch (e) { sec.hidden = true; return; }
    const S = R.sufficiency || R.benchmark;
    const curve = S && S.aggregate && S.aggregate.graph_learning && S.aggregate.graph_learning.success_rate;
    if (!curve || curve.length < 2) { sec.hidden = true; return; }
    const series = curve.map(mean);
    const BL = S.aggregate.baselines || {};
    const vk = BL.vector_k15 && mean(BL.vector_k15.success_rate);
    const first = series[0], last = series[series.length - 1];
    const cfg = S.config || {};
    const isSolver = !!R.sufficiency;

    $("#proof-curve-claim").innerHTML = `Held-out tasks solved with only the returned tools went from <b>${pct(first)}</b> to <b>${pct(last)}</b> after ${fmt(series.length - 1)} batches of reported outcomes.`;
    $("#proof-curve").innerHTML = lineChart(series, vk);
    $("#proof-curve-note").textContent = `X axis: training batches of report_outcome. Dashed line: vector search returning its top 15 tools.`;

    const tokOurs = mean(S.aggregate.graph_learning.context_tokens && S.aggregate.graph_learning.context_tokens[series.length - 1]);
    const tokAll = BL.all_tools && mean(BL.all_tools.context_tokens);
    const tokVec = BL.vector_k15 && mean(BL.vector_k15.context_tokens);
    if (tokOurs && tokAll) {
      $("#proof-tokens-claim").innerHTML = `<b>${fmt(tokOurs)}</b> tokens of tool definitions per task, against <b>${fmt(tokAll)}</b> for loading every tool.`;
      const rows = [{ label: "All tools", v: tokAll, text: fmt(tokAll), color: "var(--muted)" }];
      if (tokVec) rows.push({ label: "Vector top 15", v: tokVec, text: fmt(tokVec), color: "var(--vector)" });
      rows.push({ label: "ToolGraph", v: tokOurs, text: fmt(tokOurs), color: "var(--tool)" });
      $("#proof-tokens").innerHTML = bars(rows);
    } else { $("#proof-tokens-panel").hidden = true; }

    const B = R.benchmark;
    const bc = B && B.aggregate && B.aggregate.graph_learning && B.aggregate.graph_learning.success_rate;
    if (isSolver && bc && bc.length > 1) {
      const bv = B.aggregate.baselines && B.aggregate.baselines.vector_k15 && mean(B.aggregate.baselines.vector_k15.success_rate);
      $("#proof-llm-claim").innerHTML = `With a real LLM planner (<code>${esc(B.config.planner)}</code>), success went from <b>${pct(mean(bc[0]))}</b> to <b>${pct(mean(bc[bc.length - 1]))}</b>${bv != null ? `; vector top 15 scored <b>${pct(bv)}</b>` : ""}.`;
      $("#proof-llm-note").textContent = `${fmt(B.config.n_test)} held-out tasks, ${fmt(B.config.seeds)} ${B.config.seeds === 1 ? "seed" : "seeds"}.`;
    } else { $("#proof-llm-panel").hidden = true; }

    $("#proof-src").textContent = isSolver
      ? `Measured on ${fmt(cfg.n_test)} held-out tasks over ${fmt(cfg.seeds)} seeds. "Solved" means a deterministic planner can complete the task using only the tools ToolGraph returned, so the score measures whether the context was sufficient, not how good a particular model is.`
      : `Measured on ${fmt(cfg.n_test)} held-out tasks over ${fmt(cfg.seeds)} ${cfg.seeds === 1 ? "seed" : "seeds"} with planner ${cfg.planner}.`;

    // Ablation for the "why a graph" section: same pipeline with prerequisite edges switched off.
    const A = R.ablations_sufficiency && R.ablations_sufficiency.aggregate;
    if (A && A.graph_full && A.no_prereq) {
      $("#ablation").innerHTML = `In our benchmark, switching off prerequisite edges drops held-out success from <b>${pct(mean(A.graph_full.success_rate))}</b> to <b>${pct(mean(A.no_prereq.success_rate))}</b>.`;
      $("#ablation").hidden = false;
    }
  }
  loadProof();

  /* ---------- live usage line */
  api("/v1/usage").then((u) => {
    if (!u || u.workspaces == null) return;
    const el = $("#usage");
    el.innerHTML = `<b>${fmt(u.workspaces)}</b> ${u.workspaces === 1 ? "workspace" : "workspaces"} created and <b>${fmt(u.find_tools)}</b> find_tools ${u.find_tools === 1 ? "call" : "calls"} served so far.`;
    el.hidden = false;
  }).catch(() => {});
})();
