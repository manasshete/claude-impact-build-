"""Apla BMC · Ward Accountability Board (Streamlit). Reads apla_data.json only.

Run:  streamlit run app.py
"""
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from urllib.parse import quote

import altair as alt
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent / "server"))
from store import Store  # noqa: E402  the shared store the phone app writes to

DATA = Path(__file__).parent / "apla_data.json"
st.set_page_config(page_title="Apla BMC · Ward Accountability Board", page_icon="🏛️", layout="wide", initial_sidebar_state="collapsed")

# Emerald Ledger palette
PINE, PINE_D, TEAL2 = "#0F4C5C", "#0A3844", "#0D9488"
OCHRE, GREEN, RED, TANG = "#CA8A04", "#15803D", "#B91C1C", "#E36414"

LABELS = {
    "overcharged": "Overcharged", "billed_twice": "Billed twice", "no_complaint": "No complaint",
    "guarantee_paid": "Guarantee road", "built_weak": "Built weak", "failed_fix": "Failed fix",
    "repeat_patch": "Repeat patch", "pump_no_rain": "Pump, no rain", "utility_damage": "Utility damage",
    "closed_by_old_repair": "Closed by old repair",
}
RED_TYPES = ["overcharged", "billed_twice", "no_complaint", "guarantee_paid", "built_weak"]
AMBER_TYPES = ["failed_fix", "repeat_patch", "pump_no_rain", "utility_damage", "closed_by_old_repair"]
STATUS = {"pending": "Pending", "answered": "Answered", "held": "Payment held",
          "fixfree": "Contractor to fix free", "recover": "Recover from utility", "released": "Hold released"}
KIND_STATUS = {"answer": "answered", "hold": "held", "fixfree": "fixfree", "recover": "recover", "release": "released"}
VIA_LABEL = {"exif": "Photo location", "gps": "Phone GPS", "link": "Google Maps link", "address": "Typed address", "road": "Chose the road"}


@st.cache_data
def load():
    return json.loads(DATA.read_text(encoding="utf-8"))


def fmt_inr(x):
    sign, x = ("-" if x < 0 else ""), abs(float(x))
    if x >= 1e7:
        return f"{sign}₹{x / 1e7:.2f} Cr"
    if x >= 1e5:
        v = x / 1e5
        return f"{sign}₹{v:.1f} lakh" if v < 100 else f"{sign}₹{v:.0f} lakh"
    return f"{sign}₹{x:,.0f}"


esc = lambda s: html.escape(str(s), quote=True)


def H(s):  # flatten so Markdown never mistakes indented HTML for a code block
    return re.sub(r"\n\s*", "", s)


if not DATA.exists():
    st.error("apla_data.json not found. Run `python build_data.py` first.")
    st.stop()

D = load()
AS_OF = pd.Timestamp(D["as_of"])
W = {w["code"]: w for w in D["wards"]}
flags_all = pd.DataFrame(D["flags"])
flags_all["key"] = flags_all["job_id"].astype(str) + "|" + flags_all["type"] + "|" + flags_all["evidence"].map(lambda e: str(e.get("complaint_id", "")))
ROADS = {r["road_id"]: r for r in D["roads"]}
GRADE = {(c["ward"], c["name"]): c["grade"] for c in D["contractors"]}
FLAGS_BY_ROAD = {rid: g.to_dict("records") for rid, g in flags_all.groupby("road_id") if rid}

# ---- the link to the phone app: the shared store. Local file by default; set APLA_API_URL to read a hosted API instead.
API_URL = os.environ.get("APLA_API_URL", "").rstrip("/")
ADMIN_TOKEN = os.environ.get("APLA_ADMIN_TOKEN", "dev-ward-office")


@st.cache_resource
def _store_at(path):
    return Store(path or None)


def _local_store():
    return _store_at(os.environ.get("APLA_DB", ""))  # keyed on the path, so changing APLA_DB takes effect


def _http(path, method="GET", body=None):
    req = urllib.request.Request(API_URL + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "X-Admin-Token": ADMIN_TOKEN})
    with urllib.request.urlopen(req, timeout=6) as r:
        return json.loads(r.read())


def get_signals():
    try:
        return (_http("/api/signals")["roads"] if API_URL else _local_store().signals()), True
    except Exception:
        return {}, False


def get_reports(ward=None, days=None, limit=500):
    try:
        if API_URL:
            q = f"?limit={limit}" + (f"&ward={quote(ward, safe='')}" if ward else "") + (f"&days={days}" if days else "")
            return _http("/api/reports" + q)["reports"], True
        since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days) if days else None
        return _local_store().recent_reports(ward, limit, since), True
    except Exception:
        return [], False


def get_actions():
    try:
        return (_http("/api/actions")["actions"] if API_URL else _local_store().latest_actions()), True
    except Exception:
        return {}, False


def post_action(road_id, kind, text, job_id, flag_key):
    try:
        if API_URL:
            _http("/api/actions", "POST", {"road_id": road_id, "kind": kind, "text": text, "job_id": job_id, "flag_key": flag_key})
        else:
            _local_store().add_action(road_id, kind, text, job_id, flag_key)
        return True
    except Exception:
        return False


def status_in(actions, key):
    return KIND_STATUS.get((actions.get(key) or {}).get("kind"), "pending")


def voice_by_road(ward, days):
    """Complaints from the phone app, grouped by road (the board only sees which device, never who)."""
    reps, ok = get_reports(ward, days)
    out = {}
    for r in reps:
        v = out.setdefault(r["road_id"], {"reports": 0, "devices": set(), "last": r["created_at"], "notes": [], "thumbs": [], "via": {}})
        v["reports"] += 1
        v["devices"].add(r["device"])
        v["last"] = max(v["last"], r["created_at"])
        v["via"][r["via"]] = v["via"].get(r["via"], 0) + 1
        if r.get("note"):
            v["notes"].append((r["created_at"], r["note"]))
        if r.get("thumb"):
            v["thumbs"].append(r["thumb"])
    return out, ok


