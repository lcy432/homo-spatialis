"""界面层导出的草稿交给接入层审阅：E3 读到的答案状态与界面上显示的一致。

草稿样本由界面的状态更新函数按页面操作生成（web/tests/draft-fixtures.test.ts），存在 web/tests/fixtures/drafts/。界面改了草稿的
写法、或这里的审阅规则变了，这些测试就会失败——两边对"未答、0、无所谓、暂不确定"的理解不能悄悄分家。

v3.5 起六维与物质是独立评分（DECISIONS/0047）：样本里是原始评分，权重由这里的审阅按比例精确换算。可手算的例子：
六维 80/40/40/20/10/10（原始合计 200）→ 权重 40/20/20/10/5/5；两组都"同等重视"（每项 50）→ 六维每项 50/3、物质每项 25；
物质父项为 0 时四项不适用、草稿里的四项答案留着。
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import pytest

from common.product_contracts import (
    PRODUCT_SCHEMA_VERSION,
    READABLE_SCHEMA_VERSIONS,
    UserInputDraft,
)
from product.answers import review_draft
from product.fixtures import prepare_workspace
from product.pack import DataPack, load_pack

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "web" / "tests" / "fixtures" / "drafts"
REGENERATE = "在 web/ 里运行 UPDATE_DRAFT_FIXTURES=1 npx vitest run tests/draft-fixtures.test.ts 重新生成样本"


@pytest.fixture(scope="module")
def pack(tmp_path_factory) -> DataPack:
    work = prepare_workspace(tmp_path_factory.mktemp("ws"), REPO)
    with contextlib.chdir(work):
        return load_pack()


def reviewed(pack: DataPack, name: str):
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    version = raw.get("meta", {}).get("schema_version")
    if version not in READABLE_SCHEMA_VERSIONS:
        # 草稿样本由界面（E5）生成；读不回的旧结构要由界面一侧重新生成。这里明确跳过并说出原因，不改界面文件
        pytest.skip(
            f"{name} 是按 {version} 生成的，当前公共结构是 {PRODUCT_SCHEMA_VERSION}：{REGENERATE}"
        )
    draft = UserInputDraft.model_validate(raw)
    assert draft.meta.data_pack.content_fingerprint == pack.meta().data_pack.content_fingerprint, (
        f"{name} 是按另一版资料包生成的：{REGENERATE}"
    )
    assert version == PRODUCT_SCHEMA_VERSION, (
        f"{name} 不是当前共享结构 {PRODUCT_SCHEMA_VERSION}：{REGENERATE}"
    )
    return review_draft(pack, draft)


def issue_keys(review) -> set[tuple[str, str]]:
    return {(i.code, i.field) for i in review.issues}


def six_lines(review):
    return {ln.key: ln for ln in review.six_dimensions.lines}


def material_lines(review):
    return {ln.key: ln for ln in review.material.lines}


def test_ratings_are_raw_scores_and_weights_come_from_exact_proportion(pack):
    review = reviewed(pack, "ratings.json")
    assert review.six_dimensions.mode == "ratings_normalized_v1"
    assert review.six_dimensions.rule == "ratings_normalized_v1"
    lines = six_lines(review)
    # 界面存的是原始评分；权重 = 100 × 评分 ÷ 200
    assert {k: lines[k].rating_exact for k in lines} == {
        "material": "80",
        "safety": "40",
        "opportunity": "40",
        "leisure": "20",
        "environment": "10",
        "commute": "10",
    }
    assert {k: lines[k].exact for k in lines} == {
        "material": "40",
        "safety": "20",
        "opportunity": "20",
        "leisure": "10",
        "environment": "5",
        "commute": "5",
    }
    assert review.six_dimensions.rating_total is not None
    assert review.six_dimensions.rating_total.exact == "200"
    assert review.six_dimensions.total is not None
    assert review.six_dimensions.total.exact == "100"
    assert review.six_dimensions.status == "complete"
    mat = material_lines(review)
    assert {k: mat[k].exact for k in mat} == {
        "housing_condition": "25",
        "food_life": "25",
        "daily_convenience": "30",
        "personal_enjoyment": "20",
    }
    assert review.material.status == "complete"


def test_unanswered_zero_indifferent_and_unsure_stay_distinct(pack):
    review = reviewed(pack, "states.json")
    lines = six_lines(review)
    assert (lines["material"].state, lines["material"].rating_exact) == ("provided", "0")
    assert lines["environment"].state == "indifferent"
    assert lines["leisure"].state == "unanswered"
    assert ("allocation_unanswered", "six_dimensions.entries.leisure") in issue_keys(review)
    assert ("allocation_indifferent", "six_dimensions.entries.environment") in issue_keys(review)
    # 无所谓、未答都不能代替评分：六维还不完整，不出权重、不补 0，不能确认
    assert review.six_dimensions.status == "incomplete"
    assert review.six_dimensions.total is None
    assert lines["safety"].exact is None
    assert not review.confirmable
    health = {h.question_id: h.answer_state for h in review.health}
    assert health["hc_more_pay_more_sick_risk"] == "unsure"
    assert health["hc_reserve_for_health_costs"] == "unanswered"


def test_equal_weight_sets_every_rating_to_50_and_weights_follow(pack):
    review = reviewed(pack, "equal-weight.json")
    lines = six_lines(review)
    assert all(lines[k].rating_exact == "50" for k in lines)
    assert all(lines[k].exact == "50/3" for k in lines)
    assert review.six_dimensions.rating_total is not None
    assert review.six_dimensions.rating_total.exact == "300"
    assert review.six_dimensions.total is not None
    assert review.six_dimensions.total.exact == "100"
    mat = material_lines(review)
    assert all(mat[k].rating_exact == "50" and mat[k].exact == "25" for k in mat)


def test_material_parent_zero_keeps_inner_answers_but_does_not_use_them(pack):
    review = reviewed(pack, "parent-zero.json")
    lines = six_lines(review)
    assert lines["material"].rating_exact == "0" and lines["material"].exact == "0"
    # 五项 40/20/10/10/10（合计 90）：权重 400/9、200/9、100/9、100/9、100/9
    assert lines["safety"].exact == "400/9"
    assert lines["commute"].exact == "100/9"
    assert review.six_dimensions.status == "complete"
    assert review.material.status == "not_applicable"
    assert not review.material.lines
    assert ("material_parent_zero_ignored", "material") in issue_keys(review)
    # 草稿里的物质内部答案仍在（这里不改草稿；界面也不清零）
    raw = json.loads((FIXTURES / "parent-zero.json").read_text(encoding="utf-8"))
    assert raw["material"]["entries"]["housing_condition"] == {
        "state": "provided",
        "value_text": "70",
    }
    assert raw["material"]["entries"]["daily_convenience"] == {
        "state": "provided",
        "value_text": "0",
    }
    assert "food_life" not in raw["material"]["entries"]


def test_cash_written_with_a_comma_decimal_is_rejected_not_reinterpreted(pack):
    review = reviewed(pack, "money-comma.json")
    assert ("money_separator_invalid", "cash.amount_text") in issue_keys(review)
    assert review.money.converted is None
