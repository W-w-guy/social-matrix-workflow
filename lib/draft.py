"""草案生成：确定性、可复盘、零 API 成本。

客户原话「优先使用自有素材」「保留人工监管和调整」，所以默认不调模型：
同一个素材 + 同一份排期 + 同一份口径，必须产出逐字节相同的草案。
这样客户能 git diff 两天的输出，也能在审核时说清「你批的是哪一版」。

★ 为什么用「确定性」而不是「调模型」：
  1) 成本可核对：0 token，客户不用为演示付钱
  2) 可复现：同一输入两次跑出不同结果的东西，没法做回归测试
  3) 可审计：草案的每个字都能指回某份素材的某个字段
  LLM 适配层留在 llm.py，默认关闭；打开后接口不变。

★ 首行钩子是硬约束，不是建议：
  原文「首行必须是可独立成立的钩子」。所以首行由品名+核心卖点组成，
  且不允许出现「点击链接」「了解更多」这类无信息量的开场。
"""
import hashlib
import json
import os
import re

from .store import read_json

# 平台规则（Facebook / Instagram / TikTok）。扩展新平台只加一条，不改流程。
PLATFORM_RULES = {
    "facebook": {"max_chars": 220, "min_chars": 90, "hashtags_min": 3,
                 "hashtags_max": 5, "links_max": 1, "must_tag": "#跨境出海"},
    "instagram": {"max_chars": 2200, "min_chars": 40, "hashtags_min": 3,
                  "hashtags_max": 10, "links_max": 0, "must_tag": "#跨境出海"},
    "tiktok": {"max_chars": 150, "min_chars": 30, "hashtags_min": 2,
               "hashtags_max": 4, "links_max": 0, "must_tag": "#跨境出海"},
}

# 无信息量开场：用了就不算「可独立成立的钩子」
WEAK_OPENERS = ("点击链接", "了解更多", "点击查看", "快来买", "限时抢购",
                "Click here", "Learn more")

HASHTAG_POOL = {
    "US": ["#AmazonFBA", "#Ecommerce", "#跨境出海"],
    "UK": ["#UKBusiness", "#Ecommerce", "#跨境出海"],
    "DE": ["#AmazonDE", "#Ecommerce", "#跨境出海"],
}


