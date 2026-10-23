"""E3B 六城真实生活资料包的组装（DECISIONS/0042）：住房（本模块的 ``life_pack``）、通勤关系与片区（``commute``）、生活机会与
职业（``life_items`` / ``occupations``）、官方税（``tax_rules``）合成 E1 能读的生活输入、评分配置、评价配置与城市事实。

组装只做确定性的拼接与登记过的规则，不补数：

* **岗位的就业片区**：按通勤资料里“各就业片区的到岗者占比（按 SOC 大类）”取占比最大的片区（登记规则
  ``job_placement_modal_area_v1``；同一大类的岗位放在同一片区，没有反映同一职业分散在各片区的情况）。
* **住房**只留通勤资料里有居住片区的 PUMA；没有片区的照实计数排除。
* **最低生活**：最低住房 = 最便宜的住房（全部住房都合格：ACS 租住单元是独立住房单元）；最低食品 = 生活机会资料给出的最低食品方案。
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
    environment_base,
    housing_items,
    load_pack_config,
    stratified_menus,
)
from product.realdata.safety import (
    CrimeCount,
    SafetyInput,
    relative_index,
    safety_inputs,
    source_digests,
)
from product.realdata.tax_rules import compile_tax

FROZEN = Path("data/frozen/life/us-v1")
BASE_LIFE_INPUT = Path("configs/life/synthetic_product.yaml")
BASE_SCORING = Path("configs/life/scoring_product.yaml")
BASE_EVALUATION = Path("configs/life/evaluation_product.yaml")
BASE_FACTS = Path("configs/life/city_facts_product.yaml")
INTAKE_DB = Path("runs/intake/us-v1/work/freeze/562c32339bbae67f/intake.sqlite")
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


def modal_work_area(shares: dict[str, Any], soc: str) -> str:
    """一个职业放在哪个就业片区：它的 SOC 大类到岗者占比最大的片区；没有这一大类就看全体（job_placement_modal_area_v1）。"""
    table = shares.get("by_soc_major", {}).get(soc[:2]) or shares["all"]
    rows = table["shares"]
    return max(sorted(rows), key=lambda a: Decimal(str(rows[a])))


@dataclass
class CityBuild:
    catalog: dict[str, Any]
    counts: dict[str, Any] = field(default_factory=dict)
    excluded: Counter[str] = field(default_factory=Counter)


@dataclass
class PackBuild:
    life_input: dict[str, Any]
    cities: dict[str, CityBuild]
    tax_blocked: dict[str, list[str]]
    identity: str = ""


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


def assemble_city(
    city: dict[str, Any],
    cfg: dict[str, Any],
    *,
    candidates: list[dict[str, Any]],
    puma_counties: dict[str, tuple[str, ...]],
    commute: dict[str, Any],
    items: dict[str, Any],
    settings_identity: str,
) -> CityBuild:
    key, cbsa, numeraire = city["key"], city["cbsa"], cfg["numeraire"]
    out_excluded: Counter[str] = Counter()
    hb = housing_items(city, cfg, candidates, puma_counties)
    out_excluded.update({f"housing.{k}": v for k, v in hb.excluded.items()})
    home_areas = {a["area_id"] for a in commute["areas"] if a["kind"] in ("home", "home_work")}
    housing = []
    for h in hb.items:
        if h.area in home_areas:
            housing.append(h)
        else:
            out_excluded["housing.no_commute_area"] += 1
    weights = {h.opportunity_id: hb.weights[h.opportunity_id] for h in housing}
    strata = {h.opportunity_id: hb.strata[h.opportunity_id] for h in housing}

    opps: list[dict[str, Any]] = [h.model_dump(mode="json") for h in housing]
    shares = commute["work_area_job_shares"]
    job_weights: dict[str, float] = {}
    by_domain: Counter[str] = Counter()
    for it in items["items"]:
        o = copy.deepcopy(it["opportunity"])
        status = it["status"]
        if status == "needs_area":
            o["area"] = modal_work_area(shares, o["occupation"]["code"])
            emp = (o.get("attributes") or {}).get("employment")
            job_weights[o["opportunity_id"]] = float(emp or 0)
        elif status == "needs_e1_extension":
            out_excluded[f"{it['domain']}.needs_e1_extension"] += 1
            continue
        elif status not in ("executable", "data_unknown"):
            out_excluded[f"{it['domain']}.{status}"] += 1
            continue
        by_domain[it["domain"]] += 1
        opps.append(o)
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

    years, envs = cfg["simulation_years"], cfg["env_repeats"]
    menus = stratified_menus(
        key=key,
        cbsa=cbsa,
        category="housing",
        weights=weights,
        strata=strata,
        per_stratum=cfg["menus"]["housing"]["per_stratum"],
        size=None,
        years=years,
        env_repeats=envs,
        seed=cfg["menus"]["seed"],
        settings_identity=settings_identity,
    ) + stratified_menus(
        key=key,
        cbsa=cbsa,
        category="job",
        weights=job_weights,
        strata=None,
        per_stratum=None,
        size=cfg["menus"]["job"]["size"],
        years=years,
        env_repeats=envs,
        seed=cfg["menus"]["seed"],
        settings_identity=settings_identity,
    )

    cheapest = min(housing, key=lambda h: (h.price.amount.amount, h.opportunity_id))
    # 目录只放这次运行能接触到的住房：任一年、任一环境重复的菜单上出现过的，加上最低住房（兜底要用）。可执行的全集照样计数、
    # 进菜单抽样（菜单的抽样池是全集）；没上任何菜单的住房分身永远看不到、也选不了，放进目录只会让文件变大
    reachable = {oid for m in menus if m.category == "housing" for oid in m.opportunity_ids}
    reachable.add(cheapest.opportunity_id)
    opps = [o for o in opps if o["category"] != "housing" or o["opportunity_id"] in reachable]
    housing = [h for h in housing if h.opportunity_id in reachable]
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
                "meets": "一个人独占的整套租住单元（ACS 租住单元；厨卫设施按登记假设）",
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
    counts = {
        "housing_candidates": len(candidates),
        "housing_executable": len(weights),
        "housing_in_catalog": len(housing),
        "items_by_domain": dict(sorted(by_domain.items())),
        "jobs_placed": len(job_weights),
        "menus": len(menus),
        "areas": len(commute["areas"]),
        "commute_legs": len(commute["commute"]["legs"]),
    }
    return CityBuild(catalog=catalog, counts=counts, excluded=out_excluded)


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
    manifest = _json(frozen / "items" / "manifest.json")
    tax = compile_tax(cache=cache)
    d = fetch(geo.delineation_request(), cache, "offline")
    tp = fetch(geo.tract_puma_request(), cache, "offline")
    if not (d.ok and tp.ok):
        raise FileNotFoundError(
            "缓存里没有 CBSA 划定或普查区—PUMA 对照（先运行 intake 的获取命令）"
        )
    all_geo = geo.parse_delineation(d.content)  # type: ignore[arg-type]
    tract_puma = geo.parse_tract_puma(tp.content)  # type: ignore[arg-type]
    # 菜单种子只取菜单设定（算法、配置种子、每层个数、年数、环境重复数），不含任何资料身份、评分条件或税：加权无放回抽样的
    # 键按“种子 + 编号”逐项算，所以资料改动只经由抽样池本身（候选与权重）改变菜单——改背景值、油费或税不会重抽别的住房与岗位
    menu_identity = canonical_identity(
        {
            "menus": cfg["menus"],
            "simulation_years": cfg["simulation_years"],
            "env_repeats": cfg["env_repeats"],
        }
    )
    identity_inputs = {
        "config": canonical_identity(cfg),
        "items": manifest["content_identity"],
        "commute": {
            c["key"]: _json(frozen / "commute" / f"{c['key']}.json")["content_identity"]
            for c in cfg["cities"]
        },
        "tax": {
            pid: canonical_identity(p.model_dump(mode="json"))
            for pid, p in sorted(tax.policies.items())
        },
        "safety_sources": source_digests(cache),
        "intake_build": manifest["intake_build_id"],
    }
    build_identity = canonical_identity(identity_inputs)
    con = sqlite3.connect(f"file:{intake_db}?mode=ro", uri=True)
    cities: dict[str, CityBuild] = {}
    for city in cfg["cities"]:
        g = geo.attach_pumas(all_geo[city["cbsa"]], tract_puma)
        counties = {p.code: tuple(p.counties) for p in g.pumas}
        cands = [
            json.loads(j)
            for (j,) in con.execute(
                "select json from opportunities where kind='rental_listing_candidate' and cbsa=? order by opportunity_id",
                (city["cbsa"],),
            )
        ]
        cities[city["key"]] = assemble_city(
            city,
            cfg,
            candidates=cands,
            puma_counties=counties,
            commute=_json(frozen / "commute" / f"{city['key']}.json"),
            items=_json(frozen / "items" / f"{city['cbsa']}.json"),
            settings_identity=menu_identity,
        )
    con.close()
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
        identity=build_identity,
    )


def area_conditions(
    cfg: dict[str, Any], catalog: dict[str, Any], share_good_days: float | None
) -> dict[str, Any]:
    """一城的评分条件里按片区的部分：安全（共同先验）与环境（AQI 固定尺度）对每个居住片区写同一个城市值。"""
    homes = sorted(a["area_id"] for a in catalog["areas"] if a["kind"] in ("home", "home_work"))
    c = cfg["conditions"]
    env = environment_base(cfg, share_good_days)
    return {
        "safety": {a: c["safety"]["base"] for a in homes},
        "environment": None if env is None else {a: env for a in homes},
    }


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
    jobs = [i for i in items["items"] if i["domain"] == "job"]
    socs = {i["opportunity"]["occupation"]["code"] for i in jobs}
    supported = len({i["opportunity"]["occupation"]["code"] for i in jobs}) or 1
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
        "E3B 六城真实资料包的评分配置：评分规格与参数仍是合成探索设定（与产品基础样例相同）；城市背景按登记的规则从公开资料或"
        "共同先验得到。不能据此排序真实城市"
    )
    group = out["comparison_group"]
    group.update(
        group_id="group.e3b-us-v1",
        cities=[city_ref(c["cbsa"]) for c in cfg["cities"]],
        geography_scope=GROUP_SCOPE,
        allowed_source_kinds=["observed", "derived", "estimated", "synthetic"],
        note=(
            "六城真实资料比较组：同一地理口径（都会区 × 支持范围内的 PUMA）、同一币种与基年（USD_2024_LOCAL）；住房、通勤、食品、"
            "服务、设备、活动与岗位来自公开资料与登记的模型设定；安全与休闲背景是共同先验，环境按 AQI 的固定尺度"
        ),
    )
    prior = _source(
        "synthetic",
        "六城共同先验",
        "life-pack-us-v1",
        synthetic_note=cfg["conditions"]["safety"]["note"],
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
                        "source": prior,
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
                        "source": prior,
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
        "description": "基本住房：本城最便宜的住房（最低住房方案）",
    },
    {
        "slot_id": "housing_comfort",
        "role": "housing",
        "description": "较舒适的住房：居民平时通勤最短的片区里的一卧中档",
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
        out += [
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
        "E3B 六城的期末参考生活：模板与选择规则同产品基础样例的思路，槽位按真实目录绑定；估值期沿用第 3 年菜单、不抽设备故障；"
        "没有可绑定的槽位写确定没有并说明原因"
    )
    bindings = []
    for c in cfg["cities"]:
        k = c["key"]
        cat = pack.cities[k].catalog
        opps = {o["opportunity_id"]: o for o in cat["opportunities"]}
        ids = set(opps)
        typical = {a["area_id"]: a.get("typical_roundtrip_minutes") for a in cat["areas"]}
        homes = [o for o in opps.values() if o["category"] == "housing"]
        cheapest = cat["minimum_living"][0]["baseline_opportunity_id"]
        mid1 = [
            o
            for o in homes
            if o["spec"].get("bedrooms") == 1 and o["spec"].get("quality_level") == 2
        ]
        comfort = min(
            mid1,
            key=lambda o: (
                typical.get(o["area"]) or 10**6,
                o["price"]["amount"]["amount"],
                o["opportunity_id"],
            ),
            default=None,
        )
        award_jobs = [
            f"{k}.job.{g[4:-6].replace('_', '-')}.p50"
            for o in opps.values()
            if o["category"] == "training"
            for g in o.get("grants", ())
            if g.endswith("_award")
        ]
        qualified = next((j for j in sorted(award_jobs) if j in ids), None)
        no_training = "本城没有能授予岗位资格的培训项目（IPEDS 里没有支持职业的整年项目），没有可作参考的“改做已具资格的岗位”"
        bindings.append(
            {
                "city": city_ref(c["cbsa"]),
                "targets": [
                    _target("housing_basic", cheapest, ids, "没有住房"),
                    _target(
                        "housing_comfort",
                        comfort["opportunity_id"] if comfort else None,
                        ids,
                        "没有一卧中档",
                    ),
                    _target("food_basic", f"{k}.food.home_staples", ids, "没有主食为主的方案"),
                    _target("food_comfort", f"{k}.food.home_fresh", ids, "没有鲜食较多的方案"),
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
        "0–10，越高越安全：这个都会区 2024 年暴力犯罪与财产犯罪人口发生率相对全国平均的综合（全国平均水平是 6）。它只描述都会区的"
        "治安背景对居住与出行安排里安全考虑的影响，不是你个人遭遇犯罪或死亡的概率，也不区分街区或交通方式",
        "指数（0–10）",
        "safety",
    ),
    "development_index": (
        "发展机会（支持职业覆盖）",
        "0–10：本都会区有岗位资料的支持职业占比 × 4 + 有本都会区培训项目的支持职业占比 × 4。不含你自己的学习进度；不是就业统计",
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
        "空气质量（好天比例 × 10）",
        "0–10：一年里 EPA 空气质量指数为“好”（AQI ≤ 50）的天数比例 × 10，越高越好；只反映空气，不含绿地与噪声",
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
        return "都会区的纽约州部分（约 2/3 人口，含纽约市五区）"
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
                "E3B 六城的城市背景事实：环境与发展来自公开资料的固定尺度换算，安全与休闲是登记的共同先验。事实表可见；"
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
    con = sqlite3.connect(f"file:{intake_db}?mode=ro", uri=True)
    numbers = {
        c["key"]: background_numbers(
            cfg,
            city_background(con, c["cbsa"]),
            _json(FROZEN / "items" / f"{c['cbsa']}.json"),
            safety.get(c["cbsa"]),
            national,
        )
        for c in cfg["cities"]
    }
    con.close()
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
        "files": digests,
        "tax_blocked": pack.tax_blocked,
        "cities": {
            k: {"counts": c.counts, "excluded": dict(sorted(c.excluded.items()))}
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
            manifest=_json(FROZEN / "items" / "manifest.json"),
            cities=cfg["cities"],
        )
    )
    return report
