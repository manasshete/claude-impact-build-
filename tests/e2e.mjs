// Drives the real page in headless Chrome (DevTools protocol, no npm deps) through the spec's test cases.
// Usage: node tests/e2e.mjs   (serves app/ on 127.0.0.1:8765, needs Google Chrome installed)
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const APP_DIR = new URL("../app/", import.meta.url).pathname;
const ORIGIN = "http://127.0.0.1:8765";
const PORT = 9333;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const profile = mkdtempSync(join(tmpdir(), "apla-e2e-"));
const server = spawn("python3", ["-I", "-m", "http.server", "8765", "--bind", "127.0.0.1", "--directory", APP_DIR], { stdio: "ignore" });
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, "about:blank"], { stdio: "ignore" });

let failed = 0;
const check = (name, ok, detail = "") => {
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${ok ? "" : "  → " + detail}`);
  if (!ok) failed += 1;
};

async function connect() {
  for (let i = 0; i < 50; i++) {
    try {
      const t = await (await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`, { method: "PUT" })).json();
      const ws = new WebSocket(t.webSocketDebuggerUrl);
      await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
      return ws;
    } catch { await sleep(200); }
  }
  throw new Error("chrome did not start");
}

try {
  const ws = await connect();
  let seq = 0;
  const pending = new Map();
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
  };
  const send = (method, params = {}) => new Promise((res) => { const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
  const js = async (expr) => (await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true })).result?.result?.value;
  const open = async (hash = "") => {
    await send("Page.navigate", { url: `${ORIGIN}/index.html${hash}` });
    for (let i = 0; i < 50 && !(await js("!!document.querySelector('#app > :not(.loading)')")); i++) await sleep(200);
  };
  const click = (sel) => js(`document.querySelector(${JSON.stringify(sel)}).click()`);
  const type = (sel, v) => js(`(() => { const el = document.querySelector(${JSON.stringify(sel)}); el.value = ${JSON.stringify(v)}; el.dispatchEvent(new Event("input", { bubbles: true })); })()`);
  const text = (sel) => js(`document.querySelector(${JSON.stringify(sel)})?.textContent ?? ""`);

  await send("Page.enable");
  await send("Runtime.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 360, height: 780, deviceScaleFactor: 2, mobile: true });
  await open();
  await js("localStorage.clear()");

  // A2 location denied → sample road + note
  await send("Browser.setPermission", { origin: ORIGIN, permission: { name: "geolocation" }, setting: "denied" });
  await open();
  await click('[data-action="locate"]');
  await sleep(1200);
  check("A2 location off → Kurla Andheri Road + note", (await js("location.hash")) === "#/road/RD0220" && (await text(".note")).includes("नमुना रस्ता"), await js("location.hash"));

  // A2b permission prompt never answered → watchdog falls back after 12 s
  await send("Browser.setPermission", { origin: ORIGIN, permission: { name: "geolocation" }, setting: "prompt" });
  await open();
  await click('[data-action="locate"]');
  await sleep(13000);
  check("A2b unanswered location prompt → sample road after 12 s", (await js("location.hash")) === "#/road/RD0220", await js("location.hash"));

  // A1 location allowed near Kurla Andheri Road
  await send("Browser.grantPermissions", { origin: ORIGIN, permissions: ["geolocation"] });
  await send("Emulation.setGeolocationOverride", { latitude: 19.0813, longitude: 72.886, accuracy: 10 });
  await open();
  await click('[data-action="locate"]');
  await sleep(1200);
  check("A1 location near Kurla Andheri Road → its card, no note", (await js("location.hash")) === "#/road/RD0220" && !(await js("!!document.querySelector('.note')")));

  // A6 guarantee + A16 money on the card
  check("A6 guarantee box + ₹0 line", (await js("!!document.querySelector('.guarantee')")) && (await text(".good-line")).includes("₹0"));
  check("A16 big number ₹30.5 लाख", (await text(".money .big")) === "₹30.5 लाख", await text(".money .big"));

  // A14 tax empty → nothing; typed → line
  check("A14 tax empty → no line", (await text("#taxline")) === "");
  await type("#tax", "12000");
  check("    tax typed → line with ₹12,000", (await text("#taxline")).includes("₹12,000"), await text("#taxline"));

  // A11 me too twice → +1 once
  const before = await text('[data-action="metoo"] .num');
  await click('[data-action="metoo"]');
  await click('[data-action="metoo"]');
  const after = await text('[data-action="metoo"] .num');
  check("A11 me too twice → count +1 once", Number(after.replace(/,/g, "")) === Number(before.replace(/,/g, "")) + 1 && (await text('[data-action="metoo"]')).includes("✓"), `${before} → ${after}`);

  // A12 WhatsApp link
  const wa = decodeURIComponent((await js(`document.querySelector('.actions a.green').href`)).replace("https://wa.me/?text=", ""));
  check("A12 WhatsApp link has filled Marathi text", wa.includes("Kurla Andheri Road (कुर्ला) चा हिशोब") && wa.includes("#/road/RD0220"), wa);

  // A5 switch language: labels change, numbers stay
  await click('[data-lang="hi"]');
  const hi = await text(".money .label");
  await click('[data-lang="en"]');
  check("A5 Hindi → English labels change, numbers stay", hi.includes("मानसून") && (await text(".money .label")).startsWith("Your ward") && (await text(".money .big")) === "₹30.5 lakh" && (await text(".stats")).includes("35"));
  check("    tax value + me-too survive the switch", (await text("#taxline")).includes("₹12,000") && (await text('[data-action="metoo"]')).includes("Your voice added"));

  // A13 RTI with real ids; name fills; copy
  await click('[data-go="#/rti/RD0220"]');
  await sleep(300);
  await send("Browser.grantPermissions", { origin: ORIGIN, permissions: ["geolocation", "clipboardReadWrite", "clipboardSanitizedWrite"] });
  await send("Page.bringToFront");
  await type("#rname", "Raju Patil");
  const rti = await text("#rtitext");
  check("A13 RTI shows job ids + typed name", rti.includes("WO25-00677") && rti.includes("Raju Patil"));
  await click('[data-action="copy"]');
  await sleep(200);
  check("    copy button confirms", (await text('[data-action="copy"]')).includes("Copied"), await text('[data-action="copy"]'));
  check("    name is not written to localStorage", !(await js("JSON.stringify(localStorage)")).includes("Raju"));

  // A3 / A4 search on home
  await click('[data-lang="mr"]');
  await open("#/");
  await type("#q", "कुर्ला");
  check("A3 search कुर्ला → L roads", (await text("#results")).includes("L · कुर्ला"));
  await type("#q", "Saki Naka");
  check("A4 search Saki Naka → L roads", (await text("#results")).includes("Saki Naka Junction"));
  await type("#q", "zzzz");
  check("    no match → friendly message", (await text("#results")).includes("काहीच सापडले नाही"));

  // A7 / A9 / A10 states
  await open("#/road/RD0022");
  check("A9 open complaint, no repair → 'no repair yet'", (await text(".state-box")).includes("अजून दुरुस्ती नाही") && !(await js("!!document.querySelector('.money')")));
  const noGuar = await js(`window.APLA_DATA.roads.find(r => r.jobs && !r.under_guarantee).road_id`);
  await open(`#/road/${noGuar}`);
  check("A7 no guarantee → no box, no ₹0 line", !(await js("!!document.querySelector('.guarantee') || !!document.querySelector('.good-line')")));
  const clean = await js(`window.APLA_DATA.roads.find(r => r.jobs && !r.flags.length).road_id`);
  await open(`#/road/${clean}`);
  check("A10 zero flags → green 'no suspicious bills'", (await text(".all-clear")).includes("संशयास्पद खर्च नाही"));

  // A15 at 360px: no sideways scroll, every button ≥ 48px tall
  for (const h of ["#/", "#/road/RD0220", "#/rti/RD0220", "#/ward/L", "#/road/RD0022"]) {
    await open(h);
    const r = await js(`({ w: document.documentElement.scrollWidth, small: [...document.querySelectorAll('button, a.btn, .langs button, summary')].filter(b => b.offsetParent && b.getBoundingClientRect().height < 47.5).map(b => b.textContent.trim().slice(0, 30)) })`);
    check(`A15 ${h} fits 360px, buttons ≥ 48px`, r.w <= 360 && r.small.length === 0, JSON.stringify(r));
  }
  ws.close();
} catch (e) {
  console.error(e);
  failed += 1;
} finally {
  chrome.kill();
  server.kill();
  await sleep(300);
  rmSync(profile, { recursive: true, force: true });
}
console.log(failed ? `\n${failed} check(s) failed` : "\nall e2e checks passed");
process.exit(failed ? 1 : 0);
