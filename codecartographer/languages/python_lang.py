"""Python adapter backed by the standard library's real parser.

Where a genuine AST exists we use it. That gives exact function bounds, exact
call receivers, decorators, and a real syntax error with a real column — none
of which a regex can honestly claim. If the file does not compile (common in
legacy trees pinned to Python 2) we degrade to a tokenizer pass rather than
dropping the file.
"""

from __future__ import annotations

import ast
import re
from typing import Dict, List, Optional

from ..models import (
    CallSite,
    Diagnostic,
    FieldDef,
    FunctionDef,
    ImportDef,
    Parameter,
    SEVERITY_ERROR,
    SourceFile,
    TypeDef,
)
from ..tokenizer import LineComment, ScanResult, StringRule, Syntax
from .base import COMMON_ENTRY_NAMES, FRAMEWORK_ANNOTATIONS, LanguageAdapter

PY_DECISION_KEYWORDS = (
    "if", "elif", "for", "while", "except", "with", "assert", "case",
)


class PythonAdapter(LanguageAdapter):
    name = "python"
    label = "Python"
    extensions = (".py", ".pyw", ".pyi")
    family = "scripting"
    decision_keywords = PY_DECISION_KEYWORDS
    boolean_operators = (" and ", " or ")

    @property
    def syntax(self) -> Syntax:
        return Syntax(
            name="python",
            line_comments=(LineComment("#"),),
            strings=(
                StringRule('"""', '"""', escape="\\", multiline=True),
                StringRule("'''", "'''", escape="\\", multiline=True),
                StringRule('"', '"', escape="\\"),
                StringRule("'", "'", escape="\\"),
            ),
        )

    def balance_pairs(self):
        return (("(", ")"), ("[", "]"), ("{", "}"))

    def extract(self, sf: SourceFile, sr: ScanResult) -> None:
        try:
            tree = ast.parse(sf.source, filename=sf.rel_path)
        except SyntaxError as exc:
            sf.parse_confidence = 0.45
            sf.diagnostics.append(Diagnostic(
                line=exc.lineno or 1,
                column=exc.offset or 0,
                code="syntax-error",
                message=exc.msg or "Could not parse this file as Python 3.",
                severity=SEVERITY_ERROR,
                snippet=(exc.text or "").strip()[:160],
            ))
            self._fallback(sf, sr)
            return

        _Walker(self, sf, sr).visit(tree)

    # -- AST unavailable ---------------------------------------------------

    def _fallback(self, sf: SourceFile, sr: ScanResult) -> None:
        """Indentation-based extraction for files the AST rejects."""
        lines = sr.mask.split("\n")
        stack: List[tuple] = []   # (indent, TypeDef|None)
        for idx, line in enumerate(lines, 1):
            m = re.match(r"^(?P<ind>[ \t]*)class\s+(?P<name>\w+)", line)
            if m:
                td = TypeDef(name=m.group("name"), kind="class",
                             file=sf.rel_path, line=idx, end_line=idx)
                sf.types.append(td)
                stack.append((len(m.group("ind")), td))
                continue
            m = re.match(r"^(?P<ind>[ \t]*)(?:async\s+)?def\s+(?P<name>\w+)"
                         r"\s*\((?P<params>[^)]*)\)", line)
            if m:
                indent = len(m.group("ind"))
                owner = None
                for ind, td in reversed(stack):
                    if ind < indent:
                        owner = td
                        break
                end = _block_end(lines, idx, indent)
                fn = FunctionDef(
                    name=m.group("name"), file=sf.rel_path, line=idx,
                    end_line=end, language=self.name,
                    owner=owner.name if owner else None,
                    params=[Parameter(p.strip().split(":")[0].split("=")[0])
                            for p in m.group("params").split(",") if p.strip()],
                )
                body = "\n".join(lines[idx:end])
                fn.metrics = _py_metrics(self, body, fn)
                fn.calls = self.extract_calls(
                    sr, sum(len(l) + 1 for l in lines[:idx]),
                    sum(len(l) + 1 for l in lines[:end]))
                fn.is_entry_point = self.is_entry_point(fn)
                sf.functions.append(fn)
                if owner:
                    owner.methods.append(fn.name)
        sf.parse_confidence = min(sf.parse_confidence, 0.5)


def _block_end(lines: List[str], start_idx: int, indent: int) -> int:
    for j in range(start_idx, len(lines)):
        line = lines[j]
        if not line.strip():
            continue
        cur = len(line) - len(line.lstrip())
        if cur <= indent:
            return j
    return len(lines)


