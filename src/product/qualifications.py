"""学历、证书与相关经验的结构化答案 → 资格（E3 输入体验收敛，DECISIONS/0046；规则 ``confirmed_experience_mapping_v1``）。

与 E3B 资料包编译时用的 ``product.realdata.occupations.Scope.from_confirmed`` 是同一条规则（测试按冻结资料逐项核对）：

* 学历（``education_at_least_v1``）：选中的最高学历授予它和它以下各级的“至少达到”；职业证书不由学历授予。
* 证书：只授予选中的那张（输入目录列出的职业专属证书）。
* 相关经验（``related_experience_v1``）：经验问题 Y 的 ``related`` 里有一段经历、年数达到 Y 的 ``min_years``，就授予 Y 的经验资格。

职业、职称、自由文字与搜索词都不授予资格；未答、暂不确定不授予任何资格。新的结构化答案与旧的逐项资格确认（``abilities``）同时
出现、给出的资格又不一样，就拒绝，不取并集。这些判断都在 E3，E5 只显示目录与审阅里的结果。
"""

from __future__ import annotations

from dataclasses import dataclass

from common.product_contracts import (
    ConfirmedQualifications,
    ProductIssue,
    QualificationGrant,
    QualificationRequirement,
    QualificationReview,
    UserInputDraft,
)
from product.display import Ent, Join, Num, Raw, Sub, say, t
from product.input_catalog import LoadedCatalog
from product.pack import DataPack

RULE = "confirmed_experience_mapping_v1"
SECTIONS = ("education", "certificates", "experience")
_SECTION_NAME = {
    "education": Sub("input.qualification.section.education", "最高学历"),
    "certificates": Sub("input.qualification.section.certificates", "职业证书"),
    "experience": Sub("input.qualification.section.experience", "相关工作经验"),
}


@dataclass(frozen=True)
class Grant:
    ability_id: str
    because: Sub


def structured_answered(draft: UserInputDraft) -> bool:
    """学历、证书、经验里有没有任何一项不是“未答”。"""
    return any(getattr(draft, s).state != "unanswered" for s in SECTIONS)


def grants(catalog: LoadedCatalog, draft: UserInputDraft) -> list[Grant]:
    """已经核对过的结构化答案 → 资格（按目录顺序：学历由低到高、证书、经验；不重复）。"""
    out: dict[str, Grant] = {}
    d = catalog.decl
    edu = draft.education
    if edu.state == "provided" and edu.option_id in catalog.education:
        top = [o.option_id for o in d.education].index(edu.option_id)
        chosen = catalog.education[edu.option_id]
        for o in d.education[: top + 1]:
            if o.ability:
                out.setdefault(
                    o.ability,
                    Grant(
                        o.ability,
                        Sub(
                            "input.qualification.because.education",
                            "最高学历“{option}”：至少达到这一级（更高的学历也满足较低的学历门槛）",
                            (
                                (
                                    "option",
                                    Ent("education_option", chosen.option_id, chosen.label),
                                ),
                            ),
                        ),
                    ),
                )
    cert = draft.certificates
    if cert.state == "provided":
        for aid in cert.selected:
            if aid in catalog.certificates:
                out.setdefault(
                    aid,
                    Grant(
                        aid, Sub("input.qualification.because.certificate", "你回答持有这张证书")
                    ),
                )
    exp = draft.experience
    if exp.state == "provided":
        names = {o.concept_id: o.label for o in catalog.pack.body.occupations}
        for q in d.experience:
            for e in exp.entries:
                if e.concept_id in q.related and e.years >= q.min_years:
                    out.setdefault(
                        q.ability_id,
                        Grant(
                            q.ability_id,
                            Sub(
                                "input.qualification.because.experience",
                                "在“{occupation}”做过 {years} 年（这一项要求在相关职业里至少做过 {min} 年）",
                                (
                                    (
                                        "occupation",
                                        Ent("occupation", e.concept_id, names[e.concept_id]),
                                    ),
                                    ("years", Num(str(e.years))),
                                    ("min", Num(str(q.min_years))),
                                ),
                            ),
                        ),
                    )
                    break
    return list(out.values())


