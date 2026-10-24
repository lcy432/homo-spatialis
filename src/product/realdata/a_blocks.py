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

**报价接线**（E3B 收尾 §4.1）是一条单向链：基础成本预测（``life_items``）→ 本地报价清单
（``configs/intake/local_quotes_us_v1.yaml``，``load_local_quotes`` 校验、按城市与编号去重）→ 同规格同口径的报价做一次零售修正
（``apply_local_quotes``）→ 执行价写进机会的 ``price`` → 冻结、目录与 E1 结算都读这一个价。修正记录按规格交给 ``city_blocks``
汇总，不再重算，所以报告的价与执行的价是同一个。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, replace
from decimal import Decimal, InvalidOperation
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


# --------------------------------------------------------------------------- 本地报价清单与接线（E3B 收尾 §4.1）

#: 每条本地报价的字段（含义见 ``configs/intake/local_quotes_us_v1.yaml``），全部必填
QUOTE_FIELDS = (
    "city",
    "quote_id",
    "supplier",
    "spec",
    "amount",
    "currency",
    "base_year",
    "period",
    "billing",
    "per_unit",
    "tax_basis",
    "bundle",
    "source",
    "locator",
)
QUOTE_ASSUMPTION = "local_quote_comparability_v1"
_PERIOD = re.compile(r"^(\d{4})(-\d{2}(-\d{2})?)?$")


class QuoteError(ValueError):
    """本地报价清单不合规则，或报价接不到一个可修正的机会上：拒绝构建。"""


@dataclass(frozen=True)
class LocalQuote:
    """本地报价清单里的一条。"""

    city: str
    quote_id: str
    supplier: str
    spec: str
    amount: Decimal
    currency: str
    base_year: int
    period: str
    billing: str
    per_unit: str
    tax_basis: str
    bundle: str
    source: str
    locator: str

    def basis(self) -> dict[str, str]:
        """参与同一次更新必须完全相同的口径（不做任何换算）。"""
        return {
            "numeraire": f"{self.currency}_{self.base_year}_LOCAL",
            "billing": self.billing,
            "per_unit": self.per_unit,
            "tax_basis": self.tax_basis,
            "bundle": self.bundle,
        }

    def as_dict(self) -> dict[str, Any]:
        return {k: str(v) if isinstance(v, Decimal) else v for k, v in asdict(self).items()}


def _basis_key(spec: str, basis: dict[str, str]) -> str:
    return "|".join([spec, *(f"{k}={basis[k]}" for k in sorted(basis))])


def _quote(raw: Any, n: int, taxes: set[str], bundles: set[str]) -> LocalQuote:
    where = f"本地报价第 {n + 1} 条"
    if not isinstance(raw, dict):
        raise QuoteError(f"{where}不是映射")
    missing = [k for k in QUOTE_FIELDS if raw.get(k) is None or str(raw[k]).strip() == ""]
    extra = sorted(set(raw) - set(QUOTE_FIELDS))
    if missing or extra:
        raise QuoteError(f"{where}：缺字段 {missing}，不认识的字段 {extra}")
    t = {k: str(raw[k]).strip() for k in QUOTE_FIELDS}
    where = f"{where}（城市 {t['city']}、编号 {t['quote_id']}）"
    try:
        amount = Decimal(t["amount"])
    except InvalidOperation as e:
        raise QuoteError(f"{where}：金额 {t['amount']!r} 不是十进制数") from e
    if not amount.is_finite() or amount != amount.quantize(Decimal("0.01")):
        raise QuoteError(f"{where}：金额要是有限的十进制数、精确到分，得到 {t['amount']!r}")
    if amount < 0:
        raise QuoteError(f"{where}：负价拒绝（退款或补贴不是报价）")
    if not re.fullmatch(r"[A-Z]{3}", t["currency"]) or not re.fullmatch(r"\d{4}", t["base_year"]):
        raise QuoteError(f"{where}：币种要是三个大写字母、base_year 要是四位年份")
    m = _PERIOD.match(t["period"])
    if m is None or m.group(1) != t["base_year"]:
        raise QuoteError(
            f"{where}：观测时期 {t['period']!r} 的年份要等于 base_year {t['base_year']}（跨年要登记的时期换算，现在没有）"
        )
    if t["tax_basis"] not in taxes or t["bundle"] not in bundles:
        raise QuoteError(
            f"{where}：税费口径 {t['tax_basis']!r} 或捆绑口径 {t['bundle']!r} 不在登记的取值里"
        )
    return LocalQuote(
        **{k: t[k] for k in QUOTE_FIELDS if k not in ("amount", "base_year")},
        amount=amount.quantize(Decimal("0.01")),
        base_year=int(t["base_year"]),
    )


