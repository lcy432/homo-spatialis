"""输入目录（E3 输入体验收敛，DECISIONS/0046）：职业的分类索引与搜索词、学历 / 证书 / 相关经验问题、兴趣与菜系的分组。

每个资料包可以配一份独立的小配置 ``configs/product/input_catalog/<pack_id>.yaml``。它只引用资料包里已有的职业概念、资格编号与
兴趣 / 菜系的键：加载时逐项核对（引用不存在、职业没有分组或分了两个组、板块与选项对不上都报错），不新增职业、资格、活动、评分
类别或系数，也不改资料包本身（资料包的内容身份不变）。

* **内容身份**：配置（规范 JSON）加所引用资料包的内容身份。按目录选职业的草稿带着它；目录换了，旧草稿里的选择算过期，要重新选。
* **搜索**（``catalog_search_nfkc_casefold_all_terms_v1``）：Unicode NFKC（全角半角统一）、``casefold``（英文不分大小写）、首尾空白
  忽略；用空白分开的几个词都要命中，每个词出现在这个条目的任意一个搜索词里就算命中。只筛选已发布的条目，不生成新条目；空查询给出
  （当前分组的）全部条目，顺序固定。搜索词照原文，不翻译；E5 在完整的小目录里筛选，服务端只认提交的编号。
* 资格换算不在这里：``product.qualifications``。
"""

from __future__ import annotations

import unicodedata
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from common.product_contracts import (
    CatalogGroup,
    CatalogOccupation,
    CatalogSearchRules,
    CertificateOption,
    EducationOption,
    ExperienceQuestion,
    InputCatalog,
    InputSections,
    InterestEntry,
    InterestGroup,
    InterestPanel,
    QualificationStateText,
    SearchNote,
    content_fingerprint,
)
from product.display import Ent, Join, Num, Sub, label, say, t
from product.occupation import duties
from product.pack import DataPack

REPO = Path(__file__).resolve().parents[2]
CATALOG_DIR = REPO / "configs/product/input_catalog"
SEARCH_METHOD = "catalog_search_nfkc_casefold_all_terms_v1"


class CatalogError(ValueError):
    """输入目录的配置不合法，或与资料包对不上。"""


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class GroupDecl(_Strict):
    group_id: str
    label: str


class OccupationEntryDecl(_Strict):
    concept_id: str
    group: str
    requires: tuple[str, ...] = ()
    search: tuple[str, ...] = ()


class EducationOptionDecl(_Strict):
    option_id: str
    #: configs/intake/occupations_us_v1.yaml 教育表里的等级（只作说明与核对；换算按这里的顺序与 ability）
    level: str
    label: str
    ability: str | None = None


class CertificateDecl(_Strict):
    ability_id: str
    occupations: tuple[str, ...] = Field(min_length=1)


class ExperienceDecl(_Strict):
    question_id: str
    ability_id: str
    applies_to: tuple[str, ...] = Field(min_length=1)
    related: tuple[str, ...] = Field(min_length=1)
    min_years: int = Field(ge=1)


class InterestGroupDecl(_Strict):
    group_id: str
    label: str
    members: tuple[str, ...] = Field(min_length=1)


class SearchNoteDecl(_Strict):
    note_id: str
    terms: tuple[str, ...] = Field(min_length=1)
    points_to: tuple[str, ...] = ()
    note: str


class PanelDecl(_Strict):
    groups: tuple[InterestGroupDecl, ...] = Field(min_length=1)
    search: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    search_notes: tuple[SearchNoteDecl, ...] = ()


class SectionsDecl(_Strict):
    education: bool
    certificates: bool
    experience: bool


class CatalogDecl(_Strict):
    catalog_id: str
    catalog_version: str
    pack_id: str
    sections: SectionsDecl
    groups: tuple[GroupDecl, ...] = Field(min_length=1)
    occupations: tuple[OccupationEntryDecl, ...] = Field(min_length=1)
    education: tuple[EducationOptionDecl, ...] = ()
    certificates: tuple[CertificateDecl, ...] = ()
    experience: tuple[ExperienceDecl, ...] = ()
    leisure: PanelDecl
    taste: PanelDecl


