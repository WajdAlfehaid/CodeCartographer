"""Adapters for languages that delimit blocks with braces.

Java, C, C++, C#, JavaScript, TypeScript, PHP and Go share a shape, so they
share :class:`BraceAdapter` and differ only in their declaration patterns.

Every pattern is anchored to a statement boundary — the start of a line, or
just after ``{``, ``}`` or ``;``. That one constraint removes almost all of
the false positives a free-floating regex produces, because a declaration in
these languages always begins a statement while a call almost never does.
Anchoring to the line start alone was not enough: it silently missed every
single-line class body, which is common in tests and generated code.
"""

from __future__ import annotations

import re
from typing import Tuple

from ..models import FunctionDef
from ..tokenizer import (
    BlockComment,
    LineComment,
    StringRule,
    Syntax,
    c_like,
)
from .base import BraceAdapter

M = re.MULTILINE | re.VERBOSE

#: Words that look like a callable name in a declaration regex but never are.
CONTROL_WORDS = {
    "if", "for", "while", "switch", "catch", "synchronized", "return",
    "new", "do", "else", "try", "finally", "case", "default", "throw",
    "using", "lock", "unsafe", "fixed", "foreach", "with", "match",
    "sizeof", "typeof", "assert", "yield", "await", "static_assert",
    "elseif", "declare", "namespace", "extern", "constexpr", "operator",
}


class _ControlFilterMixin:
    """Reject declaration matches whose name is really a control keyword."""

    def _extract_functions(self, sf, sr, types):  # type: ignore[override]
        super()._extract_functions(sf, sr, types)
        sf.functions = [f for f in sf.functions
                        if f.name.lower() not in CONTROL_WORDS]
        for t in types:
            t.methods = [m for m in t.methods if m.lower() not in CONTROL_WORDS]


# --------------------------------------------------------------------------
# Java
# --------------------------------------------------------------------------

class JavaAdapter(_ControlFilterMixin, BraceAdapter):
    name = "java"
    label = "Java"
    extensions = (".java",)
    family = "enterprise-oo"

    type_re = re.compile(r"""
        (?:^|(?<=[{};]))[ \t]*
        (?P<mods>(?:(?:public|protected|private|static|final|abstract|sealed|
                     non-sealed|strictfp)\s+)*)
        (?P<kind>class|interface|enum|record|@interface)\s+
        (?P<name>[A-Za-z_$][\w$]*)
        (?:\s*<[^\n{]*?>)?
        (?:\s*\([^)]*\))?
        (?:\s+extends\s+(?P<extends>[\w.$<>,\s\[\]]+?))?
        (?:\s+implements\s+(?P<implements>[\w.$<>,\s\[\]]+?))?
        (?:\s+permits\s+[\w.,\s]+?)?
        \s*\{
    """, M)

    function_re = re.compile(r"""
        (?:^|(?<=[{};]))[ \t]*
        (?P<mods>(?:(?:public|protected|private|static|final|abstract|
                     synchronized|native|strictfp|default|transient)\s+)*)
        (?:(?P<ret>[A-Za-z_$][\w$.<>\[\],?\s]*?)\s+)?
        (?P<name>[A-Za-z_$][\w$]*)\s*
        \(\s*(?P<params>[^;{}()]*(?:\([^()]*\)[^;{}()]*)*)\s*\)
        (?:\s*throws\s+[\w.,\s]+?)?
        \s*\{
    """, M)

    import_re = re.compile(
        r"(?:^|(?<=[{};]))[ \t]*import\s+(?:static\s+)?(?P<module>[\w.$]+(?:\.\*)?)\s*;", re.M)
    package_re = re.compile(r"(?:^|(?<=[{};]))[ \t]*package\s+(?P<package>[\w.]+)\s*;", re.M)

    field_re = re.compile(r"""
        (?:^|(?<=[{};]))[ \t]*
        (?P<mods>(?:(?:public|protected|private|static|final|transient|
                     volatile)\s+)+)
        (?P<type>[A-Za-z_$][\w$.<>\[\],\s]*?)\s+
        (?P<name>[A-Za-z_$][\w$]*)\s*
        (?:=\s*(?P<value>[^;]*))?\s*;
    """, M)

    @property
    def syntax(self) -> Syntax:
        return Syntax(
            name="java",
            line_comments=(LineComment("//"),),
            block_comments=(BlockComment("/*", "*/"),),
            strings=(
                StringRule('"""', '"""', escape="\\", multiline=True),
                StringRule('"', '"', escape="\\"),
                StringRule("'", "'", escape="\\", kind="char"),
            ),
        )

    def is_entry_point(self, fn: FunctionDef) -> bool:
        if "static" in fn.modifiers and fn.name == "main":
            return True
        return super().is_entry_point(fn)


