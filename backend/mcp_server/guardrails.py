"""Output and request guardrails shared by the MCP tools."""

import re

REFILL_MAX_DAYS = 30  # refill quantity cap: at most one month's supply
AMBER_MAX_PER_DAY = 3  # amber alerts per parent per rolling 24 h; red is never capped

# Words that would turn a descriptive summary into diagnosis, prognosis or dosing advice.
# Summaries may only describe numbers against limits set by the doctor.
_BLOCKED = [
    r"diagnos\w*",
    r"worsen\w*",
    r"worse",
    r"deteriorat\w*",
    r"improv\w*",
    r"better",
    r"diabet\w*",
    r"hypertensi\w*",
    r"hypotensi\w*",
    r"hypoglyc\w*",
    r"hyperglyc\w*",
    r"disease",
    r"condition",
    r"heart attack",
    r"stroke",
    r"risk\w*",
    r"dose",
    r"doses",
    r"dosage",
    r"dosing",
    r"mg(?!/)",  # "mg" as a medicine strength; "mg/dL" readings are fine
    r"increase\w*",
    r"decrease\w*",
    r"reduce\w*",
    r"double",
    r"halve",
    r"skip\w*",
    r"stop taking",
    r"start taking",
    r"take (?:more|less|extra)",
    r"prescri\w*",
    r"you should",
]
_BLOCKED_RE = re.compile(r"\b(?:" + "|".join(_BLOCKED) + r")\b", re.IGNORECASE)


def blocked_terms(text: str) -> list[str]:
    """Return the blocked words found in `text` (empty list = safe to send)."""
    return sorted({m.group(0).lower() for m in _BLOCKED_RE.finditer(text)})
