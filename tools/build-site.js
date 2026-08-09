#!/usr/bin/env node
/* =========================================================================
   build-site.js — post-processes every page in the site.

   Four jobs, all idempotent (safe to run repeatedly):

     1. Normalise the header nav across all pages and add the mobile
        hamburger toggle.
     2. Normalise the footer link columns (adds Contact and Pricing).
     3. Replace the plain call-to-action band on interior pages with a real
        inline lead-capture form plus a "or sign up now" second path, so
        every page can convert instead of bouncing the visitor home.
     4. Rewrite root-absolute internal links (/assets/..., /about.html) to
        relative paths, so the site opens correctly straight off the
        filesystem via file:// as well as over HTTP.

   Canonical, og:url and JSON-LD URLs are deliberately left absolute — those
   must stay pointed at the production domain.

   Usage:  node tools/build-site.js [--check]
           --check reports what would change without writing.
   ========================================================================= */

"use strict";

const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const CHECK_ONLY = process.argv.includes("--check");

/* Interior sections that sit one directory below the site root. */
const SUBDIRS = ["states", "find", "learn"];

const states = JSON.parse(fs.readFileSync(path.join(ROOT, "data/states.json"), "utf8"));
const admins = JSON.parse(fs.readFileSync(path.join(ROOT, "data/administrators.json"), "utf8"));

/* Both files wrap their records under a named key alongside other arrays
   (states.json also carries an empty `excludedStates`), so pick the named
   key first and fall back to the longest array rather than the first one. */
function records(obj, key) {
  if (Array.isArray(obj)) return obj;
  if (Array.isArray(obj[key])) return obj[key];
  return Object.values(obj)
    .filter(Array.isArray)
    .sort((a, b) => b.length - a.length)[0] || [];
}

const stateBySlug = new Map(records(states, "states").map((s) => [s.slug, s]));
const adminBySlug = new Map(records(admins, "administrators").map((a) => [a.slug, a]));

/* ---- page inventory ---------------------------------------------------- */
function pages() {
  const out = fs
    .readdirSync(ROOT)
    .filter((f) => f.endsWith(".html"))
    .map((f) => ({ file: path.join(ROOT, f), prefix: "", section: "root", slug: f.replace(/\.html$/, "") }));

  for (const dir of SUBDIRS) {
    const abs = path.join(ROOT, dir);
    if (!fs.existsSync(abs)) continue;
    for (const f of fs.readdirSync(abs).filter((f) => f.endsWith(".html"))) {
      out.push({
        file: path.join(abs, f),
        prefix: "../",
        section: dir,
        slug: f.replace(/\.html$/, "")
      });
    }
  }
  return out;
}

/* ---- 1. header nav ------------------------------------------------------ */
function navMarkup(page) {
  const p = page.prefix;
  /* On the homepage the section links are same-page anchors. */
  const home = page.section === "root" && page.slug === "index";
  const how = home ? "#how" : `${p}index.html#how`;
  const search = home ? "#search" : `${p}index.html#search`;

  return `      <nav class="nav-links" id="primaryNav" aria-label="Primary">
        <a href="${how}">How It Works</a>
        <a href="${p}states/index.html">By State</a>
        <a href="${p}learn/index.html">Guides</a>
        <a href="${p}pricing.html">Pricing</a>
        <a href="${p}contact.html">Contact</a>
        <a href="${search}" class="btn btn-gold" style="padding:.55rem 1.1rem;font-size:.9rem">Free Search</a>
      </nav>`;
}

function applyNav(html, page) {
  /* Replace the whole nav block so every page carries the same links. */
  html = html.replace(/ {0,6}<nav class="nav-links"[\s\S]*?<\/nav>/, navMarkup(page));

  /* Add the hamburger button once, immediately after the brand link. */
  if (!html.includes("nav-toggle")) {
    html = html.replace(
      /(<a class="brand"[\s\S]*?<\/a>)\s*\n(\s*)(<nav class="nav-links")/,
      `$1
$2<button class="nav-toggle" type="button" aria-expanded="false" aria-controls="primaryNav" aria-label="Menu">
$2  <span class="bars" aria-hidden="true"></span>
$2</button>
$2$3`
    );
  }
  return html;
}

