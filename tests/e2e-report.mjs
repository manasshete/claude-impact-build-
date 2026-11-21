// Photo → place flow, profile and tax card, in the real page (headless Chrome over the DevTools protocol, no npm deps).
// Usage: node tests/e2e-report.mjs     (CHROME=<path to Chrome/Edge> if it is not in the usual place)
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { existsSync, mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { extname, join } from "node:path";
import { fileURLToPath } from "node:url";

const APP_DIR = fileURLToPath(new URL("../app/", import.meta.url));
const CANDIDATES = [process.env.CHROME,
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "/usr/bin/google-chrome", "/usr/bin/chromium"].filter(Boolean);
const CHROME = CANDIDATES.find((p) => existsSync(p));
if (!CHROME) { console.log("no Chrome/Edge found; set CHROME=<path>"); process.exit(1); }
const PORT = 9334, WEB = 8766, ORIGIN = `http://127.0.0.1:${WEB}`;
const MIME = { ".html": "text/html", ".js": "text/javascript", ".json": "application/json", ".css": "text/css" };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const web = createServer((req, res) => {
  const f = join(APP_DIR, req.url.split("?")[0] === "/" ? "index.html" : decodeURIComponent(req.url.split("?")[0]));
  if (!f.startsWith(APP_DIR) || !existsSync(f)) { res.writeHead(404).end(); return; }
  res.writeHead(200, { "content-type": MIME[extname(f)] || "application/octet-stream" }).end(readFileSync(f));
}).listen(WEB, "127.0.0.1");
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", `--remote-debugging-port=${PORT}`, `--user-data-dir=${mkdtempSync(join(tmpdir(), "apla-r-"))}`, "about:blank"], { stdio: "ignore" });

let failed = 0;
const check = (name, ok, detail = "") => { console.log(`${ok ? "PASS" : "FAIL"}  ${name}${ok ? "" : "  → " + detail}`); if (!ok) failed += 1; };

// a minimal JPEG whose EXIF says 19°04'30" N, 72°53'00" E (big-endian TIFF), built byte by byte
function jpegWithGps() {
  const u16 = (n) => [n >> 8, n & 255], u32 = (n) => [n >>> 24, (n >> 16) & 255, (n >> 8) & 255, n & 255];
  const ent = (tag, type, cnt, val) => [...u16(tag), ...u16(type), ...u32(cnt), ...val];
  const rat = (a) => a.flatMap((n) => [...u32(n), ...u32(1)]);
  const tiff = [0x4d, 0x4d, 0, 42, ...u32(8), ...u16(1), ...ent(0x8825, 4, 1, u32(26)), ...u32(0),
    ...u16(4), ...ent(1, 2, 2, [0x4e, 0, 0, 0]), ...ent(2, 5, 3, u32(80)), ...ent(3, 2, 2, [0x45, 0, 0, 0]), ...ent(4, 5, 3, u32(104)), ...u32(0),
    ...rat([19, 4, 30]), ...rat([72, 53, 0])];
  const app1 = [0x45, 0x78, 0x69, 0x66, 0, 0, ...tiff];
  return [0xff, 0xd8, 0xff, 0xe1, ...u16(app1.length + 2), ...app1, 0xff, 0xd9];
}

try {
  let ws;
  for (let i = 0; i < 50 && !ws; i++) {
    try {
      const t = await (await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`, { method: "PUT" })).json();
      ws = new WebSocket(t.webSocketDebuggerUrl);
      await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    } catch { ws = null; await sleep(200); }
  }
  let seq = 0;
  const pending = new Map();
  ws.onmessage = (m) => { const msg = JSON.parse(m.data); if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); } };
  const send = (method, params = {}) => new Promise((res) => { const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
  const js = async (expr) => (await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true })).result?.result?.value;
  const open = async (hash = "") => {
    await send("Page.navigate", { url: `${ORIGIN}/index.html${hash}` });
    for (let i = 0; i < 50 && !(await js("!!document.querySelector('#app > :not(.loading)')")); i++) await sleep(200);
  };
  const click = (sel) => js(`document.querySelector(${JSON.stringify(sel)}).click()`);
  const type = (sel, v) => js(`(() => { const el = document.querySelector(${JSON.stringify(sel)}); el.value = ${JSON.stringify(v)}; el.dispatchEvent(new Event("input", { bubbles: true })); })()`);
  const text = (sel) => js(`document.querySelector(${JSON.stringify(sel)})?.textContent ?? ""`);
  const hash = () => js("location.hash");
  const addPhoto = () => js(`(() => { state.photo = { url: "", exif: null, thumb: "" }; state.photoRoad = null; state.cands = []; state.whereMsg = ""; go("#/report"); })()`);
  const lat = (m) => `https://www.google.com/maps/place/Kurla/@${m},17z`;

  await send("Page.enable");
  await open("");
  await js("localStorage.clear()");
  await open("");

  // R1 a photo alone must not give an answer
  await addPhoto();
  await click('[data-action="find"]');
  check("R1 photo, no place → stays on the report screen, asks for a place", (await hash()) === "#/report" && (await text("#wheremsg")).length > 0, await hash());
  await js(`navigator.geolocation.getCurrentPosition = (ok, fail) => setTimeout(fail, 50)`); // location denied
  await click('[data-action="gps"]');
  await sleep(400);
  check("R1b photo + location denied → still no road (never the sample road)", (await hash()) === "#/report" && (await text("#wheremsg")).length > 10, `${await hash()} / ${await text("#wheremsg")}`);

  // R2 Google Maps link with coordinates near Kurla Andheri Road (RD0220)
  await type("#where", lat("19.081324,72.885982"));
  await click('[data-action="find"]');
  await sleep(150);
  check("R2 Maps link with coordinates → that road's card", (await hash()).startsWith("#/road/"), await hash());
  check("    card says the place came from the link", (await text(".located")).includes("Google Maps"), await text(".located"));

  // R3 short links cannot be read
  await addPhoto();
  await type("#where", "https://maps.app.goo.gl/AbCdEf123");
  await click('[data-action="find"]');
  check("R3 short link → asks for the full address, no road", (await hash()) === "#/report" && (await text("#wheremsg")).length > 10, await text("#wheremsg"));

  // R4 typed area → choices, never an automatic pick
  await type("#where", "Saki Naka");
  await click('[data-action="find"]');
  check("R4 typed area → a list to pick from", (await js("document.querySelectorAll('[data-action=\"pick\"]').length")) > 0 && (await hash()) === "#/report");
  await click('[data-action="pick"]');
  check("    tapping one opens its card", (await hash()).startsWith("#/road/"), await hash());

  // R5 a place outside Mumbai
  await addPhoto();
  await type("#where", "28.6139, 77.2090");
  await click('[data-action="find"]');
  check("R5 coordinates outside Mumbai → refused", (await hash()) === "#/report" && (await text("#wheremsg")).length > 10, await text("#wheremsg"));

  // R6 gibberish
  await type("#where", "qqqqzzzz nowhere");
  await click('[data-action="find"]');
  check("R6 unknown text → no road, friendly message", (await hash()) === "#/report" && (await text("#wheremsg")).length > 10);

  // R7 EXIF GPS inside a real JPEG is read
  const bytes = jpegWithGps();
  const got = await js(`(() => { const g = readExifGps(new Uint8Array(${JSON.stringify(bytes)}).buffer); return g ? [Number(g.lat.toFixed(4)), Number(g.lon.toFixed(4))] : null; })()`);
  check("R7 EXIF GPS read from a JPEG (19.0750, 72.8833)", Array.isArray(got) && got[0] === 19.075 && got[1] === 72.8833, JSON.stringify(got));
  check("R7b a file with no EXIF gives no location", (await js(`readExifGps(new Uint8Array([255,216,255,217]).buffer)`)) === null);

  // R8 profile: the report is kept on the phone, tax is saved and compared with the ward
  await open("#/profile");
  check("R8 profile lists the photo report", (await js("document.querySelectorAll('.report').length")) >= 2, await js("document.querySelectorAll('.report').length"));
  await type("#tax", "18000");
  const line = await text("#taxline");
  check("R8b tax card sets your tax beside the ward's road money", line.includes("₹18,000") && line.includes("₹"), line);
  await open("#/profile");
  check("R8c tax survives a reload", (await js("document.querySelector('#tax').value")) === "18000");
  check("R8d updates section shows the road's status", (await js("document.querySelectorAll('.update').length")) >= 1);

  // R9 erase my data
  await click('[data-action="clear"]');
  check("R9 first tap only arms the erase", (await js("document.querySelectorAll('.report').length")) >= 2);
  await click('[data-action="clear"]');
  check("    second tap erases reports and tax", (await js("document.querySelectorAll('.report').length")) === 0 && (await js("document.querySelector('#tax').value")) === "");

  // R10 layout at 360px on the new screens
  await send("Emulation.setDeviceMetricsOverride", { width: 360, height: 780, deviceScaleFactor: 2, mobile: true });
  for (const h of ["#/report", "#/profile", "#/"]) {
    await open(h);
    const r = await js(`({ w: document.documentElement.scrollWidth, small: [...document.querySelectorAll('button, a.btn, summary, select')].filter(b => b.offsetHeight && b.offsetHeight < 48).map(b => b.textContent.trim().slice(0, 14)) })`);
    check(`R10 ${h} fits 360px, controls ≥ 48px`, r.w <= 360 && r.small.length === 0, JSON.stringify(r));
  }
} catch (e) {
  console.log("ERROR", e);
  failed += 1;
} finally {
  chrome.kill();
  web.close();
}
console.log(failed ? `\n${failed} check(s) failed` : "\nall report/profile checks passed");
process.exit(failed ? 1 : 0);