class LoadedCatalog:
    """一份已核对的输入目录（配置 + 它引用的资料包）。"""

    def __init__(self, decl: CatalogDecl, pack: DataPack) -> None:
        self.decl = decl
        self.pack = pack
        problems = self._problems()
        if problems:
            raise CatalogError("输入目录与资料包对不上：\n  " + "\n  ".join(problems))
        self.fingerprint = content_fingerprint(
            {"catalog": decl.model_dump(mode="json"), "pack_fingerprint": pack.fingerprint}
        )
        self.occupations = {o.concept_id: o for o in decl.occupations}
        self.education = {o.option_id: o for o in decl.education}
        self.certificates = {c.ability_id: c for c in decl.certificates}
        self.experience = {q.question_id: q for q in decl.experience}
        self.question_of = {occ: q for q in decl.experience for occ in q.applies_to}

    def _problems(self) -> list[str]:
        d, b = self.decl, self.pack.body
        out: list[str] = []
        if d.pack_id != b.pack_id:
            out.append(f"目录写的资料包 {d.pack_id} 不是 {b.pack_id}")
        concepts = {o.concept_id for o in b.occupations}
        abilities = {a.ability_id for a in b.abilities}
        groups = [g.group_id for g in d.groups]
        if len(groups) != len(set(groups)):
            out.append(f"职业分组重复：{groups}")
        listed = [o.concept_id for o in d.occupations]
        for c in sorted({c for c in listed if listed.count(c) > 1}):
            out.append(f"职业 {c} 出现了不止一次（每个职业恰好一个主分组）")
        for c in sorted(concepts - set(listed)):
            out.append(f"资料包的职业 {c} 不在目录里（每个职业都要能浏览）")
        for o in d.occupations:
            if o.concept_id not in concepts:
                out.append(f"目录里的职业 {o.concept_id} 不在资料包里")
            if o.group not in groups:
                out.append(f"职业 {o.concept_id} 的分组 {o.group} 没有声明")
            for a in o.requires:
                if a not in abilities:
                    out.append(f"职业 {o.concept_id} 要求的资格 {a} 不在资料包里")
            if any(not s.strip() for s in o.search):
                out.append(f"职业 {o.concept_id} 有空白的搜索词")
        for g in groups:
            if g not in {o.group for o in d.occupations}:
                out.append(f"职业分组 {g} 没有任何职业")
        for name, on, items in (
            ("学历", d.sections.education, d.education),
            ("证书", d.sections.certificates, d.certificates),
            ("相关经验", d.sections.experience, d.experience),
        ):
            if on != bool(items):
                out.append(f"{name}板块{'开了却没有' if on else '没开却写了'}题目")
        ids = [o.option_id for o in d.education]
        if len(ids) != len(set(ids)) or len({o.level for o in d.education}) != len(ids):
            out.append(f"学历选项或等级重复：{ids}")
        for o in d.education:
            if o.ability is not None and o.ability not in abilities:
                out.append(f"学历选项 {o.option_id} 的资格 {o.ability} 不在资料包里")
        edu = {o.ability for o in d.education if o.ability}
        for c in d.certificates:
            if c.ability_id not in abilities:
                out.append(f"证书 {c.ability_id} 不在资料包里")
            if c.ability_id in edu:
                out.append(f"证书 {c.ability_id} 是学历门槛（学历不能当证书问）")
            for occ in c.occupations:
                if occ not in concepts:
                    out.append(f"证书 {c.ability_id} 的职业 {occ} 不在资料包里")
        seen: set[str] = set()
        for q in d.experience:
            if q.ability_id not in abilities:
                out.append(f"经验问题 {q.question_id} 的资格 {q.ability_id} 不在资料包里")
            for occ in (*q.applies_to, *q.related):
                if occ not in concepts:
                    out.append(f"经验问题 {q.question_id} 的职业 {occ} 不在资料包里")
            for occ in q.applies_to:
                if occ in seen:
                    out.append(f"职业 {occ} 有不止一个经验问题")
                seen.add(occ)
        for kind, panel, vocab in (
            ("兴趣", d.leisure, b.interests.leisure_categories),
            ("菜系", d.taste, b.interests.cuisines),
        ):
            keys = {v.key for v in vocab}
            members = [m for g in panel.groups for m in g.members]
            pgroups = [g.group_id for g in panel.groups]
            if len(pgroups) != len(set(pgroups)):
                out.append(f"{kind}分组重复：{pgroups}")
            for m in sorted({m for m in members if members.count(m) > 1}):
                out.append(f"{kind}类别 {m} 出现了不止一次")
            for m in sorted(set(members) - keys):
                out.append(f"{kind}分组里的 {m} 不在资料包词表里（不新增类别）")
            for m in sorted(keys - set(members)):
                out.append(f"资料包的{kind}类别 {m} 不在任何分组里")
            for m in sorted(set(panel.search) - keys):
                out.append(f"{kind}搜索词写给了词表外的 {m}")
            for n in panel.search_notes:
                for m in n.points_to:
                    if m not in keys:
                        out.append(f"{kind}搜索说明 {n.note_id} 指向词表外的 {m}")
        return out

    # ---- 公开目录

    def occupation_terms(self, concept_id: str) -> tuple[str, ...]:
        """一个职业发布的搜索词：资料包里的名称与别名，加上目录补充的叫法（去重，保持顺序）。"""
        concept = self.pack.occupation(concept_id)
        assert concept is not None
        entry = self.occupations[concept_id]
        return tuple(dict.fromkeys((concept.label, *concept.aliases, *entry.search)))

    def panel_terms(self, kind: str, key: str) -> tuple[str, ...]:
        b = self.pack.body
        vocab = b.interests.leisure_categories if kind == "leisure" else b.interests.cuisines
        term = next(v for v in vocab if v.key == key)
        panel = self.decl.leisure if kind == "leisure" else self.decl.taste
        return tuple(dict.fromkeys((term.label, *term.aliases, *panel.search.get(key, ()))))

    def public(self) -> InputCatalog:
        """能力清单里的 ``input_catalog``（名称与说明按 ``kind/编号`` 生成显示文字；搜索词照原文）。"""
        d, b = self.decl, self.pack.body
        group_order = {g.group_id: i for i, g in enumerate(d.groups)}
        ordered = sorted(enumerate(d.occupations), key=lambda x: (group_order[x[1].group], x[0]))
        ability_label = {a.ability_id: a.label for a in b.abilities}
        names = {o.concept_id: o.label for o in b.occupations}

        def occ(c: str) -> Ent:
            return Ent("occupation", c, names[c])

        return InputCatalog(
            catalog_id=d.catalog_id,
            catalog_version=d.catalog_version,
            content_fingerprint=self.fingerprint,
            pack_id=d.pack_id,
            sections=InputSections(
                education=d.sections.education,
                certificates=d.sections.certificates,
                experience=d.sections.experience,
            ),
            search=CatalogSearchRules(
                method=SEARCH_METHOD,
                rules=(
                    t(
                        "input.catalog.search.rule.normalize",
                        "全角与半角统一、英文不分大小写、首尾空白忽略",
                    ),
                    t(
                        "input.catalog.search.rule.all_terms",
                        "用空格分开的几个词要全部命中：每个词出现在这个条目的任意一个搜索词里就算命中",
                    ),
                    t(
                        "input.catalog.search.rule.scope",
                        "只在目录已经发布的条目里筛选，不生成新条目；搜索框里的字不保存，也不进入画像",
                    ),
                    t(
                        "input.catalog.search.rule.empty",
                        "搜索为空时显示当前分组的全部条目，顺序固定",
                    ),
                    t(
                        "input.catalog.search.rule.server",
                        "提交的是选中条目的编号；服务端核对编号与目录版本，不认搜索词",
                    ),
                ),
                no_match=t(
                    "input.catalog.search.no_match",
                    "目录里没有找到匹配的职业。本版只支持目录里的 {count} 种职业（教师、护士、医生等暂不在范围内）；请按分类浏览，"
                    "或换一个常用叫法。系统不会替你换成别的职业",
                    count=Num(str(len(d.occupations))),
                ),
            ),
            groups=tuple(
                CatalogGroup(
                    group_id=g.group_id,
                    label=label("catalog_group", g.group_id, g.label),
                    order=i + 1,
                )
                for i, g in enumerate(d.groups)
            ),
            occupations=tuple(
                CatalogOccupation(
                    concept_id=o.concept_id,
                    group_id=o.group,
                    order=n + 1,
                    label=label("occupation", o.concept_id, names[o.concept_id]),
                    duties=duties(self.pack.occupation(o.concept_id)),  # type: ignore[arg-type]
                    search_terms=self.occupation_terms(o.concept_id),
                    qualification_refs=o.requires,
                    experience_question=None
                    if o.concept_id not in self.question_of
                    else self.question_of[o.concept_id].question_id,
                )
                for n, (_i, o) in enumerate(ordered)
            ),
            education=tuple(
                EducationOption(
                    option_id=o.option_id,
                    label=label("education_option", o.option_id, o.label),
                    order=i + 1,
                )
                for i, o in enumerate(d.education)
            ),
            certificates=tuple(
                CertificateOption(
                    ability_id=c.ability_id,
                    label=label("ability", c.ability_id, ability_label[c.ability_id]),
                    occupations=c.occupations,
                )
                for c in d.certificates
            ),
            experience=tuple(
                ExperienceQuestion(
                    question_id=q.question_id,
                    ability_id=q.ability_id,
                    applies_to=q.applies_to,
                    related=q.related,
                    prompt=t(
                        "input.catalog.experience.prompt",
                        "{occupation}的开局岗位要求相关工作经验：在{related}里至少做过 {years} 年。请填你在这些职业里各做过几年；"
                        "没有就回答“没有相关经验”",
                        occupation=Join(tuple(occ(c) for c in q.applies_to)),
                        related=Join(tuple(occ(c) for c in q.related)),
                        years=Num(str(q.min_years)),
                    ),
                )
                for q in d.experience
            ),
            qualification_states=qualification_states(d),
            leisure=self._panel("leisure"),
            taste=self._panel("taste"),
            notes=(
                t(
                    "input.catalog.note.groups",
                    "职业分组只用于浏览，不改变职业的含义；每个职业只在一个分组里",
                ),
                t(
                    "input.catalog.note.no_grant",
                    "选职业不会授予学历、证书或经验；这些按你在资格题里的回答换算（规则由 E3 统一执行）",
                ),
                t(
                    "input.catalog.note.free_text",
                    "补充文字仍可以写，只供分身做计划参考：它不改变点数、现金、资格或兴趣类别，也不会变成硬要求",
                ),
            ),
        )

    def _panel(self, kind: str) -> InterestPanel:
        b = self.pack.body
        panel = self.decl.leisure if kind == "leisure" else self.decl.taste
        vocab = b.interests.leisure_categories if kind == "leisure" else b.interests.cuisines
        labels = {v.key: v.label for v in vocab}
        entity = "leisure_category" if kind == "leisure" else "cuisine"
        group_entity = f"{kind}_group"
        entries = [(g, m) for g in panel.groups for m in g.members]
        return InterestPanel(
            kind=kind,  # type: ignore[arg-type]
            groups=tuple(
                InterestGroup(
                    group_id=g.group_id,
                    label=label(group_entity, g.group_id, g.label),
                    order=i + 1,
                )
                for i, g in enumerate(panel.groups)
            ),
            entries=tuple(
                InterestEntry(
                    key=m,
                    group_id=g.group_id,
                    order=i + 1,
                    label=label(entity, m, labels[m]),
                    search_terms=self.panel_terms(kind, m),
                )
                for i, (g, m) in enumerate(entries)
            ),
            search_notes=tuple(
                SearchNote(
                    note_id=n.note_id,
                    terms=n.terms,
                    points_to=n.points_to,
                    note=label("search_note", n.note_id, n.note, "note"),
                )
                for n in panel.search_notes
            ),
            note=t(
                "input.catalog.leisure.note",
                "可以多选，也可以跳过（跳过就是未答）；选中的类别直接用于评价，不需要写文字。没有固定爱好、随当地体验（通用休闲评价）"
                "和暂不确定可以单独选",
            )
            if kind == "leisure"
            else t(
                "input.catalog.taste.note",
                "可以多选，也可以跳过（跳过就是未答）；选中的菜系直接用于饮食评价，不需要写文字。没有口味偏好和暂不确定可以单独选",
            ),
        )


