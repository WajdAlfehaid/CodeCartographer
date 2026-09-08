"""Change risk: how likely is editing this file to hurt, and why.

The score exists to be argued with, so every factor states the raw
measurement, the normalised value, the weight, and a sentence a developer can
verify. A number without that is just a vibe with decimal places.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

from ..graphs import DiGraph, instability
from ..models import SourceFile
from .hotspots import GitHistory, hotspot_quadrant

#: name -> (weight, human label). Weights sum to 1.0.
FACTORS = {
    "maintainability": (0.24, "Internal complexity"),
    "blast_radius": (0.22, "Blast radius"),
    "churn": (0.16, "Change frequency"),
    "defect_signals": (0.14, "Known problems"),
    "test_shield": (0.10, "Test coverage signal"),
    "knowledge": (0.08, "Knowledge concentration"),
    "documentation": (0.06, "Documentation"),
}

LEVELS = (
    (75, "Critical"),
    (55, "High"),
    (35, "Moderate"),
    (0, "Low"),
)


class RiskScorer:
    def __init__(self, files: Sequence[SourceFile], complexity: Dict[str, dict],
                 file_graph: DiGraph, smells: Sequence[dict],
                 history: Optional[GitHistory] = None):
        self.files = list(files)
        self.complexity = complexity
        self.file_graph = file_graph
        self.history = history
        self.scores: Dict[str, dict] = {}

        self._smells_by_file: Dict[str, List[dict]] = {}
        for smell in smells:
            self._smells_by_file.setdefault(smell["file"], []).append(smell)

        self._pagerank = file_graph.pagerank() if len(file_graph) else {}
        self._test_files = {
            f.rel_path for f in self.files
            if "test" in f.rel_path.lower() or "spec" in f.rel_path.lower()
        }
        self._max_commits = 1
        if history and history.available:
            self._max_commits = max(
                [history.stats_for(f.rel_path).get("commits", 0)
                 for f in self.files] + [1])

    # -- main ---------------------------------------------------------------

    def run(self) -> Dict[str, dict]:
        for sf in self.files:
            self.scores[sf.rel_path] = self._score(sf)
        self._add_percentiles()
        return self.scores

    def _score(self, sf: SourceFile) -> dict:
        rel = sf.rel_path
        metrics = self.complexity.get(rel, {})
        factors: Dict[str, dict] = {}

        # 1. Internal complexity, via the maintainability index.
        mi = metrics.get("maintainability_index", 70.0)
        factors["maintainability"] = _factor(
            "maintainability", 100.0 - mi,
            f"Maintainability index {mi}/100 "
            f"({metrics.get('rating', 'unknown')}), "
            f"peak cyclomatic {metrics.get('cyclomatic_max', 0)}, "
            f"deepest nesting {metrics.get('max_nesting', 0)}.")

        # 2. Blast radius: who breaks if this changes.
        fan_in = self.file_graph.in_degree(rel)
        rank = self._pagerank.get(rel, 0.0)
        rank_scaled = min(100.0, rank * len(self.file_graph) * 55.0)
        radius = min(100.0, max(fan_in * 11.0, rank_scaled))
        factors["blast_radius"] = _factor(
            "blast_radius", radius,
            f"{fan_in} file(s) depend on this directly; instability "
            f"{instability(self.file_graph, rel)}.")

        # 3. Change frequency.
        git_stats = self.history.stats_for(rel) if self.history and \
            self.history.available else {"tracked": False}
        if git_stats.get("tracked"):
            commits = git_stats.get("commits", 0)
            churn = min(100.0, 100.0 * math.log1p(commits) /
                        math.log1p(self._max_commits))
            recency = git_stats.get("days_since_change")
            if recency is not None and recency > 730:
                churn *= 0.6      # stable for two years is a good sign
            factors["churn"] = _factor(
                "churn", churn,
                f"{commits} commits touched this file; last change "
                f"{recency} days ago.")
        else:
            factors["churn"] = _factor(
                "churn", 40.0,
                "No git history available, so change frequency is assumed "
                "average rather than measured.", measured=False)

        # 4. Problems already found.
        found = self._smells_by_file.get(rel, [])
        weights = {"critical": 22, "high": 12, "medium": 6, "low": 2}
        defect = min(100.0, sum(weights.get(s["severity"], 3) for s in found))
        top = ", ".join(sorted({s["title"] for s in found})[:3]) or "none found"
        factors["defect_signals"] = _factor(
            "defect_signals", defect,
            f"{len(found)} design issue(s) detected: {top}.")

        # 5. Is anything testing this?
        has_test = self._has_test(sf)
        factors["test_shield"] = _factor(
            "test_shield", 15.0 if has_test else 85.0,
            "A test file appears to reference this code."
            if has_test else
            "No test file in this project appears to reference this code.")

        # 6. Who still understands it.
        if git_stats.get("tracked"):
            conc = git_stats.get("knowledge_concentration") or 0.0
            authors = git_stats.get("authors", 1)
            value = 100.0 * conc if authors <= 2 else 60.0 * conc
            factors["knowledge"] = _factor(
                "knowledge", value,
                f"{authors} author(s); the top contributor wrote "
                f"{round(conc * 100)}% of the changes.")
        else:
            factors["knowledge"] = _factor(
                "knowledge", 40.0, "Authorship unknown without git history.",
                measured=False)

        # 7. Documentation.
        ratio = metrics.get("comment_ratio", 0.0)
        doc_score = 100.0 if ratio < 0.03 else max(0.0, 100.0 - ratio * 400.0)
        factors["documentation"] = _factor(
            "documentation", doc_score,
            f"{round(ratio * 100, 1)}% of lines are comments.")

        total = sum(f["normalised"] * f["weight"] for f in factors.values())
        # Unparseable files are risky for a different reason: we cannot see in.
        if sf.parse_confidence < 0.7:
            total = min(100.0, total + 10.0)

        return {
            "file": rel,
            "language": sf.language,
            "score": round(total, 1),
            "level": _level(total),
            "factors": factors,
            "narrative": self._narrative(sf, factors, total),
            "git": git_stats,
            "top_smells": [
                {"rule": s["rule"], "title": s["title"], "line": s["line"],
                 "severity": s["severity"]}
                for s in sorted(found,
                                key=lambda s: {"critical": 0, "high": 1,
                                               "medium": 2, "low": 3}
                                .get(s["severity"], 9))[:5]
            ],
        }

    def _has_test(self, sf: SourceFile) -> bool:
        if sf.rel_path in self._test_files:
            return True
        names = [t.name for t in sf.types] or [sf.name.rsplit(".", 1)[0]]
        for test_path in self._test_files:
            test_file = next((f for f in self.files
                              if f.rel_path == test_path), None)
            if not test_file:
                continue
            if any(n and n in test_file.source for n in names):
                return True
        return False

    def _narrative(self, sf: SourceFile, factors: Dict[str, dict],
                   total: float) -> str:
        ranked = sorted(factors.items(),
                        key=lambda kv: -kv[1]["normalised"] * kv[1]["weight"])
        drivers = [FACTORS[k][1].lower() for k, v in ranked[:2]
                   if v["normalised"] > 45]
        level = _level(total).lower()
        if not drivers:
            return (f"{sf.rel_path} looks low risk to change: nothing stands "
                    f"out across complexity, coupling or history.")
        joined = " and ".join(drivers)
        return (f"{sf.rel_path} carries {level} change risk, driven mainly by "
                f"{joined}. " + ranked[0][1]["detail"])

    def _add_percentiles(self) -> None:
        if not self.scores:
            return
        complexities = sorted(
            self.complexity.get(p, {}).get("cyclomatic_total", 0)
            for p in self.scores)
        churns = sorted(
            (self.history.stats_for(p).get("commits", 0)
             if self.history and self.history.available else 0)
            for p in self.scores)

        for path, entry in self.scores.items():
            cc = self.complexity.get(path, {}).get("cyclomatic_total", 0)
            commits = entry["git"].get("commits", 0) if entry.get("git") else 0
            entry["complexity_percentile"] = _percentile(complexities, cc)
            entry["churn_percentile"] = _percentile(churns, commits)
            entry["quadrant"] = hotspot_quadrant(
                entry["complexity_percentile"], entry["churn_percentile"])

    # -- summaries ----------------------------------------------------------

    def top(self, limit: int = 15) -> List[dict]:
        return sorted(self.scores.values(),
                      key=lambda s: -s["score"])[:limit]

    def summary(self) -> Dict[str, object]:
        if not self.scores:
            return {}
        values = [s["score"] for s in self.scores.values()]
        distribution: Dict[str, int] = {}
        for s in self.scores.values():
            distribution[s["level"]] = distribution.get(s["level"], 0) + 1
        quadrants: Dict[str, int] = {}
        for s in self.scores.values():
            q = s.get("quadrant", "quiet")
            quadrants[q] = quadrants.get(q, 0) + 1
        return {
            "files": len(values),
            "average": round(sum(values) / len(values), 1),
            "max": round(max(values), 1),
            "distribution": distribution,
            "quadrants": quadrants,
            "refactor_first": [
                s["file"] for s in sorted(self.scores.values(),
                                          key=lambda s: -s["score"])
                if s.get("quadrant") == "refactor-first"
            ][:10],
        }


def _factor(name: str, raw: float, detail: str, measured: bool = True) -> dict:
    weight, label = FACTORS[name]
    value = max(0.0, min(100.0, raw))
    return {
        "label": label,
        "normalised": round(value, 1),
        "weight": weight,
        "contribution": round(value * weight, 2),
        "detail": detail,
        "measured": measured,
    }


def _level(score: float) -> str:
    for threshold, name in LEVELS:
        if score >= threshold:
            return name
    return "Low"


def _percentile(sorted_values: Sequence[float], value: float) -> float:
    if not sorted_values:
        return 0.0
    below = sum(1 for v in sorted_values if v < value)
    return round(below / len(sorted_values), 3)
