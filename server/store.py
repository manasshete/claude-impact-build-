"""Shared signal store (SQLite).

Holds what the phone app sends and what the ward office does with it:
  reports  - a citizen complaint on a road (with an optional place, note and small photo)
  metoo    - one "Me too" per road per device
  actions  - ward-office decisions (answer, hold payment, ask for a free fix, recover, release)

Privacy by design: no names, no IP addresses. A device is a random id the phone made up.
Coordinates are rounded to 4 decimals (about 11 m).
"""
import contextlib
import datetime as dt
import hashlib
import os
import sqlite3
import threading
from pathlib import Path

DEFAULT_DB = Path(os.environ.get("APLA_DB", Path(__file__).parent / "apla_signals.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  road_id TEXT NOT NULL, ward TEXT NOT NULL, device_id TEXT NOT NULL,
  via TEXT NOT NULL, lat REAL, lon REAL, place_text TEXT, note TEXT, lang TEXT, thumb TEXT,
  created_at TEXT NOT NULL, day TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_report_day ON reports(device_id, road_id, day);
CREATE INDEX IF NOT EXISTS ix_reports_ward ON reports(ward, created_at);
CREATE INDEX IF NOT EXISTS ix_reports_road ON reports(road_id, created_at);
CREATE TABLE IF NOT EXISTS metoo (
  road_id TEXT NOT NULL, device_id TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY (road_id, device_id)
);
CREATE TABLE IF NOT EXISTS actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  flag_key TEXT, road_id TEXT NOT NULL, job_id TEXT, kind TEXT NOT NULL, text TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_actions_road ON actions(road_id, id);
CREATE INDEX IF NOT EXISTS ix_actions_key ON actions(flag_key, id);
"""

ACTION_KINDS = ("answer", "hold", "fixfree", "recover", "release")


def device_handle(device_id):
    return hashlib.sha256(device_id.encode("utf-8")).hexdigest()[:10]


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    def __init__(self, path=None):
        self.path = str(path or DEFAULT_DB)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._tx() as c:
            c.executescript(SCHEMA)

    @contextlib.contextmanager
    def _tx(self):
        """One connection per operation: commit on success, always close (no leaked handles)."""
        c = self._connect()
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def _connect(self):
        c = sqlite3.connect(self.path, timeout=15, check_same_thread=False)
        c.row_factory = sqlite3.Row
        try:
            c.execute("PRAGMA journal_mode=WAL")  # the board can read while the API writes
        except sqlite3.DatabaseError:
            pass
        return c

    # ------------------------------------------------------------ writes
    def add_report(self, road_id, ward, device_id, via, lat=None, lon=None, place_text="", note="", lang="mr", thumb="", now=None):
        """Returns {"id", "duplicate"}. One report per device per road per day."""
        t = now or now_utc()
        with self._lock, self._tx() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO reports (road_id, ward, device_id, via, lat, lon, place_text, note, lang, thumb, created_at, day) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (road_id, ward, device_id, via, lat, lon, place_text or "", note or "", lang, thumb or "", iso(t), t.strftime("%Y-%m-%d")))
            if cur.rowcount:
                return {"id": cur.lastrowid, "duplicate": False}
            row = c.execute("SELECT id FROM reports WHERE device_id=? AND road_id=? AND day=?", (device_id, road_id, t.strftime("%Y-%m-%d"))).fetchone()
            return {"id": row["id"], "duplicate": True}

    def add_metoo(self, road_id, device_id, now=None):
        with self._lock, self._tx() as c:
            cur = c.execute("INSERT OR IGNORE INTO metoo (road_id, device_id, created_at) VALUES (?,?,?)", (road_id, device_id, iso(now or now_utc())))
            return bool(cur.rowcount)

    def add_action(self, road_id, kind, text="", job_id=None, flag_key=None, now=None):
        if kind not in ACTION_KINDS:
            raise ValueError(f"unknown action kind {kind!r}")
        with self._lock, self._tx() as c:
            cur = c.execute("INSERT INTO actions (flag_key, road_id, job_id, kind, text, created_at) VALUES (?,?,?,?,?,?)",
                            (flag_key, road_id, job_id, kind, (text or "")[:400], iso(now or now_utc())))
            return cur.lastrowid

    # ------------------------------------------------------------ rate-limit helpers
    def reports_by_device_since(self, device_id, since):
        with self._tx() as c:
            return c.execute("SELECT COUNT(*) FROM reports WHERE device_id=? AND created_at>=?", (device_id, iso(since))).fetchone()[0]

    def metoo_by_device_since(self, device_id, since):
        with self._tx() as c:
            return c.execute("SELECT COUNT(*) FROM metoo WHERE device_id=? AND created_at>=?", (device_id, iso(since))).fetchone()[0]

    # ------------------------------------------------------------ reads
    def signals(self, window_days=7, now=None):
        """Public, anonymous summary per road with any activity."""
        t = now or now_utc()
        cutoff = iso(t - dt.timedelta(days=window_days))
        out = {}
        with self._tx() as c:
            for r in c.execute("SELECT road_id, COUNT(*) n, MAX(created_at) last FROM reports GROUP BY road_id"):
                out[r["road_id"]] = {"reports": r["n"], "reports_7d": 0, "devices_7d": 0, "metoo": 0, "last_report_at": r["last"], "status": None}
            for r in c.execute("SELECT road_id, COUNT(*) n, COUNT(DISTINCT device_id) d FROM reports WHERE created_at>=? GROUP BY road_id", (cutoff,)):
                out[r["road_id"]]["reports_7d"], out[r["road_id"]]["devices_7d"] = r["n"], r["d"]
            for r in c.execute("SELECT road_id, COUNT(*) n FROM metoo GROUP BY road_id"):
                out.setdefault(r["road_id"], {"reports": 0, "reports_7d": 0, "devices_7d": 0, "metoo": 0, "last_report_at": None, "status": None})["metoo"] = r["n"]
            for r in c.execute("SELECT a.road_id, a.kind, a.text, a.created_at FROM actions a "
                               "JOIN (SELECT road_id, MAX(id) mid FROM actions GROUP BY road_id) m ON a.id = m.mid"):
                out.setdefault(r["road_id"], {"reports": 0, "reports_7d": 0, "devices_7d": 0, "metoo": 0, "last_report_at": None, "status": None})["status"] = {
                    "kind": r["kind"], "text": r["text"], "at": r["created_at"]}
        return out

    def recent_reports(self, ward=None, limit=100, since=None):
        q, args = "SELECT id, road_id, ward, device_id, via, lat, lon, place_text, note, lang, thumb, created_at FROM reports", []
        where = []
        if ward:
            where.append("ward=?"); args.append(ward)
        if since:
            where.append("created_at>=?"); args.append(iso(since))
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY id DESC LIMIT ?"; args.append(int(limit))
        with self._tx() as c:
            rows = [dict(r) for r in c.execute(q, args)]
        for r in rows:  # the board only needs to tell devices apart, not know them: a one-way hash, never the raw id
            r["device"] = device_handle(r.pop("device_id"))
        return rows

    def latest_actions(self):
        """Latest action per flag_key, as {flag_key: {kind, text, at, road_id, job_id}}."""
        with self._tx() as c:
            rows = c.execute("SELECT a.flag_key, a.road_id, a.job_id, a.kind, a.text, a.created_at FROM actions a "
                             "JOIN (SELECT flag_key, MAX(id) mid FROM actions WHERE flag_key IS NOT NULL GROUP BY flag_key) m ON a.id = m.mid").fetchall()
        return {r["flag_key"]: {"kind": r["kind"], "text": r["text"], "at": r["created_at"], "road_id": r["road_id"], "job_id": r["job_id"]} for r in rows}

    def metoo_total(self, ward_roads=None):
        with self._tx() as c:
            if ward_roads is None:
                return c.execute("SELECT COUNT(*) FROM metoo").fetchone()[0]
            marks = ",".join("?" * len(ward_roads)) or "''"
            return c.execute(f"SELECT COUNT(*) FROM metoo WHERE road_id IN ({marks})", list(ward_roads)).fetchone()[0]
