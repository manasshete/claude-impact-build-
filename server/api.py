"""Apla BMC signal API: the link between the phone app and the ward board.

    python server/api.py                         # http://127.0.0.1:8787
    python server/api.py --host 0.0.0.0          # reachable from a phone on the same Wi-Fi

Public (the phone app):   POST /api/reports   POST /api/metoo   GET /api/signals   GET /api/health
Ward office (token):      GET  /api/reports   GET  /api/actions  POST /api/actions
                          send the token in the X-Admin-Token header (set APLA_ADMIN_TOKEN)

Standard library only. No names or IP addresses are stored; see store.py.
"""
import argparse
import hmac
import json
import os
import re
import sys
import datetime as dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).parent))
from store import ACTION_KINDS, Store, now_utc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEV_TOKEN = "dev-ward-office"
MAX_BODY = 90_000
MAX_THUMB = 45_000
VIA = {"exif", "gps", "link", "address", "road"}
LANGS = {"mr", "hi", "en"}
DEVICE_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
MUMBAI = (18.8, 19.35, 72.7, 73.1)  # lat min, lat max, lon min, lon max
LIMITS = {"reports_per_day": 20, "metoo_per_day": 300}


def load_roads(path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    return {r["road_id"]: r["ward"] for r in d["roads"]}


class Api:
    """Pure request logic, kept apart from the HTTP plumbing so it can be tested directly."""

    def __init__(self, store, roads, admin_token):
        self.store, self.roads, self.token = store, roads, admin_token

    def is_admin(self, token):
        return bool(token) and hmac.compare_digest(str(token), str(self.token))

    def post_report(self, b):
        road = b.get("road_id")
        if road not in self.roads:
            return 400, {"error": "unknown road_id"}
        dev = str(b.get("device_id", ""))
        if not DEVICE_RE.match(dev):
            return 400, {"error": "bad device_id"}
        via = b.get("via", "road")
        if via not in VIA:
            return 400, {"error": "bad via"}
        note = str(b.get("note", "") or "").strip()[:500]
        place = str(b.get("place_text", "") or "").strip()[:200]
        thumb = str(b.get("thumb", "") or "")
        if thumb and not (thumb.startswith("data:image/jpeg;base64,") and len(thumb) <= MAX_THUMB):
            thumb = ""
        if not (note or place or thumb or via != "road"):
            return 400, {"error": "a complaint needs a note, a photo or a place"}
        lat, lon = b.get("lat"), b.get("lon")
        try:
            lat, lon = round(float(lat), 4), round(float(lon), 4)
            if not (MUMBAI[0] <= lat <= MUMBAI[1] and MUMBAI[2] <= lon <= MUMBAI[3]):
                lat = lon = None
        except (TypeError, ValueError):
            lat = lon = None
        lang = b.get("lang") if b.get("lang") in LANGS else "mr"
        if self.store.reports_by_device_since(dev, now_utc() - dt.timedelta(days=1)) >= LIMITS["reports_per_day"]:
            return 429, {"error": "too many reports today"}
        r = self.store.add_report(road, self.roads[road], dev, via, lat, lon, place, note, lang, thumb)
        return (200 if r["duplicate"] else 201), r

    def post_metoo(self, b):
        road, dev = b.get("road_id"), str(b.get("device_id", ""))
        if road not in self.roads:
            return 400, {"error": "unknown road_id"}
        if not DEVICE_RE.match(dev):
            return 400, {"error": "bad device_id"}
        if self.store.metoo_by_device_since(dev, now_utc() - dt.timedelta(days=1)) >= LIMITS["metoo_per_day"]:
            return 429, {"error": "too many taps today"}
        return 200, {"new": self.store.add_metoo(road, dev)}

    def post_action(self, b):
        road, kind = b.get("road_id"), b.get("kind")
        if road not in self.roads:
            return 400, {"error": "unknown road_id"}
        if kind not in ACTION_KINDS:
            return 400, {"error": "bad kind"}
        aid = self.store.add_action(road, kind, str(b.get("text", ""))[:400], b.get("job_id"), b.get("flag_key"))
        return 201, {"id": aid}


class Handler(BaseHTTPRequestHandler):
    api: Api = None
    server_version = "AplaSignals/1.0"

    # -- plumbing
    def log_message(self, fmt, *args):  # no IPs, no bodies
        if os.environ.get("APLA_VERBOSE"):
            sys.stderr.write("%s %s\n" % (self.command, self.path.split("?")[0]))

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Admin-Token")
        self.send_header("Access-Control-Allow-Private-Network", "true")  # an https page may call a localhost API
        self.send_header("Access-Control-Max-Age", "600")

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            return None, (413, {"error": "too large"})
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
            return (data if isinstance(data, dict) else {}), None
        except (ValueError, UnicodeDecodeError):
            return None, (400, {"error": "bad json"})

    def _admin(self):
        if self.api.is_admin(self.headers.get("X-Admin-Token")):
            return True
        self._send(401, {"error": "ward office token required"})
        return False

    # -- verbs
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/api/health":
            return self._send(200, {"ok": True})
        if u.path == "/api/signals":
            return self._send(200, {"generated_at": now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"), "roads": self.api.store.signals()})
        if u.path == "/api/reports":
            if not self._admin():
                return
            since = None
            if q.get("days"):
                since = now_utc() - dt.timedelta(days=float(q["days"]))
            return self._send(200, {"reports": self.api.store.recent_reports(q.get("ward"), int(q.get("limit", 200)), since)})
        if u.path == "/api/actions":
            if not self._admin():
                return
            return self._send(200, {"actions": self.api.store.latest_actions()})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        b, err = self._body()
        if err:
            return self._send(*err)
        if u.path == "/api/reports":
            return self._send(*self.api.post_report(b))
        if u.path == "/api/metoo":
            return self._send(*self.api.post_metoo(b))
        if u.path == "/api/actions":
            if not self._admin():
                return
            return self._send(*self.api.post_action(b))
        self._send(404, {"error": "not found"})


def make_server(host, port, db=None, data=None, token=None):
    roads = load_roads(data or ROOT / "apla_data.json")
    Handler.api = Api(Store(db), roads, token or os.environ.get("APLA_ADMIN_TOKEN", DEV_TOKEN))
    return ThreadingHTTPServer((host, port), Handler)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--db", default=None, help="SQLite file (default server/apla_signals.db, or $APLA_DB)")
    ap.add_argument("--data", default=None, help="apla_data.json, used to check road ids")
    a = ap.parse_args()
    srv = make_server(a.host, a.port, a.db, a.data)
    tok = Handler.api.token
    print(f"Apla signal API on http://{a.host}:{a.port}   db: {Handler.api.store.path}")
    if tok == DEV_TOKEN:
        print("WARNING: using the built-in dev ward-office token. Set APLA_ADMIN_TOKEN before exposing this beyond your laptop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
