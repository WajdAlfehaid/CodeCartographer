"""Language registry and detection."""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional

from .base import LanguageAdapter
from .brace_langs import (
    CAdapter,
    CppAdapter,
    CSharpAdapter,
    GoAdapter,
    JavaAdapter,
    JavaScriptAdapter,
    PhpAdapter,
    TypeScriptAdapter,
)
from .legacy_langs import (
    CobolAdapter,
    FortranAdapter,
    PascalAdapter,
    PerlAdapter,
    RubyAdapter,
    ShellAdapter,
    SqlAdapter,
    VisualBasicAdapter,
)
from .python_lang import PythonAdapter

ADAPTERS: List[LanguageAdapter] = [
    JavaAdapter(),
    PythonAdapter(),
    CobolAdapter(),
    FortranAdapter(),
    VisualBasicAdapter(),
    PascalAdapter(),
    SqlAdapter(),
    CSharpAdapter(),
    CppAdapter(),
    CAdapter(),
    JavaScriptAdapter(),
    TypeScriptAdapter(),
    PhpAdapter(),
    PerlAdapter(),
    RubyAdapter(),
    GoAdapter(),
    ShellAdapter(),
]

BY_NAME: Dict[str, LanguageAdapter] = {a.name: a for a in ADAPTERS}

_BY_EXT: Dict[str, LanguageAdapter] = {}
for _a in ADAPTERS:
    for _ext in _a.extensions:
        _BY_EXT.setdefault(_ext, _a)

_BY_FILENAME: Dict[str, LanguageAdapter] = {}
for _a in ADAPTERS:
    for _fn in _a.filenames:
        _BY_FILENAME.setdefault(_fn, _a)

#: ``.h`` and ``.inc`` are shared; ``.pl`` is Perl or Prolog. Decide by content.
_AMBIGUOUS = {".h", ".inc", ".pl", ".for", ".f", ".cls", ".sql", ".bas"}

_CONTENT_HINTS = (
    (re.compile(r"^\s*IDENTIFICATION\s+DIVISION", re.I | re.M), "cobol"),
    (re.compile(r"^\s*PROGRAM-ID", re.I | re.M), "cobol"),
    (re.compile(r"^\s*(?:CREATE|ALTER)\s+(?:OR\s+REPLACE\s+)?"
                r"(?:PACKAGE|PROCEDURE|FUNCTION|TRIGGER|TABLE)", re.I | re.M), "sql"),
    (re.compile(r"^\s*(?:SUBROUTINE|PROGRAM|MODULE)\s+\w+", re.I | re.M), "fortran"),
    (re.compile(r"^\s*(?:Public|Private)?\s*(?:Sub|Function)\s+\w+", re.M), "vb"),
    (re.compile(r"^\s*(?:unit|program)\s+\w+\s*;", re.I | re.M), "pascal"),
    (re.compile(r"^\s*(?:use\s+strict|package\s+[\w:]+\s*;|sub\s+\w+\s*\{)", re.M),
     "perl"),
    (re.compile(r"^\s*(?:class|template|namespace)\b|::", re.M), "cpp"),
    (re.compile(r"^\s*#include\b", re.M), "c"),
)


def detect(path: str, source: Optional[str] = None) -> Optional[LanguageAdapter]:
    """Pick an adapter for ``path``, using content to break ties."""
    base = os.path.basename(path).lower()
    if base in _BY_FILENAME:
        return _BY_FILENAME[base]

    _, ext = os.path.splitext(base)
    if ext in _AMBIGUOUS and source:
        head = source[:8000]
        for pattern, lang in _CONTENT_HINTS:
            if pattern.search(head):
                candidate = BY_NAME.get(lang)
                if candidate and (ext in candidate.extensions or
                                  lang in ("cobol", "sql", "fortran", "vb",
                                           "pascal", "perl")):
                    return candidate
    return _BY_EXT.get(ext)


def supported_extensions() -> List[str]:
    return sorted(_BY_EXT)


def language_table() -> List[Dict[str, object]]:
    """Everything the UI needs to describe coverage."""
    return [
        {
            "name": a.name,
            "label": a.label,
            "family": a.family,
            "extensions": list(a.extensions),
        }
        for a in ADAPTERS
    ]


__all__ = [
    "ADAPTERS", "BY_NAME", "LanguageAdapter", "detect",
    "language_table", "supported_extensions",
]
