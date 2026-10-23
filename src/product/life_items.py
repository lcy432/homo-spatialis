"""生活扩展候选的编译检查（E3，DECISIONS/0041）：完整食品方案、休闲活动、装备、享受型用品与服务能不能编进 E1。

判定只用 E1 自己的契约与词表，不另写一套规则：

1. 原文带日期窗口或年龄限制 → ``rejected``（E1 的体验没有日期窗口、资格标签不求值；不能删掉限制假装可执行）；
2. ``LifeOpportunity`` 契约拒绝（缺做饭时间、缺新鲜餐数、不是完整方案、代替正餐的套餐、寿命不明……）→ ``rejected``，原因取契约的
   拒绝说明；
3. 报价未知 → ``data_unknown``（不当零价，也不当可得）；
4. 用了词表没有的菜系、活动类别、装备能力或享受功能 → ``unsupported``；
5. 其余 → ``executable``。

缺的价格、寿命或时间不在这里补；资料不够的候选照实报告。编译产物带自己的内容身份。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError

from common.life_contracts import LifeOpportunity, canonical_fingerprint
from common.product_contracts import LifeItemCompilation, LifeItemVerdict, ProductMeta
from ledger.life_catalog import LifeBundle, life_facet_vocabulary_problems

LIFE_ITEM_COMPILER_VERSION = "e3-life-item-compiler-v1"
_RESTRICTED_ZH = (
    "原文带日期窗口或年龄限制：E1 的体验没有日期窗口、资格标签不求值，也不能删掉限制假装可执行"
)


@dataclass(frozen=True)
class LifeItemCandidate:
    """一条候选：编号、种类、标签、按 ``LifeOpportunity`` 写好的事实，以及来源原文里的日期或年龄限制（原样）。"""

    candidate_id: str
    kind: Literal["food_plan", "activity", "equipment", "enjoyment"]
    label: str
    opportunity: dict[str, Any]
    restrictions_zh: tuple[str, ...] = ()


def _contract_reasons(exc: ValidationError) -> tuple[str, ...]:
    out = []
    for e in exc.errors():
        where = ".".join(str(p) for p in e["loc"])
        msg = str(e["msg"]).removeprefix("Value error, ")
        out.append(f"{where}: {msg}"[:400] if where else msg[:400])
    return tuple(dict.fromkeys(out))


def verdict(bundle: LifeBundle, c: LifeItemCandidate) -> LifeItemVerdict:
    head = {"candidate_id": c.candidate_id, "kind": c.kind, "label": c.label}
    if c.restrictions_zh:
        reasons = (_RESTRICTED_ZH, *(f"原文：{r}"[:400] for r in c.restrictions_zh))
        return LifeItemVerdict(**head, status="rejected", reasons_zh=reasons)
    try:
        o = LifeOpportunity.model_validate(c.opportunity)
    except ValidationError as exc:
        return LifeItemVerdict(**head, status="rejected", reasons_zh=_contract_reasons(exc))
    if o.availability == "unknown":
        return LifeItemVerdict(
            **head, status="data_unknown", reasons_zh=("报价未知：不当零价，也不当可得",)
        )
    problems = life_facet_vocabulary_problems(bundle, c.candidate_id, o)
    if problems:
        return LifeItemVerdict(**head, status="unsupported", reasons_zh=tuple(problems))
    return LifeItemVerdict(**head, status="executable")


def compile_life_items(
    bundle: LifeBundle,
    candidates: tuple[LifeItemCandidate, ...],
    *,
    meta: ProductMeta,
    city_ref: str,
    scope_zh: str,
) -> LifeItemCompilation:
    verdicts = tuple(verdict(bundle, c) for c in candidates)
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