def critical_check(rid, v, min_devices, actions):
    """Critical voice = independent voices AND proof AND a poor record, all at once. Voice alone never counts."""
    road, flags = ROADS[rid], FLAGS_BY_ROAD.get(rid, [])
    unanswered = [f for f in flags if f["severity"] == "red" and status_in(actions, f["key"]) == "pending"]
    poor = sorted({f["contractor"] for f in unanswered if GRADE.get((road["ward"], f["contractor"]), "A") in "DF"})
    checks = {
        f"{len(v['devices'])} independent devices (needs {min_devices})": len(v["devices"]) >= min_devices,
        "an unanswered red flag on the road": bool(unanswered),
        "a failed fix on the road": road["broke_again"] > 0 or any(f["type"] == "failed_fix" for f in flags),
        "a contractor with a poor record (D or worse)": bool(poor),
    }
    return {"critical": all(checks.values()), "checks": checks, "contractors": poor}


def check_chips(c):
    return "".join(f'<span class="{"ok" if met else "no"}">{"Met" if met else "Not yet"}: {esc(name)}</span>' for name, met in c["checks"].items())


def when(iso):
    try:
        return pd.to_datetime(iso).tz_convert("Asia/Kolkata").strftime("%d %b %H:%M")
    except Exception:
        return str(iso)

