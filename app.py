"""Apla BMC · Ward Accountability Board (Streamlit). Reads apla_data.json only.

Run:  streamlit run app.py
"""
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

DATA = Path(__file__).parent / "apla_data.json"
st.set_page_config(page_title="Apla BMC · Ward Accountability Board", page_icon="🚧", layout="wide")

TEAL, AMBER, GREEN, RED = "#0F4C5C", "#CA8A04", "#15803D", "#B91C1C"  # Emerald Ledger: pine teal, ochre, forest, deep red
TEAL2, TANGERINE = "#0D9488", "#E36414"

LABELS = {
    "overcharged": "Overcharged", "billed_twice": "Billed twice", "no_complaint": "No complaint",
    "guarantee_paid": "Guarantee road", "built_weak": "Built weak", "failed_fix": "Failed fix",
    "repeat_patch": "Repeat patch", "pump_no_rain": "Pump, no rain", "utility_damage": "Utility damage",
    "closed_by_old_repair": "Closed by old repair",
}
RED_TYPES = ["overcharged", "billed_twice", "no_complaint", "guarantee_paid", "built_weak"]
AMBER_TYPES = ["failed_fix", "repeat_patch", "pump_no_rain", "utility_damage", "closed_by_old_repair"]


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


if not DATA.exists():
    st.error("apla_data.json not found. Run `python build_data.py` first.")
    st.stop()

D = load()
AS_OF = pd.Timestamp(D["as_of"])
W = {w["code"]: w for w in D["wards"]}
flags_all = pd.DataFrame(D["flags"])

st.markdown(
    f"""<style>
    .tile {{background:#FFFFFF;border-radius:12px;padding:14px 16px;border:1px solid #E4E4E7}}
    .tile .l {{font-size:.8rem;color:#555}} .tile .v {{font-size:1.7rem;font-weight:700}}
    .tile .s {{font-size:.8rem}} .grade {{font-size:3rem;font-weight:800;line-height:1}}
    .chip {{display:inline-block;padding:2px 10px;border-radius:99px;font-size:.8rem;margin:2px;background:#EAF3F5;color:{TEAL}}}
    .ok {{color:{GREEN}}} .bad {{color:{RED}}} .warn {{color:{AMBER}}}
    </style>""", unsafe_allow_html=True)


def tile(label, value, sub="", color="#1C1B19"):
    st.markdown(f'<div class="tile"><div class="l">{label}</div><div class="v" style="color:{color}">{value}</div>'
                f'<div class="s" style="color:{color}">{sub}</div></div>', unsafe_allow_html=True)


# ------------------------------------------------------------------ top bar
c1, c2, c3 = st.columns([3, 2, 1])
c1.markdown(f"### 🚧 Apla BMC · Ward Accountability Board")
codes = [w["code"] for w in D["wards"]]
ward = c2.selectbox("Ward", codes, index=codes.index("L"),
                    format_func=lambda c: f"{c} · {W[c]['name']}", label_visibility="collapsed")
lang = c3.selectbox("Text language", ["en", "mr", "hi"], format_func={"en": "English", "mr": "मराठी", "hi": "हिंदी"}.get,
                    label_visibility="collapsed")
st.caption("Public board. Data: synthetic dataset. No names of people or parties. "
           "**BMC's win:** answer these before they become headlines — every answered flag shows publicly as ✅.")

w = W[ward]
wf = flags_all[flags_all["ward"] == ward].copy()
wf["key"] = wf["job_id"].astype(str) + "|" + wf["type"] + "|" + wf["evidence"].map(lambda e: str(e.get("complaint_id", "")))
if "status" not in st.session_state:
    st.session_state.status = {}      # key -> answered / held / fix-free / recover
    st.session_state.answers = {}
    st.session_state.plan = set()


def days_left(due):
    return (pd.Timestamp(due) - AS_OF).days


def status_of(key):
    return st.session_state.status.get(key, "pending")


tabs = st.tabs(["1 · Overview", "2 · Red flags", "3 · Contractors", "4 · Next rupee", "5 · Citizen voice"])

