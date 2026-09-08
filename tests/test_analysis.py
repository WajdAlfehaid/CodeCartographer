"""The analysis passes, especially the claims that would be easy to fake."""

import os
import shutil
import tempfile

import pytest

from codecartographer.analysis.classifier import FunctionClassifier
from codecartographer.analysis.engine import analyse, discover
from codecartographer.analysis.symbols import SymbolTable
from codecartographer.graphs import DiGraph
from codecartographer.languages import BY_NAME
from codecartographer.models import AnalysisConfig

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

class Project:
    """A throwaway directory of source files."""

    def __init__(self, files):
        self.dir = tempfile.mkdtemp(prefix="cc-test-")
        for name, content in files.items():
            path = os.path.join(self.dir, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(content)

    def analyse(self, **kwargs):
        config = AnalysisConfig(root=self.dir, use_git_history=False, **kwargs)
        return analyse(config)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.dir, ignore_errors=True)


# --------------------------------------------------------------------------
# Graphs
# --------------------------------------------------------------------------

def test_scc_finds_a_cycle():
    g = DiGraph()
    g.add_edge("a", "b")
    g.add_edge("b", "c")
    g.add_edge("c", "a")
    g.add_edge("d", "a")
    cycles = g.cycles()
    assert len(cycles) == 1
    assert set(cycles[0]) == {"a", "b", "c"}


def test_scc_is_iterative_and_survives_a_deep_chain():
    g = DiGraph()
    for i in range(3000):
        g.add_edge(str(i), str(i + 1))
    assert g.cycles() == []
    assert len(g.strongly_connected_components()) == 3001


def test_reachability():
    g = DiGraph()
    g.add_edge("main", "a")
    g.add_edge("a", "b")
    g.add_node("orphan")
    assert g.reachable_from(["main"]) == {"main", "a", "b"}


def test_pagerank_ranks_the_hub_highest():
    g = DiGraph()
    for node in "bcde":
        g.add_edge(node, "hub")
    ranks = g.pagerank()
    assert max(ranks, key=ranks.get) == "hub"


# --------------------------------------------------------------------------
# Call resolution — the accuracy claim
# --------------------------------------------------------------------------

RESOLUTION_FILES = {
    "Order.java": """
public class Order {
    public void submit() { }
    public void cancel() { }
}
""",
    "Invoice.java": """
public class Invoice {
    public void submit() { }
}
""",
    "Service.java": """
public class Service {
    private Order order;

    public void run() {
        Order local = new Order();
        local.submit();
        this.order.cancel();
    }
}
""",
}


def test_receiver_type_disambiguates_identical_method_names():
    """`local.submit()` must bind to Order.submit, not Invoice.submit."""
    with Project(RESOLUTION_FILES) as project:
        analysis = project.analyse()
        symbols = analysis.symbols
        run = next(f for f in symbols.functions.values() if f.name == "run")
        submit = next(c for c in run.calls if c.name == "submit")
        targets, confidence = symbols.resolve_call(run, submit)
        assert confidence == "exact"
        assert [t.owner for t in targets] == ["Order"]


def test_field_type_resolves_an_implicit_receiver():
    with Project(RESOLUTION_FILES) as project:
        analysis = project.analyse()
        run = next(f for f in analysis.symbols.functions.values()
                   if f.name == "run")
        cancel = next(c for c in run.calls if c.name == "cancel")
        targets, confidence = analysis.symbols.resolve_call(run, cancel)
        assert confidence == "exact"
        assert targets[0].owner == "Order"


def test_a_name_only_in_a_comment_is_not_a_dependency():
    """The single biggest false-positive source in the previous version."""
    files = {
        "A.java": "public class A { public void ping() { } }",
        "B.java": """
// This class used to call A.ping() but no longer does.
public class B {
    /* see also: new A(); */
    public void noop() { }
}
""",
    }
    with Project(files) as project:
        analysis = project.analyse()
        assert ("B.java", "A.java") not in analysis.file_graph.edges


def test_a_substring_class_name_is_not_a_dependency():
    """`OrderItem` contains `Order`; a naive scan links them."""
    files = {
        "Order.java": "public class Order { public void go() {} }",
        "OrderItem.java": "public class OrderItem { public void go2() {} }",
    }
    with Project(files) as project:
        analysis = project.analyse()
        assert analysis.file_graph.edges == []


def test_resolution_report_separates_library_calls_from_failures():
    with Project(RESOLUTION_FILES) as project:
        report = project.analyse().resolution
        assert report["internal_call_sites"] > 0
        assert report["resolution_rate"] <= 1.0
        assert report["call_sites"] == (
            report["internal_call_sites"] + report["external_calls"])


# --------------------------------------------------------------------------
# Dead code
# --------------------------------------------------------------------------

DEAD_FILES = {
    "App.java": """
public class App {
    public static void main(String[] args) {
        App app = new App();
        app.start();
    }

    public void start() { helper(); }

    private void helper() { }

    private void neverCalled() { int x = 1; }

    private void alsoDead() { neverCalled(); }
}
""",
}


