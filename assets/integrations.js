/* =========================================================================
   ReclaimWealth — integrations layer

   ONE FILE TO EDIT when connecting your payment processor and CRM.
   Nothing else in the site needs to change.

   Everything here is designed for a static site with no backend:
   - Payments use Stripe Payment Links (no secret key, no server).
   - CRM uses an outbound webhook (works with Zapier, Make, GoHighLevel,
     HubSpot, Salesforce, or your own endpoint).

   SECURITY: never put a Stripe SECRET key (sk_...) in this file. It ships to
   every visitor's browser. Only Payment Link URLs and publishable keys
   (pk_...) belong in client-side code.
   ========================================================================= */

var INTEGRATIONS = {

  /* ---------------------------------------------------------------------
     1. PAYMENTS — Stripe Payment Links
     ---------------------------------------------------------------------
     Setup (about 10 minutes, no code):
       1. Stripe Dashboard → Product catalogue → add your products:
            "7-Day Full Access"   one-time      $19   (NO recurring price)
            "30-Day Full Access"  recurring     $29 / month
       2. For each product: Payment links → Create link.
       3. In the link settings, set the success URL to:
            https://raysmgmt.com/thank-you.html?plan=7-day
            https://raysmgmt.com/thank-you.html?plan=30-day
       4. Paste each link URL below.

     Leave a URL empty and that plan's button shows a "contact us" fallback
     instead of a broken checkout.

     COMPLIANCE NOTE: we capture express affirmative consent to the
     subscription terms on /checkout.html BEFORE redirecting to Stripe, and
     log it as evidence. Do not bypass that page by linking straight to
     Stripe from the pricing table — the consent record is what demonstrates
     compliance with ROSCA and state automatic-renewal laws.
     ------------------------------------------------------------------- */
  payments: {
    provider: "stripe-payment-links",
    links: {
      "7-day": "",    // e.g. "https://buy.stripe.com/xxxxxxxxxxxx"
      "30-day": ""    // e.g. "https://buy.stripe.com/yyyyyyyyyyyy"
    },
    /* Shown to the user if a link is not configured yet. */
    fallbackEmail: "info@rmgcredit.com"
  },

  /* ---------------------------------------------------------------------
     2. CRM — outbound webhook
     ---------------------------------------------------------------------
     Set `webhookUrl` to any endpoint that accepts a JSON POST. Examples:

       Zapier          Zap → "Webhooks by Zapier" → Catch Hook → copy URL
       Make.com        Scenario → "Custom webhook" → copy URL
       GoHighLevel     Automation → Trigger: Inbound Webhook → copy URL
       HubSpot         Workflow → Webhook trigger, or use the Forms API URL
       Salesforce      Web-to-Lead endpoint, or a Flow HTTP callout
       Your own API    any HTTPS endpoint

     The payload shape is documented in crmPayloadShape() below so you can
     map fields in your CRM without guessing.

     `mode: "cors"` posts JSON normally. If your endpoint does not send CORS
     headers (Zapier and Make do; some do not), switch to "no-cors" — the
     request still delivers, you just cannot read the response.
     ------------------------------------------------------------------- */
  crm: {
    webhookUrl: "",          // e.g. "https://hooks.zapier.com/hooks/catch/123456/abcdef/"
    mode: "cors",            // "cors" | "no-cors"
    /* Optional static fields merged into every payload — useful for routing
       leads to the right pipeline or owner in your CRM. */
    extraFields: {
      source: "ReclaimWealth website",
      pipeline: "Lost Account Search"
    }
  },

  /* ---------------------------------------------------------------------
     3. ANALYTICS — optional
     ---------------------------------------------------------------------
     Paste your GA4 measurement ID or Plausible domain. Leave empty to run
     without analytics; site.js degrades silently either way.
     ------------------------------------------------------------------- */
  analytics: {
    ga4MeasurementId: "",    // e.g. "G-XXXXXXXXXX"
    plausibleDomain: ""      // e.g. "raysmgmt.com"
  }
};

/* =========================================================================
   Plan catalogue — the single source of truth for prices shown to users.

   These MUST match what you configure in Stripe. A mismatch between the
   price shown here and the price charged is a deceptive-pricing problem, so
   update both together.
   ========================================================================= */
