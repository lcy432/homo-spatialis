"""E3-A 新产品画像的 E1 侧形状（DECISIONS/0034）。**只有形状与局部约束，没有评分计算。**

新产品问卷的含义（所有者 2026-10-17 确认）：

* **六维定和 100**：``material / safety / opportunity / leisure / environment / commute``。点数用**精确分数**写，规范写法是
  约分后的 ``"30"``、``"50/3"``——六维均分每项 ``50/3``，不能让排在前面的维度多拿 1 分；显示时四舍五入只是显示，不回流计算。
* **物质细分**：物质父项为正时，细项（当前词表 ``housing_condition / food_life / daily_convenience / personal_enjoyment``）另分 100；父项明确为 0 时细分
  不适用（``not_applicable_parent_zero``），不伪造三个回答。
* **独立健康回答**：一份有版本的健康风险应对问卷，**不占**六维 100 分，也**不是**第七个评分权重；只作为分身做计划的信息，
  不改变疾病规则、γ、H、账目或满足感。未答与“暂不确定”照实保存，观察里说明没有相应的明确指令。

它是用户**陈述**，不是人的完整心理真值；不给画像补 ``healthcare = 0``。

精确分数怎样进入评分（``econ.life_scoring.preference_from_confirmed_profile``）：w_k = p_k / 100，精确相除后**只转一次**浮点。
"""

from __future__ import annotations

from fractions import Fraction
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, model_validator

from common.ids import OccupationId
from common.life_contracts import ContractModel, HardRequirement, IdStr, KeyStr
from worlds.dimensions import ALL_DIMENSIONS, MATERIAL

#: 新产品的六维：七维去掉 healthcare，顺序沿用 ``worlds.dimensions`` 的唯一定义。
SIX_DIMENSIONS: tuple[str, ...] = tuple(d for d in ALL_DIMENSIONS if d != "healthcare")
assert MATERIAL in SIX_DIMENSIONS and len(SIX_DIMENSIONS) == 6
PRODUCT_PROFILE_VERSION = "product-life-profile-v2"
#: 定和总数。
POINTS_TOTAL = Fraction(100)
_POINTS_PATTERN = r"^(0|[1-9][0-9]*)(/[1-9][0-9]*)?$"
_SHA256 = r"^[0-9a-f]{64}$"


def canonical_points(q: Fraction) -> str:
    """一个精确点数的规范写法：约分后的整数（``"30"``）或 ``分子/分母``（``"50/3"``）。"""
    q = Fraction(q)
    if q < 0:
        raise ValueError(f"点数不能为负，得到 {q}")
    return str(q.numerator) if q.denominator == 1 else f"{q.numerator}/{q.denominator}"


def _canonical_text(text: str) -> str:
    q = Fraction(text)
    if canonical_points(q) != text:
        raise ValueError(f"点数 {text!r} 不是规范写法（应写 {canonical_points(q)!r}）")
    if not 0 <= q <= POINTS_TOTAL:
        raise ValueError(f"点数 {text} 不在 0–100 之间")
    return text


#: 规范的精确点数字符串（0–100）。
ExactPoints = Annotated[str, Field(pattern=_POINTS_PATTERN), AfterValidator(_canonical_text)]

HealthAnswerState = Literal["provided", "unsure", "unanswered"]
#: 生活扩展（DECISIONS/0041）：休闲兴趣与口味的确认状态。未答、暂不确定、明确没有固定爱好 / 没有口味偏好、选择通用休闲评价与已确认
#: 类别分开保存；只有 ``confirmed`` 带类别，其余都按中性规则评分（空白不会被悄悄当成“通用评价”的选择）。
LeisureInterestStatus = Literal["unanswered", "unsure", "no_fixed_hobby", "generic", "confirmed"]
TasteStatus = Literal["unanswered", "unsure", "no_preference", "confirmed"]
HealthOption = Literal["agree", "disagree", "no_clear_tendency"]


class ProductHealthAnswer(ContractModel):
    """独立健康问卷里的一题：题目、作答状态、选项与给分身的读法（由 E3 按题目与选项生成，不推断任何数值）。

    * ``provided``：选了“符合我 / 不符合我 / 无明显倾向”之一（``option``）；
    * ``unsure``：明确选了“暂不确定”——没有这一项的明确指令，不补中间值；
    * ``unanswered``：没有作答——同样没有明确指令，与“不确定”分开保存。
    """

    question_id: KeyStr
    question_text: str = Field(min_length=1, max_length=400)
    answer_state: HealthAnswerState
    option: HealthOption | None = None
    option_label: str | None = Field(default=None, max_length=40)
    planning_note: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def _option_matches_state(self) -> ProductHealthAnswer:
        if (self.answer_state == "provided") != (self.option is not None):
            raise ValueError(
                f"{self.question_id}: 只有 provided 才有选项（得到 {self.answer_state} / {self.option}）"
            )
        if (self.option is None) != (self.option_label is None):
            raise ValueError(f"{self.question_id}: 选项与选项文字要一起写")
        return self


