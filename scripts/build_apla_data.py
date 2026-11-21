#!/usr/bin/env python3
"""Build app/apla_data.json (+ apla_data.js) from the Follow the Rupee dataset.

Usage:  python3 -I scripts/build_apla_data.py <dataset>/data app/
Stdlib only. Every number in the output comes straight from the CSVs.
"""
import csv
import datetime as dt
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

LANGS = ("en", "mr", "hi")
OVERCHARGE_RATIO = 1.5  # normal jobs sit at 0.75-1.3x the rate card; >=1.5 is a separate cluster
TWICE_WINDOW_DAYS = 7
DRY_MM = 2.5  # below IMD "light rain" at both stations, that day and the day before
WEAK_SINCE = dt.date(2023, 1, 1)  # resurfaced within ~2.5 years of the 2025 monsoon
WEAK_MIN_REPAIRS = 6
SURFACE_WORK = {"Pothole", "Broken paver blocks", "Road cave-in"}
PUMP_METHODS = {"Dewatering pump deployed", "Pump + debris removal"}
UTILITY_REMARK = "Utility (MTNL/MGL) trench"
DEMO_ROAD = "RD0220"

WARD_NAMES = {  # short display name per ward: en, mr, hi
    "A": ("Colaba", "कुलाबा", "कोलाबा"), "B": ("Dongri", "डोंगरी", "डोंगरी"),
    "C": ("Kalbadevi", "काळबादेवी", "कालबादेवी"), "D": ("Grant Road", "ग्रँट रोड", "ग्रांट रोड"),
    "E": ("Byculla", "भायखळा", "भायखला"), "F/S": ("Parel", "परळ", "परेल"),
    "F/N": ("Matunga", "माटुंगा", "माटुंगा"), "G/S": ("Worli", "वरळी", "वर्ली"),
    "G/N": ("Dadar", "दादर", "दादर"), "H/E": ("Bandra East", "वांद्रे पूर्व", "बांद्रा पूर्व"),
    "H/W": ("Bandra West", "वांद्रे पश्चिम", "बांद्रा पश्चिम"),
    "K/E": ("Andheri East", "अंधेरी पूर्व", "अंधेरी पूर्व"),
    "K/W": ("Andheri West", "अंधेरी पश्चिम", "अंधेरी पश्चिम"),
    "P/S": ("Goregaon", "गोरेगाव", "गोरेगांव"), "P/N": ("Malad", "मालाड", "मलाड"),
    "R/S": ("Kandivali", "कांदिवली", "कांदिवली"), "R/C": ("Borivali", "बोरिवली", "बोरीवली"),
    "R/N": ("Dahisar", "दहिसर", "दहिसर"), "L": ("Kurla", "कुर्ला", "कुर्ला"),
    "M/E": ("Govandi", "गोवंडी", "गोवंडी"), "M/W": ("Chembur", "चेंबूर", "चेंबूर"),
    "N": ("Ghatkopar", "घाटकोपर", "घाटकोपर"), "S": ("Bhandup", "भांडुप", "भांडुप"),
    "T": ("Mulund", "मुलुंड", "मुलुंड"),
}
METHOD_NAMES = {
    "Cold mix patch": ("Pothole patch (cold mix)", "खड्डा भरणे (कोल्ड मिक्स)", "गड्ढा भराई (कोल्ड मिक्स)"),
    "Hot mix patch": ("Pothole patch (hot mix)", "खड्डा भरणे (हॉट मिक्स)", "गड्ढा भराई (हॉट मिक्स)"),
    "Mastic asphalt": ("Mastic asphalt", "मॅस्टिक डांबर", "मैस्टिक डामर"),
    "Paver blocks relaid": ("Paver blocks relaid", "पेव्हर ब्लॉक पुन्हा बसवले", "पेवर ब्लॉक दोबारा लगाए"),
    "Dewatering pump deployed": ("Water pump", "पाणी उपसा पंप", "पानी निकासी पंप"),
    "Drain desilting": ("Drain cleaning", "गटार सफाई", "नाली सफाई"),
    "Pump + debris removal": ("Pump + debris removal", "पंप + कचरा काढणे", "पंप + मलबा हटाना"),
    "Manhole cover replaced + grating": ("Manhole cover replaced", "मॅनहोल झाकण बदलले", "मैनहोल ढक्कन बदला"),
    "Excavation, pipeline repair, PQC patch": ("Dig-up and pipeline repair", "खोदकाम व पाइपलाइन दुरुस्ती", "खुदाई और पाइपलाइन मरम्मत"),
    "Desilting and garbage removal": ("Silt and garbage removal", "गाळ व कचरा काढणे", "गाद और कचरा हटाना"),
}
MONTHS = {
    "en": "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(),
    "mr": "जाने फेब्रु मार्च एप्रि मे जून जुलै ऑग सप्टें ऑक्टो नोव्हें डिसें".split(),
    "hi": "जन फ़र मार्च अप्रै मई जून जुल अग सित अक्टू नव दिस".split(),
}
UNITS = {"en": ("lakh", "crore"), "mr": ("लाख", "कोटी"), "hi": ("लाख", "करोड़")}
FLAG_ORDER = ["built_weak", "billed_twice", "overcharged", "guarantee_paid", "pump_no_rain", "utility_dug"]


