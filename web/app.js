// Deja UI: vanilla JS, no build step.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const state = { status: null, incidents: [], selected: null, busy: false };

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined });
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch {}
    throw new Error(msg);
  }
  return res.json();
}

function toast(msg, kind = "") {
  const t = $("#toast");
  t.className = `toast show ${kind}`;
  t.innerHTML = msg;
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.className = "toast"), 4500);
}

const fmtDate = (iso) => (iso ? new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "");
const fmtTime = (iso) => (iso ? new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) + " UTC" : "");

// ---------------------------------------------------------------- status + lists
async function loadStatus() {
  state.status = await api("/api/status");
  $("#scenarios").innerHTML = state.status.scenarios.map((s) =>
    `<button class="scenario" data-key="${esc(s.key)}">${esc(s.label)}<small>${esc(s.service)} · ${esc(s.severity)}</small></button>`).join("");
  await refreshBadges();
}

async function refreshBadges() {
  const s = state.status;
  let stats = null;
  try { stats = await api("/api/stats"); } catch {}
  const hs = s.memory_backend === "hindsight";
  $("#status").innerHTML = `
    <span class="badge ${hs ? "" : "warn"}"><span class="dot"></span>${hs ? `Hindsight bank <b>${esc(s.bank_id)}</b>` : "<b>Local fallback</b> (set HINDSIGHT_URL)"}</span>
    <span class="badge">memories <b id="mem-count">${stats?.memory_count ?? "–"}</b></span>
    <span class="badge ${s.llm ? "" : "warn"}"><span class="dot"></span>${s.llm ? `LLM <b>${esc(s.llm)}</b>` : "<b>No LLM key</b> · heuristic mode"}</span>`;
  return stats;
}

async function loadIncidents() {
  state.incidents = await api("/api/incidents");
  $("#inc-count").textContent = state.incidents.length ? `(${state.incidents.length})` : "";
  $("#incidents").innerHTML = state.incidents.map((i) => `
    <li data-id="${esc(i.id)}" class="${state.selected === i.id ? "sel" : ""}">
      <div class="row1"><span>${esc(i.id)} · ${esc(i.service)}</span>
        <span class="pill ${i.source === "history" ? "history" : i.status}">${i.source === "history" ? fmtDate(i.started_at) : esc(i.status)}</span></div>
      <div class="t"><span class="sev ${esc(i.severity)}">${esc(i.severity)}</span> ${esc(i.title)}</div>
    </li>`).join("") || `<li class="muted small">No incidents yet.</li>`;
}

// ---------------------------------------------------------------- incident view
function renderAnswer(a, kind, meta = {}) {
  const confColor = a.confidence >= 70 ? "var(--good)" : a.confidence >= 45 ? "var(--warn)" : "var(--bad)";
  const ev = (e) => e ? `<span class="ev ${/general/i.test(e) ? "gen" : ""}">${esc(e)}</span>` : "";
  return `
  <div class="answer ${kind}">
    <div class="who"><span>${kind === "deja" ? "Deja · with Hindsight memory" : "Stateless agent · same LLM, no memory"}</span>
      ${meta.engine ? `<span class="engine">${esc(meta.engine)}${meta.ms ? ` · ${meta.ms} ms` : ""}</span>` : ""}</div>
    ${kind === "deja" ? `<span class="seen ${a.seen_before ? "yes" : "no"}">${a.seen_before
      ? `Seen before: ${a.matched_incidents.length || 1} matching incident${a.matched_incidents.length === 1 ? "" : "s"}`
      : "New failure mode: nothing in memory yet"}</span>` : ""}
    <h4>${esc(a.headline)}</h4>
    <div class="conf">confidence <span class="meter"><i style="width:${a.confidence}%;background:${confColor}"></i></span> ${a.confidence}%</div>
    ${a.matched_incidents?.length ? `<div class="lbl">Matched memories</div><div class="matches">${a.matched_incidents.map((m) =>
      `<span class="match"><b>${esc(m.id)}</b> ${esc(m.date || "")} · ${esc(m.why || "")}</span>`).join("")}</div>` : ""}
    <div class="lbl">Likely root cause</div><div>${esc(a.likely_root_cause)}</div>
    <div class="lbl">Do this first</div>
    <ol>${a.first_actions.map((x) => `<li>${esc(x.action)} ${ev(x.evidence)}<span class="why">${esc(x.why)}</span></li>`).join("")}</ol>
    ${a.do_not.length ? `<div class="lbl">Do NOT do this</div><ul class="dont">${a.do_not.map((x) =>
      `<li>${esc(x.action)} ${ev(x.evidence)}<span class="why">${esc(x.why)}</span></li>`).join("")}</ul>` : ""}
    <div class="lbl">Page</div><div class="page"><b>${esc(a.page.who)}</b> ${a.page.why ? `· <span class="muted">${esc(a.page.why)}</span>` : ""}</div>
  </div>`;
}

