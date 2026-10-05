"""Run the test suite with one process-pool task per test class.

Usage: python3 tests/run_parallel.py
"""
import io
import os
import sys
import unittest
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))


def flatten(suite):
    for t in suite:
        if isinstance(t, unittest.TestSuite):
            yield from flatten(t)
        else:
            yield t


def run_class(name):
    """Worker: run one test class by dotted name; return plain data only."""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromName(name)
    r = unittest.TextTestRunner(stream=stream, verbosity=1).run(suite)
    return {"name": name, "run": r.testsRun,
            "failures": [(str(t), tb) for t, tb in r.failures],
            "errors": [(str(t), tb) for t, tb in r.errors],
            "unexpectedSuccesses": [str(t) for t in r.unexpectedSuccesses],
            "output": stream.getvalue()}


def main():
    sys.path.insert(0, HERE)
    tests = list(flatten(unittest.defaultTestLoader.discover(HERE)))
    broken = [t.id() for t in tests if isinstance(t, unittest.loader._FailedTest)]
    if broken:
        print("import errors: " + ", ".join(broken), file=sys.stderr)
        return 1
    counts = {}
    for t in tests:
        k = f"{type(t).__module__}.{type(t).__qualname__}"
        counts[k] = counts.get(k, 0) + 1
    order = sorted(counts, key=lambda k: -counts[k])
    workers = int(os.environ.get("CGP_TEST_WORKERS") or 2 * (os.cpu_count() or 1))
    bad = False
    total = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for res in pool.map(run_class, order):
            total += res["run"]
            if res["failures"] or res["errors"] or res["unexpectedSuccesses"]:
                bad = True
                print(res["output"])
    print(f"Ran {total} tests in {len(order)} classes ({workers} workers)")
    if total == 0 or total != len(tests):
        print(f"expected {len(tests)} tests, ran {total}", file=sys.stderr)
        bad = True
    print("FAILED" if bad else "OK")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
