"use strict";
// Apla BMC — citizen app. Reads apla_data.json (or apla_data.js) and only displays it; no recalculation.

const MYBMC_URL = "https://portal.mcgm.gov.in/";
const RTI_URL = "https://rtionline.maharashtra.gov.in/";
const DEPT_URL = "https://mmrda.maharashtra.gov.in/";
const FAR_KM = 25; // farther than this from every road = not in Mumbai (home "near me" button)
const PLACE_KM = 3; // a pasted link / address / photo location must fall within this of a road we hold
const PUBLIC_URL = "https://manasshete.github.io/claude-impact-build-/app/index.html"; // used in WhatsApp links so a share opens the live app
const APK_HOST = "appassets.androidplatform.net"; // origin the Android app serves its bundled files from
const LANGS = ["mr", "hi", "en"];
const RED_FLAGS = new Set(["built_weak", "billed_twice", "overcharged"]);
const MAX_REPORTS = 20;

const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); return true; } catch { return false; } },
  del(k) { try { localStorage.removeItem(k); } catch { /* ignore */ } },
};

const state = {
  data: null, lang: LANGS.includes(store.get("apla_lang")) ? store.get("apla_lang") : "mr",
  note: null, noteRoad: null, noteVia: null, locating: false, query: "",
  tax: store.get("apla_tax") || "", name: store.get("apla_name") || "",
  rtiName: "", rtiAddr: "", meTooMem: new Set(), navDepth: 0,
  // photo-to-place flow
  photo: null, photoRoad: null, where: "", whereMsg: "", cands: [], gpsBusy: false, clearArm: false,
};
const roadById = new Map();
const wardByCode = new Map();

// ---------- formatting ----------
const T = () => translations[state.lang];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const trimZeros = (s) => (s.includes(".") ? s.replace(/0+$/, "").replace(/\.$/, "") : s);
// UI strings carry a leading emoji from the first version; icons are drawn instead
const plain = (s) => String(s ?? "").replace(/^[\p{Extended_Pictographic}️✓\s]+/u, "");

function money(n, lang = state.lang) {
  const t = translations[lang];
  n = Math.round(Math.abs(n));
  if (n >= 1e7) return `₹${(n / 1e7).toFixed(2)} ${t.crore}`;
  if (n >= 1e6) return `₹${trimZeros((n / 1e5).toFixed(1))} ${t.lakh}`;
  if (n >= 1e5) return `₹${trimZeros((n / 1e5).toFixed(2))} ${t.lakh}`;
  return "₹" + n.toLocaleString("en-IN");
}
const count = (n) => Number(n).toLocaleString("en-IN");
function monthYear(iso) {
  const [y, m] = iso.split("-").map(Number);
  return `${MONTHS[state.lang][m - 1]} ${y}`;
}
function dayMonthYear(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return `${d} ${MONTHS[state.lang][m - 1]} ${y}`;
}
function today() {
  const d = new Date();
  return [d.getDate(), d.getMonth() + 1].map((x) => String(x).padStart(2, "0")).join("-") + "-" + d.getFullYear();
}
const wardName = (code) => wardByCode.get(code)?.names?.[state.lang] || wardByCode.get(code)?.name || code;

// ---------- icons (drawn, never emoji) ----------
const ICONS = {
  pin: '<path d="M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11z"/><circle cx="12" cy="10" r="2.5"/>',
  camera: '<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c1-4.5 4-6 8-6s7 1.5 8 6"/>',
  home: '<path d="M4 11l8-7 8 7v9h-5v-6H9v6H4z"/>',
  ward: '<path d="M5 20V11M12 20V4M19 20v-6M3 20h18"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  alert: '<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17v.5"/>',
  bus: '<rect x="5" y="4" width="14" height="13" rx="2"/><path d="M5 12h14M8 20v-3M16 20v-3"/>',
  send: '<path d="M4 12l16-8-6 16-2-7z"/>',
  voice: '<circle cx="9" cy="8" r="3.5"/><path d="M3 20c.8-3.6 3-5 6-5s5.2 1.4 6 5M18 8v6M15 11h6"/>',
  doc: '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5M10 13h6M10 17h6"/>',
  back: '<path d="M15 5l-7 7 7 7"/>',
  trash: '<path d="M5 7h14M9 7V4h6v3M7 7l1 13h8l1-13"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
};
const icon = (n, cls = "") => `<svg class="ico ${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[n] || ""}</svg>`;

// ---------- data ----------
function indexData(d) {
  state.data = d;
  roadById.clear();
  wardByCode.clear();
  d.roads.forEach((r) => roadById.set(r.road_id, r));
  d.wards.forEach((w) => wardByCode.set(w.code, w));
}

function km(a, b) {
  const rad = Math.PI / 180;
  const dLat = (b[0] - a[0]) * rad, dLon = (b[1] - a[1]) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[0] * rad) * Math.cos(b[0] * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.sqrt(h));
}

function nearestRoad(lat, lon) {
  let best = null, bestKm = Infinity;
  for (const r of state.data.roads) {
    const mid = [(r.start[0] + r.end[0]) / 2, (r.start[1] + r.end[1]) / 2];
    const d = km([lat, lon], mid);
    if (d < bestKm) { best = r; bestKm = d; }
  }
  return { road: best, km: bestKm };
}

const norm = (s) => String(s ?? "").normalize("NFC").toLowerCase().replace(/[^\p{L}\p{N}\p{M}]/gu, "");
const bySpent = (a, b) => b.spent_inr - a.spent_inr;

function searchRoads(q) {
  const n = norm(q);
  if (n.length < 2) return null;
  const d = state.data, groups = [], shown = new Set();
  const named = d.roads.filter((r) => norm(r.name).includes(n)).sort(bySpent).slice(0, 20);
  if (named.length) groups.push({ title: T().roadsNamed, roads: named });
  named.forEach((r) => shown.add(r.road_id));

  const wardHits = new Map(); // ward code -> matched locality (or "")
  for (const w of d.wards) {
    const names = [w.name, w.areas, ...Object.values(w.names || {})];
    if (norm(w.code) === n || names.some((s) => norm(s).includes(n))) wardHits.set(w.code, "");
  }
  for (const l of d.localities || []) {
    if (!wardHits.has(l.ward) && [l.name, ...l.aliases].some((s) => norm(s).includes(n))) wardHits.set(l.ward, l.name);
  }
  for (const [code, loc] of wardHits) {
    const roads = d.roads.filter((r) => r.ward === code && !shown.has(r.road_id)).sort(bySpent).slice(0, 12);
    if (!roads.length) continue;
    roads.forEach((r) => shown.add(r.road_id));
    groups.push({ title: T().roadsIn(`${code} · ${wardName(code)}${loc ? ` (${loc})` : ""}`), roads });
  }
  return groups;
}

