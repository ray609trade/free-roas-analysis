#!/usr/bin/env node
/* =========================================================================
   qa.js — correctness, compliance and quality gate.

   NOTE ON HISTORY: README.md documents a `build/qa.js` with a 54-check gate.
   That file was never committed — `.gitignore` ignored `build/` — so it is
   absent from the repository and could not be recovered. This is a fresh
   gate covering the invariants that can be verified against the pages as
   they exist today. It is deliberately narrower than the one described in
   the README; treat that count as historical, not as a target.

   The compliance checks are not stylistic. Several state finder statutes
   require disclosing that a person can search and claim for free, and the
   TCPA requires that consent to be separate and optional. A page that loses
   that language is a legal problem, not a cosmetic one.

   Usage:  node tools/qa.js            correctness + compliance
           node tools/qa.js --links    additionally verify external URLs
                                       (needs open network access)
   ========================================================================= */

"use strict";

const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const WITH_LINKS = process.argv.includes("--links");

let passed = 0;
const failures = [];

function check(name, ok, detail) {
  if (ok) {
    passed++;
  } else {
    failures.push({ name, detail });
  }
}

/* ---- page inventory ---------------------------------------------------- */
function htmlFiles(dir = ROOT, acc = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === ".git" || entry.name === "node_modules") continue;
    const abs = path.join(dir, entry.name);
    if (entry.isDirectory()) htmlFiles(abs, acc);
    else if (entry.name.endsWith(".html")) acc.push(abs);
  }
  return acc;
}

const files = htmlFiles();
const pages = files.map((f) => ({
  file: f,
  rel: path.relative(ROOT, f),
  html: fs.readFileSync(f, "utf8")
}));

check("Site has pages to check", pages.length > 0, "no HTML files found");

/* ---- 1. per-page invariants -------------------------------------------- */
const missingCanonical = [];
const missingGovDisclaimer = [];
const missingFreeClaim = [];
const missingStylesheet = [];

for (const p of pages) {
  if (!/rel="canonical"/i.test(p.html)) missingCanonical.push(p.rel);
  if (!/not a government agency/i.test(p.html)) missingGovDisclaimer.push(p.rel);
  /* The free-claim disclosure appears in several phrasings across the site. */
  if (!/at no cost|free to search|yourself for free/i.test(p.html)) {
    missingFreeClaim.push(p.rel);
  }
  if (!/assets\/site\.css/.test(p.html)) missingStylesheet.push(p.rel);
}

check("Every page has a canonical URL", !missingCanonical.length, missingCanonical.join(", "));
check(
  "Every page states we are not a government agency",
  !missingGovDisclaimer.length,
  missingGovDisclaimer.join(", ")
);
check(
  "Every page carries the free-claim disclosure",
  !missingFreeClaim.length,
  missingFreeClaim.join(", ")
);
check("Every page loads the stylesheet", !missingStylesheet.length, missingStylesheet.join(", "));

/* ---- 2. links must stay relative --------------------------------------- */
/* Root-absolute internal links break the site when opened from the
   filesystem, which is how it is reviewed before deploys. */
const absolute = [];
for (const p of pages) {
  const hits = p.html.match(/(?:href|src)="\/(?!\/)[^"]*"/g);
  if (hits) absolute.push(`${p.rel}: ${hits.slice(0, 3).join(" ")}`);
}
check("No root-absolute internal links", !absolute.length, absolute.join(" | "));

