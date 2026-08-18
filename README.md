# AI Business Analyst Assistant

### 🔗 [Live demo →](https://ai-business-analyst.streamlit.app)


Ask a business question in plain English. The assistant writes the SQL, runs it
against a real database, decomposes what actually moved, draws the right chart,
and explains the answer the way an analyst would.

> **Q:** *Why did revenue drop in March?*
>
> **A:** Revenue decreased **17.6%** in March 2026 vs February 2026
> (1,053,271 → 867,811).
> - **Category: Electronics** down 27.6% — **92% of the total change**
> - **Channel: Online** down 21.7% — 57% of the change
> - **Customer: Bright Path Industries** down 87.4% — 16% of the change
> - **Customer: Blue Harbor Group** down 83.9% — 13% of the change
>
> *Caveats: February had 28 days vs March's 31 — per-day the change is −25.6%.
> Cancellation rate rose from 2.6% to 5.8%.*

---

## Quick start

**Zero install** — open `docs/index.html` in any browser. It's a single
self-contained file (no server, no CDN, works offline) with the full engine
ported to JavaScript.

**Full app:**

```bash
cd ai_business_analyst
python setup.py                       # installs deps, builds the DB, runs tests
streamlit run streamlit_app.py        # open the web app
```

No API key needed. The app ships with a rule-based engine so it works offline.

### Turning on the LLM

