"""E3-A 模块 E：确认画像实际经过已有运行器、三年执行、评价与汇总；产品结果的数值、分母、空值、事件与硬要求与 E1 一致；结果里没有
内部对象；提交幂等、断线查询与暂停的语义。全部是合成人物，mock 规划器，零网络。"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import pytest

from common.run_store import RunStore
from orchestration.life_runner import preflight_from_snapshot, replay_run
from product import scenarios as sc
from product.bridge import submit
from product.fixtures import K51, M52

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def normal(tmp_path_factory):
    return sc.closed_loop(tmp_path_factory.mktemp("normal"), "normal")


def _report(s):
    return s.outcome.reports["arm.main"]


def test_the_confirmed_profile_really_runs_through_the_existing_runner(normal):
    s = normal
    archive = s.work / "archive/normal"
    calls = RunStore(archive).records("calls")
    accepted = RunStore(archive).records("accepted")
    assert (
        s.outcome.status == "completed"
        and s.outcome.calls_used == len(calls) == 6
        and len(accepted) == 6
    )
    assert calls[0]["planner"]["planner_id"] == "mock.rules.e3a-product"
    assert calls[0]["request"]["observation"]["profile"]["six_dimension_points"]["material"] == "30"
    assert not RunStore(archive).has("provider_calls") and s.result.real_model_calls == 0
    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["identities"]["prompt_template"] and manifest["identities"][
        "scoring_content"
    ].startswith("c36f768c")
    index = json.loads((archive / "inputs/index.json").read_text(encoding="utf-8"))
    assert index["replayable_from_snapshot"] is True  # 编译产物是相对路径：输入完整冻结进归档


def test_zero_call_replay_from_the_frozen_inputs_rebuilds_the_same_lives(normal):
    with contextlib.chdir(normal.work):
        world = preflight_from_snapshot(Path("archive/normal"))
        replay = replay_run(world, Path("archive/normal"))
    assert replay["planner_calls"] == 0 and replay["bindings_identical"]
    assert all(replay["evaluations_identical"].values()) and all(
        replay["reports_identical"].values()
    )


def test_projected_numbers_and_denominators_equal_the_e1_report(normal):
    report = _report(normal)
    by_city = {c.city_ref: c for c in normal.result.cities}
    for cr in report.city_reports:
        r = by_city[str(cr.city)]
        assert (r.planned, r.complete, r.exits, r.missing) == (
            cr.planned,
            cr.complete,
            cr.exits,
            cr.missing,
        )
        assert r.mean_j.exact == _canon(cr.mean_j.exact) and r.mean_denominator == cr.complete
        assert (
            r.range_lower.exact == _canon(cr.range_lower.exact)
            and r.range_denominator == cr.range_denominator
        )
        assert "不是" in r.headline_zh  # 写明均值的分母，不称原计划全部分身的平均
    entries = {e.clone_id: e for cr in report.city_reports for e in cr.entries}
    for c in normal.result.clones:
        e = entries[c.clone_ref]
        assert c.category == e.category and c.j.exact == _canon(e.j.exact)
        assert c.l_actual.exact == _canon(e.l_actual.exact) and c.v_reference.exact == _canon(
            e.v_reference.exact
        )


def _canon(exact: str) -> str:
    from fractions import Fraction

    q = Fraction(exact)
    return str(q.numerator) if q.denominator == 1 else f"{q.numerator}/{q.denominator}"


def test_new_profiles_carry_no_hard_requirement_into_results(normal):
    """新问卷不再收集硬要求：正常闭环的确认画像、E1 汇总与产品结果里都没有硬要求。"""
    assert normal.confirmed.hard_requirements == ()
    assert all(cr.hard_requirements == () for cr in _report(normal).city_reports)
    assert all(c.hard_requirements == () for c in normal.result.clones)


@pytest.fixture(scope="module")
def legacy(tmp_path_factory):
    """带旧硬要求（往返 60 分钟）的历史画像（受控夹具）：E1 内部的硬要求机制与结果里的报告照旧成立。"""
    return sc.closed_loop(
        tmp_path_factory.mktemp("legacy"),
        "legacy",
        origin="controlled_fixture",
        legacy_hard_requirements=sc.LEGACY_COMMUTE_60,
    )


def test_hard_requirements_are_reported_separately_with_checked_counts(legacy):
    report = _report(legacy)
    for cr, r in zip(report.city_reports, legacy.result.cities, strict=True):
        assert [(h.need, h.checked, h.not_checked, h.violated) for h in cr.hard_requirements] == [
            (h.need, h.checked, h.not_checked, h.violated) for h in r.hard_requirements
        ]
    lines = {c.clone_ref: c.hard_requirements for c in legacy.result.clones}
    assert lines and all(hs for hs in lines.values())
    assert all(h.need == "commute_limit" for hs in lines.values() for h in hs)


def test_life_facts_show_devices_services_payments_and_household_hours(normal):
    clones = {c.city_ref: c for c in normal.result.clones}
    k51, m52 = clones[K51], clones[M52]
    assert [d.label for d in k51.years[0].devices] == ["便利设备包（洗烘一体机＋扫地机器人）"]
    assert (
        k51.years[0].devices[0].months_in_service == tuple(range(1, 13))
        and k51.years[0].services == ()
    )
    svc = m52.years[0].services[0]
    assert svc.months_covered == tuple(range(1, 13)) and svc.months_not_obtained == ()
    k51_hours = {h.task: h.monthly_hours for h in k51.years[0].household}
    m52_hours = {h.task: h.monthly_hours for h in m52.years[0].household}
    assert k51_hours == {"laundry": ("4",) * 12, "cleaning": ("6",) * 12}  # 设备包的残余
    assert m52_hours == {"laundry": ("2",) * 12, "cleaning": ("2",) * 12}  # 杂务服务的残余
    assert k51.years[0].paid_total.amount_minor > 0 and k51.years[0].cash_end is not None
    # 换岗有来由：第 1 年实际参加了 12 个月的课程，第 2 年起才在会计岗位（资格下一年生效）
    for c in (k51, m52):
        assert any("会计资格课程" in t and "1–12 月" in t for t in c.years[0].training)
        assert all("文员" in j for j in c.years[0].jobs) and all(
            "会计" in j for j in c.years[1].jobs
        )
        assert c.years[1].training == ()


def test_results_carry_no_internal_scoring_objects_paths_or_model_reasons(normal):
    text = json.dumps(normal.result.model_dump(mode="json"), ensure_ascii=False)
    for word in (
        '"tau"',
        '"gamma"',
        '"spec_id"',
        '"rationale"',
        '"need_summary"',
        "180fd938",
        "score.e1-convenience",
        ":\\\\",
        "archive/",
    ):
        assert word not in text, word


def test_duplicate_submission_returns_the_same_run_and_reused_keys_or_stale_preflights_are_refused(
    normal, tmp_path
):
    s = normal
    assert (
        s.receipt.status == "accepted"
        and s.duplicate.status == "duplicate_same_run"
        and s.duplicate.run_id == s.receipt.run_id
    )
    other = sc.preflight_only(tmp_path / "other", "other", person="u509")
    with contextlib.chdir(s.work):
        reused = sc._submission(s.pack, other.confirmed, other.pre, s.submission.idempotency_key)
        refused = submit(s.pack, other.confirmed, other.pre.preflight, reused, Path("registry"))
    assert refused.status == "rejected" and "idempotency_key_reused" in [
        r.code for r in refused.reasons
    ]


def test_a_preflight_is_stale_after_the_answers_change(tmp_path):
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "product_e3a_cli", REPO / "scripts/product_e3a.py"
    )
    sys.path.insert(0, str(REPO / "scripts"))
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    receipt = cli._stale_submission(tmp_path / "stale")
    assert receipt.status == "rejected"
    assert {"submission_confirmed_mismatch", "preflight_stale"} <= {r.code for r in receipt.reasons}


@pytest.fixture(scope="module")
def mixed(tmp_path_factory):
    events = {
        "mode": "injected",
        "stream_version": "e1-5c-illness-stream-v1",
        "note": "受控夹具",
        "injected": [
            {
                "person_id": "u603",
                "repeat_index": 1,
                "year_index": 1,
                "month": 1,
                "severity": "major",
                "note": "受控夹具：大病",
            }
        ],
    }
    from common.life_planning_contracts import MockScript

    return sc.closed_loop(
        tmp_path_factory.mktemp("mixed"),
        "mixed",
        person="u603",
        origin="controlled_fixture",
        repeat_indexes=(0, 1),
        illness_events=events,
        scripts=(MockScript(clone_id="u603.m52.r0", behavior="malformed_output", years=(1,)),),
        legacy_hard_requirements=sc.LEGACY_COMMUTE_60,  # 历史画像的通勤上限：核验硬要求的单独报告
    )


def test_mixed_c_e_m_keeps_planning_failures_out_of_exits_and_c_zero_has_no_mean(mixed):
    cities = {c.city_ref: c for c in mixed.result.cities}
    k51, m52 = cities[K51], cities[M52]
    assert (k51.planned, k51.complete, k51.exits, k51.missing) == (2, 1, 1, 0)
    assert (m52.planned, m52.complete, m52.exits, m52.missing) == (2, 0, 1, 1)
    assert (
        m52.mean_j is None
        and m52.range_denominator == 1
        and m52.range_lower.exact == "0"
        and m52.range_upper.exact == "1"
    )
    assert k51.range_denominator == 1  # 出局者不进缺失范围的分母
    clones = {c.clone_ref: c for c in mixed.result.clones}
    assert (
        clones["u603.m52.r0"].category == "M"
        and clones["u603.m52.r0"].missing_reason == "plan_or_execution"
    )
    exit_chain = clones["u603.k51.r1"].event_chain
    assert clones["u603.k51.r1"].category == "E" and any("付不起最低生活" in x for x in exit_chain)
    assert any("大病" in x for x in exit_chain)
    # 硬要求单独报告：没有开始生活的模拟问题分身没有可信记录，记为未核验（不当作满足，不删人、不扣分）
    assert [(h.need, h.checked, h.not_checked) for h in m52.hard_requirements] == [
        ("commute_limit", 1, 1)
    ]
    assert [(h.need, h.checked, h.not_checked) for h in k51.hard_requirements] == [
        ("commute_limit", 2, 0)
    ]
    assert clones["u603.m52.r0"].hard_requirements == ()
    assert [h.months_not_lived for h in clones["u603.k51.r1"].hard_requirements] == [35]


def test_all_exit_keeps_mean_and_range_empty(tmp_path):
    events = {
        "mode": "injected",
        "stream_version": "e1-5c-illness-stream-v1",
        "note": "受控夹具",
        "injected": [
            {
                "person_id": "u604",
                "repeat_index": 0,
                "year_index": 1,
                "month": 1,
                "severity": "major",
                "note": "受控夹具：大病",
            }
        ],
    }
    s = sc.closed_loop(
        tmp_path,
        "all_exit",
        person="u604",
        origin="controlled_fixture",
        draft_kwargs={"hard_requirements": (), "cash": ("500", "USD", "2024")},
        illness_events=events,
    )
    for c in s.result.cities:
        assert (c.complete, c.exits, c.missing) == (0, 1, 0)
        assert c.mean_j is None and c.range_lower is None and c.range_denominator == 0
        assert c.exit_share.exact == "1"


def test_not_generated_and_paused_runs_do_not_invent_classifications(tmp_path):
    from common.life_planning_contracts import MockScript

    stop = sc.closed_loop(
        tmp_path / "stop", "stop", person="u605", origin="controlled_fixture", stop_after_calls=2
    )
    assert (
        stop.result.evaluation_status == "not_generated"
        and stop.result.cities == ()
        and stop.result.clones == ()
    )
    assert (
        stop.progress.stage == "interrupted"
        and stop.progress.resumable
        and not stop.progress.report_available
    )
    paused = sc.closed_loop(
        tmp_path / "paused",
        "paused",
        person="u606",
        origin="controlled_fixture",
        scripts=(MockScript(clone_id="u606.k51.r0", behavior="raise_error", years=(1,)),),
    )
    assert paused.progress.stage == "paused"
    # 程序错误暂停：E1 把名单跑完并写出 C / E / M 报告，这次运行不能恢复——公开说明照实写，不再同时说“还没有生成”
    pp = paused.progress
    assert (
        pp.report_available and not pp.resumable and paused.result.evaluation_status == "generated"
    )
    assert "已经生成" in pp.pause.impact_zh and "还没有生成" not in pp.pause.model_dump_json()
    assert "不能恢复" in pp.pause.next_action_zh
    # 中断：没有报告、可以恢复
    sp = stop.progress
    assert "还没有生成" in sp.pause.impact_zh and "已经生成" not in sp.pause.impact_zh
    assert "可以恢复" in sp.pause.next_action_zh
    reasons = {c.clone_ref: (c.category, c.missing_reason) for c in paused.result.clones}
    assert reasons == {
        "u606.k51.r0": ("M", "input_config_program_error"),
        "u606.m52.r0": ("M", "not_run"),
    }


def test_partial_service_months_are_shown_as_facts(tmp_path):
    facts, info = sc.partial_service_months(tmp_path)
    svc = facts.services[0]
    assert svc.months_covered == (1, 2, 3, 5, 7) and svc.months_not_obtained == (
        4,
        6,
        8,
        9,
        10,
        11,
        12,
    )
    assert info["convenience_q_by_month"] == [
        "18",
        "18",
        "18",
        "0",
        "18",
        "0",
        "18",
        "0",
        "0",
        "0",
        "0",
        "0",
    ]
    assert (
        facts.cash_end.amount_minor == 6000
    )  # 手算：300 起步，每月净剩 120 或 −96，第 9 月起每月少 30


def test_internal_exception_text_never_reaches_public_progress_or_results(tmp_path):
    """E3-A 收尾 F5：离线规划器抛出带虚构路径与任意内部文字的异常。公开进度与结果只有按类别生成的说明与审计编号，原文只在
    内部归档里；暂停的含义、报告是否生成与 C / E / M 分类不变。"""
    from unittest.mock import patch

    from agentlayer.life_mock import ProductMockPlanner
    from product.bridge import progress, run_offline
    from product.results import project_result

    path, sentinel = "C:/audit-private-user/private-config.json", "INTERNAL_SENTINEL_closeout"

    def fail(_self, _call):
        raise OSError(f"{path} {sentinel}")

    s = sc.preflight_only(tmp_path, "leak", person="u612", origin="controlled_fixture")
    with contextlib.chdir(s.work), patch.object(ProductMockPlanner, "plan", fail):
        outcome = run_offline(s.pre.world, Path("archive/leak"), "run-leak")
        public_progress = progress(s.pack, Path("archive/leak"))
        result = project_result(
            s.pack, s.confirmed, s.pre.preflight, s.pre.world, outcome, s.pre.inventory
        )
    for public in (public_progress.model_dump_json(), result.model_dump_json()):
        assert path not in public and sentinel not in public and "OSError" not in public
    assert outcome.status == "paused" and public_progress.stage == "paused"
    assert public_progress.pause.category == "batch_paused_program_error"
    assert public_progress.pause.audit_ref.startswith("run:run-leak")
    assert public_progress.report_available and "已经生成" in public_progress.pause.impact_zh
    assert result.evaluation_status == "generated"  # 暂停批次照旧生成报告（原有含义不变）
    kinds = {
        c.clone_ref: (c.category, c.missing_reason, c.explanation.category) for c in result.clones
    }
    assert kinds == {
        "u612.k51.r0": ("M", "input_config_program_error", "program_error"),
        "u612.m52.r0": ("M", "not_run", "not_run"),
    }
    failed = next(c for c in result.clones if c.clone_ref == "u612.k51.r0")
    assert failed.explanation.audit_ref == "run:run-leak clone:u612.k51.r0 record:failures.json"
    internal = (s.work / "archive/leak/failures.json").read_text(encoding="utf-8")
    assert sentinel in internal and path in internal  # 完整原文仍可排查