// ---------- the user's own records (this phone only) ----------
function getReports() {
  try { return JSON.parse(store.get("apla_reports") || "[]").filter((r) => roadById.has(r.road)); } catch { return []; }
}
function addReport(rep) {
  const list = [rep, ...getReports()].slice(0, MAX_REPORTS);
  if (!store.set("apla_reports", JSON.stringify(list))) { // storage full: keep the record, drop the pictures
    store.set("apla_reports", JSON.stringify(list.map((r) => ({ ...r, thumb: "" }))));
  }
}
const hasMeToo = (id) => store.get(`apla_metoo_${id}`) === "1" || state.meTooMem.has(id);
const backedRoads = () => state.data.roads.filter((r) => hasMeToo(r.road_id));
const myWard = () => (wardByCode.has(store.get("apla_ward")) ? store.get("apla_ward") : "L");

// ---------- reading a place out of what the user gives us ----------
// Photo location (JPEG EXIF GPS). Browsers often strip it from camera shots, so this is a bonus, never the only way.
function readExifGps(buf) {
  try {
    const v = new DataView(buf);
    if (v.getUint16(0) !== 0xffd8) return null;
    let off = 2;
    while (off + 4 < v.byteLength) {
      const marker = v.getUint16(off);
      off += 2;
      if ((marker & 0xff00) !== 0xff00) return null;
      const len = v.getUint16(off);
      if (marker === 0xffe1 && v.getUint32(off + 2) === 0x45786966) return parseTiffGps(v, off + 8);
      off += len;
    }
  } catch { /* unreadable: no location */ }
  return null;
}
function parseTiffGps(v, t) {
  const le = v.getUint16(t) === 0x4949;
  const u16 = (o) => v.getUint16(o, le), u32 = (o) => v.getUint32(o, le);
  const ifd0 = t + u32(t + 4);
  let gps = 0;
  for (let i = 0, n = u16(ifd0); i < n; i++) if (u16(ifd0 + 2 + i * 12) === 0x8825) gps = t + u32(ifd0 + 2 + i * 12 + 8);
  if (!gps) return null;
  const tag = {};
  for (let i = 0, n = u16(gps); i < n; i++) tag[u16(gps + 2 + i * 12)] = gps + 2 + i * 12;
  if (!(1 in tag && 2 in tag && 3 in tag && 4 in tag)) return null;
  const dms = (e) => {
    const p = t + u32(e + 8);
    const r = (k) => u32(p + k * 8) / (u32(p + k * 8 + 4) || 1);
    return r(0) + r(1) / 60 + r(2) / 3600;
  };
  const ref = (e) => String.fromCharCode(v.getUint8(e + 8));
  let lat = dms(tag[2]), lon = dms(tag[4]);
  if (ref(tag[1]) === "S") lat = -lat;
  if (ref(tag[3]) === "W") lon = -lon;
  return Number.isFinite(lat) && Number.isFinite(lon) && (lat || lon) ? { lat, lon } : null;
}

// Google Maps link, "lat, lon", or plain words
function parsePlace(raw) {
  const s = String(raw || "").trim();
  if (!s) return { empty: true };
  let m = s.match(/^(-?\d{1,2}\.\d+)\s*[, ]\s*(-?\d{1,3}\.\d+)$/);
  if (m) return { lat: +m[1], lon: +m[2] };
  if (!/^https?:\/\//i.test(s)) return { text: s };
  let u;
  try { u = new URL(s); } catch { return { text: s }; }
  if (/(^|\.)goo\.gl$/i.test(u.hostname)) return { short: true };
  let dec = s;
  try { dec = decodeURIComponent(s.replace(/\+/g, " ")); } catch { /* keep raw */ }
  m = dec.match(/!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)/) || dec.match(/@(-?\d+\.\d+),(-?\d+\.\d+)/) ||
    dec.match(/[?&](?:q|ll|query|destination|daddr|center)=(-?\d+\.\d+)[, ](-?\d+\.\d+)/);
  if (m) return { lat: +m[1], lon: +m[2] };
  const q = u.searchParams.get("q") || u.searchParams.get("query");
  const place = u.pathname.match(/\/place\/([^/]+)/);
  let text = q;
  if (!text && place) { try { text = decodeURIComponent(place[1].replace(/\+/g, " ")); } catch { text = place[1]; } }
  return text ? { text } : { text: "" };
}

function roadsForText(text) {
  const groups = searchRoads(text) || [];
  const seen = new Set(), out = [];
  for (const g of groups) for (const r of g.roads) if (!seen.has(r.road_id)) { seen.add(r.road_id); out.push(r); }
  return out.slice(0, 6);
}

function makeThumb(file) {
  return createImageBitmap(file).then((bmp) => {
    const w = 160, h = Math.max(1, Math.round((bmp.height / bmp.width) * w));
    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    c.getContext("2d").drawImage(bmp, 0, 0, w, h);
    return c.toDataURL("image/jpeg", 0.6);
  });
}

