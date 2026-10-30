# 安装与运行

## 1. 要求

- Python **3.9+**（用了 `os.replace`、f-string、`urllib` 标准库）
- **不需要 pip install**，零第三方依赖
- **不需要联网**（除本地 127.0.0.1 的 mock 端口）
- **不需要任何账号或密钥**

在 Windows 11（Python 3.11.7）与 Ubuntu 24.04（Python 3.12.3）上都实测过，命令完全一致。

## 2. 解压后进入目录

```bash
unzip social-matrix-workflow.zip
cd social-matrix-workflow
```

## 3. 一条命令跑全部测试

```bash
python3 test_all.py
```

预期输出（实测，见 VERIFICATION.md）：

```
test classes: AssetRegistration, CliExitCodes, DocConsistency, DraftGeneration,
              Feedback, HermesSkill, Publishing, ReviewGate
Ran 63 tests in 9.8s
OK
```

## 4. 跑一次完整链路

```bash
python3 workflow.py demo
```

这一步会：造 6 条草案 → 审批 5 条（第 3 条故意不审）→ 用 transient 场景发布
（第 1 次 503、第 2 次成功）→ 再单独跑 permanent 与 ratelimit 两种场景 →
汇总反馈。**全程不联网、不碰任何真实账号。**

## 5. 换成你们自己的素材

```bash
# 把文件放进 assets/（文本 UTF-8/GBK/GB18030 都认；图片按内容哈希登记）
python3 workflow.py register --assets assets --out out/assets.json
```

`assets/product-catalog.txt` 必须是 CSV，列名固定：

```
SKU,品名,卖点,目标站点
SP-1001,不锈钢保温杯 500ml,12小时保冷6小时保温;食品级304内胆;可刻字定制,US
```

卖点用 `;` 或 `；` 分隔。**卖点太少会生成不出够长的文案，程序会拒绝并说明，
不会用废话凑字数。**

`assets/banned-claims.txt` 一行一条禁用词，命中的草案会被拒绝，不会被悄悄改写。

```bash
python3 workflow.py draft --assets out/assets.json --accounts fb-main,fb-second \
        --days 2 --out out/drafts.json
```

## 6. 人工审核

```bash
python3 workflow.py review                                     # 待审清单
python3 workflow.py approve --draft d_xxxx --reviewer 张三
python3 workflow.py reject  --draft d_xxxx --reviewer 张三 --reason "文案需改"
```

**审批人名字不能为空**（空串、纯空格、Tab 都不算）。驳回必须写理由。

## 7. 发布到模拟端

```bash
python3 workflow.py publish --endpoint http://127.0.0.1:8765/publish
```

如果手上没有 mock 服务，用 `demo` 就行 —— 它会自己起一个。

退出码：`0` 成功 / `3` 有草案被审核门拦下 / `4` 有草案进了死信队列。

## 8. 查看反馈与孤儿

```bash
python3 workflow.py report     # 反馈台账 + 次日建议
python3 workflow.py recover    # 找出"开始过但没有结果"的发布
```

## 9. 退出码总表

| 退出码 | 含义 | 谁该处理 |
|---|---|---|
| 0 | 通过 | — |
| 1 | 有未通过项（草案被拒 / 有孤儿） | 看输出的 `rejected` / `orphans` |
| 2 | 用法或文件错误（路径、缺列、空目录） | 按 stderr 的中文提示改 |
| 3 | 审核门拒绝（未审批 / 审批后正文被改） | 重新审批 |
| 4 | 发布失败 | 看 `out/dead_letter.jsonl` |

## 10. 真机运行需要什么（样例阶段不需要）

把这些写清楚，是因为客户原文要求「明确客户设备、账号授权与平台接口要求」：

- 一台能跑 Python 的机器（样例在本机即可；长期跑建议一台 $5–10/月 VPS）
- Meta Business 账号下的一个应用（App）
- 要发布的每个 Page 的长期 Page Access Token（`pages_manage_posts` 权限）
- 未自动授予的权限需要走 App Review
- 每页一个 token，不是全账号一个 token；轮换与吊销路径见 `WORKFLOW.md`

**不会有、也不应该有的东西**：你的 Facebook 密码、已登录浏览器会话、Cookie。
用浏览器自动化驱动已登录会话既违反平台条款，也会在下一次界面改版后失效。
