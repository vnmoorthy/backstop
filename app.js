/* BACKSTOP dashboard — state, WebSocket, and the canvas FX.
   Consumes the SPEC §5 WS event stream. If the socket is down, the Run button
   plays a canned replay so the demo always survives. No build step. */

(() => {
  "use strict";

  const API = location.origin.startsWith("http") ? location.origin : "http://localhost:8000";
  const WS_URL = (location.protocol === "https:" ? "wss://" : "ws://") +
    (location.host || "localhost:8000") + "/stream";

  // ---- element refs ----
  const $ = (id) => document.getElementById(id);
  const els = {
    conn: $("conn-pill"), connLabel: $("conn-label"), runBtn: $("run-btn"),
    pavo: $("pavo-total"), frontier: $("frontier-total"), ratio: $("ratio-big"),
    segLocal: document.querySelector(".seg-local"), segMid: document.querySelector(".seg-mid"),
    segFront: document.querySelector(".seg-front"),
    spec: $("spec-body"), reconcileCard: $("reconcile-card"), reconcileBody: $("reconcile-body"),
    letterCard: $("letter-card"), letterBody: $("letter-body"),
    composeCard: $("compose-card"), composeTag: $("compose-tag"), composeBody: $("compose-body"),
    stage: $("stage"), canvas: $("fx"), core: $("core"), cells: $("cells"), stageHint: $("stage-hint"),
    sponsorRow: $("sponsor-row"), toast: $("toast"),
    picker: $("sample-picker"),
    outcomeCard: $("outcome-card"), ocRecovered: $("oc-recovered"),
    ocRatio: $("oc-ratio"), ocAgents: $("oc-agents"), ocFoot: $("oc-foot"),
  };

  const SPONSORS = ["pavo", "moss", "livekit", "truefoundry", "unsiloed", "aws", "minimax", "qwen"];
  const SPONSOR_LABEL = { pavo: "PAVO", moss: "Moss", livekit: "LiveKit", truefoundry: "TrueFoundry",
    unsiloed: "Unsiloed", aws: "AWS", minimax: "MiniMax", qwen: "Qwen" };

  const state = { cells: {}, running: false, billed: 0, agentCount: 0, lastRatio: 1,
    currentAppeal: null, gotEvent: false };

  // ---- load the seeded denials into the picker ----
  async function loadSamples() {
    try {
      const samples = await (await fetch(API + "/samples")).json();
      els.picker.innerHTML = samples.map((s) =>
        `<option value="${esc(s.denial_id)}">${esc(s.payer)} · ${esc(s.denial_code)} · $${Number(s.billed_amount).toLocaleString()}</option>`
      ).join("");
    } catch (_) {
      els.picker.innerHTML = `<option value="">Aetna · CO-197 (default)</option>`;
    }
  }

  // ===================== canvas particle system =====================
  const ctx = els.canvas.getContext("2d");
  let W = 0, H = 0, DPR = Math.min(window.devicePixelRatio || 1, 2);
  const particles = [];

  function resize() {
    const r = els.stage.getBoundingClientRect();
    W = r.width; H = r.height;
    els.canvas.width = W * DPR; els.canvas.height = H * DPR;
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    relayoutCells();
  }
  window.addEventListener("resize", resize);

  function coreCenter() { return { x: W / 2, y: H / 2 }; }

  const TIER_COLOR = { frontier: "#D8A23A", mid_reason: "#4F7CFF", local_fast: "#35B97E" };

  function fireParticle(agentId, tier) {
    const cell = state.cells[agentId];
    if (!cell) return;
    const from = coreCenter();
    const to = cell.center;
    particles.push({
      fromX: from.x, fromY: from.y, toX: to.x, toY: to.y, x: from.x, y: from.y,
      t: 0, speed: tier === "frontier" ? 0.045 : 0.06,
      color: TIER_COLOR[tier] || TIER_COLOR.local_fast,
      size: tier === "frontier" ? 3.2 : 2.4,
    });
  }

  // Draw the swarm as a topology: thin connector lines from the PAVO core to
  // each specialist cell. Active calls light their line with the accent.
  function drawConnectors() {
    const c = coreCenter();
    ctx.lineWidth = 1;
    Object.keys(state.cells).forEach((id) => {
      const cell = state.cells[id];
      if (!cell || !cell.center) return;
      const active = cell.el && cell.el.classList.contains("active");
      ctx.strokeStyle = active ? "rgba(79,124,255,0.22)" : "rgba(46,55,68,0.55)";
      ctx.beginPath(); ctx.moveTo(c.x, c.y); ctx.lineTo(cell.center.x, cell.center.y); ctx.stroke();
    });
  }

  function tick() {
    ctx.clearRect(0, 0, W, H);
    drawConnectors();
    for (let i = particles.length - 1; i >= 0; i--) {
      const p = particles[i];
      p.t += p.speed;
      const e = p.t < 1 ? 1 - Math.pow(1 - p.t, 2) : 1; // ease-out
      p.x = p.fromX + (p.toX - p.fromX) * e;
      p.y = p.fromY + (p.toY - p.fromY) * e;
      // faint trailing segment along the connector
      ctx.strokeStyle = p.color + "40"; ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(p.fromX + (p.toX - p.fromX) * Math.max(0, e - 0.10), p.fromY + (p.toY - p.fromY) * Math.max(0, e - 0.10));
      ctx.lineTo(p.x, p.y); ctx.stroke();
      // small head, minimal glow (no neon)
      ctx.shadowColor = p.color; ctx.shadowBlur = 6;
      ctx.fillStyle = p.color; ctx.beginPath();
      ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2); ctx.fill();
      ctx.shadowBlur = 0;
      if (p.t >= 1) { burst(p); particles.splice(i, 1); }
    }
    requestAnimationFrame(tick);
  }
  function burst(p) {
    // a quiet ring on arrival, not a flare
    ctx.save(); ctx.globalAlpha = 0.5; ctx.strokeStyle = p.color; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.arc(p.toX, p.toY, p.size * 2.2, 0, Math.PI * 2); ctx.stroke();
    ctx.restore();
  }
  requestAnimationFrame(tick);

  // ===================== cells =====================
  function relayoutCells() {
    const ids = Object.keys(state.cells);
    const n = ids.length; if (!n) return;
    const cx0 = W / 2, cy0 = H / 2;
    // elliptical burst: use the stage's full width horizontally so cells fan out
    // into a clean triangle/ring around the singularity instead of clustering.
    const rx = Math.min(W * 0.36, 340);
    const ry = H * 0.34;
    const start = -Math.PI / 2 + (n === 2 ? Math.PI / 6 : 0); // top, then even
    ids.forEach((id, i) => {
      const ang = start + (i / n) * Math.PI * 2;
      const cx = Math.max(120, Math.min(W - 120, cx0 + Math.cos(ang) * rx));
      const cy = Math.max(70, Math.min(H - 70, cy0 + Math.sin(ang) * ry));
      const cell = state.cells[id];
      cell.center = { x: cx, y: cy };
      cell.el.style.left = cx + "px";
      cell.el.style.top = cy + "px";
    });
  }

  function spawnCells(specialists) {
    els.cells.innerHTML = "";
    state.cells = {};
    state.agentCount = specialists.length;
    els.stageHint.style.display = "none";
    specialists.forEach((s) => {
      const el = document.createElement("div");
      el.className = "cell";
      el.innerHTML =
        `<div class="cell-head"><span class="cell-name">${esc(s.label || s.id)}</span>` +
        `<span class="cell-tier">idle</span></div>` +
        `<div class="cell-line">dialing ${esc(s.payer || "")}…</div>`;
      els.cells.appendChild(el);
      state.cells[s.id] = { el, line: el.querySelector(".cell-line"),
        tier: el.querySelector(".cell-tier"), center: { x: W / 2, y: H / 2 } };
      requestAnimationFrame(() => el.classList.add("in"));
    });
    relayoutCells();
  }

  // ===================== event handlers =====================
  const handlers = {
    "sponsor.mode": (e) => renderSponsor(e.name, e.mode),
    "intake.parsed": (e) => renderSpec(e),
    "swarm.spawn": (e) => spawnCells(e.specialists || []),
    "call.turn": (e) => {
      const c = state.cells[e.agent_id]; if (!c) return;
      const t = e.turn || {};
      const who = t.speaker === "rep" ? "rep" : (t.speaker === "ivr" ? "ivr" : "agent");
      c.line.innerHTML = `<span class="${who === "rep" ? "rep" : ""}">${esc((t.text || "").slice(0, 96))}</span>`;
    },
    "pavo.route": (e) => {
      const c = state.cells[e.agent_id]; if (!c) return;
      c.tier.textContent = e.tier; c.tier.className = "cell-tier " + e.tier;
      c.el.classList.add("active");
      if (e.tier === "frontier") { c.el.classList.add("flare"); setTimeout(() => c.el.classList.remove("flare"), 900); }
      fireParticle(e.agent_id, e.tier);
    },
    "cost.tick": (e) => renderCost(e),
    "moss.hit": (e) => renderMoss(e),
    "reconcile.found": (e) => renderReconcile(e),
    "agent.compose": (e) => renderCompose(e),
    "letter.ready": (e) => renderLetter(e),
    "appeal.done": (e) => { setRunning(false); renderOutcome(e); toast("Appeal worked. Awaiting nurse signature."); },
    "appeal.error": (e) => { setRunning(false); toast("Pipeline error: " + (e.error || "")); },
  };

  function dispatch(ev) {
    if (ev.appeal_id && ev.appeal_id === state.currentAppeal) state.gotEvent = true;
    const h = handlers[ev.type]; if (h) try { h(ev); } catch (_) {}
  }

  function renderSpec(e) {
    const d = (e.denial) || {};
    state.billed = Number(d.billed_amount) || 0;
    const rows = [
      ["Payer", d.payer], ["Plan", d.plan], ["Denial code", d.denial_code, true],
      ["Claim", d.claim_id], ["CPT", (d.cpt || []).join(", ")],
      ["Billed", d.billed_amount != null ? "$" + Number(d.billed_amount).toLocaleString() : ""],
      ["DOS", d.date_of_service], ["Specialists", (e.required_specialists || []).length],
      ["Filing deadline", e.sol_deadline],
    ];
    els.spec.innerHTML = `<div class="kv">` + rows.filter(r => r[1] != null && r[1] !== "")
      .map(([k, v, code]) => `<div class="kv-row"><span class="kv-k">${esc(k)}</span><span class="kv-v ${code ? "code" : ""}">${esc(String(v))}</span></div>`).join("") + `</div>`;
  }

  function renderCost(e) {
    els.pavo.textContent = "$" + Number(e.pavo_total || 0).toFixed(4);
    els.frontier.textContent = "$" + Number(e.frontier_total || 0).toFixed(4);
    state.lastRatio = e.ratio || 1;
    els.ratio.textContent = (e.ratio || 1).toFixed(1) + "x";
    els.ratio.classList.remove("flash"); void els.ratio.offsetWidth; els.ratio.classList.add("flash");
    const tc = e.tier_counts || {}; const total = Math.max(1, (tc.local_fast || 0) + (tc.mid_reason || 0) + (tc.frontier || 0));
    els.segLocal.style.width = (100 * (tc.local_fast || 0) / total) + "%";
    els.segMid.style.width = (100 * (tc.mid_reason || 0) / total) + "%";
    els.segFront.style.width = (100 * (tc.frontier || 0) / total) + "%";
  }

  function renderMoss(e) {
    const c = state.cells[e.agent_id]; if (!c) return;
    let card = c.el.querySelector(".moss-card");
    if (!card) { card = document.createElement("div"); card.className = "moss-card"; c.el.appendChild(card); }
    card.innerHTML = `<div class="mh">⚡ Moss rebuttal · ${esc(e.source_id || "")}</div>` +
      `<div class="mw">"${esc(e.magic_words || "")}"</div>` +
      `<div>win rate <span class="wr">${Math.round((e.win_rate || 0) * 100)}%</span></div>`;
    requestAnimationFrame(() => card.classList.add("in"));
  }

  function renderReconcile(e) {
    els.reconcileCard.hidden = false;
    els.reconcileBody.innerHTML =
      `<div class="ctr-block claim"><div class="ctr-label">Payer claim · turn ${esc(String(e.rep_turn_id))}</div><div class="ctr-q">"${esc(e.claim || "")}"</div></div>` +
      `<div class="ctr-block evi"><div class="ctr-label">Contradicted by record · turn ${esc(String(e.evidence_turn_id))}</div><div class="ctr-q">"${esc(e.evidence || "")}"</div></div>`;
  }

  function renderCompose(e) {
    els.composeCard.hidden = false;
    const real = e.mode === "real";
    els.composeTag.textContent = "MiniMax · " + (real ? "real" : "sim") + (e.model ? " · " + e.model : "");
    els.composeTag.style.color = real ? "var(--ok)" : "";
    els.composeBody.innerHTML = `<div class="letter-desc">${esc(e.text || "")}</div>`;
  }

  function renderLetter(e) {
    els.letterCard.hidden = false;
    const url = e.pdf_url ? (API + e.pdf_url) : "";
    const safeUrl = url ? esc(encodeURI(url)) : "";
    els.letterBody.innerHTML =
      `<div class="letter-meta">${esc(e.appeal_id || "")} · citations: ${esc((e.citations || []).join(", "))}</div>` +
      `<div class="letter-desc">Verbatim appeal letter drafted from the call record. Requires licensed appeals-nurse signature before filing.</div>` +
      `<div class="letter-actions">` +
      (safeUrl ? `<a class="btn-link" href="${safeUrl}" target="_blank" rel="noopener noreferrer">Open PDF</a>` : "") +
      `<button class="btn-sign" id="sign-btn">Nurse — sign &amp; file</button></div>`;
    const sb = $("sign-btn");
    if (sb) sb.onclick = () => {
      sb.textContent = "✓ Signed & filed"; sb.classList.add("signed");
      if (els.ocFoot) { els.ocFoot.textContent = "filed · nurse-signed"; els.ocFoot.classList.add("signed"); }
      toast("Appeal filed (nurse-signed). Deploying the AI workforce…");
      deployWorkforce();
    };
  }

  function renderOutcome(e) {
    const ratio = (e.cost && e.cost.ratio) || state.lastRatio || 1;
    els.ocRecovered.textContent = "$" + Number(state.billed || 0).toLocaleString();
    els.ocRatio.textContent = ratio.toFixed(1) + "x";
    els.ocAgents.textContent = state.agentCount || 0;
    els.outcomeCard.hidden = false;
  }

  function renderSponsor(name, mode) {
    let el = document.querySelector(`.sponsor[data-n="${name}"]`);
    if (!el) {
      el = document.createElement("div"); el.className = "sponsor"; el.dataset.n = name;
      el.innerHTML = `<span class="sdot"></span><span class="sname">${esc(SPONSOR_LABEL[name] || name)}</span><span class="smode"></span>`;
      els.sponsorRow.appendChild(el);
    }
    el.classList.remove("real", "sim"); el.classList.add(mode);
    el.querySelector(".smode").textContent = mode;
  }
  // seed greyed badges so the row is full before events arrive
  SPONSORS.forEach((n) => renderSponsor(n, "sim"));

  // ===================== run + WS =====================
  async function runAppeal() {
    if (state.running) return;
    setRunning(true); resetUI();
    state.currentAppeal = null; state.gotEvent = false;
    try {
      const id = els.picker && els.picker.value ? ("?denial_id=" + encodeURIComponent(els.picker.value)) : "";
      const r = await fetch(API + "/appeals" + id, { method: "POST" });
      if (!r.ok) throw new Error("http " + r.status);
      const data = await r.json();
      state.currentAppeal = data.appeal_id;
      // WS-miss watchdog: if the live stream delivered nothing (dropped socket,
      // flaky wifi), pull the recorded snapshot and replay it so the stage is
      // never blank. No-op when the WS worked.
      setTimeout(() => snapshotFallback(data.appeal_id), 3500);
    } catch (err) { toast("Server unreachable — playing replay."); replay(); }
  }

  async function snapshotFallback(appealId) {
    if (state.gotEvent || appealId !== state.currentAppeal) return; // live stream delivered
    try {
      const a = await (await fetch(API + "/appeals/" + appealId)).json();
      if (!a.events || !a.events.length) return;
      resetUI();
      a.events.forEach((ev, i) => setTimeout(() => dispatch(ev), Math.min(i * 25, 1400)));
      toast("Recovered via snapshot.");
    } catch (_) {}
  }
  els.runBtn.addEventListener("click", runAppeal);

  function setRunning(v) { state.running = v; els.runBtn.disabled = v; }
  function resetUI() {
    els.reconcileCard.hidden = true; els.letterCard.hidden = true; els.outcomeCard.hidden = true;
    if (els.composeCard) els.composeCard.hidden = true;
    els.cells.innerHTML = ""; state.cells = {}; els.stageHint.style.display = "";
    renderCost({ pavo_total: 0, frontier_total: 0, ratio: 1, tier_counts: {} });
  }
  function toast(msg) { els.toast.textContent = msg; els.toast.hidden = false; clearTimeout(toast._t); toast._t = setTimeout(() => els.toast.hidden = true, 3200); }

  // ===================== AI workforce deploy (on nurse sign-off) =====================
  const WF_PHASES = ["Intake", "Triage", "Prep", "Call", "Reason", "Draft", "File", "Recover", "Learn"];
  const WORKFORCE = [
    { n: "Intake Agent", s: "Unsiloed", p: "Intake" }, { n: "Document Agent", s: "Unsiloed", p: "Intake" },
    { n: "Triage Agent", s: "PAVO", p: "Triage" }, { n: "Disposition Agent", s: "MiniMax", p: "Triage" },
    { n: "PAVO Router", s: "PAVO", p: "Prep" }, { n: "Eligibility Agent", s: "", p: "Prep" }, { n: "Prior-Auth Agent", s: "Moss", p: "Prep" },
    { n: "Records Agent", s: "Moss", p: "Prep" }, { n: "Coding Agent", s: "MiniMax", p: "Prep" }, { n: "Supervisor Agent", s: "AWS", p: "Prep" },
    { n: "Provider-Line Caller", s: "LiveKit", p: "Call" }, { n: "Billing-Office Caller", s: "LiveKit", p: "Call" }, { n: "Records-Desk Caller", s: "LiveKit", p: "Call" },
    { n: "IVR Navigator", s: "MiniMax", p: "Call" }, { n: "Voice Agent", s: "Qwen", p: "Call" },
    { n: "Rebuttal Retrieval", s: "Moss", p: "Reason" }, { n: "Reconciler Agent", s: "", p: "Reason" },
    { n: "Letter Writer", s: "MiniMax", p: "Draft" }, { n: "Compliance Agent", s: "TrueFoundry", p: "Draft" },
    { n: "Filing Agent", s: "", p: "File" }, { n: "Status Tracker", s: "", p: "Recover" }, { n: "Recovery & Billing", s: "", p: "Recover" }, { n: "Learning Agent", s: "PAVO", p: "Learn" },
  ];
  // Always-visible roster on the dashboard so every agent is on the live site.
  function renderWorkforce() {
    const body = document.getElementById("workforce-body");
    if (!body) return;
    body.innerHTML =
      '<div style="display:flex;flex-wrap:wrap;gap:10px">' +
      WF_PHASES.map((ph) => {
        const ags = WORKFORCE.filter((a) => a.p === ph);
        return '<div style="flex:1;min-width:158px;border:1px solid var(--border);border-radius:10px;background:var(--surface-2);padding:11px 12px">' +
          '<div style="font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--text-faint);font-weight:700;margin-bottom:9px">' + ph + '</div>' +
          ags.map((a) =>
            '<div style="display:flex;align-items:center;gap:7px;padding:3px 0">' +
            '<span style="width:6px;height:6px;border-radius:50%;background:var(--ok);flex:none"></span>' +
            '<span style="font-size:11px;color:var(--text);flex:1;line-height:1.3">' + esc(a.n) + '</span>' +
            (a.s ? '<span style="font-size:9px;color:var(--text-faint);font-family:ui-monospace,monospace">' + esc(a.s) + '</span>' : "") +
            '</div>').join("") +
          '</div>';
      }).join("") + '</div>';
  }
  function deployWorkforce() {
    if (document.getElementById("wf-ov")) return;
    const ov = document.createElement("div");
    ov.id = "wf-ov";
    ov.style.cssText = "position:fixed;inset:0;z-index:200;background:rgba(4,5,8,.88);backdrop-filter:blur(7px);display:flex;align-items:center;justify-content:center;padding:24px;animation:wffade .2s ease";
    ov.innerHTML =
      '<div style="width:min(1000px,95vw);max-height:92vh;overflow:auto;background:#131720;border:1px solid #2E3744;border-radius:16px;padding:22px 24px;box-shadow:0 24px 70px rgba(0,0,0,.65)">' +
      '<div style="display:flex;justify-content:space-between;align-items:center"><div style="font-size:18px;font-weight:750;color:#E6E8EB;letter-spacing:-.01em">✓ Nurse signed — deploying the AI workforce</div>' +
      '<button id="wf-x" style="background:#1E242F;border:1px solid #232A35;color:#8B929C;border-radius:8px;padding:6px 11px;cursor:pointer;font-size:12px">close</button></div>' +
      '<div style="font-size:12px;color:#8B929C;margin:6px 0 16px"><b id="wf-n" style="color:#35B97E">0</b>/23 specialized agents initiated to recover this claim end to end</div>' +
      '<div id="wf-grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(214px,1fr));gap:8px"></div></div>';
    document.body.appendChild(ov);
    document.getElementById("wf-x").onclick = () => ov.remove();
    const grid = document.getElementById("wf-grid"), counter = document.getElementById("wf-n");
    let lit = 0;
    WORKFORCE.forEach((a, i) => {
      const name = a.n, sponsor = a.s;
      const c = document.createElement("div");
      c.style.cssText = "display:flex;align-items:center;gap:9px;padding:10px 12px;border:1px solid #232A35;border-radius:9px;background:#171C26;opacity:.22;transform:translateY(5px);transition:opacity .18s ease,transform .18s ease,border-color .18s ease";
      c.innerHTML = '<span class="wfd" style="width:8px;height:8px;border-radius:50%;background:#5B636E;flex:none;transition:all .18s ease"></span>' +
        '<span style="font-size:12px;color:#E6E8EB;font-weight:550;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">' + esc(name) + '</span>' +
        (sponsor ? '<span style="font-size:9px;color:#8B929C;font-family:ui-monospace,monospace">' + esc(sponsor) + '</span>' : "");
      grid.appendChild(c);
      setTimeout(() => {
        c.style.opacity = "1"; c.style.transform = "translateY(0)"; c.style.borderColor = "#2E3744";
        const d = c.querySelector(".wfd"); d.style.background = "#35B97E"; d.style.boxShadow = "0 0 0 3px rgba(53,185,126,.20)";
        counter.textContent = String(++lit);
      }, 90 + i * 42);
    });
  }
  function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

  let ws;
  function connect() {
    try { ws = new WebSocket(WS_URL); } catch (_) { setConn(false); return; }
    ws.onopen = () => { setConn(true); };
    ws.onclose = () => { setConn(false); setTimeout(connect, 1500); };
    ws.onerror = () => { try { ws.close(); } catch (_) {} };
    ws.onmessage = (m) => { try { dispatch(JSON.parse(m.data)); } catch (_) {} };
  }
  function setConn(up) {
    els.conn.className = "conn-pill " + (up ? "conn-up" : "conn-down");
    els.connLabel.textContent = up ? "live" : "offline (replay ready)";
  }

  // ===================== canned replay (demo survival) =====================
  function replay() {
    const A = "AP-replay";
    const seq = [];
    SPONSORS.forEach((n) => seq.push([60, { type: "sponsor.mode", name: n, mode: n === "pavo" ? "real" : "sim" }]));
    seq.push([200, { type: "intake.parsed", denial: { payer: "Aetna", plan: "Aetna Choice POS II", denial_code: "CO-197", claim_id: "CLM-55-7741", cpt: ["99285"], billed_amount: 2480, date_of_service: "2026-03-14" }, required_specialists: ["provider_line", "prior_auth_desk", "records_desk"], sol_deadline: "2026-09-10" }]);
    seq.push([250, { type: "swarm.spawn", specialists: [
      { id: "provider_line", kind: "provider_line", label: "Aetna provider services", payer: "Aetna" },
      { id: "prior_auth_desk", kind: "prior_auth_desk", label: "Aetna prior-auth desk", payer: "Aetna" },
      { id: "records_desk", kind: "records_desk", label: "Records desk", payer: "Aetna" }] }]);
    const lines = [
      ["provider_line", "ivr", "press 3 for claims", "local_fast", false],
      ["records_desk", "ivr", "please hold", "local_fast", false],
      ["prior_auth_desk", "ivr", "please hold", "local_fast", false],
      ["provider_line", "ivr", "still holding…", "local_fast", false],
      ["records_desk", "rep", "The chart shows pre-auth A4471 on file.", "frontier", true],
      ["prior_auth_desk", "rep", "Authorization A4471 was issued for that DOS.", "frontier", true],
      ["provider_line", "rep", "That claim was denied CO-197 — no prior auth on file.", "frontier", true],
      ["provider_line", "rep", "By the rendering NPI I do see auth A4471. Reprocessing.", "frontier", true],
    ];
    const tc = { local_fast: 9, mid_reason: 4, frontier: 0 }; let pavo = 0.0006, front = 0.02;
    let i = 0;
    lines.forEach((l) => {
      seq.push([260 + i * 70, { type: "call.turn", agent_id: l[0], turn: { speaker: l[1], text: l[2] } }]);
      seq.push([280 + i * 70, { type: "pavo.route", agent_id: l[0], tier: l[3], is_denial_reason: l[4] }]);
      if (l[3] === "frontier") { tc.frontier++; pavo += 0.005; } else { pavo += 0.00005; }
      front += 0.005;
      seq.push([282 + i * 70, { type: "cost.tick", pavo_total: pavo, frontier_total: front, ratio: front / pavo, tier_counts: { ...tc } }]);
      if (l[4]) seq.push([300 + i * 70, { type: "moss.hit", agent_id: l[0], source_id: "RB-AETNA-CO197", magic_words: "Run the auth-on-file lookup by the RENDERING NPI, not the billing NPI.", win_rate: 0.73 }]);
      i++;
    });
    seq.push([260 + i * 70 + 200, { type: "reconcile.found", claim: "there is no prior authorization on file", evidence: "authorization A4471 was issued for that date of service", rep_turn_id: 10, evidence_turn_id: 3 }]);
    seq.push([260 + i * 70 + 500, { type: "letter.ready", appeal_id: A, citations: ["RB-AETNA-CO197", "call-record"], pdf_url: "" }]);
    seq.push([260 + i * 70 + 700, { type: "appeal.done", cost: {} }]);
    seq.forEach(([delay, ev]) => setTimeout(() => dispatch({ appeal_id: A, ...ev }), delay));
  }

  // boot
  loadSamples();
  renderWorkforce();
  resize();
  connect();
})();
