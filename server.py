"""Two Rooms and a Boom — local game server.

Run:  python server.py
Then open the printed LAN URL on player phones, and /admin on your laptop.
"""

import json
import os
import random
import socket

from dotenv import load_dotenv
from flask import (
    Flask,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_socketio import SocketIO, emit, join_room

import game as gamemod
from game import Game
from roles import optional_roles

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("TRAB_SECRET", os.urandom(24).hex())
socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")

CONFIGURED_ADMIN_PIN = os.environ.get("TRAB_ADMIN_PIN")
ADMIN_PIN = CONFIGURED_ADMIN_PIN or f"{random.randint(1000, 9999)}"
PORT = int(os.environ.get("TRAB_PORT", "5001"))
SNAPSHOT_PATH = os.path.join(os.path.dirname(__file__), "data", "state.json")

GAME = Game()
SID_TO_PID = {}          # socket session id -> player id
ADMIN_SIDS = set()       # socket session ids of connected admins


# --------------------------------------------------------------------------
# Persistence (best-effort snapshot so a laptop restart can recover a game)
# --------------------------------------------------------------------------
def save_snapshot():
    try:
        os.makedirs(os.path.dirname(SNAPSHOT_PATH), exist_ok=True)
        with open(SNAPSHOT_PATH, "w") as f:
            json.dump(GAME.to_snapshot(), f)
    except OSError:
        pass


def load_snapshot():
    try:
        with open(SNAPSHOT_PATH) as f:
            GAME.load_snapshot(json.load(f))
    except (OSError, ValueError):
        pass


# --------------------------------------------------------------------------
# Broadcasting: each phone gets its OWN view (its card, its room). Admins get
# the full picture.
# --------------------------------------------------------------------------
def broadcast_state():
    # Use socketio.emit (not flask_socketio.emit): this runs both from socket
    # handlers AND from the background timer thread, which has no request context.
    player_views = GAME.player_views(SID_TO_PID.values())
    for sid, pid in list(SID_TO_PID.items()):
        socketio.emit("state", player_views.get(pid, {"phase": "GONE"}), to=sid)
    admin_view = GAME.admin_view()
    admin_view["optional_roles"] = [
        {"id": r["id"], "name": r["name"], "team": r["team"]} for r in optional_roles()
    ]
    for sid in list(ADMIN_SIDS):
        socketio.emit("admin_state", admin_view, to=sid)
    save_snapshot()


def local_ip():
    """Best-effort LAN IP (the address phones should dial)."""
    advertised_host = os.environ.get("TRAB_PUBLIC_HOST")
    if advertised_host:
        return advertised_host
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


# --------------------------------------------------------------------------
# HTTP routes
# --------------------------------------------------------------------------
@app.route("/")
def index():
    pid = session.get("player_id")
    if pid and GAME.get(pid):
        return redirect(url_for("player"))
    return render_template("login.html")


@app.route("/login", methods=["POST"])
def login():
    name = (request.form.get("name") or "").strip()[:24]
    if not name:
        return redirect(url_for("index"))
    # Reuse an existing profile if this browser already owns one.
    pid = session.get("player_id")
    if pid and GAME.get(pid):
        GAME.get(pid).name = name
    else:
        p = GAME.add_player(name)
        session["player_id"] = p.id
        session.permanent = True
    return redirect(url_for("player"))


@app.route("/player")
def player():
    pid = session.get("player_id")
    if not (pid and GAME.get(pid)):
        return redirect(url_for("index"))
    return render_template("player.html", player_id=pid)


@app.route("/logout")
def logout():
    session.pop("player_id", None)
    return redirect(url_for("index"))


@app.route("/admin")
def admin():
    if not session.get("admin"):
        return render_template("admin_login.html")
    return render_template("admin.html")


@app.route("/admin/login", methods=["POST"])
def admin_login():
    if (request.form.get("pin") or "").strip() == ADMIN_PIN:
        session["admin"] = True
    return redirect(url_for("admin"))


# --------------------------------------------------------------------------
# Socket.IO — player events
# --------------------------------------------------------------------------
@socketio.on("player_hello")
def on_player_hello(data):
    pid = (data or {}).get("player_id")
    p = GAME.get(pid)
    if not p:
        emit("state", {"phase": "GONE"})
        return
    p.connected = True
    p.sid = request.sid
    SID_TO_PID[request.sid] = pid
    broadcast_state()


@socketio.on("claim_leader")
def on_claim_leader(data):
    pid = SID_TO_PID.get(request.sid)
    p = GAME.get(pid)
    if p and p.room and GAME.phase in (gamemod.ROUND, gamemod.HOSTAGE_SELECT):
        GAME.set_leader(p.room, pid)
        broadcast_state()


@socketio.on("appoint_leader")
def on_appoint_leader(data):
    """Current leader hands leadership to another player in the same room."""
    pid = SID_TO_PID.get(request.sid)
    p = GAME.get(pid)
    target = (data or {}).get("player_id")
    t = GAME.get(target)
    if p and t and p.room == t.room and GAME.leaders.get(p.room) == pid:
        GAME.set_leader(p.room, target)
        broadcast_state()


@socketio.on("set_hostages")
def on_set_hostages(data):
    pid = SID_TO_PID.get(request.sid)
    p = GAME.get(pid)
    if p and GAME.phase == gamemod.HOSTAGE_SELECT and GAME.leaders.get(p.room) == pid:
        GAME.set_hostages(p.room, (data or {}).get("player_ids", []))
        broadcast_state()


@socketio.on("confirm_hostages")
def on_confirm_hostages(data):
    pid = SID_TO_PID.get(request.sid)
    p = GAME.get(pid)
    if not (p and GAME.phase == gamemod.HOSTAGE_SELECT and GAME.leaders.get(p.room) == pid):
        return
    if GAME.confirm_hostages(p.room) and GAME.both_confirmed():
        outcome = GAME.apply_swap()
        if outcome == "next":
            GAME.phase = gamemod.DEAL  # between-round "ready" screen
    broadcast_state()


# --------------------------------------------------------------------------
# Socket.IO — admin events
# --------------------------------------------------------------------------
def _is_admin():
    return session.get("admin") or request.sid in ADMIN_SIDS


@socketio.on("admin_hello")
def on_admin_hello(data):
    if not session.get("admin"):
        return
    ADMIN_SIDS.add(request.sid)
    join_room("admins")
    broadcast_state()


@socketio.on("admin_start")
def on_admin_start(data):
    if not _is_admin():
        return
    cfg = gamemod.default_config((data or {}).get("num_rounds", 5))
    if data:
        if "round_durations" in data:
            cfg["round_durations"] = [int(x) for x in data["round_durations"]]
        if "hostage_counts" in data:
            cfg["hostage_counts"] = [int(x) for x in data["hostage_counts"]]
        if "enabled_optional_ids" in data:
            cfg["enabled_optional_ids"] = list(data["enabled_optional_ids"])
        if "reveal_all_roles" in data:
            cfg["reveal_all_roles"] = bool(data["reveal_all_roles"])
    try:
        GAME.start_game(cfg)
    except ValueError as e:
        emit("admin_error", {"message": str(e)})
        return
    broadcast_state()


@socketio.on("admin_begin_round")
def on_admin_begin_round(data):
    if not _is_admin():
        return
    if GAME.phase in (gamemod.DEAL, gamemod.ROUND):
        GAME.begin_round()
        broadcast_state()


@socketio.on("admin_pause")
def on_admin_pause(data):
    if _is_admin():
        GAME.pause()
        broadcast_state()


@socketio.on("admin_resume")
def on_admin_resume(data):
    if _is_admin():
        GAME.resume()
        broadcast_state()


@socketio.on("admin_adjust")
def on_admin_adjust(data):
    if _is_admin():
        GAME.adjust_time(int((data or {}).get("delta", 0)))
        broadcast_state()


@socketio.on("admin_skip_round")
def on_admin_skip_round(data):
    """End the timer now and go to hostage selection."""
    if _is_admin():
        GAME.end_round()
        broadcast_state()


@socketio.on("admin_set_leader")
def on_admin_set_leader(data):
    if _is_admin() and data:
        GAME.set_leader(data.get("room"), data.get("player_id"))
        broadcast_state()


@socketio.on("admin_force_swap")
def on_admin_force_swap(data):
    """Skip hostage confirmation and just advance (admin override)."""
    if not _is_admin():
        return
    if GAME.phase == gamemod.HOSTAGE_SELECT:
        outcome = GAME.apply_swap()
        if outcome == "next":
            GAME.phase = gamemod.DEAL
        broadcast_state()


@socketio.on("admin_kick")
def on_admin_kick(data):
    if not _is_admin():
        return
    pid = (data or {}).get("player_id")
    # Boot their socket if connected.
    for sid, mapped in list(SID_TO_PID.items()):
        if mapped == pid:
            emit("kicked", {}, to=sid)
            SID_TO_PID.pop(sid, None)
    GAME.remove_player(pid)
    broadcast_state()


@socketio.on("admin_reveal")
def on_admin_reveal(data):
    if _is_admin():
        GAME.compute_result()
        broadcast_state()


@socketio.on("admin_reset")
def on_admin_reset(data):
    if not _is_admin():
        return
    keep_players = bool((data or {}).get("keep_players", True))
    GAME.reset(full=not keep_players)
    broadcast_state()


# --------------------------------------------------------------------------
# Connection lifecycle + authoritative timer watcher
# --------------------------------------------------------------------------
@socketio.on("disconnect")
def on_disconnect():
    sid = request.sid
    ADMIN_SIDS.discard(sid)
    pid = SID_TO_PID.pop(sid, None)
    p = GAME.get(pid) if pid else None
    if p:
        p.connected = False
        p.sid = None
        broadcast_state()


def timer_watcher():
    """Single background loop: the server owns the clock and fires round-end."""
    while True:
        socketio.sleep(0.5)
        if (
            GAME.phase == gamemod.ROUND
            and not GAME.paused
            and GAME.round_ends_at
            and gamemod.now_ms() >= GAME.round_ends_at
        ):
            GAME.end_round()
            broadcast_state()


def main():
    load_snapshot()
    socketio.start_background_task(timer_watcher)
    ip = local_ip()
    line = "=" * 52
    print(f"\n{line}")
    print("  Two Rooms and a Boom — game server running")
    print(line)
    print(f"  Players open:  http://{ip}:{PORT}/")
    print(f"  Admin panel:   http://{ip}:{PORT}/admin")
    if CONFIGURED_ADMIN_PIN:
        print("  Admin PIN:     configured with TRAB_ADMIN_PIN")
    else:
        print(f"  Admin PIN:     {ADMIN_PIN}")
    print(f"{line}\n  (Phones must be on the SAME Wi-Fi. Allow the macOS")
    print("   firewall prompt if it appears.)")
    print(f"{line}\n")
    socketio.run(app, host="0.0.0.0", port=PORT, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    main()
