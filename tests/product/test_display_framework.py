"""显示附层的基本规则（``product.display``）：中文与原来逐字相同；绑定只来自同一次生成；英文按冻结资源渲染并先核对中文；过时、
对不上、有歧义的照实报缺失；用户原文不绑定、不算覆盖；冻结资源不可覆盖、被改过就拒绝。"""

from __future__ import annotations

import json

import pytest
import yaml

from product.display import Code, Ent, Join, Months, Num, Raw, Sub, bind_json, label, recording, t
from product.display.catalog import DisplayResourceError, freeze, resources
from product.display.document import binding_document, binding_set, json_fingerprint
from product.display.render import BindingSet, display_fields
from product.display.text import months_zh

PACK = "a" * 64


def _catalog(tmp_path, templates, entities=None):
    src = tmp_path / "src"
    (src / "templates").mkdir(parents=True)
    (src / "entities").mkdir()
    (src / "templates" / "t.yaml").write_text(
        yaml.safe_dump({"templates": templates}, allow_unicode=True), encoding="utf-8"
    )
    (src / "entities" / "e.yaml").write_text(
        yaml.safe_dump({"entities": entities or {}}, allow_unicode=True), encoding="utf-8"
    )
    frozen = tmp_path / "frozen"
    version = freeze(PACK, "pack.test", root=src, frozen=frozen)
    return resources(PACK, version, frozen=frozen)


BASE = {
    "entity.label": {"zh": "{v}", "en": "{v}"},
    "fact.item_months": {"zh": "{item}（{months}）", "en": "{item} ({months})"},
    "note.cities": {"zh": "候选城市：{cities}", "en": "Candidate cities: {cities}"},
    "note.cash": {"zh": "现金 {amount}", "en": "Cash {amount}"},
}
ENT = {
    "city": {
        "US:X:1": {"label": {"zh": "西雅图", "en": "Seattle"}},
        "US:X:2": {"label": {"zh": "芝加哥", "en": "Chicago"}},
    }
}


def test_t_returns_the_same_chinese_and_months_keep_the_old_format():
    assert months_zh((1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)) == "1–12 月"
    assert months_zh((3, 5, 6)) == "3 月、5–6 月" and months_zh(()) == "无"
    with recording() as rec:
        a = t(
            "fact.item_months",
            "{item}（{months}）",
            item=Ent("job", "j1", "办公室文员·p50"),
            months=Months((1, 2, 3)),
        )
        b = t(
            "note.cities",
            "候选城市：{cities}",
            cities=Join((Ent("city", "US:X:1", "西雅图"), Ent("city", "US:X:2", "芝加哥"))),
        )
    assert a == "办公室文员·p50（1–3 月）" and b == "候选城市：西雅图、芝加哥"
    assert set(rec.by_text) == {a, b}
    assert (
        t("note.cash", "现金 {amount}", amount=Num("3,000.50")) == "现金 3,000.50"
    )  # 没有记录器时照样返回中文


def test_render_follows_the_chinese_check_and_reports_what_it_cannot_say(tmp_path):
    res = _catalog(tmp_path, BASE, ENT)
    with recording() as rec:
        msg = {
            "a": t(
                "note.cities",
                "候选城市：{cities}",
                cities=Join((Ent("city", "US:X:1", "西雅图"), Ent("city", "US:X:2", "芝加哥"))),
            ),
            "b": label("city", "US:X:1", "西雅图"),
            "c": t("note.cash", "现金 {amount}", amount=Raw("三千")),  # 没有译文的中文参数
            "d": t("note.new", "新的说法"),  # 资源里还没有
            "e": "没有绑定的中文",
            "f": "Plain English stays out",
            "user": {"text": "我喜欢西雅图"},
        }
    bset = BindingSet(tuple(bind_json(msg, rec, user_text=("/user",))), res, res.resource_version)
    fields, status, cov = display_fields(msg, [bset], "en", user_text=("/user",))
    by = {f["pointer"]: f for f in fields}
    assert (
        by["/a"]["text"] == "Candidate cities: Seattle, Chicago" and by["/b"]["text"] == "Seattle"
    )
    assert by["/b"]["key"] == "entity.city.label:US:X:1"
    assert by["/c"]["missing_reason"] == "untranslated_param"
    assert by["/d"]["missing_reason"] == "translation_missing"
    assert by["/e"]["missing_reason"] == "no_binding"
    assert "/f" not in by and "/user/text" not in by  # 不含中文的、用户原文都不算
    assert status == "partial" and cov["displayable"] == 5 and cov["provided"] == 2
    zh, zh_status, _ = display_fields(msg, [bset], "zh", user_text=("/user",))
    assert zh_status == "complete" and all(f["text"] == msg[f["pointer"][1:]] for f in zh)


