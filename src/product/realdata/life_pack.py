"""E3B 六城真实生活资料包的编译（DECISIONS/0042；E3B 映射修正）：intake-02 的冻结资料 → E1 可执行目录的一部分。

这里只做本模块负责的几块，都是确定性的映射，数值来自资料或 ``configs/intake/life_pack_us_v1.yaml`` 里登记的规则：

* **完整候选池与年度菜单分开**：候选池是全部可执行的逻辑机会（只有编号、权重与引用，不展开）；每城 × 每年 × 每个环境重复的
  菜单从完整池加权无放回抽 ``size`` 个（键 = 菜单设定身份、城市、年、类别、环境重复、配置种子，不含分身编号）；目录只展开
  菜单上出现过的，加上最低生活与期末参考要用的。
* **住房**：intake 的目标格（PUMA × 卧室）引用解析格的完整加权经验支持表（``housing_rent_support@1``）；逻辑候选 = （目标 PUMA，
  支持点），权重 = 目标格权重 × 支持点权重 ÷ 表权重和（总权重来自目标格，回退只借形状）。租金删失或未知、厨卫不完整或未知的
  支持点不进池，质量照实报告。
* **岗位**：职业模板（OEWS p10–p90 的 20 个年薪支持点）× 这一职业大类的（每周工时 × 就业片区）经验联合分布（同一批人员记录），
  工资与（工时 × 片区）在给定职业后条件独立（登记的假设）；开局岗位另列（中位年薪，联合分布里权重最大的一点）。
* **地区条件**：安全按各都会区的犯罪资料换算（``safety``，城内各片区同值），休闲背景是六城共同设定；环境按 EPA 年度 AQI 的“好”天比例 × 固定尺度
  （城市级证据，各片区同值）。

通勤关系、食品与服务、爱好、职业模板来自另外两份冻结资料，在资料包组装时并入。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

import yaml

from common.life_contracts import LifeOpportunity, MenuDecl
from product.realdata.housing import STRUCTURE_CLASS, STRUCTURE_ZH
from product.realdata.menus import keyed_sample
from product.realdata.occupations import job_opportunity

PACK_CONFIG = Path("configs/intake/life_pack_us_v1.yaml")
INTAKE_FROZEN_ON = _dt.date(2026, 10, 23)


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


@dataclass(frozen=True)
class Candidate:
    """完整候选池里的一项逻辑机会：编号、抽样权重（目标权重，不是入选概率）、片区，与展开成 E1 机会要用的资料（共享引用）。"""

    opportunity_id: str
    weight: float
    area: str
    data: dict[str, Any]


@dataclass
class Pool:
    """一城一个类别的完整候选池。``masses`` 按原始权重记各部分的质量（精确分数）：``raw`` 是全部，``executable`` 是进池的，其余
    是不进池的原因（可以重叠：一个支持点可以既删失又缺厨房）。``excluded`` 数整格（或整个模板）排除的原因。"""

    candidates: list[Candidate] = field(default_factory=list)
    masses: Counter[str] = field(default_factory=Counter)
    excluded: Counter[str] = field(default_factory=Counter)

    def weights(self) -> list[tuple[str, float]]:
        return [(c.opportunity_id, c.weight) for c in self.candidates]

    def mass_report(self) -> dict[str, Any]:
        raw = Fraction(self.masses.get("raw", 0))
        return {
            "candidates": len(self.candidates),
            "masses": {k: f"{float(v):.2f}" for k, v in sorted(self.masses.items())},
            "shares_of_raw": {
                k: (f"{float(Fraction(v) / raw):.4f}" if raw else None)
                for k, v in sorted(self.masses.items())
                if k != "raw"
            },
            "excluded": dict(sorted(self.excluded.items())),
        }


#: 住房支持点可以进可执行池的租金状态（housing_rent_support@1）：同一条记录的毛租金，或合同租金 + 同格水电燃气中位数（插补）
HOUSING_PRICE_EXECUTABLE = ("observed", "imputed")
TIER_ZH = {1: "低", 2: "中", 3: "高"}


def point_problems(pt: dict[str, Any]) -> list[str]:
    """一个住房支持点不能执行的原因（空 = 可执行）：租金删失或未知；厨房或卫生设施不完整或未知（不能当最低住房，也不进池）。"""
    out = []
    if pt.get("price_status") not in HOUSING_PRICE_EXECUTABLE or pt.get("gross_rent") is None:
        out.append(f"price_{pt.get('price_status') or 'unknown'}")
    for f in ("kitchen", "plumbing"):
        v = pt.get(f)
        if v != "complete":
            out.append(f"facilities_{'incomplete' if v == 'incomplete' else 'unknown'}")
    return sorted(set(out))


def housing_pool(
    city: dict[str, Any],
    cfg: dict[str, Any],
    cells: list[dict[str, Any]],
    tables: dict[str, dict[str, Any]],
    puma_counties: dict[str, tuple[str, ...]],
    home_areas: set[str],
) -> Pool:
    """intake 的目标格（PUMA × 卧室，``rental_support_cell``）× 解析格的完整加权经验支持表 → 完整可执行住房池。

    只留税务支持范围里、通勤资料有居住片区、在供应、目标权重为正的格。``puma_counties``：PUMA（7 位）→ 它在都会区里的县。"""
    key = city["key"]
    levels = cfg["housing"]["space_level_by_bedrooms"]
    pool = Pool()
    for cell in sorted(cells, key=lambda c: c["opportunity_id"]):
        puma = cell["geo"]["code"]
        if not in_tax_scope(city, puma[:2], puma_counties.get(puma, ())):
            pool.excluded["outside_tax_scope"] += 1
            continue
        area = home_area_id(key, puma)
        if area not in home_areas:
            pool.excluded["no_commute_area"] += 1
            continue
        if cell.get("supply_status") != "offered":
            pool.excluded["not_offered"] += 1
            continue
        bed = cell.get("stratum")
        if bed not in levels:
            pool.excluded["unmapped_class"] += 1
            continue
        target = Fraction(Decimal(str(cell.get("target_weight") or "0")))
        if target <= 0:
            pool.excluded["zero_target_weight"] += 1
            continue
        tid = cell["attributes"]["support_table"]
        table = tables.get(tid)
        if table is None:
            raise ValueError(f"{cell['opportunity_id']}: intake 冻结库里没有支持表 {tid}")
        total = Fraction(table["weight_sum"])
        pool.masses["raw"] += target
        for pt in table["points"]:
            share = target * pt["weight"] / total
            problems = point_problems(pt)
            for why in problems:
                pool.masses[why] += share
            if problems:
                continue
            pool.masses["executable"] += share
            pool.candidates.append(
                Candidate(
                    f"{key}.housing.{puma}.{bed}.{pt['id']}",
                    float(share),
                    area,
                    {"cell": cell, "table": table, "point": pt},
                )
            )
    return pool


def housing_opportunity(c: Candidate, city: dict[str, Any], cfg: dict[str, Any]) -> LifeOpportunity:
    """展开一处逻辑住房：月租是这个支持点的毛租金；空间等级按卧室格，条件档次是同一解析格里合同租金的加权三分位（含误差的估计）。"""
    h = cfg["housing"]
    cell, table, pt = c.data["cell"], c.data["table"], c.data["point"]
    puma, bed = cell["geo"]["code"], cell["stratum"]
    nbed = int(pt["bedrooms"])
    bed_label = "开间" if nbed == 0 else f"{nbed} 卧"
    tier = TIER_ZH[int(pt["quality_tier"])]
    place = (cell["geo"].get("label") or puma).split(" PUMA")[0][:60]
    structure = STRUCTURE_ZH.get(
        STRUCTURE_CLASS.get(str(pt.get("structure") or "").zfill(2), ""), "建筑类型未知"
    )
    if pt["price_status"] == "observed":
        price_zh = "月租是 ACS 同一条记录的合同租金加水电燃气"
        rule = "毛租金 = 同一条 ACS 记录的 GRNTP（合法、未顶码）"
    else:
        d = table["donors"]
        price_zh = f"月租 = 合同租金 + 同格 {d['n']} 户水电燃气月费中位数 {d['utilities_median_month']}（插补）"
        rule = (
            f"毛租金 = 合同租金 + 同一解析格水电燃气月费的加权中位数 {d['utilities_median_month']}"
            f"（{d['n']} 户供体，{d['assumption']}）"
        )
    level = table["level"]
    return LifeOpportunity.model_validate(
        {
            "opportunity_id": c.opportunity_id,
            "category": "housing",
            "label": f"{bed_label}整套租住（{place}，{tier}档）"[:120],
            "spec": {
                "bedrooms": nbed,
                "space_level": h["space_level_by_bedrooms"][bed],
                "quality_level": int(pt["quality_tier"]),
                "exclusive": True,
                "basic_facilities": True,
            },
            "functions": ["housing"],
            "unit": "dwelling",
            "quality": (
                f"{place}的{bed_label}整套租住，{structure}，{pt['rooms']} 个房间，厨房与卫生设施完整（ACS 记录）；"
                f"同格租金三档里的{tier}档（估计，格 = {level}）；{price_zh}"
            )[:280],
            "availability": "available",
            "price": {
                "amount": {
                    "amount": float(Decimal(pt["gross_rent"])),
                    "numeraire": cfg["numeraire"],
                },
                "billing": "per_month",
                "per_unit": "dwelling-month",
            },
            "duration": {"kind": "ongoing_service"},
            "resource": {"basis": "passive", "note": "住房本身不占时间；通勤按片区关系表算"},
            "area": c.area,
            "source": {
                "kind": "estimated",
                "observed_on": INTAKE_FROZEN_ON.isoformat(),
                "geography_scope": f"PUMA {puma}（CBSA {city['cbsa']}）",
                "mapping_version": cfg["version"],
                "inputs": [
                    "census_acs1_pums_2024",
                    f"intake:{cell['opportunity_id']}"[:200],
                    f"{table['table_id']}#{pt['id']}"[:200],
                    *(r[:200] for r in pt["records"][:3]),
                ],
                "rule": (
                    f"完整经验支持点（housing_rent_support@1，{level}）：{rule}；空间等级按卧室格，条件档次是同格合同租金的加权三分位；"
                    "抽样权重 = 目标格权重 × 支持点权重 ÷ 表权重和；厨卫按 ACS KIT/PLM 核对"
                )[:400],
            },
        }
    )


def joint_for(joint: dict[str, Any], major: str) -> tuple[dict[str, Any], str]:
    """一个职业大类用的（每周工时 × 就业片区）联合分布：大类自己的（未加权条数够门槛时有），否则全体在职者。"""
    t = joint["by_soc_major"].get(major)
    return (t, f"soc_major_{major}") if t is not None else (joint["all"], "all")


def area_code(key: str, area: str) -> str:
    return area.removeprefix(f"{key}.area.")


def job_pool(
    city: dict[str, Any],
    templates: list[dict[str, Any]],
    joint: dict[str, Any],
    work_areas: set[str],
) -> Pool:
    """职业模板 × 20 个年薪支持点 × 职业大类的（每周工时 × 就业片区）经验联合分布 → 完整可执行岗位池（因子化，只记引用）。

    权重 = OEWS 就业人数 × 年薪支持点概率（0.04）× 联合分布这一点的权重 ÷ 联合分布总权重（含工时顶码与缺失）。两端 20% 的工资
    （未识别）、WKHP 顶码与缺失、不在通勤资料就业片区的点不进池，各记质量。"""
    key = city["key"]
    pool = Pool()
    for tpl in sorted(templates, key=lambda x: x["soc"]):
        if not tpl.get("employment"):
            pool.excluded["employment_unknown"] += 1
            continue
        table, level = joint_for(joint, tpl["soc_major"])
        emp, total = Fraction(tpl["employment"]), Fraction(table["weight"])
        wages = tpl["wage"]["points"]
        identified = sum((Fraction(w["mass"]) for w in wages), Fraction(0))
        pool.masses["raw"] += emp
        pool.masses["wage_unidentified"] += emp * (1 - identified)
        pool.masses["hours_topcoded"] += emp * identified * table["weight_hours_topcoded"] / total
        pool.masses["hours_missing"] += emp * identified * table["weight_hours_missing"] / total
        inside = []
        for pt in table["points"]:
            share = emp * identified * pt["weight"] / total
            if pt["area"] in work_areas:
                pool.masses["executable"] += share
                inside.append(pt)
            else:
                pool.masses["work_area_out_of_scope"] += share
        for w in wages:
            for pt in inside:
                pool.candidates.append(
                    Candidate(
                        f"{key}.job.{tpl['soc']}.w{w['k']:02d}.h{pt['hours']}.{area_code(key, pt['area'])}",
                        float(emp * Fraction(w["mass"]) * pt["weight"] / total),
                        pt["area"],
                        {"template": tpl, "wage": w, "point": pt, "level": level},
                    )
                )
    return pool


def job_from_candidate(
    c: Candidate, rules: dict[str, Any], numeraire: str, version: str
) -> LifeOpportunity:
    tpl, w, pt = c.data["template"], c.data["wage"], c.data["point"]
    return job_opportunity(
        tpl,
        rules,
        annual=Decimal(w["annual"]),
        weekly_hours=int(pt["hours"]),
        area=pt["area"],
        opportunity_id=c.opportunity_id,
        wage_label=f"工资约在本职业 {round(float(w['quantile']) * 100)} 分位",
        numeraire=numeraire,
        mapping_version=version,
        hours_level=c.data["level"],
    )


def start_job_opportunity(
    key: str,
    tpl: dict[str, Any],
    joint: dict[str, Any],
    work_areas: set[str],
    rules: dict[str, Any],
    numeraire: str,
    version: str,
) -> LifeOpportunity | None:
    """开局岗位（``start_job_median_v2``）：OEWS 中位年薪；每周工时与就业片区取这一职业大类联合分布里权重最大的一点（同权取工时少、
    片区编号小的）。只用于统一开局，不进抽样池。没有中位年薪或联合分布没有范围内的点时没有开局岗位。"""
    if tpl.get("median_annual") is None:
        return None
    table, level = joint_for(joint, tpl["soc_major"])
    inside = sorted(
        (pt for pt in table["points"] if pt["area"] in work_areas),
        key=lambda pt: (-pt["weight"], pt["hours"], pt["area"]),
    )
    if not inside:
        return None
    pt = inside[0]
    return job_opportunity(
        tpl,
        rules,
        annual=Decimal(str(tpl["median_annual"])),
        weekly_hours=int(pt["hours"]),
        area=pt["area"],
        opportunity_id=f"{key}.job.{tpl['soc']}.start",
        wage_label="p50",
        numeraire=numeraire,
        mapping_version=version,
        hours_level=f"{level}.mode",
    )


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


def sampled_menus(
    *,
    key: str,
    cbsa: str,
    category: str,
    pool: list[tuple[str, float]],
    size: int,
    years: int,
    env_repeats: int,
    seed: str,
    settings_identity: str,
) -> list[MenuDecl]:
    """每年 × 每个环境重复一份菜单：从完整池按目标权重加权无放回抽 ``size`` 个（池不够就全部）。权重 ≤ 0 的不进抽样。"""
    out: list[MenuDecl] = []
    for year in range(1, years + 1):
        for env in range(1, env_repeats + 1):
            s = menu_seed(settings_identity, cbsa, year, category, env, seed)
            chosen = keyed_sample(pool, size, s)
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
                        f"weighted_keyed_sample@1：从完整可执行池（{len(pool)} 个）按目标权重加权无放回抽 {size} 个；"
                        f"种子 {s[:16]}…（菜单设定、城市、年、类别、环境重复，不含资料身份与分身编号）；菜单是接触，不是成交或录用"
                    )[:400],
                )
            )
    return out


def _wq(values: list[tuple[float, float]], q: float) -> float | None:
    """加权分位（值, 权重）：累计权重第一次达到 q 的值。"""
    if not values:
        return None
    values = sorted(values)
    total = math.fsum(w for _, w in values)
    acc = 0.0
    for v, w in values:
        acc += w
        if acc >= q * total:
            return v
    return values[-1][0]


def menu_size_diagnostics(
    *,
    cbsa: str,
    category: str,
    pool: Pool,
    describe: Any,
    sizes: tuple[int, ...],
    repeats: int,
    seed: str,
    settings_identity: str,
) -> dict[str, Any]:
    """菜单大小诊断（只报告，不替代候选分布验收）：用 ``repeats`` 份诊断种子（与实际菜单同一算法、不同的诊断编号）各取前
    max(sizes) 个，再看每个大小下菜单与完整池的差别。``describe(candidate) → (组, 片区, 数值)``：住房是（卧室格, 居住片区, 月租），
    岗位是（职业, 就业片区, 年薪）。菜单项按出现计（菜单是接触，不加权），完整池按目标权重。"""
    by_id = {c.opportunity_id: c for c in pool.candidates}
    full = [(describe(c), c.weight) for c in pool.candidates]
    total_w = math.fsum(w for _, w in full)
    pool_groups = {d[0] for d, _ in full}
    pool_areas = {d[1] for d, _ in full}
    weights = pool.weights()
    orders = [
        keyed_sample(
            weights,
            max(sizes),
            menu_seed(settings_identity, cbsa, 1000 + r, f"{category}.size_diagnostic", 0, seed),
        )
        for r in range(repeats)
    ]
    out: dict[str, Any] = {
        "pool": {
            "candidates": len(full),
            "groups": len(pool_groups),
            "areas": len(pool_areas),
            "value_weighted_p10_p50_p90": [
                _wq([(d[2], w) for d, w in full], q) for q in (0.1, 0.5, 0.9)
            ],
        },
        "repeats": repeats,
        "sizes": {},
    }
    for k in sizes:
        rows = []
        for order in orders:
            got = [by_id[oid] for oid in order[:k]]
            ds = [describe(c) for c in got]
            vals = sorted(d[2] for d in ds)
            rows.append(
                {
                    "groups": len({d[0] for d in ds}),
                    "areas": len({d[1] for d in ds}),
                    "weight_share": math.fsum(c.weight for c in got) / total_w if total_w else 0.0,
                    "p10_p50_p90": [_wq([(v, 1.0) for v in vals], q) for q in (0.1, 0.5, 0.9)],
                    "min": vals[0] if vals else None,
                }
            )
        n = len(rows) or 1
        out["sizes"][str(k)] = {
            "mean_groups": round(sum(r["groups"] for r in rows) / n, 2),
            "mean_areas": round(sum(r["areas"] for r in rows) / n, 2),
            "mean_pool_weight_share": round(sum(r["weight_share"] for r in rows) / n, 6),
            "mean_value_p10_p50_p90": [
                round(sum(r["p10_p50_p90"][i] for r in rows) / n, 2) for i in range(3)
            ],
            "mean_min_value": round(sum(r["min"] for r in rows) / n, 2),
        }
    return out


def environment_base(cfg: dict[str, Any], share_good_days: float | None) -> float | None:
    """环境的城市背景（固定参考尺度）：scale × AQI 为“好”的天数比例；没有有效年份就是未知（不当 0）。"""
    if share_good_days is None:
        return None
    scale = Decimal(str(cfg["conditions"]["environment"]["scale"]))
    return float((scale * Decimal(str(share_good_days))).quantize(Decimal("0.0001")))