# --------------------------------------------------------------------------
# C#
# --------------------------------------------------------------------------

class CSharpAdapter(_ControlFilterMixin, BraceAdapter):
    name = "csharp"
    label = "C#"
    extensions = (".cs",)
    family = "enterprise-oo"

    type_re = re.compile(r"""
        (?:^|(?<=[{};]))[ \t]*
        (?P<mods>(?:(?:public|protected|private|internal|static|sealed|
                     abstract|partial|readonly|unsafe)\s+)*)
        (?P<kind>class|interface|struct|enum|record)\s+
        (?P<name>[A-Za-z_][\w]*)
        (?:\s*<[^\n{]*?>)?
        (?:\s*:\s*(?P<extends>[\w.<>,\s]+?))?
        \s*\{
    """, M)

    function_re = re.compile(r"""
        (?:^|(?<=[{};]))[ \t]*
        (?P<mods>(?:(?:public|protected|private|internal|static|virtual|
                     override|sealed|abstract|async|extern|unsafe|new|
                     partial)\s+)*)
        (?:(?P<ret>[A-Za-z_][\w.<>\[\],?\s]*?)\s+)?
        (?P<name>[A-Za-z_][\w]*)\s*
        (?:<[^\n(]*?>)?\s*
        \(\s*(?P<params>[^;{}()]*(?:\([^()]*\)[^;{}()]*)*)\s*\)
        (?:\s*where\s+[^\n{]+?)?
        \s*\{
    """, M)

    import_re = re.compile(r"(?:^|(?<=[{};]))[ \t]*using\s+(?:static\s+)?(?P<module>[\w.]+)\s*;", re.M)
    package_re = re.compile(r"(?:^|(?<=[{};]))[ \t]*namespace\s+(?P<package>[\w.]+)", re.M)
    field_re = re.compile(r"""
        (?:^|(?<=[{};]))[ \t]*
        (?P<mods>(?:(?:public|protected|private|internal|static|readonly|
                     const|volatile)\s+)+)
        (?P<type>[A-Za-z_][\w.<>\[\],?\s]*?)\s+
        (?P<name>[A-Za-z_][\w]*)\s*
        (?:=\s*(?P<value>[^;]*))?\s*;
    """, M)

    @property
    def syntax(self) -> Syntax:
        return Syntax(
            name="csharp",
            line_comments=(LineComment("//"),),
            block_comments=(BlockComment("/*", "*/"),),
            strings=(
                StringRule('@"', '"', escape=None, doubling=True, multiline=True),
                StringRule('$"', '"', escape="\\"),
                StringRule('"', '"', escape="\\"),
                StringRule("'", "'", escape="\\", kind="char"),
            ),
        )


# --------------------------------------------------------------------------
# C and C++
# --------------------------------------------------------------------------

class CAdapter(_ControlFilterMixin, BraceAdapter):
    name = "c"
    label = "C"
    extensions = (".c", ".h")
    family = "systems"

    type_re = re.compile(r"""
        (?:^|(?<=[{};]))[ \t]*
        (?:typedef\s+)?
        (?P<kind>struct|union|enum)\s+
        (?P<name>[A-Za-z_]\w*)\s*
        \{
    """, M)

    function_re = re.compile(r"""
        (?:^|(?<=[{};]))[ \t]*
        (?P<mods>(?:(?:static|inline|extern|const|unsigned|signed|register|
                     volatile|_Noreturn)\s+)*)
        (?P<ret>[A-Za-z_][\w\s*]*?)[\s*]+
        (?P<name>[A-Za-z_]\w*)\s*
        \(\s*(?P<params>[^;{}]*?)\s*\)\s*
        \{
    """, M)

    import_re = re.compile(r'(?:^|(?<=[{};]))[ \t]*\#\s*include\s*[<"](?P<module>[^>"]+)[>"]', re.M)
    field_re = re.compile(r"""
        (?:^|(?<=[{};]))[ \t]*
        (?P<mods>(?:(?:static|extern|const|volatile)\s+)+)
        (?P<type>[A-Za-z_][\w\s*]*?)[\s*]+
        (?P<name>[A-Za-z_]\w*)\s*
        (?:\[[^\]]*\])?\s*
        (?:=\s*(?P<value>[^;]*))?\s*;
    """, M)

    @property
    def syntax(self) -> Syntax:
        return c_like(self.name)


