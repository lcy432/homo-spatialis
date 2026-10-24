"""E3B 菜单诊断（E3B 收尾第 5 节）：只报告，不用来筛选机会、不调整菜单大小，也不改变产品默认值。

回答两件事，口径都是**首月 / 空间诊断，不是长期成功率**：

* **菜单长什么样**：六城 × 每年 × 每个环境重复的实际 60 项菜单（住房月租分位、居住片区数、质量档与卧室数；岗位年薪分位、
  就业片区数、职业数），抽样项与额外的最低住房、合法延续项分开计数；30 / 60 / 120 的大小诊断沿用资料包报告里已有的
  价格分位与片区覆盖，只补上质量档与卧室数。
* **统一诊断人物能不能落脚**：u501 的能力（学历 ≥ 高中，E3B 验收所用）与每城的标准开局岗位（办公室文员 SOC 43-9061 的
  ``<城市>.job.43-9061.start``），往返上限固定 60 分钟，初始现金 0、1,000、3,000 USD_2024_LOCAL（诊断夹具）。
  住房诊断：保留开局岗位，第 1 月能通勤（≤ 60 分钟）并且付得起最低食品、这处住房与必要交通的选项数；不用车与用车
  （付用车权月费与油费）分开报，任一可行就算。岗位诊断：同一能力能就任的岗位数，以及其中同一份住房菜单里至少有一处
  60 分钟内能通勤的岗位数（只看资格与空间匹配）。完整合格池给出同样定义的基准。

**判定全部由 E1 做**，没有另写预算公式：

* 通勤：``econ.life_commute.executed_commute``（E1 结算本身用的那一个：``available_legs`` + ``fastest_available_v1``，
  用车时功能里有用车权），最长往返 > 60 或没有可用方式即不通过；
* 现金：一份整年计划（开局岗位、这处住房、本城最低食品，用车时加用车权）交给公开的 ``econ.life_settlement.settle_year``，
  协议的新疾病来源换成确定性的 ``none``、不抽设备故障（与估值期派生协议同一做法，写在本模块里，不改 econ）；
  税后工资、付款时点、用车权月费与油费都是 E1 的。读第 1 月：第 1 月出局、第 1 月有计划不可行问题、第 1 月住房 /
  食品 / 用车权被拒，都算不通过；问题落在第 2 月以后说明第 1 月过得去，照实记下。价格未知的项目报未知，不当零。

**精确缓存**：资料包里的住房机会除月租与居住片区外，其余会影响结算的字段完全相同（``assert_housing_uniform`` 核对），
结算只经由通勤读片区，所以第 1 月判定只取决于（月租、实际选中的通勤方式 / 分钟 / 每次花费、现金、是否用车）。每个
这样的组合用一处真实候选跑一次 E1，结果原样用于同一组合的全部候选。不对月租二分、不假定单调。

诊断用的目录只为绕过“这一年的菜单上有没有”这道合法性门槛：把要检验的住房放进第 1 年、第 1 个环境重复的住房菜单，
别的岗位与住房从目录里拿掉（它们不进这份计划，结算也不读）；钱、时间、通勤的计算不变。
"""

from __future__ import annotations

import copy
import json
import math
import os
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from common.life_contracts import (
    CityLifeCatalog,
    IllnessEventSource,
    LifeContractError,
    LifeOpportunity,
    LifeProtocol,
    PersonYearState,
    YearAction,
    exact_minor_units,
    qualification_gaps,
)
from econ.life_commute import (
    CommuteUnavailableError,
    OnsiteJob,
    available_legs,
    choose_leg,
    executed_commute,
)
from econ.life_settlement import LifeTaxBinding, binding_from_policy, settle_year
from ledger.life_catalog import LifeBundle, cross_validate, parse_bundle

SCHEMA = "e3b-menu-diagnostics-v1"
CAP_MINUTES = 60
CASH_LEVELS = (0, 1000, 3000)
#: 统一诊断人物：合成人物 u501（办公室文员）在 E3B 验收里用的能力与职业
DIAG_ABILITIES = ("edu_at_least_hs",)
DIAG_OCCUPATION = {"scheme": "SOC2018", "code": "43-9061"}
DIAG_AGE = 35
NOTICE = (
    "首月 / 空间诊断，不是长期成功率：只看统一诊断人物第 1 月能不能通勤并付清最低食品、住房与必要交通，以及资格与空间匹配；"
    "不声称三年生活可行。诊断夹具（u501 的能力、标准开局岗位、60 分钟、现金 0 / 1,000 / 3,000）不改变产品默认值，"
    "结果也不用来筛选机会或调整菜单大小"
)
_EMERGENCY = {"minor": "decline", "major": "decline"}
#: 住房机会里允许因候选而不同的字段（展示文字、规格、来源、片区与月租）；其余必须完全相同，精确缓存才成立
_HOUSING_VARYING = frozenset(
    {"opportunity_id", "label", "quality", "spec", "source", "area", "price", "attributes"}
)


# --------------------------------------------------------------------------- 资料包与诊断人物


def load_pack(pack: Path) -> tuple[dict[str, Any], LifeBundle]:
    """读资料包的生活输入（JSON；比按 YAML 读快得多）并交叉校验到可以结算。"""
    raw = json.loads((Path(pack) / "life_input.json").read_text(encoding="utf-8"))
    bundle = parse_bundle(raw)
    report = cross_validate(bundle)
    if not report.settlement_ready:
        raise LifeContractError(
            "资料包不能结算：" + "；".join((report.errors + report.settlement_problems)[:5])
        )
    return raw, bundle


