"""机会注册表的共同规则：来源性质取最弱、资料状态、四种能力、编译状态。各板块的构造函数在各自的模块里，都经过这里。

**四种能力分开判断**（``Capabilities``）：

* 已采集：机会本身就是一条真实记录（``origin = record``）。由映射生成的候选不是“已采集”，即使它的数字来自真实资料。
* 能供规划参考：必需的事实都有（资料状态不是 ``insufficient``），可以放进给代理看的事实里。
* 可执行：能编进当前引擎（``compile = compilable``）、供给已知有卖、报价已知。
* 可评分：能对应现有评分项而且评分要读的资料齐全（由板块明确声明；默认否）。

**编译缺口分两类**（``CompileInfo``）：资料缺口自动包含必需事实、金额事实与“E1 需要的事实”里缺的那些（``missing:<事实名>``），
板块再补充其他资料缺口（例如通勤不是按房源算的）；E1 表达能力的缺口由板块写明。状态由两类缺口推出来。

**资料状态**：必需事实有缺 → ``insufficient``；必需的齐、可选的有缺 → ``partial``；全部齐 → ``complete``。缺失、隐去、无法映射、
访问受限都算缺；``not_applicable`` 不算。
"""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

from common.intake_contracts import (
    KIND_ORDER,
    Calendar,
    Capabilities,
    CompileInfo,
    FactKind,
    FieldValue,
    GeoRef,
    MenuPolicyKind,
    Opportunity,
    Origin,
    Scalar,
    Sector,
    SupplyStatus,
    Terms,
)


def weakest_kind(facts: tuple[FieldValue, ...] | list[FieldValue]) -> FactKind:
    kinds = [f.kind for f in facts if f.status == "present"]
    if not kinds:
        return "observed"
    return min(kinds, key=KIND_ORDER.index)


def weight_text(x: Fraction | int | float) -> str:
    """目标权重的文字（6 位小数）。权重只用于抽样，不是金额。"""
    return str(Decimal(str(float(x))).quantize(Decimal("0.000001")))


def data_status(
    facts: tuple[FieldValue, ...], required: tuple[str, ...]
) -> tuple[str, tuple[str, ...]]:
    missing = tuple(f.name for f in facts if f.status not in ("present", "not_applicable"))
    req_missing = [
        name
        for name in required
        if not any(f.name == name and f.status == "present" for f in facts)
    ]
    if req_missing:
        return "insufficient", tuple(sorted(set(missing) | set(req_missing)))
    return ("partial" if missing else "complete"), missing


def make_opportunity(
    *,
    opportunity_id: str,
    sector: Sector,
    kind: str,
    label: str,
    cbsa: str | None,
    geo: GeoRef,
    origin: Origin,
    lineage: tuple[str, ...],
    facts: tuple[FieldValue, ...],
    required: tuple[str, ...],
    supply_status: SupplyStatus,
    menu_policy: MenuPolicyKind,
    e1_category: str | None,
    e1_needs: tuple[str, ...] = (),
    data_gaps: tuple[str, ...] = (),
    e1_gaps: tuple[str, ...] = (),
    reference_only: bool = False,
    method: str | None = None,
    attributes: dict[str, Scalar] | None = None,
    terms: dict[str, Terms] | None = None,
    target_weight: str | None = None,
    weight_basis: str | None = None,
    stratum: str | None = None,
    calendar: Calendar | None = None,
    scorable: bool = False,
    price_fact: str | None = None,
) -> Opportunity:
    status, missing = data_status(facts, required)
    present = {f.name for f in facts if f.status == "present"}
    needed = [*required, *([price_fact] if price_fact else []), *e1_needs]
    auto = [f"missing:{name}" for name in dict.fromkeys(needed) if name not in present]
    compile = CompileInfo.of(
        data_gaps=(*auto, *data_gaps),
        e1_gaps=e1_gaps,
        e1_category=e1_category,
        reference_only=reference_only,
    )
    price_known = price_fact is None or price_fact in present
    executable = compile.status == "compilable" and supply_status == "offered" and price_known
    return Opportunity(
        opportunity_id=opportunity_id,
        sector=sector,
        kind=kind,
        label=label[:200],
        cbsa=cbsa,
        geo=geo,
        origin=origin,
        method=method,
        lineage=lineage,
        facts=facts,
        attributes=attributes or {},
        terms=terms or {},
        supply_status=supply_status,
        data_status=status,  # type: ignore[arg-type]
        missing_fields=missing,
        target_weight=target_weight,
        weight_basis=weight_basis,
        stratum=stratum,
        calendar=calendar,
        menu_policy=menu_policy,
        capabilities=Capabilities(
            collected=origin == "record",
            planning_reference=status != "insufficient",
            executable=executable,
            scorable=scorable and status != "insufficient",
        ),
        compile=compile,
        weakest_kind=weakest_kind(facts),
        price_fact=price_fact,
    )
