"""Discovery, parsing, and the pipeline that produces a full analysis."""

from __future__ import annotations

import fnmatch
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence

from ..graphs import DiGraph, instability
from ..languages import BY_NAME, detect, language_table
from ..models import AnalysisConfig, SourceFile
from .business import BusinessLogicExtractor
from .classifier import FunctionClassifier, distribution
from .complexity import ComplexityAnalyzer
from .deadcode import DeadCodeDetector
from .hotspots import GitHistory
from .risk import RiskScorer
from .smells import SmellDetector
from .symbols import SymbolTable

ProgressFn = Callable[[str, float], None]

BINARY_SNIFF = b"\x00"


@dataclass
class Analysis:
    """The complete result of one run."""

    config: AnalysisConfig
    files: List[SourceFile] = field(default_factory=list)
    skipped: List[dict] = field(default_factory=list)
    symbols: Optional[SymbolTable] = None
    call_graph: Optional[DiGraph] = None
    file_graph: Optional[DiGraph] = None
    complexity: Dict[str, dict] = field(default_factory=dict)
    complexity_summary: Dict[str, object] = field(default_factory=dict)
    smells: List[dict] = field(default_factory=list)
    dead_code: Dict[str, object] = field(default_factory=dict)
    risk: Dict[str, dict] = field(default_factory=dict)
    risk_summary: Dict[str, object] = field(default_factory=dict)
    classifications: Dict[str, List[dict]] = field(default_factory=dict)
    model_card: Dict[str, object] = field(default_factory=dict)
    business: Dict[str, object] = field(default_factory=dict)
    glossary: List[dict] = field(default_factory=list)
    history: Dict[str, object] = field(default_factory=dict)
    resolution: Dict[str, object] = field(default_factory=dict)
    duration_seconds: float = 0.0
    generated_at: str = ""

    # -- convenience --------------------------------------------------------

    def file(self, rel_path: str) -> Optional[SourceFile]:
        return next((f for f in self.files if f.rel_path == rel_path), None)

    def overview(self) -> Dict[str, object]:
        languages: Dict[str, dict] = {}
        for sf in self.files:
            entry = languages.setdefault(
                sf.language, {"files": 0, "lines": 0, "functions": 0})
            entry["files"] += 1
            entry["lines"] += sf.lines
            entry["functions"] += len(sf.functions)
        for name, entry in languages.items():
            adapter = BY_NAME.get(name)
            entry["label"] = adapter.label if adapter else name
            entry["family"] = adapter.family if adapter else "other"

        diagnostics = sum(len(f.diagnostics) for f in self.files)
        return {
            "root": self.config.root,
            "files": len(self.files),
            "skipped": len(self.skipped),
            "total_lines": sum(f.lines for f in self.files),
            "total_sloc": self.complexity_summary.get("total_sloc", 0),
            "functions": sum(len(f.functions) for f in self.files),
            "types": sum(len(f.types) for f in self.files),
            "languages": languages,
            "language_count": len(languages),
            "diagnostics": diagnostics,
            "files_with_diagnostics": sum(
                1 for f in self.files if f.diagnostics),
            "dependencies": len(self.file_graph.edges) if self.file_graph else 0,
            "cycles": len(self.file_graph.cycles()) if self.file_graph else 0,
            "duration_seconds": round(self.duration_seconds, 2),
            "generated_at": self.generated_at,
        }

    def dependency_view(self) -> Dict[str, object]:
        """Graph data shaped for the dashboard."""
        if not self.file_graph:
            return {"nodes": [], "edges": [], "cycles": []}
        graph = self.file_graph
        ranks = graph.pagerank()
        cycles = graph.cycles()
        in_cycle = {n for c in cycles for n in c}
        nodes = []
        for rel in graph.nodes:
            sf = self.file(rel)
            risk = self.risk.get(rel, {})
            nodes.append({
                "id": rel,
                "label": rel.split("/")[-1],
                "language": sf.language if sf else "unknown",
                "lines": sf.lines if sf else 0,
                "functions": len(sf.functions) if sf else 0,
                "in_degree": graph.in_degree(rel),
                "out_degree": graph.out_degree(rel),
                "importance": round(ranks.get(rel, 0.0), 5),
                "instability": instability(graph, rel),
                "risk": risk.get("score", 0),
                "risk_level": risk.get("level", "Low"),
                "in_cycle": rel in in_cycle,
            })
        edges = [
            {"from": s, "to": d, **graph.edge_data.get((s, d), {})}
            for s, d in graph.edges
        ]
        return {
            "nodes": nodes,
            "edges": edges,
            "cycles": cycles,
            "max_depth": graph.longest_path_length(),
        }

    def to_dict(self, include_source: bool = False) -> Dict[str, object]:
        return {
            "schema_version": 2,
            "overview": self.overview(),
            "config": self.config.to_dict(),
            "languages_supported": language_table(),
            "files": [f.to_dict(include_source) for f in self.files],
            "skipped": self.skipped,
            "dependencies": self.dependency_view(),
            "complexity": self.complexity,
            "complexity_summary": self.complexity_summary,
            "smells": self.smells,
            "dead_code": self.dead_code,
            "risk": self.risk,
            "risk_summary": self.risk_summary,
            "classifications": self.classifications,
            "classification_distribution": distribution(self.classifications),
            "model_card": self.model_card,
            "business": self.business,
            "glossary": self.glossary,
            "history": self.history,
            "resolution": self.resolution,
        }


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

