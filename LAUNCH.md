# ReclaimWealth — Step-by-Step Launch Guide

Everything below takes you from "code is done" to "leads are landing in your
inbox." Work top to bottom; each step says **what to do**, **where**, and
**how to know it worked**.

Legend: ⏱ time · 🔴 blocking (must do before taking traffic) · 🟡 do in week one · 🟢 nice to have

---

## Status: what's already built

| Area | State |
|---|---|
| Landing page, copy, brand system | ✅ Done |
| 3-step interactive search wizard (validation, progress, draft-save) | ✅ Done |
| Animated registry "scan" + personalized, checkable action plan | ✅ Done |
| Lead capture with real success/failure handling + local backup | ✅ Done |
| Mobile nav, sticky mobile CTA, scroll reveals, counters | ✅ Done |
| Accessibility: skip link, focus trap, aria-live, reduced-motion | ✅ Done |
| Privacy Policy, Terms, 404 page | ✅ Done |
| SEO: meta, Open Graph, Twitter card, FAQ schema, sitemap, robots | ✅ Done |
| Favicon + social share image | ✅ Done |
| **Email activation, domain, analytics, legal review** | ⬅ **Your turn — steps below** |

---

## 🔴 Step 1 — Turn on lead delivery (5 min)

Leads are emailed through [FormSubmit.co](https://formsubmit.co) — no account needed —
but it stays dormant until you confirm the address once.

1. Open the live site (or `index.html` locally in a browser).
2. Fill in the wizard with **your own** name and email and submit.
3. Check `info@rmgcredit.com` for a "Confirm your email" message from FormSubmit.
4. Click the confirmation link. **One time only.**
5. Submit the form again.

✅ **Verify:** the second submission arrives as a formatted table email with name,
former name, email, phone, states, work period, and former employers.

> Changing the destination address: edit the email at the end of `leadEndpoint`
> in the `CONFIG` block near the bottom of `index.html`, then re-confirm once.

**If email never arrives:** the results panel will show an amber
"we couldn't reach our server" pill with a mailto fallback, and every lead is
still cached in the visitor's browser under `localStorage.rw_leads`. That's your
signal the endpoint is wrong or unconfirmed.

---

## 🔴 Step 2 — Publish the site (10 min)

**GitHub Pages (free, fastest):**

1. Merge this branch into `main`.
2. Repo → **Settings → Pages → Source: Deploy from a branch → `main` / `/ (root)`**.
3. Wait ~60 seconds, then open `https://<your-username>.github.io/free-roas-analysis/`.

**Or Netlify / Vercel:** "Add new site → Import from Git" → pick the repo →
no build command, publish directory `/`. Both give you HTTPS and a deploy URL
in about two minutes.

✅ **Verify:** the page loads over `https://`, the hero fonts render, and the
wizard advances between steps.

---

## 🔴 Step 3 — Point a real domain at it (20 min + DNS wait)

A `github.io` URL undercuts a money-recovery pitch. Use a real domain.

1. Buy the domain (Namecheap / Cloudflare / Google Domains).
2. **GitHub Pages:** Settings → Pages → Custom domain → enter it → Save.
   Then add these DNS records at your registrar:
   - `A` → `185.199.108.153`, `185.199.109.153`, `185.199.110.153`, `185.199.111.153`
   - `CNAME` `www` → `<your-username>.github.io`
   (Netlify/Vercel: just follow their "Add domain" wizard.)
3. Tick **Enforce HTTPS** once the certificate is issued.
4. Update the placeholder domain in **three** files:
   - `index.html` — `<link rel="canonical">` and both `og:url` / image URLs
   - `sitemap.xml` — all three `<loc>` entries
   - `robots.txt` — the `Sitemap:` line

✅ **Verify:** `https://yourdomain.com` loads with a padlock, and
`https://yourdomain.com/sitemap.xml` returns XML.

---

## 🔴 Step 4 — Have a lawyer read the legal pages (1 hour of their time)

`privacy.html` and `terms.html` are solid, honest starting templates — they are
**not** a substitute for review. Lead-generation around retirement funds touches:

- **TCPA / consent** if you ever call or text leads (the consent checkbox text matters);
- **CAN-SPAM** for the follow-up emails you send;
- **State unclaimed-property rules** — several states cap or license "finder" fees.
  If you plan to charge a percentage of recovered funds, this is the one to ask about first.
- **CCPA/CPRA** if you get California traffic.

✅ **Verify:** counsel signs off, and you update the "Last updated" date on both pages.

---

## 🟡 Step 5 — Analytics + conversion tracking (15 min)

You can't optimize what you can't see. Add one snippet right before `</head>` in
`index.html` — [Plausible](https://plausible.io) (privacy-friendly, no cookie
banner needed) or GA4:

```html
<script defer data-domain="yourdomain.com" src="https://plausible.io/js/script.js"></script>
```

Then track the funnel. Add this inside the `submit` handler in `index.html`,
right after `const data = collect();`:

```js
if (window.plausible) plausible('Lead Submitted');
```

Worth watching: hero → step 1 start, step 1 → step 2 → step 3 drop-off, and
submissions. If step 2 (employers) is where people quit, shorten it.

✅ **Verify:** your own visit shows in the realtime dashboard.

---

## 🟡 Step 6 — Set up the follow-up you promised (30 min)

The results panel tells every visitor "a specialist will reach out." Make that true.

1. **Autoresponder (same day).** FormSubmit can send an instant reply — add
   `_autoresponse: "Thanks! Your ReclaimWealth search plan is attached..."` to the
   payload object in `buildPayload()`.
2. **A real inbox routine.** Decide who works the leads and how fast — 24 hours
   is the promise the copy implies.
3. **A simple pipeline.** A Google Sheet is enough to start: Zapier/Make can watch
   the inbox and append each lead. Move to a CRM when volume justifies it.

✅ **Verify:** submit a test lead, confirm the autoresponse arrives and the row lands.

---

## 🟡 Step 7 — Pre-launch QA pass (30 min)

Run through this list on a real phone, not just a resized browser window.

- [ ] Wizard: try to advance step 1 with an empty name → inline error appears
- [ ] Bad email on step 3 → inline error; fix it → error clears on typing
- [ ] Consent unchecked → cannot submit
- [ ] Back button preserves what you typed; refreshing the page restores the draft
- [ ] "Add another employer" and the × remove button both work
- [ ] State chips toggle and sync with the text field
- [ ] Submit → scan animation → plan renders with your first name in the title
- [ ] Plan links each open the right registry in a new tab
- [ ] "Done" checkboxes persist after closing and reopening the modal
- [ ] "Copy my plan" puts a readable list on the clipboard
- [ ] Mobile: hamburger menu opens/closes; sticky bottom CTA appears on scroll
- [ ] Modal closes with ✕, Escape, and clicking the backdrop; Tab stays trapped inside
- [ ] Footer links to Privacy and Terms both load
- [ ] Share the URL in Slack/iMessage → the gold-and-green social card appears

✅ **Verify:** run [PageSpeed Insights](https://pagespeed.web.dev/) — aim for 90+
on mobile. Being a single static file, it should pass comfortably.

---

## 🟢 Step 8 — Then drive traffic

Only after steps 1–4. In rough order of cost-effectiveness:

1. **Organic content** — "how to find a 401(k) from an old job" is a real,
   high-intent search. Add 3–5 articles as `/blog/*.html` reusing this stylesheet.
2. **Facebook/Meta ads** — the 45+ audience over-indexes for forgotten accounts.
   Creative: the `$1.65 trillion` stat, one clear CTA.
3. **Google Search ads** — expensive keywords; start with a tiny daily cap and
   exact-match phrases like "find old 401k from previous employer."
4. **Partnerships** — HR outplacement firms, unions, and estate attorneys all
   sit on top of exactly this problem.

---

## 🟢 Step 9 — Improvements worth building next

| Idea | Why it pays |
|---|---|
| Server-side Form 5500 (EFAST) lookup | Return actual plan-administrator names instead of a search link — a real "wow" moment |
| Emailed PDF of the plan | Gives you a second touchpoint and a reason to follow up |
| Progress dashboard via magic link | Lets people come back to their checklist; big retention win |
| A/B test the hero headline | Cheap, and the headline is doing most of the work |
| Spanish translation | Large, under-served audience for unclaimed-property services |

---

## Where things live (for whoever picks this up next)

```
index.html      Entire site: markup, styles, wizard, lead capture. CONFIG is at
                the bottom of the <script> block — that's the only thing most
                changes need to touch.
privacy.html    Privacy Policy
terms.html      Terms of Service
404.html        Branded not-found page
favicon.svg     Tab icon
og-image.png    1200×630 social share card
robots.txt      Crawler rules + sitemap pointer
sitemap.xml     Three URLs; update the domain before submitting to Search Console
```

No build step, no dependencies, no framework. Edit the HTML, commit, push — it's live.
