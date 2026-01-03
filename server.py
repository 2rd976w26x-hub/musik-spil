from flask import Flask, request, jsonify, send_from_directory
import json
import random, string, time, json
from copy import deepcopy
import os

app = Flask(__name__, static_folder="web", static_url_path="")


@app.after_request
def add_no_cache_headers(resp):
    # Prevent stale JS/CSS after deploys (Render may return 304 from cache).
    if request.path in ('/', '/client.js', '/styles.css') or request.path.startswith('/covers/'):
        resp.headers['Cache-Control'] = 'no-store, max-age=0'
        resp.headers['Pragma'] = 'no-cache'
    return resp

PORT = 8787
VERSION = "1.4.23"
rooms = {}

# --- connection / presence tracking ---
# If a client stops polling (e.g. closes tab / loses connection), we remove them after a timeout.
PLAYER_TIMEOUT_SEC = 30

def _touch_player(room, player_id):
    if not room or not player_id:
        return None
    for p in room.get("players", []):
        if p.get("id") == player_id:
            p["last_seen"] = time.time()
            p["connected"] = True
            return p
    return None

def _remove_player(room, player_id):
    """Remove player by id and keep indices consistent. Returns True if removed."""
    if not room or not player_id:
        return False
    players = room.get("players", [])
    idx = next((i for i, p in enumerate(players) if p.get("id") == player_id), None)
    if idx is None:
        return False

    # Remove player
    players.pop(idx)

    # If room became empty, caller should delete it.
    if not players:
        return True

    # Fix host
    if room.get("host_id") == player_id:
        room["host_id"] = players[0]["id"]

    # Fix DJ index (keep pointing at same logical next player)
    dj_index = int(room.get("dj_index", 0) or 0)
    if idx < dj_index:
        dj_index -= 1
    elif idx == dj_index:
        # DJ left -> new DJ is whoever is now at same index (or 0)
        dj_index = dj_index % len(players)
        room["waiting_for_dj"] = True
        # if a round is ongoing, force round end so UI doesn't hang
        if room.get("status") == "round":
            room["status"] = "round_result"

    room["dj_index"] = dj_index

    # If game is running with fixed rounds_total, keep remaining rounds fair across remaining players
    if room.get("started") and room.get("rounds_total"):
        remaining = max(0, int(room["rounds_total"]) - int(room.get("round_index", 0)))
        n = len(players)
        if n > 0:
            remaining_fair = (remaining // n) * n
            room["rounds_total"] = int(room.get("round_index", 0)) + remaining_fair
            if room.get("round_index", 0) >= room["rounds_total"]:
                room["status"] = "game_over"
                room["started"] = False

    return True

def _cleanup_inactive(room):
    if not room:
        return
    now = time.time()
    stale = []
    for p in room.get("players", []):
        last = p.get("last_seen", now)
        if now - last > PLAYER_TIMEOUT_SEC:
            stale.append(p.get("id"))
    for pid in stale:
        _remove_player(room, pid)



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

    # Auto-generate decade categories from the Standard set (and keep any explicit categories intact).
    # This makes it possible to pick e.g. "1990" / "2000" and only get songs from that decade.
    decade_map = {}
    for s in songsets.get("Standard", []) or []:
        try:
            y = int(s.get("year"))
        except Exception:
            continue
        decade = (y // 10) * 10
        decade_map.setdefault(decade, []).append(s)

    for decade, songs in decade_map.items():
        # Two aliases for the same decade.
        key_plain = str(decade)          # e.g. "1990"
        key_dk = f"{decade}'erne"       # e.g. "1990'erne"
        songsets.setdefault(key_plain, songs)
        songsets.setdefault(key_dk, songs)

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
    # Be permissive: some clients/proxies may omit or alter the Content-Type header.
    # Using silent=True avoids raising and lets us respond with a clean JSON error.
    # Accept JSON even if client/proxy sends a non-JSON content-type.
    data = request.get_json(silent=True)
    if data is None:
        raw = (request.data or b'').strip()
        if raw:
            try:
                import json as _json
                data = _json.loads(raw.decode('utf-8'))
            except Exception:
                data = {}
        else:
            data = {}


    # Also accept form-encoded payloads (defensive fallback).
    if not data and request.form:
        data = request.form.to_dict(flat=True)
    action = data.get("action")

    # Common: touch player + cleanup inactive players on any request that includes a room code.
    room_code_common = data.get("room") or data.get("room_code")
    if room_code_common:
        room_common = rooms.get(room_code_common)
        if room_common:
            _cleanup_inactive(room_common)
            pid_common = data.get("player") or data.get("player_id")
            if pid_common:
                _touch_player(room_common, pid_common)
            if not room_common.get("players"):
                rooms.pop(room_code_common, None)


    if action == "version":
        # Keep both fields so old/new clients can read it.
        return jsonify({"ok": True, "version": VERSION})

    if action in ("create_room", "create"):
        room = gen_code()
        pid = gen_id()
        rooms[room] = {
            "room_code": room,
            "players": [{"id": pid, "name": data.get("name","") or "Spiller"}],
            "host_id": pid,
            "started": False,
            "round_index": 0,
            "rounds_total": int(data.get("rounds", 10)),
            "dj_index": 0,
            "current_song": None,
            "category": data.get("category") or "Standard",
            "unused_songs": get_songs_for_category(data.get("category") or "Standard").copy(),
            "guesses": {},
            "scores": {pid: 0}  # placeholder,
            "last_round_points": {},
            "history": [],
            "timer_seconds": int(data.get("timer", 20)),
            "round_started_at": None,
            "status": "lobby"
        }
        return jsonify({"ok": True, "room": room, "player": {"id": pid}})

    if action == "join":
        room = rooms.get(data.get("room"))
        if not room:
            return jsonify({"error": "room_not_found"}), 400
        pid = gen_id()
        room["players"].append({"id": pid, "name": data.get("name","") or "Spiller"})
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

        # Only host can start
        if data.get("player") != room.get("host_id"):
            return jsonify({"error": "only_host_can_start"}), 403

        # Rounds: ensure fairness (each player becomes DJ the same number of times)
        try:
            desired_rounds = int(data.get("rounds_total") or data.get("rounds") or room.get("rounds_total") or 10)
        except Exception:
            desired_rounds = int(room.get("rounds_total") or 10)

        n_players = max(1, len(room.get("players", [])))
        # round up to nearest multiple of players (minimum 1 full rotation)
        desired_rounds = max(n_players, desired_rounds)
        if desired_rounds % n_players != 0:
            desired_rounds = ((desired_rounds + n_players - 1) // n_players) * n_players
        room["rounds_total"] = desired_rounds

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

        # stop if we reached the selected number of rounds
        if room.get("rounds_total") and room.get("round_index", 0) >= int(room["rounds_total"]) - 1:
            room["status"] = "game_over"
            room["started"] = False
            return jsonify({"ok": True, "room": room})

        room["round_index"] += 1
        if room["rounds_total"] and room["round_index"] >= room["rounds_total"]:
            room["status"] = "game_over"
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

        removed = _remove_player(room, pid)
        room.get("scores", {}).pop(pid, None)
        room.get("guesses", {}).pop(pid, None)
        room.get("last_round_points", {}).pop(pid, None)

        if not room.get("players"):
            rooms.pop(room_code, None)

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