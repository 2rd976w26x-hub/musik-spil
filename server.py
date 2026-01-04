from flask import Flask, request, jsonify, send_from_directory
import random, string, time, json
from copy import deepcopy
import os
import hashlib
from typing import Optional
import uuid

# Optional Postgres persistence (game runs fine without it)
try:
    import psycopg2
    import psycopg2.extras
except Exception:
    psycopg2 = None

app = Flask(__name__, static_folder="web", static_url_path="")
PORT = 8787
VERSION = "v1.4.31-github-ready"
rooms = {}

# Simple in-memory statistics (reset on deploy/restart)
STATS = {
    "visits": 0,
    "unique_devices": set(),   # device_id values we've seen
    "rooms_created": 0,
    "games_completed": 0,
}

# -----------------------------
# Persistence (optional)
# -----------------------------

DB_URL = os.getenv("DATABASE_URL") or os.getenv("INTERNAL_DATABASE_URL")
DB_DISABLED = os.getenv("DISABLE_DB", "").strip().lower() in {"1", "true", "yes"}
DB_AVAILABLE = bool(DB_URL) and not DB_DISABLED and psycopg2 is not None

def _hash_device(device_id: str) -> str:
    """Store only a one-way hash of device id in the database (privacy)."""
    if not device_id:
        return ""
    return hashlib.sha256(device_id.encode("utf-8")).hexdigest()


