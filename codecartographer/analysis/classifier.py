"""Classify what a function is *for*, and be honest about how well it works.

The previous classifier trained a scikit-learn Naive Bayes on a list of
hand-written phrases and then reported ``max(predict_proba)`` as a
"confidence" — a number that is high precisely when the model is
overconfident, and that was never measured against held-out data.

This version:

* is pure Python, so the tool installs with no dependencies at all;
* combines a learned model with explicit structural evidence (a method with
  no parameters returning a field is an accessor, whatever it is called);
* cross-validates itself at startup and publishes the measured accuracy, so
  the UI can say "78% accurate on held-out examples" instead of implying
  certainty;
* returns ``unclassified`` rather than guessing when nothing fits.
"""

from __future__ import annotations

import math
import random
import re
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..models import FunctionDef, SourceFile

CATEGORIES = (
    "business_logic",
    "data_access",
    "validation",
    "transformation",
    "orchestration",
    "presentation",
    "error_handling",
    "configuration",
    "accessor",
    "test",
)

CATEGORY_LABELS = {
    "business_logic": "Business logic",
    "data_access": "Data access",
    "validation": "Validation",
    "transformation": "Transformation / formatting",
    "orchestration": "Orchestration",
    "presentation": "Presentation",
    "error_handling": "Error handling",
    "configuration": "Configuration",
    "accessor": "Accessor",
    "test": "Test",
}

# Training phrases. Written as natural phrases; the vectoriser also sees the
# camelCase and snake_case spellings of each, which is how real method names
# are written.
TRAINING: Dict[str, Tuple[str, ...]] = {
    "business_logic": (
        "calculate tax", "compute total", "apply discount", "process order",
        "calculate shipping cost", "calculate commission", "apply interest",
        "determine eligibility", "calculate balance", "post journal entry",
        "compute premium", "assess penalty", "apply late fee", "rate policy",
        "calculate payroll", "compute depreciation", "settle invoice",
        "allocate inventory", "compute discount rate", "accrue interest",
        "compute net pay", "apply surcharge", "determine credit limit",
        "calculate loyalty tier", "compute subtotal", "prorate charge",
        "evaluate risk score", "compute reorder point", "apply tariff",
    ),
    "data_access": (
        "execute query", "fetch record", "insert row", "update record",
        "delete record", "open connection", "close connection", "commit",
        "rollback transaction", "select customer", "load from database",
        "save to database", "read file", "write file", "find by id",
        "get connection", "prepare statement", "execute update", "persist",
        "bulk insert", "read cursor", "fetch page", "query table",
        "load configuration file", "serialize to disk", "open dataset",
        "read record sequential", "write master file", "rewrite record",
    ),
    "validation": (
        "validate input", "check required field", "is valid email",
        "verify format", "check range", "ensure not null", "is empty",
        "validate zip code", "check permissions", "verify signature",
        "assert precondition", "is well formed", "check length",
        "validate date", "verify checksum", "is numeric", "check duplicate",
        "validate account number", "verify credentials", "check mandatory",
    ),
    "transformation": (
        "format date", "parse string", "convert currency", "map to dto",
        "serialize object", "deserialize json", "encode base64",
        "normalize address", "to upper case", "build display string",
        "translate code", "convert units", "format number", "pad left",
        "trim whitespace", "marshal record", "unpack buffer", "reformat",
        "transform payload", "flatten structure", "edit picture clause",
    ),
    "orchestration": (
        "process batch", "run job", "coordinate workflow", "dispatch request",
        "handle message", "schedule task", "execute pipeline", "main routine",
        "control flow", "invoke service", "route request", "start process",
        "perform housekeeping", "drive report", "sequence steps",
        "manage transaction", "orchestrate import", "run nightly cycle",
    ),
    "presentation": (
        "render page", "display message", "draw chart", "print report",
        "show dialog", "update screen", "paint form", "write header line",
        "format report line", "display menu", "refresh grid", "build html",
        "print detail line", "show error dialog", "populate list box",
        "write page footer", "layout columns",
    ),
    "error_handling": (
        "handle error", "log exception", "raise error", "retry operation",
        "handle timeout", "recover from failure", "abort transaction",
        "write error log", "notify failure", "fallback handler",
        "rollback on error", "trap error", "report abend", "handle sql error",
    ),
    "configuration": (
        "load settings", "read config", "get property", "set parameter",
        "initialize environment", "configure logging", "load defaults",
        "bind options", "read ini file", "setup context", "apply profile",
        "register bean", "wire dependencies",
    ),
    "accessor": (
        "get id", "set id", "get name", "set name", "get amount", "set amount",
        "get status", "set status", "is active", "get total", "set total",
        "get customer", "set customer", "get date", "set date", "get type",
        "has value", "get description", "set description", "get code",
    ),
    "test": (
        "test calculate total", "should return true", "assert equals",
        "setup fixture", "teardown fixture", "given when then", "mock service",
        "verify called once", "test invalid input", "expect exception",
    ),
}


