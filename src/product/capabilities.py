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
    InterestCapability,
    MaterialLeafDefinition,
    MoneyBasisOption,
    OccupationCapability,
    PointsRules,
    ProductCapabilities,
    SampleLabel,
    VocabularyTerm,
)
from product.display import label, t
from product.pack import DataPack

#: 一次审阅最多给几个职业候选（少量可读候选）。
MAX_OCCUPATION_CANDIDATES = 4


def money_input_rule() -> str:
    """金额写法（E3-A 收尾 F3，与 product.exact.parse_money_text 同一条规则）。"""
    return t(
        "input.capabilities.money_input_rule",
        "金额只接受两种写法：不分组的非负十进制（例如 3000.50），或严格每三位一组的千分位（例如 3,000.50）；"
        "小数点只能用“.”，最多两位小数。“3000,50”“3,,000”“1,23.45”这类写法会被拒绝，系统不猜你的意思",
    )


def money_basis_id(currency: str, price_period: str) -> str:
    """现金口径选项的显示编号（币种与金额时期）。"""
    return f"{currency}.{price_period}"


def matching_note(pack: DataPack) -> str:
    """兴趣与口味匹配规则的说明（能力清单与确认画像共用同一个显示编号）。"""
    return label("capability", "interests", pack.body.interests.matching_note_zh, "matching_note")


