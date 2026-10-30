"""数据反馈：把每次发布的结果写成可查询的台账，并产出次日排期建议。

客户验收要求里有「数据反馈说明」。这里做到两件事：
 1) 每篇一张记录：账号、槽位、是否发出、是否重试、是否重复、耗时
 2) 基于这些记录给出次日建议：哪些账号连续失败该暂停、哪些槽位该错峰

★ 诚实边界（写进 README 与 VERIFICATION）：
  mock 端返回的互动数据（impressions/likes）是**模拟值**，只用于演示
  「反馈如何回流到排期决策」，不代表真实触达。文档里会明确标注哪些是模拟的。
  绝不把模拟触达当成真实数据汇报给客户。
"""
import json
import os

from .store import append_jsonl, read_json, read_jsonl, write_json


def record_result(root, draft, result, simulated_metrics=None):
    """把一次发布结果写进反馈台账。"""
    rec = {
        "draft_id": draft["draft_id"],
        "account": draft["account"],
        "platform": draft["platform"],
        "slot": draft["slot"],
        "sku": draft["sku"],
        "ok": bool(result.get("ok")),
        "attempts": result.get("attempts", 0),
        "duplicate": bool(result.get("duplicate")),
        "post_id": result.get("post_id"),
        "reason": result.get("reason"),
        "simulated": simulated_metrics or {},
    }
    append_jsonl(os.path.join(root, "out", "feedback.jsonl"), rec)
    return rec


def simulated_metrics_for(post_id):
    """由 post_id 派生的确定值 —— 演示用，不接任何真实平台。

    用派生而不是随机：同一篇两次查询结果一致，录像/截图可复现。
    """
    if not post_id:
        return {}
    h = sum(ord(c) for c in post_id)
    return {"impressions": 200 + h % 800,
            "likes": 5 + h % 40,
            "clicks": 2 + h % 20,
            "simulated": True}


def build_report(root):
    """汇总反馈，并给出次日建议。"""
    rows = read_jsonl(os.path.join(root, "out", "feedback.jsonl"))
    by_account = {}
    for r in rows:
        a = by_account.setdefault(r["account"], {"total": 0, "ok": 0, "failed": 0,
                                                 "duplicate": 0, "retried": 0})
        a["total"] += 1
        a["ok" if r["ok"] else "failed"] += 1
        if r["duplicate"]:
            a["duplicate"] += 1
        if r.get("attempts", 0) > 1:
            a["retried"] += 1

    suggestions = []
    for acc, stat in sorted(by_account.items()):
        # ★ 2026-10-30 补：demo 里有一条是"永久失败"（故意留的演示），
        #   失败率 1/6 = 17% 达不到 50% 的门槛，于是 suggestions 为空 ——
        #   报告看起来像"一切正常"，但明明有 1 条躺在死信队列里。
        #   死信必须独立成一条建议，不能只在失败率阈值里体现。
        if stat["failed"]:
            suggestions.append(
                "账号 %s：有 %d 条发布失败，已进死信队列（out/dead_letter.jsonl）。"
                "失败率 %.0f%%；这一条不会被自动重发，需要人处理后重跑"
                % (acc, stat["failed"], 100.0 * stat["failed"] / max(stat["total"], 1)))
        if stat["total"] and stat["failed"] / stat["total"] >= 0.5:
            suggestions.append(
                "账号 %s：失败率 %.0f%%，建议先暂停该账号排期，排查授权或内容规则后再恢复"
                % (acc, 100.0 * stat["failed"] / stat["total"]))
        if stat["duplicate"]:
            suggestions.append(
                "账号 %s：有 %d 条命中幂等（重试未重复发），说明重试链路正常；"
                "若同一槽位反复出现，检查排期生成是否重复" % (acc, stat["duplicate"]))
        if stat["retried"] and not stat["failed"]:
            suggestions.append(
                "账号 %s：%d 条经历重试后成功，属可接受；若比例持续升高，"
                "说明对方接口不稳，建议把 backoff 调大而非加次数" % (acc, stat["retried"]))

    report = {"total": len(rows), "accounts": by_account,
              "suggestions": suggestions,
              "note": "impressions/likes/clicks 为模拟值，仅用于演示反馈回流路径，"
                      "不代表真实触达。"}
    write_json(os.path.join(root, "out", "feedback_report.json"), report)
    return report
