"""发布能力清单（``ProductCapabilities``，E3-A）：E5 只能呈现这里有的城市、选项与规则。"""

from __future__ import annotations

from common.product_contracts import (
    AbilityOption,
    AttributeKindDef,
    CityOption,
    DimensionDefinition,
    HardRequirementOption,
    HealthOptionDef,
    HealthQuestion,
    HealthQuestionnaire,
    MaterialLeafDefinition,
    MoneyBasisOption,
    OccupationCapability,
    PointsRules,
    ProductCapabilities,
    SampleLabel,
)
from product.pack import DataPack

#: 一次审阅最多给几个职业候选（少量可读候选）。
MAX_OCCUPATION_CANDIDATES = 4


#: 金额写法（E3-A 收尾 F3，与 product.exact.parse_money_text 同一条规则）。
MONEY_INPUT_RULE = (
    "金额只接受两种写法：不分组的非负十进制（例如 3000.50），或严格每三位一组的千分位（例如 3,000.50）；"
    "小数点只能用“.”，最多两位小数。“3000,50”“3,,000”“1,23.45”这类写法会被拒绝，系统不猜你的意思"
)


def build_capabilities(pack: DataPack, *, sample: SampleLabel | None = None) -> ProductCapabilities:
    b = pack.body
    hq = b.health_questionnaire
    languages = sorted(
        {"zh"} | ({"en"} if any(a.isascii() for o in b.occupations for a in o.aliases) else set())
    )
    return ProductCapabilities(
        meta=pack.meta(sample),
        capabilities_id=f"caps.{b.pack_id}",
        cities=tuple(
            CityOption(
                city_ref=c.city_ref,
                country=c.country,
                scheme=c.scheme,
                code=c.code,
                label=c.label,
                geography_version=c.geography_version,
                data_status="synthetic_fixture",
                selectable=True,
                note=c.note,
            )
            for c in b.cities
        ),
        occupation=OccupationCapability(
            vocabulary_version=pack.meta().vocabulary_version,
            method="controlled_vocabulary_alias_match_v1",
            languages=tuple(languages),
            concept_count=len(b.occupations),
            max_candidates=MAX_OCCUPATION_CANDIDATES,
            note=(
                "用自然语言描述你的工作；系统按受控词表的别名给出少量可读候选（名称与职责），由你确认。分类代码只在内部保留，"
                "不需要、也不会请你填写；匹配不到时说明原因，不自动改成“普通白领”"
            ),
        ),
        dimensions=tuple(
            DimensionDefinition(
                key=d.key,  # type: ignore[arg-type]
                label=d.label,
                definition=d.definition,
                reference_improvement=d.reference_improvement,
                support=d.support,  # type: ignore[arg-type]
                support_note=d.support_note,
            )
            for d in b.dimensions
        ),
        material_leaves=tuple(
            MaterialLeafDefinition(
                key=m.key,
                label=m.label,
                definition=m.definition,
                support=m.support,  # type: ignore[arg-type]
                note=m.note,
            )
            for m in b.material_leaves
        ),
        points=PointsRules(
            total=100,
            manual_entry=b.points.manual_entry,
            manual_max_decimals=b.points.manual_max_decimals,
            display_decimals=b.points.display_decimals,
            canonical_form=b.points.canonical_form,
            equal_split_six=b.points.equal_split_six,
            equal_split_material=b.points.equal_split_material,
            rules=b.points.rules,
        ),
        health_questionnaire=HealthQuestionnaire(
            version=hq.version,
            status=hq.status,
            common_prompt=hq.common_prompt,
            questions=tuple(
                HealthQuestion(question_id=q.question_id, text=q.text) for q in hq.questions
            ),
            options=tuple(
                HealthOptionDef(option_id=o.option_id, label=o.label, meaning=o.meaning)
                for o in hq.options
            ),
            unanswered_rule=hq.unanswered_rule,
            rules=hq.rules,
        ),
        hard_requirement_options=tuple(
            HardRequirementOption(
                option_id=h.option_id,
                label=h.label,
                parameter=h.parameter,
                unit=h.unit,
                minimum=h.minimum,
                maximum=h.maximum,
                step=h.step,
                compiles_to=h.compiles_to,
                verification=h.verification,
            )
            for h in b.hard_requirement_options
        ),
        attribute_kinds=tuple(
            AttributeKindDef(
                kind=a.kind,
                label=a.label,
                support=a.support,  # type: ignore[arg-type]
                usage=a.usage,  # type: ignore[arg-type]
                note=a.note,
            )
            for a in b.attribute_kinds
        ),
        abilities=tuple(
            AbilityOption(ability_id=a.ability_id, label=a.label, ask_when=a.ask_when)
            for a in b.abilities
        ),
        money_bases=tuple(
            MoneyBasisOption(
                currency=m.currency,
                price_period=m.price_period,
                meaning=m.meaning,
                conversion_rule=m.conversion_rule,
                numeraire_id=m.numeraire_id,
                note=m.note,
            )
            for m in b.money_bases
        ),
        money_input_rule_zh=MONEY_INPUT_RULE,
        age_min=b.age_min,
        age_max=b.age_max,
        free_text_max_chars=2000,
        start_scenario=b.start_scenario,
        unsupported=b.unsupported,
        limits=b.limits,
    )