def load_local_quotes(cfg: dict[str, Any]) -> dict[str, tuple[LocalQuote, ...]]:
    """校验本地报价清单并按城市与编号去重：同城同编号同内容只算一次，同编号不同内容拒绝。返回 {城市: 按编号排序的报价}。"""
    taxes, bundles = set(cfg["tax_basis_values"]), set(cfg["bundle_values"])
    basis = cfg["comparable_basis"]
    if set(basis) != {"tax_basis", "bundle"} or (
        basis["tax_basis"] not in taxes or basis["bundle"] not in bundles
    ):
        raise QuoteError(f"comparable_basis 要写登记取值里的 tax_basis 与 bundle，得到 {basis}")
    seen: dict[tuple[str, str], LocalQuote] = {}
    for n, raw in enumerate(cfg.get("quotes") or []):
        q = _quote(raw, n, taxes, bundles)
        key = (q.city, q.quote_id)
        if key in seen and seen[key] != q:
            raise QuoteError(
                f"城市 {q.city} 的报价编号 {q.quote_id} 出现两次且内容不同：拒绝（一个稳定编号只能对应一份报价）"
            )
        seen[key] = q
    out: dict[str, list[LocalQuote]] = {}
    for (city, _), q in sorted(seen.items()):
        out.setdefault(city, []).append(q)
    return {city: tuple(v) for city, v in out.items()}


def _member(oid: str, fam: dict[str, Any]) -> bool:
    local = _local(oid)
    return any(local.startswith(x) for x in fam["id_prefixes"]) and not any(
        local.startswith(x) for x in fam.get("exclude_prefixes", ())
    )


