"""旧草稿进入新问卷的显式迁移（E3 输入体验收敛，DECISIONS/0046）。

新问卷（``QUESTIONNAIRE_VERSION`` v0.4）不再收集硬要求与属性偏好。旧草稿里还有这两类答案时，它们会改变模拟，所以既不悄悄沿用、也
不悄悄丢掉：

* ``GET`` 草稿时附上迁移说明（``DraftMigration``）：逐条列出要清空的旧答案、版本从哪到哪、迁移会做什么；
* 用户明确同意之后（``POST /api/v1/drafts/{id}/migrate``，``clear_removed = true``），才把草稿的版本换成当前版本、清空这两类答案、
  其他答案原样保留，写成新的一个修订；之后要重新审阅与确认。只差版本、没有要清空的答案时也要用户点一次（不在读取时悄悄改写）。

已经确认的画像、已经开始的运行与历史结果不改写：它们用自己保存的副本。E1 内部的硬要求机制不变。

v0.5 问卷（v3.5，DECISIONS/0047）改用独立评分。旧草稿里的六维 / 物质分配是当时的百分比，不是用户填的独立评分：迁移说明照原样列出
（``LegacyAllocation``），迁移时用户必须明确选择 ``rerate``（这两组改成独立评分、各项未答，由用户重新打分）或 ``equal_weight``（每项
评分 50）；系统不把旧百分比改读成评分。
"""

from __future__ import annotations

from typing import Any

from common.product_contracts import (
    RATINGS_RULE,
    DraftMigration,
    LegacyAllocation,
    LegacyAllocationLine,
    RemovedAnswer,
    UserInputDraft,
)
from product.display import Ent, Num, Raw, Sub, say, t
from product.pack import DataPack

_VERSION_KEYS = (
    "schema_version",
    "questionnaire_version",
    "vocabulary_version",
    "health_questionnaire_version",
    "conversion_version",
)


