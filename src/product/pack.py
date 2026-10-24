"""E3 合成资料包的加载与内容身份（E3-A，DECISIONS/0034）。

资料包是 E3 维护的数据：城市、职业受控词表、六维与物质题干、独立健康问卷、硬要求与属性偏好的支持清单、标准开局的模型设定、
便利资料候选，以及它**显式采用**的产品协议基础样例（``e1_base``）。结构在这里严格检查（未知字段报错）；内容身份是规范 JSON 的
SHA-256——资料包改了，旧确认与预检查都对不上。
"""

from __future__ import annotations

import datetime as _dt
from functools import cached_property
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from common.product_contracts import (
    CONVERSION_VERSION,
    HEALTH_QUESTIONNAIRE_VERSION,
    PRODUCT_SCHEMA_VERSION,
    QUESTIONNAIRE_VERSION,
    VOCABULARY_VERSION,
    DataPackRef,
    OpportunityCandidate,
    ProductMeta,
    SampleLabel,
    SourceFact,
    content_fingerprint,
)

DEFAULT_PACK = Path("configs/product/e3a_synthetic_pack.yaml")


class PackError(ValueError):
    """资料包结构或内容不合法。"""


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class E1Base(_Strict):
    life_input: str
    scoring_config: str
    evaluation_config: str
    city_facts: str
    protocol_id: str
    scoring_version: str
    scoring_content_fingerprint: str
    prompt_template: str


class PlannerDecl(_Strict):
    kind: Literal["mock"]
    planner_id: str
    version: str
    rules_version: str


class RunDecl(_Strict):
    prompt_template: str
    repeat_indexes: tuple[int, ...] = Field(min_length=1)
    max_revisions_per_year: int
    #: 资料包配套验证过、可以开放的模拟年数（E3C 收尾）。不写 = 只有基础样例协议的那一个年数；服务的年数选项与它取交集。
    years_supported: tuple[int, ...] | None = None
    permission_version: str
    show_health_answers: bool
    planner: PlannerDecl


class MoneyBasisDecl(_Strict):
    currency: str
    base_year: int
    minor_unit_digits: int


class MoneyBasisOptionDecl(_Strict):
    currency: str
    price_period: str
    meaning: Literal["liquid_funds_v1"]
    conversion_rule: str
    numeraire_id: str
    note: str


class StartSettings(_Strict):
    time_budget_hours_per_year: float
    energy_quota: float
    energy_scale_version: str
    reserved_text: str
    readable_time_note: str
    readable_energy_note: str


class CityDecl(_Strict):
    city_ref: str
    country: str
    scheme: str
    code: str
    label: str
    geography_version: str
    note: str


class MappingDecl(_Strict):
    country: str
    scheme: str
    version: str
    code: str
    basis: str


class OccupationDecl(_Strict):
    concept_id: str
    label: str
    duties: tuple[str, ...]
    aliases: tuple[str, ...]
    mappings: tuple[MappingDecl, ...]


class AbilityDecl(_Strict):
    ability_id: str
    label: str
    ask_when: str


class DimensionDecl(_Strict):
    key: str
    label: str
    definition: str
    reference_improvement: str
    support: str
    support_note: str


class MaterialLeafDecl(_Strict):
    key: str
    label: str
    definition: str
    support: str
    note: str


class VocabularyTermDecl(_Strict):
    key: str
    label: str
    aliases: tuple[str, ...]


class InterestsDecl(_Strict):
    """兴趣与口味的有限词表与别名（DECISIONS/0041）。键必须与生活输入里声明的词表相同（编译时核对）。"""

    leisure_categories: tuple[VocabularyTermDecl, ...]
    cuisines: tuple[VocabularyTermDecl, ...]
    leisure_states_zh: tuple[str, ...]
    taste_states_zh: tuple[str, ...]
    matching_note_zh: str


class PointsDecl(_Strict):
    manual_entry: str
    manual_max_decimals: int
    display_decimals: int
    canonical_form: str
    equal_split_six: str
    equal_split_material: str
    rules: tuple[str, ...]


class HealthQuestionDecl(_Strict):
    question_id: str
    text: str
    agree_meaning: str
    disagree_meaning: str


class HealthOptionDecl(_Strict):
    option_id: Literal["agree", "disagree", "no_clear_tendency", "unsure"]
    label: str
    meaning: str


class HealthQuestionnaireDecl(_Strict):
    version: str
    status: Literal["draft_wording_not_validated"]
    common_prompt: str
    questions: tuple[HealthQuestionDecl, ...]
    options: tuple[HealthOptionDecl, ...]
    unanswered_rule: str
    rules: tuple[str, ...]


class HardRequirementDecl(_Strict):
    option_id: str
    label: str
    parameter: str
    unit: str
    minimum: int
    maximum: int
    step: int
    need: str
    compiles_to: str
    verification: str


class AttributeKindDecl(_Strict):
    kind: str
    label: str
    support: str
    usage: tuple[str, ...]
    note: str


class CatalogScope(_Strict):
    scope_version: str
    declared_scope: str
    categories: tuple[str, ...]
    tasks: tuple[str, ...]
    geography_scope: str