// ---------- navigation ----------
function route() {
  const [page = "", ...rest] = location.hash.replace(/^#\/?/, "").split("/");
  return { page, id: decodeURIComponent(rest.join("/")) };
}
function go(hash, replace = false) {
  if (replace) { history.replaceState(null, "", hash); render(); window.scrollTo(0, 0); return; }
  if (location.hash === hash) { render(); return; }
  state.navDepth += 1;
  location.hash = hash;
}
function back() {
  if (state.navDepth > 0) { state.navDepth -= 1; history.back(); return; }
  const { page, id } = route();
  location.hash = page === "rti" ? `#/road/${encodeURIComponent(id)}` : "#/";
}
function shareLink(hash) {
  if (PUBLIC_URL) return PUBLIC_URL + hash;
  if (location.hostname === APK_HOST) return "आपला BMC app"; // in-app URL means nothing to the receiver
  return location.href.split("#")[0] + hash;
}
const waHref = (text) => "https://wa.me/?text=" + encodeURIComponent(text);

// ---------- location ----------
function openRoad(id, note, via = null) {
  state.note = note;
  state.noteRoad = id;
  state.noteVia = via;
  go(`#/road/${id}`);
}
// Location allowed only after we already fell back to the sample road: show the real road if the sample is still on screen.
function swapIfOnSample(pos) {
  const demo = state.data.meta?.demo_road || "RD0220";
  const { road, km: dist } = nearestRoad(pos.coords.latitude, pos.coords.longitude);
  const { page, id } = route();
  if (dist > FAR_KM || page !== "road" || id !== demo || state.note !== "locOff") return;
  store.set("apla_ward", road.ward);
  state.note = null;
  state.noteRoad = road.road_id;
  go(`#/road/${road.road_id}`, true);
}
// Called by the Android app when the user grants location; Android WebView times the first request out while its prompt is open.
window.aplaRetryLocation = () => {
  if (navigator.geolocation && state.note === "locOff") {
    navigator.geolocation.getCurrentPosition(swapIfOnSample, () => {}, { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 });
  }
};

function locate() {
  if (state.locating) return;
  let settled = false;
  const finish = (roadId, note) => {
    if (settled) return;
    settled = true;
    clearTimeout(watchdog);
    state.locating = false;
    openRoad(roadId, note);
  };
  const demo = state.data.meta?.demo_road || "RD0220";
  // geolocation's own timeout only starts after the permission prompt is answered; an ignored prompt never calls back
  const watchdog = setTimeout(() => finish(demo, "locOff"), 12000);
  if (!navigator.geolocation) return finish(demo, "locOff");
  state.locating = true;
  render();
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      if (settled) return swapIfOnSample(pos); // browser answered after the watchdog fired
      const { road, km: dist } = nearestRoad(pos.coords.latitude, pos.coords.longitude);
      if (dist > FAR_KM) return finish(demo, "locFar");
      store.set("apla_ward", road.ward);
      finish(road.road_id, null);
    },
    () => finish(demo, "locOff"),
    { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 },
  );
}

// ---------- photo → place ----------
// A photo alone answers nothing. It only gets a road once we hold a place: the photo's own location,
// the phone's GPS, a Google Maps link or a typed address. No place, no road, never the sample road.
function placeFound(road, via, lat = null, lon = null) {
  addReport({
    id: Date.now(), road: road.road_id, via, lat, lon, where: state.where.trim().slice(0, 200),
    date: new Date().toISOString().slice(0, 10), thumb: state.photo?.thumb || "",
  });
  state.photoRoad = road.road_id;
  state.whereMsg = "";
  state.cands = [];
  state.where = "";
  store.set("apla_ward", road.ward);
  openRoad(road.road_id, null, via);
}

function placeFromCoords(lat, lon, via) {
  const { road, km: dist } = nearestRoad(lat, lon);
  if (dist > PLACE_KM) { state.whereMsg = T().farAway; state.cands = []; return render(); }
  placeFound(road, via, lat, lon);
}

function findPlace() {
  const t = T();
  if (!state.photo) { state.whereMsg = t.needPhoto; return render(); }
  const p = parsePlace(state.where);
  state.cands = [];
  if (p.empty) state.whereMsg = t.wherePh;
  else if (p.short) state.whereMsg = t.shortLink;
  else if (p.lat !== undefined) return placeFromCoords(p.lat, p.lon, "link");
  else {
    const roads = p.text ? roadsForText(p.text) : [];
    if (!roads.length) state.whereMsg = t.noMatch;
    else { state.whereMsg = ""; state.cands = roads; }
  }
  render();
}

function useGps() {
  if (state.gpsBusy) return;
  const t = T();
  if (!state.photo) { state.whereMsg = t.needPhoto; return render(); }
  if (!navigator.geolocation) { state.whereMsg = t.gpsFail; return render(); }
  state.gpsBusy = true;
  state.whereMsg = "";
  render();
  navigator.geolocation.getCurrentPosition(
    (pos) => { state.gpsBusy = false; placeFromCoords(pos.coords.latitude, pos.coords.longitude, "gps"); },
    () => { state.gpsBusy = false; state.whereMsg = T().gpsFail; render(); },
    { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 },
  );
}

// ---------- shared pieces ----------
function langButtons(big = false) {
  return `<div class="langs${big ? " big" : ""}" role="group" aria-label="Language">` +
    LANGS.map((l) => `<button type="button" data-lang="${l}" aria-pressed="${l === state.lang}">${translations[l].langName}</button>`).join("") +
    `</div>`;
}
const topBar = () => `<div class="top"><button type="button" class="back" data-action="back">${icon("back")}<span>${esc(plain(T().back).replace(/^←\s*/, ""))}</span></button>${langButtons()}</div>`;
const asOf = () => `<p class="small">${esc(T().asOf(dayMonthYear(state.data.meta?.as_of || "2025-10-10")))}</p>`;
const wardChip = (code) => `<span class="wchip">${esc(code)}</span><span class="wname">${esc(wardName(code))}</span>`;

function navBar(page) {
  const t = T();
  const items = [
    ["", "home", t.navHome, "#/"],
    ["ward", "ward", t.navWard, `#/ward/${encodeURIComponent(myWard())}`],
    ["report", "camera", t.navReport, "#/report"],
    ["profile", "user", t.navProfile, "#/profile"],
  ];
  return `<nav class="tabs" aria-label="Main">` + items.map(([p, ic, label, href]) =>
    `<button type="button" class="tab" data-go="${href}"${p === page ? ' aria-current="page"' : ""}>${icon(ic)}<span>${esc(label)}</span></button>`).join("") + `</nav>`;
}