class Db:
    def __init__(self, url: Optional[str]):
        self.url = url

    def is_enabled(self) -> bool:
        return bool(self.url) and not DB_DISABLED and psycopg2 is not None


    @property
    def enabled(self):
        return self.is_enabled()

    def conn(self):
        # short connections are OK for Render Postgres; keep it simple
        return psycopg2.connect(self.url, sslmode=os.getenv("PGSSLMODE", "prefer"))

    def init(self):
        if not self.is_enabled():
            return
        with self.conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS daily_metrics (
                        day DATE PRIMARY KEY,
                        visits INTEGER NOT NULL DEFAULT 0,
                        rooms_created INTEGER NOT NULL DEFAULT 0,
                        games_completed INTEGER NOT NULL DEFAULT 0
                    );
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS devices (
                        device_hash TEXT PRIMARY KEY,
                        first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS game_history (
                        id TEXT PRIMARY KEY,
                        room_code TEXT,
                        started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        ended_at TIMESTAMPTZ,
                        category TEXT,
                        rounds_total INTEGER,
                        players JSONB,
                        history JSONB
                    );
                    """
                )
            c.commit()

    def inc_metric(self, field: str, amount: int = 1):
        if not self.is_enabled():
            return
        if field not in {"visits", "rooms_created", "games_completed"}:
            return
        with self.conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO daily_metrics (day, {field})
                    VALUES (CURRENT_DATE, %s)
                    ON CONFLICT (day) DO UPDATE
                    SET {field} = daily_metrics.{field} + EXCLUDED.{field};
                    """,
                    (amount,),
                )
            c.commit()

    def upsert_device(self, device_id: str) -> bool:
        """Return True if it's the first time we've seen this device (in DB)."""
        if not self.is_enabled():
            return False
        h = _hash_device(device_id)
        if not h:
            return False
        with self.conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO devices (device_hash)
                    VALUES (%s)
                    ON CONFLICT DO NOTHING;
                    """,
                    (h,),
                )
                inserted = cur.rowcount == 1
            c.commit()
        return inserted

    def save_game_end(self, game_id: str, room_code: str, room_obj: dict):
        if not self.is_enabled():
            return
        # Keep history compact but useful
        payload_players = room_obj.get("players")
        payload_hist = room_obj.get("history")
        category = room_obj.get("category")
        rounds_total = room_obj.get("rounds")
        started_at = room_obj.get("created_at")
        ended_at = now()
        # created_at is epoch seconds; convert in SQL
        with self.conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO game_history (id, room_code, started_at, ended_at, category, rounds_total, players, history)
                    VALUES (%s, %s, to_timestamp(%s), to_timestamp(%s), %s, %s, %s::jsonb, %s::jsonb)
                    ON CONFLICT (id) DO UPDATE
                    SET ended_at = EXCLUDED.ended_at,
                        category = EXCLUDED.category,
                        rounds_total = EXCLUDED.rounds_total,
                        players = EXCLUDED.players,
                        history = EXCLUDED.history;
                    """,
                    (
                        game_id,
                        room_code,
                        float(started_at or now()),
                        float(ended_at),
                        category,
                        int(rounds_total or 0),
                        json.dumps(payload_players or {}),
                        json.dumps(payload_hist or []),
                    ),
                )
            c.commit()

    def admin_summary(self, days: int = 30) -> dict:
        if not self.is_enabled():
            return {}
        with self.conn() as c:
            with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT day, visits, rooms_created, games_completed
                    FROM daily_metrics
                    WHERE day >= CURRENT_DATE - %s::int
                    ORDER BY day;
                    """,
                    (days,),
                )
                series = list(cur.fetchall())
                cur.execute("SELECT COUNT(*) AS unique_devices FROM devices;")
                unique_devices = cur.fetchone()["unique_devices"]
                cur.execute(
                    """
                    SELECT COUNT(*) AS games_total,
                           COUNT(*) FILTER (WHERE ended_at IS NOT NULL) AS games_finished
                    FROM game_history;
                    """
                )
                games_meta = cur.fetchone()
            return {
                "series": series,
                "unique_devices": int(unique_devices or 0),
                "games_total": int(games_meta.get("games_total") or 0),
                "games_finished": int(games_meta.get("games_finished") or 0),
            }

    def list_games(self, limit: int = 50) -> list:
        if not self.is_enabled():
            return []
        limit = max(1, min(int(limit or 50), 200))
        with self.conn() as c:
            with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id, room_code, started_at, ended_at, category, rounds_total
                    FROM game_history
                    ORDER BY started_at DESC
                    LIMIT %s;
                    """,
                    (limit,),
                )
                return list(cur.fetchall())

    def get_game(self, game_id: str) -> Optional[dict]:
        if not self.is_enabled():
            return None
        with self.conn() as c:
            with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id, room_code, started_at, ended_at, category, rounds_total, players, history
                    FROM game_history
                    WHERE id = %s;
                    """,
                    (game_id,),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    def bump_daily(self, field: str, amount: int = 1):
        """Compatibility alias used by older code."""
        return self.inc_metric(field, amount)
    def register_device(self, device_id: str):
        """Compatibility alias used by older code."""
        return self.upsert_device(device_id)
    def save_game(
        self,
        game_id: str,
        *,
        room_code: str,
        category: str,
        rounds_total: int,
        guessed_seconds: int,
        players: list,
        history: list,
        ended: bool = False,
    ) -> None:
        """Upsert game row. If ended=True, delegates to save_game_end.

        This keeps the game playable even when DB is optional and ensures the
        admin history views have a row to read later.
        """
        if not self.enabled:
            return
        if ended:
            # ensure a row exists, then update ended fields
            self._upsert_game_row(
                game_id,
                room_code=room_code,
                category=category,
                rounds_total=rounds_total,
                guessed_seconds=guessed_seconds,
                players=players,
                history=history,
            )
            self.save_game_end(game_id, players=players, history=history)
            return

        self._upsert_game_row(
            game_id,
            room_code=room_code,
            category=category,
            rounds_total=rounds_total,
            guessed_seconds=guessed_seconds,
            players=players,
            history=history,
        )

    def _upsert_game_row(
        self,
        game_id: str,
        *,
        room_code: str,
        category: str,
        rounds_total: int,
        guessed_seconds: int,
        players: list,
        history: list,
    ) -> None:
        """Internal helper to insert/update the main game row."""
        import json
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO game_history (
                    game_id, room_code, category, rounds_total, guessed_seconds, players_json, history_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (game_id) DO UPDATE SET
                    room_code = EXCLUDED.room_code,
                    category = EXCLUDED.category,
                    rounds_total = EXCLUDED.rounds_total,
                    guessed_seconds = EXCLUDED.guessed_seconds,
                    players_json = EXCLUDED.players_json,
                    history_json = EXCLUDED.history_json
                """,
                (
                    game_id,
                    room_code,
                    category,
                    int(rounds_total),
                    int(guessed_seconds),
                    json.dumps(players or []),
                    json.dumps(history or []),
                ),
            )

    def game_by_id(self, game_id: str):
        """Backward-compatible alias used by some admin routes."""
        return self.get_game(game_id)

    def daily_metrics(self, days: int = 30):
        """Return daily series for the last N days."""
        try:
            return self.admin_summary(days=days).get("series", [])
        except Exception:
            return []
    def recent_games(self, limit: int = 200):
        """Return recent finished games."""
        try:
            return self.list_games(limit=limit)
        except Exception:
            return []
    def game_details(self, game_id: int):
        """Return stored game record."""
        try:
            return self.get_game(game_id)
        except Exception:
            return None

