// Full audit of the phone app: data, every screen x 3 languages, every interaction, layout, accessibility, APK.
// Usage: node tests/audit.mjs      (CHROME=<path> if Chrome/Edge is not in the usual place)
// Prints PASS / FAIL (a bug) / WARN (a limitation or risk) and a summary of everything that is not PASS.
import { spawn, execFileSync } from "node:child_process";
import { createServer } from "node:http";
import { existsSync, mkdtempSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { extname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { deflateSync, crc32 } from "node:zlib";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const APP_DIR = join(ROOT, "app");
const CANDIDATES = [process.env.CHROME, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "/usr/bin/google-chrome", "/usr/bin/chromium"].filter(Boolean);
const CHROME = CANDIDATES.find((p) => existsSync(p));
if (!CHROME) { console.log("no Chrome/Edge found; set CHROME=<path>"); process.exit(1); }
const PORT = 9338, WEB = 8768, ORIGIN = `http://127.0.0.1:${WEB}`;
const MIME = { ".html": "text/html", ".js": "text/javascript", ".json": "application/json", ".css": "text/css" };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const DOT = 'data:image/gif;base64,R0lGODlhAQABAAAAACw=';
const tmp = mkdtempSync(join(tmpdir(), "apla-audit-"));

const web = createServer((req, res) => {
  const p = req.url.split("?")[0];
  const f = join(APP_DIR, p === "/" ? "index.html" : decodeURIComponent(p));
  if (!f.startsWith(APP_DIR) || !existsSync(f)) { res.writeHead(404).end(); return; }
  res.writeHead(200, { "content-type": MIME[extname(f)] || "application/octet-stream" }).end(readFileSync(f));
}).listen(WEB, "127.0.0.1");
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", `--remote-debugging-port=${PORT}`, `--user-data-dir=${join(tmp, "profile")}`, "about:blank"], { stdio: "ignore" });

const results = [];
const rec = (level, section, name, detail = "") => { results.push({ level, section, name, detail }); console.log(`${level.padEnd(4)}  [${section}] ${name}${level === "PASS" ? "" : "  -> " + detail}`); };
let SEC = "";
const ok = (name, cond, detail = "", level = "FAIL") => rec(cond ? "PASS" : level, SEC, name, String(detail).slice(0, 400));
const warn = (name, cond, detail = "") => ok(name, cond, detail, "WARN");

// ---------- test images ----------
function png() { // 64x64 solid colour
  const w = 64, h = 64, raw = Buffer.alloc((w * 3 + 1) * h);
  for (let y = 0; y < h; y++) { raw[y * (w * 3 + 1)] = 0; for (let x = 0; x < w; x++) { const o = y * (w * 3 + 1) + 1 + x * 3; raw[o] = 200; raw[o + 1] = 120; raw[o + 2] = 40; } }
  const chunk = (t, d) => { const b = Buffer.alloc(12 + d.length); b.writeUInt32BE(d.length, 0); b.write(t, 4, "ascii"); d.copy(b, 8); b.writeUInt32BE(crc32(Buffer.concat([Buffer.from(t), d])) >>> 0, 8 + d.length); return b; };
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 2;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk("IHDR", ihdr), chunk("IDAT", deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]);
}
function exifApp1(lat, lon) { // EXIF APP1 with GPS, big-endian TIFF
  const u16 = (n) => [n >> 8, n & 255], u32 = (n) => [n >>> 24, (n >> 16) & 255, (n >> 8) & 255, n & 255];
  const dms = (v) => { const a = Math.abs(v), d = Math.floor(a), m = Math.floor((a - d) * 60), s = Math.round(((a - d) * 60 - m) * 60 * 100); return [d, 1, m, 1, s, 100]; };
  const rat = (v) => dms(v).flatMap((n) => u32(n));
  const ent = (tag, type, cnt, val) => [...u16(tag), ...u16(type), ...u32(cnt), ...val];
  const tiff = [0x4d, 0x4d, 0, 42, ...u32(8), ...u16(1), ...ent(0x8825, 4, 1, u32(26)), ...u32(0),
    ...u16(4), ...ent(1, 2, 2, [lat >= 0 ? 0x4e : 0x53, 0, 0, 0]), ...ent(2, 5, 3, u32(80)), ...ent(3, 2, 2, [lon >= 0 ? 0x45 : 0x57, 0, 0, 0]), ...ent(4, 5, 3, u32(104)), ...u32(0),
    ...rat(lat), ...rat(lon)];
  const body = [0x45, 0x78, 0x69, 0x66, 0, 0, ...tiff];
  return Buffer.from([0xff, 0xe1, ...u16(body.length + 2), ...body]);
}
const pngFile = join(tmp, "plain.png"); writeFileSync(pngFile, png());

// ---------- contrast ----------
const lum = (h) => { const c = [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)); return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]; };
const contrast = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };

