"""Recover the business rules buried in the code.

This is the analysis that matters most to the people paying for a legacy
system: not "this method is complex" but "orders over 500 ship free, and that
rule lives on line 71 of OrderProcessor".

The previous version emitted sentences like "This condition checks if a value
is greater than a threshold" — true of every ``>`` ever written, and useless.
Here a rule is only reported when concrete operands can be recovered, and the
explanation names them.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..languages import BY_NAME
from ..models import SourceFile
from ..tokenizer import scan

#: Vocabulary that marks a value as commercially meaningful rather than
#: incidental. Deliberately domain-flavoured: these are the words that show up
#: in the systems people are afraid to change.
DOMAIN_TERMS = {
    "price", "cost", "tax", "vat", "gst", "discount", "total", "subtotal",
    "rate", "fee", "charge", "threshold", "limit", "minimum", "maximum",
    "min", "max", "commission", "interest", "balance", "amount", "payment",
    "shipping", "freight", "premium", "deductible", "penalty", "surcharge",
    "credit", "debit", "invoice", "salary", "wage", "bonus", "quota",
    "tier", "level", "grade", "score", "points", "loyalty", "expiry",
    "due", "overdue", "term", "period", "quantity", "qty", "stock",
    "inventory", "reorder", "margin", "markup", "currency", "exchange",
    "eligible", "eligibility", "approve", "approval", "reject", "status",
    "age", "years", "months", "days", "count", "weight", "volume",
}

COMPARATORS = {
    ">": ("is more than", "above"),
    ">=": ("is at least", "at or above"),
    "<": ("is less than", "below"),
    "<=": ("is at most", "at or below"),
    "==": ("equals", "equal to"),
    "!=": ("is not", "different from"),
    "<>": ("is not", "different from"),
}


class BusinessRule(dict):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class BusinessLogicExtractor:
    def __init__(self, files: Sequence[SourceFile]):
        self.files = list(files)
        self.rules: List[BusinessRule] = []

    def run(self) -> List[BusinessRule]:
        for sf in self.files:
            adapter = BY_NAME.get(sf.language)
            masked = scan(sf.source, adapter.syntax).mask if adapter else sf.source
            lines = masked.split("\n")
            raw_lines = sf.source.split("\n")
            self._thresholds(sf, lines, raw_lines)
            self._formulas(sf, lines, raw_lines)
            self._constants(sf)
            self._state_machines(sf, lines, raw_lines)
        self.rules.sort(key=lambda r: (-r["importance"], r["file"], r["line"]))
        return self.rules

    # -- conditional thresholds --------------------------------------------

    def _thresholds(self, sf: SourceFile, lines: List[str],
                    raw: List[str]) -> None:
        pattern = re.compile(
            r"\b(?P<left>[A-Za-z_][\w.\[\]]*)\s*"
            r"(?P<op><=|>=|==|!=|<>|<|>)\s*"
            r"(?P<right>-?\d+(?:\.\d+)?|'[^']*'|\"[^\"]*\"|[A-Z_][A-Z0-9_]{2,})")
        for idx, line in enumerate(lines, 1):
            if not re.search(r"\b(if|elsif|elseif|when|while|case|until|"
                             r"evaluate)\b", line, re.I):
                continue
            for m in pattern.finditer(line):
                left, op, right = m.group("left"), m.group("op"), m.group("right")
                subject = _subject(left)
                if not self._is_domain(subject):
                    continue
                phrase, _ = COMPARATORS.get(op, ("compares with", ""))
                value = right.strip("'\"")
                owner = self._owner_at(sf, idx)
                self.rules.append(BusinessRule(
                    file=sf.rel_path, line=idx, kind="threshold",
                    function=owner,
                    code=raw[idx - 1].strip()[:180],
                    subject=subject,
                    operator=op,
                    value=value,
                    explanation=(
                        f"When {_humanise(subject)} {phrase} {value}, "
                        f"a different path is taken."),
                    importance=3 if _is_number(right) else 2,
                ))

    # -- calculations -------------------------------------------------------

    def _formulas(self, sf: SourceFile, lines: List[str],
                  raw: List[str]) -> None:
        pattern = re.compile(
            r"(?P<target>[A-Za-z_][\w.\[\]]*)\s*(?:=|:=)\s*"
            r"(?P<expr>[^=;{}\n][^;{}\n]*)")
        for idx, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith(("if", "while", "for", "==")):
                continue
            m = pattern.search(line)
            if not m:
                continue
            target = _subject(m.group("target"))
            expr = m.group("expr").strip().rstrip(";").strip()
            if not self._is_domain(target):
                continue
            if not re.search(r"[-+*/]|\bMOD\b", expr, re.I):
                continue
            if len(expr) > 160:
                continue
            owner = self._owner_at(sf, idx)
            numbers = re.findall(r"(?<![\w.])(\d+(?:\.\d+)?)(?![\w.])", expr)
            self.rules.append(BusinessRule(
                file=sf.rel_path, line=idx, kind="calculation",
                function=owner,
                code=raw[idx - 1].strip()[:180],
                subject=target,
                value=expr,
                explanation=(
                    f"{_humanise(target).capitalize()} is calculated as "
                    f"`{expr}`."
                    + (f" The constants {', '.join(sorted(set(numbers))[:4])} "
                       f"are business parameters with no name."
                       if numbers else "")),
                importance=4 if numbers else 3,
            ))

    # -- named constants ----------------------------------------------------

    def _constants(self, sf: SourceFile) -> None:
        candidates = list(sf.globals)
        for td in sf.types:
            candidates += td.fields
        for fd in candidates:
            if not fd.initial_value:
                continue
            if not self._is_domain(fd.name):
                continue
            self.rules.append(BusinessRule(
                file=sf.rel_path, line=fd.line, kind="constant",
                function=fd.owner,
                code=f"{fd.name} = {fd.initial_value}"[:180],
                subject=fd.name,
                value=fd.initial_value,
                explanation=(
                    f"{_humanise(fd.name).capitalize()} is fixed at "
                    f"{fd.initial_value}. Changing this value changes "
                    f"behaviour everywhere it is used."),
                importance=4 if {"final", "const", "static", "readonly"}
                & fd.modifiers else 3,
            ))

    # -- status / state transitions ----------------------------------------

    def _state_machines(self, sf: SourceFile, lines: List[str],
                        raw: List[str]) -> None:
        pattern = re.compile(
            r"\b(?P<target>[A-Za-z_][\w.]*(?:status|state|stage|phase|flag)"
            r"[\w.]*)\s*(?:=|:=|\.set\w*\(|\bTO\b)\s*"
            r"['\"](?P<value>[^'\"]{1,40})['\"]", re.I)
        for idx, line in enumerate(lines, 1):
            for m in pattern.finditer(line):
                owner = self._owner_at(sf, idx)
                self.rules.append(BusinessRule(
                    file=sf.rel_path, line=idx, kind="state-transition",
                    function=owner,
                    code=raw[idx - 1].strip()[:180],
                    subject=_subject(m.group("target")),
                    value=m.group("value"),
                    explanation=(
                        f"{_humanise(_subject(m.group('target'))).capitalize()} "
                        f"is set to \"{m.group('value')}\" here."),
                    importance=3,
                ))

    # -- helpers ------------------------------------------------------------

    def _is_domain(self, name: str) -> bool:
        parts = re.split(r"(?<=[a-z0-9])(?=[A-Z])|[_\-.]", name)
        return any(p.lower() in DOMAIN_TERMS for p in parts if p)

    def _owner_at(self, sf: SourceFile, line: int) -> Optional[str]:
        for fn in sf.functions:
            if fn.line <= line <= fn.end_line:
                return fn.qualified_name
        return None

    # -- output -------------------------------------------------------------

    def summary(self) -> Dict[str, object]:
        by_file: Dict[str, List[dict]] = defaultdict(list)
        by_kind: Dict[str, int] = defaultdict(int)
        subjects: Dict[str, int] = defaultdict(int)
        for rule in self.rules:
            by_file[rule["file"]].append(rule)
            by_kind[rule["kind"]] += 1
            subjects[_humanise(rule["subject"])] += 1
        return {
            "total": len(self.rules),
            "by_kind": dict(by_kind),
            "by_file": {k: v for k, v in by_file.items()},
            "top_subjects": sorted(subjects.items(), key=lambda kv: -kv[1])[:15],
            "highlights": self.rules[:25],
        }

    def glossary(self) -> List[dict]:
        """The vocabulary this system actually operates on."""
        terms: Dict[str, dict] = {}
        for rule in self.rules:
            key = _humanise(rule["subject"])
            entry = terms.setdefault(key, {
                "term": key, "occurrences": 0, "kinds": set(),
                "files": set(), "example": rule["explanation"]})
            entry["occurrences"] += 1
            entry["kinds"].add(rule["kind"])
            entry["files"].add(rule["file"])
        out = []
        for entry in terms.values():
            out.append({
                "term": entry["term"],
                "occurrences": entry["occurrences"],
                "kinds": sorted(entry["kinds"]),
                "files": sorted(entry["files"])[:6],
                "file_count": len(entry["files"]),
                "example": entry["example"],
            })
        return sorted(out, key=lambda e: -e["occurrences"])[:40]


def _subject(expr: str) -> str:
    text = expr.split("[")[0]
    parts = [p for p in text.split(".") if p]
    if not parts:
        return text
    tail = parts[-1]
    if tail.lower().startswith("get") and len(tail) > 3:
        tail = tail[3:]
    return tail


def _humanise(name: str) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    text = re.sub(r"[_\-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def _is_number(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False