#: 资格板块每种作答状态的名称与含义（显示文字由 E3 生成；E5 照着显示，不自己判断授予）
QUALIFICATION_STATES: dict[str, tuple[tuple[str, Sub, Sub], ...]] = {
    "education": (
        (
            "unanswered",
            Sub("input.catalog.state.education.unanswered.name", "未答"),
            Sub("input.catalog.state.education.unanswered.meaning", "不授予任何学历门槛"),
        ),
        (
            "unsure",
            Sub("input.catalog.state.education.unsure.name", "暂不确定"),
            Sub(
                "input.catalog.state.education.unsure.meaning",
                "按没有确认学历处理：不授予学历门槛",
            ),
        ),
        (
            "provided",
            Sub("input.catalog.state.education.provided.name", "已选择最高学历"),
            Sub(
                "input.catalog.state.education.provided.meaning",
                "授予这一级和它以下各级的学历门槛；不授予职业证书",
            ),
        ),
    ),
    "certificates": (
        (
            "unanswered",
            Sub("input.catalog.state.certificates.unanswered.name", "未答"),
            Sub("input.catalog.state.certificates.unanswered.meaning", "不授予任何证书"),
        ),
        (
            "unsure",
            Sub("input.catalog.state.certificates.unsure.name", "暂不确定"),
            Sub("input.catalog.state.certificates.unsure.meaning", "不授予证书"),
        ),
        (
            "provided",
            Sub("input.catalog.state.certificates.provided.name", "已回答"),
            Sub(
                "input.catalog.state.certificates.provided.meaning",
                "授予选中的证书；一张都不选表示没有这些证书",
            ),
        ),
    ),
    "experience": (
        (
            "unanswered",
            Sub("input.catalog.state.experience.unanswered.name", "未答"),
            Sub("input.catalog.state.experience.unanswered.meaning", "不授予相关经验"),
        ),
        (
            "unsure",
            Sub("input.catalog.state.experience.unsure.name", "暂不确定"),
            Sub("input.catalog.state.experience.unsure.meaning", "不授予相关经验"),
        ),
        (
            "provided",
            Sub("input.catalog.state.experience.provided.name", "已回答"),
            Sub(
                "input.catalog.state.experience.provided.meaning",
                "按填写的职业与年数判断是否满足；不填任何经历表示没有相关经验",
            ),
        ),
    ),
}