def test_stale_translations_and_mismatched_chinese_are_not_shown(tmp_path):
    res = _catalog(
        tmp_path,
        {**BASE, "note.cash": {"zh": "旧的现金说法 {amount}", "en": "Old cash {amount}"}},
        ENT,
    )
    with recording() as rec:
        msg = {
            "a": t("note.cash", "现金 {amount}", amount=Num("5")),
            "b": label("city", "US:X:1", "西雅图（旧名）"),
        }
    bset = BindingSet(tuple(bind_json(msg, rec)), res, res.resource_version)
    by = {f["pointer"]: f for f in display_fields(msg, [bset], "en")[0]}
    assert by["/a"]["missing_reason"] == "translation_stale"  # 中文模板改了，英文还是旧的
    assert by["/b"]["missing_reason"] == "translation_stale"  # 实体的中文名与资源里的不同
    fresh = _catalog(tmp_path / "fresh", BASE, ENT)
    with recording() as rec2:
        good = {"a": t("note.cash", "现金 {amount}", amount=Num("5"))}
    cands = bind_json(good, rec2)[0]["candidates"]
    tampered = {"a": "现金 6"}  # 消息里的中文被改过：用绑定重算的中文对不上，不给英文
    fake = BindingSet(({"pointer": "/a", "candidates": cands},), fresh, fresh.resource_version)
    assert display_fields(tampered, [fake], "en")[0][0]["missing_reason"] == "source_changed"


def test_one_chinese_text_with_two_meanings_is_ambiguous(tmp_path):
    res = _catalog(
        tmp_path,
        {**BASE, "a.one": {"zh": "低档", "en": "Low tier"}, "a.two": {"zh": "低档", "en": "Basic"}},
    )
    with recording() as rec:
        t("a.one", "低档")
        t("a.two", "低档")
    msg = {"x": "低档"}
    bset = BindingSet(tuple(bind_json(msg, rec)), res, res.resource_version)
    assert display_fields(msg, [bset], "en")[0][0]["missing_reason"] == "ambiguous"


def test_nested_templates_and_codes_render_in_both_languages(tmp_path):
    res = _catalog(
        tmp_path,
        {
            **BASE,
            "outer": {"zh": "原因：{why}；代码 {code}", "en": "Reason: {why}; code {code}"},
            "inner": {"zh": "缺少{what}", "en": "missing {what}"},
        },
        ENT,
    )
    with recording() as rec:
        msg = {
            "x": t(
                "outer",
                "原因：{why}；代码 {code}",
                why=Sub("inner", "缺少{what}", (("what", Ent("city", "US:X:2", "芝加哥")),)),
                code=Code("E-12"),
            )
        }
    bset = BindingSet(tuple(bind_json(msg, rec)), res, res.resource_version)
    assert display_fields(msg, [bset], "en")[0][0]["text"] == "Reason: missing Chicago; code E-12"


def test_templates_must_have_the_same_slots_and_no_chinese_or_html_in_english(tmp_path):
    for bad in (
        {"zh": "共 {n} 个", "en": "{count} items"},
        {"zh": "共 {n} 个", "en": "共 {n} items"},
        {"zh": "共 {n} 个", "en": "<b>{n}</b>"},
    ):
        with pytest.raises(DisplayResourceError):
            _catalog(tmp_path / str(abs(hash(json.dumps(bad)))), {"k": bad})