# ---------- formatting (mirrors money() in app/app.js) ----------
def _trim(x: float, places: int) -> str:
    s = f"{x:.{places}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def _commas(n: int) -> str:
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups) + "," + tail


def money(n: float, lang: str) -> str:
    n = round(abs(n))
    lakh, crore = UNITS[lang]
    if n >= 10_000_000:
        return f"₹{n / 10_000_000:.2f} {crore}"
    if n >= 1_000_000:
        return f"₹{_trim(n / 100_000, 1)} {lakh}"
    if n >= 100_000:
        return f"₹{_trim(n / 100_000, 2)} {lakh}"
    return f"₹{_commas(n)}"


def month_year(d: dt.date, lang: str) -> str:
    return f"{MONTHS[lang][d.month - 1]} {d.year}"


def day_month(d: dt.date, lang: str) -> str:
    return f"{d.day} {MONTHS[lang][d.month - 1]}"


def ids(jobs, n=4) -> str:
    return ", ".join(jobs[:n]) + (" …" if len(jobs) > n else "")


# ---------- loading ----------
def load(d: Path, name: str) -> list[dict]:
    with open(d / name, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def ts(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, "%Y-%m-%d %H:%M")


def km(a, b) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def build(data_dir: Path) -> dict:
    roads = load(data_dir, "roads.csv")
    wos = load(data_dir, "work_orders.csv")
    comps = load(data_dir, "complaints.csv")
    wards = load(data_dir, "wards.csv")
    locs = load(data_dir, "localities.csv")
    rate = {r["repair_method"]: (r["unit"], float(r["standard_rate_inr"])) for r in load(data_dir, "rate_card.csv")}
    rain = defaultdict(list)
    for r in load(data_dir, "rainfall_daily.csv"):
        rain[r["date"]].append(float(r["rainfall_mm"]))

    R = {r["road_id"]: r for r in roads}
    mid = {k: ((float(r["start_lat"]) + float(r["end_lat"])) / 2, (float(r["start_lon"]) + float(r["end_lon"])) / 2) for k, r in R.items()}
    as_of = max(ts(w["completed_at"]).date() for w in wos)

    # ---- per job facts ----
    for w in wos:
        unit, std = rate[w["repair_method"]]
        w["cost"] = int(w["cost_inr"])
        w["fair"] = round(std * float(w["area_sqm"] or 0)) if unit == "per sqm" else round(std)
        w["at"] = ts(w["assigned_at"])
        w["links"] = [c for c in w["linked_complaint_ids"].split("|") if c]

    job_flags = defaultdict(list)  # type -> [job dicts], built_weak is road-level
    for w in wos:
        road = R[w["road_id"]]
        if w["fair"] and w["cost"] / w["fair"] >= OVERCHARGE_RATIO:
            job_flags["overcharged"].append(w)
        if road["under_dlp"] == "Y" and w["work_type"] in SURFACE_WORK and w["assigned_at"][:10] > road["last_resurfaced"]:
            job_flags["guarantee_paid"].append(w)
        if w["repair_method"] in PUMP_METHODS:
            d = w["at"].date()
            mm = rain.get(str(d), []) + rain.get(str(d - dt.timedelta(days=1)), [])
            if mm and max(mm) < DRY_MM:
                w["rain_mm"] = max(mm)
                job_flags["pump_no_rain"].append(w)
        if w["engineer_remark"].startswith(UTILITY_REMARK):
            job_flags["utility_dug"].append(w)
    same = defaultdict(list)
    for w in wos:
        same[(w["road_id"], w["repair_method"])].append(w)
    for w in wos:  # the copy with no complaint behind it, near an identical job
        if w["links"]:
            continue
        twins = [o for o in same[(w["road_id"], w["repair_method"])] if o is not w and abs((w["at"] - o["at"]).days) <= TWICE_WINDOW_DAYS]
        if twins:
            w["twin"] = min(twins, key=lambda o: abs((w["at"] - o["at"]).total_seconds()))
            job_flags["billed_twice"].append(w)

    # ---- contractor grades (city-wide, same letter everywhere) ----
    cstat = defaultdict(lambda: {"jobs": 0, "on_time": 0, "broke": 0, "cost": 0, "fair": 0})
    for w in wos:
        s = cstat[w["contractor_name"]]
        s["jobs"] += 1
        s["on_time"] += w["sla_met"] == "Y"
        s["broke"] += w["reopened_within_30d"] == "Y"
        s["cost"] += w["cost"]
        s["fair"] += w["fair"]
    contractors = {}
    for name, s in cstat.items():
        held = 1 - s["broke"] / s["jobs"]
        ontime = s["on_time"] / s["jobs"]
        price = max(0.0, 1 - (s["cost"] / s["fair"] - 1) / 0.3)
        score = 50 * held + 30 * ontime + 20 * price
        grade = "A" if score >= 80 else "B" if score >= 70 else "C" if score >= 60 else "D" if score >= 50 else "F"
        contractors[name] = {"name": name.replace(" (synthetic)", ""), "grade": grade, "score": round(score, 1),
                             "jobs": s["jobs"], "on_time": s["on_time"], "broke_again": s["broke"],
                             "spent_inr": s["cost"], "fair_cost_inr": s["fair"]}

    # ---- complaints -> roads ----
    loc_index = {}
    for l in locs:
        for n in [l["locality"]] + [a.strip() for a in l["aliases"].split(";") if a.strip()]:
            loc_index[n.lower()] = (l["ward_code"], float(l["lat"]), float(l["lon"]))
    names = sorted({r["road_name"] for r in roads}, key=len, reverse=True)
    by_name = defaultdict(list)
    for r in roads:
        by_name[r["road_name"]].append(r["road_id"])
    job_road = {}
    for w in wos:
        for c in w["links"]:
            job_road.setdefault(c, (w["road_id"], w["work_order_id"]))

    def place(c):
        loc = loc_index.get(c["locality_text"].lower())
        ward = c["ward_code"] or (loc[0] if loc else "")
        ref = (float(c["lat"]), float(c["lon"])) if c["lat"] else ((loc[1], loc[2]) if loc else None)
        text = c["description"].lower()
        name = next((n for n in names if n.lower() in text), None)
        if name:
            cands = [k for k in by_name[name] if R[k]["ward_code"] == ward] or by_name[name]
            return min(cands, key=lambda k: km(ref, mid[k])) if ref else cands[0]
        if c["lat"]:
            k = min(R, key=lambda k: km(ref, mid[k]))
            return k if km(ref, mid[k]) <= 0.3 else None
        return None

    road_comps = defaultdict(lambda: {"done": [], "open": [], "not_bmc": 0, "upvotes": 0})
    for c in comps:
        st = c["status"]
        if c["complaint_id"] in job_road:
            rid, job = job_road[c["complaint_id"]]
            rc = road_comps[rid]
            rc["upvotes"] += int(c["upvotes"] or 0)
            if st == "Resolved":
                rc["done"].append((c["received_at"], c["complaint_id"], job))
            continue
        if st not in ("Open", "In Progress", "Rejected - Not BMC road", "Forwarded to MMRDA/PWD"):
            continue
        rid = place(c)
        if not rid:
            continue
        rc = road_comps[rid]
        rc["upvotes"] += int(c["upvotes"] or 0)
        if st in ("Open", "In Progress"):
            rc["open"].append(((as_of - ts(c["received_at"]).date()).days, c["complaint_id"], st))
        else:
            rc["not_bmc"] += 1

    # ---- roads ----
    road_jobs = defaultdict(list)
    for w in wos:
        road_jobs[w["road_id"]].append(w)
    flagged_by_road = defaultdict(lambda: defaultdict(list))
    for ftype, jobs in job_flags.items():
        for w in jobs:
            flagged_by_road[w["road_id"]][ftype].append(w)
    seg_count = Counter((r["road_name"], r["ward_code"]) for r in roads)

    out_roads = []
    for r in roads:
        rid, jobs = r["road_id"], road_jobs[r["road_id"]]
        rc = road_comps[rid]
        resurfaced = dt.date.fromisoformat(r["last_resurfaced"])
        surface_jobs = [w for w in jobs if w["work_type"] in SURFACE_WORK]
        fl = flagged_by_road[rid]
        flags = []
        if resurfaced >= WEAK_SINCE and len(surface_jobs) >= WEAK_MIN_REPAIRS:
            flags.append(flag_built_weak(resurfaced, surface_jobs))
        for ftype in FLAG_ORDER[1:]:
            if fl.get(ftype):
                flags.append(FLAG_TEXT[ftype](sorted(fl[ftype], key=lambda w: w["at"])))
        owner = "Not BMC" if not jobs and rc["not_bmc"] and not rc["open"] else "BMC"
        cmap = Counter(w["contractor_name"] for w in jobs)
        opened = sorted(rc["open"], reverse=True)
        done = sorted(rc["done"], reverse=True)
        trail = [{"complaint": cid, "job": None, "status": st, "days_open": d} for d, cid, st in opened[:2]]
        trail += [{"complaint": cid, "job": job, "status": "Done"} for _, cid, job in done[: 3 - len(trail)]]
        road = {
            "road_id": rid, "name": r["road_name"], "segment": int(r["segment_no"]),
            "segments_in_ward": seg_count[(r["road_name"], r["ward_code"])],
            "ward": r["ward_code"], "ward_name": WARD_NAMES[r["ward_code"]][0],
            "road_type": r["road_type"], "bus_route": r["bus_route"] == "Y", "owner": owner,
            "start": [float(r["start_lat"]), float(r["start_lon"])], "end": [float(r["end_lat"]), float(r["end_lon"])],
            "spent_inr": sum(w["cost"] for w in jobs), "fair_cost_inr": sum(w["fair"] for w in jobs),
            "jobs": len(jobs), "broke_again": sum(w["reopened_within_30d"] == "Y" for w in jobs),
            "on_time": sum(w["sla_met"] == "Y" for w in jobs),
            "under_guarantee": r["under_dlp"] == "Y", "last_resurfaced": r["last_resurfaced"],
            "contractors": [{"name": contractors[n]["name"], "grade": contractors[n]["grade"], "jobs": k} for n, k in cmap.most_common()],
            "flags": flags, "me_too": rc["upvotes"],
            "open_complaints": len(opened), "max_days_open": opened[0][0] if opened else 0,
            "not_bmc_complaints": rc["not_bmc"], "trail": trail,
        }
        road["rti_text"] = rti_text(road, jobs, fl)
        out_roads.append(road)

    # ---- wards ----
    out_wards = []
    for wd in wards:
        code = wd["ward_code"]
        jobs = [w for w in wos if w["ward_code"] == code]
        wroads = [r for r in out_roads if r["ward"] == code]
        fc = {t: sum(1 for w in job_flags[t] if w["ward_code"] == code) for t in FLAG_ORDER[1:]}
        fc = {"built_weak": sum(any(f["type"] == "built_weak" for f in r["flags"]) for r in wroads), **fc}
        flagged_jobs = {w["work_order_id"] for t in FLAG_ORDER[1:] for w in job_flags[t] if w["ward_code"] == code}
        cmap = Counter(w["contractor_name"] for w in jobs)
        ward = {
            "code": code, "name": WARD_NAMES[code][0],
            "names": dict(zip(LANGS, WARD_NAMES[code])), "areas": wd["ward_name"],
            "budget_inr": int(wd["monsoon_repair_budget_inr_2025"]),
            "spent_inr": sum(w["cost"] for w in jobs), "fair_cost_inr": sum(w["fair"] for w in jobs),
            "lost_failed_inr": sum(w["cost"] for w in jobs if w["reopened_within_30d"] == "Y"),
            "jobs": len(jobs), "on_time": sum(w["sla_met"] == "Y" for w in jobs),
            "broke_again": sum(w["reopened_within_30d"] == "Y" for w in jobs),
            "flag_counts": fc, "bmc_answered": 0, "bmc_pending": len(flagged_jobs),
            "top_roads": [r["road_id"] for r in sorted(wroads, key=lambda r: -r["spent_inr"])[:5]],
            "contractors": [{"name": contractors[n]["name"], "grade": contractors[n]["grade"], "jobs": k} for n, k in cmap.most_common()],
        }
        ward["summary"] = ward_summary(ward)
        out_wards.append(ward)

    localities = [{"name": l["locality"], "ward": l["ward_code"],
                   "aliases": [a.strip() for a in l["aliases"].split(";") if a.strip()]} for l in locs]
    return {
        "meta": {"as_of": str(as_of), "demo_road": DEMO_ROAD, "source": "Follow the Rupee (synthetic), CC BY 4.0",
                 "rules": {"overcharge_ratio": OVERCHARGE_RATIO, "billed_twice_days": TWICE_WINDOW_DAYS,
                           "dry_mm": DRY_MM, "built_weak_since": str(WEAK_SINCE), "built_weak_min_repairs": WEAK_MIN_REPAIRS}},
        "roads": out_roads, "wards": out_wards,
        "contractors": sorted(contractors.values(), key=lambda c: -c["score"]),
        "localities": localities,
    }


# ---------- flag texts ----------
def _t(en, mr, hi):
    return {"en": en, "mr": mr, "hi": hi}


def flag_built_weak(resurfaced, surface_jobs):
    n = len(surface_jobs)
    m = {l: month_year(resurfaced, l) for l in LANGS}
    return {"type": "built_weak", "count": n, "jobs": [w["work_order_id"] for w in surface_jobs],
            "text": _t(f"Resurfaced {m['en']}, but needed {n} road repairs (potholes, cave-ins, paver blocks) this monsoon.",
                       f"{m['mr']} ला नवा केला, तरी या पावसाळ्यात {n} वेळा खड्डे / रस्ता दुरुस्ती करावी लागली.",
                       f"{m['hi']} में नई बनी, फिर भी इस मानसून में {n} बार गड्ढे / सड़क की मरम्मत करनी पड़ी।")}


def flag_billed_twice(jobs):
    n, w = len(jobs), jobs[0]
    a, b = sorted([w["twin"]["work_order_id"], w["work_order_id"]])
    d = abs((w["at"] - w["twin"]["at"]).days) or 1
    amt = {l: money(sum(x["cost"] for x in jobs), l) for l in LANGS}
    if n == 1:
        text = _t(f"Same work billed twice within {d} days: {a} and {b}. Extra paid: {amt['en']}.",
                  f"एकच काम {d} दिवसांत दोनदा बिल केले: {a} आणि {b}. जास्तीचे पैसे: {amt['mr']}.",
                  f"एक ही काम {d} दिन में दो बार बिल हुआ: {a} और {b}। ज़्यादा दिया: {amt['hi']}।")
    else:
        text = _t(f"{n} jobs look like the same work billed twice within a week ({amt['en']}). Example: {a} and {b}.",
                  f"{n} कामे आठवड्याभरात दोनदा बिल केल्यासारखी दिसतात ({amt['mr']}). उदा: {a} आणि {b}.",
                  f"{n} काम एक हफ़्ते में दो बार बिल हुए लगते हैं ({amt['hi']})। उदा: {a} और {b}।")
    return {"type": "billed_twice", "count": n, "jobs": [x["work_order_id"] for x in jobs],
            "pairs": [sorted([x["twin"]["work_order_id"], x["work_order_id"]]) for x in jobs], "text": text}


def flag_overcharged(jobs):
    n = len(jobs)
    w = max(jobs, key=lambda x: x["cost"] / x["fair"])
    x = f"{w['cost'] / w['fair']:.1f}"
    m = dict(zip(LANGS, METHOD_NAMES[w["repair_method"]]))
    f = {l: money(w["fair"], l) for l in LANGS}
    c = {l: money(w["cost"], l) for l in LANGS}
    j = w["work_order_id"]
    if n == 1:
        text = _t(f"One job billed {x} times the normal rate: {m['en']} normally ≈ {f['en']}, billed {c['en']} ({j}).",
                  f"एका कामाचे बिल नेहमीच्या दराच्या {x} पट: {m['mr']} साधारण {f['mr']}, बिल {c['mr']} ({j}).",
                  f"एक काम का बिल सामान्य दर का {x} गुना: {m['hi']} आम तौर पर {f['hi']}, बिल {c['hi']} ({j})।")
    else:
        text = _t(f"{n} jobs billed far above the normal rate. Worst: {m['en']} normally ≈ {f['en']}, billed {c['en']} ({j}).",
                  f"{n} कामांचे बिल नेहमीपेक्षा खूप जास्त. सर्वात जास्त: {m['mr']} साधारण {f['mr']}, बिल {c['mr']} ({j}).",
                  f"{n} कामों का बिल सामान्य से बहुत ज़्यादा। सबसे ज़्यादा: {m['hi']} आम तौर पर {f['hi']}, बिल {c['hi']} ({j})।")
    return {"type": "overcharged", "count": n, "jobs": [x["work_order_id"] for x in jobs],
            "worst": {"job": j, "fair_inr": w["fair"], "billed_inr": w["cost"]}, "text": text}


def flag_guarantee_paid(jobs):
    n = len(jobs)
    amt = {l: money(sum(x["cost"] for x in jobs), l) for l in LANGS}
    text = _t(f"BMC paid {amt['en']} for {n} repair{'s' if n > 1 else ''} though the road is under guarantee.",
              f"हमी असूनही BMC ने {'एका दुरुस्तीसाठी' if n == 1 else f'{n} दुरुस्त्यांसाठी'} {amt['mr']} दिले.",
              f"गारंटी के बावजूद BMC ने {'एक मरम्मत' if n == 1 else f'{n} मरम्मतों'} के लिए {amt['hi']} दिए।")
    return {"type": "guarantee_paid", "count": n, "jobs": [x["work_order_id"] for x in jobs],
            "amount_inr": sum(x["cost"] for x in jobs), "text": text}


def flag_pump_no_rain(jobs):
    n, w = len(jobs), jobs[0]
    d = {l: day_month(w["at"].date(), l) for l in LANGS}
    c = {l: money(w["cost"], l) for l in LANGS}
    j = w["work_order_id"]
    if n == 1:
        text = _t(f"Pump billed on {d['en']} though no rain fell that day or the day before ({j}, {c['en']}).",
                  f"{d['mr']} ला पाऊस नसताना पंपाचे बिल ({j}, {c['mr']}).",
                  f"{d['hi']} को बारिश नहीं थी, फिर भी पंप का बिल ({j}, {c['hi']})।")
    else:
        text = _t(f"{n} pump jobs billed on dry days. Example: {d['en']} ({j}, {c['en']}).",
                  f"पाऊस नसलेल्या दिवशी {n} वेळा पंपाचे बिल. उदा: {d['mr']} ({j}).",
                  f"सूखे दिनों में {n} बार पंप का बिल। उदा: {d['hi']} ({j})।")
    return {"type": "pump_no_rain", "count": n, "jobs": [x["work_order_id"] for x in jobs], "text": text}


def flag_utility_dug(jobs):
    n = len(jobs)
    amt = {l: money(sum(x["cost"] for x in jobs), l) for l in LANGS}
    text = _t(f"{n} repair{'s' if n > 1 else ''} on MTNL/MGL trenches ({amt['en']}). This cost should be recovered from them.",
              f"{n} दुरुस्त्या MTNL/MGL च्या खोदकामामुळे ({amt['mr']}). हा खर्च त्यांच्याकडून वसूल व्हायला हवा.",
              f"{n} मरम्मत MTNL/MGL की खुदाई की वजह से ({amt['hi']})। यह खर्च उनसे वसूल होना चाहिए।")
    return {"type": "utility_dug", "count": n, "jobs": [x["work_order_id"] for x in jobs],
            "amount_inr": sum(x["cost"] for x in jobs), "text": text}


FLAG_TEXT = {"billed_twice": flag_billed_twice, "overcharged": flag_overcharged, "guarantee_paid": flag_guarantee_paid,
             "pump_no_rain": flag_pump_no_rain, "utility_dug": flag_utility_dug}


# ---------- RTI application (placeholders {NAME} {ADDRESS} {DATE} filled in the app) ----------
def rti_text(road, jobs, fl):
    if road["owner"] != "BMC" or not (jobs or road["open_complaints"]):
        return None
    nm, rid, wd = road["name"], road["road_id"], road["ward"]
    rs = dt.date.fromisoformat(road["last_resurfaced"]).strftime("%d-%m-%Y")
    q = {l: [] for l in LANGS}

    def add(en, mr, hi):
        for lang, s in zip(LANGS, (en, mr, hi)):
            q[lang].append(s)

    if not jobs:  # complaints open, no work order at all
        ci, d = ", ".join(t["complaint"] for t in road["trail"]), road["max_days_open"]
        add(f"Present status of complaints {ci} about {nm}, open for up to {d} days, and the date the site was inspected.",
            f"{nm} बद्दलच्या तक्रारी {ci} ({d} दिवसांपर्यंत उघड्या) यांची सद्यस्थिती व जागेची पाहणी केल्याची तारीख.",
            f"{nm} से जुड़ी शिकायतों {ci} ({d} दिन तक खुली) की मौजूदा स्थिति और स्थल निरीक्षण की तारीख।")
        add(f"Reason no repair work order was issued for {nm} ({rid}) this monsoon, with the file notings.",
            f"या पावसाळ्यात {nm} ({rid}) साठी दुरुस्तीचा कार्यादेश का दिला नाही, याचे कारण व फाइलवरील नोंदी.",
            f"इस मानसून में {nm} ({rid}) के लिए मरम्मत का कार्यादेश क्यों नहीं दिया गया, इसका कारण और फ़ाइल नोटिंग।")
        add("The date by which the repair will be done.",
            "दुरुस्ती कोणत्या तारखेपर्यंत होईल.",
            "मरम्मत किस तारीख तक होगी।")
        return _rti_wrap(q, nm, rid, wd)

    flagged = [w for t in FLAG_ORDER[1:] for w in fl.get(t, [])]
    pick = list(dict.fromkeys(w["work_order_id"] for w in flagged))[:6] or \
        [w["work_order_id"] for w in sorted(jobs, key=lambda w: -w["cost"])[:3]]
    add(f"Copies of bills and measurement-book records for work orders {', '.join(pick)} on {nm}.",
        f"{nm} वरील कामे {', '.join(pick)} यांची बिले व मोजमाप पुस्तिकेच्या (MB) प्रती.",
        f"{nm} पर कामों {', '.join(pick)} के बिल और माप पुस्तिका (MB) की प्रतियां।")
    if road["under_guarantee"]:
        g = fl.get("guarantee_paid", [])
        if g:
            amt = {l: money(sum(w["cost"] for w in g), l) for l in LANGS}
            gi = ids([w["work_order_id"] for w in g])
            add(f"This road was resurfaced on {rs} and is under the Defect Liability Period. Why did BMC pay {amt['en']} for repairs {gi} instead of the original contractor?",
                f"हा रस्ता {rs} रोजी नव्याने केला असून तो दोष दायित्व कालावधीत (हमी) आहे. मूळ ठेकेदाराऐवजी BMC ने {gi} या दुरुस्त्यांसाठी {amt['mr']} का दिले?",
                f"यह सड़क {rs} को नई बनी थी और दोष दायित्व अवधि (गारंटी) में है। मूल ठेकेदार के बजाय BMC ने मरम्मत {gi} के लिए {amt['hi']} क्यों दिए?")
        add(f"Name of the contractor who resurfaced this road on {rs}, and the guarantee (Defect Liability) period in that contract.",
            f"हा रस्ता {rs} रोजी नव्याने करणाऱ्या ठेकेदाराचे नाव व करारातील हमी कालावधी.",
            f"{rs} को यह सड़क बनाने वाले ठेकेदार का नाम और अनुबंध में गारंटी अवधि।")
    if fl.get("overcharged"):
        w = max(fl["overcharged"], key=lambda x: x["cost"] / x["fair"])
        c = {l: money(w["cost"], l) for l in LANGS}
        f = {l: money(w["fair"], l) for l in LANGS}
        j = w["work_order_id"]
        add(f"Reason for paying {c['en']} for {j} when BMC's own rate card puts it at about {f['en']}.",
            f"BMC च्या दरपत्रकानुसार साधारण {f['mr']} असताना {j} साठी {c['mr']} का दिले, याचे कारण.",
            f"BMC की दर सूची के अनुसार लगभग {f['hi']} होने पर भी {j} के लिए {c['hi']} क्यों दिए गए, इसका कारण।")
    if fl.get("billed_twice"):
        w = fl["billed_twice"][0]
        a, b = sorted([w["twin"]["work_order_id"], w["work_order_id"]])
        d = abs((w["at"] - w["twin"]["at"]).days) or 1
        add(f"Why were both {a} and {b} paid for the same work on the same road within {d} days?",
            f"एकाच रस्त्यावर एकाच कामासाठी {d} दिवसांत {a} आणि {b} या दोन्ही कामांचे पैसे का दिले?",
            f"एक ही सड़क पर एक ही काम के लिए {d} दिनों में {a} और {b} दोनों का भुगतान क्यों हुआ?")
    if fl.get("pump_no_rain"):
        w = fl["pump_no_rain"][0]
        d = w["at"].date().strftime("%d-%m-%Y")
        j = w["work_order_id"]
        add(f"Pump logbook and reason for {j} on {d}, a day when no rain was recorded.",
            f"पाऊस नोंदला नसताना {d} रोजी {j} साठी पंप लावल्याची नोंदवही व कारण.",
            f"बारिश दर्ज न होने पर भी {d} को {j} के लिए पंप लगाने की लॉगबुक और कारण।")
    if fl.get("utility_dug"):
        u = fl["utility_dug"]
        amt = {l: money(sum(w["cost"] for w in u), l) for l in LANGS}
        add(f"Whether the {amt['en']} spent on {len(u)} repairs of MTNL/MGL trenches was recovered from them, with receipts.",
            f"MTNL/MGL च्या खोदकामामुळे झालेल्या {len(u)} दुरुस्त्यांचा {amt['mr']} खर्च त्यांच्याकडून वसूल केला का? पावत्यांसह.",
            f"MTNL/MGL की खुदाई से हुई {len(u)} मरम्मतों का {amt['hi']} खर्च क्या उनसे वसूला गया? रसीदों सहित।")
    if road["broke_again"]:
        n = road["broke_again"]
        add(f"Whether the contractors re-did, at their own cost, the {n} repairs that broke again within 30 days.",
            f"30 दिवसांत पुन्हा तुटलेल्या {n} दुरुस्त्या ठेकेदारांनी स्वखर्चाने पुन्हा केल्या का?",
            f"30 दिनों में दोबारा टूटी {n} मरम्मतें क्या ठेकेदारों ने अपने खर्च पर दोबारा कीं?")
    return _rti_wrap(q, nm, rid, wd)


def _rti_wrap(q, nm, rid, wd):
    head = {
        "en": f"To,\nThe Public Information Officer,\n{wd} Ward Office, Brihanmumbai Municipal Corporation, Mumbai\n\n"
              f"Subject: Application under the Right to Information Act, 2005 — {nm} ({rid}), {wd} ward\n\n"
              f"Please provide the following information:",
        "mr": f"प्रति,\nजन माहिती अधिकारी,\n{wd} विभाग कार्यालय, बृहन्मुंबई महानगरपालिका, मुंबई\n\n"
              f"विषय: माहितीचा अधिकार अधिनियम, 2005 अंतर्गत अर्ज — {nm} ({rid}), {wd} विभाग\n\n"
              f"कृपया खालील माहिती द्यावी:",
        "hi": f"सेवा में,\nजन सूचना अधिकारी,\n{wd} वार्ड कार्यालय, बृहन्मुंबई महानगरपालिका, मुंबई\n\n"
              f"विषय: सूचना का अधिकार अधिनियम, 2005 के तहत आवेदन — {nm} ({rid}), {wd} वार्ड\n\n"
              f"कृपया निम्नलिखित जानकारी दें:",
    }
    tail = {
        "en": "Name: {NAME}\nAddress: {ADDRESS}\nDate: {DATE}\nI have paid the RTI fee of ₹10.",
        "mr": "नाव: {NAME}\nपत्ता: {ADDRESS}\nदिनांक: {DATE}\nमी ₹10 माहिती अधिकार शुल्क भरले आहे.",
        "hi": "नाम: {NAME}\nपता: {ADDRESS}\nदिनांक: {DATE}\nमैंने ₹10 RTI शुल्क भर दिया है।",
    }
    return {l: head[l] + "\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(q[l], 1)) + "\n\n" + tail[l] for l in LANGS}


# ---------- ward summary (3 lines per language) ----------
def ward_summary(w):
    code, fc = w["code"], w["flag_counts"]
    diff = w["spent_inr"] - w["budget_inr"]
    b, s, d, lost = ({l: money(v, l) for l in LANGS} for v in (w["budget_inr"], w["spent_inr"], diff, w["lost_failed_inr"]))
    over = diff >= 0
    priority = ["guarantee_paid", "overcharged", "billed_twice", "built_weak", "pump_no_rain", "utility_dug"]
    top = [(fc[t], t) for t in priority if fc.get(t)][:3]  # money-loss flags first
    words = {
        "built_weak": ("roads built weak", "कमकुवत बांधलेले रस्ते", "कमज़ोर बनी सड़कें"),
        "billed_twice": ("jobs billed twice", "दोनदा बिल झालेली कामे", "दो बार बिल हुए काम"),
        "overcharged": ("overcharged jobs", "जास्त बिल झालेली कामे", "ज़्यादा बिल वाले काम"),
        "guarantee_paid": ("repairs paid on guarantee roads", "हमी असलेल्या रस्त्यांवर पैसे दिलेल्या दुरुस्त्या", "गारंटी वाली सड़कों पर भुगतान"),
        "pump_no_rain": ("pump bills on dry days", "पाऊस नसताना पंपाची बिले", "सूखे दिनों के पंप बिल"),
        "utility_dug": ("repairs of utility trenches", "युटिलिटी खोदकामाच्या दुरुस्त्या", "यूटिलिटी खुदाई की मरम्मत"),
    }
    li = {l: i for i, l in enumerate(LANGS)}
    fl = {l: ", ".join(f"{n} {words[t][li[l]]}" for n, t in top) for l in LANGS}
    return {
        "en": f"{code} ward got {b['en']} for road repairs and spent {s['en']} — {d['en']} {'more' if over else 'less'}.\n"
              f"{lost['en']} went on fixes that broke again within a month. {w['on_time']} of {w['jobs']} repairs were on time.\n"
              + (f"Red flags: {fl['en']}." if top else "No suspicious bills found in this ward."),
        "mr": f"{code} वॉर्डला रस्ते दुरुस्तीसाठी {b['mr']} मिळाले, खर्च झाला {s['mr']} — {d['mr']} {'जास्त' if over else 'कमी'}.\n"
              f"महिन्याभरात पुन्हा तुटलेल्या दुरुस्त्यांवर {lost['mr']} गेले. {w['jobs']} पैकी {w['on_time']} दुरुस्त्या वेळेवर झाल्या.\n"
              + (f"धोक्याच्या खुणा: {fl['mr']}." if top else "या वॉर्डमध्ये संशयास्पद बिल आढळले नाही."),
        "hi": f"{code} वार्ड को सड़क मरम्मत के लिए {b['hi']} मिले, खर्च हुआ {s['hi']} — {d['hi']} {'ज़्यादा' if over else 'कम'}।\n"
              f"महीने भर में फिर टूटी मरम्मतों पर {lost['hi']} गए। {w['jobs']} में से {w['on_time']} मरम्मतें समय पर हुईं।\n"
              + (f"खतरे के निशान: {fl['hi']}।" if top else "इस वार्ड में कोई संदिग्ध बिल नहीं मिला।"),
    }


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: build_apla_data.py <dataset>/data <out-dir>")
    data_dir, out_dir = Path(sys.argv[1]).expanduser(), Path(sys.argv[2]).expanduser()
    data = build(data_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    (out_dir / "apla_data.json").write_text(blob, encoding="utf-8")
    (out_dir / "apla_data.js").write_text(f"window.APLA_DATA={blob};\n", encoding="utf-8")

    # self-check against the numbers quoted in the spec
    r = next(x for x in data["roads"] if x["road_id"] == DEMO_ROAD)
    w = next(x for x in data["wards"] if x["code"] == "L")
    assert (r["spent_inr"], r["fair_cost_inr"], r["jobs"], r["broke_again"], r["on_time"]) == (3052667, 2951740, 35, 6, 6), r
    assert (w["budget_inr"], w["spent_inr"], w["fair_cost_inr"], w["lost_failed_inr"]) == (11900000, 13043418, 12090020, 1820204), w
    assert money(3052667, "mr") == "₹30.5 लाख" and money(13043418, "en") == "₹1.30 crore"
    roads = data["roads"]
    print(f"wrote {out_dir / 'apla_data.json'} ({len(blob) // 1024} KB) · {len(roads)} roads · {len(data['wards'])} wards · as of {data['meta']['as_of']}")
    print("flags (jobs):", {t: sum(f["count"] for x in roads for f in x["flags"] if f["type"] == t) for t in FLAG_ORDER})
    print("L ward flag_counts:", w["flag_counts"], "pending:", w["bmc_pending"])
    print("special states: not BMC =", [x["road_id"] for x in roads if x["owner"] != "BMC"],
          "· no repair yet =", [x["road_id"] for x in roads if x["owner"] == "BMC" and not x["jobs"] and x["open_complaints"]],
          "· zero flags (with jobs) =", sum(1 for x in roads if x["jobs"] and not x["flags"]))
    print("grades:", {c["name"]: c["grade"] for c in data["contractors"]})


if __name__ == "__main__":
    main()