# ---------------------------------------------------------------- style
st.markdown(H("""<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Noto+Sans+Devanagari:wght@400;600;700&display=swap');
:root{--pine:#0F4C5C;--pine-d:#0A3844;--teal2:#0D9488;--paper:#FAF9F6;--paper2:#F1EFE8;--rule:#E4E4E7;--ink:#18181B;--muted:#52525B;
--red:#B91C1C;--red50:#FBEAEA;--ochre:#CA8A04;--ochre50:#FBF3DD;--ochre-t:#7C4A03;--green:#15803D;--green50:#E6F4EA;--green-t:#106030;--tang:#E36414;--tang50:#FDF0E6;--tang-t:#A8420A;--pine50:#EAF3F5;}
html,body,[class*="css"],.stApp{font-family:'Inter','Noto Sans Devanagari',system-ui,sans-serif;color:var(--ink);}
.stApp{background:var(--paper);}
#MainMenu,footer,[data-testid="stToolbar"],[data-testid="stDecoration"]{display:none!important;}
header[data-testid="stHeader"]{background:transparent;height:0;}
.block-container{padding-top:1.2rem!important;padding-bottom:4rem;max-width:1240px;}
h1,h2,h3,h4{font-family:'Inter','Noto Sans Devanagari',sans-serif;letter-spacing:-.01em;color:var(--pine-d);}
.hero{position:relative;overflow:hidden;border-radius:22px;padding:28px 32px;color:#FAF9F6;background:linear-gradient(135deg,#0F4C5C 0%,#0A3844 70%);box-shadow:0 18px 40px -26px rgba(10,56,68,.8);}
.hero:after{content:"";position:absolute;right:-60px;top:-80px;width:260px;height:260px;border-radius:50%;background:radial-gradient(circle at 30% 30%,rgba(13,148,136,.55),rgba(13,148,136,0) 70%);}
.hero .row{display:flex;align-items:center;gap:18px;position:relative;z-index:1;flex-wrap:wrap;}
.mark{width:56px;height:56px;border-radius:16px;display:grid;place-items:center;background:var(--tang);color:#18181B;font-weight:800;font-size:1.5rem;box-shadow:0 8px 20px -10px rgba(227,100,20,.9);}
.hero h1{margin:0;color:#FAF9F6;font-size:1.9rem;line-height:1.15;font-weight:800;}
.hero .sub{color:#B8D3D8;font-size:.98rem;margin-top:2px;}
.hero .win{position:relative;z-index:1;margin-top:16px;display:inline-block;background:rgba(250,249,246,.1);border:1px solid rgba(250,249,246,.22);border-radius:12px;padding:9px 14px;font-size:.92rem;color:#EAF3F5;max-width:780px;}
.hero .win b{color:#fff;}
.wardbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:6px 0 2px;}
.wardbar .big{font-size:1.5rem;font-weight:800;color:var(--pine-d);}
.pill{display:inline-flex;align-items:center;gap:6px;padding:4px 12px;border-radius:999px;font-size:.82rem;font-weight:600;}
.pill.red{background:var(--red50);color:#8E1414;}.pill.ochre{background:var(--ochre50);color:var(--ochre-t);}
.pill.green{background:var(--green50);color:var(--green-t);}.pill.pine{background:var(--pine50);color:var(--pine);}.pill.tang{background:var(--tang50);color:var(--tang-t);}
.kpis{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:14px;margin:14px 0 6px;}
.kpi{position:relative;background:#fff;border:1px solid var(--rule);border-radius:16px;padding:16px 16px 14px;box-shadow:0 1px 0 var(--rule),0 12px 24px -22px rgba(10,56,68,.6);overflow:hidden;}
.kpi:before{content:"";position:absolute;left:0;top:0;bottom:0;width:5px;background:var(--c,var(--pine));}
.kpi .l{font-size:.82rem;color:var(--muted);font-weight:500;line-height:1.3;min-height:2.1em;}
.kpi .v{font-size:1.85rem;font-weight:800;line-height:1.15;color:var(--c,var(--pine-d));margin-top:4px;font-variant-numeric:tabular-nums;}
.kpi .s{font-size:.82rem;color:var(--muted);margin-top:4px;min-height:1.3em;}
.kpi .meter{height:8px;border-radius:6px;background:var(--paper2);margin-top:10px;position:relative;overflow:visible;}
.kpi .meter i{display:block;height:100%;border-radius:6px;background:var(--c);}
.kpi .meter b{position:absolute;top:-4px;bottom:-4px;width:2px;background:var(--ink);}
.strip{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0 4px;}
.strip .chip{display:flex;align-items:baseline;gap:8px;padding:10px 16px;border-radius:14px;background:#fff;border:1px solid var(--rule);}
.strip .chip .n{font-size:1.4rem;font-weight:800;font-variant-numeric:tabular-nums;}.strip .chip .t{font-size:.86rem;color:var(--muted);}
.card{background:#fff;border:1px solid var(--rule);border-radius:18px;padding:20px 22px;box-shadow:0 1px 0 var(--rule),0 14px 28px -24px rgba(10,56,68,.6);}
.card h3{margin:0 0 10px;font-size:1.1rem;}
.sumline{display:flex;gap:12px;align-items:flex-start;padding:8px 0;border-bottom:1px solid var(--paper2);font-size:1rem;}
.sumline:last-child{border-bottom:0;}.sumline .n{flex:none;width:26px;height:26px;border-radius:50%;background:var(--pine);color:#FAF9F6;display:grid;place-items:center;font-size:.8rem;font-weight:700;margin-top:1px;}
.sec{font-size:1.25rem;font-weight:800;color:var(--pine-d);margin:22px 0 8px;display:flex;align-items:center;gap:10px;}
.sec .pill{font-size:.78rem;}
.note{color:var(--muted);font-size:.9rem;margin:2px 0 10px;}
.rank{flex:none;width:44px;height:44px;border-radius:14px;background:var(--pine);color:#FAF9F6;display:grid;place-items:center;font-weight:800;font-size:1.2rem;}
.nr{display:flex;gap:16px;align-items:flex-start;}.nr .b{flex:1;min-width:0;}.nr .name{font-size:1.1rem;font-weight:700;color:var(--pine-d);}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0;}.chips span{background:var(--pine50);color:var(--pine);border-radius:999px;padding:3px 10px;font-size:.8rem;font-weight:600;}
.chips span.g{background:var(--green50);color:var(--green-t);}
.fix{font-size:.93rem;color:var(--ink);}
.cost{font-size:1.6rem;font-weight:800;font-variant-numeric:tabular-nums;}.cost.zero{color:var(--green);}.cost small{display:block;font-size:.76rem;font-weight:500;color:var(--muted);}
.ct{display:flex;gap:16px;align-items:flex-start;}.grade{flex:none;width:68px;height:68px;border-radius:18px;display:grid;place-items:center;font-weight:800;font-size:2.2rem;color:#FAF9F6;}
.grade.A,.grade.B{background:var(--green);}.grade.C{background:var(--ochre);color:#3F2602;}.grade.D,.grade.F{background:var(--red);}
.ct .nm{font-size:1.1rem;font-weight:700;color:var(--pine-d);}.ct .jb{font-size:.85rem;color:var(--muted);}
.m{margin:10px 0 0;}.m .t{display:flex;justify-content:space-between;font-size:.84rem;color:var(--muted);}.m .t b{color:var(--ink);font-variant-numeric:tabular-nums;}
.m .bar{height:8px;background:var(--paper2);border-radius:6px;margin-top:4px;overflow:hidden;}.m .bar i{display:block;height:100%;border-radius:6px;background:var(--teal2);}
.ctnote{font-size:.88rem;color:var(--muted);margin-top:12px;line-height:1.5;}
.flow{display:flex;align-items:stretch;gap:0;flex-wrap:wrap;margin:6px 0 4px;}
.flow .st{flex:1;min-width:150px;background:#fff;border:1px solid var(--rule);padding:14px 16px;position:relative;}
.flow .st:first-child{border-radius:16px 0 0 16px;}.flow .st:last-child{border-radius:0 16px 16px 0;}.flow .st+.st{border-left:0;}
.flow .st .n{font-size:1.7rem;font-weight:800;font-variant-numeric:tabular-nums;color:var(--pine-d);}.flow .st .t{font-size:.84rem;color:var(--muted);}
.flow .st.bad .n{color:var(--red);}.flow .st.warn .n{color:var(--ochre-t);}
.lb{display:flex;align-items:center;gap:10px;margin:8px 0;font-size:.9rem;}.lb .nm{flex:0 0 46%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.lb .bar{flex:1;height:10px;background:var(--paper2);border-radius:6px;overflow:hidden;}.lb .bar i{display:block;height:100%;background:var(--tang);border-radius:6px;}.lb .v{width:42px;text-align:right;font-weight:700;font-variant-numeric:tabular-nums;}
.ev .k{color:var(--muted);font-size:.84rem;}.ev .val{font-weight:600;}
.foot{margin-top:34px;padding-top:14px;border-top:1px solid var(--rule);font-size:.82rem;color:var(--muted);}
.vrow{background:#fff;border:1px solid var(--rule);border-radius:16px;padding:14px 16px;margin:8px 0;box-shadow:0 1px 0 var(--rule);}
.vrow .vh{display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:1.02rem;color:var(--pine-d);}
.vrow .vm{font-size:.9rem;color:var(--muted);margin:4px 0 6px;}
.chips span.ok{background:var(--green50);color:var(--green-t);}.chips span.no{background:var(--paper2);color:var(--muted);}
.livehead{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:18px 0 4px;}
.livehead .sec{margin:0;}.livehead .note{margin:0;}
/* streamlit widgets */
[data-testid="stTabs"] [role="tablist"]{gap:6px;border-bottom:1px solid var(--rule);}
[data-testid="stTabs"] [role="tab"]{padding:12px 20px;border-radius:12px 12px 0 0;color:var(--muted);height:auto;}
[data-testid="stTabs"] [role="tab"] p{font-size:1.02rem;font-weight:600;}
[data-testid="stTabs"] [role="tab"][aria-selected="true"]{background:var(--pine50);}
[data-testid="stTabs"] [role="tab"][aria-selected="true"] p{color:var(--pine);}
[data-testid="stTabs"] [data-baseweb="tab-highlight"]{background:var(--teal2);height:3px;}
.stButton>button{border-radius:12px;font-weight:600;border:1.5px solid var(--pine);color:var(--pine);background:#fff;padding:.55rem 1rem;min-height:44px;transition:transform .12s,background-color .12s;}
.stButton>button:hover{background:var(--pine50);border-color:var(--pine);color:var(--pine);}.stButton>button:active{transform:scale(.98);}
.stButton>button[kind="primary"]{background:var(--tang);border-color:var(--tang);color:#18181B;}.stButton>button[kind="primary"]:hover{background:#D25A10;}
[data-testid="stDataFrame"]{border:1px solid var(--rule);border-radius:14px;overflow:hidden;}
[data-testid="stVerticalBlockBorderWrapper"]{border-radius:18px;border-color:var(--rule);background:#fff;}
div[data-baseweb="select"]>div{border-radius:12px;}
@media(max-width:1000px){.kpis{grid-template-columns:repeat(2,minmax(0,1fr));}}
</style>"""), unsafe_allow_html=True)


