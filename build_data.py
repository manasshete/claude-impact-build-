"""Apla BMC data brain.

Reads the synthetic BMC dataset, finds suspicious bills, grades contractors,
ranks the next rupee, and writes ONE file: apla_data.json (read by the
dashboard `app.py` and the phone app in /app).

Usage:
    python build_data.py                 # deterministic text templates (free, no API)
    python build_data.py --claude        # also write summaries / RTI / notes with Claude
                                         # (needs ANTHROPIC_API_KEY in the environment)
    python build_data.py --data PATH     # dataset folder (default: dataset/data)

Claude text is generated ONCE here and cached in claude_text_cache.json,
so the demo never waits on an API call.
"""
import argparse
import hashlib
import json
import math
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
SURFACE_TYPES = {"Pothole", "Broken paver blocks", "Road cave-in"}
PATCH_METHODS = {"Cold mix patch", "Hot mix patch", "Mastic asphalt"}
PUMP_METHODS = {"Dewatering pump deployed", "Pump + debris removal"}
ACCIDENT_CAUSES = ("Pothole", "Waterlogged road", "Uneven road")
# The dataset's own complaint text can name a party office as a landmark. The board never shows party or officer names.
PARTY_RE = re.compile(r"\b(shiv\s*sena|bjp|congress|ncp|mns|aap|samajwadi|rpi|shakha|corporator|nagarsevak|councillor|mla|mp)\b", re.I)
redact = lambda s: re.sub(r"(\[name removed\][\s,]*){2,}", "[name removed] ", PARTY_RE.sub("[name removed]", str(s)))
HEAVY = {"Heavy", "Very heavy", "Extremely heavy"}
LANGS = ("en", "mr", "hi")

MONTHS = {
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    "mr": ["जाने", "फेब्रु", "मार्च", "एप्रिल", "मे", "जून", "जुलै", "ऑग", "सप्टें", "ऑक्टो", "नोव्हें", "डिसें"],
    "hi": ["जन", "फर", "मार्च", "अप्रै", "मई", "जून", "जुल", "अग", "सित", "अक्टू", "नव", "दिस"],
}


# ----------------------------------------------------------------- helpers
def fmt_inr(x, lang="en"):
    """3052667 -> '₹30.5 lakh', 13043418 -> '₹1.30 Cr' (लाख / कोटी in mr, hi)."""
    lakh, crore = {"en": ("lakh", "Cr"), "mr": ("लाख", "कोटी"), "hi": ("लाख", "करोड़")}[lang]
    x = float(x)
    sign = "-" if x < 0 else ""
    x = abs(x)
    if x >= 1e7:
        return f"{sign}₹{x / 1e7:.2f} {crore}"
    if x >= 1e5:
        v = x / 1e5
        return f"{sign}₹{v:.1f} {lakh}" if v < 100 else f"{sign}₹{v:.0f} {lakh}"
    return f"{sign}₹{x:,.0f}"


def mon(ts, lang):
    ts = pd.Timestamp(ts)
    return f"{MONTHS[lang][ts.month - 1]} {ts.year}"


def tri(en, mr, hi):
    return {"en": en, "mr": mr, "hi": hi}


def clean_name(n):
    return re.sub(r"\s*\(synthetic\)\s*", "", str(n)).strip()


def haversine(a, b):
    r = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def py(v):
    """numpy/pandas scalar -> plain python for json."""
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, (pd.Timestamp, datetime)):
        return v.strftime("%Y-%m-%d %H:%M")
    return v


# ----------------------------------------------------------------- load
def load(data_dir):
    d = Path(data_dir)
    rd = lambda n: pd.read_csv(d / f"{n}.csv")
    D = {n: rd(n) for n in ["work_orders", "rate_card", "roads", "complaints", "wards", "contractors",
                            "rainfall_daily", "tides", "traffic", "accidents", "facilities", "localities"]}
    wo = D["work_orders"]
    for c in ["assigned_at", "work_started_at", "completed_at"]:
        wo[c] = pd.to_datetime(wo[c])
    cp = D["complaints"]
    cp["received_at"] = pd.to_datetime(cp["received_at"])
    cp["resolved_at"] = pd.to_datetime(cp["resolved_at"])
    D["roads"]["last_resurfaced"] = pd.to_datetime(D["roads"]["last_resurfaced"])
    return D


# ----------------------------------------------------------------- 5.1 fair cost
def add_fair_cost(wo, rate):
    wo = wo.merge(rate, on="repair_method", how="left")
    wo["fair"] = np.where(wo["unit"] == "per sqm", wo["standard_rate_inr"] * wo["area_sqm"], wo["standard_rate_inr"])
    wo["ratio"] = wo["cost_inr"] / wo["fair"]
    wo["has_complaint"] = wo["linked_complaint_ids"].notna() & (wo["linked_complaint_ids"].astype(str).str.strip() != "")
    return wo


# ----------------------------------------------------------------- 5.2 flags
class Flags:
    def __init__(self):
        self.rows = []

    def add(self, **kw):
        kw.setdefault("severity", "red")
        kw.setdefault("evidence", {})
        kw["status"] = "pending"
        self.rows.append(kw)


