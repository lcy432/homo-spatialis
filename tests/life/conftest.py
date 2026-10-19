"""E1-1 生活契约测试的共享夹具。

``raw`` 给的是 YAML 解析后的原始字典：拒绝类测试拿它深拷贝一份、改坏一处，再交给
解析器，看它是否**明确**拒绝。这样每条拒绝测试只改一个地方，失败原因不会混在一起。
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from ledger.life_catalog import LifeBundle, load_life_bundle

FIXTURE = Path("configs/life/synthetic_e1_1.yaml")


@pytest.fixture(scope="session")
def raw_once() -> dict:
    return yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def raw(raw_once) -> dict:
    """每条测试一份独立的深拷贝，改坏了不影响别人。"""
    return copy.deepcopy(raw_once)


@pytest.fixture(scope="session")
def bundle() -> LifeBundle:
    b, _ = load_life_bundle(FIXTURE)
    return b


def catalog(raw: dict, code: str, country: str = "US") -> dict:
    return next(
        c for c in raw["catalogs"] if c["city"]["code"] == code and c["city"]["country"] == country
    )


def opportunity(raw: dict, opportunity_id: str) -> dict:
    for c in raw["catalogs"]:
        for o in c["opportunities"]:
            if o["opportunity_id"] == opportunity_id:
                return o
    raise KeyError(opportunity_id)


# --------------------------------------------------------------------------
# E1-2：可结算的样例与结算辅助函数
# --------------------------------------------------------------------------

FIXTURE_E1_2 = Path("configs/life/synthetic_e1_2.yaml")


@pytest.fixture(scope="session")
def raw2_once() -> dict:
    return yaml.safe_load(FIXTURE_E1_2.read_text(encoding="utf-8"))


@pytest.fixture
def raw2(raw2_once) -> dict:
    return copy.deepcopy(raw2_once)


@pytest.fixture(scope="session")
def bundle2() -> LifeBundle:
    b, _ = load_life_bundle(FIXTURE_E1_2, require_settlement_ready=True)
    return b


# --------------------------------------------------------------------------
# E1-3：探索评分的样例与配置
# --------------------------------------------------------------------------

FIXTURE_E1_3 = Path("configs/life/synthetic_e1_3.yaml")
SCORING_E1_3 = Path("configs/life/scoring_e1_3.yaml")


@pytest.fixture(scope="session")
def raw3_once() -> dict:
    return yaml.safe_load(FIXTURE_E1_3.read_text(encoding="utf-8"))


@pytest.fixture
def raw3(raw3_once) -> dict:
    return copy.deepcopy(raw3_once)


@pytest.fixture(scope="session")
def bundle3() -> LifeBundle:
    b, _ = load_life_bundle(FIXTURE_E1_3, require_settlement_ready=True)
    return b


@pytest.fixture(scope="session")
def raw_scoring_once() -> dict:
    return yaml.safe_load(SCORING_E1_3.read_text(encoding="utf-8"))


@pytest.fixture
def raw_scoring(raw_scoring_once) -> dict:
    return copy.deepcopy(raw_scoring_once)


@pytest.fixture(scope="session")
def scoring3(bundle3):
    from ledger.life_scoring_config import load_scoring_config

    config, _ = load_scoring_config(SCORING_E1_3, bundle3)
    return config


# --------------------------------------------------------------------------
# E1-3 增补：功能计分、景观、租金—通勤与覆盖的样例与配置（DECISIONS/0019）
# --------------------------------------------------------------------------

FIXTURE_E1_3S = Path("configs/life/synthetic_e1_3s.yaml")
SCORING_E1_3S = Path("configs/life/scoring_e1_3s.yaml")


@pytest.fixture(scope="session")
def raw3s_once() -> dict:
    return yaml.safe_load(FIXTURE_E1_3S.read_text(encoding="utf-8"))


@pytest.fixture
def raw3s(raw3s_once) -> dict:
    return copy.deepcopy(raw3s_once)


@pytest.fixture(scope="session")
def bundle3s() -> LifeBundle:
    b, _ = load_life_bundle(FIXTURE_E1_3S, require_settlement_ready=True)
    return b


@pytest.fixture(scope="session")
def raw_scoring3s_once() -> dict:
    return yaml.safe_load(SCORING_E1_3S.read_text(encoding="utf-8"))


@pytest.fixture
def raw_scoring3s(raw_scoring3s_once) -> dict:
    return copy.deepcopy(raw_scoring3s_once)


@pytest.fixture(scope="session")
def scoring3s(bundle3s):
    from ledger.life_scoring_config import load_scoring_config

    config, _ = load_scoring_config(SCORING_E1_3S, bundle3s)
    return config


# --------------------------------------------------------------------------
# E1-4A：月度时间与精力的样例（DECISIONS/0020）
# --------------------------------------------------------------------------

FIXTURE_E1_4A = Path("configs/life/synthetic_e1_4a.yaml")


@pytest.fixture(scope="session")
def raw4a_once() -> dict:
    return yaml.safe_load(FIXTURE_E1_4A.read_text(encoding="utf-8"))


@pytest.fixture
def raw4a(raw4a_once) -> dict:
    return copy.deepcopy(raw4a_once)


@pytest.fixture(scope="session")
def bundle4a() -> LifeBundle:
    b, _ = load_life_bundle(FIXTURE_E1_4A, require_settlement_ready=True)
    return b


# --------------------------------------------------------------------------
# E1-4B：满足感 → 下一年月度精力额度的样例与评分配置（DECISIONS/0021）
# --------------------------------------------------------------------------

FIXTURE_E1_4B = Path("configs/life/synthetic_e1_4b.yaml")
SCORING_E1_4B = Path("configs/life/scoring_e1_4b.yaml")


@pytest.fixture(scope="session")
def raw4b_once() -> dict:
    return yaml.safe_load(FIXTURE_E1_4B.read_text(encoding="utf-8"))


@pytest.fixture
def raw4b(raw4b_once) -> dict:
    return copy.deepcopy(raw4b_once)


@pytest.fixture(scope="session")
def bundle4b() -> LifeBundle:
    b, _ = load_life_bundle(FIXTURE_E1_4B, require_settlement_ready=True)
    return b


@pytest.fixture(scope="session")
def scoring4b(bundle4b):
    from ledger.life_scoring_config import load_scoring_config

    config, _ = load_scoring_config(SCORING_E1_4B, bundle4b)
    return config


# --------------------------------------------------------------------------
# E1-5A：学习与技能 → 岗位选择 → 实际收入的样例与评分配置（DECISIONS/0023）
# --------------------------------------------------------------------------

FIXTURE_E1_5A = Path("configs/life/synthetic_e1_5a.yaml")
SCORING_E1_5A = Path("configs/life/scoring_e1_5a.yaml")


@pytest.fixture(scope="session")
def raw5a_once() -> dict:
    return yaml.safe_load(FIXTURE_E1_5A.read_text(encoding="utf-8"))


@pytest.fixture
def raw5a(raw5a_once) -> dict:
    return copy.deepcopy(raw5a_once)


@pytest.fixture(scope="session")
def bundle5a() -> LifeBundle:
    b, _ = load_life_bundle(FIXTURE_E1_5A, require_settlement_ready=True)
    return b


@pytest.fixture(scope="session")
def scoring5a(bundle5a):
    from ledger.life_scoring_config import load_scoring_config

    config, _ = load_scoring_config(SCORING_E1_5A, bundle5a)
    return config


# ---------------------------------------------------------------------- E1-5A 补充：成长满足感与广义交通（DECISIONS/0024）
FIXTURE_GT = Path("configs/life/synthetic_e1_5a_growth_transport.yaml")
SCORING_GT = Path("configs/life/scoring_e1_5a_growth_transport.yaml")


@pytest.fixture(scope="session")
def raw_gt_once() -> dict:
    return yaml.safe_load(FIXTURE_GT.read_text(encoding="utf-8"))


@pytest.fixture
def raw_gt(raw_gt_once) -> dict:
    return copy.deepcopy(raw_gt_once)


@pytest.fixture(scope="session")
def scoring_gt_raw_once() -> dict:
    return yaml.safe_load(SCORING_GT.read_text(encoding="utf-8"))


@pytest.fixture
def scoring_gt_raw(scoring_gt_raw_once) -> dict:
    return copy.deepcopy(scoring_gt_raw_once)


@pytest.fixture(scope="session")
def bundle_gt() -> LifeBundle:
    b, _ = load_life_bundle(FIXTURE_GT, require_settlement_ready=True)
    return b


@pytest.fixture(scope="session")
def scoring_gt(bundle_gt):
    from ledger.life_scoring_config import load_scoring_config

    config, _ = load_scoring_config(SCORING_GT, bundle_gt)
    return config


# ---------------------------------------------------------------------- E1-5B：目标储蓄（DECISIONS/0026）
FIXTURE_SV = Path("configs/life/synthetic_e1_5b_savings.yaml")
SCORING_SV = Path("configs/life/scoring_e1_5b_savings.yaml")


@pytest.fixture(scope="session")
def raw_sv_once() -> dict:
    return yaml.safe_load(FIXTURE_SV.read_text(encoding="utf-8"))


@pytest.fixture
def raw_sv(raw_sv_once) -> dict:
    return copy.deepcopy(raw_sv_once)


@pytest.fixture(scope="session")
def bundle_sv() -> LifeBundle:
    b, _ = load_life_bundle(FIXTURE_SV, require_settlement_ready=True)
    return b


@pytest.fixture(scope="session")
def scoring_sv(bundle_sv):
    from ledger.life_scoring_config import load_scoring_config

    config, _ = load_scoring_config(SCORING_SV, bundle_sv)
    return config


# ---------------------------------------------------------------------- E1-5C：疾病、应急与恢复（DECISIONS/0027）
FIXTURE_HL = Path("configs/life/synthetic_e1_5c_health.yaml")
SCORING_HL = Path("configs/life/scoring_e1_5c_health.yaml")


@pytest.fixture(scope="session")
def raw_hl_once() -> dict:
    return yaml.safe_load(FIXTURE_HL.read_text(encoding="utf-8"))


@pytest.fixture
def raw_hl(raw_hl_once) -> dict:
    return copy.deepcopy(raw_hl_once)


@pytest.fixture(scope="session")
def bundle_hl() -> LifeBundle:
    b, _ = load_life_bundle(FIXTURE_HL, require_settlement_ready=True)
    return b


@pytest.fixture(scope="session")
def scoring_hl(bundle_hl):
    from ledger.life_scoring_config import load_scoring_config

    config, _ = load_scoring_config(SCORING_HL, bundle_hl)
    return config


# ---------------------------------------------------------------------- E1-6A：单分身跨期评价（DECISIONS/0028）
FIXTURE_EV = Path("configs/life/synthetic_e1_6a_evaluation.yaml")
SCORING_EV = Path("configs/life/scoring_e1_6a_evaluation.yaml")
#: E1-6A 收尾（DECISIONS/0029）：v1 规格不再执行，夹具改用同一样例的 v2 规格（四个模板；原 p5 = p1 的不治疗分支）。
EVALUATION_EV = Path("configs/life/evaluation_e1_6a_v2.yaml")


@pytest.fixture(scope="session")
def raw_ev_once() -> dict:
    return yaml.safe_load(FIXTURE_EV.read_text(encoding="utf-8"))


@pytest.fixture
def raw_ev(raw_ev_once) -> dict:
    return copy.deepcopy(raw_ev_once)


@pytest.fixture(scope="session")
def bundle_ev() -> LifeBundle:
    b, _ = load_life_bundle(FIXTURE_EV, require_settlement_ready=True)
    return b


@pytest.fixture(scope="session")
def scoring_ev(bundle_ev):
    from ledger.life_scoring_config import load_scoring_config

    config, _ = load_scoring_config(SCORING_EV, bundle_ev)
    return config


@pytest.fixture(scope="session")
def evaluation_ev(bundle_ev, scoring_ev):
    from ledger.life_evaluation_config import load_evaluation_config

    return load_evaluation_config(EVALUATION_EV, bundle_ev, scoring_ev).spec


@pytest.fixture(scope="session")
def lives_ev(bundle_ev, scoring_ev) -> dict:
    """样例里每个分身按预设计划跑完的三年实际生活（只跑一次；结果对象不可变）。"""
    from .evaluation_support import run_clone

    return {
        s.clone_id: run_clone(bundle_ev, scoring_ev, s.clone_id) for s in bundle_ev.initial_states
    }


# ---------------------------------------------------------------------- E1-6B：城市汇总（DECISIONS/0029）


@pytest.fixture(scope="session")
def batch_eb():
    """按冻结名单跑的小批次（实际生活只跑一次，三个运行臂各评价一次；u101.a95.r4 刻意不运行）。结果对象不可变。"""
    from .aggregation_support import run_batch

    return run_batch()