def kpi(label, value, sub="", color=PINE, meter=None):
    m = ""
    if meter is not None:
        pct, mark = meter
        m = f'<div class="meter"><i style="width:{min(pct, 100):.0f}%"></i><b style="left:{min(mark, 100):.0f}%"></b></div>'
    return f'<div class="kpi" style="--c:{color}"><div class="l">{esc(label)}</div><div class="v">{esc(value)}</div><div class="s">{esc(sub)}</div>{m}</div>'


# ---------------------------------------------------------------- header
st.markdown(H(f"""<div class="hero"><div class="row"><div class="mark">B</div>
<div><h1>Apla BMC · Ward Accountability Board</h1>
<div class="sub">Where the road-repair money went, what it should have cost, and every bill that needs an answer.</div></div></div>
<div class="win"><b>BMC's win:</b> answer these before they become headlines. Every answered flag shows publicly as answered.</div></div>"""), unsafe_allow_html=True)

codes = [w["code"] for w in D["wards"]]
c1, c2, c3 = st.columns([3, 2, 5])
ward = c1.selectbox("Ward", codes, index=codes.index("L"), format_func=lambda c: f"{c}  ·  {W[c]['name']}")
lang = c2.segmented_control("Text language", ["en", "mr", "hi"], default="en", format_func={"en": "English", "mr": "मराठी", "hi": "हिंदी"}.get) or "en"
w = W[ward]
over = w["spent_inr"] - w["budget_inr"]
pills = [f'<span class="pill {"red" if over > 0 else "green"}">{"Over budget by " + fmt_inr(over) if over > 0 else "Under budget by " + fmt_inr(-over)}</span>']
if w["low_lying_index"] >= 0.7:
    pills.append('<span class="pill pine">Flood-prone ward</span>')
c3.markdown(H(f'<div class="wardbar" style="padding-top:26px"><span class="big">{esc(ward)} · {esc(w["name"])}</span>{"".join(pills)}</div>'), unsafe_allow_html=True)
st.caption("Public board. Data: synthetic dataset. No names of people or parties.")

wf = flags_all[flags_all["ward"] == ward].copy()
wf["key"] = wf["job_id"].astype(str) + "|" + wf["type"] + "|" + wf["evidence"].map(lambda e: str(e.get("complaint_id", "")))
if "plan" not in st.session_state:
    st.session_state.plan = set()
days_left = lambda due: (pd.Timestamp(due) - AS_OF).days
ACTIONS, LINK_OK = get_actions()
status_of = lambda key: status_in(ACTIONS, key)
st.caption(("Phone-app link: " + (API_URL or f"shared store on this machine ({Path(_local_store().path).name})") + ". ") if LINK_OK else "Phone-app link is down: complaints and ward-office actions cannot be read or saved right now.")


def do_action(row, kind, text):
    """Save a ward-office decision to the shared store, so the phone app can show it on the road."""
    if not row["road_id"]:
        st.warning("This flag has no road linked, so it cannot carry a payment action.")
        return False
    ok = post_action(row["road_id"], kind, text, row["job_id"] or None, row["key"])
    if not ok:
        st.error("Could not save. The phone-app link is down.")
    return ok


def vmetrics(ward):
    days, mind = st.session_state.get("cv_days", 7), st.session_state.get("cv_min", 3)
    voice, _ = voice_by_road(ward, days)
    actions, _ = get_actions()
    sig, _ = get_signals()
    reps24, _ = get_reports(ward, 1)
    roads_in_ward = [rid for rid, r in ROADS.items() if r["ward"] == ward]
    crit = {rid: critical_check(rid, v, mind, actions) for rid, v in voice.items()}
    return dict(days=days, mind=mind, voice=voice, crit=crit, today=len(reps24), window=sum(v["reports"] for v in voice.values()),
                metoo=sum(sig.get(rid, {}).get("metoo", 0) for rid in roads_in_ward), n_crit=sum(1 for c in crit.values() if c["critical"]))


def live_kpis(m):
    return H('<div class="kpis" style="grid-template-columns:repeat(4,minmax(0,1fr))">' +
             kpi("Complaints today", str(m["today"]), "from the phone app", TANG) +
             kpi(f"Complaints, last {m['days']} days", str(m["window"]), "all roads in this ward", PINE) +
             kpi("“Me too” from the app", str(m["metoo"]), "one per device per road", TEAL2) +
             kpi("Roads in critical voice", str(m["n_crit"]), "voice, proof and a poor record agree", RED if m["n_crit"] else GREEN) + '</div>')


@st.fragment(run_every="5s")
def live_strip(ward):
    m = vmetrics(ward)
    st.markdown('<div class="livehead"><div class="sec">Live from the phone app</div><div class="note">updates every few seconds</div></div>', unsafe_allow_html=True)
    st.markdown(live_kpis(m), unsafe_allow_html=True)


