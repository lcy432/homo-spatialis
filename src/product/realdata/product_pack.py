"""E3B 真实资料包的产品配置（DECISIONS/0042）：``configs/product/e3b_us_v1_pack.yaml``。

问卷、六维与物质题干、健康问卷、硬要求与属性的支持清单原样沿用 E3-A 资料包（它们是产品词表，不是城市资料）；换上的是：六座真实
城市、支持的 40 个职业（标签来自 ``configs/intake/occupations_us_v1.yaml``，已有概念的别名保留）、真实资料包的能力词表、真实
菜系词表、E1 基础样例改指冻结的六城资料包，以及照实写的范围与局限。不编入任何合成便利候选（便利设备与服务来自真实资料包）。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import yaml

from product.realdata.life_assembly import GROUP_SCOPE, PROTOCOL_ID

SYNTH_PACK = Path("configs/product/e3a_synthetic_pack.yaml")
REAL_PACK = Path("configs/product/e3b_us_v1_pack.yaml")
OCCUPATIONS = Path("configs/intake/occupations_us_v1.yaml")
PACK_DIR = "data/frozen/life/us-v1/pack"

CUISINE_LABELS = {
    "home_style": ("家常菜", ["家常"]),
    "chinese": ("中餐", ["中餐", "中国菜", "川菜", "粤菜", "chinese"]),
    "italian": ("意大利菜", ["意大利", "意式", "披萨", "意面", "italian", "pasta"]),
    "mexican": ("墨西哥菜", ["墨西哥", "塔可", "mexican", "taco"]),
    "japanese": ("日本料理", ["日料", "日本菜", "寿司", "拉面", "japanese", "sushi"]),
    "indian": ("印度菜", ["印度", "咖喱", "indian", "curry"]),
    "thai": ("泰国菜", ["泰国", "泰餐", "thai"]),
    "korean": ("韩国料理", ["韩国", "韩餐", "korean"]),
    "vietnamese": ("越南菜", ["越南", "河粉", "vietnamese", "pho"]),
    "mediterranean": ("地中海菜", ["地中海", "希腊菜", "mediterranean", "greek"]),
}


def build_product_pack(
    *, scoring_fingerprint: str, manifest: dict[str, Any], cities: list[dict[str, Any]]
) -> dict[str, Any]:
    synth = yaml.safe_load(SYNTH_PACK.read_text(encoding="utf-8"))
    pack = copy.deepcopy(synth["pack"])
    occ_cfg = yaml.safe_load(OCCUPATIONS.read_text(encoding="utf-8"))
    known = {m["code"]: o for o in pack["occupations"] for m in o["mappings"]}
    pack.update(
        pack_id="pack.e3b.us-v1",
        status="real_data_frozen",
        observed_on="2026-10-23",
        note=(
            "E3B 六城真实资料包：城市（CBSA 2023 都会区，按税务支持范围裁剪）、住房、通勤、食品、服务、设备、活动、岗位、培训与官方"
            " 2024 年税来自公开资料与登记的模型设定；生活规则与评分参数仍是合成探索设定。问卷与产品词表沿用 E3-A"
        ),
    )
    base = pack["e1_base"]
    base.update(
        life_input=f"{PACK_DIR}/life_input.json",
        scoring_config=f"{PACK_DIR}/scoring.json",
        evaluation_config=f"{PACK_DIR}/evaluation.json",
        city_facts=f"{PACK_DIR}/city_facts.json",
        protocol_id=PROTOCOL_ID,
        scoring_content_fingerprint=scoring_fingerprint,
    )
    pack["run"]["repeat_indexes"] = [0, 1, 2, 3]
    pack["cities"] = [
        {
            "city_ref": f"US:CBSA2023:{c['cbsa']}",
            "country": "US",
            "scheme": "CBSA2023",
            "code": c["cbsa"],
            "label": c["label"],
            "geography_version": "CBSA2023 / PUMA2020",
            "note": f"支持的居住与工作地按税务范围裁剪（{c['tax_policy']}）",
        }
        for c in cities
    ]
    occupations = []
    for o in occ_cfg["supported"]:
        soc = o["soc"]
        mapping = {
            "country": "US",
            "scheme": "SOC2018",
            "version": "2018",
            "code": soc,
            "basis": f"SOC 2018 职业 {soc}（E3B 支持的职业范围）",
        }
        if soc in known:
            k = copy.deepcopy(known[soc])
            k["mappings"] = [mapping]
            occupations.append(k)
        else:
            occupations.append(
                {
                    "concept_id": f"occ.soc_{soc.replace('-', '_')}",
                    "label": o["label"],
                    "duties": [o["label"]],
                    "aliases": [o["label"]],
                    "mappings": [mapping],
                }
            )
    pack["occupations"] = occupations
    pack["abilities"] = [
        {
            "ability_id": a["ability_id"],
            "label": a["label"],
            "ask_when": "只有你选的职业或候选城市里的岗位要求它时才问；系统不从职称或自由文字授予学历、证书或经验",
        }
        for a in manifest["abilities"]
    ]
    pack["interests"]["cuisines"] = [
        {"key": k, "label": CUISINE_LABELS[k][0], "aliases": CUISINE_LABELS[k][1]}
        for k in manifest["vocabulary"]["cuisines"]
    ]
    pack["catalog_scope"]["geography_scope"] = GROUP_SCOPE
    pack["catalog_scope"]["declared_scope"] = (
        "便利设备与人工服务来自真实资料包（ENERGY STAR 型号与本都会区服务职业工资），不在这里另编合成候选"
    )
    pack["facts"] = []
    pack["candidates"] = []
    pack["unsupported"] = [
        "多人家庭、非工资收入（利息、股息、副业）、家庭共同资产、买房与贷款",
        "税收抵免、税前扣除与逐项扣除；纽约市以外的纽约都会区居民、芝加哥都会区的印第安纳部分、跨州组合",
        "停车费与按距离计的公交票价；旧金山的公交票价（官方网站拒绝访问）",
        "芝加哥公园管理局的真实活动（需要日期窗口、整期报名与年龄资格，引擎暂不支持）",
        "品牌、制作方式、档次、服务质量与上门顾虑的评分（只记录原文）",
        "访谈式输入（只保留来源与证据引用接口）",
    ]
    pack["limits"] = [
        "城市资料来自公开来源与登记的模型设定；生活规则、评分与评价参数是合成探索设定：结果不排序真实城市，也不是现实推荐",
        "安全与休闲背景是六城共同先验（没有可比资料）；治疗价格是共同假设；外食、服务、设备寿命与活动价格含登记的模型设定",
        "离线规划器是可控的 mock，不代表真实大语言模型的能力或偏好理解",
        "问卷文案与参考改善范围是暂定稿，没有做认知访谈与量表验证",
    ]
    return {
        "pack_notice": (
            "real_data_frozen：E3B 六城真实资料包。城市资料来自公开来源与登记的模型设定，生活规则与评分参数是合成探索设定；"
            "不排序真实城市"
        ),
        "pack": pack,
    }


def write_product_pack(data: dict[str, Any], path: Path = REAL_PACK) -> None:
    header = (
        "# E3B 六城真实资料包的产品配置（DECISIONS/0042）。由 scripts/life_pack_us.py build 生成，不要手改：\n"
        "# 问卷与产品词表沿用 E3-A；城市、职业、能力、菜系与 E1 基础样例指向冻结的六城资料包。\n"
    )
    text = header + yaml.safe_dump(
        json.loads(json.dumps(data, default=str)), allow_unicode=True, sort_keys=False, width=1000
    )
    path.write_text(text, encoding="utf-8", newline="\n")
