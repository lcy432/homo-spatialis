"""E3B 六城真实生活资料包的组装（DECISIONS/0042）：住房（本模块的 ``life_pack``）、通勤关系与片区（``commute``）、生活机会与
职业（``life_items`` / ``occupations``）、官方税（``tax_rules``）合成 E1 能读的生活输入、评分配置、评价配置与城市事实。

组装只做确定性的拼接与登记过的规则，不补数：

* **完整候选池与年度菜单分开**（E3B 映射修正，``life_pack``）：住房池 = 目标格 × 完整经验支持点；岗位池 = 职业模板 × 年薪支持点
  × 职业大类的（每周工时 × 就业片区）联合分布。每份菜单从完整池加权无放回抽 60 个；目录只展开菜单上出现过的，加上最低住房、
  期末参考住房与各职业的开局岗位。
* **住房**只留通勤资料里有居住片区的 PUMA；没有片区的照实计数排除。
* **最低生活**：最低住房 = 完整池里最便宜的住房（池里都是租金可用、厨卫完整的整套租住单元）；最低食品 = 生活机会资料给出的
  最低食品方案。
* **治疗目录**没有真实医疗价格：用登记的共同假设（六城同一个名义价，``treatment_common_assumption_v1``），不制造城市差异。
* 芝加哥公园管理局的真实活动需要 E1 扩展（日期窗口、整期报名、年龄资格），不放进目录，照实计数。
"""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from product.realdata import geo
from product.realdata.fetch import DEFAULT_CACHE, fetch
from product.realdata.life_pack import (
    PACK_CONFIG,
    Candidate,
    housing_opportunity,
    housing_pool,
    job_from_candidate,
    job_pool,
    load_pack_config,
    menu_size_diagnostics,
    sampled_menus,
    start_job_opportunity,
)
from product.realdata.pack_inputs import (
    build_identity,
    file_digests,
    intake_fetch_log,
    menu_settings_identity,
    pinned_raw_files,
    rows_digest,
    verify_commute,
    verify_intake_build,
    verify_items,
)
from product.realdata.reference_housing import (
    REFERENCE_HOUSING_RULE,
    ReferenceHousing,
    choose_reference_housing,
)
from product.realdata.safety import (
    CrimeCount,
    SafetyInput,
    relative_index,
    safety_inputs,
    source_digests,
)
from product.realdata.tax_rules import TAX_CONFIG, compile_tax

FROZEN = Path("data/frozen/life/us-v1")
BASE_LIFE_INPUT = Path("configs/life/synthetic_product.yaml")
BASE_SCORING = Path("configs/life/scoring_product.yaml")
BASE_EVALUATION = Path("configs/life/evaluation_product.yaml")
BASE_FACTS = Path("configs/life/city_facts_product.yaml")
INTAKE_DB = Path("runs/intake/us-v1/work/freeze/35667f4101a71c0e/intake.sqlite")
TREATMENT_PRICES = {
    "minor": 300,
    "major": 2000,
}  # treatment_common_assumption_v1（与合成样例同一名义价）
#: 比较组的地理口径。加载器要求每条来源的 geography_scope 与它逐字相同（各城同一粒度）；具体的 PUMA、都会区或全国口径写进
#: 来源的 inputs（派生与估计）或说明（合成设定）。
GROUP_SCOPE = "CBSA 2023 MSA（按税务支持范围裁剪的 2020 PUMA）"
PROTOCOL_ID = "life-v1-e3b-us"


def _scoped(src: dict[str, Any]) -> dict[str, Any]:
    """一条来源换成比较组的地理口径；原来写的具体口径保留在 inputs 或合成说明里（观测来源的出处在 provenance 里）。"""
    orig = src.get("geography_scope")
    out = dict(src)
    out["geography_scope"] = GROUP_SCOPE
    if orig and orig != GROUP_SCOPE:
        note = f"geography: {orig}"[:200]
        if out.get("kind") in ("derived", "estimated"):
            out["inputs"] = [*out.get("inputs", []), note]
        elif out.get("kind") == "synthetic":
            out["synthetic_note"] = f"{out.get('synthetic_note', '')}（{note}）"
    return out


def _rescope(obj: Any) -> Any:
    """递归：所有名为 source / typical_source 的来源换成比较组口径。"""
    if isinstance(obj, dict):
        return {
            k: (
                _scoped(v)
                if k in ("source", "typical_source") and isinstance(v, dict)
                else _rescope(v)
            )
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_rescope(v) for v in obj]
    return obj


def canonical_identity(obj: Any) -> str:
    text = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def city_ref(cbsa: str) -> dict[str, str]:
    return {"country": "US", "scheme": "CBSA2023", "code": cbsa}


@dataclass
class CityBuild:
    catalog: dict[str, Any]
    counts: dict[str, Any] = field(default_factory=dict)
    excluded: Counter[str] = field(default_factory=Counter)
    #: 期末参考生活的住房（独立审核 F3、F4）：按参考岗位与通勤关系从完整候选池选，与菜单无关
    reference: ReferenceHousing | None = None
    #: 完整池的质量（pools）与 30 / 60 / 120 菜单大小诊断（只报告）
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass
class PackBuild:
    life_input: dict[str, Any]
    cities: dict[str, CityBuild]
    tax_blocked: dict[str, list[str]]
    identity: str = ""
    #: 构建身份的组成（每份输入核对过的身份），写进报告
    inputs: dict[str, Any] = field(default_factory=dict)
    #: 每城核对过的生活机会文件与 intake 里的城市事实（城市背景要用，不再另读一遍）
    items: dict[str, dict[str, Any]] = field(default_factory=dict)
    backgrounds: dict[str, dict[str, Any]] = field(default_factory=dict)
    manifest: dict[str, Any] = field(default_factory=dict)


def base_files() -> tuple[Path, ...]:
    """会影响产物的基础配置与模板（内容摘要进构建身份）。产品配置的模板在 ``product_pack`` 里（它反过来用本模块，这里延迟导入）。"""
    from product.realdata.product_pack import OCCUPATIONS, SYNTH_PACK

    return (
        BASE_LIFE_INPUT,
        BASE_SCORING,
        BASE_EVALUATION,
        BASE_FACTS,
        PACK_CONFIG,
        TAX_CONFIG,
        SYNTH_PACK,
        OCCUPATIONS,
    )


