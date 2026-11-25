"""Game state machine for Two Rooms and a Boom.

This module is (almost) pure state: it holds the game, mutates it through
well-defined transitions, and produces view dicts. It does NOT own the wall
clock loop — server.py schedules the round-end callback. Time is tracked as
`round_ends_at` (epoch milliseconds) so every phone can render the same
countdown regardless of when it connected.
"""

import random
import time
import uuid

from roles import build_deck

LOBBY = "LOBBY"
DEAL = "DEAL"
ROUND = "ROUND"
HOSTAGE_SELECT = "HOSTAGE_SELECT"
ENDGAME = "ENDGAME"

# Default per-round lengths in seconds; trimmed/padded to the chosen round count.
DEFAULT_DURATIONS = [180, 120, 120, 60, 60]


def now_ms():
    return int(time.time() * 1000)


def default_config(num_rounds=5):
    durations = list(DEFAULT_DURATIONS)
    while len(durations) < num_rounds:
        durations.append(60)
    durations = durations[:num_rounds]
    return {
        "num_rounds": num_rounds,
        "round_durations": durations,          # seconds, per round
        "hostage_counts": [1] * num_rounds,    # players swapped per room, per round
        "enabled_optional_ids": [],
        "reveal_all_roles": True,              # show every card at endgame
    }


class Player:
    def __init__(self, name):
        self.id = uuid.uuid4().hex
        self.name = name
        self.room = None          # "A" | "B" | None
        self.role = None          # role dict once dealt
        self.connected = False
        self.sid = None           # current Socket.IO session id

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "room": self.room,
            "role": self.role,
            "connected": self.connected,
        }


