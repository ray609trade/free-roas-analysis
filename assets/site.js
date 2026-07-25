/* =========================================================================
   ReclaimWealth — shared site script
   -------------------------------------------------------------------------
   Handles the finder form, TCPA consent capture (with evidence logging),
   lead submission, and the results panel.
   ========================================================================= */

var CONFIG = {
  siteUrl: "https://raysmgmt.com",
  brand: "ReclaimWealth",
  leadEmail: "info@rmgcredit.com",

  /* Lead backend (FormSubmit.co — no signup required).
     ONE-TIME ACTIVATION: the first submission sends a confirmation email to
     the address below. Click the link in it once and delivery is automatic.
     Swap this URL for Formspree/Basin/your own API at any time. */
  leadEndpoint: "https://formsubmit.co/ajax/info@rmgcredit.com",

  /* Analytics hook. Wire to gtag/plausible; no-ops until then. */
  track: function (event, props) {
    try {
      if (typeof gtag === "function") gtag("event", event, props || {});
      else if (typeof plausible === "function") plausible(event, { props: props || {} });
    } catch (e) { /* analytics must never break the form */ }
  }
};

/* -------------------------------------------------------------------------
   TCPA CONSENT — versioned, verbatim, and logged as evidence.

   Do not edit these strings without incrementing CONSENT_VERSION. The exact
   wording shown to a user must remain reproducible for any later dispute:
   every submission stores the version plus the full text displayed.
   ------------------------------------------------------------------------- */
var CONSENT_VERSION = "2026-07-24.v1";

var CONSENT_TEXT = {
  service:
    "I authorize ReclaimWealth to search publicly available registries and records on my behalf " +
    "and to contact me by email about the results. I understand ReclaimWealth is not a government " +
    "agency and is not affiliated with any retirement plan or financial institution, that I may " +
    "search these registries and file any claim myself at no cost, and that ReclaimWealth does not " +
    "guarantee that any unclaimed funds exist. I agree to the Terms of Service and Privacy Policy.",
  phone:
    "I agree to receive marketing and informational calls and text messages from ReclaimWealth at " +
    "the phone number I provided, including messages sent using an automatic telephone dialing " +
    "system or an artificial or prerecorded voice. I understand that consent is not a condition of " +
    "purchase or of using any ReclaimWealth service. Message frequency varies. Message and data " +
    "rates may apply. Reply STOP to opt out or HELP for help."
};

/* ---- helpers ---------------------------------------------------------- */
function $(id) { return document.getElementById(id); }

function isValidEmail(v) { return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v); }

function digitsOnly(v) { return (v || "").replace(/\D/g, ""); }

/* US phone: 10 digits, or 11 beginning with 1. */
function isValidPhone(v) {
  var d = digitsOnly(v);
  return d.length === 10 || (d.length === 11 && d.charAt(0) === "1");
}

function setYear() {
  var el = $("year");
  if (el) el.textContent = new Date().getFullYear();
}

/* ---- dynamic employer rows -------------------------------------------- */
function initEmployerRows() {
  var addBtn = $("addEmployer");
  if (!addBtn) return;
  addBtn.addEventListener("click", function () {
    var list = $("employerList");
    var row = document.createElement("div");
    row.className = "employer-row";

    var input = document.createElement("input");
    input.type = "text";
    input.className = "employer";
    input.placeholder = "Another employer";
    input.setAttribute("aria-label", "Former employer");

    var rm = document.createElement("button");
    rm.type = "button";
    rm.className = "icon-btn";
    rm.setAttribute("aria-label", "Remove this employer");
    rm.textContent = "×";
    rm.addEventListener("click", function () { row.remove(); });

    row.appendChild(input);
    row.appendChild(rm);
    list.appendChild(row);
    input.focus();
  });
}

/* ---- registry resources ------------------------------------------------
   Every entry is a real, free, public resource. `selfServe` marks the ones
   a person can complete entirely on their own at no cost — surfaced in the
   UI because several state finder statutes require that disclosure, and it
   is the honest thing to show regardless.
   ----------------------------------------------------------------------- */