class ProductLifeProfile(ContractModel):
    """**产品可见**的新画像（E3-A）：六维精确点数、物质细分、独立健康回答、受支持的硬要求、可选文字与陈述的职业。

    由 E3 从用户确认的画像（``common.product_contracts.ConfirmedProfile``）编译而来，``confirmed_profile_fingerprint`` 记下那份
    确认的内容身份——确认失效或被改写，编译出的画像也就对不上。进不进代理输入、进多少，由
    ``agentlayer.life_views.ObservationPermission`` 显式决定（健康回答另有显式开关 ``show_health_answers``）。
    """

    profile_version: Literal["product-life-profile-v2"]
    person_id: IdStr
    confirmed_profile_fingerprint: Annotated[str, Field(pattern=_SHA256)]
    dimension_points: dict[str, ExactPoints]
    material_status: Literal["allocated", "not_applicable_parent_zero"]
    material_points: dict[KeyStr, ExactPoints] | None = None
    health_questionnaire_version: str = Field(min_length=1, max_length=80)
    health_answers: tuple[ProductHealthAnswer, ...] = Field(min_length=1)
    hard_requirements: tuple[HardRequirement, ...] = ()
    free_text: str | None = Field(default=None, max_length=2000)
    occupation: OccupationId | None = None
    #: 用户确认后进入有限匹配规则的休闲兴趣类别与菜系（词表键）；状态见上。不改变点数、现金、资格或硬要求。
    leisure_interest_status: LeisureInterestStatus
    leisure_interests: tuple[KeyStr, ...] = ()
    taste_status: TasteStatus
    cuisines: tuple[KeyStr, ...] = ()
    #: 保留但本轮不支持的描述（品牌、其他要求），原样保存给用户看；不进任何评分，不产生品牌溢价或加成。
    #: 本版未支持、只留存原文的陈述（品牌、没认出的兴趣或口味描述）：只给审计与结果说明用，不评分，也不进代理观察（收尾 R7）
    unsupported_requests: tuple[Annotated[str, Field(min_length=1, max_length=200)], ...] = ()

    @model_validator(mode="after")
    def _interests_match_status(self) -> ProductLifeProfile:
        for status, items, name in (
            (self.leisure_interest_status, self.leisure_interests, "休闲兴趣"),
            (self.taste_status, self.cuisines, "口味"),
        ):
            if (status == "confirmed") != bool(items):
                raise ValueError(f"{self.person_id}: 只有确认的{name}才带类别，确认了就必须有类别")
            if len(set(items)) != len(items):
                raise ValueError(f"{self.person_id}: {name}类别重复")
        return self

    @model_validator(mode="after")
    def _constant_sums(self) -> ProductLifeProfile:
        keys = set(self.dimension_points)
        if keys != set(SIX_DIMENSIONS):
            raise ValueError(
                f"{self.person_id}: 六维必须恰好是 {list(SIX_DIMENSIONS)}；缺 {sorted(set(SIX_DIMENSIONS) - keys)}，"
                f"多 {sorted(keys - set(SIX_DIMENSIONS))}——新画像没有 healthcare 点数，健康回答另有问卷"
            )
        total = sum(self.dimension_fractions().values(), Fraction(0))
        if total != POINTS_TOTAL:
            raise ValueError(
                f"{self.person_id}: 六维必须精确合计 100，得到 {canonical_points(total)}"
            )
        parent = self.dimension_fractions()[MATERIAL]
        if parent > 0:
            if self.material_status != "allocated" or not self.material_points:
                raise ValueError(
                    f"{self.person_id}: 物质父项有 {canonical_points(parent)} 点，必须有物质细分（合计 100）"
                )
            sub = sum(self.material_fractions().values(), Fraction(0))
            if sub != POINTS_TOTAL:
                raise ValueError(
                    f"{self.person_id}: 物质细分必须精确合计 100，得到 {canonical_points(sub)}"
                )
        elif (
            self.material_status != "not_applicable_parent_zero" or self.material_points is not None
        ):
            raise ValueError(
                f"{self.person_id}: 物质父项是 0，细分不适用——写 not_applicable_parent_zero，不伪造细项回答"
            )
        ids = [a.question_id for a in self.health_answers]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{self.person_id}: 健康问卷的题目重复")
        needs = [r.need for r in self.hard_requirements]
        if len(needs) != len(set(needs)):
            raise ValueError(f"{self.person_id}: 同一需求的硬要求重复")
        return self

    def dimension_fractions(self) -> dict[str, Fraction]:
        """六维点数的精确分数（按 ``SIX_DIMENSIONS`` 的顺序）。"""
        return {
            k: Fraction(self.dimension_points[k])
            for k in SIX_DIMENSIONS
            if k in self.dimension_points
        }

    def material_fractions(self) -> dict[str, Fraction]:
        """物质细项点数的精确分数（父项为 0 时为空）。"""
        return {k: Fraction(v) for k, v in (self.material_points or {}).items()}