def diagnostic_protocol(protocol: LifeProtocol) -> LifeProtocol:
    """新疾病来源换成确定性的 ``none``、不抽设备故障（与估值期派生协议同一做法）；其余原样。"""
    data = protocol.model_dump(mode="python")
    data["illness_events"] = IllnessEventSource(
        mode="none",
        stream_version=protocol.illness_events.stream_version,
        note="E3B 菜单诊断：不发生新疾病的确定性条件（诊断夹具，不是把患病概率改成 0）",
    ).model_dump(mode="python")
    data["device_failures"] = None
    return LifeProtocol.model_validate(data)


def city_key(catalog_raw: dict[str, Any]) -> str:
    return str(catalog_raw["opportunities"][0]["opportunity_id"]).split(".")[0]


def start_job_id(key: str) -> str:
    return f"{key}.job.{DIAG_OCCUPATION['code']}.start"


@dataclass(frozen=True)
class CityContext:
    """一座城做诊断要用的东西：原始目录（做诊断目录用）、校验过的目录、税、诊断协议与开局岗位等。"""

    key: str
    raw: dict[str, Any]
    catalog: CityLifeCatalog
    tax: LifeTaxBinding
    protocol: LifeProtocol
    start_job: LifeOpportunity
    food: LifeOpportunity
    car: LifeOpportunity | None
    minimum_housing: str
    abilities: tuple[str, ...] = DIAG_ABILITIES

    @property
    def digits(self) -> int:
        return self.catalog.money_basis.minor_unit_digits


def city_contexts(raw: dict[str, Any], bundle: LifeBundle) -> dict[str, CityContext]:
    protocol = diagnostic_protocol(bundle.protocol)
    out: dict[str, CityContext] = {}
    for cat_raw, cat in zip(raw["catalogs"], bundle.catalogs, strict=True):
        key = city_key(cat_raw)
        job = cat.opportunity(start_job_id(key))
        food = cat.minimum_spec("food")
        housing = cat.minimum_spec("housing")
        if job is None or food is None or housing is None:
            raise LifeContractError(f"{key}: 没有标准开局岗位或最低生活声明，做不了统一诊断")
        food_opp = cat.opportunity(food.baseline_opportunity_id)
        assert food_opp is not None
        out[key] = CityContext(
            key=key,
            raw=cat_raw,
            catalog=cat,
            tax=binding_from_policy(bundle.tax_policy(cat.tax_policy_id)),
            protocol=protocol,
            start_job=job,
            food=food_opp,
            car=cat.opportunity(f"{key}.service.car_access"),
            minimum_housing=housing.baseline_opportunity_id,
        )
    return out


def diagnostic_state(ctx: CityContext, cash: int, *, env_repeat: int = 1) -> PersonYearState:
    """统一诊断人物的第 1 年年初状态（与产品编译写开局状态的形状相同）。"""
    numeraire = ctx.catalog.money_basis.numeraire_id
    job = ctx.start_job
    occupation = (
        {"scheme": job.occupation.scheme, "code": job.occupation.code}
        if job.occupation is not None
        else dict(DIAG_OCCUPATION)
    )
    return PersonYearState.model_validate(
        {
            "person_id": "diag",
            "clone_id": f"diag.{ctx.key}.r0",
            "city": ctx.raw["city"],
            "year_index": 1,
            "occupation": occupation,
            "money_basis_id": numeraire,
            "cash": {"amount": cash, "numeraire": numeraire},
            "time_budget_hours": 4380,
            "energy": {"level": 250, "scale_version": "e1-4a-monthly-quota-v1"},
            "resource_budget_basis": {
                "rules_version": ctx.protocol.resource_rules.version,
                "reserved": "只预留协议声明的睡眠与基本休息（每天 12 小时），其余 12 小时/天可分配",
                "source": {
                    "kind": "synthetic",
                    "observed_on": "2026-10-24",
                    "geography_scope": "E3B 菜单诊断",
                    "mapping_version": SCHEMA,
                    "synthetic_note": "诊断夹具：与产品标准开局同一套时间与负担额度设定",
                },
            },
            "energy_quota_basis": {"base_level": 250, "status": "base_first_year"},
            "abilities": [
                {"ability_id": a, "source": "initial", "usable_from_year": 1} for a in ctx.abilities
            ],
            "employment": {
                "stated_occupation": occupation,
                "as_of": "start_declaration",
                "positions": [
                    {
                        "opportunity_id": job.opportunity_id,
                        "occupation": occupation,
                        **({"employer_id": job.employer_id} if job.employer_id else {}),
                    }
                ],
                "current_position_ids": [job.opportunity_id],
            },
            "savings_goals": [],
            "health": {"age_years": DIAG_AGE, "repeat_index": 0},
            "env_repeat": env_repeat,
            "goals": [],
            "experience_summary": [],
        }
    )


def assert_housing_uniform(opportunities: Iterable[dict[str, Any]]) -> None:
    """精确缓存的前提：住房机会除展示文字、规格、来源、片区与月租外，其余字段（含计费周期与资源设定）完全相同。"""
    seen: set[str] = set()
    for o in opportunities:
        rest = {k: v for k, v in o.items() if k not in _HOUSING_VARYING}
        rest["price_shape"] = {k: v for k, v in (o.get("price") or {}).items() if k != "amount"}
        seen.add(json.dumps(rest, sort_keys=True, ensure_ascii=False))
    if len(seen) > 1:
        raise LifeContractError(
            f"住房机会除月租与片区外还有 {len(seen)} 种结算设定：按（月租、通勤）缓存不再精确，诊断拒绝"
        )


