# Checkpoint — v0.1.0 "launch-ready pending configuration"

**Commit:** `761bb81` · **Branch:** `claude/lost-401k-ira-finder-atcm3k` · **PR:** #1
**Date:** 2026-07-25

Restore this exact state at any time:

```bash
git checkout 761bb81
# or, to tag it locally and push (tag pushes work from your machine):
git tag -a v0.1.0-launch-ready 761bb81 -m "Launch-ready pending configuration"
git push origin v0.1.0-launch-ready
```

Verify the checkpoint is intact:

```bash
node build/build.js && node build/qa.js   # expect: 54 passed, 0 failed
```

---

## State at this checkpoint

**44 pages · QA gate 54 checks, all passing · no console errors, no horizontal
overflow at 375 / 1024 / 1280 px**

### Complete

| Area | What exists |
|---|---|
| Landing page | Corrected sourced statistics ($2.1T / 31.9M / $66,691), free-claim disclosures, pricing tiers |
| TCPA | Split consent — service required, phone/text optional; versioned verbatim consent evidence captured with every lead |
| Legal | `terms.html`, `privacy.html`, `disclosures.html`, `about.html` — subscription/auto-renewal, finder-law posture, CCPA rights, retention schedule |
| Checkout | `checkout.html` captures express affirmative consent (ROSCA + state ARL) before Stripe; `thank-you.html` for the success URL |
| Knowledge base | `data/` — states, registries, administrators, faq, knowledge |
| Generated | 10 state guides, 15 recordkeeper guides, 7 learn guides, 18-term glossary, 3 hubs, `sitemap.xml`, `llms.txt` |
| Integrations | Stripe Payment Links + CRM webhook seams in `assets/integrations.js` |
| SEO / GEO | JSON-LD throughout, canonicals, OG image, `robots.txt` with AI-crawler allowances, favicon, manifest |

### Requires owner action before going live

1. **Stripe + CRM** — paste two Payment Link URLs and one webhook URL into
   `assets/integrations.js`. Nothing else needs editing.
2. **Bracketed placeholders** — legal entity name, mailing address, state of
   formation, court venue in `terms.html`, `privacy.html`, `disclosures.html`,
   `about.html`. The mailing address is also required in commercial email
   under CAN-SPAM.
3. **`node build/qa.js --links` from an open network.** ~40 external URLs
   (state unclaimed-property programs, federal registries, recordkeepers) are
   **unverified** — this sandbox blocks `.gov` and state domains, so every
   request 403s at the proxy. This is the highest-priority remaining check: a
   wrong official link on a page about recovering money is a credibility
   failure.
4. **Counsel review** of all four legal documents, plus per-state finder /
   locator registration decisions for states where you charge fees.
5. **Repo rename** — `free-roas-analysis` → `401k-income-finder`, or create the
   new repo (the GitHub app used in this session is 403-blocked from creating
   repositories).

### Deferred by decision

- States beyond the 10 pilot (CA, TX, FL, NY, PA, IL, OH, GA, NC, MI) — a
  `data/states.json` edit plus a rebuild whenever you want them.
- Recordkeepers beyond the current 15 — same pattern.
- Real search backend, payment webhooks, CRM two-way sync — separate builds.

### Known constraints

- **Serving all states.** `excludedStates` in `data/states.json` is empty.
  California is included deliberately: its ARL is the strictest bar, and the
  subscription language targets it, which covers the other states.
- **No contingency fees.** Flat pricing only, and the free-claim disclosure
  appears on every page — both are enforced by the QA gate and both matter for
  state finder-law exposure.
- **Brand ≠ domain.** `raysmgmt.com` carries no topical signal for lost-401(k)
  queries. `SITE_URL` in `build/build.js` and `siteUrl` in `assets/site.js` are
  the only two places to change if you move to a branded domain.
