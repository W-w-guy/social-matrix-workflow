#!/usr/bin/env python3
"""核对 MANIFEST.json：逐文件重算 SHA256。

    python3 tools/verify_manifest.py

任何一处不符即非 0 退出，并列出是哪一类差异：
changed（改过）、missing（少了）、extra（多了）。
"""
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {"__pycache__", ".git", "out"}


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    mpath = os.path.join(ROOT, "MANIFEST.json")
    if not os.path.exists(mpath):
        print("没有 MANIFEST.json，先跑 tools/make_manifest.py", file=sys.stderr)
        return 2
    doc = json.load(open(mpath, encoding="utf-8"))
    listed = {e["path"]: e for e in doc["manifest"]}

    changed, missing = [], []
    for rel, e in listed.items():
        p = os.path.join(ROOT, rel)
        if not os.path.isfile(p):
            missing.append(rel)
        elif os.path.getsize(p) != e["bytes"] or sha256_of(p) != e["sha256"]:
            changed.append(rel)

    actual = set()
    for dp, dns, fns in os.walk(ROOT):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            if fn == "MANIFEST.json" or fn.endswith((".pyc", ".zip")):
                continue
            actual.add(os.path.relpath(os.path.join(dp, fn), ROOT).replace(os.sep, "/"))
    extra = sorted(actual - set(listed))

    ok = not (changed or missing or extra)
    print(json.dumps({"ok": ok, "checked": len(listed),
                      "changed": changed, "missing": missing, "extra": extra},
                     ensure_ascii=False, indent=1))
    return 0 if ok else 5


if __name__ == "__main__":
    sys.exit(main())