function roadItem(r) {
  const t = T(), nf = (r.flags || []).length;
  const part = r.segments_in_ward > 1 ? `<span class="part">${esc(t.part(r.segment))}</span>` : "";
  return `<button type="button" class="road-item" data-go="#/road/${esc(r.road_id)}">` +
    `<b>${esc(r.name)}</b><span class="meta">${wardChip(r.ward)}${part}</span>` +
    `<span class="amt num">${r.jobs ? money(r.spent_inr) : "—"}${nf ? `<small class="flagcount">${icon("alert")}${nf}</small>` : ""}</span></button>`;
}

function costBars(fair, spent, guaranteeLine) {
  const t = T(), max = Math.max(fair, spent) || 1, diff = spent - fair;
  const pct = (v) => Math.max(2, Math.round((v / max) * 100));
  return `<h2>${esc(t.costTitle)}</h2>
    <div class="bar-row"><div class="lbl"><span>${esc(t.shouldCost)} <small>(${esc(t.shouldCostNote)})</small></span><span class="val num">${money(fair)}</span></div>
      <div class="bar"><span class="fair" style="width:${pct(fair)}%"></span></div></div>
    <div class="bar-row"><div class="lbl"><span>${esc(t.billed)}</span><span class="val num">${money(spent)}</span></div>
      <div class="bar"><span class="billed${diff <= 0 ? " ok" : ""}" style="width:${pct(spent)}%"></span></div></div>
    <p class="diff ${diff > 0 ? "up" : "down"}">${esc(diff > 0 ? t.extra : t.less)}: <span class="num">${money(diff)}</span></p>
    ${guaranteeLine ? `<p class="good-line">${icon("check")}<span>${esc(t.guaranteeZero)}</span></p>` : ""}`;
}

// ---------- the tax card: your tax beside what your ward did with its road money ----------
function taxLine(code) {
  const n = parseInt(String(state.tax).replace(/\D/g, ""), 10);
  const w = wardByCode.get(code);
  if (!(n > 0) || !w) return "";
  const t = T(), extra = Math.max(0, w.spent_inr - w.fair_cost_inr);
  return `<div class="taxgrid">
      <div class="you"><span class="k">${esc(t.taxYouPaid)}</span><span class="big num">${money(n)}</span></div>
      <div class="ward"><span class="k">${esc(t.taxWardSpent(w.code))}</span><span class="big num">${money(w.spent_inr)}</span></div>
    </div>
    <ul class="taxrows">
      <li><span>${esc(t.taxWardExtra)}</span><b class="num bad">${money(extra)}</b></li>
      <li><span>${esc(t.taxWardLost)}</span><b class="num bad">${money(w.lost_failed_inr)}</b></li>
    </ul>`;
}
const taxHasValue = () => parseInt(String(state.tax).replace(/\D/g, ""), 10) > 0;
const taxBlock = (code) => `<section class="card tax" data-ward="${esc(code)}"><h2>${esc(T().taxTitle)}</h2>
  <label for="tax">${esc(T().taxLabel)}</label>
  <input id="tax" type="text" inputmode="numeric" autocomplete="off" placeholder="₹" value="${esc(state.tax)}">
  <p class="hint" id="taxhint">${esc(taxHasValue() ? T().taxHint : T().taxEmpty)}</p>
  <div class="taxline" id="taxline">${taxLine(code)}</div></section>`;

// ---------- screens ----------
function updatesFor(roads) {
  const t = T();
  return roads.map((r) => {
    const lines = [r.open_complaints ? t.updOpen(r.open_complaints, r.max_days_open) : t.updClear];
    if (r.broke_again) lines.push(t.updBroke(r.broke_again));
    return `<button type="button" class="update" data-go="#/road/${esc(r.road_id)}"><b>${esc(r.name)}</b>` +
      `<span class="meta">${wardChip(r.ward)}</span>` +
      lines.map((l, i) => `<span class="uline${i === 0 && r.open_complaints ? " open" : ""}">${esc(l)}</span>`).join("") + `</button>`;
  }).join("");
}
function followedRoads() {
  const ids = new Set(getReports().map((r) => r.road));
  backedRoads().forEach((r) => ids.add(r.road_id));
  return [...ids].map((id) => roadById.get(id)).filter(Boolean);
}

function homeScreen() {
  const t = T(), w = wardByCode.get(myWard());
  const diff = w.spent_inr - w.budget_inr;
  const top = (w.top_roads || []).map((id) => roadById.get(id)).filter(Boolean).slice(0, 3).map(roadItem).join("");
  const followed = followedRoads().slice(0, 3);
  const initial = state.name.trim() ? esc([...state.name.trim()][0].toUpperCase()) : icon("user");
  return `<header class="home-head">
      <div><h1 class="brand">आपला BMC</h1><p class="tagline">${esc(t.tagline)}</p></div>
      <button type="button" class="avatar" data-go="#/profile" aria-label="${esc(t.profileTitle)}">${initial}</button>
    </header>
    ${langButtons(true)}
    <section class="hero-card">
      <button type="button" class="btn cta xl" data-action="locate"${state.locating ? " disabled" : ""}>${icon("pin")}<span>${esc(plain(state.locating ? t.locating : t.checkNear))}</span></button>
      <button type="button" class="btn ghost" data-action="photo">${icon("camera")}<span>${esc(plain(t.photoBtn))}</span></button>
    </section>
    <input class="search" id="q" type="search" autocomplete="off" placeholder="${esc(t.searchPh)}" aria-label="${esc(t.searchPh)}" value="${esc(state.query)}">
    <div class="results" id="results">${resultsHtml()}</div>
    <section class="card glance">
      <div class="glance-head"><h2>${esc(t.myWard)}</h2><span class="wtag">${wardChip(w.code)}</span></div>
      <p class="sentence">${esc(t.wardSentence(w.code, money(w.budget_inr), money(w.spent_inr), money(diff), diff >= 0))}</p>
      <button type="button" class="btn outline" data-go="#/ward/${encodeURIComponent(w.code)}"><span>${esc(t.wardLink(w.code).replace(/\s*→\s*$/, ""))}</span>${icon("arrow")}</button>
    </section>
    ${top ? `<h2 class="sect">${esc(t.topRoads)}</h2><div class="results">${top}</div>` : ""}
    ${followed.length ? `<h2 class="sect">${esc(t.updatesTitle)}</h2><div class="updates">${updatesFor(followed)}</div>` : ""}
    <p class="footer">${esc(t.footer)}</p>`;
}

