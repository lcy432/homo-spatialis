"""原始答案 → 审阅 → 确认画像（E3-A 模块 B，DECISIONS/0034）。

三层分开保存：``UserInputDraft``（原始答案与逐项状态）→ ``ProfileReview``（E3 的可读解释、候选、缺失、未支持与待确认）→
``ConfirmedProfile``（用户确认的语义与精确数值，带内容身份）。规则：

* 未答、已提供（明确 0 也是已提供）、明确无所谓、暂不确定四种状态互不代替；“无所谓”“不确定”不会被自动换成 0 或均分。
* 六维合计必须**精确**等于 100；手工输入按精确十进制解析；只有用户主动选择平均分配才生成均分（六维每项 50/3、物质每项 25）。
  不预填、不按比例自动扣减；合计不对就报“还差 / 多出几分”，由用户自己改。
* 物质父项为正时四项细分必须精确合计 100；父项明确为 0 时细分不适用，不伪造回答。旧问卷的 ``laundry`` 不能自动当作
  ``daily_convenience``；``healthcare`` 不在六维里（健康回答另有独立问卷）。
* 职业由用户在少量可读候选里确认，不填职业代码，不从职称授予资格；金额保留原始金额、币种、时期与口径，没有合法换算就阻断。
* 自由文字超过 2,000 字符报错，不截断；属性偏好与未获执行保障的“不可妥协”要求只留存原文与支持状态，确认时要显式知悉。

确认画像的内容身份覆盖版本、资料包与草稿引用：改了相关答案、词表、问卷或转换规则，旧确认就失效；内容被改写，指纹核对不通过。
"""

from __future__ import annotations

from collections.abc import Iterable
from fractions import Fraction

from common.product_contracts import (
    PRODUCT_SCHEMA_VERSION,
    AllocationAnswer,
    AllocationLine,
    AllocationReview,
    ConfirmedAllocation,
    ConfirmedHardRequirement,
    ConfirmedHealth,
    ConfirmedHealthAnswer,
    ConfirmedMaterial,
    ConfirmedMoney,
    ConfirmedOccupation,
    ConfirmedProfile,
    DraftRef,
    EvidenceRef,
    ExactNumber,
    FieldSource,
    HardRequirementAnswer,
    HealthReviewItem,
    ItemStatus,
    MoneyReview,
    OccupationMapping,
    OccupationReview,
    ProductIssue,
    ProductMoney,
    ProfileReview,
    RetainedStatement,
    SampleLabel,
    StatementReview,
    UserInputDraft,
    content_fingerprint,
)
from product.exact import (
    ExactParseError,
    canonical,
    display,
    money_display,
    parse_money_text,
    parse_points,
)
from product.occupation import match_occupation
from product.pack import DataPack

TOTAL = Fraction(100)
SIX = ("material", "safety", "opportunity", "leisure", "environment", "commute")
#: 维度与细项的用途：用于评分，也交给分身做计划（按信息许可）。
POINT_USAGE: tuple[str, ...] = ("scoring", "planning")


class ConfirmationRefusedError(ValueError):
    """不能确认：审阅里还有阻断问题，或用户没有知悉全部待确认事项。"""

    def __init__(self, issues: tuple[ProductIssue, ...]) -> None:
        super().__init__("；".join(i.message_zh for i in issues))
        self.issues = issues


def issue(
    code: str,
    field: str,
    message: str,
    impact: str,
    action: str,
    severity: str = "error",
    *,
    audit_ref: str | None = None,
) -> ProductIssue:
    return ProductIssue(
        code=code,
        severity=severity,  # type: ignore[arg-type]
        field=field,
        message_zh=message[:400],
        impact_zh=impact[:300],
        next_action_zh=action[:300],
        audit_ref=audit_ref,
    )


def draft_fingerprint(draft: UserInputDraft) -> str:
    """草稿的内容身份（版本、资料包与全部答案；样例标签不参与）。改了任何答案，指纹就不同。"""
    content = draft.model_dump(mode="json")
    content["meta"].pop("sample", None)
    return content_fingerprint(content)


def draft_ref(draft: UserInputDraft) -> DraftRef:
    return DraftRef(
        draft_id=draft.draft_id, revision=draft.revision, draft_fingerprint=draft_fingerprint(draft)
    )


# ---------------------------------------------------------------------- 版本


def _version_issues(pack: DataPack, meta_dump: dict, where: str) -> list[ProductIssue]:
    want = pack.meta().model_dump(mode="json")
    out = []
    for key in (
        "schema_version",
        "questionnaire_version",
        "vocabulary_version",
        "health_questionnaire_version",
        "conversion_version",
    ):
        if meta_dump.get(key) != want[key]:
            out.append(
                issue(
                    "version_changed",
                    f"{where}.meta.{key}",
                    f"{where} 用的 {key} 是 {meta_dump.get(key)}，现在是 {want[key]}",
                    "按旧版本问卷、词表或转换规则填写的答案不能直接沿用",
                    "按当前版本重新填写或重新确认相关答案",
                )
            )
    if (
        meta_dump.get("data_pack", {}).get("content_fingerprint")
        != want["data_pack"]["content_fingerprint"]
    ):
        out.append(
            issue(
                "data_pack_changed",
                f"{where}.meta.data_pack",
                f"{where} 所依据的资料包与现在的资料包内容不同",
                "城市、词表或资料变了，旧的解释不一定还成立",
                "用当前资料包重新审阅并确认",
            )
        )
    return out


