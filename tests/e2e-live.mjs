// The whole loop in a real browser: phone app -> signal API -> ward-office action -> back on the phone.
// Starts the API (python, standard library) and a static server for app/, then drives Chrome.
// Usage: node tests/e2e-live.mjs      (CHROME=<path> if Chrome/Edge is not in the usual place)
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
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
const PY = process.env.PYTHON || (process.platform === "win32" ? "python" : "python3");
const API_PORT = 8790, WEB = 8791, CDP = 9343, TOKEN = "e2e-token";
const API = `http://127.0.0.1:${API_PORT}`, APP = `http://127.0.0.1:${WEB}/index.html`;
const MIME = { ".html": "text/html", ".js": "text/javascript", ".json": "application/json", ".css": "text/css" };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const tmp = mkdtempSync(join(tmpdir(), "apla-live-"));

const web = createServer((req, res) => {
  const p = req.url.split("?")[0];
  const f = join(APP_DIR, p === "/" ? "index.html" : decodeURIComponent(p));
  if (!f.startsWith(APP_DIR) || !existsSync(f)) { res.writeHead(404).end(); return; }
  res.writeHead(200, { "content-type": MIME[extname(f)] || "application/octet-stream" }).end(readFileSync(f));
}).listen(WEB, "127.0.0.1");
const apiProc = spawn(PY, [join(ROOT, "server", "api.py"), "--port", String(API_PORT), "--db", join(tmp, "live.db")],
  { stdio: "ignore", env: { ...process.env, APLA_ADMIN_TOKEN: TOKEN } });
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", `--remote-debugging-port=${CDP}`, `--user-data-dir=${join(tmp, "profile")}`, "about:blank"], { stdio: "ignore" });

let failed = 0;
const check = (name, ok, detail = "") => { console.log(`${ok ? "PASS" : "FAIL"}  ${name}${ok ? "" : "  -> " + String(detail).slice(0, 300)}`); if (!ok) failed += 1; };
const get = async (path, admin) => (await fetch(API + path, { headers: admin ? { "X-Admin-Token": TOKEN } : {} })).json();
const post = async (path, body, admin) => { const r = await fetch(API + path, { method: "POST", headers: { "Content-Type": "application/json", ...(admin ? { "X-Admin-Token": TOKEN } : {}) }, body: JSON.stringify(body) }); return [r.status, await r.json()]; };

function png() {
  const w = 64, h = 64, raw = Buffer.alloc((w * 3 + 1) * h);
  for (let y = 0; y < h; y++) { raw[y * (w * 3 + 1)] = 0; for (let x = 0; x < w; x++) { const o = y * (w * 3 + 1) + 1 + x * 3; raw[o] = 200; raw[o + 1] = 120; raw[o + 2] = 40; } }
  const chunk = (t, d) => { const b = Buffer.alloc(12 + d.length); b.writeUInt32BE(d.length, 0); b.write(t, 4, "ascii"); d.copy(b, 8); b.writeUInt32BE(crc32(Buffer.concat([Buffer.from(t), d])) >>> 0, 8 + d.length); return b; };
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 2;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk("IHDR", ihdr), chunk("IDAT", deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]);
}
const pngFile = join(tmp, "p.png"); writeFileSync(pngFile, png());

