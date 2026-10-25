"""E3B 映射修正的公共消息（v3.3）：年度事实里的日常支持与个人消费篮子逐月对得上 E1 的结算记录；物质两项的含义变了，旧版本
的确认要重新审阅确认（不静默沿用）。离线，不联网、不调用模型。"""

from __future__ import annotations

import pytest

from common.product_contracts import (
    PRODUCT_SCHEMA_VERSION,
    QUESTIONNAIRE_VERSION,
    VOCABULARY_VERSION,
)
from product import scenarios as sc
from product.answers import _version_issues
from product.pack import load_pack


@pytest.fixture(scope="module")
def loop(tmp_path_factory):
    return sc.closed_loop(tmp_path_factory.mktemp("support"), "support", repeat_indexes=(0,))


def test_support_and_basket_facts_match_the_settlement_month_by_month(loop):
    seen_support = seen_basket = False
    for clone in loop.result.clones:
        lives = loop.outcome.lives[clone.clone_ref]
        for facts, life in zip(clone.years, lives, strict=True):
            items = {it.opportunity_id: it for it in life.settlement.outcome.items}
            for line in facts.support:
                seen_support = True
                got = {m for oid in line.opportunity_ids for m in items[oid].covered_months}
                assert set(line.months_obtained) == got
                assert set(line.months_not_obtained) == set(line.months_planned) - got
                assert line.label_zh in ("个人通信", "居家联网", "家用日耗品")
            c = facts.consumption
            if c is not None:
                seen_basket = True
                covered = {m for oid in c.opportunity_ids for m in items[oid].covered_months}
                assert len(c.baskets_by_month) == 12
                for m in range(1, 13):
                    want = "1" if m in covered else "0"
                    assert c.baskets_by_month[m - 1] in (want, "—")
    # mock 按公开点数买了三项支持与每月 1 个标准篮子：结果里看得到
    assert seen_support and seen_basket


def test_the_material_texts_say_what_the_two_changed_items_mean():
    pack = load_pack()
    leaves = {x.key: x for x in pack.body.material_leaves}
    daily, enjoy = leaves["daily_convenience"], leaves["personal_enjoyment"]
    assert daily.label == "日常支持与便利"
    assert daily.definition == "通信联网、家用日耗品是否方便获得，以及日常杂务是否省心。"
    assert enjoy.definition == "在其他生活开支之外，获得衣着、个人护理等个人消费的满足。"
    assert "没有测量真实人的幸福感" in enjoy.note and "重新审阅" in enjoy.note


def test_an_old_confirmation_must_be_reviewed_again():
    pack = load_pack()
    assert (PRODUCT_SCHEMA_VERSION, QUESTIONNAIRE_VERSION, VOCABULARY_VERSION) == (
        "e3a-product-schema-v3.4",
        "e3a-questionnaire-v0.4-draft",
        "e3a-vocabulary-v5",
    )
    old = pack.meta().model_dump(mode="json") | {
        "schema_version": "e3a-product-schema-v3.2",
        "questionnaire_version": "e3a-questionnaire-v0.2-draft",
        "vocabulary_version": "e3a-vocabulary-v3",
    }
    issues = _version_issues(pack, old, "confirmed_profile")
    fields = {i.field.rsplit(".", 1)[1] for i in issues if i.code == "version_changed"}
    assert fields == {"schema_version", "questionnaire_version", "vocabulary_version"}
    assert all("重新" in i.next_action_zh for i in issues)
