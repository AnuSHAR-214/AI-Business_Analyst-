# Build Guide — AI Business Analyst Assistant

This is the whole project, explained in the order you'd build it. Each step says
**what** you're building, **why** it exists, and **how to check it works** before
moving on. The code for every step is already in this repo, so you can read the
guide and the file side by side.

Total build time if you're typing it yourself: roughly a weekend.

---

## Step 0 — Get it running first (10 minutes)

Before understanding anything, see it work. Reading code you've watched run is
far easier.

```bash
cd ai_business_analyst
python setup.py
streamlit run streamlit_app.py
```

Click **"Why did revenue drop in March?"** in the sidebar. That single click
exercises every layer you're about to build.

---

## Step 1 — Build a database worth analysing

**File:** `data/generate_data.py`

Most portfolio projects fail here. They point an LLM at the Titanic CSV, ask
"how many passengers survived", and the answer is one `COUNT(*)`. There's no
analysis, so there's nothing to show.

You need data with a **story buried in it** — a movement whose cause is
discoverable but not obvious.

### The schema

```
regions ──< customers ──< orders ──< order_items >── products
```

Star-ish schema, like a real warehouse: one fact table (`order_items`) and
dimensions hanging off it. Then a denormalised view:

```sql
CREATE VIEW v_sales AS
SELECT o.order_id, o.order_date, substr(o.order_date,1,7) AS order_month,
       o.channel, c.customer_name, c.segment, r.region_name,
       p.product_name, p.category,
       i.quantity, i.unit_price, i.discount, i.revenue, i.cost,
       (i.revenue - i.cost) AS profit
FROM orders o
JOIN customers c ON ... JOIN regions r ON ...
JOIN order_items i ON ... JOIN products p ON ...
WHERE o.status = 'Completed';
```

**Why the view matters more than it looks.** LLM text-to-SQL accuracy drops hard
when the model has to invent a 5-table join. Give it one wide, pre-joined,
pre-filtered view and accuracy jumps. This is the single cheapest accuracy win
in the entire project, and it's a real technique — production analytics stacks
call these "semantic layers" or "marts".

### Generating the data

Revenue per day is composed from factors that a real business actually has:

```python
n_orders = poisson(base
                   * trend(day)          # ~14% annual growth
                   * seasonality(day)    # Q4 peak, Feb/Mar trough
                   * weekday_factor(day) # weekends ~25% lower
                   * shock(day))         # the planted event
```

### Planting the story

March 2026 gets a multi-layered shock, because real declines are never one thing:

| Layer | Effect |
|---|---|
| Global | 12% fewer orders |
| Electronics | ~18% of remaining electronics line items dropped |
| Two enterprise accounts | 70% of their orders vanish (churn risk) |
| EMEA region | 18% weaker |
| Online channel | 10% weaker |
| Cancellations | 2.5% → ~5% |

Result: **−17.6% total revenue, with Electronics at −27.6%.**

**Check it works:**

```bash
python data/generate_data.py
```

It prints the resulting Feb→Mar change. If you fork this, tune the constants at
the top of the file until you like the numbers — that's what the printout is for.

---

## Step 2 — Make the database safe to hand to an AI

**File:** `app/guard.py`

An LLM writing SQL against your database is a `DROP TABLE` waiting to happen.
Two independent defences:

**1. A read-only connection** (`app/database.py`)

```python
sqlite3.connect(f"file:{path}?mode=ro", uri=True)
```

SQLite itself now refuses writes. Even a perfect bypass of your Python code
hits a wall at the driver.

**2. A statement guard** (`app/guard.py`)

```python
def validate_sql(sql: str) -> str:
    cleaned = strip_sql(sql)                    # strip ``` fences and comments
    scan = _STRING_LIT.sub("''", cleaned).lower()   # blank out string literals
    if not scan.startswith(("select", "with")): raise UnsafeSQLError(...)
    if ";" in scan:                             raise UnsafeSQLError(...)
    for word in re.findall(r"[a-z_]+", scan):
        if word in FORBIDDEN:                   raise UnsafeSQLError(...)
    return cleaned
```

Three details that separate this from a naive version:

- **String literals are blanked before scanning.** Otherwise
  `WHERE product_name = 'drop kit'` gets rejected. Naive keyword filters are
  notorious for this.
- **Comments are stripped first**, so `SELECT 1 --; DROP TABLE x` can't smuggle
  anything past the semicolon check.
- **Markdown fences are stripped**, because LLMs wrap SQL in ` ```sql ` no matter
  how firmly you ask them not to.

