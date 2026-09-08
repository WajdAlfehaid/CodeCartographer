"""The language adapter contract, plus the machinery most adapters reuse.

An adapter's job is narrow: turn one file into a :class:`SourceFile` full of
:class:`TypeDef` and :class:`FunctionDef` records. Everything else — call
resolution, dead code, risk, reporting — is language agnostic and lives in
``codecartographer.analysis``.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..models import (
    CallSite,
    Diagnostic,
    FieldDef,
    FunctionDef,
    ImportDef,
    Parameter,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    SourceFile,
    TypeDef,
)
from ..tokenizer import ScanResult, Syntax, check_balance, find_matching, scan


# Keywords that add a branch. Shared across the C family; adapters override.
DEFAULT_DECISION_KEYWORDS = (
    "if", "for", "while", "case", "catch", "elif", "elsif", "when",
    "foreach", "until", "rescue", "except",
)

DEFAULT_BOOLEAN_OPERATORS = ("&&", "||", "??", " and ", " or ")

#: Names that are almost always invoked by a framework or the runtime rather
#: than by code we can see, so treating them as unreachable is a false alarm.
COMMON_ENTRY_NAMES = {
    "main", "run", "start", "init", "initialize", "setup", "teardown",
    "dispose", "close", "execute", "handle", "process", "service",
    "tostring", "equals", "hashcode", "clone", "finalize", "compareto",
    "__init__", "__main__", "__str__", "__repr__", "__eq__", "__hash__",
    "doget", "dopost", "doput", "dodelete", "actionperformed",
}

FRAMEWORK_ANNOTATIONS = {
    "override", "test", "before", "after", "beforeeach", "aftereach",
    "beforeall", "afterall", "bean", "component", "service", "repository",
    "controller", "restcontroller", "requestmapping", "getmapping",
    "postmapping", "putmapping", "deletemapping", "eventlistener",
    "scheduled", "postconstruct", "predestroy", "webmethod", "entrypoint",
    "functionalinterface", "springbootapplication", "autowired", "inject",
}


class LanguageAdapter(ABC):
    """Base class for every language front end."""

    #: Short stable id, e.g. ``"java"``.
    name: str = "unknown"
    #: Human label for reports.
    label: str = "Unknown"
    #: Lowercase file extensions including the dot.
    extensions: Tuple[str, ...] = ()
    #: Exact lowercase file names (Makefile, Dockerfile, ...).
    filenames: Tuple[str, ...] = ()
    #: Rough era marker used by the report to explain *why* code is legacy.
    family: str = "other"

    decision_keywords: Tuple[str, ...] = DEFAULT_DECISION_KEYWORDS
    boolean_operators: Tuple[str, ...] = DEFAULT_BOOLEAN_OPERATORS

    @property
    @abstractmethod
    def syntax(self) -> Syntax:
        """Lexical rules handed to the tokenizer."""

    # -- main entry point ---------------------------------------------------

    def parse(self, path: str, rel_path: str, source: str) -> SourceFile:
        sr = scan(source, self.syntax)
        sf = SourceFile(
            path=path,
            rel_path=rel_path,
            language=self.name,
            source=source,
            size=len(source.encode("utf-8", errors="ignore")),
        )
        try:
            self.extract(sf, sr)
        except Exception as exc:  # a bad parse must never kill a whole run
            sf.parse_confidence = 0.2
            sf.diagnostics.append(Diagnostic(
                line=1,
                code="parse-failure",
                message=f"{self.label} parser could not finish: {exc}",
                severity=SEVERITY_WARNING,
            ))
        self.diagnose(sf, sr)
        self.measure(sf, sr)
        return sf

    @abstractmethod
    def extract(self, sf: SourceFile, sr: ScanResult) -> None:
        """Populate ``sf.types``, ``sf.functions``, ``sf.imports``."""

    # -- shared behaviour ---------------------------------------------------

    def diagnose(self, sf: SourceFile, sr: ScanResult) -> None:
        """Structural problems, reported from the mask so literals can't lie."""
        for code, line, message in check_balance(sr, self.balance_pairs()):
            sf.diagnostics.append(Diagnostic(
                line=line,
                code=code,
                message=message,
                severity=SEVERITY_ERROR,
                snippet=sr.line_text(line).strip()[:160],
            ))

    def balance_pairs(self) -> Sequence[Tuple[str, str]]:
        return (("{", "}"), ("(", ")"), ("[", "]"))

    def measure(self, sf: SourceFile, sr: ScanResult) -> None:
        total = sr.total_lines
        comment = len(sr.comment_lines)
        blank = len(sr.blank_lines)
        sf.metrics.update({
            "lines": total,
            "sloc": max(0, total - comment - blank),
            "comment_lines": comment,
            "blank_lines": blank,
            "comment_ratio": round(comment / total, 4) if total else 0.0,
            "function_count": len(sf.functions),
            "type_count": len(sf.types),
        })

    # -- helpers for subclasses --------------------------------------------

    def is_entry_point(self, fn: FunctionDef) -> bool:
        if fn.name.lower() in COMMON_ENTRY_NAMES:
            return True
        for ann in fn.annotations:
            if ann.lower().lstrip("@[").rstrip("]") in FRAMEWORK_ANNOTATIONS:
                return True
        return False

    def extract_calls(self, sr: ScanResult, start: int, end: int,
                      skip: Iterable[str] = ()) -> List[CallSite]:
        """Find call sites in a masked region.

        Reads the *mask*, so a method name mentioned in a comment or inside a
        string never becomes a call edge. Keywords like ``if (`` are excluded.
        """
        region = sr.mask[start:end]
        skipset = {s.lower() for s in skip} | _NON_CALL_KEYWORDS
        calls: List[CallSite] = []
        for m in _CALL_RE.finditer(region):
            receiver = m.group("recv")
            name = m.group("name")
            if name.lower() in skipset:
                continue
            offset = start + m.start("name")
            args = _arg_count(region, m.end())
            is_new = bool(m.group("new"))
            if receiver:
                receiver = receiver.rstrip(".").rstrip("->").rstrip(":")
            calls.append(CallSite(
                name=name,
                line=sr.line_of(offset),
                receiver=receiver or None,
                arg_count=args,
                is_constructor=is_new,
            ))
        return calls

    def cyclomatic(self, code: str) -> int:
        """McCabe complexity over already-masked code."""
        score = 1
        for kw in self.decision_keywords:
            score += len(re.findall(rf"\b{re.escape(kw)}\b", code, re.IGNORECASE))
        for op in self.boolean_operators:
            score += code.count(op)
        score += code.count("?:")           # elvis
        score += len(re.findall(r"[^?]\?[^?:.]", code))   # ternaries
        return score

    def cognitive(self, code: str) -> int:
        """Cognitive complexity: nesting makes a branch cost more.

        A four-deep ``if`` is far harder to hold in your head than four flat
        ones, and McCabe scores them identically. This is the metric that
        actually predicts "nobody dares touch this method".
        """
        score = 0
        depth = 0
        pattern = re.compile(
            r"[{}]|\b(" + "|".join(re.escape(k) for k in self.decision_keywords) + r")\b",
            re.IGNORECASE,
        )
        for m in pattern.finditer(code):
            tok = m.group(0)
            if tok == "{":
                depth += 1
            elif tok == "}":
                depth = max(0, depth - 1)
            else:
                score += 1 + max(0, depth - 1)
        for op in self.boolean_operators:
            score += code.count(op)
        return score

    def max_nesting(self, code: str) -> int:
        depth = 0
        peak = 0
        for ch in code:
            if ch == "{":
                depth += 1
                peak = max(peak, depth)
            elif ch == "}":
                depth = max(0, depth - 1)
        return peak

    def local_declarations(self, region: str) -> Dict[str, str]:
        """``name -> declared type`` for locals, used to resolve receivers."""
        out: Dict[str, str] = {}
        for m in _LOCAL_DECL_RE.finditer(region):
            out[m.group("name")] = m.group("type")
        for m in _NEW_ASSIGN_RE.finditer(region):
            out.setdefault(m.group("name"), m.group("type"))
        return out

    def split_params(self, text: str) -> List[Parameter]:
        """Parse a parameter list, respecting nested generics and defaults."""
        params: List[Parameter] = []
        for chunk in _split_top_level(text):
            chunk = chunk.strip()
            if not chunk or chunk in ("void", "..."):
                continue
            chunk = chunk.split("=")[0].strip()
            chunk = re.sub(r"^(final|const|in|out|ref|params|@\w+)\s+", "",
                           chunk, flags=re.IGNORECASE).strip()
            parts = re.split(r"\s+", chunk)
            if len(parts) >= 2:
                params.append(Parameter(name=parts[-1].lstrip("*&"),
                                        type=" ".join(parts[:-1])))
            elif parts and parts[0]:
                params.append(Parameter(name=parts[0].lstrip("*&"), type=None))
        return params


