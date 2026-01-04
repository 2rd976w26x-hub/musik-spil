from flask import Flask, request, jsonify, send_from_directory
import random, string, time, json
from copy import deepcopy
import os

app = Flask(__name__, static_folder="web", static_url_path="")
PORT = 8787
VERSION = "v1.4.28-github-ready"
rooms = {}

# Simple in-memory statistics (reset on deploy/restart)
STATS = {
    "unique_devices": set(),   # device_id values we've seen
    "rooms_created": 0,
    "games_completed": 0,
}

def gen_code(n=4):
    return "".join(random.choices(string.ascii_uppercase, k=n))

def gen_id():
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=8))

def now():
    return time.time()

def load_songsets():
    """Load all song json files from web/. Supports songs.json and songs_*.json."""
    songsets = {}
    # Default
    try:
        with open("web/songs.json", encoding="utf-8") as f:
            songsets["Standard"] = json.load(f)
    except Exception:
        songsets["Standard"] = []
    # Extra categories
    import glob, os
    for path in glob.glob("web/songs_*.json"):
        name = os.path.basename(path)
        # songs_Danske 1960 til 2025.json -> Danske 1960 til 2025
        cat = name[len("songs_"):-len(".json")]
        cat = cat.replace("_", " ").strip()
        if not cat:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                songsets[cat] = json.load(f)
        except Exception:
            pass

    return songsets

SONGSETS = load_songsets()

def get_songs_for_category(category: str):
    if not category:
        category = "Standard"
    return SONGSETS.get(category) or SONGSETS.get("Standard") or []
def points_for_guess(guess: int, correct: int) -> int:
    d = abs(int(guess) - int(correct))
    return 3 if d == 0 else 2 if d == 1 else 1 if d == 2 else 0

def dj_id(room):
    if not room or not room.get("players"):
        return None
    try:
        return room["players"][room["dj_index"]]["id"]
    except Exception:
        return None

def dj_name(room):
    if not room or not room.get("players"):
        return None
    try:
        return room["players"][room["dj_index"]]["name"]
    except Exception:
        return None

def all_non_dj_have_guessed(room) -> bool:
    if not room or room.get("status") != "round":
        return False
    did = dj_id(room)
    if not did:
        return False
    guesses = room.get("guesses", {})
    for p in room.get("players", []):
        if p["id"] == did:
            continue
        if p["id"] not in guesses:
            return False
    return len(room.get("players", [])) >= 2

def _players_by_id(room):
    return {p["id"]: p.get("name","") for p in room.get("players", [])}

def record_round_history(room):
    # Create a snapshot for history (song + guesses + points + dj + timestamp)
    players = _players_by_id(room)
    did = dj_id(room)
    song = deepcopy(room.get("current_song")) if room.get("current_song") else None

    guesses_named = []
    for pid, year in (room.get("guesses") or {}).items():
        guesses_named.append({
            "player_id": pid,
            "player_name": players.get(pid, pid),
            "guess_year": year,
            "points": (room.get("last_round_points") or {}).get(pid, 0)
        })
    # Sort by name for readability
    guesses_named.sort(key=lambda x: (x.get("player_name") or ""))

    entry = {
        "round_number": int(room.get("round_index", 0)) + 1,
        "ended_at": int(now()),
        "dj_id": did,
        "dj_name": dj_name(room),
        "song": song,
        "guesses": guesses_named
    }
    room.setdefault("history", []).append(entry)

def end_round(room):
    correct = int(room["current_song"]["year"])
    last_points = {}

    for p in room["players"]:
        pid = p["id"]
        g = room["guesses"].get(pid)
        if g is None:
            last_points[pid] = 0
            continue
        pts = points_for_guess(g, correct)
        last_points[pid] = pts
        room["scores"][pid] += pts

    room["last_round_points"] = last_points

    # NEW: store history snapshot before switching view / wiping things later
    record_round_history(room)

    room["status"] = "round_result"
    room["round_started_at"] = None

def end_round_if_needed(room):
    if not room:
        return

    if all_non_dj_have_guessed(room):
        end_round(room)
        return

    started_at = room.get("round_started_at")
    if not started_at:
        return
    if now() - started_at < room["timer_seconds"]:
        return

    end_round(room)

@app.route("/")
def index():
    return send_from_directory("web", "index.html")

@app.route("/<path:path>")
def files(path):
    return send_from_directory("web", path)