function resultsHtml() {
  const groups = searchRoads(state.query);
  if (!groups) return "";
  if (!groups.length) return `<p class="small">${esc(T().noResults)}</p>`;
  return groups.map((g) => `<h3>${esc(g.title)}</h3>` + g.roads.map(roadItem).join("")).join("");
}

function roadScreen(r) {
  const t = T();
  const note = state.note && state.noteRoad === r.road_id ? `<p class="note">${icon("pin")}<span>${esc(t[state.note])}</span></p>` : "";
  const via = state.noteVia && state.noteRoad === r.road_id ? `<p class="located">${icon("pin")}<span>${esc(t.locatedVia(t.via[state.noteVia]))}</span></p>` : "";
  const part = r.segments_in_ward > 1 ? `<span class="part">${esc(t.part(r.segment))}</span>` : "";
  const head = `<section class="road-head"><h1>${esc(r.name)}</h1>
    <p class="sub">${wardChip(r.ward)}${part}</p>
    ${r.bus_route ? `<span class="pill">${icon("bus")}${esc(t.busRoute)}</span>` : ""}</section>`;
  const photo = state.photo && state.photoRoad === r.road_id
    ? `<figure class="photo-card"><img class="photo" src="${esc(state.photo.url)}" alt=""><figcaption class="photo-cap">${icon("pin")}<span>${esc(t.yourPhoto)}</span></figcaption></figure>` : "";

  let body;
  if (r.owner && r.owner !== "BMC") body = notBmcBox(r);
  else if (!r.jobs) body = noRepairBox(r);
  else body = moneyBlock(r) + statsBlock(r) + `<section class="card">${costBars(r.fair_cost_inr, r.spent_inr, r.under_guarantee)}</section>` +
    guaranteeBlock(r) + flagsBlock(r) + contractorsBlock(r) + trailBlock(r);
  const tax = r.owner && r.owner !== "BMC" ? "" : taxBlock(r.ward);
  const wardBtn = `<button type="button" class="btn outline" data-go="#/ward/${encodeURIComponent(r.ward)}"><span>${esc(t.wardLink(r.ward).replace(/\s*→\s*$/, ""))}</span>${icon("arrow")}</button>`;
  return topBar() + note + via + head + photo + body + tax + wardBtn + asOf() + actionsBar(r);
}

const moneyBlock = (r) => `<section class="card money"><p class="label">${esc(T().moneyLabel)}</p><p class="big">${money(r.spent_inr)}</p></section>`;

function statsBlock(r) {
  const t = T(), [pre, big, post] = t.outOf(r.on_time, r.jobs);
  const late = r.on_time * 2 < r.jobs;
  return `<div class="stats">
    <div class="stat"><div class="k">${esc(t.repairs)}</div><div class="big">${r.jobs}</div></div>
    <div class="stat${r.broke_again ? " red" : ""}"><div class="k">${esc(t.brokeAgain)}</div><div class="big">${r.broke_again}</div></div>
    <div class="stat${late ? " amber" : ""}"><div class="k">${esc(t.onTime)}</div>${pre ? `<div class="of">${esc(pre)}</div>` : ""}<div class="big">${big}</div>${post ? `<div class="of">${esc(post)}</div>` : ""}</div>
  </div>`;
}

function guaranteeBlock(r) {
  if (!r.under_guarantee) return "";
  const t = T();
  return `<section class="card guarantee"><h2>${icon("check")}<span>${esc(t.underGuarantee)}</span></h2><p>${esc(t.guaranteeBody(monthYear(r.last_resurfaced)))}</p></section>`;
}

function flagsBlock(r) {
  const t = T(), flags = r.flags || [];
  if (!flags.length) return `<section class="card"><h2>${esc(t.flagsTitle)}</h2><p class="all-clear">${icon("check")}<span>${esc(t.noFlags)}</span></p></section>`;
  const items = flags.map((f) => {
    const red = RED_FLAGS.has(f.type);
    return `<details class="flag ${red ? "red" : "amber"}"><summary>${icon("alert")}<span class="fname">${esc(t.flagNames[f.type] || f.type)}</span>` +
      `${f.count ? `<span class="count">${f.count}</span>` : ""}</summary><p>${esc(f.text?.[state.lang] || f.text?.en || "")}</p></details>`;
  }).join("");
  return `<section class="card"><h2>${esc(t.flagsTitle)}</h2><p class="hint">${esc(t.tapToSee)}</p>${items}</section>`;
}

function contractorsBlock(r) {
  const t = T(), list = r.contractors || [];
  if (!list.length) return "";
  const row = (c) => `<div class="contractor"><span class="grade ${esc(c.grade)}">${esc(c.grade)}</span><span class="name">${esc(c.name)}</span><span class="jobs">${esc(t.jobsN(c.jobs))}</span></div>`;
  const rest = list.slice(3);
  return `<section class="card"><h2>${esc(t.contractorsTitle)}</h2>${list.slice(0, 3).map(row).join("")}` +
    (rest.length ? `<details class="more"><summary>${esc(t.moreN(rest.length))}</summary>${rest.map(row).join("")}</details>` : "") +
    `<p class="hint tight">${esc(t.gradeHelp)}</p></section>`;
}

function trailBlock(r) {
  const t = T(), trail = r.trail || [];
  if (!trail.length) return `<section class="card"><h2>${esc(t.trailTitle)}</h2><p>${esc(t.noTrail)}</p></section>`;
  const rows = trail.map((x) => {
    const job = x.job ? `<span class="chip chip-id">${esc(t.job)} ${esc(x.job)}</span>` : `<span class="chip none">${esc(t.noJob)}</span>`;
    const status = x.status === "Done" ? `<span class="chip done">${icon("check")}${esc(t.done)}</span>` : `<span class="chip open">${esc(t.stillOpen(x.days_open))}</span>`;
    return `<div class="trail"><span class="chip chip-id">${esc(t.complaint)} ${esc(x.complaint)}</span>${icon("arrow", "step")}${job}${icon("arrow", "step")}${status}</div>`;
  }).join("");
  return `<section class="card"><h2>${esc(t.trailTitle)}</h2>${rows}</section>`;
}

