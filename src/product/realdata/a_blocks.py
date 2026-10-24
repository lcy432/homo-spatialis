"""A 板块的共同结构与价格更新（E3B 映射修正 §4）。

每个 A 板块（食品、杂务与享受服务、设备、爱好装备、活动……）统一保存：方案族与合法组合规则；参数的含义、单位、支持范围；共同
先验及来源或假设；城市证据与允许更新的参数；更新结果与未更新的原因；机会之间的真实差异与参数估计不确定性的区分；编译、资料
覆盖与模型支持状态。板块的静态声明在 ``configs/intake/life_items_us_v1.yaml`` 的 ``a_blocks``，这里按城市补上证据与状态。

A 的覆盖网格不是市场份额：没有供给抽样资料，不声称知道某个方案的真实出现概率。

**零售价修正**（``retail_correction_lognormal_v1``）：既有成本公式给出基础预测价 ``P_base``；``δ = log(P_actual / P_base)``，共同
先验 ``δ ~ N(0, log(1.5)²)``。同规格直接报价先按供应商去重，每家供应商对同一方案族只给一个对数比值（同一家的多条报价取对数比值
的平均），不能靠重复抓取增加证据量；本地均值观测的方差 = ``max(s²/n, log(1.1)²)``，只有一家时 ``log(1.5)²``；按正态共轭公式
更新，执行价用后验中位数 ``P_base × exp(后验均值)``。直接报价本身另存，不被预测值覆盖；零价格单独处理，不取对数；规格、捆绑、
税费或时期不同的报价不合并。这些方差是模型设定，不是从报价估计出来的。没有合格报价时报告“未发生本地报价更新”，执行价就是
基础预测，标为估计，不叫本地观测价。工资、RPP 只更新对应的成本预测，不更新服务效果。
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

RETAIL_RULE = "retail_correction_lognormal_v1"
PRIOR_SD = math.log(1.5)
MIN_OBSERVATION_SD = math.log(1.1)
SINGLE_SUPPLIER_SD = math.log(1.5)
NO_UPDATE_ZH = "未发生本地报价更新"
COVERAGE_NOTE_ZH = "A 的覆盖网格不是市场份额：没有供给抽样资料，不声称知道某个方案的真实出现概率"


@dataclass(frozen=True)
class DirectQuote:
    """一条同规格直接报价。``spec`` 是可比较的规格键（方案族、规格、计价单位、时期、税费口径都相同才算同一规格）。"""

    supplier: str
    spec: str
    amount: Decimal


def retail_update(p_base: Decimal, quotes: Sequence[DirectQuote], spec: str) -> dict[str, Any]:
    """一个方案族在一座城的零售价修正（见模块说明）。返回先验、观测、后验与执行价；不改直接报价本身。"""
    prior = {"distribution": "normal", "mean": 0.0, "sd": PRIOR_SD, "of": "log(P_actual / P_base)"}
    comparable = [q for q in quotes if q.spec == spec]
    out: dict[str, Any] = {
        "rule": RETAIL_RULE,
        "p_base": str(p_base),
        "prior": prior,
        "quotes_not_comparable": len(quotes) - len(comparable),
        "zero_price_quotes": sum(1 for q in comparable if q.amount <= 0),
        "direct_quotes": [
            {"supplier": q.supplier, "amount": str(q.amount)}
            for q in sorted(comparable, key=lambda q: (q.supplier, q.amount))
        ],
    }
    if p_base <= 0:
        return {
            **out,
            "status": "zero_base_price",
            "note_zh": "基础预测价为 0：零价格单独处理，不取对数、不做修正",
            "execution_price": str(p_base),
        }
    logs: dict[str, list[float]] = {}
    for q in comparable:
        if q.amount > 0:
            logs.setdefault(q.supplier, []).append(math.log(float(q.amount / p_base)))
    ratios = [math.fsum(v) / len(v) for _, v in sorted(logs.items())]
    n = len(ratios)
    if n == 0:
        return {
            **out,
            "status": "no_local_quotes",
            "note_zh": NO_UPDATE_ZH,
            "suppliers": 0,
            "posterior": prior,
            "execution_price": str(p_base),
        }
    mean = math.fsum(ratios) / n
    if n == 1:
        variance = SINGLE_SUPPLIER_SD**2
    else:
        s2 = math.fsum((r - mean) ** 2 for r in ratios) / (n - 1)
        variance = max(s2 / n, MIN_OBSERVATION_SD**2)
    post_var = 1 / (1 / PRIOR_SD**2 + 1 / variance)
    post_mean = post_var * mean / variance
    execution = (p_base * Decimal(repr(math.exp(post_mean)))).quantize(Decimal("0.01"))
    return {
        **out,
        "status": "updated",
        "suppliers": n,
        "observation": {"mean": mean, "variance": variance},
        "posterior": {"distribution": "normal", "mean": post_mean, "sd": math.sqrt(post_var)},
        "execution_price": str(execution),
        "note_zh": f"按 {n} 家供应商的同规格报价更新；执行价 = 基础预测 × exp(后验均值)",
    }


def _local(oid: str) -> str:
    """机会编号去掉城市前缀（``chi.food.staples.h21`` → ``food.staples.h21``）。"""
    return oid.split(".", 1)[1]


def family_update(
    priced: Sequence[tuple[str, Decimal]], quotes: dict[str, list[DirectQuote]]
) -> dict[str, Any]:
    """一个方案族里每个规格（每个机会）各自做零售修正，汇总状态。``quotes`` 按规格（机会编号去掉城市前缀）给出同规格直接报价。"""
    status = Counter(
        retail_update(price, quotes.get(spec, []), spec)["status"] for spec, price in priced
    )
    return {
        "rule": RETAIL_RULE,
        "prior": {
            "distribution": "normal",
            "mean": 0.0,
            "sd": PRIOR_SD,
            "of": "log(P_actual / P_base)",
        },
        "specs": len(priced),
        "by_status": dict(sorted(status.items())),
        "comparable_quotes": sum(len(quotes.get(spec, [])) for spec, _ in priced),
        "note_zh": NO_UPDATE_ZH
        if not status.get("updated")
        else f"{status['updated']} 个规格按同规格报价更新",
    }


def city_blocks(
    declarations: Iterable[dict[str, Any]],
    *,
    items: Sequence[Any],
    report_entries: Sequence[dict[str, Any]],
    evidence: dict[str, dict[str, Any]],
    quotes: dict[str, list[DirectQuote]] | None = None,
) -> list[dict[str, Any]]:
    """一城的 A 板块结构：静态声明 + 这座城的证据、零售修正与状态。``items`` 是编译好的机会（``Item``），``evidence`` 按板块给出
    本城实际用到的证据值（工资、RPP、商品类换算系数、场所家数等），``quotes`` 按规格给出同规格直接报价（现在一条也没有）。"""
    quotes = quotes or {}
    out = []
    for d in declarations:
        domains = set(d["domains"])
        mine = [i for i in items if i.domain in domains]
        by_status = Counter(i.status for i in mine)
        gaps = [
            {"key": e["key"], "kind": e["kind"], "reason_zh": e["reason_zh"], "count": e["count"]}
            for e in report_entries
            if e["domain"] in domains
        ]
        families = []
        for fam in d["families"]:
            members = [
                i
                for i in mine
                if any(
                    _local(i.opportunity.opportunity_id).startswith(x) for x in fam["id_prefixes"]
                )
                and not any(
                    _local(i.opportunity.opportunity_id).startswith(x)
                    for x in fam.get("exclude_prefixes", ())
                )
            ]
            priced = [
                (
                    _local(i.opportunity.opportunity_id),
                    Decimal(str(i.opportunity.price.amount.amount)),
                )
                for i in members
                if i.opportunity.price is not None
            ]
            # 这一族的价格本身就是直接报价（例如芝加哥公园管理局的整期报名费）：原样保留，不做修正、不被预测值覆盖
            update = (
                {
                    "rule": RETAIL_RULE,
                    "specs": len(priced),
                    "by_status": {"direct_quotes_kept": len(priced)},
                    "comparable_quotes": len(priced),
                    "note_zh": "价格本身就是直接报价，原样保留，不做修正",
                }
                if fam.get("direct_quotes")
                else family_update(priced, quotes)
            )
            families.append(
                {
                    "family": fam["family"],
                    "label_zh": fam["label_zh"],
                    "opportunities": len(members),
                    "priced": len(priced),
                    "retail_correction": {
                        **update,
                        "why_no_quotes_zh": fam["why_no_quotes_zh"]
                        if not update["by_status"].get("updated")
                        else None,
                    },
                }
            )
        out.append(
            {
                "block": d["block"],
                "label_zh": d["label_zh"],
                "legal_combinations_zh": d["legal_combinations_zh"],
                "parameters": d["parameters"],
                "city_evidence": evidence.get(d["block"], {}),
                "families": families,
                "variation_vs_uncertainty_zh": d["variation_vs_uncertainty_zh"],
                "sensitivity_zh": d.get(
                    "sensitivity_zh",
                    "默认运行用中心参数；零售修正与参数不确定性只进独立的敏感性情景（同城所有分身共用同一组值），不生成额外机会",
                ),
                "coverage_note_zh": COVERAGE_NOTE_ZH,
                "status": {
                    "compiled": dict(sorted(by_status.items())),
                    "data_gaps": gaps,
                    "model_support_zh": d["model_support_zh"],
                },
            }
        )
    return out