# ------------------------------------------------------------------ Tab 1
with tabs[0]:
    over = w["spent_inr"] - w["budget_inr"]
    gap = w["spent_inr"] - w["fair_cost_inr"]
    k = st.columns(5)
    with k[0]: tile("Budget", fmt_inr(w["budget_inr"]), "monsoon road repair", TEAL)
    with k[1]: tile("Spent", fmt_inr(w["spent_inr"]),
                    f"{fmt_inr(over)} over" if over > 0 else f"{fmt_inr(-over)} under", RED if over > 0 else GREEN)
    with k[2]: tile("Should have cost", fmt_inr(w["fair_cost_inr"]), f"{fmt_inr(gap)} paid above fair rate", AMBER)
    with k[3]: tile("Lost on fixes that broke within a month", fmt_inr(w["lost_failed_inr"]), "", RED)
    red_open = [x for x in wf.itertuples() if x.type in ("overcharged", "billed_twice", "guarantee_paid", "built_weak")]
    pend = sum(1 for x in red_open if status_of(x.key) == "pending")
    with k[4]: tile("Open questions", str(pend), "to answer within 30 days", RED)
    answered = len(red_open) - pend
    overdue = sum(1 for x in red_open if status_of(x.key) == "pending" and days_left(x.due_date) < 0)
    st.markdown(f"**BMC answers:** ✅ answered {answered} · ❌ pending {pend} · ⏰ overdue (past 30 days) {overdue}")
    st.markdown("#### Summary")
    for line in str(w["summary"][lang]).split("\n"):
        st.markdown(f"- {line}")
    st.markdown("#### Budget vs spent — all wards")
    df = pd.DataFrame([{"ward": x["code"], "Budget": x["budget_inr"] / 1e5, "Spent": x["spent_inr"] / 1e5} for x in D["wards"]]).set_index("ward")
    st.bar_chart(df, color=[TEAL2, RED], height=260, stack=False)
    st.caption("Values in ₹ lakh. Spent above budget → wards A, E, L, M/W, R/C, R/S.")

# ------------------------------------------------------------------ Tab 2
with tabs[1]:
    st.info("Normal price variation (±30%) is not flagged. Every flag shows its proof.")
    chips = ["All"] + [LABELS[t] for t in RED_TYPES] + [f"(amber) {LABELS[t]}" for t in AMBER_TYPES]
    pick = st.radio("Filter", chips, horizontal=True, label_visibility="collapsed")

    def table(types, title, color):
        d = wf[wf["type"].isin(types)].copy()
        if pick != "All":
            t = [k for k, v in LABELS.items() if v in pick.replace("(amber) ", "")][:1]
            d = d[d["type"].isin(t)]
        st.markdown(f"<h4 style='color:{color}'>{title} ({len(d)})</h4>", unsafe_allow_html=True)
        if d.empty:
            st.caption("Nothing in this section for the current filter.")
            return
        d["Why flagged"] = d["reason"].map(lambda r: r[lang])
        d["30-day clock"] = d["due_date"].map(lambda x: "Overdue" if days_left(x) < 0 else f"{days_left(x)} days left")
        d["Status"] = d["key"].map(lambda k: {"pending": "❌ pending", "answered": "✅ answered", "held": "⏸ payment held",
                                              "fixfree": "🛠 contractor to fix free", "recover": "💸 recover from utility"}[status_of(k)])
        show = d.assign(Job=d["job_id"], Road=d["road_name"], Contractor=d["contractor"], Type=d["type"].map(LABELS),
                        Amount=d["amount_inr"].map(fmt_inr), **{"Fair cost": d["fair_inr"].map(fmt_inr)})
        show = show[["Job", "Type", "Road", "Contractor", "Amount", "Fair cost", "Why flagged", "30-day clock", "Status"]]
        ev = st.dataframe(show, hide_index=True, width="stretch", height=min(420, 38 * (len(show) + 1)),
                          on_select="rerun", selection_mode="single-row", key=f"tbl_{title}")
        sel = ev.selection.rows
        if sel:
            row = d.iloc[sel[0]]
            st.session_state.sel = row["key"]
            evidence_panel(row)

    def evidence_panel(row):
        e = row["evidence"]
        with st.container(border=True):
            st.markdown(f"**Evidence · {row['job_id'] or '—'} · {LABELS[row['type']]}**  \n{row['reason'][lang]}")
            lines = {k: v for k, v in e.items() if v not in ("", None)}
            if row["type"] == "overcharged":
                st.write(f"Rate card: {fmt_inr(e['rate_card_inr'])} {e['unit']} × {e['area_sqm']} → fair {fmt_inr(row['fair_inr'])}; "
                         f"billed {fmt_inr(row['amount_inr'])} = {e['ratio']}× normal.")
            st.json(lines, expanded=False)
            b = st.columns(4)
            k = row["key"]
            if b[0].button("Answer (for BMC)", key=f"a_{k}"):
                st.session_state.show_answer = k
            if b[1].button("Hold payment", key=f"h_{k}"):
                st.session_state.status[k] = "held"; st.rerun()
            if row["type"] in ("guarantee_paid", "built_weak"):
                if b[2].button("Ask contractor to fix free", key=f"f_{k}"):
                    st.session_state.status[k] = "fixfree"; st.rerun()
            if row["type"] == "utility_damage":
                if b[2].button("Recover from utility", key=f"r_{k}"):
                    st.session_state.status[k] = "recover"; st.rerun()
            if st.session_state.get("show_answer") == k:
                txt = st.text_area("Public answer", key=f"t_{k}", value=st.session_state.answers.get(k, ""))
                if st.button("Publish answer ✅", key=f"p_{k}") and txt.strip():
                    st.session_state.answers[k] = txt
                    st.session_state.status[k] = "answered"
                    st.session_state.show_answer = None
                    st.rerun()
            if k in st.session_state.answers:
                st.success(f"BMC answer: {st.session_state.answers[k]}")

    table(RED_TYPES, "🔴 Red flags — strong signals", RED)
    st.markdown("---")
    table(AMBER_TYPES, "🟠 Amber — check with context (not counted as corruption)", AMBER)

