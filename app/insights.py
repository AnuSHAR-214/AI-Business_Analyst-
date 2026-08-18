"""
Narrative layer.

The LLM's job here is *writing*, not *calculating*. It receives a block of
pre-computed facts and is explicitly told not to introduce numbers that are not
in that block - the standard guard against hallucinated metrics.

If no model is available, `_offline_*` composes the same narrative from the same
facts with templates. Slightly stiffer prose, identical numbers.
"""

from __future__ import annotations

from .analyzer import RootCause
from .llm import LLM

SYSTEM = """You are a sharp business analyst writing for an executive audience.

Rules:
- Use ONLY the numbers in the FACTS block. Never invent or extrapolate a figure.
- Lead with the answer, then the drivers, then what to do about it.
- Be specific and concrete. No filler, no hedging, no restating the question.
- Markdown. Short paragraphs and tight bullets. Under 200 words.
- If the facts include caveats (seasonality, period length, cancellations),
  reflect them honestly rather than overclaiming causation.
"""

DIAGNOSTIC_TEMPLATE = """Business question: {question}

FACTS
-----
{facts}

Write:
1. **Answer** - one or two sentences stating what happened and by how much.
2. **Why** - 3-5 bullets, each naming a segment, its % change and its share of
   the total movement.
3. **Caveats** - only if the facts list any.
4. **Recommended next step** - one concrete analysis or action.
"""

SUMMARY_TEMPLATE = """Business question: {question}

SQL that was executed:
{sql}

FACTS about the result table
----------------------------
{facts}

Write a 3-6 sentence answer. Lead with the direct answer to the question, then
the one or two most decision-relevant patterns. Finish with one short
"what I'd look at next" line.
"""


class InsightWriter:
    def __init__(self, llm: LLM) -> None:
        self.llm = llm

    # -- public ------------------------------------------------------------
    def diagnostic(self, question: str, rc: RootCause) -> str:
        facts = rc.to_facts()
        if self.llm.is_offline:
            return _offline_diagnostic(rc)
        try:
            return self.llm.complete(
                SYSTEM, DIAGNOSTIC_TEMPLATE.format(question=question, facts=facts),
                800).text
        except Exception:
            return _offline_diagnostic(rc)

    def summarise(self, question: str, sql: str, facts: str) -> str:
        if self.llm.is_offline:
            return _offline_summary(facts)
        try:
            return self.llm.complete(
                SYSTEM,
                SUMMARY_TEMPLATE.format(question=question, sql=sql, facts=facts),
                600).text
        except Exception:
            return _offline_summary(facts)


# --------------------------------------------------------------------------- #
def _offline_diagnostic(rc: RootCause) -> str:
    verb = "decreased" if rc.delta < 0 else "increased"
    out = [
        f"**{rc.metric_label} {verb} {abs(rc.pct_change):.1f}% in "
        f"{rc.period_label}** versus {rc.compare_label} "
        f"({rc.prev_total:,.0f} -> {rc.curr_total:,.0f}, a change of "
        f"{rc.delta:+,.0f}).",
        "",
        "**What drove it**",
    ]
    if not rc.contributors:
        out.append("- No single segment explains the change; it is broad-based.")
    else:
        seen = set()
        for c in rc.contributors[:6]:
            key = (c.dimension, c.value)
            if key in seen:
                continue
            seen.add(key)
            arrow = "down" if c.delta < 0 else "up"
            out.append(
                f"- **{c.label}: {c.value}** {arrow} {abs(c.pct_change):.1f}% "
                f"({c.prev:,.0f} -> {c.curr:,.0f}) — "
                f"{abs(c.share_of_change) * 100:.0f}% of the total change"
            )

    if rc.notes:
        out += ["", "**Caveats**"] + [f"- {n}" for n in rc.notes]

    if rc.contributors:
        top = rc.contributors[0]
        out += [
            "",
            f"**Next step** — pull weekly {rc.metric_label.lower()} for "
            f"{top.label.lower()} '{top.value}' to see whether the drop is a "
            "one-off week or a sustained level shift.",
        ]
    return "\n".join(out)


def _offline_summary(facts: str) -> str:
    lines = [ln for ln in facts.splitlines() if ln.strip()]
    if not lines:
        return "The query returned no rows, so there is nothing to summarise."
    body = "\n".join(f"- {ln}" for ln in lines[1:6])
    return (
        f"**Result** — {lines[0]}\n\n{body}\n\n"
        "*(Offline mode: this summary is generated from the computed statistics. "
        "Set an API key for a written narrative.)*"
    )