def _py_metrics(adapter: LanguageAdapter, body: str, fn: FunctionDef) -> Dict:
    return {
        "cyclomatic": adapter.cyclomatic(body),
        "cognitive": adapter.cognitive(body),
        "max_nesting": _indent_depth(body),
        "loc": max(1, fn.end_line - fn.line + 1),
        "params": len(fn.params),
        "calls": len(fn.calls),
        "returns": len(re.findall(r"\breturn\b", body)),
    }


def _indent_depth(body: str) -> int:
    depths = []
    for line in body.split("\n"):
        if line.strip():
            stripped = line.expandtabs(4)
            depths.append((len(stripped) - len(stripped.lstrip())) // 4)
    if not depths:
        return 0
    base = min(depths)
    return max(depths) - base + 1


class _Walker(ast.NodeVisitor):
    """Turns a real Python AST into the shared model."""

    def __init__(self, adapter: PythonAdapter, sf: SourceFile, sr: ScanResult):
        self.adapter = adapter
        self.sf = sf
        self.sr = sr
        self.class_stack: List[TypeDef] = []
        self.func_depth = 0

    # -- imports ------------------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.sf.imports.append(ImportDef(
                raw=f"import {alias.name}", line=node.lineno,
                module=alias.name, symbol=alias.asname))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ("." * (node.level or 0))
        for alias in node.names:
            self.sf.imports.append(ImportDef(
                raw=f"from {module} import {alias.name}", line=node.lineno,
                module=module, symbol=alias.asname or alias.name,
                is_wildcard=alias.name == "*"))
        self.generic_visit(node)

    # -- classes ------------------------------------------------------------

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        td = TypeDef(
            name=node.name,
            kind="class",
            file=self.sf.rel_path,
            line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
            extends=[_expr_name(b) for b in node.bases if _expr_name(b)],
            annotations=[f"@{_expr_name(d)}" for d in node.decorator_list
                         if _expr_name(d)],
            doc=(ast.get_docstring(node) or "")[:600],
        )
        self.sf.types.append(td)
        self.class_stack.append(td)
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                td.fields.append(FieldDef(
                    name=stmt.target.id, line=stmt.lineno,
                    type=_expr_name(stmt.annotation), owner=node.name))
            elif isinstance(stmt, ast.Assign):
                for tgt in stmt.targets:
                    if isinstance(tgt, ast.Name):
                        td.fields.append(FieldDef(
                            name=tgt.id, line=stmt.lineno, owner=node.name,
                            modifiers={"static"}))
        self.generic_visit(node)
        self.class_stack.pop()

    # -- functions ----------------------------------------------------------

    def visit_FunctionDef(self, node) -> None:
        self._function(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def _function(self, node) -> None:
        if self.func_depth > 0:
            # Nested helpers are part of their parent's body, not API surface.
            self.generic_visit(node)
            return

        owner = self.class_stack[-1] if self.class_stack else None
        end = getattr(node, "end_lineno", node.lineno) or node.lineno
        decorators = [f"@{_expr_name(d)}" for d in node.decorator_list
                      if _expr_name(d)]
        modifiers = set()
        for d in decorators:
            low = d.lower().lstrip("@")
            if low in ("staticmethod", "classmethod", "property",
                       "abstractmethod", "cached_property"):
                modifiers.add(low)
        if node.name.startswith("__") and not node.name.endswith("__"):
            modifiers.add("private")
        elif node.name.startswith("_"):
            modifiers.add("protected")
        else:
            modifiers.add("public")

        fn = FunctionDef(
            name=node.name,
            file=self.sf.rel_path,
            line=node.lineno,
            end_line=end,
            language=self.adapter.name,
            owner=owner.name if owner else None,
            params=_params(node.args),
            return_type=_expr_name(node.returns),
            modifiers=modifiers,
            annotations=decorators,
            doc=(ast.get_docstring(node) or "")[:600],
            is_constructor=node.name == "__init__",
            is_abstract=any("abstract" in d.lower() for d in decorators),
        )

        collector = _CallCollector()
        self.func_depth += 1
        for stmt in node.body:
            collector.visit(stmt)
        fn.calls = collector.calls
        fn.locals = collector.locals
        fn.reads = sorted(collector.names)[:200]

        body_src = "\n".join(
            self.sr.masked_line(i) for i in range(node.lineno, end + 1))
        fn.metrics = _py_metrics(self.adapter, body_src, fn)
        fn.metrics["cyclomatic"] = _ast_cyclomatic(node)
        fn.metrics["cognitive"] = _ast_cognitive(node)
        fn.metrics["max_nesting"] = _ast_nesting(node)
        fn.is_entry_point = (
            node.name.lower() in COMMON_ENTRY_NAMES
            or any(d.lower().lstrip("@").split(".")[-1] in FRAMEWORK_ANNOTATIONS
                   for d in decorators)
            or node.name.startswith("test_")
        )
        self.sf.functions.append(fn)
        if owner:
            owner.methods.append(fn.name)

        self.generic_visit(node)
        self.func_depth -= 1

    # -- module level -------------------------------------------------------

    def visit_Assign(self, node: ast.Assign) -> None:
        if not self.class_stack and self.func_depth == 0:
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    self.sf.globals.append(FieldDef(
                        name=tgt.id, line=node.lineno, modifiers={"global"},
                        initial_value=_short(node.value)))
        self.generic_visit(node)


def _params(args: ast.arguments) -> List[Parameter]:
    out: List[Parameter] = []
    everything = list(getattr(args, "posonlyargs", [])) + list(args.args) + \
        list(args.kwonlyargs)
    for a in everything:
        if a.arg in ("self", "cls"):
            continue
        out.append(Parameter(name=a.arg, type=_expr_name(a.annotation)))
    if args.vararg:
        out.append(Parameter(name="*" + args.vararg.arg))
    if args.kwarg:
        out.append(Parameter(name="**" + args.kwarg.arg))
    return out


def _expr_name(node) -> Optional[str]:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _expr_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return _expr_name(node.func)
    if isinstance(node, ast.Constant):
        return str(node.value)
    if isinstance(node, ast.Subscript):
        return _expr_name(node.value)
    return None


def _short(node) -> Optional[str]:
    try:
        text = ast.unparse(node)
    except Exception:
        return None
    return text[:80]


class _CallCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.calls: List[CallSite] = []
        self.locals: Dict[str, str] = {}
        self.names: set = set()

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute):
            self.calls.append(CallSite(
                name=func.attr, line=node.lineno,
                receiver=_expr_name(func.value),
                arg_count=len(node.args) + len(node.keywords)))
        elif isinstance(func, ast.Name):
            self.calls.append(CallSite(
                name=func.id, line=node.lineno,
                arg_count=len(node.args) + len(node.keywords),
                is_constructor=func.id[:1].isupper()))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        self.names.add(node.id)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name):
            t = _expr_name(node.annotation)
            if t:
                self.locals[node.target.id] = t
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if isinstance(node.value, ast.Call):
            t = _expr_name(node.value.func)
            if t and t[:1].isupper():
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        self.locals[tgt.id] = t
        self.generic_visit(node)