function notBmcBox(r) {
  const t = T();
  return `<div class="state-box amber">${icon("alert")}<div>${esc(t.notBmc)}${r.not_bmc_complaints ? `<p class="sub">${esc(t.notBmcCount(r.not_bmc_complaints))}</p>` : ""}</div></div>`;
}
function noRepairBox(r) {
  const t = T();
  if (!r.open_complaints) return `<div class="state-box amber">${esc(t.noRepairAtAll)}</div>`;
  return `<div class="state-box red">${icon("clock")}<div>${esc(t.noRepairYet(r.max_days_open))}</div></div>` + trailBlock(r);
}

function roadWaText(r) {
  const t = T();
  const p = { road: r.name, ward: wardName(r.ward), spent: money(r.spent_inr), jobs: r.jobs, broke: r.broke_again,
    guarantee: r.under_guarantee, days: r.max_days_open, link: shareLink(`#/road/${r.road_id}`) };
  if (r.owner && r.owner !== "BMC") return t.waNotBmc(p);
  if (!r.jobs && r.open_complaints) return t.waNoRepair(p);
  return t.waRoad(p);
}

function actionsBar(r) {
  const t = T();
  const wa = `<a class="btn green" href="${esc(waHref(roadWaText(r)))}" target="_blank" rel="noopener">${icon("send")}<span>${esc(plain(t.whatsapp))}</span></a>`;
  let buttons;
  if (r.owner && r.owner !== "BMC") {
    buttons = [wa, `<a class="btn outline" href="${DEPT_URL}" target="_blank" rel="noopener"><span>${esc(t.deptLink)}</span></a>`];
  } else {
    const mine = hasMeToo(r.road_id);
    const me = `<button type="button" class="btn cta${mine ? " done" : ""}" data-action="metoo" data-id="${esc(r.road_id)}" aria-pressed="${mine}">` +
      `${mine ? "" : icon("voice")}<span>${esc(mine ? t.meTooDone : plain(t.meToo))} <span class="num">${count((r.me_too || 0) + (mine ? 1 : 0))}</span></span></button>`;
    const rti = r.rti_text ? `<button type="button" class="btn teal" data-go="#/rti/${esc(r.road_id)}">${icon("doc")}<span>${esc(plain(t.askBmc))}</span></button>` : "";
    const my = `<a class="btn outline" href="${MYBMC_URL}" target="_blank" rel="noopener"><span>${esc(plain(t.myBmc))}</span></a>`;
    buttons = [me, wa, rti, my].filter(Boolean);
  }
  return `<nav class="actions" aria-label="Actions"><div class="grid">${buttons.join("")}</div></nav>`;
}

function rtiFilled(r) {
  const blank = "__________";
  return (r.rti_text?.[state.lang] || r.rti_text?.en || "")
    .replaceAll("{NAME}", state.rtiName.trim() || blank)
    .replaceAll("{ADDRESS}", state.rtiAddr.trim().replace(/\s*\n\s*/g, ", ") || blank)
    .replaceAll("{DATE}", today());
}

function rtiScreen(r) {
  const t = T();
  if (!r.rti_text) return topBar() + `<p class="state-box amber">${esc(t.noRti)}</p>`;
  const text = rtiFilled(r);
  return topBar() + `<section class="road-head"><h1>${esc(t.rtiTitle)}</h1>
      <p class="sub"><span class="wname">${esc(r.name)}</span>${wardChip(r.ward)}</p></section>
    <p class="lead">${esc(t.rtiIntro)}</p>
    <div class="form">
      <label for="rname">${esc(t.yourName)}</label><input id="rname" autocomplete="off" value="${esc(state.rtiName)}">
      <label for="raddr">${esc(t.yourAddress)}</label><textarea id="raddr" autocomplete="off">${esc(state.rtiAddr)}</textarea>
    </div>
    <p class="hint lockline">${icon("lock")}<span>${esc(plain(t.notSaved))}</span></p>
    <div class="rti-text" id="rtitext">${esc(text)}</div>
    <div class="stack">
      <button type="button" class="btn teal" data-action="copy" data-id="${esc(r.road_id)}">${esc(t.copy)}</button>
      <a class="btn green" id="rtiwa" href="${esc(waHref(text))}" target="_blank" rel="noopener">${icon("send")}<span>${esc(plain(t.whatsapp))}</span></a>
      <a class="btn outline" href="${RTI_URL}" target="_blank" rel="noopener"><span>${esc(t.rtiHow)}</span></a>
    </div>
    <p class="small">${esc(t.rtiFee)}</p>`;
}

function wardScreen(w) {
  const t = T(), lang = state.lang, diff = w.spent_inr - w.budget_inr;
  const options = state.data.wards.map((x) => `<option value="${esc(x.code)}"${x.code === w.code ? " selected" : ""}>${esc(x.code)} ${esc(wardName(x.code))}</option>`).join("");
  const chips = Object.entries(w.flag_counts || {}).filter(([, n]) => n > 0)
    .map(([k, n]) => `<span class="chip ${RED_FLAGS.has(k) ? "red" : "amber"}">${esc(t.flagNames[k] || k)} <span class="num">${n}</span></span>`).join("");
  const top = (w.top_roads || []).map((id) => roadById.get(id)).filter(Boolean).map(roadItem).join("");
  const grades = (w.contractors || []).map((c) => `<span class="chip"><span class="grade ${esc(c.grade)}">${esc(c.grade)}</span>${esc(c.name)}</span>`).join("");
  const summary = w.summary?.[lang] || w.summary?.en || "";
  const waText = t.waWard(w.code, summary, shareLink(`#/ward/${encodeURIComponent(w.code)}`));
  return topBar() + `<section class="road-head"><h1>${esc(t.wardTitle)} ${esc(w.code)} <span class="wname">${esc(wardName(w.code))}</span></h1>
      <p class="sub"><label>${esc(t.changeWard)} <select class="ward-pick" id="wardpick">${options}</select></label></p></section>
    <section class="card"><p class="sentence">${esc(t.wardSentence(w.code, money(w.budget_inr), money(w.spent_inr), money(diff), diff >= 0))}</p></section>
    <section class="card">${costBars(w.fair_cost_inr, w.spent_inr, false)}</section>
    <section class="card lost"><h2>${esc(t.lostFailed)}</h2><p class="big num">${money(w.lost_failed_inr)}</p><p class="hint tight">${esc(t.lostFailedNote)}</p></section>
    <section class="card"><h2>${esc(t.wardFlags)}</h2><div class="counts">${chips || `<p class="all-clear">${icon("check")}<span>${esc(t.noFlags)}</span></p>`}</div></section>
    <section class="card"><h2>${esc(t.bmcAnswers)}</h2><div class="answers">
      <div>${esc(t.answered)}<span class="big num">${w.bmc_answered ?? 0}</span></div>
      <div>${esc(t.pending)}<span class="big num">${w.bmc_pending ?? 0}</span></div></div></section>
    <section class="card"><h2>${esc(t.topRoads)}</h2><div class="results">${top}</div></section>
    ${taxBlock(w.code)}
    <section class="card"><h2>${esc(t.wardContractors)}</h2><div class="grades">${grades}</div><p class="hint tight">${esc(t.gradeHelp)}</p></section>
    <section class="card"><h2>${esc(t.summaryTitle)}</h2><p class="summary-text">${esc(summary)}</p></section>
    <a class="btn green" href="${esc(waHref(waText))}" target="_blank" rel="noopener">${icon("send")}<span>${esc(plain(t.shareWard))}</span></a>
    ${asOf()}`;
}