def _treatments(key: str, numeraire: str) -> list[dict[str, Any]]:
    source = {
        "kind": "synthetic",
        "observed_on": "2026-10-23",
        "geography_scope": "六城共同假设（没有真实医疗价格资料）",
        "mapping_version": "treatment_common_assumption_v1",
        "synthetic_note": "没有真实医疗价格资料：六城用同一个登记的名义价与就医时间，不制造城市差异；不是任何医疗机构的报价",
    }
    out = []
    for sev, need, label, hours in (
        ("minor", "minor_illness_care", "门诊与药物（共同假设）", 2),
        ("major", "major_illness_care", "住院治疗（共同假设）", 8),
    ):
        out.append(
            {
                "treatment_id": f"{key}.care.{sev}",
                "severity": sev,
                "need_id": need,
                "label": label,
                "serves": "小病：看一次门诊、拿药；接受后当月月末恢复"
                if sev == "minor"
                else "大病：住院治疗；接受后当月月末恢复",
                "availability": "available",
                "price": {
                    "amount": {"amount": TREATMENT_PRICES[sev], "numeraire": numeraire},
                    "billing": "one_time",
                    "per_unit": "illness-episode",
                },
                "visit_hours": hours,
                "burden_per_hour": 1.0,
                "source": source,
            }
        )
    return out


def _reference_view(c: Candidate) -> dict[str, Any]:
    """参考住房选择只看片区、月租与居住条件：完整池的逻辑候选不必展开成 E1 机会。"""
    pt = c.data["point"]
    return {
        "opportunity_id": c.opportunity_id,
        "area": c.area,
        "price": {"amount": {"amount": float(Decimal(pt["gross_rent"]))}},
        "spec": {"bedrooms": int(pt["bedrooms"]), "quality_level": int(pt["quality_tier"])},
    }


def assemble_city(
    city: dict[str, Any],
    cfg: dict[str, Any],
    *,
    cells: list[dict[str, Any]],
    tables: dict[str, dict[str, Any]],
    puma_counties: dict[str, tuple[str, ...]],
    commute: dict[str, Any],
    items: dict[str, Any],
    job_rules: dict[str, Any],
    settings_identity: str,
) -> CityBuild:
    key, cbsa, numeraire = city["key"], city["cbsa"], cfg["numeraire"]
    out_excluded: Counter[str] = Counter()
    home_areas = {a["area_id"] for a in commute["areas"] if a["kind"] in ("home", "home_work")}
    work_areas = {a["area_id"] for a in commute["areas"] if a["kind"] in ("work", "home_work")}
    hp = housing_pool(city, cfg, cells, tables, puma_counties, home_areas)
    out_excluded.update({f"housing.{k}": v for k, v in hp.excluded.items()})
    if not hp.candidates:
        raise ValueError(f"{key}: 完整住房池是空的")
    joint = commute["hours_area_joint"]
    templates = items["job_templates"]
    jp = job_pool(city, templates, joint, work_areas)
    out_excluded.update({f"job.{k}": v for k, v in jp.excluded.items()})

    opps: list[dict[str, Any]] = []
    by_domain: Counter[str] = Counter()
    for it in items["items"]:
        status = it["status"]
        if status == "needs_e1_extension":
            out_excluded[f"{it['domain']}.needs_e1_extension"] += 1
            continue
        if status not in ("executable", "data_unknown"):
            out_excluded[f"{it['domain']}.{status}"] += 1
            continue
        by_domain[it["domain"]] += 1
        opps.append(copy.deepcopy(it["opportunity"]))
    starts = []
    for tpl in sorted(templates, key=lambda x: x["soc"]):
        o = start_job_opportunity(
            key, tpl, joint, work_areas, job_rules, numeraire, job_rules["mapping_version"]
        )
        if o is None:
            out_excluded["job.no_start_job"] += 1
            continue
        starts.append(o.model_dump(mode="json"))
    opps.append(copy.deepcopy(commute["car_access"]))
    if not any(o["category"] == "training" for o in opps):
        # 加载器要求每种行动类型都有记录（可得、确定不可得或未知）：这城的声明可执行集合里没有培训项目
        opps.append(
            {
                "opportunity_id": f"{key}.training.none_in_scope",
                "category": "training",
                "label": "培训项目（支持范围内没有）",
                "unit": "program",
                "quality": "IPEDS 里本都会区没有能授予支持职业资格的整年项目：这份资料包里没有可执行的培训",
                "availability": "unavailable",
                "duration": {"kind": "training_course", "completion_years": 1},
                "resource": {
                    "basis": "per_service_month",
                    "hours": 0,
                    "burden_per_hour": 0,
                    "quantity_scales": False,
                    "note": "没有可执行的项目",
                },
                "source": {
                    "kind": "derived",
                    "observed_on": "2026-10-23",
                    "geography_scope": f"CBSA {cbsa}",
                    "mapping_version": cfg["version"],
                    "inputs": ["ipeds_2023", "occupations_us_v1"],
                    "rule": "支持职业 × 本都会区整年项目的交集为空",
                },
            }
        )

    years, envs, mc = cfg["simulation_years"], cfg["env_repeats"], cfg["menus"]
    common = {
        "key": key,
        "cbsa": cbsa,
        "years": years,
        "env_repeats": envs,
        "seed": mc["seed"],
        "settings_identity": settings_identity,
    }
    menus = sampled_menus(
        category="housing", pool=hp.weights(), size=mc["housing"]["size"], **common
    ) + sampled_menus(category="job", pool=jp.weights(), size=mc["job"]["size"], **common)
    diag = cfg["menu_size_diagnostic"]
    sizes = tuple(diag["sizes"])

    def describe_housing(c: Candidate) -> tuple[str, str, float]:
        return c.data["cell"]["stratum"], c.area, float(Decimal(c.data["point"]["gross_rent"]))

    def describe_job(c: Candidate) -> tuple[str, str, float]:
        return c.data["template"]["soc"], c.area, float(Decimal(c.data["wage"]["annual"]))

    diagnostics = {
        cat: menu_size_diagnostics(
            cbsa=cbsa,
            category=cat,
            pool=pool,
            describe=describe,
            sizes=sizes,
            repeats=diag["repeats"],
            seed=mc["seed"],
            settings_identity=settings_identity,
        )
        for cat, pool, describe in (
            ("housing", hp, describe_housing),
            ("job", jp, describe_job),
        )
    }

    by_id = {c.opportunity_id: c for c in hp.candidates}
    cheapest = min(
        hp.candidates,
        key=lambda c: (Decimal(c.data["point"]["gross_rent"]), c.opportunity_id),
    )
    # 期末参考住房（独立审核 F3、F4）：从完整候选池按参考岗位与通勤关系选——参考岗位片区取完整岗位池与开局岗位的全部就业片区，
    # 都与菜单无关，所以只换菜单种子不改变参考集合
    job_areas = sorted({c.area for c in jp.candidates} | {o["area"] for o in starts})
    reference = choose_reference_housing(
        [_reference_view(c) for c in hp.candidates],
        [{"area": a, "availability": "available"} for a in job_areas],
        commute["commute"],
    )
    # 目录只展开这次运行能接触到的住房与岗位：任一年、任一环境重复的菜单上出现过的，加上最低住房（兜底要用）、参考住房（估值要用）
    # 与开局岗位。参考住房与开局岗位不加进任何菜单：分身在模拟年里看不到、也选不了参考住房；开局岗位只是统一开局时手上的岗位
    reachable = {oid for m in menus if m.category == "housing" for oid in m.opportunity_ids}
    reachable.add(cheapest.opportunity_id)
    reachable.update(reference.bound())
    housing = [housing_opportunity(by_id[oid], city, cfg) for oid in sorted(reachable)]
    jobs_by_id = {c.opportunity_id: c for c in jp.candidates}
    menu_jobs = sorted({oid for m in menus if m.category == "job" for oid in m.opportunity_ids})
    jobs = [
        job_from_candidate(jobs_by_id[oid], job_rules, numeraire, job_rules["mapping_version"])
        for oid in menu_jobs
    ]
    opps = (
        [h.model_dump(mode="json") for h in housing]
        + [j.model_dump(mode="json") for j in jobs]
        + starts
        + opps
    )
    foods = [
        o
        for o in opps
        if o["category"] == "food" and (o.get("food_plan") or {}).get("covers_basic_month")
    ]
    min_food = items["minimum_food_candidate"]["opportunity_id"]
    catalog_raw = {
        "city": city_ref(cbsa),
        "jurisdiction": {"path": list(city["jurisdiction"])},
        "label": city["label"],
        "money_basis": {"currency": "USD", "base_year": cfg["base_year"]},
        "tax_policy_id": city["tax_policy"],
        "minimum_living": [
            {
                "need": "housing",
                "baseline_opportunity_id": cheapest.opportunity_id,
                "baseline_quantity": 1,
                "qualifying_opportunity_ids": sorted(h.opportunity_id for h in housing),
                "meets": "一个人独占的整套租住单元（ACS 租住单元，厨房与卫生设施完整）",
            },
            {
                "need": "food",
                "baseline_opportunity_id": min_food,
                "baseline_quantity": 1,
                "qualifying_opportunity_ids": sorted(o["opportunity_id"] for o in foods),
                "meets": "一个成年人一个月的完整基本饮食（补齐到参照热量的方案；不是营养方案）",
            },
        ],
        "areas": commute["areas"],
        "commute": commute["commute"],
        "menus": [m.model_dump(mode="json") for m in menus],
        "treatments": _treatments(key, numeraire),
        "opportunities": opps,
    }
    catalog = _rescope(catalog_raw)
    # 计数（公开的构建计数只读这里：整数或一层整数字典）；池的质量与菜单大小诊断另放
    counts = {
        "housing_candidates": len(hp.candidates),
        "housing_in_catalog": len(housing),
        "job_candidates": len(jp.candidates),
        "jobs_in_catalog": len(jobs),
        "start_jobs": len(starts),
        "items_by_domain": dict(sorted(by_domain.items())),
        "menus": len(menus),
        "areas": len(commute["areas"]),
        "commute_legs": len(commute["commute"]["legs"]),
    }
    pools = {
        "housing": hp.mass_report(),
        "job": jp.mass_report(),
        "menu_sizes": {"housing": mc["housing"]["size"], "job": mc["job"]["size"]},
    }
    return CityBuild(
        catalog=catalog,
        counts=counts,
        excluded=out_excluded,
        reference=reference,
        diagnostics={"pools": pools, "menu_size_diagnostics": diagnostics},
    )


