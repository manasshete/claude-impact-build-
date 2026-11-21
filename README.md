# आपला BMC · Apla BMC

**Where the road-repair money for your road went.** Two parts that tell one story about Mumbai's monsoon road repairs:

| Citizen phone app | Ward Accountability Board (laptop) |
|---|---|
| <img src="docs/road-card.png" width="260" alt="Road Card for Kurla Andheri Road"> | <img src="docs/dashboard-overview.png" width="520" alt="Overview tab of the board for L ward"> |
| Raju, 45, Kurla, reads Marathi, lives on WhatsApp. Type a road or share a photo and see how much was spent, how often it broke again, the contractor, what it should have cost against what was billed, and whether the road is still under guarantee. Then: **Me too**, **Share on WhatsApp**, **Ask BMC (ready-made RTI)**. | Public board for citizens' groups, journalists, corporators and the BMC ward office. Per ward: money, red flags with proof, contractor report cards, where the next rupee should go, and the citizen voice. BMC can answer every flag in public. |

It is not a complaint app: complaints still go to BMC's own MyBMC. It adds what none of them show: **the money, the contractor, the guarantee, and whether the fix lasted.** No party, corporator or officer name appears anywhere.

Team 23 · Claude Impact Lab Mumbai · 21 Nov 2026

## Live

