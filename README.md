# ReclaimWealth — Lost 401(k) &amp; IRA Finder

Helps people locate and reclaim retirement accounts left behind at former
employers, using the official public registries. Static site, no build
dependencies, generated from a structured knowledge base.

**Domain:** raysmgmt.com · **Brand:** ReclaimWealth
**Operated by:** Ray Management Group, LLC — 16 Hamilton Street, Allentown, NJ · 609-453-8990

---

## Quick start

```bash
node build/build.js      # generate state + recordkeeper pages, sitemap, llms.txt
node build/qa.js         # run the QA gate (58 checks)
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
checkout.html       plan terms + express affirmative consent (pre-Stripe)
thank-you.html      post-payment confirmation (Stripe success URL)
404.html

data/               THE KNOWLEDGE BASE — everything generates from here
├── states.json          state unclaimed property programs + finder-law posture
├── registries.json      federal/national registries: covers / excludes / requires
├── administrators.json  15 major recordkeepers + participant lookup URLs
├── faq.json             canonical Q&A (single source for copy AND schema)
├── knowledge.json       guides + glossary (powers /learn/)

build/
├── build.js        generates states/, find/, sitemap.xml, llms.txt
└── qa.js           correctness + legal + quality gate

states/             GENERATED — 10 state guides + hub
find/               GENERATED — 15 recordkeeper guides + hub
learn/              GENERATED — 7 guides + glossary + hub
assets/site.css     shared styles
assets/site.js      form, validation, consent capture, results
assets/integrations.js  ← EDIT THIS to connect Stripe + CRM + analytics
assets/og-image.png     1200x630 social card
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

## Connecting your payment processor and CRM

Everything lives in **`assets/integrations.js`** — one file, no other changes needed.

### Stripe (about 10 minutes, no code, no backend)

1. Stripe Dashboard → **Product catalogue** → create two products:
   - `7-Day Full Access` — **one-time $19** (do NOT add a recurring price)
   - `30-Day Full Access` — **recurring monthly $29**
2. For each: **Payment links → Create link**.
3. Set each link's success URL to:
   - `https://raysmgmt.com/thank-you.html?plan=7-day`
   - `https://raysmgmt.com/thank-you.html?plan=30-day`
4. Paste the two URLs into `INTEGRATIONS.payments.links` in `assets/integrations.js`.

Until they are set, `/checkout.html` shows a "contact us" fallback rather than a
broken button — nothing breaks in the meantime.

**Never put a Stripe secret key (`sk_...`) in this repo.** It ships to every
visitor's browser. Payment Link URLs and publishable keys (`pk_...`) only. The QA
gate fails the build if a secret key pattern appears in client-side code.

**Do not link the pricing table straight to Stripe.** Paid tiers must route through
`/checkout.html`, which captures the express affirmative consent to the renewal
terms that ROSCA and state auto-renewal laws require. The QA gate enforces this.

### CRM (any provider)

Set `INTEGRATIONS.crm.webhookUrl` to an endpoint that accepts a JSON POST:

| Provider | Where to get the URL |
|---|---|
| Zapier | Zap → Webhooks by Zapier → Catch Hook |
| Make.com | Scenario → Custom webhook |
| GoHighLevel | Automation → Trigger: Inbound Webhook |
| HubSpot | Workflow webhook, or Forms API |
| Salesforce | Web-to-Lead endpoint, or Flow HTTP callout |
| Your own API | any HTTPS endpoint |

Call `crmPayloadShape()` in the browser console to see the exact field structure
before you map anything. Two events fire: `lead_created` (free search submitted)
and `purchase_consent` (checkout consent given, before Stripe redirect). Each
carries the full consent record.

`clientReferenceId` is passed to Stripe as `client_reference_id` and appears on the
payment in your dashboard — use it to reconcile a Stripe charge to a CRM lead.

If your webhook endpoint does not send CORS headers, set `mode: "no-cors"`. A CRM
outage never blocks a user: the lead is still stored locally and the results still
render.

### Analytics

Set `ga4MeasurementId` or `plausibleDomain` in the same file. Events emitted:
`lead_submit`, `registry_click`, `purchase_complete`.

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

## Continuous integration

Two workflows run in GitHub Actions:

- **`qa.yml`** — on every push and PR: rebuilds the site, fails if the committed
  generated pages are out of date, then runs the 54-check QA gate. Compliance
  regressions (missing free-claim disclosure, stripped TCPA language, a leaked
  secret key) fail the build instead of reaching the site.
- **`link-check.yml`** — weekly and whenever `data/` changes: verifies every
  external URL still resolves and opens/updates an issue if any break. GitHub's
  runners have open network access, so this covers the URL verification that
  cannot run in a restricted sandbox.

The build is deterministic — dates come from `_meta` fields in `data/`, never the
system clock — so a rebuild only changes files when content actually changed.
**When you edit `data/` or a template, run `node build/build.js` and commit the
generated output**, or CI will fail the sync check.

## Before launch

1. **Add the ZIP code** to the mailing address (currently "16 Hamilton Street,
   Allentown, NJ" with no ZIP). CAN-SPAM requires a valid physical postal
   address in commercial email, so complete it before sending any.
2. **Run `node build/qa.js --links` on an open network** — URL verification is
   blocked in restricted CI/sandbox environments and reports as unverified.
3. Have counsel review all four documents plus per-state finder registration.
4. Click the FormSubmit activation link; send one real test lead.
5. Verify Search Console + Bing; submit `sitemap.xml`.
6. Connect Stripe Payment Links and your CRM webhook (see above).

## Legal

ReclaimWealth is an independent search-assistance service, not a government
agency, and is not affiliated with the DOL, PBGC, IRS, any state treasury, or any
retirement plan or financial institution. Every referenced registry is free to
search and any claim can be filed directly at no cost. Not financial, tax, or
legal advice.
