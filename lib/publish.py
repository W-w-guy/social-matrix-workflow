"""发布：幂等 + 重试 + 死信队列 + 发布前后各写一条台账。

这个模块是整份交付里最值得看的一块，因为它处理的是「真实世界里会丢帖子」的地方。

四条硬规则：
 1. **发布前先落一条 attempt 台账**。进程在 publish 中途被杀，盘上会留下
    "attempted but no result" —— 这是证据，不是空白。没有这条，
    事后无法判断"到底发出去了没有"，只能靠猜。
 2. **幂等键 = 账号 + 草案版本 + 排期槽位**（哈希）。重试带同一个键，
    平台侧如果已经成功，会返回 duplicate 而不是再发一条。
 3. **错误分两类**：可重试（5xx/429/超时）与不可重试（4xx 参数错）。
    不可重试的直接进死信队列，不浪费时间重试。
 4. **重试有上限且指数退避**。次数用完仍失败，进死信队列并写明最后一次错误。

不做的事：不猜"可能发出去了"于是跳过。发不出去就是发不出去，进 DLQ 等人处理。
"""
import hashlib
import json
import os
import time
import urllib.error
import urllib.request

from .review import ReviewError, require_approved
from .store import append_jsonl, read_json, read_jsonl, write_json


class PublishError(Exception):
    pass


RETRYABLE_HTTP = {429, 500, 502, 503, 504}


def idempotency_key(draft):
    """账号 + 草案版本 + 排期槽位。同一草案改版后算新的一条，这是有意的。"""
    raw = "%s|%s|%s|%s" % (draft["account"], draft.get("draft_version", draft.get("version", 1)),
                           draft["slot"], draft["draft_id"])
    return "idem_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _post(url, payload, timeout):
    """发一次。返回 (http_code, body_dict_or_text)。超时返回 (0, 'timeout')。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    # ★ 2026-10-30 修：endpoint 写成 "not-a-url" 时 urllib.request.Request
    #   会抛 ValueError（unknown url type）。原来它发生在 try 之外 → 一路冒到 CLI，
    #   而此时 attempt_start 已落账却没有 attempt_result，留下一个假孤儿。
    #   畸形地址属于「配置错」，判为不可重试错误、进死信，并写明原因。
    try:
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "social-sample/1.0"})
    except Exception as exc:
        # -1 = 配置错（畸形 URL），与 0（超时/网络）区分开：
        # 后者重试有意义，前者重试一百次也还是同一个错。
        return -1, "bad endpoint %r: %s: %s" % (url, type(exc).__name__, exc)
    try:
        with opener.open(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(body)
            except Exception:
                return r.status, body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(body)
        except Exception:
            return exc.code, body
    except Exception as exc:
        return 0, "%s: %s" % (type(exc).__name__, exc)


def publish_one(root, draft, endpoint, max_attempts=3, timeout=5.0,
                backoff=0.0, sleep=time.sleep):
    """发布一条草案。返回结果 dict。失败进 DLQ 并返回 ok=False。"""
    # 第一道门：没有有效审批，直接拒绝（连尝试都不尝试）
    approval = require_approved(root, draft)   # 抛 ReviewError

    key = idempotency_key(draft)
    ledger = os.path.join(root, "out", "publish_ledger.jsonl")
    dlq = os.path.join(root, "out", "dead_letter.jsonl")

    payload = {"idempotency_key": key, "account": draft["account"],
               "platform": draft["platform"], "slot": draft["slot"],
               "text": draft["text"], "draft_id": draft["draft_id"],
               "reviewer": approval.get("reviewer")}

    # ★ 发布前落账：此刻起，无论进程怎么死，都能查到"曾经尝试过"
    append_jsonl(ledger, {"event": "attempt_start", "at": time.time(),
                          "idempotency_key": key, "draft_id": draft["draft_id"],
                          "account": draft["account"], "slot": draft["slot"],
                          "reviewer": approval.get("reviewer")})

    last = None
    for attempt in range(1, max_attempts + 1):
        code, body = _post(endpoint, payload, timeout)
        retryable = (code == 0 or code in RETRYABLE_HTTP)  # -1（配置错）不可重试
        append_jsonl(ledger, {"event": "attempt_result", "at": time.time(),
                              "idempotency_key": key, "attempt": attempt,
                              "http": code, "retryable": retryable,
                              "body": body if isinstance(body, (dict, str)) else str(body)})
        last = {"attempt": attempt, "http": code, "body": body}

        if code == 200 and isinstance(body, dict) and body.get("ok"):
            res = {"ok": True, "draft_id": draft["draft_id"],
                   "idempotency_key": key, "attempts": attempt,
                   "post_id": body.get("post_id"),
                   "duplicate": bool(body.get("duplicate"))}
            append_jsonl(ledger, {"event": "published", "at": time.time(), **res})
            return res

        if not retryable:
            break
        if attempt < max_attempts and backoff:
            sleep(backoff * (2 ** (attempt - 1)))

    reason = ("不可重试错误（第 %d 次尝试，http=%s）"
              % (last["attempt"], last["http"])
              if last and last["http"] and last["http"] not in RETRYABLE_HTTP
              else "重试 %d 次仍失败（最后一次 http=%s）"
                   % (last["attempt"], last["http"]))
    rec = {"draft_id": draft["draft_id"], "idempotency_key": key,
           "account": draft["account"], "slot": draft["slot"],
           "attempts": last["attempt"] if last else 0,
           "last_http": last["http"] if last else None,
           "reason": reason, "at": time.time()}
    append_jsonl(dlq, rec)
    append_jsonl(ledger, {"event": "dead_letter", "at": time.time(), **rec})
    return {"ok": False, "draft_id": draft["draft_id"], "reason": reason,
            "attempts": last["attempt"] if last else 0}


def publish_all(root, endpoint, only_approved=True, **kw):
    """发布全部草案。返回汇总。"""
    drafts_doc = read_json(os.path.join(root, "out", "drafts.json"), {}) or {}
    results = []
    for d in drafts_doc.get("drafts", []):
        try:
            results.append(publish_one(root, d, endpoint, **kw))
        except ReviewError as exc:
            results.append({"ok": False, "draft_id": d["draft_id"],
                            "reason": "审核门拒绝：%s" % exc, "blocked": True})
    ok = [r for r in results if r.get("ok")]
    blocked = [r for r in results if r.get("blocked")]
    failed = [r for r in results if not r.get("ok") and not r.get("blocked")]
    summary = {"total": len(results), "published": len(ok),
               "blocked_by_review": len(blocked), "failed": len(failed),
               "results": results}
    write_json(os.path.join(root, "out", "publish_summary.json"), summary)
    return summary


def recover_orphans(root):
    """找出"开始过但没有任何结果"的台账记录 —— 崩溃留下的悬案。

    这是发布前落账的价值兑现点：没有 attempt_start，就无法区分
    "从没试过" 与 "试了但没写回结果"。
    """
    ledger = read_jsonl(os.path.join(root, "out", "publish_ledger.jsonl"))
    started, finished = {}, set()
    for rec in ledger:
        if rec.get("event") == "attempt_start":
            started[rec["idempotency_key"]] = rec
        elif rec.get("event") in ("published", "dead_letter"):
            finished.add(rec["idempotency_key"])
    orphans = [{"idempotency_key": k, "draft_id": v.get("draft_id"),
                "account": v.get("account"), "slot": v.get("slot")}
               for k, v in started.items() if k not in finished]
    return {"started": len(started), "finished": len(finished),
            "orphans": orphans}
