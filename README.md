# Automated Micro-Influencer Outreach System

End-to-end pipeline for the EDXSO AI Engineer Intern assignment:

```
Discovery -> Data Collection -> Filtering -> Enrichment -> AI Personalization -> Review -> Sending -> Tracking
 (YouTube API)  (SQLite)       (rules)     (public text)  (Claude + fallback)  (human)   (SMTP)     (CSV log)
```

**Niche implemented:** Fashion & Beauty (skincare, makeup, haircare, fashion styling, thrifting, fragrance, nails).
**Data integrity:** nothing in this repo is pre-filled. Every influencer, metric and email comes from a live
API run on your machine. Missing data is written as `Not Found` / `Not Available` - never guessed.

---

## 1. Technology stack
| Layer | Tool |
|---|---|
| Language | Python 3.10+ |
| Discovery / metrics | YouTube Data API v3 (official, free quota) |
| Storage | SQLite (`data/outreach.db`) - swappable via `outreach/db.py` |
| LLM personalization | Anthropic Claude API (model set in `.env`, default `claude-sonnet-5-5`) |
| Sending | SMTP (Gmail App Password works) with dry-run / test-redirect / live modes |
| Config | `config.yaml` (campaign) + `.env` (secrets) |
| Tests | pytest (13 tests, incl. an end-to-end run with a fake API) |

## 2. Project layout
```
main.py                 CLI (one command per pipeline stage + run-all)
config.yaml             brand, queries, thresholds, keyword taxonomy
outreach/youtube.py     API client: retries, backoff, quota detection
outreach/discovery.py   search -> channel details -> recent-video metrics
outreach/analysis.py    keyword scoring, engagement (median), themes
outreach/filtering.py   PASS/FAIL + human-readable reasons
outreach/enrichment.py  email/social extraction (+ optional site lookup)
outreach/personalization.py  LLM prompt, validation/retry, rule-based fallback
outreach/sender.py      approval gate, dedupe, SMTP, manual-DM workflow
outreach/exporter.py    all CSV / Markdown deliverables
tests/                  unit + end-to-end tests (synthetic fixtures only)
```

## 3. Setup
```bash
cd influencer-outreach
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

```

## 4. Running it
**One command (demo):**
```bash
python main.py run-all --auto-approve            # dry-run send (nothing is transmitted)
```
**Stage by stage (recommended - this includes the human review step):**
```bash
python main.py discover                 # ~60 in-range channels; ~2,000-3,000 API quota units
python main.py filter                   # PASS/FAIL with reasons
python main.py enrich                   # add --scrape-websites to also check creators' own sites
python main.py generate                 # add --no-llm to force fallback
python main.py review                   # open outputs/personalized_messages.md and read them
python main.py approve --all            # or: approve --ids UCxxxx UCyyyy
python main.py send                     # DRY RUN (default): logs "simulated", transmits nothing
python main.py send --test-recipient you@gmail.com   # REAL SMTP, but every mail goes to YOU
python main.py send --live              # real sends to influencers (only after you're happy)
python main.py dm-queue                 # manual Instagram DM queue
python main.py mark-dm-sent --ids UCxxxx            # log a DM you sent by hand
python main.py export                   # refresh all CSVs
```
Re-running is safe: discovery skips known channels, generation skips existing messages, sending never
duplicates (see section 9).

## 5. Outputs (in `outputs/`)
| File | Assignment item |
|---|---|
| `influencer_dataset.csv` | **B** - all discovered influencers: Name, Platform, Followers, Engagement, Niche, Email, Profile URL, Content Theme, Status, Pass/Fail reason |
| `shortlisted_enriched.csv` | Section 3 - full enrichment fields incl. email source |
| `personalized_messages.csv` / `.md` | **C** - email + Instagram DM per shortlisted influencer |
| `outreach_tracker.csv` | **D** - Influencer, Email, Message Generated, Sent, Date, Status, Mode, DM status |
| `dm_queue.csv` | manual Instagram DM workflow |
| `logs/pipeline.log` | full run log |

## 6. Discovery methodology
* Official **YouTube Data API v3** only - no scraping of YouTube or Instagram.
* For each query in `config.yaml` (e.g. "skincare routine", "thrift haul fashion") the system runs both a
  **channel search** and a **video search** (video search surfaces small creators who rank for niche topics),
  collects unique channel IDs, then batch-fetches channel stats.
* For channels inside 5k-100k subscribers it pulls the last 10 uploads + their statistics.
* Stops when `target_in_range` (default 60) micro-influencers are found, so >=50 qualified-size profiles
  are in the dataset. Out-of-range channels are stored too (status FAIL + reason) so filtering is auditable.
* Quota: a search call costs 100 units; the free daily quota is 10,000. On quota exhaustion the run stops
  gracefully and keeps everything fetched so far.

