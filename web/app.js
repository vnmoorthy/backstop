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
    stage: $("stage"), canvas: $("fx"), core: $("core"), cells: $("cells"), stageHint: $("stage-hint"),
    sponsorRow: $("sponsor-row"), toast: $("toast"),
  };

  const SPONSORS = ["pavo", "moss", "livekit", "truefoundry", "unsiloed", "aws", "minimax", "qwen"];
  const SPONSOR_LABEL = { pavo: "PAVO", moss: "Moss", livekit: "LiveKit", truefoundry: "TrueFoundry",
    unsiloed: "Unsiloed", aws: "AWS", minimax: "MiniMax", qwen: "Qwen" };

  const state = { cells: {}, running: false };

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

  function fireParticle(agentId, tier) {
    const cell = state.cells[agentId];
    if (!cell) return;
    const from = coreCenter();
    const to = cell.center;
    const frontier = tier === "frontier";
    particles.push({
      x: from.x, y: from.y, fromX: from.x, fromY: from.y, toX: to.x, toY: to.y,
      t: 0, speed: frontier ? 0.035 : 0.05,
      color: frontier ? "#ff2d9b" : (tier === "mid_reason" ? "#5b8cff" : "#3ef2c4"),
      size: frontier ? 4.2 : 2.6, glow: frontier ? 26 : 12,
    });
  }

  function tick() {
    ctx.clearRect(0, 0, W, H);
    for (let i = particles.length - 1; i >= 0; i--) {
      const p = particles[i];
      p.t += p.speed;
      const e = p.t < 1 ? 1 - Math.pow(1 - p.t, 2) : 1; // ease-out
      p.x = p.fromX + (p.toX - p.fromX) * e;
      p.y = p.fromY + (p.toY - p.fromY) * e;
      // trail
      ctx.strokeStyle = p.color + "55"; ctx.lineWidth = p.size * 0.7;
      ctx.beginPath();
      ctx.moveTo(p.fromX + (p.toX - p.fromX) * Math.max(0, e - 0.12), p.fromY + (p.toY - p.fromY) * Math.max(0, e - 0.12));
      ctx.lineTo(p.x, p.y); ctx.stroke();
      // head
      ctx.shadowColor = p.color; ctx.shadowBlur = p.glow;
      ctx.fillStyle = p.color; ctx.beginPath();
      ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2); ctx.fill();
      ctx.shadowBlur = 0;
      if (p.t >= 1) { burst(p); particles.splice(i, 1); }
    }
    requestAnimationFrame(tick);
  }
  function burst(p) {
    ctx.save(); ctx.globalAlpha = 0.8; ctx.shadowColor = p.color; ctx.shadowBlur = p.glow;
    ctx.fillStyle = p.color; ctx.beginPath(); ctx.arc(p.toX, p.toY, p.size * 1.8, 0, Math.PI * 2); ctx.fill();
    ctx.restore();
  }
  requestAnimationFrame(tick);

  // ===================== cells =====================
  function relayoutCells() {
    const ids = Object.keys(state.cells);
    const n = ids.length; if (!n) return;
    const radius = Math.min(W, H) * 0.33;
    ids.forEach((id, i) => {
      // spread cells in a ring, starting at the top, biased to the right side
      const ang = (-Math.PI / 2) + (i / n) * Math.PI * 2;
      const cx = W / 2 + Math.cos(ang) * radius;
      const cy = H / 2 + Math.sin(ang) * radius;
      const cell = state.cells[id];
      cell.center = { x: cx, y: cy };
      cell.el.style.left = cx + "px";
      cell.el.style.top = cy + "px";
    });
  }

  function spawnCells(specialists) {
    els.cells.innerHTML = "";
    state.cells = {};
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
    "letter.ready": (e) => renderLetter(e),
    "appeal.done": (e) => { setRunning(false); toast("Appeal worked. Awaiting nurse signature."); },
    "appeal.error": (e) => { setRunning(false); toast("Pipeline error: " + (e.error || "")); },
  };

  function dispatch(ev) { const h = handlers[ev.type]; if (h) try { h(ev); } catch (_) {} }

  function renderSpec(e) {
    const d = (e.denial) || {};
    const rows = [
      ["Payer", d.payer], ["Plan", d.plan], ["Denial code", d.denial_code, true],
      ["Claim", d.claim_id], ["CPT", (d.cpt || []).join(", ")],
      ["Billed", d.billed_amount != null ? "$" + Number(d.billed_amount).toLocaleString() : ""],
      ["DOS", d.date_of_service], ["Specialists", (e.required_specialists || []).length],
      ["Filing deadline", e.sol_deadline],
    ];
    els.spec.innerHTML = rows.filter(r => r[1] != null && r[1] !== "")
      .map(([k, v, code]) => `<div class="spec-row"><span class="k">${k}</span><span class="v ${code ? "code" : ""}">${esc(String(v))}</span></div>`).join("");
  }

  function renderCost(e) {
    els.pavo.textContent = "$" + Number(e.pavo_total || 0).toFixed(4);
    els.frontier.textContent = "$" + Number(e.frontier_total || 0).toFixed(4);
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
      `<div class="ctr-claim"><div class="ctr-label">Payer claim · turn ${e.rep_turn_id}</div><div class="q">"${esc(e.claim || "")}"</div></div>` +
      `<div class="ctr-evi"><div class="ctr-label">Contradicted by record · turn ${e.evidence_turn_id}</div><div class="q">"${esc(e.evidence || "")}"</div></div>`;
  }

  function renderLetter(e) {
    els.letterCard.hidden = false;
    const url = e.pdf_url ? (API + e.pdf_url) : "";
    els.letterBody.innerHTML =
      `<div class="letter-meta">${esc(e.appeal_id || "")} · citations: ${esc((e.citations || []).join(", "))}</div>` +
      `<div>Verbatim appeal letter drafted from the call record. Requires licensed appeals-nurse signature before filing.</div>` +
      `<div class="letter-actions">` +
      (url ? `<a class="btn-link" href="${url}" target="_blank">Open PDF</a>` : "") +
      `<button class="btn-sign" id="sign-btn">Nurse: sign &amp; file</button></div>`;
    const sb = $("sign-btn");
    if (sb) sb.onclick = () => { sb.textContent = "✓ Signed & filed"; sb.classList.add("signed"); toast("Appeal filed (nurse-signed)."); };
  }

  function renderSponsor(name, mode) {
    let el = document.querySelector(`.sponsor[data-n="${name}"]`);
    if (!el) {
      el = document.createElement("div"); el.className = "sponsor"; el.dataset.n = name;
      el.innerHTML = `<span class="sdot"></span><span>${SPONSOR_LABEL[name] || name}</span><span class="smode"></span>`;
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
    try {
      const r = await fetch(API + "/appeals", { method: "POST" });
      if (!r.ok) throw new Error("http " + r.status);
    } catch (err) { toast("Server unreachable — playing replay."); replay(); }
  }
  els.runBtn.addEventListener("click", runAppeal);

  function setRunning(v) { state.running = v; els.runBtn.disabled = v; }
  function resetUI() {
    els.reconcileCard.hidden = true; els.letterCard.hidden = true;
    els.cells.innerHTML = ""; state.cells = {}; els.stageHint.style.display = "";
    renderCost({ pavo_total: 0, frontier_total: 0, ratio: 1, tier_counts: {} });
  }
  function toast(msg) { els.toast.textContent = msg; els.toast.hidden = false; clearTimeout(toast._t); toast._t = setTimeout(() => els.toast.hidden = true, 3200); }
  function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

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
  resize();
  connect();
})();
