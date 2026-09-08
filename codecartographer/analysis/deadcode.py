"""Dead code detection by reachability, with honest confidence.

Two different questions get conflated by most tools:

* *Unreferenced* — no resolved call site anywhere points at this function.
* *Unreachable* — no path exists to it from any entry point.

Unreachable is the stronger claim and the more useful one: a cluster of five
functions that only call each other is invisible to reference counting but is
still dead. Both are reported, and every finding carries a confidence plus
the reason it might be wrong, because a developer deleting code needs to know
what the analysis could not see.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Sequence, Set

from ..graphs import DiGraph
from ..languages import BY_NAME
from ..models import FunctionDef, SourceFile
from ..tokenizer import scan
from .symbols import SymbolTable, _norm

#: If any of these appear, names can be invoked from strings at runtime and
#: no static tool can prove a method is dead.
REFLECTION_PATTERNS = (
    (r"\bClass\.forName\b", "Java reflection (Class.forName)"),
    (r"\.getMethod\b|\.getDeclaredMethod\b", "Java reflection (getMethod)"),
    (r"\bMethod\.invoke\b|\.invoke\s*\(", "reflective invocation"),
    (r"\bgetattr\s*\(|\bglobals\s*\(\)|\blocals\s*\(\)", "Python dynamic attribute access"),
    (r"\beval\s*\(|\bexec\s*\(", "eval/exec"),
    (r"\b__import__\s*\(|\bimportlib\b", "dynamic import"),
    (r"\bCALL\s+['\"]?[A-Z-]*\s*USING\b", "COBOL dynamic CALL"),
    (r"\bEXECUTE\s+IMMEDIATE\b|\bsp_executesql\b", "dynamic SQL"),
    (r"\bCallByName\b", "VB CallByName"),
    (r"@Autowired|@Inject|@Bean|@Component", "dependency injection"),
    (r"\bnew\s+\w+\s*\(\s*\)\s*\{", "anonymous class / callback"),
)


class DeadCodeReport:
    def __init__(self) -> None:
        self.unreferenced: List[dict] = []
        self.unreachable: List[dict] = []
        self.unused_imports: List[dict] = []
        self.unused_fields: List[dict] = []
        self.unused_parameters: List[dict] = []
        self.dead_files: List[dict] = []
        self.reflection_warnings: List[dict] = []
        self.entry_points: List[dict] = []
        self.removable_lines: int = 0
        self.mode: str = "application"
        self.mode_note: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "unreferenced": self.unreferenced,
            "unreachable": self.unreachable,
            "unused_imports": self.unused_imports,
            "unused_fields": self.unused_fields,
            "unused_parameters": self.unused_parameters,
            "dead_files": self.dead_files,
            "reflection_warnings": self.reflection_warnings,
            "entry_points": self.entry_points[:200],
            "entry_point_count": len(self.entry_points),
            "mode": self.mode,
            "mode_note": self.mode_note,
            "totals": {
                "unreferenced": len(self.unreferenced),
                "unreachable": len(self.unreachable),
                "unused_imports": len(self.unused_imports),
                "unused_fields": len(self.unused_fields),
                "unused_parameters": len(self.unused_parameters),
                "dead_files": len(self.dead_files),
                "removable_lines": self.removable_lines,
            },
            "confident_findings": len(
                [f for f in self.unreachable if f["confidence"] == "high"]),
        }


class DeadCodeDetector:
    def __init__(self, symbols: SymbolTable, call_graph: DiGraph,
                 file_graph: DiGraph, files: Sequence[SourceFile]):
        self.symbols = symbols
        self.call_graph = call_graph
        self.file_graph = file_graph
        self.files = list(files)
        self.mode = "application"
        self.mode_note = ""
        # Comment and string bodies removed, so a class mentioned only in a
        # comment can never keep its import looking "used".
        self._masked = {}
        for sf in self.files:
            adapter = BY_NAME.get(sf.language)
            self._masked[sf.rel_path] = (
                scan(sf.source, adapter.syntax).mask if adapter else sf.source)
        self._reflection = self._scan_reflection()

    # -- entry points -------------------------------------------------------

    def entry_points(self) -> List[FunctionDef]:
        """Everything the outside world can start execution at.

        Deliberately generous. A false "this is dead" costs a developer a
        production incident; a false "this is alive" costs them nothing but a
        missed cleanup opportunity.

        The mode matters. An application has a ``main`` and reachability from
        it is meaningful. A library has no main, and treating every public
        method as unreachable would flag the entire codebase — a report that
        is technically defensible and completely useless. So the mode is
        detected and stated in the output rather than silently assumed.
        """
        roots: List[FunctionDef] = []
        explicit = 0
        for fn in self.symbols.functions.values():
            if fn.is_entry_point:
                roots.append(fn)
                explicit += 1
                continue
            if fn.is_constructor:
                roots.append(fn)
                continue
            if fn.is_abstract or "abstract" in fn.modifiers:
                roots.append(fn)      # implemented elsewhere, called virtually
            elif any(a.lower().lstrip("@[").rstrip("]").split(".")[-1]
                     in _FRAMEWORK for a in fn.annotations):
                roots.append(fn)
                explicit += 1
            elif fn.name.lower().startswith("test") or \
                    fn.file.lower().find("test") >= 0:
                roots.append(fn)
                explicit += 1

        total = max(1, len(self.symbols.functions))
        reached = len(self.call_graph.reachable_from(f.key for f in roots))
        # Half is the line. Below it the entry points found are not actually
        # the way this code is used — a mixed tree, or an application whose
        # real entry lives in a framework we cannot see — and reporting the
        # other half as unreachable would be noise, not a finding.
        if explicit == 0 or reached / total < 0.5:
            # Library mode: the public API is the entry surface.
            self.mode = "library"
            self.mode_note = (
                "No program entry point covers this codebase, so it is treated "
                "as a library: every public function counts as an entry point. "
                "Findings below are therefore about internal code that nothing "
                "inside the project reaches."
            )
            known = {f.key for f in roots}
            for fn in self.symbols.functions.values():
                if fn.key not in known and fn.is_public:
                    roots.append(fn)
        else:
            self.mode = "application"
            self.mode_note = (
                f"Reachability is measured from {explicit} explicit entry "
                f"point(s) such as main methods, tests and framework hooks."
            )
        return roots

    # -- main ---------------------------------------------------------------

    def run(self) -> DeadCodeReport:
        report = DeadCodeReport()
        report.reflection_warnings = self._reflection

        roots = self.entry_points()
        report.mode = self.mode
        report.mode_note = self.mode_note
        report.entry_points = [
            {"key": f.key, "name": f.qualified_name, "file": f.file,
             "line": f.line, "reason": self._entry_reason(f)}
            for f in roots
        ]

        reachable = self.call_graph.reachable_from(f.key for f in roots)

        for fn in self.symbols.functions.values():
            callers = self.call_graph.predecessors(fn.key)
            is_root = fn.key in {r.key for r in roots}
            if not callers and not is_root:
                report.unreferenced.append(self._finding(fn, "unreferenced"))
            if fn.key not in reachable:
                finding = self._finding(fn, "unreachable")
                report.unreachable.append(finding)
                if finding["confidence"] == "high":
                    report.removable_lines += fn.loc

        report.unused_imports = self._unused_imports()
        report.unused_fields = self._unused_fields()
        report.unused_parameters = self._unused_parameters()
        report.dead_files = self._dead_files(reachable)

        report.unreachable.sort(key=lambda x: (-_RANK[x["confidence"]], -x["loc"]))
        report.unreferenced.sort(key=lambda x: (-_RANK[x["confidence"]], -x["loc"]))
        return report

    # -- findings -----------------------------------------------------------

    def _finding(self, fn: FunctionDef, kind: str) -> dict:
        caveats: List[str] = []
        confidence = "high"

        if fn.is_public:
            confidence = "low"
            caveats.append(
                "Public API — may be called from code outside this project.")
        if self._reflection:
            confidence = "low" if confidence == "high" else confidence
            caveats.append(
                "This project uses runtime dispatch, so no static tool can "
                "prove this is unused.")
        if fn.annotations:
            confidence = _lower(confidence)
            caveats.append("Annotated — a framework may invoke it.")
        if fn.is_abstract:
            confidence = "low"
            caveats.append("Abstract or interface member.")
        if _norm(fn.name).startswith(("get", "set", "is", "has")) and \
                len(fn.params) <= 1:
            confidence = _lower(confidence)
            caveats.append(
                "Accessor — often reached through serialisation or reflection.")

        sf = self.symbols.files.get(fn.file)
        if sf and sf.parse_confidence < 0.8:
            confidence = "low"
            caveats.append("This file did not parse cleanly.")

        hints = [t for t in ("unused", "old", "dead", "temp", "legacy",
                             "deprecated", "obsolete", "backup", "v1", "_bak")
                 if t in _norm(fn.name)]
        if hints:
            confidence = "high" if not caveats else confidence
            caveats.append(f"Name suggests it was already abandoned ({hints[0]}).")

        return {
            "key": fn.key,
            "name": fn.qualified_name,
            "simple_name": fn.name,
            "file": fn.file,
            "line": fn.line,
            "end_line": fn.end_line,
            "loc": fn.loc,
            "language": fn.language,
            "kind": kind,
            "visibility": "public" if fn.is_public else "private",
            "complexity": fn.metrics.get("cyclomatic", 0),
            "confidence": confidence,
            "caveats": caveats,
        }

    def _entry_reason(self, fn: FunctionDef) -> str:
        if fn.name.lower() == "main":
            return "program entry point"
        if fn.annotations:
            return f"annotated {fn.annotations[0]}"
        if fn.is_constructor:
            return "constructor"
        if fn.is_abstract:
            return "abstract / interface member"
        if "test" in fn.file.lower() or fn.name.lower().startswith("test"):
            return "test"
        return "conventional entry name"

    # -- supporting checks --------------------------------------------------

    def _unused_imports(self) -> List[dict]:
        out: List[dict] = []
        for sf in self.files:
            if not sf.imports:
                continue
            import_lines = {i.line for i in sf.imports}
            # Search the code with imports and comments removed, so a class
            # mentioned only in a comment no longer counts as "used".
            body = "\n".join(
                line for n, line in
                enumerate(self._masked[sf.rel_path].split("\n"), 1)
                if n not in import_lines)
            for imp in sf.imports:
                if imp.is_wildcard:
                    continue
                symbol = imp.local_name
                if not symbol or len(symbol) < 2:
                    continue
                if re.search(rf"\b{re.escape(symbol)}\b", body):
                    continue
                out.append({
                    "file": sf.rel_path,
                    "line": imp.line,
                    "module": imp.module,
                    "symbol": symbol,
                    "statement": imp.raw[:120],
                })
        return out

    def _unused_fields(self) -> List[dict]:
        out: List[dict] = []
        for sf in self.files:
            masked = self._masked[sf.rel_path]
            for fd in sf.globals:
                uses = len(re.findall(rf"\b{re.escape(fd.name)}\b", masked))
                if uses > 1:
                    continue
                # A field may be read from another file in the same project.
                external = any(
                    re.search(rf"\b{re.escape(fd.name)}\b",
                              self._masked[other.rel_path])
                    for other in self.files if other.rel_path != sf.rel_path)
                if external:
                    continue
                out.append({
                    "file": sf.rel_path,
                    "line": fd.line,
                    "name": fd.name,
                    "type": fd.type,
                    "owner": fd.owner,
                })
        return out

    def _unused_parameters(self) -> List[dict]:
        out: List[dict] = []
        for sf in self.files:
            lines = self._masked[sf.rel_path].split("\n")
            for fn in sf.functions:
                if fn.is_abstract or not fn.params:
                    continue
                body = "\n".join(lines[fn.line:fn.end_line])
                for p in fn.params:
                    nm = p.name.lstrip("*&")
                    if not nm or len(nm) < 2 or nm.startswith("_"):
                        continue
                    if re.search(rf"\b{re.escape(nm)}\b", body):
                        continue
                    out.append({
                        "file": sf.rel_path,
                        "line": fn.line,
                        "function": fn.qualified_name,
                        "parameter": nm,
                    })
        return out

    def _dead_files(self, reachable: Set[str]) -> List[dict]:
        out: List[dict] = []
        for sf in self.files:
            if not sf.functions:
                continue
            if self.file_graph.in_degree(sf.rel_path) > 0:
                continue
            live = [f for f in sf.functions if f.key in reachable]
            if live:
                continue
            out.append({
                "file": sf.rel_path,
                "language": sf.language,
                "lines": sf.lines,
                "functions": len(sf.functions),
                "reason": "Nothing in this project references it and none of "
                          "its functions are reachable from an entry point.",
            })
        return out

    def _scan_reflection(self) -> List[dict]:
        hits: Dict[str, dict] = {}
        for sf in self.files:
            for pattern, label in REFLECTION_PATTERNS:
                m = re.search(pattern, sf.source)
                if not m:
                    continue
                entry = hits.setdefault(label, {
                    "pattern": label, "files": [], "count": 0})
                entry["count"] += len(re.findall(pattern, sf.source))
                if len(entry["files"]) < 8:
                    entry["files"].append(sf.rel_path)
        return sorted(hits.values(), key=lambda x: -x["count"])


_FRAMEWORK = {
    "override", "test", "bean", "component", "service", "repository",
    "controller", "restcontroller", "eventlistener", "scheduled",
    "postconstruct", "predestroy", "autowired", "inject", "webmethod",
    "before", "after", "beforeeach", "aftereach", "parameterizedtest",
}

_RANK = {"high": 3, "medium": 2, "low": 1}


def _lower(confidence: str) -> str:
    return {"high": "medium", "medium": "low", "low": "low"}[confidence]