def build_capabilities(pack: DataPack, *, sample: SampleLabel | None = None) -> ProductCapabilities:
    """能力清单。资料包里的名称与说明按 ``kind/编号`` 生成显示文字（``product.display.label``）；词表的别名是匹配用的原文词语，
    不是显示文字，照原样给出、不翻译（英文页面不能暗示用英文写同一个词也能认出）。"""
    b = pack.body
    hq = b.health_questionnaire

    def text(section: str, field: str, zh: str) -> str:
        return label("capability", section, zh, field)

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
                label=label("city", c.city_ref, c.label),
                geography_version=c.geography_version,
                data_status="real_data" if b.status == "real_data_frozen" else "synthetic_fixture",
                selectable=True,
                note=label("city", c.city_ref, c.note, "note"),
            )
            for c in b.cities
        ),
        occupation=OccupationCapability(
            vocabulary_version=pack.meta().vocabulary_version,
            method="controlled_vocabulary_alias_match_v1",
            languages=tuple(languages),
            concept_count=len(b.occupations),
            max_candidates=MAX_OCCUPATION_CANDIDATES,
            note=t(
                "input.capabilities.occupation_note",
                "用自然语言描述你的工作；系统按受控词表的别名给出少量可读候选（名称与职责），由你确认。分类代码只在内部保留，"
                "不需要、也不会请你填写；匹配不到时说明原因，不自动改成“普通白领”",
            ),
        ),
        dimensions=tuple(
            DimensionDefinition(
                key=d.key,  # type: ignore[arg-type]
                label=label("dimension", d.key, d.label),
                definition=label("dimension", d.key, d.definition, "definition"),
                reference_improvement=label(
                    "dimension", d.key, d.reference_improvement, "reference_improvement"
                ),
                support=d.support,  # type: ignore[arg-type]
                support_note=label("dimension", d.key, d.support_note, "support_note"),
            )
            for d in b.dimensions
        ),
        material_leaves=tuple(
            MaterialLeafDefinition(
                key=m.key,
                label=label("leaf", m.key, m.label),
                definition=label("leaf", m.key, m.definition, "definition"),
                support=m.support,  # type: ignore[arg-type]
                note=label("leaf", m.key, m.note, "note"),
            )
            for m in b.material_leaves
        ),
        points=PointsRules(
            total=100,
            manual_entry=text("points", "manual_entry", b.points.manual_entry),
            manual_max_decimals=b.points.manual_max_decimals,
            display_decimals=b.points.display_decimals,
            canonical_form=text("points", "canonical_form", b.points.canonical_form),
            equal_split_six=b.points.equal_split_six,
            equal_split_material=b.points.equal_split_material,
            rules=tuple(text("points", f"rules.{i}", r) for i, r in enumerate(b.points.rules)),
        ),
        health_questionnaire=HealthQuestionnaire(
            version=hq.version,
            status=hq.status,
            common_prompt=text("health_questionnaire", "common_prompt", hq.common_prompt),
            questions=tuple(
                HealthQuestion(
                    question_id=q.question_id,
                    text=label("health_question", q.question_id, q.text, "text"),
                )
                for q in hq.questions
            ),
            options=tuple(
                HealthOptionDef(
                    option_id=o.option_id,
                    label=label("health_option", o.option_id, o.label),
                    meaning=label("health_option", o.option_id, o.meaning, "meaning"),
                )
                for o in hq.options
            ),
            unanswered_rule=text("health_questionnaire", "unanswered_rule", hq.unanswered_rule),
            rules=tuple(
                text("health_questionnaire", f"rules.{i}", r) for i, r in enumerate(hq.rules)
            ),
        ),
        hard_requirement_options=tuple(
            HardRequirementOption(
                option_id=h.option_id,
                label=label("hard_requirement", h.option_id, h.label),
                parameter=h.parameter,
                unit=label("hard_requirement", h.option_id, h.unit, "unit"),
                minimum=h.minimum,
                maximum=h.maximum,
                step=h.step,
                compiles_to=label("hard_requirement", h.option_id, h.compiles_to, "compiles_to"),
                verification=label("hard_requirement", h.option_id, h.verification, "verification"),
            )
            for h in b.hard_requirement_options
        ),
        attribute_kinds=tuple(
            AttributeKindDef(
                kind=a.kind,
                label=label("attribute_kind", a.kind, a.label),
                support=a.support,  # type: ignore[arg-type]
                usage=a.usage,  # type: ignore[arg-type]
                note=label("attribute_kind", a.kind, a.note, "note"),
            )
            for a in b.attribute_kinds
        ),
        abilities=tuple(
            AbilityOption(
                ability_id=a.ability_id,
                label=label("ability", a.ability_id, a.label),
                ask_when=label("ability", a.ability_id, a.ask_when, "ask_when"),
            )
            for a in b.abilities
        ),
        money_bases=tuple(
            MoneyBasisOption(
                currency=m.currency,
                price_period=m.price_period,
                meaning=m.meaning,
                conversion_rule=m.conversion_rule,
                numeraire_id=m.numeraire_id,
                note=label(
                    "money_basis", money_basis_id(m.currency, m.price_period), m.note, "note"
                ),
            )
            for m in b.money_bases
        ),
        money_input_rule_zh=money_input_rule(),
        age_min=b.age_min,
        age_max=b.age_max,
        free_text_max_chars=2000,
        start_scenario=tuple(
            text("start_scenario", f"item.{i}", s) for i, s in enumerate(b.start_scenario)
        ),
        unsupported=tuple(text("unsupported", f"item.{i}", s) for i, s in enumerate(b.unsupported)),
        limits=tuple(text("limits", f"item.{i}", s) for i, s in enumerate(b.limits)),
        interests=InterestCapability(
            leisure_categories=tuple(
                VocabularyTerm(
                    key=term.key,
                    label=label("leisure_category", term.key, term.label),
                    aliases=term.aliases,
                )
                for term in b.interests.leisure_categories
            ),
            cuisines=tuple(
                VocabularyTerm(
                    key=term.key, label=label("cuisine", term.key, term.label), aliases=term.aliases
                )
                for term in b.interests.cuisines
            ),
            leisure_states_zh=tuple(
                text("interests", f"leisure_states.{i}", s)
                for i, s in enumerate(b.interests.leisure_states_zh)
            ),
            taste_states_zh=tuple(
                text("interests", f"taste_states.{i}", s)
                for i, s in enumerate(b.interests.taste_states_zh)
            ),
            matching_note_zh=matching_note(pack),
        ),
    )
