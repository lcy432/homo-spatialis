"""E1-6B 收尾（DECISIONS/0029 第八节，审核 R1–R3）：标准开局 v1、冻结前的完整跨城评价配置检查、敏感性证据配对，以及硬要求
核验人数与估值说明文字。

这些检查保护的是"城市差异"这句话本身：同一个人从同样的起点出发（R1），同名的参考生活在两座城真是同一类东西（R2），说"只换了
评价假设"时两边确实是同一批生活（R3）。它们不证明合成参数在现实中正确，也不让城市比较变成真实推荐。
"""

from __future__ import annotations

from collections.abc import Callable
from fractions import Fraction
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from common.life_aggregation_contracts import (
    AGGREGATION_REVISION,
    START_CONDITION_RULE,
    FrozenRoster,
)
from common.life_contracts import HeldAbility, Holding, LifeContractError, TrainingProgress
from econ.life_aggregation import aggregate_arm, freeze_roster, sensitivity
from ledger.life_catalog import load_life_bundle
from ledger.life_roster import (
    PERSONAL_CONDITIONS,
    common_start_digest,
    load_roster,
    roster_problems,
    standard_start_problems,
)

from .aggregation_support import A95, B96, ROSTER, evaluate, live
from .evaluation_support import evaluate as evaluate_ev
from .settle_support import revise, with_items

MAIN, D099, BASIC = "arm.main", "arm.delta099", "arm.basic_only"
EVAL_CONFIGS = {
    MAIN: "configs/life/evaluation_e1_6b.yaml",
    D099: "configs/life/evaluation_e1_6b_delta099.yaml",
    BASIC: "configs/life/evaluation_e1_6b_basic_only.yaml",
}


def x(a) -> Fraction:
    assert a is not None
    return a.fraction()


def _states(bundle, change: Callable, clones: set[str] | None = None):
    """改几个分身的开局（重新走一遍校验）；``clones`` 为空时改 α（u101）的全部分身。"""
    out = []
    for s in bundle.initial_states:
        hit = s.clone_id in clones if clones is not None else s.person_id == "u101"
        out.append(change(s) if hit else s)
    return revise(bundle, initial_states=out)


def _variant(tmp_path: Path, mutate_life=None, mutate_spec=None) -> Path:
    """在临时目录写一份改过的生活输入（与评价规格），以及指向它们的名单；返回名单路径。只改副本，不改仓库里的配置。"""
    roster_raw = yaml.safe_load(ROSTER.read_text(encoding="utf-8"))
    life = yaml.safe_load(Path(roster_raw["roster"]["life_input"]).read_text(encoding="utf-8"))
    if mutate_life is not None:
        mutate_life(life)
    (tmp_path / "life.yaml").write_text(yaml.safe_dump(life, allow_unicode=True), encoding="utf-8")
    roster_raw["roster"]["life_input"] = str(tmp_path / "life.yaml")
    if mutate_spec is not None:
        for arm in roster_raw["roster"]["arms"]:
            spec = yaml.safe_load(Path(arm["evaluation_config"]).read_text(encoding="utf-8"))
            mutate_spec(arm["arm_id"], spec)
            out = tmp_path / f"{arm['arm_id']}.yaml"
            out.write_text(yaml.safe_dump(spec, allow_unicode=True), encoding="utf-8")
            arm["evaluation_config"] = str(out)
    path = tmp_path / "roster.yaml"
    path.write_text(yaml.safe_dump(roster_raw, allow_unicode=True), encoding="utf-8")
    return path


# ---------------------------------------------------------------------- A. 标准开局 v1（R1）


def test_the_standard_sample_freezes_and_records_which_check_it_passed(batch_eb):
    config, bundle, scoring, specs = load_roster(ROSTER)
    assert roster_problems(config, bundle, scoring, specs) == []
    roster = freeze_roster(config, bundle, scoring, specs)
    assert roster == batch_eb.roster
    assert (roster.start_condition_rule, roster.aggregation_revision) == (
        START_CONDITION_RULE,
        AGGREGATION_REVISION,
    )
    report = aggregate_arm(roster, MAIN, batch_eb.evaluations[MAIN])
    assert (report.start_condition_rule, report.aggregation_revision) == (
        START_CONDITION_RULE,
        AGGREGATION_REVISION,
    )
    # 合法差异：分身编号、城市、重复分身序号、开局声明的当地岗位（城市专属编号）都不同，没有被拦
    a, b = bundle.initial_state("u101.a95.r0"), bundle.initial_state("u101.b96.r3")
    assert str(a.city) != str(b.city) and a.health.repeat_index != b.health.repeat_index
    assert a.employment.current_position_ids != b.employment.current_position_ids
    assert common_start_digest(a) == common_start_digest(b)
    assert standard_start_problems(a) == standard_start_problems(b) == []
    assert set(common_start_digest(a)) == set(PERSONAL_CONDITIONS)


