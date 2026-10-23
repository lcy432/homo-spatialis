"""E3B 六城资料包的组装（DECISIONS/0042）：组装规则的单元测试，与对已冻结、已提交的资料包的整体检查。

整体检查只读 ``data/frozen/life/us-v1/pack``（进仓库的冻结产物），不读 intake 冻结库或原始缓存。
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from product.realdata.life_assembly import (
    GROUP_SCOPE,
    _rescope,
    background_numbers,
    load_pack_config,
    modal_work_area,
)

PACK = Path("data/frozen/life/us-v1/pack")
CFG = load_pack_config()


def test_jobs_go_to_the_modal_work_area_of_their_occupation_group():
    shares = {
        "all": {"shares": {"x.area.w.a": "0.6", "x.area.w.b": "0.4"}},
        "by_soc_major": {"15": {"shares": {"x.area.w.a": "0.3", "x.area.w.b": "0.7"}}},
    }
    assert modal_work_area(shares, "15-1252") == "x.area.w.b"
    assert modal_work_area(shares, "43-9061") == "x.area.w.a"  # 没有这一大类：看全体


def test_every_source_gets_the_group_label_and_keeps_its_detail():
    src = {"kind": "estimated", "geography_scope": "PUMA 1", "inputs": ["a"], "rule": "r"}
    out = _rescope({"source": src, "areas": [{"typical_source": dict(src)}]})
    assert out["source"]["geography_scope"] == GROUP_SCOPE
    assert out["source"]["inputs"] == ["a", "geography: PUMA 1"]
    assert out["areas"][0]["typical_source"]["geography_scope"] == GROUP_SCOPE


def test_background_numbers_by_hand():
    items = {
        "items": [
            {"domain": "job", "opportunity": {"occupation": {"code": c}}, "status": "needs_area"}
            for c in ("11-1021", "43-9061")
        ]
        + [
            {
                "domain": "training",
                "status": "executable",
                "opportunity": {"grants": ["soc_43_9061_award"]},
            }
        ]
    }
    n = background_numbers(CFG, {"share_good_days": {"value": "0.5589"}}, items)
    assert n["environment"] == (
        Decimal("5.589"),
        Decimal("2.7945"),
    )  # 指数 = 10 × 好天比例，b = 0.5 × 指数
    # 两个支持职业都有岗位、一个有培训：b = 2 × 1 + 2 × 0.5 = 3，指数 6
    assert n["opportunity"] == (Decimal("6.000"), Decimal("3.000"))
    assert n["leisure"] == (Decimal(2), Decimal(1))  # 共同休闲背景 b = 1（所有者 2026-10-23）
    assert n["safety"] is None  # 没有给治安资料：未知，不当零犯罪，也不用他城平均填补
    assert background_numbers(CFG, {}, items)["environment"] is None  # 没有 AQI：未知，不当 0
    from product.realdata.safety import CrimeCount, SafetyInput

    national = CrimeCount(population=1000000, violent=4000, property=20000)
    s = SafetyInput("x", CrimeCount(100000, 400, 2000), "整个都会区", "observed", "100%")
    n = background_numbers(CFG, {}, items, s, national)
    assert n["safety"] == (Decimal("6.000"), Decimal("3.000"))  # 与全国一样：r = 1，指数 6，b = 3


# --------------------------------------------------------------------------- 冻结资料包的整体检查


@pytest.fixture(scope="module")
def loaded():
    from ledger.life_catalog import load_life_bundle
    from ledger.life_city_facts import load_city_facts
    from ledger.life_evaluation_config import load_evaluation_config
    from ledger.life_scoring_config import load_scoring_config

    bundle, _ = load_life_bundle(PACK / "life_input.json", require_settlement_ready=True)
    scoring, _ = load_scoring_config(PACK / "scoring.json", bundle)
    facts = load_city_facts(PACK / "city_facts.json", bundle, scoring)
    ev = load_evaluation_config(PACK / "evaluation.json", bundle, scoring)
    return bundle, scoring, facts, ev


def test_the_frozen_files_match_the_report(loaded):
    rep = json.loads((PACK / "report.json").read_text(encoding="utf-8"))
    for name, digest in rep["files"].items():
        assert hashlib.sha256((PACK / name).read_bytes()).hexdigest() == digest
    assert rep["tax_blocked"] == {}


def test_six_cities_with_official_tax_menus_areas_and_commute(loaded):
    bundle, *_ = loaded
    assert len(bundle.catalogs) == 6
    policies = {p.policy_id: p for p in bundle.tax_policies}
    for cat in bundle.catalogs:
        assert policies[cat.tax_policy_id].status == "official_2024_rules"
        assert cat.commute is not None and cat.areas
        years = {(m.category, m.year, m.env_repeat) for m in cat.menus}
        assert years == {(c, y, e) for c in ("housing", "job") for y in (1, 2, 3) for e in (1, 2)}
        homes = [o for o in cat.opportunities if o.category == "housing"]
        assert all(o.area is not None and cat.area(o.area) is not None for o in homes)
        jobs = [o for o in cat.opportunities if o.category == "job"]
        assert jobs and all(o.area is not None for o in jobs)


def test_scoring_reads_area_values_and_the_group_is_six_real_cities(loaded):
    _, scoring, facts, _ = loaded
    g = scoring.comparison_group
    assert g.group_id == "group.e3b-us-v1" and len(g.cities) == 6
    for city in g.cities:
        cond = scoring.conditions_for(str(city))
        safety = cond.condition("safety")
        assert (
            safety.area_bases and len(set(safety.area_bases.values())) == 1
        )  # 都会区共同值：各片区同值
    assert facts is not None


def test_safety_inputs_are_traceable_per_metro_and_shared_by_areas(loaded):
    _, scoring, *_ = loaded
    rep = json.loads((PACK / "report.json").read_text(encoding="utf-8"))
    nat = rep["safety_national_2024"]
    # FBI CIUS 2024 表 1 的数与人口重算的率（表里印的是取到 0.1 的 359.1、1760.1）
    assert (nat["violent_rate"], nat["property_rate"]) == ("359.10", "1760.13")
    inputs = rep["safety_inputs"]
    assert set(inputs) == {"nyc", "la", "sf", "chi", "hou", "sea"}
    assert inputs["nyc"]["scope"].startswith("纽约都会区的纽约州部分")  # 部分覆盖照实标出
    assert all(
        inputs[k]["scope"].startswith("整个都会区") for k in ("la", "sf", "chi", "hou", "sea")
    )
    for city in scoring.comparison_group.cities:
        key = {
            "35620": "nyc",
            "31080": "la",
            "41860": "sf",
            "16980": "chi",
            "26420": "hou",
            "42660": "sea",
        }[city.code]
        safety = scoring.conditions_for(str(city)).condition("safety")
        assert set(safety.area_bases.values()) == {float(inputs[key]["b"])}  # 城内各片区同值
        leisure = scoring.conditions_for(str(city)).condition("leisure")
        assert leisure.base == 1


def test_internal_safety_parameters_stay_out_of_the_agent_view(loaded):
    from agentlayer.life_planning import visible_city_facts

    _, _, facts, _ = loaded
    for sheet in facts.sheets:
        text = json.dumps(
            [f.model_dump(mode="json") for f in visible_city_facts(sheet)], ensure_ascii=False
        )
        for secret in ("w_violent", "fbi_relative_rate", "national_index", "0.5 ×", "b = "):
            assert secret not in text
        assert "治安" in text and "暴力犯罪率" in text