**Check it works:** `pytest tests -q -k guard` — 14 tests covering writes, DDL,
stacked statements, `PRAGMA`, `ATTACH`, and the string-literal false positive.

---

## Step 3 — Describe the schema *for a reader*

**File:** `app/database.py`, `schema_description()`

The prompt you give the model is the product. Don't dump `CREATE TABLE`
statements — introspect and write a description, with **example values for
low-cardinality text columns**:

```
VIEW v_sales  -- PREFERRED denormalised view, already filtered to completed orders
  - category (TEXT)  e.g. 'Electronics', 'Home & Kitchen', 'Office', 'Apparel'
  - region_name (TEXT)  e.g. 'North America', 'EMEA', 'APAC', 'LATAM'
  - segment (TEXT)  e.g. 'Enterprise', 'SMB', 'Consumer'
  ...
Data covers order_date from 2024-01-01 to 2026-06-30.
Dates are TEXT 'YYYY-MM-DD'; use substr(order_date,1,7) for month.
```

Without the example values the model writes `WHERE category = 'Tech'` and gets
zero rows. With them it writes `'Electronics'`. This one change fixes the most
common class of text-to-SQL failure.

Note this is **generated by introspection**, not hard-coded — point the app at a
different database and the prompt regenerates itself.

---

## Step 4 — Understand the question before answering it

**File:** `app/question.py`

Everyone skips this and pipes the raw question straight to the LLM. Don't. A
small parser that produces a structured `Plan` gives you three things:

1. an **offline mode** that works with no API key
2. a **hint** you can hand the LLM, which measurably improves its SQL
3. a **routing decision** — "why" questions need a completely different pipeline

```python
@dataclass
class Plan:
    intent: str          # aggregate | trend | rank | diagnostic
    metric: str          # revenue | profit | margin | orders | aov | ...
    dimension: str|None  # category | region | channel | segment | product | customer | month
    period: Period       # resolved date window
    compare_to: Period   # the baseline, for diagnostics
```

What the parser handles:

- **Metrics** by synonym — "sales"/"turnover"/"top line" → `revenue`;
  "basket size" → `aov`
- **Dimensions** with `by|per|across X` given priority over incidental mentions
- **Time**: `"March 2026"`, `"Q1 2026"`, `"last 6 months"`, `"2025"`, and bare
  `"March"` (resolved to the latest year present in the data)
- **Intent**: `"why"` + a direction word → `diagnostic`, which auto-sets
  `compare_to` to the previous month

**Check it works:** `pytest tests -q -k parse`

---

## Step 5 — The LLM layer, with a real fallback

**File:** `app/llm.py`

One interface, three backends: `anthropic`, `openai`, `offline`. Provider
selection is `auto` by default — Anthropic if a key exists, else OpenAI, else
offline.

```python
llm = LLM()          # auto
llm.provider         # 'anthropic' | 'openai' | 'offline'
llm.is_offline       # callers branch on this
```

**Why the offline backend is not a cop-out:** it makes the project demoable on
any machine, keeps your test suite deterministic and free, and forces a clean
separation between *deciding what to compute* and *deciding how to phrase it*.
That separation is good architecture regardless of keys.

SDK imports are **lazy** (inside `__init__`), so neither `anthropic` nor `openai`
is a hard dependency.

---

## Step 6 — Text-to-SQL with self-repair

**File:** `app/sql_agent.py`

```
question + schema + plan hint  ──►  LLM  ──►  SQL
                                              │
                                    guard ────┤ fails? ──► one repair attempt
                                              │              (error fed back)
                                          execute            still fails? ──► template
```

The system prompt is short and absolute:

```
- Return ONLY the SQL. No prose, no markdown fences.
- Exactly one statement. SELECT or WITH only.
- Prefer the denormalised view v_sales.
- Only use column values that appear in the schema's example values.
```

**Self-repair** is one retry that feeds the database's own error message back:

```python
REPAIR_PROMPT = "The SQL you produced failed. Fix it...\nSQL:\n{sql}\n\nError:\n{error}"
```

One retry, not a loop — an unbounded retry loop against a paid API is how you
wake up to a large bill. If the repair fails, drop to the template engine.

The template engine (`build_template_sql`) generates SQL from the `Plan` for
each intent. It's ~40 lines and it's what runs offline.

---

## Step 7 — The part that makes it an *analyst*

**File:** `app/analyzer.py`