/* ---- 2. footer columns -------------------------------------------------- */
function applyFooter(html, page) {
  const p = page.prefix;
  const home = page.section === "root" && page.slug === "index";
  const anchor = (frag) => (home ? `#${frag}` : `${p}index.html#${frag}`);

  const explore = `        <div>
          <h5>Explore</h5>
          <a href="${anchor("how")}">How It Works</a>
          <a href="${anchor("registries")}">Where We Search</a>
          <a href="${p}states/index.html">Search by State</a>
          <a href="${p}find/index.html">Find Your Plan</a>
          <a href="${p}learn/index.html">Guides &amp; Glossary</a>
          <a href="${p}pricing.html">Pricing</a>
        </div>`;

  const company = `        <div>
          <h5>Company</h5>
          <a href="${p}about.html">About &amp; How We Make Money</a>
          <a href="${p}contact.html">Contact Us</a>
          <a href="${p}privacy.html">Privacy Policy</a>
          <a href="${p}terms.html">Terms of Service</a>
          <a href="${p}disclosures.html">Disclosures</a>
        </div>`;

  return html.replace(
    /( {0,8}<div>\s*<h5>Explore<\/h5>[\s\S]*?<\/div>\s*)( {0,8}<div>\s*<h5>Company<\/h5>[\s\S]*?<\/div>)/,
    `${explore}\n${company}`
  );
}

/* ---- 3. inline lead capture on interior pages --------------------------- */
/* Prefill what the page already tells us. A state guide knows the state, so
   fill it in. A recordkeeper guide knows the plan administrator — but that
   is not the visitor's employer, so the employer field stays blank there. */
function prefillFor(page) {
  if (page.section === "states" && stateBySlug.has(page.slug)) {
    return { state: stateBySlug.get(page.slug).name, employer: "" };
  }
  return { state: "", employer: "" };
}

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

function leadSection(page, heading, copy) {
  const p = page.prefix;
  const fill = prefillFor(page);
  const id = page.section + "-" + page.slug;
  const stateAttr = fill.state ? ` value="${esc(fill.state)}"` : "";
  const employerAttr = fill.employer ? ` value="${esc(fill.employer)}"` : "";

  return `    <section class="lead-band">
      <div class="wrap grid">
        <div>
          <span class="eyebrow">Free search plan</span>
          <h2>${heading}</h2>
          <p class="lead-copy">${copy}</p>
          <div class="free-notice">
            <strong>Always free to search yourself.</strong> Every registry we use is a free public or government resource, and you can file any claim directly at no cost. We charge only if you want us to do the work for you.
          </div>
          <div class="dual-cta">
            <span class="or">Already know you want help?</span>
            <p>Skip ahead and we'll run the full 50-state sweep and trace every employer you list.</p>
            <a href="${p}pricing.html" class="btn btn-gold">See plans &amp; sign up →</a>
          </div>
        </div>

        <div class="lead-card">
          <h3>Get your free search plan</h3>
          <p class="sub">Tell us where you've worked and we'll email your personalized checklist. No payment, no obligation.</p>

          <form data-lead novalidate>
            <div class="row-2">
              <div class="field">
                <label for="${id}-first">First name</label>
                <input id="${id}-first" name="firstName" type="text" autocomplete="given-name" required />
              </div>
              <div class="field">
                <label for="${id}-last">Last name</label>
                <input id="${id}-last" name="lastName" type="text" autocomplete="family-name" required />
              </div>
            </div>

            <div class="field">
              <label for="${id}-email">Email <span class="hint">— where we send your plan</span></label>
              <input id="${id}-email" name="email" type="email" autocomplete="email" required />
            </div>

            <div class="row-2">
              <div class="field">
                <label for="${id}-phone">Phone <span class="hint">(optional)</span></label>
                <input id="${id}-phone" name="phone" type="tel" autocomplete="tel" />
              </div>
              <div class="field">
                <label for="${id}-state">State(s) you've worked in</label>
                <input id="${id}-state" name="state" type="text" placeholder="e.g. NY, NJ, FL"${stateAttr} />
              </div>
            </div>

            <div class="field">
              <label for="${id}-employer">A former employer</label>
              <input id="${id}-employer" name="employer" type="text" placeholder="e.g. Acme Corp"${employerAttr} />
            </div>

            <!-- Honeypot: hidden from people, tempting to bots -->
            <div style="position:absolute;left:-9999px" aria-hidden="true">
              <label for="${id}-website">Leave this field empty</label>
              <input id="${id}-website" name="website" type="text" tabindex="-1" autocomplete="off" />
            </div>

            <div class="consent-group">
              <label class="consent" for="${id}-consent">
                <input type="checkbox" id="${id}-consent" data-consent-service required />
                <span>I authorize ReclaimWealth to search publicly available registries on my behalf and to contact me by email about the results. I understand ReclaimWealth is not a government agency, that I may search these registries and file any claim myself at no cost, and that ReclaimWealth does not guarantee that any unclaimed funds exist. I agree to the <a href="${p}terms.html">Terms</a> and <a href="${p}privacy.html">Privacy Policy</a>.</span>
              </label>
              <label class="consent" for="${id}-phone-consent">
                <input type="checkbox" id="${id}-phone-consent" data-consent-phone />
                <span>
                  I agree to receive calls and texts from ReclaimWealth at the number I provided, including messages sent using an automatic telephone dialing system or prerecorded voice.
                  <span class="optional-tag">Optional</span>
                  <small>Consent is not a condition of purchase. Message frequency varies. Message and data rates may apply. Reply STOP to opt out.</small>
                </span>
              </label>
            </div>

            <p class="err" tabindex="-1" role="alert"></p>

            <button type="submit" class="btn btn-green btn-lg" style="width:100%">Email My Free Search Plan</button>
            <p class="fineprint">🔒 We never sell your personal information.</p>
          </form>
        </div>
      </div>
    </section>`;
}