def discover(config: AnalysisConfig) -> List[str]:
    """Walk the tree honouring include/exclude globs and the file cap."""
    root = os.path.abspath(config.root)
    found: List[str] = []
    for dirpath, dirnames, filenames in os.walk(
            root, followlinks=config.follow_symlinks):
        rel_dir = os.path.relpath(dirpath, root)
        dirnames[:] = [
            d for d in sorted(dirnames)
            if not _excluded(os.path.join(rel_dir, d, "x"), config.exclude_globs)
            and not d.startswith(".")
        ]
        for name in sorted(filenames):
            rel = os.path.normpath(os.path.join(rel_dir, name))
            if rel.startswith("./"):
                rel = rel[2:]
            if _excluded(rel, config.exclude_globs):
                continue
            if config.include_globs and not any(
                    fnmatch.fnmatch(rel, g) for g in config.include_globs):
                continue
            found.append(rel)
            if len(found) >= config.max_files:
                return found
    return found


def _excluded(rel: str, patterns: Sequence[str]) -> bool:
    normalised = rel.replace(os.sep, "/")
    for pattern in patterns:
        if fnmatch.fnmatch(normalised, pattern) or \
                fnmatch.fnmatch("/" + normalised, pattern):
            return True
        bare = pattern.strip("*/")
        if bare and f"/{bare}/" in f"/{normalised}/":
            return True
    return False


def read_source(path: str, limit: int) -> Optional[str]:
    """Read a file as text, refusing binaries and oversized blobs."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if size > limit:
        return None
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return None
    if BINARY_SNIFF in raw[:4096]:
        return None
    for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------

def analyse(config: AnalysisConfig,
            progress: Optional[ProgressFn] = None) -> Analysis:
    """Run every pass and return one Analysis."""

    started = time.time()

    def step(message: str, fraction: float) -> None:
        if progress:
            progress(message, fraction)

    root = os.path.abspath(config.root)
    if not os.path.isdir(root):
        raise NotADirectoryError(f"Not a directory: {config.root}")
    config.root = root

    analysis = Analysis(config=config)

    step("Discovering files", 0.02)
    candidates = discover(config)

    step(f"Parsing {len(candidates)} candidate files", 0.08)
    wanted = set(config.languages) if config.languages else None
    for index, rel in enumerate(candidates):
        absolute = os.path.join(root, rel)
        source = read_source(absolute, config.max_file_bytes)
        if source is None:
            analysis.skipped.append({"file": rel, "reason": "binary or too large"})
            continue
        adapter = detect(absolute, source)
        if adapter is None:
            continue
        if wanted and adapter.name not in wanted:
            continue
        analysis.files.append(adapter.parse(absolute, rel.replace(os.sep, "/"),
                                            source))
        if progress and index % 50 == 0:
            step(f"Parsed {index}/{len(candidates)}",
                 0.08 + 0.32 * (index / max(1, len(candidates))))

    if not analysis.files:
        analysis.duration_seconds = time.time() - started
        analysis.generated_at = _now()
        return analysis

    step("Resolving symbols and call graph", 0.45)
    analysis.symbols = SymbolTable(analysis.files)
    analysis.call_graph, _ = analysis.symbols.build_call_graph()
    analysis.file_graph = analysis.symbols.build_file_graph(analysis.call_graph)
    analysis.resolution = analysis.symbols.resolution_report()

    step("Measuring complexity", 0.58)
    complexity = ComplexityAnalyzer(analysis.files)
    analysis.complexity = complexity.run()
    analysis.complexity_summary = complexity.summary()

    step("Detecting design problems", 0.66)
    analysis.smells = SmellDetector(
        analysis.files, analysis.symbols, analysis.call_graph,
        analysis.file_graph, analysis.complexity).run()

    step("Finding dead code", 0.74)
    analysis.dead_code = DeadCodeDetector(
        analysis.symbols, analysis.call_graph, analysis.file_graph,
        analysis.files).run().to_dict()

    step("Reading change history", 0.80)
    history = GitHistory(root).load() if config.use_git_history else None
    analysis.history = history.summary() if history else {"available": False}

    step("Scoring change risk", 0.86)
    scorer = RiskScorer(analysis.files, analysis.complexity,
                        analysis.file_graph, analysis.smells, history)
    analysis.risk = scorer.run()
    analysis.risk_summary = scorer.summary()

    step("Classifying functions", 0.92)
    classifier = FunctionClassifier()
    analysis.classifications = classifier.classify_all(analysis.files)
    analysis.model_card = classifier.model_card()

    step("Extracting business rules", 0.97)
    extractor = BusinessLogicExtractor(analysis.files)
    extractor.run()
    analysis.business = extractor.summary()
    analysis.glossary = extractor.glossary()

    analysis.duration_seconds = time.time() - started
    analysis.generated_at = _now()
    step("Done", 1.0)
    return analysis


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
