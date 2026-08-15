# CLAUDE.md

Guidance for AI assistants working in this repository.

## Read this first: the repo does not run

Every file here except `README.md` and `LICENSE` was committed through the GitHub web
UI by pasting **shell commands instead of file contents**. `package.json`, `index.html`,
and `file structure` each literally begin with `cat > … << 'EOF'` and end with `EOF`.

So:

- **`package.json` is not valid JSON.** It is a shell heredoc that would *write* a
  valid `package.json` if executed. `npm install` cannot parse it.
- **`index.html` is not HTML.** Same wrapper problem.
- **`src/` and `public/` do not exist**, and neither does `src/App.js` — the file
  `index.html` points its `<script type="module">` at.
- There is no application code anywhere in the repository. No React components, no
  calculator logic.

Nothing here builds, installs, serves, or deploys in its current state. Do not report
otherwise, and do not attempt `npm install` or `npm start` expecting them to work.

## What it's supposed to be

A **Free ROAS (Return on Ad Spend) Analysis Calculator** — a lead-generation landing
page for AILeadVoice.com. Intended behavior, per the README draft inside
`Update README.md`:

1. Visitor enters current performance: monthly ad spend, leads, conversion rates.
2. The page computes current vs. potential ROAS against benchmarks for 6 industry
   verticals, and projects monthly/annual revenue increases.
3. A CTA books a discovery call via Calendly (`https://calendly.com/raymgnt/website`).

The intended stack, from the heredoc'd `package.json`: **Create React App**
(`react-scripts` 5.0.1), React 18, `lucide-react` for icons, **Tailwind via the CDN
`<script>`** (not a build-time dependency), deployed to **GitHub Pages** via
`gh-pages -d build`.

## File inventory

| File | What it actually is |
|---|---|
| `README.md` | Real, valid Markdown. Two lines — the only accurate file. |
| `Update README.md` | Heredoc that would overwrite `README.md` with the full version. Note the space in the filename. |
| `package.json` | Heredoc wrapper around the intended CRA manifest. Invalid JSON. |
| `index.html` | Heredoc wrapper around the intended CRA HTML shell. |
| `file structure` | Setup notes: `mkdir src public`, `touch` the three files. Never executed. |
| `LICENSE` | MIT. Valid. |

## Fixing it

The repair is mechanical: for each affected file, strip the leading
`cat > <name> << 'EOF'` line and the trailing `EOF` line, leaving the real content.
Then create `src/App.js` with the actual calculator, and `public/` with a `favicon.ico`
(`index.html` references `%PUBLIC_URL%/favicon.ico`).

`Update README.md` and `file structure` are scaffolding notes, not source files. Once
their content has been applied, they should be deleted rather than left to rot —
confirm with the user before removing them.

Placeholders that must be filled in before anything ships:

- `homepage` in `package.json` is `https://YOURUSERNAME.github.io/free-roas-analysis`
  — GitHub Pages deploys will break until the real username is substituted.
- The README's "Live Demo" link has the same `YOURUSERNAME` placeholder.
- `"author": "Your Name"`.

Also worth flagging to the user rather than silently deciding: the intended stack is
dated. `react-scripts` 5.0.1 (Create React App) is deprecated and unmaintained, and
Tailwind via `cdn.tailwindcss.com` is explicitly not for production. Vite plus a real
Tailwind build is the conventional replacement — but that's a stack change, so ask
first.

## Conventions

There is no established code style, no linter, no formatter, no tests, and no CI —
because there is no code yet. If you are the one writing the first application code,
you are setting the conventions; keep them conventional for the chosen stack and say
in your summary what you established.

## Related

The sibling repo in this workspace, **`ray609trade/v0-scorecard-rmg`**, is a working
Next.js implementation of a very similar funnel (marketing hero → lead-capture form →
promised report) for a different brand. If you need a working reference for the shape
this project is reaching for, look there — but do not copy its v0-generated
boilerplate wholesale, and note it uses a completely different stack.
