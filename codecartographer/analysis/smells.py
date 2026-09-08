"""Design-level problems, each with the evidence behind it.

A smell is only useful if a developer can check it in ten seconds, so every
finding carries the file, the line, the measured value, the threshold it
crossed, and a concrete next step. No finding is emitted on a hunch.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..graphs import DiGraph, instability
from ..models import FunctionDef, SourceFile
from .symbols import SymbolTable, _norm

# Thresholds. Deliberately conservative: crying wolf on a 30-line method
# trains people to ignore the tool.
T_LONG_METHOD = 60
T_VERY_LONG_METHOD = 150
T_HIGH_CC = 15
T_VERY_HIGH_CC = 30
T_COGNITIVE = 25
T_NESTING = 5
T_PARAMS = 6
T_GOD_METHODS = 25
T_GOD_LINES = 600
T_FAN_OUT = 12
T_FAN_IN = 10
T_DUPLICATE_WINDOW = 6


class Smell(dict):
    """A finding. A dict so it serialises straight to JSON."""

    def __init__(self, *, rule: str, title: str, severity: str, file: str,
                 line: int, detail: str, action: str, value=None,
                 threshold=None, **extra):
        super().__init__(
            rule=rule, title=title, severity=severity, file=file, line=line,
            detail=detail, action=action, value=value, threshold=threshold,
            **extra)


class SmellDetector:
    def __init__(self, files: Sequence[SourceFile], symbols: SymbolTable,
                 call_graph: DiGraph, file_graph: DiGraph,
                 complexity: Dict[str, dict]):
        self.files = list(files)
        self.symbols = symbols
        self.call_graph = call_graph
        self.file_graph = file_graph
        self.complexity = complexity

    def run(self) -> List[Smell]:
        found: List[Smell] = []
        found += self._function_smells()
        found += self._type_smells()
        found += self._coupling_smells()
        found += self._duplication()
        found += self._error_handling()
        found += self._documentation()
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        found.sort(key=lambda s: (severity_order.get(s["severity"], 9),
                                  -(s.get("value") or 0)))
        return found

    # -- function level -----------------------------------------------------

    def _function_smells(self) -> List[Smell]:
        out: List[Smell] = []
        for sf in self.files:
            for fn in sf.functions:
                loc = fn.metrics.get("loc", fn.loc)
                cc = fn.metrics.get("cyclomatic", 1)
                cog = fn.metrics.get("cognitive", 0)
                nest = fn.metrics.get("max_nesting", 0)
                params = len(fn.params)

                if loc >= T_LONG_METHOD:
                    out.append(Smell(
                        rule="long-method",
                        title="Long method",
                        severity="high" if loc >= T_VERY_LONG_METHOD else "medium",
                        file=sf.rel_path, line=fn.line, value=loc,
                        threshold=T_LONG_METHOD,
                        detail=f"{fn.qualified_name} is {loc} lines long.",
                        action="Extract the distinct steps into named helper "
                               "functions; the names become the documentation.",
                        function=fn.qualified_name))

                if cc >= T_HIGH_CC:
                    out.append(Smell(
                        rule="high-complexity",
                        title="High cyclomatic complexity",
                        severity="critical" if cc >= T_VERY_HIGH_CC else "high",
                        file=sf.rel_path, line=fn.line, value=cc,
                        threshold=T_HIGH_CC,
                        detail=f"{fn.qualified_name} has {cc} independent paths, "
                               f"so it needs at least {cc} tests for full branch "
                               f"coverage.",
                        action="Split the branching into separate functions or "
                               "replace the conditional chain with a lookup "
                               "table or polymorphism.",
                        function=fn.qualified_name))

                if cog >= T_COGNITIVE:
                    out.append(Smell(
                        rule="high-cognitive-load",
                        title="Hard to follow",
                        severity="high", file=sf.rel_path, line=fn.line,
                        value=cog, threshold=T_COGNITIVE,
                        detail=f"{fn.qualified_name} scores {cog} on cognitive "
                               f"complexity — deeply nested branching rather "
                               f"than merely lots of it.",
                        action="Flatten with early returns and guard clauses "
                               "before attempting any behavioural change.",
                        function=fn.qualified_name))

                if nest >= T_NESTING:
                    out.append(Smell(
                        rule="deep-nesting",
                        title="Deep nesting",
                        severity="medium", file=sf.rel_path, line=fn.line,
                        value=nest, threshold=T_NESTING,
                        detail=f"{fn.qualified_name} nests {nest} levels deep.",
                        action="Invert the conditions and return early.",
                        function=fn.qualified_name))

                if params >= T_PARAMS:
                    out.append(Smell(
                        rule="long-parameter-list",
                        title="Long parameter list",
                        severity="medium", file=sf.rel_path, line=fn.line,
                        value=params, threshold=T_PARAMS,
                        detail=f"{fn.qualified_name} takes {params} parameters, "
                               f"which makes call sites easy to get wrong.",
                        action="Group related parameters into a small "
                               "parameter object.",
                        function=fn.qualified_name))

                envy = self._feature_envy(fn)
                if envy:
                    other, count = envy
                    out.append(Smell(
                        rule="feature-envy",
                        title="Feature envy",
                        severity="medium", file=sf.rel_path, line=fn.line,
                        value=count, threshold=4,
                        detail=f"{fn.qualified_name} calls into {other} "
                               f"{count} times — more than it uses its own class.",
                        action=f"Consider moving this logic onto {other}.",
                        function=fn.qualified_name))
        return out

    def _feature_envy(self, fn: FunctionDef) -> Optional[Tuple[str, int]]:
        if not fn.owner or len(fn.calls) < 5:
            return None
        counts: Dict[str, int] = defaultdict(int)
        own = 0
        for call in fn.calls:
            target = self.symbols.resolve_receiver(fn, call.receiver)
            if not target:
                continue
            if _norm(target) == _norm(fn.owner):
                own += 1
            else:
                counts[target] += 1
        if not counts:
            return None
        other, count = max(counts.items(), key=lambda kv: kv[1])
        if count >= 4 and count > own * 2:
            return other, count
        return None

    # -- type level ---------------------------------------------------------

    def _type_smells(self) -> List[Smell]:
        out: List[Smell] = []
        for sf in self.files:
            metrics = self.complexity.get(sf.rel_path, {})
            for td in sf.types:
                methods = [f for f in sf.functions if f.owner == td.name]
                span = max(1, td.end_line - td.line)

                if len(methods) >= T_GOD_METHODS or span >= T_GOD_LINES:
                    out.append(Smell(
                        rule="god-class",
                        title="God class",
                        severity="high", file=sf.rel_path, line=td.line,
                        value=len(methods), threshold=T_GOD_METHODS,
                        detail=f"{td.name} has {len(methods)} methods across "
                               f"{span} lines. It almost certainly holds more "
                               f"than one responsibility.",
                        action="Identify clusters of methods that share fields "
                               "and split each cluster into its own class.",
                        type_name=td.name))

                accessors = [f for f in methods
                             if _norm(f.name).startswith(("get", "set", "is"))]
                if methods and len(td.fields) >= 3 and \
                        len(accessors) >= max(4, int(0.85 * len(methods))):
                    out.append(Smell(
                        rule="data-class",
                        title="Data class",
                        severity="low", file=sf.rel_path, line=td.line,
                        value=len(accessors), threshold=len(methods),
                        detail=f"{td.name} is {len(accessors)} accessors and "
                               f"almost no behaviour.",
                        action="Look for logic elsewhere that manipulates this "
                               "class's fields, and move it here.",
                        type_name=td.name))

            mi = metrics.get("maintainability_index")
            if mi is not None and mi < 25 and metrics.get("sloc", 0) > 80:
                out.append(Smell(
                    rule="unmaintainable-file",
                    title="Effectively unmaintainable",
                    severity="critical", file=sf.rel_path, line=1,
                    value=mi, threshold=25,
                    detail=f"Maintainability index {mi}/100 over "
                           f"{metrics.get('sloc')} lines of code.",
                    action="Pin the behaviour down with characterisation tests "
                           "before changing anything in this file."))

            magic = metrics.get("magic_numbers", 0)
            if magic >= 15:
                out.append(Smell(
                    rule="magic-numbers",
                    title="Unexplained constants",
                    severity="medium", file=sf.rel_path, line=1,
                    value=magic, threshold=15,
                    detail=f"{magic} numeric literals appear in executable "
                           f"positions with no named constant.",
                    action="Name them. Each one encodes a business rule that "
                           "is currently invisible."))

            if metrics.get("global_count", 0) >= 8:
                out.append(Smell(
                    rule="global-state",
                    title="Heavy global state",
                    severity="high", file=sf.rel_path, line=1,
                    value=metrics["global_count"], threshold=8,
                    detail=f"{metrics['global_count']} module-level or static "
                           f"variables.",
                    action="Shared mutable state makes this file impossible to "
                           "test in isolation; pass state in explicitly."))
        return out

    # -- coupling -----------------------------------------------------------

    def _coupling_smells(self) -> List[Smell]:
        out: List[Smell] = []
        for cycle in self.file_graph.cycles():
            if len(cycle) < 2:
                continue
            out.append(Smell(
                rule="circular-dependency",
                title="Circular dependency",
                severity="high" if len(cycle) > 2 else "medium",
                file=cycle[0], line=1, value=len(cycle), threshold=1,
                detail="These files depend on each other in a loop: "
                       + " → ".join(cycle) + f" → {cycle[0]}.",
                action="Break the loop by extracting the shared abstraction "
                       "into a third module that both can depend on.",
                cycle=cycle))

        for sf in self.files:
            rel = sf.rel_path
            fan_out = self.file_graph.out_degree(rel)
            fan_in = self.file_graph.in_degree(rel)
            if fan_out >= T_FAN_OUT:
                out.append(Smell(
                    rule="high-fan-out",
                    title="Depends on too much",
                    severity="medium", file=rel, line=1,
                    value=fan_out, threshold=T_FAN_OUT,
                    detail=f"{rel} depends on {fan_out} other files, so it "
                           f"breaks whenever any of them changes.",
                    action="Introduce an interface so this file depends on a "
                           "contract instead of many concrete modules."))
            if fan_in >= T_FAN_IN:
                out.append(Smell(
                    rule="change-amplifier",
                    title="Change amplifier",
                    severity="high", file=rel, line=1,
                    value=fan_in, threshold=T_FAN_IN,
                    detail=f"{fan_in} files depend on {rel}. Every change here "
                           f"can ripple into all of them.",
                    action="Treat as a published contract: add tests first, "
                           "change behaviour second.",
                    instability=instability(self.file_graph, rel)))
        return out

    # -- duplication --------------------------------------------------------

    def _duplication(self) -> List[Smell]:
        """Find repeated blocks by hashing normalised sliding windows.

        Normalisation strips whitespace and string/number literals, so renamed
        constants still match while genuinely different code does not.
        """
        buckets: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
        for sf in self.files:
            lines = [_normalise(l) for l in sf.source.split("\n")]
            for i in range(len(lines) - T_DUPLICATE_WINDOW):
                window = lines[i:i + T_DUPLICATE_WINDOW]
                if sum(1 for w in window if w) < T_DUPLICATE_WINDOW:
                    continue
                if len("".join(window)) < 80:
                    continue
                digest = hashlib.blake2b("\n".join(window).encode(),
                                         digest_size=12).hexdigest()
                buckets[digest].append((sf.rel_path, i + 1))

        out: List[Smell] = []
        for digest, hits in buckets.items():
            if len(hits) < 2:
                continue
            # Collapse overlapping windows inside the same file.
            distinct: List[Tuple[str, int]] = []
            for path, line in sorted(hits):
                if distinct and distinct[-1][0] == path and \
                        line - distinct[-1][1] < T_DUPLICATE_WINDOW:
                    continue
                distinct.append((path, line))
            if len(distinct) < 2:
                continue
            files = sorted({p for p, _ in distinct})
            out.append(Smell(
                rule="duplicate-code",
                title="Duplicated block",
                severity="medium" if len(distinct) < 4 else "high",
                file=distinct[0][0], line=distinct[0][1],
                value=len(distinct), threshold=2,
                detail=f"A {T_DUPLICATE_WINDOW}-line block appears "
                       f"{len(distinct)} times across {len(files)} file(s).",
                action="Extract it once. Duplicated logic is where bugs get "
                       "fixed in one copy and not the others.",
                occurrences=[{"file": p, "line": l} for p, l in distinct[:8]]))
        # Keep the report readable.
        out.sort(key=lambda s: -s["value"])
        return out[:40]

    # -- error handling -----------------------------------------------------

    def _error_handling(self) -> List[Smell]:
        out: List[Smell] = []
        patterns = (
            (r"catch\s*\([^)]*\)\s*\{\s*\}", "empty-catch",
             "Swallowed exception",
             "An exception is caught and discarded, so failures here are "
             "silent.",
             "Log it, wrap it, or let it propagate — but do not hide it."),
            (r"except[^\n:]*:\s*\n\s*pass\b", "empty-catch",
             "Swallowed exception",
             "An exception is caught and discarded, so failures here are "
             "silent.",
             "Log it, wrap it, or let it propagate — but do not hide it."),
            (r"On\s+Error\s+Resume\s+Next", "on-error-resume-next",
             "Errors ignored globally",
             "`On Error Resume Next` makes every subsequent statement ignore "
             "failures.",
             "Replace with targeted error handling around the statement that "
             "can actually fail."),
            (r"catch\s*\(\s*(?:Exception|Throwable|std::exception)\s", "broad-catch",
             "Overly broad catch",
             "Catching the base exception type hides programming errors "
             "alongside expected failures.",
             "Catch the specific exceptions this block can recover from."),
            (r"except\s*:\s*\n", "broad-catch",
             "Bare except",
             "A bare `except:` also swallows KeyboardInterrupt and SystemExit.",
             "Catch the specific exception type."),
        )
        for sf in self.files:
            for pattern, rule, title, detail, action in patterns:
                for m in re.finditer(pattern, sf.source, re.IGNORECASE):
                    line = sf.source[:m.start()].count("\n") + 1
                    out.append(Smell(
                        rule=rule, title=title, severity="high",
                        file=sf.rel_path, line=line, value=1, threshold=0,
                        detail=detail, action=action))
        return out[:60]

    # -- documentation ------------------------------------------------------

    def _documentation(self) -> List[Smell]:
        out: List[Smell] = []
        for sf in self.files:
            metrics = self.complexity.get(sf.rel_path, {})
            complex_undocumented = [
                f for f in sf.functions
                if f.metrics.get("cyclomatic", 1) >= 10 and not f.doc
            ]
            if len(complex_undocumented) >= 3:
                out.append(Smell(
                    rule="undocumented-complexity",
                    title="Complex and undocumented",
                    severity="medium", file=sf.rel_path,
                    line=complex_undocumented[0].line,
                    value=len(complex_undocumented), threshold=3,
                    detail=f"{len(complex_undocumented)} functions here are "
                           f"branch-heavy with no explanatory comment.",
                    action="Write down why the rules are what they are — the "
                           "code already says what it does.",
                    functions=[f.qualified_name
                               for f in complex_undocumented[:8]]))

            todos = len(re.findall(r"\b(?:TODO|FIXME|HACK|XXX|KLUDGE|BUG)\b",
                                   sf.source))
            if todos >= 5:
                out.append(Smell(
                    rule="todo-debt",
                    title="Accumulated TODOs",
                    severity="low", file=sf.rel_path, line=1,
                    value=todos, threshold=5,
                    detail=f"{todos} TODO/FIXME/HACK markers in one file.",
                    action="Triage them into tracked work or delete the ones "
                           "that no longer apply."))
        return out


_STRING_RE = re.compile(r"""(['"]).*?\1""")
_NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?\b")


def _normalise(line: str) -> str:
    text = line.strip()
    if not text or text.startswith(("//", "#", "*", "--", "'")):
        return ""
    text = _STRING_RE.sub("'S'", text)
    text = _NUMBER_RE.sub("N", text)
    return re.sub(r"\s+", " ", text)