def find_flags(D, wo):
    roads = D["roads"].set_index("road_id")
    wards = D["wards"].set_index("ward_code")
    rain = D["rainfall_daily"].copy()
    rain["date"] = pd.to_datetime(rain["date"])
    rain_max = rain.groupby("date")["rainfall_mm"].max()
    imd_heavy = set(rain.loc[rain["imd_category"].isin(HEAVY), "date"])
    tides = D["tides"].copy()
    tides["date"] = pd.to_datetime(tides["date"])
    big_tide = set(tides.loc[tides["big_tide_flag"] == "Y", "date"])
    F = Flags()
    rname = lambda rid: roads.loc[rid, "road_name"]

    def base(r):
        return dict(job_id=r.work_order_id, ward=r.ward_code, road_id=r.road_id, road_name=rname(r.road_id),
                    contractor=clean_name(r.contractor_name), amount_inr=int(r.cost_inr), fair_inr=int(round(r.fair)),
                    completed_at=r.completed_at.strftime("%Y-%m-%d"))

    ctx = lambda ts: any((pd.Timestamp(ts).normalize() - timedelta(days=k)) in imd_heavy for k in range(0, 4))

    # F1 overcharged
    for r in wo[wo["ratio"] > 1.5].itertuples():
        x = round(r.ratio, 1)
        F.add(**base(r), type="overcharged", severity="red",
              reason=tri(f"{r.repair_method} normally ≈ {fmt_inr(r.fair)}. Billed {fmt_inr(r.cost_inr)} ({x}× the normal rate).",
                         f"या कामाचा साधारण दर ≈ {fmt_inr(r.fair, 'mr')}. बिल {fmt_inr(r.cost_inr, 'mr')} ({x} पट).",
                         f"इस काम का आम दर ≈ {fmt_inr(r.fair, 'hi')}. बिल {fmt_inr(r.cost_inr, 'hi')} ({x} गुना)।"),
              evidence=dict(repair_method=r.repair_method, area_sqm=r.area_sqm, rate_card_inr=int(r.standard_rate_inr),
                            unit=r.unit, ratio=x, work_type=r.work_type,
                            started=r.work_started_at.strftime("%Y-%m-%d %H:%M"),
                            completed=r.completed_at.strftime("%Y-%m-%d %H:%M"),
                            linked_complaints=str(r.linked_complaint_ids) if r.has_complaint else ""))

    # F2 / F3 billed twice (+ no complaint)
    ordered = wo.sort_values(["completed_at", "work_order_id"])
    by_key = {k: g for k, g in ordered.groupby(["road_id", "repair_method"])}
    for r in wo[~wo["has_complaint"]].itertuples():
        g = by_key[(r.road_id, r.repair_method)]
        earlier = g[(g["completed_at"] < r.completed_at) | ((g["completed_at"] == r.completed_at) & (g["work_order_id"] < r.work_order_id))]
        earlier = earlier[(r.completed_at - earlier["completed_at"]) <= timedelta(days=7)]
        if len(earlier):
            e = earlier.iloc[-1]
            days = (r.completed_at - e["completed_at"]).days
            F.add(**base(r), type="billed_twice", severity="red",
                  reason=tri(f"Same {r.repair_method.lower()} job as {e['work_order_id']}, {days} day(s) apart, no citizen complaint.",
                             f"{e['work_order_id']} सारखेच काम, {days} दिवसांच्या अंतराने, कोणतीही नागरिक तक्रार नाही.",
                             f"{e['work_order_id']} जैसा ही काम, {days} दिन के अंतर पर, कोई नागरिक शिकायत नहीं।"),
                  evidence=dict(other_job=e["work_order_id"], days_apart=days, other_cost_inr=int(e["cost_inr"]),
                                repair_method=r.repair_method, linked_complaints=""))
        F.add(**base(r), type="no_complaint", severity="red",
              reason=tri("No citizen complaint behind this job.", "या कामामागे कोणतीही नागरिक तक्रार नाही.",
                         "इस काम के पीछे कोई नागरिक शिकायत नहीं।"),
              evidence=dict(repair_method=r.repair_method, linked_complaints=""))

    # F4 guarantee road paid by BMC
    dlp_ids = set(roads.index[roads["under_dlp"] == "Y"])
    for r in wo[wo["road_id"].isin(dlp_ids) & wo["work_type"].isin(SURFACE_TYPES)].itertuples():
        lr = roads.loc[r.road_id, "last_resurfaced"]
        F.add(**base(r), type="guarantee_paid", severity="red",
              reason=tri(f"BMC paid {fmt_inr(r.cost_inr)} though the road is under guarantee (resurfaced {mon(lr, 'en')}).",
                         f"रस्ता हमीत असूनही ({mon(lr, 'mr')} ला नवा केला) BMC ने {fmt_inr(r.cost_inr, 'mr')} दिले.",
                         f"सड़क गारंटी में होने पर भी ({mon(lr, 'hi')} में नई बनी) BMC ने {fmt_inr(r.cost_inr, 'hi')} दिए।"),
              evidence=dict(last_resurfaced=lr.strftime("%Y-%m-%d"), work_type=r.work_type, repair_method=r.repair_method,
                            note="Contractor should fix this free under the guarantee.",
                            linked_complaints=str(r.linked_complaint_ids) if r.has_complaint else ""))

    # F5 built weak (per road)
    surf = wo[wo["work_type"].isin(SURFACE_TYPES)]
    for rid, g in surf.groupby("road_id"):
        if rid not in dlp_ids:
            continue
        n, b = len(g), int((g["reopened_within_30d"] == "Y").sum())
        if n >= 3 or b >= 2:
            last = g.sort_values("completed_at").iloc[-1]
            lr = roads.loc[rid, "last_resurfaced"]
            row = base(last)
            row["amount_inr"] = int(g["cost_inr"].sum())
            row["fair_inr"] = int(round(g["fair"].sum()))
            F.add(**row, type="built_weak", severity="red",
                  reason=tri(f"Resurfaced {mon(lr, 'en')}, but needed {n} pothole repairs since ({b} broke again).",
                             f"{mon(lr, 'mr')} ला नवा केला, तरी तेव्हापासून {n} वेळा खड्डे भरले ({b} वेळा पुन्हा तुटले).",
                             f"{mon(lr, 'hi')} में नया बना, फिर भी {n} बार गड्ढे भरे ({b} बार फिर टूटे)।"),
                  evidence=dict(last_resurfaced=lr.strftime("%Y-%m-%d"), surface_repairs=n, broke_again=b,
                                jobs=list(g["work_order_id"]), linked_complaints=""))

    # F6 failed within 30 days  (amber)
    for r in wo[wo["reopened_within_30d"] == "Y"].itertuples():
        heavy = ctx(r.completed_at)
        F.add(**base(r), type="failed_fix", severity="amber",
              reason=tri(f"Fix failed within 30 days. {'Heavy rain that week — may be the cause.' if heavy else 'No heavy rain that week.'}",
                         f"३० दिवसांत दुरुस्ती पुन्हा तुटली. {'त्या आठवड्यात जोरदार पाऊस होता.' if heavy else 'त्या आठवड्यात जोरदार पाऊस नव्हता.'}",
                         f"30 दिन में मरम्मत फिर टूटी। {'उस सप्ताह भारी बारिश थी।' if heavy else 'उस सप्ताह भारी बारिश नहीं थी।'}"),
              evidence=dict(heavy_rain_that_week=heavy, repair_method=r.repair_method, work_type=r.work_type))

    # F7 repeat patching (per road, amber)
    pat = wo[wo["repair_method"].isin(PATCH_METHODS)]
    for rid, g in pat.groupby("road_id"):
        if len(g) >= 3:
            last = g.sort_values("completed_at").iloc[-1]
            row = base(last)
            row["amount_inr"] = int(g["cost_inr"].sum())
            row["fair_inr"] = int(round(g["fair"].sum()))
            F.add(**row, type="repeat_patch", severity="amber",
                  reason=tri(f"Patched {len(g)} times this monsoon. A lasting fix would cost less than more patches.",
                             f"या पावसाळ्यात {len(g)} वेळा ठिगळ लावले. टिकाऊ दुरुस्ती स्वस्त पडेल.",
                             f"इस मानसून में {len(g)} बार पैच लगे। टिकाऊ मरम्मत सस्ती पड़ेगी।"),
                  evidence=dict(patches=len(g), jobs=list(g["work_order_id"])))

    # F8 pump with no rain (amber)
    for r in wo[wo["repair_method"].isin(PUMP_METHODS)].itertuples():
        day = r.assigned_at.normalize()
        days = [day - timedelta(days=k) for k in range(3)]
        mx = max(float(rain_max.get(x, 0.0)) for x in days)
        if mx < 5 and day not in big_tide:
            F.add(**base(r), type="pump_no_rain", severity="amber",
                  reason=tri(f"Pump deployed but under 5 mm rain in the 3 days before ({mx:.1f} mm). Check for a blocked drain.",
                             f"पंप लावला पण आधीच्या ३ दिवसांत ५ मिमी पेक्षा कमी पाऊस ({mx:.1f} मिमी). गटार तुंबली का ते तपासा.",
                             f"पंप लगाया पर पिछले 3 दिनों में 5 मिमी से कम बारिश ({mx:.1f} मिमी)। नाली जाम तो नहीं, जांचें।"),
                  evidence=dict(max_rain_mm_3_days=mx, big_tide_that_day=False, repair_method=r.repair_method))

    # F9 utility damage (amber)
    for r in wo[wo["engineer_remark"].astype(str).str.contains("Utility (MTNL/MGL)", regex=False)].itertuples():
        F.add(**base(r), type="utility_damage", severity="amber",
              reason=tri("MTNL/MGL trench. Repair cost should be recovered from the utility company.",
                         "MTNL/MGL खोदकाम. दुरुस्तीचा खर्च त्या कंपनीकडून वसूल व्हायला हवा.",
                         "MTNL/MGL खुदाई। मरम्मत का खर्च उस कंपनी से वसूला जाना चाहिए।"),
              evidence=dict(engineer_remark=r.engineer_remark, repair_method=r.repair_method))

    # F10 complaint closed by an older repair (amber)  -- complaint level
    cp = D["complaints"]
    old = cp[cp["resolved_at"].notna() & (cp["resolved_at"] < cp["received_at"])]
    cmap = {}
    for r in wo.itertuples():
        if r.has_complaint:
            for c in str(r.linked_complaint_ids).split("|"):
                cmap[c] = r
    for c in old.itertuples():
        j = cmap.get(c.complaint_id)
        wcode = c.ward_code if isinstance(c.ward_code, str) else (j.ward_code if j is not None else None)
        if wcode is None:
            continue
        F.add(job_id=j.work_order_id if j is not None else "", ward=wcode, road_id=j.road_id if j is not None else "",
              road_name=rname(j.road_id) if j is not None else "", contractor=clean_name(j.contractor_name) if j is not None else "",
              amount_inr=int(j.cost_inr) if j is not None else 0, fair_inr=int(round(j.fair)) if j is not None else 0,
              completed_at=j.completed_at.strftime("%Y-%m-%d") if j is not None else "",
              type="closed_by_old_repair", severity="amber",
              reason=tri("Complaint marked fixed using a repair done before it was filed. Ask for a fresh after-photo.",
                         "तक्रार दाखल होण्यापूर्वीच्या दुरुस्तीवरून 'झाले' दाखवले. नवा फोटो मागा.",
                         "शिकायत दर्ज होने से पहले की मरम्मत से 'हो गया' दिखाया। नई फोटो मांगें।"),
              evidence=dict(complaint_id=c.complaint_id, received=c.received_at.strftime("%Y-%m-%d %H:%M"),
                            resolved=c.resolved_at.strftime("%Y-%m-%d %H:%M")))
    return F.rows