function reportScreen() {
  const t = T(), p = state.photo;
  const exif = p?.exif && nearestRoad(p.exif.lat, p.exif.lon);
  const exifOk = exif && exif.km <= PLACE_KM;
  const preview = p
    ? `<figure class="photo-card"><img class="photo" src="${esc(p.url)}" alt=""></figure>
       <button type="button" class="btn outline slim" data-action="photo">${icon("camera")}<span>${esc(t.changePhoto)}</span></button>`
    : `<button type="button" class="drop" data-action="photo">${icon("camera")}<span>${esc(plain(t.photoBtn))}</span></button>`;
  const exifCard = exifOk
    ? `<section class="card exif"><p>${icon("pin")}<span>${esc(t.exifFound(exif.road.name))}</span></p>
        <button type="button" class="btn teal" data-action="exif-use">${icon("check")}<span>${esc(t.exifUse)}</span></button></section>` : "";
  const cands = state.cands.length
    ? `<h2 class="sect">${esc(t.pickRoad)}</h2><div class="results">${state.cands.map((r) =>
      `<button type="button" class="road-item" data-action="pick" data-id="${esc(r.road_id)}"><b>${esc(r.name)}</b><span class="meta">${wardChip(r.ward)}${r.segments_in_ward > 1 ? `<span class="part">${esc(t.part(r.segment))}</span>` : ""}</span><span class="amt">${icon("arrow")}</span></button>`).join("")}</div>` : "";
  return topBar() + `<section class="road-head"><h1>${esc(t.reportTitle)}</h1></section>
    <p class="lead">${esc(t.reportIntro)}</p>
    ${preview}
    ${exifCard}
    <section class="card where">
      <label for="where">${esc(t.whereLabel)}</label>
      <input id="where" type="text" autocomplete="off" inputmode="url" placeholder="${esc(t.wherePh)}" value="${esc(state.where)}">
      <p class="hint">${esc(t.whereHelp)}</p>
      <p class="warn" id="wheremsg" role="alert">${esc(state.whereMsg)}</p>
      <div class="stack">
        <button type="button" class="btn teal" data-action="find">${icon("link")}<span>${esc(t.findRoad)}</span></button>
        <button type="button" class="btn outline" data-action="gps"${state.gpsBusy ? " disabled" : ""}>${icon("pin")}<span>${esc(state.gpsBusy ? t.gpsBusy : t.useGps)}</span></button>
      </div>
    </section>
    ${cands}
    <p class="small">${icon("lock", "inline")}${esc(t.savedOnPhone)}</p>`;
}

function profileScreen() {
  const t = T(), code = myWard();
  const reports = getReports();
  const backed = backedRoads();
  const options = state.data.wards.map((x) => `<option value="${esc(x.code)}"${x.code === code ? " selected" : ""}>${esc(x.code)} ${esc(wardName(x.code))}</option>`).join("");
  const initial = state.name.trim() ? esc([...state.name.trim()][0].toUpperCase()) : icon("user");
  const followed = followedRoads();
  const rep = reports.map((r) => {
    const road = roadById.get(r.road);
    return `<button type="button" class="report" data-go="#/road/${esc(r.road)}">
      ${r.thumb ? `<img src="${esc(r.thumb)}" alt="">` : `<span class="noimg">${icon("camera")}</span>`}
      <span class="rtxt"><b>${esc(road.name)}</b><span class="meta">${wardChip(road.ward)}</span>
      <span class="via">${icon("pin")}${esc(t.via[r.via] || "")}</span>
      <span class="when num">${esc(dayMonthYear(r.date))}</span></span></button>`;
  }).join("");
  return topBar() + `<header class="profile-head"><span class="avatar big">${initial}</span>
      <div class="pform"><label for="pname">${esc(t.yourNameOpt)}</label>
      <input id="pname" autocomplete="off" value="${esc(state.name)}"></div></header>
    <section class="card"><h2>${esc(t.homeWard)}</h2>
      <select class="ward-pick wide" id="pward" aria-label="${esc(t.homeWard)}">${options}</select>
      <h2 class="spaced">${esc(t.langTitle)}</h2>${langButtons(true)}</section>
    ${taxBlock(code)}
    <h2 class="sect">${esc(t.updatesTitle)}</h2>
    ${followed.length ? `<div class="updates">${updatesFor(followed)}</div>` : `<p class="empty">${esc(t.noUpdates)}</p>`}
    <h2 class="sect">${esc(t.myReports)}</h2>
    ${reports.length ? `<div class="reports">${rep}</div>` : `<p class="empty">${esc(t.noReports)}</p>`}
    <h2 class="sect">${esc(t.myMeToo)}</h2>
    ${backed.length ? `<div class="results">${backed.map(roadItem).join("")}</div>` : `<p class="empty">${esc(t.noMeToo)}</p>`}
    <p class="small">${icon("lock", "inline")}${esc(t.savedOnPhone)}</p>
    <button type="button" class="btn danger" data-action="clear">${icon("trash")}<span>${esc(state.clearArm ? t.clearData + "?" : t.clearData)}</span></button>`;
}

