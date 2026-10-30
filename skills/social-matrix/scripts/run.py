#!/usr/bin/env python3
"""Hermes skill entry point: run the workflow from the skill directory.

Hermes loads SKILL.md and then runs this script via its terminal tool. The script
only forwards to the repository's workflow.py, so there is exactly one
implementation — the standalone runner and the Hermes skill cannot drift apart.

    python3 scripts/run.py demo
    python3 scripts/run.py draft --days 2
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))   # repo root (two levels up)
WORKFLOW = os.path.join(ROOT, "workflow.py")


def main(argv):
    if not os.path.exists(WORKFLOW):
        print("找不到 workflow.py（期望在 %s）。这个 skill 需要整个仓库，"
              "不能只拷贝 skills/social-matrix 目录。" % WORKFLOW, file=sys.stderr)
        return 2
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    cmd = [sys.executable, WORKFLOW] + list(argv or ["demo"])
    # 前两个参数是 --root，让 skill 在仓库根目录下读写 out/
    if "--root" not in cmd:
        cmd += ["--root", ROOT]
    return subprocess.call(cmd, cwd=ROOT, env=env)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