class Game:
    def __init__(self):
        self.reset(full=True)

    # ----- lifecycle -------------------------------------------------------
    def reset(self, full=False):
        """Return to the lobby. `full=True` also clears the player list."""
        self.phase = LOBBY
        self.current_round = 0                 # 1-based once rounds begin
        self.leaders = {"A": None, "B": None}
        self.round_ends_at = None              # epoch ms
        self.paused = False
        self.remaining_ms = None               # set while paused
        self.hostages = {"A": [], "B": []}     # selected player ids
        self.confirmed = {"A": False, "B": False}
        self.result = None
        if full or not hasattr(self, "players"):
            self.players = {}                  # id -> Player
            self.config = default_config()

    # ----- players (lobby) -------------------------------------------------
    def add_player(self, name):
        p = Player(name)
        self.players[p.id] = p
        return p

    def get(self, player_id):
        return self.players.get(player_id)

    def remove_player(self, player_id):
        self.players.pop(player_id, None)
        for room in ("A", "B"):
            if self.leaders[room] == player_id:
                self.leaders[room] = None
            self.hostages[room] = [pid for pid in self.hostages[room] if pid != player_id]
            self.confirmed[room] = False

    def room_members(self, room):
        return [p for p in self.players.values() if p.room == room]

    # ----- start / deal ----------------------------------------------------
    def start_game(self, config):
        self.config = config
        ids = list(self.players.keys())
        if len(ids) < 2:
            raise ValueError("Need at least 2 players to start.")

        deck = build_deck(len(ids), config["enabled_optional_ids"])
        random.shuffle(ids)
        for pid, role in zip(ids, deck):
            self.players[pid].role = role

        # Split players as evenly as possible into rooms A and B.
        random.shuffle(ids)
        half = len(ids) // 2
        for i, pid in enumerate(ids):
            self.players[pid].room = "A" if i < half else "B"

        self.phase = DEAL
        self.current_round = 0
        self.leaders = {"A": None, "B": None}
        self.result = None

    # ----- rounds ----------------------------------------------------------
    def begin_round(self, round_no=None):
        """Start (or restart) a round's timer. Returns the round number."""
        if round_no is None:
            round_no = self.current_round + 1 if self.current_round else 1
        self.current_round = round_no
        self.prune_leaders()
        self.phase = ROUND
        self.confirmed = {"A": False, "B": False}
        self.hostages = {"A": [], "B": []}
        duration = self.config["round_durations"][round_no - 1]
        self.paused = False
        self.remaining_ms = None
        self.round_ends_at = now_ms() + duration * 1000
        return round_no

    def pause(self):
        if self.phase == ROUND and not self.paused and self.round_ends_at:
            self.remaining_ms = max(0, self.round_ends_at - now_ms())
            self.paused = True

    def resume(self):
        if self.phase == ROUND and self.paused:
            self.round_ends_at = now_ms() + (self.remaining_ms or 0)
            self.paused = False
            self.remaining_ms = None

    def adjust_time(self, delta_seconds):
        """Add/subtract time to the current round."""
        if self.phase != ROUND:
            return
        if self.paused:
            self.remaining_ms = max(0, (self.remaining_ms or 0) + delta_seconds * 1000)
        elif self.round_ends_at:
            self.round_ends_at = max(now_ms(), self.round_ends_at + delta_seconds * 1000)

    def end_round(self):
        """Timer expired (or leader/admin skipped): move to hostage selection."""
        if self.phase != ROUND:
            return
        self.phase = HOSTAGE_SELECT
        self.paused = False
        self.remaining_ms = None
        self.hostages = {"A": [], "B": []}
        self.confirmed = {"A": False, "B": False}

    # ----- leadership ------------------------------------------------------
    def set_leader(self, room, player_id):
        p = self.get(player_id)
        if p and p.room == room:
            self.leaders[room] = player_id

    def clear_leader(self, room):
        self.leaders[room] = None

    # ----- hostage selection ----------------------------------------------
    def required_hostages(self, room):
        counts = self.config.get("hostage_counts") or [1]
        idx = max(0, min(self.current_round - 1, len(counts) - 1))
        return min(counts[idx], len(self.room_members(room)))

    def prune_leaders(self):
        """Drop a leader who is gone or who now sits in the other room."""
        for room in ("A", "B"):
            pid = self.leaders.get(room)
            p = self.get(pid) if pid else None
            if not p or p.room != room:
                self.leaders[room] = None

    def admin_exchange(self, from_a, from_b):
        """Admin picks the hostages for both rooms and the swap runs now.

        Returns "endgame" or "next". Raises ValueError for a bad request.
        """
        if self.phase != HOSTAGE_SELECT:
            raise ValueError("The hostage exchange is possible only after a round ends.")
        from_a = list(dict.fromkeys(from_a or []))
        from_b = list(dict.fromkeys(from_b or []))
        for room, ids in (("A", from_a), ("B", from_b)):
            for pid in ids:
                p = self.get(pid)
                if not p or p.room != room:
                    raise ValueError(f"A chosen player is not in Room {room}.")
        if len(from_a) != len(from_b):
            raise ValueError("Both rooms must send the same number of hostages.")
        self.hostages = {"A": from_a, "B": from_b}
        return self.apply_swap()

    def set_hostages(self, room, player_ids):
        valid = {p.id for p in self.room_members(room)}
        self.hostages[room] = [pid for pid in player_ids if pid in valid]
        self.confirmed[room] = False

    def confirm_hostages(self, room):
        if len(self.hostages[room]) == self.required_hostages(room):
            self.confirmed[room] = True
            return True
        return False

    def both_confirmed(self):
        return self.confirmed["A"] and self.confirmed["B"]

    def apply_swap(self):
        """Move confirmed hostages to the opposite room, then advance phase.

        Returns "endgame" if that was the final round, else "next".
        """
        going_to_b = list(self.hostages["A"])
        going_to_a = list(self.hostages["B"])
        for pid in going_to_b:
            if self.get(pid):
                self.get(pid).room = "B"
        for pid in going_to_a:
            if self.get(pid):
                self.get(pid).room = "A"
        self.hostages = {"A": [], "B": []}
        self.confirmed = {"A": False, "B": False}
        self.prune_leaders()

        if self.current_round >= self.config["num_rounds"]:
            self.compute_result()
            return "endgame"
        self.phase = DEAL  # between-round "ready" screen
        return "next"

    # ----- endgame ---------------------------------------------------------
    def _find_role(self, role_id):
        for p in self.players.values():
            if p.role and p.role["id"] == role_id:
                return p
        return None

    def compute_result(self):
        president = self._find_role("president")
        bomber = self._find_role("bomber")
        pres_room = president.room if president else None
        bomb_room = bomber.room if bomber else None
        same = pres_room is not None and pres_room == bomb_room
        self.phase = ENDGAME
        self.result = {
            "winner": "red" if same else "blue",
            "same_room": same,
            "president": {"name": president.name, "room": pres_room} if president else None,
            "bomber": {"name": bomber.name, "room": bomb_room} if bomber else None,
        }

    # ----- views -----------------------------------------------------------
    def timer_state(self):
        return {
            "round_ends_at": self.round_ends_at,
            "server_now": now_ms(),
            "paused": self.paused,
            "remaining_ms": self.remaining_ms,
        }

    def _room_member_views(self):
        room_members = {"A": [], "B": []}
        for player in self.players.values():
            if player.room in room_members:
                room_members[player.room].append(
                    {"id": player.id, "name": player.name, "connected": player.connected}
                )
        return room_members

    def player_views(self, player_ids=None):
        """Build all phone views with one pass over the room rosters."""
        room_members = self._room_member_views()
        player_ids = self.players if player_ids is None else set(player_ids)
        return {
            player_id: self._player_view(player_id, room_members)
            for player_id in player_ids
            if player_id in self.players
        }

    def player_view(self, player_id):
        """What a single phone should render."""
        return self._player_view(player_id, self._room_member_views())

    def _player_view(self, player_id, room_members):
        me = self.get(player_id)
        if not me:
            return {"phase": "GONE"}
        view = {
            "phase": self.phase,
            "me": me.to_dict(),
            "current_round": self.current_round,
            "num_rounds": self.config["num_rounds"],
            "timer": self.timer_state(),
        }
        if self.phase in (DEAL, ROUND, HOSTAGE_SELECT) and me.room:
            room = me.room
            view["room"] = room
            view["room_members"] = room_members[room]
            if self.phase in (ROUND, HOSTAGE_SELECT):
                view["leader"] = self.leaders[room]
                leader_p = self.get(self.leaders[room]) if self.leaders[room] else None
                view["leader_online"] = bool(leader_p and leader_p.connected)
                view["is_leader"] = self.leaders[room] == player_id
                view["required_hostages"] = self.required_hostages(room)
                view["hostages"] = self.hostages[room]
                view["confirmed"] = self.confirmed[room]
                view["other_confirmed"] = self.confirmed["B" if room == "A" else "A"]
        if self.phase == ENDGAME:
            view["result"] = self.result
            if self.config.get("reveal_all_roles"):
                view["all_roles"] = self._roster_with_roles()
        return view

    def _roster_with_roles(self):
        return [
            {"name": p.name, "room": p.room, "role": p.role}
            for p in self.players.values()
        ]

    def admin_view(self):
        return {
            "phase": self.phase,
            "current_round": self.current_round,
            "config": self.config,
            "timer": self.timer_state(),
            "leaders": self.leaders,
            "confirmed": self.confirmed,
            "hostages": self.hostages,
            "required": {
                "A": self.required_hostages("A") if self.phase in (ROUND, HOSTAGE_SELECT) else 0,
                "B": self.required_hostages("B") if self.phase in (ROUND, HOSTAGE_SELECT) else 0,
            },
            "result": self.result,
            # NOTE: player roles are deliberately NOT included here. The admin
            # laptop is visible to bystanders, so cards must stay secret. Only
            # the endgame `result` (President/Bomber) is revealed, after play.
            "players": [
                {
                    "id": p.id,
                    "name": p.name,
                    "room": p.room,
                    "connected": p.connected,
                    "is_leader": p.id in self.leaders.values(),
                }
                for p in self.players.values()
            ],
            "rooms": {
                "A": len(self.room_members("A")),
                "B": len(self.room_members("B")),
            },
        }

    # ----- snapshot (restart recovery) ------------------------------------
    def to_snapshot(self):
        return {
            "phase": self.phase,
            "current_round": self.current_round,
            "config": self.config,
            "leaders": self.leaders,
            "round_ends_at": self.round_ends_at,
            "paused": self.paused,
            "remaining_ms": self.remaining_ms,
            "hostages": self.hostages,
            "confirmed": self.confirmed,
            "result": self.result,
            "players": [p.to_dict() for p in self.players.values()],
        }

    def load_snapshot(self, data):
        self.reset(full=True)
        self.phase = data.get("phase", LOBBY)
        self.current_round = data.get("current_round", 0)
        self.config = data.get("config", default_config())
        self.leaders = data.get("leaders", {"A": None, "B": None})
        self.round_ends_at = data.get("round_ends_at")
        self.paused = data.get("paused", False)
        self.remaining_ms = data.get("remaining_ms")
        self.hostages = data.get("hostages", {"A": [], "B": []})
        self.confirmed = data.get("confirmed", {"A": False, "B": False})
        self.result = data.get("result")
        for pd in data.get("players", []):
            p = Player(pd["name"])
            p.id = pd["id"]
            p.room = pd.get("room")
            p.role = pd.get("role")
            p.connected = False
            self.players[p.id] = p