try {
  for (let i = 0; i < 60; i++) { try { if ((await fetch(API + "/api/health")).ok) break; } catch { await sleep(150); } }
  let ws;
  for (let i = 0; i < 50 && !ws; i++) {
    try { const t = await (await fetch(`http://127.0.0.1:${CDP}/json/new?about:blank`, { method: "PUT" })).json(); ws = new WebSocket(t.webSocketDebuggerUrl); await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; }); }
    catch { ws = null; await sleep(200); }
  }
  let seq = 0; const pending = new Map();
  ws.onmessage = (m) => { const msg = JSON.parse(m.data); if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); } };
  const send = (method, params = {}) => new Promise((res) => { const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
  const ev = async (expr) => { const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }); if (r.result?.exceptionDetails) throw new Error("page error: " + (r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text).split("\n")[0] + " in " + expr.slice(0, 100)); return r.result?.result?.value; };
  const fresh = async (url) => { await send("Page.navigate", { url: "about:blank" }); await sleep(150); await send("Page.navigate", { url }); for (let i = 0; i < 60 && !(await ev("!!document.querySelector('#app > :not(.loading)')")); i++) await sleep(150); await sleep(250); };
  const click = (sel) => ev(`document.querySelector(${JSON.stringify(sel)}).click()`);
  const type = (sel, v) => ev(`(() => { const el = document.querySelector(${JSON.stringify(sel)}); el.value = ${JSON.stringify(v)}; el.dispatchEvent(new Event("input", { bubbles: true })); })()`);
  const text = (sel) => ev(`document.querySelector(${JSON.stringify(sel)})?.textContent ?? ""`);
  const count = (sel) => ev(`document.querySelectorAll(${JSON.stringify(sel)}).length`);
  const hash = () => ev("location.hash");
  const setFile = async (path) => { const doc = await send("DOM.getDocument", {}); const q = await send("DOM.querySelector", { nodeId: doc.result.root.nodeId, selector: "#photo" }); await send("DOM.setFileInputFiles", { files: [path], nodeId: q.result.nodeId }); };
  const sig = async (road) => ((await get("/api/signals")).roads[road]) || { reports: 0, metoo: 0, status: null };
  await send("Page.enable"); await send("Runtime.enable"); await send("DOM.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });

  // -------- the app only talks to a board when it is told where it is
  await fresh(APP + "#/road/RD0220");
  check("L0 without an API address the complaint card is hidden (nothing leaves the phone)", (await count(".raise")) === 0);
  await fresh(`${APP}?api=${encodeURIComponent(API)}#/road/RD0220`);
  check("L1 with an API address the road card offers 'Raise a complaint'", (await count(".raise")) === 1);
  const before = (await sig("RD0220")).reports;

  // -------- raise a complaint
  await click('.raise [data-go^="#/complain/"]'); await sleep(150);
  check("L2 the button opens the complaint screen for that road", (await hash()) === "#/complain/RD0220" && (await count("#cnote")) === 1, await hash());
  check("L2b the screen says exactly what is sent, and that the name never is", /Never your name|तुमचे नाव कधीच नाही/.test(await text(".where .hint")), await text(".where .hint"));
  await click('[data-action="send-complaint"]'); await sleep(100);
  check("L3 an empty complaint is refused with a message, nothing is sent", (await text("#cmsg")).length > 5 && (await sig("RD0220")).reports === before, await text("#cmsg"));
  await type("#cnote", "खोल खड्डा स्टेशनजवळ, दुचाकी घसरत आहेत");
  await click('[data-action="send-complaint"]'); await sleep(900);
  check("L4 sending lands on the road card with a 'sent' confirmation", (await hash()) === "#/road/RD0220" && (await text(".located")).length > 5, await text(".located"));
  const s1 = await sig("RD0220");
  check("L5 the complaint reached the board (signals count went up by 1)", s1.reports === before + 1 && s1.devices_7d >= 1, JSON.stringify(s1));
  const rep = (await get("/api/reports?ward=L", true)).reports[0];
  check("L6 the ward office can read it: Marathi text intact, road and ward right", rep.note.includes("खोल खड्डा") && rep.road_id === "RD0220" && rep.ward === "L", JSON.stringify(rep).slice(0, 160));
  check("L7 the board sees a handle for the device, never the real id", /^[0-9a-f]{10}$/.test(rep.device) && !JSON.stringify(rep).includes(await ev("localStorage.getItem('apla_device')")));
  check("L8 the phone's own record is marked sent", (await ev("JSON.parse(localStorage.getItem('apla_reports'))[0].sent")) === true);

  // -------- the same device, the same road, the same day: counted once
  await click('.raise [data-go^="#/complain/"]'); await sleep(100);
  await type("#cnote", "Another complaint, same road, same day"); await click('[data-action="send-complaint"]'); await sleep(900);
  check("L9 a second complaint the same day says so, and does not inflate the count", (await sig("RD0220")).reports === before + 1 && (await text(".located")).length > 5);

  // -------- Me too is shared and counted once, even after a reload
  const base = await ev("roadById.get('RD0220').me_too");
  const m0 = (await sig("RD0220")).metoo;
  await click('[data-action="metoo"]'); await sleep(700);
  check("L10 Me too reaches the board once", (await sig("RD0220")).metoo === m0 + 1);
  check("L11 the count on the phone is the dataset's plus the shared tap, no double count", Number((await text('[data-action="metoo"] .num')).replace(/,/g, "")) === base + m0 + 1, await text('[data-action="metoo"] .num'));
  await fresh(`${APP}#/road/RD0220`); await sleep(700);
  check("L12 after a reload the shown count is unchanged (still no double count)", Number((await text('[data-action="metoo"] .num')).replace(/,/g, "")) === base + m0 + 1, await text('[data-action="metoo"] .num'));
  check("L13 the live line shows this week's complaints and Me too from the app", /\d/.test(await text(".liveline")), await text(".liveline"));

  // -------- a photo with a place goes to the board, with a small copy only if allowed
  await fresh(`${APP}?api=${encodeURIComponent(API)}#/`);
  await ev("document.querySelector('#photo').removeAttribute('capture')");
  await setFile(pngFile); await sleep(900);
  check("L14 with the board connected, the photo screen asks for an optional note and consent", (await count("#rnote")) === 1 && (await count("#rshare")) === 1);
  await ev("(() => { const c = document.querySelector('#rshare'); c.checked = false; c.dispatchEvent(new Event('change', { bubbles: true })); })()");
  await type("#rnote", "Photo with a Google Maps pin"); await type("#where", "https://www.google.com/maps/@19.0881,72.8886,17z");
  const rBefore = (await get("/api/reports?limit=500", true)).reports.length;
  await click('[data-action="find"]'); await sleep(1100);
  const rows = (await get("/api/reports?limit=500", true)).reports;
  const photoRep = rows.find((r) => r.note === "Photo with a Google Maps pin");
  check("L15 the photo report reached the board with its place (rounded) and note", rows.length === rBefore + 1 && photoRep && photoRep.via === "link" && Math.abs(photoRep.lat - 19.0881) < 0.0002, JSON.stringify(photoRep || {}).slice(0, 200));
  check("L16 consent off: no photo copy was sent", photoRep && photoRep.thumb === "");
  check("L17 the road card says whether the photo copy was sent", (await text(".photo-cap")).length > 5, await text(".photo-cap"));

  // -------- consent on: the small photo copy does reach the board
  await fresh(`${APP}?api=${encodeURIComponent(API)}#/complain/RD0225`);
  await ev("document.querySelector('#photo').removeAttribute('capture')");
  await click('[data-action="complain-photo"]'); await setFile(pngFile); await sleep(900);
  check("L17b the complaint screen shows the photo and the consent box (ticked by default)", (await count(".photo-card img")) === 1 && (await ev("document.querySelector('#cshare').checked")) === true);
  await type("#cnote", "Pothole with a photo"); await click('[data-action="send-complaint"]'); await sleep(1100);
  const withPhoto = (await get("/api/reports?limit=500", true)).reports.find((r) => r.note === "Pothole with a photo");
  check("L17c with consent on, a small jpeg copy reaches the board, and it is small", withPhoto && withPhoto.thumb.startsWith("data:image/jpeg;base64,") && withPhoto.thumb.length < 45000, (withPhoto || {}).thumb && withPhoto.thumb.length);

  // -------- offline: the complaint is kept and sent later
  await fresh(`${APP}?api=${encodeURIComponent("http://127.0.0.1:1")}#/complain/RD0224`);
  await type("#cnote", "Sent while offline"); await click('[data-action="send-complaint"]'); await sleep(1200);
  check("L18 offline: the app says it saved the complaint to send later", (await text(".note")).length > 5 && (await ev("JSON.parse(localStorage.getItem('apla_outbox')).length")) === 1, await text(".note"));
  check("L19 offline: nothing reached the board", !((await get("/api/reports?limit=500", true)).reports.some((r) => r.note === "Sent while offline")));
  await fresh(`${APP}?api=${encodeURIComponent(API)}#/profile`); await sleep(1500);
  check("L20 back online: the waiting complaint is sent on its own", (await get("/api/reports?limit=500", true)).reports.some((r) => r.note === "Sent while offline"));
  check("L21 and the queue is empty again", (await ev("JSON.parse(localStorage.getItem('apla_outbox') || '[]').length")) === 0);

  // -------- the ward office answers; the phone shows it on the road
  const [code1] = await post("/api/actions", { road_id: "RD0220", kind: "hold", text: "Payment for the last repair is held" });
  check("L22 an action without the ward-office token is refused", code1 === 401);
  const [code2] = await post("/api/actions", { road_id: "RD0220", kind: "hold", job_id: "WO25-02068", flag_key: "WO25-02068|guarantee_paid|", text: "Payment for the last repair is held" }, true);
  check("L23 the ward office records a hold", code2 === 201);
  await post("/api/actions", { road_id: "RD0220", kind: "fixfree", text: "Original contractor asked to repair it free under the guarantee" }, true);
  await fresh(`${APP}?api=${encodeURIComponent(API)}#/road/RD0220`); await sleep(900);
  check("L24 the road card on the phone shows the ward office's latest action", (await count(".statusbox")) === 1 && (await text(".statusbox")).includes("guarantee"), await text(".statusbox"));
  await click('[data-lang="en"]'); await sleep(100);
  check("L25 the status is shown in the chosen language", /Ward office/.test(await text(".statusbox")), await text(".statusbox"));
  await fresh(`${APP}#/profile`); await sleep(900);
  check("L26 'latest on your roads' on the Me tab carries the answer too", (await text(".updates")).includes("Ward office") || (await text(".updates")).includes("वॉर्ड कार्यालय"), await text(".updates"));

  // -------- erasing keeps the API address but removes the device identity
  const dev1 = await ev("localStorage.getItem('apla_device')");
  await click('[data-action="clear"]'); await click('[data-action="clear"]'); await sleep(150);
  check("L27 erase keeps the board address, drops the old device id", (await ev("localStorage.getItem('apla_api')")) === API && (await ev("localStorage.getItem('apla_device')")) !== dev1);
} catch (e) {
  console.log("ERROR", e); failed += 1;
} finally {
  chrome.kill(); apiProc.kill(); web.close();
}
console.log(failed ? `\n${failed} check(s) failed` : "\nall live-loop checks passed");
process.exit(failed ? 1 : 0);
