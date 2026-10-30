---
name: social-matrix
description: Draft, review and publish social posts to a mock endpoint.
version: 1.0.0
author: LocalEngineeringAssistant
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [social, content, workflow, facebook, automation]
    config:
      assets_dir:
        type: string
        default: assets
      out_dir:
        type: string
        default: out
      endpoint:
        type: string
        default: http://127.0.0.1:8765/publish
      daily_posts:
        type: integer
        default: 3
related_skills: []
---

# Social matrix workflow

Build a day's worth of post drafts from the customer's own assets, require a human
approval on each one, then publish to a **simulated** endpoint that replays success,
transient errors, permanent errors, rate limits and timeouts. Nothing here ever
touches a real social account, and no credentials are used.

## When to use

Use this skill when the task is "produce and schedule social posts from our own
material, with human review, and show what happens when publishing fails".
Do not use it when the task requires publishing to a live account — that needs
OAuth tokens this skill deliberately does not handle.

## Inputs

- `assets/` — the customer's own material. Text files are read as UTF-8, UTF-8-BOM,
  GBK or GB18030 (auto-detected). Images are registered by content hash only.
- `assets/product-catalog.txt` — CSV with columns `SKU,品名,卖点,目标站点`.
  Selling points are separated by `;` or `；`.
- `assets/banned-claims.txt` — one banned phrase per line. Any draft that contains
  one is rejected, not silently rewritten.

## Steps

Run these in order. Each returns a distinct exit code, so the whole flow can be
wired into cron without parsing text.

```bash
python3 workflow.py register --assets assets --out out/assets.json
python3 workflow.py draft    --assets out/assets.json --accounts fb-main --days 2 --out out/drafts.json
python3 workflow.py review                       # list what still needs approval
python3 workflow.py approve  --draft d_xxx --reviewer 张三 --note "ok"
python3 workflow.py reject   --draft d_xxx --reviewer 张三 --reason "文案需改"
python3 workflow.py publish  --endpoint http://127.0.0.1:8765/publish
python3 workflow.py report                       # feedback ledger + next-day advice
python3 workflow.py recover                      # find publishes that died mid-way
python3 workflow.py demo                         # one-shot end-to-end, includes failure replay
```

## Rules the skill enforces (do not work around them)

1. **A draft cannot be published without an approval record.** The record stores the
   SHA-256 of the exact text that was approved. Change one byte and the approval is
   void — re-approval is required. This is a state machine, not a warning.
2. **Blank reviewer names are not approvals.** `""`, `"   "` and tab-only names are
   rejected.
3. **Retries are idempotent.** The key is derived from account + draft version +
   schedule slot, so a retry can never double-post.
4. **Non-retryable errors do not get retried.** 4xx goes straight to the dead-letter
   queue with the exact reason.
5. **Attempts are logged before the call, not after.** If the process dies mid-publish,
   the ledger shows an orphan instead of silence. `recover` lists them.
6. **Simulated numbers stay labelled simulated.** The mock returns impressions/likes
   that are derived, not measured; the report says so in its `note`.

## Output

- `out/assets.json`, `out/drafts.json`, `out/approvals.json`
- `out/reviews.jsonl`, `out/publish_ledger.jsonl`, `out/dead_letter.jsonl`
- `out/feedback.jsonl`, `out/feedback_report.json`, `out/publish_summary.json`

## Failure modes and what to do

| Symptom | Meaning | Action |
|---|---|---|
| `草案过短` | selling points too few for the platform minimum | add selling points, do not pad |
| `同一账号同一天的文案重复` | fewer assets than posts per day | add assets or lower `--days`/daily count |
| `命中禁用词` | banned phrase in generated text | fix the source copy |
| `审批失效` | text edited after approval | re-approve |
| exit 3 | review gate refused | approve first |
| exit 4 | publish failed | read `out/dead_letter.jsonl` |
