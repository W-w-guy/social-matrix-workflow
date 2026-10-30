# 工作流与失败恢复说明

这份文档回答客户验收里的两件事：**人工审核步骤** 与 **发布失败恢复**。

---

## 一、六段式流程

```
assets/ ──register──► out/assets.json
                          │
                          ├──draft──► out/drafts.json      （草案队列）
                          │
                          ├──review──► 待审清单
                          ├──approve─► out/approvals.json  （审批记录，绑正文哈希）
                          │
                          ├──publish─► out/publish_ledger.jsonl  （每次尝试）
                          │            out/dead_letter.jsonl     （发不出去的）
                          │
                          └──report──► out/feedback_report.json  （反馈 + 次日建议）
                                       out/publish_summary.json
```

每段一条命令、一个退出码。**不靠解析文字判断成败**，可以直接接 cron。

---

## 二、人工审核：为什么是硬门

客户原文「保留人工监管和调整」。多数交付会把它做成一句提示语，这里做成了发布前的强制条件。

**审批记录长这样**：

```json
{
 "draft_id": "d_7672e0f75a101751",
 "reviewer": "张三",
 "at": "2026-10-30 16:41:02",
 "text_sha256": "cd92301585dc....",
 "draft_version": 1,
 "note": "首条试审",
 "supersedes": null
}
```

**三条强制规则**：

1. **没有审批记录 → 发布直接拒绝**（退出码 3），不是警告、不是跳过。
2. **审批绑的是正文哈希**。批准后改一个字节（哪怕是末尾空格），`text_sha256` 就对不上，
   审批立即失效，必须重新批。
3. **审批人不能空**。`""`、`"   "`、Tab 都拒绝 —— 这是最常见的"看起来审过了"的假通过。

**为什么存哈希而不是存正文副本**：存副本会带来"以哪份为准"的二义性。
存哈希只有一个真值：正文在哪都能重算出来比对。

**同一条重复审批**：允许，但会记录 `supersedes`（谁覆盖了谁），并进 `reviews.jsonl` 历史。

---

## 三、发布失败恢复：四种真实世界的坏情况

### 1. 瞬时故障（对方 503 / 网络抖动）

重试，指数退避。默认最多 3 次。

```
第 1 次 → 503
第 2 次 → 200 ok
结果：ok=True, attempts=2
```

### 2. 永久错误（4xx 参数错）

**不重试**。重试一百次结果一样，直接进死信队列并写明原因。

```
第 1 次 → 400 invalid payload
结果：ok=False, attempts=1, 原因="不可重试错误"
```

### 3. 限流（429）

重试（配额会恢复），退避后成功。

### 4. 进程崩在发布中途 ★

这是最危险的一种，也是这套设计最值钱的地方。

**发布前先落一条 `attempt_start`**，然后才发请求：

```json
{"event": "attempt_start", "idempotency_key": "idem_a1b2...", "draft_id": "d_xxx", "at": 1793345000.1}
```

进程被杀 → 盘上只有 `attempt_start`，没有 `attempt_result`。
`python3 workflow.py recover` 会把这种记录列成"孤儿"：

```json
{"started": 3, "finished": 2, "orphans": [{"idempotency_key": "idem_a1b2...", "draft_id": "d_xxx"}]}
```

**没有这条记录会怎样**：事后无法区分"从没试过"和"试了但没写回结果"。
前者可以放心重发，后者重发可能造成重复发帖 —— 只能靠猜。

### 幂等：重试绝不重复发

幂等键 = `账号 + 草案版本 + 排期槽位` 的哈希：

```python
idem_ = sha256("fb-main|1|D+0|d_7672e0f75a101751")[:24]
```

同一个键再发一次，mock 端返回 `{"ok": true, "duplicate": true}` 而不是再发一条。
真实平台也应如此 —— 我们的 mock 对**缺少幂等键**的请求直接返回 422，
因为无法与"新的一条"区分。

> 草案改版（version+1）会产生新的幂等键 —— 这是有意的：改了文案就是新的一条，
> 不该被旧的幂等记录挡掉。

---

## 四、多账号

- 每个账号有独立排期、独立速率预算
- **一个账号失败不阻塞其他账号**（有测试）
- 幂等键包含账号名，两个账号发同一篇文案互不影响

---

## 五、数据反馈

`out/feedback.jsonl` 每条一行：

```json
{"draft_id": "d_xxx", "account": "fb-main", "slot": "D+0", "ok": true,
 "attempts": 2, "duplicate": false, "post_id": "post_2",
 "simulated": {"impressions": 412, "likes": 21, "clicks": 7, "simulated": true}}
```

`report` 会给出次日建议，例如：

- 「账号 fb-main：失败率 60%，建议先暂停该账号排期」
- 「账号 fb-main：有 1 条命中幂等（重试未重复发），说明重试链路正常」
- 「账号 fb-main：1 条经历重试后成功；若比例持续升高，说明对方接口不稳，
  建议把 backoff 调大而非加次数」

**`simulated` 字段是真话标记**：impressions / likes / clicks 是派生值，不是真实触达。
`feedback_report.json` 的 `note` 里也写明了这一点。

---

## 六、扩到 Instagram / TikTok

改一处即可：`lib/draft.py` 的 `PLATFORM_RULES`。

```python
PLATFORM_RULES = {
    "facebook":  {"max_chars": 220,  "min_chars": 90, "hashtags_min": 3, "hashtags_max": 5,  "links_max": 1},
    "instagram": {"max_chars": 2200, "min_chars": 40, "hashtags_min": 3, "hashtags_max": 10, "links_max": 0},
    "tiktok":    {"max_chars": 150,  "min_chars": 30, "hashtags_min": 2, "hashtags_max": 4,  "links_max": 0},
}
```

长度、标签数、链接数在**生成时**就校验，不拖到发布前才发现。

---

## 七、真机发布需要什么（样例不需要）

| 项 | 说明 |
|---|---|
| Meta 应用 | 在你们 Business 账号下创建，用于拿 Page token |
| Page Access Token | 每页一个长期 token，权限 `pages_manage_posts` |
| App Review | 未自动授予的权限需要走审核 |
| 轮换与吊销 | token 泄露或人员变动时，在 Business 后台重置，代码无需改动（只读配置） |
| 网络 | 出站访问 `graph.facebook.com`，443 |

**明确不做**：不要求 Facebook 密码、不存 Cookie、不用浏览器自动化驱动已登录会话。
那条路违反平台条款，且下一次界面改版就会失效。
