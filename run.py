#!/usr/bin/env python3
"""隔离模式（python3 -I）下的统一入口。

用法：
    python3 -I run.py plan --lot J17
    python3 -I run.py test
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _run_tests() -> int:
    import unittest

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for name in ("test_corpus", "test_sampling"):
        sys.path.insert(0, str(ROOT / "tests"))
        suite.addTests(loader.loadTestsFromName(name))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        return _run_tests()
    from sampling.cli import main as cli_main

    cli_main(sys.argv[1:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
