"""File and project level complexity metrics.

Function-level numbers come from the adapters (which can see real structure).
This module aggregates them and adds the two measures that make the numbers
comparable across a mixed-language codebase: Halstead volume and the
Maintainability Index.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Dict, List, Sequence

from ..models import SourceFile
from ..tokenizer import scan
from ..languages import BY_NAME

_OPERATOR_RE = re.compile(
    r"(\+\+|--|<<=|>>=|\*\*|//|<=|>=|==|!=|&&|\|\||->|=>|::|\+=|-=|\*=|/=|%=|"
    r"[-+*/%=<>!&|^~?:.,;(){}\[\]])")
_OPERAND_RE = re.compile(r"\b[A-Za-z_]\w*\b|\b\d+(?:\.\d+)?\b")

#: Numbers that carry their own meaning and are not "magic".
BENIGN_NUMBERS = {0, 1, 2, -1, 10, 100, 1000, 24, 60, 12, 7, 365, 255, 1024}


def halstead(code: str) -> Dict[str, float]:
    """Halstead measures over masked code.

    Volume is the useful one here: it scales with how much a reader has to
    hold in their head, and unlike raw line count it is not gamed by
    formatting.
    """
    operators = Counter(_OPERATOR_RE.findall(code))
    operands = Counter(_OPERAND_RE.findall(code))
    n1, n2 = len(operators), len(operands)
    total1, total2 = sum(operators.values()), sum(operands.values())
    vocabulary = n1 + n2
    length = total1 + total2
    volume = length * math.log2(vocabulary) if vocabulary > 1 else 0.0
    difficulty = (n1 / 2) * (total2 / n2) if n2 else 0.0
    return {
        "vocabulary": vocabulary,
        "length": length,
        "volume": round(volume, 2),
        "difficulty": round(difficulty, 2),
        "effort": round(volume * difficulty, 2),
    }


def maintainability_index(volume: float, cyclomatic: int, sloc: int,
                          comment_ratio: float = 0.0) -> float:
    """SEI Maintainability Index, normalised to 0-100.

    Below ~65 is the conventional "hard to maintain" line; below 20 usually
    means a rewrite is cheaper than a change.
    """
    volume = max(volume, 1.0)
    sloc = max(sloc, 1)
    mi = (171
          - 5.2 * math.log(volume)
          - 0.23 * cyclomatic
          - 16.2 * math.log(sloc))
    if comment_ratio > 0:
        mi += 50 * math.sin(math.sqrt(2.4 * min(comment_ratio, 1.0)))
    return round(max(0.0, min(100.0, mi * 100.0 / 171.0)), 1)


def count_magic_numbers(sf: SourceFile, masked: str) -> int:
    """Numeric literals in executable positions, excluding declared constants.

    The old version counted every digit run in the raw file, including ones
    inside comments and inside ``static final`` declarations — which are the
    opposite of magic numbers, they are the fix.
    """
    const_lines = {
        g.line for g in sf.globals
        if {"final", "const", "readonly", "static"} & g.modifiers
    }
    for td in sf.types:
        for fd in td.fields:
            if {"final", "const", "readonly"} & fd.modifiers:
                const_lines.add(fd.line)

    count = 0
    for idx, line in enumerate(masked.split("\n"), 1):
        if idx in const_lines:
            continue
        if re.search(r"\b(?:const|final|readonly|parameter|#define)\b", line, re.I):
            continue
        for token in re.findall(r"(?<![\w.])(\d+(?:\.\d+)?)(?![\w.])", line):
            try:
                value = float(token)
            except ValueError:
                continue
            if value in BENIGN_NUMBERS or (value.is_integer() and
                                           int(value) in BENIGN_NUMBERS):
                continue
            count += 1
    return count


class ComplexityAnalyzer:
    def __init__(self, files: Sequence[SourceFile]):
        self.files = list(files)
        self.scores: Dict[str, Dict[str, object]] = {}

    def run(self) -> Dict[str, Dict[str, object]]:
        for sf in self.files:
            adapter = BY_NAME.get(sf.language)
            masked = scan(sf.source, adapter.syntax).mask if adapter else sf.source

            fn_cc = [f.metrics.get("cyclomatic", 1) for f in sf.functions]
            fn_cog = [f.metrics.get("cognitive", 0) for f in sf.functions]
            fn_loc = [f.metrics.get("loc", 1) for f in sf.functions]
            nesting = [f.metrics.get("max_nesting", 0) for f in sf.functions]

            sloc = sf.metrics.get("sloc", sf.lines)
            comment_ratio = sf.metrics.get("comment_ratio", 0.0)
            h = halstead(masked)
            total_cc = sum(fn_cc) if fn_cc else 1
            mi = maintainability_index(h["volume"], total_cc, sloc, comment_ratio)

            self.scores[sf.rel_path] = {
                "language": sf.language,
                "lines": sf.lines,
                "sloc": sloc,
                "comment_lines": sf.metrics.get("comment_lines", 0),
                "comment_ratio": comment_ratio,
                "cyclomatic_total": total_cc,
                "cyclomatic_max": max(fn_cc) if fn_cc else 0,
                "cyclomatic_avg": round(sum(fn_cc) / len(fn_cc), 2) if fn_cc else 0,
                "cognitive_total": sum(fn_cog),
                "cognitive_max": max(fn_cog) if fn_cog else 0,
                "max_nesting": max(nesting) if nesting else 0,
                "longest_function": max(fn_loc) if fn_loc else 0,
                "function_count": len(sf.functions),
                "magic_numbers": count_magic_numbers(sf, masked),
                "global_count": len(sf.globals),
                "halstead": h,
                "maintainability_index": mi,
                "rating": rating_for(mi),
            }
        return self.scores

    def summary(self) -> Dict[str, object]:
        if not self.scores:
            return {}
        mis = [s["maintainability_index"] for s in self.scores.values()]
        sloc = sum(s["sloc"] for s in self.scores.values())
        ccs = [s["cyclomatic_total"] for s in self.scores.values()]
        by_language: Dict[str, Dict[str, float]] = {}
        for path, s in self.scores.items():
            entry = by_language.setdefault(
                s["language"], {"files": 0, "sloc": 0, "mi_total": 0.0})
            entry["files"] += 1
            entry["sloc"] += s["sloc"]
            entry["mi_total"] += s["maintainability_index"]
        for entry in by_language.values():
            entry["mi_avg"] = round(entry["mi_total"] / entry["files"], 1)
            entry.pop("mi_total")

        worst = sorted(self.scores.items(),
                       key=lambda kv: kv[1]["maintainability_index"])[:10]
        return {
            "total_sloc": sloc,
            "average_maintainability": round(sum(mis) / len(mis), 1),
            "median_maintainability": round(sorted(mis)[len(mis) // 2], 1),
            "total_cyclomatic": sum(ccs),
            "files_below_threshold": sum(1 for m in mis if m < 65),
            "by_language": by_language,
            "least_maintainable": [
                {"file": p, "maintainability_index": s["maintainability_index"],
                 "rating": s["rating"], "sloc": s["sloc"]}
                for p, s in worst
            ],
        }


def rating_for(mi: float) -> str:
    if mi >= 85:
        return "Excellent"
    if mi >= 65:
        return "Good"
    if mi >= 40:
        return "Fair"
    if mi >= 20:
        return "Poor"
    return "Critical"
