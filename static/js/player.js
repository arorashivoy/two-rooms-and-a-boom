/* Two Rooms and a Boom — player client.
   Renders whatever phase the server pushes. The server owns the clock; we
   correct for phone-vs-laptop skew with a one-time offset per state message. */

const socket = io();
const app = document.getElementById("app");
const timerbar = document.getElementById("timerbar");
const clockEl = document.getElementById("clock");
const roundLabel = document.getElementById("roundlabel");
const pausedBadge = document.getElementById("paused-badge");
const whoami = document.getElementById("whoami");

let state = null;
let clockOffset = 0;          // server_now - Date.now()
let cardRevealed = false;
let hostagePick = [];         // player ids the leader has selected

socket.on("connect", () => socket.emit("player_hello", { player_id: window.PLAYER_ID }));
socket.on("kicked", () => { window.location.href = "/"; });
socket.on("state", (s) => {
  if (s.phase === "GONE") { window.location.href = "/"; return; }
  const phaseChanged = !state || state.phase !== s.phase;
  if (phaseChanged) { cardRevealed = false; hostagePick = []; }
  state = s;
  if (s.timer && s.timer.server_now) clockOffset = s.timer.server_now - Date.now();
  // keep leader's in-progress picks in sync with server
  if (s.phase === "HOSTAGE_SELECT" && s.is_leader && s.hostages) hostagePick = [...s.hostages];
  render();
  refreshCardOverlay();
});