def _digest(obj):
    return hashlib.sha256(
        json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


# 控制字符（含 \x00-\x1f 里的不可打印项）：会原样进正文、发到平台就是脏数据。
# 只保留 \t 换成空格，其余删掉。这不是"美化"，是防止把二进制垃圾发出去。
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean_cell(text):
    """清洗单个字段：去控制字符、去首尾空白、压缩内部连续空白。"""
    if text is None:
        return ""
    t = _CTRL.sub("", str(text)).replace("\t", " ")
    return " ".join(t.split())


def parse_catalog(text):
    """把 catalog 文本解析成产品行。

    容忍：BOM、CRLF、缺列、字段里有逗号（用 csv 模块）。
    不容忍：缺 SKU 或缺品名 —— 这两样缺了就没法生成，直接报错不猜。
    """
    import csv
    import io

    if text is None:
        raise ValueError("素材里没有可解析的产品目录文本")
    text = text.lstrip("\ufeff")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise ValueError("产品目录是空的")
    header = [h.strip() for h in rows[0]]
    need = {"SKU", "品名", "卖点"}
    missing = need - set(header)
    if missing:
        raise ValueError("产品目录缺列：%s（实际列：%s）"
                         % ("、".join(sorted(missing)), "、".join(header)))
    idx = {h: i for i, h in enumerate(header)}
    products, bad = [], []
    for lineno, r in enumerate(rows[1:], start=2):
        if not any(str(c).strip() for c in r):
            continue

        def take(col):
            i = idx.get(col)
            return (r[i].strip() if i is not None and i < len(r) else "")

        sku, name, hook = take("SKU"), take("品名"), take("卖点")
        # ★ 2026-10-30 修：原来任何一行缺 SKU/品名就整批抛错 —— 1000 行表里
        #   有 1 行脏数据，整个日更流程停摆。这在实际业务里不可接受：
        #   客户每天早上要的是"能发的先发，坏的挑出来给我改"。
        #   现在坏行进 bad 列表（带行号与原值），好行继续生成；
        #   只有当一行都没能读出来时才报错 —— 那种情况才说明表结构真的不对。
        if not sku:
            bad.append({"line": lineno, "row": r, "reason": "缺 SKU"})
            continue
        if not name:
            bad.append({"line": lineno, "sku": sku, "reason": "缺品名"})
            continue
        # ★ 2026-10-30 修：卖点原来按 ; 切开就完事，于是
        #   "保温;保温;保温" 会生成 "保温、保温" —— 客户看到的是重复废话，
        #   而且白白撑长正文。这里保序去重。
        pts, seen_pt = [], set()
        for raw_pt in re.split(r"[;；]", hook):
            pt = _clean_cell(raw_pt)
            if not pt:
                continue
            key = pt.replace(" ", "")
            if key in seen_pt:
                continue
            seen_pt.add(key)
            pts.append(pt)
        products.append({
            "sku": _clean_cell(sku),
            "name": _clean_cell(name),
            "selling_points": pts,
            "market": _clean_cell(take("目标站点")) or "US",
        })
    if not products:
        if bad:
            raise ValueError(
                "产品目录里没有任何一行可用的数据（共 %d 行有问题，第一行：第 %d 行 %s）"
                % (len(bad), bad[0]["line"], bad[0]["reason"]))
        raise ValueError("产品目录只有表头，没有数据行")
    return products, bad


def load_banned(assets):
    """从已登记资产里取禁用词。找不到就返回空表 —— 但不静默假装有。"""
    for a in assets:
        if a["file"].startswith("banned-claims") and a.get("text"):
            return [w.strip() for w in a["text"].splitlines() if w.strip()]
    return []


def _check_banned(text, banned):
    hits = [w for w in banned if w in text]
    return hits


def build_draft(account, product, platform, slot_date, links=None):
    """生成一条草案。纯函数：同样入参永远同样输出。"""
    rules = PLATFORM_RULES[platform]
    points = product["selling_points"]
    if not points:
        raise ValueError("SKU %s 没有卖点，无法生成钩子" % product["sku"])

    hook = "%s：%s" % (product["name"], points[0])
    if any(hook.startswith(w) for w in WEAK_OPENERS):
        raise ValueError("首行是无信息量开场，不是可独立成立的钩子：%r" % hook)

    # 正文结构：钩子行 + 卖点展开 + 事实性收尾。
    # 收尾不写 CTA（"点击链接/了解更多"既无信息量、也违反"首行须可独立成立"），
    # 只写能被核对的事实：面向哪个站点、几项卖点、规格以目录为准。
    # 每一句都能指回素材里的某个字段，没有一句是编的。
    body_lines = [hook]
    if len(points) > 1:
        body_lines.append("、".join(points[1:]))
    body_lines.append("面向 %s 站点，%d 项卖点已在目录标注，规格与库存以产品目录为准"
                      % (product["market"], len(points)))
    body = "。".join(body_lines) + "。"

    # ★ 2026-10-30 修：目标站点写错（例如 "XX"）原来会静默用 US 的标签，
    #   正文却写"面向 XX 站点" —— 一条自相矛盾的帖子。现在直接拒绝。
    market = product["market"]
    if market not in HASHTAG_POOL:
        raise ValueError("目标站点 %r 不在支持列表 %s 里，请改成其中之一"
                         % (market, "/".join(sorted(HASHTAG_POOL))))
    tags = list(HASHTAG_POOL[market])
    if rules["must_tag"] not in tags:
        tags.append(rules["must_tag"])
    tags = tags[:rules["hashtags_max"]]
    if len(tags) < rules["hashtags_min"]:
        raise ValueError("标签数不足：%d < %d" % (len(tags), rules["hashtags_min"]))

    text = body + "\n\n" + " ".join(tags)
    if links and rules["links_max"] > 0:
        text += "\n" + links[0]

    # 长度与链接数校验在生成时就做，不留到发布前才发现
    if len(text) > rules["max_chars"]:
        raise ValueError("草案超长：%d > %d（%s）"
                         % (len(text), rules["max_chars"], platform))
    if len(text) < rules["min_chars"]:
        raise ValueError("草案过短：%d < %d（%s）"
                         % (len(text), rules["min_chars"], platform))
    if text.count("http") > rules["links_max"]:
        raise ValueError("链接数超出平台上限")

    draft = {
        "account": account,
        "platform": platform,
        "slot": slot_date,
        "sku": product["sku"],
        "text": text,
        "hashtags": tags,
        "source_asset": product.get("source_asset"),
    }
    # ★ draft_id 由内容派生：同样的草案重复生成不会变成两条
    draft["draft_id"] = "d_" + _digest({k: draft[k] for k in
                                       ("account", "platform", "slot", "sku", "text")})[:16]
    draft["version"] = 1
    return draft


def generate(assets_path, accounts, days, out_path, per_day=3, links=None):
    """按账号 × 天数生成草案队列。"""
    doc = read_json(assets_path)
    if not doc:
        raise FileNotFoundError("没有素材登记文件：%s" % assets_path)
    assets = doc["assets"]
    catalog_asset = None
    for a in assets:
        if a["file"].startswith("product-catalog") and a.get("text"):
            catalog_asset = a
            break
    if catalog_asset is None:
        raise ValueError("素材里找不到 product-catalog 文本")
    products, bad_rows = parse_catalog(catalog_asset["text"])
    for p in products:
        p["source_asset"] = catalog_asset["asset_id"]

    banned = load_banned(assets)
    drafts, rejected, emitted = [], list(bad_rows), set()
    for account in accounts:
        for d in range(days):
            slot_date = "D+%d" % d
            for i in range(per_day):
                p = products[(d * per_day + i) % len(products)]
                try:
                    dr = build_draft(account, p, "facebook", slot_date, links)
                except ValueError as exc:
                    rejected.append({"account": account, "slot": slot_date,
                                     "sku": p["sku"], "reason": str(exc)})
                    continue
                hits = _check_banned(dr["text"], banned)
                if hits:
                    rejected.append({"account": account, "slot": slot_date,
                                     "sku": p["sku"],
                                     "reason": "命中禁用词：%s" % "、".join(hits)})
                    continue
                # ★ 2026-10-30 修：同账号同槽位出现完全相同的正文时，
                #   draft_id 会相同 → 两条草案撞成一个 id，审批互相覆盖、
                #   发布被判重复。这不是「去重优化」，是内容排期错了：
                #   素材不够却要每天发多条，就会出现同一句文案发两遍。
                #   所以拒绝并报出来，让人补素材或调条数，而不是悄悄合并。
                seen_key = (account, slot_date, dr["text"])
                if seen_key in emitted:
                    rejected.append({
                        "account": account, "slot": slot_date, "sku": p["sku"],
                        "reason": "同一账号同一天的文案重复（素材条目不足以支撑"
                                  "每天 %d 条），请补充素材或减少条数" % per_day})
                    continue
                emitted.add(seen_key)
                dr["seq"] = len(drafts)
                drafts.append(dr)

    from .store import write_json
    write_json(out_path, {"count": len(drafts), "rejected": rejected,
                          "drafts": drafts})
    return drafts, rejected