Everything so far is a natural-language SQL client. Useful, but not an analyst.
This is the step that changes that.

### The idea: contribution to change

"Why did revenue drop?" is not answered by *what is big*. It's answered by
*what moved*.

```
Ranked by size:      Electronics is our largest category.        ← useless
Ranked by Δ share:   Electronics explains 92% of the drop.       ← an answer
```

### The algorithm

```
for each dimension in [category, region, channel, segment, customer, product]:
      prev = SELECT metric BY dimension WHERE period = baseline
      curr = SELECT metric BY dimension WHERE period = target
      merge on dimension value (outer join, fill 0)   ← segments that vanished
      Δ         = curr - prev
      %Δ        = Δ / prev
      share of Δ = Δ / total_Δ                        ← the ranking key
      keep segments moving with the headline, take top 3 per dimension
sort all survivors by |share of Δ|
```

Two details that matter:

- **Outer join, fill 0.** A customer who bought in February and nothing in March
  has no March row. An inner join silently deletes your biggest finding.
- **Top-N per dimension, then merge.** Products and customers have hundreds of
  values; regions have four. A flat global ranking lets wide dimensions crowd
  out narrow ones, and you lose "EMEA collapsed" behind twelve SKUs.

### Sanity checks — the part that makes it credible

Before publishing, `_sanity_notes()` runs the checks a good analyst runs before
walking into the meeting:

| Check | Why |
|---|---|
| **Period length** — 28 vs 31 days | Feb→Mar comparisons are structurally unfair; per-day the drop is −25.6%, which is *worse* than the headline |
| **Year-over-year** | Is this just seasonality? |
| **Cancellation rate** | `v_sales` only counts completed orders, so a cancellation spike hides demand that did exist |

These surface in the answer as **Caveats**. Shipping the caveats is what
separates a demo from a tool someone trusts.

**Check it works:** `pytest tests -q -k root_cause`. The tests assert the engine
rediscovers the planted story *and* that its totals reconcile with direct SQL.

---

## Step 8 — Pick the chart automatically

**File:** `app/charts.py`

Rules, in priority order:

| Data shape | Chart |
|---|---|
| A time-like column present | line |
| A root-cause result | **waterfall** |
| 1 category + 1 number, ≤25 rows | horizontal bar, sorted |
| 2+ numeric columns | scatter |
| Single scalar | big-number metric card |
| Anything else | table |

The **waterfall** is the one that sells the project: baseline bar → one negative
bar per driver → "everything else" → final bar. Someone glances at it and
understands the entire month.

Note the split: `choose()` returns a `ChartSpec`, `to_plotly()` renders it. So
chart *logic* is unit-testable without a plotting library, and any front end
could render the same spec.

---

## Step 9 — Write the insight without hallucinating numbers

**File:** `app/insights.py`

The rule that makes this trustworthy:

> **The LLM never calculates. It only phrases.**

It receives a pre-computed FACTS block and is instructed:

```
- Use ONLY the numbers in the FACTS block. Never invent or extrapolate a figure.
- Lead with the answer, then the drivers, then what to do about it.
- If the facts include caveats, reflect them honestly rather than overclaiming causation.
```

FACTS looks like:

```
Revenue decreased 17.6% in March 2026 vs February 2026 (1,053,271 -> 867,811, -185,459)

Largest contributors to the change:
  - [category] Category 'Electronics' fell 27.6% (616,526 -> 446,287), 92% of the total change
  - [customer] Customer 'Bright Path Industries' fell 87.4% (33,984 -> 4,276), 16% of the total change
  ...
  ! Periods differ in length (28 vs 31 days); on a per-day basis the change is -25.6%.
```

Every number is already computed. The model's only job is English. If it goes
off-script you can detect it, because you have the ground truth.

Offline, `_offline_diagnostic()` renders the same facts through templates —
stiffer prose, identical numbers.

---

## Step 10 — Orchestrate

**File:** `app/pipeline.py`

```python
answer = Analyst().ask("Why did revenue drop in March?")
```

```
parse → diagnostic? ─ yes → root_cause() → waterfall ─┐
                    └ no  → text-to-SQL → execute ────┤→ insight → Answer
```

`Answer` carries `sql`, `data`, `chart`, `insight`, `plan`, `facts`, and a
**`trace`** — a list of every decision made. The trace is what you show when
someone asks "how do I know it did the right thing", and it's what you debug
with at 1am.

The whole `ask()` body is wrapped in try/except that returns a populated
`Answer` with an `error` field. **The UI has no stack-trace failure state.**

