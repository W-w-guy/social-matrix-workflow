#!/usr/bin/env python3
"""社媒矩阵工作流样例 —— 命令行入口。

一条命令一段，退出码就是判据（可直接接 cron / CI，不用解析文案）：

    0  成功
    1  有未通过项（草案被拒、有失败但流程跑完了）
    2  用法或文件错误（路径不存在、参数写错、素材缺列）
    3  审核门拒绝（未审批 / 审批后正文被改）
    4  发布失败（已进死信队列）
    5  台账核验不一致

用法：
    python3 workflow.py register  --assets assets --out out/assets.json
    python3 workflow.py draft     --assets out/assets.json --accounts fb-main --days 2 --out out/drafts.json
    python3 workflow.py review    --root .                     # 列出待审
    python3 workflow.py approve   --draft d_xxx --reviewer 张三
    python3 workflow.py reject    --draft d_xxx --reviewer 张三 --reason "文案需改"
    python3 workflow.py publish   --endpoint http://127.0.0.1:PORT/publish
    python3 workflow.py report
    python3 workflow.py recover
    python3 workflow.py demo                                   # 一键跑完整链路（含失败重放）
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from lib import assets as A, draft as DR, feedback as FB, mock as MK, publish as PB
from lib import review as RV
from lib.store import read_json, read_jsonl


def _say(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True))


def _root(args):
    return os.path.abspath(getattr(args, "root", None) or HERE)


def cmd_register(args):
    res = A.register(args.assets, args.out)
    _say({"ok": True, "count": len(res),
          "assets": [{"asset_id": a["asset_id"], "file": a["file"],
                      "kind": a["kind"], "bytes": a["bytes"],
                      "encoding": a["encoding"], "text_chars": a["text_chars"]}
                     for a in res]})
    return 0


def cmd_draft(args):
    drafts, rejected = DR.generate(args.assets,
                                   accounts=[a.strip() for a in args.accounts.split(",") if a.strip()],
                                   days=args.days, per_day=args.per_day,
                                   out_path=args.out)
    _say({"ok": True, "drafts": len(drafts), "rejected": rejected})
    return 0 if drafts else 1


def cmd_review(args):
    rep = RV.review_report(_root(args))
    _say(rep)
    return 0 if not rep["pending"] else 1


def cmd_approve(args):
    try:
        rec = RV.approve(_root(args), args.draft, args.reviewer, args.note or "")
    except RV.ReviewError as exc:
        _say({"ok": False, "error": str(exc)})
        return 3
    _say({"ok": True, **rec})
    return 0


def cmd_reject(args):
    try:
        rec = RV.reject(_root(args), args.draft, args.reviewer, args.reason)
    except RV.ReviewError as exc:
        _say({"ok": False, "error": str(exc)})
        return 3
    _say({"ok": True, **rec})
    return 0


def cmd_publish(args):
    root = _root(args)
    summary = PB.publish_all(root, args.endpoint,
                             max_attempts=args.max_attempts,
                             timeout=args.timeout, backoff=args.backoff)
    # 把结果回填进反馈台账（模拟互动值，文档里已标注为模拟）
    drafts = (read_json(os.path.join(root, "out", "drafts.json"), {}) or {}).get("drafts", [])
    by_id = {d["draft_id"]: d for d in drafts}
    for r in summary["results"]:
        d = by_id.get(r.get("draft_id"))
        if d:
            FB.record_result(root, d, r, FB.simulated_metrics_for(r.get("post_id")))
    _say(summary)
    if summary["failed"]:
        return 4
    if summary["blocked_by_review"]:
        return 3
    return 0


def cmd_report(args):
    _say(FB.build_report(_root(args)))
    return 0


def cmd_recover(args):
    res = PB.recover_orphans(_root(args))
    _say(res)
    return 0 if not res["orphans"] else 1


def cmd_demo(args):
    """一键演示：登记 -> 生成 -> 审批 -> 发布（含失败重放）-> 反馈。

    用临时目录，不污染 out/；结束后打印每一步的退出码。
    """
    import tempfile
    tmp = tempfile.mkdtemp(prefix="social_demo_")
    codes = []
    os.makedirs(os.path.join(tmp, "out"), exist_ok=True)
    A.register(os.path.join(HERE, "assets"), os.path.join(tmp, "out", "assets.json"))
    DR.generate(os.path.join(tmp, "out", "assets.json"), ["fb-main"], 2,
                os.path.join(tmp, "out", "drafts.json"), per_day=3)
    drafts = read_json(os.path.join(tmp, "out", "drafts.json"), {})["drafts"]
    for i, d in enumerate(drafts):
        # 第 3 条故意不审批 —— 演示审核门拒绝
        if i == 2:
            continue
        RV.approve(tmp, d["draft_id"], "demo-reviewer")

    srv, th, state, base = MK.start("transient")
    try:
        summary = PB.publish_all(tmp, base + "/publish", max_attempts=3, timeout=5.0)
        for r in summary["results"]:
            dd = next(d for d in drafts if d["draft_id"] == r["draft_id"])
            FB.record_result(tmp, dd, r, FB.simulated_metrics_for(r.get("post_id")))
    finally:
        srv.shutdown()

    # 再演示一次永久失败 + 限流
    for scen in ("permanent", "ratelimit"):
        srv, th, state, base = MK.start(scen)
        try:
            target = drafts[0]
            PB.publish_one(tmp, target, base + "/publish", max_attempts=3, timeout=5.0)
        finally:
            srv.shutdown()

    rep = FB.build_report(tmp)
    orphan = PB.recover_orphans(tmp)
    _say({"ok": True, "root": tmp,
          "drafts": len(drafts),
          "published": summary["published"],
          "blocked_by_review": summary["blocked_by_review"],
          "failed": summary["failed"],
          "dead_letter": len(read_jsonl(os.path.join(tmp, "out", "dead_letter.jsonl"))),
          "orphans": len(orphan["orphans"]),
          "feedback": rep["accounts"],
          "suggestions": rep["suggestions"]})
    return 0


def _guard(fn, args):
    try:
        return fn(args)
    except FileNotFoundError as exc:
        print("找不到文件或目录：%s" % exc, file=sys.stderr)
        print("（--assets / --out 请给真实存在的路径）", file=sys.stderr)
        return 2
    except ValueError as exc:
        print("输入不合规：%s" % exc, file=sys.stderr)
        return 2
    except RV.ReviewError as exc:
        print("审核门拒绝：%s" % exc, file=sys.stderr)
        return 3
    except OSError as exc:
        print("读写失败：%s" % exc, file=sys.stderr)
        return 2


def main(argv=None):
    ap = argparse.ArgumentParser(description="社媒矩阵工作流样例（模拟发布端，不接触真实账号）")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("register"); p.add_argument("--assets", required=True)
    p.add_argument("--out", required=True); p.set_defaults(fn=cmd_register)

    p = sub.add_parser("draft"); p.add_argument("--assets", required=True)
    p.add_argument("--accounts", default="fb-main"); p.add_argument("--days", type=int, default=2)
    p.add_argument("--per-day", type=int, default=3); p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_draft)

    p = sub.add_parser("review"); p.add_argument("--root", default=HERE)
    p.set_defaults(fn=cmd_review)

    for name, fn in (("approve", cmd_approve), ("reject", cmd_reject)):
        p = sub.add_parser(name); p.add_argument("--draft", required=True)
        p.add_argument("--reviewer", required=True)
        p.add_argument("--root", default=HERE)
        if name == "approve":
            p.add_argument("--note", default="")
        else:
            p.add_argument("--reason", required=True)
        p.set_defaults(fn=fn)

    p = sub.add_parser("publish"); p.add_argument("--endpoint", required=True)
    p.add_argument("--root", default=HERE); p.add_argument("--max-attempts", type=int, default=3)
    p.add_argument("--timeout", type=float, default=5.0); p.add_argument("--backoff", type=float, default=0.0)
    p.set_defaults(fn=cmd_publish)

    p = sub.add_parser("report"); p.add_argument("--root", default=HERE); p.set_defaults(fn=cmd_report)
    p = sub.add_parser("recover"); p.add_argument("--root", default=HERE); p.set_defaults(fn=cmd_recover)
    p = sub.add_parser("demo"); p.set_defaults(fn=cmd_demo)

    args = ap.parse_args(argv)
    if not getattr(args, "fn", None):
        ap.print_help()
        return 2
    return _guard(args.fn, args)


if __name__ == "__main__":
    raise SystemExit(main())
