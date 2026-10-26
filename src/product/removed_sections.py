"""草稿里新问卷不再收集的答案（硬要求、属性偏好）的可读写法（E3 输入体验收敛，DECISIONS/0046；旧草稿迁移已去掉，DECISIONS/0050）。

新问卷（v0.4 起）不再收集硬要求与属性偏好。草稿里还有这两类答案时，审阅照实列出并阻断确认（它们会改变模拟，不悄悄沿用、也不悄悄
丢掉）；旧版本的草稿不再迁移，用户新开一份草稿。E1 内部的硬要求机制不变。
"""

from __future__ import annotations

from typing import Any

from common.product_contracts import UserInputDraft
from product.display import Ent, Num, Raw, Sub
from product.pack import DataPack


def _rows(pack: DataPack, draft: UserInputDraft) -> list[tuple[str, str, Sub, str | None]]:
    """（板块，编号，可读写法，用户原文）。"""
    options = {h.option_id: h for h in pack.body.hard_requirement_options}
    out: list[tuple[str, str, Sub, str | None]] = []
    for h in draft.hard_requirements:
        opt = options.get(h.option_id or "")
        if opt is None:
            text = (h.raw_text or h.option_id or h.requirement_id)[:300]
            out.append(
                (
                    "hard_requirements",
                    h.requirement_id,
                    Sub("input.removed.quoted", "“{v}”", (("v", Raw(text)),)),
                    text,
                )
            )
            continue
        value: Any = (
            Sub("input.hard_requirement.unfilled", "未填")
            if h.parameter_value is None
            else Num(str(h.parameter_value))
        )
        out.append(
            (
                "hard_requirements",
                h.requirement_id,
                Sub(
                    "input.removed.hard_requirement",
                    "{label} {value} {unit}",
                    (
                        ("label", Ent("hard_requirement", opt.option_id, opt.label)),
                        ("value", value),
                        ("unit", Ent("hard_requirement", opt.option_id, opt.unit, "unit")),
                    ),
                ),
                h.raw_text,
            )
        )
    for a in draft.attribute_preferences:
        out.append(
            (
                "attribute_preferences",
                a.preference_id,
                Sub(
                    "input.removed.attribute_hard",
                    "“{v}”（标成了不可妥协）",
                    (("v", Raw(a.raw_text)),),
                )
                if a.as_hard_requirement
                else Sub("input.removed.quoted", "“{v}”", (("v", Raw(a.raw_text)),)),
                a.raw_text,
            )
        )
    return out


def removed_params(pack: DataPack, draft: UserInputDraft, section: str) -> tuple[Sub, ...]:
    """某一类要清空的旧答案的可读写法（显示参数，审阅的问题说明里用）。"""
    return tuple(s for sec, _ref, s, _raw in _rows(pack, draft) if sec == section)