def review_qualifications(
    pack: DataPack,
    catalog: LoadedCatalog | None,
    draft: UserInputDraft,
    *,
    claimed: tuple[str, ...],
    concept_id: str | None,
) -> tuple[QualificationReview, list[ProductIssue], tuple[str, ...]]:
    """学历、证书与相关经验的审阅。``claimed`` 是旧的逐项资格确认里持有的资格（已经去掉重复与未知的）；``concept_id`` 是这次
    确认的职业（没有就是 ``None``）。返回（审阅、问题、开局持有的资格）。"""
    from product.answers import issue

    issues: list[ProductIssue] = []
    answered = structured_answered(draft)
    ability_label = {a.ability_id: a.label for a in pack.body.abilities}

    def ab(aid: str) -> Ent | Raw:
        return Ent("ability", aid, ability_label[aid]) if aid in ability_label else Raw(aid)

    if answered and catalog is None:
        issues.append(
            issue(
                "input_catalog_unavailable",
                "education",
                t(
                    "input.qualification.catalog_unavailable.message",
                    "这个资料包没有输入目录，不能按学历、证书或经验题换算资格",
                ),
                t(
                    "input.qualification.catalog_unavailable.impact",
                    "这些回答不会授予任何资格，画像不能确认",
                ),
                t(
                    "input.qualification.catalog_unavailable.next",
                    "清空这几题的回答，或换成配了目录的资料包",
                ),
            )
        )
    if catalog is not None:
        on = catalog.decl.sections
        for section in SECTIONS:
            if getattr(draft, section).state != "unanswered" and not getattr(on, section):
                issues.append(
                    issue(
                        "qualification_section_not_offered",
                        section,
                        t(
                            "input.qualification.section_not_offered.message",
                            "这一版的资料包不问“{section}”，却收到了回答",
                            section=_SECTION_NAME[section],
                        ),
                        t(
                            "input.qualification.section_not_offered.impact",
                            "这个回答不会授予任何资格，画像不能确认",
                        ),
                        t("input.qualification.section_not_offered.next", "把这一题改回未答"),
                    )
                )
        edu = draft.education
        if edu.state == "provided" and edu.option_id not in catalog.education:
            issues.append(
                issue(
                    "education_option_unknown",
                    "education.option_id",
                    t(
                        "input.qualification.education_unknown.message",
                        "最高学历选项 {option} 不在输入目录里",
                        option=Raw(str(edu.option_id)),
                    ),
                    t(
                        "input.qualification.education_unknown.impact",
                        "学历不会授予任何门槛，画像不能确认",
                    ),
                    t("input.qualification.education_unknown.next", "从目录列出的学历选项里选一项"),
                )
            )
        seen: set[str] = set()
        for aid in draft.certificates.selected:
            if aid in seen:
                issues.append(
                    issue(
                        "certificate_duplicated",
                        "certificates.selected",
                        t(
                            "input.qualification.certificate_duplicated.message",
                            "证书 {aid} 选了不止一次：系统不替你去重",
                            aid=ab(aid),
                        ),
                        t("input.qualification.certificate_duplicated.impact", "画像不能确认"),
                        t("input.qualification.certificate_duplicated.next", "每张证书只选一次"),
                    )
                )
            elif aid not in catalog.certificates:
                issues.append(
                    issue(
                        "certificate_unknown",
                        "certificates.selected",
                        t(
                            "input.qualification.certificate_unknown.message",
                            "{aid} 不是输入目录里的职业证书",
                            aid=ab(aid),
                        ),
                        t(
                            "input.qualification.certificate_unknown.impact",
                            "它不会授予任何资格（学历门槛不能当证书选），画像不能确认",
                        ),
                        t(
                            "input.qualification.certificate_unknown.next",
                            "只选目录列出的证书；学历在“最高学历”里回答",
                        ),
                    )
                )
            seen.add(aid)
        related = {c for q in catalog.decl.experience for c in q.related}
        names = {o.concept_id: o.label for o in pack.body.occupations}
        counted: set[str] = set()
        for e in draft.experience.entries:
            occ = (
                Ent("occupation", e.concept_id, names[e.concept_id])
                if e.concept_id in names
                else Raw(e.concept_id)
            )
            if e.concept_id in counted:
                issues.append(
                    issue(
                        "experience_duplicated",
                        "experience.entries",
                        t(
                            "input.qualification.experience_duplicated.message",
                            "“{occupation}”的经历写了不止一条：系统不替你合并年数",
                            occupation=occ,
                        ),
                        t("input.qualification.experience_duplicated.impact", "画像不能确认"),
                        t(
                            "input.qualification.experience_duplicated.next",
                            "每个职业只写一条，填一共做过几年",
                        ),
                    )
                )
            elif e.concept_id not in related:
                issues.append(
                    issue(
                        "experience_occupation_not_related",
                        "experience.entries",
                        t(
                            "input.qualification.experience_not_related.message",
                            "“{occupation}”不在任何相关经验问题列出的职业里",
                            occupation=occ,
                        ),
                        t(
                            "input.qualification.experience_not_related.impact",
                            "这段经历不会授予任何资格，画像不能确认",
                        ),
                        t(
                            "input.qualification.experience_not_related.next",
                            "只填问题里列出的相关职业",
                        ),
                    )
                )
            counted.add(e.concept_id)
    structured = [] if catalog is None or not answered else grants(catalog, draft)
    new = tuple(g.ability_id for g in structured)
    if answered and claimed and set(new) != set(claimed):
        issues.append(
            issue(
                "qualification_conflict",
                "abilities",
                t(
                    "input.qualification.conflict.message",
                    "资格同时用了新的学历 / 证书 / 经验回答和旧的逐项资格确认，两边给出的资格不一样（新回答：{new}；"
                    "旧确认：{old}）",
                    new=Join(tuple(ab(a) for a in new)) if new else _NONE,
                    old=Join(tuple(ab(a) for a in claimed)),
                ),
                t(
                    "input.qualification.conflict.impact",
                    "系统不合并两边、也不替你取舍，画像不能确认",
                ),
                t(
                    "input.qualification.conflict.next",
                    "只保留一种：按新的学历、证书与经验题回答，并清空旧的逐项资格确认",
                ),
            )
        )
    if answered:
        source = "structured_answers"
        held = new
        lines = [
            QualificationGrant(
                ability_id=g.ability_id,
                label=_label(pack, g.ability_id),
                because=say(g.because),
            )
            for g in structured
        ]
    elif claimed:
        source, held = "ability_claims", claimed
        lines = [
            QualificationGrant(
                ability_id=a,
                label=_label(pack, a),
                because=t("input.qualification.because.claim", "你在逐项资格确认里选了“已提供”"),
            )
            for a in claimed
        ]
    else:
        source, held, lines = "none", (), []
    requirements: list[QualificationRequirement] = []
    ask: tuple[str, ...] = ()
    if catalog is not None and concept_id in catalog.occupations:
        entry = catalog.occupations[concept_id]
        requirements = [
            QualificationRequirement(ability_id=a, label=_label(pack, a), met=a in held)
            for a in entry.requires
        ]
        if concept_id in catalog.question_of:
            ask = (catalog.question_of[concept_id].question_id,)
        missing = [r.ability_id for r in requirements if not r.met]
        if missing:
            issues.append(
                issue(
                    "qualification_requirement_unmet",
                    "occupation",
                    t(
                        "input.qualification.requirement_unmet.message",
                        "{occupation}的开局岗位要求：{missing}；按你现在的回答还不满足",
                        occupation=Ent(
                            "occupation", concept_id, _occupation_label(pack, concept_id)
                        ),
                        missing=Join(tuple(ab(a) for a in missing)),
                    ),
                    t(
                        "input.qualification.requirement_unmet.impact",
                        "这样各城的开局岗位会被挡下：系统不替你补资格，也不换成别的职业",
                    ),
                    t(
                        "input.qualification.requirement_unmet.next",
                        "如果你有，就在最高学历、证书或相关经验里如实回答；没有就选你实际在做的职业",
                    ),
                    severity="warning",
                )
            )
    if source == "structured_answers":
        message = (
            t(
                "input.qualification.review.structured",
                "资格按你的回答换算：{granted}",
                granted=Join(tuple(ab(g.ability_id) for g in structured)),
            )
            if structured
            else t(
                "input.qualification.review.structured_none",
                "按你的回答，没有得到任何资格门槛（未答与暂不确定都不授予）",
            )
        )
    elif source == "ability_claims":
        message = t(
            "input.qualification.review.claims",
            "资格来自逐项资格确认（兼容的写法）：{granted}",
            granted=Join(tuple(ab(a) for a in claimed)),
        )
    else:
        message = t(
            "input.qualification.review.none",
            "还没有回答最高学历、职业证书或相关经验：不授予任何资格",
        )
    review = QualificationReview(
        source=source,  # type: ignore[arg-type]
        education_state=draft.education.state,
        education_option=draft.education.option_id,
        certificates_state=draft.certificates.state,
        certificates=draft.certificates.selected,
        experience_state=draft.experience.state,
        experience=draft.experience.entries,
        ask_experience=ask,
        granted=tuple(lines),
        requirements=tuple(requirements),
        message_zh=message[:400],
    )
    return review, issues, tuple(held)


def confirmed(review: QualificationReview) -> ConfirmedQualifications:
    """审阅（已经可确认）→ 确认画像里的资格答案。"""
    return ConfirmedQualifications(
        source=review.source,
        education_state=review.education_state,
        education_option=review.education_option,
        certificates_state=review.certificates_state,
        certificates=review.certificates,
        experience_state=review.experience_state,
        experience=review.experience,
        rule=RULE,
    )


_NONE = Sub("input.qualification.none", "无")


def _label(pack: DataPack, aid: str) -> str:
    from product.display import label

    name = {a.ability_id: a.label for a in pack.body.abilities}.get(aid, aid)
    return label("ability", aid, name)


def _occupation_label(pack: DataPack, concept_id: str) -> str:
    concept = pack.occupation(concept_id)
    return concept.label if concept is not None else concept_id