// ---------- render + events ----------
const NAV_PAGES = new Set(["", "ward", "report", "profile"]);
function render() {
  const app = document.getElementById("app");
  const { page, id } = route();
  document.documentElement.lang = state.lang;
  if (page !== "road") { state.note = null; state.noteVia = null; }
  let html, shown = page;
  if (page === "road" && roadById.has(id)) html = roadScreen(roadById.get(id));
  else if (page === "rti" && roadById.has(id)) html = rtiScreen(roadById.get(id));
  else if (page === "ward" && wardByCode.has(id)) html = wardScreen(wardByCode.get(id));
  else if (page === "report") html = reportScreen();
  else if (page === "profile") html = profileScreen();
  else { html = homeScreen(); shown = ""; }
  const withNav = NAV_PAGES.has(shown);
  app.innerHTML = html + (withNav ? navBar(shown) : "");
  app.classList.toggle("with-actions", page === "road" && roadById.has(id));
  app.classList.toggle("with-nav", withNav);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  }
}

function eraseMyData() {
  const keys = [];
  try { for (let i = 0; i < localStorage.length; i++) { const k = localStorage.key(i); if (k && k.startsWith("apla_") && k !== "apla_lang") keys.push(k); } } catch { /* ignore */ }
  keys.forEach((k) => store.del(k));
  state.meTooMem.clear();
  state.tax = ""; state.name = ""; state.photo = null; state.photoRoad = null;
}

function wire() {
  const app = document.getElementById("app");
  const photo = document.getElementById("photo");
  app.addEventListener("click", async (e) => {
    const el = e.target.closest("[data-lang],[data-go],[data-action]");
    if (!el) return;
    if (el.dataset.lang) {
      state.lang = el.dataset.lang;
      store.set("apla_lang", state.lang);
      render();
      return;
    }
    if (el.dataset.go) return go(el.dataset.go);
    const action = el.dataset.action, id = el.dataset.id;
    if (action !== "clear") state.clearArm = false;
    if (action === "back") back();
    else if (action === "locate") locate();
    else if (action === "photo") photo.click();
    else if (action === "find") findPlace();
    else if (action === "gps") useGps();
    else if (action === "exif-use" && state.photo?.exif) placeFromCoords(state.photo.exif.lat, state.photo.exif.lon, "exif");
    else if (action === "pick" && roadById.has(id)) placeFound(roadById.get(id), "address");
    else if (action === "clear") {
      if (!state.clearArm) { state.clearArm = true; render(); return; }
      eraseMyData(); state.clearArm = false; render();
    } else if (action === "metoo" && !hasMeToo(id)) {
      state.meTooMem.add(id);
      store.set(`apla_metoo_${id}`, "1");
      render();
    } else if (action === "copy") {
      if (await copyText(rtiFilled(roadById.get(id)))) {
        el.textContent = T().copied;
        setTimeout(() => { el.textContent = T().copy; }, 2000);
      } else { // clipboard blocked: select the text so a long-press copy works
        const range = document.createRange();
        range.selectNodeContents(document.getElementById("rtitext"));
        getSelection().removeAllRanges();
        getSelection().addRange(range);
      }
    }
  });
  app.addEventListener("input", (e) => {
    const el = e.target;
    if (el.id === "q") {
      state.query = el.value;
      document.getElementById("results").innerHTML = resultsHtml();
    } else if (el.id === "tax") {
      state.tax = el.value;
      store.set("apla_tax", state.tax);
      const card = el.closest(".tax");
      card.querySelector("#taxline").innerHTML = taxLine(card.dataset.ward);
      card.querySelector("#taxhint").textContent = taxHasValue() ? T().taxHint : T().taxEmpty;
    } else if (el.id === "where") {
      state.where = el.value; // memory only until a road is chosen
    } else if (el.id === "pname") {
      state.name = el.value;
      store.set("apla_name", state.name);
      const av = document.querySelector(".avatar.big");
      if (av) av.innerHTML = state.name.trim() ? esc([...state.name.trim()][0].toUpperCase()) : icon("user");
    } else if (el.id === "rname" || el.id === "raddr") {
      state[el.id === "rname" ? "rtiName" : "rtiAddr"] = el.value; // memory only, never stored
      const r = roadById.get(route().id), text = rtiFilled(r);
      document.getElementById("rtitext").textContent = text;
      document.getElementById("rtiwa").href = waHref(text);
    }
  });
  app.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.id === "where") { e.preventDefault(); findPlace(); }
  });
  app.addEventListener("change", (e) => {
    if (e.target.id === "wardpick") {
      store.set("apla_ward", e.target.value);
      go(`#/ward/${encodeURIComponent(e.target.value)}`, true);
    } else if (e.target.id === "pward") {
      store.set("apla_ward", e.target.value);
      render();
    }
  });
  photo.addEventListener("change", async () => {
    const file = photo.files && photo.files[0];
    if (!file) return;
    if (state.photo?.url) URL.revokeObjectURL(state.photo.url);
    const p = { url: URL.createObjectURL(file), exif: null, thumb: "" }; // proof only: never uploaded or analysed
    state.photo = p;
    state.photoRoad = null;
    state.cands = [];
    state.whereMsg = "";
    photo.value = "";
    go("#/report"); // a photo alone answers nothing: next we ask where it is
    try { p.exif = readExifGps(await file.slice(0, 262144).arrayBuffer()); } catch { /* no location in the file */ }
    try { p.thumb = await makeThumb(file); } catch { /* thumbnail is a nicety */ }
    if (state.photo === p && route().page === "report") render();
  });
  window.addEventListener("hashchange", () => { render(); window.scrollTo(0, 0); });
}

async function boot() {
  try {
    const d = window.APLA_DATA || (await (await fetch("apla_data.json")).json());
    indexData(d);
  } catch {
    document.getElementById("app").innerHTML = `<p class="loading">apla_data.json not found</p>`;
    return;
  }
  wire();
  render();
}

if (typeof document !== "undefined" && document.getElementById("app")) boot();