function renderRecall(t) {
  const mems = t.memories || [];
  return `<div class="recall">
    <div class="recall-h"><h3>Hindsight recall</h3><span class="muted small">${mems.length} memories in ${t.recall_ms} ms ${state.status.memory_backend === "hindsight" ? "(semantic + keyword + graph + temporal, reranked)" : "(local keyword fallback)"}${t.rules?.length ? ` · ${t.rules.length} team rule${t.rules.length > 1 ? "s" : ""} enforced` : ""}</span></div>
    <div class="recall-list">${mems.length ? mems.map((m, i) => `
      <div class="mem" style="animation-delay:${i * 60}ms">
        <div class="mh"><span class="type ${esc(m.type)}">${esc(m.type)}</span><span>${esc(m.metadata?.incident_id || (m.occurred_at || "").slice(0, 10))}</span></div>
        <p>${esc(m.text)}</p><div class="bar"><i style="width:${Math.round((m.score || 0) * 100)}%"></i></div>
      </div>`).join("") : `<div class="muted small">Memory is empty for this alert. Deja is on its own, same as the stateless agent.</div>`}</div>
  </div>`;
}

function renderResolve(inc) {
  const p = inc.prefill || {};
  const team = state.status.team;
  return `<div class="card resolve" id="resolve">
    <div class="card-h"><h3>Resolve &amp; teach Deja</h3><span class="muted small">The postmortem and your verdict are retained into Hindsight</span></div>
    <form id="resolve-form">
      <label class="full">Root cause<textarea name="root_cause" rows="2">${esc(p.root_cause)}</textarea></label>
      <label class="full">Fix that worked<textarea name="fix" rows="2">${esc(p.fix)}</textarea></label>
      <label class="full">Things that did NOT work (one per line)<textarea name="failed" rows="2">${esc(p.failed)}</textarea></label>
      <label>Resolved by<select name="resolved_by">${team.map((t) => `<option ${t === p.resolved_by ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></label>
      <label>Time to resolve (minutes)<input name="ttr_minutes" type="number" min="1" value="${esc(p.ttr_minutes || 30)}" /></label>
      ${inc.triage ? `<label class="full">Was Deja's triage right?
        <div class="verdicts"><button type="button" data-v="correct">Spot on</button><button type="button" data-v="partial">Partly</button><button type="button" data-v="wrong">Wrong</button></div></label>
      <label class="full">Correction for Deja (optional)<input name="notes" placeholder="e.g. the new analytics-jobs-v2 chart bypasses the CI guard" /></label>` : ""}
      <div class="full"><button type="submit">Resolve &amp; retain postmortem</button></div>
    </form></div>`;
}

function renderIncident(inc) {
  const v = $("#incident-view");
  v.className = "";
  const t = inc.triage;
  const r = inc.resolution;
  v.innerHTML = `
    <div class="card alert-card ${inc.status}">
      <div class="alert-head"><span class="sev ${esc(inc.severity)}">${esc(inc.severity)}</span><h2>${esc(inc.title)}</h2>
        <span class="pill ${inc.status}">${esc(inc.status)}</span><span class="muted small">${esc(inc.id)} · ${esc(inc.service)} · ${fmtDate(inc.started_at)} ${fmtTime(inc.started_at)}</span></div>
      <p class="alert-text">${esc(inc.alert)}</p>
      ${inc.logs?.length ? `<pre class="logs">${esc(inc.logs.join("\n"))}</pre>` : ""}
      ${inc.status === "open" ? `<div class="triage-cta"><button id="btn-triage">${t ? "Re-run triage" : "Triage with Deja"}</button>
        <span class="muted small">Recall from Hindsight, then answer with and without memory</span></div>` : ""}
    </div>
    <div id="triage-out">${t ? renderRecall(t) + `<div class="versus">${renderAnswer(t.baseline, "base")}${renderAnswer(t.deja, "deja", { engine: t.engine, ms: t.deja_ms })}</div>` : ""}</div>
    ${r && inc.source === "live" ? renderLearned(inc) : ""}
    ${r && inc.source === "history" ? renderHistory(r) : ""}
    ${inc.status === "open" ? renderResolve(inc) : ""}`;
  bindIncident(inc);
}

function renderLearned(inc) {
  const r = inc.resolution;
  return `<div class="card learned"><div class="card-h"><h3>Retained into memory</h3><span class="muted small">${inc.retain_ms ? `retain took ${inc.retain_ms} ms` : ""}</span></div>
    <dl class="kv"><dt>Root cause</dt><dd>${esc(r.root_cause)}</dd><dt>Fix</dt><dd>${esc(r.fix)}</dd>
    ${r.failed?.length ? `<dt>Didn't work</dt><dd>${r.failed.map(esc).join("<br>")}</dd>` : ""}
    <dt>Resolved by</dt><dd>${esc(r.resolved_by)} in ${esc(r.ttr_minutes)} min</dd>
    ${r.verdict ? `<dt>Deja verdict</dt><dd class="v-${esc(r.verdict)}">${esc(r.verdict)}${r.notes ? ` · “${esc(r.notes)}”` : ""}</dd>` : ""}</dl>
    <p class="muted small">Next time a similar alert fires, this postmortem will be recalled. The <b>${esc(inc.service)}</b> runbook is being rebuilt.</p></div>`;
}

function renderHistory(r) {
  return `<div class="card"><div class="card-h"><h3>Postmortem (history)</h3></div>
    <dl class="kv"><dt>Root cause</dt><dd>${esc(r.root_cause)}</dd><dt>Fix</dt><dd>${esc(r.fix)}</dd>
    ${r.failed?.length ? `<dt>Didn't work</dt><dd>${r.failed.map(esc).join("<br>")}</dd>` : ""}
    <dt>Resolved by</dt><dd>${esc(r.resolved_by)} in ${esc(r.ttr_minutes)} min</dd></dl></div>`;
}

function bindIncident(inc) {
  $("#btn-triage")?.addEventListener("click", () => triage(inc.id));
  const form = $("#resolve-form");
  if (!form) return;
  let verdict = null;
  $$(".verdicts button", form).forEach((b) => b.addEventListener("click", () => {
    verdict = b.dataset.v;
    $$(".verdicts button", form).forEach((x) => x.classList.toggle("on", x === b));
  }));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("button[type=submit]", form);
    btn.disabled = true;
    btn.textContent = "Retaining postmortem into Hindsight…";
    const body = Object.fromEntries(new FormData(form));
    body.ttr_minutes = parseInt(body.ttr_minutes, 10) || null;
    body.verdict = verdict;
    try {
      const done = await api(`/api/incidents/${inc.id}/resolve`, { method: "POST", body });
      toast(`<b>${esc(inc.id)} resolved.</b> Postmortem${verdict ? " + feedback" : ""} retained to memory in ${done.retain_ms} ms.`, "good");
      await selectIncident(inc.id);
      refreshBadges();
    } catch (err) {
      toast(`Resolve failed: ${esc(err.message)}`, "bad");
      btn.disabled = false;
      btn.textContent = "Resolve & retain postmortem";
    }
  });
}

async function triage(id) {
  const out = $("#triage-out");
  const btn = $("#btn-triage");
  if (btn) btn.disabled = true;
  const steps = ["Recalling similar incidents from Hindsight", "Loading team rules (directives)", "Asking the stateless agent", "Reasoning over memories"];
  let k = 0;
  const draw = () => (out.innerHTML = `<div class="card"><ul class="steps">${steps.map((s, i) =>
    `<li class="${i < k ? "done" : i === k ? "cur" : ""}">${s}</li>`).join("")}</ul></div>`);
  draw();
  const timer = setInterval(() => { if (k < steps.length - 1) { k++; draw(); } }, 900);
  try {
    const inc = await api(`/api/incidents/${id}/triage`, { method: "POST" });
    clearInterval(timer);
    renderIncident(inc);
    $("#triage-out").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    clearInterval(timer);
    out.innerHTML = `<div class="card" style="border-color:var(--bad)">Triage failed: ${esc(err.message)}</div>`;
    if (btn) btn.disabled = false;
  }
}

async function selectIncident(id) {
  state.selected = id;
  switchTab("incident");
  const inc = await api(`/api/incidents/${id}`);
  renderIncident(inc);
  await loadIncidents();
}

async function openScenario(body) {
  try {
    const inc = await api("/api/incidents", { method: "POST", body });
    toast(`<b>${esc(inc.id)}</b> opened · ${esc(inc.service)}`);
    await selectIncident(inc.id);
  } catch (err) { toast(`Could not open incident: ${esc(err.message)}`, "bad"); }
}

// ---------------------------------------------------------------- learning tab
async function renderLearning() {
  const s = await refreshBadges();
  if (!s) return;
  const tl = s.timeline;
  const live = tl.filter((x) => x.source === "live");
  const hist = tl.filter((x) => x.source === "history");
  const avg = (a) => (a.length ? Math.round(a.reduce((p, x) => p + (x.ttr || 0), 0) / a.length) : null);
  $("#kpis").innerHTML = [
    [s.memory_count ?? "–", "memories in the Hindsight bank"],
    [avg(hist) != null ? `${avg(hist)}m` : "–", "avg time to resolve, before Deja"],
    [avg(live) != null ? `${avg(live)}m` : "–", "avg time to resolve, with Deja"],
    [s.accuracy != null ? `${s.accuracy}%` : "–", `triage accuracy (${s.graded} graded)`],
  ].map(([v, l]) => `<div class="kpi"><div class="v">${esc(v)}</div><div class="l">${esc(l)}</div></div>`).join("");

  const W = 900, H = 260, pad = { l: 36, r: 10, t: 14, b: 44 };
  const max = Math.max(60, ...tl.map((x) => x.ttr || 0));
  const bw = tl.length ? (W - pad.l - pad.r) / tl.length : 0;
  const y = (v) => H - pad.b - (v / max) * (H - pad.t - pad.b);
  const ticks = [0, Math.round(max / 2), max];
  $("#ttr-chart").innerHTML = tl.length ? `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    ${ticks.map((t) => `<line x1="${pad.l}" x2="${W - pad.r}" y1="${y(t)}" y2="${y(t)}" stroke="var(--line)"/><text x="${pad.l - 6}" y="${y(t) + 3}" text-anchor="end">${t}m</text>`).join("")}
    ${tl.map((x, i) => {
      const h = H - pad.b - y(x.ttr || 0), X = pad.l + i * bw + bw * 0.18, w = bw * 0.64;
      return `<g><title>${esc(x.id)} · ${esc(x.service)} · ${x.ttr} min</title>
        <rect x="${X}" y="${y(x.ttr || 0)}" width="${w}" height="${Math.max(h, 1)}" rx="3" fill="${x.source === "live" ? "var(--accent)" : "var(--line2)"}"/>
        <text x="${X + w / 2}" y="${y(x.ttr || 0) - 4}" text-anchor="middle">${x.ttr}</text>
        <text x="${X + w / 2}" y="${H - pad.b + 14}" text-anchor="middle">${esc(x.id.replace("INC-", ""))}</text>
        <text x="${X + w / 2}" y="${H - pad.b + 28}" text-anchor="middle" style="font-size:9px">${esc(x.service.split("-")[0])}</text></g>`;
    }).join("")}</svg>` : `<p class="muted">Resolve incidents (or load history) to see the curve.</p>`;

  const graded = tl.filter((x) => x.verdict);
  $("#graded").innerHTML = graded.length ? graded.map((x) => `<div class="graded-row"><b>${esc(x.id)}</b><span>${esc(x.service)}</span>
    <span class="muted">Deja confidence ${x.confidence ?? "–"}% · resolved in ${x.ttr} min</span><span class="v-${esc(x.verdict)}">${esc(x.verdict)}</span></div>`).join("")
    : `<p class="muted small">No graded triages yet. Grade Deja when you resolve an incident.</p>`;
}

// ---------------------------------------------------------------- runbooks / ask / rules / memory
function md(src) {
  const inline = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/(^|\W)_([^_]+)_(?=\W|$)/g, "$1<i>$2</i>");
  const lines = String(src || "").split("\n");
  let html = "", list = null;
  const close = () => { if (list) { html += `</${list}>`; list = null; } };
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    let m;
    if ((m = l.match(/^(#{1,4})\s+(.*)/))) { close(); const n = Math.min(m[1].length, 3); html += `<h${n}>${inline(m[2])}</h${n}>`; }
    else if ((m = l.match(/^\s*[-*]\s+(.*)/))) { if (list !== "ul") { close(); html += "<ul>"; list = "ul"; } html += `<li>${inline(m[1])}</li>`; }
    else if ((m = l.match(/^\s*\d+[.)]\s+(.*)/))) { if (list !== "ol") { close(); html += "<ol>"; list = "ol"; } html += `<li>${inline(m[1])}</li>`; }
    else if (/^\|.*\|$/.test(l.trim())) {
      close();
      const rows = [];
      while (i < lines.length && /^\|.*\|$/.test(lines[i].trim())) { if (!/^\|[\s:|-]+\|$/.test(lines[i].trim())) rows.push(lines[i]); i++; }
      i--;
      html += "<table>" + rows.map((r, ri) => "<tr>" + r.trim().slice(1, -1).split("|").map((c) => `<${ri ? "td" : "th"}>${inline(c.trim())}</${ri ? "td" : "th"}>`).join("") + "</tr>").join("") + "</table>";
    }
    else if (l.trim()) { close(); html += `<p>${inline(l)}</p>`; }
    else close();
  }
  close();
  return html;
}

async function renderRunbooks() {
  const sel = $("#rb-service");
  const services = [...new Set(state.incidents.map((i) => i.service))].sort();
  if (!services.length) { $("#runbook").innerHTML = `<p class="muted">No services in memory yet.</p>`; sel.innerHTML = ""; return; }
  const cur = sel.value && services.includes(sel.value) ? sel.value : services.includes("checkout-api") ? "checkout-api" : services[0];
  sel.innerHTML = services.map((s) => `<option ${s === cur ? "selected" : ""}>${esc(s)}</option>`).join("");
  $("#runbook").innerHTML = `<div class="thinking"><span class="spinner"></span>Reading the ${esc(cur)} mental model from Hindsight…</div>`;
  try {
    const rb = await api(`/api/runbook/${encodeURIComponent(cur)}`);
    $("#runbook").innerHTML = md(rb.content) + `<p class="src">source: ${esc(rb.source)}</p>`;
  } catch (err) { $("#runbook").innerHTML = `<p class="v-wrong">${esc(err.message)}</p>`; }
}

async function ask(q) {
  const log = $("#chat-log");
  log.insertAdjacentHTML("beforeend", `<div class="msg user">${esc(q)}</div><div class="msg bot pending"><span class="spinner"></span></div>`);
  log.scrollTop = log.scrollHeight;
  try {
    const r = await api("/api/ask", { method: "POST", body: { question: q } });
    $(".msg.pending", log).outerHTML = `<div class="msg bot md">${md(r.answer)}<span class="src">${esc(r.engine)} · ${r.based_on.length} memories</span></div>`;
  } catch (err) { $(".msg.pending", log).outerHTML = `<div class="msg bot v-wrong">${esc(err.message)}</div>`; }
  log.scrollTop = log.scrollHeight;
}

async function renderRules(list) {
  const rules = list || (await api("/api/rules").catch(() => []));
  $("#rules").innerHTML = rules.length ? rules.map((r) => `<li><b>${esc(r.name)}</b><span class="muted">${esc(r.content)}</span></li>`).join("")
    : `<li class="muted" style="border-left-color:var(--line)">No rules yet. Rules you add here override anything Deja infers from memory.</li>`;
}

async function renderMemories() {
  $("#memories").innerHTML = `<li><span class="spinner"></span></li>`;
  try {
    const mems = await api("/api/memories");
    $("#memories").innerHTML = mems.length ? mems.map((m) => `<li><span class="type ${esc(m.type)}">${esc(m.type)}</span><span style="flex:1">${esc(m.text)}</span><span class="d">${esc((m.occurred_at || "").slice(0, 10))}</span></li>`).join("")
      : `<li class="muted">Memory is empty. Load history or resolve an incident. (Hindsight extracts facts asynchronously, so give it a few seconds after seeding.)</li>`;
  } catch (err) { $("#memories").innerHTML = `<li class="v-wrong">${esc(err.message)}</li>`; }
}

// ---------------------------------------------------------------- wiring
function switchTab(name) {
  $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === `tab-${name}`));
  ({ learning: renderLearning, runbooks: renderRunbooks, rules: () => renderRules(), memory: renderMemories }[name] || (() => {}))();
}

document.addEventListener("click", (e) => {
  const sc = e.target.closest(".scenario");
  if (sc) return openScenario({ scenario: sc.dataset.key });
  const li = e.target.closest(".incidents li[data-id]");
  if (li) return selectIncident(li.dataset.id);
  const tab = e.target.closest("#tabs button");
  if (tab) return switchTab(tab.dataset.tab);
  const chip = e.target.closest(".chip.q");
  if (chip) return ask(chip.textContent);
});

$("#custom-form").addEventListener("submit", (e) => {
  e.preventDefault();
  openScenario(Object.fromEntries(new FormData(e.target)));
  e.target.reset();
});
$("#ask-form").addEventListener("submit", (e) => { e.preventDefault(); const q = e.target.q.value.trim(); if (q) { e.target.reset(); ask(q); } });
$("#rule-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const rules = await api("/api/rules", { method: "POST", body: Object.fromEntries(new FormData(e.target)) });
    e.target.reset(); renderRules(rules); toast("Rule stored as a Hindsight directive. It is enforced from the next triage on.", "good");
  } catch (err) { toast(esc(err.message), "bad"); }
});
$("#rb-service").addEventListener("change", renderRunbooks);
$("#mem-refresh").addEventListener("click", renderMemories);
$("#btn-seed").addEventListener("click", async (e) => {
  e.target.disabled = true;
  try {
    const r = await api("/api/demo/seed", { method: "POST" });
    toast(`<b>${r.retained} postmortems</b> sent to Hindsight. Fact extraction runs in the background, so the memory count will climb.`, "good");
    await loadIncidents(); refreshBadges();
    let n = 0; const poll = setInterval(() => { refreshBadges(); if (++n > 20) clearInterval(poll); }, 4000);
  } catch (err) { toast(esc(err.message), "bad"); }
  e.target.disabled = false;
});
$("#btn-reset").addEventListener("click", async () => {
  if (!confirm("Delete the memory bank and all incidents?")) return;
  try {
    await api("/api/demo/reset", { method: "POST" });
    state.selected = null;
    $("#incident-view").className = "empty-state";
    location.reload();
  } catch (err) { toast(esc(err.message), "bad"); }
});

(async () => {
  try { await loadStatus(); await loadIncidents(); }
  catch (err) { toast(`Backend unreachable: ${esc(err.message)}`, "bad"); }
})();