def base_protocol(manifest: dict[str, Any]) -> dict[str, Any]:
    """协议沿用产品基础样例的生活规则（不改数学），换上真实资料包的身份、能力词表与设备故障来源。"""
    life = _yaml(BASE_LIFE_INPUT)
    p = copy.deepcopy(life["protocol"])
    p["protocol_id"] = PROTOCOL_ID
    rules = p["career_rules"]
    rules["abilities"] = manifest["abilities"]
    rules["employers"] = []
    rules["note"] = (
        "E3B 真实资料包：能力只来自结构化事实（学历、职业资格、相关经验），岗位没有可识别的雇主"
    )
    p["device_failures"] = {
        "mode": "random",
        "pairing_id": "e3b-us-v1",
        "seed": 20261023,
        "note": "E3B 六城的设备故障随机数（与疾病同一套做法；只有登记了年故障概率的物品会坏）",
    }
    return p


def assemble(
    cfg: dict[str, Any] | None = None,
    *,
    intake_db: Path = INTAKE_DB,
    cache: Path = DEFAULT_CACHE,
    frozen: Path = FROZEN,
) -> PackBuild:
    cfg = cfg or load_pack_config()
    # 独立审核 F2：每份输入按实际内容核对它声明的身份，构建身份只用核对过的身份（不信文件里写着的 content_identity）
    items = verify_items(frozen / "items", [c["cbsa"] for c in cfg["cities"]])
    manifest = items.manifest
    commute = verify_commute(frozen / "commute", [c["key"] for c in cfg["cities"]])
    tax = compile_tax(cache=cache)
    con = sqlite3.connect(f"file:{Path(intake_db).as_posix()}?mode=ro", uri=True)
    try:
        intake_build = verify_intake_build(
            con,
            {manifest["intake_build_id"]}
            | {payload["inputs"]["intake_build_id"] for payload, _ in commute.values()},
        )
        raw, raw_digests = pinned_raw_files(
            {"cbsa_delineation": geo.delineation_request(), "tract_puma": geo.tract_puma_request()},
            cache,
            intake_fetch_log(con),
        )
        cells = {
            c["cbsa"]: [
                json.loads(j)
                for (j,) in con.execute(
                    "select json from opportunities where kind='rental_support_cell' and cbsa=? order by opportunity_id",
                    (c["cbsa"],),
                )
            ]
            for c in cfg["cities"]
        }
        tables = {
            c["cbsa"]: {
                tid: json.loads(j)
                for tid, j in con.execute(
                    "select table_id, json from tables where method='housing_rent_support@1' and cbsa=? order by table_id",
                    (c["cbsa"],),
                )
            }
            for c in cfg["cities"]
        }
        backgrounds = {c["cbsa"]: city_background(con, c["cbsa"]) for c in cfg["cities"]}
    finally:
        con.close()
    all_geo = geo.parse_delineation(raw["cbsa_delineation"])
    tract_puma = geo.parse_tract_puma(raw["tract_puma"])
    menu_identity = menu_settings_identity(cfg)
    identity_inputs = {
        "config": canonical_identity(cfg),
        "items": items.identity,
        "commute": {key: ident for key, (_, ident) in commute.items()},
        "intake_build": intake_build,
        "intake_reads": {
            cbsa: {
                "rental_support_cells": rows_digest(cells[cbsa]),
                "housing_support_tables": rows_digest(list(tables[cbsa].values())),
                "city_facts": rows_digest(backgrounds[cbsa]),
            }
            for cbsa in cells
        },
        "raw_geography": raw_digests,
        "tax": {
            pid: canonical_identity(p.model_dump(mode="json"))
            for pid, p in sorted(tax.policies.items())
        },
        "safety_sources": source_digests(cache),
        "base_files": file_digests(base_files()),
    }
    cities: dict[str, CityBuild] = {}
    for city in cfg["cities"]:
        g = geo.attach_pumas(all_geo[city["cbsa"]], tract_puma)
        counties = {p.code: tuple(p.counties) for p in g.pumas}
        cities[city["key"]] = assemble_city(
            city,
            cfg,
            cells=cells[city["cbsa"]],
            tables=tables[city["cbsa"]],
            puma_counties=counties,
            commute=commute[city["key"]][0],
            items=items.cities[city["cbsa"]],
            job_rules=manifest["job_rules"],
            settings_identity=menu_identity,
        )
    vocab = manifest["vocabulary"]
    life = _yaml(BASE_LIFE_INPUT)
    functions = sorted(
        set(life.get("functions", [])) | set(vocab.get("functions", [])) | {"car_access"}
    )
    life_input = {
        "fixture_notice": (
            "E3B 六城真实资料包（life-pack-us-v1）：城市资料来自公开来源，生活规则与评分参数仍是合成探索设定；"
            "不能据此排序真实城市"
        ),
        "protocol": base_protocol(manifest),
        "consumption_needs": life["consumption_needs"],
        "categories": life["categories"],
        "functions": functions,
        "attribute_keys": sorted(set(vocab.get("attribute_keys", []))),
        "function_kinds": life.get("function_kinds", {}),
        "cuisines": vocab.get("cuisines", []),
        "leisure_categories": vocab.get("leisure_categories", []),
        "capabilities": vocab.get("capabilities", []),
        "enjoyment_functions": vocab.get("enjoyment_functions", []),
        "tax_policies": [p.model_dump(mode="json") for _, p in sorted(tax.policies.items())],
        "catalogs": [cities[c["key"]].catalog for c in cfg["cities"]],
        "product_profiles": [],
        "initial_states": [],
    }
    return PackBuild(
        life_input=life_input,
        cities=cities,
        tax_blocked=tax.blocked,
        identity=build_identity(identity_inputs),
        inputs=identity_inputs,
        items=items.cities,
        backgrounds=backgrounds,
        manifest=manifest,
    )


