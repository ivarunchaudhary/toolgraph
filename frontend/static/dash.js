/* ToolGraph dashboard (/app). Needs site.js (window.TG). The key lives only in this browser's localStorage. */
(function () {
  "use strict";
  const { $, $$, esc, fmt, api, store, wireCopy, wireTabs, renderResults, renderMeter, stripId } = window.TG;
  const KEY = "toolgraph.key";
  const origin = window.location.origin;

  let key = store.get(KEY);
  let ws = null;          // last GET /v1/workspace
  let pendingKey = null;  // key shown once, before the user continues
  let lastFind = null;    // {task, tools[]} for report_outcome

  wireCopy();
  wireTabs();

  const views = ["boot", "view-onboard", "view-key", "view-dash"];
  function show(id) {
    views.forEach((v) => { $("#" + v).hidden = v !== id; });
    $("#nav-forget").hidden = id !== "view-dash";
    window.scrollTo(0, 0);
  }
  function busy(btn, on, label) {
    if (!btn) return;
    if (on) { btn.dataset.idle = btn.textContent; btn.textContent = label || "Working…"; btn.disabled = true; btn.classList.add("busy"); }
    else { btn.textContent = btn.dataset.idle || btn.textContent; btn.disabled = false; btn.classList.remove("busy"); }
  }
  const msg = (el, kind, html) => { el.innerHTML = html ? `<p class="${kind}">${html}</p>` : ""; };

  /* ---------------------------------------------------------------- onboarding */
  $("#create-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = $("#ws-name").value.trim();
    if (!name) { msg($("#ob-msg"), "err", "Give your workspace a name."); $("#ws-name").focus(); return; }
    const b = $("#create-go");
    busy(b, true, "Creating…"); msg($("#ob-msg"), "", "");
    try {
      const r = await api("/v1/workspaces", { body: { name } });
      showKeyOnce(r.key, false);
    } catch (err) {
      msg($("#ob-msg"), "err", esc(err.status === 429 ? "Too many workspaces created from this network in the last hour. Try again later." : err.message));
    } finally { busy(b, false); }
  });

  $("#key-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const k = $("#have-key").value.trim();
    if (!k) { msg($("#ob-msg"), "err", "Paste your workspace key, which starts with <code>tg_</code>."); $("#have-key").focus(); return; }
    const b = $("#key-go");
    busy(b, true, "Opening…"); msg($("#ob-msg"), "", "");
    try {
      await api("/v1/workspace", { key: k });
      key = k; store.set(KEY, k); $("#have-key").value = "";
      await load();
    } catch (err) {
      msg($("#ob-msg"), "err", err.status === 401 ? "That key doesn't match any workspace. Check for missing characters, or create a new workspace." : esc(err.message));
    } finally { busy(b, false); }
  });

  function showKeyOnce(k, rotated) {
    pendingKey = k;
    key = k;
    const saved = store.set(KEY, k);
    $("#key-store-warn").hidden = saved;
    $("#new-key").textContent = k;
    $("#key-h").textContent = rotated ? "Your new key" : "Save your key now";
    $("#key-lede").textContent = rotated
      ? "Your old key and its secret MCP URL stopped working. Update every agent that used them with this key. This is the only time it is shown."
      : "This is the only time ToolGraph shows this key. We store only a hash of it, so it can't be recovered later.";
    show("view-key");
    $("#key-continue").focus();
  }
  $("#key-continue").addEventListener("click", () => { pendingKey = null; load(); });

  function forget() {
    if (!window.confirm("Forget this workspace key on this device? You'll need the key to open the workspace again.")) return;
    store.del(KEY); key = null; ws = null;
    msg($("#ob-msg"), "", "");
    show("view-onboard");
  }
  $("#forget").addEventListener("click", forget);
  $("#nav-forget").addEventListener("click", forget);

  /* ---------------------------------------------------------------- load + render */
  async function load() {
    if (!key) { show("view-onboard"); return; }
    try {
      ws = await api("/v1/workspace", { key });
    } catch (err) {
      if (err.status === 401) {
        store.del(KEY); key = null;
        show("view-onboard");
        msg($("#ob-msg"), "err", "The saved key no longer opens a workspace (it may have been rotated). Paste your current key or create a new workspace.");
      } else {
        $("#boot").innerHTML = `<span class="err" style="display:block">${esc(err.message)}</span>`;
        show("boot");
      }
      return;
    }
    renderHead();
    renderServers();
    renderConnect();
    show("view-dash");
    loadTools();
    loadPresets();
  }

  function renderHead() {
    $("#ws-title").textContent = ws.name || ws.workspace;
    $("#ws-slug").textContent = ws.workspace;
    $("#ws-plan").textContent = (ws.plan && ws.plan.name) || "Free";
    $("#st-tools").textContent = fmt(ws.tools);
    $("#st-tools-lim").textContent = ws.plan && ws.plan.tools_limit ? `of ${fmt(ws.plan.tools_limit)}` : "";
    $("#st-calls").textContent = fmt(ws.calls);
    $("#st-outcomes").textContent = fmt(ws.outcomes);
    const none = !ws.tools;
    $("#try-empty").hidden = !none;
    $("#try-go").disabled = none;
  }

  const SOURCE = { paste: "pasted", url: "imported from URL", preset: "preset", sync: "synced" };
  function renderServers() {
    const list = $("#servers");
    const s = ws.servers || [];
    $("#srv-count").textContent = s.length ? `${s.length} connected` : "";
    if (!s.length) {
      list.innerHTML = `<li class="empty" style="border:0">No servers yet. Sync the ones you already use, paste a tool list, or pick a preset to start.</li>`;
      return;
    }
    list.innerHTML = s.map((x) => `<li>
      <span class="nm">${esc(x.server)}</span>
      <span class="meta">${fmt(x.tools)} ${x.tools === 1 ? "tool" : "tools"}, ${esc(SOURCE[x.source] || x.source || "")}</span>
      <button class="btn sm quiet" type="button" data-remove="${esc(x.server)}" aria-label="Remove server ${esc(x.server)}">Remove</button>
    </li>`).join("");
  }

  $("#servers").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-remove]");
    if (!b) return;
    const name = b.dataset.remove;
    if (!window.confirm(`Remove ${name} and its tools from this workspace?`)) return;
    busy(b, true, "Removing…");
    try {
      await api("/v1/workspace/servers/" + encodeURIComponent(name), { method: "DELETE", key });
      msg($("#add-msg"), "ok-note", `Removed <code>${esc(name)}</code>.`);
      await refresh();
    } catch (err) { msg($("#add-msg"), "err", esc(err.message)); busy(b, false); }
  });

  function renderConnect() {
    const c = ws.connect || {};
    const sub = (s) => String(s || "").split("<your key>").join(key);
    $("#c-cc").textContent = sub(c.claude_code);
    $("#c-ca").textContent = sub(c.secret_mcp_url);
    $("#c-cu").textContent = sub(JSON.stringify(c.cursor || {}, null, 2));
    $("#c-curl").textContent = sub(c.curl);
    $("#c-mcp").textContent = c.mcp_url || origin + "/mcp";
    $("#cli-sync").textContent = `toolgraph sync --key ${key} --ws-url ${origin}`;
  }

  async function refresh() {
    try { ws = await api("/v1/workspace", { key }); } catch (err) { msg($("#add-msg"), "err", esc(err.message)); return; }
    renderHead(); renderServers(); markPresets(); loadTools();
  }
  $("#sync-refresh").addEventListener("click", async (e) => {
    const b = e.currentTarget; busy(b, true, "Refreshing…");
    await refresh();
    busy(b, false);
    msg($("#add-msg"), "ok-note", `Servers refreshed: ${fmt((ws.servers || []).length)} connected.`);
  });

  /* ---------------------------------------------------------------- rotate */
  $("#rotate").addEventListener("click", async (e) => {
    if (!window.confirm("Rotate your key? The current key and secret MCP URL stop working right away, and every agent using them has to be updated.")) return;
    const b = e.currentTarget; busy(b, true, "Rotating…");
    try {
      const r = await api("/v1/workspace/rotate_key", { method: "POST", key });
      showKeyOnce(r.key, true);
    } catch (err) {
      window.alert("Couldn't rotate the key: " + err.message);
    } finally { busy(b, false); }
  });

  /* ---------------------------------------------------------------- add server */
  async function addServer(body, btn) {
    busy(btn, true, "Adding…"); msg($("#add-msg"), "", "");
    try {
      const r = await api("/v1/workspace/servers", { body, key });
      msg($("#add-msg"), "ok-note", `Added ${fmt(r.added)} ${r.added === 1 ? "tool" : "tools"} from <code>${esc(r.server)}</code>.`);
      await refresh();
      return true;
    } catch (err) {
      msg($("#add-msg"), "err", esc(err.message));
      return false;
    } finally { busy(btn, false); }
  }

  $$("[data-sample]").forEach((b) => b.addEventListener("click", async () => {
    const name = b.dataset.sample;
    try {
      const res = await fetch(`/static/samples/${name}.tools.json`);
      if (!res.ok) throw new Error();
      $("#paste-json").value = await res.text();
      $("#paste-name").value = name;
      msg($("#add-msg"), "", "");
    } catch (e) { msg($("#add-msg"), "err", "Couldn't load that sample. Paste your own tools/list JSON instead."); }
  }));

  $("#paste-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const server = $("#paste-name").value.trim(), raw = $("#paste-json").value.trim();
    if (!server) { msg($("#add-msg"), "err", "Name the server, e.g. <code>slack</code>."); $("#paste-name").focus(); return; }
    if (!raw) { msg($("#add-msg"), "err", "Paste the server's tools/list JSON, or load a sample."); $("#paste-json").focus(); return; }
    let tools;
    try { tools = JSON.parse(raw); } catch (err) {
      msg($("#add-msg"), "err", `That isn't valid JSON: ${esc(err.message)}`); $("#paste-json").focus(); return;
    }
    if (await addServer({ server, tools }, $("#paste-go"))) { $("#paste-json").value = ""; $("#paste-name").value = ""; }
  });

  $("#url-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const server = $("#url-name").value.trim(), url = $("#url-url").value.trim(), auth = $("#url-auth").value.trim();
    if (!server) { msg($("#add-msg"), "err", "Name the server, e.g. <code>linear</code>."); $("#url-name").focus(); return; }
    if (!/^https:\/\//i.test(url)) { msg($("#add-msg"), "err", "Enter the server's full URL, starting with https://."); $("#url-url").focus(); return; }
    const body = { server, url };
    if (auth) body.authorization = auth;
    const ok = await addServer(body, $("#url-go"));
    $("#url-auth").value = "";
    if (ok) { $("#url-name").value = ""; $("#url-url").value = ""; }
  });

  let presetsLoaded = false;
  async function loadPresets() {
    if (presetsLoaded) { markPresets(); return; }
    try {
      const r = await api("/v1/presets");
      $("#presets").innerHTML = (r.presets || []).map((p) => `<button class="preset" type="button" data-preset="${esc(p.server)}">
        <b>${esc(p.server)}</b><span>${esc(p.description || "")}</span><span>${fmt(p.tools)} tools</span></button>`).join("") || `<p class="empty">No presets on this server.</p>`;
      presetsLoaded = true;
      markPresets();
    } catch (e) { $("#presets").innerHTML = `<p class="err">Couldn't load presets: ${esc(e.message)}</p>`; }
  }
  function markPresets() {
    const have = new Set((ws && ws.servers || []).map((s) => s.server));
    $$("[data-preset]").forEach((b) => {
      const on = have.has(b.dataset.preset);
      b.classList.toggle("added", on);
      b.setAttribute("aria-label", `${on ? "Re-add" : "Add"} preset ${b.dataset.preset}`);
    });
  }
  $("#presets").addEventListener("click", (e) => {
    const b = e.target.closest("[data-preset]");
    if (!b || b.disabled) return;
    const p = b.dataset.preset;
    b.disabled = true; b.classList.add("busy"); b.setAttribute("aria-busy", "true");
    addServer({ server: p, preset: p }, null).finally(() => {
      b.disabled = false; b.classList.remove("busy"); b.removeAttribute("aria-busy");
    });
  });

  /* ---------------------------------------------------------------- tools table */
  async function loadTools() {
    let r;
    try { r = await api("/v1/workspace/tools", { key }); } catch (e) {
      $("#tools-table").innerHTML = `<p class="err">${esc(e.message)}</p>`; return;
    }
    const L = r.learned || {};
    $("#learned-sum").textContent = (L.intents || L.solved_with || L.next)
      ? `Learned so far: ${fmt(L.intents)} task ${L.intents === 1 ? "pattern" : "patterns"}, ${fmt(L.solved_with)} tool links, ${fmt(L.next)} call-order links.`
      : "Nothing learned yet. Report outcomes from your agent, or below.";
    if (!r.tools || !r.tools.length) {
      $("#tools-table").innerHTML = `<p class="empty">No tools yet. Add a server above.</p>`;
      $("#chains-wrap").hidden = true;
      return;
    }
    const producers = {};
    (r.types || []).forEach((t) => { if (t.producers && t.producers.length) producers[t.id] = t; });
    const chains = Object.values(producers).filter((t) => t.consumers && t.consumers.length);
    $("#chains-wrap").hidden = !chains.length;
    const SHOW = 6;
    $("#chains").innerHTML = chains.slice(0, SHOW).map((t) => {
      const cons = t.consumers.slice(0, 4).map((c) => `<span class="t">${esc(stripId(c))}</span>`).join(" ");
      const more = t.consumers.length > 4 ? ` <span>and ${fmt(t.consumers.length - 4)} more</span>` : "";
      return `<li>${t.producers.map((p) => `<span class="t">${esc(stripId(p))}</span>`).join(" ")}
        <span>produces</span> <span class="t typed">${esc(stripId(t.id))}</span> <span>for</span> ${cons}${more}</li>`;
    }).join("") + (chains.length > SHOW ? `<li>and ${fmt(chains.length - SHOW)} more chains, marked in green in the table below</li>` : "");

    const typeChip = (id, isOut) => {
      const p = producers[id];
      const title = p ? `produced by ${p.producers.map(stripId).join(", ")}` : "supplied by the user or the agent";
      return `<span class="t${p || isOut ? " typed" : ""}" title="${esc(title)}">${esc(stripId(id))}</span>`;
    };
    $("#tools-table").innerHTML = `<div class="table-wrap"><table class="tools">
      <caption class="sr-only">Tools with the typed inputs they consume and the outputs they produce</caption>
      <thead><tr><th scope="col">Tool</th><th scope="col">Consumes</th><th scope="col">Produces</th><th scope="col">Capabilities</th></tr></thead>
      <tbody>${r.tools.map((t) => `<tr class="${t.produces.length ? "producer" : ""}">
        <td><div class="tn">${esc(t.server)}.${esc(t.name)}</div>${t.description ? `<div class="td">${esc(t.description)}</div>` : ""}</td>
        <td><div class="cell">${t.consumes.map((c) => typeChip(c, false)).join("") || `<span class="hint">none</span>`}</div></td>
        <td><div class="cell">${t.produces.map((c) => typeChip(c, true)).join("") || `<span class="hint">none</span>`}</div></td>
        <td><div class="cell">${t.capabilities.map((c) => `<span class="t">${esc(stripId(c))}</span>`).join("")}</div></td>
      </tr>`).join("")}</tbody></table></div>
      <p class="hint" style="margin-top:10px">Green types have a producer in this workspace. Rows with a green edge produce an input that other tools need.</p>`;
  }

  /* ---------------------------------------------------------------- try + outcome */
  $("#try-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const task = $("#try-task").value.trim();
    const out = $("#try-out"), res = $("#try-results");
    if (task.length < 3) { out.hidden = false; res.innerHTML = `<p class="err">Describe the task in at least a few words.</p>`; $("#try-meter").innerHTML = ""; $("#feedback").hidden = true; return; }
    const b = $("#try-go");
    busy(b, true, "Finding…");
    try {
      const r = await api("/v1/find_tools", { body: { task }, key });
      out.hidden = false;
      renderResults(res, r);
      if (r.tools && r.tools.length) renderMeter($("#try-meter"), r); else $("#try-meter").innerHTML = "";
      lastFind = { task, tools: (r.tools || []).map((t) => `${t.server}.${t.name}`) };
      $("#feedback").hidden = !lastFind.tools.length;
      $("#fb-tools").innerHTML = lastFind.tools.map((n) => `<label class="fb-tool"><input type="checkbox" value="${esc(n)}" checked> <code>${esc(n)}</code></label>`).join("");
      $("#fb-out").innerHTML = ""; $("#fb-missing").value = "";
      $("#fb-ok").disabled = $("#fb-fail").disabled = false;
      ws.calls = (ws.calls || 0) + 1; $("#st-calls").textContent = fmt(ws.calls);
    } catch (err) {
      out.hidden = false; res.innerHTML = `<p class="err">${esc(err.message)}</p>`; $("#try-meter").innerHTML = ""; $("#feedback").hidden = true;
    } finally { busy(b, false); if (!ws.tools) b.disabled = true; }
  });

  async function report(success, btn) {
    if (!lastFind) return;
    const missing = $("#fb-missing").value.trim();
    const called = $$("#fb-tools input:checked").map((i) => i.value);
    if (!called.length) { $("#fb-out").innerHTML = `<p class="err">Tick at least one tool your agent called.</p>`; return; }
    const body = { task: lastFind.task, tools: called, success };
    if (!success && missing) body.missing = missing;
    busy(btn, true, "Reporting…");
    try {
      const r = await api("/v1/outcome", { body, key });
      $("#fb-out").innerHTML = learnedHtml(r, success);
      $("#fb-ok").disabled = $("#fb-fail").disabled = true;
      ws.outcomes = (ws.outcomes || 0) + 1; $("#st-outcomes").textContent = fmt(ws.outcomes);
      loadTools();
    } catch (err) { $("#fb-out").innerHTML = `<p class="err">${esc(err.message)}</p>`; }
    finally { busy(btn, false); if (!$("#fb-out").querySelector(".err")) { $("#fb-ok").disabled = $("#fb-fail").disabled = true; } }
  }
  $("#fb-ok").addEventListener("click", (e) => report(true, e.currentTarget));
  $("#fb-fail").addEventListener("click", (e) => report(false, e.currentTarget));

  function learnedHtml(r, success) {
    if (!r.learned) return `<p class="hint">${esc(r.note || "Nothing was learned from this report.")}</p>`;
    const edges = r.reinforced || r.penalised || {};
    const names = Object.keys(edges);
    const items = names.map((t) => `<li><code>${esc(stripId(t))}</code> ${success ? "strengthened" : "weakened"} by ${esc(edges[t])}</li>`);
    if (r.gap) {
      const g = typeof r.gap === "object" ? r.gap : { type: r.gap };
      items.push(`<li>Missing input noted: <code>${esc(stripId(g.type))}</code>.${g.producer ? ` <code>${esc(stripId(g.producer))}</code>, which produces it, now ranks higher for tasks like this.` : ""}</li>`);
    }
    if (r.unmatched && r.unmatched.length) items.push(`<li>Not in this workspace, so ignored: ${r.unmatched.map((u) => `<code>${esc(u)}</code>`).join(", ")}</li>`);
    const head = success
      ? `<p class="ok-note">Recorded as a success. Similar tasks will get this chain sooner.</p>`
      : `<p class="warn-note">Recorded as a failure. Similar tasks will lean on these tools less.</p>`;
    return head + (items.length ? `<ul>${items.join("")}</ul>` : "");
  }

  /* ---------------------------------------------------------------- boot */
  load();
})();
