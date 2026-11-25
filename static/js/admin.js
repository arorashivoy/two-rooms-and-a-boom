/* Two Rooms and a Boom — admin/game-master console. */

const socket = io();
let st = null;
let clockOffset = 0;

const phasePill = document.getElementById("phase-pill");
const roundPill = document.getElementById("round-pill");
const clockEl = document.getElementById("clock");
const counts = document.getElementById("counts");
const setupEl = document.getElementById("setup");
const controlsEl = document.getElementById("controls");
const playersEl = document.getElementById("players");
const resultCard = document.getElementById("result-card");
const resultEl = document.getElementById("result");

socket.on("connect", () => socket.emit("admin_hello", {}));
socket.on("admin_error", (e) => alert(e.message || "error"));
socket.on("admin_state", (s) => {
  const first = !st;
  st = s;
  if (s.timer && s.timer.server_now) clockOffset = s.timer.server_now - Date.now();
  if (first || setupEl.dataset.dirty !== "1") renderSetup();
  renderHeader();
  syncExchange();
  renderControls();
  renderPlayers();
  renderResult();
});

function fmt(ms) {
  ms = Math.max(0, ms);
  const t = Math.round(ms / 1000);
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
}
setInterval(() => {
  if (!st) return;
  if (st.phase === "ROUND" && st.timer) {
    const t = st.timer;
    const remaining = t.paused ? (t.remaining_ms || 0) : (t.round_ends_at || 0) - (Date.now() + clockOffset);
    clockEl.textContent = fmt(remaining) + (t.paused ? " ⏸" : "");
  } else {
    clockEl.textContent = "--:--";
  }
}, 200);

function renderHeader() {
  phasePill.textContent = st.phase;
  roundPill.textContent = st.phase === "LOBBY" ? "lobby"
    : `round ${st.current_round || 0}/${st.config.num_rounds}`;
  counts.textContent = `· ${st.players.length} players · A:${st.rooms.A} B:${st.rooms.B}`;
}

function el(html) { const d = document.createElement("div"); d.innerHTML = html.trim(); return d.firstChild; }

function renderSetup() {
  const c = st.config;
  const toggles = (st.optional_roles || []).map((r) => {
    const on = c.enabled_optional_ids.includes(r.id);
    return `<label><input type="checkbox" data-role="${r.id}" ${on ? "checked" : ""}> ${r.name}</label>`;
  }).join("");
  setupEl.innerHTML = `
    <label class="field">Rounds <input id="cfg-rounds" type="number" min="1" max="9" value="${c.num_rounds}"></label>
    <label class="field">Round durations (sec, comma) <input id="cfg-durations" type="text" value="${c.round_durations.join(", ")}"></label>
    <label class="field">Hostages per round (comma) <input id="cfg-hostages" type="text" value="${c.hostage_counts.join(", ")}"></label>
    <div class="role-toggles"><div class="muted small">Optional characters:</div>${toggles}</div>
    <label class="field"><input id="cfg-reveal" type="checkbox" ${c.reveal_all_roles ? "checked" : ""}> Reveal all cards at end</label>
    <button id="start-btn" class="btn btn-primary">${st.phase === "LOBBY" ? "Deal cards & assign rooms" : "Re-deal (new game)"}</button>
  `;
  setupEl.oninput = () => { setupEl.dataset.dirty = "1"; };
  document.getElementById("start-btn").onclick = () => {
    setupEl.dataset.dirty = "0";
    socket.emit("admin_start", readConfig());
  };
}

function readConfig() {
  const roundsN = parseInt(document.getElementById("cfg-rounds").value, 10) || 5;
  const durations = document.getElementById("cfg-durations").value.split(",").map((x) => parseInt(x.trim(), 10) || 60);
  const hostages = document.getElementById("cfg-hostages").value.split(",").map((x) => parseInt(x.trim(), 10) || 1);
  const roles = [...setupEl.querySelectorAll("input[data-role]:checked")].map((i) => i.dataset.role);
  const pad = (arr, fallback) => { while (arr.length < roundsN) arr.push(fallback); return arr.slice(0, roundsN); };
  return {
    num_rounds: roundsN,
    round_durations: pad(durations, 60),
    hostage_counts: pad(hostages, 1),
    enabled_optional_ids: roles,
    reveal_all_roles: document.getElementById("cfg-reveal").checked,
  };
}

