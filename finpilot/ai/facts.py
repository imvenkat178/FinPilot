"""Numbers in model wording come only from the application.

The deterministic reference answer is rewritten with every figure replaced by a token such
as [F1]. The model rewrites the words and keeps the tokens; it never sees or types a number.
The server puts the exact figures back, rejects any figure the model typed itself, and records
which result field each figure came from so the answer can show its sources.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Optional

# Written numbers other than "one", which is too common as a pronoun to treat as a figure.
_NUMBER_WORDS = (r"(?:two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|forty|fifty|sixty"
                 r"|seventy|eighty|ninety|hundred|thousand|million|billion|trillion|dozen)s?")
# Money and percentages before dates, dates before bare numbers, then number words and lone symbols.
_FIGURE = re.compile(
    r"[-−]?(?:USD|EUR|GBP|INR)\s?[-−]?\d[\d,]*(?:\.\d+)?"
    r"|[-−]?[$€£₹]\s?[-−]?\d[\d,]*(?:\.\d+)?"
    r"|\b\d{4}-\d{2}-\d{2}(?:T[\d:.]+(?:Z|[+-]\d{2}:\d{2})?)?\b"
    r"|[-−]?\d[\d,]*(?:\.\d+)?\s?%"
    r"|[-−]?\d[\d,]*(?:\.\d+)?"
    r"|(?i:\b" + _NUMBER_WORDS + r"\b)|[$€£₹%]"
)
# Models sometimes add spaces or lowercase inside a token; the figure index is what matters.
TOKEN = re.compile(r"\[\s*[Ff]\s*(\d{1,3})\s*\]")
_STRAY = re.compile(r"\d|[$€£₹%]|\b" + _NUMBER_WORDS + r"\b", re.I)


class TokenError(ValueError):
    """A draft that did not keep figures inside application tokens."""


@dataclass
class FactSheet:
    tokenized: str
    facts: dict[str, str] = field(default_factory=dict)


def _figures(text: str):
    for match in _FIGURE.finditer(text or ""):
        start, end = match.span()
        value = match.group(0)
        while value.endswith(","):
            value, end = value[:-1], end - 1
        if value:
            yield start, end, value


def tokenize(reference: str) -> FactSheet:
    """Replace each distinct figure with a token; the same figure always gets the same token."""
    facts: dict[str, str] = {}
    tokens_by_text: dict[str, str] = {}
    pieces, last = [], 0
    for start, end, value in _figures(reference):
        token = tokens_by_text.get(value)
        if token is None:
            token = f"F{len(facts) + 1}"
            facts[token], tokens_by_text[value] = value, token
        pieces.append(reference[last:start])
        pieces.append(f"[{token}]")
        last = end
    pieces.append((reference or "")[last:])
    return FactSheet("".join(pieces), facts)


def mask(text: str, sheet: Optional[FactSheet] = None, keep_unknown: bool = False) -> str:
    """Hide figures in language-only context; figures the answer already holds become their tokens.

    With keep_unknown, other figures stay as typed text, which is how a draft with an invented figure looks.
    """
    known = {value: token for token, value in (sheet.facts.items() if sheet else [])}
    pieces, last = [], 0
    for start, end, value in _figures(text):
        pieces.append(text[last:start])
        pieces.append(f"[{known[value]}]" if value in known else value if keep_unknown else "#")
        last = end
    pieces.append((text or "")[last:])
    return "".join(pieces)


def substitute(draft: str, sheet: FactSheet) -> tuple[str, list[str]]:
    """Insert the exact figures, or raise TokenError when the draft typed or invented one."""
    unknown = sorted({m.group(0) for m in TOKEN.finditer(draft or "") if f"F{m.group(1)}" not in sheet.facts})
    if unknown:
        raise TokenError("the draft used figure tokens that do not exist: " + ", ".join(unknown))
    stray = _STRAY.search(TOKEN.sub(" ", draft or ""))
    if stray:
        words = TOKEN.sub(" ", draft)
        snippet = words[max(0, stray.start() - 20):stray.end() + 25].strip()
        raise TokenError(f"the draft typed a figure instead of using a token: {snippet!r}")
    used = list(dict.fromkeys(f"F{m.group(1)}" for m in TOKEN.finditer(draft or "")))
    if sheet.facts and not used:
        raise TokenError("the draft dropped every figure the answer needs")
    return TOKEN.sub(lambda m: sheet.facts[f"F{m.group(1)}"], draft), used


def _number(text: Any) -> Optional[Decimal]:
    if not isinstance(text, (str, int, float, Decimal)) or isinstance(text, bool):
        return None
    cleaned = re.sub(r"USD|EUR|GBP|INR|[$€£₹,%\s]", "", str(text)).replace("−", "-")
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", cleaned):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _leaves(node: Any, path: str):
    if isinstance(node, dict):
        if "display" in node and "amount" in node:
            yield path, str(node["display"]), _number(node["amount"])
            return
        for key, value in node.items():
            if not str(key).startswith("_"):
                yield from _leaves(value, f"{path}.{key}" if path else str(key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _leaves(value, f"{path}[{index}]")
    elif isinstance(node, (str, int, float, Decimal)) and not isinstance(node, bool):
        yield path, str(node), _number(node)


def figure_sources(sheet: FactSheet, payloads: Iterable[Any], limit: int = 3) -> list[dict]:
    """For each figure, the result fields that hold exactly that value."""
    leaves = []
    for payload in payloads or []:
        if isinstance(payload, dict):
            tool = str(payload.get("_tool") or "result")
            leaves.extend((f"{tool}.{path}" if path else tool, display, number)
                          for path, display, number in _leaves(payload, ""))
    out = []
    for token, text in sheet.facts.items():
        value = _number(text)
        exact = [path for path, display, _ in leaves if display == text]
        numeric = [path for path, _, number in leaves
                   if value is not None and number is not None and number == value and path not in exact]
        out.append({"token": token, "text": text, "sources": (exact + numeric)[:limit]})
    return out