def _default(obj: Any) -> Any:
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    raise TypeError(f"{type(obj).__name__} 不能写成 JSON")


def dump_json(obj: Any, path: Path) -> str:
    """写成规范 JSON（键排序、UTF-8、LF）；返回内容的 SHA-256。YAML 读进来的日期写成 ISO 文字。"""
    text = json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1, default=_default) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- 评分条件、评价绑定与城市事实


def city_background(con: sqlite3.Connection, cbsa: str) -> dict[str, Any]:
    """intake 冻结库里这城的背景事实（只读需要的几项；没有就是 None，不当 0）。"""
    out: dict[str, Any] = {}
    for (j,) in con.execute(
        "select json from opportunities where kind='city_fact' and cbsa=?", (cbsa,)
    ):
        d = json.loads(j)
        for f in d.get("facts", ()):
            if f.get("status", "present") == "present" and f.get("value") is not None:
                out[f["name"]] = {
                    "value": f["value"],
                    "label": d.get("label"),
                    "kind": f.get("kind"),
                }
    return out


def _decimal(x: Any) -> Decimal:
    return Decimal(str(x))


def safety_pair(cfg: dict[str, Any], s: SafetyInput | None, national: CrimeCount | None):
    """一城的安全（指数，b，r）：资料缺失就是 None（未知，不当零犯罪，也不用他城平均填补）。"""
    if s is None or s.count is None or national is None:
        return None
    c = cfg["conditions"]["safety"]
    r, index = relative_index(
        s.count,
        national,
        w_violent=_decimal(c["w_violent"]),
        w_property=_decimal(c["w_property"]),
        national_index=_decimal(c["national_index"]),
        cap=_decimal(c["cap"]),
    )
    return index, index / 2, r


def background_numbers(
    cfg: dict[str, Any],
    bg: dict[str, Any],
    items: dict[str, Any],
    safety: SafetyInput | None = None,
    national: CrimeCount | None = None,
) -> dict[str, Any]:
    """四个城市背景的 0–10 指数与评分条件里的 b（b = 0.5 × 指数，逐位相等）。"""
    c = cfg["conditions"]
    share = bg.get("share_good_days", {}).get("value")
    env_index = (
        None if share is None else (Decimal(10) * _decimal(share)).quantize(Decimal("0.001"))
    )
    # 发展背景（supported_occupation_coverage_v1）：分子是本城有岗位模板（有已发布 OEWS 工资）的不同职业数，分母是固定的支持职业
    # 总数（职业配置的范围，每城相同）——不是本城自己的职业数
    socs = {t["soc"] for t in items["job_templates"]}
    supported = len(items["start_jobs"]) or 1
    awards = {
        g
        for i in items["items"]
        if i["domain"] == "training" and i["status"] == "executable"
        for g in i["opportunity"].get("grants", ())
    }
    trained = {s for s in socs if f"soc_{s.replace('-', '_')}_award" in awards}
    dev_base = _decimal(c["opportunity"]["scale_jobs"]) * Decimal(len(socs)) / Decimal(
        supported
    ) + _decimal(c["opportunity"]["scale_training"]) * Decimal(len(trained)) / Decimal(supported)
    dev_index = (dev_base * 2).quantize(Decimal("0.001"))
    pair = safety_pair(cfg, safety, national)
    return {
        "safety": None if pair is None else (pair[0], pair[1]),
        "safety_r": None if pair is None else pair[2],
        "safety_input": safety,
        "environment": None if env_index is None else (env_index, env_index / 2),
        "leisure": (_decimal(c["leisure"]["base"]) * 2, _decimal(c["leisure"]["base"])),
        "opportunity": (dev_index, dev_index / 2),
        "coverage": {"supported": supported, "with_jobs": len(socs), "with_training": len(trained)},
        "share_good_days": share,
    }


def _source(kind: str, scope: str, version: str, **kw: Any) -> dict[str, Any]:
    s = {
        "kind": kind,
        "observed_on": "2026-10-23",
        "geography_scope": scope,
        "mapping_version": version,
    }
    s.update(kw)
    return s