@st.fragment(run_every="5s")
def live_panel(ward):
    m = vmetrics(ward)
    st.markdown('<div class="livehead"><div class="sec">Live from the phone app</div><div class="note">updates every few seconds</div></div>', unsafe_allow_html=True)
    st.markdown(live_kpis(m), unsafe_allow_html=True)
    if not m["voice"]:
        st.markdown('<div class="card"><h3>No complaints yet in this window</h3><div class="note">When someone raises a complaint in the phone app on a road in this ward, it appears here within seconds.</div></div>', unsafe_allow_html=True)
        return
    st.markdown('<div class="sec">Roads by live voice</div>', unsafe_allow_html=True)
    actions, _ = get_actions()
    sig, _ = get_signals()
    for rid, v in sorted(m["voice"].items(), key=lambda kv: -len(kv[1]["devices"])):
        c = m["crit"][rid]
        label, tone = ("Critical voice", "red") if c["critical"] else ("Building", "ochre")
        chips = check_chips(c)
        who = f' · contractor(s) to review: {esc(", ".join(c["contractors"]))}' if c["critical"] and c["contractors"] else ""
        st.markdown(H(f'<div class="vrow"><div class="vh"><b>{esc(ROADS[rid]["name"])}</b> <span style="color:var(--muted);font-size:.85rem">{esc(rid)}</span><span class="pill {tone}">{label}</span></div>'
                      f'<div class="vm">{v["reports"]} complaints from {len(v["devices"])} devices · {sig.get(rid, {}).get("metoo", 0)} Me too · last {esc(when(v["last"]))}{who}</div>'
                      f'<div class="chips">{chips}</div></div>'), unsafe_allow_html=True)
    st.markdown('<div class="sec">Latest complaints</div>', unsafe_allow_html=True)
    reps, _ = get_reports(ward, m["days"], 50)
    rows = pd.DataFrame([{"When": when(r["created_at"]), "Road": ROADS[r["road_id"]]["name"], "Complaint": r["note"] or "(photo or place only)",
                          "How located": VIA_LABEL.get(r["via"], r["via"]), "Photo": r["thumb"] or None, "Device": r["device"][:6]} for r in reps])
    st.dataframe(rows, hide_index=True, width="stretch", height=min(420, 44 * (len(rows) + 1)),
                 column_config={"When": st.column_config.TextColumn(width=110), "Road": st.column_config.TextColumn(width=150), "Complaint": st.column_config.TextColumn(width="large"),
                                "How located": st.column_config.TextColumn(width=130), "Photo": st.column_config.ImageColumn(width=70), "Device": st.column_config.TextColumn(width=80)})

tabs = st.tabs(["Overview", "Red flags", "Contractors", "Next rupee", "Citizen voice"])

# ================================================================ Overview
with tabs[0]:
    gap = w["spent_inr"] - w["fair_cost_inr"]
    red_open = [x for x in wf.itertuples() if x.type in ("overcharged", "billed_twice", "guarantee_paid", "built_weak")]
    pend = sum(1 for x in red_open if status_of(x.key) == "pending")
    answered = len(red_open) - pend
    overdue = sum(1 for x in red_open if status_of(x.key) == "pending" and days_left(x.due_date) < 0)
    spent_pct = w["spent_inr"] / w["budget_inr"] * 100
    st.markdown(H('<div class="kpis">' +
        kpi("Monsoon road budget", fmt_inr(w["budget_inr"]), "what the ward was given", PINE) +
        kpi("Spent", fmt_inr(w["spent_inr"]), f"{spent_pct:.0f} out of every 100 rupees of budget", RED if over > 0 else GREEN, (spent_pct / 1.5, 100 / 1.5)) +
        kpi("Should have cost", fmt_inr(w["fair_cost_inr"]), f"{fmt_inr(gap)} paid above BMC's own rate card", TEAL2) +
        kpi("Lost on fixes that broke within a month", fmt_inr(w["lost_failed_inr"]), "money that did not last", OCHRE) +
        kpi("Open questions", str(pend), "to answer within 30 days", TANG) + '</div>'), unsafe_allow_html=True)
    st.markdown(H(f"""<div class="strip">
<div class="chip"><span class="n" style="color:var(--green)">{answered}</span><span class="t">answered by BMC</span></div>
<div class="chip"><span class="n" style="color:var(--tang-t)">{pend}</span><span class="t">still pending</span></div>
<div class="chip"><span class="n" style="color:var(--red)">{overdue}</span><span class="t">overdue, past 30 days</span></div></div>"""), unsafe_allow_html=True)
    live_strip(ward)

    left, right = st.columns([2, 3], gap="large")
    with left:
        lines = "".join(f'<div class="sumline"><span class="n">{i}</span><span>{esc(t)}</span></div>' for i, t in enumerate(str(w["summary"][lang]).split("\n"), 1))
        st.markdown(H(f'<div class="card"><h3>In three lines</h3>{lines}</div>'), unsafe_allow_html=True)
    with right:
        df = pd.DataFrame([{"Ward": x["code"], "Budget": x["budget_inr"] / 1e5, "Spent": x["spent_inr"] / 1e5} for x in D["wards"]])
        long = df.melt("Ward", var_name="Kind", value_name="Lakh")
        long["Selected"] = long["Ward"] == ward
        chart = (alt.Chart(long).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
            x=alt.X("Ward:N", sort=None, axis=alt.Axis(labelAngle=0, title=None, labelFontSize=11, domain=False, ticks=False)),
            xOffset="Kind:N",
            y=alt.Y("Lakh:Q", title="₹ lakh", axis=alt.Axis(gridColor="#E4E4E7", domain=False, ticks=False)),
            color=alt.Color("Kind:N", scale=alt.Scale(domain=["Budget", "Spent"], range=[TEAL2, RED]), legend=alt.Legend(orient="top", title=None)),
            opacity=alt.condition("datum.Selected", alt.value(1), alt.value(0.42)),
            tooltip=["Ward", "Kind", alt.Tooltip("Lakh:Q", format=",.1f", title="₹ lakh")])
            .properties(height=250).configure_view(strokeWidth=0).configure(font="Inter"))
        over_codes = [x["code"] for x in D["wards"] if x["spent_inr"] > x["budget_inr"]]
        with st.container(border=True):
            st.markdown("<h3 style='margin:4px 0 0;font-size:1.1rem'>Budget against spend, all wards</h3>", unsafe_allow_html=True)
            st.altair_chart(chart, width="stretch")
            st.markdown(f'<div class="note">The selected ward is bold. Spent above budget: {", ".join(over_codes)}.</div>', unsafe_allow_html=True)