def qualification_states(d: CatalogDecl) -> tuple[QualificationStateText, ...]:
    """开着的资格板块每种作答状态的名称与含义（按状态给出，不靠顺序）。"""
    out = []
    for section, on in (
        ("education", d.sections.education),
        ("certificates", d.sections.certificates),
        ("experience", d.sections.experience),
    ):
        if not on:
            continue
        for state, name, meaning in QUALIFICATION_STATES[section]:
            out.append(
                QualificationStateText(
                    section=section,  # type: ignore[arg-type]
                    state=state,  # type: ignore[arg-type]
                    name=say(name),
                    meaning=say(meaning),
                )
            )
    return tuple(out)


@lru_cache(maxsize=16)
def _load(path: str) -> CatalogDecl | None:
    p = Path(path)
    if not p.is_file():
        return None
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "catalog" not in raw:
        raise CatalogError(f"{p.name}: 顶层必须有 catalog 一节")
    try:
        return CatalogDecl.model_validate(raw["catalog"])
    except ValidationError as exc:
        raise CatalogError(f"{p.name}: 输入目录结构不合法：{exc}") from exc


@lru_cache(maxsize=16)
def _loaded(path: str, pack: DataPack) -> LoadedCatalog | None:
    decl = _load(path)
    return None if decl is None else LoadedCatalog(decl, pack)