# ------------------------------------------------------------------ Tab 3
with tabs[2]:
    scope = st.radio("Compare", ["This ward", "Whole city"], horizontal=True)
    target = ward if scope == "This ward" else "ALL"
    cards = sorted([c for c in D["contractors"] if c["ward"] == target], key=lambda c: -c["score"])
    if scope == "This ward":
        if w["low_lying_index"] >= 0.7:
            st.warning(f"{w['name']} is flood-prone. Contractors are compared only within this ward.")
        else:
            st.info("Contractors are compared only within this ward.")
    cols = st.columns(2)
    gcol = {"A": GREEN, "B": GREEN, "C": AMBER, "D": RED, "F": RED}
    for i, c in enumerate(cards):
        with cols[i % 2].container(border=True):
            a, b = st.columns([1, 4])
            a.markdown(f'<div class="grade" style="color:{gcol[c["grade"]]}">{c["grade"]}</div>', unsafe_allow_html=True)
            b.markdown(f"**{c['name']}** · {c['jobs']} jobs  \n"
                       f"Fix lasted **{c['lasted_n']} of {c['jobs']}** · On time **{c['on_time_n']} of {c['jobs']}** · "
                       f"Price **{c.get('price_label', '')}** · Red flags **{c['red_flags']}**")
            st.caption(c["note"])
    if not cards:
        st.caption("No contractor work recorded.")

# ------------------------------------------------------------------ Tab 4
with tabs[3]:
    rows = D["next_rupee"][ward]
    plan = sum(r["est_cost_inr"] for r in rows if r["road_id"] in st.session_state.plan)
    remaining = w["budget_inr"] - w["spent_inr"]
    c1, c2 = st.columns(2)
    with c1: tile("Plan cost", fmt_inr(plan), "roads added to plan", TEAL)
    with c2: tile("Ward budget left", fmt_inr(remaining) if remaining >= 0 else "Already over budget",
                  fmt_inr(remaining) if remaining < 0 else "", GREEN if remaining >= 0 else RED)
    st.caption("Cold-mix patches break most often — about 1 in 4 repairs fail within a month. One lasting repair costs less than five patches.")
    for i, r in enumerate(rows, 1):
        with st.container(border=True):
            a, b, c = st.columns([5, 2, 1.4])
            a.markdown(f"**{i}. {r['name']}** ({r['road_id']})  \n" + " ".join(f'<span class="chip">{x}</span>' for x in r["reasons"])
                       + f"  \n{r['fix']}", unsafe_allow_html=True)
            b.markdown(f"<span style='font-size:1.4rem;font-weight:700;color:{GREEN if r['est_cost_inr']==0 else '#1C1B19'}'>"
                       f"{fmt_inr(r['est_cost_inr'])}</span>  \nrisk {r['risk']}", unsafe_allow_html=True)
            on = r["road_id"] in st.session_state.plan
            if c.button("✓ In plan" if on else "Add to plan", key=f"plan_{r['road_id']}"):
                (st.session_state.plan.discard if on else st.session_state.plan.add)(r["road_id"]); st.rerun()

# ------------------------------------------------------------------ Tab 5
with tabs[4]:
    cz = D["citizen"][ward]
    st.markdown("#### What citizens are saying")
    for line in str(w["citizen_voice"][lang]).split("\n"):
        st.markdown(f"- {line}")
    k = st.columns(4)
    k[0].metric("Complaints", cz["complaints"]); k[1].metric("Duplicates merged", cz["duplicates_merged"])
    k[2].metric("Issues", cz["issues"]); k[3].metric("Never fixed", cz["never_fixed"])
    left, right = st.columns([3, 1])
    with left:
        st.markdown("#### Complaints → issues → jobs")
        il = pd.DataFrame(cz["issue_list"])
        if len(il):
            il["status"] = il["status"].map({"Fixed": "✅ Fixed", "Never fixed": "❌ Never fixed", "Closed by old repair": "🟠 Closed by old repair"})
            st.dataframe(il.rename(columns={"title": "Issue", "quote": "Complaint (original language)", "merged": "Merged",
                                            "job": "Job", "status": "Status", "upvotes": "Me too"})
                         [["Issue", "Complaint (original language)", "Merged", "Job", "Status", "Me too"]],
                         hide_index=True, width="stretch", height=420)
    with right:
        st.markdown("#### Most 'Me too'")
        for r in cz["me_too_roads"]:
            st.markdown(f"**{r['me_too']}** · {r['name']} ({r['road_id']})")