def test_unreachable_cluster_is_found():
    """`alsoDead` calls `neverCalled`, so reference counting misses both."""
    with Project(DEAD_FILES) as project:
        dead = project.analyse().dead_code
        unreachable = {f["simple_name"] for f in dead["unreachable"]}
        assert "neverCalled" in unreachable
        assert "alsoDead" in unreachable
        assert "helper" not in unreachable
        assert "start" not in unreachable


def test_application_mode_is_detected_when_a_main_exists():
    with Project(DEAD_FILES) as project:
        assert project.analyse().dead_code["mode"] == "application"


def test_library_mode_when_there_is_no_entry_point():
    files = {"Util.java": """
public class Util {
    public static int add(int a, int b) { return a + b; }
    private static int unusedHelper() { return 0; }
}
"""}
    with Project(files) as project:
        dead = project.analyse().dead_code
        assert dead["mode"] == "library"
        unreachable = {f["simple_name"] for f in dead["unreachable"]}
        assert "unusedHelper" in unreachable
        assert "add" not in unreachable      # public API is an entry point


def test_reflection_lowers_confidence():
    files = {"R.java": """
public class R {
    public static void main(String[] a) throws Exception {
        Class.forName("R").getMethod("hidden").invoke(null);
    }
    private static void hidden() { }
}
"""}
    with Project(files) as project:
        dead = project.analyse().dead_code
        assert dead["reflection_warnings"]
        for finding in dead["unreachable"]:
            assert finding["confidence"] != "high"


def test_unused_import_ignores_mentions_in_comments():
    files = {"A.java": """
import java.util.List;
import java.util.Map;

// We used to use a Map here.
public class A {
    List<String> items;
}
"""}
    with Project(files) as project:
        unused = {i["symbol"] for i in project.analyse().dead_code["unused_imports"]}
        assert "Map" in unused
        assert "List" not in unused


# --------------------------------------------------------------------------
# Complexity and smells
# --------------------------------------------------------------------------

def test_cognitive_complexity_punishes_nesting():
    flat = """
public class A {
  void f(int x) {
    if (x > 1) { g(); }
    if (x > 2) { g(); }
    if (x > 3) { g(); }
  }
  void g() {}
}
"""
    nested = """
public class B {
  void f(int x) {
    if (x > 1) {
      if (x > 2) {
        if (x > 3) { g(); }
      }
    }
  }
  void g() {}
}
"""
    a = BY_NAME["java"].parse("A.java", "A.java", flat).functions[0]
    b = BY_NAME["java"].parse("B.java", "B.java", nested).functions[0]
    assert a.metrics["cyclomatic"] == b.metrics["cyclomatic"]
    assert b.metrics["cognitive"] > a.metrics["cognitive"]


def test_magic_numbers_exclude_named_constants():
    files = {"A.java": """
public class A {
    private static final int RETRY_LIMIT = 4271;
    void f() { int delay = 9137; }
}
"""}
    with Project(files) as project:
        metrics = project.analyse().complexity["A.java"]
        assert metrics["magic_numbers"] == 1


def test_duplicate_block_is_detected_across_files():
    block = "\n".join(f"        int value{i} = compute({i}) + offset;" for i in range(8))
    files = {
        "A.java": "public class A {\n  void f() {\n" + block + "\n  }\n  int compute(int i){return i;}\n}",
        "B.java": "public class B {\n  void g() {\n" + block + "\n  }\n  int compute(int i){return i;}\n}",
    }
    with Project(files) as project:
        smells = project.analyse().smells
        assert any(s["rule"] == "duplicate-code" for s in smells)


def test_circular_dependency_is_reported():
    files = {
        "A.java": "public class A { B b; void f(){ b.g(); } void h(){} }",
        "B.java": "public class B { A a; void g(){ a.h(); } }",
    }
    with Project(files) as project:
        analysis = project.analyse()
        assert analysis.dependency_view()["cycles"]
        assert any(s["rule"] == "circular-dependency" for s in analysis.smells)


def test_empty_catch_is_flagged():
    files = {"A.java": """
public class A {
    void f() {
        try { g(); } catch (Exception e) {}
    }
    void g() throws Exception {}
}
"""}
    with Project(files) as project:
        rules = {s["rule"] for s in project.analyse().smells}
        assert "empty-catch" in rules


# --------------------------------------------------------------------------
# Business rules
# --------------------------------------------------------------------------

def test_threshold_rule_captures_the_actual_number():
    files = {"P.java": """
public class P {
    double shipping(double subtotal) {
        if (subtotal > 50) { return 0; }
        return 5.99;
    }
}
"""}
    with Project(files) as project:
        rules = project.analyse().business
        thresholds = [r for r in rules["by_file"]["P.java"]
                      if r["kind"] == "threshold"]
        assert thresholds
        assert thresholds[0]["value"] == "50"
        assert "50" in thresholds[0]["explanation"]


def test_generic_conditions_are_not_reported_as_business_rules():
    """`if (i < n)` is a loop bound, not a commercial decision."""
    files = {"P.java": """
public class P {
    void f(int n) {
        for (int i = 0; i < n; i++) { g(i); }
    }
    void g(int i) {}
}
"""}
    with Project(files) as project:
        assert project.analyse().business["total"] == 0


