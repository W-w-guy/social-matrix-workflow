"""人工审核：硬门，不是提示。

客户原话「保留人工监管和调整」。这句话在多数交付里会做成一句提示语，
这里做成**发布前的强制条件**：

  * 没有审批记录的草案，publish 直接拒绝（不是警告、不是跳过）
  * 审批记录里存的是【被批准那一刻的正文哈希】
  * 批准之后改一个字节（哪怕是空格），审批立即失效，必须重新批
  * 同一草案重复审批：允许，但会覆盖并留下历史（保留谁在什么时候改的）
  * 审批人不能为空字符串（"" 和 "   " 都不算审过）

★ 为什么存哈希而不是存正文副本：
  存副本会带来「以哪份为准」的二义性——审批表里的正文和草案文件里的正文
  哪个是真？存哈希只有一个真值：正文在哪都能重算出来比对。
"""
import hashlib
import os
import time

from .store import append_jsonl, read_json, read_jsonl, write_json


class ReviewError(Exception):
    """审核相关的业务拒绝（不是程序缺陷）。"""


def text_sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _review_log_path(root):
    return os.path.join(root, "out", "reviews.jsonl")


def _approvals_path(root):
    return os.path.join(root, "out", "approvals.json")


def list_pending(root):
    """返回还没被批准（或批准已失效）的草案。"""
    drafts = read_json(os.path.join(root, "out", "drafts.json"), {}) or {}
    approvals = read_json(_approvals_path(root), {}) or {}
    pending = []
    for d in drafts.get("drafts", []):
        ap = approvals.get(d["draft_id"])
        if not ap:
            pending.append({"draft_id": d["draft_id"], "why": "未审批"})
            continue
        if ap.get("text_sha256") != text_sha256(d["text"]):
            pending.append({"draft_id": d["draft_id"],
                            "why": "审批后正文被改动，审批已失效"})
        elif ap.get("draft_version") != d.get("version", 1):
            pending.append({"draft_id": d["draft_id"],
                            "why": "草案版本变化，审批已失效"})
    return pending


def approve(root, draft_id, reviewer, note=""):
    """批准一条草案。返回审批记录。"""
    drafts = read_json(os.path.join(root, "out", "drafts.json"), {}) or {}
    target = None
    for d in drafts.get("drafts", []):
        if d["draft_id"] == draft_id:
            target = d
            break
    if target is None:
        raise ReviewError("没有这条草案：%s" % draft_id)

    name = str(reviewer or "").strip()
    if not name:
        # ★ 空审批人是常见的"看起来审过了"的假通过：表单提交空名字、
        #   API 传了空串、脚本默认参数 "" —— 全部拦在这里。
        raise ReviewError("审批人不能为空（空字符串和纯空格都不算审批）")
    if len(name) > 64:
        raise ReviewError("审批人名字过长（%d > 64）" % len(name))

    approvals = read_json(_approvals_path(root), {}) or {}
    prev = approvals.get(draft_id)
    rec = {
        "draft_id": draft_id,
        "reviewer": name,
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "text_sha256": text_sha256(target["text"]),
        "draft_version": target.get("version", 1),
        "note": str(note or "")[:200],
        "supersedes": ({"reviewer": prev["reviewer"], "at": prev["at"]} if prev else None),
    }
    approvals[draft_id] = rec
    write_json(_approvals_path(root), approvals)
    append_jsonl(_review_log_path(root), {"action": "approve", **rec})
    return rec


def reject(root, draft_id, reviewer, reason):
    """驳回一条草案：从 approvals 里移除，并记录原因。"""
    approvals = read_json(_approvals_path(root), {}) or {}
    name = str(reviewer or "").strip()
    if not name:
        raise ReviewError("驳回人也必须署名")
    reason = str(reason or "").strip()
    if not reason:
        # 不写理由的驳回，等于把人推回原样重来
        raise ReviewError("驳回必须写理由")
    approvals.pop(draft_id, None)
    write_json(_approvals_path(root), approvals)
    rec = {"draft_id": draft_id, "reviewer": name,
           "at": time.strftime("%Y-%m-%d %H:%M:%S"), "reason": reason[:200]}
    append_jsonl(_review_log_path(root), {"action": "reject", **rec})
    return rec


def require_approved(root, draft):
    """发布前调用。不满足条件就抛 ReviewError —— 永不返回"差不多能发"。"""
    approvals = read_json(_approvals_path(root), {}) or {}
    ap = approvals.get(draft["draft_id"])
    if not ap:
        raise ReviewError("草案 %s 未审批，拒绝发布" % draft["draft_id"])
    if ap.get("text_sha256") != text_sha256(draft["text"]):
        raise ReviewError(
            "草案 %s 的正文在审批后被改动（审批时 %s，现在 %s），审批失效，需重新审批"
            % (draft["draft_id"], ap.get("text_sha256", "")[:12],
               text_sha256(draft["text"])[:12]))
    if ap.get("draft_version") != draft.get("version", 1):
        raise ReviewError("草案 %s 版本从 %s 变为 %s，审批失效"
                          % (draft["draft_id"], ap.get("draft_version"),
                             draft.get("version", 1)))
    return ap


def review_report(root):
    """给审核人看的清单：待审、已批、被驳回。"""
    drafts = read_json(os.path.join(root, "out", "drafts.json"), {}) or {}
    approvals = read_json(_approvals_path(root), {}) or {}
    by_id = {d["draft_id"]: d for d in drafts.get("drafts", [])}
    rows = []
    for d in drafts.get("drafts", []):
        ap = approvals.get(d["draft_id"])
        state = "未审批"
        if ap:
            state = ("已批准" if ap.get("text_sha256") == text_sha256(d["text"])
                     else "审批已失效（正文被改）")
        rows.append({"draft_id": d["draft_id"], "account": d["account"],
                     "slot": d["slot"], "sku": d["sku"], "chars": len(d["text"]),
                     "state": state,
                     "reviewer": (ap or {}).get("reviewer")})
    return {"total": len(rows), "rows": rows,
            "pending": list_pending(root)}