def test_old_frozen_rosters_are_not_relabelled_as_checked(batch_eb):
    """旧的冻结名单没有开局检查与执行修订的身份：按新契约读入就拒绝，不补贴"已通过新检查"的标签。"""
    data = batch_eb.roster.model_dump(mode="json")
    for key in ("start_condition_rule", "aggregation_revision"):
        old = {k: v for k, v in data.items() if k != key}
        with pytest.raises(ValidationError):
            FrozenRoster.model_validate(old)


def test_an_ill_start_is_refused_by_the_loader_and_by_direct_freezing(tmp_path, batch_eb):
    rules, source = batch_eb.bundle.protocol.health_rules, batch_eb.bundle.protocol.illness_events

    def ill(raw):
        s = next(s for s in raw["initial_states"] if s["clone_id"] == "u101.b96.r0")
        s["health"]["episode"] = {
            "episode_id": "initial.ill",
            "severity": "major",
            "source": "injected",
            "onset_year": 1,
            "onset_month": 1,
            "months_ill": 1,
        }
        s["health"]["binding"] = {
            "rules_version": rules.version,
            "rules_fingerprint": rules.content_fingerprint,
            "source_fingerprint": source.content_fingerprint,
        }

    path = _variant(tmp_path, mutate_life=ill)
    pattern = r"u101\.b96\.r0: 标准开局 v1_standard_start_2026_10_12 不接受——health\.episode：开局正在生病（major"
    with pytest.raises(LifeContractError, match=pattern):
        load_roster(path)
    config, _, scoring, specs = load_roster(ROSTER)
    sick_bundle, _ = load_life_bundle(tmp_path / "life.yaml", require_settlement_ready=True)
    with pytest.raises(LifeContractError, match=pattern):
        freeze_roster(config, sick_bundle, scoring, specs)


def test_starting_holdings_are_outside_this_version_even_when_identical(batch_eb):
    """本版不接受开局持有物：哪怕两城都是同一台洗烘一体机也拒绝——这是缩小支持范围，不是判断了两件物品等价不等价。"""
    config, bundle, scoring, specs = load_roster(ROSTER)

    def washer(s):
        code = str(s.city).rsplit(":", 1)[-1].lower()
        held = Holding(
            opportunity_id=f"{code}.durable.washer_dryer",
            quantity=1,
            acquired_year=0,
            remaining_months=60,
        )
        return revise(s, holdings=[held])

    with_washers = _states(bundle, washer)
    problems = roster_problems(config, with_washers, scoring, specs)
    assert len([p for p in problems if "holdings：开局已有持有物" in p]) == 10  # α 的 10 个分身
    assert all("不接受开局持有物" in p for p in problems if "holdings" in p)
    with pytest.raises(LifeContractError, match="不接受开局持有物"):
        freeze_roster(config, with_washers, scoring, specs)


@pytest.mark.parametrize("field", ["training", "experience_summary"])
def test_other_existing_life_records_are_refused(field, batch_eb):
    config, bundle, scoring, specs = load_roster(ROSTER)
    if field == "training":

        def change(s):
            code = str(s.city).rsplit(":", 1)[-1].lower()
            progress = TrainingProgress(
                opportunity_id=f"{code}.training.accounting_course",
                months_participated=3,
                months_required=12,
                fee_paid=True,
                enrolled_year=1,
                enrolled_month=1,
            )
            return revise(s, training=[progress])

        expect = "training：开局已有培训进度"
    else:

        def change(s):
            return revise(s, experience_summary=["去年在这座城住过一年"])

        expect = "experience_summary：开局已有 1 条经历摘要"
    changed = _states(bundle, change, {"u101.b96.r2"})
    problems = roster_problems(config, changed, scoring, specs)
    assert [p for p in problems if expect in p] and all("u101.b96.r2" in p for p in problems)
    with pytest.raises(LifeContractError, match=expect):
        freeze_roster(config, changed, scoring, specs)


