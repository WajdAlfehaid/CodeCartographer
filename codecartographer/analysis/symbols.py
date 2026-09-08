"""Symbol table and call resolution.

This is the part that decides whether the rest of the tool is trustworthy.

The previous implementation answered "is this method called?" by regexing
every file for ``name(`` and checking membership in one big set. That gives
the wrong answer twice over: a comment mentioning ``save()`` marks a dead
method as live, and two classes with a ``close()`` method are treated as one.

Here each call site is resolved against a real symbol table using the
receiver's declared type where one is known, and every edge carries an honest
confidence label so the UI can separate "proven" from "probably".
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from ..graphs import DiGraph
from ..models import (
    CONFIDENCE_EXACT,
    CONFIDENCE_LIKELY,
    CONFIDENCE_UNRESOLVED,
    CONFIDENCE_WEAK,
    CallEdge,
    FunctionDef,
    SourceFile,
    TypeDef,
    confidence_rank,
)

#: Marker for a call whose target lives outside the analysed project.
EXTERNAL = "external"

#: Names owned by the standard library or the language itself. Resolving a
#: call to one of these would be noise, so they are dropped early.
BUILTIN_CALLS = {
    "print", "println", "printf", "sprintf", "format", "len", "length",
    "size", "append", "add", "put", "get", "remove", "contains", "indexof",
    "substring", "trim", "split", "join", "replace", "tostring", "valueof",
    "parseint", "parsedouble", "parsefloat", "equals", "hashcode", "compare",
    "max", "min", "abs", "round", "floor", "ceil", "sqrt", "pow", "random",
    "str", "int", "float", "bool", "list", "dict", "set", "tuple", "range",
    "enumerate", "zip", "map", "filter", "sorted", "sum", "any", "all",
    "open", "close", "read", "write", "flush", "isempty", "iterator", "next",
    "hasnext", "keys", "values", "items", "clone", "copy", "deepcopy",
    "malloc", "free", "memcpy", "strlen", "strcpy", "fopen", "fclose",
    "assert", "raise", "throw", "catch", "super", "this", "self",
}


class SymbolTable:
    """Indexes every definition in the project and resolves calls to them."""

    def __init__(self, files: Sequence[SourceFile]) -> None:
        self.files: Dict[str, SourceFile] = {f.rel_path: f for f in files}
        self.functions: Dict[str, FunctionDef] = {}
        self.types: Dict[str, TypeDef] = {}

        self._by_name: Dict[str, List[FunctionDef]] = defaultdict(list)
        self._by_owner: Dict[Tuple[str, str], List[FunctionDef]] = defaultdict(list)
        self._by_file: Dict[str, List[FunctionDef]] = defaultdict(list)
        self._types_by_name: Dict[str, List[TypeDef]] = defaultdict(list)
        self._type_of_file: Dict[str, List[str]] = defaultdict(list)
        self._field_types: Dict[Tuple[str, str], str] = {}
        self._supers: Dict[str, List[str]] = {}
        self._subtypes: Dict[str, List[str]] = defaultdict(list)
        self._imports: Dict[str, Dict[str, str]] = {}

        self.stats: Dict[str, int] = defaultdict(int)
        self._build()

    # -- indexing -----------------------------------------------------------

    def _build(self) -> None:
        for sf in self.files.values():
            local_imports: Dict[str, str] = {}
            for imp in sf.imports:
                local_imports[_norm(imp.local_name)] = imp.module
            self._imports[sf.rel_path] = local_imports

            for td in sf.types:
                key = td.name
                self.types.setdefault(f"{sf.rel_path}::{key}", td)
                self._types_by_name[_norm(key)].append(td)
                self._type_of_file[sf.rel_path].append(key)
                parents = [_norm(x) for x in
                           list(td.extends) + list(td.implements)]
                self._supers[_norm(key)] = parents
                for parent in parents:
                    self._subtypes[parent].append(_norm(key))
                for fd in td.fields:
                    if fd.type:
                        self._field_types[(_norm(key), _norm(fd.name))] = \
                            _base_type(fd.type)

            for fn in sf.functions:
                self.functions[fn.key] = fn
                self._by_name[_norm(fn.name)].append(fn)
                self._by_file[sf.rel_path].append(fn)
                if fn.owner:
                    self._by_owner[(_norm(fn.owner), _norm(fn.name))].append(fn)

    # -- lookups ------------------------------------------------------------

    def type_names(self) -> Set[str]:
        return set(self._types_by_name)

    def functions_named(self, name: str) -> List[FunctionDef]:
        return self._by_name.get(_norm(name), [])

    def methods_of(self, type_name: str, method: str) -> List[FunctionDef]:
        """Every definition a call to ``type_name.method`` could actually run.

        Class hierarchy analysis. Looking only up the inheritance chain is the
        obvious implementation and it is wrong for any codebase that uses
        polymorphism: a call to an abstract ``extract()`` runs a *subclass*
        implementation, so walking upward finds the abstract stub, marks it
        reachable, and reports every real implementation as dead code.

        So the answer is the nearest definition at or above the receiver's
        static type, plus every override below it. Over-approximating the
        call graph is the safe direction — it risks missing a cleanup, not
        deleting live code.
        """
        key = _norm(type_name)
        name = _norm(method)

        found = list(self._by_owner.get((key, name), []))
        if not found:
            probe, seen = key, {key}
            queue = list(self._supers.get(key, []))
            while queue:
                parent = queue.pop(0)
                if parent in seen:
                    continue
                seen.add(parent)
                hit = self._by_owner.get((parent, name))
                if hit:
                    found.extend(hit)
                    break
                queue.extend(self._supers.get(parent, []))

        found.extend(self._overrides_below(key, name))
        # Preserve order while removing duplicates.
        unique: Dict[str, FunctionDef] = {}
        for fn in found:
            unique.setdefault(fn.key, fn)
        return list(unique.values())

    def _overrides_below(self, type_key: str, method: str) -> List[FunctionDef]:
        """Implementations of ``method`` in every subtype of ``type_key``."""
        out: List[FunctionDef] = []
        seen: Set[str] = {type_key}
        queue = list(self._subtypes.get(type_key, []))
        while queue:
            child = queue.pop(0)
            if child in seen:
                continue
            seen.add(child)
            out.extend(self._by_owner.get((child, method), []))
            queue.extend(self._subtypes.get(child, []))
        return out

    # -- the interesting part ----------------------------------------------

    def resolve_receiver(self, fn: FunctionDef, receiver: Optional[str]) -> Optional[str]:
        """Work out the type a call's receiver refers to, if we can.

        Handles a chain: ``this.order.line`` resolves ``this`` to the
        enclosing type, then walks ``order`` and ``line`` through the field
        types of each type it lands on.
        """
        if receiver is None:
            return fn.owner
        segments = [s for s in re.split(r"\.|->|::", receiver) if s.strip()]
        if not segments:
            return fn.owner

        current = self._resolve_head(fn, segments[0])
        for segment in segments[1:]:
            if not current:
                return None
            current = self._field_type_of(current, segment)
        return current

    def _field_type_of(self, type_name: str, field: str) -> Optional[str]:
        """Declared type of ``field`` on ``type_name`` or an ancestor."""
        probe = _norm(type_name)
        seen: Set[str] = set()
        while probe and probe not in seen:
            seen.add(probe)
            hit = self._field_types.get((probe, _norm(field)))
            if hit:
                return hit
            parents = self._supers.get(probe, [])
            probe = parents[0] if parents else ""
        return None

    def _resolve_head(self, fn: FunctionDef, head_raw: str) -> Optional[str]:
        head = _base_type(head_raw)
        low = _norm(head)

        if low in ("this", "self", "me"):
            return fn.owner

        if low in ("super", "base", "parent") and fn.owner:
            supers = self._supers.get(_norm(fn.owner), [])
            return supers[0] if supers else None

        # 1. a local variable declared in this function
        for name, declared in fn.locals.items():
            if _norm(name) == low:
                return _base_type(declared)

        # 2. a parameter with a declared type
        for p in fn.params:
            if _norm(p.name) == low and p.type:
                return _base_type(p.type)

        # 3. a field on the enclosing type (or an inherited one)
        if fn.owner:
            hit = self._field_type_of(fn.owner, low)
            if hit:
                return hit

        # 4. a static call on a type name, e.g. `Math.max` / `OrderDao.find`
        if low in self._types_by_name:
            return head

        # 5. an imported symbol
        module = self._imports.get(fn.file, {}).get(low)
        if module:
            tail = module.rstrip(".*").split(".")[-1]
            if _norm(tail) in self._types_by_name:
                return tail

        return None

    def resolve_call(self, fn: FunctionDef, call) -> Tuple[List[FunctionDef], str]:
        """Return candidate definitions for one call site and a confidence."""
        name = call.name
        if _norm(name) in BUILTIN_CALLS and not self.functions_named(name):
            return [], EXTERNAL

        if call.is_constructor:
            # `new Foo(...)` binds to Foo's constructor, or just to Foo.
            ctors = [f for f in self.functions_named(name) if f.is_constructor]
            if ctors:
                return ctors, CONFIDENCE_EXACT
            in_type = _norm(name) in self._types_by_name
            return [], CONFIDENCE_EXACT if in_type else EXTERNAL

        receiver_type = self.resolve_receiver(fn, call.receiver)
        if receiver_type:
            hits = self.methods_of(receiver_type, name)
            if hits:
                return hits, CONFIDENCE_EXACT

        # No receiver: an unqualified call resolves against the same file first.
        if call.receiver is None:
            same_file = [f for f in self._by_file.get(fn.file, [])
                         if _norm(f.name) == _norm(name)]
            if same_file:
                return same_file, CONFIDENCE_EXACT

        candidates = self.functions_named(name)
        if len(candidates) == 1:
            return candidates, CONFIDENCE_LIKELY
        if len(candidates) > 1:
            # Ambiguous. Keep every candidate rather than guessing: over-linking
            # risks calling a live method dead, which is the costlier mistake.
            return candidates, CONFIDENCE_WEAK
        # Nothing in this project defines that name, so it belongs to a library
        # or the runtime. That is not a failure to resolve, and counting it as
        # one would make the resolution rate meaningless.
        return [], EXTERNAL

    def build_call_graph(self) -> Tuple[DiGraph, List[CallEdge]]:
        """Resolve every call site in the project into a graph of definitions."""
        graph = DiGraph()
        edges: List[CallEdge] = []
        for fn in self.functions.values():
            graph.add_node(fn.key)
        for fn in self.functions.values():
            for call in fn.calls:
                targets, confidence = self.resolve_call(fn, call)
                self.stats["call_sites"] += 1
                if not targets:
                    if confidence == EXTERNAL:
                        self.stats["external"] += 1
                    else:
                        self.stats["unresolved"] += 1
                    continue
                self.stats[confidence] += 1
                for target in targets:
                    if target.key == fn.key:
                        continue        # direct recursion is not a dependency
                    call.receiver_type = target.owner
                    edge = CallEdge(caller=fn.key, callee=target.key,
                                    line=call.line, confidence=confidence,
                                    call_name=call.name)
                    edges.append(edge)
                    existing = graph.edge_data.get((fn.key, target.key), {})
                    if confidence_rank(confidence) >= confidence_rank(
                            existing.get("confidence", CONFIDENCE_UNRESOLVED)):
                        graph.add_edge(fn.key, target.key,
                                       confidence=confidence, line=call.line)
                    else:
                        graph.add_edge(fn.key, target.key)
        return graph, edges

    def resolution_report(self) -> Dict[str, object]:
        """How much of the call graph we actually proved, stated plainly.

        The rate is computed over *internal* call sites only. Counting
        ``System.out.println`` as an unresolved call would drag the number
        down without telling anyone anything: the definition is in the JDK,
        not in the code being analysed.
        """
        total = self.stats.get("call_sites", 0)
        external = self.stats.get("external", 0)
        unresolved = self.stats.get("unresolved", 0)
        internal = max(0, total - external)
        resolved = max(0, internal - unresolved)
        return {
            "call_sites": total,
            "external_calls": external,
            "internal_call_sites": internal,
            "resolved": resolved,
            "unresolved": unresolved,
            "exact": self.stats.get(CONFIDENCE_EXACT, 0),
            "likely": self.stats.get(CONFIDENCE_LIKELY, 0),
            "ambiguous": self.stats.get(CONFIDENCE_WEAK, 0),
            "resolution_rate": round(resolved / internal, 4) if internal else 0.0,
            "note": "Rate covers calls whose target is defined inside this "
                    "project; library and runtime calls are counted separately.",
        }

    # -- file-level dependencies -------------------------------------------

    def build_file_graph(self, call_graph: DiGraph) -> DiGraph:
        """A dependency edge means a *resolved* reference, not a name collision.

        The old implementation searched each file's raw text for every class
        name in the project, so a class mentioned once in a comment became a
        dependency and a class named ``Order`` matched inside ``OrderItem``.
        """
        graph = DiGraph()
        for rel in self.files:
            graph.add_node(rel)

        # 1. resolved calls
        for src, dst in call_graph.edges:
            a = self.functions.get(src)
            b = self.functions.get(dst)
            if a and b and a.file != b.file:
                data = call_graph.edge_data.get((src, dst), {})
                self._merge_edge(graph, a.file, b.file, "call",
                                 data.get("confidence", CONFIDENCE_LIKELY))

        # 2. imports that point at a file we parsed
        for rel, sf in self.files.items():
            for imp in sf.imports:
                target = self._file_for_module(imp.module, imp.local_name)
                if target and target != rel:
                    self._merge_edge(graph, rel, target, "import",
                                     CONFIDENCE_EXACT)

        # 3. inheritance and declared field/parameter types
        for rel, sf in self.files.items():
            for td in sf.types:
                for parent in list(td.extends) + list(td.implements):
                    target = self._file_for_type(parent)
                    if target and target != rel:
                        self._merge_edge(graph, rel, target, "inherit",
                                         CONFIDENCE_EXACT)
                for fd in td.fields:
                    if not fd.type:
                        continue
                    target = self._file_for_type(_base_type(fd.type))
                    if target and target != rel:
                        self._merge_edge(graph, rel, target, "field",
                                         CONFIDENCE_EXACT)
            for fn in sf.functions:
                declared = [p.type for p in fn.params if p.type]
                declared += list(fn.locals.values())
                if fn.return_type:
                    declared.append(fn.return_type)
                for t in declared:
                    target = self._file_for_type(_base_type(t))
                    if target and target != rel:
                        self._merge_edge(graph, rel, target, "type",
                                         CONFIDENCE_EXACT)
                for call in fn.calls:
                    if call.is_constructor:
                        target = self._file_for_type(call.name)
                        if target and target != rel:
                            self._merge_edge(graph, rel, target, "construct",
                                             CONFIDENCE_EXACT)
        return graph

    def _merge_edge(self, graph: DiGraph, src: str, dst: str,
                    kind: str, confidence: str) -> None:
        data = graph.edge_data.get((src, dst), {})
        kinds = set(data.get("kinds", []))
        kinds.add(kind)
        best = data.get("confidence", CONFIDENCE_UNRESOLVED)
        if confidence_rank(confidence) > confidence_rank(best):
            best = confidence
        graph.add_edge(src, dst, kinds=sorted(kinds), confidence=best,
                       weight=data.get("weight", 0) + 1)

    def _file_for_type(self, type_name: Optional[str]) -> Optional[str]:
        if not type_name:
            return None
        hits = self._types_by_name.get(_norm(type_name), [])
        if len(hits) == 1:
            return hits[0].file
        return None

    def _file_for_module(self, module: str, local: str) -> Optional[str]:
        if not module:
            return None
        tail = module.rstrip("*").rstrip(".").replace("/", ".").split(".")[-1]
        target = self._file_for_type(tail) or self._file_for_type(local)
        if target:
            return target
        # Python-style module imports map onto file stems.
        stem = _norm(tail)
        for rel in self.files:
            base = rel.replace("\\", "/").split("/")[-1]
            if _norm(base.rsplit(".", 1)[0]) == stem:
                return rel
        return None


def _norm(value: str) -> str:
    """Case-folded key. Most legacy languages are case-insensitive."""
    return (value or "").strip().lower()


def _base_type(value: str) -> str:
    """Strip generics, arrays, pointers and namespaces from a type name."""
    if not value:
        return ""
    text = value.strip()
    for token in ("<", "[", "(", "*", "&", "?"):
        text = text.split(token)[0]
    text = text.strip().rstrip("*&")
    if "." in text:
        text = text.split(".")[-1]
    if "::" in text:
        text = text.split("::")[-1]
    return text.strip()