# ---------------------------------------------------------------------- 定和分配


def _allocation(
    pack: DataPack,
    answer: AllocationAnswer,
    *,
    keys: tuple[str, ...],
    labels: dict[str, str],
    supports: dict[str, str],
    equal_value: str,
    where: str,
    what: str,
) -> tuple[AllocationReview, dict[str, Fraction] | None, list[ProductIssue]]:
    """审阅一组定和分配。返回（审阅，完整时的精确点数，问题）。"""
    digits = pack.body.points.display_decimals
    max_dec = pack.body.points.manual_max_decimals
    issues: list[ProductIssue] = []
    for k in answer.entries:
        if k in keys:
            continue
        if k == "healthcare":
            issues.append(
                issue(
                    "healthcare_not_in_six_dimensions",
                    f"{where}.entries.healthcare",
                    "健康不在六维 100 分里：新问卷的健康风险应对是一份独立问卷，不占这 100 分",
                    "这里的医疗点数不会被使用，也不会被改读成健康回答",
                    "删掉这一项，在健康问卷里回答",
                )
            )
        elif k == "laundry":
            issues.append(
                issue(
                    "legacy_laundry_key",
                    f"{where}.entries.laundry",
                    "旧问卷的“洗衣”问的是有没有洗衣条件，不能自动当作“日常生活便利”（愿意花钱减少日常杂务）",
                    "这一项不会被使用，也不会被改读成新题目",
                    "按新题目重新分配物质四项（居住空间、饮食生活、日常生活便利、个人消费与享受）",
                )
            )
        else:
            issues.append(
                issue(
                    "unknown_allocation_key",
                    f"{where}.entries.{k}",
                    f"“{k}”不是本版{what}的一项",
                    "这一项不会被使用",
                    f"只在 {'、'.join(labels[x] for x in keys)} 之间分配",
                )
            )
    lines: list[AllocationLine] = []
    values: dict[str, Fraction] = {}
    if answer.mode == "equal_split":
        if answer.entries:
            issues.append(
                issue(
                    "equal_split_with_values",
                    f"{where}.entries",
                    "选择了“平均分配”，又提交了手工数值：两者只能选一种",
                    "不能确定你要的是哪一种分配",
                    "撤销平均分配，或清空手工数值",
                )
            )
        q = Fraction(equal_value)
        for k in keys:
            values[k] = q
            lines.append(
                AllocationLine(
                    key=k,
                    label=labels[k],
                    state="provided",
                    exact=canonical(q),
                    display=display(q, digits),
                    support=supports[k],  # type: ignore[arg-type]
                    usage=POINT_USAGE,  # type: ignore[arg-type]
                )
            )
    else:
        for k in keys:
            entry = answer.entries.get(k)
            state = "unanswered" if entry is None else entry.state
            exact = shown = None
            if entry is not None and entry.state == "provided":
                assert entry.value_text is not None
                try:
                    q = parse_points(entry.value_text, max_decimals=max_dec)
                except ExactParseError as exc:
                    issues.append(
                        issue(
                            exc.code,
                            f"{where}.entries.{k}",
                            f"{labels[k]}：{exc}",
                            "这一项没有可用的数值",
                            "改成最多两位小数的数，或使用平均分配",
                        )
                    )
                else:
                    if q > TOTAL:
                        issues.append(
                            issue(
                                "points_out_of_range",
                                f"{where}.entries.{k}",
                                f"{labels[k]} 的 {entry.value_text} 分超过了 100",
                                "这一项没有可用的数值",
                                "每一项在 0 到 100 之间",
                            )
                        )
                    else:
                        values[k] = q
                        exact, shown = canonical(q), display(q, digits)
            lines.append(
                AllocationLine(
                    key=k,
                    label=labels[k],
                    state=state,  # type: ignore[arg-type]
                    exact=exact,
                    display=shown,
                    support=supports[k],  # type: ignore[arg-type]
                    usage=POINT_USAGE,  # type: ignore[arg-type]
                )
            )
        not_given = [ln for ln in lines if ln.state != "provided"]
        for ln in not_given:
            text = {
                "unanswered": "还没有分配",
                "indifferent": "选了“无所谓”",
                "unsure": "选了“暂不确定”",
            }[ln.state]
            issues.append(
                issue(
                    f"allocation_{ln.state}",
                    f"{where}.entries.{ln.key}",
                    f"{ln.label} {text}：系统不会把它自动换成 0 分或平均分",
                    f"{what}还不完整，不能确认",
                    "给它一个明确的分数（可以是 0），或选择平均分配",
                )
            )
    total = sum(values.values(), Fraction(0)) if values else None
    complete = len(values) == len(keys) and not any(i.severity == "error" for i in issues)
    if len(values) == len(keys) and total is not None and total != TOTAL:
        diff = TOTAL - total
        gap = f"还差 {canonical(diff)} 分" if diff > 0 else f"多出 {canonical(-diff)} 分"
        issues.append(
            issue(
                "points_sum_not_100",
                f"{where}.entries",
                f"{what}合计是 {canonical(total)}（显示 {display(total, digits)}），不是 100：{gap}",
                "系统不会按比例自动扣减或补齐任何一项",
                "由你决定从哪一项挪到哪一项，直到精确合计 100",
            )
        )
        complete = False
    review = AllocationReview(
        mode=answer.mode,
        status="complete" if complete else "incomplete",
        total=None
        if total is None
        else ExactNumber(exact=canonical(total), display=display(total, digits)),
        lines=tuple(lines),
    )
    return review, (values if complete else None), issues