def diagnostic_catalog(ctx: CityContext, housing: Sequence[dict[str, Any]]) -> CityLifeCatalog:
    """只为绕过菜单合法性门槛的诊断目录：要检验的住房进第 1 年、第 1 个环境重复的住房菜单（各年各重复的住房菜单都写
    同一批，保持菜单齐全），最低住房保留（兜底与出局检查要用它的月费），其余住房与开局岗位以外的岗位拿掉（不进计划，
    结算也不读）。钱、时间、通勤的计算不变。"""
    data = copy.deepcopy(ctx.raw)
    keep_job = ctx.start_job.opportunity_id
    added = {h["opportunity_id"]: h for h in housing}
    base = [
        o
        for o in data["opportunities"]
        if not (o["category"] == "housing" and o["opportunity_id"] != ctx.minimum_housing)
        and not (o["category"] == "job" and o["opportunity_id"] != keep_job)
        and o["opportunity_id"] not in added
    ]
    data["opportunities"] = base + list(added.values())
    ids = sorted({*added, ctx.minimum_housing})
    for m in data.get("menus", []):
        m["opportunity_ids"] = ids if m["category"] == "housing" else [keep_job]
    for m in data["minimum_living"]:
        if m["need"] == "housing":
            m["qualifying_opportunity_ids"] = ids
    return CityLifeCatalog.model_validate(data)


# --------------------------------------------------------------------------- 第 1 月判定（E1）


@dataclass(frozen=True)
class Verdict:
    """一处住房、一档现金、用不用车的第 1 月诊断：``pass`` / ``fail`` / ``unknown``，原因码与第 1 月最长往返分钟。"""

    status: str
    reason: str
    minutes: int | None = None


def _onsite(job: LifeOpportunity) -> list[OnsiteJob]:
    r = job.resource.job if job.resource is not None else None
    return [OnsiteJob(job, r.onsite_days_per_month)] if r and r.onsite_days_per_month > 0 else []


def _priced(o: LifeOpportunity) -> bool:
    return o.availability == "available" and (o.price is not None or o.compensation is not None)


def commute_signature(
    ctx: CityContext, catalog: CityLifeCatalog, home: LifeOpportunity, car: bool
) -> tuple[Any, ...]:
    """第 1 月的通勤执行事实（E1 ``executed_commute``）压成可缓存的签名：没有到岗、走不通，或每份岗位选中的（方式、往返
    分钟、每次花费）。"""
    functions = frozenset(ctx.car.functions) if car and ctx.car is not None else frozenset()
    try:
        c = executed_commute(catalog, 1, home, _onsite(ctx.start_job), functions)
    except CommuteUnavailableError:
        return ("unavailable",)
    if c.status == "no_onsite_work":
        return ("no_onsite_work",)
    return tuple((u.mode, u.roundtrip_minutes, u.cost.amount) for u in c.uses)


def _items(ctx: CityContext, housing_id: str, car: bool) -> list[dict[str, Any]]:
    whole = {"quantity": 1, "start_month": 1, "end_month": 12}
    ids = [ctx.start_job.opportunity_id, housing_id, ctx.food.opportunity_id]
    if car and ctx.car is not None:
        ids.append(ctx.car.opportunity_id)
    return [{"opportunity_id": i, **whole} for i in ids]


def month_one(
    ctx: CityContext,
    catalog: CityLifeCatalog,
    home: LifeOpportunity,
    cash: int,
    car: bool,
    signature: tuple[Any, ...] | None = None,
) -> Verdict:
    """E1 的第 1 月判定。``catalog`` 必须让 ``home`` 出现在第 1 年第 1 个环境重复的住房菜单上（``diagnostic_catalog``）。"""
    if car and ctx.car is None:
        return Verdict("unknown", "no_car_access_in_city")
    for o in (home, ctx.food, ctx.start_job, *([ctx.car] if car and ctx.car else [])):
        if not _priced(o):
            return Verdict("unknown", f"price_unknown:{o.opportunity_id}")
    sig = signature if signature is not None else commute_signature(ctx, catalog, home, car)
    if sig == ("unavailable",):
        return Verdict("fail", "commute_unavailable")
    minutes = 0 if sig == ("no_onsite_work",) else max(m for _, m, _ in sig)
    if minutes > CAP_MINUTES:
        return Verdict("fail", "commute_over_cap", minutes)
    state = diagnostic_state(ctx, cash)
    r = _settle(ctx, catalog, state, home, car, fallback=False)
    if r.status in ("input_error", "data_error", "stopped"):
        code = r.problems[0].code if r.problems else r.status
        return Verdict("unknown", f"{r.status}:{code}", minutes)
    if r.status == "plan_infeasible":
        # 没有声明兜底：住房 / 食品付不起、通勤付不起或走不通、时间精力超限都在出事的那个月停下，问题带月份
        months = [p.month for p in r.problems if p.month is not None]
        code = r.problems[0].code if r.problems else "plan_infeasible"
        if not months or min(months) == 1:
            return Verdict("fail", f"month1:{code}", minutes)
        if not car:
            return Verdict("pass", f"later_month:{code}", minutes)
        # 计划不可行不给账目：用车权（按月服务）第 1 月有没有被拒看不到。声明兜底再结算一次，拿到整年账目再看第 1 月
        r = _settle(ctx, catalog, state, home, car, fallback=True)
        if r.status not in ("completed", "exited"):
            if commute_signature(ctx, catalog, home, False) == ("unavailable",):
                # 这处住房不用车到不了岗位：E1 第 1 月的通勤走得通，只能是用车权在用——在用的按月服务就是当月付了款的
                return Verdict("pass", f"later_month:{code};car_paid_month1_by_commute", minutes)
            return Verdict("unknown", f"car_month1_unobservable:{code}", minutes)
    return _month_one_outcome(ctx, r, home, car, minutes)


