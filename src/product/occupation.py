"""职业匹配：受控词表 + 明确规则（E3-A，规则 ``controlled_vocabulary_alias_match_v1``）。

用户写自然语言（“我在餐厅做店员，有时收银，有时做饮品”）；系统把文字规范化（去空格、英文小写），找出别名出现在文字里的词表概念：

* 恰好 1 个概念 → ``explicit``：仍请用户确认这一个候选；
* 多于 1 个 → ``ambiguous``：给少量可读候选（名称与职责），由用户选；
* 0 个 → ``unknown``：说明原因，不自动改成“普通白领”，也不调用 LLM 猜。

分类代码（例如美国 SOC 2018）只在内部映射里保留，不出现在候选里，也不是用户的永久身份。职业不授予任何资格。
"""

from __future__ import annotations

from dataclasses import dataclass

from common.product_contracts import OccupationCandidate
from product.capabilities import MAX_OCCUPATION_CANDIDATES
from product.display import Ent, Join, label, t
from product.pack import DataPack, OccupationDecl


def _normalize(text: str) -> str:
    return "".join(text.split()).lower()


def duties(concept: OccupationDecl) -> tuple[str, ...]:
    """一个职业概念的职责（显示文字；编号是概念编号加序号）。"""
    return tuple(
        label("duty", f"{concept.concept_id}.{i}", d) for i, d in enumerate(concept.duties)
    )


@dataclass(frozen=True)
class OccupationMatch:
    status: str  # explicit / ambiguous / unknown / not_provided
    candidates: tuple[OccupationCandidate, ...]
    message_zh: str


def match_occupation(pack: DataPack, raw_text: str | None) -> OccupationMatch:
    if raw_text is None or not raw_text.strip():
        return OccupationMatch(
            "not_provided", (), t("input.occupation.not_provided", "还没有描述你的工作")
        )
    text = _normalize(raw_text)
    found: list[OccupationCandidate] = []
    for concept in pack.body.occupations:
        hits = tuple(a for a in concept.aliases if _normalize(a) in text)
        if hits:
            found.append(
                OccupationCandidate(
                    candidate_id=concept.concept_id,
                    label=label("occupation", concept.concept_id, concept.label),
                    duties=duties(concept),
                    matched_terms=hits,
                )
            )
    if not found:
        return OccupationMatch(
            "unknown",
            (),
            t(
                "input.occupation.unknown",
                "受控词表里没有找到与这段描述对应的职业：请换一种说法描述主要职责，或从候选城市的岗位类型里选择；系统不会自动归成"
                "“普通白领”",
            ),
        )
    names = tuple(Ent("occupation", c.candidate_id, c.label) for c in found)
    if len(found) == 1:
        return OccupationMatch(
            "explicit",
            tuple(found),
            t(
                "input.occupation.explicit",
                "看起来你的工作是“{label}”；请确认这个理解是否正确",
                label=names[0],
            ),
        )
    shown = tuple(found[:MAX_OCCUPATION_CANDIDATES])
    listed = Join(names[:MAX_OCCUPATION_CANDIDATES])
    if len(found) > MAX_OCCUPATION_CANDIDATES:
        message = t(
            "input.occupation.ambiguous_more",
            "这段描述对应不止一种职业：{labels}。请选出你主要做的那一种（还有更多匹配，请写得更具体）",
            labels=listed,
        )
    else:
        message = t(
            "input.occupation.ambiguous",
            "这段描述对应不止一种职业：{labels}。请选出你主要做的那一种",
            labels=listed,
        )
    return OccupationMatch("ambiguous", shown, message)