# ---------------------------------------------------------------------- 健康问卷


def health_meaning(pack: DataPack, question_id: str, choice: str | None) -> tuple[str, str]:
    """一题作答的（含义，给分身的读法）。只照题目与选项写，不推断任何数值。"""
    q = next(x for x in pack.body.health_questionnaire.questions if x.question_id == question_id)
    if choice is None:
        meaning = "用户没有回答这一题"
        note = "没有回答：没有这一项的明确指令，不要替用户补成某种倾向"
    elif choice == "unsure":
        meaning = "用户暂不确定"
        note = "暂不确定：没有这一项的明确指令，不要替用户补成某种倾向"
    elif choice == "no_clear_tendency":
        meaning = "用户在这一题上没有明显倾向"
        note = "用户表示没有明显倾向（只供做计划参考，不说明生病概率、病痛、疗效或愿付金额）"
    else:
        meaning = q.agree_meaning if choice == "agree" else q.disagree_meaning
        note = f"用户自我描述：{meaning}（只供做计划参考，不说明生病概率、病痛、疗效或愿付金额）"
    return meaning, note


# ---------------------------------------------------------------------- 审阅


def review_draft(
    pack: DataPack, draft: UserInputDraft, *, sample: SampleLabel | None = None
) -> ProfileReview:
    """审阅一份草稿：给出可读解释与全部问题。不修改草稿，不替用户补任何答案。"""
    b = pack.body
    issues: list[ProductIssue] = list(
        _version_issues(pack, draft.meta.model_dump(mode="json"), "草稿")
    )
    pending: list[str] = []
    summary: list[str] = []
    items: list[ItemStatus] = []

    # 城市
    city_refs: list[str] = []
    if draft.cities.state != "provided" or not draft.cities.city_refs:
        issues.append(
            issue(
                "cities_missing",
                "cities",
                "还没有选择要比较的城市",
                "没有城市就不能安排分身",
                "从能力清单列出的城市里至少选一座",
            )
        )
    else:
        seen: set[str] = set()
        for ref in draft.cities.city_refs:
            if pack.city(ref) is None:
                issues.append(
                    issue(
                        "city_not_supported",
                        "cities.city_refs",
                        f"城市 {ref} 不在本版能力清单里",
                        "这座城市没有资料，不能运行",
                        "从能力清单列出的城市里选择",
                    )
                )
            elif ref in seen:
                issues.append(
                    issue(
                        "city_duplicated",
                        "cities.city_refs",
                        f"城市 {ref} 选了两次",
                        "同一座城不重复计数",
                        "删掉重复的那一项",
                    )
                )
            else:
                seen.add(ref)
                city_refs.append(ref)
        if len(city_refs) == 1:
            summary.append("只选了一座城：可以运行，但没有城市之间的比较")
    items.append(
        ItemStatus(
            item="cities",
            label="候选城市",
            answer_state=draft.cities.state,
            support="supported",
            confirmation="pending",
            usage=("start_state",),
            note="每座城一个分身固定生活三年；城市身份用完整的国家 / 体系 / 代码",
        )
    )

    # 职业
    match = match_occupation(
        pack, draft.occupation.raw_text if draft.occupation.state == "provided" else None
    )
    selected = draft.occupation.selected_candidate
    if match.status == "not_provided":
        issues.append(
            issue(
                "occupation_missing",
                "occupation.raw_text",
                "还没有描述你主要做什么工作",
                "开局岗位要按当地目录匹配，没有职业就不能生成开局",
                "用一两句话描述你的主要工作内容",
            )
        )
    elif match.status == "unknown":
        issues.append(
            issue(
                "occupation_unknown",
                "occupation.raw_text",
                match.message_zh,
                "开局岗位无法匹配，不能生成开局",
                "换一种说法描述主要职责",
            )
        )
    elif selected is None:
        issues.append(
            issue(
                "occupation_needs_confirmation",
                "occupation.selected_candidate",
                match.message_zh,
                "职业还没有确认，不能生成开局",
                "在候选里选出你主要做的那一种",
            )
        )
    elif selected not in {c.candidate_id for c in match.candidates}:
        issues.append(
            issue(
                "occupation_selection_invalid",
                "occupation.selected_candidate",
                "选中的职业不在这段描述的候选里（描述改过之后要重新选）",
                "职业还没有确认",
                "按当前描述重新选择候选",
            )
        )
    occupation_review = OccupationReview(
        status=match.status,  # type: ignore[arg-type]
        raw_text=draft.occupation.raw_text,
        candidates=match.candidates,
        selected=selected if selected in {c.candidate_id for c in match.candidates} else None,
        message_zh=match.message_zh,
    )
    items.append(
        ItemStatus(
            item="occupation",
            label="职业",
            answer_state=draft.occupation.state,
            support="supported",
            confirmation="pending",
            usage=("start_state", "planning"),
            note="用于匹配开局时的当地目录岗位；不授予任何资格，分类代码只在内部保留",
        )
    )

    # 年龄
    age = draft.age.value if draft.age.state == "provided" else None
    if age is None:
        issues.append(
            issue(
                "age_missing",
                "age",
                "还没有填写年龄",
                "开局状态需要年龄（模型的疾病规则按年龄段，未经医学标定）",
                "填写模拟开始时的整数年龄",
            )
        )
    elif not b.age_min <= age <= b.age_max:
        issues.append(
            issue(
                "age_out_of_range",
                "age.value",
                f"年龄 {age} 不在本版支持的 {b.age_min}–{b.age_max} 岁之间",
                "本版只支持单个成年人的情景",
                "填写支持范围内的年龄",
            )
        )
        age = None
    items.append(
        ItemStatus(
            item="age",
            label="年龄",
            answer_state=draft.age.state,
            support="supported",
            confirmation="pending",
            usage=("start_state",),
            note="进入已有的年龄规则（合成设定，未经医学标定）；不从职业猜年龄",
        )
    )

    # 现金
    money_review, money_issues = _money(pack, draft)
    issues += money_issues
    items.append(
        ItemStatus(
            item="cash",
            label="开始时可用于生活的钱",
            answer_state=draft.cash.state,
            support="supported",
            confirmation="pending",
            usage=("start_state", "planning"),
            note="可动用的流动资金，不是房产总值、信用额度或月薪；没填不是 0，不静默借贷",
        )
    )

    # 六维与物质
    dim_labels = {d.key: d.label for d in b.dimensions}
    dim_support = {d.key: d.support for d in b.dimensions}
    six_review, six_values, six_issues = _allocation(
        pack,
        draft.six_dimensions,
        keys=SIX,
        labels=dim_labels,
        supports=dim_support,
        equal_value=b.points.equal_split_six,
        where="six_dimensions",
        what="六维",
    )
    issues += six_issues
    leaf_keys = tuple(m.key for m in b.material_leaves)
    leaf_labels = {m.key: m.label for m in b.material_leaves}
    leaf_support = {m.key: m.support for m in b.material_leaves}
    material_parent = six_values.get("material") if six_values is not None else None
    if material_parent == 0:
        material_review = AllocationReview(mode=None, status="not_applicable")
        if draft.material.entries or draft.material.mode is not None:
            issues.append(
                issue(
                    "material_parent_zero_ignored",
                    "material",
                    "物质生活给了 0 分，物质内部的细分不适用：已填写的细分不会被使用",
                    "这几项细分只留在原始答案里，不进入画像",
                    "如果物质生活其实很重要，先把它的分数改成正数",
                    severity="info",
                )
            )
    else:
        material_review, _material_values, material_issues = _allocation(
            pack,
            draft.material,
            keys=leaf_keys,
            labels=leaf_labels,
            supports=leaf_support,
            equal_value=b.points.equal_split_material,
            where="material",
            what="物质四项",
        )
        if material_parent is not None:
            issues += material_issues
        elif draft.material.entries:
            issues += [
                i
                for i in material_issues
                if i.code in ("legacy_laundry_key", "unknown_allocation_key")
            ]
    for i, (label, review) in enumerate((("六维", six_review), ("物质四项", material_review))):
        items.append(
            ItemStatus(
                item="six_dimensions" if i == 0 else "material",
                label=label,
                answer_state="provided"
                if review.status in ("complete", "not_applicable")
                else "unanswered",
                support="supported",
                confirmation="pending",
                usage=POINT_USAGE,  # type: ignore[arg-type]
                note="陈述的相对重要程度，不是开支比例或预算；精确分数参与计算，显示值只供阅读",
            )
        )
    if draft.six_dimensions.mode == "equal_split":
        summary.append("六维选择了平均分配：每项 50/3 分（显示 16.7）")

    # 健康问卷
    hq = b.health_questionnaire
    health_items: list[HealthReviewItem] = []
    if draft.health.questionnaire_version != hq.version:
        issues.append(
            issue(
                "health_questionnaire_version_mismatch",
                "health.questionnaire_version",
                f"健康回答按问卷 {draft.health.questionnaire_version} 填写，现在的问卷是 {hq.version}",
                "旧题目的答案不能改读成新题目的答案",
                "按当前问卷重新回答",
            )
        )
    answers = {a.question_id: a.choice for a in draft.health.items}
    for qid in answers:
        if qid not in {q.question_id for q in hq.questions}:
            issues.append(
                issue(
                    "health_question_unknown",
                    f"health.items.{qid}",
                    f"问卷里没有题目 {qid}",
                    "这一题不会被使用",
                    "按当前问卷回答",
                )
            )
    labels = {o.option_id: o.label for o in hq.options}
    # E3-A 收尾 F4：同一题收到不止一个答案（包括两个相同的答案）一律拒绝，不按最后一个算、不替用户选
    given: dict[str, list[str]] = {}
    for a in draft.health.items:
        given.setdefault(a.question_id, []).append(
            "未答" if a.choice is None else labels.get(a.choice, a.choice)
        )
    duplicated = {qid for qid, got in given.items() if len(got) > 1}
    for qid in sorted(duplicated):
        issues.append(
            issue(
                "health_answer_duplicated",
                f"health.items.{qid}",
                f"健康题 {qid} 收到了 {len(given[qid])} 个答案（{'、'.join(sorted(given[qid]))}）："
                "同一题只能有一个答案，系统不替你选，也不按最后一个算",
                "这一题的倾向不能确定，画像不能确认",
                "只保留一个答案（拿不准可以选“暂不确定”）",
            )
        )
    for q in hq.questions:
        if q.question_id in duplicated:
            health_items.append(
                HealthReviewItem(
                    question_id=q.question_id,
                    text=q.text,
                    answer_state="unanswered",
                    option=None,
                    option_label=None,
                    meaning="这一题收到了多个答案：确认前请只保留一个（系统不替你选）",
                )
            )
            continue
        choice = answers.get(q.question_id)
        meaning, _note = health_meaning(pack, q.question_id, choice)
        state = "unanswered" if choice is None else ("unsure" if choice == "unsure" else "provided")
        health_items.append(
            HealthReviewItem(
                question_id=q.question_id,
                text=q.text,
                answer_state=state,  # type: ignore[arg-type]
                option=choice if state == "provided" else None,  # type: ignore[arg-type]
                option_label=labels[choice] if state == "provided" else None,
                meaning=meaning,
            )
        )
    unclear = [h for h in health_items if h.answer_state != "provided"]
    if unclear:
        summary.append(
            f"健康问卷有 {len(unclear)} 题没有明确倾向（未答或暂不确定）：照实保留，分身会被告知没有这几项的明确指令"
        )
    items.append(
        ItemStatus(
            item="health",
            label="健康风险应对（独立问卷）",
            answer_state="provided"
            if not unclear
            else ("unanswered" if len(unclear) == len(health_items) else "provided"),
            support="partially_supported",
            confirmation="pending",
            usage=("planning",),
            note="不占六维 100 分，不是第七个评分权重；只供分身做计划，不改变疾病规则、账目或满足感；题目是暂定文案",
        )
    )

    # 自由文字
    text = draft.free_text.text if draft.free_text.state == "provided" else None
    chars = len(text or "")
    if chars > 2000:
        issues.append(
            issue(
                "free_text_too_long",
                "free_text.text",
                f"补充文字有 {chars} 个字符，超过 2,000 的上限",
                "系统不会自动截断，超出部分不能静默丢弃",
                "自己删减到 2,000 字符以内",
            )
        )
    items.append(
        ItemStatus(
            item="free_text",
            label="可选补充文字",
            answer_state=draft.free_text.state,
            support="partially_supported",
            confirmation="pending",
            usage=("planning",),
            note="只供分身做计划参考；不会自动改分数、现金、资格或硬要求，本轮不用 LLM 提取",
        )
    )

    # 属性偏好与硬要求
    statements: list[StatementReview] = []
    kinds = {a.kind: a for a in b.attribute_kinds}
    for a in draft.attribute_preferences:
        decl = kinds.get(a.kind)
        support = decl.support if decl is not None else "unsupported"
        usage = decl.usage if decl is not None else ("retained_only",)
        note = decl.note if decl is not None else "本版没有这类偏好的定义：只留存原文"
        msg = f"“{a.raw_text}”：{note}"
        if a.as_hard_requirement:
            msg += (
                "。你把它标成了不可妥协的要求，但本版没有执行保障：它不会拦截任何计划，只原样留存"
            )
            issues.append(
                issue(
                    "hard_requirement_not_enforceable",
                    f"attribute_preferences.{a.preference_id}",
                    f"“{a.raw_text}”被标成不可妥协，但本版没有能核验或拦截它的机制",
                    "分身仍可能选择与它冲突的方案（例如上门服务）；这条要求只留存，不保证执行",
                    "知悉后确认，或改成普通偏好",
                    severity="warning",
                )
            )
        pending.append(f"ack:retained:{a.preference_id}")
        statements.append(
            StatementReview(
                ref=a.preference_id,
                kind=a.kind,
                raw_text=a.raw_text,
                support=support,  # type: ignore[arg-type]
                usage=usage,  # type: ignore[arg-type]
                as_hard_requirement=a.as_hard_requirement,
                execution_guaranteed=False,
                message_zh=msg[:400],
            )
        )
    options = {h.option_id: h for h in b.hard_requirement_options}
    # E3-A 收尾 F4：同一编号、或同一项 E1 需求（例如两条通勤上限）出现不止一次，一律拒绝——不按最后一条算，也不自动取更严格的
    # 那一条；两条完全相同也拒绝（V1 不做合并规则）。按需求比较，不只比较编号字符串。
    id_count: dict[str, int] = {}
    by_need: dict[str, list[HardRequirementAnswer]] = {}
    for h in draft.hard_requirements:
        id_count[h.requirement_id] = id_count.get(h.requirement_id, 0) + 1
        opt = options.get(h.option_id or "")
        if opt is not None:
            by_need.setdefault(opt.need, []).append(h)
    for rid, n in sorted(id_count.items()):
        if n > 1:
            issues.append(
                issue(
                    "hard_requirement_duplicated",
                    f"hard_requirements.{rid}",
                    f"硬要求编号 {rid} 出现了 {n} 次：系统不替你决定保留哪一条",
                    "这条硬要求还不能使用，画像不能确认",
                    "只保留一条",
                )
            )
    for need, group in sorted(by_need.items()):
        if len(group) > 1:
            opt = options[group[0].option_id or ""]
            values = "、".join(
                sorted(
                    "未填" if h.parameter_value is None else f"{h.parameter_value} {opt.unit}"
                    for h in group
                )
            )
            issues.append(
                issue(
                    "hard_requirement_conflict",
                    f"hard_requirements.need.{need}",
                    f"“{opt.label}”收到了 {len(group)} 条（{values}）：同一项要求只能有一条，"
                    "系统不自动取更严格的上限，也不按最后一条算",
                    "这项硬要求还不能使用，画像不能确认",
                    "只保留一条，写你真正要的那个数",
                )
            )
    for h in draft.hard_requirements:
        opt = options.get(h.option_id or "")
        if opt is None:
            pending.append(f"ack:retained:{h.requirement_id}")
            issues.append(
                issue(
                    "hard_requirement_unsupported",
                    f"hard_requirements.{h.requirement_id}",
                    f"“{h.raw_text or h.option_id}”不是本版能核验的硬要求选项",
                    "它只原样留存，不会拦截计划，也不会被核验",
                    "知悉后确认，或从受支持的选项里选择",
                    severity="warning",
                )
            )
            statements.append(
                StatementReview(
                    ref=h.requirement_id,
                    kind="hard_requirement",
                    raw_text=(h.raw_text or h.option_id or "")[:300],
                    support="unsupported",
                    usage=("retained_only",),
                    as_hard_requirement=True,
                    execution_guaranteed=False,
                    message_zh="本版没有这项硬要求的定义与资料：只留存原文，不核验、不拦截",
                )
            )
            continue
        v = h.parameter_value
        if v is None or not opt.minimum <= v <= opt.maximum or (v - opt.minimum) % opt.step:
            issues.append(
                issue(
                    "hard_requirement_parameter_invalid",
                    f"hard_requirements.{h.requirement_id}.parameter_value",
                    f"“{opt.label}”需要 {opt.minimum}–{opt.maximum} {opt.unit}、步长 {opt.step} 的整数，得到 {v}",
                    "这条硬要求还不能使用",
                    "填写范围内的数值",
                )
            )
        statements.append(
            StatementReview(
                ref=h.requirement_id,
                kind="hard_requirement",
                raw_text=f"{opt.label} {v} {opt.unit}",
                support="supported",
                usage=("planning", "scoring"),
                as_hard_requirement=True,
                execution_guaranteed=False,
                message_zh=f"{opt.verification}",
            )
        )
    # 资格
    known = {a.ability_id for a in b.abilities}
    abilities: list[str] = []
    # E3-A 收尾 F4：同一资格出现不止一次（状态冲突或完全相同）一律拒绝，不按“出现一次已提供就算持有”处理
    claimed: dict[str, list[str]] = {}
    state_text = {
        "provided": "已提供",
        "unsure": "暂不确定",
        "indifferent": "无所谓",
        "unanswered": "未答",
    }
    for c in draft.abilities:
        claimed.setdefault(c.ability_id, []).append(state_text.get(c.state, c.state))
    repeated = {aid for aid, got in claimed.items() if len(got) > 1}
    for aid in sorted(repeated):
        issues.append(
            issue(
                "ability_duplicated",
                f"abilities.{aid}",
                f"资格 {aid} 出现了 {len(claimed[aid])} 次（{'、'.join(sorted(claimed[aid]))}）："
                "系统不替你判断持不持有",
                "这项资格不能进入开局，画像不能确认",
                "只保留一条：持有就选“已提供”，不确定就选“暂不确定”",
            )
        )
    for c in draft.abilities:
        if c.ability_id in repeated:
            continue
        if c.ability_id not in known:
            issues.append(
                issue(
                    "ability_unknown",
                    f"abilities.{c.ability_id}",
                    f"本版没有资格 {c.ability_id} 的定义",
                    "这项资格不会进入开局",
                    "只确认能力清单里列出的资格",
                )
            )
        elif c.state == "provided":
            abilities.append(c.ability_id)
    items.append(
        ItemStatus(
            item="abilities",
            label="已确认的资格",
            answer_state="provided" if abilities else "unanswered",
            support="supported",
            confirmation="pending",
            usage=("qualification", "start_state"),
            note="只来自你明确确认的事实，不从职业称呼自动授予",
        )
    )
    from product.interests import Term, review_one

    leisure_terms = tuple(Term(t.key, t.label, t.aliases) for t in b.interests.leisure_categories)
    cuisine_terms = tuple(Term(t.key, t.label, t.aliases) for t in b.interests.cuisines)
    leisure_review, more, _ = review_one(
        "leisure",
        draft.leisure_interest.state,
        draft.leisure_interest.text,
        tuple(draft.leisure_interest.selected),
        leisure_terms,
    )
    issues += more
    taste_review, more, _ = review_one(
        "taste", draft.taste.state, draft.taste.text, tuple(draft.taste.selected), cuisine_terms
    )
    issues += more
    for key, label, r in (
        ("leisure_interest", "休闲兴趣", leisure_review),
        ("taste", "口味与菜系", taste_review),
    ):
        items.append(
            ItemStatus(
                item=key,
                label=label,
                answer_state="provided"
                if r.state == "described"
                else (
                    "unsure"
                    if r.state == "unsure"
                    else "unanswered"
                    if r.state == "unanswered"
                    else "provided"
                ),
                support="partially_supported",
                confirmation="pending",
                usage=("planning", "scoring") if r.selected else ("planning",),
                note=r.message_zh[:300],
            )
        )
    pending.append("ack:standard_start")
    confirmable = not any(i.severity == "error" for i in issues)
    if confirmable:
        summary.insert(0, "答案可以确认：请核对下面的解释，并知悉标准开局与只留存的陈述")
    review = ProfileReview(
        meta=pack.meta(sample),
        review_id=f"review.{draft.draft_id}.r{draft.revision}",
        draft=draft_ref(draft),
        confirmable=confirmable,
        cities=tuple(city_refs),
        occupation=occupation_review,
        age=age,
        money=money_review,
        six_dimensions=six_review,
        material=material_review,
        health=tuple(health_items),
        free_text_chars=chars,
        statements=tuple(statements),
        abilities=tuple(abilities),
        items=tuple(items),
        pending_confirmations=tuple(dict.fromkeys(pending)),
        issues=tuple(issues),
        summary_zh=tuple(summary),
        leisure_interest=leisure_review,
        taste=taste_review,
    )
    return review