# ================================================================ Red flags
with tabs[1]:
    n_red = int(wf["type"].isin(RED_TYPES).sum()); n_amb = int(wf["type"].isin(AMBER_TYPES).sum())
    amt_red = int(wf[wf["type"].isin(["overcharged", "billed_twice", "guarantee_paid"])].drop_duplicates(["job_id", "type"])["amount_inr"].sum())
    st.markdown(H('<div class="kpis" style="grid-template-columns:repeat(4,minmax(0,1fr))">' +
        kpi("Red flags", str(n_red), "strong signals, each with proof", RED) + kpi("Amber flags", str(n_amb), "check with context", OCHRE) +
        kpi("Billed on flagged jobs", fmt_inr(amt_red), "overcharged, billed twice, guarantee road", PINE) +
        kpi("Pending answers", str(pend), "BMC can answer each one", TANG) + '</div>'), unsafe_allow_html=True)
    st.markdown('<div class="note">Normal price variation (about 30% either way) is not flagged. Every flag shows its proof.</div>', unsafe_allow_html=True)
    opts = ["All"] + [LABELS[t] for t in RED_TYPES + AMBER_TYPES]
    count_of = {LABELS[t]: int((wf["type"] == t).sum()) for t in RED_TYPES + AMBER_TYPES}
    pick = st.pills("Filter", opts, default="All", format_func=lambda o: o if o == "All" else f"{o}  {count_of[o]}", label_visibility="collapsed") or "All"
    chosen = [k for k, v in LABELS.items() if v == pick][:1]
    VM = vmetrics(ward)

    def sev_style(row):
        out = [""] * len(row)
        for i, c in enumerate(row.index):
            v = str(row[c])
            if c == "30-day clock":
                out[i] = "color:#8E1414;font-weight:700" if v == "Overdue" else ("color:#7C4A03;font-weight:600" if int(re.sub(r"\D", "", v) or 99) < 10 else "")
            if c == "Status":
                out[i] = {"Answered": "color:#106030;font-weight:700", "Pending": "color:#A8420A;font-weight:700"}.get(v, "color:#0F4C5C;font-weight:600")
            if c == "Voice":
                out[i] = "color:#8E1414;font-weight:700" if v == "Critical" else ("color:#7C4A03;font-weight:600" if v != "" else "")
        return out

    def table(types, title, tone, tag):
        d = wf[wf["type"].isin(types)].copy()
        if chosen:
            d = d[d["type"].isin(chosen)]
        st.markdown(f'<div class="sec">{title}<span class="pill {tone}">{len(d)}</span></div>', unsafe_allow_html=True)
        if d.empty:
            st.markdown('<div class="note">Nothing here for the current filter.</div>', unsafe_allow_html=True)
            return
        show = pd.DataFrame({
            "Job": d["job_id"].values, "Type": d["type"].map(LABELS).values, "Road": d["road_name"].values, "Contractor": d["contractor"].values,
            "Amount": d["amount_inr"].map(fmt_inr).values, "Fair cost": d["fair_inr"].map(fmt_inr).values,
            "30-day clock": d["due_date"].map(lambda x: "Overdue" if days_left(x) < 0 else f"{days_left(x)} days left").values,
            "Status": d["key"].map(lambda k: STATUS[status_of(k)]).values,
            "Voice": d["road_id"].map(lambda rid: "" if rid not in VM["voice"] else ("Critical" if VM["crit"][rid]["critical"] else f"{len(VM['voice'][rid]['devices'])} devices")).values,
            "Why flagged": d["reason"].map(lambda r: r[lang]).values})  # the long column goes last so nothing important is cut off
        ev = st.dataframe(show.style.apply(sev_style, axis=1), hide_index=True, width="stretch", height=min(420, 38 * (len(show) + 1)),
                          on_select="rerun", selection_mode="single-row", key=f"tbl_{tag}",
                          column_config={"Job": st.column_config.TextColumn(width=96), "Type": st.column_config.TextColumn(width=118), "Road": st.column_config.TextColumn(width=140), "Contractor": st.column_config.TextColumn(width=150), "Amount": st.column_config.TextColumn(width=82), "Fair cost": st.column_config.TextColumn(width=82), "30-day clock": st.column_config.TextColumn("Clock", width=96), "Status": st.column_config.TextColumn(width=78), "Voice": st.column_config.TextColumn(width=84), "Why flagged": st.column_config.TextColumn(width="large")})
        target = d.iloc[ev.selection.rows[0]] if ev.selection.rows else None
        if target is None and st.query_params.get("flag"):  # a shareable link to one flag: ?flag=WO25-02068
            hit = d[d["job_id"] == st.query_params.get("flag")]
            target = hit.iloc[0] if len(hit) else None
        if target is not None:
            evidence(target)

    def evidence(row):
        e, k = row["evidence"], row["key"]
        with st.container(border=True):
            st.markdown(H(f'<div class="ev"><span class="pill {"red" if row["type"] in RED_TYPES else "ochre"}">{LABELS[row["type"]]}</span> '
                          f'<b style="font-size:1.05rem">{esc(row["job_id"] or "No job")} · {esc(row["road_name"])}</b>'
                          f'<div style="margin:8px 0">{esc(row["reason"][lang])}</div></div>'), unsafe_allow_html=True)
            facts = {k2: v for k2, v in e.items() if v not in ("", None) and not isinstance(v, (list, dict))}
            cols = st.columns(min(4, max(1, len(facts))))
            for i, (a, b) in enumerate(facts.items()):
                cols[i % len(cols)].markdown(H(f'<div class="ev"><div class="k">{esc(a.replace("_", " ").capitalize())}</div><div class="val">{esc(b)}</div></div>'), unsafe_allow_html=True)
            if row["type"] == "overcharged":
                st.markdown(f'Rate card: **{fmt_inr(e["rate_card_inr"])}** {e["unit"]} × {e["area_sqm"]} gives a fair cost of **{fmt_inr(row["fair_inr"])}**. Billed **{fmt_inr(row["amount_inr"])}**, which is **{e["ratio"]}×** the normal rate.')
            v = VM["voice"].get(row["road_id"])
            if v:
                c = VM["crit"][row["road_id"]]
                st.markdown(H(f'<div class="vrow"><div class="vh"><b>Voice on this road</b><span class="pill {"red" if c["critical"] else "ochre"}">{"Critical voice" if c["critical"] else "Building"}</span></div>'
                              f'<div class="vm">{v["reports"]} complaints from {len(v["devices"])} devices in the last {VM["days"]} days · last {esc(when(v["last"]))}</div>'
                              f'<div class="chips">{check_chips(c)}</div></div>'), unsafe_allow_html=True)
                for _, note in sorted(v["notes"], reverse=True)[:3]:
                    st.markdown(f"> {note}")
                if v["thumbs"]:
                    st.image(v["thumbs"][:4], width=110)
            st.markdown('<div class="note" style="margin-top:10px">For BMC</div>', unsafe_allow_html=True)
            cur = ACTIONS.get(k)
            if cur:
                st.info(f"Latest ward-office action: **{STATUS[status_in(ACTIONS, k)]}**" + (f" · {cur['text']}" if cur.get("text") else "") + f" · {when(cur['at'])}")
            b = st.columns(4)
            if b[0].button("Answer", key=f"a_{k}", type="primary"):
                st.session_state.show_answer = k
            if b[1].button("Hold payment", key=f"h_{k}"):
                if do_action(row, "hold", "Payment held pending review"):
                    st.rerun()
            if row["type"] in ("guarantee_paid", "built_weak") and b[2].button("Ask contractor to fix free", key=f"f_{k}"):
                if do_action(row, "fixfree", "Original contractor asked to repair it free under the guarantee"):
                    st.rerun()
            if row["type"] == "utility_damage" and b[2].button("Recover from utility", key=f"r_{k}"):
                if do_action(row, "recover", "Cost to be recovered from the utility company"):
                    st.rerun()
            if status_in(ACTIONS, k) == "held" and b[3].button("Release hold", key=f"x_{k}"):
                if do_action(row, "release", "Payment hold released"):
                    st.rerun()
            if st.session_state.get("show_answer") == k:
                txt = st.text_area("Public answer (shown on the road in the phone app)", key=f"t_{k}", value="")
                if st.button("Publish answer", key=f"p_{k}") and txt.strip():
                    if do_action(row, "answer", txt.strip()):
                        st.session_state.show_answer = None
                        st.rerun()

    table(RED_TYPES, "Red flags: strong signals", "red", "red")
    table(AMBER_TYPES, "Amber: check with context, not counted as corruption", "ochre", "amber")

