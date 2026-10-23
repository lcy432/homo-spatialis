"""官方税规则（DECISIONS/0042）：编译与核对、按同一份记录计税与生成说明。

测试不联网、不读真实缓存：把配置里的摘句写成一份份假的“官方网页”放进临时缓存，核对逻辑照常跑。手算值按配置里的 2024 年规则
（单身、只有工资、标准扣除）。
"""

from __future__ import annotations

import copy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from common.money import Money
from product.realdata.fetch import _store
from product.realdata.tax_rules import TAX_CONFIG, compile_tax, numbers_in, tax_requests

CFG = yaml.safe_load(TAX_CONFIG.read_text(encoding="utf-8"))


def fake_cache(tmp: Path, cfg: dict, *, drop: str | None = None) -> Path:
    """每份来源一页：正文就是配置里引用它的全部摘句（``drop`` 那一句不写进去）。"""
    quotes: dict[str, list[str]] = {sid: [] for sid in cfg["sources"]}
    for r in cfg["rules"].values():
        for c in r["cite"]:
            quotes[c["source"]].append(c["quote"])
    for a in cfg.get("absences", {}).values():
        quotes[a["source"]].append(a["quote"])
    for sid, req in tax_requests(cfg).items():
        body = " <p> ".join(q for q in quotes[sid] if q != drop)
        _store(tmp, req, f"<html><body>{body}</body></html>".encode(), 200, "text/html")
    return tmp


@pytest.fixture(scope="module")
def build(tmp_path_factory):
    return compile_tax(CFG, fake_cache(tmp_path_factory.mktemp("tax"), CFG))


def annual(policy, wage: float) -> Decimal:
    from econ.life_settlement import OfficialRulesTax

    out = OfficialRulesTax(policy).tax(Money(wage, policy.money_basis_id), policy.applies_to, 2024)
    return Decimal(repr(out.amount)).quantize(Decimal("0.01"))


def test_every_policy_passes_the_quote_checks(build):
    assert sorted(build.policies) == [
        "tax.us2024.ca",
        "tax.us2024.il",
        "tax.us2024.nyc",
        "tax.us2024.tx",
        "tax.us2024.wa",
    ]
    assert build.blocked == {}
    for p in build.policies.values():
        assert all(r.citations for r in p.rules)
        assert all(c.url.startswith("https://") for r in p.rules for c in r.citations)


@pytest.mark.parametrize(
    ("policy", "wage", "expected"),
    [
        # 联邦 5,216（(60,000 − 14,600) 按档）+ 社保 3,720 + 医保 870 = 9,806
        ("tax.us2024.tx", 60000, "9806.00"),
        # + 纽约州 2,695（52,000 按档）+ 纽约市 1,890.69 + 残障保险 31.20（封顶）+ 家事假 223.80
        ("tax.us2024.nyc", 60000, "14646.69"),
        # + 加州 1,845.16（54,460 按档）+ 州残障保险 660
        ("tax.us2024.ca", 60000, "12311.16"),
        # + 伊利诺伊 4.95% × (60,000 − 2,775) = 2,832.64
        ("tax.us2024.il", 60000, "12638.64"),
        # + 带薪假 0.53% 318 + WA Cares 0.58% 348
        ("tax.us2024.wa", 60000, "10472.00"),
        # 社保只算 168,600 以内：6.2% × 168,600 = 10,453.20；附加医保 0.9% × (250,000 − 200,000) = 450
        # 联邦 (250,000 − 14,600) = 235,400：1,160 + 4,266 + 11,742.50 + 21,942 + 13,904 = 53,014.50；医保 3,625
        ("tax.us2024.tx", 250000, "67542.70"),
    ],
)
def test_annual_tax_by_hand(build, policy, wage, expected):
    assert annual(build.policies[policy], wage) == Decimal(expected)


def test_a_value_missing_from_its_quote_blocks_the_policy(tmp_path):
    cfg = copy.deepcopy(CFG)
    cfg["rules"]["us_federal_income"]["deduction"] = 14700
    b = compile_tax(cfg, fake_cache(tmp_path, cfg))
    assert "tax.us2024.nyc" in b.blocked and "tax.us2024.nyc" not in b.policies
    assert any("deduction = 14700" in x for x in b.blocked["tax.us2024.nyc"])
    assert not b.policies  # 每城都用联邦所得税：全部阻断，不拿合成税顶替