class CppAdapter(CAdapter):
    name = "cpp"
    label = "C++"
    extensions = (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx", ".ipp")
    family = "systems"

    type_re = re.compile(r"""
        (?:^|(?<=[{};]))[ \t]*
        (?:template\s*<[^\n]*>\s*)?
        (?P<kind>class|struct|union|enum(?:\s+class)?)\s+
        (?:[A-Z_]+\s+)?
        (?P<name>[A-Za-z_]\w*)
        (?:\s*:\s*(?P<extends>[\w:<>,\s]+?))?
        \s*\{
    """, M)

    function_re = re.compile(r"""
        (?:^|(?<=[{};]))[ \t]*
        (?P<mods>(?:(?:static|inline|extern|const|virtual|explicit|constexpr|
                     friend|unsigned|signed)\s+)*)
        (?:(?P<ret>[A-Za-z_~][\w:<>,\s*&]*?)[\s*&]+)?
        (?P<name>~?[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)\s*
        \(\s*(?P<params>[^;{}]*?)\s*\)\s*
        (?:const\s*)?(?:noexcept\s*)?(?:override\s*)?(?:->[^{]+)?
        \{
    """, M)


# --------------------------------------------------------------------------
# JavaScript / TypeScript
# --------------------------------------------------------------------------

class JavaScriptAdapter(_ControlFilterMixin, BraceAdapter):
    name = "javascript"
    label = "JavaScript"
    extensions = (".js", ".jsx", ".mjs", ".cjs")
    family = "web"
    boolean_operators = ("&&", "||", "??")

    type_re = re.compile(r"""
        (?:^|(?<=[{};]))[ \t]*
        (?P<mods>(?:export\s+|default\s+)*)
        (?P<kind>class)\s+(?P<name>[A-Za-z_$][\w$]*)
        (?:\s+extends\s+(?P<extends>[\w.$]+))?
        \s*\{
    """, M)

    function_re = re.compile(r"""
        (?:^|(?<=[{};]))[ \t]*
        (?P<mods>(?:export\s+|default\s+|async\s+|static\s+|get\s+|set\s+)*)
        (?:
            function\s*\*?\s*(?P<name>[A-Za-z_$][\w$]*)\s*
              \(\s*(?P<params>[^;{}()]*(?:\([^()]*\)[^;{}()]*)*)\s*\)
          |
            (?:const|let|var)\s+(?P<name2>[A-Za-z_$][\w$]*)\s*=\s*
              (?:async\s+)?(?:function\s*\*?\s*)?
              \(\s*(?P<params2>[^;{}()]*)\s*\)\s*(?:=>)?
          |
            (?P<name3>[A-Za-z_$][\w$]*)\s*
              \(\s*(?P<params3>[^;{}()]*(?:\([^()]*\)[^;{}()]*)*)\s*\)
        )
        \s*\{
    """, M)

    import_re = re.compile(
        r"""(?:^|(?<=[{};]))[ \t]*(?:
              import\s+(?:(?P<symbol>[\w{},*\s]+?)\s+from\s+)?['"](?P<module>[^'"]+)['"]
            | (?:const|let|var)\s+[\w{},\s]+=\s*require\(\s*['"](?P<module2>[^'"]+)['"]\s*\)
            )""", re.M | re.X)

    @property
    def syntax(self) -> Syntax:
        return Syntax(
            name=self.name,
            line_comments=(LineComment("//"),),
            block_comments=(BlockComment("/*", "*/"),),
            strings=(
                StringRule("`", "`", escape="\\", multiline=True),
                StringRule('"', '"', escape="\\"),
                StringRule("'", "'", escape="\\"),
            ),
            regex_literals=True,
            identifier_extra="_$",
        )


class TypeScriptAdapter(JavaScriptAdapter):
    name = "typescript"
    label = "TypeScript"
    extensions = (".ts", ".tsx")

    type_re = re.compile(r"""
        (?:^|(?<=[{};]))[ \t]*
        (?P<mods>(?:export\s+|default\s+|declare\s+|abstract\s+)*)
        (?P<kind>class|interface|enum)\s+(?P<name>[A-Za-z_$][\w$]*)
        (?:\s*<[^\n{]*?>)?
        (?:\s+extends\s+(?P<extends>[\w.$<>,\s]+?))?
        (?:\s+implements\s+(?P<implements>[\w.$<>,\s]+?))?
        \s*\{
    """, M)


# --------------------------------------------------------------------------
# PHP
# --------------------------------------------------------------------------

class PhpAdapter(_ControlFilterMixin, BraceAdapter):
    name = "php"
    label = "PHP"
    extensions = (".php", ".php3", ".php4", ".php5", ".phtml", ".inc")
    family = "web"
    boolean_operators = ("&&", "||", " and ", " or ", " xor ")

    type_re = re.compile(r"""
        (?:^|(?<=[{};]))[ \t]*
        (?P<mods>(?:(?:abstract|final)\s+)*)
        (?P<kind>class|interface|trait)\s+(?P<name>[A-Za-z_]\w*)
        (?:\s+extends\s+(?P<extends>[\w\\,\s]+?))?
        (?:\s+implements\s+(?P<implements>[\w\\,\s]+?))?
        \s*\{
    """, M)

    function_re = re.compile(r"""
        (?:^|(?<=[{};]))[ \t]*
        (?P<mods>(?:(?:public|protected|private|static|final|abstract)\s+)*)
        function\s*&?\s*(?P<name>[A-Za-z_]\w*)\s*
        \(\s*(?P<params>[^;{}()]*(?:\([^()]*\)[^;{}()]*)*)\s*\)
        (?:\s*:\s*[\w\\|?]+)?
        \s*\{
    """, M)

    import_re = re.compile(
        r"(?:^|(?<=[{};]))[ \t]*(?:use|require(?:_once)?|include(?:_once)?)\s+"
        r"[('\"]?(?P<module>[\w\\/.]+)", re.M)
    package_re = re.compile(r"(?:^|(?<=[{};]))[ \t]*namespace\s+(?P<package>[\w\\]+)", re.M)

    @property
    def syntax(self) -> Syntax:
        return Syntax(
            name="php",
            line_comments=(LineComment("//"), LineComment("#")),
            block_comments=(BlockComment("/*", "*/"),),
            strings=(
                StringRule('"', '"', escape="\\"),
                StringRule("'", "'", escape="\\"),
            ),
        )


# --------------------------------------------------------------------------
# Go
# --------------------------------------------------------------------------

class GoAdapter(_ControlFilterMixin, BraceAdapter):
    name = "go"
    label = "Go"
    extensions = (".go",)
    family = "systems"
    boolean_operators = ("&&", "||")

    type_re = re.compile(r"""
        (?:^|(?<=[{};]))[ \t]*type\s+(?P<name>[A-Za-z_]\w*)\s+
        (?P<kind>struct|interface)\s*\{
    """, M)

    function_re = re.compile(r"""
        (?:^|(?<=[{};]))[ \t]*func\s*
        (?:\(\s*[\w*\s]+\s*\)\s*)?
        (?P<name>[A-Za-z_]\w*)\s*
        \(\s*(?P<params>[^;{}()]*(?:\([^()]*\)[^;{}()]*)*)\s*\)
        [^{\n]*\{
    """, M)

    import_re = re.compile(r'(?:^|(?<=[{};]))[ \t]*(?:import\s+)?[\w.]*\s*"(?P<module>[\w./-]+)"', re.M)
    package_re = re.compile(r"(?:^|(?<=[{};]))[ \t]*package\s+(?P<package>\w+)", re.M)

    @property
    def syntax(self) -> Syntax:
        return Syntax(
            name="go",
            line_comments=(LineComment("//"),),
            block_comments=(BlockComment("/*", "*/"),),
            strings=(
                StringRule("`", "`", escape=None, multiline=True),
                StringRule('"', '"', escape="\\"),
                StringRule("'", "'", escape="\\", kind="char"),
            ),
        )
