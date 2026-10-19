"""每月满足感（评分 e1-convenience-v1）：S = (1 − γ)·B + γ·H，B = Σ 六维权重 × 维度分，每个叶子 s = min(c, z/(z + τ))。

u301 的六维点数：物质 45（居住面积 30、新鲜食品 30、日常便利 40）、安全 5、发展 10、休闲 15、环境 5、交通 20；γ = 0.2。
K51 基本安排（远处一居室 600 平方英尺、基本食品每周 3 顿新鲜餐、全职文员）健康的一个月：

* 居住面积 z = 0.01 × 600 = 6，s = 6/(6 + 6) = 1/2；新鲜食品 s = 3/(3 + 7) = 3/10；日常便利（没有替代）s = 0；
  物质 = 0.3 × 1/2 + 0.3 × 3/10 + 0.4 × 0 = 6/25；
* 安全 3/(3 + 3) = 1/2；发展 2/(2 + 2) = 1/2；休闲 4/(4 + 12) = 1/4；环境 3/(3 + 3) = 1/2；
* 交通：往返 70 分钟、到岗 20 次，k = 0.25 + 0.75 × 20/20 = 1，S = 60/(60 + 70) = 6/13；
* B = 0.45 × 6/25 + 0.05 × 1/2 + 0.10 × 1/2 + 0.15 × 1/4 + 0.05 × 1/2 + 0.20 × 6/13；u = 0.8·B + 0.2 × 1。
"""

from __future__ import annotations

from fractions import Fraction

import pytest

from common.life_contracts import IllnessEventSource, LifeContractError
from econ.life_scoring import effective_weights, preference_from_confirmed_profile, score_year

from .conftest import CLONE_K, K51, base_items, live_year, whole
from .settle_support import revise

B_BASE = (
    Fraction(45, 100) * Fraction(6, 25)
    + Fraction(5, 100) * Fraction(1, 2)
    + Fraction(10, 100) * Fraction(1, 2)
    + Fraction(15, 100) * Fraction(1, 4)
    + Fraction(5, 100) * Fraction(1, 2)
    + Fraction(20, 100) * Fraction(6, 13)
)
U_BASE = Fraction(4, 5) * B_BASE + Fraction(1, 5)


def _component(month, cid):
    return next(c for c in month.components if c.component_id == cid)


def _year(bundle, scoring, items, **kw):
    return live_year(bundle, scoring, bundle.initial_state(CLONE_K), items, **kw)


def test_a_healthy_base_month_by_hand(first_year):
    m = first_year.score.months[0]
    assert m.status == "scored"
    assert _component(m, "housing_space").s == pytest.approx(0.5, abs=1e-12)
    assert _component(m, "fresh_food").s == pytest.approx(0.3, abs=1e-12)
    assert _component(m, "daily_convenience").s == 0
    assert _component(m, "commute").s == pytest.approx(6 / 13, abs=1e-12)
    assert m.u == pytest.approx(float(U_BASE), abs=1e-12)
    assert first_year.score.annual_mean == pytest.approx(float(U_BASE), abs=1e-12)


def test_the_dimension_weights_are_exactly_the_stated_points(bundle, scoring):
    """B 的权重就是用户确认的六维点数 / 100（合计 1）；健康项的 γ 在 B 之外分，不占六维的 100 分。"""
    pref = preference_from_confirmed_profile(bundle.profile_for("u301"))
    w = effective_weights(scoring.spec, pref)
    assert w == pytest.approx(
        {
            "material": 0.45,
            "safety": 0.05,
            "opportunity": 0.10,
            "leisure": 0.15,
            "environment": 0.05,
            "commute": 0.20,
        },
        abs=1e-12,
    )
    assert pref.material_weights == pytest.approx(
        {"housing_space": 0.3, "fresh_food": 0.3, "daily_convenience": 0.4}, abs=1e-12
    )


def test_living_near_work_scores_better_on_transport(bundle, scoring):
    near = [whole("k51.housing.near"), whole("k51.food.basic"), whole("k51.job.clerk")]
    m = _year(bundle, scoring, near).score.months[0]
    assert _component(m, "commute").s == pytest.approx(60 / 90, abs=1e-12)  # 往返 30 分钟