# ================================================================ Contractors
with tabs[2]:
    scope = st.segmented_control("Compare", ["This ward", "Whole city"], default="This ward") or "This ward"
    target = ward if scope == "This ward" else "ALL"
    cards = sorted([c for c in D["contractors"] if c["ward"] == target], key=lambda c: -c["score"])
    if scope == "This ward":
        msg = f"{w['name']} is flood-prone. Contractors are compared only within this ward." if w["low_lying_index"] >= 0.7 else "Contractors are compared only within this ward."
        st.markdown(f'<div class="note">{esc(msg)}</div>', unsafe_allow_html=True)
    cols = st.columns(2, gap="large")
    for i, c in enumerate(cards):
        lasted, ontime = c["lasted"] * 100, c["on_time"] * 100
        pc = "green" if c.get("price_label") == "Normal" else "ochre"
        with cols[i % 2]:
            st.markdown(H(f"""<div class="card" style="margin-bottom:16px"><div class="ct"><div class="grade {c['grade']}">{c['grade']}</div>
<div style="flex:1;min-width:0"><div class="nm">{esc(c['name'])}</div><div class="jb">{c['jobs']} jobs {'in this ward' if c['ward'] != 'ALL' else 'across the city'}</div>
<div class="chips" style="margin:8px 0 0"><span class="{'g' if pc == 'green' else ''}">Price: {esc(c.get('price_label', ''))}</span><span>{c['red_flags']} red flags</span></div></div></div>
<div class="m"><div class="t"><span>Fixes that lasted a month</span><b>{c['lasted_n']} of {c['jobs']}</b></div><div class="bar"><i style="width:{lasted:.0f}%"></i></div></div>
<div class="m"><div class="t"><span>Finished on time</span><b>{c['on_time_n']} of {c['jobs']}</b></div><div class="bar"><i style="width:{ontime:.0f}%"></i></div></div>
<div class="ctnote">{esc(c['note'])}</div></div>"""), unsafe_allow_html=True)
    if not cards:
        st.markdown('<div class="note">No contractor work recorded.</div>', unsafe_allow_html=True)