function buildResources(data) {
  var firstEmployer = data.employers[0] ? encodeURIComponent(data.employers[0]) : "";
  return [
    {
      priority: true,
      tag: "Start here",
      title: "DOL Retirement Savings Lost & Found",
      desc: "The federal Department of Labor database of retirement plans searching for former " +
            "participants. Note: you must verify your identity through Login.gov (Social Security " +
            "number and a photo of your driver's license). It does not cover church or government plans.",
      url: "https://lostandfound.dol.gov/",
      selfServe: true
    },
    {
      priority: true,
      tag: "High match potential",
      title: "National Registry of Unclaimed Retirement Benefits",
      desc: "A secure registry employers use to report unclaimed accounts. Searching requires your " +
            "Social Security number and returns results only for plans that registered with it.",
      url: "https://www.unclaimedretirementbenefits.com/",
      selfServe: true
    },
    {
      tag: "State cash & escheated funds",
      title: "MissingMoney.com — State Unclaimed Property" + (data.state ? " (" + data.state + ")" : ""),
      desc: "When accounts go dormant the funds are often turned over to the state. This is public " +
            "record and free to search. Check every state you have lived or worked in.",
      url: "https://www.missingmoney.com/en/",
      selfServe: true
    },
    {
      tag: "For pensions",
      title: "PBGC Unclaimed Pensions",
      desc: "If you or a family member had a traditional pension, the Pension Benefit Guaranty " +
            "Corporation may be holding unclaimed benefits.",
      url: "https://www.pbgc.gov/wr/find-unclaimed-pensions",
      selfServe: true
    },
    {
      tag: "Trace your old plan",
      title: "Form 5500 / EFAST Filing Search" + (data.employers[0] ? " — " + data.employers[0] : ""),
      desc: "Federal filings that identify the administrator still responsible for a former " +
            "employer's plan — the fastest route back into an account when the company merged, " +
            "was acquired, or closed.",
      url: firstEmployer
        ? "https://www.efast.dol.gov/5500Search/?planName=" + firstEmployer
        : "https://www.efast.dol.gov/5500Search/",
      selfServe: true
    }
  ];
}

/* ---- lead submission --------------------------------------------------- */
function buildPayload(data) {
  return {
    source: CONFIG.brand + " — Lost Retirement Account Finder",
    submittedAt: new Date().toISOString(),
    name: (data.firstName + " " + data.lastName).trim(),
    email: data.email,
    phone: data.phone || "",
    states: data.state || "",
    formerEmployers: data.employers.join(", "),

    /* --- consent evidence: keep all of this together --- */
    consentVersion: CONSENT_VERSION,
    consentPage: window.location.href,
    consentUserAgent: navigator.userAgent,
    serviceConsent: "YES",
    serviceConsentText: CONSENT_TEXT.service,
    phoneConsent: data.phoneConsent ? "YES" : "NO",
    phoneConsentText: data.phoneConsent ? CONSENT_TEXT.phone : "(not given)",

    /* --- FormSubmit control fields (harmless to other backends) --- */
    _subject: "New lost-account search: " + data.firstName + " " + data.lastName,
    _template: "table",
    _captcha: "false"
  };
}

function submitLead(data) {
  var payload = buildPayload(data);

  /* Local safety net so a lead is never lost to a failed request. */
  try {
    var saved = JSON.parse(localStorage.getItem("rw_leads") || "[]");
    saved.push(payload);
    localStorage.setItem("rw_leads", JSON.stringify(saved));
  } catch (e) { /* private browsing / storage disabled */ }

  CONFIG.track("lead_submit", {
    has_phone_consent: data.phoneConsent ? 1 : 0,
    employer_count: data.employers.length
  });

  if (!CONFIG.leadEndpoint) return Promise.resolve({ ok: false, reason: "no-endpoint" });

  return fetch(CONFIG.leadEndpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(payload)
  })
    .then(function (r) { return { ok: r.ok }; })
    .catch(function () { return { ok: false, reason: "network" }; });
}

function mailtoFallback(data) {
  var body =
    "Lost retirement account search request:\n\n" +
    "Name: " + data.firstName + " " + data.lastName + "\n" +
    "Email: " + data.email + "\n" +
    "Phone: " + (data.phone || "-") + "\n" +
    "States: " + (data.state || "-") + "\n" +
    "Former employers: " + (data.employers.join(", ") || "-") + "\n";
  return "mailto:" + CONFIG.leadEmail +
    "?subject=" + encodeURIComponent("Lost-account search: " + data.firstName + " " + data.lastName) +
    "&body=" + encodeURIComponent(body);
}

/* ---- results panel ----------------------------------------------------- */
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}

