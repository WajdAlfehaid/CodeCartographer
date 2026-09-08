"""Command line interface.

The web dashboard is for exploring. The CLI is for the two things a dashboard
cannot do: run in CI and produce an artefact. ``--fail-on`` gives a build a
reason to go red, so the analysis can stop a codebase getting worse rather
than only describing how bad it already is.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from .analysis.engine import analyse
from .languages import language_table
from .models import AnalysisConfig, DEFAULT_EXCLUDES
from .report import html_report, markdown

EXIT_OK = 0
EXIT_THRESHOLD = 2
EXIT_ERROR = 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codecartographer",
        description="Map, measure and de-risk a legacy codebase.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  codecartographer analyze ./src --html report.html
  codecartographer analyze . --markdown REPORT.md --json analysis.json
  codecartographer analyze ./legacy --language cobol --language java
  codecartographer analyze . --fail-on-risk 80 --fail-on-critical 5
  codecartographer serve --root ~/projects --port 5000
  codecartographer languages
""")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("analyze", aliases=["analyse"],
                         help="Analyse a directory and write reports.")
    run.add_argument("path", nargs="?", default=".",
                     help="Directory to analyse (default: current).")
    run.add_argument("--json", metavar="FILE", help="Write the full result as JSON.")
    run.add_argument("--markdown", "--md", metavar="FILE", dest="markdown",
                     help="Write a Markdown report.")
    run.add_argument("--html", metavar="FILE",
                     help="Write a self-contained interactive HTML report.")
    run.add_argument("--include-source", action="store_true",
                     help="Embed file contents in the JSON/HTML output.")
    run.add_argument("--language", action="append", dest="languages",
                     metavar="NAME",
                     help="Restrict to a language (repeatable). "
                          "See `codecartographer languages`.")
    run.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                     help="Extra path glob to skip (repeatable).")
    run.add_argument("--include", action="append", default=[], metavar="GLOB",
                     help="Only analyse paths matching this glob (repeatable).")
    run.add_argument("--no-git", action="store_true",
                     help="Skip git history even if the tree is a repository.")
    run.add_argument("--max-files", type=int, default=20000)
    run.add_argument("--max-file-bytes", type=int, default=4_000_000)
    run.add_argument("--quiet", "-q", action="store_true",
                     help="Only print errors.")
    run.add_argument("--fail-on-risk", type=float, metavar="SCORE",
                     help="Exit non-zero if any file scores above this risk.")
    run.add_argument("--fail-on-critical", type=int, metavar="N",
                     help="Exit non-zero if there are more than N critical "
                          "findings.")
    run.add_argument("--fail-on-cycles", type=int, metavar="N",
                     help="Exit non-zero if there are more than N dependency "
                          "cycles.")

    serve = sub.add_parser("serve", help="Run the interactive dashboard.")
    serve.add_argument("--root", default=os.getcwd(),
                       help="Workspace root; nothing outside it can be read.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=5000)
    serve.add_argument("--debug", action="store_true")

    sub.add_parser("languages", help="List the languages this build understands.")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "languages":
        return _languages()
    if args.command == "serve":
        return _serve(args)
    return _analyze(args)


def _languages() -> int:
    rows = language_table()
    label_w = max(len(r["label"]) for r in rows)
    name_w = max(len(r["name"]) for r in rows)
    print(f"{len(rows)} languages supported\n")
    print(f"  {'NAME':<{name_w}}  {'LANGUAGE':<{label_w}}  {'ERA':<14}  EXTENSIONS")
    for row in sorted(rows, key=lambda r: (r["family"], r["label"])):
        print(f"  {row['name']:<{name_w}}  {row['label']:<{label_w}}  "
              f"{row['family']:<14}  {' '.join(row['extensions'])}")
    print("\nUse the NAME column with --language, e.g. `--language cobol`.")
    return EXIT_OK


def _analyze(args) -> int:
    config = AnalysisConfig(
        root=args.path,
        include_globs=list(args.include),
        exclude_globs=list(DEFAULT_EXCLUDES) + list(args.exclude),
        languages=args.languages,
        max_files=args.max_files,
        max_file_bytes=args.max_file_bytes,
        use_git_history=not args.no_git,
    )

    def progress(message: str, fraction: float) -> None:
        if args.quiet:
            return
        bar_width = 24
        filled = int(bar_width * fraction)
        sys.stderr.write(
            f"\r  [{'█' * filled}{'·' * (bar_width - filled)}] {message[:48]:<48}")
        sys.stderr.flush()

    try:
        analysis = analyse(config, progress)
    except NotADirectoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if not args.quiet:
        sys.stderr.write("\r" + " " * 80 + "\r")

    if not analysis.files:
        print("No source files this build understands were found in "
              f"{config.root}.", file=sys.stderr)
        print("Run `codecartographer languages` to see what is supported.",
              file=sys.stderr)
        return EXIT_ERROR

    written: List[str] = []
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(analysis.to_dict(include_source=args.include_source),
                      handle, indent=2, default=str)
        written.append(args.json)
    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as handle:
            handle.write(markdown.render(analysis))
        written.append(args.markdown)
    if args.html:
        html_report.write(analysis, args.html, args.include_source)
        written.append(args.html)

    if not args.quiet:
        _print_summary(analysis, written)

    return _gate(args, analysis)


def _print_summary(analysis, written: List[str]) -> None:
    o = analysis.overview()
    cs = analysis.complexity_summary
    dead = analysis.dead_code.get("totals", {})
    res = analysis.resolution

    print(f"\n  {o['files']} files · {o['total_lines']:,} lines · "
          f"{o['functions']} functions · {o['language_count']} language(s)")
    langs = sorted(o["languages"].items(), key=lambda kv: -kv[1]["lines"])
    print("  " + ", ".join(f"{v['label']} {v['files']}" for _, v in langs[:6]))
    print()
    print(f"  Maintainability   {cs.get('average_maintainability', 0)}/100 "
          f"average, {cs.get('files_below_threshold', 0)} file(s) below 65")
    print(f"  Dependencies      {o['dependencies']} edges, {o['cycles']} cycle(s)")
    print(f"  Call resolution   {int(res.get('resolution_rate', 0) * 100)}% of "
          f"{res.get('internal_call_sites', 0)} internal call sites")
    print(f"  Dead code         {dead.get('unreachable', 0)} unreachable "
          f"function(s), ~{dead.get('removable_lines', 0)} lines")
    print(f"  Design problems   {len(analysis.smells)} "
          f"({sum(1 for s in analysis.smells if s['severity'] == 'critical')} critical)")
    print(f"  Business rules    {analysis.business.get('total', 0)} recovered")

    top = sorted(analysis.risk.values(), key=lambda r: -r["score"])[:5]
    if top:
        print("\n  Riskiest files")
        for entry in top:
            print(f"    {entry['score']:5.1f}  {entry['level']:<9} {entry['file']}")

    if written:
        print("\n  Wrote " + ", ".join(written))
    print()


def _gate(args, analysis) -> int:
    failures: List[str] = []

    if args.fail_on_risk is not None:
        over = [r for r in analysis.risk.values() if r["score"] > args.fail_on_risk]
        if over:
            worst = max(over, key=lambda r: r["score"])
            failures.append(
                f"{len(over)} file(s) exceed risk {args.fail_on_risk} "
                f"(worst: {worst['file']} at {worst['score']})")

    if args.fail_on_critical is not None:
        criticals = [s for s in analysis.smells if s["severity"] == "critical"]
        if len(criticals) > args.fail_on_critical:
            failures.append(
                f"{len(criticals)} critical finding(s), limit is "
                f"{args.fail_on_critical}")

    if args.fail_on_cycles is not None:
        cycles = analysis.dependency_view()["cycles"]
        if len(cycles) > args.fail_on_cycles:
            failures.append(
                f"{len(cycles)} dependency cycle(s), limit is "
                f"{args.fail_on_cycles}")

    if failures:
        print("Thresholds exceeded:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        return EXIT_THRESHOLD
    return EXIT_OK


def _serve(args) -> int:
    try:
        from .web.app import create_app
    except ImportError:
        print("error: the dashboard needs Flask. Install it with:\n"
              "  pip install flask\n"
              "The analysis engine itself has no dependencies — "
              "`codecartographer analyze --html report.html` works without it.",
              file=sys.stderr)
        return EXIT_ERROR

    app = create_app(args.root)
    print(f"  CodeCartographer — workspace {os.path.realpath(args.root)}")
    print(f"  http://{args.host}:{args.port}\n")
    app.run(host=args.host, port=args.port, debug=args.debug)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