def test_a_historical_money_goal_is_refused(bundle_sv, scoring_sv):
    """E1-5B 样例第 1 年设定资金目标之后的第 2 年年初状态：除了"不是第 1 年"，资金目标本身也被逐项报出。（第 1 年年初的状态按
    契约本来就不可能带着当年的目标事件——加载时就拒绝；这条检查是城市比较入口的明确说明，不是唯一的防线。）"""
    first, *_ = live(bundle_sv, scoring_sv, "u81.a81.save")
    state = first.next_state
    assert state.year_index == 2 and state.savings_goals
    problems = standard_start_problems(state)
    assert any(p.startswith("year_index / status：第 2 年") for p in problems)
    assert any(p.startswith("savings_goals：开局已有结构化资金目标（dryer）") for p in problems)


@pytest.mark.parametrize(
    ("label", "change"),
    [
        ("开局现金", lambda s: revise(s, cash={"amount": 2500, "numeraire": "USD_2024_LOCAL"})),
        ("年龄", lambda s: revise(s, health=revise(s.health, age_years=36))),
        (
            "开局能力",
            lambda s: revise(
                s,
                abilities=[
                    HeldAbility(ability_id="accounting_cert", source="initial", usable_from_year=1)
                ],
            ),
        ),
        ("文字目标", lambda s: revise(s, goals=["攒钱买房"])),
    ],
)
def test_personal_conditions_must_match_field_by_field(label, change, batch_eb):
    config, bundle, scoring, specs = load_roster(ROSTER)
    changed = _states(bundle, change, {"u101.b96.r1"})
    problems = roster_problems(config, changed, scoring, specs)
    hits = [p for p in problems if f"「{label}」" in p]
    assert len(hits) == 1 and "u101.b96.r1" in hits[0] and "user.alpha" in hits[0]
    with pytest.raises(LifeContractError, match=f"「{label}」"):
        freeze_roster(config, changed, scoring, specs)


def test_the_check_does_not_touch_its_inputs(batch_eb):
    config, bundle, scoring, specs = load_roster(ROSTER)
    bad = _states(
        bundle,
        lambda s: revise(
            s, experience_summary=["已有记录"], cash={"amount": 1, "numeraire": "USD_2024_LOCAL"}
        ),
        {"u101.b96.r0"},
    )
    before = (
        config.model_dump_json(),
        bad.model_dump_json(),
        {k: v.model_dump_json() for k, v in specs.items()},
    )
    assert roster_problems(config, bad, scoring, specs)
    with pytest.raises(LifeContractError):
        freeze_roster(config, bad, scoring, specs)
    after = (
        config.model_dump_json(),
        bad.model_dump_json(),
        {k: v.model_dump_json() for k, v in specs.items()},
    )
    assert before == after  # 不补齐、不清空、不重新抽样，也不删人
    assert len(config.entries) == 14


def test_life_after_the_start_still_runs(
    tmp_path, batch_eb, lives_ev, bundle_ev, scoring_ev, evaluation_ev
):
    """开局检查只管城市比较的起点，不进生活引擎：第一个月新发疾病、第二年带着上一年的状态继续、带病或带物品的终态估值都照常。"""

    def first_month(raw):
        raw["protocol"]["illness_events"]["injected"].append(
            {
                "person_id": "u101",
                "repeat_index": 0,
                "year_index": 1,
                "month": 1,
                "severity": "minor",
                "note": "测试：第 1 年第 1 月小病（开局照旧健康）",
            }
        )

    config2, bundle2, scoring2, specs2 = load_roster(_variant(tmp_path, mutate_life=first_month))
    freeze_roster(config2, bundle2, scoring2, specs2)  # 开局照旧健康：名单通过
    ev = evaluate(
        bundle2, scoring2, specs2[MAIN], "u101.a95.r0", live(bundle2, scoring2, "u101.a95.r0")
    )
    assert (1, 1, "illness_onset") in [
        (e.year_index, e.month, e.event_kind) for e in ev.trajectory.events
    ]
    assert ev.trajectory.status == "complete"
    # 第二年的状态不是标准开局，但生活引擎照常结算它
    year2 = batch_eb.lives["u101.a95.r1"][0].next_state
    assert year2.year_index == 2 and standard_start_problems(year2)
    assert [r.handoff for r in batch_eb.lives["u101.a95.r1"]] == [
        "next_year_ready",
        "next_year_ready",
        "terminal",
    ]
    # 带病的终态（β r1 在 A95）与带物品的终态（E1-6A 样例的 u63）照常估值
    ill = next(e for e in batch_eb.evaluations[MAIN] if e.clone_id == "u102.a95.r1")
    assert ill.valuation.status == "complete" and ill.valuation.continuing_severity == "major"
    dryer = evaluate_ev(
        bundle_ev, scoring_ev, evaluation_ev, "u63.a93.dryer", lives_ev["u63.a93.dryer"]
    )
    assert dryer.valuation.context.start.holdings and dryer.valuation.status == "complete"


