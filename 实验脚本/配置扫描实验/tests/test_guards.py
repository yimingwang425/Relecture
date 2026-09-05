"""三层指纹守卫 + 判读规则。**每条守卫都要能被反向验证变红。**

通则七 #4：把被测逻辑换成平凡实现，断言必须红；不会红的断言是空的。
本文件里凡是名字带 `would_` 的，都是那个反向验证。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import scan  # noqa: F401  — 注入路径
from scan.points import AXIS_POINTS, POINTS, point_by_id
from scan.readout import band_of, monotone_verdict, segmentation_verdict
from scan.smoke import run_smoke
from scan.verify_scan import verify_scan_run


@pytest.fixture(scope="module")
def smoke_run(tmp_path_factory) -> Path:
    return run_smoke(tmp_path_factory.mktemp("smoke"))


def _rewrite(run_dir: Path, target: Path, mutate) -> Path:
    """把一个批次复制到新目录并改坏它，用于反向验证。"""
    target.mkdir(parents=True, exist_ok=True)
    (target / "transcripts").mkdir(exist_ok=True)
    meta = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    rounds = [
        json.loads(l)
        for l in (run_dir / "rounds.jsonl").read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    meta, rounds = mutate(meta, rounds)
    (target / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    (target / "rounds.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rounds), encoding="utf-8"
    )
    return target


class TestConfigPoints:
    def test_five_fingerprints_one_metric_set(self):
        """配置扫描的核心性质：**指纹逐点不同，指标集全程恒定**。

        没有前者，扫描没有自变量；没有后者，五个点画在同一张图上不合法。
        """
        dicts = [p.to_dict() for p in POINTS]
        assert len({d["config_fingerprint"] for d in dicts}) == len(POINTS)
        assert len({d["metric_set_id"] for d in dicts}) == 1

    def test_all_points_are_v1_three_dimensions(self):
        for p in POINTS:
            activation = p.config().describe_activation()
            assert activation["term_annotation"] is False, p.id
            assert activation["chinese_ratio"] is True, p.id

    def test_axis_is_monotonically_tightening(self):
        """P0→P3 的区间必须真的越来越紧，否则「单调性」主判据无从谈起。"""
        widths = [
            p.config().paragraph_length.max - p.config().paragraph_length.min
            for p in AXIS_POINTS
        ]
        assert widths == sorted(widths, reverse=True)

    def test_natural_centre_leaves_the_interval_at_p3(self):
        """偏离度的定义：模型自然中枢（第 0 周实测 ≈55 字）离区间边界多远。

        P0 把它放在区间中部，P3 把它放在区间**外**。这条钉住那个定义——
        否则「强偏离」只是一个形容词。
        """
        natural = 55
        p0 = point_by_id("P0").config().paragraph_length
        p3 = point_by_id("P3").config().paragraph_length
        assert p0.contains(natural) and not p3.contains(natural)

    def test_quote_limit_is_never_an_independent_variable(self):
        """引用长度来自 §12.1 的版权约束，不是风格偏好（D16）。

        把它当自变量扫，测的是产品里不存在的配置。
        """
        assert {p.config().max_quote_chars for p in POINTS} == {100}

    def test_p4_is_off_axis(self):
        assert point_by_id("P4").axis_order is None
        assert point_by_id("P4") not in AXIS_POINTS


class TestVerifyScan:
    def test_smoke_batch_passes(self, smoke_run):
        ok, lines = verify_scan_run(smoke_run)
        assert ok, "\n".join(lines)

    def test_would_catch_mid_trajectory_config_change(self, smoke_run, tmp_path):
        """层一反向验证：轨迹中途换配置。"""
        def mutate(meta, rounds):
            rounds[3]["config_fingerprint"] = "deadbeefcafe"
            return meta, rounds

        ok, lines = verify_scan_run(_rewrite(smoke_run, tmp_path / "a", mutate))
        assert not ok and any("点内指纹不唯一" in l for l in lines)

    def test_would_catch_duplicate_points(self, smoke_run, tmp_path):
        """层一附加：两个点写成了同一份配置。

        它不会报错，只会让曲线上多出一个看起来独立的点。
        """
        def mutate(meta, rounds):
            fp = next(r["config_fingerprint"] for r in rounds if r["config_point_id"] == "P0")
            for r in rounds:
                if r["config_point_id"] == "P1":
                    r["config_fingerprint"] = fp
            return meta, rounds

        ok, lines = verify_scan_run(_rewrite(smoke_run, tmp_path / "b", mutate))
        assert not ok and any("指纹相同" in l for l in lines)

    def test_would_catch_mixed_metric_sets(self, smoke_run, tmp_path):
        """层二反向验证：两个指标集混在一批里。"""
        def mutate(meta, rounds):
            rounds[0]["metric_set_id"] = "0123456789ab"
            return meta, rounds

        ok, lines = verify_scan_run(_rewrite(smoke_run, tmp_path / "c", mutate))
        assert not ok and any("点间指标集不一致" in l for l in lines)

    def test_would_catch_historical_four_dimension_data(self, smoke_run, tmp_path):
        """层三反向验证：D02/D44 的四维批次混进来。

        **这是判断一要防的那件事**：新批次与历史数据被误合并。
        """
        def mutate(meta, rounds):
            rounds[0]["config_version"] = "1.1"
            return meta, rounds

        ok, lines = verify_scan_run(_rewrite(smoke_run, tmp_path / "d", mutate))
        assert not ok and any("历史口径" in l for l in lines)

    def test_would_catch_term_dimension_sneaking_back(self, smoke_run, tmp_path):
        def mutate(meta, rounds):
            rounds[0]["final_evaluation"]["counted_dimensions"] = [
                "chinese_ratio", "paragraph_length", "quote_length", "term_annotation",
            ]
            return meta, rounds

        ok, lines = verify_scan_run(_rewrite(smoke_run, tmp_path / "e", mutate))
        assert not ok and any("不是 v1 三维" in l for l in lines)

    def test_would_catch_diverging_retrieval(self, smoke_run, tmp_path):
        """检索同一性反向验证：两组拿到了不同的片。

        第 0 周为了避开这件事干脆不含检索。本轮靠「共用一份 ContextPack」
        从结构上排除它，这条守卫证明排除是真的。
        """
        def mutate(meta, rounds):
            for r in rounds:
                if r["condition"] == "without_review":
                    r["retrieval"]["signature"] = "ffffffffffffffff"
                    break
            return meta, rounds

        ok, lines = verify_scan_run(_rewrite(smoke_run, tmp_path / "f", mutate))
        assert not ok and any("检索结果不同" in l for l in lines)

    def test_would_catch_question_drift(self, smoke_run, tmp_path):
        def mutate(meta, rounds):
            for r in rounds:
                if r["condition"] == "without_review":
                    r["question"] = "换了一个提问"
                    break
            return meta, rounds

        ok, lines = verify_scan_run(_rewrite(smoke_run, tmp_path / "g", mutate))
        assert not ok and any("提问不一致" in l for l in lines)

    def test_arm_is_part_of_the_grouping_key(self, smoke_run):
        """两条臂都跑 P0 但提问序列不同。

        守卫若只按 (点, 轮) 分组，会报出一个并不存在的分歧——
        假跑第一次运行就撞上了这个，故钉住它。
        """
        ok, lines = verify_scan_run(smoke_run)
        assert ok and any("(臂, 点, 轮)" in l for l in lines)


class TestReadout:
    def test_bands(self):
        assert band_of(0.0) == "近零"
        assert band_of(1 / 12) == "近零"
        assert band_of(3 / 12) == "不可分辨"
        assert band_of(6 / 12) == "高"
        assert band_of(None) == "无数据"

    def test_boundary_values_land_in_the_decisive_band(self):
        """边界值必须归入**有结论**的那一带。

        判读带写的是「≤1/12 判近零、≥6/12 判高」。若边界掉进「不可分辨」，
        一个本该有结论的点会变成没结论——而且不报错。
        """
        assert band_of(1 / 12) != "不可分辨"
        assert band_of(6 / 12) != "不可分辨"

    def test_single_trajectory_is_never_evidence(self):
        """R17：单格差异不算证据，无论大小。换种子仍是同一格。"""
        r = monotone_verdict([[0.0, 0.0, 0.5, 1.0]], ["P0", "P1", "P2", "P3"])
        assert r.verdict == "证据不足"

    def test_two_agreeing_trajectories_are_evidence(self):
        r = monotone_verdict([[0.0, 0.0, 0.5, 1.0], [0.0, 0.1, 0.4, 0.9]], list("abcd"))
        assert r.verdict == "单调不减，≥2 轨同向"

    def test_non_monotone_is_not_read_as_no_value(self):
        """「没测出配置依赖」≠「审查没用」。两者在报告里必须分开写。"""
        r = monotone_verdict([[0.5, 0.0, 0.5, 0.0], [0.4, 0.1, 0.0, 0.3]], list("abcd"))
        assert r.verdict == "未见同向单调"
        assert "不等于" in r.detail

    def test_segmentation_evasion_is_detected(self):
        """E10：总字数不变、段数翻倍 → 达标是靠分段实现的。"""
        r = segmentation_verdict(
            {"mean_total_chars": 800, "mean_paragraph_count": 6},
            {"mean_total_chars": 790, "mean_paragraph_count": 24},
        )
        assert r.verdict == "达标靠分段实现"
        assert "总输出长度" in r.detail

    def test_genuine_conciseness_is_distinguished(self):
        r = segmentation_verdict(
            {"mean_total_chars": 800, "mean_paragraph_count": 6},
            {"mean_total_chars": 400, "mean_paragraph_count": 8},
        )
        assert r.verdict == "确实讲得更简洁"

    def test_would_not_confuse_the_two(self):
        """反向验证：两种形态必须给出**不同**结论。

        若判据对两者给同一个答案，它就没在区分任何东西。
        """
        evasion = segmentation_verdict(
            {"mean_total_chars": 800, "mean_paragraph_count": 6},
            {"mean_total_chars": 790, "mean_paragraph_count": 24},
        )
        concise = segmentation_verdict(
            {"mean_total_chars": 800, "mean_paragraph_count": 6},
            {"mean_total_chars": 400, "mean_paragraph_count": 8},
        )
        assert evasion.verdict != concise.verdict


class TestSanity:
    def test_points_are_distinguishable_in_the_request(self):
        """口径 X05：配置必须同时驱动 prompt，不能只驱动判定。"""
        from explain.fake import FakeExplainModel

        from scan.sanity import check_points_distinguishable
        from scan.smoke import make_smoke_retriever

        r = check_points_distinguishable(
            FakeExplainModel(), make_smoke_retriever(), "问题", "22.081J"
        )
        assert r.passed and len(set(r.details["signatures"].values())) == len(POINTS)

    def test_two_conditions_start_identical(self):
        from explain.fake import FakeExplainModel

        from scan.sanity import check_inputs_identical
        from scan.smoke import make_smoke_retriever

        assert check_inputs_identical(
            FakeExplainModel(), make_smoke_retriever(), "问题", "22.081J"
        ).passed

    def test_system_is_measured_not_assumed_cacheable(self):
        """**接检索后 system 撑不到最小可缓存长度。**

        这条是实测，不是推测——它决定报告里那句话怎么写，
        并且直接指向 PRD §7.3 的论断前提在新架构下是否还成立。
        """
        from explain.fake import FakeExplainModel

        from scan.sanity import check_inputs_identical
        from scan.smoke import make_smoke_retriever

        r = check_inputs_identical(
            FakeExplainModel(), make_smoke_retriever(), "问题", "22.081J"
        )
        assert r.details["system_cache"]["system_cache_expected_effective"] is False


class TestCalipers:
    """P4 的三口径并列重算。**必须用本批次的 transcripts。**"""

    TEXT_WITH = "热机（heat engine）驱动涡轮机（turbine）。" * 6
    TEXT_WITHOUT = "热机驱动涡轮机，把热能转成机械能，效率受卡诺极限约束。" * 6

    def test_three_calipers_are_ordered_by_permissiveness(self):
        """形态规则最宽松、无排除最严——顺序必须稳定。

        顺序若不稳定，「B 会让 P4 触发率偏低」这句话就无从谈起。
        """
        from scan.calipers import recompute_calipers

        rows = recompute_calipers([self.TEXT_WITH], threshold=0.95)
        by = {r.caliper.split("（")[0].strip(): r.mean_ratio for r in rows}
        assert by["无排除"] <= by["形态规则 B"]

    def test_no_annotations_collapses_the_difference(self):
        """**这条是那个话题会不会自己关掉的判据。**

        v1 的 prompt 里没有任何一句让模型写括注。若模型因此不写，
        三种口径给出同一个数——P4 根本不口径敏感。
        """
        from scan.calipers import recompute_calipers

        rows = recompute_calipers([self.TEXT_WITHOUT], threshold=0.95)
        assert len({round(r.mean_ratio, 6) for r in rows}) == 1

    def test_verdict_line_states_which_case_it_is(self):
        from scan.calipers import format_calipers, recompute_calipers

        rows = recompute_calipers([self.TEXT_WITHOUT], threshold=0.95)
        out = format_calipers(rows, {"total": 0, "mean": 0.0, "rounds": 1, "rounds_with": 0})
        assert "不口径敏感" in out and "D02 期 prompt" in out

    def test_annotation_count_and_exclusion_agree(self):
        """`annotation_count` 与 D48 的排除必须共用匹配器。

        不共用会出现「排除了 N 处、却报告了 M 次」的自相矛盾日志，
        而两个数都看起来正常——没人会去比对它们。
        """
        from explain.observe import observe

        o = observe(self.TEXT_WITH)
        assert o.annotation_count == 12
        assert observe(self.TEXT_WITHOUT).annotation_count == 0


class TestDecomposition:
    """`字/段 = 总字数 ÷ 段数` 的对数分解。**恒等式，不是判据。**"""

    BASE = {"mean_total_chars": 444.0, "mean_paragraph_count": 5.5}

    def test_reproduces_the_stage1_p3_split(self):
        """阶段 1 实测：P3 的达标约四分之三来自分段、四分之一来自真的讲短。"""
        from scan.readout import decompose_paragraph_drop

        d = decompose_paragraph_drop(
            self.BASE, {"P3": {"mean_total_chars": 353.0, "mean_paragraph_count": 11.5}}
        )[0]
        assert 0.70 <= d.split_share <= 0.80
        assert 0.20 <= d.shrink_share <= 0.30
        assert d.shrink_share + d.split_share == pytest.approx(1.0)

    def test_pure_splitting_is_all_split(self):
        """总字数不变、段数翻倍 → 100% 来自分段。"""
        from scan.readout import decompose_paragraph_drop

        d = decompose_paragraph_drop(
            self.BASE, {"X": {"mean_total_chars": 444.0, "mean_paragraph_count": 11.0}}
        )[0]
        assert d.split_share == pytest.approx(1.0)

    def test_pure_shrinking_is_all_shrink(self):
        from scan.readout import decompose_paragraph_drop

        d = decompose_paragraph_drop(
            self.BASE, {"X": {"mean_total_chars": 222.0, "mean_paragraph_count": 5.5}}
        )[0]
        assert d.shrink_share == pytest.approx(1.0)

    def test_identity_holds(self):
        """分解的前提是恒等式本身成立：字/段之比 == 总字数之比 ÷ 段数之比。

        **这条是分解为什么不需要阈值的全部理由。** 它一旦不成立，
        「四分之三 / 四分之一」就变成了一个有自由参数的估计。
        """
        from scan.readout import decompose_paragraph_drop

        for point in ({"mean_total_chars": 353.0, "mean_paragraph_count": 11.5},
                      {"mean_total_chars": 600.0, "mean_paragraph_count": 4.0}):
            d = decompose_paragraph_drop(self.BASE, {"X": point})[0]
            lhs = (point["mean_total_chars"] / point["mean_paragraph_count"]) / (
                self.BASE["mean_total_chars"] / self.BASE["mean_paragraph_count"]
            )
            assert d.per_para_ratio == pytest.approx(lhs)
            assert d.per_para_ratio == pytest.approx(d.chars_ratio / d.paras_ratio)

    def test_baseline_point_has_no_decomposition(self):
        # 基点相对自己无可分解——报 None 而不是编一个 50/50
        from scan.readout import decompose_paragraph_drop

        d = decompose_paragraph_drop(self.BASE, {"P0": dict(self.BASE)})[0]
        assert d.shrink_share is None and "基点" in d.formula()

    def test_decomposition_does_not_change_the_e10_verdict(self):
        """分解**不是新判据**：E10 的三分类不受它影响。"""
        from scan.readout import segmentation_verdict

        before = segmentation_verdict(
            self.BASE, {"mean_total_chars": 353.0, "mean_paragraph_count": 11.5}
        )
        assert before.verdict == "确实讲得更简洁"  # 阈值技术性的结果，保留原样

    def test_sign_mixed_row_does_not_claim_shortening(self):
        """总字数**上升**时，不得报「真的讲短 N%」。

        阶段 1 的 P1 就是这个形态（总字数比 1.058）：模型写得更多，
        字/段的下降全部来自分段。份额按绝对值仍算得出来，但那个标签会被读成
        「讲短了 25%」——**与事实相反**。
        """
        from scan.readout import decompose_paragraph_drop

        d = decompose_paragraph_drop(
            self.BASE, {"P1": {"mean_total_chars": 470.0, "mean_paragraph_count": 6.5}}
        )[0]
        assert d.sign_mixed is True
        assert "真的讲短" not in d.formula()
        assert "总字数反而上升" in d.formula()


class TestCrossBatchMerge:
    """跨批次合并。**`metric_set_id` 唯一真正被用上的场合。**

    阶段 1 与阶段 2 是两个批次；合并画在同一张图上之前必须证明它们是同一个指标集。
    历史的 D02/D44 四维批次会在这里被挡住——那正是判断一要防的事。
    """

    def test_replicate_offset_keeps_trajectories_distinct(self):
        """阶段 2 的轨迹编号必须从 2 开始。

        两个批次都出现 `replicate=1`，合并时两条**独立**轨迹会被当成同一条——
        而「重复是整条轨迹的重复」这条纪律正是靠编号区分的。
        主判据「≥2 条轨迹同向」会因此永远只看到 1 条。
        """
        from scan.live import stage1_config, stage2_config

        s1 = {a.name: a for a in stage1_config().arms}
        s2 = {a.name: a for a in stage2_config().arms}
        assert s1["scan"].replicate_offset == 0
        assert s2["scan"].replicate_offset == 1

    def test_stage2_adds_the_p3_cost_arm(self):
        """停止规则 3b：P0=0.000 vs P3=0.500，条件已触发。"""
        from scan.live import stage2_config

        arms = {a.name: a for a in stage2_config().arms}
        assert arms["cost_p3"].point_ids == ("P3",)
        assert len(arms["cost_p3"].questions) == 15

    def test_p3_cost_arm_uses_the_same_questions_as_p0(self):
        """两条成本臂必须同序列，否则不可比——差异会跑到提问上。"""
        from scan.live import stage1_config, stage2_config

        p0 = {a.name: a for a in stage1_config().arms}["cost"].questions
        p3 = {a.name: a for a in stage2_config().arms}["cost_p3"].questions
        assert [q["question"] for q in p0] == [q["question"] for q in p3]

    def test_merge_rejects_mixed_metric_sets(self, tmp_path):
        """**反向验证**：混入另一个指标集必须拒绝合并，而不是画在一起。"""
        import json

        from scan.__main__ import _merge

        a, b = tmp_path / "a", tmp_path / "b"
        for d, msid in ((a, "111111111111"), (b, "222222222222")):
            d.mkdir()
            (d / "rounds.jsonl").write_text(
                json.dumps({"metric_set_id": msid, "config_version": "2.0"}), encoding="utf-8"
            )
        with pytest.raises(SystemExit):
            _merge([a, b])

    def test_merge_accepts_same_metric_set(self, tmp_path):
        import json

        from scan.__main__ import _merge

        a, b = tmp_path / "a", tmp_path / "b"
        for d in (a, b):
            d.mkdir()
            (d / "rounds.jsonl").write_text(
                json.dumps({"metric_set_id": "111111111111", "config_version": "2.0"}),
                encoding="utf-8",
            )
        assert len(_merge([a, b])) == 2


class TestCrashSafety:
    """一次中途失败不该让**已经付过钱的数据**变成不可核验的废数据。

    实测触发（2026-08-23）：阶段 2 跑到第 12 轮时 API 余额耗尽，
    11 轮已落盘、$0.1396 已付，而 `metadata.json` 只在跑完时才写——
    `verify` 的第一句话是「缺少 metadata.json」。
    """

    def test_metadata_exists_and_is_marked_complete(self, smoke_run):
        import json

        meta = json.loads((smoke_run / "metadata.json").read_text(encoding="utf-8"))
        assert meta["status"] == "complete"

    def test_metadata_is_written_before_the_first_round(self, tmp_path):
        """开跑前就要有 metadata——否则第一轮就崩的批次连边界声明都没有。"""
        import json

        from explain.retrieval_adapter import InMemoryRetriever

        from scan.runner import Arm, ScanConfig, run_scan

        seen = {}

        class Boom:
            name = "fake:boom"
            params: dict = {}

            def complete(self, system, messages):
                seen["meta_existed"] = (tmp_path / "metadata.json").is_file()
                raise RuntimeError("credit balance is too low")

            def request_signature(self, system, messages):
                return "x"

        cfg = ScanConfig(
            course_label="t",
            arms=(Arm("scan", ("P0",), ({"question": "q"},), 1, "t"),),
        )
        with pytest.raises(RuntimeError):
            run_scan(scan=cfg, retriever=InMemoryRetriever(), make_model=lambda *a: Boom(),
                     out_dir=tmp_path)
        assert seen["meta_existed"] is True
        meta = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
        assert meta["status"] == "aborted"

    def test_resume_skips_only_complete_trajectories(self, smoke_run):
        """**只认整条轨迹，不认单轮。**

        轨迹是有状态的：第 k 轮的输入含前 k−1 轮历史。从中途接续要把历史
        精确重建出来，一处细微不符就会让两组输入不再逐字节相同——
        而那是本实验唯一要保住的东西。半条轨迹重跑几分钱，接错整批作废。
        """
        from scan.runner import Arm, completed_trajectories

        arms = (Arm("scan", ("P0",), tuple({"question": f"q{i}"} for i in range(6)), 2, ""),)
        done = completed_trajectories(smoke_run, arms)
        assert ("scan", "P0", "with_review", 1) in done
        # 轮数不足的臂不会被误判为完成
        longer = (Arm("scan", ("P0",), tuple({"question": f"q{i}"} for i in range(99)), 2, ""),)
        assert completed_trajectories(smoke_run, longer) == set()


class TestIncompleteVsDisagreeing:
    """**数据缺失 ≠ 方向不一致。** 两者在报告里的含义完全相反。

    实测触发（2026-08-23）：阶段 2 中断后只有 P0 有第 2 轨，
    合并判读把「第 2 轨没跑完」读成了「未见同向单调」——
    那听起来像一个实质结论，实际只是数据没跑完。
    前者要补数据，后者要改假设。
    """

    def test_incomplete_trajectory_is_not_counted_as_disagreement(self):
        from scan.readout import monotone_verdict

        r = monotone_verdict(
            [[0.0, 0.33, 0.33, 0.5], [0.0, None, None, None]], list("abcd")
        )
        assert r.verdict == "证据不足"
        assert "数据缺失不等于方向不一致" in r.detail
        assert r.evidence["incomplete_trajectories"] == 1
        assert r.evidence["complete_trajectories"] == 1

    def test_two_complete_disagreeing_trajectories_still_read_as_disagreement(self):
        """**反向验证**：两条都跑完却不同向时，仍要判「未见同向单调」。

        不区分的话这条修正会走到另一个极端——把真正的反面证据也吞掉。
        """
        from scan.readout import monotone_verdict

        r = monotone_verdict([[0.5, 0.0, 0.5, 0.0], [0.4, 0.1, 0.0, 0.3]], list("abcd"))
        assert r.verdict == "未见同向单调"
        assert r.evidence["incomplete_trajectories"] == 0


class TestResumePruning:
    """续跑时，**不完整轨迹的残留轮次必须先删掉**。

    以追加模式续写时，中断那条轨迹已落盘的前几轮仍留在文件里，
    而它会被整条重跑。不清理的话同一条轨迹下出现两套轮次——
    报告按轮次求均值，**多出来的几轮不会报错，只会让数字偏移**。
    """

    def test_partial_trajectory_records_are_dropped(self, tmp_path):
        import json

        from scan.runner import _prune_incomplete

        path = tmp_path / "rounds.jsonl"
        rows = [
            {"arm": "scan", "config_point_id": "P0", "condition": "with_review",
             "replicate": 2, "round": i}
            for i in range(1, 7)
        ] + [
            {"arm": "scan", "config_point_id": "P0", "condition": "without_review",
             "replicate": 2, "round": i}
            for i in range(1, 6)  # 只跑了 5/6，中断
        ]
        path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

        dropped = _prune_incomplete(path, {("scan", "P0", "with_review", 2)})
        assert dropped == 5
        left = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(left) == 6
        assert {r["condition"] for r in left} == {"with_review"}

    def test_complete_trajectories_survive_untouched(self, tmp_path):
        import json

        from scan.runner import _prune_incomplete

        path = tmp_path / "rounds.jsonl"
        rows = [{"arm": "scan", "config_point_id": "P0", "condition": "with_review",
                 "replicate": 2, "round": i} for i in range(1, 7)]
        path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        assert _prune_incomplete(path, {("scan", "P0", "with_review", 2)}) == 0
        assert len(path.read_text(encoding="utf-8").strip().splitlines()) == 6

    def test_resume_would_otherwise_double_count(self, tmp_path):
        """**反向验证**：不清理会怎样。

        这条演示的是被防住的那个后果——同一条轨迹 11 轮而不是 6 轮，
        而报告只会安静地按 11 轮求均值。
        """
        import json

        from scan.runner import _prune_incomplete

        path = tmp_path / "rounds.jsonl"
        rows = [{"arm": "scan", "config_point_id": "P0", "condition": "without_review",
                 "replicate": 2, "round": i} for i in range(1, 6)]
        rows += [{"arm": "scan", "config_point_id": "P0", "condition": "without_review",
                  "replicate": 2, "round": i} for i in range(1, 7)]  # 重跑追加
        path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        assert len(rows) == 11  # 不清理的话报告看到的就是这 11 轮
        _prune_incomplete(path, set())
        assert path.read_text(encoding="utf-8").strip() == ""


class TestRestrictedPages:
    """口径 G06 · 受限页必须排除。**合规问题，且失败是静默的。**

    原实现漏了 `RESTRICTED_PAGES`：22.081J 没有受限页所以不咬人，
    14.129 有 8 页第三方保留权利内容，不排除的话它们照样进 prompt，
    **没有任何报错**。
    """

    BANNED = frozenset({10, 11, 25, 26, 31, 38, 40, 42})

    def test_14129_index_excludes_the_eight_pages(self):
        from scan.live import build_retriever

        r = build_retriever(course="MIT14_129S25_lec02.pdf")
        pages = {p for it in r.index.items for p in it.page_indices}
        assert not (pages & self.BANNED), f"受限页泄漏：{sorted(pages & self.BANNED)}"

    def test_page_numbers_are_not_copied_locally(self):
        """页号只能有一处出处。抄一份就多一个会漂的副本。

        那张清单以 `测试集/SOURCES.md` 为准、**不靠正则重新推导**
        （p31 的版权声明被解析切在两个块里，正则只搜得到 7 页）。
        """
        from pathlib import Path

        src = Path(__file__).resolve().parents[1] / "scan" / "live.py"
        text = src.read_text(encoding="utf-8")
        assert "RESTRICTED_PAGES" in text
        for page in self.BANNED:
            assert f"{page}," not in text.replace("MIT14_129S25", ""), "疑似抄了页号字面量"

    def test_14129_skipped_pages_reach_the_context(self):
        """`image_only` 通道**第一次有真实数据流过**。

        F3a 限制段第 7 条：22.081J 39 页全可读，这条通道一次没被演练过。
        """
        from scan.live import build_retriever

        r = build_retriever(course="MIT14_129S25_lec02.pdf")
        skipped = {p.index for p in r._skipped}
        assert skipped == {33, 34, 35, 36}
        assert all(p.kind == "image_only" for p in r._skipped)

    def test_22081j_still_has_no_skipped_pages(self):
        # 对照：这门课的跳过页清单是空的，G03 的限定语不该出现
        from scan.live import build_retriever

        assert build_retriever()._skipped == ()
