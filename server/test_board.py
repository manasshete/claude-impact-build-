"""The ward board reading the shared store. Run:  python -m unittest server/test_board.py -v   (from the repo root)"""
import os
import sys
import tempfile
import unittest
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
warnings.filterwarnings("ignore")

from store import Store  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

THUMB = "data:image/jpeg;base64," + "A" * 120


def board(db):
    os.environ["APLA_DB"] = str(db)
    os.environ.pop("APLA_API_URL", None)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=90)
    at.run()
    return at


def text(at):
    return " ".join(m.value for m in at.markdown)


class BoardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "b.db"
        self.store = Store(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def complain(self, road, devices, note="Cave-in near the station"):
        for i in range(devices):
            self.store.add_report(road, "L", f"device{i:02d}-aaaa-bbbb", "road", None, None, "", f"{note} {i}", "en", THUMB)

    def test_empty_store_renders_with_an_empty_state(self):
        at = board(self.db)
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertIn("No complaints yet in this window", text(at))

    def test_complaints_appear_on_the_board(self):
        self.complain("RD0220", 2)
        at = board(self.db)
        self.assertFalse(at.exception, [e.value for e in at.exception])
        t = text(at)
        self.assertIn("Kurla Andheri Road", t)
        self.assertIn("2 complaints from 2 devices", t)
        self.assertIn("Building", t, "two devices are below the default of three")
        self.assertNotIn("Critical voice</span>", t.split("Roads by live voice")[-1].split("Latest complaints")[0])

    def test_voice_and_proof_together_make_critical(self):
        self.complain("RD0220", 4)  # RD0220 carries unanswered red flags, a failed fix and a Grade F contractor
        t = text(board(self.db))
        self.assertIn("Critical voice", t)
        self.assertIn("contractor(s) to review", t)

    def test_voice_alone_is_never_critical(self):
        # a road with plenty of voice but no red flags in the data must stay "Building"
        import json
        d = json.loads((ROOT / "apla_data.json").read_text(encoding="utf-8"))
        flagged = {f["road_id"] for f in d["flags"] if f["severity"] == "red"}
        clean = next(r["road_id"] for r in d["roads"] if r["ward"] == "L" and r["road_id"] not in flagged)
        self.complain(clean, 10)
        t = text(board(self.db))
        section = t.split("Roads by live voice")[-1].split("Latest complaints")[0]
        self.assertIn("Building", section)
        self.assertNotIn("Critical voice", section)

    def test_answering_every_red_flag_removes_the_proof(self):
        import json
        d = json.loads((ROOT / "apla_data.json").read_text(encoding="utf-8"))
        self.complain("RD0220", 5)
        for f in d["flags"]:
            if f["road_id"] == "RD0220" and f["severity"] == "red":
                key = f"{f['job_id']}|{f['type']}|{f['evidence'].get('complaint_id', '')}"
                self.store.add_action("RD0220", "answer", "Answered", f["job_id"], key)
        section = text(board(self.db)).split("Roads by live voice")[-1].split("Latest complaints")[0]
        self.assertNotIn("Critical voice", section, "once the ward office has answered, the unanswered-flag check fails")

    def test_actions_are_saved_and_survive_a_reload(self):
        key = "WO25-02068|guarantee_paid|"
        self.store.add_action("RD0220", "hold", "Payment held pending review", "WO25-02068", key)
        self.assertEqual(self.store.latest_actions()[key]["kind"], "hold")
        self.assertEqual(self.store.signals()["RD0220"]["status"]["kind"], "hold")
        at = board(self.db)
        self.assertFalse(at.exception, [e.value for e in at.exception])

    def test_every_ward_still_renders_with_live_data(self):
        self.complain("RD0220", 3)
        at = board(self.db)
        bad = []
        for code in ["A", "E", "F/N", "L", "M/W", "T"]:
            at.selectbox[0].set_value(code).run()
            if at.exception:
                bad.append((code, at.exception[0].value[:100]))
        self.assertEqual(bad, [])

    def test_ward_office_buttons_save_to_the_store_and_the_phone_can_read_them(self):
        key = "WO25-02068|guarantee_paid|"
        self.complain("RD0220", 4, "Cave-in")
        os.environ["APLA_DB"] = str(self.db)
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=90)
        at.query_params["flag"] = "WO25-02068"  # the shareable link to one flag opens its evidence panel
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        t = text(at)
        self.assertIn("Voice on this road", t)
        self.assertIn("Critical voice", t)

        at.button(key=f"h_{key}").click().run()
        self.assertEqual(self.store.latest_actions()[key]["kind"], "hold")
        self.assertEqual(self.store.signals()["RD0220"]["status"]["kind"], "hold", "the phone reads this status")
        self.assertIn("Payment held", " ".join(i.value for i in at.info), "the panel shows the latest action")

        at.button(key=f"f_{key}").click().run()
        self.assertEqual(self.store.latest_actions()[key]["kind"], "fixfree")
        self.assertIn("guarantee", self.store.signals()["RD0220"]["status"]["text"])

        at.button(key=f"a_{key}").click().run()
        at.text_area(key=f"t_{key}").set_value("We are on it. The contractor has been told.").run()
        at.button(key=f"p_{key}").click().run()
        latest = self.store.latest_actions()[key]
        self.assertEqual((latest["kind"], latest["text"]), ("answer", "We are on it. The contractor has been told."))
        self.assertFalse(at.exception, [e.value for e in at.exception])

    def test_a_held_payment_can_be_released(self):
        key = "WO25-02068|guarantee_paid|"
        self.store.add_action("RD0220", "hold", "held", "WO25-02068", key)
        os.environ["APLA_DB"] = str(self.db)
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=90)
        at.query_params["flag"] = "WO25-02068"
        at.run()
        at.button(key=f"x_{key}").click().run()
        self.assertEqual(self.store.latest_actions()[key]["kind"], "release")
        self.assertEqual(self.store.signals()["RD0220"]["status"]["kind"], "release")


if __name__ == "__main__":
    unittest.main()