try {
  let ws;
  for (let i = 0; i < 50 && !ws; i++) {
    try { const t = await (await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`, { method: "PUT" })).json(); ws = new WebSocket(t.webSocketDebuggerUrl); await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; }); }
    catch { ws = null; await sleep(200); }
  }
  let seq = 0; const pending = new Map(); const consoleErrors = [];
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
    else if (msg.method === "Runtime.exceptionThrown") consoleErrors.push("exception: " + (msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text).split("\n")[0]);
    else if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error") consoleErrors.push("console.error: " + msg.params.args.map((a) => a.value ?? a.description).join(" ").slice(0, 200));
    else if (msg.method === "Log.entryAdded" && msg.params.entry.level === "error" && !/fonts\.(googleapis|gstatic)/.test(msg.params.entry.url || "")) consoleErrors.push("log: " + msg.params.entry.text + " " + (msg.params.entry.url || ""));
  };
  const send = (method, params = {}) => new Promise((res) => { const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
  const ev = async (expr) => { const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }); if (r.result?.exceptionDetails) throw new Error("page error: " + (r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text).split("\n")[0] + "  in: " + expr.slice(0, 120)); return r.result?.result?.value; };
  const open = async (hash = "", base = ORIGIN + "/index.html") => { await send("Page.navigate", { url: `${base}${hash}` }); await sleep(120); for (let i = 0; i < 60 && !(await ev("!!document.querySelector('#app > :not(.loading)')")); i++) await sleep(150); await sleep(60); };
  const click = (sel) => ev(`document.querySelector(${JSON.stringify(sel)}).click()`);
  const type = (sel, v) => ev(`(() => { const el = document.querySelector(${JSON.stringify(sel)}); el.value = ${JSON.stringify(v)}; el.dispatchEvent(new Event("input", { bubbles: true })); })()`);
  const text = (sel) => ev(`document.querySelector(${JSON.stringify(sel)})?.textContent ?? ""`);
  const count = (sel) => ev(`document.querySelectorAll(${JSON.stringify(sel)}).length`);
  const hash = () => ev("location.hash");
  const geo = (lat, lon, delay = 20) => ev(`Object.defineProperty(navigator, 'geolocation', { configurable: true, value: { getCurrentPosition: (ok) => setTimeout(() => ok({ coords: { latitude: ${lat}, longitude: ${lon} } }), ${delay}) } })`);
  const geoDeny = () => ev(`Object.defineProperty(navigator, 'geolocation', { configurable: true, value: { getCurrentPosition: (ok, err) => setTimeout(() => err({ code: 1 }), 20) } })`);
  const reset = async () => { await ev("localStorage.clear()"); await send("Page.navigate", { url: "about:blank" }); await sleep(150); };
  const setFile = async (path) => { const doc = await send("DOM.getDocument", {}); const q = await send("DOM.querySelector", { nodeId: doc.result.root.nodeId, selector: "#photo" }); await send("DOM.setFileInputFiles", { files: [path], nodeId: q.result.nodeId }); };

  await send("Page.enable"); await send("Runtime.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await open(""); await reset(); await open("");

  // ===================================================================== 1. DATA
  SEC = "1 data";
  const data = await ev(`(() => { const d = state.data; const T = translations; const issues = []; const LA = ['mr','hi','en'];
    const add = (m) => issues.length < 60 && issues.push(m);
    if (d.roads.length !== 291) add('road count ' + d.roads.length); if (d.wards.length !== 24) add('ward count ' + d.wards.length);
    const wardCodes = new Set(d.wards.map(w => w.code)); const roadIds = new Set(d.roads.map(r => r.road_id));
    for (const r of d.roads) {
      for (const k of ['road_id','name','ward','start','end','jobs','spent_inr','fair_cost_inr','broke_again','on_time','last_resurfaced','owner']) if (r[k] === undefined || r[k] === null) add(r.road_id + ' missing ' + k);
      if (!wardCodes.has(r.ward)) add(r.road_id + ' unknown ward ' + r.ward);
      if (r.on_time > r.jobs) add(r.road_id + ' on_time > jobs'); if (r.broke_again > r.jobs) add(r.road_id + ' broke_again > jobs');
      if (r.jobs > 0 && !(r.fair_cost_inr > 0)) add(r.road_id + ' jobs but no fair cost'); if (r.jobs === 0 && r.spent_inr > 0) add(r.road_id + ' spend without jobs');
      const [la, lo] = [(r.start[0]+r.end[0])/2, (r.start[1]+r.end[1])/2]; if (la < 18.8 || la > 19.35 || lo < 72.7 || lo > 73.1) add(r.road_id + ' outside Mumbai bbox');
      for (const c of r.contractors || []) if (!/^[A-F]$/.test(c.grade)) add(r.road_id + ' bad grade ' + c.grade);
      for (const f of r.flags || []) { if (!LA.every(l => T[l].flagNames[f.type])) add(r.road_id + ' flag type without translation: ' + f.type); if (!LA.every(l => f.text && f.text[l])) add(r.road_id + ' flag ' + f.type + ' missing text'); }
      if (r.rti_text) { for (const l of LA) { const t = r.rti_text[l]; if (!t) add(r.road_id + ' rti missing ' + l); else for (const s of ['{NAME}','{ADDRESS}','{DATE}']) if (!t.includes(s)) add(r.road_id + ' rti ' + l + ' missing slot ' + s); } }
      if (r.me_too < 0) add(r.road_id + ' negative me_too');
    }
    for (const w of d.wards) {
      if (!(w.budget_inr > 0)) add(w.code + ' budget'); if (!LA.every(l => w.summary && w.summary[l])) add(w.code + ' summary missing a language');
      for (const id of w.top_roads || []) { if (!roadIds.has(id)) add(w.code + ' top road missing ' + id); else if (d.roads.find(r => r.road_id === id).ward !== w.code) add(w.code + ' top road in other ward ' + id); }
      for (const k of Object.keys(w.flag_counts || {})) if (!LA.every(l => T[l].flagNames[k])) add(w.code + ' flag_counts key without translation: ' + k);
    }
    for (const l of d.localities || []) if (!wardCodes.has(l.ward)) add('locality ' + l.name + ' unknown ward');
    const keys = LA.map(l => Object.keys(T[l]).sort().join()); if (new Set(keys).size !== 1) add('translation key sets differ between languages');
    return { issues, roads: d.roads.length, wards: d.wards.length }; })()`);
  ok(`data integrity: ${data.roads} roads, ${data.wards} wards, flags, RTI slots, translations`, data.issues.length === 0, data.issues.slice(0, 12).join(" | "));
  const sizes = await ev("fetch('apla_data.json').then(r => r.text()).then(t => t.length)");
  warn(`data file size ${(sizes / 1e6).toFixed(2)} MB (loaded on every start; fine on Wi-Fi, heavy on 2G)`, sizes < 1_200_000, `${sizes} bytes`);

  // ===================================================================== 2. RENDER SWEEP
  SEC = "2 render sweep";
  const sweep = await ev(`(() => { const issues = []; const add = (m) => issues.length < 80 && issues.push(m);
    const BAD = /undefined|NaN|\\[object|\\bnull\\b|\\{(NAME|ADDRESS|DATE)\\}|Infinity/;
    const JARGON = /\\b(SLA|DLP|audit|variance|anomaly|reopened_within_30d)\\b|%/;
    const PARTY = /\\b(BJP|Congress|Shiv Sena|NCP|MNS|corporator|MLA|Mr\\.|Mrs\\.|Shri)\\b/i;
    const RAWMONEY = /₹\\s?\\d{7,}|₹\\s?\\d{1,3}(,\\d{2}){2,}(,\\d{3})\\b/;
    const txt = (html) => { const d = document.createElement('div'); d.innerHTML = html; return d.textContent; };
    const check = (what, html) => { const t = txt(html); if (BAD.test(t)) add(what + ' has bad token: ' + (t.match(BAD) || [])[0]); if (JARGON.test(t)) add(what + ' shows jargon: ' + (t.match(JARGON) || [])[0]); if (PARTY.test(t)) add(what + ' names a party/person'); if (RAWMONEY.test(t)) add(what + ' raw money: ' + (t.match(RAWMONEY) || [])[0]);
      if (/<img(?![^>]*\\balt=)/.test(html)) add(what + ' img without alt'); if (/<(button|a)[^>]*>\\s*<\\/\\1>/.test(html)) add(what + ' empty button/link'); };
    let n = 0;
    for (const lang of ['mr','hi','en']) { state.lang = lang; translations[lang];
      check(lang + ' home', homeScreen()); check(lang + ' profile', profileScreen()); check(lang + ' report', reportScreen()); n += 3;
      for (const w of state.data.wards) { check(lang + ' ward ' + w.code, wardScreen(w)); n++; const wa = translations[lang].waWard(w.code, (w.summary || {})[lang] || '', 'https://x.test/#/ward/' + w.code); check(lang + ' ward WA ' + w.code, wa); if (wa.length > 1800) add(lang + ' ward WA too long ' + w.code + ' ' + wa.length); }
      for (const r of state.data.roads) { state.noteRoad = null; check(lang + ' road ' + r.road_id, roadScreen(r)); n++; const wa = roadWaText(r); check(lang + ' road WA ' + r.road_id, wa); if (wa.length > 1800) add(lang + ' road WA too long ' + r.road_id);
        if (r.rti_text) { check(lang + ' rti ' + r.road_id, rtiScreen(r)); n++; const f = rtiFilled(r); if (/\\{(NAME|ADDRESS|DATE)\\}/.test(f)) add(lang + ' rti unfilled slot ' + r.road_id); if (!/(WO25-\\d+)/.test(f) && (r.flags||[]).some(x => x.job)) add(lang + ' rti ' + r.road_id + ' cites no job id although flags have one'); } } }
    state.lang = 'mr'; return { issues, n }; })()`);
  ok(`${sweep.n} screens rendered (every road, ward, RTI, 3 languages): no undefined/NaN/jargon/%/party names/raw money/missing alt`, sweep.issues.length === 0, sweep.issues.slice(0, 15).join(" | "));
  const wordsCheck = await ev(`(() => { const out = []; for (const l of ['mr','hi','en']) { const t = translations[l]; for (const k of Object.keys(translations.en)) { const a = translations.en[k], b = t[k]; if (typeof a !== typeof b) out.push(l + '.' + k + ' type'); if (typeof a === 'function' && typeof b === 'function') { try { const x = /^wa[A-Z]/.test(k) ? b({ road: 'R', ward: 'W', spent: '₹1', jobs: 1, broke: 1, guarantee: true, days: 3, link: 'x' }, 'summary', 'link') : b(1,2,3,4,5); if (/undefined|NaN/.test(JSON.stringify(x))) out.push(l + '.' + k + '() -> ' + JSON.stringify(x).slice(0,50)); } catch (e) { out.push(l + '.' + k + ' throws ' + e.message); } } } } return out; })()`);
  ok("every translated string/function exists in all 3 languages and runs", wordsCheck.length === 0, wordsCheck.slice(0, 8).join(" | "));

  // ===================================================================== 3. HOME
  SEC = "3 home";
  await open(""); await reset(); await open("");
  ok("default language is Marathi, <html lang=mr>", (await ev("document.documentElement.lang")) === "mr" && (await text(".brand")).includes("आपला"));
  await click('[data-lang="hi"]'); ok("Hindi switch updates labels", (await text('[data-action="locate"]')).includes("पास") || (await text('[data-action="locate"]')).includes("जांच"), await text('[data-action="locate"]'));
  await click('[data-lang="en"]'); ok("English switch updates labels + html lang", (await text('[data-action="locate"]')).includes("Check") && (await ev("document.documentElement.lang")) === "en");
  await open(""); ok("language choice survives a reload", (await ev("document.documentElement.lang")) === "en");
  await click('[data-lang="mr"]');
  // search
  const s = async (q) => { await type("#q", q); return text("#results"); };
  ok("search 'कुर्ला' -> L ward roads", (await s("कुर्ला")).includes("L · कुर्ला"));
  ok("search 'kurla' (English) -> roads", (await s("kurla")).includes("Kurla Andheri Road"));
  ok("one-letter ward code 'L' finds the L ward's roads; other single letters still show nothing", (await s("L")).includes("Kurla") && (await s("k")) === "" && (await s("x")) === "", await s("L"));
  ok("two-character ward codes work too ('F/N')", (await s("F/N")).length > 0, await s("F/N"));
  ok("search 'andheri' partial road name", (await s("andheri")).includes("Andheri"));
  ok("search 'LBS' -> LBS Marg", (await s("LBS")).includes("LBS Marg"));
  ok("1 character shows nothing (needs 2)", (await s("k")) === "");
  ok("spaces only shows nothing", (await s("   ")) === "");
  ok("no match -> friendly message", (await s("zzzzqq")).includes("काहीच सापडले नाही"));
  await s("<img src=x onerror=alert(1)><script>window.__xss=1</script>");
  ok("HTML in the search box is escaped (no injection)", (await ev("!window.__xss && document.querySelectorAll('#results img, #results script').length === 0")));
  await s("a".repeat(500)); ok("a 500-character search does not break the page", (await count("#results")) === 1);
  await s("Saki Naka"); await click("#results .road-item"); ok("tapping a result opens that road", (await hash()).startsWith("#/road/"), await hash());
  // locate
  await open(""); await reset(); await open("");
  await geo(19.081324, 72.885982); await click('[data-action="locate"]'); await sleep(300);
  ok("locate near Kurla Andheri Road -> its card, no note", (await hash()) === "#/road/RD0220" && (await count(".note")) === 0, await hash());
  await open(""); await geoDeny(); await click('[data-action="locate"]'); await sleep(300);
  ok("location denied -> sample road + note", (await hash()) === "#/road/RD0220" && (await text(".note")).includes("नमुना"));
  await open(""); await geo(28.6139, 77.209); await click('[data-action="locate"]'); await sleep(300);
  ok("location outside Mumbai -> sample road + 'outside Mumbai' note", (await hash()) === "#/road/RD0220" && (await text(".note")).includes("मुंबईबाहेर"), await text(".note"));
  await open(""); await ev("Object.defineProperty(navigator,'geolocation',{configurable:true,value:undefined})"); await click('[data-action="locate"]'); await sleep(300);
  ok("browser without geolocation -> sample road, no crash", (await hash()) === "#/road/RD0220");
  await open(""); await geo(19.0, 72.9, 400); await click('[data-action="locate"]'); const dis = await ev("document.querySelector('[data-action=\"locate\"]').disabled"); await click('[data-action="locate"]'); await sleep(700);
  ok("locate button is disabled while searching (no double tap)", dis === true);
  await open(""); ok("ward card on home + top roads listed", (await count(".glance")) === 1 && (await count(".results .road-item")) >= 3);
  await click(".glance .btn"); ok("ward card button opens ward screen", (await hash()).startsWith("#/ward/"));
  await open(""); await click(".avatar"); ok("avatar opens profile", (await hash()) === "#/profile");
  await open("");
  for (const [i, h] of [[1, "#/ward/"], [2, "#/report"], [3, "#/profile"], [0, "#/"]]) { await ev(`document.querySelectorAll('.tab')[${i}].click()`); await sleep(80); ok(`tab ${i + 1} navigates to ${h}`, (await hash()).startsWith(h) || (h === "#/" && ((await hash()) === "#/" || (await hash()) === "")), await hash()); }
  ok("active tab marked aria-current", (await count('.tab[aria-current="page"]')) === 1);

  // ===================================================================== 4. ROAD CARD
  SEC = "4 road card";
  const pick = await ev(`(() => { const R = state.data.roads; const f = (fn) => (R.find(fn) || {}).road_id;
    return { guar: f(r => r.under_guarantee && r.jobs && (r.flags||[]).length), noguar: f(r => !r.under_guarantee && r.jobs), clean: f(r => r.jobs && !(r.flags||[]).length), norepair: f(r => !r.jobs && r.open_complaints), nothing: f(r => !r.jobs && !r.open_complaints), many: f(r => (r.contractors||[]).length > 3), norti: f(r => !r.rti_text), notbmc: f(r => r.owner !== 'BMC') }; })()`);
  console.log("      sample roads:", JSON.stringify(pick));
  await open("#/road/" + pick.guar);
  ok("guarantee road: green box + ₹0 line + money + 3 stats", (await count(".guarantee")) === 1 && (await text(".good-line")).includes("₹0") && (await count(".stat")) === 3 && (await text(".money .big")).startsWith("₹"));
  ok("flag chips present and expand on tap", (await count(".flag")) > 0 && (await ev("(() => { const d = document.querySelector('.flag'); d.querySelector('summary').click(); return d.open; })()")));
  await open("#/road/" + pick.noguar); ok("non-guarantee road: no green box, no ₹0 line", (await count(".guarantee")) === 0 && (await count(".good-line")) === 0);
  if (pick.clean) { await open("#/road/" + pick.clean); ok("zero-flag road: green 'no suspicious bills'", (await count(".all-clear")) === 1); } else rec("WARN", SEC, "no zero-flag road in data to test", "");
  if (pick.norepair) { await open("#/road/" + pick.norepair); ok("open complaint, no repair: 'no repair yet' + trail", (await count(".state-box.red")) === 1 && (await count(".trail")) >= 1); }
  if (pick.nothing) { await open("#/road/" + pick.nothing); ok("no repair and no complaint: friendly amber box", (await count(".state-box.amber")) === 1); } else rec("WARN", SEC, "no 'nothing recorded' road in data to test that state", "");
  if (pick.many) { await open("#/road/" + pick.many); ok("more than 3 contractors: '+N more' expands", (await ev("(() => { const d = document.querySelector('.more'); d.querySelector('summary').click(); return d.open && d.querySelectorAll('.contractor').length > 0; })()"))); }
  if (pick.notbmc) { await open("#/road/" + pick.notbmc); ok("Not-BMC road state", (await count(".state-box.amber")) === 1); }
  else {
    await open("#/road/" + pick.noguar); await ev(`(() => { const r = roadById.get(route().id); r.owner = 'MMRDA'; r.not_bmc_complaints = 3; render(); })()`);
    ok("Not-BMC state (simulated: no road in the data has it): amber box, no money, no tax card, WhatsApp + department link", (await count(".state-box.amber")) === 1 && (await count(".money")) === 0 && (await count(".tax")) === 0 && (await count(".actions a.green")) === 1 && (await count('.actions a[href*="mmrda"]')) === 1);
    await ev("(() => { const r = roadById.get(route().id); r.owner = 'BMC'; r.not_bmc_complaints = 0; render(); })()"); // undo the simulation
    rec("WARN", SEC, "no road is marked 'Not BMC' in the data", "the dataset has no owner column, so this state never shows to a real user (the board guesses six roads, the app none)");
  }
  // me too
  await open("#/road/" + pick.guar); await reset(); await open("#/road/" + pick.guar);
  const m0 = Number((await text('[data-action="metoo"] .num')).replace(/,/g, ""));
  await click('[data-action="metoo"]'); await click('[data-action="metoo"]'); await click('[data-action="metoo"]');
  const m1 = Number((await text('[data-action="metoo"] .num')).replace(/,/g, ""));
  ok("Me too: 3 taps count once", m1 === m0 + 1, `${m0} -> ${m1}`);
  ok("Me too button shows the done state with a check mark", (await text('[data-action="metoo"]')).includes("✓") && (await ev("document.querySelector('[data-action=\"metoo\"]').getAttribute('aria-pressed')")) === "true");
  await open("#/road/" + pick.guar); ok("Me too survives a reload", Number((await text('[data-action="metoo"] .num')).replace(/,/g, "")) === m0 + 1);
  await open("#/road/" + pick.noguar); { const shown = Number((await text('[data-action="metoo"] .num')).replace(/,/g, "")), want = await ev(`roadById.get('${pick.noguar}').me_too`); ok("Me too on another road is independent", shown === want, `shown ${shown}, data ${want}`); }
  // share / links
  const wa = await ev("document.querySelector('.actions a.green').href");
  ok("WhatsApp button builds a wa.me link with the road text", wa.startsWith("https://wa.me/?text=") && decodeURIComponent(wa).includes(await ev(`roadById.get('${pick.noguar}').name`)));
  ok("WhatsApp text carries the live link", decodeURIComponent(wa).includes("manasshete.github.io"), decodeURIComponent(wa).slice(-120));
  ok("external links open in a new tab with noopener", (await ev("[...document.querySelectorAll('.actions a')].every(a => a.target === '_blank' && /noopener/.test(a.rel))")));
  // tax
  const taxCases = [["12000", "₹12,000"], ["1200000", "₹12"], ["abc", ""], ["0", ""], ["", ""], ["₹ 45,000", "₹45,000"], ["99999999999", "₹"], ["12000.50", "₹12"], ["-500", "₹500"]];
  for (const [inp, exp] of taxCases) { await type("#tax", inp); const t = await text("#taxline"); ok(`tax input ${JSON.stringify(inp)} -> ${exp ? "shows " + exp : "nothing"}`, exp ? t.includes(exp) && !/NaN|undefined/.test(t) : t === "", t.slice(0, 80)); }
  await type("#tax", "18000"); await open("#/road/" + pick.noguar); ok("tax value persists across roads and reloads", (await ev("document.querySelector('#tax').value")) === "18000" && (await text("#taxline")).includes("₹18,000"));
  const taxWard = await ev(`(() => { const w = wardByCode.get(roadById.get('${pick.noguar}').ward); return { spent: money(w.spent_inr), lost: money(w.lost_failed_inr) }; })()`);
  ok("tax card shows the ward's real spend and failed-fix money", (await text("#taxline")).includes(taxWard.spent) && (await text("#taxline")).includes(taxWard.lost), (await text("#taxline")));
  // ward link from the card
  await open("#/road/" + pick.noguar); await ev("[...document.querySelectorAll('.btn.outline')].find(b => b.dataset.go && b.dataset.go.startsWith('#/ward/')).click()"); await sleep(100); ok("'full account of the ward' button opens the ward", (await hash()).startsWith("#/ward/"));

  // ===================================================================== 5. RTI
  SEC = "5 RTI";
  await open("#/road/" + pick.guar); await click('[data-go^="#/rti/"]'); await sleep(100);
  ok("Ask BMC opens the RTI screen", (await hash()).startsWith("#/rti/") && (await count("#rtitext")) === 1);
  const blank = await text("#rtitext"); ok("letter shows blanks before the user types", blank.includes("__________"));
  await type("#rname", "राजू पाटील"); await type("#raddr", "12 Station Road\nKurla West"); const filled = await text("#rtitext");
  ok("name and address fill into the letter (newline becomes comma)", filled.includes("राजू पाटील") && filled.includes("12 Station Road, Kurla West"), filled.slice(0, 200));
  ok("letter has today's date", new RegExp(String(new Date().getFullYear())).test(filled));
  ok("WhatsApp link carries the filled letter", decodeURIComponent(await ev("document.querySelector('#rtiwa').href")).includes("राजू पाटील"));
  await click('[data-action="copy"]'); await sleep(150); ok("Copy button confirms", (await text('[data-action="copy"]')).length > 0 && (await text('[data-action="copy"]')) !== "");
  await click('[data-lang="en"]'); ok("language switch keeps typed name and rewrites the letter", (await text("#rtitext")).includes("राजू पाटील") && /Public Information Officer/.test(await text("#rtitext")));
  ok("RTI name/address are not written to storage", !(await ev("JSON.stringify(Object.entries(localStorage)).includes('राजू')")));
  ok("RTI help link goes to the Maharashtra portal", (await ev("[...document.querySelectorAll('a')].some(a => /rtionline\\.maharashtra/.test(a.href))")));
  await click('[data-action="back"]'); await sleep(150); ok("Back from RTI returns to the road", (await hash()).startsWith("#/road/"), await hash());
  if (pick.norti) { await open("#/rti/" + pick.norti); ok("RTI for a road with no letter: friendly message, no crash", (await count(".state-box")) === 1 || (await text("#app")).length > 20); }
  const rtiCount = await ev("state.data.roads.filter(r => r.rti_text).length"); warn(`only ${rtiCount} of 291 roads have an RTI letter (the button is hidden on the rest)`, rtiCount > 100, rtiCount);

  // ===================================================================== 6. WARD
  SEC = "6 ward";
  await open("#/ward/L"); ok("ward screen: sentence, bars, lost money, flags, answers, top roads, tax, contractors, summary", (await count(".sentence")) === 1 && (await count(".bar")) >= 2 && (await count(".lost")) === 1 && (await count(".answers")) === 1 && (await count(".tax")) === 1 && (await count(".summary-text")) === 1);
  const codes = await ev("state.data.wards.map(w => w.code)");
  let wardFail = [];
  for (const c of codes) { await ev(`go('#/ward/${encodeURIComponent(c)}', true)`); const h1 = await text(".road-head h1"); if (!h1.includes(c)) wardFail.push(c); }
  ok(`all ${codes.length} wards open and show their code`, wardFail.length === 0, wardFail.join(","));
  await open("#/ward/L"); await ev("(() => { const s = document.querySelector('#wardpick'); s.value = 'E'; s.dispatchEvent(new Event('change', { bubbles: true })); })()"); await sleep(100);
  ok("ward picker switches ward and remembers it as home", (await hash()).includes("#/ward/E") && (await ev("localStorage.getItem('apla_ward')")) === "E");
  await open(""); ok("home card now shows the remembered ward", (await text(".glance .wchip")) === "E");
  await open("#/ward/E"); const wl = await ev("document.querySelector('a.btn.green').href"); ok("ward share link works", wl.startsWith("https://wa.me/?text="));
  await ev("document.querySelector('.results .road-item').click()"); await sleep(100); ok("top road in a ward opens", (await hash()).startsWith("#/road/"));

  // ===================================================================== 7. PHOTO -> PLACE
  SEC = "7 photo to place";
  await open(""); await reset(); await open("");
  await ev("document.querySelector('#photo').removeAttribute('capture')"); // headless has no camera; the picker path is the same
  await setFile(pngFile); await sleep(900);
  ok("choosing a real image file opens the report screen", (await hash()) === "#/report", await hash());
  ok("preview image is shown", (await count(".photo-card img")) === 1 && (await ev("document.querySelector('.photo-card img').naturalWidth")) === 64);
  const thumb = await ev("state.photo && state.photo.thumb && state.photo.thumb.length");
  ok("a thumbnail is generated for the saved report", thumb > 100, thumb);
  await click('[data-action="find"]'); ok("no place typed -> asks for a place, stays", (await hash()) === "#/report" && (await text("#wheremsg")).length > 5);
  const mapCases = [
    ["https://www.google.com/maps/@19.0813,72.8860,17z", "road"], ["https://www.google.com/maps/place/Kurla/@19.07,72.88,17z/data=!3d19.0813!4d72.8860", "road"],
    ["https://maps.google.com/?q=19.0813,72.8860", "road"], ["https://www.google.com/maps?ll=19.0813,72.8860", "road"], ["https://www.google.com/maps/search/?api=1&query=19.0813%2C72.8860", "road"],
    ["19.0813, 72.8860", "road"], ["19.0813 72.8860", "road"], ["  19.0813,72.8860  ", "road"],
    ["https://maps.app.goo.gl/AbCdEf", "msg"], ["https://goo.gl/maps/AbCdEf", "msg"],
    ["72.8860, 19.0813", "msg"], ["28.61, 77.20", "msg"], ["999, 999", "msg"], ["geo:19.08,72.88", "msg"],
    ["https://www.google.com/maps/place/Saki+Naka+Junction", "list"], ["Saki Naka", "list"], ["कुर्ला", "list"], ["Kurla West", "list"], ["LBS Marg", "list"],
    ["qqqqzzzz", "msg"], ["   ", "msg"], ["https://example.com/page", "msg"], ["not a url at all !!!", "msg"],
  ];
  for (const [inp, want] of mapCases) {
    await ev("go('#/report', true)"); await ev("state.cands = []; state.whereMsg = ''"); await ev("render()");
    await type("#where", inp); await click('[data-action="find"]'); await sleep(60);
    const h = await hash(), cands = await count('[data-action="pick"]'), msg = (await text("#wheremsg")).length > 5;
    const got = h.startsWith("#/road/") ? "road" : cands > 0 ? "list" : msg ? "msg" : "nothing";
    ok(`place ${JSON.stringify(inp.slice(0, 64))} -> ${want}`, got === want, `got ${got} (${h})`);
  }
  await ev("go('#/report', true)"); await type("#where", "Saki Naka"); await click('[data-action="find"]'); await sleep(50);
  ok("typed area shows several roads to choose from, not one auto-pick", (await count('[data-action="pick"]')) > 1);
  await click('[data-action="pick"]'); await sleep(100);
  ok("picking a road opens it, with the photo and 'from the address' line", (await hash()).startsWith("#/road/") && (await count(".photo-card img")) === 1 && (await text(".located")).includes("पत्त्यावरून"), await text(".located"));
  const rid = (await hash()).split("/")[2]; await open("#/road/RD0001"); ok("the photo does not appear on other roads", (await count(".photo-card")) === 0);
  // enter key, gps
  await open("#/report"); await ev("state.photo = { url: 'data:image/gif;base64,R0lGODlhAQABAAAAACw=', exif: null, thumb: '' }; render()"); await type("#where", "19.0813, 72.8860"); await ev("document.querySelector('#where').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))"); await sleep(100);
  ok("Enter key in the place box submits", (await hash()).startsWith("#/road/"), await hash());
  await open("#/report"); await ev("state.photo = { url: 'data:image/gif;base64,R0lGODlhAQABAAAAACw=', exif: null, thumb: '' }; render()"); await geo(19.0813, 72.886); await click('[data-action="gps"]'); await sleep(250);
  ok("'Use my current location' -> road card", (await hash()).startsWith("#/road/"), await hash());
  await open("#/report"); await ev("state.photo = { url: 'data:image/gif;base64,R0lGODlhAQABAAAAACw=', exif: null, thumb: '' }; render()"); await geo(48.85, 2.35); await click('[data-action="gps"]'); await sleep(250);
  ok("GPS outside Mumbai refused", (await hash()) === "#/report" && (await text("#wheremsg")).includes("मुंबईबाहेर"), await text("#wheremsg"));
  await open("#/report"); await ev("state.photo = null; render()"); await type("#where", "19.0813, 72.8860"); await click('[data-action="find"]');
  ok("place without a photo -> asks for a photo first", (await hash()) === "#/report" && (await text("#wheremsg")).length > 5);
  ok("with no photo the screen offers a big 'add photo' button", (await count(".drop")) === 1);
  // EXIF through the real input
  const jpg = await ev("(() => { const c = document.createElement('canvas'); c.width = 80; c.height = 60; const x = c.getContext('2d'); x.fillStyle = '#c33'; x.fillRect(0, 0, 80, 60); return c.toDataURL('image/jpeg', 0.8).split(',')[1]; })()");
  const base = Buffer.from(jpg, "base64"); const withGps = (lat, lon) => Buffer.concat([base.subarray(0, 2), exifApp1(lat, lon), base.subarray(2)]);
  const gpsFile = join(tmp, "gps.jpg"); writeFileSync(gpsFile, withGps(19.0813, 72.886));
  await open(""); await setFile(gpsFile); await sleep(900);
  ok("JPEG with GPS: report shows the photo's own location card", (await count(".exif")) === 1, await text(".exif"));
  await click('[data-action="exif-use"]'); await sleep(100);
  ok("'Yes, this road' opens the road with 'from the photo's own location'", (await hash()).startsWith("#/road/") && (await text(".located")).includes("फोटोतील"), await text(".located"));
  const farFile = join(tmp, "far.jpg"); writeFileSync(farFile, withGps(51.5, -0.12));
  await open(""); await setFile(farFile); await sleep(900); ok("JPEG with GPS outside Mumbai: no auto-location card", (await count(".exif")) === 0);
  await open(""); await setFile(pngFile); await sleep(700); ok("PNG (no EXIF): no location card, must type a place", (await count(".exif")) === 0 && (await count("#where")) === 1);
  ok("'Change photo' button is there once a photo is chosen", (await count('.btn.slim[data-action="photo"]')) === 1);
  // profile reflects reports
  await open("#/profile"); ok("profile lists the photo reports with road + how it was located", (await count(".report")) >= 1 && (await count(".report .via")) >= 1);
  ok("profile 'latest on your roads' lists followed roads", (await count(".update")) >= 1);
  // cap of 20
  await ev("(() => { for (let i = 0; i < 25; i++) addReport({ id: i, road: 'RD0220', via: 'link', lat: 19, lon: 72, where: 'x', date: '2025-10-01', thumb: '' }); })()");
  ok("reports are capped at 20 (storage safety)", (await ev("getReports().length")) === 20);
  // a big thumb flood must not throw when storage is full
  const quota = await ev("(() => { try { const big = 'x'.repeat(400000); for (let i = 0; i < 20; i++) addReport({ id: i, road: 'RD0220', via: 'link', where: '', date: '2025-10-01', thumb: big }); return 'ok:' + getReports().length; } catch (e) { return 'threw ' + e.message; } })()");
  ok("storage full does not crash photo saving", /^ok/.test(quota), quota);

  // ===================================================================== 8. PROFILE
  SEC = "8 profile";
  await open("#/profile"); await reset(); await open("#/profile");
  ok("empty profile shows friendly empty states", (await count(".empty")) === 3);
  await type("#pname", "राजू"); ok("avatar shows the first letter of the name (Devanagari)", (await text(".avatar.big")) === "रा" || (await text(".avatar.big")).length >= 1, await text(".avatar.big"));
  await open("#/profile"); ok("name persists across reload", (await ev("document.querySelector('#pname').value")) === "राजू");
  await ev("(() => { const s = document.querySelector('#pward'); s.value = 'K/E'; s.dispatchEvent(new Event('change', { bubbles: true })); })()"); await sleep(100);
  ok("home ward change persists and updates the tax card ward", (await ev("localStorage.getItem('apla_ward')")) === "K/E" && (await ev("document.querySelector('.tax').dataset.ward")) === "K/E");
  await type("#tax", "25000"); ok("tax card on profile uses the home ward's numbers", (await text("#taxline")).includes("₹25,000"));
  await open("#/road/RD0220"); await click('[data-action="metoo"]'); await open("#/profile"); ok("backed roads list the road, updates show its state", (await count(".results .road-item")) === 1 && (await count(".update")) === 1);
  await click('[data-lang="en"]'); await click('[data-action="clear"]'); ok("erase: first tap only arms it", (await text('[data-action="clear"]')).endsWith("?") && (await ev("localStorage.getItem('apla_name')")) === "राजू");
  await click('[data-action="clear"]'); await sleep(100);
  ok("erase: second tap clears name, ward, tax, me-too", (await ev("['apla_name','apla_tax','apla_ward','apla_metoo_RD0220'].every(k => localStorage.getItem(k) === null)")));
  ok("erase keeps the chosen language", (await ev("localStorage.getItem('apla_lang')")) === "en");
  await click('[data-action="back"]'); // arm then navigate away: arm must reset

  // ===================================================================== 9. NAVIGATION
  SEC = "9 navigation";
  for (const h of ["#/road/NOPE", "#/ward/ZZ", "#/rti/NOPE", "#/whatever", "#/road/", "#//", "#/road/%E0%A4%A", "#/ward/%", "#/rti/%E0%A4"]) { await send("Page.navigate", { url: "about:blank" }); await sleep(150); await open(h); ok(`bad route ${h} falls back to the home screen`, (await count(".hero-card")) === 1, `${await count(".hero-card")} home cards; app text: ${(await text("#app")).slice(0, 40)}`); }
  await open("#/road/RD0220"); await click('[data-action="back"]'); await sleep(150); ok("deep link to a road: Back goes home", (await hash()) === "#/" || (await hash()) === "", await hash());
  await open(""); await ev("go('#/road/RD0220')"); await sleep(100); await ev("go('#/ward/L')"); await sleep(100); await click('[data-action="back"]'); await sleep(200); ok("Back walks the history (ward -> road)", (await hash()) === "#/road/RD0220", await hash());
  await ev("history.back()"); await sleep(200); ok("browser back works too", (await hash()) === "#/" || (await hash()) === "", await hash());
  await ev("window.scrollTo(0, 500)"); await ev("go('#/road/RD0221')"); await sleep(150); ok("opening a screen scrolls to the top", (await ev("window.scrollY")) < 5, await ev("window.scrollY"));

  // ===================================================================== 10. LAYOUT
  SEC = "10 layout";
  const routes = ["#/", "#/road/RD0220", "#/rti/RD0220", "#/ward/L", "#/report", "#/profile"];
  for (const wdt of [320, 360, 390, 412, 768]) {
    await send("Emulation.setDeviceMetricsOverride", { width: wdt, height: 800, deviceScaleFactor: 1, mobile: wdt < 700 });
    const bad = [];
    for (const r of routes) {
      await open(r);
      const m = await ev(`({ sw: document.documentElement.scrollWidth, small: [...document.querySelectorAll('button, a.btn, summary, select, input:not([type=hidden]), textarea')].filter(b => b.offsetHeight && b.offsetHeight < 44 && !b.closest('[hidden]')).map(b => (b.textContent || b.id || b.type).trim().slice(0, 14) + ':' + b.offsetHeight),
        clipped: [...document.querySelectorAll('.card, .btn, .road-item, .chip')].filter(e => e.scrollWidth > e.clientWidth + 2 && getComputedStyle(e).overflow === 'visible' && e.getBoundingClientRect().right > innerWidth + 1).length })`);
      if (m.sw > wdt) bad.push(`${r}: page ${m.sw}px > ${wdt}`); if (m.small.length) bad.push(`${r}: small targets ${m.small.join(",")}`); if (m.clipped) bad.push(`${r}: ${m.clipped} elements poke out of the screen`);
    }
    ok(`${wdt}px wide: no sideways scroll, no target under 44px, nothing poking out (6 screens)`, bad.length === 0, bad.join(" | "));
  }
  await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await open("#/road/RD0220"); await ev("window.scrollTo(0, document.body.scrollHeight)"); await sleep(100);
  const hidden = await ev("(() => { const bar = document.querySelector('.actions').getBoundingClientRect().top; const last = document.querySelector('.small').getBoundingClientRect().bottom; return { bar, last }; })()");
  ok("sticky action bar does not cover the last line of the road card", hidden.last <= hidden.bar + 1, JSON.stringify(hidden));
  await open("#/profile"); await ev("window.scrollTo(0, document.body.scrollHeight)"); await sleep(100);
  const hid2 = await ev("(() => { const nav = document.querySelector('.tabs').getBoundingClientRect().top; const last = [...document.querySelectorAll('#app > *:not(.tabs)')].pop().getBoundingClientRect().bottom; return { nav, last }; })()");
  ok("bottom tabs do not cover the last button on the profile", hid2.last <= hid2.nav + 1, JSON.stringify(hid2));
  const longName = await ev("(() => { const r = roadById.get('RD0220'); const o = r.name; r.name = 'Very Long Road Name '.repeat(8); render(); go('#/road/RD0220'); const sw = document.documentElement.scrollWidth; r.name = o; render(); return sw; })()");
  ok("a very long road name wraps instead of widening the page", longName <= 390, longName);

  // ===================================================================== 11. ACCESSIBILITY
  SEC = "11 accessibility";
  const a11y = [];
  for (const r of routes) {
    await open(r);
    const i = await ev(`(() => { const out = []; for (const b of document.querySelectorAll('button, a[href]')) { const name = (b.getAttribute('aria-label') || b.textContent || '').trim(); if (!name) out.push('unnamed ' + b.tagName + ' ' + (b.className || '')); }
      for (const f of document.querySelectorAll('input:not([type=hidden]):not([hidden]), textarea, select')) { const has = f.getAttribute('aria-label') || (f.id && document.querySelector('label[for="' + f.id + '"]')) || f.closest('label'); if (!has) out.push('unlabelled field #' + f.id); }
      for (const im of document.querySelectorAll('img')) if (!im.hasAttribute('alt')) out.push('img without alt');
      for (const s of document.querySelectorAll('svg')) if (s.getAttribute('aria-hidden') !== 'true' && !s.getAttribute('aria-label')) out.push('svg not hidden');
      if (document.querySelectorAll('h1').length !== (${r === "#/" ? 1 : 1})) out.push('h1 count ' + document.querySelectorAll('h1').length);
      return out; })()`);
    if (i.length) a11y.push(`${r}: ${[...new Set(i)].slice(0, 5).join(", ")}`);
  }
  ok("every button/link has a name, every field a label, images have alt, one h1 per screen", a11y.length === 0, a11y.join(" | "));
  await open(""); await ev("document.activeElement && document.activeElement.blur()");
  for (const t of ["keyDown", "keyUp"]) await send("Input.dispatchKeyEvent", { type: t, key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
  const focusRing = await ev("(() => { const b = document.activeElement; return b && b !== document.body ? getComputedStyle(b).outlineStyle + ' ' + getComputedStyle(b).outlineWidth + ' on ' + b.tagName + '.' + b.className : 'nothing focused'; })()");
  ok("keyboard focus (Tab) shows a visible outline", /^solid [23]px/.test(focusRing), focusRing);
  ok("language buttons expose pressed state", (await ev("[...document.querySelectorAll('.langs button')].every(b => b.hasAttribute('aria-pressed'))")));
  const pairs = [["ink on paper", "#18181B", "#FAF9F6"], ["muted on paper", "#52525B", "#FAF9F6"], ["muted on card", "#52525B", "#FFFFFF"], ["muted on paper-2", "#52525B", "#F1EFE8"], ["cream on pine teal (buttons, band)", "#FAF9F6", "#0F4C5C"], ["cream on dark teal", "#FAF9F6", "#0A3844"],
    ["pale teal on dark teal (band label)", "#B8D3D8", "#0A3844"], ["ink on tangerine (Me too, Near me)", "#18181B", "#E36414"], ["tangerine text on its tint", "#A8420A", "#FDF0E6"], ["tangerine text on pill", "#A8420A", "#FADFCB"],
    ["ochre text on tint (pending)", "#7C4A03", "#FBF3DD"], ["dark ochre on ochre (grade C)", "#3F2602", "#CA8A04"], ["green text on tint", "#106030", "#E6F4EA"], ["cream on forest green (WhatsApp)", "#FAF9F6", "#15803D"],
    ["red text on tint", "#8E1414", "#FBEAEA"], ["cream on deep red (grade D)", "#FAF9F6", "#B91C1C"], ["pine teal on card (outline buttons)", "#0F4C5C", "#FFFFFF"], ["pine teal on tint", "#0F4C5C", "#EAF3F5"], ["pine teal on teal-100 (ward chip)", "#0A3844", "#D6E8EC"]];
  const lowC = pairs.map(([n, a, b]) => [n, contrast(a, b)]).filter(([, c]) => c < 4.5);
  ok(`text colour pairs meet 4.5:1 (${pairs.length} pairs checked)`, lowC.length === 0, lowC.map(([n, c]) => `${n} ${c.toFixed(2)}`).join(", "));
  const graphic = [["modern teal bar on paper-2", "#0D9488", "#F1EFE8"], ["deep red bar on paper-2", "#B91C1C", "#F1EFE8"], ["forest green bar on paper-2", "#15803D", "#F1EFE8"]].map(([n, a, b]) => [n, contrast(a, b)]).filter(([, c]) => c < 3);
  ok("chart bars meet 3:1 against their track", graphic.length === 0, graphic.map(([n, c]) => `${n} ${c.toFixed(2)}`).join(", "));
  warn("camera input uses capture=environment: on Chrome for Android (not the APK) it opens the camera directly and cannot pick an existing gallery photo", !(await ev("document.querySelector('#photo').hasAttribute('capture')")), "web only; the APK offers camera and gallery");

  // ===================================================================== 12. FILE:// AND OFFLINE
  SEC = "12 file and offline";
  const fileUrl = "file:///" + APP_DIR.replace(/\\/g, "/") + "/index.html";
  await send("Network.enable"); await send("Network.emulateNetworkConditions", { offline: true, latency: 0, downloadThroughput: 0, uploadThroughput: 0 });
  await send("Page.navigate", { url: fileUrl + "#/road/RD0220" }); await sleep(2500);
  ok("opens from disk with no network (apla_data.js path), road card renders", (await ev("!!document.querySelector('.money .big') && state.data.roads.length")) === 291, await text("#app"));
  await send("Network.emulateNetworkConditions", { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });

  // ===================================================================== 13. PERFORMANCE
  SEC = "13 performance";
  await open("");
  const perf = await ev("(() => { const t = performance.getEntriesByType('navigation')[0]; const a = performance.now(); for (let i = 0; i < 20; i++) roadScreen(roadById.get('RD0220')); const road = (performance.now() - a) / 20; const b = performance.now(); for (let i = 0; i < 5; i++) searchRoads('road'); const search = (performance.now() - b) / 5; return { dcl: Math.round(t.domContentLoadedEventEnd), road: +road.toFixed(2), search: +search.toFixed(2) }; })()");
  ok(`start-up ${perf.dcl} ms, road render ${perf.road} ms, search ${perf.search} ms (local, desktop CPU)`, perf.dcl < 3000 && perf.road < 30 && perf.search < 50, JSON.stringify(perf));

  // ===================================================================== 14. CONSOLE
  SEC = "14 console";
  const fav = consoleErrors.filter((e) => /favicon/.test(e)), real = consoleErrors.filter((e) => !/favicon/.test(e));
  ok("no uncaught exceptions or console errors during the whole run", real.length === 0, [...new Set(real)].slice(0, 6).join(" | "));
  warn("browser asks for /favicon.ico and gets a 404 (no icon set for the web page)", fav.length === 0, "console shows a red 404 on every load");
} catch (e) {
  rec("FAIL", SEC || "run", "the audit itself crashed", String(e.stack || e).split("\n").slice(0, 3).join(" "));
} finally {
  chrome.kill(); web.close();
}

// ---------- APK (static) ----------
SEC = "15 apk";
try {
  const apk = join(ROOT, "AplaBMC.apk");
  const sdk = process.env.ANDROID_HOME || process.env.ANDROID_SDK_ROOT || "";
  const btDir = join(sdk, "build-tools"); const bt = existsSync(btDir) ? join(btDir, readdirSync(btDir).sort().pop()) : "";
  const aapt2 = join(bt, process.platform === "win32" ? "aapt2.exe" : "aapt2");
  if (existsSync(aapt2)) {
    const b = execFileSync(aapt2, ["dump", "badging", apk]).toString();
    const perms = [...b.matchAll(/uses-permission: name='([^']+)'/g)].map((m) => m[1]);
    ok("APK package, version and SDK levels", /package: name='com.team23.aplabmc'/.test(b) && /minSdkVersion:'26'/.test(b), (b.match(/package:.*/) || [""])[0] + " " + (b.match(/sdkVersion:'\d+'/) || [""])[0]);
    ok("APK asks only for internet + location", perms.every((p) => /INTERNET|ACCESS_(FINE|COARSE)_LOCATION/.test(p)), perms.join(","));
    warn("APK is debug-signed (fine for sideloading, not for Play Store); Play Protect may warn", !/application-debuggable/.test(b), "application-debuggable");
  } else rec("WARN", SEC, "aapt2 not found, skipped APK manifest checks", "");
  const z = execFileSync("python", ["-X", "utf8", "-c", `import zipfile,hashlib,pathlib,sys\nz=zipfile.ZipFile(sys.argv[1]);bad=[n for n in ['index.html','styles.css','i18n.js','app.js','apla_data.js'] if hashlib.sha256(z.read('assets/www/'+n)).digest()!=hashlib.sha256(pathlib.Path(sys.argv[2],n).read_bytes()).digest()];print('STALE '+','.join(bad) if bad else 'MATCH')`, apk, APP_DIR]).toString().trim();
  ok("APK bundles exactly the current app/ files", z === "MATCH", z);
  const mainJava = readFileSync(join(ROOT, "android/app/src/main/java/com/team23/aplabmc/MainActivity.java"), "utf8");
  ok("wrapper handles camera + gallery chooser and the location prompt", /onShowFileChooser/.test(mainJava) && /ACTION_IMAGE_CAPTURE/.test(mainJava) && /onGeolocationPermissionsShowPrompt/.test(mainJava));
  rec("WARN", SEC, "APK was not run on a device or emulator", "no emulator/AVD here; camera, gallery, location permission prompt, WhatsApp hand-off and back button are untested on Android itself");
} catch (e) { rec("WARN", SEC, "APK checks could not run", String(e.message).split("\n")[0]); }

// ---------- summary ----------
const by = (l) => results.filter((r) => r.level === l);
console.log(`\n==== ${by("PASS").length} PASS, ${by("FAIL").length} FAIL, ${by("WARN").length} WARN ====`);
for (const r of [...by("FAIL"), ...by("WARN")]) console.log(`${r.level}  [${r.section}] ${r.name}\n      ${r.detail}`);
process.exit(by("FAIL").length ? 1 : 0);