# ================================================================ Next rupee
with tabs[3]:
    rows = D["next_rupee"][ward]
    plan = sum(r["est_cost_inr"] for r in rows if r["road_id"] in st.session_state.plan)
    left_b = w["budget_inr"] - w["spent_inr"]
    st.markdown(H('<div class="kpis" style="grid-template-columns:repeat(3,minmax(0,1fr))">' +
        kpi("Plan cost", fmt_inr(plan), f"{len(st.session_state.plan)} of {len(rows)} roads added", PINE) +
        kpi("Ward budget left", fmt_inr(left_b) if left_b >= 0 else "Over budget", "" if left_b >= 0 else f"already {fmt_inr(-left_b)} over", GREEN if left_b >= 0 else RED) +
        kpi("Roads under guarantee", str(sum(1 for r in rows if r["est_cost_inr"] == 0)), "BMC pays nothing for these", TEAL2) + '</div>'), unsafe_allow_html=True)
    st.markdown('<div class="note">Cold-mix patches break most often, about 1 in 4 repairs fail within a month. One lasting repair costs less than five patches.</div>', unsafe_allow_html=True)
    for i, r in enumerate(rows, 1):
        on = r["road_id"] in st.session_state.plan
        with st.container(border=True):
            a, b, c = st.columns([7, 2, 2], vertical_alignment="center")
            chips = "".join(f'<span class="{"g" if "guarantee" in x.lower() else ""}">{esc(x)}</span>' for x in r["reasons"])
            a.markdown(H(f'<div class="nr"><div class="rank">{i}</div><div class="b"><div class="name">{esc(r["name"])} <span style="color:var(--muted);font-weight:500;font-size:.85rem">{esc(r["road_id"])}</span></div>'
                         f'<div class="chips">{chips}</div><div class="fix">{esc(r["fix"])}</div></div></div>'), unsafe_allow_html=True)
            zero = r["est_cost_inr"] == 0
            b.markdown(H(f'<div class="cost {"zero" if zero else ""}">{fmt_inr(r["est_cost_inr"])}<small>risk score {r["risk"]}</small></div>'), unsafe_allow_html=True)
            if c.button("Remove from plan" if on else "Add to plan", key=f"plan_{r['road_id']}", type="secondary" if on else "primary", width="stretch"):
                (st.session_state.plan.discard if on else st.session_state.plan.add)(r["road_id"]); st.rerun()

# ================================================================ Citizen voice
with tabs[4]:
    cz = D["citizen"][ward]
    with st.expander("Critical-voice rule (a proposal to tune in a pilot ward)"):
        st.markdown("A road is in **critical voice** only when the citizens' voice **and** the proof agree: enough independent devices, an unanswered red flag, a failed fix, and a contractor with a poor fair-measured record (D or worse). Voice alone never counts, and a person at the ward office makes any decision.")
        rc1, rc2 = st.columns(2)
        rc1.slider("Independent devices needed", 1, 50, 3, key="cv_min")
        rc2.slider("Look back (days)", 1, 30, 7, key="cv_days")
    live_panel(ward)
    st.markdown('<div class="sec">From the dataset</div><div class="note">The history below is the synthetic dataset, not the phone app.</div>', unsafe_allow_html=True)
    st.markdown(H(f"""<div class="flow">
<div class="st"><div class="n">{cz['complaints']}</div><div class="t">complaints received</div></div>
<div class="st"><div class="n">{cz['duplicates_merged']}</div><div class="t">duplicates merged</div></div>
<div class="st"><div class="n">{cz['issues']}</div><div class="t">distinct issues</div></div>
<div class="st bad"><div class="n">{cz['never_fixed']}</div><div class="t">never fixed</div></div>
<div class="st warn"><div class="n">{cz['closed_by_old']}</div><div class="t">closed by an older repair</div></div></div>"""), unsafe_allow_html=True)
    l, r_ = st.columns([3, 2], gap="large")
    with l:
        lines = "".join(f'<div class="sumline"><span class="n">{i}</span><span>{esc(t)}</span></div>' for i, t in enumerate(str(w["citizen_voice"][lang]).split("\n"), 1))
        st.markdown(H(f'<div class="card" style="margin-top:14px"><h3>What citizens are saying</h3>{lines}</div>'), unsafe_allow_html=True)
        st.markdown('<div class="sec">Complaints, issues and jobs</div>', unsafe_allow_html=True)
        il = pd.DataFrame(cz["issue_list"])
        if len(il):
            il["Status"] = il["status"]
            view = il.rename(columns={"title": "Issue", "quote": "Complaint, in its own language", "merged": "Merged", "job": "Job", "upvotes": "Me too"})[
                ["Issue", "Complaint, in its own language", "Merged", "Job", "Status", "Me too"]]
            sty = view.style.map(lambda v: {"Fixed": "color:#106030;font-weight:700", "Never fixed": "color:#8E1414;font-weight:700", "Closed by old repair": "color:#7C4A03;font-weight:700"}.get(v, ""), subset=["Status"])
            st.dataframe(sty, hide_index=True, width="stretch", height=430,
                         column_config={"Complaint, in its own language": st.column_config.TextColumn(width="large"), "Me too": st.column_config.NumberColumn(format="%d")})
    with r_:
        top = cz["me_too_roads"]
        mx = max([x["me_too"] for x in top] or [1])
        bars = "".join(f'<div class="lb"><span class="nm">{esc(x["name"])} <small style="color:var(--muted)">{esc(x["road_id"])}</small></span><span class="bar"><i style="width:{x["me_too"] / mx * 100:.0f}%"></i></span><span class="v">{x["me_too"]}</span></div>' for x in top)
        st.markdown(H(f'<div class="card" style="margin-top:14px"><h3>Roads with the most “Me too”</h3>{bars or "<div class=note>No taps yet.</div>"}</div>'), unsafe_allow_html=True)

st.markdown(H('<div class="foot">Dataset: Follow the Rupee: Mumbai Monsoon Roads by Sumeet Doshi for Claude Impact Lab Mumbai, CC BY 4.0. All data is synthetic and all contractors are fictional. Do not use it to judge any real person, contractor or department.</div>'), unsafe_allow_html=True)