DB = Db(DB_URL if DB_AVAILABLE else None)
try:
    DB.init()
except Exception as e:
    # If DB is misconfigured, keep the game running on in-memory mode.
    DB = Db(None)


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
    STATS["visits"] += 1
    DB.bump_daily("visits")
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
        # Store a one-way hash in the DB (no raw device_id persisted)
        device_hash = hashlib.sha256(device_id.encode("utf-8")).hexdigest()[:32]
        DB.register_device(device_hash)

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
        DB.bump_daily("rooms_created")
        rooms[room] = {
            "game_id": str(uuid.uuid4()),
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
            "completed_counted": False,
            "game_started_at": None,
            "game_ended_at": None
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

        # persist game start (optional)
        if not room.get("game_id"):
            room["game_id"] = str(uuid.uuid4())
        room["game_started_at"] = now()
        DB.save_game(
            game_id=room["game_id"],
            room_code=room.get("room_code") or data.get("room"),
            category=room.get("category"),
            rounds_total=int(room.get("rounds") or 0),
            players=room.get("players") or [],
            history=room.get("history") or [],
            started_at=room["game_started_at"],
            ended_at=None,
        )
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
                DB.bump_daily("games_completed")
                # Persist finished game (best-effort)
                room["game_ended_at"] = now()
                DB.save_game(
                    game_id=room.get("game_id") or str(uuid.uuid4()),
                    room_code=room.get("room_code"),
                    started_at=room.get("game_started_at"),
                    ended_at=room.get("game_ended_at"),
                    category=room.get("category"),
                    rounds_total=room.get("rounds_total"),
                    players=room.get("players"),
                    history=room.get("history"),
                )
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
    # In-memory "live" state + optional persisted aggregates.
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
        "db_enabled": DB.enabled,
        "unique_devices": len(STATS["unique_devices"]),
        "rooms_created": STATS["rooms_created"],
        "games_completed": STATS["games_completed"],
        "active_rooms": active_rooms,
        "active_rooms_count": len(active_rooms),
        "daily": DB.daily_metrics(days=30) if DB.enabled else [],
    })