_BRANCH_NODES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler,
                 ast.With, ast.AsyncWith, ast.Assert, ast.IfExp)


def _ast_cyclomatic(node) -> int:
    score = 1
    for child in ast.walk(node):
        if isinstance(child, _BRANCH_NODES):
            score += 1
        elif isinstance(child, ast.BoolOp):
            score += len(child.values) - 1
        elif isinstance(child, ast.comprehension):
            score += 1 + len(child.ifs)
        elif hasattr(ast, "match_case") and isinstance(child, ast.match_case):
            score += 1
    return score


def _ast_cognitive(node, depth: int = 0) -> int:
    """Cognitive complexity: each nesting level makes a branch cost more."""
    score = 0
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.If, ast.For, ast.AsyncFor, ast.While,
                              ast.ExceptHandler)):
            score += 1 + depth
            score += _ast_cognitive(child, depth + 1)
        elif isinstance(child, (ast.With, ast.AsyncWith, ast.Try)):
            score += _ast_cognitive(child, depth + 1)
        elif isinstance(child, ast.BoolOp):
            score += len(child.values) - 1
            score += _ast_cognitive(child, depth)
        else:
            score += _ast_cognitive(child, depth)
    return score


def _ast_nesting(node, depth: int = 0) -> int:
    peak = depth
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.If, ast.For, ast.AsyncFor, ast.While,
                              ast.Try, ast.With, ast.AsyncWith,
                              ast.ExceptHandler)):
            peak = max(peak, _ast_nesting(child, depth + 1))
        else:
            peak = max(peak, _ast_nesting(child, depth))
    return peak