def test_frozen_versions_follow_content_and_are_never_overwritten(tmp_path):
    res = _catalog(tmp_path, BASE, ENT)
    again = _catalog(tmp_path / "again", BASE, ENT)
    assert res.resource_version == again.resource_version  # 同样的内容同一个版本
    other = _catalog(tmp_path / "other", {**BASE, "extra": {"zh": "多一条", "en": "One more"}}, ENT)
    assert other.resource_version != res.resource_version
    target = tmp_path / "frozen" / PACK / res.resource_version / "resources.json"
    target.write_text(
        target.read_text(encoding="utf-8").replace("Seattle", "Tacoma"), encoding="utf-8"
    )
    from product.display.catalog import _load_frozen

    _load_frozen.cache_clear()
    with pytest.raises(DisplayResourceError):
        resources(PACK, res.resource_version, frozen=tmp_path / "frozen")
    assert resources(PACK, "e3-display-res-v1-0000000000000000", frozen=tmp_path / "frozen") is None


def test_a_binding_document_is_void_for_another_version_of_its_object(tmp_path):
    with recording() as rec:
        msg = {"a": t("note.cash", "现金 {amount}", amount=Num("5"))}
    doc = binding_document(msg, rec, pack_fingerprint=PACK)
    assert doc["source_fingerprint"] == json_fingerprint(msg)
    assert binding_set(doc, msg).valid and not binding_set(doc, {"a": "现金 6"}).valid
    prefixed = binding_set(doc, msg, prefix="/preflight")
    assert prefixed.bindings[0]["pointer"] == "/preflight/a"


def test_immediate_replies_do_not_copy_raw_fragments_into_english(tmp_path):
    res = _catalog(
        tmp_path, {**BASE, "issue.value": {"zh": "“{v}”不是数字", "en": '"{v}" is not a number'}}
    )
    with recording() as rec:
        msg = {"m": t("issue.value", "“{v}”不是数字", v=Raw("12,3x"))}
    bset = BindingSet(tuple(bind_json(msg, rec)), res, res.resource_version)
    (shown,), _, _ = display_fields(msg, [bset], "en")
    assert shown["text"] == '"12,3x" is not a number'  # 审阅页：本人填的值照抄（与中文原文相同）
    (withheld,), status, _ = display_fields(msg, [bset], "en", raw_params=False)
    assert withheld["text"] is None and withheld["missing_reason"] == "untranslated_param"
    assert status == "unavailable"  # 即时回复：译文里不放用户输入，页面显示中文原文
    (zh,), _, _ = display_fields(msg, [bset], "zh", raw_params=False)
    assert zh["text"] == msg["m"]


def test_the_same_template_never_borrows_another_instances_values(tmp_path):
    res = _catalog(tmp_path, BASE, ENT)
    with recording() as rec:
        msg = {
            "jobs": [
                t(
                    "fact.item_months",
                    "{item}（{months}）",
                    item=Ent("city", "US:X:1", "西雅图"),
                    months=Months((1, 2, 3)),
                ),
                t(
                    "fact.item_months",
                    "{item}（{months}）",
                    item=Ent("city", "US:X:2", "芝加哥"),
                    months=Months((5,)),
                ),
            ]
        }
    bset = BindingSet(tuple(bind_json(msg, rec)), res, res.resource_version)
    fields, status, _ = display_fields(msg, [bset], "en")
    assert status == "complete"
    first, second = (f["text"] for f in fields)
    assert first.startswith("Seattle (") and second.startswith("Chicago (")
    assert first != second and "Chicago" not in first and "Seattle" not in second
    assert {f["key"] for f in fields} == {"fact.item_months"}  # 同一个模板键，各自的参数


def test_copied_texts_borrow_the_bindings_of_the_message_they_came_from(tmp_path):
    from product.display.document import adopt_copies

    res = _catalog(tmp_path, BASE, ENT)
    with recording() as rec:
        pre = {"planner": t("note.cash", "现金 {amount}", amount=Num("20"))}
    pre_doc = binding_document(pre, rec, pack_fingerprint=PACK)
    with recording() as rec2:
        result = {
            "planner": pre["planner"],
            "own": label("city", "US:X:1", "西雅图"),
        }  # 照抄 + 自己生成
    assert (
        adopt_copies(rec2, pre, {**pre_doc, "source_fingerprint": "0" * 64}) == 0
    )  # 另一版的绑定不借
    assert adopt_copies(rec2, pre, pre_doc) == 1
    bset = BindingSet(tuple(bind_json(result, rec2)), res, res.resource_version)
    fields, status, _ = display_fields(result, [bset], "en")
    assert status == "complete"
    assert {f["pointer"]: f["text"] for f in fields} == {"/planner": "Cash 20", "/own": "Seattle"}