# --------------------------------------------------------------------------
# Regexes shared by the brace-language adapters
# --------------------------------------------------------------------------

_CALL_RE = re.compile(
    r"(?P<new>\bnew\s+)?"
    r"(?P<recv>(?:[A-Za-z_$][\w$]*\s*(?:\.|->|::)\s*)+)?"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*\(",
)

_NON_CALL_KEYWORDS = {
    "if", "for", "while", "switch", "catch", "return", "synchronized",
    "sizeof", "typeof", "instanceof", "throw", "do", "else", "elif",
    "with", "assert", "yield", "await", "print", "case", "when", "until",
    "unless", "foreach", "function", "def", "lambda", "and", "or", "not",
    "in", "is", "new", "delete", "using", "namespace", "class", "struct",
}

_LOCAL_DECL_RE = re.compile(
    r"\b(?P<type>[A-Z][\w.]*(?:<[^;{}()]*>)?)\s+(?P<name>[a-z_$][\w$]*)\s*[=;),]",
)

_NEW_ASSIGN_RE = re.compile(
    r"\b(?P<name>[A-Za-z_$][\w$]*)\s*=\s*new\s+(?P<type>[A-Z][\w.]*)",
)


def _split_top_level(text: str, sep: str = ",") -> List[str]:
    """Split on ``sep`` while ignoring separators inside brackets."""
    out: List[str] = []
    depth = 0
    buf: List[str] = []
    for ch in text:
        if ch in "<([{":
            depth += 1
        elif ch in ">)]}":
            depth = max(0, depth - 1)
        if ch == sep and depth == 0:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf))
    return out


