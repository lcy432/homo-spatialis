"""兴趣与口味的解释与确认（E3，DECISIONS/0041）。只做明确、可复核的词表映射，不调用语言模型猜。

用户可以用文字描述喜欢的休闲活动、口味或菜系。E3 把文字按分隔符切成片段，每个片段与词表的别名比对：

* 只命中一个类别 → 候选；
* 命中两个以上 → 有歧义（列出，不替用户选）；
* 一个都没命中 → 没认出（原样保留为“本版未支持”，不进评分）。

候选只是待确认的解释：用户必须在 ``selected`` 里明确选中，类别才进入有评分作用的确认画像。只有文字、没有选中时审阅不能确认
（免得没核对的解释或空白悄悄进入评分）；用户也可以改选“通用休闲评价”“明确没有固定爱好”“暂不确定”。同一类别选了两次在审阅
阶段就拒绝（不等到编译才失败）。不管选了哪种作答状态，用户写的原文都原样保留：改选通用评价时，“喜欢冲浪”照样留存为本版未支持
的描述。

这些类别只进入共同、有限的匹配规则（系数是公开登记的模型假设）；不改变六维点数、现金、资格或硬要求，品牌不产生加成。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from common.product_contracts import (
    ConfirmedInterests,
    ConfirmedProfile,
    InterestReview,
    ProductIssue,
)
from product.display import Code, Ent, Join, Raw, Sub, t

if TYPE_CHECKING:
    from product.pack import DataPack

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
        hits = [term.key for term in terms if any(a.lower() in low for a in term.aliases)]
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
    "unanswered": Sub(
        "input.interests.state.unanswered",
        "未答（没有这一项的明确信息，按中性规则评价；不是“通用评价”的选择）",
    ),
    "unsure": Sub("input.interests.state.unsure", "暂不确定（按中性规则评价）"),
    "no_fixed_hobby": Sub(
        "input.interests.state.no_fixed_hobby", "明确没有固定爱好（按中性规则评价）"
    ),
    "generic": Sub(
        "input.interests.state.generic",
        "选择通用休闲评价：城市有什么合适的就体验什么（按中性规则评价）",
    ),
    "no_preference": Sub(
        "input.interests.state.no_preference", "明确没有口味偏好（按中性规则评价）"
    ),
}
#: 两种作答各自的名称，与审阅条目的名称同一个 key
KIND_LABEL = {
    "leisure": Sub("input.item.leisure_interest.label", "休闲兴趣"),
    "taste": Sub("input.item.taste.label", "口味与菜系"),
}
#: 词表类别的实体种类（显示名称按 ``kind/key`` 查）
ENTITY = {"leisure": "leisure_category", "taste": "cuisine"}


def _say(s: Sub) -> str:
    """把一段固定说明（``Sub``）作为整段显示文字生成。"""
    return t(s.key, s.zh, **dict(s.params))


def _listed(texts: Sequence[str], params: Sequence[Any] | None = None) -> Any:
    """``str(list)`` 的写法（``['音乐', '川菜']``），中文与原来逐字相同。``params`` 是各项的显示参数（类别名称）；不给就当作用户原文
    片段。某一项的 ``repr`` 不是简单的单引号写法（含引号或转义）时整段照原文，不重写引号。"""
    items = list(texts)
    if any(repr(s) != f"'{s}'" for s in items):
        return Raw(repr(items))
    each = [Raw(s) for s in items] if params is None else list(params)
    quoted = tuple(Sub("input.list.item", "'{v}'", (("v", p),)) for p in each)
    return Sub("input.list", "[{items}]", (("items", Join(quoted, ", ", ", ")),))


def review_one(
    kind: str, state: str, text: str | None, selected: tuple[str, ...], terms: tuple[Term, ...]
) -> tuple[InterestReview, list[ProductIssue], tuple[str, ...]]:
    """一段兴趣或口味的审阅。返回（审阅、问题、没认出的片段）。"""
    from product.answers import issue

    where = "leisure_interest" if kind == "leisure" else "taste"
    label = KIND_LABEL[kind]
    known = {term.key for term in terms}
    labels = {term.key: term.label for term in terms}

    def names(keys: Sequence[str]) -> Any:
        return _listed([labels[k] for k in keys], [Ent(ENTITY[kind], k, labels[k]) for k in keys])

    found = interpret(text, terms)
    issues: list[ProductIssue] = []
    repeated = sorted({k for k in selected if selected.count(k) > 1})
    if repeated:
        issues.append(
            issue(
                "interest_duplicate_selection",
                f"{where}.selected",
                t(
                    "input.interests.duplicate_selection.message",
                    "{label}里同一类别选了不止一次：{keys}",
                    label=label,
                    keys=_listed(repeated),
                ),
                t(
                    "input.interests.duplicate_selection.impact",
                    "重复的类别会让确认画像在编译时失败；系统不替你去重",
                ),
                t("input.interests.duplicate_selection.next", "每个类别只选一次"),
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
                message_zh=t(
                    "input.interests.duplicate_selection.review",
                    "{label}：同一类别选了不止一次 {keys}",
                    label=label,
                    keys=_listed(repeated),
                ),
            ),
            issues,
            found.unrecognized,
        )
    stray = [k for k in selected if k not in known]
    if stray:
        issues.append(
            issue(
                "interest_unknown_category",
                f"{where}.selected",
                t(
                    "input.interests.unknown_category.message",
                    "{label}里选中了词表外的类别 {keys}",
                    label=label,
                    keys=_listed(stray),
                ),
                t("input.interests.unknown_category.impact", "词表外的类别不能进入评分"),
                t("input.interests.unknown_category.next", "只从能力清单列出的类别里选"),
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
                message_zh=t(
                    "input.interests.unknown_category.review",
                    "{label}：选中了词表外的类别 {keys}",
                    label=label,
                    keys=_listed(stray),
                ),
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
                    t(
                        "input.interests.state_conflict.message",
                        "{label}的作答是“{state}”，却又选中了类别 {keys}",
                        label=label,
                        state=_STATE_ZH[state],
                        keys=_listed(list(selected)),
                    ),
                    t("input.interests.state_conflict.impact", "两种回答互相矛盾，系统不替你取舍"),
                    t(
                        "input.interests.state_conflict.next",
                        "要么改成用文字描述并选中类别，要么清空选中的类别",
                    ),
                )
            )
            status = "invalid"
        else:
            status = "confirmable"
        if text:
            message = t(
                "input.interests.state_with_text.review",
                "{label}：{state}；你写的原文原样保留为本版未支持的描述（没有采用为类别，不进评分）",
                label=label,
                state=_STATE_ZH[state],
            )
        else:
            message = t(
                "input.interests.state.review",
                "{label}：{state}",
                label=label,
                state=_STATE_ZH[state],
            )
        return (
            InterestReview(
                state=state,
                text=text,
                candidates=found.candidates,
                ambiguous=found.ambiguous,
                unrecognized=found.unrecognized,
                status=status,
                message_zh=message[:400],
            ),
            issues,
            found.unrecognized,
        )
    # 文字描述
    if not selected:
        issues.append(
            issue(
                "interest_selection_pending",
                f"{where}.selected",
                t(
                    "input.interests.selection_pending.message",
                    "{label}的文字已经解释成候选 {candidates}，还没有选中要采用的类别",
                    label=label,
                    candidates=names(found.candidates)
                    if found.candidates
                    else Sub("input.interests.no_candidates", "（没有认出的类别）"),
                ),
                t(
                    "input.interests.selection_pending.impact",
                    "没有核对过的解释不会进入评分，画像暂时不能确认",
                ),
                t(
                    "input.interests.selection_pending.next",
                    "从候选或词表里选中要采用的类别；或改选“通用评价 / 没有固定爱好（没有偏好）/ 暂不确定”",
                ),
            )
        )
        status = "pending_selection"
        message = t(
            "input.interests.selection_pending.review",
            "{label}：待选中——候选 {candidates}，有歧义 {ambiguous}，没认出 {unrecognized}",
            label=label,
            candidates=_listed(found.candidates, [Code(k) for k in found.candidates]),
            ambiguous=_listed(list(found.ambiguous)),
            unrecognized=_listed(list(found.unrecognized)),
        )
    else:
        status = "confirmable"
        message = (
            t(
                "input.interests.adopted_with_unrecognized.review",
                "{label}：采用 {names}；没认出的片段 {unrecognized} 原样保留、本版不评分",
                label=label,
                names=names(selected),
                unrecognized=_listed(list(found.unrecognized)),
            )
            if found.unrecognized
            else t(
                "input.interests.adopted.review",
                "{label}：采用 {names}",
                label=label,
                names=names(selected),
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
            status=status,
            message_zh=message[:400],
        ),
        issues,
        found.unrecognized,
    )


#: 没有按具体类别个性化的每种状态怎样对用户说（未答与明确选择分开写）
_NOT_PERSONALIZED_ZH = {
    ("leisure", "unanswered"): Sub(
        "input.interests.scope.leisure.unanswered",
        "休闲兴趣：你没有回答，本次不按具体兴趣个性化（按中性规则评价）",
    ),
    ("leisure", "unsure"): Sub(
        "input.interests.scope.leisure.unsure",
        "休闲兴趣：你选择暂不确定，本次不按具体兴趣个性化（按中性规则评价）",
    ),
    ("leisure", "no_fixed_hobby"): Sub(
        "input.interests.scope.leisure.no_fixed_hobby",
        "休闲兴趣：你说明没有固定爱好，本次不按具体兴趣个性化（按中性规则评价）",
    ),
    ("leisure", "generic"): Sub(
        "input.interests.scope.leisure.generic",
        "休闲兴趣：你选择通用休闲评价（城市有什么合适的就体验什么），本次不按具体兴趣个性化",
    ),
    ("taste", "unanswered"): Sub(
        "input.interests.scope.taste.unanswered",
        "口味与菜系：你没有回答，本次不按具体菜系个性化（只看新鲜餐）",
    ),
    ("taste", "unsure"): Sub(
        "input.interests.scope.taste.unsure",
        "口味与菜系：你选择暂不确定，本次不按具体菜系个性化（只看新鲜餐）",
    ),
    ("taste", "no_preference"): Sub(
        "input.interests.scope.taste.no_preference",
        "口味与菜系：你说明没有口味偏好，本次不按具体菜系个性化（只看新鲜餐）",
    ),
}
#: 有没有按具体兴趣或口味个性化，都要用户知悉这一条（复用审阅的待确认机制；不强迫填写爱好）
NOT_PERSONALIZED_ACK = "ack:interests_not_personalized"


def scope_lines(
    leisure: InterestReview, taste: InterestReview, labels: dict[str, dict[str, str]]
) -> tuple[str, ...]:
    """确认画像与预检查、结果里共用的范围说明：按什么个性化、哪些没有个性化、哪些原文本版不支持。"""
    out = []
    for kind, r in (("leisure", leisure), ("taste", taste)):
        if r.state == "described":
            names = Join(
                tuple(
                    Ent(ENTITY[kind], k, labels[kind][k]) if k in labels[kind] else Raw(k)
                    for k in r.selected
                )
            )
            if kind == "leisure":
                out.append(
                    t(
                        "input.interests.scope.leisure.described",
                        "休闲兴趣：按你确认的兴趣（{names}）个性化",
                        names=names,
                    )
                )
            else:
                out.append(
                    t(
                        "input.interests.scope.taste.described",
                        "口味与菜系：按你确认的菜系（{names}）个性化",
                        names=names,
                    )
                )
        else:
            out.append(_say(_NOT_PERSONALIZED_ZH[(kind, r.state)]))
    retained = tuple(dict.fromkeys((*leisure.unrecognized, *taste.unrecognized)))
    if retained:
        out.append(
            t(
                "input.interests.scope.retained",
                "本版不支持、只留存原文的描述：{texts}（不进评分，也不改点数、现金、资格或硬要求）",
                texts=_listed(list(retained)),
            )
        )
    return tuple(s[:300] for s in out)


def confirmed_scope_lines(pack: DataPack, confirmed: ConfirmedProfile) -> tuple[str, ...]:
    """确认画像里的兴趣范围说明（确认时由 ``scope_lines`` 生成；预检查与结果原样转抄）。按确认画像保存的作答状态、选中的
    类别与没认出的原文，用同一个函数、同一套名称在这次生成里再生成一次，与保存的文字逐条核对：完全相同才用它（可以给英文）；
    对不上就照用保存的原文（中文不变，英文缺失）。"""
    i = confirmed.interests

    def answered(status: str, selected: tuple[str, ...], unrecognized: tuple[str, ...]) -> Any:
        # 只带 scope_lines 读的三项：作答状态（确认过的就是“用文字描述”）、选中的类别、没认出的片段
        state = "described" if status == "confirmed" else status
        return InterestReview.model_construct(
            state=state, selected=selected, unrecognized=unrecognized
        )

    labels = {
        "leisure": {term.key: term.label for term in pack.body.interests.leisure_categories},
        "taste": {term.key: term.label for term in pack.body.interests.cuisines},
    }
    try:
        ours = scope_lines(
            answered(i.leisure_status, i.leisure_categories, i.unrecognized_text),
            answered(i.taste_status, i.cuisines, ()),
            labels,
        )
    except KeyError:  # 这份资料包认不出保存的状态或类别：显示文字不影响预检查与结果本身，照用原文
        return i.scope_zh
    return ours if ours == i.scope_zh else i.scope_zh


def needs_not_personalized_ack(leisure: InterestReview, taste: InterestReview) -> bool:
    return leisure.state != "described" or taste.state != "described"


def confirmed(
    leisure: InterestReview,
    taste: InterestReview,
    note: str,
    labels: dict[str, dict[str, str]],
) -> ConfirmedInterests:
    """审阅（已经可确认）→ 确认画像里的兴趣与口味。原文在任何作答状态下都保留。"""

    def status_of(r: InterestReview) -> str:
        return "confirmed" if r.state == "described" else r.state

    return ConfirmedInterests(
        leisure_status=status_of(leisure),  # type: ignore[arg-type]
        leisure_categories=leisure.selected if leisure.state == "described" else (),
        taste_status=status_of(taste),  # type: ignore[arg-type]
        cuisines=taste.selected if taste.state == "described" else (),
        leisure_text=leisure.text,
        taste_text=taste.text,
        unrecognized_text=tuple(dict.fromkeys((*leisure.unrecognized, *taste.unrecognized))),
        scope_zh=scope_lines(leisure, taste, labels),
        matching_note_zh=note,
    )