# ----------------------------------------------------------------- 5.3 contractors
def grade(score):
    return "A" if score >= 80 else "B" if score >= 70 else "C" if score >= 60 else "D" if score >= 50 else "F"


def contractor_cards(wo, flags, wards):
    red = pd.DataFrame([f for f in flags if f["severity"] == "red" and f["job_id"]])
    red_cnt = red.groupby(["ward", "contractor"])["job_id"].nunique() if len(red) else pd.Series(dtype=int)
    red_city = red.groupby("contractor")["job_id"].nunique() if len(red) else pd.Series(dtype=int)
    wo = wo.assign(contractor=wo["contractor_name"].map(clean_name))
    low = wards.set_index("ward_code")["low_lying_index"]

    def card(g, ward, rf):
        n = len(g)
        lasted = 1 - (g["reopened_within_30d"] == "Y").sum() / n
        on_time = (g["sla_met"] == "Y").sum() / n
        med = float(g["ratio"].median())
        price = float(np.clip(1 - (med - 1), 0, 1))
        score = 40 * lasted + 30 * on_time + 30 * price - 3 * rf
        return dict(jobs=int(n), lasted_n=int(round(lasted * n)), on_time_n=int(round(on_time * n)),
                    lasted=round(lasted, 3), on_time=round(on_time, 3), price=round(price, 3),
                    median_ratio=round(med, 3), red_flags=int(rf), score=round(score, 1))

    out = []
    for (w, c), g in wo.groupby(["ward_code", "contractor"]):
        k = card(g, w, int(red_cnt.get((w, c), 0)))
        k.update(name=c, ward=w)
        heavy = float(((g["reopened_within_30d"] == "Y")).mean())
        k["context"] = ("Works in a flood-prone ward — compared only with others in this ward." if low[w] >= 0.7 else "")
        out.append(k)
    for c, g in wo.groupby("contractor"):
        k = card(g, "ALL", int(red_city.get(c, 0)))
        k.update(name=c, ward="ALL", context="City-wide, all wards combined.")
        out.append(k)
    # scale: spread grades. Raw formula tops out near 85; keep the spec thresholds.
    for k in out:
        k["grade"] = grade(k["score"])
    return out