---

## Step 11 — The interface

**File:** `streamlit_app.py`

Layout: chat on the left, a live KPI rail on the right (revenue, orders, margin,
AOV, each with month-over-month delta, plus a revenue sparkline).

Each answer renders in this order — deliberately:

```
Insight   ← the conclusion, first
Chart     ← the evidence, visual
[Data]    ← the evidence, tabular (tab, with CSV download)
[SQL]     ← the method (tab)
[Trace]   ← the reasoning (tab)
```

Executives read the first line. Analysts open the SQL tab. Both are served
without either one being in the other's way.

Implementation notes:

- `@st.cache_resource` on the `Analyst` — one DB connection and one LLM client
  for the whole session, not one per rerun.
- Sidebar example-question buttons write to `st.session_state.pending`, so a
  click behaves exactly like typing.
- Offline mode shows an explicit banner. Never let a demo silently run degraded.

---

## Step 12 — Test the thing that matters

**File:** `tests/test_analyst.py` — 34 tests, fully offline, ~4 seconds.

Most projects test that functions return something. Test the **claim** instead:

```python
def test_root_cause_finds_the_march_drop(analyst):
    rc = analyst.ask("Why did revenue drop in March 2026?").root_cause
    assert -30 < rc.pct_change < -10                       # material decline
    cats = [c for c in rc.contributors if c.dimension == "category"]
    assert cats[0].value == "Electronics"                  # found the driver
    custs = [c for c in rc.contributors if c.dimension == "customer"]
    assert len(custs) >= 2                                 # found the churn
```

Plus:

- **Reconciliation** — the engine's totals equal a direct SQL aggregate
- **Additivity** — per-category deltas sum to the headline delta
- **Guard** — 14 write/DDL/injection payloads all rejected; one false-positive
  case (`'drop kit'`) accepted
- **Breadth** — every example question returns a populated answer
- **Graceful degradation** — `"qwerty zxcv"` still returns something

That first test is the one to show an interviewer. It proves the system found a
real answer, not that it returned a string.

### Isolate tests from your own credentials

**File:** `tests/conftest.py`

This one was learned the hard way. The key-validation test did this:

```python
monkeypatch.setenv("OPENAI_API_KEY", "pip install openai")   # wrong
```

It passed on a clean checkout and failed the moment a real key went into
`.env` — because `settings` is populated from `.env` at import time and
`attempt()` reads `settings.openai_api_key or os.environ.get(...)`. The real key
won, so the malformed one was never tested.

Two problems in one: the suite behaved differently depending on private local
files, and a test could have spent real money. `conftest.py` now blanks every
credential source before collection, and tests that need a key inject it by
patching `settings` directly. There's also a test guarding the guard:

```python
def test_suite_never_sees_real_credentials():
    assert not settings.openai_api_key
    assert not os.environ.get("OPENAI_API_KEY")
```

Verified by running the suite three ways — empty `.env`, real key, malformed key
— and getting 43 passed each time. **If your tests behave differently depending
on what's in your `.env`, they aren't tests yet.**

---

## What to build next

Ordered by value-per-hour:

1. **Follow-up questions** — pass conversation history into the SQL prompt so
   "and what about EMEA?" resolves against the previous turn.
2. **Your own database** — swap `Database._connect` for SQLAlchemy. The schema
   description is introspected, so the prompts adapt automatically. This is the
   change that turns a portfolio piece into something a team would use.
3. **Query result caching** — hash the SQL, cache the DataFrame. Free latency.
4. **A metric registry** — define "active customer" once, in code, so every
   query agrees. This is the actual hard problem in production analytics, and
   knowing that is worth saying out loud in an interview.
5. **Scheduled digests** — cron → `Analyst().ask()` → Slack.
6. **Forecasting** — "what will April look like?" via statsmodels, with the same
   facts-in/prose-out discipline.

---

## The five decisions worth defending

If someone asks you to justify this design, these are the answers:

1. **A denormalised view, not raw tables** — the largest single accuracy gain in
   LLM text-to-SQL, and it mirrors how real semantic layers work.
2. **Example values in the schema prompt** — kills the most common failure mode,
   inventing category names that match nothing.
3. **Contribution-to-change ranking** — the difference between describing data
   and answering a question.
4. **The LLM never does arithmetic** — it writes SQL and it writes prose. Every
   number traces to an aggregate. That's how you get a system people trust.
5. **Degrade, never crash** — LLM → repair → template → offline. Every layer has
   a floor underneath it.
