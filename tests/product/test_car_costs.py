"""用车的固定费与行程油费（独立审核 F1，DECISIONS/0042）：同一笔油费只付一次。

AAA 2024 *Your Driving Costs* 小册子，小型轿车、每年 15,000 英里：油费 11.12 美分/英里、保养 9.55 美分/英里、拥有费用 5,786，
年度总费用 8,886（与 AAA 官网同一个数）。用车权的月费只取非燃油部分：

    (8,886 − 15,000 × 0.1112) ÷ 12 = (8,886 − 1,668) ÷ 12 = 601.50

开车通勤每次往返另付油费 = 往返分钟 / 60 × 25 英里每小时 ÷ 23.4 英里每加仑 × 本都会区 2024 年汽油价（旧金山 4.864）。
例：往返 30 分钟 = 12.5 英里 ÷ 23.4 = 0.534 加仑 × 4.864 = 2.60；一个月到岗 20 次付 52.00，加月费 601.50，用车一共 653.50。
"""

from __future__ import annotations

import json
import zlib
from decimal import Decimal
from pathlib import Path

import pytest

from product.realdata.commute import Cited, car_access_item, load_commute_config, verify_cited
from product.realdata.pdf_text import pdf_text, squash

COMMUTE = Path("data/frozen/life/us-v1/commute")
PACK = Path("data/frozen/life/us-v1/pack")
CITIES = ("nyc", "la", "sf", "chi", "hou", "sea")


def test_the_monthly_car_fee_is_the_non_fuel_part_in_every_city():
    for key in CITIES:
        car = json.loads((COMMUTE / f"{key}.json").read_text(encoding="utf-8"))["car_access"]
        assert car["price"]["amount"]["amount"] == 601.5, key  # 不是含油费的 8,886 ÷ 12 = 740.50
        assert "不含油费" in car["quality"]
        inputs = " ".join(car["source"]["inputs"])
        assert "11.12" in inputs and "1,668" in inputs


def _cite(value: str) -> Cited:
    return Cited(
        value=Decimal(value),
        citations=(
            {
                "source": "aaa_driving_costs_2024",
                "publisher": "AAA",
                "url": "https://example.invalid/aaa",
                "retrieved_on": "2026-10-23",
                "raw_sha256": "0" * 64,
                "quote": value,
            },
        ),
    )


def test_the_split_by_hand():
    cfg = load_commute_config()
    item = car_access_item(cfg, "sf", _cite("8886"), _cite("15000"), _cite("11.12"))
    assert item.price.amount.amount == pytest.approx(601.50)  # (8,886 − 1,668) ÷ 12
    assert item.functions == ("car_access",)


def _pdf(text: str) -> bytes:
    """一份最小的 PDF：一个 Flate 压缩的内容流，文字操作里是字面字符串（排版把词拆开）。"""
    stream = zlib.compress(f"BT ({text}) Tj ET".encode("latin-1"))
    head = f"%PDF-1.7\n1 0 obj << /Length {len(stream)} /Filter /FlateDecode >>\nstream\n"
    return head.encode("ascii") + stream + b"\nendstream\nendobj\n%EOF\n"


def test_pdf_quotes_are_checked_after_rejoining_split_words():
    raw = _pdf("Small Sedan Oper a ting C os ts fuel 11 . 12\\242 12.54\\242")
    text = pdf_text(raw)
    assert "11 . 12¢" in text  # 八进制转义 \242 是美分号
    assert squash("Operating Costs fuel 11.12¢") in squash(text)


def test_a_pdf_citation_must_contain_the_number():
    cfg = {
        "sources": {
            "b": {"publisher": "AAA", "url": "https://example.invalid/b.pdf", "format": "pdf"}
        }
    }

    class _Rec:
        raw_sha256 = "1" * 64
        retrieved_at = "2026-10-23T00:00:00Z"

    class _Fetched:
        record = _Rec()

    texts = {"b": squash(pdf_text(_pdf("Oper a ting C os ts fuel 11 . 12\\242")))}
    spec = {"value": "11.12", "cite": [{"source": "b", "quote": "Operating Costs fuel 11.12¢"}]}
    got, why = verify_cited(spec, cfg, texts, {"b": _Fetched()}, {})
    assert why == [] and got is not None and got.value == Decimal("11.12")
    wrong = {"value": "12.54", "cite": spec["cite"]}
    assert verify_cited(wrong, cfg, texts, {"b": _Fetched()}, {})[0] is None


# --------------------------------------------------------------------------- 结算：月费一次、油费按次


@pytest.fixture(scope="module")
def pack_bundle():
    from ledger.life_catalog import load_life_bundle

    bundle, _ = load_life_bundle(PACK / "life_input.json", require_settlement_ready=True)
    return bundle