function fmt(ms) {
  ms = Math.max(0, ms);
  const total = Math.round(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/* ---- authoritative countdown ---- */
setInterval(() => {
  if (!state || state.phase !== "ROUND" || !state.timer) { timerbar.classList.add("hidden"); return; }
  timerbar.classList.remove("hidden");
  const t = state.timer;
  let remaining;
  if (t.paused) { remaining = t.remaining_ms || 0; }
  else { remaining = (t.round_ends_at || 0) - (Date.now() + clockOffset); }
  clockEl.textContent = fmt(remaining);
  roundLabel.textContent = `Round ${state.current_round} / ${state.num_rounds}`;
  pausedBadge.classList.toggle("hidden", !t.paused);
  timerbar.classList.toggle("warn", remaining <= 30000 && remaining > 10000);
  timerbar.classList.toggle("danger", remaining <= 10000);
}, 200);

function el(html) { const d = document.createElement("div"); d.innerHTML = html.trim(); return d.firstChild; }

function render() {
  if (!state) return;
  if (whoami) whoami.textContent = state.me ? state.me.name : "";
  app.innerHTML = "";
  switch (state.phase) {
    case "LOBBY": return renderLobby();
    case "DEAL": return renderDeal();
    case "ROUND": return renderRound();
    case "HOSTAGE_SELECT": return renderHostage();
    case "ENDGAME": return renderEndgame();
    default: app.innerHTML = `<p class="muted">Waiting…</p>`;
  }
}

function renderLobby() {
  app.appendChild(el(`
    <div class="notice">
      <div class="big-emoji">⏳</div>
      <h2>You're in, ${escapeHtml(state.me.name)}!</h2>
      <p class="muted">Waiting for the host to start the game.</p>
      <p class="muted small">Keep this page open. You can refresh without losing your spot.</p>
    </div>`));
}

function roomBanner() {
  const room = state.room;
  return el(`<div class="room-banner room-${room}">You are in Room ${room}</div>`);
}

function roleCard() {
  const role = state.me.role;
  if (!role) return el(`<p class="muted">No card yet.</p>`);
  if (!cardRevealed) {
    const c = el(`
      <div class="rolecard hidden-face">
        <div class="big-emoji">🂠</div>
        <div>Your secret card</div>
        <div class="tap-hint">Tap to reveal — keep it hidden from others!</div>
      </div>`);
    c.onclick = () => { cardRevealed = true; render(); };
    return c;
  }
  const c = el(`
    <div class="rolecard team-${role.team}">
      <div class="role-name">${escapeHtml(role.name)}</div>
      <div class="role-team">${role.team} team</div>
      <div class="role-desc">${escapeHtml(role.description)}</div>
      <div class="tap-hint">Tap to hide</div>
    </div>`);
  c.onclick = () => { cardRevealed = false; render(); };
  return c;
}

function renderDeal() {
  if (state.room) app.appendChild(roomBanner());
  app.appendChild(roleCard());
  const waiting = state.current_round > 0
    ? `Round ${state.current_round} finished — waiting for the host to start the next round.`
    : `Waiting for the host to begin Round 1.`;
  app.appendChild(el(`<p class="notice muted">${waiting}</p>`));
}

function memberList({ selectable = false } = {}) {
  const ul = document.createElement("ul");
  ul.className = "members";
  (state.room_members || []).forEach((m) => {
    const isLeader = m.id === state.leader;
    const li = el(`
      <li class="${selectable ? "selectable" : ""} ${hostagePick.includes(m.id) ? "selected" : ""}">
        <span class="dot ${m.connected ? "" : "off"}"></span>
        <span>${escapeHtml(m.name)}${m.id === state.me.id ? " (you)" : ""}</span>
        ${isLeader ? '<span class="leader-tag">★ leader</span>' : ""}
      </li>`);
    if (selectable) {
      li.onclick = () => toggleHostage(m.id);
    }
    ul.appendChild(li);
  });
  return ul;
}

function renderRound() {
  app.appendChild(roomBanner());
  const leaderName = leaderNameOf();
  app.appendChild(el(`<h3 class="section-title">Room ${state.room} · ${state.room_members.length} here</h3>`));
  app.appendChild(el(`<p class="muted">Leader: <b>${leaderName || "— none yet —"}</b></p>`));

  if (!state.is_leader) {
    const b = el(`<button class="btn btn-ghost btn-sm">I'll be leader</button>`);
    b.onclick = () => socket.emit("claim_leader", {});
    app.appendChild(b);
  } else {
    app.appendChild(el(`<p class="muted small">You are the leader. When the timer ends you'll pick hostages to send to the other room.</p>`));
  }
  app.appendChild(memberList());
}

function renderHostage() {
  app.appendChild(roomBanner());
  if (!state.is_leader) {
    app.appendChild(el(`
      <div class="notice">
        <div class="big-emoji">🤝</div>
        <h2>Hostage exchange</h2>
        <p class="muted">Your leader (<b>${escapeHtml(leaderNameOf() || "—")}</b>) is choosing who to send to the other room.</p>
      </div>`));
    if (!state.leader) {
      const b = el(`<button class="btn btn-ghost">No leader — I'll do it</button>`);
      b.onclick = () => socket.emit("claim_leader", {});
      app.appendChild(b);
    }
    app.appendChild(memberList());
    return;
  }
  const need = state.required_hostages;
  app.appendChild(el(`<h3 class="section-title">Pick ${need} to send away</h3>`));
  app.appendChild(el(`<p class="muted">Selected ${hostagePick.length} / ${need}</p>`));
  app.appendChild(memberList({ selectable: true }));
  const confirm = el(`<button class="btn btn-green" ${hostagePick.length === need ? "" : "disabled"}>Send these ${need} & lock in</button>`);
  confirm.onclick = () => socket.emit("confirm_hostages", {});
  app.appendChild(confirm);
  if (state.confirmed) {
    app.appendChild(el(`<p class="notice muted">Locked in. Waiting for the other room…</p>`));
  }
}

function toggleHostage(id) {
  const need = state.required_hostages;
  const i = hostagePick.indexOf(id);
  if (i >= 0) hostagePick.splice(i, 1);
  else if (hostagePick.length < need) hostagePick.push(id);
  socket.emit("set_hostages", { player_ids: hostagePick });
  render();
}

function renderEndgame() {
  const r = state.result || {};
  const winClass = r.winner === "blue" ? "win-blue" : "win-red";
  const winWord = r.winner === "blue" ? "BLUE team wins!" : "RED team wins!";
  const because = r.same_room
    ? "The President and the Bomber ended in the SAME room. 💥"
    : "The President and the Bomber ended in DIFFERENT rooms. 🕊️";
  app.appendChild(el(`
    <div class="notice">
      <div class="big-emoji">${r.winner === "red" ? "💥" : "🕊️"}</div>
      <h1 class="${winClass}">${winWord}</h1>
      <p class="muted">${because}</p>
      <p class="muted small">President: ${r.president ? escapeHtml(r.president.name) + " (Room " + r.president.room + ")" : "—"}<br>
      Bomber: ${r.bomber ? escapeHtml(r.bomber.name) + " (Room " + r.bomber.room + ")" : "—"}</p>
    </div>`));
  if (state.all_roles) {
    const rows = state.all_roles.map((p) =>
      `<tr><td>${escapeHtml(p.name)}</td><td>Room ${p.room || "—"}</td>
       <td><span class="tag ${p.role ? p.role.team : "grey"}">${p.role ? escapeHtml(p.role.name) : "—"}</span></td></tr>`).join("");
    app.appendChild(el(`<h3 class="section-title">Everyone's cards</h3>
      <table><thead><tr><th>Name</th><th>Room</th><th>Card</th></tr></thead><tbody>${rows}</tbody></table>`));
  }
}

function leaderNameOf() {
  if (!state.leader) return null;
  const m = (state.room_members || []).find((x) => x.id === state.leader);
  return m ? m.name : null;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---- how-to-play overlay (lives outside #app, survives re-renders) ---- */
const rulesOverlay = document.getElementById("rules-overlay");
document.getElementById("rules-btn").onclick = () => rulesOverlay.classList.remove("hidden");
document.getElementById("rules-close").onclick = () => rulesOverlay.classList.add("hidden");
rulesOverlay.onclick = (e) => { if (e.target === rulesOverlay) rulesOverlay.classList.add("hidden"); };

/* ---- "my card" overlay: always available so nobody forgets their role ---- */
const cardOverlay = document.getElementById("card-overlay");
const cardBody = document.getElementById("card-overlay-body");
function fillMyCard() {
  const role = state && state.me ? state.me.role : null;
  if (!role) {
    cardBody.innerHTML = `<div class="rolecard hidden-face"><div class="big-emoji">🂠</div>
      <div>No card yet</div><div class="tap-hint">You'll get one when the host deals.</div></div>`;
    return;
  }
  cardBody.innerHTML = `<div class="rolecard team-${role.team}">
      <div class="role-name">${escapeHtml(role.name)}</div>
      <div class="role-team">${role.team} team</div>
      <div class="role-desc">${escapeHtml(role.description)}</div>
    </div>`;
}
function refreshCardOverlay() { if (!cardOverlay.classList.contains("hidden")) fillMyCard(); }
document.getElementById("card-btn").onclick = () => { fillMyCard(); cardOverlay.classList.remove("hidden"); };
document.getElementById("card-close").onclick = () => cardOverlay.classList.add("hidden");
cardOverlay.onclick = (e) => { if (e.target === cardOverlay) cardOverlay.classList.add("hidden"); };
