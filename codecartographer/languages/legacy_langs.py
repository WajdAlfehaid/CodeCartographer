"""Adapters for the languages that actually hold up old systems.

COBOL, Fortran, Visual Basic, Pascal/Delphi and PL/SQL do not use braces;
they close blocks with keywords, and two of them care about column numbers.
:class:`KeywordBlockAdapter` gives them a shared implementation: find the
declaration line, then walk forward counting block openers and closers.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Pattern, Sequence, Tuple

from ..models import (
    CallSite,
    FieldDef,
    FunctionDef,
    ImportDef,
    Parameter,
    SourceFile,
    TypeDef,
)
from ..tokenizer import (
    BlockComment,
    LineComment,
    ScanResult,
    StringRule,
    Syntax,
)
from .base import LanguageAdapter

I = re.IGNORECASE
IM = re.IGNORECASE | re.MULTILINE


class KeywordBlockAdapter(LanguageAdapter):
    """Base for languages whose blocks end with a keyword, not a brace."""

    #: ``(regex, kind)`` pairs; regex must expose group ``name``.
    routine_patterns: Sequence[Tuple[Pattern, str]] = ()
    type_patterns: Sequence[Tuple[Pattern, str]] = ()
    import_patterns: Sequence[Pattern] = ()
    #: Regexes (matched per line) that open and close a nesting level.
    block_open_re: Optional[Pattern] = None
    block_close_re: Optional[Pattern] = None
    #: Regex for an explicit terminator such as ``End Sub``.
    routine_end_re: Optional[Pattern] = None
    #: Regex matching a *nested* routine declaration. Most legacy languages do
    #: not nest routines at all, so the default is "never", and the end of a
    #: routine is simply its first terminator.
    routine_open_re: Optional[Pattern] = None
    #: Whether a declaration with no body still counts as a routine.
    allow_empty_routines: bool = False
    call_patterns: Sequence[Pattern] = ()
    field_patterns: Sequence[Pattern] = ()

    def balance_pairs(self):
        return (("(", ")"),)

    def extract(self, sf: SourceFile, sr: ScanResult) -> None:
        lines = [sr.masked_line(i) for i in range(1, sr.total_lines + 1)]
        self._extract_imports(sf, sr, lines)
        types = self._extract_types(sf, sr, lines)
        self._extract_routines(sf, sr, lines, types)
        self._extract_fields(sf, sr, lines, types)

    # -- pieces -------------------------------------------------------------

    def _extract_imports(self, sf, sr, lines) -> None:
        for pat in self.import_patterns:
            for idx, line in enumerate(lines, 1):
                m = pat.match(line.strip())
                if not m:
                    continue
                raw = m.group("module")
                for part in re.split(r"[,\s]+", raw.strip().rstrip(";.")):
                    if part:
                        sf.imports.append(ImportDef(
                            raw=line.strip()[:120], line=idx, module=part))

    def _extract_types(self, sf, sr, lines) -> List[TypeDef]:
        out: List[TypeDef] = []
        for pat, kind in self.type_patterns:
            for idx, line in enumerate(lines, 1):
                m = pat.match(line.strip())
                if not m:
                    continue
                end = self._find_block_end(lines, idx)
                td = TypeDef(name=m.group("name"), kind=kind,
                             file=sf.rel_path, line=idx, end_line=end,
                             extends=_names(m.groupdict().get("extends")))
                out.append(td)
        sf.types = out
        return out

    def _owner_at(self, types: List[TypeDef], line: int) -> Optional[TypeDef]:
        best, span = None, None
        for t in types:
            if t.line <= line <= t.end_line:
                size = t.end_line - t.line
                if span is None or size < span:
                    best, span = t, size
        return best

    def _extract_routines(self, sf, sr, lines, types) -> None:
        # Gather candidates from every pattern first, then walk them in source
        # order. Claiming per-pattern instead lets a `Sub` further down the
        # file swallow a `Function` that appears above it.
        candidates: List[Tuple[int, object, str]] = []
        for pat, kind in self.routine_patterns:
            for idx, line in enumerate(lines, 1):
                m = pat.match(line.strip())
                if m:
                    candidates.append((idx, m, kind))
        candidates.sort(key=lambda c: c[0])

        claimed: List[Tuple[int, int]] = []
        for idx, m, kind in candidates:
            if any(a < idx <= b for a, b in claimed):
                continue
            end = self._find_routine_end(lines, idx)
            if end <= idx and not self.allow_empty_routines:
                continue        # a forward declaration, not an implementation
            claimed.append((idx, end))

            gd = m.groupdict()
            raw_name = gd["name"]
            # `TLedger.ApplyInterest` in Pascal names its own owner.
            qualifier, _, simple = raw_name.rpartition(".")
            owner = self._owner_at(types, idx)
            if qualifier:
                match = next((t for t in types
                              if t.name.lower() == qualifier.lower()), None)
                owner = match or owner

            body = "\n".join(lines[idx:end])
            fn = FunctionDef(
                name=simple,
                file=sf.rel_path,
                line=idx,
                end_line=end,
                language=self.name,
                owner=(owner.name if owner else (qualifier or None)),
                params=self._params(gd.get("params") or ""),
                return_type=(gd.get("ret") or "").strip() or None,
                modifiers=self._modifiers(lines[idx - 1]),
                doc=self._doc_above(sr, idx),
            )
            fn.calls = self._find_calls(body, idx)
            fn.metrics = {
                "cyclomatic": self.cyclomatic(body),
                "cognitive": self._keyword_cognitive(lines[idx:end]),
                "max_nesting": self._keyword_nesting(lines[idx:end]),
                "loc": max(1, end - idx + 1),
                "params": len(fn.params),
                "calls": len(fn.calls),
                "kind": kind,
            }
            fn.is_entry_point = self.is_entry_point(fn)
            sf.functions.append(fn)
            if owner:
                owner.methods.append(fn.name)
        sf.functions.sort(key=lambda f: f.line)

    def _extract_fields(self, sf, sr, lines, types) -> None:
        fn_spans = [(f.line, f.end_line) for f in sf.functions]
        for pat in self.field_patterns:
            for idx, line in enumerate(lines, 1):
                m = pat.match(line.strip())
                if not m:
                    continue
                if any(a < idx <= b for a, b in fn_spans):
                    continue
                gd = m.groupdict()
                owner = self._owner_at(types, idx)
                fd = FieldDef(
                    name=gd["name"], line=idx,
                    type=(gd.get("type") or "").strip() or None,
                    owner=owner.name if owner else None,
                    modifiers=self._modifiers(line) | {"global"},
                    initial_value=(gd.get("value") or "").strip() or None,
                )
                if owner:
                    owner.fields.append(fd)
                sf.globals.append(fd)

    # -- block boundaries ---------------------------------------------------

    def _find_routine_end(self, lines: List[str], start: int) -> int:
        """Line number where the routine starting at ``start`` ends.

        Only *routine* nesting is counted. Counting general block openers
        here was a bug: a fixed-form Fortran ``DO`` loop that terminates at a
        labelled ``CONTINUE`` never decrements, so the routine appeared to
        swallow every subroutine below it.
        """
        if self.routine_end_re is not None:
            depth = 0
            for j in range(start, len(lines)):
                text = lines[j].strip()
                if self.routine_open_re and self.routine_open_re.match(text):
                    depth += 1
                    continue
                if self.routine_end_re.match(text):
                    if depth == 0:
                        return j + 1
                    depth -= 1
            return len(lines)
        return self._find_block_end(lines, start)

    def _find_block_end(self, lines: List[str], start: int) -> int:
        if not (self.block_open_re and self.block_close_re):
            return len(lines)
        depth = 1
        for j in range(start, len(lines)):
            text = lines[j].strip()
            if self.block_close_re.match(text):
                depth -= 1
                if depth <= 0:
                    return j + 1
            elif self.block_open_re.match(text):
                depth += 1
        return len(lines)

    def _keyword_nesting(self, body_lines: List[str]) -> int:
        if not (self.block_open_re and self.block_close_re):
            return 0
        depth = peak = 0
        for line in body_lines:
            text = line.strip()
            if self.block_close_re.match(text):
                depth = max(0, depth - 1)
            elif self.block_open_re.match(text):
                depth += 1
                peak = max(peak, depth)
        return peak

    def _keyword_cognitive(self, body_lines: List[str]) -> int:
        score = depth = 0
        for line in body_lines:
            text = line.strip()
            if self.block_close_re and self.block_close_re.match(text):
                depth = max(0, depth - 1)
                continue
            hit = any(re.match(rf"\b{kw}\b", text, I)
                      for kw in self.decision_keywords)
            if hit:
                score += 1 + depth
            if self.block_open_re and self.block_open_re.match(text):
                depth += 1
        return score

    # -- small helpers ------------------------------------------------------

    def _params(self, text: str) -> List[Parameter]:
        out: List[Parameter] = []
        for chunk in text.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            chunk = re.sub(r"^(byval|byref|optional|const|var|out|in|in out)\s+",
                           "", chunk, flags=I).strip()
            m = re.match(r"(?P<n>[\w$]+)\s*(?::|\bas\b|\bin\b|\bout\b)?\s*"
                         r"(?P<t>[\w()\s.]*)", chunk, I)
            if m and m.group("n"):
                out.append(Parameter(name=m.group("n"),
                                     type=(m.group("t") or "").strip() or None))
        return out

    def _modifiers(self, line: str) -> set:
        found = set()
        for w in ("public", "private", "protected", "friend", "static",
                  "shared", "global", "overrides", "virtual", "abstract",
                  "mustoverride", "external"):
            if re.search(rf"\b{w}\b", line, I):
                found.add(w)
        return found

    def _find_calls(self, body: str, offset_line: int) -> List[CallSite]:
        calls: List[CallSite] = []
        for pat in self.call_patterns:
            for m in pat.finditer(body):
                name = m.group("name")
                if not name or name.lower() in _KEYWORDS:
                    continue
                line = offset_line + body[:m.start()].count("\n") + 1
                gd = m.groupdict()
                calls.append(CallSite(
                    name=name, line=line,
                    receiver=(gd.get("recv") or "").rstrip(".") or None))
        return calls

    def _doc_above(self, sr: ScanResult, line: int) -> str:
        out: List[str] = []
        probe = line - 1
        while probe >= 1 and probe in sr.comment_lines and len(out) < 12:
            out.append(sr.line_text(probe).strip())
            probe -= 1
        return "\n".join(reversed(out))[:600]


_KEYWORDS = {
    "if", "then", "else", "elseif", "end", "for", "next", "while", "wend",
    "do", "loop", "select", "case", "return", "and", "or", "not", "mod",
    "to", "step", "each", "in", "is", "as", "new", "set", "let", "call",
    "sub", "function", "dim", "print", "write", "read", "open", "close",
    "with", "on", "error", "goto", "resume", "exit", "begin", "declare",
    "perform", "move", "compute", "add", "subtract", "multiply", "divide",
    "display", "accept", "when", "evaluate", "until", "varying", "value",
    "pic", "picture", "integer", "string", "boolean", "char", "true",
    "false", "null", "nothing", "me", "this", "self", "size", "count",
}


def _names(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [p.strip() for p in re.split(r"[,()]", value) if p.strip()]


# ==========================================================================
# COBOL
# ==========================================================================

class CobolAdapter(KeywordBlockAdapter):
    """COBOL, where the unit of reuse is the paragraph and ``PERFORM`` is the call.

    Dead-paragraph detection is one of the few genuinely valuable static
    analyses for COBOL: a program with 400 paragraphs where 90 are never
    performed is extremely common, and nobody can tell by reading.
    """

    name = "cobol"
    label = "COBOL"
    extensions = (".cbl", ".cob", ".cpy", ".ccp", ".cobol")
    family = "mainframe"
    decision_keywords = ("if", "evaluate", "when", "perform until",
                         "perform varying", "search", "at end")
    boolean_operators = (" and ", " or ")

    #: Statement verbs that begin a sentence and are therefore never paragraphs.
    _VERBS = {
        "goback", "exit", "continue", "next",
        "accept", "add", "call", "cancel", "close", "compute", "continue",
        "delete", "display", "divide", "else", "end", "evaluate", "exit",
        "go", "goback", "if", "initialize", "inspect", "merge", "move",
        "multiply", "open", "perform", "read", "release", "return",
        "rewrite", "search", "set", "sort", "start", "stop", "string",
        "subtract", "unstring", "write", "when", "not", "copy", "eject",
        "skip1", "skip2", "skip3", "sql", "exec",
    }

    @property
    def syntax(self) -> Syntax:
        return Syntax(
            name="cobol",
            line_comments=(LineComment("*>"),),
            strings=(
                StringRule('"', '"', escape=None, doubling=True),
                StringRule("'", "'", escape=None, doubling=True),
            ),
            case_sensitive=False,
            code_columns=(8, 72),
            indicator_column=7,
            indicator_comment_chars="*/",
            identifier_extra="_-",
        )

    def balance_pairs(self):
        return (("(", ")"),)

    def extract(self, sf: SourceFile, sr: ScanResult) -> None:
        lines = [sr.masked_line(i) for i in range(1, sr.total_lines + 1)]
        upper = [l.upper() for l in lines]

        # PROGRAM-ID is the compilation unit.
        for idx, line in enumerate(upper, 1):
            m = re.search(r"\bPROGRAM-ID\s*\.\s*([\w-]+)", line)
            if m:
                sf.types.append(TypeDef(
                    name=m.group(1), kind="program", file=sf.rel_path,
                    line=idx, end_line=len(lines)))
                break

        # COPY statements are COBOL's include mechanism.
        for idx, line in enumerate(upper, 1):
            m = re.search(r"\bCOPY\s+([\w-]+)", line)
            if m:
                sf.imports.append(ImportDef(
                    raw=lines[idx - 1].strip()[:120], line=idx,
                    module=m.group(1)))

        proc_start = next(
            (i for i, l in enumerate(upper, 1)
             if re.search(r"\bPROCEDURE\s+DIVISION\b", l)), None)

        self._extract_data_items(sf, lines, upper, proc_start or len(lines))
        if proc_start:
            self._extract_paragraphs(sf, sr, lines, upper, proc_start)

    def _extract_data_items(self, sf, lines, upper, limit) -> None:
        for idx in range(1, min(limit, len(lines) + 1)):
            text = lines[idx - 1]
            m = re.match(r"\s*(0?[1-9]|[1-4][0-9]|66|77|88)\s+([\w-]+)",
                         text.strip())
            if not m:
                continue
            level, nm = m.group(1), m.group(2)
            if nm.upper() in ("FILLER",):
                continue
            pic = re.search(r"\bPIC(?:TURE)?\s+(?:IS\s+)?([\w()$.,+\-/*]+)",
                            upper[idx - 1])
            val = re.search(r"\bVALUE\s+(?:IS\s+)?(.+?)\s*\.?$", text, I)
            sf.globals.append(FieldDef(
                name=nm, line=idx, type=pic.group(1) if pic else None,
                modifiers={"global", f"level-{level.zfill(2)}"},
                initial_value=val.group(1).strip() if val else None))

    def _extract_paragraphs(self, sf, sr, lines, upper, proc_start) -> None:
        """A paragraph is a name in Area A (columns 8-11) followed by a period."""
        starts: List[Tuple[int, str, str]] = []
        for idx in range(proc_start, len(lines) + 1):
            raw = lines[idx - 1]
            if not raw.strip():
                continue
            stripped = raw.lstrip()
            column = len(raw) - len(stripped) + 1
            if column > 11:          # Area A is columns 8-11; Area B is code
                continue
            m = re.match(r"^([A-Z0-9][\w-]*)\s*(SECTION\s*)?\.\s*$",
                         stripped.upper())
            if not m:
                continue
            nm = m.group(1)
            if nm.lower() in self._VERBS or "DIVISION" in stripped.upper():
                continue
            # `END-IF.` and friends are scope terminators, not paragraph names.
            if nm.startswith("END-") or nm in _COBOL_TERMINATORS:
                continue
            starts.append((idx, nm, "section" if m.group(2) else "paragraph"))

        for i, (line_no, nm, kind) in enumerate(starts):
            end = starts[i + 1][0] - 1 if i + 1 < len(starts) else len(lines)
            body = "\n".join(lines[line_no:end])
            body_upper = body.upper()
            fn = FunctionDef(
                name=nm, file=sf.rel_path, line=line_no, end_line=max(end, line_no),
                language=self.name,
                owner=sf.types[0].name if sf.types else None,
                modifiers={kind},
                doc=self._doc_above(sr, line_no),
            )
            fn.calls = self._cobol_calls(body_upper, line_no)
            fn.metrics = {
                "cyclomatic": self._cobol_cyclomatic(body_upper),
                "cognitive": self._cobol_cognitive(body.split("\n")),
                "max_nesting": self._cobol_nesting(body.split("\n")),
                "loc": max(1, end - line_no + 1),
                "params": 0,
                "calls": len(fn.calls),
                "kind": kind,
            }
            # The first paragraph after PROCEDURE DIVISION is the entry point.
            fn.is_entry_point = (i == 0) or nm.upper().endswith("-MAIN") or \
                nm.upper().startswith("MAIN")
            sf.functions.append(fn)
            if sf.types:
                sf.types[0].methods.append(nm)

    def _cobol_calls(self, body_upper: str, offset: int) -> List[CallSite]:
        calls: List[CallSite] = []

        def add(name: str, pos: int, ctor: bool = False) -> None:
            calls.append(CallSite(
                name=name, line=offset + body_upper[:pos].count("\n") + 1,
                is_constructor=ctor))

        for m in re.finditer(
                r"\bPERFORM\s+(?:(\d+)\s+TIMES|([\w-]+)"
                r"(?:\s+(?:THRU|THROUGH)\s+([\w-]+))?)", body_upper):
            if m.group(2):
                add(m.group(2), m.start(2))
            if m.group(3):
                add(m.group(3), m.start(3))
        for m in re.finditer(r"\bGO\s+TO\s+([\w-]+)", body_upper):
            add(m.group(1), m.start(1))
        for m in re.finditer(r"\bCALL\s+['\"]?([\w-]+)", body_upper):
            add(m.group(1), m.start(1), ctor=True)
        return calls

    def _cobol_cyclomatic(self, upper: str) -> int:
        score = 1
        for pat in (r"\bIF\b", r"\bWHEN\b", r"\bUNTIL\b", r"\bVARYING\b",
                    r"\bAND\b", r"\bOR\b", r"\bAT\s+END\b",
                    r"\bINVALID\s+KEY\b", r"\bON\s+SIZE\s+ERROR\b"):
            score += len(re.findall(pat, upper))
        return score

    def _cobol_nesting(self, body_lines: List[str]) -> int:
        depth = peak = 0
        for line in body_lines:
            u = line.upper()
            depth += len(re.findall(r"\bIF\b|\bEVALUATE\b", u))
            depth -= len(re.findall(r"\bEND-IF\b|\bEND-EVALUATE\b", u))
            depth = max(0, depth)
            peak = max(peak, depth)
        return peak

    def _cobol_cognitive(self, body_lines: List[str]) -> int:
        score = depth = 0
        for line in body_lines:
            u = line.upper()
            opens = len(re.findall(r"\bIF\b|\bEVALUATE\b", u))
            closes = len(re.findall(r"\bEND-IF\b|\bEND-EVALUATE\b", u))
            if opens:
                score += opens * (1 + depth)
            score += len(re.findall(r"\bWHEN\b", u)) * (1 + depth)
            depth = max(0, depth + opens - closes)
        return score


#: COBOL scope terminators, which look like paragraph labels but are not.
_COBOL_TERMINATORS = {
    "END-IF", "END-EVALUATE", "END-PERFORM", "END-READ", "END-WRITE",
    "END-COMPUTE", "END-ADD", "END-SUBTRACT", "END-MULTIPLY", "END-DIVIDE",
    "END-CALL", "END-STRING", "END-UNSTRING", "END-SEARCH", "END-START",
    "END-DELETE", "END-REWRITE", "END-RETURN", "END-EXEC", "EXIT",
}


# ==========================================================================
# Fortran
# ==========================================================================

class FortranAdapter(KeywordBlockAdapter):
    name = "fortran"
    label = "Fortran"
    extensions = (".f", ".for", ".f77", ".f90", ".f95", ".f03", ".ftn")
    family = "scientific"
    decision_keywords = ("if", "do", "select case", "case", "where", "forall")
    boolean_operators = (".and.", ".or.")

    routine_patterns = (
        (re.compile(r"^(?:\w+\s+)?SUBROUTINE\s+(?P<name>\w+)\s*"
                    r"(?:\(\s*(?P<params>[^)]*)\))?", I), "subroutine"),
        (re.compile(r"^(?:(?P<ret>INTEGER|REAL|DOUBLE\s+PRECISION|COMPLEX|"
                    r"LOGICAL|CHARACTER)[\w()*\s]*\s+)?FUNCTION\s+(?P<name>\w+)"
                    r"\s*(?:\(\s*(?P<params>[^)]*)\))?", I), "function"),
        (re.compile(r"^PROGRAM\s+(?P<name>\w+)", I), "program"),
    )
    type_patterns = (
        (re.compile(r"^MODULE\s+(?!PROCEDURE\b)(?P<name>\w+)\s*$", I), "module"),
    )
    import_patterns = (
        re.compile(r"^USE\s+(?P<module>\w+)", I),
        re.compile(r"^INCLUDE\s+['\"](?P<module>[^'\"]+)", I),
    )
    routine_end_re = re.compile(
        r"^END\s*(?:SUBROUTINE|FUNCTION|PROGRAM)?\s*\w*\s*$", I)
    block_open_re = re.compile(r"^(?:IF\b.*\bTHEN\b|DO\b|SELECT\s+CASE\b)", I)
    block_close_re = re.compile(
        r"^(?:END\s*(?:IF|DO|SELECT)\b|\d+\s+CONTINUE\b)", I)
    call_patterns = (
        re.compile(r"\bCALL\s+(?P<name>\w+)", I),
    )

    @property
    def syntax(self) -> Syntax:
        return Syntax(
            name="fortran",
            line_comments=(
                LineComment("!"),
                LineComment("C", column=1, ignore_case=True, word_boundary=True),
                LineComment("*", column=1),
            ),
            strings=(
                StringRule('"', '"', escape=None, doubling=True),
                StringRule("'", "'", escape=None, doubling=True),
            ),
            case_sensitive=False,
        )


# ==========================================================================
# Visual Basic (VB6 / VBA / VB.NET)
# ==========================================================================

class VisualBasicAdapter(KeywordBlockAdapter):
    name = "vb"
    label = "Visual Basic"
    extensions = (".vb", ".bas", ".cls", ".frm", ".vbs", ".ctl")
    family = "rad"
    decision_keywords = ("if", "elseif", "for", "while", "do", "case",
                         "catch", "select")
    boolean_operators = (" And ", " Or ", " AndAlso ", " OrElse ")

    routine_patterns = (
        (re.compile(r"^(?:(?:Public|Private|Friend|Protected|Shared|Static|"
                    r"Overrides|Overridable|MustOverride|Partial)\s+)*"
                    r"Sub\s+(?P<name>\w+)\s*(?:\(\s*(?P<params>[^)]*)\))?", I),
         "sub"),
        (re.compile(r"^(?:(?:Public|Private|Friend|Protected|Shared|Static|"
                    r"Overrides|Overridable|MustOverride|Partial)\s+)*"
                    r"Function\s+(?P<name>\w+)\s*(?:\(\s*(?P<params>[^)]*)\))?"
                    r"(?:\s+As\s+(?P<ret>[\w.()]+))?", I), "function"),
        (re.compile(r"^(?:(?:Public|Private|Friend|Protected|Shared)\s+)*"
                    r"Property\s+(?:Get|Let|Set)?\s*(?P<name>\w+)"
                    r"\s*(?:\(\s*(?P<params>[^)]*)\))?", I), "property"),
    )
    type_patterns = (
        (re.compile(r"^(?:(?:Public|Private|Friend|Partial|MustInherit|"
                    r"NotInheritable)\s+)*Class\s+(?P<name>\w+)"
                    r"(?:\s*\(Of\s+[^)]*\))?"
                    r"(?:\s+Inherits\s+(?P<extends>[\w.]+))?", I), "class"),
        (re.compile(r"^(?:(?:Public|Friend)\s+)*Module\s+(?P<name>\w+)", I),
         "module"),
        (re.compile(r"^(?:(?:Public|Friend)\s+)*Interface\s+(?P<name>\w+)", I),
         "interface"),
    )
    import_patterns = (re.compile(r"^Imports\s+(?P<module>[\w.]+)", I),)
    routine_end_re = re.compile(r"^End\s+(?:Sub|Function|Property)\b", I)
    block_open_re = re.compile(
        r"^(?:If\b.*\bThen\s*$|For\b|While\b|Do\b|Select\s+Case\b|Try\b|"
        r"With\b|SyncLock\b|Using\b)", I)
    block_close_re = re.compile(
        r"^(?:End\s+(?:If|While|Select|Try|With|SyncLock|Using)|Next\b|"
        r"Loop\b|Wend\b)", I)
    call_patterns = (
        re.compile(r"\bCall\s+(?:(?P<recv>[\w.]+)\.)?(?P<name>\w+)", I),
        re.compile(r"(?:(?P<recv>[A-Za-z_]\w*)\.)(?P<name>[A-Za-z_]\w*)\s*\("),
        # Basic lets you invoke a Function without `Call`, which is how most
        # real VB6 code is written.
        re.compile(r"(?<![.\w])(?P<name>[A-Za-z_]\w*)\s*\("),
    )
    field_patterns = (
        re.compile(r"^(?:(?:Public|Private|Friend|Protected|Global|Dim|"
                   r"Const|Shared|Static)\s+)+(?P<name>\w+)"
                   r"(?:\s+As\s+(?P<type>[\w.()]+))?"
                   r"(?:\s*=\s*(?P<value>.+?))?\s*$", I),
    )

    @property
    def syntax(self) -> Syntax:
        return Syntax(
            name="vb",
            line_comments=(
                LineComment("'"),
                LineComment("REM", ignore_case=True, word_boundary=True),
            ),
            strings=(StringRule('"', '"', escape=None, doubling=True),),
            case_sensitive=False,
        )


# ==========================================================================
# Pascal / Delphi
# ==========================================================================

class PascalAdapter(KeywordBlockAdapter):
    name = "pascal"
    label = "Pascal / Delphi"
    extensions = (".pas", ".pp", ".dpr", ".dpk", ".inc_pas", ".lpr")
    family = "rad"
    decision_keywords = ("if", "for", "while", "repeat", "case", "except")
    boolean_operators = (" and ", " or ")

    routine_patterns = (
        (re.compile(r"^(?:class\s+)?procedure\s+(?P<name>[\w.]+)\s*"
                    r"(?:\(\s*(?P<params>[^)]*)\))?\s*;", I), "procedure"),
        (re.compile(r"^(?:class\s+)?function\s+(?P<name>[\w.]+)\s*"
                    r"(?:\(\s*(?P<params>[^)]*)\))?\s*:\s*(?P<ret>[\w.]+)\s*;", I),
         "function"),
        (re.compile(r"^constructor\s+(?P<name>[\w.]+)\s*"
                    r"(?:\(\s*(?P<params>[^)]*)\))?", I), "constructor"),
        (re.compile(r"^destructor\s+(?P<name>[\w.]+)", I), "destructor"),
    )
    type_patterns = (
        (re.compile(r"^(?P<name>\w+)\s*=\s*class\s*"
                    r"(?:\(\s*(?P<extends>[^)]*)\))?", I), "class"),
        (re.compile(r"^unit\s+(?P<name>[\w.]+)\s*;", I), "module"),
    )
    import_patterns = (re.compile(r"^uses\s+(?P<module>[\w.,\s]+);?", I),)
    block_open_re = re.compile(r"^(?:begin\b|case\b|try\b|repeat\b)", I)
    block_close_re = re.compile(r"^(?:end\b|until\b)", I)
    call_patterns = (
        re.compile(r"(?:(?P<recv>[A-Za-z_]\w*)\.)?(?P<name>[A-Za-z_]\w*)\s*\("),
    )

    @property
    def syntax(self) -> Syntax:
        return Syntax(
            name="pascal",
            line_comments=(LineComment("//"),),
            block_comments=(
                BlockComment("{", "}"),
                BlockComment("(*", "*)"),
            ),
            strings=(StringRule("'", "'", escape=None, doubling=True),),
            case_sensitive=False,
        )

    def balance_pairs(self):
        return (("(", ")"), ("[", "]"))

    def _find_routine_end(self, lines: List[str], start: int) -> int:
        """Pascal bodies run from the next ``begin`` to its matching ``end``.

        A declaration in the ``interface`` section has no body at all. Those
        are forward declarations of the implementations further down, and
        counting both would double every routine in the file.
        """
        for j in range(start, min(start + 60, len(lines))):
            text = lines[j].strip()
            if re.match(r"^begin\b", text, I):
                return self._find_block_end(lines, j + 1)
            if re.match(r"^(?:procedure|function|constructor|destructor|"
                        r"implementation|end)\b", text, I):
                return start      # forward declaration: no body, skip it
        return start


# ==========================================================================
# SQL: PL/SQL and T-SQL
# ==========================================================================

class SqlAdapter(KeywordBlockAdapter):
    name = "sql"
    label = "SQL / PL-SQL"
    extensions = (".sql", ".pls", ".plsql", ".pks", ".pkb", ".prc", ".fnc",
                  ".trg", ".tsql")
    family = "database"
    decision_keywords = ("if", "elsif", "case", "when", "loop", "while",
                         "for", "exception")
    boolean_operators = (" and ", " or ")

    routine_patterns = (
        (re.compile(r"^CREATE\s+(?:OR\s+REPLACE\s+)?(?:PROCEDURE|PROC)\s+"
                    r"(?P<name>[\w.\"$#]+)\s*(?:\(\s*(?P<params>[^)]*)\))?", I),
         "procedure"),
        (re.compile(r"^CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+"
                    r"(?P<name>[\w.\"$#]+)\s*(?:\(\s*(?P<params>[^)]*)\))?"
                    r"(?:\s*RETURNS?\s+(?P<ret>[\w()]+))?", I), "function"),
        (re.compile(r"^CREATE\s+(?:OR\s+REPLACE\s+)?TRIGGER\s+"
                    r"(?P<name>[\w.\"$#]+)", I), "trigger"),
        (re.compile(r"^\s*(?:PROCEDURE|FUNCTION)\s+(?P<name>[\w$#]+)\s*"
                    r"(?:\(\s*(?P<params>[^)]*)\))?\s*(?:RETURN\s+\w+\s*)?"
                    r"(?:IS|AS)\b", I), "procedure"),
    )
    type_patterns = (
        (re.compile(r"^CREATE\s+(?:OR\s+REPLACE\s+)?PACKAGE\s+"
                    r"(?:BODY\s+)?(?P<name>[\w.\"$#]+)", I), "module"),
        (re.compile(r"^CREATE\s+TABLE\s+(?P<name>[\w.\"$#]+)", I), "table"),
        (re.compile(r"^CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+(?P<name>[\w.\"$#]+)", I),
         "view"),
    )
    block_open_re = re.compile(r"^(?:BEGIN\b|CASE\b|LOOP\b|IF\b)", I)
    block_close_re = re.compile(r"^END\b", I)
    call_patterns = (
        re.compile(r"\b(?:EXEC(?:UTE)?)\s+(?:(?P<recv>[\w$#]+)\.)?"
                   r"(?P<name>[\w$#]+)", I),
        re.compile(r"(?:(?P<recv>[A-Za-z_]\w*)\.)(?P<name>[A-Za-z_]\w*)\s*\("),
    )

    @property
    def syntax(self) -> Syntax:
        return Syntax(
            name="sql",
            line_comments=(LineComment("--"),),
            block_comments=(BlockComment("/*", "*/"),),
            strings=(
                StringRule("'", "'", escape=None, doubling=True),
                StringRule('"', '"', escape=None, doubling=True,
                           kind="identifier"),
                StringRule("[", "]", escape=None, kind="identifier"),
            ),
            case_sensitive=False,
        )

    def balance_pairs(self):
        return (("(", ")"),)

    def _find_routine_end(self, lines: List[str], start: int) -> int:
        for j in range(start, min(start + 200, len(lines))):
            if re.match(r"^BEGIN\b", lines[j].strip(), I):
                return self._find_block_end(lines, j + 1)
        # Statement-level routine: ends at the next CREATE or a lone slash.
        for j in range(start, len(lines)):
            text = lines[j].strip()
            if text == "/" or re.match(r"^CREATE\b", text, I):
                return j
        return len(lines)


# ==========================================================================
# Perl, Ruby, Shell
# ==========================================================================

class PerlAdapter(KeywordBlockAdapter):
    name = "perl"
    label = "Perl"
    extensions = (".pl", ".pm", ".t", ".plx", ".perl")
    family = "scripting"
    decision_keywords = ("if", "elsif", "unless", "while", "until", "for",
                         "foreach")
    boolean_operators = ("&&", "||", " and ", " or ")

    routine_patterns = (
        (re.compile(r"^sub\s+(?P<name>\w+)", I), "sub"),
    )
    type_patterns = (
        (re.compile(r"^package\s+(?P<name>[\w:]+)\s*;", I), "module"),
    )
    import_patterns = (
        re.compile(r"^(?:use|require)\s+(?P<module>[\w:]+)", I),
    )
    call_patterns = (
        re.compile(r"(?:(?P<recv>[\w:$]+)\s*->\s*)?(?P<name>[A-Za-z_]\w*)\s*\("),
        re.compile(r"\b(?P<name>[a-z_]\w*)\s*\("),
    )

    @property
    def syntax(self) -> Syntax:
        return Syntax(
            name="perl",
            line_comments=(LineComment("#"),),
            strings=(
                StringRule('"', '"', escape="\\"),
                StringRule("'", "'", escape="\\"),
            ),
            doc_blocks=(("=pod", "=cut"), ("=head1", "=cut"),
                        ("=over", "=cut"), ("=item", "=cut")),
            identifier_extra="_",
        )

    def _find_routine_end(self, lines: List[str], start: int) -> int:
        depth = 0
        started = False
        for j in range(start - 1, len(lines)):
            depth += lines[j].count("{")
            if depth:
                started = True
            depth -= lines[j].count("}")
            if started and depth <= 0:
                return j + 1
        return len(lines)


class RubyAdapter(KeywordBlockAdapter):
    name = "ruby"
    label = "Ruby"
    extensions = (".rb", ".rake", ".gemspec")
    family = "scripting"
    decision_keywords = ("if", "elsif", "unless", "while", "until", "for",
                         "case", "when", "rescue")
    boolean_operators = ("&&", "||", " and ", " or ")

    routine_patterns = (
        (re.compile(r"^def\s+(?:self\.)?(?P<name>[\w?!=]+)"
                    r"\s*(?:\(\s*(?P<params>[^)]*)\))?", I), "method"),
    )
    type_patterns = (
        (re.compile(r"^class\s+(?P<name>[\w:]+)"
                    r"(?:\s*<\s*(?P<extends>[\w:]+))?", I), "class"),
        (re.compile(r"^module\s+(?P<name>[\w:]+)", I), "module"),
    )
    import_patterns = (
        re.compile(r"^require(?:_relative)?\s+['\"](?P<module>[^'\"]+)", I),
    )
    routine_end_re = re.compile(r"^end\b", I)
    block_open_re = re.compile(
        r"^(?:if\b|unless\b|while\b|until\b|case\b|begin\b|for\b|def\b|"
        r"class\b|module\b|do\b)", I)
    block_close_re = re.compile(r"^end\b", I)
    call_patterns = (
        re.compile(r"(?:(?P<recv>[A-Za-z_@][\w.]*)\.)?(?P<name>[a-z_]\w*[?!]?)\s*\("),
    )

    @property
    def syntax(self) -> Syntax:
        return Syntax(
            name="ruby",
            line_comments=(LineComment("#"),),
            strings=(
                StringRule('"', '"', escape="\\"),
                StringRule("'", "'", escape="\\"),
            ),
            doc_blocks=(("=begin", "=end"),),
        )


class ShellAdapter(KeywordBlockAdapter):
    name = "shell"
    label = "Shell"
    extensions = (".sh", ".bash", ".ksh", ".zsh")
    filenames = ("makefile",)
    family = "scripting"
    decision_keywords = ("if", "elif", "for", "while", "until", "case")
    boolean_operators = ("&&", "||")

    routine_patterns = (
        (re.compile(r"^(?:function\s+)?(?P<name>[\w.-]+)\s*\(\s*\)\s*\{?"), "function"),
        (re.compile(r"^function\s+(?P<name>[\w.-]+)\s*\{?"), "function"),
    )
    import_patterns = (
        re.compile(r"^(?:\.|source)\s+(?P<module>[\w./$-]+)"),
    )
    call_patterns = (
        re.compile(r"^\s*(?P<name>[a-z_][\w.-]*)\b", re.M),
    )

    @property
    def syntax(self) -> Syntax:
        return Syntax(
            name="shell",
            line_comments=(LineComment("#"),),
            strings=(
                StringRule('"', '"', escape="\\"),
                StringRule("'", "'", escape=None),
            ),
        )

    def _find_routine_end(self, lines: List[str], start: int) -> int:
        depth = 0
        started = False
        for j in range(start - 1, len(lines)):
            depth += lines[j].count("{")
            if depth:
                started = True
            depth -= lines[j].count("}")
            if started and depth <= 0:
                return j + 1
        return len(lines)
