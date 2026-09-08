"""Markdown report: the version a developer pastes into a ticket."""

from __future__ import annotations

from typing import Dict, List, Sequence

from ..analysis.engine import Analysis
from ..analysis.classifier import CATEGORY_LABELS, distribution


def render(analysis: Analysis, limit: int = 15) -> str:
    o = analysis.overview()
    out: List[str] = []
    add = out.append

    add(f"# Codebase report — {_short(o['root'])}")
    add("")
    add(f"*Generated {o['generated_at']} · {o['files']} files · "
        f"{o['total_lines']:,} lines · {o['functions']} functions · "
        f"analysed in {o['duration_seconds']}s*")
    add("")

    # -- headline -----------------------------------------------------------
    add("## What you are looking at")
    add("")
    langs = sorted(o["languages"].items(), key=lambda kv: -kv[1]["lines"])
    add("| Language | Files | Lines | Functions |")
    add("|---|--:|--:|--:|")
    for name, data in langs:
        add(f"| {data['label']} | {data['files']} | {data['lines']:,} | "
            f"{data['functions']} |")
    add("")

    cs = analysis.complexity_summary
    if cs:
        add(f"Average maintainability index is **{cs['average_maintainability']}/100** "
            f"across {cs['total_sloc']:,} lines of code. "
            f"{cs['files_below_threshold']} file(s) sit below the "
            f"conventional maintainability threshold of 65.")
        add("")

    res = analysis.resolution
    if res:
        add(f"Call resolution: **{int(res['resolution_rate'] * 100)}%** of the "
            f"{res['internal_call_sites']} internal call sites were matched to "
            f"a definition ({res['exact']} exactly, {res['likely']} by unique "
            f"name, {res['ambiguous']} ambiguous). A further "
            f"{res['external_calls']} calls target libraries outside this "
            f"project. Findings below inherit that accuracy.")
        add("")

    # -- risk ---------------------------------------------------------------
    add("## Where change is riskiest")
    add("")
    top = sorted(analysis.risk.values(), key=lambda r: -r["score"])[:limit]
    if top:
        add("| File | Risk | Level | Main driver |")
        add("|---|--:|---|---|")
        for entry in top:
            driver = max(entry["factors"].items(),
                         key=lambda kv: kv[1]["contribution"])
            add(f"| `{entry['file']}` | {entry['score']} | {entry['level']} | "
                f"{driver[1]['label']} |")
        add("")
        add(f"> {top[0]['narrative']}")
        add("")

    quadrant = analysis.risk_summary.get("refactor_first") or []
    if quadrant:
        add("### Refactor these first")
        add("")
        add("Files that are both complicated and changed often — the "
            "intersection where refactoring pays back fastest.")
        add("")
        for path in quadrant:
            add(f"- `{path}`")
        add("")

    # -- dead code ----------------------------------------------------------
    dead = analysis.dead_code
    if dead:
        totals = dead["totals"]
        add("## Code that appears to be dead")
        add("")
        add(f"*{dead['mode_note']}*")
        add("")
        add(f"- {totals['unreachable']} unreachable function(s), "
            f"~{totals['removable_lines']} lines")
        add(f"- {totals['unused_imports']} unused import(s)")
        add(f"- {totals['unused_fields']} unused field(s)")
        add(f"- {totals['dead_files']} file(s) nothing references")
        add("")
        confident = [f for f in dead["unreachable"] if f["confidence"] == "high"]
        if confident:
            add("| Function | File | Line | Lines | Why we are confident |")
            add("|---|---|--:|--:|---|")
            for f in confident[:limit]:
                why = f["caveats"][0] if f["caveats"] else \
                    "Private, and no resolved call site reaches it."
                add(f"| `{f['simple_name']}` | `{f['file']}` | {f['line']} | "
                    f"{f['loc']} | {why} |")
            add("")
        if dead["reflection_warnings"]:
            add("> **Caution.** This codebase uses runtime dispatch "
                f"({', '.join(w['pattern'] for w in dead['reflection_warnings'][:3])}), "
                "so no static tool can prove a method is unused. Verify before "
                "deleting.")
            add("")

    # -- design problems ----------------------------------------------------
    if analysis.smells:
        add("## Design problems")
        add("")
        by_rule: Dict[str, List[dict]] = {}
        for s in analysis.smells:
            by_rule.setdefault(s["rule"], []).append(s)
        add("| Problem | Count | Worst example |")
        add("|---|--:|---|")
        for rule, items in sorted(by_rule.items(),
                                  key=lambda kv: -len(kv[1]))[:15]:
            worst = items[0]
            add(f"| {worst['title']} | {len(items)} | "
                f"`{worst['file']}:{worst['line']}` |")
        add("")
        add("### The five worth fixing first")
        add("")
        for s in analysis.smells[:5]:
            add(f"**{s['title']}** — `{s['file']}:{s['line']}`  ")
            add(f"{s['detail']}  ")
            add(f"*What to do:* {s['action']}")
            add("")

    # -- dependencies -------------------------------------------------------
    deps = analysis.dependency_view()
    if deps["nodes"]:
        add("## Structure")
        add("")
        add(f"{len(deps['edges'])} dependencies between {len(deps['nodes'])} "
            f"files, with a maximum chain depth of {deps['max_depth']}.")
        add("")
        hubs = sorted(deps["nodes"], key=lambda n: -n["in_degree"])[:8]
        if hubs and hubs[0]["in_degree"] > 0:
            add("**Most depended upon** (changing these ripples furthest):")
            add("")
            for n in hubs:
                if n["in_degree"] == 0:
                    continue
                add(f"- `{n['id']}` — {n['in_degree']} dependent file(s), "
                    f"instability {n['instability']}")
            add("")
        if deps["cycles"]:
            add("**Circular dependencies:**")
            add("")
            for cycle in deps["cycles"][:6]:
                add(f"- {' → '.join(cycle)} → {cycle[0]}")
            add("")

    # -- business rules -----------------------------------------------------
    business = analysis.business
    if business.get("total"):
        add("## Business rules found in the code")
        add("")
        add(f"{business['total']} rule(s) recovered: "
            + ", ".join(f"{v} {k}" for k, v in business["by_kind"].items()) + ".")
        add("")
        add("| Where | Rule |")
        add("|---|---|")
        for rule in business["highlights"][:limit]:
            add(f"| `{rule['file']}:{rule['line']}` | {rule['explanation']} |")
        add("")

    if analysis.glossary:
        add("### Domain vocabulary")
        add("")
        add("The concepts this system actually operates on, ranked by how "
            "often they drive a decision.")
        add("")
        for term in analysis.glossary[:12]:
            add(f"- **{term['term']}** — appears in {term['occurrences']} "
                f"rule(s) across {term['file_count']} file(s)")
        add("")

    # -- classification -----------------------------------------------------
    dist = distribution(analysis.classifications)
    if dist:
        add("## What the code spends its time doing")
        add("")
        total = sum(dist.values()) or 1
        for category, count in dist.items():
            label = CATEGORY_LABELS.get(category, category.title())
            add(f"- {label}: {count} function(s) ({count * 100 // total}%)")
        add("")
        card = analysis.model_card
        if card:
            add(f"*Classifier: {card['algorithm']}. Measured "
                f"{int(card['cross_validated_accuracy'] * 100)}% accurate under "
                f"{card['validation']}. {card['caveat']}*")
            add("")

    # -- history ------------------------------------------------------------
    history = analysis.history
    if history.get("available"):
        add("## Change history")
        add("")
        add(f"{history['commits_scanned']} commits from "
            f"{history['contributors']} contributor(s).")
        add("")
        for c in history["top_contributors"][:5]:
            add(f"- {c['name']}: {c['commits']} commits")
        add("")

    add("---")
    add("")
    add("*Produced by CodeCartographer. Every finding above is derived from "
        "static analysis of the source alone; nothing was executed.*")
    return "\n".join(out)


def _short(path: str, keep: int = 3) -> str:
    parts = path.replace("\\", "/").rstrip("/").split("/")
    return "/".join(parts[-keep:]) if len(parts) > keep else path
