"""生活引擎测试的共享夹具：只有一份样例——产品协议样例（``configs/life/synthetic_product.yaml`` 及其评分、评价配置）。

``raw`` 给的是 YAML 解析后的原始字典：拒绝类测试拿它深拷贝一份、改坏一处，再交给解析器，看它是否**明确**拒绝。
这样每条拒绝测试只改一个地方，失败原因不会混在一起。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from ledger.life_catalog import LifeBundle, load_life_bundle

FIXTURE = Path("configs/life/synthetic_product.yaml")
SCORING = Path("configs/life/scoring_product.yaml")
EVALUATION = Path("configs/life/evaluation_product.yaml")
#: 样例里的两座城与两个分身（同一个人 u301）
K51, M52 = "US:SYNTH2026:K51", "US:SYNTH2026:M52"
CLONE_K, CLONE_M = "u301.k51.r0", "u301.m52.r0"


def load_plain(path: Path) -> dict:
    """读 YAML 并经 JSON 往返：拆开锚点（改一处不连带改别处），日期写成字符串。"""
    return json.loads(json.dumps(yaml.safe_load(path.read_text(encoding="utf-8")), default=str))


@pytest.fixture(scope="session")
def raw_once() -> dict:
    return load_plain(FIXTURE)


@pytest.fixture
def raw(raw_once) -> dict:
    """每条测试一份独立的深拷贝，改坏了不影响别人。"""
    return copy.deepcopy(raw_once)


@pytest.fixture(scope="session")
def bundle() -> LifeBundle:
    b, _ = load_life_bundle(FIXTURE, require_settlement_ready=True)
    return b


@pytest.fixture(scope="session")
def raw_scoring_once() -> dict:
    return load_plain(SCORING)


@pytest.fixture
def raw_scoring(raw_scoring_once) -> dict:
    return copy.deepcopy(raw_scoring_once)


@pytest.fixture(scope="session")
def scoring(bundle):
    from ledger.life_scoring_config import load_scoring_config

    config, _ = load_scoring_config(SCORING, bundle)
    return config


@pytest.fixture(scope="session")
def evaluation(bundle, scoring):
    from ledger.life_evaluation_config import load_evaluation_config

    return load_evaluation_config(EVALUATION, bundle, scoring).spec


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


# ---------------------------------------------------------------------- 一年生活的小工具（各机制测试共用）

#: 不治疗、不暂停：受控主场景不发生新疾病，应急安排只是必须写
HEALTHY = {"minor": "decline", "major": "decline", "pause_order": []}


def whole(oid: str, start: int = 1, end: int = 12, quantity: int = 1) -> dict:
    """按月机会从 start 月到 end 月。"""
    return {"opportunity_id": oid, "quantity": quantity, "start_month": start, "end_month": end}


def once(oid: str, month: int, quantity: int = 1) -> dict:
    """一次性购买（设备、体验）在 month 月。"""
    return {"opportunity_id": oid, "quantity": quantity, "month": month}


def base_items(city: str = K51, job: str = "clerk") -> list[dict]:
    """远处住房、基本食品与一份岗位，全年。"""
    c = "k51" if city == K51 else "m52"
    return [whole(f"{c}.housing.far"), whole(f"{c}.food.basic"), whole(f"{c}.job.{job}")]


def year_action(state, items, *, emergency=None, fallback=("housing", "food"), savings_goal=None):
    from common.life_contracts import YearAction

    data = {
        "person_id": state.person_id,
        "clone_id": state.clone_id,
        "year_index": state.year_index,
        "items": list(items),
        "minimum_fallback": list(fallback),
        "emergency": emergency or HEALTHY,
        "rationale": "测试：预先写好的计划",
    }
    if savings_goal is not None:
        data["savings_goal"] = savings_goal
    return YearAction.model_validate(data)


def scoring_inputs(bundle, scoring, city: str, profile=None):
    from econ.life_year import ScoringInputs

    p = profile or bundle.product_profiles[0]
    return ScoringInputs(
        spec=scoring.spec,
        conditions=scoring.conditions_for(city),
        preference_input=p,
        hard_requirements=p.hard_requirements,
    )


def tax_for(bundle, city: str):
    from econ.life_settlement import binding_from_policy

    return binding_from_policy(bundle.tax_policy(bundle.catalog_for(city).tax_policy_id))


def live_year(
    bundle,
    scoring,
    state,
    items,
    *,
    protocol=None,
    catalog=None,
    emergency=None,
    profile=None,
    savings_goal=None,
):
    """结算并评分一年（econ.life_year.run_life_year）。"""
    from econ.life_year import run_life_year

    city = str(state.city)
    return run_life_year(
        protocol=protocol or bundle.protocol,
        state=state,
        action=year_action(state, items, emergency=emergency, savings_goal=savings_goal),
        catalog=catalog or bundle.catalog_for(city),
        tax=tax_for(bundle, city),
        scoring=scoring_inputs(bundle, scoring, city, profile),
    )


@pytest.fixture(scope="session")
def first_year(bundle, scoring):
    """K51 分身第一年：远处住房、基本食品、全职文员。"""
    return live_year(bundle, scoring, bundle.initial_state(CLONE_K), base_items())