function btn(label, cls, handler) {
  const b = el(`<button class="btn ${cls}">${label}</button>`);
  b.onclick = handler;
  return b;
}

function renderControls() {
  controlsEl.innerHTML = "";
  const add = (n) => controlsEl.appendChild(n);
  const row = () => { const r = document.createElement("div"); r.className = "row"; controlsEl.appendChild(r); return r; };

  if (st.phase === "LOBBY") {
    add(el(`<p class="muted">Waiting for players to join. Configure setup, then deal.</p>`));
  }
  if (st.phase === "DEAL") {
    const label = st.current_round > 0 ? `Begin Round ${st.current_round + 1}` : "Begin Round 1";
    add(btn(label, "btn-blue", () => socket.emit("admin_begin_round", {})));
  }
  if (st.phase === "ROUND") {
    const r1 = row();
    r1.appendChild(st.timer.paused
      ? btn("▶ Resume", "btn-green", () => socket.emit("admin_resume", {}))
      : btn("⏸ Pause", "btn-ghost", () => socket.emit("admin_pause", {})));
    r1.appendChild(btn("+30s", "btn-ghost", () => socket.emit("admin_adjust", { delta: 30 })));
    r1.appendChild(btn("−30s", "btn-ghost", () => socket.emit("admin_adjust", { delta: -30 })));
    add(btn("⏭ End round now (go to hostages)", "btn-ghost", () => socket.emit("admin_skip_round", {})));
  }
  if (st.phase === "HOSTAGE_SELECT") {
    add(el(`<p class="muted">Leaders are choosing hostages. A locked: <b>${st.confirmed.A ? "✔" : "…"}</b> · B locked: <b>${st.confirmed.B ? "✔" : "…"}</b></p>`));
    renderExchange(add);
  }
  if (st.phase === "ENDGAME") {
    add(btn("↺ New game (keep players)", "btn-primary", () => socket.emit("admin_reset", { keep_players: true })));
  }

  if (st.phase !== "LOBBY") {
    controlsEl.appendChild(el(`<hr style="border-color:var(--surface);margin:14px 0">`));
    if (st.phase !== "ENDGAME") add(btn("Reveal result now", "btn-red", () => { if (confirm("End the game and reveal the result?")) socket.emit("admin_reveal", {}); }));
    add(btn("Reset to lobby (keep players)", "btn-ghost", () => { if (confirm("Reset the game back to the lobby?")) socket.emit("admin_reset", { keep_players: true }); }));
  }
}

/* ---- admin hostage exchange ----
   The admin picks the hostages of both rooms and runs the swap. The boxes start
   with the picks of the leaders. After the admin changes a box, the picks stay
   as the admin set them until the phase or the round changes. */
let exPick = { A: [], B: [] };
let exKey = "";
let exTouched = false;

function syncExchange() {
  const key = st.phase + ":" + st.current_round;
  if (key !== exKey) { exKey = key; exTouched = false; exPick = { A: [], B: [] }; }
  if (st.phase === "HOSTAGE_SELECT" && !exTouched) {
    exPick = { A: [...(st.hostages.A || [])], B: [...(st.hostages.B || [])] };
  }
  for (const room of ["A", "B"]) {
    exPick[room] = exPick[room].filter((id) => st.players.some((p) => p.id === id && p.room === room));
  }
}