def _settle(
    ctx: CityContext,
    catalog: CityLifeCatalog,
    state: PersonYearState,
    home: LifeOpportunity,
    car: bool,
    *,
    fallback: bool,
) -> Any:
    action = YearAction.model_validate(
        {
            "person_id": state.person_id,
            "clone_id": state.clone_id,
            "year_index": 1,
            "items": _items(ctx, home.opportunity_id, car),
            "minimum_fallback": ["housing", "food"] if fallback else [],
            "emergency": _EMERGENCY,
            "rationale": "E3B 菜单诊断：统一诊断人物的整年计划，只读第 1 月",
        }
    )
    return settle_year(
        protocol=ctx.protocol, state=state, action=action, catalog=catalog, tax=ctx.tax
    )


def _month_one_outcome(
    ctx: CityContext, r: Any, home: LifeOpportunity, car: bool, minutes: int
) -> Verdict:
    out = r.outcome
    assert out is not None
    if out.exit is not None and out.exit.month == 1:
        return Verdict("fail", "exit_month1", minutes)
    watched = {home.opportunity_id: "housing", ctx.food.opportunity_id: "food"}
    if car and ctx.car is not None:
        watched[ctx.car.opportunity_id] = "car_access"
    for item in out.items:
        what = watched.get(item.opportunity_id)
        if what is None:
            continue
        if any(x.month == 1 for x in item.rejections) or item.downgraded_from_month == 1:
            return Verdict("fail", f"rejected_month1:{what}", minutes)
    return Verdict("pass", "exited_later" if out.exit is not None else "ok", minutes)


def price_minor(o: dict[str, Any], digits: int) -> int | None:
    price = o.get("price")
    if not price:
        return None
    return exact_minor_units(float(price["amount"]["amount"]), digits)


@dataclass(frozen=True)
class Key:
    """精确缓存键：月租（最小单位）、通勤签名、现金、是否用车。"""

    rent: int | None
    signature: tuple[Any, ...]
    cash: int
    car: bool


def run_checks(
    ctx: CityContext, housing: Sequence[dict[str, Any]], *, cash_levels: Sequence[int] = CASH_LEVELS
) -> dict[str, dict[tuple[int, bool], Verdict]]:
    """对一批住房（机会字典）做全部（现金 × 用不用车）的第 1 月判定，按精确键缓存。返回 编号 → {(现金, 用车): 判定}。"""
    if not housing:
        return {}
    assert_housing_uniform(housing)
    catalog = diagnostic_catalog(ctx, housing)
    homes = {h["opportunity_id"]: catalog.opportunity(h["opportunity_id"]) for h in housing}
    cache: dict[Key, Verdict] = {}
    out: dict[str, dict[tuple[int, bool], Verdict]] = {}
    sigs: dict[tuple[str | None, bool], tuple[Any, ...]] = {}
    for h in housing:
        oid = h["opportunity_id"]
        home = homes[oid]
        assert home is not None
        rent = price_minor(h, ctx.digits)
        out[oid] = {}
        for car in (False, True):
            area_key = (home.area, car)
            if area_key not in sigs:
                sigs[area_key] = commute_signature(ctx, catalog, home, car)
            sig = sigs[area_key]
            for cash in cash_levels:
                k = Key(rent, sig, cash, car)
                if k not in cache:
                    cache[k] = month_one(ctx, catalog, home, cash, car, signature=sig)
                out[oid][(cash, car)] = cache[k]
    return out


# --------------------------------------------------------------------------- 汇总


def _q(values: list[float], q: float) -> float | None:
    """菜单项按出现计的阶梯分位（与资料包大小诊断同一口径）。"""
    if not values:
        return None
    v = sorted(values)
    idx = max(0, math.ceil(q * len(v)) - 1)
    return v[idx]


def _quantiles(values: list[float]) -> list[float | None]:
    return [_q(values, q) for q in (0.1, 0.5, 0.9)]


def summarize_checks(
    ids: Iterable[str],
    verdicts: dict[str, dict[tuple[int, bool], Verdict]],
    cash_levels=CASH_LEVELS,
) -> dict[str, Any]:
    ids = list(ids)
    out: dict[str, Any] = {"options": len(ids)}
    for cash in cash_levels:
        row: dict[str, Any] = {}
        either = unknown = 0
        reasons: Counter[str] = Counter()
        for case, car in (("no_car", False), ("car", True)):
            row[case] = sum(verdicts[i][(cash, car)].status == "pass" for i in ids)
        for i in ids:
            v0, v1 = verdicts[i][(cash, False)], verdicts[i][(cash, True)]
            if "pass" in (v0.status, v1.status):
                either += 1
            elif "unknown" in (v0.status, v1.status):
                unknown += 1
            else:
                reasons[f"{v0.reason} | {v1.reason}"] += 1
        row["either"] = either
        row["unknown"] = unknown
        row["at_least_one"] = either > 0
        row["fail_reasons_no_car|car"] = dict(reasons.most_common(6))
        out[f"cash_{cash}"] = row
    return out