class MigrationError(ValueError):
    """``code``：``migration_consent_required``（有要清空的旧答案、用户没有同意）、``migration_choice_required``（有旧的百分比分配、
    用户没有选择重新评分或同等重视）或 ``migration_not_needed``。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


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


def removed_answers(pack: DataPack, draft: UserInputDraft) -> tuple[RemovedAnswer, ...]:
    return tuple(
        RemovedAnswer(section=sec, ref=ref, summary=say(s)[:300], raw_text=raw)  # type: ignore[arg-type]
        for sec, ref, s, raw in _rows(pack, draft)
    )


#: 迁移时两组分配各自的键（六维、物质四项）
def _groups(pack: DataPack) -> dict[str, tuple[tuple[str, str], ...]]:
    return {
        "six_dimensions": tuple((d.key, d.label) for d in pack.body.dimensions),
        "material": tuple((m.key, m.label) for m in pack.body.material_leaves),
    }


def legacy_allocations(pack: DataPack, draft: UserInputDraft) -> tuple[LegacyAllocation, ...]:
    """草稿里旧的百分比分配（``manual`` / ``equal_split``，或没写方式却填了数值）：照原样列出，不换算。"""
    from product.display import label

    out = []
    equal = {
        "six_dimensions": pack.body.points.equal_split_six,
        "material": pack.body.points.equal_split_material,
    }
    for group, keys in _groups(pack).items():
        answer = getattr(draft, group)
        if answer.mode == RATINGS_RULE or (answer.mode is None and not answer.entries):
            continue
        kind = "dimension" if group == "six_dimensions" else "leaf"
        lines = []
        for k, name in keys:
            entry = answer.entries.get(k)
            if answer.mode == "equal_split":
                state, value = "provided", equal[group]
            elif entry is None:
                state, value = "unanswered", None
            else:
                state, value = entry.state, entry.value_text
            lines.append(
                LegacyAllocationLine(
                    key=k,
                    label=label(kind, k, name),
                    state=state,  # type: ignore[arg-type]
                    value_text=value,
                )
            )
        out.append(
            LegacyAllocation(group=group, mode=answer.mode, lines=tuple(lines))  # type: ignore[arg-type]
        )
    return tuple(out)


def version_changed(pack: DataPack, draft: UserInputDraft) -> bool:
    want = pack.meta().model_dump(mode="json")
    have = draft.meta.model_dump(mode="json")
    return any(have.get(k) != want[k] for k in _VERSION_KEYS) or (
        have["data_pack"]["content_fingerprint"] != want["data_pack"]["content_fingerprint"]
    )


def migration_for(pack: DataPack, draft: UserInputDraft) -> DraftMigration | None:
    """这份草稿进入新问卷要做的迁移；不需要就是 ``None``。"""
    changed = version_changed(pack, draft)
    removed = removed_answers(pack, draft)
    allocations = legacy_allocations(pack, draft)
    if not changed and not removed and not allocations:
        return None
    effects = [
        t(
            "input.migration.effect.keep_except_allocation",
            "城市、职业、年龄、现金、健康问卷、兴趣与口味、资格与补充文字原样保留",
        )
        if allocations
        else t(
            "input.migration.effect.keep",
            "城市、职业、年龄、现金、六维与物质点数、健康问卷、兴趣与口味、资格与补充文字原样保留",
        ),
        t("input.migration.effect.review", "迁移后要重新审阅并确认，才能准备新的模拟"),
        t(
            "input.migration.effect.history",
            "已经确认的画像、已经开始的模拟与历史结果不受影响（它们用自己保存的副本）",
        ),
    ]
    if allocations:
        effects.insert(
            0,
            t(
                "input.migration.effect.allocation",
                "旧的六维 / 物质分配是百分比，不是独立评分：请选择重新评分（这两组改成未答，由你重新打分）或同等重视（每项 50 分）；"
                "系统不把旧百分比改读成评分",
            ),
        )
    if removed:
        effects.insert(
            0,
            t(
                "input.migration.effect.clear",
                "清空 {n} 条新问卷不再收集的答案（硬要求、品牌与菜系等属性偏好）；之后的模拟不再用它们",
                n=len(removed),
            ),
        )
    if changed:
        effects.insert(
            0,
            t(
                "input.migration.effect.version",
                "草稿换成当前的问卷、词表与转换版本",
            ),
        )
    message = (
        t(
            "input.migration.message.removed",
            "这份草稿是按旧版问卷填的，里面有 {n} 条新问卷不再收集的答案。看过下面的说明、同意清空之后才能继续",
            n=len(removed),
        )
        if removed
        else t(
            "input.migration.message.allocation",
            "这份草稿的六维 / 物质分配是旧问卷的百分比：迁移时请选择重新评分或同等重视（其他答案原样保留）",
        )
        if allocations
        else t(
            "input.migration.message.version",
            "这份草稿是按旧版问卷填的：换成当前版本之后再审阅（答案原样保留）",
        )
    )
    return DraftMigration(
        from_questionnaire_version=draft.meta.questionnaire_version,
        to_questionnaire_version=pack.meta().questionnaire_version,
        version_changed=changed,
        removed=removed,
        consent_required=bool(removed),
        message_zh=message[:400],
        effects_zh=tuple(effects),
        allocations=allocations,
        allocation_choice_required=bool(allocations),
    )


def migrate(
    pack: DataPack,
    draft: UserInputDraft,
    *,
    clear_removed: bool,
    allocation_choice: str | None = None,
) -> UserInputDraft:
    """按迁移说明改写一份草稿（修订号由服务端写入时加一）。没有要迁移的、要清空却没有同意、有旧分配却没有选择，都拒绝、草稿不变。

    ``allocation_choice``：``rerate`` 把有旧分配的那几组改成独立评分、各项未答；``equal_weight`` 把它们的每项评分设为 50。"""
    m = migration_for(pack, draft)
    if m is None:
        raise MigrationError("migration_not_needed")
    if m.consent_required and not clear_removed:
        raise MigrationError("migration_consent_required")
    if m.allocation_choice_required and allocation_choice not in ("rerate", "equal_weight"):
        raise MigrationError("migration_choice_required")
    body = {
        **draft.model_dump(mode="json"),
        "meta": pack.meta(draft.meta.sample).model_dump(mode="json"),
        "hard_requirements": [],
        "attribute_preferences": [],
    }
    keys = _groups(pack)
    for old in m.allocations:
        entries = (
            {k: {"state": "provided", "value_text": "50"} for k, _ in keys[old.group]}
            if allocation_choice == "equal_weight"
            else {}
        )
        body[old.group] = {"mode": RATINGS_RULE, "entries": entries}
    return UserInputDraft.model_validate(body)
