"""Tests for the signal API. Run:  python -m unittest server/test_api.py -v   (from the repo root)"""
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import api  # noqa: E402
from store import device_handle  # noqa: E402

TOKEN = "test-token"
DEV = "dev00000-aaaa-bbbb"
THUMB = "data:image/jpeg;base64," + "A" * 200


def call(base, method, path, body=None, token=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})
    if token:
        req.add_header("X-Admin-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}"), r.headers
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}"), e.headers
        finally:
            e.close()


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.srv = api.make_server("127.0.0.1", 0, Path(cls.tmp.name) / "t.db", None, TOKEN)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.tmp.cleanup()

    def test_health_and_cors(self):
        s, d, h = call(self.base, "GET", "/api/health")
        self.assertEqual((s, d), (200, {"ok": True}))
        self.assertEqual(h["Access-Control-Allow-Origin"], "*")
        s, _, h = call(self.base, "OPTIONS", "/api/reports")
        self.assertEqual(s, 204)
        self.assertEqual(h["Access-Control-Allow-Private-Network"], "true")  # https page -> localhost API
        self.assertIn("X-Admin-Token", h["Access-Control-Allow-Headers"])

    def test_report_validation(self):
        ok = {"road_id": "RD0220", "device_id": "dev11111-aaaa-bbbb", "via": "road", "note": "Cave-in near the station"}
        self.assertEqual(call(self.base, "POST", "/api/reports", {**ok, "road_id": "RD9999"})[0], 400)
        self.assertEqual(call(self.base, "POST", "/api/reports", {**ok, "device_id": "x"})[0], 400)
        self.assertEqual(call(self.base, "POST", "/api/reports", {**ok, "via": "telepathy"})[0], 400)
        self.assertEqual(call(self.base, "POST", "/api/reports", {"road_id": "RD0220", "device_id": "dev11111-aaaa-bbbb"})[0], 400, "empty complaint")
        self.assertEqual(call(self.base, "POST", "/api/reports", raw=b"{not json")[0], 400)
        self.assertEqual(call(self.base, "POST", "/api/reports", raw=b"x" * 100_000)[0], 413)
        s, d, _ = call(self.base, "POST", "/api/reports", ok)
        self.assertEqual(s, 201)
        self.assertFalse(d["duplicate"])

    def test_one_report_per_device_per_road_per_day(self):
        b = {"road_id": "RD0221", "device_id": "dev22222-aaaa-bbbb", "via": "road", "note": "First"}
        self.assertEqual(call(self.base, "POST", "/api/reports", b)[0], 201)
        s, d, _ = call(self.base, "POST", "/api/reports", {**b, "note": "Second"})
        self.assertEqual(s, 200)
        self.assertTrue(d["duplicate"])
        s, d, _ = call(self.base, "POST", "/api/reports", {**b, "road_id": "RD0222"})
        self.assertEqual(s, 201, "same device, different road is fine")

    def test_location_and_photo_rules(self):
        base = {"road_id": "RD0223", "via": "link", "note": "x"}
        call(self.base, "POST", "/api/reports", {**base, "device_id": "dev33333-aaaa-bbbb", "lat": 19.081324, "lon": 72.885982, "thumb": THUMB})
        call(self.base, "POST", "/api/reports", {**base, "road_id": "RD0224", "device_id": "dev33333-aaaa-bbbb", "lat": 48.85, "lon": 2.35, "thumb": "http://evil/x.png"})
        rows = call(self.base, "GET", "/api/reports?limit=500", token=TOKEN)[1]["reports"]
        a = next(r for r in rows if r["road_id"] == "RD0223" and r["device"] == device_handle("dev33333-aaaa-bbbb"))
        b = next(r for r in rows if r["road_id"] == "RD0224" and r["device"] == device_handle("dev33333-aaaa-bbbb"))
        self.assertEqual((a["lat"], a["lon"]), (19.0813, 72.886), "rounded to ~11 m")
        self.assertTrue(a["thumb"].startswith("data:image/jpeg"))
        self.assertIsNone(b["lat"], "a place outside Mumbai is dropped")
        self.assertEqual(b["thumb"], "", "only a small jpeg data-URL is kept")
        self.assertNotIn("device_id", a)
        self.assertNotIn("dev33333", json.dumps(rows), "the raw device id never leaves the store")

    def test_metoo_is_once_per_device(self):
        b = {"road_id": "RD0225", "device_id": "dev44444-aaaa-bbbb"}
        self.assertTrue(call(self.base, "POST", "/api/metoo", b)[1]["new"])
        self.assertFalse(call(self.base, "POST", "/api/metoo", b)[1]["new"])
        self.assertTrue(call(self.base, "POST", "/api/metoo", {**b, "device_id": "dev55555-aaaa-bbbb"})[1]["new"])
        self.assertEqual(call(self.base, "GET", "/api/signals")[1]["roads"]["RD0225"]["metoo"], 2)

    def test_signals_count_independent_devices(self):
        for i in range(4):
            call(self.base, "POST", "/api/reports", {"road_id": "RD0227", "device_id": f"dev6666{i}-aaaa-bbbb", "via": "road", "note": f"n{i}"})
        s = call(self.base, "GET", "/api/signals")[1]["roads"]["RD0227"]
        self.assertEqual((s["reports"], s["reports_7d"], s["devices_7d"]), (4, 4, 4))
        self.assertNotIn("note", json.dumps(s), "the public summary carries no text")

    def test_rate_limit(self):
        d = "dev77777-aaaa-bbbb"
        roads = [r for r in api.Handler.api.roads][:25]
        codes = [call(self.base, "POST", "/api/reports", {"road_id": r, "device_id": d, "via": "road", "note": "x"})[0] for r in roads]
        self.assertEqual(codes.count(201), api.LIMITS["reports_per_day"])
        self.assertEqual(codes[-1], 429)

    def test_ward_office_endpoints_need_the_token(self):
        self.assertEqual(call(self.base, "GET", "/api/reports")[0], 401)
        self.assertEqual(call(self.base, "GET", "/api/reports", token="wrong")[0], 401)
        self.assertEqual(call(self.base, "GET", "/api/actions")[0], 401)
        self.assertEqual(call(self.base, "POST", "/api/actions", {"road_id": "RD0220", "kind": "hold"})[0], 401)
        self.assertEqual(call(self.base, "GET", "/api/reports", token=TOKEN)[0], 200)

    def test_actions_flow_back_to_signals(self):
        a = {"road_id": "RD0228", "kind": "hold", "job_id": "WO25-02068", "flag_key": "WO25-02068|guarantee_paid|", "text": "Payment held"}
        self.assertEqual(call(self.base, "POST", "/api/actions", {**a, "kind": "explode"}, token=TOKEN)[0], 400)
        self.assertEqual(call(self.base, "POST", "/api/actions", a, token=TOKEN)[0], 201)
        call(self.base, "POST", "/api/actions", {**a, "kind": "answer", "text": "Contractor asked to fix it free"}, token=TOKEN)
        st = call(self.base, "GET", "/api/signals")[1]["roads"]["RD0228"]["status"]
        self.assertEqual((st["kind"], st["text"]), ("answer", "Contractor asked to fix it free"), "latest action wins")
        latest = call(self.base, "GET", "/api/actions", token=TOKEN)[1]["actions"]
        self.assertEqual(latest["WO25-02068|guarantee_paid|"]["kind"], "answer")

    def test_unknown_routes(self):
        self.assertEqual(call(self.base, "GET", "/nope")[0], 404)
        self.assertEqual(call(self.base, "POST", "/nope", {})[0], 404)


if __name__ == "__main__":
    unittest.main()