class PackBody(_Strict):
    pack_id: str
    status: Literal["synthetic_fixture_only", "real_data_frozen"]
    observed_on: _dt.date
    note: str
    e1_base: E1Base
    run: RunDecl
    money_basis: MoneyBasisDecl
    money_bases: tuple[MoneyBasisOptionDecl, ...]
    start_model_settings: StartSettings
    age_min: int
    age_max: int
    cities: tuple[CityDecl, ...]
    occupations: tuple[OccupationDecl, ...]
    abilities: tuple[AbilityDecl, ...] = ()
    dimensions: tuple[DimensionDecl, ...]
    material_leaves: tuple[MaterialLeafDecl, ...]
    points: PointsDecl
    health_questionnaire: HealthQuestionnaireDecl
    hard_requirement_options: tuple[HardRequirementDecl, ...] = ()
    attribute_kinds: tuple[AttributeKindDecl, ...] = ()
    start_scenario: tuple[str, ...]
    unsupported: tuple[str, ...] = ()
    limits: tuple[str, ...] = ()
    catalog_scope: CatalogScope
    interests: InterestsDecl
    facts: tuple[SourceFact, ...] = ()
    candidates: tuple[OpportunityCandidate, ...] = ()


class DataPack:
    """一份已加载、已检查的资料包。``raw`` 是 YAML 原样（内容身份按它算），``body`` 是严格结构。"""

    def __init__(self, raw: dict[str, Any], path: Path | None = None) -> None:
        self.raw = raw
        self.path = path
        try:
            self.body = PackBody.model_validate(raw["pack"])
        except (KeyError, ValidationError) as exc:
            raise PackError(f"资料包结构不合法：{exc}") from exc
        problems = self._problems()
        if problems:
            raise PackError("资料包内容不合法：\n  " + "\n  ".join(problems))

    def _problems(self) -> list[str]:
        b = self.body
        out: list[str] = []
        if b.health_questionnaire.version != HEALTH_QUESTIONNAIRE_VERSION:
            out.append(
                f"健康问卷版本 {b.health_questionnaire.version} 与契约声明的 {HEALTH_QUESTIONNAIRE_VERSION} 不同"
            )
        for name, terms in (
            ("休闲活动类别", b.interests.leisure_categories),
            ("菜系", b.interests.cuisines),
        ):
            tk = [t.key for t in terms]
            if len(tk) != len(set(tk)):
                out.append(f"{name}的键重复：{tk}")
            if any(not t.aliases for t in terms):
                out.append(f"{name}每一项都要有别名（明确映射，不靠模型猜）")
        keys = [d.key for d in b.dimensions]
        want = ["material", "safety", "opportunity", "leisure", "environment", "commute"]
        if keys != want:
            out.append(f"六维必须按 {want} 的顺序声明，得到 {keys}")
        for name, ids in (
            ("城市", [c.city_ref for c in b.cities]),
            ("职业概念", [o.concept_id for o in b.occupations]),
            ("候选", [c.candidate_id for c in b.candidates]),
            ("来源事实", [f.fact_id for f in b.facts]),
            ("健康题", [q.question_id for q in b.health_questionnaire.questions]),
        ):
            dup = sorted({i for i in ids if ids.count(i) > 1})
            if dup:
                out.append(f"{name} 重复：{dup}")
        facts = {f.fact_id for f in b.facts}
        cities = {c.city_ref for c in b.cities}
        for c in b.candidates:
            if c.city_ref not in cities:
                out.append(f"候选 {c.candidate_id} 的城市 {c.city_ref} 不在资料包的城市里")
            for ref in (*c.price.facts, *c.lifetime.facts, *c.effect.facts, *c.participation.facts):
                if ref not in facts:
                    out.append(f"候选 {c.candidate_id} 引用了不存在的来源事实 {ref}")
        return out

    @cached_property
    def fingerprint(self) -> str:
        return content_fingerprint(_jsonable(self.raw["pack"]))

    def ref(self) -> DataPackRef:
        return DataPackRef(
            pack_id=self.body.pack_id,
            content_fingerprint=self.fingerprint,
            status=self.body.status,
            note="合成资料包：全部数值是手工合成的模型设定或测试资料，不是真实城市资料"
            if self.body.status == "synthetic_fixture_only"
            else "冻结的真实资料包（DECISIONS/0042）：城市资料来自公开来源与登记的模型设定，生活规则与评分参数是合成探索设定",
        )

    def meta(self, sample: SampleLabel | None = None) -> ProductMeta:
        return ProductMeta(
            schema_version=PRODUCT_SCHEMA_VERSION,
            questionnaire_version=QUESTIONNAIRE_VERSION,
            vocabulary_version=VOCABULARY_VERSION,
            health_questionnaire_version=self.body.health_questionnaire.version,
            conversion_version=CONVERSION_VERSION,
            data_pack=self.ref(),
            sample=sample,
        )

    def city(self, city_ref: str) -> CityDecl | None:
        return next((c for c in self.body.cities if c.city_ref == city_ref), None)

    def occupation(self, concept_id: str) -> OccupationDecl | None:
        return next((o for o in self.body.occupations if o.concept_id == concept_id), None)


def _jsonable(obj: Any) -> Any:
    """YAML 读进来的日期等对象换成 JSON 能表示的文字（内容身份用）。"""
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, _dt.date):
        return obj.isoformat()
    return obj


def load_pack(path: Path = DEFAULT_PACK) -> DataPack:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "pack" not in raw:
        raise PackError(f"{path}: 顶层必须是映射，并有 pack 一节")
    return DataPack(raw, Path(path))