/* ---- 3. no broken internal links --------------------------------------- */
const broken = [];
for (const p of pages) {
  const dir = path.dirname(p.file);
  const links = p.html.match(/(?:href|src)="([^"]+)"/g) || [];
  for (const raw of links) {
    const link = raw.slice(raw.indexOf('"') + 1, -1);
    if (/^(https?:|mailto:|tel:|#|\/\/|data:)/.test(link)) continue;
    const target = link.split("#")[0].split("?")[0];
    if (!target) continue;
    if (!fs.existsSync(path.resolve(dir, target))) broken.push(`${p.rel} → ${link}`);
  }
}
check("No broken internal links", !broken.length, [...new Set(broken)].join(", "));

/* ---- 4. consent capture on every lead form ------------------------------ */
const formPages = pages.filter((p) => /<form[^>]*(data-lead|id="finderForm")/.test(p.html));
check("Lead forms exist", formPages.length > 0, "no lead-capture form found on any page");

const noServiceConsent = [];
const badPhoneConsent = [];

for (const p of formPages) {
  if (!/data-consent-service|id="consent"/.test(p.html)) noServiceConsent.push(p.rel);

  /* Where phone consent is offered it must be optional and carry the
     opt-out language. Bundled or unlabelled consent is the TCPA risk. */
  if (/data-consent-phone|id="phoneConsent"/.test(p.html)) {
    const optional = /optional-tag|\(optional\)/i.test(p.html);
    const stop = /Reply STOP/i.test(p.html);
    const notCondition = /not a condition of purchase/i.test(p.html);
    if (!optional || !stop || !notCondition) badPhoneConsent.push(p.rel);
  }
}

check(
  "Every lead form captures service consent",
  !noServiceConsent.length,
  noServiceConsent.join(", ")
);
check(
  "Phone consent is optional and carries opt-out language",
  !badPhoneConsent.length,
  badPhoneConsent.join(", ")
);

/* Phone consent must never be pre-checked — that is not consent. */
const prechecked = pages.filter((p) =>
  /<input[^>]*data-consent-phone[^>]*\schecked|<input[^>]*id="phoneConsent"[^>]*\schecked/.test(p.html)
);
check(
  "No pre-checked consent boxes",
  !prechecked.length,
  prechecked.map((p) => p.rel).join(", ")
);

/* ---- 5. no leaked keys -------------------------------------------------- */
const secrets = [];
const scanDirs = ["assets", "tools", "data"];
for (const d of scanDirs) {
  const abs = path.join(ROOT, d);
  if (!fs.existsSync(abs)) continue;
  for (const f of fs.readdirSync(abs)) {
    const full = path.join(abs, f);
    if (full === __filename) continue; /* this file defines the patterns */
    if (!fs.statSync(full).isFile()) continue;
    if (/\.(png|jpg|jpeg|gif|ico|webp)$/i.test(f)) continue;
    const body = fs.readFileSync(full, "utf8");
    if (/\b(sk_live_|rk_live_|sk_test_[A-Za-z0-9]{20,})/.test(body)) {
      secrets.push(path.relative(ROOT, full));
    }
  }
}
check("No live secret keys committed", !secrets.length, secrets.join(", "));

/* ---- 6. metadata quality ------------------------------------------------ */
const titles = new Map();
const noDescription = [];
for (const p of pages) {
  const t = (p.html.match(/<title>([\s\S]*?)<\/title>/) || [])[1];
  if (t) titles.set(t.trim(), (titles.get(t.trim()) || 0) + 1);
  if (!/<meta name="description"/i.test(p.html)) noDescription.push(p.rel);
}
const dupes = [...titles].filter(([, n]) => n > 1).map(([t]) => t);
check("No duplicate page titles", !dupes.length, dupes.join(" | "));
check("Every page has a meta description", !noDescription.length, noDescription.join(", "));

/* ---- 7. sitemap integrity ----------------------------------------------- */
const sitemapPath = path.join(ROOT, "sitemap.xml");
if (fs.existsSync(sitemapPath)) {
  const xml = fs.readFileSync(sitemapPath, "utf8");
  const locs = [...xml.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1]);
  const missing = [];

  for (const loc of locs) {
    let rel = loc.replace(/^https?:\/\/[^/]+\//, "");
    if (rel === "" || rel.endsWith("/")) rel += "index.html";
    if (!fs.existsSync(path.join(ROOT, rel))) missing.push(loc);
  }

  check("Sitemap has entries", locs.length > 0, "sitemap.xml contains no <loc> elements");
  check("Every sitemap URL resolves to a real page", !missing.length, missing.join(", "));
} else {
  check("sitemap.xml exists", false, "not found");
}

/* ---- 8. external URLs (opt-in) ------------------------------------------ */
async function checkExternalLinks() {
  const urls = new Set();
  const dataDir = path.join(ROOT, "data");

  for (const f of fs.readdirSync(dataDir).filter((f) => f.endsWith(".json"))) {
    const body = fs.readFileSync(path.join(dataDir, f), "utf8");
    for (const m of body.matchAll(/"(https?:\/\/[^"]+)"/g)) urls.add(m[1]);
  }

  console.log(`\nVerifying ${urls.size} external URLs from data/…\n`);
  const dead = [];

  for (const url of urls) {
    let ok = false;
    let status = "";
    for (const method of ["HEAD", "GET"]) {
      try {
        const res = await fetch(url, {
          method,
          redirect: "follow",
          signal: AbortSignal.timeout(15000),
          headers: { "User-Agent": "ReclaimWealth-linkcheck/1.0" }
        });
        status = res.status;
        /* Some government sites reject HEAD but serve GET fine. */
        if (res.ok || res.status === 403 || res.status === 405) {
          ok = res.ok || method === "GET";
        }
        if (res.ok) { ok = true; break; }
      } catch (e) {
        status = e.name === "TimeoutError" ? "timeout" : e.message;
      }
    }
    console.log(`    ${ok ? "✓" : "✗"} ${url}${ok ? "" : "  (" + status + ")"}`);
    if (!ok) dead.push(`${url} (${status})`);
  }

  check("Every external URL in data/ resolves", !dead.length, dead.join(", "));
}

/* ---- report ------------------------------------------------------------- */
function report() {
  console.log(`\n${"-".repeat(60)}`);
  console.log(`Checked ${pages.length} pages.`);
  console.log(`${passed} passed, ${failures.length} failed.`);

  if (failures.length) {
    console.log("\nFailures:\n");
    for (const f of failures) {
      console.log(`  ✗ ${f.name}`);
      if (f.detail) console.log(`      ${f.detail}`);
    }
    console.log("");
    process.exit(1);
  }
  console.log("");
}

if (WITH_LINKS) {
  checkExternalLinks().then(report);
} else {
  report();
}
