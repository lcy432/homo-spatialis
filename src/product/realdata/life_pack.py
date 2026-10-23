"""E3B 六城真实生活资料包的编译（DECISIONS/0042）：intake-02 的冻结资料 → E1 可执行目录的一部分。

这里只做本模块负责的几块，都是确定性的映射，数值来自资料或 ``configs/intake/life_pack_us_v1.yaml`` 里登记的规则：

* **住房**：intake 的 B 映射候选（每个 PUMA × 卧室 × 三个价位），只留税务支持范围里的 PUMA；月租 = 合同租金分位 + 水电燃气
  月费中位数（两个估计相加，照实标为估计）；居住条件的空间等级按卧室数、条件档次按同格三档的位置；片区是候选所在的 PUMA。
* **年度菜单**：每城 × 每年 × 每个环境重复一份，加权无放回（键 = 资料身份、城市、年、类别、环境重复、配置种子，不含分身编号），
  住房按卧室分层。
* **地区条件**：安全按各都会区的犯罪资料换算（``safety``，城内各片区同值），休闲背景是六城共同设定；环境按 EPA 年度 AQI 的“好”天比例 × 固定尺度
  （城市级证据，各片区同值）。

通勤关系、食品与服务、爱好、职业与岗位来自另外两份冻结资料，在资料包组装时并入。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import yaml

from common.life_contracts import LifeOpportunity, MenuDecl
from product.realdata.menus import keyed_sample

PACK_CONFIG = Path("configs/intake/life_pack_us_v1.yaml")
INTAKE_FROZEN_ON = _dt.date(2026, 10, 22)


def load_pack_config(path: Path = PACK_CONFIG) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def home_area_id(key: str, puma7: str) -> str:
    return f"{key}.area.h.{puma7}"


def in_tax_scope(city: dict[str, Any], state: str, counties: tuple[str, ...]) -> bool:
    """一个 PUMA 在不在这城的税务支持范围里：限定了县就要求它在都会区里的县全部在名单上；否则看州。"""
    if city.get("home_counties"):
        return bool(counties) and all(c in city["home_counties"] for c in counties)
    return state in city.get("home_states", ())


def _cents(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _fact(cand: dict[str, Any], name: str) -> dict[str, Any] | None:
    return next((f for f in cand.get("facts", ()) if f["name"] == name), None)


@dataclass
class HousingBuild:
    items: list[LifeOpportunity] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=dict)
    strata: dict[str, str] = field(default_factory=dict)
    excluded: Counter[str] = field(default_factory=Counter)


def housing_items(
    city: dict[str, Any],
    cfg: dict[str, Any],
    candidates: list[dict[str, Any]],
    puma_counties: dict[str, tuple[str, ...]],
) -> HousingBuild:
    """intake B 候选 → 住房机会。``puma_counties``：PUMA（7 位）→ 它在都会区里的县（5 位 FIPS）。"""
    h = cfg["housing"]
    key, numeraire = city["key"], cfg["numeraire"]
    out = HousingBuild()
    for cand in sorted(candidates, key=lambda c: c["opportunity_id"]):
        puma = cand["geo"]["code"]
        if not in_tax_scope(city, puma[:2], puma_counties.get(puma, ())):
            out.excluded["outside_tax_scope"] += 1
            continue
        if cand.get("supply_status") != "offered":
            out.excluded["not_offered"] += 1
            continue
        rent = _fact(cand, "monthly_rent")
        util = cand.get("attributes", {}).get("utilities_median_month")
        bed = cand.get("stratum")
        pos = cand.get("attributes", {}).get("price_position")
        if rent is None or rent.get("status") != "present" or rent.get("value") is None:
            out.excluded["rent_unknown"] += 1
            continue
        if util in (None, ""):
            out.excluded["utilities_unknown"] += 1  # 不知道租金含不含水电：不当 0
            continue
        if bed not in h["space_level_by_bedrooms"] or pos not in h["quality_level_by_position"]:
            out.excluded["unmapped_class"] += 1
            continue
        contract, utilities = Decimal(rent["value"]), Decimal(str(util))
        gross = _cents(contract + utilities)
        quality = h["quality_level_by_position"][pos]
        tier = {1: "低", 2: "中", 3: "高"}[quality]
        nbed = h["bedrooms_by_class"][bed]
        bed_label = "开间" if nbed == 0 else f"{nbed}{'+' if bed == '3plus' else ''} 卧"
        place = (cand["geo"].get("label") or puma).split(" PUMA")[0][:60]
        oid = f"{key}.housing.{puma}.{bed}.{pos.replace('/', '-')}"
        level = cand.get("attributes", {}).get("price_level_used", "?")
        out.items.append(
            LifeOpportunity.model_validate(
                {
                    "opportunity_id": oid,
                    "category": "housing",
                    "label": f"{bed_label}整套租住（{place}，{tier}档）"[:120],
                    "spec": {
                        "bedrooms": nbed,
                        "space_level": h["space_level_by_bedrooms"][bed],
                        "quality_level": quality,
                        "exclusive": True,
                        "basic_facilities": True,
                    },
                    "functions": ["housing"],
                    "unit": "dwelling",
                    "quality": (
                        f"{place}的{bed_label}整套租住；同格租金三档里的{tier}档（估计，"
                        f"格 = {level}）；月租含水电燃气月费中位数 {utilities}（估计）"
                    )[:280],
                    "availability": "available",
                    "price": {
                        "amount": {"amount": float(gross), "numeraire": numeraire},
                        "billing": "per_month",
                        "per_unit": "dwelling-month",
                    },
                    "duration": {"kind": "ongoing_service"},
                    "resource": {
                        "basis": "passive",
                        "note": "住房本身不占时间；通勤按片区关系表算",
                    },
                    "area": home_area_id(key, puma),
                    "source": {
                        "kind": "estimated",
                        "observed_on": INTAKE_FROZEN_ON.isoformat(),
                        "geography_scope": f"PUMA {puma}（CBSA {city['cbsa']}）",
                        "mapping_version": cfg["version"],
                        "inputs": [
                            "census_acs1_pums_2024",
                            f"intake:{cand['opportunity_id']}"[:200],
                        ],
                        "rule": (
                            f"合同租金分位（housing_rent_conditional@2，{level}）+ 水电燃气月费中位数（{h['gross_rent_rule']}）；"
                            f"空间等级按卧室数，条件档次按同格三档位置；厨卫设施按假设 {h['basic_facilities_assumption']}"
                        )[:400],
                    },
                }
            )
        )
        out.weights[oid] = float(cand.get("target_weight") or 0)
        out.strata[oid] = bed
    return out


def menu_seed(
    settings_identity: str, cbsa: str, year: int, category: str, env: int, seed: str
) -> str:
    """菜单种子：菜单设定的身份、城市、年、类别、环境重复、配置种子。不含资料身份与分身编号。"""
    text = json.dumps(
        {
            "build": settings_identity,
            "cbsa": cbsa,
            "year": year,
            "category": category,
            "env_repeat": env,
            "seed": seed,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stratified_menus(
    *,
    key: str,
    cbsa: str,
    category: str,
    weights: dict[str, float],
    strata: dict[str, str] | None,
    per_stratum: int | None,
    size: int | None,
    years: int,
    env_repeats: int,
    seed: str,
    settings_identity: str,
) -> list[MenuDecl]:
    """每年 × 每个环境重复一份菜单：分层时每层按权重无放回抽 ``per_stratum`` 个，不分层时一共抽 ``size`` 个。权重 ≤ 0 的不进抽样。"""
    out: list[MenuDecl] = []
    for year in range(1, years + 1):
        for env in range(1, env_repeats + 1):
            s = menu_seed(settings_identity, cbsa, year, category, env, seed)
            if strata is not None:
                assert per_stratum is not None
                chosen: list[str] = []
                for layer in sorted(set(strata.values())):
                    pool = [(oid, w) for oid, w in sorted(weights.items()) if strata[oid] == layer]
                    chosen += keyed_sample(pool, per_stratum, f"{s}|{layer}")
            else:
                assert size is not None
                chosen = keyed_sample(sorted(weights.items()), size, s)
            if not chosen:
                continue
            out.append(
                MenuDecl(
                    menu_id=f"menu.{key}.{category}.y{year}.r{env}",
                    category=category,
                    year=year,
                    env_repeat=env,
                    opportunity_ids=tuple(chosen),
                    basis=(
                        f"weighted_keyed_sample@1：{'按卧室分层，每层 ' + str(per_stratum) if strata else '一共 ' + str(size)} 个，"
                        f"按目标权重加权无放回；种子 {s[:16]}…（菜单设定、城市、年、类别、环境重复，不含资料身份与分身编号）；"
                        "菜单是接触，不是成交或录用"
                    )[:400],
                )
            )
    return out


def environment_base(cfg: dict[str, Any], share_good_days: float | None) -> float | None:
    """环境的城市背景（固定参考尺度）：scale × AQI 为“好”的天数比例；没有有效年份就是未知（不当 0）。"""
    if share_good_days is None:
        return None
    scale = Decimal(str(cfg["conditions"]["environment"]["scale"]))
    return float((scale * Decimal(str(share_good_days))).quantize(Decimal("0.0001")))