# ---------------------------------------------------------------------- B. 冻结前的跨城配置检查（R2）


def _unrelated_laundry(raw_life):
    cat = next(c for c in raw_life["catalogs"] if c["city"]["code"] == "B96")
    washer = next(
        o for o in cat["opportunities"] if o["opportunity_id"] == "b96.durable.washer_dryer"
    )
    unrelated = {**washer, "opportunity_id": "b96.durable.unrelated", "functions": []}
    cat["opportunities"].append(unrelated)


def _bind_laundry_to_unrelated(arm_id, spec, only=None):
    if only is not None and arm_id != only:
        return
    for binding in spec["spec"]["city_bindings"]:
        if binding["city"]["code"] == "B96":
            for target in binding["targets"]:
                if target["slot_id"] == "laundry_appliance":
                    target["opportunity_id"] = "b96.durable.unrelated"


def test_a_slot_with_a_different_meaning_is_refused_on_both_paths(tmp_path):
    """同一个"洗衣设备"槽位，A95 绑定有洗衣功能的洗烘一体机，B96 绑定一件没有洗衣功能的耐用品：正常加载与直接冻结都拒绝。"""
    path = _variant(
        tmp_path, mutate_life=_unrelated_laundry, mutate_spec=_bind_laundry_to_unrelated
    )
    with pytest.raises(LifeContractError, match="laundry_appliance"):
        load_roster(path)
    # 直接冻结：生活目录合法，规格对象合法，只有跨城检查能发现
    config, _bundle, scoring, specs = load_roster(ROSTER)
    bundle2, _ = load_life_bundle(tmp_path / "life.yaml", require_settlement_ready=True)
    bad = {}
    for arm_id, spec in specs.items():
        data = spec.model_dump(mode="python")
        for binding in data["city_bindings"]:
            if binding["city"]["code"] == "B96":
                for target in binding["targets"]:
                    if target["slot_id"] == "laundry_appliance":
                        target["opportunity_id"] = "b96.durable.unrelated"
        bad[arm_id] = type(spec).model_validate(data)
    problems = roster_problems(config, bundle2, scoring, bad)
    assert {p.split("：", 1)[0] for p in problems if "laundry_appliance" in p} == {
        f"运行臂 {a} 的评价规格 {bad[a].spec_id}" for a in bad
    }
    with pytest.raises(LifeContractError, match="laundry_appliance"):
        freeze_roster(config, bundle2, scoring, bad)


def test_every_arm_is_checked_not_only_the_main_one(tmp_path):
    config, _bundle, scoring, specs = load_roster(ROSTER)
    bundle2, _ = load_life_bundle(
        _variant(tmp_path, mutate_life=_unrelated_laundry).parent / "life.yaml",
        require_settlement_ready=True,
    )
    data = specs[D099].model_dump(mode="python")
    _bind_laundry_to_unrelated(D099, {"spec": data})
    only_delta = {**specs, D099: type(specs[D099]).model_validate(data)}
    problems = roster_problems(config, bundle2, scoring, only_delta)
    assert problems and all(p.startswith(f"运行臂 {D099} 的评价规格") for p in problems)
    with pytest.raises(LifeContractError, match=f"运行臂 {D099}"):
        freeze_roster(config, bundle2, scoring, only_delta)


def test_missing_or_undeclared_arm_specs_get_a_clear_diagnosis():
    config, bundle, scoring, specs = load_roster(ROSTER)
    missing = {k: v for k, v in specs.items() if k != BASIC}
    with pytest.raises(LifeContractError, match=r"运行臂 arm\.basic_only 没有加载评价规格"):
        freeze_roster(config, bundle, scoring, missing)  # 不是 KeyError
    extra = {**specs, "arm.extra": specs[MAIN]}
    with pytest.raises(LifeContractError, match=r"评价规格 arm\.extra 不是名单声明的运行臂"):
        freeze_roster(config, bundle, scoring, extra)


