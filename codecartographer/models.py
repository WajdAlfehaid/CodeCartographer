"""Core data model shared by every language adapter and analysis pass.

Everything downstream (call graph, dead code, risk, reports) speaks these
types, so adding a language never means touching an analysis pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Set


# --------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"


@dataclass
class Diagnostic:
    """A problem found in a source file."""

    line: int
    code: str                 # stable machine id, e.g. "unbalanced-brace"
    message: str
    severity: str = SEVERITY_WARNING
    column: int = 0
    snippet: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------
# Program structure
# --------------------------------------------------------------------------


@dataclass
class Parameter:
    name: str
    type: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CallSite:
    """A call as written in the source, before resolution."""

    name: str                          # method/function identifier
    line: int
    receiver: Optional[str] = None     # text left of the dot, if any
    receiver_type: Optional[str] = None  # filled in by the resolver
    arg_count: int = 0
    is_constructor: bool = False       # `new Foo(...)`

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FunctionDef:
    """A callable unit: method, function, procedure, paragraph, subroutine."""

    name: str
    file: str
    line: int
    end_line: int
    language: str
    owner: Optional[str] = None            # enclosing type, if any
    params: List[Parameter] = field(default_factory=list)
    return_type: Optional[str] = None
    modifiers: Set[str] = field(default_factory=set)
    annotations: List[str] = field(default_factory=list)
    doc: str = ""
    calls: List[CallSite] = field(default_factory=list)
    reads: List[str] = field(default_factory=list)   # identifiers referenced
    locals: Dict[str, str] = field(default_factory=dict)  # name -> declared type
    is_constructor: bool = False
    is_abstract: bool = False
    is_entry_point: bool = False
    metrics: Dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        """Stable identity across the whole project."""
        owner = self.owner or ""
        return f"{self.file}::{owner}.{self.name}:{self.line}"

    @property
    def qualified_name(self) -> str:
        return f"{self.owner}.{self.name}" if self.owner else self.name

    @property
    def loc(self) -> int:
        return max(1, self.end_line - self.line + 1)

    @property
    def is_public(self) -> bool:
        """Whether this is API surface an outside caller could reach.

        Explicit modifiers win, except that a leading underscore always means
        "internal" — in Python and Ruby that convention *is* the visibility
        system, and there is no modifier to contradict it.
        """
        if self.name.startswith("_") and not self.name.endswith("__"):
            return False
        if "private" in self.modifiers or "internal" in self.modifiers:
            return False
        if "public" in self.modifiers or "export" in self.modifiers:
            return True
        if "protected" in self.modifiers:
            return True
        return True

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["modifiers"] = sorted(self.modifiers)
        d["key"] = self.key
        d["qualified_name"] = self.qualified_name
        d["loc"] = self.loc
        return d


@dataclass
class FieldDef:
    name: str
    line: int
    type: Optional[str] = None
    owner: Optional[str] = None
    modifiers: Set[str] = field(default_factory=set)
    initial_value: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["modifiers"] = sorted(self.modifiers)
        return d


@dataclass
class TypeDef:
    """A class, interface, enum, struct, module, or COBOL program."""

    name: str
    kind: str                 # class | interface | enum | struct | module | program
    file: str
    line: int
    end_line: int
    extends: List[str] = field(default_factory=list)
    implements: List[str] = field(default_factory=list)
    modifiers: Set[str] = field(default_factory=set)
    annotations: List[str] = field(default_factory=list)
    doc: str = ""
    methods: List[str] = field(default_factory=list)   # function names
    fields: List[FieldDef] = field(default_factory=list)
    package: Optional[str] = None

    @property
    def qualified_name(self) -> str:
        return f"{self.package}.{self.name}" if self.package else self.name

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["modifiers"] = sorted(self.modifiers)
        d["fields"] = [f.to_dict() for f in self.fields]
        d["qualified_name"] = self.qualified_name
        return d


@dataclass
class ImportDef:
    raw: str
    line: int
    module: str = ""
    symbol: Optional[str] = None
    is_wildcard: bool = False

    @property
    def local_name(self) -> str:
        if self.symbol:
            return self.symbol
        return self.module.replace("/", ".").split(".")[-1]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["local_name"] = self.local_name
        return d


@dataclass
class SourceFile:
    """One parsed file."""

    path: str                  # absolute path on disk
    rel_path: str              # path relative to the analysed root (the id)
    language: str
    source: str
    size: int = 0
    package: Optional[str] = None
    types: List[TypeDef] = field(default_factory=list)
    functions: List[FunctionDef] = field(default_factory=list)
    imports: List[ImportDef] = field(default_factory=list)
    globals: List[FieldDef] = field(default_factory=list)
    diagnostics: List[Diagnostic] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    parse_confidence: float = 1.0   # 0..1, how much to trust this parse

    @property
    def lines(self) -> int:
        return self.source.count("\n") + 1 if self.source else 0

    @property
    def name(self) -> str:
        return self.rel_path.replace("\\", "/").split("/")[-1]

    @property
    def primary_type(self) -> Optional[TypeDef]:
        if not self.types:
            return None
        # The type whose name matches the file name wins (Java, C#, ...).
        stem = self.name.rsplit(".", 1)[0]
        for t in self.types:
            if t.name == stem:
                return t
        return self.types[0]

    def to_dict(self, include_source: bool = False) -> Dict[str, Any]:
        d = {
            "path": self.path,
            "rel_path": self.rel_path,
            "name": self.name,
            "language": self.language,
            "size": self.size,
            "lines": self.lines,
            "package": self.package,
            "types": [t.to_dict() for t in self.types],
            "functions": [f.to_dict() for f in self.functions],
            "imports": [i.to_dict() for i in self.imports],
            "globals": [g.to_dict() for g in self.globals],
            "diagnostics": [x.to_dict() for x in self.diagnostics],
            "metrics": self.metrics,
            "parse_confidence": self.parse_confidence,
        }
        if include_source:
            d["source"] = self.source
        return d


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------

# How a call site was matched to a definition. Reported honestly so a user
# can tell a proven edge from a guessed one.
CONFIDENCE_EXACT = "exact"        # receiver type known and resolved
CONFIDENCE_LIKELY = "likely"      # unique name match across the project
CONFIDENCE_WEAK = "weak"          # ambiguous name, arbitrary pick
CONFIDENCE_UNRESOLVED = "unresolved"

_CONFIDENCE_RANK = {
    CONFIDENCE_EXACT: 3,
    CONFIDENCE_LIKELY: 2,
    CONFIDENCE_WEAK: 1,
    CONFIDENCE_UNRESOLVED: 0,
}


def confidence_rank(value: str) -> int:
    return _CONFIDENCE_RANK.get(value, 0)


@dataclass
class CallEdge:
    caller: str          # FunctionDef.key
    callee: str          # FunctionDef.key
    line: int
    confidence: str = CONFIDENCE_LIKELY
    call_name: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AnalysisConfig:
    """Everything tunable about a run, in one place."""

    root: str = "."
    include_globs: List[str] = field(default_factory=list)
    exclude_globs: List[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDES))
    languages: Optional[List[str]] = None      # None = auto-detect all
    max_file_bytes: int = 4_000_000
    max_files: int = 20_000
    follow_symlinks: bool = False
    use_git_history: bool = True
    dead_code_min_confidence: str = CONFIDENCE_LIKELY

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


DEFAULT_EXCLUDES = (
    "**/node_modules/**",
    "**/.git/**",
    "**/.hg/**",
    "**/.svn/**",
    "**/venv/**",
    "**/.venv/**",
    "**/__pycache__/**",
    "**/target/**",
    "**/build/**",
    "**/dist/**",
    "**/out/**",
    "**/bin/**",
    "**/obj/**",
    "**/vendor/**",
    "**/.idea/**",
    "**/.gradle/**",
    "**/site-packages/**",
    "**/*.min.js",
)
