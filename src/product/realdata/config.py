"""资料构建配置：验证都会区、来源清单、方法参数、菜单政策与种子（``configs/intake/us_v1.yaml``）。

配置是构建身份的一部分：改了任何一项，构建身份都会变。加载时只做形状检查；来源与都会区之间的跨对象检查在
``ledger.intake_data``。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, model_validator

from common.intake_contracts import IntakeModel, Sector, SourceSpec, content_identity

DEFAULT_CONFIG = Path("configs/intake/us_v1.yaml")


class PrincipalCity(IntakeModel):
    name: str
    wikidata: str = Field(pattern=r"^Q[0-9]+$")


class Metro(IntakeModel):
    """一个验证都会区。``census_region`` 是 BLS 价格资料用的人口普查大区代码；``ap_metro_area`` 是 CPI 的都会区代码
    （它的划定沿用较早的 CBSA 版本，与 2023 年划定不完全相同，记为重叠关系）。"""

    cbsa: str = Field(pattern=r"^[0-9]{5}$")
    key: str = Field(pattern=r"^[a-z]{2,8}$")
    label: str
    label_zh: str
    principal_city: PrincipalCity
    census_region: str
    ap_metro_area: str
    ce_region_file: str


class BuildSettings(IntakeModel):
    name: str
    country: str = Field(min_length=2, max_length=2)
    geo_scheme: str
    money_target: str
    base_year: int
    seed: str = Field(min_length=8)


class PumsSettings(IntakeModel):
    year: int
    housing_vars: tuple[str, ...]
    person_vars: tuple[str, ...]


class MenuSettings(IntakeModel):
    algorithm: str
    algorithm_version: str
    env_repeats: int = Field(ge=1, le=20)
    years: tuple[int, ...]
    policies: dict[Sector, dict[str, Any]]
    size_diagnostic: dict[str, Any] = Field(default_factory=dict)


class BuildConfig(IntakeModel):
    build: BuildSettings
    metros: tuple[Metro, ...] = Field(min_length=1)
    sources: tuple[SourceSpec, ...] = Field(min_length=1)
    pums: PumsSettings
    mapping: dict[str, dict[str, Any]]
    menus: MenuSettings

    @model_validator(mode="after")
    def _unique(self) -> BuildConfig:
        for name, ids in (
            ("都会区", [m.cbsa for m in self.metros]),
            ("都会区简称", [m.key for m in self.metros]),
            ("来源", [s.source_id for s in self.sources]),
        ):
            if len(ids) != len(set(ids)):
                raise ValueError(f"{name}重复：{ids}")
        return self

    def metro(self, cbsa: str) -> Metro:
        return next(m for m in self.metros if m.cbsa == cbsa)

    def source(self, source_id: str) -> SourceSpec:
        return next(s for s in self.sources if s.source_id == source_id)

    @property
    def identity(self) -> str:
        return content_identity(self.model_dump(mode="json"))


def load_config(path: Path | str = DEFAULT_CONFIG) -> BuildConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return BuildConfig.model_validate(raw)