def test_legal_city_differences_still_freeze(tmp_path):
    """确定没有（B96 没有会计岗位）、资料未知（B96 住院）与各城不同的报价都是合法差异：名单照常冻结。"""

    def pricier(raw):
        cat = next(c for c in raw["catalogs"] if c["city"]["code"] == "B96")
        washer = next(
            o for o in cat["opportunities"] if o["opportunity_id"] == "b96.durable.washer_dryer"
        )
        washer["price"]["amount"]["amount"] = 2600

    config, bundle, scoring, specs = load_roster(_variant(tmp_path, mutate_life=pricier))
    roster = freeze_roster(config, bundle, scoring, specs)
    a95 = roster.city(A95).catalog_fingerprint
    assert roster.city(B96).catalog_fingerprint != a95  # 各城分别绑定自己的事实
    targets = {t.slot_id: t.status for t in specs[MAIN].binding_for(B96).targets}
    assert "unavailable" in targets.values()  # B96 确定没有要求资格的岗位


# ---------------------------------------------------------------------- C. 敏感性证据配对（R3）


@pytest.fixture(scope="module")
def changed(batch_eb):
    """对照臂里 α r0 在 A95 第 1 月多去一次音乐会：实际生活真的变了（审核的反例）。"""
    clone = "u101.a95.r0"
    plans = list(batch_eb.bundle.plans_for(clone))
    items = [it.model_dump(mode="python") for it in plans[0].items]
    items.append({"opportunity_id": "a95.experience.concert", "quantity": 1, "month": 1})
    plans[0] = with_items(plans[0], *items)
    ev = evaluate(
        batch_eb.bundle,
        batch_eb.scoring,
        batch_eb.specs[D099],
        clone,
        live(batch_eb.bundle, batch_eb.scoring, clone, plans),
    )
    evs = [ev if e.clone_id == clone else e for e in batch_eb.evaluations[D099]]
    return ev, evs, aggregate_arm(batch_eb.roster, D099, evs)


def test_matching_evidence_proves_the_same_lives(batch_eb):
    main = aggregate_arm(batch_eb.roster, MAIN, batch_eb.evaluations[MAIN])
    other = aggregate_arm(batch_eb.roster, D099, batch_eb.evaluations[D099])
    s = sensitivity(main, other, batch_eb.evaluations[MAIN], batch_eb.evaluations[D099])
    assert (s.lives_check, s.actual_lives_identical) == ("identical", True)
    assert len(s.compared_clone_ids) == 13 and not s.evidence_problems
    (skipped,) = s.not_compared  # 两边都没有可信评价的分身不算"已经证明相同"
    assert skipped.startswith("u101.a95.r4：主臂没有可信评价（未运行 / 未返回）")


def test_a_real_change_in_actions_is_not_called_an_evaluation_change(batch_eb, changed):
    ev, evs, other = changed
    assert x(ev.combined.j) != x(
        next(e for e in batch_eb.evaluations[D099] if e.clone_id == ev.clone_id).combined.j
    )
    main = aggregate_arm(batch_eb.roster, MAIN, batch_eb.evaluations[MAIN])
    s = sensitivity(main, other, batch_eb.evaluations[MAIN], evs)
    assert (s.lives_check, s.actual_lives_identical) == ("different", False)
    assert s.different_clone_ids == ("u101.a95.r0",) and "不是只改评价假设" in s.note


def test_evidence_that_does_not_belong_to_the_reports_proves_nothing(batch_eb, changed):
    _ev, evs, other = changed
    main = aggregate_arm(batch_eb.roster, MAIN, batch_eb.evaluations[MAIN])
    stale = batch_eb.evaluations[D099]  # 旧缓存：属于没改行动之前的那份报告
    cases = {
        "旧缓存": (stale, "不是报告引用的那一份"),
        "空列表": ([], "缺少报告引用的那份评价结果"),
        "少一份": (
            [e for e in evs if e.clone_id != "u101.b96.r3"],
            "u101.b96.r3 缺少报告引用的那份评价结果",
        ),
        "重复": ([*evs, evs[0]], "收到 2 份评价结果"),
        "错运行臂": (
            [
                batch_eb.evaluations[BASIC][0]
                if e.clone_id == batch_eb.evaluations[BASIC][0].clone_id
                else e
                for e in evs
            ],
            "不是这个运行臂的",
        ),
    }
    for name, (other_evidence, expect) in cases.items():
        s = sensitivity(main, other, batch_eb.evaluations[MAIN], other_evidence)
        assert (s.lives_check, s.actual_lives_identical) == ("evidence_mismatch", False), name
        assert any(expect in p for p in s.evidence_problems), (name, s.evidence_problems)
        assert "这不等于生活不同" in s.note
    both_empty = sensitivity(main, other, [], [])
    assert both_empty.lives_check == "evidence_mismatch" and not both_empty.compared_clone_ids