1. Open `.env` (already created, pre-configured for OpenAI).
2. Paste your key on the `OPENAI_API_KEY=` line — get one at
   [platform.openai.com/api-keys](https://platform.openai.com/api-keys).
3. `pip install openai` if you haven't already.
4. Verify:

```bash
python check_llm.py
```

That prints which backend was selected **and why** — a selection log, so a
missing package or a revoked key never looks the same as "no key configured".
It then makes one real round-trip, so a bad key fails there rather than
mid-demo.

For Claude instead, set `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY`.
`.env` is gitignored; never commit it.

Prefer the terminal?

```bash
python ask.py "Why did revenue drop in March?"
python ask.py                         # interactive REPL
```

---

## What it does

| Capability | How |
|---|---|
| Understands the business question | Intent/metric/dimension/time-window parser (`app/question.py`) |
| Generates SQL | LLM with schema + example values injected, or template engine offline (`app/sql_agent.py`) |
| Refuses to damage the database | Read-only URI connection behind a keyword/statement guard (`app/guard.py`) |
| Fetches data | SQLite, denormalised `v_sales` view (`app/database.py`) |
| Performs analysis | Contribution-to-change decomposition across 6 dimensions + sanity checks (`app/analyzer.py`) |
| Creates visualisations | Rule-based chart selection → Plotly, including a change waterfall (`app/charts.py`) |
| Generates insights | LLM writes prose from a locked FACTS block; templates offline (`app/insights.py`) |

---

## Architecture

```
                        ┌──────────────────────────────┐
   "Why did revenue     │   question.py  — parse       │
    drop in March?"  ─► │   intent · metric · period    │
                        └──────────────┬───────────────┘
                                       │  Plan
                    ┌──────────────────┴──────────────────┐
                    │                                     │
           intent = diagnostic                    everything else
                    │                                     │
        ┌───────────▼────────────┐          ┌─────────────▼────────────┐
        │ analyzer.root_cause()  │          │ sql_agent.generate()     │
        │  • metric by dimension │          │  LLM ─ or ─ template     │
        │    × 2 periods × 6 dims│          └─────────────┬────────────┘
        │  • Δ, %Δ, share of Δ   │                        │ SQL
        │  • seasonality / day-  │          ┌─────────────▼────────────┐
        │    count / cancel rate │          │ guard.validate_sql()     │
        └───────────┬────────────┘          │ read-only, single stmt   │
                    │                       └─────────────┬────────────┘
                    │                                     │
                    │                       ┌─────────────▼────────────┐
                    │                       │ database.run() → DataFrame│
                    │                       └─────────────┬────────────┘
                    │                                     │
                    └──────────────┬──────────────────────┘
                                   │  facts
                    ┌──────────────▼───────────────┐
                    │ charts.choose()  → ChartSpec │
                    │ insights.write() → narrative │
                    └──────────────┬───────────────┘
                                   │
                        Answer(sql, data, chart, insight, trace)
                                   │
                    ┌──────────────▼───────────────┐
                    │ streamlit_app.py  /  ask.py  │
                    └──────────────────────────────┘
```

Full step-by-step build walkthrough: **[docs/BUILD_GUIDE.md](docs/BUILD_GUIDE.md)**
Interview prep and resume lines: **[docs/PORTFOLIO.md](docs/PORTFOLIO.md)**

---

## The data

`python data/generate_data.py` builds `data/retail.db` — 2.5 years, ~27,000
orders, ~54,000 line items, 300 customers, 23 products across 5 categories,
4 regions, 4 channels, 3 customer segments.

A revenue shock is deliberately buried in March 2026 (Electronics collapse, two
enterprise accounts churning, EMEA and Online weakness, higher cancellations).
Nothing about that story is hard-coded in the app — the analysis engine has to
find it. That is exactly what `tests/test_analyst.py` asserts.

Schema:

```
regions ──< customers ──< orders ──< order_items >── products
                                 └── v_sales  (denormalised view, completed orders only)
```

---

## Project layout

```
ai_business_analyst/
├── app/
│   ├── config.py        env-driven settings + tiny .env loader
│   ├── database.py      read-only connection, schema introspection, execution
│   ├── guard.py         SQL safety: read-only, single statement, no DDL
│   ├── question.py      natural language → structured Plan
│   ├── llm.py           Anthropic / OpenAI / offline, one interface
│   ├── sql_agent.py     text-to-SQL + self-repair + template fallback
│   ├── analyzer.py      contribution-to-change root cause + sanity checks
│   ├── charts.py        automatic chart selection → Plotly
│   ├── insights.py      narrative generation from a locked facts block
│   └── pipeline.py      orchestrator → Answer
├── data/generate_data.py
├── tests/test_analyst.py
├── web/
│   ├── template.html    the standalone demo (engine ported to JavaScript)
│   └── build.py         inlines the data → docs/index.html
├── docs/
│   ├── index.html       ← THE LIVE PAGE, single self-contained file
│   ├── BUILD_GUIDE.md
│   └── PORTFOLIO.md
├── streamlit_app.py     chat UI + live KPI rail
├── ask.py               CLI / REPL
├── check_llm.py         verify your API key before you rely on it
└── setup.py             one-command bootstrap
```

---

## Deploying the live page

`docs/index.html` is a single file with everything inlined, which makes hosting
trivial:

```bash
python data/export_web.py    # DB  -> docs/web_data.json  (aggregate cubes)
python web/build.py          # cubes + template -> docs/index.html
```

**GitHub Pages** — push the repo, then Settings → Pages → Source: `main` /
`/docs`. Your project is live at `https://<user>.github.io/<repo>/`.
Also works as-is on Netlify drop, Vercel, or straight from `file://`.

A live URL on your resume beats a GitHub link — most reviewers won't clone.

**How it stays honest:** the page doesn't replay canned answers. The
contribution-to-change algorithm is re-implemented in JavaScript over
pre-aggregated cubes exported from the same SQLite database, and its output
reconciles exactly with the Python app (verified: Feb 1,053,271 → Mar 867,811,
−17.6%, Electronics −27.6% in both). Two documented limits: the cubes are
one-dimensional so it can't cross-filter (`Electronics in EMEA`), and it uses
the rule engine rather than a live LLM.

---

## Testing

```bash
python -m pytest tests -q      # 34 tests, all offline, ~4s
```

The suite checks the SQL guard blocks writes and injection payloads, the parser
maps realistic phrasings to the right plan, and — the important one — that the
root-cause engine independently rediscovers the planted March story and that its
numbers reconcile with direct SQL aggregates.

---

## Design notes worth knowing

**The LLM never does arithmetic.** It writes SQL and it writes prose. Every
number in an answer comes from a SQL aggregate. The insight prompt receives a
FACTS block and is told not to introduce figures outside it. This is the
standard defence against confidently wrong metrics.

**Contribution-to-change, not size.** Ranking segments by revenue tells you
Electronics is big. Ranking by *share of the movement* tells you Electronics
explains 92% of the drop. The second one is an answer.

**Sanity checks are part of the product.** Before publishing, the analyzer
checks period length (28 vs 31 days), year-over-year seasonality, and
cancellation rate — and surfaces them as caveats. An analyst who skips those
gets caught in the meeting.

**Degrade, never crash.** LLM → repair attempt → template engine. Missing key →
offline mode. Bad question → a graceful answer. The UI has no failure state that
shows a stack trace.

---

## Extending it

- **Your own database** — point `RETAIL_DB_PATH` at another SQLite file, or
  swap `Database._connect` for SQLAlchemy to reach Postgres/Snowflake. The
  schema description is generated by introspection, so prompts adapt on their own.
- **More metrics/dimensions** — add an entry to `METRICS` / `DIMENSIONS` in
  `app/question.py`; the analyzer, templates and charts pick it up automatically.
- **Follow-up questions** — pass `st.session_state.history` into the SQL prompt
  to resolve "and what about EMEA?" against the previous turn.
- **Scheduled digests** — call `Analyst().ask()` from a cron job and post the
  insight to Slack.