def test_attending_the_course_is_growth_not_the_credential(bundle, scoring):
    """上会计课的月份：能力进度每月 1/12，发展 z = 2 + 12 × 1/12 = 3，s = 3/5。证书本身不另加分。"""
    items = [
        whole("k51.housing.near"),
        whole("k51.food.basic"),
        whole("k51.job.clerk"),
        whole("k51.training.accounting_course"),
    ]
    y = _year(bundle, scoring, items)
    assert {round(_component(m, "opportunity").s, 12) for m in y.score.months} == {0.6}
    m = live_year(bundle, scoring, y.next_state, items[:3]).score.months[0]
    assert _component(m, "opportunity").s == pytest.approx(0.5, abs=1e-12)  # 学完之后没有新进度


def test_a_major_illness_month_scores_health_zero_and_no_commute(bundle, scoring):
    """3 月月初大病（不治，3–5 月）：H = 0；病假没有到岗，k = 0.25，T_eq = 17.5，交通 60/77.5。"""
    src = IllnessEventSource.model_validate(
        {
            "mode": "injected",
            "stream_version": "e1-5c-illness-stream-v1",
            "note": "测试用的指定事件",
            "injected": [
                {
                    "person_id": "u301",
                    "repeat_index": 0,
                    "year_index": 1,
                    "month": 3,
                    "severity": "major",
                    "note": "测试：3 月大病",
                }
            ],
        }
    )
    y = _year(bundle, scoring, base_items(), protocol=revise(bundle.protocol, illness_events=src))
    march = y.score.months[2]
    assert (march.health.state, march.health.value, march.health.deduction) == ("major", 0.0, 0.2)
    assert _component(march, "commute").s == pytest.approx(60 / 77.5, abs=1e-12)
    assert y.score.months[0].health.contribution == pytest.approx(0.2)


def test_an_unknown_city_condition_makes_the_month_incomplete_not_zero(bundle, scoring, first_year):
    """城市安全条件未知：这一维不补 0、不删权重重新归一化，月份评分不完整，年度平均没有。"""
    cond = scoring.conditions_for(K51)
    data = cond.model_dump(mode="json")
    for c in data["components"]:
        if c["component_id"] == "safety":
            c.update(
                status="unknown", base=None, cap=None, unknown_reason="合成情景：安全资料没有采集到"
            )
    unknown = type(cond).model_validate(data)
    s = score_year(
        protocol=bundle.protocol,
        spec=scoring.spec,
        conditions=unknown,
        catalog=bundle.catalog_for(K51),
        state=bundle.initial_state(CLONE_K),
        outcome=first_year.settlement.outcome,
        preference=preference_from_confirmed_profile(bundle.profile_for("u301")),
    )
    assert {m.status for m in s.months} == {"incomplete"}
    assert all(m.u is None for m in s.months) and s.annual_mean is None
    assert _component(s.months[0], "safety").status == "unknown"


def test_scoring_reads_the_settled_life_not_the_money(bundle, scoring):
    """同样的生活、多花钱（学费以外的同一处住房、同一份食品）不加分：u 只读实际住的、吃的、做的，不读付款。"""
    plain = _year(bundle, scoring, base_items()).score
    rich_state = revise(
        bundle.initial_state(CLONE_K), cash={"amount": 90000, "numeraire": "USD_2024_LOCAL"}
    )
    rich = live_year(bundle, scoring, rich_state, base_items()).score
    assert [m.u for m in rich.months] == [m.u for m in plain.months]


def test_a_mismatched_scoring_content_is_refused(bundle, scoring):
    """协议的反馈政策记着评分规格的内容身份；规格换了（τ 改了）而协议没跟着换，不能拿来算下一年的额度。"""
    comp = scoring.spec.component("safety")
    changed = scoring.spec.model_copy(
        update={
            "components": tuple(
                comp.model_copy(update={"tau": 4.0}) if c.component_id == "safety" else c
                for c in scoring.spec.components
            )
        }
    )
    other = type(scoring)(**{**scoring.__dict__, "spec": changed})
    with pytest.raises(LifeContractError, match="fdf920f447af"):  # 协议记着的旧身份
        live_year(bundle, other, bundle.initial_state(CLONE_K), base_items())