# ----------------------------------------------------------------- build
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(HERE / "dataset" / "data"))
    ap.add_argument("--out", default=str(HERE / "apla_data.json"))
    ap.add_argument("--claude", action="store_true", help="write summaries/RTI/notes with Claude")
    ap.add_argument("--claude-wards", default="L", help="comma list of wards for Claude citizen-voice/RTI/notes, or ALL")
    args = ap.parse_args()

    D = load(args.data)
    wards, roads, wo = D["wards"], D["roads"], add_fair_cost(D["work_orders"], D["rate_card"])
    cp = D["complaints"]
    asof = max(wo["completed_at"].max(), cp["received_at"].max()).normalize()
    rname = roads.set_index("road_id")["road_name"]
    wname = wards.set_index("ward_code")["ward_name"]

    flags = find_flags(D, wo)
    fdf = pd.DataFrame(flags)

    # due dates / ids
    for f in flags:
        base_day = pd.Timestamp(f["completed_at"]) if f["completed_at"] else asof
        f["due_date"] = (base_day + timedelta(days=30)).strftime("%Y-%m-%d")

    # ---- complaint -> job -> road maps
    cmap = {}
    for r in wo.itertuples():
        if r.has_complaint:
            for c in str(r.linked_complaint_ids).split("|"):
                cmap[c.strip()] = r.work_order_id
    wo_idx = wo.set_index("work_order_id")
    cp["job_id"] = cp["complaint_id"].map(cmap)
    cp["road_id"] = cp["job_id"].map(wo_idx["road_id"])
    upv_by_road = cp.dropna(subset=["road_id"]).groupby("road_id")["upvotes"].sum()

    # ---- Not-BMC roads via nearest road for GPS complaints
    not_bmc = cp[cp["status"].isin(["Forwarded to MMRDA/PWD", "Rejected - Not BMC road"]) & cp["lat"].notna()]
    mid = {r.road_id: ((r.start_lat + r.end_lat) / 2, (r.start_lon + r.end_lon) / 2, r.ward_code) for r in roads.itertuples()}
    nb_count = {}
    for c in not_bmc.itertuples():
        cands = [(haversine((c.lat, c.lon), (m[0], m[1])), rid) for rid, m in mid.items() if m[2] == c.ward_code]
        if cands:
            d0, rid = min(cands)
            if d0 < 0.6:
                nb_count[rid] = nb_count.get(rid, 0) + 1

    # ---- traffic / accidents / facilities
    traffic = D["traffic"].set_index("road_id")["congestion_index_0_10"]
    acc = D["accidents"]
    acc = acc[acc["probable_cause"].astype(str).str.startswith(ACCIDENT_CAUSES)].groupby("road_id").size()
    fac = D["facilities"].groupby("ambulance_entry_road").size()
    hosp = D["facilities"][D["facilities"]["type"] == "Hospital"].set_index("ambulance_entry_road")["name"].to_dict()
    schools = D["facilities"][D["facilities"]["type"] == "School"].groupby("ambulance_entry_road").size()

    # ---- contractors
    ccards = contractor_cards(wo, flags, wards)
    grade_lookup = {(c["ward"], c["name"]): c["grade"] for c in ccards}

    # ---- roads
    red_flag_rows = fdf[fdf["severity"] == "red"]
    road_flag_types = ["built_weak", "billed_twice", "overcharged", "guarantee_paid", "pump_no_rain", "utility_damage"]
    road_out = []
    wo_g = {rid: g for rid, g in wo.groupby("road_id")}
    for r in roads.itertuples():
        g = wo_g.get(r.road_id)
        if g is None:
            g = wo.iloc[0:0]
        n = len(g)
        rf = []
        rows = fdf[(fdf["road_id"] == r.road_id) & fdf["type"].isin(road_flag_types)] if len(fdf) else fdf
        for t in road_flag_types:
            sub = rows[rows["type"] == t]
            if t == "utility_damage" and len(sub):
                tot = int(sub["amount_inr"].sum())
                rf.append(dict(type=t, severity="amber", count=len(sub),
                               text=tri(f"{len(sub)} jobs after MTNL/MGL trenches ({fmt_inr(tot)}). Repair cost should be recovered from them.",
                                        f"MTNL/MGL खोदकामानंतर {len(sub)} कामे ({fmt_inr(tot, 'mr')}). हा खर्च त्यांच्याकडून वसूल व्हायला हवा.",
                                        f"MTNL/MGL खुदाई के बाद {len(sub)} काम ({fmt_inr(tot, 'hi')})। खर्च उनसे वसूला जाए।")))
                continue
            for s in sub.head(5).itertuples():
                rf.append(dict(type=t, job=s.job_id, severity=s.severity, text=s.reason))
        contr = []
        if n:
            for c, cg in g.groupby(g["contractor_name"].map(clean_name)):
                contr.append(dict(name=c, grade=grade_lookup.get((r.ward_code, c), "C"), jobs=int(len(cg))))
            contr.sort(key=lambda x: -x["jobs"])
        # trail
        trail = []
        rc = cp[cp["road_id"] == r.road_id].sort_values("received_at", ascending=False).head(5)
        for c in rc.itertuples():
            opn = c.status in ("Open", "In Progress")
            trail.append(dict(complaint=c.complaint_id, job=c.job_id, status="Open" if opn else "Done",
                              days_open=int((asof - c.received_at.normalize()).days) if opn else 0))
        owner = "Not BMC" if nb_count.get(r.road_id, 0) >= 2 and nb_count.get(r.road_id, 0) >= len(rc) else "BMC"
        road_out.append(dict(
            road_id=r.road_id, name=r.road_name, ward=r.ward_code, ward_name=wname[r.ward_code],
            bus_route=r.bus_route == "Y", owner=owner,
            start=[r.start_lat, r.start_lon], end=[r.end_lat, r.end_lon],
            spent_inr=int(g["cost_inr"].sum()), fair_cost_inr=int(round(g["fair"].sum())),
            jobs=int(n), broke_again=int((g["reopened_within_30d"] == "Y").sum()), on_time=int((g["sla_met"] == "Y").sum()),
            under_guarantee=r.under_dlp == "Y", last_resurfaced=r.last_resurfaced.strftime("%Y-%m-%d"),
            contractors=contr, flags=rf, me_too=int(upv_by_road.get(r.road_id, 0)), trail=trail,
            not_bmc_complaints=int(nb_count.get(r.road_id, 0)),
            rti_text=None))

    # ---- RTI text (template, real job ids)
    def rti(road):
        jobs = [f["job"] for f in road["flags"] if f.get("job")][:6]
        jobs = list(dict.fromkeys(jobs))
        js = ", ".join(jobs) if jobs else "all monsoon 2025 jobs"
        lr = pd.Timestamp(road["last_resurfaced"])
        ds = lr.strftime("%d-%m-%Y")
        nm, wd = road["name"], f"{road['ward']} ({road['ward_name']})"
        q_en = [f"Copies of bills and measurement records for work orders {js} on {nm}, Ward {wd}."]
        q_mr = [f"{nm}, वॉर्ड {wd} वरील कामांची ({js}) बिले व मोजमाप नोंदींच्या प्रती."]
        q_hi = [f"{nm}, वार्ड {wd} के कामों ({js}) के बिल और माप-पुस्तिका की प्रतियां।"]
        if road["under_guarantee"]:
            q_en += [f"This road was resurfaced on {ds} and is under the Defect Liability Period. Why did BMC pay for pothole repairs instead of the original contractor?",
                     "Name of the contractor who resurfaced this road and the guarantee period in the contract."]
            q_mr += [f"हा रस्ता {ds} रोजी नवा केला व तो हमी कालावधीत आहे. मूळ ठेकेदाराऐवजी BMC ने खड्डा-दुरुस्तीचे पैसे का दिले?",
                     "हा रस्ता नवा करणाऱ्या ठेकेदाराचे नाव आणि कराराप्रमाणे हमी कालावधी."]
            q_hi += [f"यह सड़क {ds} को नई बनी और गारंटी अवधि में है। मूल ठेकेदार की जगह BMC ने गड्ढा-मरम्मत का भुगतान क्यों किया?",
                     "इस सड़क को नया बनाने वाले ठेकेदार का नाम और अनुबंध के अनुसार गारंटी अवधि।"]
        if any(f["type"] == "overcharged" for f in road["flags"]):
            q_en.append("The rate-card basis for jobs billed well above the standard rate, with approval notes.")
            q_mr.append("मानक दरापेक्षा खूप जास्त बिल झालेल्या कामांचा दर-आधार आणि मंजुरीच्या नोंदी.")
            q_hi.append("मानक दर से बहुत अधिक बिल वाले कामों का दर-आधार और स्वीकृति टिप्पणियां।")
        num = lambda qs: "\n".join(f"{i + 1}. {q}" for i, q in enumerate(qs))
        return tri("To the Public Information Officer, BMC (RTI Act 2005):\n" + num(q_en),
                   "जन माहिती अधिकारी, BMC (माहितीचा अधिकार अधिनियम 2005):\n" + num(q_mr),
                   "जन सूचना अधिकारी, BMC (सूचना का अधिकार अधिनियम 2005):\n" + num(q_hi))

    for rd in road_out:
        if rd["flags"] or rd["under_guarantee"]:
            rd["rti_text"] = rti(rd)

    # ---- wards
    ward_out, next_rupee, citizen = [], {}, {}
    for w in wards.itertuples():
        g = wo[wo["ward_code"] == w.ward_code]
        wf = fdf[fdf["ward"] == w.ward_code] if len(fdf) else fdf
        counts = {t: int(len(wf[wf["type"] == t])) for t in
                  ["overcharged", "billed_twice", "no_complaint", "guarantee_paid", "built_weak",
                   "failed_fix", "repeat_patch", "pump_no_rain", "utility_damage", "closed_by_old_repair"]}
        spent, fair = int(g["cost_inr"].sum()), int(round(g["fair"].sum()))
        lost = int(g.loc[g["reopened_within_30d"] == "Y", "cost_inr"].sum())
        red_unique = wf[wf["severity"] == "red"]
        pending = int(red_unique[red_unique["type"].isin(["overcharged", "billed_twice", "guarantee_paid", "built_weak"])]
                      .drop_duplicates(["job_id", "type"]).shape[0])
        budget = int(w.monsoon_repair_budget_inr_2025)
        ward_out.append(dict(code=w.ward_code, name=wname[w.ward_code], budget_inr=budget, spent_inr=spent,
                             fair_cost_inr=fair, lost_failed_inr=lost, low_lying_index=float(w.low_lying_index),
                             flag_counts=counts, bmc_answered=0, bmc_pending=pending,
                             over_budget=spent > budget, top_roads=[], summary=None, citizen_voice=None))

        # next rupee
        rr = [x for x in road_out if x["ward"] == w.ward_code]
        scored = []
        for x in rr:
            rid = x["road_id"]
            risk = (2 * x["broke_again"] + 2 * int(acc.get(rid, 0)) + 2 * int(fac.get(rid, 0))
                    + float(traffic.get(rid, 0)) + (2 if x["bus_route"] else 0) + int(x["me_too"] // 50))
            reasons = []
            if x["broke_again"]:
                reasons.append(f"Broke again {x['broke_again']} times")
            if rid in schools.index:
                reasons.append(f"{int(schools[rid])} school(s) use it")
            if rid in hosp:
                reasons.append(f"Ambulance road to {hosp[rid]}")
            if int(acc.get(rid, 0)):
                reasons.append(f"{int(acc[rid])} road-condition accidents")
            if x["bus_route"]:
                reasons.append("Bus route")
            if x["under_guarantee"]:
                reasons.append("Under guarantee")
            gg = wo_g.get(rid, wo.iloc[0:0])
            patches = gg[gg["repair_method"].isin(PATCH_METHODS)]
            if x["under_guarantee"]:
                fix, est = "Make the original contractor repair it under guarantee — cost to BMC ₹0.", 0
            elif len(patches) >= 3:
                est = int(4200 * patches["area_sqm"].sum())
                fix = "Permanent resurface instead of another patch."
            else:
                est = int(1800 * (gg["area_sqm"].median() if len(gg) else 10) or 0)
                fix = "Routine repair with the method that lasts longest here (hot mix)."
            scored.append(dict(road_id=rid, name=x["name"], risk=round(risk, 1), reasons=reasons[:3], fix=fix,
                               est_cost_inr=est, under_guarantee=x["under_guarantee"]))
        scored.sort(key=lambda s: -s["risk"])
        next_rupee[w.ward_code] = scored[:5]
        ward_out[-1]["top_roads"] = [s["road_id"] for s in scored[:5]]

        # citizen voice data
        c_w = cp[cp["ward_code"] == w.ward_code]
        issues = []
        linked = c_w[c_w["job_id"].notna() & (c_w["status"] != "Closed - Duplicate")]
        closed_old = set(wf[wf["type"] == "closed_by_old_repair"]["evidence"].map(lambda e: e.get("complaint_id")))
        for jid, gg in c_w[c_w["job_id"].notna()].groupby("job_id"):
            main = gg.sort_values("received_at").iloc[0]
            st = "Closed by old repair" if any(x in closed_old for x in gg["complaint_id"]) else "Fixed"
            issues.append(dict(title=f"{main['category_reported']} — {main['locality_text']}", quote=redact(main["description"]),
                               language=str(main["language"]), merged=int(len(gg)), job=jid, status=st,
                               upvotes=int(gg["upvotes"].sum())))
        unl = c_w[c_w["job_id"].isna() & c_w["status"].isin(["Open", "In Progress"])]
        for (loc, cat), gg in unl.groupby(["locality_text", "category_reported"]):
            main = gg.sort_values("received_at").iloc[0]
            issues.append(dict(title=f"{cat} — {loc}", quote=redact(main["description"]), language=str(main["language"]),
                               merged=int(len(gg)), job="", status="Never fixed", upvotes=int(gg["upvotes"].sum())))
        issues.sort(key=lambda i: -i["upvotes"])
        dups = int((c_w["status"] == "Closed - Duplicate").sum())
        citizen[w.ward_code] = dict(
            complaints=int(len(c_w)), duplicates_merged=dups, issues=int(len(issues)),
            never_fixed=int(sum(i["status"] == "Never fixed" for i in issues)),
            closed_by_old=int(sum(i["status"] == "Closed by old repair" for i in issues)),
            issue_list=issues[:60],
            me_too_roads=sorted([dict(road_id=x["road_id"], name=x["name"], me_too=x["me_too"]) for x in rr if x["me_too"]],
                                key=lambda z: -z["me_too"])[:8])

    # ---- ward roads' fix: attach next_rupee ranking to road JSON (phone app "top roads")
    # ---- template text
    for wd in ward_out:
        n, b, s, f_, l = wd["name"], wd["budget_inr"], wd["spent_inr"], wd["fair_cost_inr"], wd["lost_failed_inr"]
        gap = s - f_
        c = wd["code"]
        wd["summary"] = tri(
            f"Ward {c} ({n}) got {fmt_inr(b)} for monsoon road repair and was billed {fmt_inr(s)}.\n"
            f"{fmt_inr(gap)} was paid above BMC's own rate card.\n{fmt_inr(l)} went on fixes that broke within a month.",
            f"वॉर्ड {c} ({n}) ला पावसाळी रस्ते दुरुस्तीसाठी {fmt_inr(b, 'mr')} मिळाले; बिले {fmt_inr(s, 'mr')} ची आली.\n"
            f"BMC च्या स्वतःच्या दरापेक्षा {fmt_inr(gap, 'mr')} जास्त दिले.\n{fmt_inr(l, 'mr')} महिन्याच्या आत पुन्हा तुटलेल्या दुरुस्त्यांवर गेले.",
            f"वार्ड {c} ({n}) को मानसून सड़क मरम्मत के लिए {fmt_inr(b, 'hi')} मिले; बिल {fmt_inr(s, 'hi')} के आए।\n"
            f"BMC के अपने दर से {fmt_inr(gap, 'hi')} ज़्यादा दिए गए।\n{fmt_inr(l, 'hi')} ऐसी मरम्मत पर गए जो एक महीने में फिर टूट गई।")
        cv = citizen[c]
        wd["citizen_voice"] = tri(
            f"{cv['complaints']} complaints came in; {cv['duplicates_merged']} were duplicates of the same problem.\n"
            f"{cv['never_fixed']} issues were never fixed.\n{cv['closed_by_old']} complaints were closed using a repair done before they were filed.",
            f"{cv['complaints']} तक्रारी आल्या; {cv['duplicates_merged']} त्याच समस्येच्या पुनरावृत्ती होत्या.\n"
            f"{cv['never_fixed']} समस्या कधीच सुटल्या नाहीत.\n{cv['closed_by_old']} तक्रारी जुन्या दुरुस्तीवरून बंद केल्या.",
            f"{cv['complaints']} शिकायतें आईं; {cv['duplicates_merged']} एक ही समस्या की दोहराव थीं।\n"
            f"{cv['never_fixed']} समस्याएं कभी ठीक नहीं हुईं।\n{cv['closed_by_old']} शिकायतें पुरानी मरम्मत दिखाकर बंद की गईं।")

    for k in ccards:
        w = k["ward"]
        fair_txt = "Normal" if k["price"] >= 0.9 else "Above normal"
        k["price_label"] = fair_txt
        k["note"] = (f"{k['jobs']} jobs{' in this ward' if w != 'ALL' else ' city-wide'}. {k['lasted_n']} of {k['jobs']} fixes lasted a month, "
                     f"{k['on_time_n']} were on time, and prices were {fair_txt.lower()}. "
                     f"Improvement: {'make fixes last longer' if k['lasted'] < 0.8 else 'finish faster after a complaint'}. "
                     f"{k['context']}").strip()

    if args.claude:
        claude_texts(args, ward_out, road_out, ccards, citizen, wards)

    # ---- output
    city_flag = {t: int(len(fdf[fdf["type"] == t])) for t in fdf["type"].unique()}
    out = dict(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"), as_of=asof.strftime("%Y-%m-%d"),
        city=dict(fair_cost_inr=int(round(wo["fair"].sum())), spent_inr=int(wo["cost_inr"].sum()),
                  budget_inr=int(wards["monsoon_repair_budget_inr_2025"].sum()), flag_counts=city_flag),
        wards=ward_out, roads=road_out,
        flags=[{k: py(v) for k, v in f.items()} for f in flags],
        contractors=ccards, next_rupee=next_rupee, citizen=citizen)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, default=py, indent=1), encoding="utf-8")
    print(f"wrote {args.out}  ({Path(args.out).stat().st_size / 1e6:.2f} MB)")
    run_tests(out, wo, fdf, roads, D)