function applyLeadCapture(html, page) {
  if (page.section === "root") return html; /* root pages are hand-written */

  /* This section is owned by the build script: re-render it in place on
     every run so template changes reach pages converted by earlier runs. */
  const existing = html.match(/ {0,4}<section class="lead-band">[\s\S]*?<\/section>/);
  const band = existing || html.match(/ {0,4}<section class="band">[\s\S]*?<\/section>/);
  if (!band) return html;

  /* Reuse the page-specific headline and copy already on the page. */
  const h2 = band[0].match(/<h2[^>]*>([\s\S]*?)<\/h2>/);
  const p = existing
    ? band[0].match(/<p class="lead-copy">([\s\S]*?)<\/p>/)
    : band[0].match(/<p[^>]*>([\s\S]*?)<\/p>/);

  const heading = h2 ? h2[1].trim() : "Find the retirement money you left behind";
  const copy = p
    ? p[1].trim()
    : "Tell us where you've worked and we'll map every registry worth checking, in about a minute.";

  return html.replace(band[0], leadSection(page, heading, copy));
}

/* ---- 4. root-absolute → relative --------------------------------------- */
function relativize(html, page) {
  const p = page.prefix;

  return html.replace(/(href|src)="\/([^"]*)"/g, (match, attr, rest) => {
    /* "//cdn.example.com/x" is protocol-relative and must not be touched. */
    if (rest.startsWith("/")) return match;

    let target = rest;

    if (target === "") {
      target = "index.html";                       /* href="/" → home */
    } else if (target.startsWith("#")) {
      target = "index.html" + target;              /* href="/#search" */
    } else if (target.endsWith("/")) {
      target += "index.html";                      /* href="/states/" */
    }

    return `${attr}="${p}${target}"`;
  });
}

/* ---- dead icon references ---------------------------------------------- */
/* favicon.ico and apple-touch-icon.png were referenced but never existed. */
function dropMissingIcons(html) {
  return html
    .replace(/\s*<link rel="icon" href="[^"]*favicon\.ico"[^>]*>/g, "")
    .replace(/\s*<link rel="apple-touch-icon"[^>]*>/g, "");
}

/* ---- run ---------------------------------------------------------------- */
let changed = 0;
const all = pages();

for (const page of all) {
  const before = fs.readFileSync(page.file, "utf8");
  let html = before;

  html = applyNav(html, page);
  html = applyFooter(html, page);
  html = applyLeadCapture(html, page);
  html = dropMissingIcons(html);
  html = relativize(html, page);

  if (html !== before) {
    changed++;
    if (!CHECK_ONLY) fs.writeFileSync(page.file, html);
  }
}

console.log(
  `${CHECK_ONLY ? "Would update" : "Updated"} ${changed} of ${all.length} pages` +
    ` (${all.filter((p) => p.section !== "root").length} interior, ` +
    `${all.filter((p) => p.section === "root").length} top level).`
);
