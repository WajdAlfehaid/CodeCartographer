"""Change history from git: churn, authorship, and knowledge concentration.

Static metrics alone rank a file that has been stable for a decade the same
as one that changes every sprint. History is what separates "complex" from
"complex *and* actively dangerous", and it is also the only way to see that
one departed contributor wrote 90% of a critical module.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence


class GitHistory:
    """Reads history if the analysed tree happens to be a git repository."""

    def __init__(self, root: str, max_commits: int = 4000):
        self.root = root
        self.max_commits = max_commits
        self.available = False
        self.error: Optional[str] = None
        self.commits: int = 0
        self.by_file: Dict[str, dict] = {}
        self.first_commit: Optional[str] = None
        self.last_commit: Optional[str] = None

    def load(self) -> "GitHistory":
        if not self._is_repo():
            return self
        try:
            self._read_log()
            self.available = True
        except Exception as exc:
            self.error = str(exc)
        return self

    def _is_repo(self) -> bool:
        try:
            out = self._git(["rev-parse", "--is-inside-work-tree"])
        except Exception as exc:
            self.error = f"git unavailable: {exc}"
            return False
        if out.strip() != "true":
            self.error = "not a git repository"
            return False
        return True

    def _git(self, args: Sequence[str], timeout: int = 60) -> str:
        proc = subprocess.run(
            ["git", "-C", self.root, *args],
            capture_output=True, text=True, timeout=timeout,
            errors="replace")
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip()[:200] or "git failed")
        return proc.stdout

    def _read_log(self) -> None:
        raw = self._git([
            "log", f"--max-count={self.max_commits}",
            "--pretty=format:@@%H|%an|%aI", "--name-only", "--no-merges",
        ])
        current: Optional[tuple] = None
        seen_commits = set()
        for line in raw.split("\n"):
            if line.startswith("@@"):
                sha, author, date = line[2:].split("|", 2)
                current = (sha, author, date)
                seen_commits.add(sha)
                self.last_commit = self.last_commit or date
                self.first_commit = date
                continue
            path = line.strip()
            if not path or current is None:
                continue
            entry = self.by_file.setdefault(path, {
                "commits": 0, "authors": Counter(), "last_change": None,
                "first_change": None,
            })
            entry["commits"] += 1
            entry["authors"][current[1]] += 1
            entry["last_change"] = entry["last_change"] or current[2]
            entry["first_change"] = current[2]
        self.commits = len(seen_commits)

    # -- per-file view ------------------------------------------------------

    def stats_for(self, rel_path: str) -> Dict[str, object]:
        entry = self.by_file.get(rel_path)
        if not entry:
            # git reports paths from the repo root; the analysis root may be
            # a subdirectory, so try a suffix match once.
            for path, value in self.by_file.items():
                if path.endswith("/" + rel_path) or path == rel_path:
                    entry = value
                    break
        if not entry:
            return {"tracked": False, "commits": 0, "authors": 0,
                    "days_since_change": None, "knowledge_concentration": None}

        authors: Counter = entry["authors"]
        total = sum(authors.values()) or 1
        top_share = authors.most_common(1)[0][1] / total if authors else 0.0
        return {
            "tracked": True,
            "commits": entry["commits"],
            "authors": len(authors),
            "top_author": authors.most_common(1)[0][0] if authors else None,
            "knowledge_concentration": round(top_share, 3),
            "days_since_change": _days_since(entry["last_change"]),
            "age_days": _days_since(entry["first_change"]),
        }

    def summary(self) -> Dict[str, object]:
        if not self.available:
            return {"available": False, "reason": self.error}
        all_authors: Counter = Counter()
        for entry in self.by_file.values():
            all_authors.update(entry["authors"])
        return {
            "available": True,
            "commits_scanned": self.commits,
            "files_tracked": len(self.by_file),
            "contributors": len(all_authors),
            "top_contributors": [
                {"name": n, "commits": c} for n, c in all_authors.most_common(8)
            ],
            "history_starts": self.first_commit,
            "history_ends": self.last_commit,
        }


def _days_since(iso: Optional[str]) -> Optional[int]:
    if not iso:
        return None
    try:
        when = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - when).days)


def hotspot_quadrant(complexity_percentile: float,
                     churn_percentile: float) -> str:
    """Where a file sits on the complexity/churn grid.

    The top-right quadrant — complicated code that also changes constantly —
    is where refactoring effort pays back fastest.
    """
    hot = churn_percentile >= 0.6
    hard = complexity_percentile >= 0.6
    if hot and hard:
        return "refactor-first"
    if hot and not hard:
        return "healthy-churn"
    if hard and not hot:
        return "stable-but-complex"
    return "quiet"