def _money(pack: DataPack, draft: UserInputDraft) -> tuple[MoneyReview, list[ProductIssue]]:
    c = draft.cash
    if c.state != "provided" or c.amount_text is None:
        return (
            MoneyReview(status="missing", meaning="可动用的流动资金"),
            [
                issue(
                    "cash_missing",
                    "cash.amount_text",
                    "还没有填写开始时可用于生活的钱（没填不是 0）",
                    "开局现金未知，不能生成开局",
                    "填写金额、币种与金额时期",
                )
            ],
        )
    issues: list[ProductIssue] = []
    digits = pack.body.money_basis.minor_unit_digits
    minor = None
    try:
        minor = parse_money_text(c.amount_text, minor_digits=digits)
    except ExactParseError as exc:
        issues.append(
            issue(
                exc.code,
                "cash.amount_text",
                str(exc),
                "开局现金不能确定：系统不会猜你想填的金额",
                exc.next_action or "填写非负金额，最多两位小数",
            )
        )
    option = next(
        (
            m
            for m in pack.body.money_bases
            if m.currency == c.currency and m.price_period == c.price_period
        ),
        None,
    )
    if option is None:
        issues.append(
            issue(
                "money_conversion_missing",
                "cash",
                f"{c.currency or '（未写币种）'} / {c.price_period or '（未写时期）'} 的金额在本版没有合法的换算依据",
                "不能把这个金额直接改标签成 2024 年当地美元——那会改变它的含义",
                "改用能力清单支持的币种与时期（本版：2024 年美元），或等待真实换算资料接入（E3-B/C）",
            )
        )
    review = MoneyReview(
        status="convertible" if option is not None and minor is not None else "blocked",
        amount_text=c.amount_text,
        currency=c.currency,
        price_period=c.price_period,
        meaning="可动用的流动资金（不是总资产、信用额度或月薪）",
        conversion_rule=None if option is None else option.conversion_rule,
        converted=None
        if option is None or minor is None
        else ProductMoney(
            amount_minor=minor,
            currency=option.currency,
            numeraire_id=option.numeraire_id,
            display=money_display(minor, digits),
        ),
    )
    return review, issues