# --------------------------------------------------------------------------
# Classifier
# --------------------------------------------------------------------------

def test_classifier_reports_a_believable_accuracy():
    card = FunctionClassifier().model_card()
    accuracy = card["cross_validated_accuracy"]
    # Chance on 10 classes is 0.1. Anything at or near 1.0 means the folds
    # are leaking, which is the bug this test exists to catch.
    assert 0.25 < accuracy < 0.95
    assert card["training_examples"] > 100


def test_classifier_declines_to_guess_on_a_meaningless_name():
    from codecartographer.models import FunctionDef
    fn = FunctionDef(name="xq7", file="a.java", line=1, end_line=2,
                     language="java")
    verdict = FunctionClassifier().classify(fn, "")
    assert verdict["category"] == "unclassified"


def test_structural_evidence_beats_the_name():
    from codecartographer.models import FunctionDef
    fn = FunctionDef(name="getStuff", file="a.java", line=1, end_line=40,
                     language="java", metrics={"loc": 40, "cyclomatic": 6})
    body = ("PreparedStatement ps = connection.prepareStatement("
            "\"SELECT * FROM orders\"); ResultSet rs = ps.executeQuery();")
    verdict = FunctionClassifier().classify(fn, body)
    assert verdict["category"] == "data_access"


# --------------------------------------------------------------------------
# Engine plumbing
# --------------------------------------------------------------------------

def test_excluded_directories_are_skipped():
    files = {
        "src/A.java": "public class A {}",
        "node_modules/lib/B.java": "public class B {}",
        "target/C.java": "public class C {}",
    }
    with Project(files) as project:
        found = discover(AnalysisConfig(root=project.dir))
        assert any(f.endswith("A.java") for f in found)
        assert not any("node_modules" in f for f in found)
        assert not any("target" in f for f in found)


def test_include_glob_narrows_the_scan():
    files = {"a.py": "def f(): pass", "b.java": "class B {}"}
    with Project(files) as project:
        analysis = project.analyse(include_globs=["*.py"])
        assert [f.rel_path for f in analysis.files] == ["a.py"]


def test_language_filter():
    files = {"a.py": "def f(): pass", "b.java": "public class B {}"}
    with Project(files) as project:
        analysis = project.analyse(languages=["java"])
        assert [f.language for f in analysis.files] == ["java"]


def test_mixed_language_project_analyses_end_to_end():
    files = {}
    for name in os.listdir(FIXTURES):
        with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
            files[name] = handle.read()
    with Project(files) as project:
        analysis = project.analyse()
        languages = {f.language for f in analysis.files}
        assert {"cobol", "fortran", "vb", "pascal", "sql", "python"} <= languages
        assert analysis.overview()["functions"] > 15
        assert analysis.risk
        assert analysis.to_dict()["schema_version"] == 2


def test_binary_and_oversized_files_are_skipped_not_crashed():
    with Project({"a.java": "public class A {}"}) as project:
        with open(os.path.join(project.dir, "blob.java"), "wb") as handle:
            handle.write(b"\x00\x01\x02binary")
        analysis = project.analyse()
        assert [f.rel_path for f in analysis.files] == ["a.java"]
        assert any(s["file"] == "blob.java" for s in analysis.skipped)


def test_empty_directory_does_not_explode():
    with Project({"README.txt": "nothing here"}) as project:
        analysis = project.analyse()
        assert analysis.files == []
        assert analysis.overview()["files"] == 0


def test_polymorphic_call_reaches_the_override():
    """A call to an abstract method runs the subclass implementation.

    Resolving only up the inheritance chain finds the abstract stub, marks
    that reachable, and reports every real implementation as dead.
    """
    files = {
        "Shape.java": """
public abstract class Shape {
    public abstract double area();
}
""",
        "Circle.java": """
public class Circle extends Shape {
    private double r;
    public double area() { return 3.14159 * r * r; }
}
""",
        "Square.java": """
public class Square extends Shape {
    private double side;
    public double area() { return side * side; }
}
""",
        "Main.java": """
public class Main {
    public static void main(String[] args) {
        Shape s = new Circle();
        System.out.println(s.area());
    }
}
""",
    }
    with Project(files) as project:
        analysis = project.analyse()
        unreachable = {f["name"] for f in analysis.dead_code["unreachable"]}
        assert "Circle.area" not in unreachable
        assert "Square.area" not in unreachable


def test_override_resolution_does_not_link_unrelated_classes():
    """CHA follows inheritance, not name collisions."""
    files = {
        "A.java": "public class A {\n  public void run() {}\n}",
        "B.java": "public class B {\n  public void run() {}\n}",
        "C.java": """
public class C {
    public void go() {
        A a = new A();
        a.run();
    }
}
""",
    }
    with Project(files) as project:
        analysis = project.analyse()
        go = next(f for f in analysis.symbols.functions.values()
                  if f.name == "go")
        call = next(c for c in go.calls if c.name == "run")
        targets, confidence = analysis.symbols.resolve_call(go, call)
        assert confidence == "exact"
        assert {t.owner for t in targets} == {"A"}
