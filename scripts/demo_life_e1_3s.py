"""E1-3 增补的离线演示（`DECISIONS/0019`）。零大模型调用。

    uv run python scripts/demo_life_e1_3s.py --out runs/2026-10-07-e1-3s-demo

读增补的合成生活输入与评分配置，按 E1-2 规则结算每份计划，对每个分身的年份做探索月度评分
（研究路径用合成真值；产品路径只用用户明确给出的数值），并输出：

* β = 0.1 主配置与 β = 0 对照（对照由主配置只改 β 与规格 ID 派生，派生结果原样存进运行目录）；
* 每个人在比较组上的偏好覆盖报告（只报告，不解锁总分）；
* 可行性探查：同一人、同一起点面对两座城的价格与可得性，哪些安排能执行（**不是代理的选择**，
  也不说明 LLM 会这样选）；
* 手算核对。

把配置副本、源码指纹、实际命令、协议、结果与核对写进一个**新的**运行目录——目录已存在就拒绝，
不覆盖旧结果。代码没有提交时，记录 dirty 状态与关键源文件指纹，不只写一个旧 HEAD。

**不是现实城市的幸福估计，不输出城市排行。** 结果只按"分身 × 偏好来源"列出。

退出码：0 = 全部手算核对通过；1 = 有核对不通过，或输入/配置不合法。
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import math
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

import _bootstrap  # noqa: F401  (side effect: puts src/ on the path)
from common.life_contracts import LifeContractError, YearAction
from common.life_scoring_contracts import (
    CoverageReport,
    ExploratoryYearScore,
    PreferenceInsufficient,
)
from econ.life_coverage import coverage_report
from econ.life_scoring import (
    preference_from_research_truth,
    preference_from_stated_profile,
    score_year,
    source_table,
)
from econ.life_settlement import SETTLEMENT_CONVENTION, binding_from_policy, settle_plans
from ledger.life_catalog import load_life_bundle
from ledger.life_scoring_config import (
    ScoringConfig,
    check_scoring_config,
    load_scoring_config,
    parse_scoring_config,
)
from worlds.dimensions import ALL_DIMENSIONS

REPO = Path(__file__).resolve().parent.parent
LIFE = Path("configs/life/synthetic_e1_3s.yaml")
SCORING = Path("configs/life/scoring_e1_3s.yaml")
KEY_SOURCES = (
    "src/common/life_contracts.py",
    "src/common/life_scoring_contracts.py",
    "src/common/life_billing.py",
    "src/ledger/life_catalog.py",
    "src/ledger/life_scoring_config.py",
    "src/econ/life_settlement.py",
    "src/econ/life_scoring.py",
    "src/econ/life_coverage.py",
    "src/agentlayer/life_views.py",
    "src/worlds/dimensions.py",
    "scripts/demo_life_e1_3s.py",
)
TOL = 1e-12
RESEARCH = "research_synthetic_truth"
PRODUCT = "product_stated_numeric"
CONTROL_SPEC_SUFFIX = ".beta0"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*args: str) -> str | None:
    try:
        r = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return r.stdout.strip()


def _environment(life: Path, scoring: Path) -> dict[str, Any]:
    status = _git("status", "--porcelain")
    files = [*KEY_SOURCES, str(life), str(scoring)]
    return {
        "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "git_head": _git("rev-parse", "HEAD"),
        "git_dirty": None if status is None else bool(status),
        "git_status_porcelain": None if status is None else status.splitlines(),
        "note": "工作区未提交时，git_head 不能代表实际运行的代码；以下指纹才是实际输入",
        "fingerprints_sha256": {f: _sha256(REPO / f) for f in files},
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }


def _control_data(raw: dict[str, Any]) -> dict[str, Any]:
    """β = 0 对照：只改环境分项的 β 与规格 ID（城市条件跟着改 ID），其余逐字相同。"""
    data = copy.deepcopy(raw)
    spec = data["spec"]
    spec["spec_id"] = spec["spec_id"] + CONTROL_SPEC_SUFFIX
    env = [c for c in spec["components"] if c.get("landscape") is not None]
    if len(env) != 1:
        raise LifeContractError("β = 0 对照需要恰好一个声明了景观加分的环境分项")
    env[0]["landscape"]["beta"] = 0.0
    for cc in data["city_conditions"]:
        cc["spec_id"] = spec["spec_id"]
    return data


def _fmt(x: float | None) -> str:
    return "—" if x is None else f"{x:.4f}"


def _comp(score: ExploratoryYearScore, month: int, cid: str):
    return next(c for c in score.months[month - 1].components if c.component_id == cid)


def _dim(score: ExploratoryYearScore, month: int, dim: str):
    return next(d for d in score.months[month - 1].dimensions if d.dimension == dim)


def _score_table(score: ExploratoryYearScore) -> list[str]:
    head = "| 月 | 状态 | 月度分 u | " + " | ".join(ALL_DIMENSIONS) + " |"
    lines = [head, "|" + "---|" * (3 + len(ALL_DIMENSIONS))]
    for m in score.months:
        dims = {d.dimension: d.s for d in m.dimensions}
        cells = [_fmt(dims.get(d)) for d in ALL_DIMENSIONS]
        lines.append(f"| {m.month} | {m.status} | {_fmt(m.u)} | " + " | ".join(cells) + " |")
    return lines


def _material_table(score: ExploratoryYearScore, config: ScoringConfig) -> list[str]:
    needs = [c.component_id for c in config.spec.material_components()]
    lines = ["| 月 | " + " | ".join(needs) + " |", "|" + "---|" * (1 + len(needs))]
    for m in score.months:
        if not m.components:
            continue
        comps = {c.component_id: c.s for c in m.components}
        lines.append(f"| {m.month} | " + " | ".join(_fmt(comps.get(n)) for n in needs) + " |")
    return lines


def _settle_all(bundle, config: ScoringConfig, control: ScoringConfig):
    """结算每个分身的计划并评分。返回（结果条目，主配置评分，对照评分，结算结果）。"""
    results: list[dict[str, Any]] = []
    main: dict[tuple[str, str], ExploratoryYearScore] = {}
    ctrl: dict[tuple[str, str], ExploratoryYearScore] = {}
    settled_by_clone: dict[str, Any] = {}
    for s in bundle.initial_states:
        plans = bundle.plans_for(s.clone_id)
        if not plans:
            continue
        cat = bundle.catalog_for(str(s.city))
        policy = bundle.tax_policy(cat.tax_policy_id)
        profile = next(p for p in bundle.stated_profiles if p.person_id == s.person_id)
        truth = next(t for t in bundle.internal_truths if t.person_id == s.person_id)
        (r,) = settle_plans(
            protocol=bundle.protocol,
            state=s,
            plans=plans,
            catalog=cat,
            tax=binding_from_policy(policy),
        )
        settled_by_clone[s.clone_id] = (s, r)
        entry: dict[str, Any] = {
            "clone_id": s.clone_id,
            "person_id": s.person_id,
            "city": str(s.city),
            "year_index": s.year_index,
            "settlement_status": r.status,
            "scores": {},
        }
        if r.outcome is None:
            entry["problems"] = [p.model_dump(mode="json") for p in r.problems]
            results.append(entry)
            continue
        o = r.outcome
        entry["cash_start"] = o.cash_start.amount if o.cash_start else None
        entry["cash_end"] = o.cash_end.amount if o.cash_end else None
        entry["month_end_cash"] = [m.cash_end.amount for m in o.months]
        entry["exit"] = o.exit.model_dump(mode="json") if o.exit else None
        entry["holdings_service"] = [h.model_dump(mode="json") for h in o.holdings_service]
        for pref in (
            preference_from_research_truth(truth),
            preference_from_stated_profile(profile),
        ):
            if isinstance(pref, PreferenceInsufficient):
                entry["scores"][pref.source] = pref.model_dump(mode="json")
                continue
            for cfg, store in ((config, main), (control, ctrl)):
                if cfg is control and pref.source != RESEARCH:
                    continue  # 对照只跑研究路径：只为说明 β 的作用
                score = score_year(
                    protocol=bundle.protocol,
                    spec=cfg.spec,
                    conditions=cfg.conditions_for(str(s.city)),
                    catalog=cat,
                    state=s,
                    outcome=o,
                    preference=pref,
                    hard_requirements=profile.hard_requirements,
                )
                store[(s.clone_id, pref.source)] = score
            entry["scores"][pref.source] = main[(s.clone_id, pref.source)].model_dump(mode="json")
        results.append(entry)
    return results, main, ctrl, settled_by_clone


def _coverages(bundle, config: ScoringConfig) -> dict[tuple[str, str], CoverageReport]:
    out: dict[tuple[str, str], CoverageReport] = {}
    assert config.comparison_group is not None
    for truth in bundle.internal_truths:
        profile = next(p for p in bundle.stated_profiles if p.person_id == truth.person_id)
        for pref in (
            preference_from_research_truth(truth),
            preference_from_stated_profile(profile),
        ):
            if isinstance(pref, PreferenceInsufficient):
                continue
            out[(truth.person_id, pref.source)] = coverage_report(
                spec=config.spec,
                group=config.comparison_group,
                conditions=config.city_conditions,
                catalogs=bundle.catalogs,
                preference=pref,
            )
    return out


#: 可行性探查的安排：同一份食品与工作，住房远近 × 有无共享工位。不是代理的选择。
PROBE_CLONES = ("u11.a41.c0", "u11.b42.c0")


def _probe(bundle) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for clone in PROBE_CLONES:
        s = bundle.initial_state(clone)
        cat = bundle.catalog_for(str(s.city))
        code = s.city.code.lower()
        for where in ("near", "far"):
            home = cat.opportunity(f"{code}.housing.{where}")
            for desk in (True, False):
                items: list[dict[str, Any]] = [
                    {
                        "opportunity_id": home.opportunity_id,
                        "quantity": 1,
                        "start_month": 1,
                        "end_month": 12,
                    },
                    {
                        "opportunity_id": f"{code}.food.fresh",
                        "quantity": 1,
                        "start_month": 1,
                        "end_month": 12,
                    },
                    {
                        "opportunity_id": f"{code}.job.office",
                        "quantity": 1,
                        "start_month": 1,
                        "end_month": 12,
                    },
                ]
                if desk:
                    items.append(
                        {
                            "opportunity_id": f"{code}.service.cowork",
                            "quantity": 1,
                            "start_month": 1,
                            "end_month": 12,
                        }
                    )
                plan = YearAction.model_validate(
                    {
                        "person_id": s.person_id,
                        "clone_id": s.clone_id,
                        "year_index": 1,
                        "items": items,
                        "rationale": "可行性探查：不是代理的选择",
                    }
                )
                (r,) = settle_plans(
                    protocol=bundle.protocol,
                    state=s,
                    plans=(plan,),
                    catalog=cat,
                    tax=binding_from_policy(bundle.tax_policy(cat.tax_policy_id)),
                )
                o = r.outcome
                rejected = (
                    sorted(
                        {
                            f"{it.opportunity_id}：{it.rejections[0].reason}（{len(it.rejections)} 个月）"
                            for it in o.items
                            if it.rejections
                        }
                    )
                    if o is not None
                    else []
                )
                rows.append(
                    {
                        "clone_id": clone,
                        "city": str(s.city),
                        "housing": home.opportunity_id,
                        "rent_per_month": home.price.amount.amount if home.price else None,
                        "commute_roundtrip_minutes": home.spec.get("commute_roundtrip_minutes"),
                        "with_cowork": desk,
                        "settlement_status": r.status,
                        "all_items_executed_every_month": o is not None
                        and not rejected
                        and o.exit is None,
                        "rejected": rejected,
                        "exit_month": o.exit.month if o is not None and o.exit else None,
                        "cash_end": o.cash_end.amount if o is not None and o.cash_end else None,
                    }
                )
    return rows


def _checks(
    main: dict[tuple[str, str], ExploratoryYearScore],
    ctrl: dict[tuple[str, str], ExploratoryYearScore],
    settled: dict[str, Any],
    cov: dict[tuple[str, str], CoverageReport],
    cov_ctrl: dict[tuple[str, str], CoverageReport],
    probe: list[dict[str, Any]],
) -> list[tuple[str, bool, str]]:
    """手算核对（合成样例，见 synthetic_e1_3s.yaml 文末与 ADR0019 第十一节）。"""
    out: list[tuple[str, bool, str]] = []

    def close(name: str, actual: float | None, expected: float) -> None:
        ok = actual is not None and math.isclose(actual, expected, rel_tol=0, abs_tol=TOL)
        out.append((name, ok, f"实际 {actual!r}，手算 {expected!r}"))

    def check(name: str, ok: bool, detail: str) -> None:
        out.append((name, ok, detail))

    near = main[("u11.a41.c0", RESEARCH)]
    far = main[("u11.a41.c1", RESEARCH)]
    _, r_near = settled["u11.a41.c0"]
    _, r_far = settled["u11.a41.c1"]

    # 1. 数量直接记录
    cab = next(
        h for h in r_near.outcome.holdings_service if h.opportunity_id == "a41.durable.cabinet"
    )
    check(
        "数量由结算写出：两个储物柜那条持有服务记录的数量是 2，评分写明 recorded",
        cab.quantity == 2 and near.holding_quantity_source == "recorded",
        f"quantity = {cab.quantity}，holding_quantity_source = {near.holding_quantity_source}",
    )
    # 2. 重复功能
    comm = _comp(near, 3, "communication")
    terms = [(t.opportunity_id, t.counted) for t in comm.terms]
    check(
        "重复功能：第 3 月有两部手机，只有第一部计入",
        terms == [("a41.durable.phone", True), ("a41.durable.phone_premium", False)],
        f"terms = {terms}",
    )
    close("重复功能：通信 = 3/(3+1)，第二部手机不再加分", comm.s, 0.75)
    idle = next((n for n in near.notes if "在用但全年没有带来任何计入的改善" in n), "")
    check(
        "没有功能的小摆件、多出来的高端手机都被列为在用但未计入",
        "a41.durable.gadget" in idle and "a41.durable.phone_premium" in idle,
        idle or "（没有这条说明）",
    )
    # 3. 容量型
    close("容量型：两个 200 升柜子 = 400 升，储物 = 4/(4+2)", _comp(near, 5, "storage").s, 4 / 6)
    material5 = (
        0.3 * 0.5 + 0.1 * 0.75 + 0.2 * 0.75 + 0.1 * 0.75 + 0.15 * 0.75 + 0.1 * 0.75 + 0.05 * (4 / 6)
    )
    close("u11（A41 近处）第 5 月物质 = Σ m_j s_j", _dim(near, 5, "material").s, material5)
    env = 3 / (3 + 3) + 0.1 * 0.8
    u5 = (
        0.35 * material5
        + 0.15 * 0.5
        + 0.10 * 0.5
        + 0.10 * 0.5
        + 0.05 * 0.25
        + 0.10 * env
        + 0.15 * (60 / 90)
    )
    close("u11（A41 近处）第 5 月月度分", near.months[4].u, u5)
    u1 = (
        0.35 * 0.45
        + 0.15 * 0.5
        + 0.10 * 0.5
        + 0.10 * 0.5
        + 0.05 * 0.25
        + 0.10 * env
        + 0.15 * (60 / 90)
    )
    u2 = u1 + 0.35 * 0.15 * 0.75
    u4 = u2 + 0.35 * 0.10 * 0.75
    close(
        "u11（A41 近处）年均 = 先逐月评分再平均", near.annual_mean, (u1 + 2 * u2 + u4 + 8 * u5) / 12
    )
    # 4. 景观
    e1 = _comp(near, 1, "environment")
    ls = e1.landscape
    check(
        "景观：A41 环境 = 3/(3+3) + 0.1×0.8 = 0.58，基础 0.5、景观贡献 0.08、未截顶、来源合成",
        ls is not None
        and math.isclose(e1.s, 0.58, abs_tol=TOL)
        and math.isclose(ls.physical, 0.5, abs_tol=TOL)
        and math.isclose(ls.contribution, 0.08, abs_tol=TOL)
        and not e1.capped
        and ls.source_kind == "synthetic",
        f"s = {e1.s!r}，landscape = {ls.model_dump() if ls else None}，capped = {e1.capped}",
    )
    b42 = main[("u11.b42.c0", RESEARCH)]
    check(
        "景观未知不当零：B42 的景观数据留空（β = 0.1），环境未知，u11 在 B42 十二个月都不完整",
        b42.annual_status == "incomplete_months"
        and b42.annual_mean is None
        and _comp(b42, 1, "environment").status == "unknown",
        f"{b42.annual_status}，年均 {b42.annual_mean}，环境 {_comp(b42, 1, 'environment').status}",
    )
    b42c = ctrl[("u11.b42.c0", RESEARCH)]
    near_c = ctrl[("u11.a41.c0", RESEARCH)]
    check(
        "β = 0 对照：B42 环境 = 0.5 不再阻断、有年均；A41 环境回到 0.5，月度分少 0.1×0.08",
        b42c.annual_status == "complete"
        and math.isclose(_comp(b42c, 1, "environment").s, 0.5, abs_tol=TOL)
        and math.isclose(_comp(near_c, 1, "environment").s, 0.5, abs_tol=TOL)
        and math.isclose(near.months[4].u - near_c.months[4].u, 0.1 * 0.08, abs_tol=TOL),
        f"B42 {b42c.annual_status}（年均 {b42c.annual_mean!r}），A41 第 5 月 {near.months[4].u!r} → "
        f"{near_c.months[4].u!r}",
    )
    material_b42 = (
        0.3 * 0.5 + 0.1 * 0.75 + 0.2 * 0 + 0.1 * 0.75 + 0.15 * 0.75 + 0.1 * 0.75 + 0.05 * (4 / 6)
    )
    u_b42 = (
        0.35 * material_b42
        + 0.15 * (6 / 9)
        + 0.10 * (2 / 6)
        + 0.10 * (6 / 8)
        + 0.05 * (2 / 14)
        + 0.10 * 0.5
        + 0.15 * (60 / 140)
    )
    close(
        "β = 0 对照：u11 在 B42 第 5 月月度分（没有共享办公：工作空间 0）", b42c.months[4].u, u_b42
    )
    # 5. 租金—通勤
    cn, cf = _comp(near, 1, "commute"), _comp(far, 1, "commute")
    check(
        "通勤读实际所住住房：近处 30 分钟、远处 70 分钟，配的都是同一份线下工作",
        cn.commute is not None
        and cf.commute is not None
        and (cn.commute.housing_opportunity_id, cn.commute.minutes) == ("a41.housing.near", 30)
        and (cf.commute.housing_opportunity_id, cf.commute.minutes) == ("a41.housing.far", 70),
        f"近处 {cn.commute}，远处 {cf.commute}",
    )
    close("近处通勤 = 60/(60+30) = 2/3", cn.s, 2 / 3)
    close("远处通勤 = 60/(60+70) = 6/13（长通勤降分）", cf.s, 6 / 13)
    close(
        "远处每月少付 400：年末现金差 4,800",
        r_far.outcome.cash_end.amount - r_near.outcome.cash_end.amount,
        4800.0,
    )
    close(
        "近处比远处月度分高 0.15×(2/3 − 6/13)（其他消费相同、省下的钱留存到期末，尚未获得期末评价）",
        near.months[4].u - far.months[4].u,
        0.15 * (2 / 3 - 6 / 13),
    )
    u13 = main[("u13.a41.c0", RESEARCH)]
    series = [
        (_comp(u13, m, "commute").commute.housing_opportunity_id, _comp(u13, m, "commute").s)
        for m in range(1, 5)
    ]
    want = [("a41.housing.near", 2 / 3)] * 3 + [("a41.housing.far", 6 / 13)]
    check(
        "u13（A41）第 4 月退回远处：住房与通勤一起切换（30 → 70 分钟），第 5 月出局",
        all(
            a == b and math.isclose(x, y, abs_tol=TOL)
            for (a, x), (b, y) in zip(series, want, strict=True)
        )
        and u13.months[4].status == "exit_month",
        f"{series}，第 5 月 {u13.months[4].status}",
    )
    u13b = main[("u13.b42.c0", RESEARCH)]
    _, r13b = settled["u13.b42.c0"]
    check(
        "同一偏好面对更高房租：u13 在 B42 第 3 月就退回远处、第 4 月出局（A41 是第 4、5 月）",
        [m.cash_end.amount for m in r13b.outcome.months] == [1900.0, 800.0, 300.0, 1300.0]
        and r13b.outcome.exit is not None
        and r13b.outcome.exit.month == 4
        and [m.status for m in u13b.months[3:5]] == ["exit_month", "after_exit"],
        f"月末 {[m.cash_end.amount for m in r13b.outcome.months]}，出局 "
        f"{r13b.outcome.exit.month if r13b.outcome.exit else None}",
    )
    # 6. 未支持的需求与覆盖
    u12 = main[("u12.a41.c0", RESEARCH)]
    c12 = cov[("u12", RESEARCH)]
    idle12 = next((n for n in u12.notes if "在用但全年没有带来任何计入的改善" in n), "")
    check(
        "u12 的家务需求没有评分分项：十二个月不完整；保洁列为在用但未计入",
        u12.annual_status == "incomplete_months" and "a41.service.cleaning" in idle12,
        f"{u12.annual_status}；{idle12}",
    )
    close("u12 覆盖：家务叶子权重 0.4×0.1 = 0.04 列为没有评分分项", c12.no_component_weight, 0.04)
    c11 = cov[("u11", RESEARCH)]
    check(
        "u11 覆盖：共同支持权重 0.9（环境在 B42 未知），标为合成契约覆盖",
        math.isclose(c11.common_weight, 0.9, abs_tol=TOL)
        and math.isclose(c11.unknown_weight, 0.1, abs_tol=TOL)
        and c11.coverage_label == "synthetic_contract_coverage",
        f"C = {c11.common_weight!r}，未知 {c11.unknown_weight!r}，{c11.coverage_label}",
    )
    close(
        "β = 0 对照下 u11 覆盖 = 1.0（未启用的景观项不阻断）",
        cov_ctrl[("u11", RESEARCH)].common_weight,
        1.0,
    )
    # 7. 产品路径
    prod = main.get(("u11.a41.c0", PRODUCT))
    check(
        "产品路径只用明确点数：u11 的点数 / 100 与真值相同，逐月分一致",
        prod is not None and [m.u for m in prod.months] == [m.u for m in near.months],
        "一致" if prod is not None else "缺产品路径结果",
    )
    # 8. 可行性探查
    b42_desk = [r for r in probe if r["clone_id"] == "u11.b42.c0" and r["with_cowork"]]
    a41_all = [r for r in probe if r["clone_id"] == "u11.a41.c0"]
    check(
        "可行性探查：A41 四种安排都能执行；B42 没有共享办公，含工位的安排里工位每月被拒（确定缺货）",
        all(r["all_items_executed_every_month"] for r in a41_all)
        and all(any("确定缺货" in x for x in r["rejected"]) for r in b42_desk),
        f"A41 {[r['all_items_executed_every_month'] for r in a41_all]}；B42 含工位 {[r['rejected'] for r in b42_desk]}",
    )
    check(
        "方案属性已记录、未评价：u11 的结果列出 brand、prep_mode、tier",
        near.unevaluated_attributes == ("brand", "prep_mode", "tier"),
        f"{near.unevaluated_attributes}",
    )
    # 9. 2026-10-08 边界修复：覆盖报告 v2 分开三层与两类来源；默认样例的覆盖数值不变
    storage = next(x for x in c11.leaves if x.leaf == "storage")
    env_leaf = next(x for x in c11.leaves if x.leaf == "environment")
    commute_leaf = next(x for x in c11.leaves if x.leaf == "commute")
    check(
        "覆盖报告 v2：储物的城市资料齐全（柜子写了容量、两城都声明储物可得），环境在 B42 有缺口，"
        "通勤的可评分性看实际安排",
        c11.report_version == "coverage-v2"
        and (storage.city_data, storage.fact_evidence, storage.parameter_evidence)
        == ("complete", "synthetic_only", "synthetic_only")
        and env_leaf.city_data == "gap"
        and commute_leaf.action_scope == "conditional",
        f"{c11.report_version}；储物 {storage.city_data}/{storage.fact_evidence}/{storage.parameter_evidence}；"
        f"环境 {env_leaf.city_data}；通勤 {commute_leaf.action_scope}",
    )
    b42_ws = _comp(b42, 6, "workspace")
    check(
        "确定不可得按规则评价：B42 声明没有共享办公，u11 在 B42 的工作空间是确定的 0（不是未知）",
        (b42_ws.status, b42_ws.s) == ("known", 0.0),
        f"{b42_ws.status}，s = {b42_ws.s}",
    )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="E1-3 增补的离线演示（不评城市、不排序）")
    ap.add_argument("--out", type=Path, required=True, help="新的运行目录；已存在则拒绝")
    ap.add_argument("--life", type=Path, default=LIFE)
    ap.add_argument("--scoring", type=Path, default=SCORING)
    args = ap.parse_args()

    if args.out.exists():
        print(f"{args.out} 已存在：不覆盖旧结果，请换一个新目录")
        return 1
    try:
        bundle, life_report = load_life_bundle(args.life, require_settlement_ready=True)
        config, score_report = load_scoring_config(args.scoring, bundle)
        if config.comparison_group is None:
            raise LifeContractError("增补演示需要评分配置声明比较组")
        raw_scoring = yaml.safe_load(args.scoring.read_text(encoding="utf-8"))
        control_raw = _control_data(raw_scoring)
        control = parse_scoring_config(control_raw)
        control_report = check_scoring_config(control, bundle)
        if not control_report.exploration_scoring_ready:
            raise LifeContractError(
                "β = 0 对照配置不能用：\n  " + "\n  ".join(control_report.errors)
            )
    except LifeContractError as exc:
        print(exc)
        return 1

    results, main_scores, ctrl_scores, settled = _settle_all(bundle, config, control)
    cov = _coverages(bundle, config)
    cov_ctrl = _coverages(bundle, control)
    probe = _probe(bundle)
    checks = _checks(main_scores, ctrl_scores, settled, cov, cov_ctrl, probe)

    # ---------------------------------------------------------------- summary
    summary = [
        "# E1-3 增补演示：功能计分、自然景观、租金—通勤与覆盖报告",
        "",
        "合成样例，参数是探索设定：**不是现实城市的幸福估计，不能据此排序城市。**",
        f"生活输入 `{args.life.as_posix()}`（协议 {bundle.protocol.protocol_id}），评分配置 `{args.scoring.as_posix()}`"
        f"（{config.spec.scoring_version}，函数 {config.spec.function}）。记账能否执行：settlement_ready = "
        f"{str(life_report.settlement_ready).lower()}；探索评分能否执行：exploration_scoring_ready = "
        f"{str(score_report.exploration_scoring_ready).lower()}；正式评分：formal_scoring_ready = "
        f"{str(score_report.formal_scoring_ready).lower()}。",
        f"金额单位：{bundle.catalogs[0].money_basis.numeraire_id}（合成城的当地价格水平、2024 年不变价）。",
        "",
        "## 三个生活例子",
        "",
        "1. **两部手机不算两份通信。** u11 第 1 月买大众款手机，第 3 月又买了一部高端手机：通信叶子仍是",
        "   3/(3+1) = 0.75，第二部记为“未计入”；没有声明功能的小摆件也不带来任何改善。两个 200 升的柜子",
        "   是容量型功能：400 升折成 4 个百升单位，储物 = 4/(4+2) ≈ 0.667。",
        "2. **自然景观加分有界、可关闭。** A41 基础环境 3/(3+3) = 0.5，景观指标 0.8，β = 0.1：环境 = 0.58。",
        "   B42 景观数据留空，β > 0 时环境未知（不当零），月度总分不完整；β = 0 对照下环境回到 0.5、不阻断。",
        "3. **远一点、便宜一点。** 同一套一居室：近处月租 1,400、每工作日往返 30 分钟；远处月租 1,000、",
        "   70 分钟。通勤满足感 60/(60+30) = 2/3 对 60/(60+70) = 6/13；远处一年省 4,800。在其他实际消费",
        "   相同、节省的现金留存到期末的当前演示中，节省部分尚未获得期末评价，因此分数只体现通勤劣势。",
        "   省下的钱若用于其他可评分的实际服务，收益已经可以经这些服务体现，不再给现金节约另加一次分；",
        "   留存现金的未来价值由 E1-5/E1-6 处理。",
        "4. **资料没采集不等于确实没有**（2026-10-08 边界修复）。每座城对评分要用的功能写明可得、确定",
        "   不可得或资料未知：B42 确定没有共享办公，工作空间按规则得确定的 0；若某项功能只是没采集，",
        "   没在用的月份报未知，覆盖报告也列为缺口，不再报“已支持、确定为零”。",
        "",
    ]
    for entry in results:
        clone = entry["clone_id"]
        summary.append(f"## {clone}（{entry['city']}，结算：{entry['settlement_status']}）")
        summary.append("")
        if "month_end_cash" in entry:
            summary.append(f"月末现金：{entry['month_end_cash']}")
            summary.append("")
        for source in (RESEARCH, PRODUCT):
            score = main_scores.get((clone, source))
            if score is None:
                ins = entry["scores"].get(source)
                if ins is not None:
                    summary.append(f"- {source}：偏好信息不足——{ins.get('message')}")
                    summary.append("")
                continue
            summary.append(
                f"### {source}：年度状态 {score.annual_status}，年均 {_fmt(score.annual_mean)}"
                f"（持有物数量：{score.holding_quantity_source}）"
            )
            summary.append("")
            summary.append(f"_{score.explanation}_")
            summary.append("")
            summary.extend(_score_table(score))
            summary.append("")
            if source == RESEARCH:
                summary.append("物质叶子：")
                summary.append("")
                summary.extend(_material_table(score, config))
                summary.append("")
                for m in score.months:
                    for c in m.components:
                        if c.component_id == "commute" and m.month in (1, 3, 4):
                            summary.append(
                                f"- 第 {m.month} 月通勤：{c.status}，s = {_fmt(c.s)}；"
                                f"{c.commute.model_dump() if c.commute else '；'.join(c.reasons)}"
                            )
                        if c.component_id == "environment" and m.month == 1:
                            summary.append(
                                f"- 环境：{c.status}，s = {_fmt(c.s)}；"
                                f"{c.landscape.model_dump() if c.landscape else '；'.join(c.reasons)}"
                            )
                cs = ctrl_scores.get((clone, RESEARCH))
                if cs is not None:
                    summary.append(
                        f"- β = 0 对照：年度状态 {cs.annual_status}，年均 {_fmt(cs.annual_mean)}，"
                        f"逐月 {[_fmt(m.u) for m in cs.months]}"
                    )
            for h in score.hard_requirements:
                summary.append(
                    f"- 硬要求 {h.need}：{h.summary}（满足 {list(h.months_satisfied)}，不满足 "
                    f"{list(h.months_not_satisfied)}，无法判断 {list(h.months_unknown)}，未生活 "
                    f"{list(h.months_not_lived)}）"
                )
            for n in score.notes:
                summary.append(f"- {n}")
            summary.append("")

    summary += [
        "## 功能供给声明（目录里没有记录不等于确定没有）",
        "",
        "| 城市 | 功能 | 数量语义 | 供给 | 说明 |",
        "|---|---|---|---|---|",
    ]
    for cat in bundle.catalogs:
        for fs in cat.function_supply:
            summary.append(
                f"| {cat.city} | {fs.function} | {bundle.function_kinds.get(fs.function, '—')} | "
                f"{fs.status} | {fs.note or '—'} |"
            )
    summary += ["", "## 偏好覆盖（只报告，不解锁总分）", ""]
    for (person, source), rep in sorted(cov.items()):
        ctrl_rep = cov_ctrl[(person, source)]
        summary.append(
            f"### {person} · {source}：共同支持 C = {rep.common_weight:.4f}（β = 0 对照 "
            f"{ctrl_rep.common_weight:.4f}），某城未知 {rep.unknown_weight:.4f}，没有分项 "
            f"{rep.no_component_weight:.4f}，标签 {rep.coverage_label}（{rep.report_version}）"
        )
        summary.append("")
        summary.append(
            "| 叶子 | 维度 | 权重 a | 支持 | 规则 | 城市资料 | 行动可评分性 | 事实来源 | 参数来源 | "
            "行动机制 | 资料缺口 | 说明 |"
        )
        summary.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for leaf in rep.leaves:
            summary.append(
                f"| {leaf.leaf} | {leaf.dimension} | {leaf.weight:.4f} | {leaf.support} | {leaf.rule} | "
                f"{leaf.city_data} | {leaf.action_scope} | {leaf.fact_evidence} | {leaf.parameter_evidence} | "
                f"{'有' if leaf.action_mechanism else '无'} | {'；'.join(leaf.data_gaps) or '—'} | "
                f"{'；'.join(leaf.reasons) or '—'} |"
            )
        summary.append("")
        for n in rep.notes:
            summary.append(f"- {n}")
        summary.append("")

    summary += [
        "## 可行性探查（不是代理的选择，也不说明 LLM 会这样选）",
        "",
        "同一个人（u11）、同样的起点与食品、工作，换住房远近与有无共享工位，看哪些安排能逐月执行：",
        "",
        "| 分身 | 住房 | 月租 | 往返分钟 | 共享工位 | 全部逐月执行 | 被拒 | 出局月 | 年末现金 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in probe:
        summary.append(
            f"| {r['clone_id']} | {r['housing']} | {r['rent_per_month']} | {r['commute_roundtrip_minutes']} | "
            f"{'有' if r['with_cowork'] else '无'} | {'是' if r['all_items_executed_every_month'] else '否'} | "
            f"{'；'.join(r['rejected']) or '—'} | {r['exit_month'] or '—'} | {r['cash_end']} |"
        )
    summary += [
        "",
        "## 评分来源表",
        "",
        "| 分项 | 维度 | 来源 | 读哪个字段 | 取值 | 单位 | 合并 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in source_table(config.spec):
        summary.append(
            "| "
            + " | ".join(
                row[k]
                for k in (
                    "component",
                    "dimension",
                    "source",
                    "read_from",
                    "value",
                    "unit",
                    "combine",
                )
            )
            + " |"
        )
    summary += [
        "",
        "## 省略清单（省略了什么、影响谁）",
        "",
        "| 省略 | 可能受影响的人 | 归属 |",
        "|---|---|---|",
    ]
    for o in config.spec.omissions:
        summary.append(f"| {o.what} | {o.affects} | {o.owner} |")

    check_lines = [
        "# 手算核对",
        "",
        f"容差：绝对误差 ≤ {TOL}（评分是浮点运算；金额仍按整数分在结算里精确守恒）。",
        "",
    ]
    check_lines += [
        f"- {'通过' if ok else '不通过'}　{name}：{detail}" for name, ok, detail in checks
    ]

    # ---------------------------------------------------------------- write
    args.out.mkdir(parents=True)
    cfg_dir = args.out / "configs"
    cfg_dir.mkdir()
    shutil.copyfile(args.life, cfg_dir / args.life.name)
    shutil.copyfile(args.scoring, cfg_dir / args.scoring.name)
    control_path = cfg_dir / (args.scoring.stem + "_beta0_control.yaml")
    control_path.write_text(
        "# β = 0 对照：由主配置派生，只改了环境分项的 landscape.beta（0.1 → 0）与规格 ID（加后缀 "
        f"{CONTROL_SPEC_SUFFIX}），城市条件的 spec_id 跟着改；其余逐字相同。注释在派生时丢失，见主配置。\n"
        + yaml.safe_dump(control_raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    env = _environment(args.life, args.scoring)
    (args.out / "environment.json").write_text(
        json.dumps(env, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    command = {
        "argv": sys.argv,
        "python_executable": sys.executable,
        "cwd": os.getcwd(),
        "reproduce": f"uv run python scripts/demo_life_e1_3s.py --out <新目录> --life {args.life.as_posix()} "
        f"--scoring {args.scoring.as_posix()}",
    }
    (args.out / "command.json").write_text(
        json.dumps(command, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    protocol = {
        "protocol": bundle.protocol.model_dump(mode="json"),
        "settlement_convention": SETTLEMENT_CONVENTION,
        "spec_id": config.spec.spec_id,
        "scoring_version": config.spec.scoring_version,
        "scoring_function": config.spec.function,
        "comparison_group": config.comparison_group.model_dump(mode="json"),
        "control_spec_id": control.spec.spec_id,
        "formal_scoring_ready": bundle.protocol.scoring_ready,
    }
    (args.out / "protocol.json").write_text(
        json.dumps(protocol, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    inputs = {
        "life_file": str(args.life),
        "life_sha256": _sha256(args.life),
        "scoring_file": str(args.scoring),
        "scoring_sha256": _sha256(args.scoring),
        "control_file": str(control_path.relative_to(args.out)),
        "control_sha256": _sha256(control_path),
        "life_report": life_report.model_dump(mode="json"),
        "scoring_report": score_report.model_dump(mode="json"),
        "control_report": control_report.model_dump(mode="json"),
    }
    (args.out / "inputs.json").write_text(
        json.dumps(inputs, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.out / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    control_out = [
        {
            "clone_id": c,
            "preference_source": src,
            "spec_id": score.spec_id,
            "annual_status": score.annual_status,
            "annual_mean": score.annual_mean,
            "months": [
                {
                    "month": m.month,
                    "status": m.status,
                    "u": m.u,
                    "environment": next(
                        (
                            x.model_dump(mode="json")
                            for x in m.components
                            if x.component_id == "environment"
                        ),
                        None,
                    ),
                }
                for m in score.months
            ],
        }
        for (c, src), score in ctrl_scores.items()
    ]
    (args.out / "control_beta0.json").write_text(
        json.dumps(control_out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    coverage_out = [
        {"config": which, **rep.model_dump(mode="json")}
        for which, reports in (("main", cov), ("beta0_control", cov_ctrl))
        for _, rep in sorted(reports.items())
    ]
    (args.out / "coverage.json").write_text(
        json.dumps(coverage_out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.out / "feasibility_probe.json").write_text(
        json.dumps(probe, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.out / "summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    (args.out / "checks.md").write_text("\n".join(check_lines) + "\n", encoding="utf-8")
    (args.out / "checks.json").write_text(
        json.dumps(
            [{"check": name, "passed": ok, "detail": detail} for name, ok, detail in checks],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (args.out / "README.md").write_text(
        "\n".join(
            [
                "# E1-3 增补演示（运行证据）",
                "",
                "零大模型调用的离线演示（`DECISIONS/0019`）。输入、结果与核对都在本目录；它们是合成探索设定下的",
                "结果，**不是现实城市的幸福估计，不是城市排行**。",
                "",
                "- `command.json`：实际命令（argv）、解释器与工作目录",
                "- `environment.json`：git HEAD、是否有未提交改动、关键源文件与配置的 SHA-256",
                "- `configs/`：本次使用的生活输入与评分配置副本，以及派生的 β = 0 对照配置",
                "- `protocol.json`：协议、结算约定、评分规格版本与比较组",
                "- `inputs.json`：输入文件与指纹、三份加载检查报告（生活输入、主配置、对照配置）",
                "- `results.json`：每个分身的结算摘要（月末现金、出局、持有服务及数量）与逐月评分（研究 / 产品）",
                "- `control_beta0.json`：β = 0 对照（研究路径）的逐月状态、月度分与环境分项明细",
                "- `coverage.json`：每个人在比较组上的偏好覆盖（主配置与对照）",
                "- `feasibility_probe.json`：可行性探查（不是代理的选择）",
                "- `summary.md`：生活例子、逐月表格、覆盖表、探查表、评分来源表与省略清单",
                "- `checks.md` / `checks.json`：手算核对",
                "",
                "复现：`uv run python scripts/demo_life_e1_3s.py --out <新目录>`。代码未提交时，以",
                "`environment.json` 的指纹为准，不以 HEAD 代表实际运行的代码。",
                "",
            ]
        ),
        encoding="utf-8",
    )
    for line in check_lines[4:]:
        print(line)
    print(f"\n结果已写入 {args.out}（不含任何城市排行）")
    return 0 if all(ok for _, ok, _ in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
