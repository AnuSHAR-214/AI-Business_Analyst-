"""
SQL safety guard.

An LLM writing SQL against a production warehouse is only acceptable if the
statement is provably read-only. This module is the choke point: every query,
whether written by an LLM or by the offline template engine, has to pass
`validate_sql` before it reaches the database.
"""

from __future__ import annotations

import re

FORBIDDEN = {
    "insert", "update", "delete", "drop", "alter", "create", "replace",
    "truncate", "attach", "detach", "pragma", "vacuum", "reindex", "grant",
    "revoke", "commit", "rollback", "begin",
}

_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.S)
_COMMENT_LINE = re.compile(r"--[^\n]*")
_STRING_LIT = re.compile(r"'[^']*'")


class UnsafeSQLError(ValueError):
    """Raised when a generated statement is not a safe, read-only SELECT."""


def strip_sql(sql: str) -> str:
    """Remove markdown fences, comments and trailing semicolons."""
    sql = sql.strip()
    if sql.startswith("```"):
        sql = re.sub(r"^```[a-zA-Z]*\n?", "", sql)
        sql = re.sub(r"```\s*$", "", sql)
    sql = _COMMENT_BLOCK.sub(" ", sql)
    sql = _COMMENT_LINE.sub(" ", sql)
    return sql.strip().rstrip(";").strip()


def validate_sql(sql: str) -> str:
    """Return a cleaned statement, or raise UnsafeSQLError."""
    cleaned = strip_sql(sql)
    if not cleaned:
        raise UnsafeSQLError("Empty statement.")

    # Ignore anything inside string literals when scanning for keywords.
    scan = _STRING_LIT.sub("''", cleaned).lower()

    if not (scan.startswith("select") or scan.startswith("with")):
        raise UnsafeSQLError("Only SELECT / WITH statements are allowed.")

    if ";" in scan:
        raise UnsafeSQLError("Multiple statements are not allowed.")

    for word in re.findall(r"[a-z_]+", scan):
        if word in FORBIDDEN:
            raise UnsafeSQLError(f"Statement contains forbidden keyword: {word.upper()}")

    return cleaned


def enforce_limit(sql: str, max_rows: int) -> str:
    """Append a LIMIT if the query does not already cap its own output."""
    if re.search(r"\blimit\b\s+\d+\s*$", sql, re.I):
        return sql
    return f"{sql}\nLIMIT {max_rows}"
