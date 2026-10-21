# Two Rooms and a Boom — local game server

A self-hosted, phone-friendly game master for *Two Rooms and a Boom*. Players join
from their phones over your Wi-Fi; your laptop deals secret cards, runs a synced
countdown, tracks rooms/leaders/hostages, and reveals the winner.

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python server.py
```

The terminal prints the player URL, the admin URL, and the **admin PIN**, e.g.:

```
Players open:  http://192.168.1.42:5001/
Admin panel:   http://192.168.1.42:5001/admin
Admin PIN:     4821
```

- Phones and laptop must be on the **same Wi-Fi**. On first run, allow the macOS
  firewall prompt (or System Settings → Network → Firewall → allow incoming Python).
- Players open the player URL and enter a name — no password. A signed cookie keeps
  them logged in across refreshes.
- You open `/admin` on the laptop and enter the PIN.

### Environment overrides
- `TRAB_PORT` — port (default `5001`)
- `TRAB_ADMIN_PIN` — fixed admin PIN (default: random 4 digits each boot when run with Python)
- `TRAB_SECRET` — cookie signing secret (default: random each boot; set it to keep
  player logins valid across restarts)
- `TRAB_PUBLIC_HOST` — LAN host name or IP to print in the server URLs. Set this
   when the server runs in Docker.

## Run on a Raspberry Pi with Docker

Use a Raspberry Pi with Docker installed. The official Python base image supports
ARM platforms, so build the image on the Pi or use Docker Buildx for another target.

```bash
docker build -t two-rooms-and-a-boom .
docker run -d \
   --name two-rooms-and-a-boom \
   --restart unless-stopped \
   -p 5001:5001 \
   -e TRAB_ADMIN_PIN="set-a-private-pin" \
   -e TRAB_PUBLIC_HOST="$(hostname -I | awk '{print $1}')" \
   -v trab-data:/app/data \
   two-rooms-and-a-boom
```

Set `TRAB_ADMIN_PIN` in the Docker launch page's environment or secret settings.
Use that same value on the admin login page. Docker requires this setting and
does not print the PIN in its logs. Keep it private. The named volume keeps the
game snapshot across container restarts. Set `TRAB_SECRET` as an environment
variable to keep player logins valid across restarts. Keep the players and Pi on
the same Wi-Fi network.

## How a game runs

1. **Lobby** — players join; you see the roster and can kick anyone.
2. **Setup & deal** — pick rounds, per-round durations, hostages per round, and which
   optional characters to include, then **Deal cards & assign rooms**. Each phone shows
   its room and a tap-to-reveal secret card.
3. **Rounds** — press **Begin Round N**. A synced countdown runs on every phone. In each
   room a player taps **I'll be leader**. You can pause/resume, add/remove time, or end
   the round early.
4. **Hostage exchange** — when time runs out, each room's leader picks the required number
   of hostages; when both lock in, the app swaps those players and shows the next-round
   ready screen.
5. **Result** — after the final round the app checks whether the **President** and
   **Bomber** ended in the same room: same → **Red wins**, apart → **Blue wins**. Optionally
   reveals every card.

## Files

| File | Role |
|------|------|
| `server.py` | Flask + Socket.IO server, routes, event handlers, authoritative timer |
| `game.py` | Game state machine, rounds, hostages, endgame, view builders |
| `roles.py` | Role catalog + balanced deck builder |
| `templates/` | login, player, admin pages |
| `static/` | mobile-first CSS + player/admin JS + vendored Socket.IO client |

Adding a new optional character is a one-entry addition to `ROLE_CATALOG` in `roles.py`
(mark it `"optional": True`).