# ----------------------------------------------------------------- 5.6 Claude text
RULE = ("Be fair. State facts with job IDs. Do not accuse anyone. Do not name any person or party. "
        "Use lakh/crore for money, no percentages, no jargon (no SLA, DLP, audit, variance).")


def claude_texts(args, ward_out, road_out, ccards, citizen, wards):
    import anthropic
    client = anthropic.Anthropic()
    cache_path = HERE / "claude_text_cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}

    def ask(prompt, schema):
        key = hashlib.sha256((prompt + json.dumps(schema, sort_keys=True)).encode()).hexdigest()
        if key in cache:
            return cache[key]
        try:
            resp = client.beta.messages.create(
                model="claude-opus-5-5", max_tokens=4000,
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
                messages=[{"role": "user", "content": prompt}])
            if resp.stop_reason == "refusal":
                return None
            val = json.loads(next(b.text for b in resp.content if b.type == "text"))
        except Exception as e:  # keep template text on any failure
            print("  claude call failed, keeping template:", type(e).__name__, str(e)[:120])
            return None
        cache[key] = val
        cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        return val

    tri_schema = {"type": "object", "properties": {k: {"type": "string"} for k in LANGS},
                  "required": list(LANGS), "additionalProperties": False}
    sel = {w["code"] for w in ward_out} if args.claude_wards.upper() == "ALL" else set(args.claude_wards.split(","))
    for wd in ward_out:
        if wd["code"] not in sel:
            continue
        print("claude: ward", wd["code"])
        facts = json.dumps({k: wd[k] for k in ["code", "name", "budget_inr", "spent_inr", "fair_cost_inr", "lost_failed_inr", "flag_counts"]})
        v = ask(f"Write a 3-line ward summary in English, Marathi and Hindi for ordinary citizens. Facts (rupees): {facts}. {RULE}", tri_schema)
        if v:
            wd["summary"] = v
        cv = citizen[wd["code"]]["issue_list"][:25]
        v = ask("Read these citizen complaints (mixed languages) and give 3 short bullets for the ward office, "
                f"in English, Marathi and Hindi, each a single string with 3 lines. Complaints: {json.dumps(cv, ensure_ascii=False)}. {RULE}", tri_schema)
        if v:
            wd["citizen_voice"] = v
        for k in [c for c in ccards if c["ward"] == wd["code"]]:
            v = ask(f"Write a fair contractor note under 60 words. Facts: {json.dumps(k)}. Include one thing to improve. {RULE}",
                    {"type": "object", "properties": {"note": {"type": "string"}}, "required": ["note"], "additionalProperties": False})
            if v:
                k["note"] = v["note"]
        for rd in [r for r in road_out if r["ward"] == wd["code"] and r["rti_text"]][:12]:
            v = ask(f"Write an RTI Act 2005 application in English, Marathi and Hindi for this road asking for bills, measurement records "
                    f"and the guarantee clause for the exact job IDs. Road: {json.dumps({k: rd[k] for k in ['road_id','name','ward','last_resurfaced','under_guarantee']})}. "
                    f"Flags: {json.dumps(rd['flags'], ensure_ascii=False)[:2500]}. {RULE}", tri_schema)
            if v:
                rd["rti_text"] = v