def _family_of(item: Any, declarations: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    for d in declarations:
        if item.domain in d["domains"]:
            for fam in d["families"]:
                if _member(item.opportunity.opportunity_id, fam):
                    return fam
    return None


def _priced(item: Any, record: dict[str, Any]) -> Any:
    """把零售修正的执行价写进机会：价格、价格口径属性、可见说明与来源都改成实际采用的这一支（契约重新校验）。"""
    o = item.opportunity
    base, final, n = record["p_base"], record["execution_price"], record["suppliers"]
    data = o.model_dump(mode="json")
    data["price"]["amount"]["amount"] = float(Decimal(final))
    data["attributes"] = {
        **data["attributes"],
        "cost_basis": "local_quote_retail_correction",
        "base_cost_basis": data["attributes"].get("cost_basis", "base_cost_prediction"),
        "retail_base_price": base,
        "local_quote_suppliers": n,
        "observed_unit_price": False,
    }
    note = f"；价格按 {n} 家本地同规格报价做零售修正：基础预测 {base} → {final}（后验中位数，不是某一家的报价）"
    data["quality"] = data["quality"][: 280 - len(note)] + note
    src = data["source"]
    src["kind"] = "estimated"
    src["inputs"] = [
        *src.get("inputs", []),
        *(f"local_quote:{q}"[:200] for q in record["comparable_quote_ids"]),
    ]
    src["rule"] = (
        f"{RETAIL_RULE}：基础预测 {base} × exp(后验均值 {record['posterior']['mean']:.6f})，{n} 家供应商的同规格同口径"
        f"本地报价，四舍五入到分 = {final}；基础预测：{src.get('rule') or '既有成本公式'}"
    )[:400]
    return replace(
        item,
        opportunity=type(o).model_validate(data),
        assumptions=(*item.assumptions, RETAIL_RULE, QUOTE_ASSUMPTION),
    )


def apply_local_quotes(
    items: Sequence[Any],
    quotes: Sequence[LocalQuote],
    declarations: Iterable[dict[str, Any]],
    basis: dict[str, str],
) -> tuple[list[Any], dict[str, dict[str, Any]]]:
    """把一城的本地报价接进机会价格。每个有报价的规格做一次零售修正：只有规格相同、口径（计价口径、计费周期、计价单位，以及
    ``basis`` 给出的税费与捆绑口径）完全相同的报价参与；``updated`` 时把执行价写进机会的 ``price``，只应用这一次。返回（机会，
    按规格的修正记录：基础价、合格报价、先验与后验、最终价、未更新原因）。没有报价时机会原样返回。"""
    out = list(items)
    if not quotes:
        return out, {}
    declarations = list(declarations)
    index = {_local(i.opportunity.opportunity_id): n for n, i in enumerate(out)}
    by_spec: dict[str, list[LocalQuote]] = {}
    for q in quotes:
        by_spec.setdefault(q.spec, []).append(q)
    records: dict[str, dict[str, Any]] = {}
    for spec, qs in sorted(by_spec.items()):
        where = f"城市 {qs[0].city} 的报价 {[q.quote_id for q in qs]}（规格 {spec}）"
        n = index.get(spec)
        if n is None:
            raise QuoteError(f"{where}：本城没有这个机会")
        item = out[n]
        o, fam = item.opportunity, _family_of(item, declarations)
        if fam is None:
            raise QuoteError(f"{where}：这个机会不属于任何 A 方案族，零售修正不适用")
        if fam.get("direct_quotes"):
            raise QuoteError(f"{where}：这个机会的价格本身就是直接报价，原样保留，不做零售修正")
        if o.price is None:
            raise QuoteError(
                f"{where}：这个机会没有基础预测价，零售修正无从更新（未知不当零，也不拿报价直接填）"
            )
        target = {
            "numeraire": o.price.amount.numeraire,
            "billing": o.price.billing,
            "per_unit": o.price.per_unit,
            **basis,
        }
        r = retail_update(
            Decimal(str(o.price.amount.amount)),
            [DirectQuote(q.supplier, _basis_key(q.spec, q.basis()), q.amount) for q in qs],
            _basis_key(spec, target),
        )
        record = {
            **r,
            "spec": spec,
            "basis": target,
            "comparable_quote_ids": [q.quote_id for q in qs if q.basis() == target],
            "not_comparable": [
                {
                    "quote_id": q.quote_id,
                    "differs_in": sorted(k for k in target if q.basis()[k] != target[k]),
                }
                for q in qs
                if q.basis() != target
            ],
            "quotes": [q.as_dict() for q in qs],
            "final_price": r["execution_price"],
        }
        if r["status"] == "updated":
            out[n] = _priced(item, record)
        records[spec] = record
    return out, records


def family_update(
    priced: Sequence[tuple[str, Decimal]], corrections: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """一个方案族的零售修正汇总。``corrections`` 是 ``apply_local_quotes`` 按规格（机会编号去掉城市前缀）给出的修正记录，原样
    采用、不重算（机会里的价已经是它的最终价）；没有记录的规格没有任何报价，状态按基础价判定。有报价的规格把整条记录列在
    ``spec_updates``；一族都没有报价时输出与没有报价清单时相同。"""
    results = {
        spec: corrections[spec] if spec in corrections else retail_update(price, [], spec)
        for spec, price in priced
    }
    status = Counter(r["status"] for r in results.values())
    out: dict[str, Any] = {
        "rule": RETAIL_RULE,
        "prior": {
            "distribution": "normal",
            "mean": 0.0,
            "sd": PRIOR_SD,
            "of": "log(P_actual / P_base)",
        },
        "specs": len(priced),
        "by_status": dict(sorted(status.items())),
        "comparable_quotes": sum(len(r["direct_quotes"]) for r in results.values()),
        "note_zh": NO_UPDATE_ZH
        if not status.get("updated")
        else f"{status['updated']} 个规格按同规格报价更新",
    }
    quoted = {spec: r for spec, r in sorted(results.items()) if spec in corrections}
    if quoted:
        out["spec_updates"] = quoted
    return out


def city_blocks(
    declarations: Iterable[dict[str, Any]],
    *,
    items: Sequence[Any],
    report_entries: Sequence[dict[str, Any]],
    evidence: dict[str, dict[str, Any]],
    corrections: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """一城的 A 板块结构：静态声明 + 这座城的证据、零售修正与状态。``items`` 是编译好的机会（``Item``，价格已经是最终价），
    ``evidence`` 按板块给出本城实际用到的证据值（工资、RPP、商品类换算系数、场所家数等），``corrections`` 是
    ``apply_local_quotes`` 按规格给出的修正记录（现在六城一条报价也没有）。"""
    corrections = corrections or {}
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
            members = [i for i in mine if _member(i.opportunity.opportunity_id, fam)]
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
                else family_update(priced, corrections)
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
