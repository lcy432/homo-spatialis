"""E1-7A 城市背景事实：代理可以看到的生活事实，与它们到评分输入的内部对应（`DECISIONS/0030` 第三节）。

问题：安全、发展机会、休闲与环境这类城市背景，以前只记在评分条件 ``CityScoringConditions`` 里，与隐藏的映射参数（基础量 b、
上限 c、改善效率 η）放在一起。分身做计划需要能比较这些生活条件；但评分条件不能整份放出去，也不能把 b 换个字段名当成事实。

所以分成两份：

* **城市事实表**（``CityFactSheet`` / ``CityFact``，代理可见）：这是什么事实、数值或类别与单位、已知 / 确定不存在 / 资料未知、
  时间口径与来源性质（城市范围取来源记录的 ``geography_scope``）。合成事实明确标成合成（来源 ``kind = synthetic``）。各城用同一套
  事实定义（编号、名称、含义、取值类型、单位、时间口径）；将来 E3 用同一个结构接真实资料，规划器不用重写。
* **事实到评分输入的对应**（``FactScoringLink``，内部）：某个数值事实按哪条换算变成评分条件里哪个分项的背景量 b，以及这条换算
  自己的来源（合成设定、派生或估计）。"事实从哪来"与"换算或评分假设从哪来"分开记。它与评分条件一样是内部对象：不进观察、
  不进提示（登记在 ``internal_city_fact_models``，保留键 ``RESERVED_FACT_LINK_KEYS``）。

这里只有形状、局部约束与内容身份；跨对象检查（各城定义相同、事实与评分条件逐项对得上、有城市背景的分项都有可见事实）在
``ledger.life_city_facts``。本轮不引入新的评分公式：换算只是把事实写成评分条件里已有的那个 b。
"""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Literal

from pydantic import Field, field_validator, model_validator

from common.ids import CityId
from common.life_contracts import (
    ContractModel,
    IdStr,
    KeyStr,
    SourceRecord,
    canonical_fingerprint,
    to_fraction,
)

#: 事实表的格式版本。改字段含义就换名字，并在 ADR 记录。
CITY_FACTS_VERSION = "e1-7a-city-facts-v1"
#: 唯一支持的换算：背景量 b = 系数 × 事实数值（精确十进制，不加容差）。
LINEAR_BASE_RULE = "linear_base_v1"

FactStatus = Literal["known", "unavailable", "unknown"]
FACT_STATUS_LABEL: dict[str, str] = {
    "known": "已知",
    "unavailable": "确定不存在",
    "unknown": "资料未知",
}
SOURCE_KIND_LABEL: dict[str, str] = {
    "observed": "观测资料",
    "derived": "可审计派生",
    "estimated": "统计估计",
    "synthetic": "合成设定（不是观测资料）",
}


def _decimal_text(text: str, what: str) -> Fraction:
    try:
        d = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"{what} 必须是十进制写法，得到 {text!r}") from exc
    if not d.is_finite():
        raise ValueError(f"{what} 必须是有限数，得到 {text!r}")
    return Fraction(d)


class CityFact(ContractModel):
    """一座城的一条背景事实（代理可见）。``known`` 必须有值；``unavailable``（确定不存在）与 ``unknown``（资料未知）都不带值——
    未知不是零，确定不存在也不是"资料少就当零"。"""

    fact_id: KeyStr
    label: str = Field(min_length=1, max_length=80)
    #: 这是什么、怎样读（数值越大越好还是越差、确定不存在指什么）。
    meaning: str = Field(min_length=1, max_length=400)
    value_kind: Literal["number", "category"]
    unit: str = Field(min_length=1, max_length=40)
    #: 时间口径，例如"合成快照（基年 2024）"。
    period: str = Field(min_length=1, max_length=120)
    status: FactStatus
    number: float | None = None
    category: str | None = Field(default=None, min_length=1, max_length=80)
    unknown_reason: str | None = Field(default=None, min_length=1, max_length=280)
    #: 事实本身的来源（城市范围在 ``geography_scope``）。合成事实 ``kind = synthetic`` 并写 ``synthetic_note``。
    source: SourceRecord

    @field_validator("number")
    @classmethod
    def _finite(cls, v: float | None) -> float | None:
        if v is not None and not math.isfinite(v):
            raise ValueError("事实数值必须是有限数")
        return v

    @model_validator(mode="after")
    def _status_consistent(self) -> CityFact:
        has = (self.number is not None, self.category is not None)
        if self.status == "known":
            want = (True, False) if self.value_kind == "number" else (False, True)
            if has != want:
                raise ValueError(
                    f"{self.fact_id}: 已知的{('数值' if self.value_kind == 'number' else '类别')}事实必须只给 "
                    f"{'number' if self.value_kind == 'number' else 'category'}"
                )
            if self.unknown_reason is not None:
                raise ValueError(f"{self.fact_id}: 已知事实不写 unknown_reason")
        else:
            if any(has):
                raise ValueError(
                    f"{self.fact_id}: {FACT_STATUS_LABEL[self.status]}的事实不能带数值或类别——未知不是零"
                )
            if (self.status == "unknown") != (self.unknown_reason is not None):
                raise ValueError(
                    f"{self.fact_id}: 资料未知必须写原因；确定不存在不写 unknown_reason（含义写在 meaning）"
                )
        return self

    def definition(self) -> dict[str, str]:
        """各城必须相同的那部分：编号、名称、含义、取值类型、单位与时间口径。"""
        return {
            "fact_id": self.fact_id,
            "label": self.label,
            "meaning": self.meaning,
            "value_kind": self.value_kind,
            "unit": self.unit,
            "period": self.period,
        }


