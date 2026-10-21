"""Role catalog and deck builder for Two Rooms and a Boom.

A "role" is a card a player can hold. Every role belongs to a team:
  - "blue" wins if the President and Bomber end in DIFFERENT rooms
  - "red"  wins if the President and Bomber end in the SAME room
  - "grey" are neutral characters with their own private win condition (verbal)

The two pivotal cards (President, Bomber) are always dealt. Everything else is
either a generic team member (used as filler to size the deck) or an optional
special character the admin toggles on per game.
"""

import random

# Each role: id, name, team, description. `filler=True` marks the generic
# team members used to pad the deck up to the player count.
ROLE_CATALOG = {
    # --- always present ---
    "president": {
        "id": "president",
        "name": "President",
        "team": "blue",
        "description": "You are the Blue team's VIP. Blue wins if you are NOT in the same room as the Bomber when the game ends.",
    },
    "bomber": {
        "id": "bomber",
        "name": "Bomber",
        "team": "red",
        "description": "You are the Red team's VIP. Red wins if you ARE in the same room as the President when the game ends.",
    },
    # --- generic filler team members ---
    "blue_team": {
        "id": "blue_team",
        "name": "Blue Team",
        "team": "blue",
        "description": "You are on the Blue team. Keep the President away from the Bomber.",
        "filler": True,
    },
    "red_team": {
        "id": "red_team",
        "name": "Red Team",
        "team": "red",
        "description": "You are on the Red team. Get the President into the same room as the Bomber.",
        "filler": True,
    },
    # --- optional special characters (admin toggles these) ---
    "grey_neutral": {
        "id": "grey_neutral",
        "name": "Gambler (Grey)",
        "team": "grey",
        "description": "You are neutral. Before the reveal, privately guess which team will win — you win if you guessed right.",
        "optional": True,
    },
    "doctor": {
        "id": "doctor",
        "name": "Doctor (Blue)",
        "team": "blue",
        "description": "Blue team. If the President is in your room at the end, you may 'heal' them from the blast (house rule).",
        "optional": True,
    },
    "engineer": {
        "id": "engineer",
        "name": "Engineer (Red)",
        "team": "red",
        "description": "Red team. If the Bomber is in your room at the end, you may 'prime' a second bomb (house rule).",
        "optional": True,
    },
    "spy": {
        "id": "spy",
        "name": "Spy (Grey)",
        "team": "grey",
        "description": "You appear on one team but secretly score for the other. Reveal your true card to no one lightly.",
        "optional": True,
    },
}

ALWAYS_PRESENT = ["president", "bomber"]
FILLER_BY_TEAM = {"blue": "blue_team", "red": "red_team"}


def optional_roles():
    """Roles the admin can toggle on, in catalog order."""
    return [r for r in ROLE_CATALOG.values() if r.get("optional")]


def build_deck(player_count, enabled_optional_ids):
    """Return a shuffled list of exactly `player_count` role dicts.

    Guarantees President + Bomber are present, includes each enabled optional
    role once, then pads with generic Blue/Red filler so the two win-condition
    teams (blue vs red, ignoring grey) differ by at most one.
    """
    if player_count < 2:
        raise ValueError("Need at least 2 players (one President, one Bomber).")

    deck = [dict(ROLE_CATALOG[rid]) for rid in ALWAYS_PRESENT]
    for rid in enabled_optional_ids:
        if rid in ROLE_CATALOG and len(deck) < player_count:
            deck.append(dict(ROLE_CATALOG[rid]))

    # The leftover slots below are all filled with blue/red filler, so they
    # only divide evenly if (player_count - grey_count) is even. Make one
    # more player the Gambler (regardless of the optional toggle) whenever
    # that's not yet true -- an enabled grey card like Spy already counts.
    grey_count = sum(1 for r in deck if r["team"] == "grey")
    if (player_count - grey_count) % 2 == 1 and len(deck) < player_count:
        deck.append(dict(ROLE_CATALOG["grey_neutral"]))

    # Pad with filler, keeping blue/red counts balanced. Grey cards don't
    # affect the blue/red balance target.
    def count(team):
        return sum(1 for r in deck if r["team"] == team)

    while len(deck) < player_count:
        add_team = "blue" if count("blue") <= count("red") else "red"
        deck.append(dict(ROLE_CATALOG[FILLER_BY_TEAM[add_team]]))

    # If optionals overshot the player count, drop extras but never the VIPs.
    if len(deck) > player_count:
        keepers = [r for r in deck if r["id"] in ALWAYS_PRESENT]
        others = [r for r in deck if r["id"] not in ALWAYS_PRESENT]
        random.shuffle(others)
        deck = keepers + others[: player_count - len(keepers)]

    random.shuffle(deck)
    return deck
