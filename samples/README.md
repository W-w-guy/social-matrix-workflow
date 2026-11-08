# samples/ · 一次真实 demo 运行的输出示例

客户验收里写着「输入素材**及输出示例**」。`assets/` 是输入素材，这个目录就是输出示例 ——
**下面 9 个文件不是手写的，是 `python3 workflow.py demo` 在 Windows 11 / Python 3.11.7 上跑完后
直接从临时目录拷出来的原始产物**，一字未改。

| 文件 | 是什么 |
|---|---|
| `assets.json` | 素材台账：4 份素材，含编码识别结果（其中一份 GBK）与内容 SHA256 |
| `drafts.json` | 6 条草案（fb-main，D+0 / D+1 两个时段） |
| `approvals.json` | 审批记录：5 条批准，每条绑正文 SHA256 |
| `reviews.jsonl` | 审批历史（谁批的、何时、覆盖了谁） |
| `publish_ledger.jsonl` | 发布台账：每次尝试一行（attempt_start / attempt_result） |
| `dead_letter.jsonl` | 死信：1 条永久错误（http=400，不可重试） |
| `feedback.jsonl` | 反馈明细：6 条发布结果 |
| `feedback_report.json` | 反馈汇总 + 次日建议 |
| `publish_summary.json` | 这次发布的汇总（published 5 / blocked_by_review 1 / dead_letter 1） |

## 这些数字怎么读

```
drafts 6  →  approved 5  →  published 5（其中 1 条遇 503、退避重试后成功）
                          →  blocked_by_review 1（第 3 条故意没批，被审核门拦下）
           dead_letter 1（另跑的一次永久错误场景）
           orphans 0
```

## 两点如实说明

1. **`at` 字段是 epoch 秒、`idempotency_key` 是运行时随机生成**，所以每次跑都不一样。
   这个目录的价值是"长什么样"，不是"字节可复现"。
   真正字节可复现的是 `assets.json` 与 `drafts.json` 里**由素材决定的那些字段** ——
   这两个文件在两平台跑出来的 SHA256 完全相同，见 `VERIFICATION.md`。
2. `impressions` / `likes` / `clicks` 是**模拟值**，只用于演示反馈回流路径，
   不代表真实触达。样例不向任何真实账号发布，也不发任何外部请求。

自己复现：

```bash
python3 workflow.py demo      # 会打印一份汇总，其中 "root" 就是这次产物所在的临时目录
```
