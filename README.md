# Intelligence Flywheel

The daily intelligence pipeline: ~43 scheduled producers,
processors, and maintainers that turn web sources into vault captures, daily
briefs, and published intelligence — plus the vaultkeeper jobs that keep the
knowledge base healthy.

**This repo is the canonical version-controlled copy.** Live scripts run from
`~/.hermes/profiles/*/scripts/`; the sync protocol that keeps this repo and
the live copies consistent is defined in the companion repo
[`hermes-agentdb-memory`](https://github.com/0x-wzw/hermes-agentdb-memory)
(`AGENTDB_SYNC_PROTOCOL.md`).

> Private repo. Contains operational topology (feed URLs, Cloudflare account /
> KV namespace identifiers, host paths). **No secrets** — all API tokens are
> read from `~/.hermes/.env` at runtime and are never committed (see
> Security Notes).

---

## Repository Layout

```
intelligence-flywheel/
├── scripts-ta/                     Transformation-architect profile scripts (the flywheel)
│   ├── market-trends*.py           6 vertical producers (fashion, fnb, hospitality,
│   │                               property/infra/energy, csuite, ai-science)
│   ├── market-intelligence.py      aggregator
│   ├── policy-tracker.py           policy tracker producer
│   ├── arxiv-digest.py             arXiv paper digest producer
│   ├── thought-leader-scan.py      thought-leaders scan
│   ├── venture-news.py             venture news producer
│   ├── podcast-transcribe.py       podcast transcription producer
│   ├── leadership-insights.py      leadership insights producer
│   ├── content-pipeline.py         weekly content pipeline
│   ├── daily-intelligence-loop.py  08:00 loop closer — synthesizes the Daily Brief
│   ├── intelligence-publish.py     08:15 publisher — Notion page + Cloudflare KV mirror
│   ├── intelligence-notify.py      08:20 link-share — Telegram + Lark (Feishu)
│   ├── intelligence-prune.py/.sh   Sunday prune — retention & .trash moves
│   ├── intelligence_source_of_truth.py  canonical producer taxonomy (PRODUCER_LIST)
│   ├── intelligence-sources.yaml   source registry consumed by producers
│   ├── loop-tuning.json            loop-tuner output (synthesis parameters)
│   ├── dedup.py                    shared 6-layer URL dedup module
│   ├── trend_synthesizer.py        LLM synthesis leg (ilmu backend)
│   ├── hermes_feeds.py             shared frontmatter/frontmatter_block + authorship tag
│   ├── hermes_trend_save.py        shared same-day merge-on-write save logic
│   ├── ooda-loop.py                OODA bot orchestrator (observe→orient→decide)
│   ├── flywheel-health-check.py    weekly health aggregation
│   ├── bias-balance-check.py       weekly bias-balance report
│   ├── signal-noise.py             weekly signal-vs-noise scoring
│   ├── category-contamination-check.py  daily contamination monitor + auto-fix
│   ├── cron-health-digest.py       daily health JSON aggregator
│   ├── distillation-tracker.py     raw→distilled coverage tracker
│   ├── trends-consistency.py       trends output consistency checker
│   ├── trends-index.py             trends index rebuilder
│   ├── loop-tuner.py               weekly loop self-improvement
│   ├── vault-*.py/.sh              vault maintenance (sync, dedupe, lint, rebuild indexes)
│   └── ...                         MUI downloaders, video/podcast tooling, RAG harness,
│                                    obliviarch consolidation, MBA ingest, helpers
├── scripts-vk/                     Vaultkeeper profile scripts
│   ├── vault-daily.py              daily vault housekeeping
│   ├── vault-learn.py              00:30 vault-learn consolidation
│   ├── vault-index-cron.py         05:15 semantic index rebuild
│   ├── vault-schema-lint.py        Sunday schema-lint (compliance %)
│   ├── obliviarch-unified.py       06:00 memory consolidation
│   ├── flywheel-health-check.py    Monday flywheel health check
│   ├── memory-sync.py              Sunday memory sync (MEMORY.md + stack)
│   └── goal-ticker.py              hourly goal ticker
├── cron/
│   ├── transformation-architect-jobs.json   43-job registry (schedules, prompts, state)
│   └── vaultkeeper-jobs.json                3-job registry
├── launchd/
│   ├── ai.hermes.vault-sync.plist  2-hourly vault sync job
│   └── vault-sync.py               the script that plist runs
├── .gitignore                      secrets & artifacts exclusions
└── LICENSE                         MIT
```

---

## Cron Schedule (Live Topology)

Authoritative registries: `cron/transformation-architect-jobs.json` (43 jobs)
and `cron/vaultkeeper-jobs.json` (3 jobs). All times Asia/Kuala_Lumpur (GMT+8).

### Daily — Producers (00:00 – 07:00)

| Time | Job | Script | Output |
|---|---|---|---|
| 00:00 | csuite-leadership-trends | `market-trends-csuite.py` | `Knowledge/00-Raw/trends/` |
| 01:00 | fnb-trends | `market-trends-fnb.py` | 〃 |
| 02:00 | hospitality-trends | `market-trends-hospitality.py` | 〃 |
| 03:00 | market-trends (fashion/retail) | `market-trends.py` | 〃 |
| 03:30 | market-intelligence | `market-intelligence-fixed.py` | 〃 |
| 04:00 | property-infra-energy-trends | `market-trends-property.py` | 〃 |
| 04:30 | arxiv-digest | `arxiv-digest.py` | `Knowledge/00-Raw/research/` |
| 05:30 | ai-science-investment-trends | `market-trends-ai-science.py` | `Knowledge/00-Raw/trends/` |
| 06:15 | llm-learning-scan | `llm-learning-scan.py` | `Knowledge/00-Raw/llm-learning/` |
| 06:30 | policy-tracker | `policy-tracker.py` | `Knowledge/00-Raw/policy/` |
| 06:45 | venture-news | `venture-news-fixed.py` | `Knowledge/00-Raw/` |

### Daily — Processing & Publication (07:00 – 09:00)

| Time | Job | Script | Output |
|---|---|---|---|
| 07:00* | content-pipeline | `content-pipeline.py` (Sun) | `Knowledge/00-Raw/content-pipeline/` |
| 07:30* | podcast-transcribe | `podcast-transcribe.py` (Mon) | `Knowledge/00-Raw/podcasts/` |
| 07:30* | intelligence-prune | `intelligence-prune.sh → .py --apply` (Sun) | `.trash/` + indexes + KV dated-key cleanup |
| 07:55 | category-contamination-check | `category-contamination-check.py` | `System/_generated/category-contamination-*.md` |
| 08:00 | daily-intelligence-loop | `daily-intelligence-loop.py` | `Knowledge/20-Reports/Daily-Intelligence-Brief-YYYY-MM-DD.md` + daily note |
| 08:15 | intelligence-publish | `intelligence-publish.py` | Notion Market Research DB (1 page/day, idempotent) + KV mirror |
| 08:20 | intelligence-notify | `intelligence-notify.py` | Telegram home + Lark home chat (link + stats only) |
| 08:20 | notion-filing-chain | `notion-filing-chain.sh` | files Notion inbox into vault |
| 08:00* | bias-balance-check | `bias-balance-check.py` (Sun) | `Knowledge/20-Reports/Bias-Balance-W*.md` |
| 09:00* | signal-noise | `signal-noise.py` (Sun) | `Knowledge/20-Reports/Signal-Noise-W*.md` |

### Daily — Decision Layer & System

| Time | Job | Script | Output |
|---|---|---|---|
| 00:30 | vault-daily / vault-learn | `vault-daily.py` / `vault-learn.py` | daily note housekeeping |
| 06:00 | obliviarch-consolidation | `obliviarch-unified.py` | memory consolidation |
| 09:00 | intelligence-grilling | agent job (SIED council) | grilling-state.json rounds |
| 08:00 | action-nudge | agent job | daily nudge |
| */30 | email-attention | `email-attention.py` | unread email checks |
| 05:15 | vault-semantic-index | `vault-index-cron.py` | `~/.hermes/vault-index/` |

### Weekly / Monthly / Hourly

| Schedule | Job | Purpose |
|---|---|---|
| Sun 06:30 | schema-lint | `vault-schema-lint.py` — Schema v2 compliance % |
| Mon 06:00 | flywheel-health-check | `flywheel-health-check.py` — `System/_generated/flywheel-health-*.md` |
| Mon 10:00 | vault-weekly-brief | agent job — weekly vault brief |
| Sun 11:30 | memory-sync | `memory-sync.py` — MEMORY.md ↔ project memory files |
| Monthly 1st 08:30 | sied-kpi-report | agent job — SIED KPIs from grilling-state |
| hourly | goal-ticker | `goal-ticker.py` |
| every 2h | ai.hermes.vault-sync (launchd) | `vault-sync.py` — vault ↔ sync maintenance |

*`*` = weekly (day noted). All other times daily.*

### Paused (kept for reference, do not re-enable without review)

| Job | Paused | Reason |
|---|---|---|
| daily-insights-publish (`7ed16ae8ec2b`) | 2026-10-08 | second acquisition path; collided with the 08:15 publisher (single-source-of-truth rule) |

---

## Architecture Rules (enforced in code)

1. **Producers never publish.** All producer scripts write vault-only captures
   (Schema v2 frontmatter via `hermes_feeds.frontmatter_block()`); publication
   happens only in `intelligence-publish.py`.
2. **Single source of truth for taxonomy.** `intelligence_source_of_truth.PRODUCER_LIST`
   defines producer categories; prune, loop, publisher, and both indexes derive
   from it. Change it there first.
3. **Idempotent publish.** Publisher queries by Date+Category before creating;
   re-runs update, never duplicate.
4. **Stop-ship gate.** `publication_gate()` withholds publication when ≥3 of
   the 5 commercial verticals are empty (false-completeness guard).
5. **Link-only human touchpoint.** Telegram/Lark receive the Notion URL + counts.
6. **Retention: prune only what is ingested.** >30 days AND a synthesized brief
   exists, or >60 days absolute; everything to `.trash/`, never `rm`. The
   seen-URL / arXiv-seen dedup stores are never pruned.
7. **Never overwrite a better same-day report.** Merge-on-write logic in
   `hermes_trend_save.py`; demote/merge guards in producer writers.
8. **Authorship tags are computed, never hand-written.**
   `hermes_feeds.authorship_tag()` from `platform.system()`.

## Data Flow

```
Producers (00:00–07:00)          Processors (07:55–08:00)
  6 market-trends  ─┐             category-contamination-check
  policy-tracker   ─┤                    │ fix/remove
  arxiv-digest     ─┤                    ▼
  venture-news     ─┼──► Knowledge/00-Raw/ ──► daily-intelligence-loop (08:00)
  llm-scan         ─┤                                │
  podcast/leadership┘                                ▼
                                       Knowledge/20-Reports/Daily-Intelligence-Brief
                                                    │
                                          intelligence-publish (08:15)
                                            ├─► Notion (1 page/day, idempotent)
                                            └─► Cloudflare KV (dashboard mirror)
                                                    │
                                          intelligence-notify (08:20)
                                            ├─► Telegram (link only)
                                            └─► Lark/Feishu (link only)

Retention: intelligence-prune (Sun 07:30) — raw >30d w/ synthesized brief or >60d → .trash
Health:    per-job *-health.json → cron-health-digest (daily) → flywheel-health-check (Mon)
```

## State & Dependencies

| Item | Location | Notes |
|---|---|---|
| API credentials | `~/.hermes/.env` | NOTION_API_TOKEN, CLOUDFLARE_API_TOKEN, TELEGRAM_BOT_TOKEN, FEISHU_APP_ID/SECRET, ILMU_API_KEY, FIRECRAWL_API_KEY |
| Dedup memory | `~/.hermes/data/seen-urls.json`, `~/.hermes/arxiv-seen.json` | never prune |
| Synthesis backend | ilmu `https://api.ilmu.ai/v1` (`ilmu-glm-5.3-flash`) | override: `ILMU_SYNTH_MODEL` |
| Health files | `System/<job>-health.json` in the vault | aggregated daily + weekly |
| Publish URL log | `~/.hermes/logs/notion-daily-page-url.txt` | consumed by notify |
| Grilling state | `~/.hermes/logs/grilling-state.json` | SIED KPI source |
| Vault | `/Users/admin/Documents/Obsidian Vault/` | all writes are vault-side |

Scripts reference absolute paths (`/Users/admin/...`) — they assume this host
layout. Portability is a non-goal for the live copies; this repo is the
versioned record.

## Sync Discipline (GitHub-first)

Per the companion `AGENTDB_SYNC_PROTOCOL.md`: **edits land here first** (commit
+ push), then are applied to `~/.hermes/profiles/*/scripts/` (and mirrored to
`~/.hermes/scripts/` where the launchd/global invocations read from), never the
reverse. Ad-hoc hotfixes on the live side must be back-ported in the same
session.

> Known drift at snapshot time (2026-10-17): the global mirror
> `~/.hermes/scripts/` has 21 files differing from the profile copy and 13
> profile-only files. The **profile copy is canonical** (crons execute it via
> per-job `interpreter`/workdir). This repo snapshots the profile copies.

## Security Notes

- **No API tokens, passwords, or private keys are stored in this repo.**
  All scripts read credentials from `~/.hermes/.env` (gitignored everywhere,
  and excluded here by pattern).
- Hardcoded **Cloudflare account ID** (`fcd22318…`) and **KV namespace ID**
  (`07079ee6…`) appear in several publisher/backfill scripts. These are
  resource *identifiers*, not credentials — inert without the API token.
  The KV namespace holds published intelligence (public site
  `intelligence.teoh.my`), not secrets.
- `FIX-CLOUDFLARE-TOKEN.md` (token-rotation runbook) is intentionally **not**
  committed.
- Secret scan at commit time (2026-10-17): no `sk-`/`AKIA`/`ghp_`/`xox[bp]-`/
  PEM-key patterns; the only `cfat_` matches are documentation strings
  instructing to put the token in `.env`.

## Restoring a Machine from This Repo

1. Clone to the host; confirm `~/.hermes/.env` has the credentials above.
2. Copy `scripts-ta/*` → `~/.hermes/profiles/transformation-architect/scripts/`
   and `scripts-vk/*` → `~/.hermes/profiles/vaultkeeper/scripts/`
   (profile copies are what the registries execute).
3. Mirror to `~/.hermes/scripts/` only if global invocations are used
   (launchd `vault-sync.py` reads from there).
4. Load registries via the cron system (`hermes cron` / jobs.json import);
   verify with `cron-health-digest.py` and one manual
   `intelligence-publish.py <today>` idempotency check.
5. Install `launchd/ai.hermes.vault-sync.plist` → `~/Library/LaunchAgents/` and
   `launchctl load` it.

## Related

- [`hermes-agentdb-memory`](https://github.com/0x-wzw/hermes-agentdb-memory) —
  memory system docs + the sync protocol governing this repo
- [`obsidian-vault`](https://github.com/0x-wzw/obsidian-vault) — the authored
  vault layer this pipeline writes into
- [`hermes-os-flywheel-first-release`](https://github.com/0x-wzw/hermes-os-flywheel-first-release) —
  the *knowledge* flywheel (extract→skillify→promote→measure→inject), a
  separate system from this *intelligence* flywheel

---
*Snapshot: 2026-10-17 · 118 script/config files + 2 job registries + launchd pair · MIT*