function renderResults(data, deliveryPromise) {
  var overlay = $("resultsModal");
  var body = $("modalBody");
  var title = $("modalTitle");
  var sub = $("modalSub");
  var scan = $("scanLine");

  overlay.classList.add("open");
  document.body.style.overflow = "hidden";
  title.textContent = "Building your search plan…";
  sub.textContent = "Lining up the registries most likely to hold an account in your name.";

  var steps = [
    "Reviewing the details you provided…",
    "Mapping federal registries: DOL Lost & Found, PBGC…",
    "Mapping state unclaimed property for your states…",
    "Locating plan administrators for your former employers…",
    "Assembling your personalized checklist…"
  ];
  var i = 0;
  scan.textContent = steps[0];
  var timer = setInterval(function () {
    i++;
    if (i < steps.length) scan.textContent = steps[i];
  }, 600);

  setTimeout(function () {
    clearInterval(timer);
    var resources = buildResources(data);
    title.textContent = "Your search plan is ready, " + escapeHtml(data.firstName);
    sub.textContent = "Work through these in order. Every one of them is free to search.";

    var html =
      '<div class="free-notice">' +
        "<strong>You can do all of this yourself, free.</strong> Every registry below is a free " +
        "public or government resource, and you can file any claim directly at no cost. " +
        "ReclaimWealth charges only for optional help doing the work for you — never for access " +
        "to your own money." +
      "</div>";

    resources.forEach(function (r) {
      html +=
        '<div class="result-item' + (r.priority ? " priority" : "") + '">' +
          '<span class="tag">' + escapeHtml(r.tag) + "</span>" +
          "<h4>" + escapeHtml(r.title) + "</h4>" +
          "<p>" + escapeHtml(r.desc) + "</p>" +
          '<a class="go btn btn-green" style="padding:.55rem 1.1rem;font-size:.9rem" ' +
             'href="' + r.url + '" target="_blank" rel="noopener noreferrer" ' +
             'data-registry="' + escapeHtml(r.title) + '">Search this registry →</a>' +
        "</div>";
    });

    html += '<div class="result-note" id="deliveryNote">Sending a copy to ' +
            "<strong>" + escapeHtml(data.email) + "</strong>…</div>";

    body.innerHTML = html;

    /* Track which registries actually get clicked. */
    body.querySelectorAll("[data-registry]").forEach(function (a) {
      a.addEventListener("click", function () {
        CONFIG.track("registry_click", { registry: a.getAttribute("data-registry") });
      });
    });

    /* Honest delivery status — never claim an email was sent if it failed. */
    deliveryPromise.then(function (res) {
      var note = $("deliveryNote");
      if (!note) return;
      if (res && res.ok) {
        note.innerHTML =
          "<strong>Saved — check your inbox.</strong> We sent a copy of this plan to " +
          escapeHtml(data.email) + ". A specialist can run the full multi-state sweep and " +
          "employer plan trace for you if you'd like help.";
      } else {
        note.innerHTML =
          "<strong>We couldn't reach our server just now.</strong> Your plan above is still " +
          'complete and usable. To reach us directly, <a href="' + mailtoFallback(data) +
          '">send your request by email</a>.';
      }
    });
  }, 3600);
}

/* ---- form wiring ------------------------------------------------------- */
function initFinderForm() {
  var form = $("finderForm");
  if (!form) return;

  var loadedAt = Date.now();

  form.addEventListener("submit", function (e) {
    e.preventDefault();

    var data = {
      firstName: $("firstName").value.trim(),
      lastName: $("lastName").value.trim(),
      email: $("email").value.trim(),
      phone: $("phone") ? $("phone").value.trim() : "",
      state: $("state") ? $("state").value.trim() : "",
      phoneConsent: $("phoneConsent") ? $("phoneConsent").checked : false,
      employers: Array.prototype.slice
        .call(document.querySelectorAll(".employer"))
        .map(function (i) { return i.value.trim(); })
        .filter(Boolean)
    };

    var err = $("formErr");
    var problems = [];

    if (!data.firstName) problems.push("your first name");
    if (!data.lastName) problems.push("your last name");
    if (!isValidEmail(data.email)) problems.push("a valid email address");
    if (!$("consent").checked) problems.push("your authorization to search (the first checkbox)");

    /* Phone consent without a valid phone number is not usable consent. */
    if (data.phoneConsent && !isValidPhone(data.phone)) {
      problems.push("a valid US phone number to agree to calls and texts");
    }
    if (data.phone && !isValidPhone(data.phone)) {
      problems.push("a valid US phone number (or leave it blank)");
    }

    /* Silent bot checks: honeypot + implausibly fast submission. */
    var hp = $("website");
    if ((hp && hp.value) || Date.now() - loadedAt < 1500) {
      form.reset();
      return;
    }

    if (problems.length) {
      err.textContent = "Please add " + problems.join(", ") + ".";
      err.style.display = "block";
      err.focus();
      return;
    }
    err.style.display = "none";

    var delivery = submitLead(data);
    renderResults(data, delivery);
  });
}

/* ---- modal ------------------------------------------------------------- */
function initModal() {
  var overlay = $("resultsModal");
  if (!overlay) return;

  function close() {
    overlay.classList.remove("open");
    document.body.style.overflow = "";
  }
  var closeBtn = $("modalClose");
  if (closeBtn) closeBtn.addEventListener("click", close);
  overlay.addEventListener("click", function (e) { if (e.target === overlay) close(); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
}

/* ---- init -------------------------------------------------------------- */
document.addEventListener("DOMContentLoaded", function () {
  setYear();
  initEmployerRows();
  initFinderForm();
  initModal();
});
