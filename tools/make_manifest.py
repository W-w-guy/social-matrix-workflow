#!/usr/bin/env python3
"""生成 MANIFEST.json：逐文件字节数与 SHA256。

    python3 tools/make_manifest.py

收件方解压后可逐文件核对：我测过的版本就是你手上的版本。
MANIFEST.json 自身不列入（否则自己算自己的哈希，无解）。
"""
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {"__pycache__", ".git", "out"}
SKIP_FILES = {"MANIFEST.json"}


def collect(root):
    out = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            if fn in SKIP_FILES or fn.endswith((".pyc", ".zip")):
                continue
            p = os.path.join(dp, fn)
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            data = open(p, "rb").read()
            out.append({"path": rel, "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest()})
    return sorted(out, key=lambda x: x["path"])


def main():
    files = collect(ROOT)
    # 刻意不写测试条数：它是会变的值，写死必然过期，
    # 而"文档说 62、实际 63"正是这套测试专门在抓的缺陷。
    doc = {"package": "social-matrix-workflow",
           "purpose": "海外社媒矩阵工作流样例（Facebook 优先，模拟发布端）",
           "files": len(files),
           "total_bytes": sum(f["bytes"] for f in files),
           "python": "3.9+（仅标准库）",
           "verified_on": ["Windows 11 / Python 3.11.7",
                           "Ubuntu 24.04 / Python 3.12.3"],
           "no_network": True,
           "no_credentials": True,
           "manifest": files}
    dst = os.path.join(ROOT, "MANIFEST.json")
    with open(dst, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")
    print("%d files, %d bytes -> %s" % (len(files), doc["total_bytes"], dst))
    return 0


if __name__ == "__main__":
    sys.exit(main())
