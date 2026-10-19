"""E1-5A 补充：成长满足感与广义交通评分的离线演示（`DECISIONS/0024`）。零大模型调用。

    uv run python scripts/demo_life_e1_5a_growth_transport.py --out runs/<新目录>

读 E1-5A 补充的合成生活输入与评分配置，按预先声明的年度计划逐年做**真实的**年度交接（结算 → 评分 → 反馈），输出：

* 例一 不同发展偏好：看重发展（u61，权重 0.2）与不看重发展（u62，权重 0）的人，学或不学会计——同样的生活事实，
  发展维在学习月份是 0.6 还是 0.5，总分差多少；
* 例二 学习暂停：第 1 年上半年上课、第 2 年暂停、第 3 年恢复——成长只出现在实际参加的月份，学费只付一次；
* 例三 不同出行安排：天天到岗、混合、远程、两份兼职、早晚两班、不工作专心自学——到岗次数 D、系数 k、等效分钟
  T_eq 与交通分；
* 同一份已执行的生活事实换评分：η_growth = 0 对照，ρ = 0 / 0.25 / 0.5 对照，E1-5A 原规则（方案级通勤、发展维只有
  背景）对照——差异只来自评分规则；
* 反馈开启后的跨年影响：原规则下远程或不工作的人通勤报不支持、反馈受阻；新规则下额度照常更新，一份第二年计划因此
  从"不可行"变成"可行"；
* 覆盖报告（新规则与原规则）、第 2 年的观察样例、手算核对。

派生的对照配置原样存进运行目录。目录已存在就拒绝，不覆盖旧结果。

**合成设定：** 成长系数、交通参数、学费、工资与负担都是手工设定；对照只验证接线，不证明现实中的成长感或出行感受，
也不排序城市。

退出码：0 = 全部手算核对通过；1 = 有核对不通过，或输入不合法。
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any

import yaml

import _bootstrap  # noqa: F401  (side effect: puts src/ on the path)
from agentlayer.life_views import (
    build_observation,
    observation_payload,
    standard_product_permission,
)
from common.life_contracts import (
    RESOURCE_RULES_REVISION,
    SCORING_CONTENT_HANDOFF_REVISION,
    LifeContractError,
    LifeProtocol,
    PersonYearState,
    YearAction,
)
from common.life_feedback_contracts import LifeYearResult
from econ.life_coverage import coverage_report
from econ.life_scoring import preference_from_stated_profile, score_year, source_table
from econ.life_settlement import binding_from_policy
from econ.life_year import ScoringInputs, run_life_year
from ledger.life_catalog import LifeBundle, load_life_bundle
from ledger.life_scoring_config import (
    ScoringConfig,
    load_scoring_config,
    parse_scoring_config,
    scoring_config_problems,
)

REPO = Path(__file__).resolve().parent.parent
LIFE = Path("configs/life/synthetic_e1_5a_growth_transport.yaml")
SCORING = Path("configs/life/scoring_e1_5a_growth_transport.yaml")
LEGACY_SCORING = Path("configs/life/scoring_e1_5a.yaml")
KEY_SOURCES = (
    "src/common/life_contracts.py",
    "src/common/life_scoring_contracts.py",
    "src/common/life_units.py",
    "src/ledger/life_catalog.py",
    "src/ledger/life_scoring_config.py",
    "src/econ/life_settlement.py",
    "src/econ/life_resources.py",
    "src/econ/life_career.py",
    "src/econ/life_scoring.py",
    "src/econ/life_coverage.py",
    "src/econ/life_feedback.py",
    "src/econ/life_year.py",
    "src/agentlayer/life_views.py",
    "scripts/demo_life_e1_5a_growth_transport.py",
    "tests/life/test_growth_transport.py",
)
CLONES = (
    "u61.a71.learn",
    "u61.a71.nolearn",
    "u61.a71.pause",
    "u62.a71.learn",
    "u62.a71.nolearn",
    "u63.a71.onsite",
    "u63.a71.hybrid",
    "u63.a71.remote",
    "u63.a71.parttime",
    "u63.a71.shifts",
    "u64.a71.study",
)
TRAVEL = (
    "u63.a71.onsite",
    "u63.a71.hybrid",
    "u63.a71.remote",
    "u63.a71.parttime",
    "u63.a71.shifts",
)
F = Fraction


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*args: str) -> str | None:
    try:
        r = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return r.stdout.strip()


def _environment() -> dict[str, Any]:
    status = _git("status", "--porcelain")
    files = [*KEY_SOURCES, str(LIFE), str(SCORING), str(LEGACY_SCORING)]
    return {
        "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "git_head": _git("rev-parse", "HEAD"),
        "git_dirty": None if status is None else bool(status),
        "git_status_porcelain": None if status is None else status.splitlines(),
        "note": "工作区未提交时，git_head 不能代表实际运行的代码；以下指纹才是实际输入",
        "resource_rules_revision": RESOURCE_RULES_REVISION,
        "feedback_handoff_revision": SCORING_CONTENT_HANDOFF_REVISION,
        "fingerprints_sha256": {f: _sha256(REPO / f) for f in files},
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }


def _revise(model: Any, **changes: Any) -> Any:
    data = model.model_dump(mode="python")
    data.update(changes)
    return type(model).model_validate(data)


def _arm(
    raw: dict[str, Any], suffix: str, *, eta: float | None = None, rho: float | None = None
) -> dict:
    """由主评分配置派生对照臂：另起规格编号（同一编号不能装两套内容），只改成长或交通参数。"""
    data = copy.deepcopy(raw)
    data["spec"]["spec_id"] = f"score.e1-5a-gt.{suffix}"
    for c in data["city_conditions"]:
        c["spec_id"] = data["spec"]["spec_id"]
    for comp in data["spec"]["components"]:
        if comp["component_id"] == "opportunity" and eta is not None:
            comp["growth"]["eta"] = eta
        if comp["component_id"] == "commute" and rho is not None:
            comp["transport"]["rho"] = rho
    return data


def _legacy(raw: dict[str, Any], protocol_id: str) -> dict:
    """E1-5A 原评分规则（e1-3-supplement-v1）搬到新协议上：同一份生活事实，只换评分规则。"""
    data = copy.deepcopy(raw)
    data["spec"]["spec_id"] = "score.e1-5a-gt.legacy-rules"
    data["spec"]["protocol_id"] = protocol_id
    data["comparison_group"]["group_id"] = "group.e1-5a-gt.legacy-rules"
    for c in data["city_conditions"]:
        c["spec_id"] = "score.e1-5a-gt.legacy-rules"
    return data


class World:
    def __init__(self, bundle: LifeBundle, scoring: ScoringConfig, protocol: LifeProtocol) -> None:
        self.b, self.scoring, self.protocol = bundle, scoring, protocol

    def profile(self, person: str) -> Any:
        return next(p for p in self.b.stated_profiles if p.person_id == person)

    def inputs(self, state: PersonYearState) -> ScoringInputs:
        profile = self.profile(state.person_id)
        return ScoringInputs(
            spec=self.scoring.spec,
            conditions=self.scoring.conditions_for(str(state.city)),
            preference_input=profile,
            hard_requirements=profile.hard_requirements,
        )

    def _city(self, state: PersonYearState) -> tuple[Any, Any]:
        catalog = self.b.catalog_for(str(state.city))
        assert catalog is not None
        return catalog, binding_from_policy(self.b.tax_policy(catalog.tax_policy_id))  # type: ignore[arg-type]

    def year(
        self, state: PersonYearState, plan: YearAction, *, scored: bool = True
    ) -> LifeYearResult:
        catalog, tax = self._city(state)
        return run_life_year(
            protocol=self.protocol,
            state=state,
            action=plan,
            catalog=catalog,
            tax=tax,
            scoring=self.inputs(state) if scored else None,
        )

    def start(self, clone: str) -> PersonYearState:
        s = self.b.initial_state(clone)
        assert s is not None
        return s

    def declared(self, clone: str, year: int) -> YearAction:
        return next(p for p in self.b.plans if p.clone_id == clone and p.year_index == year)

    def plan(self, clone: str, year: int, *items: dict[str, Any], why: str) -> YearAction:
        data = self.declared(clone, year).model_dump(mode="json")
        data["items"] = list(items)
        data["rationale"] = why
        return YearAction.model_validate(data)

    def run(self, clone: str, *, scored: bool = True) -> list[LifeYearResult]:
        state = self.start(clone)
        out: list[LifeYearResult] = []
        for plan in self.b.plans_for(clone):
            if plan.year_index != state.year_index:
                continue
            r = self.year(state, plan, scored=scored)
            out.append(r)
            if r.handoff != "next_year_ready" or r.next_state is None:
                break
            state = r.next_state
        return out

    def rescore(self, start: PersonYearState, r: LifeYearResult, config: ScoringConfig) -> Any:
        """同一份已结算的生活事实，换一套评分规格重新评分（评分是纯函数，不改结算）。"""
        catalog, _ = self._city(start)
        pref = preference_from_stated_profile(self.profile(start.person_id))
        return score_year(
            protocol=self.protocol,
            spec=config.spec,
            conditions=config.conditions_for(str(start.city)),
            catalog=catalog,
            state=start,
            outcome=r.settlement.outcome,
            preference=pref,
        )


def _item(oid: str, start: int = 1, end: int = 12) -> dict[str, Any]:
    return {"opportunity_id": oid, "quantity": 1, "start_month": start, "end_month": end}


def _comp(score: Any, month: int, component: str) -> Any:
    return next(c for c in score.months[month - 1].components if c.component_id == component)


def _series(score: Any, component: str) -> list[float | None]:
    return [_comp(score, m, component).s for m in range(1, 13)]


def _dec(q: Fraction | None) -> str | None:
    if q is None:
        return None
    q = Fraction(q)
    d, n2, n5 = q.denominator, 0, 0
    while d % 2 == 0:
        d, n2 = d // 2, n2 + 1
    while d % 5 == 0:
        d, n5 = d // 5, n5 + 1
    if d != 1:
        return f"{q.numerator}/{q.denominator}"
    n = max(n2, n5)
    digits = str(abs(q.numerator * 10**n // q.denominator)).rjust(n + 1, "0")
    text = f"{digits[:-n]}.{digits[-n:]}" if n else digits
    return f"-{text}" if q < 0 else text


def _frac(x: float | None) -> str:
    """评分值的读法：能识别成小分母的分数就写分数（中间量本来就是按分数算的），否则写浮点。"""
    if x is None:
        return "—"
    q = Fraction(x).limit_denominator(1000)
    return f"{q.numerator}/{q.denominator}" if float(q) == x and q.denominator != 1 else f"{x:.6g}"


def _month_rows(r: LifeYearResult) -> list[dict[str, Any]]:
    rows = []
    score = r.score
    out = r.settlement.outcome
    for m in range(1, 13):
        res = out.resources.months[m - 1] if out is not None and out.resources is not None else None
        row: dict[str, Any] = {
            "month": m,
            "onsite_visits": None if res is None else res.onsite_visits,
            "commute_hours_in_time_account": None
            if res is None
            else _dec(
                sum(
                    (c.hours.fraction() for c in res.contributions if c.category == "commute"), F(0)
                )
            ),
        }
        if score is not None:
            ms = score.months[m - 1]
            row["status"] = ms.status
            row["u"] = ms.u
            if ms.components:
                opp, com = _comp(score, m, "opportunity"), _comp(score, m, "commute")
                row["opportunity_s"] = opp.s
                row["growth_g"] = None if opp.growth is None else opp.growth.g
                row["growth_steps"] = (
                    []
                    if opp.growth is None
                    else [f"{s.ability_id} {s.before:g}→{s.after:g}" for s in opp.growth.steps]
                )
                row["commute_status"] = com.status
                row["commute_s"] = com.s
                if com.transport is not None:
                    t = com.transport
                    row["transport"] = {
                        "housing": t.housing_opportunity_id,
                        "T": t.minutes,
                        "D": t.onsite_visits,
                        "k": t.visit_factor,
                        "T_eq": t.equivalent_minutes,
                    }
                if com.status == "unknown":
                    row["commute_reason"] = com.reasons[0]
        rows.append(row)
    return rows


def _year_summary(r: LifeYearResult) -> dict[str, Any]:
    out = r.settlement.outcome
    learning = []
    if out is not None and out.career is not None:
        learning = [
            {
                "course": x.opportunity_id,
                "fee": x.fee_charged.amount,
                "months_attended": list(x.months_attended),
                "progress": f"{x.progress_before}→{x.progress_after}/{x.months_required}",
                "granted": list(x.granted),
                "usable_from_year": x.usable_from_year,
            }
            for x in out.career.learning
        ]
    return {
        "year": r.year_index,
        "settlement": r.settlement.status,
        "handoff": r.handoff,
        "problems": [f"{p.code}：{p.message}" for p in r.settlement.problems],
        "cash_end": None if out is None or out.cash_end is None else out.cash_end.amount,
        "quota_this_year": None
        if out is None or out.resources is None
        else sorted({_dec(m.energy_quota.fraction()) for m in out.resources.months}),
        "max_monthly_burden": None
        if out is None or out.resources is None
        else _dec(
            max(m.energy_used.fraction() for m in out.resources.months if m.energy_used is not None)
        ),
        "score_status": r.score.annual_status if r.score else r.score_problem,
        "S": r.score.annual_mean if r.score else None,
        "next_quota": _dec(Fraction(str(r.next_state.energy.level)))
        if r.next_state is not None and r.next_state.status == "active"
        else None,
        "learning": learning,
        "months": _month_rows(r),
    }


TABLE_HEAD = "\n".join(
    [
        "| 月 | 到岗 D | 时间账通勤（小时） | 发展分 | 成长 g | 交通 k | T_eq（分钟） | 交通分 | 月度总分 u |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
)


def _table(year: dict[str, Any]) -> list[str]:
    lines = [TABLE_HEAD]
    for row in year["months"]:
        t = row.get("transport") or {}
        cells = [
            str(row["month"]),
            str(row["onsite_visits"]),
            str(row["commute_hours_in_time_account"]),
            _frac(row.get("opportunity_s")),
            _frac(row.get("growth_g")),
            _frac(t.get("k")),
            _frac(t.get("T_eq")),
            _frac(row.get("commute_s")) if row.get("commute_status") != "unknown" else "未知",
            f"{row['u']:.6f}" if row.get("u") is not None else row.get("status", "—"),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(
        description="E1-5A 补充：成长满足感与广义交通评分的离线演示（不评城市、不排序）"
    )
    ap.add_argument("--out", type=Path, required=True, help="新的运行目录；已存在则拒绝")
    args = ap.parse_args()
    if args.out.exists():
        print(f"{args.out} 已存在：不覆盖旧结果，请换一个新目录")
        return 1
    try:
        bundle, life_report = load_life_bundle(LIFE, require_settlement_ready=True)
        scoring, score_report = load_scoring_config(SCORING, bundle)
    except LifeContractError as exc:
        print(exc)
        return 1
    scoring_raw = yaml.safe_load(SCORING.read_text(encoding="utf-8"))
    legacy_raw = _legacy(
        yaml.safe_load(LEGACY_SCORING.read_text(encoding="utf-8")), bundle.protocol.protocol_id
    )
    arms_raw = {
        "eta0": _arm(scoring_raw, "eta0", eta=0),
        "rho0": _arm(scoring_raw, "rho0", rho=0.0),
        "rho50": _arm(scoring_raw, "rho50", rho=0.5),
        "legacy_rules": legacy_raw,
    }
    arms: dict[str, ScoringConfig] = {}
    for name, data in arms_raw.items():
        config = parse_scoring_config(data)
        errors, _ = scoring_config_problems(config, bundle)
        if errors:
            print(f"对照配置 {name} 不合法：{errors}")
            return 1
        arms[name] = config

    main_world = World(bundle, scoring, bundle.protocol)
    runs = {c: main_world.run(c) for c in CLONES}
    starts = {c: main_world.start(c) for c in CLONES}

    # ---------------------------------------------------------------- 同一份生活事实换评分
    def rescored(config: ScoringConfig, clone: str, year: int = 1) -> Any:
        r = runs[clone][year - 1]
        start = starts[clone] if year == 1 else runs[clone][year - 2].next_state
        return main_world.rescore(start, r, config)

    eta0_learn = rescored(arms["eta0"], "u61.a71.learn")
    rho = {
        name: {c: _series(rescored(arms[name], c), "commute")[0] for c in TRAVEL}
        for name in ("rho0", "rho50")
    }
    rho["rho25"] = {c: _series(runs[c][0].score, "commute")[0] for c in TRAVEL}
    legacy_scores = {
        c: rescored(arms["legacy_rules"], c) for c in (*TRAVEL, "u64.a71.study", "u61.a71.learn")
    }

    # ---------------------------------------------------------------- 反馈开启后的跨年影响
    legacy_protocol = _revise(
        bundle.protocol,
        energy_feedback=_revise(
            bundle.protocol.energy_feedback,
            policy_id="feedback.e1-5a-gt.legacy-rules",
            scoring_spec_id=arms["legacy_rules"].spec.spec_id,
            scoring_version=arms["legacy_rules"].spec.scoring_version,
            # 对照臂读另一套评分内容，就声明另一套（DECISIONS/0025）；沿用主政策的声明会被正确地拒绝
            scoring_content_fingerprint=arms["legacy_rules"].spec.content_fingerprint,
        ),
    )
    off_protocol = _revise(
        bundle.protocol,
        energy_feedback=_revise(
            bundle.protocol.energy_feedback, mode="disabled", policy_id="feedback.e1-5a-gt.off"
        ),
    )
    legacy_world = World(bundle, arms["legacy_rules"], legacy_protocol)
    off_world = World(bundle, scoring, off_protocol)
    cross = {
        c: {
            "new_rules": [_year_summary(r) for r in runs[c]],
            "legacy_rules": [_year_summary(r) for r in legacy_world.run(c)],
        }
        for c in ("u63.a71.remote", "u64.a71.study", "u63.a71.hybrid", "u61.a71.learn")
    }
    # 一份第二年计划：远程客服 + 基本食材 + 会计课 + 自学 Python，每月负担 182.5 + 48 + 30 = 260.5
    heavy = main_world.plan(
        "u63.a71.remote",
        2,
        _item("a71.housing.near"),
        _item("a71.food.basic"),
        _item("a71.job.support_remote"),
        _item("a71.training.accounting_course"),
        _item("a71.training.python_selfstudy"),
        why="第二年多学两门：会计课与 Python 自学，伙食改成基本方案",
    )
    remote_y2_new = runs["u63.a71.remote"][0].next_state
    assert remote_y2_new is not None
    heavy_new = main_world.year(remote_y2_new, heavy)
    off_y1 = off_world.run("u63.a71.remote", scored=False)[0]
    assert off_y1.next_state is not None
    heavy_off = off_world.year(off_y1.next_state, heavy, scored=False)
    legacy_y1 = legacy_world.run("u63.a71.remote")[0]
    feasibility = {
        "plan": [i.model_dump(mode="json") for i in heavy.items],
        "monthly_burden": "182.5 + 48 + 30 = 260.5",
        "new_rules": {
            "year2_quota": _dec(Fraction(str(remote_y2_new.energy.level))),
            "status": heavy_new.settlement.status,
        },
        "feedback_off": {
            "year2_quota": _dec(Fraction(str(off_y1.next_state.energy.level))),
            "status": heavy_off.settlement.status,
            "problems": [p.code for p in heavy_off.settlement.problems],
        },
        "legacy_rules": {
            "year1_handoff": legacy_y1.handoff,
            "note": "原规则下第 1 年远程月份通勤不支持 → 反馈受阻，没有可用的第 2 年状态",
        },
    }

    # ---------------------------------------------------------------- 覆盖、观察
    u63_pref = preference_from_stated_profile(main_world.profile("u63"))

    def coverage(config: ScoringConfig) -> Any:
        return coverage_report(
            spec=config.spec,
            group=config.comparison_group,  # type: ignore[arg-type]
            conditions=config.city_conditions,
            catalogs=bundle.catalogs,
            preference=u63_pref,  # type: ignore[arg-type]
        )

    cov_new, cov_old = coverage(scoring), coverage(arms["legacy_rules"])
    state2 = runs["u63.a71.remote"][0].next_state
    assert state2 is not None
    catalog = bundle.catalog_for(str(state2.city))
    assert catalog is not None
    obs = build_observation(
        permission=standard_product_permission(bundle.protocol.info_permission_version),
        profile=main_world.profile("u63"),
        state=state2,
        catalog=catalog,
        resource_rules=bundle.protocol.resource_rules,
        energy_feedback=bundle.protocol.energy_feedback,
        career_rules=bundle.protocol.career_rules,
    )
    payload = observation_payload(obs)
    blob = json.dumps(payload, ensure_ascii=False)

    # ---------------------------------------------------------------- 手算核对
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append((name, bool(ok), detail))

    learn, plain = runs["u61.a71.learn"][0].score, runs["u61.a71.nolearn"][0].score
    l62, p62 = runs["u62.a71.learn"][0].score, runs["u62.a71.nolearn"][0].score
    diff61 = [x.u - y.u for x, y in zip(learn.months, plain.months, strict=True)]
    check(
        "例一·看重发展的人",
        _series(learn, "opportunity") == [0.6] * 12
        and _series(plain, "opportunity") == [0.5] * 12
        and all(abs(d - 0.02) < 1e-12 for d in diff61),
        "学习月 z = 2 + 12 × 1/12 = 3，3/5 = 0.6；不学 2/4 = 0.5；发展权重 0.2：每月总分差 0.2 × 0.1 = 0.02",
    )
    check(
        "例一·不看重发展的人",
        _series(l62, "opportunity") == [0.6] * 12
        and [m.u for m in l62.months] == [m.u for m in p62.months],
        "发展权重 0：同样学了会计、发展维同样是 0.6，月度总分一点不变",
    )
    y1, y2, y3 = runs["u61.a71.pause"]
    fees = [y.settlement.outcome.career.learning[0].fee_charged.amount for y in (y1, y3)]  # type: ignore[union-attr]
    held = y3.next_state.abilities[0]  # type: ignore[union-attr]
    check(
        "例二·学习暂停",
        _series(y1.score, "opportunity") == [0.6] * 6 + [0.5] * 6
        and _series(y2.score, "opportunity") == [0.5] * 12
        and _series(y3.score, "opportunity") == [0.6] * 6 + [0.5] * 6
        and fees == [1200.0, 0.0]
        and (held.acquired_year, held.acquired_month, held.usable_from_year) == (3, 6, 4),
        "第 1 年第 1–6 月 0.6、之后 0.5；第 2 年暂停全年 0.5；第 3 年第 1–6 月 0.6（只计新增 6/12），学费只第 1 年付 1,200；"
        "第 3 年第 6 月学完，第 4 年起可用",
    )
    expected = {
        "u63.a71.onsite": (20, F(2, 3)),
        "u63.a71.hybrid": (10, F(16, 21)),
        "u63.a71.remote": (0, F(8, 9)),
        "u63.a71.parttime": (20, F(2, 3)),
        "u63.a71.shifts": (40, F(8, 15)),
    }
    got = {
        c: (
            _comp(runs[c][0].score, 1, "commute").transport.onsite_visits,
            _series(runs[c][0].score, "commute"),
        )
        for c in TRAVEL
    }
    check(
        "例三·不同出行安排",
        all(got[c][0] == d and got[c][1] == [float(s)] * 12 for c, (d, s) in expected.items()),
        "τ_T = 60、ρ = 0.25、D_ref = 20、T = 30：天天到岗 D = 20 → 60/90 = 2/3；混合 10 → k = 0.625、T_eq = 18.75 → 16/21；"
        "远程 0 → k = 0.25、T_eq = 7.5 → 8/9；两份兼职 10 + 10 = 20 → 2/3；早晚两班 40 → k = 1.75、T_eq = 52.5 → 8/15",
    )
    study = runs["u64.a71.study"][0].score
    check(
        "例三·不工作专心自学的一年",
        study.annual_status == "complete"
        and _series(study, "commute") == [float(F(8, 9))] * 12
        and _series(study, "opportunity") == [0.6] * 12,
        "没有到岗 → 交通 8/9；自学 Python 每月 1/12 → 发展 0.6；全年完整可评（原规则下通勤报不支持）",
    )
    check(
        "η_growth = 0 对照",
        _series(eta0_learn, "opportunity") == [0.5] * 12
        and [m.u for m in eta0_learn.months] == [m.u for m in plain.months],
        "同一份学会计的生活事实，关掉成长项：发展维只剩背景 0.5，总分与不学相同",
    )
    check(
        "ρ 对照",
        rho["rho0"]["u63.a71.remote"] == 1.0
        and rho["rho25"]["u63.a71.remote"] == float(F(8, 9))
        and rho["rho50"]["u63.a71.remote"] == float(F(4, 5))
        and {rho[n]["u63.a71.onsite"] for n in rho} == {float(F(2, 3))},
        "远程：ρ = 0 → T_eq = 0 → 1；ρ = 0.25 → 8/9；ρ = 0.5 → T_eq = 15 → 60/75 = 4/5；天天到岗 k = 1，与 ρ 无关，都是 2/3",
    )
    legacy_incomplete = [
        c for c in (*TRAVEL, "u64.a71.study") if legacy_scores[c].annual_status != "complete"
    ]
    check(
        "原规则对照（同一份生活事实）",
        legacy_incomplete
        == [
            "u63.a71.hybrid",
            "u63.a71.remote",
            "u63.a71.parttime",
            "u63.a71.shifts",
            "u64.a71.study",
        ]
        and _series(legacy_scores["u63.a71.onsite"], "commute")
        == _series(runs["u63.a71.onsite"][0].score, "commute")
        and _series(legacy_scores["u61.a71.learn"], "opportunity") == [0.5] * 12,
        "原规则只评恰好一份线下工作的月份：混合、远程、两份工作、不工作都报不支持；天天到岗一份工作新旧同分；原规则下学习不计分",
    )
    hours = {
        "u63.a71.onsite": F(10),
        "u63.a71.hybrid": F(5),
        "u63.a71.remote": F(0),
        "u63.a71.parttime": F(10),
        "u63.a71.shifts": F(20),
    }
    check(
        "时间账不变",
        all(
            sum(
                (
                    c.hours.fraction()
                    for c in runs[k][0].settlement.outcome.resources.months[0].contributions
                    if c.category == "commute"
                ),  # type: ignore[union-attr]
                F(0),
            )
            == h
            for k, h in hours.items()
        ),
        "工作通勤小时 = Σ 30 分钟 × 各岗位到岗次数 / 60：10、5、0、5 + 5、10 + 10；评分里的 k 与 T_eq 不进时间账",
    )
    rr = cross["u63.a71.remote"]
    check(
        "反馈开启后的跨年影响",
        rr["legacy_rules"][0]["handoff"] == "feedback_blocked"
        and rr["new_rules"][0]["next_quota"] == "264.75"
        and feasibility["new_rules"]["status"] == "completed"
        and feasibility["feedback_off"]["status"] == "plan_infeasible",
        "远程客服：原规则下第 1 年通勤不支持 → 反馈受阻；新规则 S = 0.590277… → 第 2 年额度 250 × (1 + 0.1 × S) 向下取 0.01 = "
        "264.75；第 2 年每月负担 260.5 的计划在 264.75 下可行，在关闭反馈的 250 下不可行",
    )
    leaves_new = {x.leaf: x for x in cov_new.leaves}
    leaves_old = {x.leaf: x for x in cov_old.leaves}
    check(
        "覆盖报告与评分器一致",
        leaves_new["commute"].action_scope == "all_arrangements"
        and leaves_new["commute"].support == "supported"
        and leaves_new["opportunity"].action_mechanism
        and leaves_old["commute"].action_scope == "conditional"
        and not leaves_old["opportunity"].action_mechanism,
        "新规则：交通对任何安排都能评、发展维有行动机制；原规则：通勤看安排、发展维只有背景",
    )
    check(
        "可见输入不含评分参数",
        not any(
            k in blob
            for k in (
                "rho",
                "reference_visits",
                "visit_factor",
                "equivalent_minutes",
                "progress_rule",
                '"eta"',
            )
        ),
        "第 2 年观察里有工作安排（到岗次数）、住房往返分钟、学习进度；没有 ρ、D_ref、k、T_eq、η_growth 或分数",
    )

    # ---------------------------------------------------------------- write
    args.out.mkdir(parents=True)
    cfg = args.out / "configs"
    (cfg / "arms").mkdir(parents=True)
    shutil.copyfile(LIFE, cfg / LIFE.name)
    shutil.copyfile(SCORING, cfg / SCORING.name)
    for name, data in arms_raw.items():
        src = "scoring_e1_5a.yaml（搬到新协议）" if name == "legacy_rules" else SCORING.as_posix()
        (cfg / "arms" / f"{name}.yaml").write_text(
            f"# 对照配置 {name}：由 {src} 派生（原文件不改）。注释在派生时丢失。\n"
            + yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    def dump(name: str, data: Any) -> None:
        path = args.out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )

    dump("environment.json", _environment())
    dump(
        "command.json",
        {
            "argv": sys.argv,
            "python_executable": sys.executable,
            "cwd": os.getcwd(),
            "reproduce": "uv run python scripts/demo_life_e1_5a_growth_transport.py --out <新目录>",
        },
    )
    dump(
        "protocol.json",
        {
            "protocol": bundle.protocol.model_dump(mode="json"),
            "scoring_spec": {
                "spec_id": scoring.spec.spec_id,
                "scoring_version": scoring.spec.scoring_version,
            },
            "policy_content_fingerprints": {
                "main": bundle.protocol.energy_feedback.content_fingerprint,  # type: ignore[union-attr]
                "legacy_rules": legacy_protocol.energy_feedback.content_fingerprint,  # type: ignore[union-attr]
                "feedback_off": off_protocol.energy_feedback.content_fingerprint,  # type: ignore[union-attr]
            },
            "feedback_handoff_revision": bundle.protocol.energy_feedback.handoff_revision,  # type: ignore[union-attr]
            "resource_rules_revision": RESOURCE_RULES_REVISION,
            "formal_scoring_ready": bundle.protocol.scoring_ready,
            "source_table": list(source_table(scoring.spec)),
        },
    )
    dump(
        "inputs.json",
        {
            "life_file": str(LIFE),
            "life_sha256": _sha256(LIFE),
            "scoring_file": str(SCORING),
            "scoring_sha256": _sha256(SCORING),
            "legacy_scoring_file": str(LEGACY_SCORING),
            "legacy_scoring_sha256": _sha256(LEGACY_SCORING),
            "life_report": life_report.model_dump(mode="json"),
            "scoring_report": score_report.model_dump(mode="json"),
        },
    )
    yearly = {c: [_year_summary(r) for r in rs] for c, rs in runs.items()}
    dump("monthly_growth_transport.json", yearly)
    dump(
        "same_facts_rescored.json",
        {
            "eta0_u61_learn_year1": {
                "opportunity": _series(eta0_learn, "opportunity"),
                "u": [m.u for m in eta0_learn.months],
            },
            "rho_first_month_commute": rho,
            "legacy_rules_year1": {
                c: {
                    "annual_status": s.annual_status,
                    "commute": _series(s, "commute"),
                    "opportunity": _series(s, "opportunity"),
                }
                for c, s in legacy_scores.items()
            },
        },
    )
    dump("feedback_cross_year.json", cross)
    dump("feasibility_year2.json", feasibility)
    dump("coverage_new_rules_u63.json", cov_new.model_dump(mode="json"))
    dump("coverage_legacy_rules_u63.json", cov_old.model_dump(mode="json"))
    dump("observations/u63_remote_year2_payload.json", payload)

    lines = [
        "# E1-5A 补充：成长满足感与广义交通（合成演示）",
        "",
        "金额按当地基年不变价（USD_2024_LOCAL）；时间是小时；负担与额度是负担点；满足感是 0–1 的无量纲数。全部参数是合成"
        "探索设定，**不是现实的成长感、出行感受或城市排行**。分数里的分数（如 2/3）是精确值：评分中间量按分数算，最后才"
        "转成浮点。",
        "",
        "发展维：z = b + η_growth × g（b = 2、τ = 2、上限 0.9、η_growth = 12）。交通：k = ρ + (1 − ρ)·D/D_ref，T_eq = k·T，"
        "S = τ_T/(τ_T + T_eq)（ρ = 0.25、D_ref = 20、τ_T = 60）。“时间账通勤”是资源账里实际计的通勤小时，评分不改它。",
        "",
    ]
    titles = {
        "u61.a71.learn": "例一 看重发展（u61，发展权重 0.2）：上会计课",
        "u61.a71.nolearn": "例一 看重发展（u61）：不学",
        "u62.a71.learn": "例一 不看重发展（u62，发展权重 0）：上会计课",
        "u62.a71.nolearn": "例一 不看重发展（u62）：不学",
        "u61.a71.pause": "例二 学习暂停（u61）：第 1 年上半年上课、第 2 年暂停、第 3 年恢复",
        "u63.a71.onsite": "例三 天天到岗（u63，交通权重 0.2）",
        "u63.a71.hybrid": "例三 混合办公，每月到岗 10 次",
        "u63.a71.remote": "例三 纯远程",
        "u63.a71.parttime": "例三 两份兼职，各到岗 10 次",
        "u63.a71.shifts": "例三 早晚两班，各到岗 20 次",
        "u64.a71.study": "例三 有积蓄、第一年不工作专心自学（u64）",
    }
    for clone, title in titles.items():
        lines += [f"## {title}", ""]
        for y in yearly[clone]:
            learn_text = (
                "；".join(
                    f"{x['course'].split('.')[-1]} 学费 {x['fee']:g}，参加 {len(x['months_attended'])} 个月，进度 {x['progress']}"
                    + (
                        f"，授予 {x['granted']}（第 {x['usable_from_year']} 年起可用）"
                        if x["granted"]
                        else ""
                    )
                    for x in y["learning"]
                )
                or "无"
            )
            lines += [
                f"第 {y['year']} 年：结算 {y['settlement']}，交接 {y['handoff']}，年度满足感 S = {y['S']}（{y['score_status']}），"
                f"本年额度 {y['quota_this_year']}、下一年额度 {y['next_quota'] or '—'}，月最大负担 {y['max_monthly_burden']}，"
                f"年末现金 {y['cash_end']:,.0f}；学习：{learn_text}",
                "",
            ]
            if (
                clone in TRAVEL
                or clone == "u64.a71.study"
                or y["year"] == 1
                or clone == "u61.a71.pause"
            ):
                lines += [*_table(y), ""]
    lines += [
        "## 同一份生活事实换评分规则",
        "",
        "| 分身（第 1 年） | 新规则交通分 | ρ = 0 | ρ = 0.5 | 原规则（方案级通勤） |",
        "|---|---|---|---|---|",
    ]
    for c in TRAVEL:
        old_c = _series(legacy_scores[c], "commute")[0]
        lines.append(
            f"| {c} | {_frac(rho['rho25'][c])} | {_frac(rho['rho0'][c])} | {_frac(rho['rho50'][c])} | "
            f"{_frac(old_c) if old_c is not None else '不支持'} |"
        )
    lines += [
        "",
        f"η_growth = 0 对照：u61 第 1 年学会计，发展维 {_frac(_series(eta0_learn, 'opportunity')[0])}（新规则 0.6），"
        "月度总分与不学相同。",
        "",
        "## 反馈开启后的跨年影响",
        "",
        "| 分身 | 规则 | 第 1 年 S（状态） | 第 1 年交接 | 第 2 年额度 |",
        "|---|---|---|---|---|",
    ]
    for c, v in cross.items():
        for label, key in (("新规则", "new_rules"), ("原规则", "legacy_rules")):
            y = v[key][0]
            lines.append(
                f"| {c} | {label} | {y['S'] if y['S'] is not None else '—'}（{y['score_status']}） | {y['handoff']} | {y['next_quota'] or '—'} |"
            )
    lines += [
        "",
        f"一份第二年计划（远程客服 + 基本食材 + 会计课 + 自学 Python，每月负担 {feasibility['monthly_burden']}）：新规则下第 2 年"
        f"额度 {feasibility['new_rules']['year2_quota']}，结算 {feasibility['new_rules']['status']}；关闭反馈（额度 "
        f"{feasibility['feedback_off']['year2_quota']}）下 {feasibility['feedback_off']['status']}；原规则下第 1 年就是 "
        f"{feasibility['legacy_rules']['year1_handoff']}，没有第 2 年状态。这是新评分**预期**带来的变化。",
        "",
    ]
    (args.out / "yearly_tables.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    check_lines = [
        "# 手算核对",
        "",
        "评分中间量按精确分数比较；月度总分差只在浮点加法的最后一位以内（1e-12）。",
        "",
        *(f"- {'通过' if ok else '不通过'}　{name}：{detail}" for name, ok, detail in checks),
    ]
    (args.out / "checks.md").write_text("\n".join(check_lines) + "\n", encoding="utf-8")
    dump("checks.json", [{"check": n, "passed": ok, "detail": d} for n, ok, d in checks])
    (args.out / "README.md").write_text(
        "\n".join(
            [
                "# E1-5A 补充：成长满足感与广义交通（演示运行证据）",
                "",
                "零大模型调用的离线演示（`DECISIONS/0024`）。全部数值是合成设定下的结果，**不是现实的成长感、出行感受或城市排行**。",
                "",
                "- `command.json` / `environment.json`：实际命令、git 状态、执行修订与源码指纹",
                "- `configs/`：生活输入与评分配置副本；`configs/arms/`：派生的对照配置（η = 0、ρ = 0、ρ = 0.5、原规则）",
                "- `protocol.json` / `inputs.json`：协议、反馈政策内容指纹（主、原规则、关闭）、评分来源表与加载报告",
                "- `monthly_growth_transport.json` / `yearly_tables.md`：逐月成长、到岗、k、T_eq、交通分与月度总分",
                "- `same_facts_rescored.json`：同一份生活事实换评分规则（η、ρ、原规则）",
                "- `feedback_cross_year.json` / `feasibility_year2.json`：反馈开启后的跨年影响与计划可行性的变化",
                "- `coverage_*.json`：新规则与原规则的覆盖报告；`observations/`：第 2 年的实际 payload",
                "- `checks.md` / `checks.json`：手算核对",
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