- **Phone app:** <https://manasshete.github.io/claude-impact-build-/app/index.html> (GitHub Pages, from `app/`). Also `AplaBMC.apk` in this repo. Open the link on a phone and "Check the road near me" works over HTTPS.
- **Board:** deploy free on [Streamlit Community Cloud](https://share.streamlit.io): New app, repository `manasshete/claude-impact-build-`, branch `main`, main file `app.py`. Nothing else to set; `apla_data.json` is already committed.

## What is in this repo

```
app/                 phone web app (plain HTML + JS, no build step). Open app/index.html
AplaBMC.apk          the same app in a small Android wrapper (debug-signed, for sideloading)
android/             the wrapper's source (plain Java, no libraries)
scripts/build_apla_data.py   dataset CSVs -> app/apla_data.json (feeds the phone app)
build_data.py        the data brain: fair cost, 10 checks, grades, next rupee -> apla_data.json (feeds the board)
app.py               the Ward Accountability Board (Streamlit, reads apla_data.json only)
dataset/             the synthetic dataset (CC BY 4.0, see credit below)
tests/               checks for the phone app (Node, headless Chrome)
docs/                screenshots
```

## Run the board

```bash
pip install -r requirements.txt
python build_data.py            # writes apla_data.json and prints the data checks
streamlit run app.py
```

`python build_data.py --claude` also writes the ward summaries, RTI letters and contractor notes with Claude (needs `ANTHROPIC_API_KEY` in your environment; the key is never stored in the repo). Without it the same texts come from fixed templates in English, Marathi and Hindi built from the real numbers and job IDs. Generated text is cached in `claude_text_cache.json` so the demo never waits on an API call.

### The ten checks

Normal price variation (about ±30% of the rate card) is never flagged. Every flag carries its proof.

| Red, counted as "bills to check" | Rule |
|---|---|
| Overcharged | billed more than 1.5× the BMC rate card |
| Billed twice | same road and same method within 7 days, and no citizen complaint behind it |
| Work nobody asked for | no complaint linked to the job |
| Guarantee road paid by BMC | pothole, paver or cave-in repair on a road still under guarantee |
| Built weak | road under guarantee, yet 3+ surface repairs or 2+ that broke again |

| Amber, "check with context" | Rule |
|---|---|
| Fix failed within 30 days | reopened within 30 days, with a heavy-rain note |
| Repeat patching | patched 3+ times this monsoon |
| Pump with no rain | pump job with under 5 mm rain in the 3 days before, and no big tide |
| Utility damage | MTNL/MGL trench, a cost BMC can recover |
| Complaint closed by an older repair | marked fixed using a job done before it was filed |

The board never shows party or officer names: where a citizen's complaint text names a party office as a landmark, `build_data.py` replaces it with "[name removed]".

Waterlogging, drain and manhole jobs are never counted against a road contractor. Contractors are graded A to F within the same ward, using whether fixes lasted, on time, and price against the rate card.

Checked against the dataset: city fair cost ₹12.40 Cr against ₹13.43 Cr billed; 109 overcharged jobs; 45 billed twice (WO25-02459 pairs with WO25-02052, 1 day apart); 45 built-weak roads (RD0176 on top with 16 repairs); 369 utility-trench jobs, about ₹1.96 Cr; 364 complaints closed by an older repair; L ward budget ₹1.19 Cr, spent ₹1.30 Cr, ₹18.2 lakh on fixes that broke within a month; the six overspent wards are A, E, L, M/W, R/C and R/S.

## Run the phone app

- **Quickest:** double-click `app/index.html`. Location is off from disk, so it opens the sample road (Kurla Andheri Road) with a note.
- **With location:** `cd app && python -m http.server 8000`, open <http://localhost:8000>, use the browser's phone view (390 px).
- **On a phone:** host `app/` on GitHub Pages (HTTPS needed for location), or install `AplaBMC.apk` (allow "Install unknown apps").

No login, no backend. Everything the app remembers stays on the phone.

### Photos need a place

A photo is only proof; the app never reads it. So a photo alone gets no answer. After you add one, the app asks where it is and accepts any of:

- the photo's own location, when the file carries one (browsers often strip it from camera shots);
- the phone's current location;
- a Google Maps link with coordinates, or plain `lat, lon`;
- a typed address, road or area name, shown as a short list for you to pick from.

A short `goo.gl` link can't be opened, so the app asks for the full address. A place outside Mumbai is refused. The sample road is only for the "near me" button when location is off, never for a photo. Each photo report is kept on the phone with its place and a small thumbnail.

### Profile, updates and the tax card

The **Me** tab holds an optional name, your home ward, language, the photos you added, the roads you backed with "Me too", and **latest on your roads** (open complaints and how long, or "all closed"). Everything can be erased in two taps.

The **tax card** (road card, ward screen and profile) takes your yearly property tax and shows it beside what your ward spent on roads this monsoon, how much of that was above BMC's own rate, and how much went on fixes that broke within a month. It is a side-by-side comparison: it does not divide, estimate or claim how your money was used.

### Colours

The Emerald Ledger palette: pine teal `#0F4C5C` (brand), modern teal `#0D9488` (bars, active tab), off-white `#FAF9F6` with white cards and `#E4E4E7` borders, ink `#18181B` / `#52525B`, deep red `#B91C1C` (over budget, problems), ochre `#CA8A04` (pending), forest green `#15803D` (clean, guarantee), burnt tangerine `#E36414` (citizen actions and badges). Where a palette colour is too light for small text, a darker shade of the same hue is used for the text and the palette colour stays on fills, borders and bars.

### Rebuild the APK

```bash
cd android && ./gradlew assembleDebug     # JDK 17 or newer; output: app/build/outputs/apk/debug/app-debug.apk
```

It copies `app/` into the APK on every build. Set `PUBLIC_URL` in `app/app.js` once the app is hosted so WhatsApp shares carry a real link.

### Tests

```bash
node tests/check.mjs          # the spec's logic cases against the real app code
node tests/e2e.mjs            # drives the real page in headless Chrome at 360 px
node tests/e2e-report.mjs     # photo to place, profile, tax card, EXIF reader
node tests/audit.mjs          # full audit: all 291 roads x 3 languages, every interaction, layout, accessibility, the APK
```

`e2e.mjs` and `check.mjs` build file paths from URLs, which breaks on Windows when the folder name has spaces; run them from a path without spaces there.

## Known limits

- **Two data builders.** `scripts/build_apla_data.py` feeds the phone app and `build_data.py` feeds the board. Their rules differ in places (the phone app's built-weak rule flags 32 roads, the board's 45; the pump rain threshold is 2.5 mm against 5 mm; the phone app grades contractors city-wide only). The same road can show slightly different flag counts in the two. Pointing the phone app at the board's `apla_data.json` is the next step.
- The dataset has no road-owner column, so "Not a BMC road" is a rough guess from forwarded complaints near each road on the board, and is switched off in the phone app's data.
- Not built: the ideal cost of building a new road from material, wage and machine rates. The data does not have them.

## Dataset credit

Uses **Follow the Rupee: Mumbai Monsoon Roads** by Sumeet Doshi for Claude Impact Lab Mumbai: <https://github.com/SumeetGDoshi/Claude-Impact-Labs-data-2>, licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). **All data is synthetic and all contractors are fictional.** Ward codes, locality and road names are real names used only for realism; every amount, date and record is made up. Do not use it to judge any real person, contractor or department.
