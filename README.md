# Clawdit: Skill Audit Toolkit

Clawdit is a small toolkit for pulling OpenClaw skills from multiple sources, auditing `SKILL.md` instructions with an LLM, and visualizing risk in a React dashboard.

This repo is intentionally simple: a few Python collectors + one Vite frontend.

## What’s in this repo

- `fetch_clawhub_skills.py`
  - Pulls skills from a ClawHub-style API (`/api/v1/skills` + `/api/v1/download?slug=`).
  - Downloads ZIPs.
  - Extracts `SKILL.md`.
  - Audits with a selectable LLM provider (`minimax` default, `openai` optional).
  - Saves audit output incrementally after each skill attempt.

- `fetch_skills_sh_skills.py`
  - Scrapes `skills.sh` list pages, resolves skill pages, builds ZIP artifacts with `SKILL.md`, and can run the same audit flow.

- `fetch_skillsmp_skills.py`
  - Pulls from SkillsMP API-style endpoints with pagination/retries, builds ZIP artifacts with `SKILL.md`, and can run the same audit flow.

- `src/` + Vite/Tailwind config
  - React dashboard for browsing `skill_audit_report.json`.
  - Includes search, filtering, and sorting.

## Requirements

- Python `3.10+`
- Node `18+` and npm
- `MINIMAX_API_KEY` (audit mode default) or `OPENAI_API_KEY` (if using `--llm-provider openai`)
- `OPENAI_API_KEY` (only needed for audit mode)
- Convex account/deployment (only needed for Convex DB mode)

## Quick start

1. Install frontend deps:

```bash
npm install
```

2. Run the dashboard:

```bash
npm run dev
```

3. Open the local Vite URL and inspect loaded data from:

- `public/skill_audit_report.json` (autoload)
- or upload a JSON file from the UI

## Convex database mode (Python + Convex)

This repo now supports storing audit records in Convex and loading them from the dashboard.

1. Set up Convex in this repo (first time only):

```bash
npx convex dev
```

2. Install the Python Convex client:

```bash
pip install convex
```

3. Sync your local report into Convex:

```bash
python3 sync_audit_report_to_convex.py \
  --input skill_audit_report.json \
  --convex-url https://YOUR-DEPLOYMENT.convex.cloud \
  --clear-first
```

4. Point the frontend to Convex via `.env.local`:

```bash
VITE_CONVEX_URL=https://YOUR-DEPLOYMENT.convex.cloud
# Optional override (default already matches this repo)
VITE_CONVEX_QUERY_PATH=skillAudits:list
```

When `VITE_CONVEX_URL` is set, the UI loads from Convex first. If that fails, it falls back to `public/skill_audit_report.json`.

## Core workflow

1. Fetch skills list.
2. Download each skill ZIP with delay.
3. Extract `SKILL.md`.
4. Audit via LLM.
5. Save report incrementally.
6. Visualize in frontend.

This lets you stop/restart long runs without losing prior audit entries.

## Alert email notifications (critical/high)

All pullers now support AgentMail alert emails when an audit result contains `critical`/`high` findings (or matching risk level).

Configure via flags (or equivalent env vars):

- `--alert-email-to` (`ALERT_EMAIL_TO`)
- `--agentmail-api-key` (`AGENTMAIL_API_KEY`)
- `--agentmail-inbox-id` (`AGENTMAIL_INBOX_ID`, default `gracefulbird586@agentmail.to`)
- `--alert-levels` (`ALERT_LEVELS`, default `critical,high`)
- `--alert-email-subject-prefix` (`ALERT_EMAIL_SUBJECT_PREFIX`, default `[Puller Alert]`)

Example:

```bash
OPENAI_API_KEY=... \
ALERT_EMAIL_TO=you@example.com \
AGENTMAIL_API_KEY=... \
AGENTMAIL_INBOX_ID=gracefulbird586@agentmail.to \
python3 fetch_clawhub_skills.py \
  --download-all-from-list \
  --audit-skill-md
```

## Collector usage

## 1) ClawHub-compatible collector

Single list pull:

```bash
python3 fetch_clawhub_skills.py \
  --base-url https://wry-manatee-359.convex.site \
  --limit 100 \
  --output clawhub_skills.json
```

Full sequential scan + audit:

```bash
MINIMAX_API_KEY=... python3 fetch_clawhub_skills.py \
  --base-url https://wry-manatee-359.convex.site \
  --limit 100 \
  --output clawhub_skills.json \
  --download-all-from-list \
  --download-dir skill_zips \
  --delay 1.5 \
  --audit-skill-md \
  --audit-output skill_audit_report.json
```

Single slug test:

```bash
MINIMAX_API_KEY=... python3 fetch_clawhub_skills.py \
  --base-url https://wry-manatee-359.convex.site \
  --skip-list-fetch \
  --download-slug gifgrep \
  --download-dir skill_zips \
  --audit-skill-md \
  --audit-output skill_audit_report.json
```

Use GitHub repo source mode (for `openclaw/skills` style repos):

```bash
OPENAI_API_KEY=... python3 fetch_clawhub_skills.py \
  --github-repo-url https://github.com/openclaw/skills \
  --github-ref main \
  --github-skills-path skills \
  --limit 12000 \
  --download-all-from-list \
  --download-dir skill_zips \
  --delay 0.2 \
  --audit-skill-md \
  --audit-output skill_audit_report.json
```

Single name match test:

```bash
python3 fetch_clawhub_skills.py \
  --base-url https://wry-manatee-359.convex.site \
  --limit 100 \
  --download-name "gifgrep"
```

## 2) skills.sh collector

```bash
MINIMAX_API_KEY=... python3 fetch_skills_sh_skills.py \
  --limit 100 \
  --output skills_sh_skills.json \
  --download-all-from-list \
  --download-dir skill_zips \
  --delay 1.5 \
  --audit-skill-md \
  --audit-output skill_audit_report.json
```

## 3) skillsmp.com collector

```bash
MINIMAX_API_KEY=... python3 fetch_skillsmp_skills.py \
  --category backend \
  --sort-by recent \
  --max-pages 5 \
  --output skillsmp_skills.json \
  --download-all-from-list \
  --download-dir skill_zips \
  --delay 1.5 \
  --audit-skill-md \
  --audit-output skill_audit_report.json
```

## Output files

- `clawhub_skills.json`, `skills_sh_skills.json`, `skillsmp_skills.json`
  - Raw/discovered skill entries per source.

- `skill_zips/*.zip`
  - Downloaded or generated ZIP artifacts.

- `skill_audit_report.json`
  - Main audit dataset used by the dashboard.
  - Updated after each processed attempt in audit mode.
- `sync_audit_report_to_convex.py`
  - Pushes JSON audit records into Convex (`skillAudits` table) via upsert.
- `convex/schema.js`, `convex/skillAudits.js`
  - Convex schema/functions for storing and querying audit records.

- `public/skill_audit_report.json`
  - Frontend autoload copy.

## Frontend features

- Search by slug, summary, or finding titles
- Filter by risk and status
- Sort by:
  - risk
  - finding count
  - name
  - source (`skills.sh` first / `clawhub.ai` first)
- Source pill logic:
  - slug contains `/` -> `skills.sh` (green)
  - otherwise -> `clawhub.ai` (orange)

## Safety notes

- Collectors do static instruction analysis (`SKILL.md`) and metadata handling.
- Do not execute unknown scripts from downloaded archives on your host machine.
- Keep delays (`--delay`) non-zero to reduce rate-limit churn.

## Troubleshooting

- `HTTP 429` / rate limits:
  - increase `--delay`
  - lower `--limit`
  - run smaller batches

- Missing LLM audits:
  - confirm `MINIMAX_API_KEY` is set (or `OPENAI_API_KEY` if `--llm-provider openai`)
  - verify network access from your runtime

- Frontend shows no data:
  - ensure `public/skill_audit_report.json` exists
  - or upload your latest report manually

## Dev notes

- Vite config: `vite.config.js`
- Tailwind config: `tailwind.config.js`
- Main app: `src/App.jsx`

If you change report schema, update `src/App.jsx` mapping logic first.