@app.route("/api", methods=["POST"])
def api():
    data = request.json or {}
    action = data.get("action")

    # Best-effort device identifier sent from the client (stored in localStorage).
    # Used for simple stats + to prevent multiple joins per device in the same room.
    device_id = (data.get("device_id") or "").strip()[:64]
    if device_id:
        STATS["unique_devices"].add(device_id)

    if action == "version":
        return jsonify({"version": VERSION})

    if action == "categories":
        # Categories are based solely on the uploaded JSON songset files that exist on the server.
        # (One category per JSON songset file, including the default songs.json)
        return jsonify({
            "ok": True,
            "categories": sorted(list(SONGSETS.keys()))
        })

    if action == "create_room":
        room = gen_code()
        pid = gen_id()
        STATS["rooms_created"] += 1
        rooms[room] = {
            "room_code": room,
            "players": [{"id": pid, "name": data.get("name","") or "Spiller", "device_id": device_id or None}],
            "host_id": pid,
            "started": False,
            "round_index": 0,
            "rounds_total": int(data.get("rounds", 10)),
            "dj_index": 0,
            "current_song": None,
            "category": data.get("category") or "Standard",
            "unused_songs": get_songs_for_category(data.get("category") or "Standard").copy(),
            "guesses": {},
            "scores": {pid: 0},
            "last_round_points": {},
            "history": [],
            "timer_seconds": int(data.get("timer", 20)),
            "round_started_at": None,
            "status": "lobby",
            "created_at": int(time.time()),
            "completed_counted": False
        }
        return jsonify({"room": room, "player": {"id": pid}})

    if action == "join":
        room = rooms.get(data.get("room"))
        if not room:
            return jsonify({"error": "room_not_found"}), 400
        # Enforce: only 1 join per device in the same room.
        if device_id:
            for p in room["players"]:
                if (p.get("device_id") or "") == device_id:
                    # Treat as reconnect from the same device; don't create a duplicate player.
                    if data.get("name"):
                        p["name"] = data.get("name","") or p.get("name")
                    return jsonify({"player": {"id": p["id"], "reconnected": True}})

        pid = gen_id()
        room["players"].append({"id": pid, "name": data.get("name","") or "Spiller", "device_id": device_id or None})
        room["scores"][pid] = 0
        room["last_round_points"][pid] = 0
        return jsonify({"player": {"id": pid}})

    if action == "state":
        room = rooms.get(data.get("room"))
        if not room:
            return jsonify({"error": "room_not_found"}), 400
        end_round_if_needed(room)
        room['available_categories'] = sorted(SONGSETS.keys())
        return jsonify(room)

    if action == "start_game":
        room = rooms.get(data.get("room"))
        if not room:
            return jsonify({"error": "room_not_found"}), 400
        if not room["unused_songs"]:
            room["unused_songs"] = get_songs_for_category(room.get("category")).copy()

        room["started"] = True
        room["status"] = "round"
        room["round_index"] = 0
        room["dj_index"] = 0
        room["guesses"] = {}
        room["last_round_points"] = {}
        room["history"] = []
        room["round_started_at"] = None
        room["current_song"] = room["unused_songs"].pop(random.randrange(len(room["unused_songs"])))
        return jsonify({"ok": True})

    if action == "start_timer":
        room = rooms.get(data.get("room"))
        if not room:
            return jsonify({"error": "room_not_found"}), 400
        # only allow when a round is active
        if not room.get("started") or room.get("status") != "round" or not room.get("current_song"):
            return jsonify({"error": "no_active_round"}), 400
        # only DJ can start timer
        pid = data.get("player")
        try:
            dj = room["players"][room.get("dj_index", 0)]
        except Exception:
            dj = None
        if dj and pid and pid != dj.get("id"):
            return jsonify({"error": "not_dj"}), 400
        started_at = now()
        room["round_started_at"] = started_at
        return jsonify({"ok": True, "round_started_at": started_at})

    if action == "skip_song":
        room = rooms.get(data.get("room"))
        if not room:
            return jsonify({"error": "room_not_found"}), 400

        # only allow when a round is active
        if not room.get("started") or room.get("status") != "round" or not room.get("current_song"):
            return jsonify({"error": "no_active_round"}), 400

        # only DJ can skip
        pid = data.get("player")
        try:
            dj = room["players"][room.get("dj_index", 0)]
        except Exception:
            dj = None
        if dj and pid and pid != dj.get("id"):
            return jsonify({"error": "not_dj"}), 400

        # draw a new song from the SAME category
        cat = room.get("current_song", {}).get("category")
        # ensure pool exists
        unused = room.get("unused_songs")
        if unused is None:
            unused = []
            room["unused_songs"] = unused
        if not unused:
            room["unused_songs"] = songs.copy()
            random.shuffle(room["unused_songs"])
            unused = room["unused_songs"]

        picked = None
        # try a handful of draws to find matching category
        for _ in range(len(unused) + 5):
            if not unused:
                break
            candidate = unused.pop()
            if not cat or candidate.get("category") == cat:
                picked = candidate
                break
            # put back at front if wrong category
            unused.insert(0, candidate)

        if not picked:
            # fallback: pick any song
            if unused:
                picked = unused.pop()
            else:
                picked = random.choice(songs)

        room["current_song"] = picked
        room["guesses"] = {}
        room["last_round_points"] = {}
        room["round_started_at"] = None

        return jsonify(room)

    if action == "submit_guess":
        room = rooms.get(data.get("room"))
        if not room:
            return jsonify({"error": "room_not_found"}), 400

        year = data.get("year")
        try:
            year = int(year)
        except Exception:
            return jsonify({"error": "invalid_year"}), 400

        pid = data.get("player")
        if not pid:
            return jsonify({"error": "missing_player"}), 400

        if pid == dj_id(room):
            return jsonify({"error": "dj_cannot_guess"}), 400

        if pid in room["guesses"]:
            return jsonify({"error": "already_guessed"}), 400

        room["guesses"][pid] = year

        if all_non_dj_have_guessed(room):
            end_round(room)

        return jsonify({"ok": True})

    if action == "next_round":
        room = rooms.get(data.get("room"))
        if not room:
            return jsonify({"error": "room_not_found"}), 400

        room["round_index"] += 1
        if room["rounds_total"] and room["round_index"] >= room["rounds_total"]:
            room["status"] = "game_over"
            if not room.get("_completed_counted"):
                room["_completed_counted"] = True
                STATS["games_completed"] += 1
            return jsonify({"ok": True})

        if not room["unused_songs"]:
            room["unused_songs"] = get_songs_for_category(room.get("category")).copy()

        room["dj_index"] = (room["dj_index"] + 1) % len(room["players"])
        room["guesses"] = {}
        room["last_round_points"] = {}
        room["round_started_at"] = None
        room["status"] = "round"
        room["current_song"] = room["unused_songs"].pop(random.randrange(len(room["unused_songs"])))
        return jsonify({"ok": True})

    if action == "reset_game":
        room = rooms.get(data.get("room"))
        if not room:
            return jsonify({"error": "room_not_found"}), 400
        for p in room["players"]:
            room["scores"][p["id"]] = 0
        room["status"] = "lobby"
        room["started"] = False
        room["round_index"] = 0
        room["round_started_at"] = None
        room["unused_songs"] = get_songs_for_category(room.get("category")).copy()
        room["guesses"] = {}
        room["last_round_points"] = {}
        room["history"] = []
        return jsonify({"ok": True})
    if action == "categories":
        cats = list(SONGSETS.keys())
        def sort_key(c: str):
            if c == "Standard":
                return (0, 0, c)
            # decade numbers (e.g. "1990") before other text categories
            if c.isdigit() and len(c) == 4:
                return (1, int(c), c)
            return (2, 0, c)
        return jsonify({"categories": sorted(cats, key=sort_key)})

    if action == "set_category":
        room = rooms.get(data.get("room"))
        if not room:
            return jsonify({"error": "room_not_found"}), 400
        if room.get("started"):
            return jsonify({"error": "already_started"}), 400
        pid = data.get("player")
        if pid != room.get("host_id"):
            return jsonify({"error": "not_host"}), 400
        cat = data.get("category") or "Standard"
        if cat not in SONGSETS:
            return jsonify({"error": "bad_category"}), 400
        room["category"] = cat
        room["unused_songs"] = get_songs_for_category(cat).copy()
        room["current_song"] = None
        room["guesses"] = {}
        room["last_round_points"] = {}
        return jsonify({"ok": True})

    if action == "leave_room":
        room_code = data.get("room")
        pid = data.get("player")
        room = rooms.get(room_code)
        if not room:
            return jsonify({"ok": True})

        room["players"] = [p for p in room.get("players", []) if p.get("id") != pid]

        room.get("scores", {}).pop(pid, None)
        room.get("guesses", {}).pop(pid, None)
        room.get("last_round_points", {}).pop(pid, None)

        if not room["players"]:
            rooms.pop(room_code, None)
            return jsonify({"ok": True})

        if room.get("host_id") == pid:
            room["host_id"] = room["players"][0]["id"]

        if room.get("dj_index", 0) >= len(room["players"]):
            room["dj_index"] = 0

        if room.get("started") and len(room["players"]) < 2:
            room["status"] = "lobby"
            room["started"] = False
            room["round_started_at"] = None
            room["current_song"] = None
            room["guesses"] = {}
            room["last_round_points"] = {}
        return jsonify({"ok": True})

    return jsonify({"error": "unknown_action"}), 400