def _tokenise(text: str) -> List[str]:
    """Split identifiers the way developers actually write them."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = re.sub(r"[_\-.]+", " ", text)
    words = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9]*", text)
             if len(w) > 1]
    tokens = list(words)
    tokens += [f"{a}_{b}" for a, b in zip(words, words[1:])]   # bigrams
    return tokens


class NaiveBayes:
    """Multinomial Naive Bayes in ~40 lines, no third-party dependencies."""

    def __init__(self, alpha: float = 0.25):
        self.alpha = alpha
        self.log_prior: Dict[str, float] = {}
        self.log_likelihood: Dict[str, Dict[str, float]] = {}
        self.vocabulary: set = set()
        self._default: Dict[str, float] = {}

    def fit(self, samples: Sequence[Tuple[List[str], str]]) -> "NaiveBayes":
        by_class: Dict[str, Counter] = defaultdict(Counter)
        class_counts: Counter = Counter()
        for tokens, label in samples:
            class_counts[label] += 1
            by_class[label].update(tokens)
            self.vocabulary.update(tokens)

        total_docs = sum(class_counts.values()) or 1
        vocab_size = len(self.vocabulary) or 1
        for label, counts in by_class.items():
            self.log_prior[label] = math.log(class_counts[label] / total_docs)
            denominator = sum(counts.values()) + self.alpha * vocab_size
            self.log_likelihood[label] = {
                token: math.log((count + self.alpha) / denominator)
                for token, count in counts.items()
            }
            self._default[label] = math.log(self.alpha / denominator)
        return self

    def scores(self, tokens: Sequence[str]) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for label, prior in self.log_prior.items():
            total = prior
            likelihood = self.log_likelihood[label]
            fallback = self._default[label]
            for token in tokens:
                if token in self.vocabulary:
                    total += likelihood.get(token, fallback)
            out[label] = total
        return out

    def predict_proba(self, tokens: Sequence[str]) -> Dict[str, float]:
        scores = self.scores(tokens)
        if not scores:
            return {}
        top = max(scores.values())
        exp = {k: math.exp(v - top) for k, v in scores.items()}
        total = sum(exp.values()) or 1.0
        return {k: v / total for k, v in exp.items()}


def _spellings(phrase: str) -> List[str]:
    """The ways a developer might actually write this phrase as an identifier."""
    words = phrase.split()
    return sorted({
        phrase,
        "".join(w.capitalize() for w in words),
        words[0] + "".join(w.capitalize() for w in words[1:]),
        "_".join(words),
        "-".join(words),
    })


def _build_samples() -> List[Tuple[List[str], str]]:
    samples: List[Tuple[List[str], str]] = []
    for label, phrases in TRAINING.items():
        for phrase in phrases:
            for spelling in _spellings(phrase):
                samples.append((_tokenise(spelling), label))
    return samples


class FunctionClassifier:
    """Learned model plus structural rules, with a measured accuracy figure."""

    def __init__(self, seed: int = 20240101):
        self.samples = _build_samples()
        self.model = NaiveBayes().fit(self.samples)
        self.accuracy, self.per_class = self._cross_validate(seed)

    # -- self-evaluation ----------------------------------------------------

    def _cross_validate(self, seed: int, folds: int = 5
                        ) -> Tuple[float, Dict[str, float]]:
        """Grouped five-fold cross-validation.

        The folds are split by *phrase*, not by sample. Each phrase appears in
        the corpus five times (as ``calculate tax``, ``calculateTax``,
        ``CalculateTax``, ``calculate_tax``, ``calculate-tax``), so a plain
        random split would put camelCase in training and snake_case in test
        and score a near-perfect 99% by memorising the answer. Grouping the
        spellings together makes the number mean what it claims to mean.
        """
        groups: List[Tuple[str, str]] = [
            (phrase, label)
            for label, phrases in TRAINING.items()
            for phrase in phrases
        ]
        random.Random(seed).shuffle(groups)

        correct = 0
        per_class_hits: Counter = Counter()
        per_class_total: Counter = Counter()

        for fold in range(folds):
            test_phrases = {p for i, (p, _) in enumerate(groups)
                            if i % folds == fold}
            train, test = [], []
            for label, phrases in TRAINING.items():
                for phrase in phrases:
                    bucket = test if phrase in test_phrases else train
                    for spelling in _spellings(phrase):
                        bucket.append((_tokenise(spelling), label))
            if not train or not test:
                continue
            model = NaiveBayes().fit(train)
            for tokens, label in test:
                probabilities = model.predict_proba(tokens)
                if not probabilities:
                    continue
                predicted = max(probabilities, key=probabilities.get)
                per_class_total[label] += 1
                if predicted == label:
                    correct += 1
                    per_class_hits[label] += 1

        total = sum(per_class_total.values()) or 1
        per_class = {
            label: round(per_class_hits[label] / count, 3)
            for label, count in per_class_total.items() if count
        }
        return round(correct / total, 3), per_class

    # -- prediction ---------------------------------------------------------

    def classify(self, fn: FunctionDef, source: str = "") -> dict:
        structural = self._structural(fn, source)
        tokens = _tokenise(fn.name)
        if fn.doc:
            tokens += _tokenise(fn.doc[:200])
        body_tokens = _tokenise(source[:2000]) if source else []
        tokens += body_tokens[:120]

        probabilities = self.model.predict_proba(tokens)
        if structural:
            label, boost, reason = structural
            probabilities[label] = min(
                1.0, probabilities.get(label, 0.0) + boost)
            total = sum(probabilities.values()) or 1.0
            probabilities = {k: v / total for k, v in probabilities.items()}
        else:
            reason = "name and body vocabulary"

        if not probabilities:
            return {"category": "unclassified", "confidence": 0.0,
                    "label": "Unclassified", "evidence": reason,
                    "alternatives": []}

        ranked = sorted(probabilities.items(), key=lambda kv: -kv[1])
        best, score = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
        # Margin matters more than absolute probability: a 0.4 that beats the
        # next class by 0.3 is a better answer than a 0.5 that beats it by 0.01.
        margin = score - runner_up
        if score < 0.28 or margin < 0.08:
            best, score = "unclassified", score

        return {
            "category": best,
            "label": CATEGORY_LABELS.get(best, "Unclassified"),
            "confidence": round(score, 3),
            "margin": round(margin, 3),
            "evidence": reason,
            "alternatives": [
                {"category": c, "label": CATEGORY_LABELS.get(c, c),
                 "confidence": round(p, 3)}
                for c, p in ranked[1:4]
            ],
        }

    def _structural(self, fn: FunctionDef,
                    source: str) -> Optional[Tuple[str, float, str]]:
        """Evidence from shape, which beats naming conventions when they conflict."""
        name = fn.name.lower()
        loc = fn.metrics.get("loc", 1)
        cc = fn.metrics.get("cyclomatic", 1)

        if name.startswith("test") or "test" in fn.file.lower() or \
                any("test" in a.lower() for a in fn.annotations):
            return "test", 0.6, "test naming or annotation"

        if (name.startswith(("get", "set", "is", "has")) and
                len(fn.params) <= 1 and loc <= 6 and cc <= 2):
            return "accessor", 0.55, "trivial body with accessor signature"

        if source:
            body = source.lower()
            sql_hits = len(re.findall(
                r"\b(select|insert into|update .* set|delete from|"
                r"executequery|preparedstatement|connection|cursor|"
                r"resultset|open .* input|read .* into)\b", body))
            if sql_hits >= 2:
                return "data_access", 0.45, f"{sql_hits} data-access constructs"

            if re.search(r"\b(catch|except|on error|rescue|abend)\b", body) and \
                    cc <= 4 and loc <= 30:
                return "error_handling", 0.3, "body is mostly a handler"

            arithmetic = len(re.findall(r"[\w)]\s*[-+*/]\s*[\w(]", body))
            if arithmetic >= 6 and cc >= 3:
                return "business_logic", 0.35, (
                    f"{arithmetic} arithmetic operations combined with branching")

        if fn.is_constructor:
            return "configuration", 0.3, "constructor"
        return None

    # -- batch --------------------------------------------------------------

    def classify_all(self, files: Sequence[SourceFile]) -> Dict[str, List[dict]]:
        results: Dict[str, List[dict]] = {}
        for sf in files:
            lines = sf.source.split("\n")
            entries: List[dict] = []
            for fn in sf.functions:
                body = "\n".join(lines[fn.line - 1:fn.end_line])
                verdict = self.classify(fn, body)
                verdict.update({
                    "function": fn.qualified_name,
                    "name": fn.name,
                    "line": fn.line,
                    "key": fn.key,
                })
                entries.append(verdict)
            results[sf.rel_path] = entries
        return results

    def model_card(self) -> Dict[str, object]:
        """What this model is and is not, for display next to its output."""
        return {
            "algorithm": "Multinomial Naive Bayes (word + bigram tokens) "
                         "combined with structural rules",
            "training_examples": len(self.samples),
            "categories": list(CATEGORIES),
            "cross_validated_accuracy": self.accuracy,
            "per_category_accuracy": self.per_class,
            "validation": "5-fold cross-validation on the built-in phrase corpus",
            "caveat": "Accuracy is measured against the built-in corpus of "
                      "naming conventions, not against your codebase. Treat "
                      "each label as a hint for navigation, not a fact.",
        }


def distribution(classifications: Dict[str, List[dict]]) -> Dict[str, int]:
    counts: Counter = Counter()
    for entries in classifications.values():
        for entry in entries:
            counts[entry["category"]] += 1
    return dict(counts.most_common())
