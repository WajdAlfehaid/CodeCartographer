"""A configuration-driven lexical scanner shared by every language adapter.

The single biggest source of wrong answers in naive static analysis is
scanning raw source text: a brace inside a string literal, the word ``if``
inside a comment, a class name mentioned in a docstring. Every one of those
produces a phantom dependency or a phantom syntax error.

This module solves that once. :func:`scan` walks a file with the comment and
string rules of its language and returns a *mask*: a string of exactly the
same length as the original where every comment and every string body has
been replaced by spaces, and newlines are preserved. Offsets and line numbers
therefore stay identical, so an adapter can run cheap regexes against the mask
and still report positions in the real file.

It also handles the awkward parts of genuinely old languages: COBOL and
fixed-form Fortran care about column numbers, VB and SQL escape quotes by
doubling them, and Pascal nests its block comments.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class BlockComment:
    open: str
    close: str
    nestable: bool = False


@dataclass(frozen=True)
class StringRule:
    open: str
    close: str
    escape: Optional[str] = "\\"
    #: ``"ab""cd"`` style escaping, used by VB, SQL, Pascal, Fortran.
    doubling: bool = False
    multiline: bool = False
    kind: str = "string"       # "string" | "char" | "identifier"


@dataclass(frozen=True)
class LineComment:
    prefix: str
    #: Only treat as a comment when it starts the line (COBOL ``*``, Fortran ``C``).
    column: Optional[int] = None
    #: Match case-insensitively (Basic ``REM``, Fortran ``C``).
    ignore_case: bool = False
    #: Require a non-identifier character after the prefix (``REM `` not ``REMOVE``).
    word_boundary: bool = False


@dataclass(frozen=True)
class Syntax:
    """The lexical personality of a language."""

    name: str
    line_comments: Tuple[LineComment, ...] = ()
    block_comments: Tuple[BlockComment, ...] = ()
    strings: Tuple[StringRule, ...] = ()
    case_sensitive: bool = True
    #: 1-based inclusive column window that holds real code. Anything outside
    #: it is sequence numbers or punch-card identification. COBOL: (8, 72).
    code_columns: Optional[Tuple[int, int]] = None
    #: A character in this column marks the whole line as a comment.
    indicator_column: Optional[int] = None
    indicator_comment_chars: str = "*/"
    #: Paired documentation blocks, e.g. Perl POD ``=pod`` .. ``=cut``.
    doc_blocks: Tuple[Tuple[str, str], ...] = ()
    #: Languages where ``/`` may start a regex literal rather than division.
    regex_literals: bool = False
    identifier_extra: str = "_"


@dataclass
class Span:
    kind: str      # "line_comment" | "block_comment" | "string" | "char" | "ignored"
    start: int
    end: int
    line: int
    text: str = ""


@dataclass
class ScanResult:
    """Everything a downstream pass needs to read a file safely."""

    source: str
    #: Same length as ``source``; comments and string bodies blanked out.
    mask: str
    spans: List[Span] = field(default_factory=list)
    line_starts: List[int] = field(default_factory=list)
    #: 1-based line numbers that are entirely comment.
    comment_lines: set = field(default_factory=set)
    #: 1-based line numbers with no code and no comment.
    blank_lines: set = field(default_factory=set)
    unterminated: List[Tuple[str, int]] = field(default_factory=list)

    # -- position helpers ---------------------------------------------------

    def line_of(self, offset: int) -> int:
        """1-based line number containing ``offset`` (binary search)."""
        lo, hi = 0, len(self.line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.line_starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    def column_of(self, offset: int) -> int:
        return offset - self.line_starts[self.line_of(offset) - 1] + 1

    def line_text(self, line: int) -> str:
        if line < 1 or line > len(self.line_starts):
            return ""
        start = self.line_starts[line - 1]
        end = self.line_starts[line] if line < len(self.line_starts) else len(self.source)
        return self.source[start:end].rstrip("\n")

    def masked_line(self, line: int) -> str:
        if line < 1 or line > len(self.line_starts):
            return ""
        start = self.line_starts[line - 1]
        end = self.line_starts[line] if line < len(self.line_starts) else len(self.mask)
        return self.mask[start:end].rstrip("\n")

    @property
    def total_lines(self) -> int:
        return len(self.line_starts)

    @property
    def code_lines(self) -> int:
        """Lines carrying at least one non-whitespace code character."""
        return self.total_lines - len(self.comment_lines) - len(self.blank_lines)

    def strings_of_kind(self, kind: str = "string") -> List[Span]:
        return [s for s in self.spans if s.kind == kind]


def _line_starts(source: str) -> List[int]:
    starts = [0]
    for i, ch in enumerate(source):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def _is_ident_char(ch: str, extra: str) -> bool:
    return ch.isalnum() or ch in extra


def scan(source: str, syntax: Syntax) -> ScanResult:
    """Lex ``source`` under ``syntax`` and return a :class:`ScanResult`."""

    n = len(source)
    mask = list(source)
    spans: List[Span] = []
    starts = _line_starts(source)
    unterminated: List[Tuple[str, int]] = []

    def blank(a: int, b: int) -> None:
        for k in range(a, min(b, n)):
            if mask[k] != "\n":
                mask[k] = " "

    def line_at(off: int) -> int:
        lo, hi = 0, len(starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if starts[mid] <= off:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    # ---- column-oriented pre-pass (COBOL, fixed-form Fortran) -------------
    ignored_line_full: set = set()
    if syntax.code_columns or syntax.indicator_column:
        lo_col, hi_col = syntax.code_columns or (1, 10 ** 9)
        for idx, ls in enumerate(starts):
            le = starts[idx + 1] - 1 if idx + 1 < len(starts) else n
            line_no = idx + 1
            raw = source[ls:le]

            if syntax.indicator_column and len(raw) >= syntax.indicator_column:
                ind = raw[syntax.indicator_column - 1]
                if ind in syntax.indicator_comment_chars:
                    blank(ls, le)
                    spans.append(Span("line_comment", ls, le, line_no, raw))
                    ignored_line_full.add(line_no)
                    continue

            # Blank the sequence area and the identification area.
            if syntax.code_columns:
                blank(ls, min(ls + lo_col - 1, le))
                if len(raw) > hi_col:
                    blank(ls + hi_col, le)
                    spans.append(Span("ignored", ls + hi_col, le, line_no))

    # ---- main character scan ---------------------------------------------
    i = 0
    doc_open: Optional[Tuple[str, str]] = None
    prev_significant = ""

    while i < n:
        if line_at(i) in ignored_line_full:
            # Skip to end of that line.
            nl = source.find("\n", i)
            i = n if nl == -1 else nl + 1
            continue

        ch = source[i]
        at_line_start = i == 0 or source[i - 1] == "\n"

        # --- documentation blocks (Perl POD, Ruby =begin) ------------------
        if doc_open is not None:
            end_tok = doc_open[1]
            if at_line_start and source.startswith(end_tok, i):
                nl = source.find("\n", i)
                stop = n if nl == -1 else nl
                blank(i, stop)
                spans.append(Span("block_comment", i, stop, line_at(i)))
                doc_open = None
                i = stop
            else:
                nl = source.find("\n", i)
                stop = n if nl == -1 else nl
                blank(i, stop)
                spans.append(Span("block_comment", i, stop, line_at(i)))
                i = stop + 1
            continue

        if at_line_start and syntax.doc_blocks:
            matched = None
            for start_tok, end_tok in syntax.doc_blocks:
                if source.startswith(start_tok, i):
                    matched = (start_tok, end_tok)
                    break
            if matched:
                doc_open = matched
                continue

        # --- block comments ------------------------------------------------
        blk = None
        for rule in syntax.block_comments:
            if source.startswith(rule.open, i):
                blk = rule
                break
        if blk is not None:
            start = i
            depth = 1
            j = i + len(blk.open)
            while j < n and depth > 0:
                if blk.nestable and source.startswith(blk.open, j):
                    depth += 1
                    j += len(blk.open)
                    continue
                if source.startswith(blk.close, j):
                    depth -= 1
                    j += len(blk.close)
                    continue
                j += 1
            if depth > 0:
                unterminated.append(("block_comment", line_at(start)))
                j = n
            blank(start, j)
            spans.append(Span("block_comment", start, j, line_at(start),
                              source[start:j]))
            i = j
            continue

        # --- line comments ---------------------------------------------------
        lc = None
        for rule in syntax.line_comments:
            if rule.column is not None:
                col = i - starts[line_at(i) - 1] + 1
                if col != rule.column:
                    continue
            hay = source[i:i + len(rule.prefix)]
            target = rule.prefix
            if rule.ignore_case:
                hay, target = hay.lower(), target.lower()
            if hay != target:
                continue
            if rule.word_boundary:
                nxt = i + len(rule.prefix)
                if nxt < n and _is_ident_char(source[nxt], syntax.identifier_extra):
                    continue
            lc = rule
            break
        if lc is not None:
            nl = source.find("\n", i)
            stop = n if nl == -1 else nl
            blank(i, stop)
            spans.append(Span("line_comment", i, stop, line_at(i), source[i:stop]))
            i = stop
            continue

        # --- regex literals (JS) --------------------------------------------
        if syntax.regex_literals and ch == "/" and prev_significant in "(,=:[!&|?{};+-*~^%<>" + "\n":
            j = i + 1
            closed = False
            in_class = False
            while j < n and source[j] != "\n":
                c = source[j]
                if c == "\\":
                    j += 2
                    continue
                if c == "[":
                    in_class = True
                elif c == "]":
                    in_class = False
                elif c == "/" and not in_class:
                    closed = True
                    j += 1
                    break
                j += 1
            if closed:
                blank(i, j)
                spans.append(Span("string", i, j, line_at(i), source[i:j]))
                i = j
                prev_significant = "/"
                continue

        # --- string / char literals -----------------------------------------
        srule = None
        for rule in sorted(syntax.strings, key=lambda r: -len(r.open)):
            if not source.startswith(rule.open, i):
                continue
            # A quote immediately after an identifier char is usually not a
            # literal opener in Basic/Fortran-ish languages (e.g. don't).
            if rule.kind == "char" and rule.doubling and i > 0 and \
                    _is_ident_char(source[i - 1], syntax.identifier_extra):
                continue
            srule = rule
            break
        if srule is not None:
            start = i
            j = i + len(srule.open)
            closed = False
            while j < n:
                c = source[j]
                if c == "\n" and not srule.multiline:
                    break
                if srule.escape and c == srule.escape and j + 1 < n:
                    j += 2
                    continue
                if source.startswith(srule.close, j):
                    if srule.doubling and source.startswith(
                            srule.close, j + len(srule.close)):
                        j += 2 * len(srule.close)
                        continue
                    j += len(srule.close)
                    closed = True
                    break
                j += 1
            if not closed:
                unterminated.append((srule.kind, line_at(start)))
                if not srule.multiline:
                    nl = source.find("\n", start)
                    j = n if nl == -1 else nl
                else:
                    j = n
            body_start = start + len(srule.open)
            body_end = max(body_start, j - len(srule.close)) if closed else j
            blank(body_start, body_end)
            spans.append(Span(srule.kind, start, j, line_at(start),
                              source[start:j]))
            i = j
            prev_significant = '"'
            continue

        if not ch.isspace():
            prev_significant = ch
        i += 1

    masked = "".join(mask)

    # ---- line classification --------------------------------------------
    comment_lines: set = set()
    blank_lines: set = set()
    for idx, ls in enumerate(starts):
        le = starts[idx + 1] - 1 if idx + 1 < len(starts) else n
        line_no = idx + 1
        raw = source[ls:le]
        code = masked[ls:le]
        if not raw.strip():
            blank_lines.add(line_no)
        elif not code.strip():
            comment_lines.add(line_no)

    return ScanResult(
        source=source,
        mask=masked,
        spans=spans,
        line_starts=starts,
        comment_lines=comment_lines,
        blank_lines=blank_lines,
        unterminated=unterminated,
    )


# --------------------------------------------------------------------------
# Balance checking, done on the mask so literals never lie to us
# --------------------------------------------------------------------------

def find_matching(mask: str, open_pos: int, open_ch: str = "{",
                  close_ch: str = "}") -> int:
    """Offset just past the delimiter matching the one at ``open_pos``.

    Operates on a *masked* string, so braces inside strings and comments are
    already gone. Returns ``len(mask)`` when unbalanced.
    """
    depth = 0
    i = open_pos
    n = len(mask)
    while i < n:
        c = mask[i]
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def check_balance(scan_result: ScanResult, pairs: Sequence[Tuple[str, str]] = (
        ("{", "}"), ("(", ")"), ("[", "]"))) -> List[Tuple[str, int, str]]:
    """Return ``(code, line, message)`` for every unbalanced delimiter.

    Unlike the naive version this reports the *innermost unclosed* opener
    rather than the last one seen, which is what a developer actually needs.
    """
    problems: List[Tuple[str, int, str]] = []
    mask = scan_result.mask
    openers = {a: b for a, b in pairs}
    closers = {b: a for a, b in pairs}
    stack: List[Tuple[str, int]] = []

    for i, ch in enumerate(mask):
        if ch in openers:
            stack.append((ch, i))
        elif ch in closers:
            want = closers[ch]
            if not stack:
                problems.append((
                    "unexpected-close",
                    scan_result.line_of(i),
                    f"Found '{ch}' with no matching '{want}'.",
                ))
            elif stack[-1][0] != want:
                got, pos = stack.pop()
                problems.append((
                    "mismatched-delimiter",
                    scan_result.line_of(i),
                    f"Found '{ch}' but the innermost open delimiter is "
                    f"'{got}' from line {scan_result.line_of(pos)}.",
                ))
            else:
                stack.pop()

    for ch, pos in stack:
        problems.append((
            "unclosed-delimiter",
            scan_result.line_of(pos),
            f"'{ch}' opened here is never closed.",
        ))

    for kind, line in scan_result.unterminated:
        problems.append((
            f"unterminated-{kind.replace('_', '-')}",
            line,
            f"Unterminated {kind.replace('_', ' ')} starting here.",
        ))

    return problems


# --------------------------------------------------------------------------
# Ready-made syntaxes
# --------------------------------------------------------------------------

C_STYLE_STRINGS = (
    StringRule('"', '"', escape="\\"),
    StringRule("'", "'", escape="\\", kind="char"),
)

C_STYLE_COMMENTS = (BlockComment("/*", "*/"),)
C_STYLE_LINE = (LineComment("//"),)


def c_like(name: str, **kwargs) -> Syntax:
    base = dict(
        line_comments=C_STYLE_LINE,
        block_comments=C_STYLE_COMMENTS,
        strings=C_STYLE_STRINGS,
    )
    base.update(kwargs)
    return Syntax(name=name, **base)
