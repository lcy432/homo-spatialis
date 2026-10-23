"""都会区安全背景的换算（所有者 2026-10-23；DECISIONS/0042）：手算例。只用手写的数，不读缓存。"""

from __future__ import annotations

from decimal import Decimal

from product.realdata.safety import CrimeCount, aggregate, relative_index

NATIONAL = CrimeCount(population=340110988, violent=1221345, property=5986400)  # FBI CIUS 2024 表 1
KW = dict(
    w_violent=Decimal("0.5"), w_property=Decimal("0.5"), national_index=Decimal(6), cap=Decimal(10)
)


def test_counts_and_population_are_summed_before_the_rate():
    a = CrimeCount(population=100000, violent=100, property=1000)  # 100 / 1,000 每 10 万
    b = CrimeCount(population=900000, violent=4500, property=27000)  # 500 / 3,000 每 10 万
    total = aggregate([a, b])
    assert (total.population, total.violent, total.property) == (1000000, 4600, 28000)
    assert total.violent_rate == Decimal(460)  # 不是两地率的平均 300
    assert total.property_rate == Decimal(2800)


def test_the_national_level_maps_to_six_and_higher_crime_never_scores_better():
    r, index = relative_index(NATIONAL, NATIONAL, **KW)
    assert r == 1 and index == Decimal("6.000")
    base = CrimeCount(1000000, 4000, 20000)
    worse = CrimeCount(1000000, 6000, 20000)  # 暴力犯罪多 50%，其余相同
    _, i_base = relative_index(base, NATIONAL, **KW)
    _, i_worse = relative_index(worse, NATIONAL, **KW)
    assert i_worse < i_base
    _, i_low = relative_index(CrimeCount(1000000, 10, 100), NATIONAL, **KW)
    assert i_low == Decimal(10)  # 上限 10（犯罪更少也不再加）


def test_hand_example_for_houston_2024():
    # FBI 表 6：休斯敦都会区估计总数 暴力 43,499、财产 196,399，人口 7,713,658
    hou = CrimeCount(7713658, 43499, 196399)
    r, index = relative_index(hou, NATIONAL, **KW)
    # 暴力率 563.92 / 359.10 = 1.5704；财产率 2546.12 / 1760.13 = 1.4466；r = 1.5085；6 / r = 3.978
    assert round(r, 4) == Decimal("1.5085") and index == Decimal("3.978")


def test_one_city_changing_does_not_move_another_city():
    hou = CrimeCount(7713658, 43499, 196399)
    sea = CrimeCount(4110067, 15844, 122132)
    _, before = relative_index(sea, NATIONAL, **KW)
    _ = relative_index(CrimeCount(7713658, 90000, 300000), NATIONAL, **KW)  # 休斯敦的资料变了
    _, after = relative_index(sea, NATIONAL, **KW)
    assert before == after  # 只与固定的全国参照比，没有按六城的最高、最低拉伸
    assert relative_index(hou, NATIONAL, **KW)[1] != relative_index(sea, NATIONAL, **KW)[1]


def test_residence_scope_relation_is_stated_not_hidden():
    from product.realdata.safety import home_counties, residence_relation

    metro = [("17031", "Cook County, Illinois"), ("18089", "Lake County, Indiana")]
    home = home_counties({"home_states": ["17"]}, metro)
    assert home == [("17031", "Cook County, Illinois")]
    text = residence_relation(metro, home, whole_metro=True)
    assert (
        "Lake County, Indiana" in text and "不在模拟居住范围" in text
    )  # 芝加哥：资料含印第安纳州部分
    assert (
        residence_relation(metro, metro, whole_metro=True)
        == "资料范围与模拟居住范围相同（整个都会区）"
    )
    ny = [("36061", "New York County, New York"), ("34003", "Bergen County, New Jersey")]
    home = home_counties({"home_counties": ["36061", "34003"]}, ny)
    missing = residence_relation(ny[:1], home, whole_metro=False)
    assert (
        "Bergen County, New Jersey" in missing and "不在资料里" in missing
    )  # 资料没覆盖的居住县照实报
