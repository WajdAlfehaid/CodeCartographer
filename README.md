# CodeCartographer

[![CI](https://github.com/WajdAlfehaid/CodeCartographer/actions/workflows/ci.yml/badge.svg)](https://github.com/WajdAlfehaid/CodeCartographer/actions/workflows/ci.yml)
[![Live demo](https://img.shields.io/badge/live%20demo-dashboard-2f6feb)](https://wajdalfehaid.github.io/CodeCartographer/)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org/)
[![Dependencies](https://img.shields.io/badge/runtime%20dependencies-none-2c7a4b)](#install)
[![License](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)

Make an unfamiliar codebase legible before you change it.

Point it at a directory. It parses the source, resolves each call to a real
definition, maps how the files depend on each other, finds the code nothing
reaches, recovers the business rules buried in the logic, and tells you where
a change is most likely to hurt — across 17 languages, from COBOL to
TypeScript.

```bash
python -m codecartographer analyze ./src --html report.html
python -m codecartographer serve --root ~/projects
```

## See it running

**[Open the live dashboard →](https://wajdalfehaid.github.io/CodeCartographer/)**

That page is this repository analysing itself, rebuilt by CI on every push:
47 files across 8 languages — Python, JavaScript, Java, COBOL, Fortran,
Visual Basic, Pascal and PL/SQL — with a real dependency map, a genuine
circular dependency, and risk scores drawn from actual git history.

Start on the **Map** tab, then open **Confidence** to see how much of the
analysis was proven rather than inferred.

There is also a [smaller Java-only report](https://wajdalfehaid.github.io/CodeCartographer/legacy-sample.html)
if you want something easier to read end to end.

## The problem

Critical systems are running on code whose authors have retired. The
documentation is wrong or missing. Every change is a gamble, so nobody makes
one, so the system ossifies. What a team needs first is not a rewrite plan —
it is an honest map: what is here, what talks to what, what is already dead,
and which parts will bite.

## What makes the answers trustworthy

Most quick static analysis is regex over raw text, and it is wrong in
predictable ways: a brace inside a string literal becomes a syntax error, a
class mentioned in a comment becomes a dependency, and two classes that both
have a `close()` method become one. Those errors do not average out — they
manufacture confident nonsense.

This tool is built to avoid each of them.

**Every file is lexed before it is read.** A per-language scanner blanks out
comments and string bodies while preserving byte offsets, so downstream
passes physically cannot see a keyword inside a comment. It handles the parts
that trip up naive tools: COBOL's fixed-format column areas, VB and SQL
escaping quotes by doubling them, Pascal's two comment syntaxes, fixed-form
Fortran's column-1 comment marker, Perl's POD blocks.

**Calls are resolved, not matched.** A call site is bound to a definition
using the receiver's declared type where one is known — from a local variable,
a parameter, a field, an inherited field, an import, or a static type name.
`local.submit()` binds to `Order.submit`, not to the `submit` on some other
class. Python goes further and uses the real `ast` module.

**Every edge states its own confidence.** `exact` means the receiver type was
known and resolved. `likely` means exactly one definition in the project has
that name. `ambiguous` means several did and all were kept. Calls into
libraries are counted separately from resolution failures, so the reported
resolution rate means something. The dashboard shows all of it under a
**Confidence** tab.

**Dead code is found by reachability.** The analysis walks the resolved call
graph out from every entry point. That catches a cluster of five functions
that only call each other — invisible to reference counting, but still dead.
Each finding carries a confidence and the reasons it could be wrong: public
API, framework annotation, accessor, or a project that uses reflection at all
(in which case no static tool can prove anything and the tool says so).

**The classifier reports its measured accuracy.** Function purpose is
predicted by a small Naive Bayes model combined with structural evidence. It
cross-validates itself at startup — grouped by phrase, so the spelling
variants of one training example cannot leak between folds — and publishes
the real number (currently ~53% across 10 classes, against a 10% baseline).
It returns `unclassified` rather than guessing when nothing fits.

## What you get

| | |
|---|---|
| **Dependency map** | Interactive graph of resolved references, with cycle detection (Tarjan), PageRank importance, and Martin's instability metric. |
| **Change risk** | A weighted score per file — complexity, blast radius, churn, known problems, test coverage signal, knowledge concentration, documentation. Every factor shows its measurement, weight and contribution so you can argue with it. |
| **Hotspots** | Complexity against git churn. The top-right quadrant is where refactoring pays back fastest. |
| **Dead code** | Unreachable functions, unreferenced functions, unused imports, unused fields, unused parameters, and files nothing references. |
| **Design problems** | God class, long method, deep nesting, feature envy, data class, duplicated blocks (normalised sliding-window hashing), circular dependencies, change amplifiers, swallowed exceptions, `On Error Resume Next`, TODO debt. |
| **Business rules** | Thresholds, formulas, fixed constants and state transitions that carry commercial meaning — with the actual numbers, not "this condition checks a value". Plus a domain glossary nobody ever wrote. |
| **Complexity** | Cyclomatic, cognitive (nesting-weighted), max nesting, Halstead volume, and the Maintainability Index. |

## Languages

17 languages, each with its own lexer and structure extractor.

| Era | Languages |
|---|---|
| Mainframe | COBOL (paragraphs, `PERFORM` as the call, fixed-format columns) |
| Scientific | Fortran (fixed and free form) |
| RAD | Visual Basic (VB6, VBA, VB.NET), Pascal / Delphi |
| Database | PL/SQL, T-SQL |
| Enterprise OO | Java, C# |
| Systems | C, C++, Go |
| Web | JavaScript, TypeScript, PHP |
| Scripting | Python, Perl, Ruby, Shell |

`python -m codecartographer languages` lists them with extensions.

Adding one means writing a `LanguageAdapter`: a lexical `Syntax` and a handful
of declaration patterns. No analysis pass needs to change — they all speak the
shared model.

## Install

The analysis engine is **pure standard library**. No numpy, no scikit-learn,
no networkx. That is deliberate: the machines these codebases live on often
cannot reach a package index.

```bash
git clone <this repo> && cd CodeCartographer
python -m codecartographer analyze ./sample_legacy_codebase --html report.html
```

Flask is needed only for the interactive dashboard:

```bash
pip install flask
python -m codecartographer serve --root .
```

Requires Python 3.8+.

## Command line

```
codecartographer analyze [PATH] [options]

  --html FILE            Self-contained interactive report (no server, no network)
  --markdown FILE        Markdown report for a ticket or a wiki
  --json FILE            Full structured result
  --language NAME        Restrict to a language (repeatable)
  --include GLOB         Only these paths (repeatable)
  --exclude GLOB         Skip these paths (repeatable)
  --no-git               Skip change history
  --include-source       Embed file contents in JSON/HTML output

  --fail-on-risk SCORE   Exit 2 if any file scores above this
  --fail-on-critical N   Exit 2 if more than N critical findings
  --fail-on-cycles N     Exit 2 if more than N dependency cycles

codecartographer serve --root DIR [--host H] [--port P]
codecartographer languages
```

The `--fail-on-*` flags give a build a reason to go red, so the analysis can
stop a codebase getting worse instead of only describing how bad it is:

```yaml
- run: python -m codecartographer analyze ./src --fail-on-cycles 0 --markdown report.md
```

The HTML report is one file with the CSS, the JavaScript and the data inlined.
It opens from a `file://` URL on an air-gapped machine and can be emailed to
an architect who will never run Python.

## Library use

```python
from codecartographer import analyse, AnalysisConfig

analysis = analyse(AnalysisConfig(root="./src"))

print(analysis.overview()["total_lines"])
print(analysis.resolution["resolution_rate"])

for entry in sorted(analysis.risk.values(), key=lambda r: -r["score"])[:5]:
    print(entry["score"], entry["file"], entry["narrative"])
```

## Tests

```bash
python tests/run_tests.py        # no dependencies
pytest tests/                    # if you have pytest
```

The suite runs either way. The tests are ordinary pytest tests; `run_tests.py`
supplies just enough of pytest's surface to execute them unchanged when the
package is not installed, because a test suite you cannot run on the target
machine is not a test suite.

## What this tool does not know

Stated plainly, because a tool that hides its limits is worse than no tool.

- **Reflection defeats it.** `Class.forName`, `getattr`, `eval`, dynamic SQL
  and dependency injection all invoke code from strings. The tool detects
  these patterns and downgrades every dead-code finding in that project, but
  it cannot resolve them.
- **Ambiguous names stay ambiguous.** When the receiver type is unknown and
  several definitions share a name, all candidates are linked and the edge is
  marked `ambiguous`. Over-linking is the safer error: it risks a missed
  cleanup, not a deleted live method.
- **Non-Python parsing is structural, not a full grammar.** It reads a lexed
  token stream rather than raw text, which removes the whole class of
  literal-and-comment errors, but it is not a compiler. Deeply nested generics,
  heavy macro use and unusual formatting can still be missed.
- **Cross-language calls are invisible.** A JSP calling a Java bean, or COBOL
  calling a stored procedure, is not linked.
- **Nothing is executed.** Every finding comes from reading the source.

## Layout

```
codecartographer/
  tokenizer.py       Config-driven lexer; produces the comment/string mask
  models.py          Shared data model every pass speaks
  graphs.py          Tarjan SCC, reachability, PageRank — dependency-free
  languages/         One adapter per language
  analysis/          symbols, deadcode, complexity, smells, risk, business,
                     classifier, hotspots, engine
  report/            Markdown and self-contained HTML
  web/               Flask dashboard
  cli.py
tests/
```