# ---------------------------------------------------------------------- 确认


def confirm_profile(
    pack: DataPack,
    draft: UserInputDraft,
    *,
    person_ref: str,
    acknowledged: Iterable[str],
    sample: SampleLabel | None = None,
) -> ConfirmedProfile:
    """用户确认：审阅没有阻断问题，并且用户知悉了全部待确认事项（标准开局、只留存的陈述……）。否则拒绝，不替用户确认。"""
    review = review_draft(pack, draft)
    blocking = tuple(i for i in review.issues if i.severity == "error")
    missing_ack = [k for k in review.pending_confirmations if k not in set(acknowledged)]
    if missing_ack:
        blocking += tuple(
            issue(
                "acknowledgement_missing",
                k,
                f"还没有确认：{_ack_text(k)}",
                "没有知悉这项说明就不能确认画像",
                "阅读说明后勾选确认",
            )
            for k in missing_ack
        )
    if blocking:
        raise ConfirmationRefusedError(blocking)
    b = pack.body
    digits = b.points.display_decimals
    six = {ln.key: Fraction(ln.exact) for ln in review.six_dimensions.lines if ln.exact is not None}
    concept = pack.occupation(review.occupation.selected or "")
    assert concept is not None and review.money.converted is not None and review.age is not None
    assert draft.occupation.raw_text is not None
    material = (
        ConfirmedMaterial(status="not_applicable_parent_zero")
        if review.material.status == "not_applicable"
        else ConfirmedMaterial(
            status="allocated",
            allocation=ConfirmedAllocation(
                mode=review.material.mode or "manual",
                points={ln.key: ln.exact for ln in review.material.lines if ln.exact is not None},  # type: ignore[misc]
                display={
                    ln.key: ln.display for ln in review.material.lines if ln.display is not None
                },  # type: ignore[misc]
            ),
        )
    )
    hq = b.health_questionnaire
    answers = {a.question_id: a.choice for a in draft.health.items}
    health = ConfirmedHealth(
        questionnaire_version=hq.version,
        status=hq.status,
        answers=tuple(
            ConfirmedHealthAnswer(
                question_id=h.question_id,
                question_text=h.text,
                answer_state=h.answer_state,
                option=h.option,
                option_label=h.option_label,
                meaning=health_meaning(pack, h.question_id, answers.get(h.question_id))[0],
            )
            for h in review.health
        ),
    )
    options = {h.option_id: h for h in b.hard_requirement_options}
    hard: list[ConfirmedHardRequirement] = []
    retained: list[RetainedStatement] = []
    for h in draft.hard_requirements:
        opt = options.get(h.option_id or "")
        if opt is None:
            retained.append(
                RetainedStatement(
                    ref=h.requirement_id,
                    kind="hard_requirement",
                    raw_text=(h.raw_text or h.option_id or "")[:300],
                    support="unsupported",
                    usage=("retained_only",),
                    as_hard_requirement=True,
                    message_zh="本版没有这项硬要求的定义与资料：只留存原文，不核验、不拦截",
                )
            )
            continue
        assert h.parameter_value is not None
        hard.append(
            ConfirmedHardRequirement(
                requirement_id=h.requirement_id,
                option_id=opt.option_id,
                parameter_value=h.parameter_value,
                label_zh=f"{opt.label} {h.parameter_value} {opt.unit}（单独核验并报告，不拦截计划）",
            )
        )
    kinds = {a.kind: a for a in b.attribute_kinds}
    for a in draft.attribute_preferences:
        decl = kinds.get(a.kind)
        retained.append(
            RetainedStatement(
                ref=a.preference_id,
                kind=a.kind,
                need=a.need,
                raw_text=a.raw_text,
                support="unsupported"
                if decl is None or decl.support == "unsupported"
                else "partially_supported",
                usage=decl.usage if decl is not None else ("retained_only",),  # type: ignore[arg-type]
                as_hard_requirement=a.as_hard_requirement,
                message_zh=(
                    decl.note if decl is not None else "本版没有这类偏好的定义：只留存原文"
                )[:400],
            )
        )
    sources = tuple(
        FieldSource(
            field=f,
            evidence=(
                EvidenceRef(kind="form_field", ref=f"draft:{draft.draft_id}#{f}"),
                *(e for e in draft.evidence if e.ref.startswith(f)),
            ),
        )
        for f in (
            "cities",
            "occupation",
            "age",
            "cash",
            "six_dimensions",
            "material",
            "health",
            "free_text",
            "abilities",
            "hard_requirements",
            "attribute_preferences",
            "leisure_interest",
            "taste",
        )
    )
    from product.interests import confirmed as confirmed_interests

    fields = dict(
        meta=pack.meta(sample),
        confirmed_id=f"confirmed.{draft.draft_id}.r{draft.revision}",
        person_ref=person_ref,
        draft=review.draft,
        review_id=review.review_id,
        confirmation_source="user_form",
        cities=review.cities,
        occupation=ConfirmedOccupation(
            concept_id=concept.concept_id,
            label=concept.label,
            duties=concept.duties,
            raw_text=draft.occupation.raw_text,
            language=draft.occupation.language,
            match_status=review.occupation.status,  # type: ignore[arg-type]
            internal_mappings=tuple(
                OccupationMapping(
                    country=m.country,
                    scheme=m.scheme,
                    version=m.version,
                    code=m.code,
                    basis=m.basis,
                )
                for m in concept.mappings
            ),
        ),
        age_years=review.age,
        money=ConfirmedMoney(
            amount_text=draft.cash.amount_text,  # type: ignore[arg-type]
            currency=draft.cash.currency,  # type: ignore[arg-type]
            price_period=draft.cash.price_period,  # type: ignore[arg-type]
            meaning="liquid_funds_v1",
            conversion_rule=review.money.conversion_rule,  # type: ignore[arg-type]
            converted=review.money.converted,
        ),
        six_dimensions=ConfirmedAllocation(
            mode=review.six_dimensions.mode or "manual",
            points={k: canonical(v) for k, v in six.items()},
            display={k: display(v, digits) for k, v in six.items()},
        ),
        material=material,
        health=health,
        free_text=draft.free_text.text if draft.free_text.state == "provided" else None,
        abilities=review.abilities,
        hard_requirements=tuple(hard),
        retained=tuple(retained),
        sources=sources,
        interests=confirmed_interests(
            review.leisure_interest, review.taste, pack.body.interests.matching_note_zh
        ),
    )
    unsealed = ConfirmedProfile.model_construct(**fields, content_fingerprint="0" * 64)
    return ConfirmedProfile(**fields, content_fingerprint=unsealed.expected_fingerprint())