def menu_profile(ctx: CityContext) -> dict[str, Any]:
    """实际年度菜单的样子：抽样项与额外的最低住房、合法延续项分开计数。"""
    cat = ctx.catalog
    out: dict[str, Any] = {}
    for m in sorted(cat.menus, key=lambda m: (m.category, m.year, m.env_repeat)):
        opps = [cat.opportunity(i) for i in m.opportunity_ids]
        name = f"{m.category}.y{m.year}.r{m.env_repeat}"
        if m.category == "housing":
            rents = [float(o.price.amount.amount) for o in opps if o and o.price]
            out[name] = {
                "sampled": len(opps),
                "extra_minimum_housing": int(ctx.minimum_housing not in m.opportunity_ids),
                "legal_continuations": 0,
                "rent_p10_p50_p90": _quantiles(rents),
                "home_areas": len({o.area for o in opps if o}),
                "quality_level": dict(
                    sorted(Counter(str(o.spec.get("quality_level")) for o in opps if o).items())
                ),
                "bedrooms": dict(
                    sorted(Counter(str(o.spec.get("bedrooms")) for o in opps if o).items())
                ),
            }
        else:
            wages = [float(o.compensation.amount.amount) for o in opps if o and o.compensation]
            out[name] = {
                "sampled": len(opps),
                "legal_continuations": int(ctx.start_job.opportunity_id not in m.opportunity_ids),
                "wage_p10_p50_p90": _quantiles(wages),
                "work_areas": len({o.area for o in opps if o}),
                "socs": len({o.occupation.code for o in opps if o and o.occupation}),
            }
    return out


def reachable(
    ctx: CityContext,
    home_areas: Iterable[str],
    job: LifeOpportunity,
    car: bool,
    catalog: CityLifeCatalog | None = None,
) -> bool:
    """空间匹配：从 ``home_areas`` 里至少一个片区，用 E1 的 ``available_legs`` + ``fastest_available_v1`` 到这份岗位
    往返 ≤ 60 分钟；完全居家的岗位不用通勤。"""
    if not _onsite(job):
        return True
    if job.area is None:
        return False
    cat = catalog or ctx.catalog
    functions = frozenset(ctx.car.functions) if car and ctx.car is not None else frozenset()
    for h in home_areas:
        legs = available_legs(cat, h, job.area, functions)
        if legs and choose_leg(legs, ctx.digits).roundtrip_minutes <= CAP_MINUTES:
            return True
    return False


def job_diagnostic(
    ctx: CityContext,
    jobs: Sequence[LifeOpportunity],
    home_areas: set[str],
    counts: Sequence[float] | None = None,
) -> dict[str, Any]:
    """能就任（``qualification_gaps`` 为空）的岗位数，以及其中从 ``home_areas`` 60 分钟内能通勤的岗位数（不用车 / 用车）。
    ``counts`` 是每个岗位代表的候选个数（完整池按代表岗位汇总时用），默认每个 1。"""
    state = diagnostic_state(ctx, 0)
    counts = list(counts) if counts is not None else [1.0] * len(jobs)
    qualified = [
        (j, n) for j, n in zip(jobs, counts, strict=True) if not qualification_gaps(j, state)
    ]
    pairs: dict[tuple[str | None, bool], bool] = {}

    def ok(j: LifeOpportunity, car: bool) -> bool:
        k = (j.area if _onsite(j) else None, car)
        if k not in pairs:
            pairs[k] = reachable(ctx, home_areas, j, car)
        return pairs[k]

    return {
        "jobs": sum(counts),
        "qualified": sum(n for _, n in qualified),
        "qualified_reachable_no_car": sum(n for j, n in qualified if ok(j, False)),
        "qualified_reachable_car": sum(n for j, n in qualified if ok(j, True)),
    }


# --------------------------------------------------------------------------- 完整合格池的基准


def _chunks(seq: Sequence[Any], n: int) -> list[Sequence[Any]]:
    return [seq[i : i + n] for i in range(0, len(seq), n)]


_WORKER: dict[str, Any] = {}


def _worker_init(pack: str) -> None:
    raw, bundle = load_pack(Path(pack))
    _WORKER["ctx"] = city_contexts(raw, bundle)


