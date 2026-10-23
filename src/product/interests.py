"""兴趣与口味的解释与确认（E3，DECISIONS/0041）。只做明确、可复核的词表映射，不调用语言模型猜。

用户可以用文字描述喜欢的休闲活动、口味或菜系。E3 把文字按分隔符切成片段，每个片段与词表的别名比对：

* 只命中一个类别 → 候选；
* 命中两个以上 → 有歧义（列出，不替用户选）；
* 一个都没命中 → 没认出（原样保留为“本版未支持”，不进评分）。

候选只是待确认的解释：用户必须在 ``selected`` 里明确选中，类别才进入有评分作用的确认画像。只有文字、没有选中时审阅不能确认
（免得没核对的解释或空白悄悄进入评分）；用户也可以改选“通用休闲评价”“明确没有固定爱好”“暂不确定”。

这些类别只进入共同、有限的匹配规则（系数是公开登记的模型假设）；不改变六维点数、现金、资格或硬要求，品牌不产生加成。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from common.product_contracts import ConfirmedInterests, InterestReview, ProductIssue

#: 切分文字的分隔符（中文与英文常见的列举符号）。
_SPLIT = re.compile(r"[，,、；;。/|\n]+|\s+和\s+|\s+and\s+|和|与|及")


@dataclass(frozen=True)
class Term:
    key: str
    label: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class Interpretation:
    candidates: tuple[str, ...]
    ambiguous: tuple[str, ...]
    unrecognized: tuple[str, ...]


def interpret(text: str | None, terms: tuple[Term, ...]) -> Interpretation:
    """文字 → 候选类别、有歧义的片段、没认出的片段（确定性的别名包含比对）。"""
    if not text:
        return Interpretation((), (), ())
    candidates: list[str] = []
    ambiguous: list[str] = []
    unrecognized: list[str] = []
    for raw in _SPLIT.split(text):
        piece = raw.strip()
        if not piece:
            continue
        low = piece.lower()
        hits = [t.key for t in terms if any(a.lower() in low for a in t.aliases)]
        if len(hits) == 1:
            if hits[0] not in candidates:
                candidates.append(hits[0])
        elif len(hits) > 1:
            ambiguous.append(piece)
        else:
            unrecognized.append(piece)
    return Interpretation(tuple(candidates), tuple(ambiguous), tuple(unrecognized))


#: 不需要类别的作答状态（审阅可以直接确认）。
_NO_CATEGORY = {
    "leisure": {"unanswered", "unsure", "no_fixed_hobby", "generic"},
    "taste": {"unanswered", "unsure", "no_preference"},
}
_STATE_ZH = {
    "unanswered": "未答（没有这一项的明确信息，按中性规则评价；不是“通用评价”的选择）",
    "unsure": "暂不确定（按中性规则评价）",
    "no_fixed_hobby": "明确没有固定爱好（按中性规则评价）",
    "generic": "选择通用休闲评价：城市有什么合适的就体验什么（按中性规则评价）",
    "no_preference": "明确没有口味偏好（按中性规则评价）",
}


def review_one(
    kind: str, state: str, text: str | None, selected: tuple[str, ...], terms: tuple[Term, ...]
) -> tuple[InterestReview, list[ProductIssue], tuple[str, ...]]:
    """一段兴趣或口味的审阅。返回（审阅、问题、没认出的片段）。"""
    from product.answers import issue

    where = "leisure_interest" if kind == "leisure" else "taste"
    label = "休闲兴趣" if kind == "leisure" else "口味与菜系"
    known = {t.key for t in terms}
    labels = {t.key: t.label for t in terms}
    found = interpret(text if state == "described" else None, terms)
    issues: list[ProductIssue] = []
    stray = [k for k in selected if k not in known]
    if stray:
        issues.append(
            issue(
                "interest_unknown_category",
                f"{where}.selected",
                f"{label}里选中了词表外的类别 {stray}",
                "词表外的类别不能进入评分",
                "只从能力清单列出的类别里选",
            )
        )
        return (
            InterestReview(
                state=state,
                text=text,
                candidates=found.candidates,
                ambiguous=found.ambiguous,
                unrecognized=found.unrecognized,
                selected=selected,
                status="invalid",
                message_zh=f"{label}：选中了词表外的类别 {stray}",
            ),
            issues,
            found.unrecognized,
        )
    if state in _NO_CATEGORY[kind]:
        if selected:
            issues.append(
                issue(
                    "interest_state_conflict",
                    f"{where}.selected",
                    f"{label}的作答是“{_STATE_ZH[state]}”，却又选中了类别 {list(selected)}",
                    "两种回答互相矛盾，系统不替你取舍",
                    "要么改成用文字描述并选中类别，要么清空选中的类别",
                )
            )
            status = "invalid"
        else:
            status = "confirmable"
        return (
            InterestReview(
                state=state, text=None, status=status, message_zh=f"{label}：{_STATE_ZH[state]}"
            ),
            issues,
            (),
        )
    # 文字描述
    if not selected:
        issues.append(
            issue(
                "interest_selection_pending",
                f"{where}.selected",
                f"{label}的文字已经解释成候选 {[labels[k] for k in found.candidates] or '（没有认出的类别）'}，"
                "还没有选中要采用的类别",
                "没有核对过的解释不会进入评分，画像暂时不能确认",
                "从候选或词表里选中要采用的类别；或改选“通用评价 / 没有固定爱好（没有偏好）/ 暂不确定”",
            )
        )
        status = "pending_selection"
        message = f"{label}：待选中——候选 {list(found.candidates)}，有歧义 {list(found.ambiguous)}，没认出 {list(found.unrecognized)}"
    else:
        status = "confirmable"
        message = (
            f"{label}：采用 {[labels[k] for k in selected]}；没认出的片段 {list(found.unrecognized)} 原样保留、本版不评分"
            if found.unrecognized
            else f"{label}：采用 {[labels[k] for k in selected]}"
        )
    return (
        InterestReview(
            state=state,
            text=text,
            candidates=found.candidates,
            ambiguous=found.ambiguous,
            unrecognized=found.unrecognized,
            selected=selected,
            status=status,
            message_zh=message[:400],
        ),
        issues,
        found.unrecognized,
    )


def confirmed(leisure: InterestReview, taste: InterestReview, note: str) -> ConfirmedInterests:
    """审阅（已经可确认）→ 确认画像里的兴趣与口味。"""

    def status_of(r: InterestReview, kind: str) -> str:
        return "confirmed" if r.state == "described" else r.state

    return ConfirmedInterests(
        leisure_status=status_of(leisure, "leisure"),  # type: ignore[arg-type]
        leisure_categories=leisure.selected if leisure.state == "described" else (),
        taste_status=status_of(taste, "taste"),  # type: ignore[arg-type]
        cuisines=taste.selected if taste.state == "described" else (),
        unrecognized_text=tuple(dict.fromkeys((*leisure.unrecognized, *taste.unrecognized))),
        matching_note_zh=note,
    )