def catalog_path(pack_id: str, root: Path | None = None) -> Path:
    return (root or CATALOG_DIR) / f"{pack_id}.yaml"


def input_catalog(pack: DataPack) -> LoadedCatalog | None:
    """这个资料包的输入目录；没有配目录就是 ``None``（只有兼容的文字匹配）。配了却与资料包对不上就报错（不悄悄关掉新流程）。"""
    return _loaded(str(catalog_path(pack.body.pack_id)), pack)


# ---------------------------------------------------------------------- 搜索（公开规则的参考实现与测试用；服务端不提供搜索服务）


def normalize(text: str) -> str:
    """NFKC（全角半角统一）+ casefold（英文不分大小写）+ 去掉首尾空白。"""
    return unicodedata.normalize("NFKC", text).casefold().strip()


def keywords(query: str) -> tuple[str, ...]:
    return tuple(normalize(query).split())


def _hit(words: tuple[str, ...], terms: tuple[str, ...]) -> bool:
    texts = [normalize(s) for s in terms]
    return all(any(w in s for s in texts) for w in words)


def search_occupations(
    catalog: InputCatalog, query: str, group_id: str | None = None
) -> tuple[str, ...]:
    """按公开规则筛选职业，返回概念编号（目录顺序）。``group_id`` 只在这个分组里筛；空查询返回（这个分组的）全部条目。"""
    words = keywords(query)
    pool = [o for o in catalog.occupations if group_id is None or o.group_id == group_id]
    pool.sort(key=lambda o: o.order)
    return tuple(o.concept_id for o in pool if not words or _hit(words, o.search_terms))


def search_panel(
    panel: InterestPanel, query: str, group_id: str | None = None
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """兴趣或菜系面板的筛选：返回（命中的键，命中的搜索说明编号）。说明只解释，不新增类别：说明指向的键照样要在词表里。"""
    words = keywords(query)
    pool = sorted(
        (e for e in panel.entries if group_id is None or e.group_id == group_id),
        key=lambda e: e.order,
    )
    if not words:
        return tuple(e.key for e in pool), ()
    keys = [e.key for e in pool if _hit(words, e.search_terms)]
    notes = [n.note_id for n in panel.search_notes if _hit(words, n.terms)]
    return tuple(keys), tuple(notes)
