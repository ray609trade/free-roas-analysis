# ReclaimWealth — Lost 401(k) &amp; IRA Finder

Helps people locate and reclaim retirement accounts left behind at former
employers, using the official public registries. Static site, no build
dependencies, generated from a structured knowledge base.

**Domain:** raysmgmt.com · **Brand:** ReclaimWealth

---

## Quick start

```bash
node build/build.js      # generate state + recordkeeper pages, sitemap, llms.txt
node build/qa.js         # run the QA gate (41 checks)
node build/qa.js --links # additionally verify every external URL (needs open network)
python3 -m http.server 8080
```

## Structure

```
index.html          landing page + finder form
about.html          business identity, how we make money (E-E-A-T)
terms.html          Terms of Service — subscriptions, TCPA, finder-law posture
privacy.html        Privacy Policy — CCPA rights, retention, consent records
disclosures.html    plain-language disclosures (liability centrepiece)
404.html

data/               THE KNOWLEDGE BASE — everything generates from here
├── states.json          state unclaimed property programs + finder-law posture
├── registries.json      federal/national registries: covers / excludes / requires
├── administrators.json  15 major recordkeepers + participant lookup URLs
├── faq.json             canonical Q&A (single source for copy AND schema)

build/
├── build.js        generates states/, find/, sitemap.xml, llms.txt
└── qa.js           correctness + legal + quality gate

states/             GENERATED — 10 state guides + hub
find/               GENERATED — 15 recordkeeper guides + hub
assets/site.css     shared styles
assets/site.js      form, validation, consent capture, results
```

**The data files are the product.** The HTML is one rendering of them. Adding a
state or recordkeeper is a JSON edit plus a rebuild — never a template edit.

## Configuration

| What | Where |
|---|---|
| Site URL | `SITE_URL` in `build/build.js`, `siteUrl` in `assets/site.js` |
| Lead email / endpoint | `CONFIG` in `assets/site.js` |
| Excluding a state | `excludedStates` in `data/states.json` (e.g. `["CA"]`) |
| Consent wording | `CONSENT_TEXT` in `assets/site.js` — **bump `CONSENT_VERSION` when editing** |

### Lead capture

Leads POST to FormSubmit and arrive by email. **One-time activation:** the first
submission sends a confirmation email to `info@rmgcredit.com` — click the link
once and delivery is automatic thereafter. Every lead carries consent evidence:
timestamp, page URL, user agent, consent version, and the verbatim consent text.

## Compliance built in

- **TCPA** — phone/text consent is a separate, optional, unchecked box with
  autodialer, frequency, rates and STOP/HELP disclosures; consent is never a
  condition of service; consent evidence is captured with every lead.
- **Subscriptions** — Terms cover auto-renewal, cancellation, refunds, price-change
  notice, and express affirmative consent (California ARL is the strictest bar and
  the one the language targets).
- **Finder/locator law** — flat fees only, never contingency; free-claim disclosure
  on every page; per-state posture recorded in `states.json`; geographic-limits
  clause in the Terms.
- **FTC §5** — QA gate blocks urgency/scarcity language and any "we found your
  money" claim made before verification.
- **Privacy** — CCPA/CPRA rights, retention schedule, no-sale statement.

The QA gate enforces these on every build, so a regression fails the build rather
than reaching production.

## Before launch

1. Replace bracketed placeholders: legal entity name, mailing address, state of
   formation, and venue (in `terms.html`, `privacy.html`, `disclosures.html`,
   `about.html`).
2. **Run `node build/qa.js --links` on an open network** — URL verification is
   blocked in restricted CI/sandbox environments and reports as unverified.
3. Have counsel review all four documents plus per-state finder registration.
4. Click the FormSubmit activation link; send one real test lead.
5. Verify Search Console + Bing; submit `sitemap.xml`.
6. Add `assets/og-image.png` (1200×630) — referenced but not yet created.

## Legal

ReclaimWealth is an independent search-assistance service, not a government
agency, and is not affiliated with the DOL, PBGC, IRS, any state treasury, or any
retirement plan or financial institution. Every referenced registry is free to
search and any claim can be filed directly at no cost. Not financial, tax, or
legal advice.
