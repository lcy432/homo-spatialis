"""离线检查一份生活输入（E1-1 `DECISIONS/0016`；E1-2 `DECISIONS/0017`）。零大模型调用。

    uv run python scripts/check_life_contract.py
    uv run python scripts/check_life_contract.py --file configs/life/synthetic_e1_1.yaml

先报结构验证结果；如果每座城都能结算（最低住房/食品与合成税配置齐全），再按年结算
文件里的全部计划，逐月打印账目、出局与终态。

**不输出任何城市得分或排序。** ``scoring_ready`` 恒为 false：评分映射、精力反馈、折现、
终值等政策都还没定，这时算出来的任何"分数"都是伪造的。账目里的税是合成测试替身，
数字只验证记账规则，**不是真实税后生活成本**。

退出码：0 = 结构有效（且可结算时全部计划都得到了结算结果）；1 = 结构无效，或某份计划
被判为输入/数据错误（样例本身坏了）。"计划不可行"是合法的结算结果，不算失败。
"""

from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401  (side effect: puts src/ on the path)
from common.life_contracts import LifeContractError, PersonYearState, SettlementResult
from econ.life_settlement import binding_from_policy, settle_plans
from ledger.life_catalog import LifeBundle, load_life_bundle

DEFAULT = Path("configs/life/synthetic_e1_2.yaml")
_NEED = {"housing": "住房", "food": "食品"}


def _m(x: float) -> str:
    return f"{x:,.2f}"


def _print_state(s: PersonYearState) -> None:
    held = ", ".join(
        f"{h.opportunity_id}（剩 {h.effective_remaining_months} 个月）" for h in s.holdings
    )
    training = ", ".join(
        f"{t.opportunity_id} {t.months_participated}/{t.months_required}" for t in s.training
    )
    print(
        f"    → 第 {s.year_index} 年 {s.status}：现金 {_m(s.cash.amount)} {s.money_basis_id}"
        f"；持有物 [{held}]；培训 [{training}]"
    )


def _print_result(r: SettlementResult) -> None:
    if r.outcome is None or r.next_state is None:
        for p in r.problems:
            where = f"第 {p.month} 月 " if p.month else ""
            print(f"    {r.status}：{where}{p.message}")
        return
    out = r.outcome
    print(
        f"    {'月':>2} {'月初':>11} {'税前收入':>9} {'税':>8} {'最低要求':>9} 检查 {'支出':>9} {'月末':>11}"
    )
    for m in out.months:
        print(
            f"    {m.month:>2} {_m(m.cash_start.amount):>11} {_m(m.income_gross.amount):>9} "
            f"{_m(m.income_tax.amount):>8} {_m(m.minimum_required.amount):>9} "
            f"{'通过' if m.minimum_check == 'passed' else '不足'} {_m(m.spent.amount):>9} "
            f"{_m(m.cash_end.amount):>11}"
        )
    for it in out.items:
        extra = ""
        if it.rejections:
            extra += f"；被拒 {[x.month for x in it.rejections]}（{it.rejections[0].reason}）"
        if it.months_not_reached:
            extra += f"；出局后未到达 {list(it.months_not_reached)}"
        print(
            f"    [{it.plan_order}] {it.opportunity_id:<28} {it.status:<22} "
            f"执行月 {list(it.covered_months)}{extra}"
        )
    if out.exit is not None:
        e = out.exit
        needs = "、".join(_NEED[n] for n in e.unaffordable_needs)
        print(
            f"    出局：第 {e.year_index} 年第 {e.month} 月，现金 {_m(e.cash_available.amount)} < "
            f"最低住房 {_m(e.minimum_housing_cost.amount)} + 最低食品 "
            f"{_m(e.minimum_food_cost.amount)}；缺{needs}，差 {_m(e.shortfall.amount)}"
        )
    _print_state(r.next_state)


def _settle_all(bundle: LifeBundle) -> bool:
    ok = True
    print("\n年度结算（合成记账验证；不是真实税后生活成本，不评分、不排序）：")
    for s in bundle.initial_states:
        plans = bundle.plans_for(s.clone_id)
        if not plans:
            continue
        cat = bundle.catalog_for(str(s.city))
        policy = bundle.tax_policy(cat.tax_policy_id) if cat else None
        if cat is None or policy is None:
            continue
        results = settle_plans(
            protocol=bundle.protocol,
            state=s,
            plans=plans,
            catalog=cat,
            tax=binding_from_policy(policy),
        )
        for plan, r in zip(plans, results, strict=False):
            print(f"\n  {s.clone_id} 第 {plan.year_index} 年 → {r.status}")
            _print_result(r)
            ok = ok and r.status not in ("input_error", "data_error")
        if len(results) < len(plans):
            print(f"    （之后 {len(plans) - len(results)} 份计划不再执行：出局或终态不接受行动）")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="离线检查生活输入并结算样例计划（不评分、不排序）")
    ap.add_argument("--file", type=Path, default=DEFAULT)
    args = ap.parse_args()

    try:
        bundle, report = load_life_bundle(args.file, strict=False)
    except LifeContractError as exc:
        print(f"contract_valid = false\n{exc}")
        return 1

    print(f"文件              {args.file}")
    print(f"协议              {report.summary['protocol_id']}  status={report.protocol_status}")
    print(
        f"模拟长度          N = {report.summary['simulation_years']}，"
        f"终态编号 {report.summary['terminal_year_index']}"
    )
    print(f"contract_valid    {str(report.contract_valid).lower()}")
    print(f"settlement_ready  {str(report.settlement_ready).lower()}")
    print(f"scoring_ready     {str(report.scoring_ready).lower()}")
    print(f"国家              {report.summary['countries']}")
    print(f"计价口径          {report.summary['money_bases']}")
    print(
        f"规模              {report.summary['n_catalogs']} 城 / "
        f"{report.summary['n_opportunities']} 项机会 / {report.summary['n_people']} 人 / "
        f"{report.summary['n_clones']} 个分身 / {report.summary['n_plans']} 份年度计划 / "
        f"{report.summary['n_example_actions']} 条结构样例行动"
    )
    print(f"可得状态          {report.summary['availability']}")
    print()
    print("城市目录（只列结构，不比较、不排序）：")
    for cat in bundle.catalogs:
        avail: dict[str, int] = {}
        for o in cat.opportunities:
            avail[o.availability] = avail.get(o.availability, 0) + 1
        print(f"  {cat.city}  {cat.money_basis.numeraire_id}  {avail}  {cat.label}")
    if report.errors:
        print("\n错误：")
        for e in report.errors:
            print(f"  - {e}")
    if report.settlement_problems:
        print("\n不能结算的原因（结构仍可有效）：")
        for e in report.settlement_problems:
            print(f"  - {e}")
    if report.notes:
        print("\n说明：")
        for n in report.notes:
            print(f"  - {n}")

    settled_ok = True
    if report.settlement_ready and bundle.plans:
        settled_ok = _settle_all(bundle)
    print("\n本工具不计算任何城市得分或排序。")
    return 0 if report.contract_valid and settled_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
