"""官方税规则的编译与核对（DECISIONS/0042）。

``configs/intake/tax_us_2024.yaml`` 写 2024 年的官方规则：每条规则的数与它们在官方网页里的摘句。这里离线做三件事：

1. 从原始文件缓存读每份官方网页（``fetch`` 的同一个缓存与 SHA-256 核对）；缺了、或被拒的来源照实报告；
2. 核对每条摘句**逐字**出现在那份网页的正文里（去掉脚本与标签、还原实体、合并空白之后）；
3. 核对规则里的每个数都出现在它的某条摘句里：金额按数字（逗号可有可无），税率按百分数或小数；用文字写的数要在
   ``word_values`` 里写明读法并核对那串文字；由摘句换算出来的数写 ``derivation``，不在这里核对数字。

全部通过的策略才生成 ``RealTaxPolicy``；任何一条不过，这一城的税就是阻断（列出原因），不拿合成税顶替。规则的数是人读出来、
机器核对过的，不由模型生成。
"""

from __future__ import annotations

import datetime as _dt
import html
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from common.ids import Jurisdiction
from common.life_contracts import (
    RealTaxPolicy,
    TaxAbsence,
    TaxBracket,
    TaxCitation,
    TaxRule,
)
from product.realdata.fetch import DEFAULT_CACHE, Fetched, Request, fetch

TAX_CONFIG = Path("configs/intake/tax_us_2024.yaml")
SOURCE_ID = "official_tax_2024"
_NUMBER = re.compile(r"(?<![\w.])(?:\d[\d,]*(?:\.\d+)?|\.\d+)")


def page_text(content: bytes) -> str:
    """网页正文：去掉脚本与样式、去掉标签、还原 HTML 实体、合并空白。摘句按这份文字逐字核对。"""
    t = content.decode("utf-8", "replace")
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


def numbers_in(quote: str) -> set[Decimal]:
    """摘句里出现的数（去掉千位逗号）。"""
    return {Decimal(m.group(0).replace(",", "")) for m in _NUMBER.finditer(quote)}


def tax_requests(cfg: dict[str, Any]) -> dict[str, Request]:
    return {
        sid: Request(source_id=SOURCE_ID, url=s["url"], expect="any", timeout=60)
        for sid, s in cfg["sources"].items()
    }


@dataclass
class TaxBuild:
    """编译结果：通过核对的策略、每个策略的阻断原因、每份来源的获取记录。"""

    policies: dict[str, RealTaxPolicy] = field(default_factory=dict)
    blocked: dict[str, list[str]] = field(default_factory=dict)
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)


def _value_ok(name: str, value: float, numbers: set[Decimal], *, rate: bool) -> bool:
    v = Decimal(repr(float(value)))
    if v in numbers:
        return True
    return rate and (v * 100).normalize() in {n.normalize() for n in numbers}


def _rule_values(r: dict[str, Any]) -> list[tuple[str, float, bool]]:
    """规则里要核对的数：（名字，值，是不是税率）。第一档下限 0 与 0 税率不核对。"""
    out: list[tuple[str, float, bool]] = []
    if r.get("deduction"):
        out.append(("deduction", r["deduction"], False))
    for i, b in enumerate(r.get("brackets", ())):
        if b["lower"]:
            out.append((f"brackets[{i}].lower", b["lower"], False))
        if b["rate"]:
            out.append((f"brackets[{i}].rate", b["rate"], True))
    for key, is_rate in (("rate", True), ("wage_cap", False), ("threshold", False)):
        if r.get(key) is not None:
            out.append((key, r[key], is_rate))
    if r.get("annual_max") is not None:
        out.append(("annual_max", r["annual_max"], False))
    return out


def _citation(
    src: dict[str, Any], fetched: Fetched, text: str, quote: str
) -> tuple[TaxCitation | None, str | None]:
    if quote not in text:
        return None, f"摘句没有逐字出现在 {src['url']} 的正文里：{quote[:80]!r}"
    rec = fetched.record
    retrieved = rec.retrieved_at
    day = (
        _dt.date.fromisoformat(str(retrieved)[:10])
        if retrieved
        else _dt.date.fromisoformat("1970-01-01")
    )
    assert rec.raw_sha256 is not None
    return (
        TaxCitation(
            publisher=src["publisher"],
            url=src["url"],
            retrieved_on=day,
            raw_sha256=rec.raw_sha256,
            quote=quote,
        ),
        None,
    )


