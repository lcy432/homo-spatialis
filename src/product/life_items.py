"""生活扩展候选的编译检查（E3，DECISIONS/0041）：完整食品方案、休闲活动、装备、享受型用品与服务能不能交给 E1 执行。

“局部结构合法”不等于“能交给 E1 执行”。判定按顺序，只用 E1 自己的契约与检查，不另写一套生活数学：

1. 限制：调用方转来的原文（``restrictions_zh``）**和**候选自己携带的限制——资格标签、年龄与日期字段（与真实资料构建同一个年龄
   解析 ``product.realdata.activities.age_eligibility``）。E1 的体验没有日期窗口、资格标签不对它求值：有限制 → ``rejected``；
2. ``LifeOpportunity`` 契约拒绝（缺做饭时间、缺新鲜餐数、不是完整方案、代替正餐的套餐、寿命不明……）→ ``rejected``；
3. 种类与事实：``food_plan`` 要有完整食品方案事实，``activity`` 要有活动事实，``equipment`` 要有装备事实，``enjoyment`` 要有享受
   事实；对不上 → ``rejected``；
4. 确定不可得 → ``unavailable``；报价未知 → ``data_unknown``；
5. 词表外的菜系、活动类别、装备能力或享受功能 → ``unsupported``；
6. 放进这座城目录的副本（完整食品方案加进最低食品的合格名单），跑 E1 的目录契约（金额口径、精度、重名……）、跨对象检查（类别、
   功能……）与可结算检查（最低方案与合格目录是否矛盾、计费语义……），再核对 E1 会按什么角色执行它；任何一条不过 → ``rejected``；
7. 评分要读的事实未知（例如新鲜餐数为 null）→ ``data_unknown``（E1 能执行，但相关评分会是未知，不当 0）；
8. 其余 → ``executable``。

缺的价格、寿命、时间或限制不在这里补，也不删；资料不够的候选照实报告。编译结果带自己的内容身份。**现在只有受控样例**，还没有接
intake-02 的真实资料导出。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError

from common.life_contracts import CityLifeCatalog, LifeOpportunity, canonical_fingerprint
from common.product_contracts import LifeItemCompilation, LifeItemVerdict, ProductMeta
from ledger.life_catalog import (
    LifeBundle,
    cross_validate,
    item_role,
    life_facet_vocabulary_problems,
)
from product.realdata.activities import age_eligibility

LIFE_ITEM_COMPILER_VERSION = "e3-life-item-compiler-v2"
_RESTRICTED_ZH = (
    "带日期、年龄或资格限制：E1 的体验没有日期窗口、资格标签不求值，也不能删掉限制假装可执行"
)
#: 每种候选在 E1 里应当按什么角色执行（``ledger.life_catalog.item_role``）
_KIND_ROLES: dict[str, frozenset[str]] = {
    "food_plan": frozenset({"food"}),
    "activity": frozenset({"experience"}),
    "equipment": frozenset({"durable_good"}),
    "enjoyment": frozenset({"durable_good", "periodic_service", "experience"}),
}
#: 日期与时段字段（真实资料构建里活动的属性名，加上通用写法）
_DATE_KEYS = frozenset(
    {
        "date",
        "dates",
        "days",
        "season",
        "schedule",
        "date_notes",
        "registration_opens",
        "registration_deadline",
    }
)


@dataclass(frozen=True)
class LifeItemCandidate:
    """一条候选：编号、种类、标签、按 ``LifeOpportunity`` 写好的事实，以及调用方另外转来的限制原文。"""

    candidate_id: str
    kind: Literal["food_plan", "activity", "equipment", "enjoyment"]
    label: str
    opportunity: dict[str, Any]
    restrictions_zh: tuple[str, ...] = ()


def _is_age_key(key: str) -> bool:
    return (
        key in ("age", "ages", "min_age", "max_age")
        or key.startswith("age_")
        or key.endswith("_age")
    )


def _is_date_key(key: str) -> bool:
    return key in _DATE_KEYS or key.startswith("date_") or key.endswith("_date")


def carried_restrictions(opportunity: dict[str, Any]) -> list[str]:
    """候选自己携带的日期、年龄或资格限制（原样列出，不删、不改写）。年龄原文用真实资料构建的同一个解析：只有明确写了“不限
    年龄”才不算限制；没写清楚就是限制未知，也不能当成任何人都能参加。"""
    out: list[str] = []
    tags = list(opportunity.get("eligibility") or ())
    if tags:
        out.append(f"资格标签 {tags}")
    for where in ("spec", "attributes"):
        for key, value in (opportunity.get(where) or {}).items():
            if key == "age_restricted":
                if value is not False:
                    out.append(f"{where}.{key} = {value!r}（年龄限制或是否限制未知）")
            elif key in ("age_range", "age_rule"):
                if age_eligibility(value)["age_restricted"] is not False:
                    out.append(f"{where}.{key} = {value!r}（年龄限制或是否限制未知）")
            elif _is_age_key(key):
                out.append(f"{where}.{key} = {value!r}（年龄限制）")
            elif _is_date_key(key):
                out.append(f"{where}.{key} = {value!r}（日期或时段限制）")
    return out


def _contract_reasons(exc: ValidationError) -> tuple[str, ...]:
    out = []
    for e in exc.errors():
        where = ".".join(str(p) for p in e["loc"])
        msg = str(e["msg"]).removeprefix("Value error, ")
        out.append(f"{where}: {msg}"[:400] if where else msg[:400])
    return tuple(dict.fromkeys(out))


def _kind_problems(kind: str, o: LifeOpportunity) -> list[str]:
    if kind == "food_plan":
        if o.food_plan is None:
            return [
                "标成完整食品方案，却没有食品方案事实（组成、每周餐数、新鲜餐、菜系、做饭与外食小时）"
            ]
        if not o.food_plan.covers_basic_month:
            return ["食品方案没有覆盖整月基本饮食：几项食材报价或一家餐馆不是完整方案"]
    if kind == "activity" and o.activity is None:
        return [
            "标成休闲活动，却没有活动事实（参与小时、准备往返小时、类别）：E1 无法记录参与，也无法评价"
        ]
    if kind == "equipment" and o.equipment is None:
        return ["标成活动装备，却没有装备事实（提供的活动能力）"]
    if kind == "enjoyment" and not o.enjoyment:
        return ["标成享受型用品或服务，却没有享受事实（功能、提供方式）"]
    return []


def _e1_problems(bundle: LifeBundle, city: str, kind: str, o: LifeOpportunity) -> list[str]:
    """把候选放进这座城目录的副本，跑 E1 的目录契约、跨对象与可结算检查，再核对执行角色。返回新增的问题（空 = 都接受）。"""
    catalog = bundle.catalog_for(city)
    if catalog is None:
        return [f"{city} 没有生活目录"]
    data = catalog.model_dump(mode="json")
    if any(x["opportunity_id"] == o.opportunity_id for x in data["opportunities"]):
        return [f"{o.opportunity_id} 与目录里已有的机会重名"]
    data["opportunities"].append(o.model_dump(mode="json"))
    if kind == "food_plan":
        for need in data["minimum_living"]:
            if need["need"] == "food":
                need["qualifying_opportunity_ids"].append(o.opportunity_id)
    try:
        trial = CityLifeCatalog.model_validate(data)
    except ValidationError as exc:
        return list(_contract_reasons(exc))
    added = trial.opportunity(o.opportunity_id)
    assert added is not None
    role = item_role(trial, added)
    if role not in _KIND_ROLES[kind]:
        return [f"候选种类是 {kind}，E1 会按 {role} 执行它：种类与执行角色对不上"]
    before = cross_validate(bundle)
    after = cross_validate(
        bundle.model_copy(
            update={"catalogs": tuple(trial if str(c.city) == city else c for c in bundle.catalogs)}
        )
    )
    seen = set(before.errors) | set(before.settlement_problems)
    return [e[:400] for e in (*after.errors, *after.settlement_problems) if e not in seen]


def verdict(bundle: LifeBundle, city: str, c: LifeItemCandidate) -> LifeItemVerdict:
    head = {"candidate_id": c.candidate_id, "kind": c.kind, "label": c.label}

    def say(status: str, reasons: tuple[str, ...] | list[str] = ()) -> LifeItemVerdict:
        return LifeItemVerdict(**head, status=status, reasons_zh=tuple(r[:400] for r in reasons))  # type: ignore[arg-type]

    restrictions = [*c.restrictions_zh, *carried_restrictions(c.opportunity)]
    if restrictions:
        return say("rejected", (_RESTRICTED_ZH, *(f"限制：{r}" for r in restrictions)))
    try:
        o = LifeOpportunity.model_validate(c.opportunity)
    except ValidationError as exc:
        return say("rejected", _contract_reasons(exc))
    wrong = _kind_problems(c.kind, o)
    if wrong:
        return say("rejected", wrong)
    if o.availability == "unavailable":
        return say(
            "unavailable", ("确定不可得：本城没有这一项（保留记录，不能执行，也不能补价格）",)
        )
    if o.availability == "unknown":
        return say("data_unknown", ("报价未知：不当零价，也不当可得",))
    vocab = life_facet_vocabulary_problems(bundle, c.candidate_id, o)
    if vocab:
        return say("unsupported", vocab)
    e1 = _e1_problems(bundle, city, c.kind, o)
    if e1:
        return say("rejected", e1)
    if o.food_plan is not None:
        unknown = o.food_plan.unknown_facts(frozenset(bundle.cuisines))
        if unknown:
            return say(
                "data_unknown",
                (
                    f"{list(unknown)} 是资料未知：E1 能执行这份方案，但饮食生活评分会是未知（不当 0）",
                ),
            )
    return say("executable")


def compile_life_items(
    bundle: LifeBundle,
    candidates: tuple[LifeItemCandidate, ...],
    *,
    meta: ProductMeta,
    city_ref: str,
    scope_zh: str,
) -> LifeItemCompilation:
    verdicts = tuple(verdict(bundle, city_ref, c) for c in candidates)
    content = {
        "compiler_version": LIFE_ITEM_COMPILER_VERSION,
        "city_ref": city_ref,
        "candidates": [
            {
                "candidate_id": c.candidate_id,
                "kind": c.kind,
                "opportunity": c.opportunity,
                "restrictions": list(c.restrictions_zh),
            }
            for c in candidates
        ],
        "vocabularies": {
            "cuisines": list(bundle.cuisines),
            "leisure_categories": list(bundle.leisure_categories),
            "capabilities": list(bundle.capabilities),
            "enjoyment_functions": list(bundle.enjoyment_functions),
        },
        "verdicts": [v.model_dump(mode="json") for v in verdicts],
    }
    fingerprint = canonical_fingerprint(content)
    return LifeItemCompilation(
        meta=meta,
        compilation_id=f"life-items.{fingerprint[:12]}",
        compiler_version=LIFE_ITEM_COMPILER_VERSION,
        scope_zh=scope_zh,
        city_ref=city_ref,
        verdicts=verdicts,
        compilation_fingerprint=fingerprint,
    )
