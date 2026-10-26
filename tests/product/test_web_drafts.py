"""界面层导出的草稿交给接入层审阅：E3 读到的答案状态与界面上显示的一致。

草稿样本由界面的状态更新函数按页面操作生成（web/tests/draft-fixtures.test.ts），存在 web/tests/fixtures/drafts/。界面改了草稿的
写法、或这里的审阅规则变了，这些测试就会失败——两边对“未答、0、无所谓、暂不确定”的理解不能悄悄分家。

可手算的例子：六维均分每项 50/3；把安全改成 20 之后，5 × 50/3 + 20 = 310/3，多出 10/3（界面显示“多出 3.3 分”）；再从物质减去 10/3，
物质是 40/3，合计正好 100。

现有样本是界面按 v3.4（v0.4 问卷）生成的旧的百分比分配。v3.5 起问卷改用独立评分（DECISIONS/0047）：这里照原样审阅旧草稿——旧分配
保留原来的含义，同时带着“版本已变”与“分配方式已停用”的阻断；迁移要用户自己选择重新评分或同等重视，这里不替用户选（迁移见
``test_ratings`` 与 ``test_draft_migration``）。界面按 v3.5 重新生成样本之后，这几条要换成独立评分的例子。
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
    review = review_draft(pack, draft)
    if version != PRODUCT_SCHEMA_VERSION:
        # 旧版本问卷的草稿：照原样审阅，不能确认；迁移由用户在页面上明确选择（不替用户同意、不替用户选重新评分或同等重视）
        codes = {i.code for i in review.issues if i.severity == "error"}
        assert "version_changed" in codes and not review.confirmable
        if draft.six_dimensions.mode in ("manual", "equal_split"):
            assert "allocation_mode_retired" in codes
    return review


def issue_keys(review) -> set[tuple[str, str]]:
    return {(i.code, i.field) for i in review.issues}


def six_lines(review):
    return {ln.key: ln for ln in review.six_dimensions.lines}


def test_unanswered_zero_indifferent_and_unsure_stay_distinct(pack):
    review = reviewed(pack, "states.json")
    lines = six_lines(review)
    assert (lines["material"].state, lines["material"].exact) == ("provided", "0")
    assert lines["environment"].state == "indifferent"
    assert lines["leisure"].state == "unanswered"
    assert ("allocation_unanswered", "six_dimensions.entries.leisure") in issue_keys(review)
    assert ("allocation_indifferent", "six_dimensions.entries.environment") in issue_keys(review)
    # 无所谓、未答都不能代替分数：六维还不完整，不能确认
    assert review.six_dimensions.status == "incomplete"
    assert not review.confirmable
    health = {h.question_id: h.answer_state for h in review.health}
    assert health["hc_more_pay_more_sick_risk"] == "unsure"
    assert health["hc_reserve_for_health_costs"] == "unanswered"


def test_equal_split_then_editing_one_item_is_over_by_exactly_ten_thirds(pack):
    review = reviewed(pack, "equal-split-over.json")
    lines = six_lines(review)
    assert lines["safety"].exact == "20"
    assert all(
        lines[k].exact == "50/3"
        for k in ("material", "opportunity", "leisure", "environment", "commute")
    )
    assert review.six_dimensions.total is not None
    assert review.six_dimensions.total.exact == "310/3"
    over = [
        i
        for i in review.issues
        if i.code == "points_sum_not_100" and i.field == "six_dimensions.entries"
    ]
    assert over and "多出 10/3 分" in over[0].message_zh


def test_moving_the_overflow_out_of_material_sums_to_exactly_100(pack):
    review = reviewed(pack, "equal-split-fixed.json")
    assert six_lines(review)["material"].exact == "40/3"
    assert review.six_dimensions.total is not None
    assert review.six_dimensions.total.exact == "100"
    assert review.six_dimensions.status == "complete"


def test_cash_written_with_a_comma_decimal_is_rejected_not_reinterpreted(pack):
    review = reviewed(pack, "money-comma.json")
    assert ("money_separator_invalid", "cash.amount_text") in issue_keys(review)
    assert review.money.converted is None
