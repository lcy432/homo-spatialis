"""EconomicCore 协议的契约测试。真核已经落地，这些不再 skip。"""

from __future__ import annotations

import pytest

from common.money import USD_2024_US100
from common.providers import ExpenditureShareProvider
from econ.core import WelfareCore, build_core
from econ.types import EconomicCore, GroundTruth, Observable, ObservableTier
from tests.econ.conftest import CITY_RECORDS, REFERENCE_ID, ProgressiveTax


@pytest.fixture
def built() -> WelfareCore:
    return build_core(
        CITY_RECORDS, ProgressiveTax(), numeraire=USD_2024_US100, reference_id=REFERENCE_ID
    )


def test_real_core_satisfies_the_protocol(built):
    assert isinstance(built, EconomicCore)


def test_both_exits_return_their_own_type(built, persona, city_a):
    assert isinstance(built.observe(persona, city_a, 2024, ObservableTier.O1), Observable)
    assert isinstance(built.score(persona, city_a, 2024), GroundTruth)


def test_the_two_exits_share_one_implementation(built, persona, city_a):
    """§3.2：一份实现两个出口。若 observe 与 score 各算一遍税，税函数会
    漂移，而漂移之后一个差异出现时你无法判断是模型错了还是尺子错了。

    检验方式：Observable 里的 net_income 必须与 score 走的那条路算出来的
    完全一致，一位浮点都不差。
    """
    obs = built.observe(persona, city_a, 2024, ObservableTier.O2)
    assert obs.l2_accounting["net_income"] == built._income(persona, city_a)["net_income"]


def test_the_core_holds_no_expenditure_share_provider(built):
    """架构修正一的结构性检验。

    份额是 Persona 的字段。装配层一旦持有 CEX provider，就有人会按城市的
    税后收入去查档位，于是 P 不再是价格指数、κ 跨城漂移、§11.7 的 logit
    变成误设定——三件事一起坏，而且都不会报错。
    """
    for name, value in vars(built).items():
        assert not isinstance(value, ExpenditureShareProvider), (
            f"WelfareCore.{name} 是一个 ExpenditureShareProvider；份额必须在 Persona 上冻结"
        )


def test_the_core_never_branches_on_country():
    """架构规则一：M2 对国家无知。

    FAKE_COUNTRY fixture 跑通时 M2 一行不改，是这条规则的验收标准；这个
    用例是它的廉价前哨。

    走 AST 而不是文本搜索：文档字符串里会正当地出现"不许写 country == ..."
    这样的说明，字符串匹配会把说明本身当成违规——第一版就是这么自己咬了
    自己一口。AST 只看真正的比较表达式。
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "econ"
    offenders: list[str] = []
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            operands = [node.left, *node.comparators]
            for operand in operands:
                is_country_attr = isinstance(operand, ast.Attribute) and operand.attr == "country"
                is_country_name = isinstance(operand, ast.Name) and operand.id == "country"
                if is_country_attr or is_country_name:
                    offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, f"M2 里出现了按国家分支的比较：{offenders}"


def test_graph_for_is_exposed_for_the_single_count_assertion(built, persona, city_a):
    built.graph_for(persona, city_a).assert_all()