class CityFactSheet(ContractModel):
    """一座城的背景事实表（代理可见）。"""

    city: CityId
    label: str = Field(min_length=1, max_length=120)
    facts: tuple[CityFact, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> CityFactSheet:
        ids = [f.fact_id for f in self.facts]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{self.city}: 事实编号重复")
        return self

    def fact(self, fact_id: str) -> CityFact | None:
        return next((f for f in self.facts if f.fact_id == fact_id), None)

    @property
    def content_fingerprint(self) -> str:
        return canonical_fingerprint(self.model_dump(mode="json"))


class FactScoringLink(ContractModel):
    """**内部**：一个数值事实怎样变成评分条件里某个分项的背景量 b。

    ``b = factor × 数值``（``linear_base_v1``，精确十进制）。``unavailable_base``：事实"确定不存在"时对应的 b；不写就说明这条
    事实不允许"确定不存在"（加载时拒绝），不会被悄悄当成 0。资料未知的事实只能对应资料未知的评分条件。

    ``source`` 是**换算或评分假设**的来源，与事实自己的来源分开：合成换算标 ``synthetic``；能重算的换算标 ``derived``（列输入与
    规则）；统计估计标 ``estimated``。换算不能标 ``observed``。
    """

    fact_id: KeyStr
    component_id: KeyStr
    rule: Literal["linear_base_v1"]
    factor: str = Field(min_length=1, max_length=20)
    unavailable_base: str | None = Field(default=None, min_length=1, max_length=20)
    source: SourceRecord
    note: str | None = Field(default=None, max_length=400)

    @model_validator(mode="after")
    def _values(self) -> FactScoringLink:
        if _decimal_text(self.factor, f"{self.fact_id} 的换算系数") <= 0:
            raise ValueError(f"{self.fact_id}: 换算系数必须为正")
        if self.unavailable_base is not None and (
            _decimal_text(self.unavailable_base, f"{self.fact_id} 的确定不存在对应值") < 0
        ):
            raise ValueError(f"{self.fact_id}: 确定不存在对应的 b 不能为负")
        if self.source.kind == "observed":
            raise ValueError(
                f"{self.fact_id}: 换算是假设或规则，不是观测——请标 synthetic、derived 或 estimated"
            )
        return self

    def base_for(self, fact: CityFact) -> Fraction | None:
        """这条事实按换算对应的 b（精确分数）；资料未知返回 None。"""
        if fact.status == "known":
            assert fact.number is not None
            return _decimal_text(self.factor, "换算系数") * to_fraction(fact.number)
        if fact.status == "unavailable":
            if self.unavailable_base is None:
                raise ValueError(f"{self.fact_id}: 没有声明确定不存在对应的 b")
            return _decimal_text(self.unavailable_base, "确定不存在对应值")
        return None


class CityFactsConfig(ContractModel):
    """一个比较组的城市事实表与内部对应（YAML 顶层键 ``city_facts``）。"""

    facts_notice: str = Field(min_length=1)
    version: Literal["e1-7a-city-facts-v1"]
    comparison_group_id: IdStr
    sheets: tuple[CityFactSheet, ...] = Field(min_length=1)
    links: tuple[FactScoringLink, ...] = ()

    @model_validator(mode="after")
    def _unique(self) -> CityFactsConfig:
        cities = [str(s.city) for s in self.sheets]
        if len(cities) != len(set(cities)):
            raise ValueError("同一座城有不止一份事实表")
        comps = [x.component_id for x in self.links]
        if len(comps) != len(set(comps)):
            raise ValueError("同一个评分分项有不止一条事实对应")
        facts = [x.fact_id for x in self.links]
        if len(facts) != len(set(facts)):
            raise ValueError("同一条事实对应了不止一个评分分项")
        return self

    def sheet_for(self, city_key: str) -> CityFactSheet | None:
        return next((s for s in self.sheets if str(s.city) == city_key), None)

    def link_for(self, component_id: str) -> FactScoringLink | None:
        return next((x for x in self.links if x.component_id == component_id), None)

    @property
    def visible_fingerprint(self) -> str:
        """代理可见部分（全部事实表）的内容身份。"""
        return canonical_fingerprint([s.model_dump(mode="json") for s in self.sheets])

    @property
    def links_fingerprint(self) -> str:
        """内部对应（换算与它们的来源）的内容身份。"""
        return canonical_fingerprint([x.model_dump(mode="json") for x in self.links])


#: 内部对应的保留键：出现在观察或提示里就说明换算参数漏了出去。不与可见事实的字段重名。
RESERVED_FACT_LINK_KEYS: frozenset[str] = frozenset({"factor", "unavailable_base", "links"})


def internal_city_fact_models() -> tuple[type[ContractModel], ...]:
    """城市事实一侧的内部对象：事实到评分输入的对应，以及含有它的整份配置。事实表本身可以（逐字段投影后）进观察。"""
    return (FactScoringLink, CityFactsConfig)
