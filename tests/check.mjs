// Runs the app's real logic (app/i18n.js + app/app.js) in a sandbox against the spec's test cases.
// Usage: node tests/check.mjs
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const root = new URL("..", import.meta.url).pathname;
const data = JSON.parse(readFileSync(root + "app/apla_data.json", "utf8"));
const mem = new Map();
const ctx = vm.createContext({
  window: { APLA_DATA: data },
  localStorage: { getItem: (k) => mem.get(k) ?? null, setItem: (k, v) => mem.set(k, String(v)) },
  location: { hash: "", href: "file:///apla/index.html" },
  history: {}, navigator: {}, console,
});
for (const f of ["app/i18n.js", "app/app.js"]) vm.runInContext(readFileSync(root + f, "utf8"), ctx, { filename: f });
const app = vm.runInContext(`indexData(window.APLA_DATA); ({ money, nearestRoad, searchRoads, translations, state, roadWaText, rtiFilled, roadById })`, ctx);

let failed = 0;
const check = (name, ok, detail = "") => {
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${ok ? "" : "  → " + detail}`);
  if (!ok) failed += 1;
};
const road = (id) => data.roads.find((r) => r.road_id === id);
const ids = (groups) => (groups || []).flatMap((g) => g.roads.map((r) => r.road_id));
const wards = (groups) => new Set((groups || []).flatMap((g) => g.roads.map((r) => r.ward)));

// A1 nearest road
const r220 = road("RD0220");
const mid = [(r220.start[0] + r220.end[0]) / 2, (r220.start[1] + r220.end[1]) / 2];
check("A1 nearest road to Kurla Andheri Road midpoint", app.nearestRoad(...mid).road.road_id === "RD0220");
check("A2 sample road exists for location fallback", !!road(data.meta.demo_road));

// A3 / A4 search
check("A3 search कुर्ला lists L ward roads", wards(app.searchRoads("कुर्ला")).has("L"));
check("A4 search Saki Naka lists L ward roads", wards(app.searchRoads("Saki Naka")).has("L"));
check("    search 'kurla andheri' finds RD0220", ids(app.searchRoads("kurla andheri")).includes("RD0220"));

// A5 every label exists in every language, same kind
const keys = (l) => Object.keys(app.translations[l]).sort();
const missing = ["hi", "en"].flatMap((l) => keys("mr").filter((k) => !(k in app.translations[l])).map((k) => `${l}.${k}`))
  .concat(["hi", "en"].flatMap((l) => keys(l).filter((k) => !(k in app.translations.mr)).map((k) => `mr.${k}`)));
check("A5 all translation keys present in mr/hi/en", missing.length === 0, missing.join(", "));
const kinds = keys("mr").filter((k) => ["hi", "en"].some((l) => typeof app.translations[l][k] !== typeof app.translations.mr[k]));
check("    translation value types match", kinds.length === 0, kinds.join(", "));
const nums = (l) => { app.state.lang = l; return (app.roadWaText(r220).match(/\d+(\.\d+)?/g) || []).sort().join(","); };
check("    numbers identical across languages in WhatsApp text", nums("mr") === nums("hi") && nums("hi") === nums("en"), [nums("mr"), nums("hi"), nums("en")].join(" | "));

// A6 / A7 / A9 / A10 data states exist
check("A6 roads under guarantee exist", data.roads.some((r) => r.under_guarantee && r.jobs));
check("A7 roads not under guarantee exist", data.roads.some((r) => !r.under_guarantee && r.jobs));
check("A9 'no repair yet' roads exist", data.roads.some((r) => r.owner === "BMC" && !r.jobs && r.open_complaints > 0));
check("A10 roads with zero flags exist", data.roads.some((r) => r.jobs && !r.flags.length));

// A12 WhatsApp text per language
app.state.lang = "mr";
const mr = app.roadWaText(r220);
check("A12 Marathi WhatsApp text filled", mr.includes("Kurla Andheri Road (कुर्ला)") && mr.includes("₹30.5 लाख") && mr.includes("हमी कालावधीत") && mr.includes("#/road/RD0220"), mr);
app.state.lang = "en";
check("    English WhatsApp text filled", app.roadWaText(r220).includes("₹30.5 lakh spent this monsoon · repaired 35 times · broke again 6 times"));

// A13 RTI carries this road's real job ids and placeholders get filled
const flagged = r220.flags.filter((f) => f.type !== "built_weak").flatMap((f) => f.jobs);
check("A13 RTI cites real flagged job ids", flagged.slice(0, 3).every((j) => r220.rti_text.en.includes(j)));
const noPlaceholder = data.roads.filter((r) => r.rti_text).filter((r) => ["en", "mr", "hi"].some((l) => !["{NAME}", "{ADDRESS}", "{DATE}"].every((p) => r.rti_text[l].includes(p))));
check("    every RTI has NAME/ADDRESS/DATE slots in 3 languages", noPlaceholder.length === 0, noPlaceholder.map((r) => r.road_id).join(","));
app.state.rtiName = "Raju";
check("    name is filled into the RTI", app.rtiFilled(r220).includes("Raju") && !app.rtiFilled(r220).includes("{NAME}"));

// A16 money formatting, and JS matches the Python builder
check("A16 ₹3052667 → ₹30.5 लाख", app.money(3052667, "mr") === "₹30.5 लाख", app.money(3052667, "mr"));
check("A16 ₹13043418 → ₹1.30 कोटी", app.money(13043418, "mr") === "₹1.30 कोटी", app.money(13043418, "mr"));
const samples = [0, 999, 90000, 99999, 100000, 164123, 195064, 999999, 1000000, 1820204, 9999999, 10000000, 13043418, 123456789];
const py = JSON.parse(execFileSync("python3", ["-I", "-c",
  `import sys,json; sys.path.insert(0, sys.argv[1]); from build_apla_data import money; print(json.dumps([money(int(x), l) for x in sys.argv[2:] for l in ("mr","hi","en")], ensure_ascii=False))`,
  root + "scripts", ...samples.map(String)], { encoding: "utf8" }));
const js = samples.flatMap((x) => ["mr", "hi", "en"].map((l) => app.money(x, l)));
const diffs = js.map((v, i) => (v === py[i] ? null : `${v} vs py ${py[i]}`)).filter(Boolean);
check("    JS money() == Python money() on 42 samples", diffs.length === 0, diffs.join("; "));

// §6 words never shown, §1 politics-free
const shown = JSON.stringify([app.translations, data.roads.map((r) => [r.flags.map((f) => f.text), r.rti_text]), data.wards.map((w) => w.summary)]);
const banned = ["SLA", "DLP", "audit", "variance", "anomaly", "reopened", "%"].filter((w) => shown.includes(w));
check("§6 no banned words (SLA, DLP, audit, %, …)", banned.length === 0, banned.join(", "));
const political = ["corporator", "nagarsevak", "नगरसेवक", "BJP", "Shiv Sena", "Congress", "MNS", "NCP"].filter((w) => shown.includes(w));
check("§1 no party / corporator names", political.length === 0, political.join(", "));

console.log(failed ? `\n${failed} check(s) failed` : "\nall checks passed");
process.exit(failed ? 1 : 0);