## 7. Filtering & classification logic (`outreach/filtering.py`)
A channel is **PASS** only if all criteria hold; otherwise **FAIL** with every failing reason listed.
| Criterion | Rule (configurable) |
|---|---|
| Platform | YouTube |
| Follower count | 5,000-100,000 (hidden counts fail) |
| Engagement rate | median over recent videos of `(likes+comments)/views`, >= 1.5%; needs >=3 videos with visible likes, otherwise "unavailable" -> FAIL (never guessed) |
| Category / content relevance | keyword evidence across channel bio, tags, titles, descriptions for 8 Fashion & Beauty sub-niches; score >= 6 (each keyword capped at 5 hits so stuffing can't game it). The top-scoring sub-niche becomes the **primary niche** |
| Brand fit | blocked terms (gambling, adult, etc.) fail |
| Activity | last upload within 90 days |
| Geography | optional allow-list; unknown country is a *note*, not a fail |
| Audience demographics | not used for filtering - not publicly available (see limitations) |

## 8. Profile enrichment (`outreach/enrichment.py`)
For every shortlisted influencer: name, platform, profile URL, followers, engagement, niche, content themes
(top sub-niches + most common video tags), **contact email**, Instagram / TikTok / website, audience fields.
* **Email extraction only** from text the creator published: channel description first, then an address that
  repeats in >=2 recent video descriptions (a single hit could be a sponsor, so it's ignored). Handles
  `name [at] gmail [dot] com`. Optional `--scrape-websites` checks the creator's own site (`/`, `/contact`,
  `/about`), honoring `robots.txt`. Source of each email is recorded in `Email Source`.
* No email -> `Not Found`. Nothing is generated, inferred or pattern-guessed.
* Audience age / gender -> `Not Available`; audience geography -> creator's country labelled as a *proxy*.

## 9. AI personalization (`outreach/personalization.py`)
**Signals per creator:** greeting name, primary niche + themes, last 3 video titles, most-viewed recent video,
bio excerpt (tone hint), followers, engagement, country, and a **collaboration angle** chosen by rule:
engagement >= 6% -> affiliate; < 15k followers -> gifted-product UGC (barter); beauty niches -> UGC +
sponsored; else paid sponsorship.

**Model & prompt:** Claude (`ANTHROPIC_MODEL`) with a system prompt that forbids invented facts, hype words
and placeholders, plus a user prompt containing the brand JSON and creator-signals JSON and asking for JSON
`{subject, email_body, instagram_dm}`. See `SYSTEM_PROMPT` / `USER_PROMPT` in the file.

**Guardrails:** output is validated in code (email 60-90 words incl. greeting/sign-off; DM 15-30 words; no
placeholders). On failure the model is re-prompted with the exact problem (3 attempts). If the API key is
missing or the API fails, a **rule-based generator** builds messages from the same real signals using
sentence variants selected per creator, with automatic length fitting. Every message records which
generator produced it (`Generated By`).

## 10. Sending layer (`outreach/sender.py`)
* **Review gate:** messages start as `pending_review`; only `approved` ones are sent.
* Selects PASS influencers with a syntactically valid email; others are skipped and shown as
  "email Not Found (DM only)" in the tracker.
* **Modes:** `dry_run` (default, simulated), `--test-recipient` (real SMTP but redirected to your own inbox,
  subject tagged `[TEST for ...]`), `--live`.
* Adds an opt-out footer, respects `data/suppression.txt`, enforces `daily_cap` and `delay_seconds`.
* **Duplicate prevention (3 layers):** (1) lookup by email *and* channel before sending, (2) simulated/test
  runs never block a later real send but do block repeats of themselves, (3) a SQLite *unique partial index*
  makes a second real send to the same address impossible.
* **Failures** (SMTP errors) are logged as `failed` and retried on the next run.
* **Instagram DMs** are not sent automatically - Meta's API doesn't allow cold outreach DMs and bypassing
  that would breach platform rules. Instead `dm-queue` produces `dm_queue.csv` (profile link + DM text) and
  `mark-dm-sent` logs a hand-sent DM in the tracker.

## 11. Error handling
Retries with exponential backoff on 429/5xx/network errors; quota exhaustion stops cleanly; a failing
channel/video/enrichment/LLM call is logged and skipped without aborting the batch; hidden likes/subscribers
become `None`, not zeros; all outputs use `Not Found` / `Not Available` markers.

## 12. Limitations 
* **Email coverage will be partial.** YouTube's "business email" button sits behind a CAPTCHA and is not in
  the API, so only emails creators write publicly are found (expect roughly 15-40% of shortlisted creators).
  Others stay `Not Found`; use the DM queue for them. Raise `target_in_range` or add queries for more emails.
* **No Instagram/TikTok metrics** - their APIs don't offer public creator search; only handles that creators
  link on YouTube are collected.
* **Audience demographics are unavailable** publicly (needs creator-authorised YouTube Analytics).
* YouTube rounds public subscriber counts to 3 significant figures.
* Niche classification is keyword-based (transparent, but not semantic); engagement is per-view, which is
  the only like/comment base public for YouTube and differs from follower-based engagement elsewhere.
* Gmail SMTP limits (~500/day) apply; use a dedicated sending domain with SPF/DKIM for real campaigns.

## 13. Scaling to 500+ influencers
Discovery is quota-bound, not CPU-bound: request a quota increase or rotate API keys/projects; batch
(50 IDs per call) is already used. Swap SQLite for Postgres in `db.py`; run `enrich` and `generate` in a
worker pool/queue (they are per-record and idempotent); replace SMTP with SES/SendGrid via the `transport`
interface (`send(to, subject, body)`); wrap each CLI stage as an n8n/Make node or cron job.
