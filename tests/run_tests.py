#!/usr/bin/env python3
"""Run the test suite with or without pytest installed.

The tests are written as ordinary pytest tests, because that is what anyone
working on this will expect. But the machines this tool is aimed at often
cannot reach a package index, and a test suite you cannot run is not a test
suite. So when pytest is missing this module supplies just enough of its
surface — ``mark.parametrize``, ``raises``, ``fixture``, ``approx`` — to
execute the same files unchanged.

    python tests/run_tests.py          # auto: pytest if present
    python tests/run_tests.py -v       # show every test name
    python tests/run_tests.py -k cobol # only tests whose name matches
"""

from __future__ import annotations

import argparse
import importlib.util
import inspect
import os
import sys
import traceback
import types
from typing import Any, Callable, Dict, List, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------------
# Minimal pytest stand-in
# --------------------------------------------------------------------------

class _Skipped(Exception):
    pass


class _Raises:
    def __init__(self, expected):
        self.expected = expected
        self.value = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            raise AssertionError(
                f"DID NOT RAISE {getattr(self.expected, '__name__', self.expected)}")
        if issubclass(exc_type, self.expected):
            self.value = exc
            return True
        return False


class _Approx:
    def __init__(self, value, rel=1e-6, abs=1e-9):
        self.value, self.rel, self.abs = value, rel, abs

    def __eq__(self, other):
        return abs(other - self.value) <= max(
            self.abs, self.rel * max(abs(self.value), abs(other)))

    def __repr__(self):
        return f"approx({self.value})"


def _make_shim() -> types.ModuleType:
    module = types.ModuleType("pytest")

    def parametrize(argnames: str, argvalues):
        names = [n.strip() for n in argnames.split(",")]

        def decorator(fn):
            cases = []
            for values in argvalues:
                if not isinstance(values, (tuple, list)):
                    values = (values,)
                cases.append(dict(zip(names, values)))
            fn._parametrized = cases
            return fn
        return decorator

    def skip(reason: str = ""):
        raise _Skipped(reason)

    def fixture(*args, **kwargs):
        def decorator(fn):
            fn._is_fixture = True
            return fn
        if args and callable(args[0]):
            return decorator(args[0])
        return decorator

    mark = types.SimpleNamespace(
        parametrize=parametrize,
        skip=lambda *a, **k: (lambda f: f),
        skipif=lambda *a, **k: (lambda f: f),
        slow=lambda f: f,
    )

    module.mark = mark
    module.raises = _Raises
    module.approx = _Approx
    module.fixture = fixture
    module.skip = skip
    module.Skipped = _Skipped
    return module


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------

def _load(path: str) -> types.ModuleType:
    name = "cc_test_" + os.path.basename(path)[:-3]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _collect(module: types.ModuleType) -> List[Tuple[str, Callable, dict]]:
    found: List[Tuple[str, Callable, dict]] = []

    def add(label: str, fn: Callable) -> None:
        cases = getattr(fn, "_parametrized", None)
        if cases:
            for case in cases:
                suffix = "-".join(str(v) for v in case.values())
                found.append((f"{label}[{suffix}]", fn, case))
        else:
            found.append((label, fn, {}))

    for name, obj in vars(module).items():
        if name.startswith("test_") and inspect.isfunction(obj):
            add(f"{module.__name__}::{name}", obj)
        elif name.startswith("Test") and inspect.isclass(obj):
            instance = obj()
            for method_name, method in inspect.getmembers(
                    instance, inspect.ismethod):
                if method_name.startswith("test_"):
                    add(f"{module.__name__}::{name}::{method_name}", method)
    return found


def run(pattern: str = "", verbose: bool = False) -> int:
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    if "pytest" not in sys.modules:
        try:
            import pytest  # noqa: F401
        except ImportError:
            sys.modules["pytest"] = _make_shim()

    files = sorted(
        os.path.join(HERE, f) for f in os.listdir(HERE)
        if f.startswith("test_") and f.endswith(".py"))

    passed = failed = skipped = 0
    failures: List[Tuple[str, str]] = []

    for path in files:
        try:
            module = _load(path)
        except Exception:
            failures.append((os.path.basename(path), traceback.format_exc()))
            failed += 1
            continue

        for label, fn, kwargs in _collect(module):
            if pattern and pattern.lower() not in label.lower():
                continue
            try:
                fn(**kwargs)
            except _Skipped:
                skipped += 1
                if verbose:
                    print(f"  skip  {label}")
                continue
            except Exception:
                failed += 1
                failures.append((label, traceback.format_exc()))
                print(f"  FAIL  {label}")
                continue
            passed += 1
            if verbose:
                print(f"  ok    {label}")
            else:
                sys.stdout.write(".")
                sys.stdout.flush()

    if not verbose:
        print()
    for label, tb in failures:
        print("\n" + "=" * 70)
        print(label)
        print("-" * 70)
        print(tb.rstrip())

    print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-k", dest="pattern", default="",
                        help="Only run tests whose name contains this.")
    parser.add_argument("-v", dest="verbose", action="store_true")
    args = parser.parse_args()
    sys.exit(run(args.pattern, args.verbose))
