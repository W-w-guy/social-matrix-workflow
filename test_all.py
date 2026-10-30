#!/usr/bin/env python3
"""一条命令跑全部测试：python3 test_all.py"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            for x in flatten(item):
                yield x
        else:
            yield item


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.discover(os.path.join(HERE, "tests"), pattern="test_*.py",
                            top_level_dir=HERE)
    cases = list(flatten(suite))
    print("test classes: %s" % ", ".join(sorted({c.__class__.__name__ for c in cases})))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