def _ack_text(key: str) -> str:
    if key == "ack:standard_start":
        return (
            "这是一次从健康、没有已有物品、没有在学课程开始的简化情景（现实中的家具与病史暂未纳入）"
        )
    if key.startswith("ack:retained:"):
        return f"陈述 {key.split(':', 2)[2]} 本版不评分、不保证执行，只留存原文"
    return key


def confirmation_problems(
    pack: DataPack, confirmed: ConfirmedProfile, draft: UserInputDraft | None = None
) -> list[ProductIssue]:
    """一份确认画像现在还能不能用：内容身份、版本、资料包，以及（给了当前草稿时）草稿有没有改过。"""
    out: list[ProductIssue] = []
    if confirmed.content_fingerprint != confirmed.expected_fingerprint():
        out.append(
            issue(
                "confirmation_tampered",
                "content_fingerprint",
                "确认画像的内容与它记下的身份不一致（可能被改写过）",
                "不能用这份确认运行",
                "重新审阅并确认",
            )
        )
    if confirmed.meta.schema_version != PRODUCT_SCHEMA_VERSION:
        out.append(
            issue(
                "version_changed",
                "meta.schema_version",
                "确认画像的结构版本与现在不同",
                "旧结构的确认不能直接沿用",
                "重新确认",
            )
        )
    out += _version_issues(pack, confirmed.meta.model_dump(mode="json"), "确认画像")
    if draft is not None and draft_fingerprint(draft) != confirmed.draft.draft_fingerprint:
        out.append(
            issue(
                "confirmation_invalidated_by_edit",
                "draft",
                "原始答案在确认之后改过：旧确认已经失效",
                "改过的答案还没有确认，不能运行",
                "按修改后的答案重新审阅并确认",
            )
        )
    return out
