# ReclaimWealth — Lost 401(k) &amp; IRA Finder

**Find the retirement money you left behind at old jobs.**

Millions of Americans have forgotten or abandoned 401(k), IRA, and pension
accounts from past employers. ReclaimWealth is a lead-generating search-assistance
site: a visitor enters their name and a few identifying details, and the tool
guides them to the official registries where lost retirement accounts are
reported — while capturing them as a lead so your team can help them claim.

> **Live demo:** enable GitHub Pages on this repo (Settings → Pages → Deploy from
> branch → `main` / root) and the site is served at
> `https://YOURUSERNAME.github.io/free-roas-analysis/`.

---

## What it does

1. **Intake** — Visitor submits name, email, phone, states worked, and former
   employers via the hero search form.
2. **Search assist** — The tool builds a personalized set of deep links into the
   real registries (see below) and displays them in a results panel.
3. **Lead capture** — The submission is saved and (optionally) POSTed to your
   backend, or sent to your intake email, so you can follow up and help the
   person claim their funds.

## Where it searches (all legitimate, public sources)

| Registry | Purpose |
|---|---|
| **DOL Retirement Savings Lost &amp; Found** | Federal database of plans looking for former participants |
| **National Registry of Unclaimed Retirement Benefits** | Employer-reported unclaimed accounts |
| **PBGC Unclaimed Pensions** | Unclaimed traditional pension benefits |
| **MissingMoney.com** | State unclaimed property / escheated funds |
| **DOL Form 5500 / EFAST** | Trace a former employer's current plan administrator |

## Why it's built this way (important)

There is **no lawful database that returns a private person's 401(k) balance by
name** — those balances are protected. The honest, valuable, and legal business
model is a **search assistant**: help people navigate the official registries
where *lost/abandoned* accounts are reported, then help them verify their
identity with the institution and claim. The UI is written to reflect this
accurately (see the FAQ and disclaimer in `index.html`) so the service stays
trustworthy and compliant.

## Tech

- Single self-contained `index.html` — no build step, no dependencies.
- Vanilla HTML/CSS/JS; deploys anywhere static (GitHub Pages, Netlify, Vercel, S3).
- Fully responsive, mobile-first.

## Lead capture (wired &amp; live)

Leads are delivered by email through **[FormSubmit.co](https://formsubmit.co)** —
no account or API key required. Config lives in the `CONFIG` object near the
bottom of `index.html`:

```js
const CONFIG = {
  leadEmail: "info@rmgcredit.com",                          // mailto fallback + display
  leadEndpoint: "https://formsubmit.co/ajax/info@rmgcredit.com" // live email backend
};
```

**⚠️ One-time activation (required before emails arrive):** the *first* time
someone submits the form, FormSubmit sends a confirmation email to
`info@rmgcredit.com`. Click the link in it once. After that, every submission
is emailed to you automatically (name, email, phone, states, former employers).

- Change the address by editing the email at the end of `leadEndpoint`.
- To switch to another backend later (Formspree, Basin, your own API), just
  replace the `leadEndpoint` URL — the code POSTs JSON either way.
- Every lead is also cached in the visitor's browser `localStorage` (`rw_leads`)
  as a safety net if the network request fails.

## Legal pages

- `privacy.html` — Privacy Policy (what's collected, how it's used, no selling of data)
- `terms.html` — Terms of Service (search-assistant model, no guarantees, disclaimers)

Both are linked from the footer and the consent checkbox. They're solid
starting templates — **have a lawyer review them before you go live.**

## Next steps / roadmap

- Wire `leadEndpoint` to a real backend (Formspree/Basin or a small API + DB).
- Add a CRM/email automation on new leads.
- Add server-side Form 5500 (EFAST) lookups to pre-fill plan-administrator matches.
- Privacy policy + terms pages; consent logging for compliance.

## Legal

ReclaimWealth is an independent search-assistance service and is **not**
affiliated with the DOL, PBGC, IRS, any state treasury, or any plan or financial
institution. The underlying registries are free for consumers to search
directly. Not financial, tax, or legal advice. See the disclaimer in the site
footer.