function renderExchange(add) {
  const wrap = el(`<div class="exchange"></div>`);
  wrap.appendChild(el(`<h3>Hostage exchange (admin)</h3>`));
  wrap.appendChild(el(`<p class="muted small">Tap the players who move to the other room. Both rooms send the same number. The leaders need to do nothing.</p>`));
  const cols = document.createElement("div");
  cols.className = "ex-cols";
  for (const room of ["A", "B"]) {
    const col = document.createElement("div");
    col.className = "ex-col";
    col.appendChild(el(`<div class="ex-head">Room ${room} sends ${exPick[room].length} <span class="muted small">(round rule: ${st.required[room]})</span></div>`));
    st.players.filter((p) => p.room === room).forEach((p) => {
      const on = exPick[room].includes(p.id);
      const chip = el(`<button class="chip ${on ? "on" : ""}" aria-pressed="${on}">${escapeHtml(p.name)}${p.is_leader ? " ★" : ""}</button>`);
      chip.onclick = () => {
        exTouched = true;
        const i = exPick[room].indexOf(p.id);
        if (i >= 0) exPick[room].splice(i, 1); else exPick[room].push(p.id);
        renderControls();
      };
      col.appendChild(chip);
    });
    cols.appendChild(col);
  }
  wrap.appendChild(cols);
  const a = exPick.A.length, b = exPick.B.length;
  const skip = a === 0 && b === 0;
  const balanced = a === b;
  const go = btn(skip ? "Skip exchange (no one moves)" : `Exchange ${a} ⇄ ${b} and go on`, skip ? "btn-ghost" : "btn-primary", () => {
    if (skip && !confirm("Move nobody and go on?")) return;
    socket.emit("admin_exchange", { from_a: exPick.A, from_b: exPick.B });
  });
  go.disabled = !balanced;
  wrap.appendChild(go);
  if (!balanced) wrap.appendChild(el(`<p class="muted small">Room A sends ${a}, Room B sends ${b}. Make the two numbers equal.</p>`));
  add(wrap);
}

function renderPlayers() {
  if (!st.players.length) { playersEl.innerHTML = `<p class="muted">No players yet.</p>`; return; }
  // Cards are intentionally never shown here — the laptop is visible to players.
  const showLeader = st.phase === "ROUND" || st.phase === "HOSTAGE_SELECT";
  const rows = st.players.map((p) => {
    return `<tr>
      <td><span class="dot ${p.connected ? "" : "off"}"></span> ${escapeHtml(p.name)}</td>
      <td>${p.room ? "Room " + p.room : "—"}</td>
      ${showLeader ? `<td>${p.is_leader ? "★" : `<button class="btn btn-sm btn-ghost" data-lead="${p.id}" data-room="${p.room}">make leader</button>`}</td>` : ""}
      <td><button class="btn btn-sm btn-red" data-kick="${p.id}">kick</button></td>
    </tr>`;
  }).join("");
  playersEl.innerHTML = `<table><thead><tr>
      <th>Name</th><th>Room</th>${showLeader ? "<th>Lead</th>" : ""}<th></th>
    </tr></thead><tbody>${rows}</tbody></table>`;
  playersEl.querySelectorAll("[data-kick]").forEach((b) =>
    b.onclick = () => { if (confirm("Remove this player?")) socket.emit("admin_kick", { player_id: b.dataset.kick }); });
  playersEl.querySelectorAll("[data-lead]").forEach((b) =>
    b.onclick = () => socket.emit("admin_set_leader", { room: b.dataset.room, player_id: b.dataset.lead }));
}

function renderResult() {
  if (st.phase !== "ENDGAME" || !st.result) { resultCard.style.display = "none"; return; }
  resultCard.style.display = "";
  const r = st.result;
  const cls = r.winner === "blue" ? "win-blue" : "win-red";
  resultEl.innerHTML = `
    <p class="${cls}" style="font-size:1.4rem">${r.winner.toUpperCase()} team wins</p>
    <p class="muted">President & Bomber were in ${r.same_room ? "the SAME room 💥" : "DIFFERENT rooms 🕊️"}.</p>
    <p class="muted small">President: ${r.president ? escapeHtml(r.president.name) + " (Room " + r.president.room + ")" : "—"} ·
     Bomber: ${r.bomber ? escapeHtml(r.bomber.name) + " (Room " + r.bomber.room + ")" : "—"}</p>`;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
