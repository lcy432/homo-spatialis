"""输入目录（DECISIONS/0046）：分类索引、搜索规则，以及资格换算与 E3B 已有规则一致。

* 目录只引用资料包已有的键：每个职业恰好一个主分组、全部可以浏览；引用错了、漏了、重复了都在加载时报错，不悄悄关掉新流程。
* 搜索：NFKC + casefold + 去首尾空白；空格分开的词全部命中；只筛已发布条目，空查询给出这个分组的全部、顺序固定；找不到就是空，
  不替换成别的职业。“程序员”找到软件开发工程师；“川菜”只找到中餐并带说明；“徒步”找不到任何类别（只有说明）。
* 换算：学历、证书与相关经验的回答经 ``product.qualifications.grants`` 得到的资格，与 E3B 资料包编译时的
  ``product.realdata.occupations.Scope.from_confirmed`` 逐项相同（Scope 按冻结的六城岗位资料重建）；每个职业的 ``requires`` 与
  ``Scope.required`` 相同。
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

import pytest
import yaml

from common.product_contracts import (
    CertificatesAnswer,
    EducationAnswer,
    ExperienceAnswer,
    ExperienceEntry,
)
from product import input_catalog as ic
from product.fixtures import make_draft
from product.pack import load_pack
from product.qualifications import grants
from product.realdata.occupations import Scope

REPO = Path(__file__).resolve().parents[2]
E3A = REPO / "configs/product/e3a_synthetic_pack.yaml"
E3B = REPO / "configs/product/e3b_us_v1_pack.yaml"
FROZEN_ITEMS = REPO / "data/frozen/life/us-v1/items"
GROUPS = (
    "餐饮零售",
    "办公客服",
    "IT 技术",
    "专业商务",
    "维修制造",
    "物流仓储",
    "清洁园艺",
    "文化运动",
    "管理协调",
)


@cache
def _pack(path: Path):
    return load_pack(path)


def _catalog(path: Path = E3B):
    pack = _pack(path)
    loaded = ic.input_catalog(pack)
    assert loaded is not None
    return pack, loaded, loaded.public()


@cache
def _scope() -> tuple[Scope, dict[str, str]]:
    """按冻结的六城岗位资料重建 E3B 的 ``Scope``（EP 入职教育与相关经验），以及 SOC → 概念编号。"""
    occ = yaml.safe_load(
        (REPO / "configs/intake/occupations_us_v1.yaml").read_text(encoding="utf-8")
    )
    ep: dict[str, dict[str, str]] = {}
    for path in sorted(FROZEN_ITEMS.glob("[0-9]*[0-9].json")):
        for tpl in json.loads(path.read_text(encoding="utf-8"))["job_templates"]:
            row = {
                "typical_entry_education": tpl["ep"]["entry_education"],
                "related_work_experience": tpl["ep"]["related_experience"],
            }
            assert ep.setdefault(tpl["soc"], row) == row
    scope = Scope(occ, {"ep": ep})
    assert scope.problems() == []
    soc_of = {o.mappings[0].code: o.concept_id for o in _pack(E3B).body.occupations}
    return scope, soc_of


# ---------------------------------------------------------------------- 结构


@pytest.mark.parametrize("path", [E3A, E3B])
def test_every_published_occupation_is_browsable_in_exactly_one_group(path):
    pack, _, cat = _catalog(path)
    ids = [o.concept_id for o in cat.occupations]
    assert sorted(ids) == sorted(o.concept_id for o in pack.body.occupations)
    assert len(ids) == len(set(ids))
    groups = {g.group_id for g in cat.groups}
    assert all(o.group_id in groups for o in cat.occupations)
    assert all(any(o.group_id == g for o in cat.occupations) for g in groups)
    assert [o.order for o in cat.occupations] == list(range(1, len(ids) + 1))
    # 浏览顺序：先按分组顺序，再按配置里的顺序
    order = {g.group_id: g.order for g in cat.groups}
    assert [order[o.group_id] for o in cat.occupations] == sorted(
        order[o.group_id] for o in cat.occupations
    )


def test_e3b_has_the_nine_groups_and_all_forty_occupations():
    _, _, cat = _catalog()
    assert tuple(g.label for g in cat.groups) == GROUPS
    assert len(cat.occupations) == 40
    assert cat.sections.education and cat.sections.certificates and cat.sections.experience
    assert cat.sections.hard_requirements is False and cat.sections.attribute_preferences is False
    assert cat.sections.interest_text is False and cat.sections.occupation == "catalog_select"
    assert [o.label for o in cat.education] == [
        "高中以下",
        "高中",
        "读过大学但未取得学位",
        "副学士",
        "本科",
        "硕士",
        "博士或专业学位",
    ]
    # 职业证书单独问；学历门槛不在证书里
    assert {c.ability_id for c in cat.certificates} == {
        "soc_27_4011_award",
        "soc_49_2022_award",
        "soc_49_3023_award",
    }


def test_no_codes_in_the_public_catalog():
    """国家职业代码只在内部：公开目录里没有 SOC 代码。"""
    _, _, cat = _catalog()
    text = cat.model_dump_json()
    for o in _pack(E3B).body.occupations:
        assert o.mappings[0].code not in text


def test_synthetic_catalog_offers_only_certificates():
    _, _, cat = _catalog(E3A)
    assert not cat.sections.education and cat.sections.certificates and not cat.sections.experience
    assert cat.education == () and cat.experience == ()
    assert [c.ability_id for c in cat.certificates] == ["accounting_cert"]
    assert {o.concept_id: o.qualification_refs for o in cat.occupations}["occ.accountant"] == (
        "accounting_cert",
    )


@pytest.mark.parametrize("path", [E3A, E3B])
def test_panels_reference_only_existing_vocabulary(path):
    pack, _, cat = _catalog(path)
    for panel, vocab in (
        (cat.leisure, pack.body.interests.leisure_categories),
        (cat.taste, pack.body.interests.cuisines),
    ):
        assert sorted(e.key for e in panel.entries) == sorted(v.key for v in vocab)
        for n in panel.search_notes:
            assert set(n.points_to) <= {v.key for v in vocab}


@pytest.mark.parametrize("path", [E3A, E3B])
def test_english_names_are_registered_search_terms(path):
    """英文页面上看到的名称也能搜到（搜索词是明确维护的数据，不靠在线翻译）。"""
    pack, _, cat = _catalog(path)
    from product.display.catalog import load_entities

    ents = load_entities(pack.body.pack_id)
    for o in cat.occupations:
        assert ents["occupation"][o.concept_id]["label"]["en"] in o.search_terms, o.concept_id
        assert o.label in o.search_terms
    for kind, panel in (("leisure_category", cat.leisure), ("cuisine", cat.taste)):
        for e in panel.entries:
            assert ents[kind][e.key]["label"]["en"] in e.search_terms, e.key


def _broken(tmp_path, monkeypatch, edit):
    raw = yaml.safe_load(ic.catalog_path("pack.e3a.synthetic-v2").read_text(encoding="utf-8"))
    edit(raw["catalog"])
    (tmp_path / "pack.e3a.synthetic-v2.yaml").write_text(
        yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8"
    )
    monkeypatch.setattr(ic, "CATALOG_DIR", tmp_path)
    ic._load.cache_clear()
    ic._loaded.cache_clear()
    try:
        return ic.input_catalog(load_pack(E3A))
    finally:
        ic._load.cache_clear()
        ic._loaded.cache_clear()


@pytest.mark.parametrize(
    "edit",
    [
        lambda c: c["occupations"].pop(),  # 漏了一个职业
        lambda c: c["occupations"].append(dict(c["occupations"][0])),  # 一个职业两个分组
        lambda c: c["occupations"][0].update(concept_id="occ.teacher"),  # 资料包里没有的职业
        lambda c: c["occupations"][0].update(group="nowhere"),
        lambda c: c["groups"].append({"group_id": "empty", "label": "空"}),
        lambda c: c["certificates"][0].update(ability_id="edu_at_least_hs"),
        lambda c: c["sections"].update(education=True),  # 开了学历却没有选项
        lambda c: c["leisure"]["groups"][0]["members"].append("hiking"),  # 新增类别
        lambda c: c["taste"]["groups"].pop(),  # 菜系没有分组
    ],
)
def test_a_catalog_that_does_not_match_the_pack_is_refused(tmp_path, monkeypatch, edit):
    with pytest.raises(ic.CatalogError):
        _broken(tmp_path, monkeypatch, edit)


def test_the_catalog_identity_follows_its_content(tmp_path, monkeypatch):
    _, loaded, _ = _catalog(E3A)
    same = _broken(tmp_path, monkeypatch, lambda c: None)
    assert same is not None and same.fingerprint == loaded.fingerprint
    moved = _broken(tmp_path, monkeypatch, lambda c: c["occupations"][0]["search"].append("新叫法"))
    assert moved is not None and moved.fingerprint != loaded.fingerprint


# ---------------------------------------------------------------------- 搜索


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("程序员", ("occ.soc_15_1252",)),
        ("  ＰＲＯＧＲＡＭＭＥＲ　", ("occ.soc_15_1252",)),  # 全角字母与全角空格
        ("Software Developers", ("occ.soc_15_1252",)),
        ("软件 工程师", ("occ.soc_15_1252",)),  # 两个词都要命中
        ("软件 厨师", ()),
        ("收银", ("occ.cashier",)),
        ("cashier", ("occ.cashier",)),
        ("教师", ()),
        ("护士", ()),
        ("", None),  # 空查询：全部
    ],
)
def test_occupation_search_follows_the_published_rules(query, expected):
    _, _, cat = _catalog()
    got = ic.search_occupations(cat, query)
    if expected is None:
        assert got == tuple(o.concept_id for o in cat.occupations)
    else:
        assert got == expected


def test_empty_search_in_a_group_lists_the_group_in_a_fixed_order():
    _, _, cat = _catalog()
    it = ic.search_occupations(cat, "", "it_tech")
    assert it == ("occ.soc_15_1232", "occ.soc_15_1231", "occ.soc_15_1252", "occ.soc_15_1211")
    assert it == ic.search_occupations(cat, "   ", "it_tech")
    assert ic.search_occupations(cat, "店长", "it_tech") == ()


def test_search_only_filters_published_entries():
    _, _, cat = _catalog()
    published = {o.concept_id for o in cat.occupations}
    for q in ("文员", "经理", "维修", "a", "e", "工"):
        assert set(ic.search_occupations(cat, q)) <= published


def test_cuisine_search_finds_only_the_supported_chinese_with_a_note():
    _, _, cat = _catalog()
    keys, notes = ic.search_panel(cat.taste, "川菜")
    assert keys == ("chinese",) and notes == ("taste.chinese_regional",)
    note = next(n for n in cat.taste.search_notes if n.note_id == "taste.chinese_regional")
    assert note.points_to == ("chinese",) and "不区分" in note.note
    assert "sichuan" not in {e.key for e in cat.taste.entries}  # 没有单独的川菜类别
    keys, notes = ic.search_panel(cat.taste, "湘菜")
    assert keys == () and notes == ("taste.chinese_regional",)


def test_unsupported_hobbies_find_no_category_and_are_not_mapped_to_fitness():
    _, _, cat = _catalog()
    for q in ("徒步", "爬山", "hiking"):
        assert ic.search_panel(cat.leisure, q) == ((), ("leisure.outdoor_not_supported",))
    for q in ("打游戏", "gaming"):
        assert ic.search_panel(cat.leisure, q) == ((), ("leisure.games_not_supported",))
    assert ic.search_panel(cat.leisure, "跑步") == (("fitness",), ())
    assert ic.search_panel(cat.leisure, "FITNESS") == (("fitness",), ())


# ---------------------------------------------------------------------- 与 E3B 已有规则一致


def test_occupation_requirements_match_the_frozen_entry_requirements():
    scope, soc_of = _scope()
    _, _, cat = _catalog()
    concept_soc = {v: k for k, v in soc_of.items()}
    for o in cat.occupations:
        assert o.qualification_refs == scope.required(concept_soc[o.concept_id]), o.concept_id


def test_experience_questions_match_the_related_occupations_and_thresholds():
    scope, soc_of = _scope()
    _, loaded, cat = _catalog()
    concept_soc = {v: k for k, v in soc_of.items()}
    asked = {}
    for q in loaded.decl.experience:
        (target,) = q.applies_to
        soc = concept_soc[target]
        assert q.ability_id == f"soc_{soc.replace('-', '_')}_experience"
        assert [concept_soc[c] for c in q.related] == scope.supported[soc]["related"]
        assert q.min_years == scope.experience_of(soc)["min_years"]
        asked[soc] = q.question_id
    assert set(asked) == {s for s in scope.supported if scope.experience_of(s) is not None}
    for o in cat.occupations:
        assert o.experience_question == asked.get(concept_soc[o.concept_id])


def _granted(pack, catalog, **answers):
    draft = make_draft(pack).model_copy(update=answers)
    return sorted(g.ability_id for g in grants(catalog, draft))


def test_education_answers_convert_exactly_like_scope():
    scope, _ = _scope()
    pack, loaded, _ = _catalog()
    for o in loaded.decl.education:
        got = _granted(
            pack, loaded, education=EducationAnswer(state="provided", option_id=o.option_id)
        )
        assert got == scope.from_confirmed(education_level=o.level)["abilities"], o.option_id
    # 本科满足较低的学历门槛，但不授予职业证书
    bachelor = _granted(
        pack, loaded, education=EducationAnswer(state="provided", option_id="bachelor")
    )
    assert "edu_at_least_hs" in bachelor and not any(a.endswith("_award") for a in bachelor)
    for state in ("unanswered", "unsure"):
        assert _granted(pack, loaded, education=EducationAnswer(state=state)) == []


def test_certificate_answers_convert_exactly_like_scope():
    scope, soc_of = _scope()
    pack, loaded, _ = _catalog()
    concept_soc = {v: k for k, v in soc_of.items()}
    for c in loaded.decl.certificates:
        (occ,) = c.occupations
        got = _granted(
            pack,
            loaded,
            certificates=CertificatesAnswer(state="provided", selected=(c.ability_id,)),
        )
        assert (
            got
            == scope.from_confirmed(education_level=None, awards=(concept_soc[occ],))["abilities"]
        )
    assert (
        _granted(pack, loaded, certificates=CertificatesAnswer(state="provided")) == []
    )  # 明确“没有”
    assert _granted(pack, loaded, certificates=CertificatesAnswer(state="unsure")) == []


def test_experience_answers_convert_exactly_like_scope():
    scope, soc_of = _scope()
    pack, loaded, _ = _catalog()
    related = sorted({c for q in loaded.decl.experience for c in q.related})
    for concept in related:
        soc = {v: k for k, v in soc_of.items()}[concept]
        for years in range(0, 7):
            got = _granted(
                pack,
                loaded,
                experience=ExperienceAnswer(
                    state="provided", entries=(ExperienceEntry(concept_id=concept, years=years),)
                ),
            )
            want = scope.from_confirmed(education_level=None, experience=((soc, years),))
            assert got == want["abilities"], (concept, years)
    # 现职本身不算相关经验：没有填经历就什么都不授予
    assert _granted(pack, loaded, experience=ExperienceAnswer(state="provided")) == []
