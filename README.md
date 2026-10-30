# 海外社媒矩阵工作流样例（Facebook 优先，可扩展到 Instagram / TikTok）

从客户自有素材生成每日内容草案 → 人工逐条审核 → **模拟发布端** → 失败恢复 → 数据反馈。
零第三方依赖，纯 Python 标准库。

> **这个交付里不存在真实发布代码路径。** 客户原文明确允许「模拟发布端、不向真实账号自动发布」，
> 所以这里不是"默认关掉真实发布"，是根本没有写。运行 demo **不需要任何账号、任何密钥、任何联网**。

## 3 分钟上手

```bash
python3 test_all.py                    # 全量测试，一条命令
python3 workflow.py demo               # 一键跑完整链路（含失败重放）
```

`demo` 会打印一份汇总：生成几条草案、发出去几条、被审核门拦下几条、进死信几条。

换成你们真实的素材：

```bash
# 1) 把素材（txt/md/csv，UTF-8 或 GBK 都行）+ product-catalog.txt 放进 assets/
python3 workflow.py register --assets assets --out out/assets.json
python3 workflow.py draft    --assets out/assets.json --accounts fb-main --days 2 --out out/drafts.json
python3 workflow.py review                       # 看哪些还没审
python3 workflow.py approve  --draft d_xxxx --reviewer 张三
```

## 两层交付，任选其一

| 层 | 位置 | 用法 |
|---|---|---|
| **① Hermes skill** | `skills/social-matrix/`（合规 SKILL.md + scripts/run.py） | `hermes skills tap add <owner/repo>` 装上即可 |
| **② 独立运行器** | 仓库根 `workflow.py` | `python3 workflow.py demo`，不装 Hermes 也能跑 |

两层共用**同一份实现**：`scripts/run.py` 只是转发到根目录的 `workflow.py`，
所以不存在"skill 版和独立版行为不一致"的可能。

## 验收清单逐条对应

| 客户要求 | 这里的实现 | 怎么验 |
|---|---|---|
| 可演示流程 | 六段式：登记→草案→审核→发布→恢复→反馈，每段一条命令、独立退出码 | `python3 workflow.py demo` |
| 输入素材及输出示例 | `assets/` 4 份脱敏素材；`out/` 生成草案与发布结果 | `demo` 打印的汇总 |
| 账号授权与人工审核步骤 | 审核状态机 + `WORKFLOW.md` 的授权说明 | `workflow.py review` / 测试 `ReviewGate` |
| 发布失败恢复 | 重试（指数退避）+ 死信队列 + 孤儿检测 | `WORKFLOW.md` 的五种场景 + 测试 `Publishing` |
| 数据反馈说明 | 反馈台账 + 次日建议 | `workflow.py report` |
| 运行成本口径 | 开发费与运营费分开，见下文 | — |

## 成本口径：开发费 vs 运营费

**开发**：一次性固定，无后续抽成。

**运营**（三桶，各自有单价口径）：

| 桶 | 单位 | 样例阶段 | 真实使用 |
|---|---|---|---|
| 基础设施 | 元/月 | **0**（本机跑） | 一台 $5–10/月 VPS 足够 |
| 模型用量 | 元/千条 | **0**（草案确定性生成，不调模型） | 若打开 LLM 适配层：见下 |
| 平台 API | 元/条 | **0**（mock 不发外部请求） | Facebook Graph API 本身免费，但需要 Business 账号下的应用 + 权限审核 |

**LLM 适配层**：代码里预留了单一接缝（`lib/draft.py` 的 `build_draft` 是纯函数，
外层换实现即可），**默认关闭**。打开后每条的 token 数会在交接文档里给出实测值，
由你们按自己的单价乘 —— 而不是由我估一个数字。

## 四条不肯让步的设计

**1. 人工审核是硬门，不是提示。**
草案必须带"审阅人 + 时间戳 + 被批准确切文本的 SHA-256"才能发布。
批准后改一个字节（哪怕是空格），审批立即失效。这是状态机强制的，`tests` 里钉着。

**2. 重试幂等，绝不重复发。**
幂等键 = 账号 + 草案版本 + 排期槽位 的哈希。重试带同一个键，mock 端返回
`duplicate` 而不是再发一条。真实平台也应如此 —— 没有幂等键的发布请求会被拒绝（422）。

**3. 发布前先落账。**
进程在 publish 中途被杀，盘上会留下 `attempt_start` 而没有结果 ——
`workflow.py recover` 会把它列成"孤儿"。没有这条记录，事后无法区分
"从没试过"和"试了但没写回"，只能靠猜。

**4. 模拟数据必须标注为模拟。**
`out/feedback_report.json` 里的 `note` 字段写明 impressions/likes/clicks 是派生值。
**不会把模拟触达当成真实数据汇报。**

## 多账号与扩展

- 账号是第一等概念：每个账号有独立排期、独立速率预算；一个账号失败不阻塞其他（有测试）。
- 扩平台只改 `lib/draft.py` 的 `PLATFORM_RULES` 一行（facebook / instagram / tiktok 已在表里）。

## 目录

```
workflow.py                 CLI 入口（9 个子命令，退出码 0/1/2/3/4）
lib/store.py                原子写 + 追加式台账（fsync）
lib/assets.py               素材登记：编码识别、内容哈希去重、改名不改身份
lib/draft.py                确定性草案生成：平台规则、禁用词、钩子校验
lib/review.py               人工审核硬门：哈希绑定、篡改即失效
lib/publish.py              幂等发布：重试、死信、发布前落账、孤儿检测
lib/mock.py                 模拟发布端：success/transient/permanent/ratelimit/timeout
lib/feedback.py             反馈台账 + 次日建议
skills/social-matrix/       Hermes skill（SKILL.md + scripts/run.py）
assets/                     脱敏素材（含一份 GBK 编码的表）
tests/test_workflow.py      全量测试
```

## 一个实测踩到的打包坑（客户自己不会遇到，但值得知道）

我们最初把样例素材命名为 `（旧）供应商表 GBK.csv`，结果：**同一个 zip 在 Ubuntu 上用 `unzip`
解压后，文件名变成 `я╝ИцЧзя╝Йф╛Ых║ФхХЖшби GBK.csv`**（unzip 不认 UTF-8 标志位）。
于是同一份素材在两台机器上登记出的 `assets.json` 哈希不同，客户做 MANIFEST 核对时
会以为我们发了两个版本。修法：**交付包内的样例素材名一律 ASCII**。

**你们自己的素材可以放心用中文名** —— 程序按内容哈希登记，文件名只作展示，不改身份。
（这一条有测试钉住：`test_delivered_assets_have_ascii_filenames_only`）