def _worker_run(key: str, housing: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    got = run_checks(_WORKER["ctx"][key], housing)
    return {
        oid: {f"{cash}|{int(car)}": f"{v.status}|{v.reason}" for (cash, car), v in d.items()}
        for oid, d in got.items()
    }


def _decode(d: dict[str, dict[str, str]]) -> dict[str, dict[tuple[int, bool], Verdict]]:
    out: dict[str, dict[tuple[int, bool], Verdict]] = {}
    for oid, row in d.items():
        out[oid] = {}
        for k, v in row.items():
            cash, car = k.split("|")
            status, reason = v.split("|", 1)
            out[oid][(int(cash), car == "1")] = Verdict(status, reason)
    return out


def check_many(
    pack: Path,
    contexts: dict[str, CityContext],
    work: dict[str, list[dict[str, Any]]],
    *,
    workers: int | None = None,
    chunk: int = 150,
) -> dict[str, dict[str, dict[tuple[int, bool], Verdict]]]:
    """多进程跑 ``run_checks``（每个进程自己读资料包；同一组合只在一个块里出现时缓存仍精确——块之间不共享缓存只会多算，不会改判定）。
    ``workers`` = 1 时在本进程里跑。"""
    workers = workers or min(16, os.cpu_count() or 1)
    out: dict[str, dict[str, dict[tuple[int, bool], Verdict]]] = {k: {} for k in work}
    if workers <= 1:
        for key, housing in work.items():
            for part in _chunks(housing, chunk):
                out[key].update(run_checks(contexts[key], part))
        return out
    with ProcessPoolExecutor(workers, initializer=_worker_init, initargs=(str(pack),)) as ex:
        futures = [
            (key, ex.submit(_worker_run, key, list(part)))
            for key, housing in work.items()
            for part in _chunks(housing, chunk)
        ]
        for key, f in futures:
            out[key].update(_decode(f.result()))
    return out


def representatives(
    ctx: CityContext,
    candidates: Sequence[tuple[str, str, str]],
    materialize: Callable[[str], dict[str, Any]],
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """按精确键（月租、不用车与用车两种通勤签名）给候选分组，每组取编号最小的那一处展开成机会作代表。
    ``candidates`` 是（编号、居住片区、月租文字）；返回 候选 → 代表 与 代表机会列表。"""
    cat = ctx.catalog
    digits = ctx.digits
    area_sig: dict[str, tuple[tuple[Any, ...], tuple[Any, ...]]] = {}
    probe = cat.opportunity(ctx.minimum_housing)
    assert probe is not None
    for _, area, _ in candidates:
        if area not in area_sig:
            home = probe.model_copy(update={"area": area})
            area_sig[area] = (
                commute_signature(ctx, cat, home, False),
                commute_signature(ctx, cat, home, True),
            )
    groups: dict[tuple[Any, ...], str] = {}
    rep_of: dict[str, str] = {}
    for oid, area, rent in sorted(candidates):
        minor = exact_minor_units(float(Decimal(rent)), digits)
        k = (minor, *area_sig[area])
        rep = groups.setdefault(k, oid)
        rep_of[oid] = rep
    reps = [materialize(oid) for oid in sorted(set(groups.values()))]
    return rep_of, reps


def size_quality(
    pool: Any, cfg: dict[str, Any], cbsa: str, settings_identity: str
) -> dict[str, Any]:
    """30 / 60 / 120 大小诊断缺的那一块：与资料包报告同一批诊断抽样（同一算法、同一诊断种子），每个大小的质量档与卧室数
    （菜单项按出现计，取各次重复的平均）。"""
    from product.realdata.life_pack import menu_seed
    from product.realdata.menus import keyed_sample

    diag = cfg["menu_size_diagnostic"]
    sizes, repeats, seed = tuple(diag["sizes"]), diag["repeats"], cfg["menus"]["seed"]
    by_id = {c.opportunity_id: c for c in pool.candidates}
    weights = pool.weights()
    orders = [
        keyed_sample(
            weights,
            max(sizes),
            menu_seed(settings_identity, cbsa, 1000 + r, "housing.size_diagnostic", 0, seed),
        )
        for r in range(repeats)
    ]
    out: dict[str, Any] = {}
    for k in sizes:
        q: Counter[str] = Counter()
        b: Counter[str] = Counter()
        for order in orders:
            for oid in order[:k]:
                pt = by_id[oid].data["point"]
                q[str(pt["quality_tier"])] += 1
                b[str(pt["bedrooms"])] += 1
        out[str(k)] = {
            "mean_quality_level": {t: round(n / repeats, 2) for t, n in sorted(q.items())},
            "mean_bedrooms": {t: round(n / repeats, 2) for t, n in sorted(b.items())},
        }
    return out


# --------------------------------------------------------------------------- 主流程


def _menu_housing(ctx: CityContext) -> list[dict[str, Any]]:
    ids = {i for m in ctx.catalog.menus if m.category == "housing" for i in m.opportunity_ids}
    ids.add(ctx.minimum_housing)
    by_id = {o["opportunity_id"]: o for o in ctx.raw["opportunities"]}
    return [by_id[i] for i in sorted(ids)]


def diagnose(
    pack: Path,
    *,
    full_pool: bool = True,
    workers: int | None = None,
    pools: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """整份诊断。``full_pool`` 时重新组装资料包抓出完整合格池（约 35 秒），核对构建身份与 ``pack`` 一致才给基准；``pools``
    可以直接传入（测试用）。"""
    pack = Path(pack)
    raw, bundle = load_pack(pack)
    report = json.loads((pack / "report.json").read_text(encoding="utf-8"))
    contexts = city_contexts(raw, bundle)
    assert_housing_uniform(
        o for c in raw["catalogs"] for o in c["opportunities"] if o["category"] == "housing"
    )
    work = {k: _menu_housing(ctx) for k, ctx in contexts.items()}
    pool_note: str | None = None
    built = None
    if full_pool and pools is None:
        built, pools = capture_pools()
        stale = pool_input_mismatch(report.get("build_inputs") or {}, built.inputs)
        if stale:
            pool_note = f"重新组装时完整池的输入 {stale} 与资料包报告记的不同（资料或配置已经变了）：完整池基准没有计算"
            pools = None
    rep_maps: dict[str, dict[str, str]] = {}
    if pools is not None:
        from product.realdata.life_pack import housing_opportunity, load_pack_config

        cfg = load_pack_config()
        cities = {c["key"]: c for c in cfg["cities"]}
        for key, ctx in contexts.items():
            hp = pools[key]["housing"]
            by_id = {c.opportunity_id: c for c in hp.candidates}

            def materialize(oid: str, _key: str = key, _by_id: dict = by_id) -> dict[str, Any]:
                o = housing_opportunity(_by_id[oid], cities[_key], cfg)
                return o.model_dump(mode="json")

            rep_of, reps = representatives(
                ctx,
                [(c.opportunity_id, c.area, c.data["point"]["gross_rent"]) for c in hp.candidates],
                materialize,
            )
            rep_maps[key] = rep_of
            known = {h["opportunity_id"] for h in work[key]}
            work[key] = work[key] + [r for r in reps if r["opportunity_id"] not in known]
    verdicts = check_many(pack, contexts, work, workers=workers)
    cities_out: dict[str, Any] = {}
    for key, ctx in contexts.items():
        v = verdicts[key]
        menus = menu_profile(ctx)
        housing_rows: dict[str, Any] = {}
        job_rows: dict[str, Any] = {}
        for m in ctx.catalog.menus:
            name = f"y{m.year}.r{m.env_repeat}"
            if m.category == "housing":
                housing_rows[name] = summarize_checks(m.opportunity_ids, v)
            else:
                areas = {
                    ctx.catalog.opportunity(i).area  # type: ignore[union-attr]
                    for hm in ctx.catalog.menus
                    if (hm.category, hm.year, hm.env_repeat) == ("housing", m.year, m.env_repeat)
                    for i in hm.opportunity_ids
                }
                jobs = [ctx.catalog.opportunity(i) for i in m.opportunity_ids]
                job_rows[name] = job_diagnostic(ctx, [j for j in jobs if j is not None], areas)
        city: dict[str, Any] = {
            "start_job": ctx.start_job.opportunity_id,
            "start_job_area": ctx.start_job.area,
            "menus": menus,
            "size_diagnostics": report["cities"][key]["menu_size_diagnostics"],
            "housing_first_month": housing_rows,
            "housing_first_month_overview": _overview(housing_rows),
            "extra_minimum_housing": summarize_checks([ctx.minimum_housing], v),
            "jobs": job_rows,
        }
        if pools is not None:
            hp, jp = pools[key]["housing"], pools[key]["job"]
            rep_of = rep_maps[key]
            full = {oid: v[rep] for oid, rep in rep_of.items()}
            weights = {c.opportunity_id: c.weight for c in hp.candidates}
            city["full_pool"] = {
                "housing_candidates": len(hp.candidates),
                "exact_groups_checked": len(set(rep_of.values())),
                "housing_first_month": summarize_checks(sorted(full), full),
                "housing_weight_share_either": {
                    f"cash_{cash}": round(
                        math.fsum(
                            weights[o]
                            for o, d in full.items()
                            if "pass" in (d[(cash, False)].status, d[(cash, True)].status)
                        )
                        / math.fsum(weights.values()),
                        6,
                    )
                    for cash in CASH_LEVELS
                },
                "jobs": _pool_jobs(ctx, jp, {c.area for c in hp.candidates}, built),
            }
            if built is not None:
                cfg_all = _cfg()
                cbsa = next(c["cbsa"] for c in cfg_all["cities"] if c["key"] == key)
                from product.realdata.pack_inputs import menu_settings_identity

                city["size_diagnostics_quality"] = size_quality(
                    hp, cfg_all, cbsa, menu_settings_identity(cfg_all)
                )
        else:
            city["full_pool"] = {
                "status": "not_computed",
                "reason": pool_note or "没有请求完整池基准",
            }
        cities_out[key] = city
    return {
        "schema": SCHEMA,
        "notice": NOTICE,
        "pack_build_identity": report.get("build_identity"),
        "person": {
            "based_on": "合成人物 u501（办公室文员），E3B 验收所用的能力",
            "abilities": list(DIAG_ABILITIES),
            "occupation": DIAG_OCCUPATION,
            "start_job_rule": "每城 <城市>.job.43-9061.start（start_job_median_v2）",
            "commute_cap_minutes": CAP_MINUTES,
            "cash_levels": list(CASH_LEVELS),
            "numeraire": "USD_2024_LOCAL",
        },
        "method": {
            "commute": "econ.life_commute.executed_commute（available_legs + fastest_available_v1）",
            "cash": "econ.life_settlement.settle_year 整年计划、只读第 1 月；新疾病来源 none、不抽设备故障",
            "exact_cache": "（月租、选中的通勤方式 / 分钟 / 每次花费、现金、是否用车）；住房其余结算字段已核对完全相同",
            "env_repeats": sorted({m.env_repeat for c in bundle.catalogs for m in c.menus}),
        },
        "cities": cities_out,
    }


def _cfg() -> dict[str, Any]:
    from product.realdata.life_pack import load_pack_config

    return load_pack_config()


def _overview(rows: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for cash in CASH_LEVELS:
        vals = sorted(r[f"cash_{cash}"]["either"] for r in rows.values())
        out[f"cash_{cash}"] = {
            "min": vals[0] if vals else None,
            "median": _q([float(x) for x in vals], 0.5),
            "max": vals[-1] if vals else None,
            "menus_with_at_least_one": sum(
                r[f"cash_{cash}"]["at_least_one"] for r in rows.values()
            ),
            "menus": len(vals),
        }
    return out


def _pool_jobs(ctx: CityContext, jp: Any, home_areas: set[str], built: Any) -> dict[str, Any]:
    """完整岗位池的基准：资格只由职业模板决定、空间只由就业片区与到岗与否决定——按（职业、每周工时、就业片区）取一个代表
    展开，计数按候选个数汇总。"""
    from product.realdata.life_pack import job_from_candidate

    if built is None:
        return {"status": "not_computed", "reason": "没有岗位规则（测试传入的池）"}
    rules = built.manifest["job_rules"]
    numeraire = ctx.catalog.money_basis.numeraire_id
    groups: dict[tuple[str, str, str], list[Any]] = {}
    for c in jp.candidates:
        k = (c.data["template"]["soc"], str(c.data["point"]["hours"]), c.area)
        groups.setdefault(k, []).append(c)
    reps = [
        job_from_candidate(cs[0], rules, numeraire, rules["mapping_version"])
        for _, cs in sorted(groups.items())
    ]
    counts = [float(len(cs)) for _, cs in sorted(groups.items())]
    return job_diagnostic(ctx, reps, home_areas, counts)


#: 完整池只由这些构建输入决定（住房目标格与支持表、岗位模板与联合分布、通勤片区、地理与配置）；基础样例文件只进协议与评分
POOL_INPUTS = ("config", "items", "commute", "intake_build", "intake_reads", "raw_geography")


def pool_input_mismatch(recorded: dict[str, Any], rebuilt: dict[str, Any]) -> list[str]:
    """重新组装的完整池输入与资料包报告记的身份逐项比较，返回不一致（或缺）的输入名。"""
    return [k for k in POOL_INPUTS if k not in recorded or recorded.get(k) != rebuilt.get(k)]


def capture_pools() -> tuple[Any, dict[str, Any]]:
    """重新组装资料包，抓出每城的完整合格住房池与岗位池（与构建同一份代码与输入；不写任何文件）。"""
    from unittest import mock

    from product.realdata import life_assembly

    pools: dict[str, dict[str, Any]] = {}
    orig_h, orig_j = life_assembly.housing_pool, life_assembly.job_pool

    def spy_h(city: dict[str, Any], *a: Any, **k: Any) -> Any:
        p = orig_h(city, *a, **k)
        pools.setdefault(city["key"], {})["housing"] = p
        return p

    def spy_j(city: dict[str, Any], *a: Any, **k: Any) -> Any:
        p = orig_j(city, *a, **k)
        pools.setdefault(city["key"], {})["job"] = p
        return p

    with (
        mock.patch.object(life_assembly, "housing_pool", spy_h),
        mock.patch.object(life_assembly, "job_pool", spy_j),
    ):
        built = life_assembly.assemble()
    return built, pools


def render_markdown(d: dict[str, Any]) -> str:
    lines = [
        "# E3B 菜单诊断",
        "",
        f"> {d['notice']}",
        "",
        f"资料包构建身份：`{str(d['pack_build_identity'])[:16]}…`；诊断人物：{d['person']['based_on']}，能力 "
        f"{', '.join(d['person']['abilities'])}，往返上限 {CAP_MINUTES} 分钟，现金 0 / 1,000 / 3,000 USD_2024_LOCAL；"
        f"环境重复 {d['method']['env_repeats']}。",
        "",
        "## 住房：第 1 月能通勤并付清的菜单项（不用车或用车任一可行；每份 60 项菜单的最少 / 中位 / 最多）",
        "",
        "| 城市 | 开局岗位片区 | 现金 0 | 现金 1,000 | 现金 3,000 | 每份菜单至少一处（现金 0） | 完整池可行比例（现金 0 / 3,000，按权重） |",
        "|---|---|---|---|---|---|---|",
    ]
    for key, c in d["cities"].items():
        ov = c["housing_first_month_overview"]

        def cell(cash: int, ov: dict[str, Any] = ov) -> str:
            return "{min} / {median:g} / {max}".format(**ov[f"cash_{cash}"])

        fp = c["full_pool"]
        share = (
            f"{fp['housing_weight_share_either']['cash_0']:.1%} / "
            f"{fp['housing_weight_share_either']['cash_3000']:.1%}"
            if "housing_weight_share_either" in fp
            else "未计算"
        )
        lines.append(
            f"| {key} | {c['start_job_area']} | {cell(0)} | {cell(1000)} | {cell(3000)} | "
            f"{ov['cash_0']['menus_with_at_least_one']}/{ov['cash_0']['menus']} | {share} |"
        )
    lines += [
        "",
        "## 岗位：能就任的菜单岗位与其中 60 分钟内有本期菜单住房的岗位（每份菜单）",
        "",
        "| 城市 | 菜单 | 岗位 | 能就任 | 有可通勤住房（不用车） | 有可通勤住房（用车） |",
        "|---|---|---|---|---|---|",
    ]
    for key, c in d["cities"].items():
        for name, r in sorted(c["jobs"].items()):
            lines.append(
                f"| {key} | {name} | {r['jobs']:g} | {r['qualified']:g} | "
                f"{r['qualified_reachable_no_car']:g} | {r['qualified_reachable_car']:g} |"
            )
        fj = c["full_pool"].get("jobs")
        if isinstance(fj, dict) and "qualified" in fj:
            lines.append(
                f"| {key} | 完整池 | {fj['jobs']:g} | {fj['qualified']:g} | "
                f"{fj['qualified_reachable_no_car']:g} | {fj['qualified_reachable_car']:g} |"
            )
    lines += [
        "",
        "判定方法：通勤用 E1 的 `executed_commute`，现金用 E1 的 `settle_year`（整年计划只读第 1 月）；按（月租、通勤签名、现金、"
        "是否用车）精确缓存。细节与每份菜单的价格、片区、质量见同目录 `menu_diagnostics.json`。",
        "",
    ]
    return "\n".join(lines)


def write(d: dict[str, Any], out: Path) -> None:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "menu_diagnostics.json").write_text(
        json.dumps(d, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (out / "menu_diagnostics.md").write_text(render_markdown(d), encoding="utf-8", newline="\n")