var PLANS = {
  "7-day": {
    id: "7-day",
    name: "7-Day Full Access",
    price: "$19",
    priceNumeric: 19,
    billing: "one-time",
    recurring: false,
    summary: "Your complete findings report plus seven days of access to work through them.",
    includes: [
      "Full 50-state unclaimed property sweep run for you",
      "Form 5500 trace on every employer you list",
      "Complete findings report: every match, every plan administrator, every contact",
      "Prepared claim packet with your document checklist",
      "Seven days of access to your report",
      "Does not auto-renew — nothing further is charged"
    ]
  },
  "30-day": {
    id: "30-day",
    name: "30-Day Full Access",
    price: "$29",
    priceNumeric: 29,
    billing: "per month",
    recurring: true,
    renewalPeriod: "month",
    summary: "Everything in the 7-day report, plus ongoing monitoring and help getting the claim filed.",
    includes: [
      "Everything in 7-Day Full Access",
      "Monthly re-sweep as new property is reported to the states",
      "Alerts when a new match appears in your name",
      "Claim support by email and phone",
      "Help preparing and checking your claim forms",
      "Cancel anytime online, in one click"
    ]
  }
};

/* =========================================================================
   CRM delivery
   ========================================================================= */

/* Documents the exact payload your CRM will receive. Kept as a function so
   it is easy to log in the console while mapping fields. */
function crmPayloadShape() {
  return {
    event: "lead_created | purchase_consent",
    submittedAt: "ISO-8601 timestamp",
    name: "string", firstName: "string", lastName: "string",
    email: "string", phone: "string (may be empty)",
    states: "comma-separated string",
    formerEmployers: "comma-separated string",
    plan: "free | 7-day | 30-day",
    consent: {
      version: "string",
      page: "URL where consent was given",
      userAgent: "string",
      serviceConsent: "YES | NO",
      serviceConsentText: "verbatim text shown",
      phoneConsent: "YES | NO",
      phoneConsentText: "verbatim text shown, or (not given)",
      subscriptionConsent: "YES | NO | (n/a)",
      subscriptionConsentText: "verbatim text shown, or (n/a)"
    },
    extraFields: "whatever you set in INTEGRATIONS.crm.extraFields"
  };
}

/* Send a payload to the configured CRM. Always resolves — a CRM outage must
   never block the user or lose the lead (site.js keeps a local copy). */
function sendToCrm(payload) {
  var cfg = INTEGRATIONS.crm;
  if (!cfg.webhookUrl) return Promise.resolve({ ok: false, reason: "not-configured" });

  var body = JSON.stringify(
    Object.assign({}, payload, cfg.extraFields || {})
  );

  var opts = {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body
  };
  if (cfg.mode === "no-cors") opts.mode = "no-cors";

  return fetch(cfg.webhookUrl, opts)
    .then(function (r) { return { ok: cfg.mode === "no-cors" ? true : r.ok }; })
    .catch(function () { return { ok: false, reason: "network" }; });
}

/* =========================================================================
   Payment redirect
   ========================================================================= */

/* Returns the Stripe Payment Link for a plan, with the buyer's email
   prefilled so Stripe's receipt reaches the right person and the CRM record
   can be matched back to the payment.

   `clientRef` is written to Stripe's client_reference_id, which appears on
   the payment in your dashboard — use it to reconcile a Stripe payment with
   the lead record in your CRM. */
function paymentUrlFor(planId, email, clientRef) {
  var base = (INTEGRATIONS.payments.links || {})[planId];
  if (!base) return null;
  var sep = base.indexOf("?") === -1 ? "?" : "&";
  var qs = [];
  if (email) qs.push("prefilled_email=" + encodeURIComponent(email));
  if (clientRef) qs.push("client_reference_id=" + encodeURIComponent(clientRef));
  return qs.length ? base + sep + qs.join("&") : base;
}

/* Stable-ish reference for reconciliation. Not a security token — it only
   needs to be unique enough to match a payment to a lead. */
function makeClientRef(email) {
  var stamp = Date.now().toString(36);
  var seed = (email || "anon").split("@")[0].replace(/[^a-z0-9]/gi, "").slice(0, 12);
  return ("rw-" + seed + "-" + stamp).toLowerCase();
}

/* =========================================================================
   Analytics loader — injects GA4 or Plausible only if configured.
   ========================================================================= */
(function loadAnalytics() {
  var a = INTEGRATIONS.analytics;
  if (a.ga4MeasurementId) {
    var s = document.createElement("script");
    s.async = true;
    s.src = "https://www.googletagmanager.com/gtag/js?id=" + encodeURIComponent(a.ga4MeasurementId);
    document.head.appendChild(s);
    window.dataLayer = window.dataLayer || [];
    window.gtag = function () { window.dataLayer.push(arguments); };
    window.gtag("js", new Date());
    window.gtag("config", a.ga4MeasurementId);
  } else if (a.plausibleDomain) {
    var p = document.createElement("script");
    p.defer = true;
    p.setAttribute("data-domain", a.plausibleDomain);
    p.src = "https://plausible.io/js/script.js";
    document.head.appendChild(p);
  }
})();