def test_order_does_not_matter_and_reports_are_left_alone(batch_eb, changed):
    _ev, evs, other = changed
    main = aggregate_arm(batch_eb.roster, MAIN, batch_eb.evaluations[MAIN])
    frozen = (main.model_dump_json(), other.model_dump_json())
    a = sensitivity(main, other, batch_eb.evaluations[MAIN], evs)
    b = sensitivity(main, other, list(reversed(batch_eb.evaluations[MAIN])), list(reversed(evs)))
    assert a == b
    sensitivity(main, other, [], batch_eb.evaluations[D099])  # 核验失败也不改写报告
    assert (main.model_dump_json(), other.model_dump_json()) == frozen


def test_untrusted_entries_keep_their_missing_meaning(batch_eb):
    """主臂里一个分身的结果没返回（M：未运行）：对照臂即使有它的评价，也不拿来补；它列在未比较里，不是证据问题。"""
    evs_main = [e for e in batch_eb.evaluations[MAIN] if e.clone_id != "u101.b96.r0"]
    main = aggregate_arm(batch_eb.roster, MAIN, evs_main)
    other = aggregate_arm(batch_eb.roster, D099, batch_eb.evaluations[D099])
    s = sensitivity(main, other, evs_main, batch_eb.evaluations[D099])
    assert s.lives_check == "identical" and not s.evidence_problems
    assert any(n.startswith("u101.b96.r0：主臂没有可信评价") for n in s.not_compared)
    assert "u101.b96.r0" not in s.compared_clone_ids


# ---------------------------------------------------------------------- 报告文字：硬要求分母与估值说明


def test_hard_requirement_reports_count_who_was_checked(batch_eb):
    report = aggregate_arm(batch_eb.roster, MAIN, batch_eb.evaluations[MAIN])
    cr = next(r for r in report.city_reports if (r.user_id, str(r.city)) == ("user.beta", B96))
    (h,) = cr.hard_requirements
    assert (h.planned, h.checked, h.violated, h.not_checked) == (2, 2, 2, 0)
    # β 在 B96 的 r1 没返回：这一条硬要求照样列出，r1 记"未核验"，不是"核验过、没问题"
    partial = aggregate_arm(
        batch_eb.roster,
        MAIN,
        [e for e in batch_eb.evaluations[MAIN] if e.clone_id != "u102.b96.r1"],
    )
    cr = next(r for r in partial.city_reports if (r.user_id, str(r.city)) == ("user.beta", B96))
    (h,) = cr.hard_requirements
    assert (h.planned, h.checked, h.violated, h.not_checked, h.not_checked_clone_ids) == (
        2,
        1,
        1,
        1,
        ("u102.b96.r1",),
    )
    assert any("尚未核验" in n for n in cr.notes)
    # 没有硬要求的用户没有这一节；α 的报告不因为缺失而凭空多出硬要求
    alpha = next(r for r in partial.city_reports if r.user_id == "user.alpha")
    assert alpha.hard_requirements == ()


def test_best_known_is_mentioned_only_when_one_exists(
    batch_eb, bundle_ev, scoring_ev, evaluation_ev, lives_ev
):
    none_known = next(e for e in batch_eb.evaluations[MAIN] if e.clone_id == "u102.b96.r1")
    assert none_known.valuation.best_known is None
    (why,) = none_known.valuation.reasons
    assert "已知的分支里没有任何一个" in why and "最好" not in why
    gap = evaluate_ev(bundle_ev, scoring_ev, evaluation_ev, "u69.b94.gap", lives_ev["u69.b94.gap"])
    (why,) = gap.valuation.reasons
    assert "已知的分支里最好的是 p1_current_basic.decline，它只作诊断" in why