def test_a_quote_not_on_the_page_blocks_only_that_policy(tmp_path):
    q = CFG["rules"]["il_income"]["cite"][1]["quote"]
    b = compile_tax(CFG, fake_cache(tmp_path, CFG, drop=q))
    assert list(b.blocked) == ["tax.us2024.il"]
    assert "逐字" in b.blocked["tax.us2024.il"][0]
    assert "tax.us2024.tx" in b.policies


def test_a_missing_source_is_reported_not_replaced(tmp_path):
    cfg = copy.deepcopy(CFG)
    fake_cache(tmp_path, cfg)
    cfg["sources"]["ny_dfs_pfl_2024"]["url"] = "https://example.invalid/not-cached"
    b = compile_tax(cfg, tmp_path)
    assert "tax.us2024.nyc" in b.blocked
    assert b.sources["ny_dfs_pfl_2024"]["status"] == "not_cached"


def test_rates_are_read_as_percent_or_decimal():
    assert {Decimal("0.58"), Decimal("1.1")} <= numbers_in("is .58 percent and 1.1 percent")
    assert Decimal("1000000") in numbers_in("more than $1,000,000")


def test_visible_rules_come_from_the_same_records(build):
    from agentlayer.life_planning import visible_tax_rules

    p = build.policies["tax.us2024.nyc"]
    v = visible_tax_rules(p)
    assert v.version == "e3b-visible-tax-v1" and v.brackets == ()
    assert [c.label for c in v.components] == [r.label for r in p.rules]
    fed = v.components[0]
    assert fed.deduction == 14600 and [b.lower for b in fed.brackets][:2] == [0, 11600]
    assert "irs.gov" in fed.source
    assert any("没有建模" in line for line in v.rules)


def test_an_official_binding_must_use_the_official_function(build):
    from common.life_contracts import LifeContractError
    from econ.life_settlement import LifeTaxBinding, SyntheticBracketTax, binding_from_policy

    p = build.policies["tax.us2024.wa"]
    b = binding_from_policy(p)
    assert b.status == "official_2024_rules"
    with pytest.raises(LifeContractError, match="OfficialRulesTax"):
        LifeTaxBinding(
            function=SyntheticBracketTax(None),  # type: ignore[arg-type]
            policy_id=p.policy_id,
            status=p.status,
            money_basis_id=p.money_basis_id,
            jurisdiction=p.applies_to,
        )


def test_monthly_withholding_adds_up_to_the_annual_rule_tax(build):
    """结算按全年累计预扣（年初少扣、年底多扣）：十二个月的预扣合计 = 全年工资按同一份规则算的应缴。"""
    from common.life_contracts import CityLifeCatalog
    from econ.life_settlement import binding_from_policy, settle_year
    from ledger.life_catalog import load_life_bundle
    from tests.life.conftest import year_action

    bundle, _ = load_life_bundle(
        Path("configs/life/synthetic_product.yaml"), require_settlement_ready=True
    )
    data = bundle.catalog_for("US:SYNTH2026:K51").model_dump(mode="json")
    data["jurisdiction"] = {"path": ["US", "WA"]}
    data["tax_policy_id"] = "tax.us2024.wa"
    catalog = CityLifeCatalog.model_validate(data)
    state = bundle.initial_state("u301.k51.r0")
    items = [
        {"opportunity_id": oid, "quantity": 1, "start_month": 1, "end_month": 12}
        for oid in ("k51.housing.far", "k51.food.basic", "k51.job.clerk")
    ]
    action = year_action(state, items)
    policy = build.policies["tax.us2024.wa"]
    r = settle_year(
        protocol=bundle.protocol,
        state=state,
        action=action,
        catalog=catalog,
        tax=binding_from_policy(policy),
    )
    assert r.status == "completed"
    months = r.outcome.months
    gross = sum(Decimal(repr(m.income_gross.amount)) for m in months)
    withheld = sum(Decimal(repr(m.income_tax.amount)) for m in months)
    assert withheld == annual(policy, float(gross))
    assert months[0].income_tax.amount < months[-1].income_tax.amount  # 越过更高一档之后扣得多