def compile_tax(
    cfg: dict[str, Any] | None = None, cache: Path = DEFAULT_CACHE, *, mode: str = "offline"
) -> TaxBuild:
    """离线编译与核对全部官方税策略。``mode`` 只给显式的获取命令用；构建与测试一律离线。"""
    if cfg is None:
        cfg = yaml.safe_load(TAX_CONFIG.read_text(encoding="utf-8"))
    out = TaxBuild()
    fetched: dict[str, Fetched] = {}
    texts: dict[str, str] = {}
    for sid, req in tax_requests(cfg).items():
        f = fetch(req, cache, mode)  # type: ignore[arg-type]
        fetched[sid] = f
        out.sources[sid] = f.record.model_dump(mode="json")
        if f.ok:
            assert f.content is not None
            texts[sid] = page_text(f.content)

    rules: dict[str, TaxRule] = {}
    rule_problems: dict[str, list[str]] = {}
    for rid, r in cfg["rules"].items():
        problems: list[str] = []
        cites: list[TaxCitation] = []
        quotes: list[str] = []
        for c in r["cite"]:
            sid = c["source"]
            if sid not in texts:
                problems.append(f"来源 {sid} 没有可用的原始网页（{out.sources[sid]['status']}）")
                continue
            cite, why = _citation(cfg["sources"][sid], fetched[sid], texts[sid], c["quote"])
            if why is not None:
                problems.append(why)
            else:
                assert cite is not None
                cites.append(cite)
                quotes.append(c["quote"])
        words = r.get("word_values", {})
        numbers = set().union(*(numbers_in(q) for q in quotes)) if quotes else set()
        for name, value, is_rate in _rule_values(r):
            if name in words:
                if not any(words[name] in q for q in quotes):
                    problems.append(f"{name} 的文字读法 {words[name]!r} 不在摘句里")
                continue
            if name == "annual_max" and r.get("derivation"):
                continue  # 由摘句换算（derivation 写明怎么算的）
            if not _value_ok(name, value, numbers, rate=is_rate):
                problems.append(f"{name} = {value} 没有出现在摘句里")
        if problems:
            rule_problems[rid] = problems
            continue
        rules[rid] = TaxRule(
            rule_id=f"{cfg['version']}.{rid}".replace("_", "-"),
            label=r["label"],
            level=r["level"],
            kind=r["kind"],
            deduction=float(r.get("deduction", 0)),
            brackets=tuple(
                TaxBracket(lower=float(b["lower"]), rate=float(b["rate"]))
                for b in r.get("brackets", ())
            ),
            rate=r.get("rate"),
            wage_cap=r.get("wage_cap"),
            threshold=r.get("threshold"),
            annual_max=r.get("annual_max"),
            derivation=r.get("derivation"),
            citations=tuple(cites),
            note=r.get("note", ""),
        )

    absences: dict[str, TaxAbsence] = {}
    absence_problems: dict[str, str] = {}
    for aid, a in cfg.get("absences", {}).items():
        sid = a["source"]
        if sid not in texts:
            absence_problems[aid] = f"来源 {sid} 没有可用的原始网页"
            continue
        cite, why = _citation(cfg["sources"][sid], fetched[sid], texts[sid], a["quote"])
        if why is not None:
            absence_problems[aid] = why
            continue
        assert cite is not None
        absences[aid] = TaxAbsence(label=a["label"], citation=cite)

    for p in cfg["policies"]:
        pid = p["policy_id"]
        blocked = [f"{rid}: {why}" for rid in p["rules"] for why in rule_problems.get(rid, [])]
        blocked += [f"{rid}: 规则没有定义" for rid in p["rules"] if rid not in cfg["rules"]]
        blocked += [
            f"{aid}: {absence_problems[aid]}"
            for aid in p.get("absences", ())
            if aid in absence_problems
        ]
        if blocked:
            out.blocked[pid] = blocked
            continue
        out.policies[pid] = RealTaxPolicy(
            policy_id=pid,
            status="official_2024_rules",
            money_basis_id=cfg["money_basis_id"],
            applies_to=Jurisdiction(path=tuple(p["applies_to"])),
            rules=tuple(rules[rid] for rid in p["rules"]),
            absent_taxes=tuple(absences[aid] for aid in p.get("absences", ())),
            residence_scope=p["residence_scope"],
            not_modeled=tuple(cfg.get("federal_not_modeled", ())) + tuple(p["not_modeled"]),
            note=p["note"],
        )
    return out