@app.route("/admin")
def admin_page():
    # Built-in dashboard (no auth). Uses Postgres if available; otherwise falls back to in-memory.
    return """<!doctype html>
<html lang=\"da\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width,initial-scale=1\" />
  <title>Piratwhist — Admin</title>
  <style>
    body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:18px;}
    h1{margin:0 0 6px;}
    .muted{opacity:.7;font-size:12px;}
    .row{display:flex;gap:12px;flex-wrap:wrap;}
    .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin:12px 0 14px;}
    .card{border:1px solid #e5e7eb;border-radius:12px;padding:12px;background:#fff;}
    .kpi{font-size:22px;font-weight:700;}
    .tag{display:inline-block;border:1px solid #e5e7eb;border-radius:999px;padding:2px 8px;font-size:12px;opacity:.85;}
    table{border-collapse:collapse;width:100%;}
    th,td{border-bottom:1px solid #eee;padding:8px;text-align:left;vertical-align:top;}
    th{font-size:12px;opacity:.8;}
    a{color:inherit;}
    .chart{height:140px;}
  </style>
</head>
<body>
  <h1>Piratwhist — Admin</h1>
  <div class=\"muted\">Live (aktive rooms) + historik (spil pr. dag osv.) hvis DB er slået til.</div>

  <div class=\"cards\">
    <div class=\"card\"><div class=\"muted\">Version</div><div class=\"kpi\" id=\"v\">…</div></div>
    <div class=\"card\"><div class=\"muted\">DB</div><div class=\"kpi\" id=\"db\">…</div></div>
    <div class=\"card\"><div class=\"muted\">Unikke enheder (live)</div><div class=\"kpi\" id=\"u\">…</div></div>
    <div class=\"card\"><div class=\"muted\">Aktive rooms</div><div class=\"kpi\" id=\"ar\">…</div></div>
    <div class=\"card\"><div class=\"muted\">Spil gennemført (live)</div><div class=\"kpi\" id=\"gc\">…</div></div>
  </div>

  <div class=\"row\">
    <div class=\"card\" style=\"flex:1;min-width:320px;\">
      <div style=\"display:flex;justify-content:space-between;align-items:center;gap:8px\">
        <div>
          <div class=\"muted\">Spil pr. dag (30 dage)</div>
          <div id=\"chartHint\" class=\"muted\"></div>
        </div>
        <span class=\"tag\" id=\"chartTag\">…</span>
      </div>
      <svg id=\"chart\" class=\"chart\" viewBox=\"0 0 600 140\" preserveAspectRatio=\"none\"></svg>
    </div>
    <div class=\"card\" style=\"flex:1;min-width:320px;\">
      <div class=\"muted\">Rooms (aktive)</div>
      <table>
        <thead><tr><th>Room</th><th>Spillere</th><th>Status</th><th>Runde</th><th>Kategori</th><th>DJ</th></tr></thead>
        <tbody id=\"rooms\"></tbody>
      </table>
    </div>
  </div>

  <h2 style=\"margin-top:18px\">Seneste spil</h2>
  <div class=\"muted\">Klik et spil for at se historik (kun når DB er slået til).</div>
  <table style=\"margin-top:8px\">
    <thead><tr><th>Start</th><th>Room</th><th>Kategori</th><th>Runder</th><th>Spillere</th></tr></thead>
    <tbody id=\"games\"></tbody>
  </table>

<script>
function svgBarChart(svg, series){
  // series: [{label, value}]
  const w = 600, h = 140;
  const maxV = Math.max(1, ...series.map(s=>s.value));
  const pad = 6;
  const bw = (w - pad*2) / Math.max(1, series.length);
  let out = '';
  series.forEach((s,i)=>{
    const bh = Math.round((s.value/maxV) * (h-18));
    const x = pad + i*bw + 1;
    const y = h - bh - 12;
    const rw = Math.max(2, bw-2);
    out += `<rect x='${x}' y='${y}' width='${rw}' height='${bh}' rx='2' ry='2' fill='#111827' opacity='0.85'>`;
    out += `<title>${s.label}: ${s.value}</title></rect>`;
  });
  // baseline
  out += `<rect x='${pad}' y='${h-12}' width='${w-pad*2}' height='1' fill='#e5e7eb' />`;
  svg.innerHTML = out;
}

async function tick(){
  const r = await fetch('/admin/api/summary',{cache:'no-store'});
  const s = await r.json();
  document.getElementById('v').textContent = s.version;
  document.getElementById('db').textContent = s.db_enabled ? 'ON' : 'OFF';
  document.getElementById('u').textContent = s.unique_devices_live;
  document.getElementById('ar').textContent = s.active_rooms_count;
  document.getElementById('gc').textContent = s.games_completed_live;
  document.getElementById('chartTag').textContent = s.db_enabled ? 'Persistens' : 'Ingen DB';
  document.getElementById('chartHint').textContent = s.db_enabled ? 'Baseret på Postgres' : 'DB er slået fra — vises som 0';

  // Active rooms table
  const tbody = document.getElementById('rooms');
  tbody.innerHTML = '';
  for(const room of (s.active_rooms||[])){
    const tr = document.createElement('tr');
    const roundTxt = (room.current_round && room.rounds_total) ? `${room.current_round}/${room.rounds_total}` : '';
    tr.innerHTML = `<td>${room.room}</td><td>${room.players}</td><td>${room.status||''}</td><td>${roundTxt}</td><td>${room.category||''}</td><td>${room.dj_mode?'ja':''}</td>`;
    tbody.appendChild(tr);
  }

  // Chart
  const series = (s.daily||[]).map(d=>({label:d.day, value:d.games_completed||0}));
  svgBarChart(document.getElementById('chart'), series);
}

async function loadGames(){
  const r = await fetch('/admin/api/games?limit=30',{cache:'no-store'});
  const s = await r.json();
  const tbody = document.getElementById('games');
  tbody.innerHTML = '';
  for(const g of (s.games||[])){
    const tr = document.createElement('tr');
    const players = (g.players||[]).join(', ');
    const href = g.id ? `/admin/game/${g.id}` : '#';
    tr.innerHTML = `<td><a href='${href}'>${g.started_at||''}</a></td><td>${g.room_code||''}</td><td>${g.category||''}</td><td>${g.rounds_total||''}</td><td>${players}</td>`;
    tbody.appendChild(tr);
  }
}

tick();
loadGames();
setInterval(tick, 5000);
setInterval(loadGames, 15000);
</script>
</body>
</html>"""