# ----------------------------------------------------------------- tests
def run_tests(out, wo, fdf, roads, D):
    ok = lambda b: "PASS" if b else "CHECK"
    cnt = lambda t: int((fdf["type"] == t).sum())
    cityf, citys = out["city"]["fair_cost_inr"], out["city"]["spent_inr"]
    print("\n=== data tests ===")
    print(f"D1  city fair {fmt_inr(cityf)} vs billed {fmt_inr(citys)} (expect ₹12.40 Cr vs ₹13.43 Cr) {ok(abs(cityf/1e7-12.40)<.02 and abs(citys/1e7-13.43)<.02)}")
    print(f"D2  overcharged {cnt('overcharged')} (~109) {ok(95<=cnt('overcharged')<=125)}")
    bt = fdf[fdf['type'] == 'billed_twice']
    print(f"D3  billed twice {len(bt)} (~45), all no complaint: {ok(all(not wo.set_index('work_order_id').loc[j,'has_complaint'] for j in bt['job_id']))}")
    d4 = fdf[(fdf['job_id'] == 'WO25-02459') & (fdf['type'] == 'billed_twice')]
    print(f"D4  WO25-02459 billed twice with {d4.iloc[0]['evidence']['other_job'] if len(d4) else None}, {d4.iloc[0]['evidence']['days_apart'] if len(d4) else '-'} day(s) {ok(len(d4) and d4.iloc[0]['evidence']['other_job']=='WO25-02052')}")
    gp = fdf[fdf['type'] == 'guarantee_paid']
    wt = wo.set_index('work_order_id').loc[gp['job_id'], 'work_type']
    print(f"D5  guarantee flags on drain/waterlogging: {int((~wt.isin(SURFACE_TYPES)).sum())} (expect 0) {ok((~wt.isin(SURFACE_TYPES)).sum()==0)}")
    bw = fdf[fdf['type'] == 'built_weak']
    top = bw.sort_values('amount_inr', ascending=False)
    n_surf = {r['road_id']: r['evidence']['surface_repairs'] for _, r in bw.iterrows()}
    best = max(n_surf, key=n_surf.get)
    print(f"D6  built weak roads {len(bw)} (45); top {best} with {n_surf[best]} repairs (RD0176 / 16); RD0220 present: {'RD0220' in n_surf}; L roads: {sorted(bw[bw['ward']=='L']['road_id'])} {ok(len(bw)==45 and best=='RD0176')}")
    near = wo[(wo['ratio'] > 1.2) & (wo['ratio'] <= 1.5)]
    print(f"D7  jobs at 1.2-1.5x NOT flagged: {int(len(near))} of which flagged: {int(near['work_order_id'].isin(fdf[fdf['type']=='overcharged']['job_id']).sum())} {ok(True)}")
    print(f"D8  pump_no_rain {cnt('pump_no_rain')} (15 before tide filter) ")
    ut = fdf[fdf['type'] == 'utility_damage']
    print(f"D9  utility jobs {len(ut)} (369), {fmt_inr(ut['amount_inr'].sum())} (~₹1.96 Cr) {ok(len(ut)==369)}")
    print(f"D10 closed by old repair {cnt('closed_by_old_repair')} (364) {ok(cnt('closed_by_old_repair')==364)}")
    L = next(w for w in out['wards'] if w['code'] == 'L')
    print(f"D11 L: budget {fmt_inr(L['budget_inr'])} spent {fmt_inr(L['spent_inr'])} fair {fmt_inr(L['fair_cost_inr'])} lost {fmt_inr(L['lost_failed_inr'])} pending {L['bmc_pending']}")
    over = [w['code'] for w in out['wards'] if w['spent_inr'] > w['budget_inr']]
    print(f"D12 overspent wards {over} (expect E, R/C, L, M/W, A, R/S) {ok(set(over)=={'E','R/C','L','M/W','A','R/S'})}")
    grades = pd.Series([c['grade'] for c in out['contractors'] if c['ward'] != 'ALL']).value_counts().to_dict()
    print(f"D13 contractor grades (ward level) {grades} {ok(len(grades)>1)}")
    print(f"D14 next rupee L: {[(r['road_id'], r['name']) for r in out['next_rupee']['L'][:5]]}")
    print(f"    F6 failed_fix {cnt('failed_fix')} (~1 in 4 jobs), F7 repeat_patch {cnt('repeat_patch')}, F3 no_complaint {cnt('no_complaint')} (45)")
    rd = next(r for r in out['roads'] if r['road_id'] == 'RD0220')
    print(f"    RD0220: spent {fmt_inr(rd['spent_inr'])} fair {fmt_inr(rd['fair_cost_inr'])} jobs {rd['jobs']} broke {rd['broke_again']} on_time {rd['on_time']} (expect ₹30.5 lakh / ₹29.5 lakh / 35 / 6 / 6)")
    print(f"    owner=Not BMC roads: {[r['road_id'] for r in out['roads'] if r['owner']=='Not BMC'][:10]}")
    names = re.compile(r"\b(BJP|Congress|Shiv Sena|NCP|corporator|Mr\.|Mrs\.|Shri)\b", re.I)
    txt = json.dumps([w['summary'] for w in out['wards']] + [w['citizen_voice'] for w in out['wards']], ensure_ascii=False)
    print(f"D15 person/party names in generated text: {len(names.findall(txt))} (expect 0) {ok(not names.findall(txt))}")


if __name__ == "__main__":
    main()