def _arg_count(region: str, open_paren_end: int) -> int:
    """Count arguments given the offset just past the ``(``."""
    depth = 1
    i = open_paren_end
    n = len(region)
    args = 0
    seen = False
    while i < n and depth > 0:
        ch = region[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                break
        elif ch == "," and depth == 1:
            args += 1
        elif not ch.isspace():
            seen = True
        i += 1
    return args + 1 if seen else 0


class BraceAdapter(LanguageAdapter):
    """Shared implementation for languages that delimit blocks with ``{}``.

    Java, C, C++, C#, JavaScript, TypeScript, PHP and Go all differ in
    keywords but share a structure: a signature, then a braced body. Instead
    of one gigantic regex per language, subclasses supply the pieces.
    """

    #: Regex matching a type declaration; must expose groups ``kind`` and ``name``.
    type_re: Optional[re.Pattern] = None
    #: Regex matching a callable declaration; must expose ``name`` and ``params``.
    function_re: Optional[re.Pattern] = None
    import_re: Optional[re.Pattern] = None
    package_re: Optional[re.Pattern] = None
    field_re: Optional[re.Pattern] = None
    modifier_words: Tuple[str, ...] = (
        "public", "private", "protected", "static", "final", "abstract",
        "synchronized", "native", "transient", "volatile", "const",
        "virtual", "override", "sealed", "internal", "async", "extern",
        "inline", "export", "default", "readonly",
    )

    def pick_name(self, gd: Dict[str, Optional[str]]) -> Optional[str]:
        """Name group for this match.

        Patterns that use an alternation (JavaScript has three ways to
        declare a function) put the name in ``name``, ``name2`` or ``name3``;
        the first one that matched wins.
        """
        for key in ("name", "name2", "name3", "name4"):
            value = gd.get(key)
            if value:
                return value
        return None

    def pick_params(self, gd: Dict[str, Optional[str]]) -> str:
        for key in ("params", "params2", "params3", "params4"):
            value = gd.get(key)
            if value is not None and value != "":
                return value
        return ""

    def extract(self, sf: SourceFile, sr: ScanResult) -> None:
        self._extract_package(sf, sr)
        self._extract_imports(sf, sr)
        types = self._extract_types(sf, sr)
        self._extract_functions(sf, sr, types)
        self._extract_fields(sf, sr, types)

    # -- pieces -------------------------------------------------------------

    def _extract_package(self, sf: SourceFile, sr: ScanResult) -> None:
        if not self.package_re:
            return
        m = self.package_re.search(sr.mask)
        if m:
            sf.package = m.group("package")

    def _extract_imports(self, sf: SourceFile, sr: ScanResult) -> None:
        if not self.import_re:
            return
        for m in self.import_re.finditer(sr.mask):
            raw = sr.source[m.start():m.end()].strip()
            gd = m.groupdict()
            module = (gd.get("module") or "").strip().strip("\"';")
            symbol = (gd.get("symbol") or "").strip() or None
            sf.imports.append(ImportDef(
                raw=raw,
                line=sr.line_of(m.start()),
                module=module,
                symbol=symbol,
                is_wildcard=module.endswith("*") or module.endswith(".*"),
            ))

    def _extract_types(self, sf: SourceFile, sr: ScanResult) -> List[TypeDef]:
        if not self.type_re:
            return []
        mask = sr.mask
        found: List[TypeDef] = []
        for m in self.type_re.finditer(mask):
            body_open = mask.find("{", m.end() - 1)
            if body_open == -1:
                continue
            body_close = find_matching(mask, body_open)
            gd = m.groupdict()
            td = TypeDef(
                name=gd["name"],
                kind=(gd.get("kind") or "class").strip().lower(),
                file=sf.rel_path,
                line=sr.line_of(m.start()),
                end_line=sr.line_of(min(body_close, len(mask) - 1)),
                extends=_name_list(gd.get("extends")),
                implements=_name_list(gd.get("implements")),
                modifiers=_modifiers(_decl_text(sr.source, m.start(), m.end()),
                                     self.modifier_words),
                annotations=_annotations_before(sr.source, m.start()),
                doc=_doc_before(sr, m.start()),
                package=sf.package,
            )
            td._span = (body_open, body_close)  # type: ignore[attr-defined]
            found.append(td)
        sf.types = found
        return found

    def _owner_for(self, types: List[TypeDef], offset: int) -> Optional[TypeDef]:
        """Innermost type whose body contains ``offset``."""
        best: Optional[TypeDef] = None
        best_size = None
        for t in types:
            span = getattr(t, "_span", None)
            if not span:
                continue
            if span[0] <= offset < span[1]:
                size = span[1] - span[0]
                if best_size is None or size < best_size:
                    best, best_size = t, size
        return best

    def _extract_functions(self, sf: SourceFile, sr: ScanResult,
                           types: List[TypeDef]) -> None:
        if not self.function_re:
            return
        mask = sr.mask
        seen_spans: List[Tuple[int, int]] = []
        for m in self.function_re.finditer(mask):
            body_open = mask.find("{", m.end() - 1)
            if body_open == -1 or body_open - m.end() > 400:
                continue
            # Skip matches inside an already-claimed function body: those are
            # lambdas and nested closures, not declarations.
            if any(a <= m.start() < b for a, b in seen_spans):
                continue
            body_close = find_matching(mask, body_open)
            seen_spans.append((body_open, body_close))

            gd = m.groupdict()
            name = self.pick_name(gd)
            if not name:
                continue
            owner = self._owner_for(types, m.start())
            mods = _modifiers(_decl_text(sr.source, m.start(), m.end()),
                              self.modifier_words)
            body = mask[body_open:body_close]

            fn = FunctionDef(
                name=name,
                file=sf.rel_path,
                line=sr.line_of(m.start()),
                end_line=sr.line_of(min(body_close, len(mask) - 1)),
                language=self.name,
                owner=owner.name if owner else None,
                params=self.split_params(self.pick_params(gd)),
                return_type=(gd.get("ret") or "").strip() or None,
                modifiers=mods,
                annotations=_annotations_before(sr.source, m.start()),
                doc=_doc_before(sr, m.start()),
                is_constructor=bool(owner and owner.name == name),
            )
            fn.calls = self.extract_calls(sr, body_open, body_close,
                                          skip=[name])
            fn.locals = self.local_declarations(body)
            fn.metrics = {
                "cyclomatic": self.cyclomatic(body),
                "cognitive": self.cognitive(body),
                "max_nesting": self.max_nesting(body),
                "loc": max(1, fn.end_line - fn.line + 1),
                "params": len(fn.params),
                "calls": len(fn.calls),
                "returns": len(re.findall(r"\breturn\b", body)),
            }
            fn.is_entry_point = self.is_entry_point(fn)
            sf.functions.append(fn)
            if owner:
                owner.methods.append(name)

    def _extract_fields(self, sf: SourceFile, sr: ScanResult,
                        types: List[TypeDef]) -> None:
        if not self.field_re:
            return
        mask = sr.mask
        fn_spans = [(f.line, f.end_line) for f in sf.functions]
        for m in self.field_re.finditer(mask):
            line = sr.line_of(m.start())
            if any(a < line <= b for a, b in fn_spans):
                continue    # a local variable, not a field
            owner = self._owner_for(types, m.start())
            gd = m.groupdict()
            fd = FieldDef(
                name=gd["name"],
                line=line,
                type=(gd.get("type") or "").strip() or None,
                owner=owner.name if owner else None,
                modifiers=_modifiers(_decl_text(sr.source, m.start(), m.end()),
                                     self.modifier_words),
                initial_value=(gd.get("value") or "").strip() or None,
            )
            if owner:
                owner.fields.append(fd)
            if "static" in fd.modifiers or "global" in fd.modifiers or not owner:
                sf.globals.append(fd)


def _modifiers(text: str, words: Sequence[str]) -> set:
    found = set()
    lowered = text.lower()
    for w in words:
        if re.search(rf"\b{re.escape(w)}\b", lowered):
            found.add(w)
    return found


def _decl_text(source: str, start: int, end: int) -> str:
    """The declaration itself, without bleeding into the previous one.

    Reading a fixed-size window backwards picks up the modifiers of whatever
    came before, which is how a public method ends up reported as private.
    Cut at the nearest statement or block boundary instead.
    """
    line_start = source.rfind("\n", 0, start) + 1
    cut = max(
        source.rfind(";", 0, start),
        source.rfind("}", 0, start),
        source.rfind("{", 0, start),
        line_start - 1,
    )
    return source[cut + 1:end]


def _name_list(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [p.strip().split("<")[0] for p in value.split(",") if p.strip()]


def _annotations_before(source: str, offset: int, window: int = 400) -> List[str]:
    """Annotations attached to this declaration, stopping at the previous one."""
    start = max(0, offset - window)
    chunk = source[start:offset]
    # Everything after the last statement/block boundary belongs to us.
    cut = max(chunk.rfind(";"), chunk.rfind("}"), chunk.rfind("{"))
    chunk = chunk[cut + 1:]
    return re.findall(r"(@[\w.]+|\[[A-Z]\w+(?:\([^)]*\))?\])", chunk)[-6:]


def _doc_before(sr: ScanResult, offset: int) -> str:
    """The doc comment immediately preceding ``offset``, if any."""
    best = ""
    for span in sr.spans:
        if span.kind != "block_comment":
            continue
        gap = sr.source[span.end:offset]
        if span.end <= offset and gap.strip() == "":
            best = span.text
    if not best:
        # Consecutive line comments directly above.
        line = sr.line_of(offset)
        collected: List[str] = []
        probe = line - 1
        while probe >= 1 and probe in sr.comment_lines:
            collected.append(sr.line_text(probe).strip())
            probe -= 1
        best = "\n".join(reversed(collected))
    cleaned = re.sub(r"^\s*(/\*+|\*+/|\*|//+|#+)", "", best, flags=re.MULTILINE)
    return cleaned.strip()[:600]