@app.route("/admin/api/summary")
def admin_api_summary():
    # Live state
    active = []
    for rc, room in rooms.items():
        active.append({
            "room": rc,
            "players": len(room.get("players", [])),
            "status": room.get("status", ""),
            "current_round": room.get("round_index", 0) if room.get("started") else 0,
            "rounds_total": room.get("rounds_total", 0),
            "category": room.get("category", ""),
            "dj_mode": bool(room.get("dj_mode")),
        })

    daily = DB.daily_metrics(30)
    return jsonify({
        "version": VERSION,
        "db_enabled": DB.enabled,
        "unique_devices_live": len(STATS["unique_devices"]),
        "games_completed_live": STATS["games_completed"],
        "active_rooms_count": len(rooms),
        "active_rooms": active,
        "daily": daily,
    })


@app.route("/admin/api/games")
def admin_api_games():
    limit = int(request.args.get("limit", "30") or 30)
    limit = max(1, min(200, limit))
    if not DB.enabled:
        return jsonify({"games": []})
    return jsonify({"games": DB.recent_games(limit=limit)})


@app.route("/admin/game/<game_id>")
def admin_game_detail(game_id: str):
    if not DB.enabled:
        return "DB er ikke slået til (ingen historik).", 400
    g = DB.game_by_id(game_id)
    if not g:
        return "Spil ikke fundet.", 404
    history = g.get("history") or []
    # Simple HTML rendering
    items = "".join(
        f"<tr><td>{h.get('ts','')}</td><td>{h.get('event','')}</td><td>{json.dumps(h, ensure_ascii=False)}</td></tr>"
        for h in history
    )
    players = ", ".join(g.get("players") or [])
    return f"""<!doctype html>
<html lang='da'>
<head>
  <meta charset='utf-8' />
  <meta name='viewport' content='width=device-width,initial-scale=1' />
  <title>Spil {game_id} — Piratwhist Admin</title>
  <style>
    body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:18px;}}
    .muted{{opacity:.7;font-size:12px;}}
    table{{border-collapse:collapse;width:100%;margin-top:12px;}}
    th,td{{border-bottom:1px solid #eee;padding:8px;text-align:left;vertical-align:top;}}
    th{{font-size:12px;opacity:.8;}}
    code{{background:#f3f4f6;padding:2px 6px;border-radius:6px;}}
  </style>
</head>
<body>
  <p><a href='/admin'>&larr; tilbage</a></p>
  <h1>Spil <code>{game_id}</code></h1>
  <div class='muted'>Room: {g.get('room_code','')} • Kategori: {g.get('category','')} • Runder: {g.get('rounds_total','')} • Spillere: {players}</div>
  <div class='muted'>Start: {g.get('started_at','')} • Slut: {g.get('ended_at','')}</div>

  <h2>Historik</h2>
  <table>
    <thead><tr><th>Tid</th><th>Event</th><th>Data</th></tr></thead>
    <tbody>{items}</tbody>
  </table>
</body>
</html>"""