def build_scoring(
    cfg: dict[str, Any], pack: PackBuild, numbers: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """评分配置：评分规格原样沿用（内容身份不变），比较组换成六城，城市条件按登记的规则写（安全与环境按片区写同一个城市值）。"""
    base = _yaml(BASE_SCORING)
    out = copy.deepcopy(base)
    out["scoring_notice"] = (
        "E3B 六城真实资料包的评分配置：评分规格与参数仍是合成探索设定（与产品基础样例相同）；城市背景按登记的规则得到——安全按"
        "各都会区的犯罪资料换算，环境与发展按公开资料的固定尺度，休闲是六城共同设定。不能据此排序真实城市"
    )
    group = out["comparison_group"]
    group.update(
        group_id="group.e3b-us-v1",
        cities=[city_ref(c["cbsa"]) for c in cfg["cities"]],
        geography_scope=GROUP_SCOPE,
        allowed_source_kinds=["observed", "derived", "estimated", "synthetic"],
        note=(
            "六城真实资料比较组：同一地理口径（都会区 × 支持范围内的 PUMA）、同一币种与基年（USD_2024_LOCAL）；住房、通勤、食品、"
            "服务、设备、活动与岗位来自公开资料与登记的模型设定；安全按各都会区的犯罪资料换算，环境按 AQI 的固定尺度，休闲背景是"
            "六城共同设定"
        ),
    )
    missing = _source(
        "derived",
        "六城比较组",
        "life-pack-us-v1",
        inputs=["life_pack_us_v1"],
        rule="这一项没有可用资料：报未知，不当零，也不用他城平均填补",
    )
    leisure_prior = _source(
        "synthetic",
        "六城共同先验",
        "life-pack-us-v1",
        synthetic_note=cfg["conditions"]["leisure"]["note"],
    )
    template = next(c for c in base["city_conditions"])["components"]
    conditions = []
    for c in cfg["cities"]:
        n = numbers[c["key"]]
        cat = pack.cities[c["key"]].catalog
        homes = sorted(a["area_id"] for a in cat["areas"] if a["kind"] in ("home", "home_work"))
        comps = []
        for comp in copy.deepcopy(template):
            cid = comp["component_id"]
            if cid == "safety":
                s = n["safety_input"]
                if n["safety"] is None:
                    comp = {
                        "component_id": cid,
                        "status": "unknown",
                        "unknown_reason": (s.missing if s is not None else "没有安全资料")[:280],
                        "source": missing,
                    }
                else:
                    b = float(n["safety"][1])
                    comp.update(
                        base=b,
                        area_bases={a: b for a in homes},
                        area_basis=(
                            "都会区共同背景：每个都会区一个值（FBI 2024 或 DCJS 县合计的犯罪人口发生率相对全国），城内各居住片区共用；"
                            "不按住宅、街区或交通方式区分"
                        ),
                        source=_source(
                            "derived",
                            s.scope,
                            "fbi_relative_rate_v1",
                            inputs=[
                                *s.inputs,
                                f"violent {s.count.violent} / property {s.count.property} / population {s.count.population}",
                                f"r {n['safety_r'].quantize(Decimal('0.0001'))}",
                            ],
                            rule=(
                                "r = 0.5 × 暴力率/全国暴力率 + 0.5 × 财产率/全国财产率（全国：FBI CIUS 2024 表 1）；"
                                "指数 = min(10, 6/r)；b = 指数/2"
                            ),
                        ),
                    )
            elif cid == "environment":
                if n["environment"] is None:
                    comp = {
                        "component_id": cid,
                        "status": "unknown",
                        "unknown_reason": "没有有效年份的 AQI",
                        "source": missing,
                    }
                else:
                    b = float(n["environment"][1])
                    comp.update(
                        base=b,
                        area_bases={a: b for a in homes},
                        area_basis="城市级证据（EPA 年度 AQI 的好天比例 × 5）：各片区同值",
                        source=_source(
                            "derived",
                            f"CBSA {c['cbsa']}",
                            "aqi_good_share_fixed_scale_v1",
                            inputs=["epa_aqi_cbsa_2025", f"share_good_days {n['share_good_days']}"],
                            rule="b = 5 × 一年里 AQI 为“好”的天数比例（固定参考尺度，第七座城不改变前六座）",
                        ),
                    )
            elif cid == "leisure":
                comp.update(base=float(n["leisure"][1]), source=leisure_prior)
            elif cid == "opportunity":
                cov = n["coverage"]
                comp.update(
                    base=float(n["opportunity"][1]),
                    source=_source(
                        "derived",
                        f"CBSA {c['cbsa']}",
                        "supported_occupation_coverage_v1",
                        inputs=[
                            f"supported {cov['supported']}",
                            f"with_jobs {cov['with_jobs']}",
                            f"with_training {cov['with_training']}",
                        ],
                        rule="b = 2 × 有本都会区岗位资料的支持职业占比 + 2 × 有本都会区培训项目的支持职业占比",
                    ),
                )
            else:
                comp["source"] = _source(
                    "synthetic",
                    "六城共同（评分规格的探索参数）",
                    "scoring-e1-convenience",
                    synthetic_note="评分规格的合成探索参数（与产品基础样例相同），不是经验估计",
                )
            comps.append(comp)
        conditions.append(
            {
                "city": city_ref(c["cbsa"]),
                "spec_id": base["city_conditions"][0]["spec_id"],
                "label": c["label"],
                "components": comps,
            }
        )
    out["city_conditions"] = _rescope(conditions)
    out["spec"]["protocol_id"] = PROTOCOL_ID
    return out


def scoring_fingerprint(scoring: dict[str, Any]) -> str:
    """评分规格的内容身份（与加载器同一算法）。"""
    from ledger.life_scoring_config import parse_scoring_config

    return parse_scoring_config(
        json.loads(json.dumps(scoring, default=_default))
    ).spec.content_fingerprint


#: 真实资料包的参考生活槽位（期末估值，DECISIONS/0041、0042）：与产品基础样例同一套模板思路，按真实目录改写——没有健身月卡，
#: 健身改成每月 8 次团体课；杂务服务分成洗衣与打扫两项；“改做已具资格的岗位”只在有培训项目的城市绑定。
EVAL_SLOTS = [
    {
        "slot_id": "housing_basic",
        "role": "housing",
        "description": "基本住房：完整候选池里不用车就能到达全部参考岗位片区的住房中最便宜的一处",
    },
    {
        "slot_id": "housing_comfort",
        "role": "housing",
        "description": "较舒适的住房：不用车就能到达全部参考岗位片区的一卧中档里，最长往返最短的一处",
    },
    {
        "slot_id": "housing_basic_car",
        "role": "housing",
        "description": "基本住房（用车）：开车能到达全部参考岗位片区的住房中最便宜的一处（可不可行由估值期的通勤、现金与硬要求检查判定）",
    },
    {
        "slot_id": "housing_comfort_car",
        "role": "housing",
        "description": "较舒适的住房（用车）：开车时到全部参考岗位片区最长往返最短的一卧中档",
    },
    {
        "slot_id": "car_access",
        "role": "service",
        "description": "用车权（按月付费，月费不含油费；开车的油费按每次往返另付）：只出现在用车的参考生活里",
    },
    {"slot_id": "food_basic", "role": "food", "description": "基本食品方案（主食为主、自己做）"},
    {"slot_id": "food_comfort", "role": "food", "description": "新鲜食材较多的自己做方案"},
    {
        "slot_id": "convenience_device",
        "role": "durable",
        "description": "洗衣机加烘干机；估值期初还在服务期内就照用、不再买",
    },
    {"slot_id": "laundry_service", "role": "service", "description": "上门取送洗衣，按月付费"},
    {"slot_id": "cleaning_service", "role": "service", "description": "两周一次上门打扫，按月付费"},
    {
        "slot_id": "massage",
        "role": "service",
        "description": "每月一次按摩（享受型服务，按月付费）",
    },
    {
        "slot_id": "exercise_class",
        "role": "activity",
        "description": "团体健身课（按次，模型方案）",
    },
    {
        "slot_id": "music_practice",
        "role": "activity",
        "description": "在家弹吉他练习（要能弹的吉他）",
    },
    {"slot_id": "guitar", "role": "durable", "description": "一把吉他（只在模板规定要替换时买）"},
    {
        "slot_id": "qualified_job",
        "role": "job",
        "description": "要求本城培训项目所授资格的岗位（只有估值期初已经合格才适用）",
    },
]


def _templates() -> list[dict[str, Any]]:
    music = {
        "activity_slot": "music_practice",
        "sessions_per_month": 4,
        "equipment_slot": "guitar",
        "when_missing": "skip",
    }
    gym = {
        "activity_slot": "exercise_class",
        "sessions_per_month": 8,
        "when_missing": "skip",
        "after_expiry": "stop",
    }
    out = []
    for jobs, prefix, label in (
        ("current_positions", "p", "现职"),
        ("qualified_alternative", "q", "改做已具资格的岗位"),
    ):
        extra = {"job_slot": "qualified_job"} if jobs == "qualified_alternative" else {}
        base = [
            {
                "template_id": f"{prefix}1_basic",
                "label": f"{label} + 基本生活",
                "jobs": jobs,
                **extra,
                "housing_slot": "housing_basic",
                "food_slot": "food_basic",
                "activity_uses": [{**music, "after_expiry": "stop"}],
                "description": "最便宜的住房、基本食品；已有的设备照用，不另买设备或服务；有吉他就练，到期即停",
            },
            {
                "template_id": f"{prefix}2_device",
                "label": f"{label} + 较舒适的生活 + 洗衣设备",
                "jobs": jobs,
                **extra,
                "housing_slot": "housing_comfort",
                "food_slot": "food_comfort",
                "service_slots": ["massage"],
                "durable_purchases": [
                    {"slot_id": "convenience_device", "month": 1, "rule": "buy_if_not_held"}
                ],
                "activity_uses": [gym, {**music, "after_expiry": "replace"}],
                "description": (
                    "通勤短的一卧、新鲜食材、每月按摩、每月 8 次团体课；没有在用的洗衣设备就在第 1 月买；有吉他就练，到期换新"
                ),
            },
            {
                "template_id": f"{prefix}3_service",
                "label": f"{label} + 较舒适的生活 + 人工杂务服务",
                "jobs": jobs,
                **extra,
                "housing_slot": "housing_comfort",
                "food_slot": "food_comfort",
                "service_slots": ["laundry_service", "cleaning_service", "massage"],
                "activity_uses": [gym, {**music, "after_expiry": "replace"}],
                "description": (
                    "通勤短的一卧、新鲜食材、上门洗衣与打扫、每月按摩、每月 8 次团体课；有吉他就练，到期换新"
                ),
            },
        ]
        # 用车的参考生活（E3B 映射修正，reference_housing_by_job_reach_v2）：同样的基本 / 较舒适含义，住房换成用车槽位、另加用车权。
        # 开车能到达全部参考岗位片区时就绑定，可不可行由估值期的检查判定（一条过慢的无车路线不会把它提前禁用）；模板数不变
        cars = []
        for t in base:
            car = copy.deepcopy(t)
            car["template_id"] = f"{t['template_id']}_car"
            car["label"] = f"{t['label']}（用车）"
            car["housing_slot"] = f"{t['housing_slot']}_car"
            car["service_slots"] = [*t.get("service_slots", []), "car_access"]
            car["description"] = (
                f"{t['description']}；住处只有开车能到岗，另付用车权月费与开车油费"[:400]
            )
            cars.append(car)
        out += base + cars
    return out


def _target(slot: str, oid: str | None, ids: set[str], why: str) -> dict[str, Any]:
    if oid is not None and oid in ids:
        return {"slot_id": slot, "status": "opportunity", "opportunity_id": oid}
    return {"slot_id": slot, "status": "unavailable", "reason": why[:280]}


def build_evaluation(cfg: dict[str, Any], pack: PackBuild) -> dict[str, Any]:
    base = _yaml(BASE_EVALUATION)
    spec = copy.deepcopy(base["spec"])
    spec["spec_id"] = "eval.e3b-us.main"
    spec["slots"] = EVAL_SLOTS
    spec["life_templates"] = _templates()
    spec["note"] = (
        "E3B 六城的期末参考生活：模板与选择规则同产品基础样例的思路，槽位按真实目录绑定；参考住房按参考岗位与通勤关系从完整候选池"
        f"选（{REFERENCE_HOUSING_RULE}），开车能到达全部参考岗位片区时也绑定用车的参考生活；估值期沿用第 3 年菜单加绑定的槽位机会、"
        "不抽设备故障；没有可绑定的槽位写确定没有并说明原因"
    )
    bindings = []
    for c in cfg["cities"]:
        k = c["key"]
        cat = pack.cities[k].catalog
        opps = {o["opportunity_id"]: o for o in cat["opportunities"]}
        ids = set(opps)
        ref = pack.cities[k].reference
        assert ref is not None
        award_jobs = [
            f"{k}.job.{g[4:-6].replace('_', '-')}.start"
            for o in opps.values()
            if o["category"] == "training"
            for g in o.get("grants", ())
            if g.endswith("_award")
        ]
        qualified = next((j for j in sorted(award_jobs) if j in ids), None)
        if qualified is not None and opps[qualified].get("area") not in ref.job_areas:
            # 换岗模板按目标岗位检查：目标岗位必须在参考岗位集合里，参考住房才对它合法
            raise RuntimeError(
                f"{k}: 换岗参考岗位 {qualified} 的片区不在参考岗位集合 {ref.job_areas} 里"
            )
        no_training = "本城没有能授予岗位资格的培训项目（IPEDS 里没有支持职业的整年项目），没有可作参考的“改做已具资格的岗位”"
        bindings.append(
            {
                "city": city_ref(c["cbsa"]),
                "targets": [
                    *(
                        _target(slot, ref.slots[slot], ids, ref.reasons.get(slot, "没有"))
                        for slot in (
                            "housing_basic",
                            "housing_comfort",
                            "housing_basic_car",
                            "housing_comfort_car",
                        )
                    ),
                    _target("car_access", f"{k}.service.car_access", ids, "本城没有用车权"),
                    _target("food_basic", f"{k}.food.staples.h21", ids, "没有主食为主的方案"),
                    _target("food_comfort", f"{k}.food.fresh.h21", ids, "没有鲜食较多的方案"),
                    _target(
                        "convenience_device",
                        f"{k}.durable.pair_top_dryer",
                        ids,
                        "没有洗衣机加烘干机",
                    ),
                    _target(
                        "laundry_service", f"{k}.service.laundry_pickup", ids, "没有上门洗衣服务"
                    ),
                    _target(
                        "cleaning_service",
                        f"{k}.service.cleaning_biweekly",
                        ids,
                        "没有两周一次的打扫服务",
                    ),
                    _target("massage", f"{k}.service.massage_studio_1", ids, "没有按摩服务"),
                    _target(
                        "exercise_class",
                        f"{k}.experience.group_fitness",
                        ids,
                        "本城没有团体健身课的模型方案",
                    ),
                    _target(
                        "music_practice", f"{k}.experience.guitar_practice", ids, "没有在家练琴"
                    ),
                    _target("guitar", f"{k}.durable.acoustic_guitar", ids, "没有吉他"),
                    _target("qualified_job", qualified, ids, no_training),
                ],
            }
        )
    spec["city_bindings"] = bindings
    return {
        "evaluation_notice": (
            "E3B 六城的评价配置：评价规则与参数是合成探索设定；参考生活槽位绑定到真实资料包的机会。不能据此排序真实城市"
        ),
        "spec": spec,
    }


FACT_PERIOD = "2024 基年情景（AQI 用 2025 年有效资料）"
FACT_DEFS = {
    "safety_index": (
        "都会区治安背景",
        "0–10，越高越安全：由这个都会区 2024 年暴力犯罪与财产犯罪的报案人口发生率得到，犯罪率越高指数越低（两项犯罪率与资料覆盖"
        "范围另列）。它只描述都会区的治安背景对居住与出行安排里安全考虑的影响，不是你个人遭遇犯罪或死亡的概率，也不区分街区或"
        "交通方式",
        "指数（0–10）",
        "safety",
    ),
    "development_index": (
        "发展机会（支持职业覆盖）",
        "0–10，越高越多：由本都会区支持职业里有岗位资料的占比与有本都会区培训项目的占比合成。不含你自己的学习进度；不是就业统计",
        "指数（0–10）",
        "opportunity",
    ),
    "leisure_index": (
        "基本休闲条件（共同先验）",
        "住在一座城市就有的少量基本休闲条件，0–10；六城没有可比资料，这里是登记的共同先验。主要的休闲满足来自实际参加的活动"
        "（健身、课程、爱好等另外安排、另外付费）",
        "指数（0–10）",
        "leisure",
    ),
    "environment_index": (
        "空气质量（全年好天比例）",
        "0–10，越高越好：一年里 EPA 空气质量指数为“好”（AQI ≤ 50）的天数占全年的比例（10 表示全年都是好天）；只反映空气，"
        "不含绿地与噪声",
        "指数（0–10）",
        "environment",
    ),
}


def _fact_source(comp: str, cbsa: str) -> dict[str, Any]:
    if comp == "safety":
        return _source(
            "derived",
            f"CBSA {cbsa}",
            "life-pack-us-v1",
            inputs=["fbi_cius_2024 或 ny_dcjs_county_totals"],
            rule="FBI 2024 相对全国的综合犯罪水平 r；指数 = min(10, 6/r)",
        )
    if comp == "environment":
        return _source(
            "derived",
            f"CBSA {cbsa}",
            "life-pack-us-v1",
            inputs=["epa_aqi_cbsa_2025"],
            rule="好天比例 × 10",
        )
    if comp == "opportunity":
        return _source(
            "derived",
            f"CBSA {cbsa}",
            "life-pack-us-v1",
            inputs=["bls_oews_may2024", "ipeds_2023"],
            rule="支持职业覆盖 × 4（岗位）+ × 4（培训）",
        )
    return _source(
        "synthetic", "六城共同先验", "life-pack-us-v1", synthetic_note="登记的共同先验，不是资料"
    )


def _scope_category(s: SafetyInput) -> str:
    """代理可见的覆盖范围（一句话，不带内部参数）。"""
    if not s.scope.startswith("整个"):
        return "都会区的纽约州部分（县人口约占都会区 2/3，含纽约市五区）"
    if s.residence.startswith("资料范围与模拟居住范围相同"):
        return "整个都会区"
    return "整个都会区（含本模拟不居住的外州部分）"


def _crime_facts(cbsa: str, s: SafetyInput | None) -> list[dict[str, Any]]:
    """可读的治安背景说明（代理可见）：两类犯罪的人口发生率与资料覆盖范围。不写换算权重与评分参数，也不说哪种出行方式更安全。"""
    base = {"period": "2024 年", "value_kind": "number", "unit": "起 / 10 万人 / 年"}
    known = s is not None and s.count is not None
    if known and s.nature == "estimated":
        src = _source(
            "estimated",
            f"CBSA {cbsa}",
            "life-pack-us-v1",
            inputs=s.inputs,
            rule="FBI 表 6 的估计总数（报告不全的机构由 FBI 估算）÷ 都会区人口 × 100,000",
        )
    else:
        src = _source(
            "derived",
            f"CBSA {cbsa}",
            "life-pack-us-v1",
            inputs=s.inputs if known else ["没有资料"],
            rule="合计案件数 ÷ 合计人口 × 100,000（不平均各地的率）",
        )
    out = []
    for fid, label, meaning, attr in (
        (
            "violent_crime_rate",
            "暴力犯罪率",
            "谋杀、强奸、抢劫与严重伤害的报案数按人口折算；报案数不是全部发生的犯罪",
            "violent_rate",
        ),
        (
            "property_crime_rate",
            "财产犯罪率",
            "入室盗窃、盗窃与机动车盗窃的报案数按人口折算；报案数不是全部发生的犯罪",
            "property_rate",
        ),
    ):
        head = {"fact_id": fid, "label": label, "meaning": meaning, **base, "source": src}
        if known:
            out.append(
                {
                    **head,
                    "status": "known",
                    "number": float(getattr(s.count, attr).quantize(Decimal("0.1"))),
                }
            )
        else:
            out.append({**head, "status": "unknown", "unknown_reason": "没有资料：不当零犯罪"})
    out.append(
        {
            "fact_id": "safety_data_scope",
            "label": "治安资料覆盖范围",
            "meaning": "上面两项与治安背景所依据的资料覆盖到哪里（整个都会区，或只有一部分县）",
            "value_kind": "category",
            "unit": "范围",
            "period": "2024 年",
            "status": "known" if known else "unknown",
            **({"category": _scope_category(s)} if known else {"unknown_reason": "没有资料"}),
            "source": src,
        }
    )
    return out


def build_city_facts(cfg: dict[str, Any], numbers: dict[str, dict[str, Any]]) -> dict[str, Any]:
    base = _yaml(BASE_FACTS)["city_facts"]
    sheets = []
    for c in cfg["cities"]:
        n = numbers[c["key"]]
        facts = []
        for fid, (label, meaning, unit, comp) in FACT_DEFS.items():
            pair = n[comp]
            head = {
                "fact_id": fid,
                "label": label,
                "meaning": meaning,
                "value_kind": "number",
                "unit": unit,
                "period": FACT_PERIOD,
                "source": _fact_source(comp, c["cbsa"]),
            }
            if pair is None:
                facts.append({**head, "status": "unknown", "unknown_reason": "没有有效资料"})
            else:
                facts.append({**head, "status": "known", "number": float(pair[0])})
        facts += _crime_facts(c["cbsa"], n["safety_input"])
        sheets.append({"city": city_ref(c["cbsa"]), "label": c["label"], "facts": facts})
    link_src = _source(
        "synthetic",
        "六城共同",
        "life-pack-us-v1-fact-links",
        synthetic_note=(
            "换算：评分条件里的背景量 b = 0.5 × 0–10 的指数；只是把可见事实写成已有评分条件里的 b，没有新增评分公式"
        ),
    )
    links = [
        {
            "fact_id": fid,
            "component_id": d[3],
            "rule": "linear_base_v1",
            "factor": "0.5",
            "source": link_src,
        }
        for fid, d in FACT_DEFS.items()
    ]
    return {
        "city_facts": {
            "facts_notice": (
                "E3B 六城的城市背景事实：安全按各都会区 2024 年犯罪资料换算，环境与发展来自公开资料的固定尺度换算，休闲是六城共同"
                "设定。事实表可见；"
                "到评分输入的换算（b = 0.5 × 指数）不进观察"
            ),
            "version": base["version"],
            "comparison_group_id": "group.e3b-us-v1",
            "sheets": sheets,
            "links": links,
        }
    }


def metro_safety_inputs(
    cfg: dict[str, Any], cache: Path = DEFAULT_CACHE
) -> tuple[dict[str, SafetyInput], CrimeCount]:
    """六城的安全输入与全国参照（离线，只读缓存）：都会区名称与县来自 CBSA 2023 划定文件。"""
    d = fetch(geo.delineation_request(), cache, "offline")
    all_geo = geo.parse_delineation(d.content)  # type: ignore[arg-type]
    titles = {c["cbsa"]: all_geo[c["cbsa"]].title for c in cfg["cities"]}
    counties = {
        c["cbsa"]: [(k.fips, f"{k.name}, {k.state_name}") for k in all_geo[c["cbsa"]].counties]
        for c in cfg["cities"]
    }
    return safety_inputs(cfg["cities"], counties, titles, cache)


def build_all(
    out_dir: Path, *, intake_db: Path = INTAKE_DB, cache: Path = DEFAULT_CACHE
) -> dict[str, Any]:
    """组装并写出四份文件与报告（规范 JSON）。返回报告。"""
    cfg = load_pack_config()
    pack = assemble(cfg, intake_db=intake_db, cache=cache)
    safety, national = metro_safety_inputs(cfg, cache)
    numbers = {
        c["key"]: background_numbers(
            cfg,
            pack.backgrounds[c["cbsa"]],
            pack.items[c["cbsa"]],
            safety.get(c["cbsa"]),
            national,
        )
        for c in cfg["cities"]
    }
    scoring = build_scoring(cfg, pack, numbers)
    # 评分规格绑定到 E3B 协议之后内容身份变了：反馈政策钉住的身份一起换（DECISIONS/0025）
    pack.life_input["protocol"]["energy_feedback"]["scoring_content_fingerprint"] = (
        scoring_fingerprint(scoring)
    )
    files = {
        "life_input.json": pack.life_input,
        "scoring.json": scoring,
        "evaluation.json": build_evaluation(cfg, pack),
        "city_facts.json": _rescope(build_city_facts(cfg, numbers)),
    }
    digests = {name: dump_json(obj, out_dir / name) for name, obj in files.items()}
    report = {
        "schema": "e3b-life-pack-report-v1",
        "build_identity": pack.identity,
        "build_inputs": pack.inputs,
        "files": digests,
        "tax_blocked": pack.tax_blocked,
        "cities": {
            k: {
                "counts": c.counts,
                "excluded": dict(sorted(c.excluded.items())),
                **c.diagnostics,
            }
            for k, c in pack.cities.items()
        },
        "backgrounds": {
            k: {
                comp: (None if v is None else [str(v[0]), str(v[1])])
                for comp, v in n.items()
                if comp in ("safety", "environment", "leisure", "opportunity")
            }
            for k, n in numbers.items()
        },
        "safety_sources_sha256": source_digests(cache),
        "safety_national_2024": {
            "population": national.population,
            "violent": national.violent,
            "property": national.property,
            "violent_rate": str(national.violent_rate.quantize(Decimal("0.01"))),
            "property_rate": str(national.property_rate.quantize(Decimal("0.01"))),
            "source": "FBI CIUS 2024 表 1",
        },
        "safety_inputs": {
            k: (
                None
                if n["safety_input"] is None or n["safety_input"].count is None
                else {
                    "scope": n["safety_input"].scope,
                    "nature": n["safety_input"].nature,
                    "coverage": n["safety_input"].coverage,
                    "residence": n["safety_input"].residence,
                    "population": n["safety_input"].count.population,
                    "violent": n["safety_input"].count.violent,
                    "property": n["safety_input"].count.property,
                    "violent_rate": str(
                        n["safety_input"].count.violent_rate.quantize(Decimal("0.01"))
                    ),
                    "property_rate": str(
                        n["safety_input"].count.property_rate.quantize(Decimal("0.01"))
                    ),
                    "r": str(n["safety_r"].quantize(Decimal("0.0001"))),
                    "index": str(n["safety"][0]),
                    "b": str(n["safety"][1]),
                }
            )
            for k, n in numbers.items()
        },
    }
    dump_json(report, out_dir / "report.json")
    from product.realdata.product_pack import build_product_pack, write_product_pack

    write_product_pack(
        build_product_pack(
            scoring_fingerprint=pack.life_input["protocol"]["energy_feedback"][
                "scoring_content_fingerprint"
            ],
            manifest=pack.manifest,
            cities=cfg["cities"],
        )
    )
    return report
