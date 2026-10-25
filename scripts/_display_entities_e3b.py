"""E3B 真实资料包（``pack.e3b.us-v1``）里机会、片区与治疗的英文名称（显示附层的实体资源）。

用法（仓库根目录）::

    uv run python scripts/_display_entities_e3b.py            写出 configs/product/display/entities/result_pack.e3b.us-v1.json
    uv run python scripts/_display_entities_e3b.py --check    只核对：重新生成的内容与已提交的文件相同（不写文件）

做法：从冻结生活输入（``data/frozen/life/us-v1/pack/life_input.json``）里每一项的**结构化字段**，按资料包构建时中文标签的同一组合
规则（``product.realdata`` 的 ``life_pack`` / ``occupations`` / ``life_items`` / ``commute``）同时写出中文与英文；只有重新写出的中文与冻结
标签逐字相同才写进资源（自检），不解析中文标签。英文专名来自资料本身：SOC 职业英文名（岗位的 ``attributes.soc_title``）、PUMA 名称
（ACS 2024 Geos 原始文件，按登记的 sha256 核对；``data/raw`` 不进 git，只在构建机器上有）、县名（冻结通勤资料里就业片区的县组成）、
IPEDS 机构名（培训的 ``attributes``）。中文的固定词（篮子、菜系、条目名称）读构建时的配置；英文的固定词写在这里。

写不出来的（组合规则对不上、缺结构化字段、同一段中文在资料包里对应不同英文）不写，汇总里照实报跳过的条数与原因。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[1]
LIFE_INPUT = REPO / "data/frozen/life/us-v1/pack/life_input.json"
COMMUTE_DIR = REPO / "data/frozen/life/us-v1/commute"
OCCUPATIONS = REPO / "configs/intake/occupations_us_v1.yaml"
ITEMS = REPO / "configs/intake/life_items_us_v1.yaml"
GEOS_DIR = REPO / "data/raw/intake/census_acs1_tables_2024"
GEOS_URL_SUFFIX = "Geos20241YR.txt"
GEOS_SHA256 = "2155acb3c81672eee9b0bd971cca60cd77d8b66222b34973928f57c087ea4afe"
OUT = REPO / "configs/product/display/entities/result_pack.e3b.us-v1.json"
PACK_ID = "pack.e3b.us-v1"

#: 住房条件档次（``life_pack.TIER_ZH``）与英文
TIER = {1: ("低", "low"), 2: ("中", "middle"), 3: ("高", "high")}
#: 外食方式（``life_items`` 食品方案）
AWAY_MODE = {
    "dine_out": ("外食", "eating out"),
    "delivery": ("外送", "delivered"),
    "half": ("外食外送各半", "half eating out, half delivered"),
}
#: 篮子（``food.plan_baskets`` 的 key）的英文；中文读配置
BASKET_EN = {
    "staples": "mostly staples",
    "varied": "varied meat and vegetables",
    "fresh": "more fresh meat and produce",
}
#: 菜系的英文；中文读配置（``food.dining.cuisine_labels``）
CUISINE_EN = {
    "chinese": "Chinese",
    "italian": "Italian",
    "mexican": "Mexican",
    "japanese": "Japanese",
    "indian": "Indian",
    "thai": "Thai",
    "korean": "Korean",
    "vietnamese": "Vietnamese",
    "mediterranean": "Mediterranean",
    "home_style": "home-style",
}
#: 固定条目（``<类别>.<条目>``）的英文；中文读配置里那个条目的 ``label``（构建代码里写死的两条见 ``FIXED_ZH``）
ITEM_EN = {
    "service.laundry_pickup": "Laundry pickup and delivery (weekly)",
    "service.cleaning_biweekly": "Home cleaning (every two weeks)",
    "service.cleaning_weekly": "Home cleaning (weekly)",
    "service.mobile_basic": "Mobile phone service (basic single-line plan)",
    "service.home_internet_basic": "Home broadband (basic tier)",
    "service.home_internet_basic#ce_fallback": "Home broadband (estimated from average spending)",
    "service.household_supplies_basic": "Household supplies (cleaning products and paper goods)",
    "service.car_access": "Car access (one compact car, monthly)",
    "durable.washer_top": "Top-load washer",
    "durable.washer_front": "Front-load washer",
    "durable.pair_top_dryer": "Top-load washer + electric dryer set",
    "durable.pair_front_dryer": "Front-load washer + electric dryer set",
    "durable.combo": "Washer-dryer combo",
    "durable.tennis_racket": "Tennis racket",
    "durable.acoustic_guitar": "Acoustic guitar",
    "experience.group_fitness": "Group fitness class (one session)",
    "experience.yoga_class": "Yoga class (one session)",
    "experience.lap_swim": "Lap swim at a public pool (one session)",
    "experience.tennis_rental": "Tennis court with racket rental (one session)",
    "experience.tennis_own": "Tennis court (own racket, one session)",
    "experience.guitar_lesson": "Small-group guitar lesson (one session)",
    "experience.guitar_practice": "Guitar practice at home (one 1-hour session)",
    "experience.bodyweight_workout": "Bodyweight workout (one 1-hour session)",
    "experience.art_class": "Painting class (one session)",
    "experience.cooking_class": "Cooking class (one session, does not replace a meal)",
    "experience.museum_visit": "Museum visit (one time)",
    "training.none_in_scope": "Training program (none within the supported scope)",
}
#: 构建代码里写死、不在配置里的中文条目名（``commute.py`` 的用车权、``life_assembly.py`` 的范围内没有培训）
FIXED_ZH = {
    "service.car_access": "用车权（一辆小型轿车，按月）",
    "training.none_in_scope": "培训项目（支持范围内没有）",
}
#: 治疗（``life_assembly.py`` 的两种共同假设，按病情轻重）
TREATMENT = {
    "minor": ("门诊与药物（共同假设）", "Outpatient visit and medicine (common assumption)"),
    "major": ("住院治疗（共同假设）", "Inpatient treatment (common assumption)"),
}
#: 配置里条目编号的字段名（服务、日常支持、设备、装备、活动模板）
_ITEM_KEYS = ("service", "offer", "item", "template")


def _cut(s: str, n: int) -> str:
    """与 ``product.realdata.commute._cut`` 相同：超长时截断并加省略号。"""
    return s if len(s) <= n else s[: n - 1] + "…"


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _number(x: float) -> str:
    return f"{x:g}"


# ---------------------------------------------------------------------- 输入


def puma_names() -> dict[str, str]:
    """ACS 2024 Geos：州 + PUMA（7 位）→ 名称。只接受登记的那一份原始文件（sha256 对得上）。"""
    for meta in sorted(GEOS_DIR.glob("*.meta.json")):
        m = json.loads(meta.read_text(encoding="utf-8"))
        if not str(m.get("url", "")).endswith(GEOS_URL_SUFFIX):
            continue
        raw = meta.with_name(meta.name.replace(".meta.json", ".bin")).read_bytes()
        if hashlib.sha256(raw).hexdigest() != GEOS_SHA256:
            raise SystemExit(f"{meta.name}: Geos 原始文件的 sha256 与登记的不同，不用")
        lines = raw.decode("latin-1").splitlines()
        idx = {h: i for i, h in enumerate(lines[0].split("|"))}
        out = {}
        for ln in lines[1:]:
            p = ln.split("|")
            if len(p) >= len(idx) and p[idx["SUMLEVEL"]] == "795" and p[idx["COMPONENT"]] == "00":
                out[p[idx["STATE"]] + p[idx["PUMA5"]]] = p[idx["NAME"]]
        return out
    raise SystemExit(
        f"没有找到 ACS 2024 Geos 原始文件（{GEOS_DIR}）：PUMA 名称只能从资料本身取，不猜"
    )


def config_items(cfg: Any) -> dict[str, dict[str, str]]:
    """配置里每个条目编号 → {label, label_ce_fallback}（只取写了编号与中文名称的条目）。"""
    out: dict[str, dict[str, str]] = {}

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            key = next((v[k] for k in _ITEM_KEYS if isinstance(v.get(k), str)), None)
            if key is not None and isinstance(v.get("label"), str):
                out.setdefault(key, {})["label"] = v["label"]
                if isinstance(v.get("label_ce_fallback"), str):
                    out[key]["label_ce_fallback"] = v["label_ce_fallback"]
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(cfg)
    return out


class Sources:
    def __init__(self) -> None:
        self.life = json.loads(LIFE_INPUT.read_text(encoding="utf-8"))
        occ = yaml.safe_load(OCCUPATIONS.read_text(encoding="utf-8"))
        self.occ_zh = {str(s["soc"]): s["label"] for s in occ["supported"]}
        items = yaml.safe_load(ITEMS.read_text(encoding="utf-8"))
        food = items["food"]
        self.baskets = {b["basket"]: b for b in food["plan_baskets"]}
        self.cuisine_zh = food["dining"]["cuisine_labels"]
        self.basket_zh = items["consumption_basket"]["label"]
        self.items = config_items(items)
        self.puma = puma_names()
        self.work_counties: dict[str, list[str]] = {}
        for path in sorted(COMMUTE_DIR.glob("*.json")):
            if path.name.endswith(".time_distributions.json") or path.name == "index.json":
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            for a in data["diagnostics"]["work_areas"]["areas"]:
                self.work_counties[a["area_id"]] = list(a["counties"])


# ---------------------------------------------------------------------- 组合规则（中文与英文同一份结构化输入）


def _suffix(o: dict[str, Any]) -> str:
    """机会编号去掉城市前缀：``hou.service.cleaning_weekly`` → ``service.cleaning_weekly``。"""
    return o["opportunity_id"].split(".", 1)[1]


def job_names(o: dict[str, Any], src: Sources) -> tuple[str, str] | None:
    """``occupations.job_opportunity``：``{职业中文名}·{工资位置}``；工资位置来自年薪支持点（编号里的 ``w<k>``，
    ``job_wage_support_v1``：p10–p90 切 20 段取中点，第 k 段是 12 + 4k 分位），开局岗位是 ``p50``。"""
    soc = (o.get("occupation") or {}).get("code")
    title = (o.get("attributes") or {}).get("soc_title")
    if soc not in src.occ_zh or not title:
        return None
    parts = o["opportunity_id"].split(".")
    if parts[-1] == "start" and parts[2] == soc:
        return f"{src.occ_zh[soc]}·p50"[:120], f"{title} · median wage (p50)"
    if len(parts) >= 4 and parts[2] == soc and parts[3][:1] == "w" and parts[3][1:].isdigit():
        pct = 12 + 4 * int(parts[3][1:])
        return (
            f"{src.occ_zh[soc]}·工资约在本职业 {pct} 分位"[:120],
            f"{title} · wage around the {_ordinal(pct)} percentile for this occupation",
        )
    return None


def housing_names(o: dict[str, Any], src: Sources, *, full: bool = True) -> tuple[str, str] | None:
    """``life_pack.housing_opportunity``：``{卧室}整套租住（{PUMA 名称}，{档次}档）``。PUMA 名称取 Geos 名称在 " PUMA" 之前的部分
    （中文截到 60 字；英文用完整名称，``full=False`` 时也截到 60 字）。"""
    spec = o.get("spec") or {}
    beds, tier = spec.get("bedrooms"), spec.get("quality_level")
    code = o["opportunity_id"].split(".")[2]
    name = src.puma.get(code)
    if not isinstance(beds, int) or tier not in TIER or name is None:
        return None
    place = name.split(" PUMA")[0]
    bed_zh = "开间" if beds == 0 else f"{beds} 卧"
    bed_en = "Studio" if beds == 0 else f"{beds}-bedroom"
    tier_zh, tier_en = TIER[tier]
    return (
        f"{bed_zh}整套租住（{place[:60]}，{tier_zh}档）"[:120],
        f"{bed_en} whole-unit rental ({place if full else place[:60]}, {tier_en} tier)",
    )


def food_names(o: dict[str, Any], src: Sources) -> tuple[str, str] | None:
    """``life_items`` 食品方案：家里 21 顿是 ``自己做·{篮子}``；否则 ``自己做 {h} 顿·{篮子} + {方式} {外面顿数} 顿（{菜系}）``。"""
    attrs = o.get("attributes") or {}
    basket = src.baskets.get(attrs.get("basket"))
    h = attrs.get("home_meals_per_week")
    if basket is None or not isinstance(h, int) or basket["key"] not in BASKET_EN:
        return None
    b_zh, b_en = basket["label"], BASKET_EN[basket["key"]]
    if h == 21:
        return f"自己做·{b_zh}", f"Home-cooked · {b_en}"
    mode = attrs.get("away_mode")
    away = [p for p in (o.get("food_plan") or {}).get("parts", ()) if p.get("part_id") != "home"]
    cuisines = {p.get("cuisine") for p in away}
    if mode not in AWAY_MODE or len(cuisines) != 1:
        return None
    cuisine = cuisines.pop()
    if cuisine not in src.cuisine_zh or cuisine not in CUISINE_EN:
        return None
    n = _number(21 - h)
    m_zh, m_en = AWAY_MODE[mode]
    return (
        f"自己做 {h} 顿·{b_zh} + {m_zh} {n} 顿（{src.cuisine_zh[cuisine]}）",
        f"{h} home-cooked meals · {b_en} + {n} meals {m_en} ({CUISINE_EN[cuisine]})",
    )


def basket_names(o: dict[str, Any], src: Sources) -> tuple[str, str] | None:
    """``life_items`` 个人消费篮子：``{名称}（每月 {u} 个标准篮子）``，u 是这一项每月的标准篮子数。"""
    units = [e.get("units_per_month") for e in o.get("enjoyment") or () if e.get("units_per_month")]
    if len(units) != 1:
        return None
    u = _number(units[0])
    return (
        f"{src.basket_zh}（每月 {u} 个标准篮子）",
        f"Personal consumption basket ({u} standard basket{'' if u == '1' else 's'} a month)",
    )


def training_names(o: dict[str, Any], src: Sources) -> tuple[str, str] | None:
    """``occupations`` / ``life_items`` 培训：``CIP {代码} 证书项目·{机构}``（120 字以内）。"""
    attrs = o.get("attributes") or {}
    cip, inst = attrs.get("cip"), attrs.get("institution")
    if not cip or not inst:
        return None
    return f"CIP {cip} 证书项目·{inst}"[:120], f"CIP {cip} certificate program · {inst}"


def item_names(o: dict[str, Any], src: Sources) -> list[tuple[str, str]]:
    """固定条目：配置里这个条目的中文名称（与它的替代写法），英文按条目编号写在 ``ITEM_EN``。"""
    key = _suffix(o)
    item = key.split(".", 1)[1] if "." in key else key
    out = []
    zh = FIXED_ZH.get(key) or src.items.get(item, {}).get("label")
    if zh is not None and key in ITEM_EN:
        out.append((zh, ITEM_EN[key]))
    alt = src.items.get(item, {}).get("label_ce_fallback")
    if alt is not None and f"{key}#ce_fallback" in ITEM_EN:
        out.append((alt, ITEM_EN[f"{key}#ce_fallback"]))
    return out


def opportunity_candidates(o: dict[str, Any], src: Sources) -> tuple[str, list[tuple[str, str]]]:
    """（规则名，候选的中英写法）。"""
    cat = o["category"]
    key = _suffix(o)
    if cat == "job":
        found = job_names(o, src)
        return "job", [found] if found else []
    if cat == "housing":
        found = housing_names(o, src)
        return "housing", [found] if found else []
    if cat == "food":
        found = food_names(o, src)
        return "food", [found] if found else []
    if cat == "service" and key.startswith("service.personal_basket."):
        found = basket_names(o, src)
        return "basket", [found] if found else []
    if cat == "training" and key != "training.none_in_scope":
        found = training_names(o, src)
        return "training", [found] if found else []
    return f"item.{cat}", item_names(o, src)


def area_names(a: dict[str, Any], src: Sources) -> tuple[str, str] | None:
    """``commute``：居住片区 ``居住片区·{PUMA 名称}（PUMA {代码}）``；就业片区 ``就业片区·{县}``，合并的其余工作地
    ``就业片区·其余工作地（{县、县……}）``（都截到 120 字）。"""
    aid = a["area_id"]
    if a["kind"] == "home":
        code = aid.rsplit(".", 1)[1]
        raw = src.puma.get(code)
        name = (raw or "").removesuffix(" PUMA").split(";")[0]
        zh = f"居住片区·{name}（PUMA {code}）" if name else f"居住片区·PUMA {code}"
        en = f"Home area: {name} (PUMA {code})" if name else f"Home area: PUMA {code}"
        return _cut(zh, 120), en
    if a["kind"] == "work" and aid in src.work_counties:
        groups = src.work_counties[aid]
        counties = [c for g in groups for c in g.split("、")]
        if aid.endswith(".other"):
            return (
                _cut("就业片区·其余工作地（" + "、".join(groups) + "）", 120),
                f"Work area: other workplaces ({', '.join(counties)})",
            )
        if len(groups) == 1:
            return _cut(f"就业片区·{groups[0]}", 120), f"Work area: {', '.join(counties)}"
    return None


# ---------------------------------------------------------------------- 生成与自检


def build() -> tuple[dict[str, Any], dict[str, Any]]:
    src = Sources()
    entities: dict[str, dict[str, dict[str, dict[str, str]]]] = defaultdict(dict)
    verified: Counter[str] = Counter()
    skipped: dict[str, Counter[str]] = defaultdict(Counter)
    total: Counter[str] = Counter()
    by_zh: dict[tuple[str, str], set[str]] = defaultdict(set)
    rule_of: dict[tuple[str, str], str] = {}

    def emit(kind: str, ident: str, zh: str, en: str, rule: str) -> None:
        entities[kind][ident] = {"label": {"zh": zh, "en": en}}
        verified[rule] += 1
        by_zh[(kind, zh)].add(en)
        rule_of[(kind, ident)] = rule

    for cat in src.life["catalogs"]:
        for o in cat["opportunities"]:
            rule, cands = opportunity_candidates(o, src)
            total[rule] += 1
            hit = next((c for c in cands if c[0] == o["label"]), None)
            if hit is None:
                skipped[rule]["no_structured_rule" if not cands else "zh_mismatch"] += 1
                continue
            emit("opportunity", o["opportunity_id"], hit[0], hit[1], rule)
        for a in cat["areas"]:
            rule = f"area.{a['kind']}"
            total[rule] += 1
            found = area_names(a, src)
            if found is None or found[0] != a["label"]:
                skipped[rule]["no_structured_rule" if found is None else "zh_mismatch"] += 1
                continue
            emit("area", a["area_id"], found[0], found[1], rule)
        for t in cat["treatments"]:
            total["treatment"] += 1
            found = TREATMENT.get(t.get("severity"))
            if found is None or found[0] != t["label"]:
                skipped["treatment"]["no_structured_rule" if found is None else "zh_mismatch"] += 1
                continue
            emit("treatment", t["treatment_id"], found[0], found[1], "treatment")

    # 同一段中文在资料包里只能有一种英文（否则同一份消息里同一段中文有两种绑定，显示为 ambiguous）：住房先改用截到 60 字的
    # 名称（与中文同一份输入），还不唯一就不写
    opps = {o["opportunity_id"]: o for c in src.life["catalogs"] for o in c["opportunities"]}
    for (kind, zh), ens in list(by_zh.items()):
        if len(ens) < 2:
            continue
        idents = [i for i, f in entities[kind].items() if f["label"]["zh"] == zh]
        shorter: dict[str, str | None] = {}
        for ident in idents:
            o = opps.get(ident) if kind == "opportunity" else None
            short = (
                housing_names(o, src, full=False)
                if o is not None and o["category"] == "housing"
                else None
            )
            shorter[ident] = None if short is None else short[1]
        if None not in shorter.values() and len(set(shorter.values())) == 1:
            for ident, en in shorter.items():
                entities[kind][ident]["label"]["en"] = en  # type: ignore[assignment]
            continue
        for ident in idents:
            rule = rule_of[(kind, ident)]
            del entities[kind][ident]
            verified[rule] -= 1
            skipped[rule]["same_zh_different_en"] += 1

    summary = {
        "verified": dict(sorted(verified.items())),
        "skipped": {k: dict(sorted(v.items())) for k, v in sorted(skipped.items()) if v},
        "total": dict(sorted(total.items())),
    }
    body = {
        "pack_id": PACK_ID,
        "note": (
            "由 scripts/_display_entities_e3b.py 生成，不要手改：按冻结生活输入的结构化字段与构建时中文标签的同一组合规则写出，"
            "重新写出的中文与冻结标签逐字相同才收录"
        ),
        "summary": summary,
        "entities": {k: dict(sorted(v.items())) for k, v in sorted(entities.items())},
    }
    return body, summary


def render(body: dict[str, Any]) -> str:
    return json.dumps(body, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--check", action="store_true", help="只核对已提交的文件与重新生成的相同")
    args = ap.parse_args(argv)
    body, summary = build()
    text = render(body)
    for rule, n in summary["total"].items():
        ok = summary["verified"].get(rule, 0)
        why = summary["skipped"].get(rule, {})
        print(f"{rule}: {ok}/{n} 收录" + (f"，跳过 {why}" if why else ""))
    if args.check:
        same = OUT.is_file() and OUT.read_text(encoding="utf-8") == text
        print("与已提交的文件相同" if same else "与已提交的文件不同：重新运行本脚本")
        return 0 if same else 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"写出 {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