@app.route("/stats")
def stats():
    # Note: all of this is in-memory, resets on deploy/restart.
    active_rooms = []
    for code, r in rooms.items():
        active_rooms.append({
            "room": code,
            "players": len(r.get("players") or []),
            "status": r.get("status"),
            "current_round": r.get("current_round"),
            "rounds_total": r.get("rounds_total"),
            "category": r.get("category"),
            "dj_mode": bool(r.get("dj_mode")),
        })

    return jsonify({
        "version": VERSION,
        "unique_devices": len(STATS["unique_devices"]),
        "rooms_created": STATS["rooms_created"],
        "games_completed": STATS["games_completed"],
        "active_rooms": active_rooms,
        "active_rooms_count": len(active_rooms),
    })


@app.route("/admin")
def admin_page():
    # Simple built-in dashboard (no auth) — handy for quick checks.
    return """<!doctype html>
<html lang=\"da\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width,initial-scale=1\" />
  <title>Piratwhist — Admin</title>
  <style>
    body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:18px;}
    .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin:12px 0 18px;}
    .card{border:1px solid #e5e7eb;border-radius:12px;padding:12px;}
    table{border-collapse:collapse;width:100%;}
    th,td{border-bottom:1px solid #eee;padding:8px;text-align:left;}
    th{font-size:12px;opacity:.8;}
    .muted{opacity:.7;font-size:12px;}
  </style>
</head>
<body>
  <h1>Piratwhist — Admin</h1>
  <div class=\"muted\">In-memory stats (nulstilles ved deploy/restart). Opdaterer hvert 2. sekund.</div>

  <div class=\"cards\">
    <div class=\"card\"><div class=\"muted\">Version</div><div id=\"v\">…</div></div>
    <div class=\"card\"><div class=\"muted\">Unikke enheder</div><div id=\"u\">…</div></div>
    <div class=\"card\"><div class=\"muted\">Rooms oprettet</div><div id=\"rc\">…</div></div>
    <div class=\"card\"><div class=\"muted\">Spil gennemført</div><div id=\"gc\">…</div></div>
    <div class=\"card\"><div class=\"muted\">Aktive rooms</div><div id=\"ar\">…</div></div>
  </div>

  <h2>Aktive rooms</h2>
  <table>
    <thead>
      <tr>
        <th>Room</th><th>Spillere</th><th>Status</th><th>Runde</th><th>Kategori</th><th>DJ</th>
      </tr>
    </thead>
    <tbody id=\"rooms\"></tbody>
  </table>

<script>
async function tick(){
  const r = await fetch('/stats',{cache:'no-store'});
  const s = await r.json();
  document.getElementById('v').textContent = s.version;
  document.getElementById('u').textContent = s.unique_devices;
  document.getElementById('rc').textContent = s.rooms_created;
  document.getElementById('gc').textContent = s.games_completed;
  document.getElementById('ar').textContent = s.active_rooms_count;

  const tbody = document.getElementById('rooms');
  tbody.innerHTML = '';
  for(const room of (s.active_rooms||[])){
    const tr = document.createElement('tr');
    const roundTxt = (room.current_round && room.rounds_total) ? `${room.current_round}/${room.rounds_total}` : '';
    tr.innerHTML = `<td>${room.room}</td><td>${room.players}</td><td>${room.status||''}</td><td>${roundTxt}</td><td>${room.category||''}</td><td>${room.dj_mode?'ja':''}</td>`;
    tbody.appendChild(tr);
  }
}
tick();
setInterval(tick, 2000);
</script>
</body>
</html>"""
