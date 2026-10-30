"""全量测试。一条命令：python3 test_all.py

不联网（除本地 mock 的 127.0.0.1）、不依赖第三方包、不写仓库外的文件。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from lib import assets as A
from lib import draft as DR
from lib import feedback as FB
from lib import mock as MK
from lib import publish as PB
from lib import review as RV
from lib.store import append_jsonl, read_json, read_jsonl, write_json


def cleanup(path):
    """删掉临时目录。不用第三方库，逐层删文件再删目录。"""
    for dp, dns, fns in os.walk(path, topdown=False):
        for f in fns:
            try:
                os.remove(os.path.join(dp, f))
            except OSError:
                pass
        for d in dns:
            try:
                os.rmdir(os.path.join(dp, d))
            except OSError:
                pass
    try:
        os.rmdir(path)
    except OSError:
        pass


def tmp_root():
    d = tempfile.mkdtemp(prefix="social_t_")
    os.makedirs(os.path.join(d, "out"), exist_ok=True)
    return d


def run_py(*args, root=ROOT, timeout=120):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    return subprocess.run([sys.executable] + list(args), cwd=root,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, timeout=timeout)


def make_drafts(root, accounts=("fb-main",), days=2, per_day=3, catalog=None):
    adir = os.path.join(root, "assets")
    os.makedirs(adir, exist_ok=True)
    open(os.path.join(adir, "product-catalog.txt"), "w", encoding="utf-8",
         newline="\n").write(catalog or (
        "SKU,品名,卖点,目标站点\n"
        "SP-1,保温杯 500ml,12小时保冷6小时保温;食品级304内胆;可刻字定制,US\n"
        "SP-2,折叠水壶 750ml,轻量仅120g可折叠;徒步露营通勤三用;FDA认证材质,UK\n"))
    open(os.path.join(adir, "banned-claims.txt"), "w", encoding="utf-8",
         newline="\n").write("史上最强\n保证收益\n")
    A.register(adir, os.path.join(root, "out", "assets.json"))
    return DR.generate(os.path.join(root, "out", "assets.json"),
                       list(accounts), days, os.path.join(root, "out", "drafts.json"),
                       per_day=per_day)


# ==================== 素材登记 ====================
class AssetRegistration(unittest.TestCase):
    def setUp(self):
        self.root = tmp_root()
        self.addCleanup(cleanup, self.root)

    def _dir(self):
        d = os.path.join(self.root, "assets")
        os.makedirs(d, exist_ok=True)
        return d

    def test_gbk_file_is_read_not_rejected_as_utf8(self):
        adir = self._dir()
        with open(os.path.join(adir, "表.csv"), "wb") as fh:
            fh.write("SKU,品名\nSP-9,保温杯\n".encode("gbk"))
        res = A.register(adir, os.path.join(self.root, "out", "a.json"))
        self.assertEqual(res[0]["encoding"], "gbk")
        self.assertIn("保温杯", res[0]["text"])

    def test_utf8_bom_is_accepted(self):
        adir = self._dir()
        open(os.path.join(adir, "b.csv"), "w", encoding="utf-8-sig",
             newline="\n").write("SKU,品名\nSP-1,x\n")
        res = A.register(adir, os.path.join(self.root, "out", "a.json"))
        self.assertEqual(res[0]["encoding"], "utf-8-sig")
        self.assertTrue(res[0]["text"].startswith("SKU"))

    def test_duplicate_content_is_registered_once_with_alias(self):
        adir = self._dir()
        payload = b"\x89PNG\r\n\x1a\nfake"
        open(os.path.join(adir, "logo.png"), "wb").write(payload)
        open(os.path.join(adir, "logo-copy.png"), "wb").write(payload)
        res = A.register(adir, os.path.join(self.root, "out", "a.json"))
        self.assertEqual(len(res), 1)
        names = {res[0]["file"]} | set(res[0]["aliases"])
        self.assertEqual(names, {"logo.png", "logo-copy.png"})

    def test_asset_id_is_content_derived_so_renaming_keeps_identity(self):
        adir = self._dir()
        open(os.path.join(adir, "a.txt"), "w", encoding="utf-8", newline="\n").write("hello")
        first = A.register(adir, os.path.join(self.root, "out", "a.json"))[0]["asset_id"]
        os.rename(os.path.join(adir, "a.txt"), os.path.join(adir, "renamed.txt"))
        second = A.register(adir, os.path.join(self.root, "out", "b.json"))[0]["asset_id"]
        self.assertEqual(first, second)

    def test_delivered_assets_have_ascii_filenames_only(self):
        """交付包里的样例素材名必须是 ASCII。

        实测：ZIP 里带中文文件名，在 Ubuntu 上 `unzip` 解压后变成
        `я╝ИцЧзя╝Йф╛Ых║ФхХЖшби GBK.csv`（unzip 不认 UTF-8 标志位），
        于是同一份素材在两平台登记出的 assets.json 哈希不同，
        客户做 MANIFEST 核对时会以为我们发了两个版本。
        客户自己的素材可以是中文名（程序支持），但**我们发出去的包**不行。
        """
        adir = os.path.join(ROOT, "assets")
        names = os.listdir(adir)
        self.assertTrue(names, "assets 目录是空的")
        for n in names:
            self.assertTrue(n.isascii(), "交付包里的素材文件名必须是 ASCII：%r" % n)

    def test_missing_folder_raises_clear_error(self):
        with self.assertRaises(FileNotFoundError):
            A.register(os.path.join(self.root, "nope"),
                       os.path.join(self.root, "out", "a.json"))


# ==================== 草案生成 ====================
class DraftGeneration(unittest.TestCase):
    def setUp(self):
        self.root = tmp_root()
        self.addCleanup(cleanup, self.root)

    def test_generation_is_deterministic(self):
        d1, _ = make_drafts(self.root)
        text1 = [d["text"] for d in d1]
        ids1 = [d["draft_id"] for d in d1]
        d2, _ = make_drafts(self.root)
        self.assertEqual(text1, [d["text"] for d in d2])
        self.assertEqual(ids1, [d["draft_id"] for d in d2])

    def test_draft_ids_are_distinct(self):
        drafts, _ = make_drafts(self.root)
        self.assertEqual(len({d["draft_id"] for d in drafts}), len(drafts))

    def test_repeating_the_same_text_in_one_day_is_rejected(self):
        """素材只有 2 个产品却要每天 3 条 —— 必须报错，不能生成撞 id 的草案。"""
        drafts, rejected = make_drafts(self.root, days=1, per_day=3)
        self.assertEqual(len(drafts), 2)
        self.assertTrue(any("重复" in r["reason"] for r in rejected), rejected)
        self.assertEqual(len({d["draft_id"] for d in drafts}), len(drafts))

    def test_banned_claim_is_rejected_with_reason(self):
        drafts, rejected = make_drafts(
            self.root,
            catalog=("SKU,品名,卖点,目标站点\n"
                     "SP-1,保温杯 500ml,史上最强保温效果;保证收益翻倍;"
                     "食品级304内胆;可刻字定制;防漏一体盖,US\n"))
        self.assertEqual(len(drafts), 0)
        self.assertTrue(any("禁用词" in r["reason"] for r in rejected), rejected)

    def test_weak_opener_is_not_a_valid_hook(self):
        with self.assertRaises(ValueError) as ctx:
            DR.build_draft("acc", {"sku": "S", "name": "点击链接看详情",
                                   "selling_points": ["好看"], "market": "US"},
                           "facebook", "D+0")
        self.assertIn("钩子", str(ctx.exception))

    def test_too_short_draft_is_rejected_not_padded(self):
        _drafts, rejected = make_drafts(
            self.root, catalog="SKU,品名,卖点,目标站点\nSP-1,杯,保温,US\n")
        self.assertTrue(rejected)
        self.assertTrue(any("过短" in r["reason"] for r in rejected), rejected)

    def test_header_only_catalog_is_not_a_success(self):
        with self.assertRaises(ValueError) as ctx:
            DR.parse_catalog("SKU,品名,卖点,目标站点\n")
        self.assertIn("没有数据行", str(ctx.exception))

    def test_catalog_missing_required_column_names_it(self):
        with self.assertRaises(ValueError) as ctx:
            DR.parse_catalog("SKU,名称\nSP-1,x\n")
        msg = str(ctx.exception)
        self.assertIn("品名", msg)
        self.assertIn("卖点", msg)

    def test_row_without_sku_is_reported(self):
        products, bad = DR.parse_catalog("SKU,品名,卖点\n,杯子,好\nSP-9,杯子,好\n")
        self.assertEqual(len(products), 1)
        self.assertEqual(bad[0]["reason"], "缺 SKU")
        self.assertEqual(bad[0]["line"], 2)

    def test_one_bad_row_does_not_block_the_whole_batch(self):
        """1000 行表里有 1 行脏数据，不能整个日更停摆 —— 好的先发，坏的挑出来。"""
        products, bad = DR.parse_catalog(
            "SKU,品名,卖点,目标站点\n"
            "SP-1,好产品,12小时保冷;食品级304;可刻字定制,US\n"
            "SP-2,,缺品名的一行,US\n"
            "SP-3,另一个好产品,轻量便携;可折叠;附收纳袋,US\n")
        self.assertEqual(sorted(p["sku"] for p in products), ["SP-1", "SP-3"])
        self.assertEqual(bad[0]["sku"], "SP-2")

    def test_all_rows_bad_is_still_an_error(self):
        with self.assertRaises(ValueError) as ctx:
            DR.parse_catalog("SKU,品名,卖点\n,杯子,好\n,壶,好\n")
        self.assertIn("没有任何一行可用", str(ctx.exception))

    def test_duplicate_selling_points_are_collapsed_not_repeated(self):
        """"保温;保温;保温" 不能生成 "保温、保温" —— 重复废话还白撑长度。"""
        p, _bad = DR.parse_catalog(
            "SKU,品名,卖点,目标站点\nSP-1,杯子,保温8小时;保温8小时;便携轻巧,US\n")
        self.assertEqual(p[0]["selling_points"], ["保温8小时", "便携轻巧"])

    def test_control_characters_are_stripped_from_catalog_cells(self):
        """表里混进 \x07 这类控制符，不能原样发到平台。"""
        p, _bad = DR.parse_catalog(
            "SKU,品名,卖点,目标站点\nSP-1,带\x07控\x00制符,保温;便携,US\n")
        self.assertNotIn("\x07", p[0]["name"])
        self.assertNotIn("\x00", p[0]["name"])
        self.assertEqual(p[0]["name"], "带控制符")

    def test_unknown_market_is_refused_not_silently_mapped_to_us(self):
        """站点写错时不能静默套 US 标签，否则正文与标签自相矛盾。"""
        with self.assertRaises(ValueError) as ctx:
            DR.build_draft("acc", {"sku": "S", "name": "保温杯 500ml",
                                   "selling_points": ["12小时保冷", "食品级304"],
                                   "market": "XX"}, "facebook", "D+0")
        self.assertIn("目标站点", str(ctx.exception))

    def test_multi_account_generates_separate_drafts(self):
        drafts, _ = make_drafts(self.root, accounts=("fb-a", "fb-b"), days=1, per_day=1)
        self.assertEqual(sorted({d["account"] for d in drafts}), ["fb-a", "fb-b"])


# ==================== 人工审核硬门 ====================
class ReviewGate(unittest.TestCase):
    def setUp(self):
        self.root = tmp_root()
        self.addCleanup(cleanup, self.root)
        self.drafts, _ = make_drafts(self.root)

    def test_unapproved_draft_cannot_be_published(self):
        with self.assertRaises(RV.ReviewError):
            RV.require_approved(self.root, self.drafts[0])

    def test_blank_reviewer_is_not_an_approval(self):
        for bad in ("", "   ", "\t"):
            with self.assertRaises(RV.ReviewError):
                RV.approve(self.root, self.drafts[0]["draft_id"], bad)

    def test_changing_one_byte_after_approval_invalidates_it(self):
        did = self.drafts[0]["draft_id"]
        RV.approve(self.root, did, "张三")
        doc = read_json(os.path.join(self.root, "out", "drafts.json"))
        doc["drafts"][0]["text"] = doc["drafts"][0]["text"] + " "
        write_json(os.path.join(self.root, "out", "drafts.json"), doc)
        with self.assertRaises(RV.ReviewError) as ctx:
            RV.require_approved(self.root, doc["drafts"][0])
        self.assertIn("审批失效", str(ctx.exception))

    def test_version_bump_invalidates_approval(self):
        did = self.drafts[0]["draft_id"]
        RV.approve(self.root, did, "张三")
        doc = read_json(os.path.join(self.root, "out", "drafts.json"))
        doc["drafts"][0]["version"] = 2
        write_json(os.path.join(self.root, "out", "drafts.json"), doc)
        with self.assertRaises(RV.ReviewError):
            RV.require_approved(self.root, doc["drafts"][0])

    def test_reject_requires_a_reason(self):
        with self.assertRaises(RV.ReviewError):
            RV.reject(self.root, self.drafts[0]["draft_id"], "张三", "  ")

    def test_reject_removes_a_previous_approval(self):
        did = self.drafts[0]["draft_id"]
        RV.approve(self.root, did, "张三")
        RV.reject(self.root, did, "李四", "文案要改")
        with self.assertRaises(RV.ReviewError):
            RV.require_approved(self.root, self.drafts[0])

    def test_reapprove_keeps_history_of_who_superseded_whom(self):
        did = self.drafts[0]["draft_id"]
        RV.approve(self.root, did, "张三")
        rec = RV.approve(self.root, did, "李四")
        self.assertEqual(rec["supersedes"]["reviewer"], "张三")
        actions = [r["action"] for r in read_jsonl(
            os.path.join(self.root, "out", "reviews.jsonl"))]
        self.assertEqual(actions, ["approve", "approve"])

    def test_pending_list_flags_tampered_approval(self):
        did = self.drafts[0]["draft_id"]
        RV.approve(self.root, did, "张三")
        doc = read_json(os.path.join(self.root, "out", "drafts.json"))
        doc["drafts"][0]["text"] += "x"
        write_json(os.path.join(self.root, "out", "drafts.json"), doc)
        pend = {p["draft_id"]: p["why"] for p in RV.list_pending(self.root)}
        self.assertIn(did, pend)
        self.assertIn("失效", pend[did])


# ==================== 发布：幂等 / 重试 / 死信 ====================
class Publishing(unittest.TestCase):
    def setUp(self):
        self.root = tmp_root()
        self.addCleanup(cleanup, self.root)
        self.drafts, _ = make_drafts(self.root, days=1, per_day=2)

    def _approve_all(self):
        for d in self.drafts:
            RV.approve(self.root, d["draft_id"], "张三")

    def _with_mock(self, scenario):
        srv, th, state, base = MK.start(scenario)
        self.addCleanup(srv.shutdown)
        return state, base + "/publish"

    def test_success_path_records_post_id(self):
        self._approve_all()
        state, url = self._with_mock("success")
        res = PB.publish_one(self.root, self.drafts[0], url)
        self.assertTrue(res["ok"])
        self.assertEqual(res["attempts"], 1)
        self.assertTrue(res["post_id"])

    def test_transient_error_is_retried_then_succeeds(self):
        self._approve_all()
        state, url = self._with_mock("transient")
        res = PB.publish_one(self.root, self.drafts[0], url, max_attempts=3)
        self.assertTrue(res["ok"])
        self.assertEqual(res["attempts"], 2)

    def test_permanent_error_is_not_retried(self):
        self._approve_all()
        state, url = self._with_mock("permanent")
        res = PB.publish_one(self.root, self.drafts[0], url, max_attempts=5)
        self.assertFalse(res["ok"])
        self.assertEqual(res["attempts"], 1)
        self.assertEqual(len(state.snapshot()["calls"]), 1)

    def test_rate_limit_is_retried(self):
        self._approve_all()
        state, url = self._with_mock("ratelimit")
        res = PB.publish_one(self.root, self.drafts[0], url, max_attempts=3)
        self.assertTrue(res["ok"])
        self.assertEqual(res["attempts"], 2)

    def test_retry_does_not_double_post(self):
        self._approve_all()
        state, url = self._with_mock("success")
        first = PB.publish_one(self.root, self.drafts[0], url)
        second = PB.publish_one(self.root, self.drafts[0], url)
        self.assertEqual(first["post_id"], second["post_id"])
        self.assertTrue(second["duplicate"])

    def test_concurrent_publish_of_the_same_draft_posts_only_once(self):
        """并发/双击触发时，mock 端只能真正成功一次，其余拿 duplicate。"""
        import threading
        self._approve_all()
        state, url = self._with_mock("success")
        results = []

        def go():
            results.append(PB.publish_one(self.root, self.drafts[0], url))

        ts = [threading.Thread(target=go) for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        ok = [r for r in results if r.get("ok")]
        self.assertEqual(len(ok), 4, [r.get("reason") for r in results])
        self.assertEqual(len({r["post_id"] for r in ok}), 1, "并发时产生了多条帖子")
        self.assertEqual(len(state.snapshot()["succeeded"]), 1,
                         "mock 实际接受了不止一条")

    def test_idempotency_key_depends_on_account_version_and_slot(self):
        d = dict(self.drafts[0])
        k1 = PB.idempotency_key(d)
        d2 = dict(d); d2["account"] = "other"
        self.assertNotEqual(k1, PB.idempotency_key(d2))
        d3 = dict(d); d3["slot"] = "D+9"
        self.assertNotEqual(k1, PB.idempotency_key(d3))
        d4 = dict(d); d4["version"] = 2
        self.assertNotEqual(k1, PB.idempotency_key(d4))

    def test_attempt_is_logged_before_the_call(self):
        self._approve_all()
        state, url = self._with_mock("permanent")
        PB.publish_one(self.root, self.drafts[0], url, max_attempts=1)
        ledger = read_jsonl(os.path.join(self.root, "out", "publish_ledger.jsonl"))
        events = [r["event"] for r in ledger]
        self.assertIn("attempt_start", events)
        self.assertLess(events.index("attempt_start"), events.index("dead_letter"))

    def test_non_retryable_failure_lands_in_dead_letter(self):
        self._approve_all()
        state, url = self._with_mock("permanent")
        PB.publish_one(self.root, self.drafts[0], url, max_attempts=1)
        dlq = read_jsonl(os.path.join(self.root, "out", "dead_letter.jsonl"))
        self.assertEqual(len(dlq), 1)
        self.assertIn("不可重试", dlq[0]["reason"])

    def test_malformed_endpoint_is_not_retried_and_leaves_no_orphan(self):
        """endpoint 写错属配置错：不重试、进死信、且不能留下假孤儿。"""
        self._approve_all()
        res = PB.publish_one(self.root, self.drafts[0], "not-a-url",
                             max_attempts=3, timeout=2)
        self.assertFalse(res["ok"])
        self.assertEqual(res["attempts"], 1)
        self.assertEqual(PB.recover_orphans(self.root)["orphans"], [])

    def test_unreachable_endpoint_is_retried_then_dead_lettered(self):
        """端口不通是可重试错误，重试用尽后进死信（不留孤儿）。"""
        self._approve_all()
        res = PB.publish_one(self.root, self.drafts[0],
                             "http://127.0.0.1:1/publish",
                             max_attempts=2, timeout=1)
        self.assertFalse(res["ok"])
        self.assertEqual(res["attempts"], 2)
        self.assertEqual(PB.recover_orphans(self.root)["orphans"], [])

    def test_tampered_approval_hash_does_not_pass_the_gate(self):
        """手工改 approvals.json 里的哈希，也必须拦住。"""
        did = self.drafts[0]["draft_id"]
        RV.approve(self.root, did, "张三")
        path = os.path.join(self.root, "out", "approvals.json")
        ap = read_json(path)
        ap[did]["text_sha256"] = "0" * 64
        write_json(path, ap)
        with self.assertRaises(RV.ReviewError):
            RV.require_approved(self.root, self.drafts[0])

    def test_orphan_is_detected_when_process_died_midway(self):
        append_jsonl(os.path.join(self.root, "out", "publish_ledger.jsonl"),
                     {"event": "attempt_start", "idempotency_key": "idem_dead",
                      "draft_id": "d_x", "account": "fb-main", "slot": "D+0"})
        rep = PB.recover_orphans(self.root)
        self.assertEqual(len(rep["orphans"]), 1)
        self.assertEqual(rep["orphans"][0]["idempotency_key"], "idem_dead")

    def test_no_orphan_after_a_normal_publish(self):
        self._approve_all()
        state, url = self._with_mock("success")
        PB.publish_one(self.root, self.drafts[0], url)
        self.assertEqual(PB.recover_orphans(self.root)["orphans"], [])

    def test_unapproved_items_are_blocked_not_published(self):
        RV.approve(self.root, self.drafts[0]["draft_id"], "张三")
        state, url = self._with_mock("success")
        summary = PB.publish_all(self.root, url)
        self.assertEqual(summary["published"], 1)
        self.assertEqual(summary["blocked_by_review"], 1)

    def test_one_account_failing_does_not_block_another(self):
        drafts, _ = make_drafts(self.root, accounts=("fb-a", "fb-b"), days=1, per_day=1)
        for d in drafts:
            RV.approve(self.root, d["draft_id"], "张三")
        srv, th, state, base = MK.start("permanent")
        self.addCleanup(srv.shutdown)
        a = [d for d in drafts if d["account"] == "fb-a"][0]
        self.assertFalse(PB.publish_one(self.root, a, base + "/publish", max_attempts=1)["ok"])
        srv2, th2, state2, base2 = MK.start("success")
        self.addCleanup(srv2.shutdown)
        b = [d for d in drafts if d["account"] == "fb-b"][0]
        self.assertTrue(PB.publish_one(self.root, b, base2 + "/publish")["ok"])


# ==================== 数据反馈 ====================
class Feedback(unittest.TestCase):
    def setUp(self):
        self.root = tmp_root()
        self.addCleanup(cleanup, self.root)
        self.drafts, _ = make_drafts(self.root, days=1, per_day=2)

    def test_simulated_metrics_are_stable_for_the_same_post(self):
        a = FB.simulated_metrics_for("post_1")
        b = FB.simulated_metrics_for("post_1")
        self.assertEqual(a, b)
        self.assertTrue(a["simulated"])

    def test_report_marks_simulated_data_as_simulated(self):
        rep = FB.build_report(self.root)
        self.assertIn("模拟", rep["note"])

    def test_any_dead_letter_produces_a_suggestion_even_at_low_failure_rate(self):
        """1/6 的失败率达不到暂停阈值，但死信必须单独报出来，不能显示成一切正常。"""
        FB.record_result(self.root, self.drafts[0], {"ok": True, "attempts": 1})
        FB.record_result(self.root, self.drafts[1], {"ok": False, "attempts": 1,
                                                     "reason": "boom"})
        rep = FB.build_report(self.root)
        self.assertTrue(any("死信" in s for s in rep["suggestions"]), rep)
        for d in self.drafts:
            FB.record_result(self.root, d, {"ok": False, "attempts": 1,
                                            "reason": "boom"})
        rep = FB.build_report(self.root)
        self.assertTrue(any("暂停" in s for s in rep["suggestions"]), rep)


# ==================== 命令行退出码 ====================
class CliExitCodes(unittest.TestCase):
    def setUp(self):
        self.root = tmp_root()
        self.addCleanup(cleanup, self.root)

    def test_register_on_missing_folder_exits_2(self):
        r = run_py("workflow.py", "register", "--assets", "no-such-dir",
                   "--out", os.path.join(self.root, "out", "a.json"))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stdout + r.stderr)

    def test_bad_catalog_exits_2_with_a_readable_message(self):
        adir = os.path.join(self.root, "assets")
        os.makedirs(adir, exist_ok=True)
        open(os.path.join(adir, "product-catalog.txt"), "w", encoding="utf-8",
             newline="\n").write("SKU,名称\nSP-1,x\n")
        out = os.path.join(self.root, "out", "a.json")
        self.assertEqual(run_py("workflow.py", "register", "--assets", adir,
                                "--out", out).returncode, 0)
        r = run_py("workflow.py", "draft", "--assets", out, "--accounts", "x",
                   "--out", os.path.join(self.root, "out", "d.json"))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("品名", r.stderr)

    def test_approve_with_blank_reviewer_exits_3(self):
        make_drafts(self.root)
        did = read_json(os.path.join(self.root, "out", "drafts.json"))["drafts"][0]["draft_id"]
        r = run_py("workflow.py", "approve", "--draft", did, "--reviewer", "   ",
                   "--root", self.root)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)

    def test_publish_without_approval_exits_3(self):
        make_drafts(self.root)
        srv, th, state, base = MK.start("success")
        try:
            r = run_py("workflow.py", "publish", "--endpoint", base + "/publish",
                       "--root", self.root)
        finally:
            srv.shutdown()
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)

    def test_demo_runs_end_to_end(self):
        r = run_py("workflow.py", "demo")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        payload = json.loads(r.stdout)
        self.assertTrue(payload["ok"])
        self.assertGreaterEqual(payload["published"], 1)
        self.assertGreaterEqual(payload["blocked_by_review"], 1)
        self.assertGreaterEqual(payload["dead_letter"], 1)


# ==================== Hermes skill 合规 ====================
class HermesSkill(unittest.TestCase):
    """客户要求「需展示 Hermes 工作流或说明等价实现」。这里把格式钉死。"""

    def setUp(self):
        self.path = os.path.join(ROOT, "skills", "social-matrix", "SKILL.md")

    def _frontmatter(self):
        text = open(self.path, encoding="utf-8").read()
        self.assertTrue(text.startswith("---\n"), "SKILL.md 必须以 --- 开头")
        end = text.index("\n---", 3)
        body = text[end:]
        self.assertTrue(body.strip().lstrip("-").strip(), "frontmatter 之后必须有正文")
        return text[4:end]

    def test_required_fields_are_present(self):
        import re
        fm = self._frontmatter()
        for key in ("name", "description", "version", "author", "license", "platforms"):
            self.assertRegex(fm, r"(?m)^%s:" % key, "frontmatter 缺 %s" % key)

    def test_name_is_lowercase_hyphenated_and_short(self):
        fm = self._frontmatter()
        name = fm.split("name:", 1)[1].split("\n", 1)[0].strip()
        self.assertRegex(name, r"^[a-z0-9-]+$")
        self.assertLessEqual(len(name), 64)

    def test_description_is_at_most_sixty_chars_and_ends_with_a_period(self):
        fm = self._frontmatter()
        desc = fm.split("description:", 1)[1].split("\n", 1)[0].strip()
        self.assertLessEqual(len(desc), 60, "description 超过 60 字符：%r" % desc)
        self.assertTrue(desc.endswith("."), "description 必须以句号结尾")

    def test_version_is_semver(self):
        fm = self._frontmatter()
        ver = fm.split("version:", 1)[1].split("\n", 1)[0].strip()
        self.assertRegex(ver, r"^\d+\.\d+\.\d+$")

    def test_platforms_is_a_yaml_list(self):
        fm = self._frontmatter()
        line = fm.split("platforms:", 1)[1].split("\n", 1)[0].strip()
        self.assertTrue(line.startswith("[") and line.endswith("]"), line)

    def test_entry_script_exists_and_is_referenced(self):
        script = os.path.join(ROOT, "skills", "social-matrix", "scripts", "run.py")
        self.assertTrue(os.path.exists(script), "skill 缺 scripts/run.py")
        text = open(self.path, encoding="utf-8").read()
        self.assertIn("workflow.py", text)


# ==================== 文档一致性 ====================
class DocConsistency(unittest.TestCase):
    """文档里写的命令、参数必须真实存在，写错就红。"""

    def _docs(self):
        out = ""
        for name in ("README.md", "SETUP.md", "WORKFLOW.md", "VERIFICATION.md"):
            p = os.path.join(ROOT, name)
            if os.path.exists(p):
                out += open(p, encoding="utf-8").read()
        return out

    def total_tests(self):
        loader = unittest.TestLoader()
        suite = loader.discover(os.path.join(ROOT, "tests"), pattern="test_*.py",
                                top_level_dir=ROOT)

        def flat(x):
            for i in x:
                if isinstance(i, unittest.TestSuite):
                    for y in flat(i):
                        yield y
                else:
                    yield i

        return sum(1 for _ in flat(suite))

    def test_declared_test_count_matches_reality(self):
        """文档写死的测试条数，一加测试就过期 —— 这条让它过期即红。"""
        import re
        text = self._docs()
        claimed = {int(n) for n in re.findall(r"(\d+)\s*项测试", text)}
        claimed |= {int(n) for n in re.findall(r"Ran (\d+) tests", text)}
        self.assertTrue(claimed, "文档里没有任何测试条数声明")
        self.assertEqual(claimed, {self.total_tests()},
                         "文档写的测试数与实际不符：文档 %s，实际 %d"
                         % (sorted(claimed), self.total_tests()))

    def test_every_documented_subcommand_exists(self):
        import re
        text = self._docs()
        used = set(re.findall(r"workflow\.py ([a-z-]+)", text))
        self.assertGreaterEqual(len(used), 6, used)
        cli = open(os.path.join(ROOT, "workflow.py"), encoding="utf-8").read()
        for name in used:
            self.assertIn('"%s"' % name, cli, "文档里的子命令 %r 不存在" % name)

    def test_every_documented_flag_is_accepted(self):
        import re
        text = self._docs()
        cli = open(os.path.join(ROOT, "workflow.py"), encoding="utf-8").read()
        for flag in set(re.findall(r"(?<![\w-])(--[a-z][a-z-]+)", text)):
            self.assertIn('"%s"' % flag, cli, "文档里的参数 %s CLI 不接受" % flag)

    def test_no_two_test_classes_share_a_name(self):
        seen, dup = set(), []
        for name in dir(sys.modules[__name__]):
            obj = getattr(sys.modules[__name__], name)
            if isinstance(obj, type) and issubclass(obj, unittest.TestCase) \
                    and obj is not unittest.TestCase:
                if obj.__name__ in seen:
                    dup.append(obj.__name__)
                seen.add(obj.__name__)
        self.assertEqual(dup, [], "同名测试类会被后者覆盖: %s" % dup)


if __name__ == "__main__":
    unittest.main(verbosity=1)