def _start(bundle, city: str, job_id: str):
    from common.life_contracts import PersonYearState

    cat = bundle.catalog_for(city)
    job = cat.opportunity(job_id)
    occ = {"scheme": job.occupation.scheme, "code": job.occupation.code}
    code = city.split(":")[-1]
    return PersonYearState.model_validate(
        {
            "person_id": "t901",
            "clone_id": f"t901.{code}.r0",
            "city": {"country": "US", "scheme": "CBSA2023", "code": code},
            "year_index": 1,
            "occupation": occ,
            "money_basis_id": "USD_2024_LOCAL",
            "cash": {"amount": 20000, "numeraire": "USD_2024_LOCAL"},
            "time_budget_hours": 4380,
            "energy": {"level": 250, "scale_version": "e1-4a-monthly-quota-v1"},
            "resource_budget_basis": {
                "rules_version": "e1-4a-monthly-resources-v1",
                "reserved": "测试：只预留睡眠与基本生活",
                "source": {
                    "kind": "synthetic",
                    "observed_on": "2026-10-23",
                    "geography_scope": "测试",
                    "mapping_version": "test",
                    "synthetic_note": "测试用的开局状态",
                },
            },
            "energy_quota_basis": {"base_level": 250, "status": "base_first_year"},
            "abilities": [
                {"ability_id": "edu_at_least_hs", "source": "initial", "usable_from_year": 1}
            ],
            "employment": {
                "stated_occupation": occ,
                "as_of": "start_declaration",
                "positions": [
                    {"opportunity_id": job_id, "occupation": occ, "employer_id": job.employer_id}
                ],
                "current_position_ids": [job_id],
            },
            "health": {"age_years": 35, "repeat_index": 0},
            "env_repeat": 1,
        }
    )


def _drive_only_home(cat, state, work_area: str):
    """年度菜单上最便宜的一处住房：它的片区到岗位片区只有开车能到（或开车最快），用车才是这条通勤的执行方式。"""
    offered = cat.offered_ids(state)
    legs = {}
    for g in cat.commute.legs:
        if g.work_area == work_area:
            legs.setdefault(g.home_area, {})[g.mode] = g
    for o in sorted(
        cat.opportunities, key=lambda x: (x.price.amount.amount if x.price else 0, x.opportunity_id)
    ):
        if o.category != "housing" or o.opportunity_id not in offered:
            continue
        modes = legs.get(o.area, {})
        drive = modes.get("drive")
        others = [g for m, g in modes.items() if m != "drive"]
        if drive is not None and all(g.roundtrip_minutes > drive.roundtrip_minutes for g in others):
            return o, drive
    raise AssertionError("菜单上没有只能开车到岗的住房")


def test_a_driving_year_pays_the_fixed_fee_once_and_fuel_per_trip(pack_bundle):
    from common.life_contracts import YearAction
    from econ.life_settlement import binding_from_policy, settle_year

    city = "US:CBSA2023:41860"
    cat = pack_bundle.catalog_for(city)
    job_id = "sf.job.43-9061.start"
    state = _start(pack_bundle, city, job_id)
    home, leg = _drive_only_home(cat, state, cat.opportunity(job_id).area)
    # 手算一次的油费：往返分钟 / 60 × 25 ÷ 23.4 × 4.864，四舍五入到分
    fuel = (Decimal(leg.roundtrip_minutes) / 60 * 25 / Decimal("23.4") * Decimal("4.864")).quantize(
        Decimal("0.01")
    )
    assert Decimal(str(leg.cost_per_roundtrip.amount)) == fuel
    food = next(m for m in cat.minimum_living if m.need == "food").baseline_opportunity_id
    car = "sf.service.car_access"
    action = YearAction.model_validate(
        {
            "person_id": state.person_id,
            "clone_id": state.clone_id,
            "year_index": 1,
            "items": [
                {"opportunity_id": oid, "quantity": 1, "start_month": 1, "end_month": 12}
                for oid in (home.opportunity_id, food, job_id, car)
            ],
            "minimum_fallback": ["housing", "food"],
            "emergency": {"minor": "decline", "major": "decline", "pause_order": []},
            "rationale": "测试：预先写好的开车上班计划",
        }
    )
    tax = binding_from_policy(pack_bundle.tax_policy(cat.tax_policy_id))
    res = settle_year(
        protocol=pack_bundle.protocol, state=state, action=action, catalog=cat, tax=tax
    )
    assert res.status == "completed", [p.message for p in res.problems]
    out = res.outcome
    car_paid = [-Decimal(str(f.amount.amount)) for f in out.cash_flows if f.opportunity_id == car]
    assert car_paid == [Decimal("601.5")] * 12  # 月费每月一次，不含油费
    for m in out.resources.months:
        c = m.commute
        trips = sum(u.onsite_visits for u in c.uses if u.mode == "drive")
        assert {u.mode for u in c.uses} == {"drive"}
        assert Decimal(str(c.paid.amount)) == fuel * trips  # 油费只按实际往返次数付
        commute_flows = [
            -Decimal(str(f.amount.amount))
            for f in out.cash_flows
            if f.month == m.month and f.opportunity_id is None and f.label.startswith("通勤")
        ]
        assert commute_flows == ([fuel * trips] if trips else [])
