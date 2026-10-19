"""E1-5A 学习与技能 → 岗位选择 → 实际收入的离线演示（`DECISIONS/0023`）。零大模型调用。

    uv run python scripts/demo_life_e1_5a.py --out runs/<新目录>

读 E1-5A 合成生活输入与探索评分配置，按预先声明的年度计划逐年做**真实的**年度交接（结算 → 评分 → 反馈 →
下一年），另有几个由同一份配置派生的边界情景（只换某一年的计划，原文件不改）。输出：

* 例 1 有无学习：同一个人（u51）学会计 / 不学 / 学了但不换岗，三年的学费、进度、能力、岗位、工资、税与现金；
* 例 2 资格不泛化：会计资格不能做开发；同一家公司的不同岗位按各自的要求判断；Python 打开开发岗位；
* 例 3 免费学习也占资源：零价自学的时间与负担；同一年学两门超出额度 → 计划不可行，不留下任何成果；
* 例 4 作用域与跨年：网鹊科技的内部规则只用于网鹊科技；年中开课跨年延续、不重复收费、下一年才生效；
* 出局、终态与反馈受阻：已经发生的学习事实保留，不虚构之后的学习或收入；
* 能力—岗位对照：按年初的真实状态，每组能力能选哪些岗位、缺什么；
* 观察样例：代理第 2 年能看到的能力、进度、学费、时间与负担、课程授予什么、何时生效、岗位还缺什么；
* 年度绑定：同人同年两次真实试算（上课 / 不上课）不能互换候选状态；
* 互不竞争：单独运行与逐年轮流运行逐字相同；两个计划相同的分身都拿到同一种岗位；
* 手算核对。

**合成设定：** 学费、学习时长与负担、工资、税与额度都是手工设定的整数；不是现实培训回报或职业发展的结论，
不是录用规则，也不排序城市。学习本身不进入满足感（"学习体验 / 职业成就"满足感登记为未实现）。

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
    visible_feedback,
)
from common.life_contracts import (
    CAREER_RULES_VERSION,
    FEEDBACK_HANDOFF_REVISION,
    RESOURCE_RULES_REVISION,
    LifeContractError,
    PersonYearState,
    YearAction,
    qualification_gaps,
    to_fraction,
)
from common.life_feedback_contracts import LifeYearResult
from econ.life_feedback import apply_feedback, feedback_record
from econ.life_settlement import SETTLEMENT_CONVENTION, binding_from_policy
from econ.life_year import ScoringInputs, run_life_year, settle_and_score
from ledger.life_catalog import LifeBundle, cross_validate, load_life_bundle, parse_bundle
from ledger.life_scoring_config import load_scoring_config

REPO = Path(__file__).resolve().parent.parent
LIFE = Path("configs/life/synthetic_e1_5a.yaml")
SCORING = Path("configs/life/scoring_e1_5a.yaml")
KEY_SOURCES = (
    "src/common/life_contracts.py",
    "src/common/life_feedback_contracts.py",
    "src/ledger/life_catalog.py",
    "src/ledger/life_scoring_config.py",
    "src/econ/life_career.py",
    "src/econ/life_resources.py",
    "src/econ/life_settlement.py",
    "src/econ/life_scoring.py",
    "src/econ/life_feedback.py",
    "src/econ/life_year.py",
    "src/agentlayer/life_views.py",
    "scripts/demo_life_e1_5a.py",
    "tests/life/career_support.py",
    "tests/life/test_career_contracts.py",
    "tests/life/test_career_settlement.py",
    "tests/life/test_career_wiring.py",
)

CLERK = "a71.job.clerk_netco"
ACCOUNTANT = "a71.job.accountant_netco"
DEVELOPER = "a71.job.developer_netco"
HR_NETCO = "a71.job.hr_netco"
HR_LEDGERCO = "a71.job.hr_ledgerco"
ACCOUNTING = "a71.training.accounting_course"
PYTHON = "a71.training.python_selfstudy"
HR_TRAINING = "a71.training.hr_rules_netco"
U51 = (
    "u51.a71.study",
    "u51.a71.stay",
    "u51.a71.nostudy",
    "u51.a71.python",
    "u51.a71.midyear",
    "u51.a71.hr",
)


def _item(oid: str, start: int = 1, end: int = 12) -> dict[str, Any]:
    return {"opportunity_id": oid, "quantity": 1, "start_month": start, "end_month": end}


HOME = _item("a71.housing.near")
FOOD = _item("a71.food.fresh")
WASHER = {"opportunity_id": "a71.durable.washer", "quantity": 1, "month": 1}


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
    files = [*KEY_SOURCES, str(LIFE), str(SCORING)]
    return {
        "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "git_head": _git("rev-parse", "HEAD"),
        "git_dirty": None if status is None else bool(status),
        "git_status_porcelain": None if status is None else status.splitlines(),
        "note": "工作区未提交时，git_head 不能代表实际运行的代码；以下指纹才是实际输入",
        "resource_rules_revision": RESOURCE_RULES_REVISION,
        "feedback_handoff_revision": FEEDBACK_HANDOFF_REVISION,
        "career_rules_version": CAREER_RULES_VERSION,
        "fingerprints_sha256": {f: _sha256(REPO / f) for f in files},
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }


def _dec(q: Fraction | None) -> str | None:
    """精确有理数的十进制写法：有限小数原样写全（不舍入），否则写分数。"""
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


def _amt(value: Any) -> str:
    """金额（当地基年不变价）：整数加千分位，否则写精确小数。"""
    q = value if isinstance(value, Fraction) else to_fraction(value)
    return f"{q.numerator:,}" if q.denominator == 1 else (_dec(q) or "0")


class World:
    def __init__(self, bundle: LifeBundle, scoring: Any) -> None:
        self.b, self.scoring = bundle, scoring

    def inputs(self, state: PersonYearState) -> ScoringInputs:
        profile = next(p for p in self.b.stated_profiles if p.person_id == state.person_id)
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

    def year(self, state: PersonYearState, plan: YearAction) -> LifeYearResult:
        catalog, tax = self._city(state)
        return run_life_year(
            protocol=self.b.protocol,
            state=state,
            action=plan,
            catalog=catalog,
            tax=tax,
            scoring=self.inputs(state),
        )

    def bind(self, state: PersonYearState, plan: YearAction) -> Any:
        catalog, tax = self._city(state)
        return settle_and_score(
            protocol=self.b.protocol,
            state=state,
            action=plan,
            catalog=catalog,
            tax=tax,
            scoring=self.inputs(state),
        )

    def start(self, clone: str) -> PersonYearState:
        s = self.b.initial_state(clone)
        assert s is not None
        return s

    def declared(self, clone: str, year: int) -> YearAction:
        return next(p for p in self.b.plans if p.clone_id == clone and p.year_index == year)

    def plan(self, clone: str, year: int, *items: dict[str, Any], why: str) -> YearAction:
        """同一个分身同一年换一组行动（派生情景；原配置不改）。"""
        data = self.declared(clone, year).model_dump(mode="json")
        data["items"] = list(items)
        data["rationale"] = why
        return YearAction.model_validate(data)

    def run(self, clone: str) -> list[LifeYearResult]:
        state = self.start(clone)
        out: list[LifeYearResult] = []
        for plan in self.b.plans_for(clone):
            if plan.year_index != state.year_index:
                continue
            r = self.year(state, plan)
            out.append(r)
            if r.handoff != "next_year_ready" or r.next_state is None:
                break
            state = r.next_state
        return out


def _learning_text(x: Any) -> str:
    done = f"第 {x.completed_month} 月完成" if x.completed_month else "未完成"
    months = (
        f"第 {x.months_attended[0]}–{x.months_attended[-1]} 月" if x.months_attended else "没有参加"
    )
    text = (
        f"{x.opportunity_id.split('.')[-1]}：学费 {_amt(x.fee_charged.amount)}，{months}，"
        f"进度 {x.progress_before}→{x.progress_after}/{x.months_required}，{done}"
    )
    if x.granted:
        text += f"，授予 {'、'.join(x.granted)}（第 {x.usable_from_year} 年起可用）"
    if x.already_held:
        text += f"，{'、'.join(x.already_held)} 已有、不再授予"
    return text


def _row(r: LifeYearResult) -> dict[str, Any]:
    s = r.settlement
    out = s.outcome
    career = out.career if out is not None else None
    months = out.resources.months if out is not None and out.resources is not None else ()
    used = [m.energy_used.fraction() for m in months if m.energy_used is not None]
    hours = [m.time_used.fraction() for m in months if m.time_used is not None]
    nxt = s.next_state
    gross = tax = None
    if out is not None:
        gross = sum(
            (to_fraction(f.amount.amount) for f in out.cash_flows if f.kind == "income_gross"),
            Fraction(0),
        )
        tax = -sum(
            (to_fraction(f.amount.amount) for f in out.cash_flows if f.kind == "income_tax"),
            Fraction(0),
        )
    abilities_after = []
    if nxt is not None:
        for a in nxt.abilities:
            when = (
                "开局已有"
                if a.source == "initial"
                else f"第 {a.acquired_year} 年第 {a.acquired_month} 月学会"
            )
            abilities_after.append(f"{a.ability_id}（{when}，第 {a.usable_from_year} 年起可用）")
    return {
        "year": r.year_index,
        "settlement": s.status,
        "handoff": r.handoff,
        "problems": [f"{p.code}：{p.message}" for p in s.problems],
        "abilities_at_start": None if career is None else list(career.abilities_at_start),
        "positions_at_start": None if career is None else list(career.positions_at_start),
        "learning": [] if career is None else [_learning_text(x) for x in career.learning],
        "learning_records": []
        if career is None
        else [x.model_dump(mode="json") for x in career.learning],
        "positions": []
        if career is None
        else [f"{p.opportunity_id}（{len(p.months)} 个月）" for p in career.positions],
        "income_gross": None if gross is None else _amt(gross),
        "income_tax": None if tax is None else _amt(tax),
        "cash_start": None
        if out is None or out.cash_start is None
        else _amt(out.cash_start.amount),
        "cash_end": None if out is None or out.cash_end is None else _amt(out.cash_end.amount),
        "exit_month": None if out is None or out.exit is None else out.exit.month,
        "max_monthly_hours": _dec(max(hours)) if hours else None,
        "max_monthly_burden": _dec(max(used)) if used else None,
        "quota": sorted({_dec(m.energy_quota.fraction()) or "" for m in months}),
        "score_mean": r.score.annual_mean if r.score else None,
        "score_status": r.score.annual_status if r.score else r.score_problem,
        "next_quota": _dec(to_fraction(r.next_state.energy.level))
        if r.next_state is not None and r.next_state.status == "active"
        else None,
        "abilities_after": abilities_after,
        "current_after": None
        if nxt is None or nxt.employment is None
        else list(nxt.employment.current_position_ids),
        "occupation_after": None
        if nxt is None or nxt.occupation is None
        else f"{nxt.occupation.scheme}:{nxt.occupation.code}",
        "candidate_quota_status": None
        if nxt is None or nxt.energy_quota_basis is None
        else nxt.energy_quota_basis.status,
    }


TABLE_HEAD = "\n".join(
    [
        "| 年 | 结算 / 交接 | 年初能力 | 年初岗位 | 这一年的学习 | 实际岗位 | 税前收入 | 合成税 | 年末现金 "
        "| 月最多用时（小时） | 月最大负担 / 额度 | 年末能力 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
)


def _fmt(row: dict[str, Any]) -> str:
    def short(xs: list[str] | None) -> str:
        if xs is None:
            return "—"
        return "；".join(x.split(".")[-1] if x.startswith("a71.") else x for x in xs) or "无"

    quota = "、".join(row["quota"]) or "—"
    burden = row["max_monthly_burden"] or "—"
    cells = [
        str(row["year"]),
        f"{row['settlement']} / {row['handoff']}",
        short(row["abilities_at_start"]),
        short(row["positions_at_start"]),
        "；".join(row["learning"]) or "无",
        short(row["positions"]),
        row["income_gross"] or "—",
        row["income_tax"] or "—",
        row["cash_end"] or "—",
        row["max_monthly_hours"] or "—",
        f"{burden} / {quota}",
        "；".join(row["abilities_after"]) or "无",
    ]
    return "| " + " | ".join(cells) + " |"


def _gaps_text(opportunity: Any, state: PersonYearState) -> str:
    gaps = qualification_gaps(opportunity, state)
    if not gaps:
        return "可选"
    return "不可选：" + "；".join(g.target for g in gaps)


def main() -> int:
    ap = argparse.ArgumentParser(description="E1-5A 学习—岗位—收入的离线演示（不评城市、不排序）")
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
    raw = yaml.safe_load(LIFE.read_text(encoding="utf-8"))
    w = World(bundle, scoring)
    catalog = bundle.catalog_for(str(w.start("u51.a71.study").city))
    assert catalog is not None

    # ---------------------------------------------------------------- 按样例声明的计划逐年运行
    runs = {c: w.run(c) for c in [*U51, "u53.a71.c0", "u54.a71.c0"]}
    study, nostudy = runs["u51.a71.study"], runs["u51.a71.nostudy"]
    python, midyear, hr = runs["u51.a71.python"], runs["u51.a71.midyear"], runs["u51.a71.hr"]

    # ---------------------------------------------------------------- 派生的边界情景（只换某一年的计划）
    derived: dict[str, dict[str, Any]] = {}

    def attempt(name: str, state: PersonYearState, plan: YearAction, note: str) -> LifeYearResult:
        r = w.year(state, plan)
        derived[name] = {
            "note": note,
            "clone": state.clone_id,
            "year": state.year_index,
            "plan": [i.model_dump(mode="json") for i in plan.items],
            "row": _row(r),
        }
        return r

    s2_study = study[0].next_state
    s2_hr = hr[0].next_state
    s2_mid = midyear[0].next_state
    assert s2_study is not None and s2_hr is not None and s2_mid is not None
    dev_try = attempt(
        "ex2_accountant_cert_for_developer",
        s2_study,
        w.plan("u51.a71.study", 2, HOME, FOOD, _item(DEVELOPER), why="有会计资格了，去做开发"),
        "会计资格不满足开发岗位的 Python 要求（同一家互联网公司）",
    )
    ledger_try = attempt(
        "ex4_netco_rules_for_ledgerco",
        s2_hr,
        w.plan("u51.a71.hr", 2, HOME, FOOD, _item(HR_LEDGERCO), why="懂人事规则了，去账房事务所"),
        "网鹊科技的内部人事规则不满足账房事务所人事岗位的要求",
    )
    both_try = attempt(
        "ex3_two_courses_over_quota",
        w.start("u51.a71.nostudy"),
        w.plan(
            "u51.a71.nostudy",
            1,
            HOME,
            FOOD,
            _item(CLERK),
            WASHER,
            _item(ACCOUNTING),
            _item(PYTHON),
            why="同一年上会计课、再自学 Python",
        ),
        "195 + 48 + 30 = 273 > 250：计划不可行",
    )
    same_year_try = attempt(
        "ex4_same_year_switch",
        s2_mid,
        w.plan(
            "u51.a71.midyear",
            2,
            HOME,
            FOOD,
            _item(CLERK, 1, 6),
            _item(ACCOUNTANT, 7, 12),
            _item(ACCOUNTING, 1, 6),
            why="上半年学完，下半年就去做会计",
        ),
        "当年第 6 月完成的学习不追溯改变当年的可选岗位",
    )

    # ---------------------------------------------------------------- 能力—岗位对照（真实年初状态）
    s3_study = study[1].next_state
    assert s3_study is not None
    starts = {
        "不学（第 2 年年初）": nostudy[0].next_state,
        "学会计（第 2 年年初）": s2_study,
        "自学 Python（第 2 年年初）": python[0].next_state,
        "网鹊科技内训（第 2 年年初）": s2_hr,
        "年中开课（第 2 年年初，6/12）": s2_mid,
        "学会计、已做会计（第 3 年年初）": s3_study,
    }
    targets = (ACCOUNTANT, DEVELOPER, HR_NETCO, HR_LEDGERCO, HR_TRAINING)
    matrix = {
        name: {
            "abilities": [a.ability_id for a in s.abilities],  # type: ignore[union-attr]
            "current": list(s.employment.current_position_ids),  # type: ignore[union-attr]
            "options": {t: _gaps_text(catalog.opportunity(t), s) for t in targets},  # type: ignore[arg-type]
        }
        for name, s in starts.items()
    }

    # ---------------------------------------------------------------- 观察样例（标准产品许可）
    def observe(state: PersonYearState, last: LifeYearResult) -> dict[str, Any]:
        profile = next(p for p in bundle.stated_profiles if p.person_id == state.person_id)
        obs = build_observation(
            permission=standard_product_permission(bundle.protocol.info_permission_version),
            profile=profile,
            state=state,
            catalog=catalog,
            last_feedback=visible_feedback(last.settlement),
            resource_rules=bundle.protocol.resource_rules,
            energy_feedback=bundle.protocol.energy_feedback,
            career_rules=bundle.protocol.career_rules,
        )
        return observation_payload(obs)

    payload_mid = observe(s2_mid, midyear[0])
    payload_study = observe(s2_study, study[0])

    # ---------------------------------------------------------------- 年度绑定（两次真实试算）
    start = w.start("u51.a71.study")
    with_course = w.bind(start, w.declared("u51.a71.study", 1))
    without = w.bind(
        start,
        w.plan("u51.a71.study", 1, HOME, FOOD, _item(CLERK), WASHER, why="同一年不上课"),
    )
    rec_with = feedback_record(protocol=bundle.protocol, year=with_course)
    binding: dict[str, Any] = {
        "with_course_binding": with_course.binding_fingerprint,
        "without_course_binding": without.binding_fingerprint,
        "correct_pair_abilities": [
            a.ability_id
            for a in apply_feedback(with_course.settlement.next_state, rec_with).abilities
        ],
    }
    try:
        apply_feedback(without.settlement.next_state, rec_with)
        binding["cross_pair"] = "被接受（不应发生）"
    except LifeContractError as exc:
        binding["cross_pair"] = f"拒绝：{exc}"
    forged = with_course.settlement.next_state.model_copy(
        update={
            "abilities": (
                *with_course.settlement.next_state.abilities,
                with_course.settlement.next_state.abilities[0].model_copy(
                    update={"ability_id": "python_programming"}
                ),
            )
        }
    )
    try:
        apply_feedback(forged, rec_with)
        binding["forged_candidate"] = "被接受（不应发生）"
    except LifeContractError as exc:
        binding["forged_candidate"] = f"拒绝：{exc}"

    # ---------------------------------------------------------------- 互不竞争
    alone = {c: [r.model_dump_json() for r in runs[c]] for c in U51}
    states = {c: w.start(c) for c in U51}
    together: dict[str, list[str]] = {c: [] for c in U51}
    for y in (1, 2, 3):
        for c in reversed(U51):
            r = w.year(states[c], w.declared(c, y))
            together[c].append(r.model_dump_json())
            if r.handoff == "next_year_ready" and r.next_state is not None:
                states[c] = r.next_state
    same_alone = together == alone
    twin_raw = copy.deepcopy(raw)
    twin = copy.deepcopy(
        next(s for s in twin_raw["initial_states"] if s["clone_id"] == "u51.a71.study")
    )
    twin["clone_id"] = "u51.a71.twin"
    twin_raw["initial_states"].append(twin)
    for p in [p for p in twin_raw["plans"] if p["clone_id"] == "u51.a71.study"]:
        twin_raw["plans"].append({**copy.deepcopy(p), "clone_id": "u51.a71.twin"})
    twin_bundle = parse_bundle(twin_raw)
    twin_errors = cross_validate(twin_bundle).errors
    twin_scoring, _ = load_scoring_config(SCORING, twin_bundle)
    tw = World(twin_bundle, twin_scoring)
    twin_rows = {c: [_row(r) for r in tw.run(c)] for c in ("u51.a71.study", "u51.a71.twin")}

    # ---------------------------------------------------------------- 手算核对
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append((name, bool(ok), detail))

    rows = {c: [_row(r) for r in rs] for c, rs in runs.items()}
    st, no, sy = rows["u51.a71.study"], rows["u51.a71.nostudy"], rows["u51.a71.stay"]
    py, md, hrr = rows["u51.a71.python"], rows["u51.a71.midyear"], rows["u51.a71.hr"]
    check(
        "例 1·第 1 年现金",
        (st[0]["cash_end"], no[0]["cash_end"]) == ("10,100", "11,300"),
        "学会计 3,000 + 31,200 − 22,900 − 学费 1,200 = 10,100；不学 11,300（差额就是学费）",
    )
    check(
        "例 1·学完时点",
        st[0]["abilities_after"] == ["accounting_cert（第 1 年第 12 月学会，第 2 年起可用）"],
        "第 1 年第 1–12 月参加满 12 个月，第 12 月完成；第 2 年起可用",
    )
    check(
        "例 1·换岗后的工资、税与负担",
        (st[1]["income_gross"], st[1]["income_tax"], st[1]["max_monthly_burden"], st[1]["cash_end"])
        == ("48,000", "7,200", "211", "28,700"),
        "会计 48,000，税 (48,000 − 12,000) × 20% = 7,200；每月负担 176 + 7.5 + 20 + 1.5 + 6 = 211；"
        "10,100 + 40,800 − 22,200 = 28,700",
    )
    check(
        "例 1·有资格不换岗不涨薪",
        (sy[1]["income_gross"], sy[1]["cash_end"], sy[1]["positions"])
        == ("36,000", "19,100", [f"{CLERK}（12 个月）"]),
        "持有会计资格、仍选文员：工资仍是 36,000；10,100 + 31,200 − 22,200 = 19,100",
    )
    check(
        "例 1·不学",
        (no[1]["income_gross"], no[1]["cash_end"]) == ("36,000", "20,300"),
        "11,300 + 31,200 − 22,200 = 20,300",
    )
    dev_row = derived["ex2_accountant_cert_for_developer"]["row"]
    check(
        "例 2·会计资格不能做开发",
        dev_try.settlement.status == "input_error"
        and any("python_programming" in p for p in dev_row["problems"]),
        "第 2 年计划选开发岗位：年初没有 python_programming → 整份计划退回（qualification_missing）",
    )
    check(
        "例 2·Python 打开开发岗位",
        (py[1]["income_gross"], py[1]["income_tax"], py[1]["max_monthly_burden"], py[1]["cash_end"])
        == ("60,000", "9,600", "227", "39,500"),
        "开发 60,000，税 9,600；负担 192 + 35 = 227；11,300 + 50,400 − 22,200 = 39,500",
    )
    check(
        "例 3·零价自学也占时间与负担",
        (py[0]["max_monthly_burden"], no[0]["max_monthly_burden"], py[0]["max_monthly_hours"])
        == ("225", "195", "253")
        and no[0]["max_monthly_hours"] == "223"
        and py[0]["cash_end"] == no[0]["cash_end"] == "11,300",
        "每月 30 小时 × 1.0：负担 195 → 225，时间 223 → 253；学费 0，年末现金与不学相同",
    )
    both_row = derived["ex3_two_courses_over_quota"]["row"]
    check(
        "例 3·同一年学两门超出额度",
        both_try.settlement.status == "plan_infeasible"
        and both_try.settlement.outcome is None
        and both_try.next_state is None
        and both_row["learning"] == [],
        "195 + 48 + 30 = 273 > 250：计划不可行，没有账目、学费、进度或能力",
    )
    check(
        "例 4·网鹊科技内训打开网鹊科技的人事岗位",
        (
            hrr[0]["max_monthly_burden"],
            hrr[1]["income_gross"],
            hrr[1]["income_tax"],
            hrr[1]["cash_end"],
        )
        == ("205", "44,000", "6,400", "26,700"),
        "内训每月 10 小时 × 1.0 → 205；人事 44,000 − 6,400；11,300 + 37,600 − 22,200 = 26,700",
    )
    check(
        "例 4·公司知识不迁移",
        ledger_try.settlement.status == "input_error"
        and any(
            "hr_rules_ledgerco" in p
            for p in derived["ex4_netco_rules_for_ledgerco"]["row"]["problems"]
        ),
        "网鹊科技的规则不满足账房事务所的要求 → 整份计划退回",
    )
    m1 = runs["u51.a71.midyear"][0].settlement.outcome.career.learning[0]  # type: ignore[union-attr]
    m2 = runs["u51.a71.midyear"][1].settlement.outcome.career.learning[0]  # type: ignore[union-attr]
    check(
        "例 4·年中开课第 1 年",
        (to_fraction(m1.fee_charged.amount), m1.months_attended, m1.progress_after)
        == (1200, (7, 8, 9, 10, 11, 12), 6),
        "第 7–12 月参加 6 个月，学费 1,200 在第 7 月付清，进度 6/12",
    )
    check(
        "例 4·跨年延续不重复收费、下一年生效",
        (
            to_fraction(m2.fee_charged.amount),
            m2.months_attended,
            m2.completed_month,
            m2.usable_from_year,
        )
        == (0, (1, 2, 3, 4, 5, 6), 6, 3),
        "第 2 年第 1–6 月继续，学费 0，第 6 月完成，第 3 年起可用",
    )
    check(
        "例 4·当年完成不追溯",
        same_year_try.settlement.status == "input_error",
        "第 2 年下半年就去做会计：年初还没有资格 → 整份计划退回",
    )
    check(
        "例 4·第 3 年做会计",
        (md[2]["income_gross"], md[2]["cash_end"]) == ("48,000", "37,700"),
        "第 2 年 10,100 + 31,200 − 22,200 = 19,100；第 3 年 19,100 + 40,800 − 22,200 = 37,700",
    )
    ex = rows["u53.a71.c0"][0]
    check(
        "出局保留真实进度",
        (ex["handoff"], ex["exit_month"], ex["cash_end"], ex["abilities_after"])
        == ("exited", 3, "1,240", [])
        and ex["learning"][0].endswith("进度 0→2/12，未完成"),
        "每月最低生活 1,380：4,000 → 2,620 → 1,240，第 3 月付不起出局；自学进度 2/12，没有能力",
    )
    bl = rows["u54.a71.c0"][0]
    check(
        "反馈受阻保留结算的学习事实",
        bl["handoff"] == "feedback_blocked"
        and bl["candidate_quota_status"] == "pending_feedback"
        and bl["abilities_after"] == ["accounting_cert（第 1 年第 12 月学会，第 2 年起可用）"],
        "u54 的家务点数没有评分分项 → 年度满足感不完整；结算里的学习与能力保留，下一年额度定不下来",
    )
    check(
        "终态不再行动",
        sy[2]["handoff"] == "terminal"
        and "python_programming（第 3 年第 12 月学会，第 4 年起可用）" in sy[2]["abilities_after"],
        "第 3 年学会 Python，第 4 年起可用；N = 3，第 4 年是终态，没有行动年",
    )
    scores = {rows[c][0]["score_mean"] for c in U51}
    quotas = {rows[c][0]["next_quota"] for c in U51}
    check(
        "学习不进入满足感",
        scores == {0.5354166666666667} and quotas == {"263.38"},
        "六个分身第 1 年生活相同（近处住房、新鲜食材、文员、洗衣机）、只是学不学不同：满足感都是 "
        "0.5354166666666667，第 2 年额度都是 250 × (1 + 0.10 × S) 向下保留到 0.01 = 263.38",
    )
    check(
        "年度绑定保护新候选状态",
        binding["cross_pair"].startswith("拒绝") and binding["forged_candidate"].startswith("拒绝"),
        "不上课那次的候选状态拿上课那次的反馈记录被拒；候选状态里塞进一项没学过的能力也被拒",
    )
    check(
        "单独运行与轮流运行相同",
        same_alone,
        "六个分身逐年轮流交接（顺序倒过来），每个分身的结果与单独运行逐字相同",
    )
    check(
        "没有名额与竞争",
        not twin_errors
        and [x["income_gross"] for x in twin_rows["u51.a71.twin"]]
        == [x["income_gross"] for x in twin_rows["u51.a71.study"]]
        == ["36,000", "48,000", "48,000"],
        "两个计划相同的分身都在第 2 年做会计，工资都是 48,000",
    )
    acc_obs = next(o for o in payload_mid["opportunities"] if o["opportunity_id"] == ACCOUNTANT)
    check(
        "观察写明缺什么、学到哪",
        acc_obs["qualification"]["status"] == "not_yet"
        and "已参加 6/12 个月，完成后从下一个行动年起可用"
        in acc_obs["qualification"]["missing"][0],
        "年中开课的分身第 2 年看到：会计岗位当前不可选，缺会计资格，课程已参加 6/12 个月",
    )

    # ---------------------------------------------------------------- write
    args.out.mkdir(parents=True)
    cfg = args.out / "configs"
    cfg.mkdir()
    shutil.copyfile(LIFE, cfg / LIFE.name)
    shutil.copyfile(SCORING, cfg / SCORING.name)
    (args.out / "observations").mkdir()

    def dump(name: str, data: Any) -> None:
        (args.out / name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )

    dump("environment.json", _environment())
    dump(
        "command.json",
        {
            "argv": sys.argv,
            "python_executable": sys.executable,
            "cwd": os.getcwd(),
            "reproduce": "uv run python scripts/demo_life_e1_5a.py --out <新目录>",
        },
    )
    rules = bundle.protocol.career_rules
    assert rules is not None
    dump(
        "protocol.json",
        {
            "protocol": bundle.protocol.model_dump(mode="json"),
            "career_rules_content_fingerprint": rules.content_fingerprint,
            "career_rules_content": rules.content(),
            "feedback_handoff_revision": FEEDBACK_HANDOFF_REVISION,
            "resource_rules_revision": RESOURCE_RULES_REVISION,
            "settlement_convention": SETTLEMENT_CONVENTION,
            "scoring_spec": {
                "spec_id": scoring.spec.spec_id,
                "scoring_version": scoring.spec.scoring_version,
            },
            "formal_scoring_ready": bundle.protocol.scoring_ready,
        },
    )
    dump(
        "inputs.json",
        {
            "life_file": str(LIFE),
            "life_sha256": _sha256(LIFE),
            "scoring_file": str(SCORING),
            "scoring_sha256": _sha256(SCORING),
            "life_report": life_report.model_dump(mode="json"),
            "scoring_report": score_report.model_dump(mode="json"),
        },
    )
    dump("yearly_results.json", rows)
    dump("derived_scenarios.json", derived)
    dump("ability_job_matrix.json", matrix)
    dump(
        "life_year_results_u51_study.json",
        [r.model_dump(mode="json") for r in runs["u51.a71.study"]],
    )
    dump("observations/u51_midyear_year2_payload.json", payload_mid)
    dump("observations/u51_study_year2_payload.json", payload_study)
    dump("year_binding.json", binding)
    dump(
        "independence.json",
        {
            "same_as_running_alone": same_alone,
            "order": "逐年轮流，分身顺序倒过来",
            "twin_errors": list(twin_errors),
            "twin_rows": twin_rows,
        },
    )

    lines = [
        "# E1-5A 学习—岗位—收入演示（合成）",
        "",
        "金额按当地基年不变价（USD_2024_LOCAL）；时间是小时；负担与额度单位是负担点。全部数字是手工设定的合成值，"
        "**不是现实培训回报、录用规则或城市排行**。学习本身不进入满足感（未实现），所以下面的满足感只来自住房、"
        "食品、家务与通勤等已有分项。",
        "",
    ]
    sections = (
        ("例 1　有无学习：学会计后换岗（u51.a71.study）", "u51.a71.study"),
        ("例 1　有资格但不换岗（u51.a71.stay；第 3 年再自学 Python）", "u51.a71.stay"),
        ("例 1　不学（u51.a71.nostudy）", "u51.a71.nostudy"),
        ("例 2 / 例 3　零价自学 Python 后做开发（u51.a71.python）", "u51.a71.python"),
        ("例 4　网鹊科技内训后做网鹊科技的人事（u51.a71.hr）", "u51.a71.hr"),
        ("例 4　年中开课、跨年完成（u51.a71.midyear）", "u51.a71.midyear"),
        ("出局：没有工作、边自学边找机会（u53.a71.c0）", "u53.a71.c0"),
        ("反馈受阻：学习事实保留（u54.a71.c0）", "u54.a71.c0"),
    )
    for title, clone in sections:
        lines += [f"## {title}", "", TABLE_HEAD, *(_fmt(x) for x in rows[clone]), ""]
        for x in rows[clone]:
            lines += [f"- 第 {x['year']} 年结算问题：{p}" for p in x["problems"]]
            score = x["score_mean"] if x["score_mean"] is not None else "—"
            lines += [
                f"- 第 {x['year']} 年满足感 {score}（{x['score_status']}）；下一年额度 {x['next_quota'] or '—'}；"
                f"年末当前岗位 {x['current_after']}，职业摘要 {x['occupation_after']}"
            ]
        lines += [""]
    lines += ["## 派生的边界情景（只换某一年的计划，原配置不改）", ""]
    for name, d in derived.items():
        row = d["row"]
        lines += [
            f"### {name}",
            "",
            f"{d['note']}。{d['clone']} 第 {d['year']} 年：结算 {row['settlement']}，交接 {row['handoff']}。",
            "",
            *(f"- {p}" for p in row["problems"]),
            "",
        ]
    lines += [
        "## 能力—岗位对照（按年初的真实状态；不可选时列出缺的要求）",
        "",
        "| 年初状态 | 能力 | 当前岗位 | 会计 | 开发 | 人事（网鹊科技） | 人事（账房事务所） | 网鹊科技内训 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, m in matrix.items():
        cells = [
            name,
            "、".join(m["abilities"]) or "无",
            "、".join(x.split(".")[-1] for x in m["current"]) or "无",
            *(m["options"][t] for t in targets),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines += [""]
    (args.out / "yearly_tables.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    obs_lines = [
        "# 观察样例：年中开课的分身第 2 年能看到什么（标准产品许可）",
        "",
        "完整 payload 见 `u51_midyear_year2_payload.json`（学会计的分身第 2 年见 `u51_study_year2_payload.json`）。",
        "只有公开事实与本人陈述；没有满足感、分数、评分参数或内部记录。",
        "",
        f"- 已有能力：{payload_mid['abilities'] or '无'}",
        f"- 岗位记录：{payload_mid['employment']['as_of']}；当前岗位 "
        f"{[p['opportunity_id'] for p in payload_mid['employment']['current']]}",
        *(
            f"- 学习进度：{t['opportunity_id']} 已参加 {t['months_participated']}/{t['months_required']} 个月，"
            f"学费已结清 {t['fee_paid']}"
            for t in payload_mid["training"]
        ),
        "",
        "| 机会 | 年薪或价格 | 每月小时 × 每小时负担（岗位另有到岗通勤） | 授予 | 今年能不能选 | 缺什么 |",
        "|---|---|---|---|---|---|",
    ]
    for o in payload_mid["opportunities"]:
        if o["category"] not in ("job", "training"):
            continue
        q = o.get("qualification")
        te = o.get("time_energy") or {}
        job = te.get("job") or {}
        hours = job.get("work_hours_per_month", te.get("hours"))
        per_hour = job.get("burden_per_work_hour", te.get("burden_per_hour"))
        money = o.get("price") or o.get("compensation")
        obs_lines.append(
            "| "
            + " | ".join(
                [
                    o["label"],
                    _amt(money["amount"]["amount"]) if money else "—",
                    f"{hours:g} × {per_hour:g}",
                    "、".join(o.get("grants") or ()) or "—",
                    "无要求" if q is None else q["status"],
                    "—" if q is None else "；".join(q["missing"]) or "—",
                ]
            )
            + " |"
        )
    obs_lines += [
        "",
        "规则说明（原文）：",
        "",
        *(f"- {t}" for t in payload_mid["career_rules"]["rules"]),
    ]
    (args.out / "observations" / "README.md").write_text(
        "\n".join(obs_lines) + "\n", encoding="utf-8"
    )

    check_lines = [
        "# 手算核对",
        "",
        "金额精确到分、负担与时间按精确分数比较；不加容差。",
        "",
        *(f"- {'通过' if ok else '不通过'}　{name}：{detail}" for name, ok, detail in checks),
    ]
    (args.out / "checks.md").write_text("\n".join(check_lines) + "\n", encoding="utf-8")
    dump("checks.json", [{"check": n, "passed": ok, "detail": d} for n, ok, d in checks])
    (args.out / "README.md").write_text(
        "\n".join(
            [
                "# E1-5A 学习—岗位—收入（演示运行证据）",
                "",
                "零大模型调用的离线演示（`DECISIONS/0023`）。全部数值是合成设定下的结果，**不是现实培训回报、录用规则",
                "或城市排行**。",
                "",
                "- `command.json` / `environment.json`：实际命令、git 状态、执行修订与源码指纹",
                "- `configs/`：生活输入与评分配置副本",
                "- `protocol.json` / `inputs.json`：协议（含职业规则与其内容指纹）、评分规格与加载检查报告",
                "- `yearly_results.json` / `yearly_tables.md`：逐年学习、能力、岗位、工资、税、现金与资源",
                "- `derived_scenarios.json`：派生的边界情景（只换某一年的计划）",
                "- `ability_job_matrix.json`：按年初真实状态的能力—岗位对照",
                "- `life_year_results_u51_study.json`：学会计分身三年的完整年度交接结果（内部对象）",
                "- `observations/`：代理第 2 年的实际 payload 与说明",
                "- `year_binding.json`：两次真实试算不能互换候选状态",
                "- `independence.json`：单独运行与轮流运行一致；两个相同分身都拿到岗位",
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
