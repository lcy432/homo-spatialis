"""便利资料：候选 → 合法 E1 机会、未编入原因与选项目录覆盖（E3-A 模块 D，DECISIONS/0034；收尾 F2 见第九节）。

便利机制已经在 E1 里完成（ADR0033），这里**不重写**它：只把资料候选编译成 E1 能执行的机会（设备 = 一次性购买的持续物品，服务 =
按月购买），让 E1 照旧按“每项家务取最小残余、设备寿命、服务实际覆盖的月份”结算与评分。编译规则：

* 报价记录币种、基年、当地价口径、计费周期与计价单位；口径与资料包不同不编入；**单次上门不能无依据变成月套餐**（``per_visit``
  不编入）。报价未知但其余结构齐全 → 编成 ``availability = unknown``（分身看到资料未知、不能购买；不当免费）。计价单位缺失不编入，
  **不自动补**成 appliance-set 或 service-month（收尾 F2）。金额按实际数值判断能否精确表示为最小记账单位（“1e-3”同样拒绝，不四舍五入）。
* 报价来源的单位必须与候选的**计费周期和计价对象**都对上（``_SOURCE_DENOMINATORS``、``_PER_UNIT_KIND`` 两张小表）：“216 美元/次”的
  来源不能支持“216 美元/月”的候选，按台的来源不能支持按套的候选；没有登记的计价对象或计价单位不猜，按候选拒绝。
* 设备寿命必须是明确的 1–30 整数年并写明生效时点；未知或非整数年**不补值、不取整**，不编入——也不改写成“确定买不到”，真实供给
  状态（``market_status``）原样保留在未编入记录里。
* 减负效果是“本人处理整项家务后仍需的月小时”，按协议的共同任务定义（同一单位“小时/月”）；效果未知**不能**删掉替代字段伪装成
  确定无效果，不编入。同一项家务在一个候选里只能有一条残余（重复的，包括内容完全相同的，在候选阶段拒绝，不交给 E1 去抛异常）。
  服务本人的参与时间未知也不编入（不当 0）；参与小时与每小时负担必须是不小于 0 的数（收尾 F2）。
* **每个实际用到的已知字段都要有来源**（收尾 F2）：报价、寿命、效果、本人参与各自引用资料包里的来源事实；事实必须存在、记录的
  就是这个字段（跨字段推导本版没有登记任何方法，不接受）、单位对得上；报价与寿命这类单个数值还要有一条事实的原始值等于它，或
  是写明了换算方法的派生 / 估计事实。缺来源就不编入——不自动生成“合成”来源或空泛的假设说明。合成事实本身必须写假设说明
  （``SourceFact`` 的校验），所以合成路径只有在假设真实登记并被引用时才能用。
* 编入的机会整条的来源标签取实际用到的事实里最弱的那一类（有一项是合成假设，整条就是合成）——真实报价不会把假设效果变成观测。
* 可以预期的资料错误逐候选给出结构化原因，其余合法候选照常编译；编出来的机会仍须通过 E1 的 ``LifeOpportunity`` 校验，那里再失败
  就是程序错误，原样抛出（不当成资料缺失吞掉）。

``option_inventory_coverage`` 是另一件事：在**声明的调查范围**内，候选的供给状态、资料状态与编译状态分开记（已解决 / 未解决 /
排除，分母是调查过的全部候选）。已证实不可得是已确定的调查结果（已解决），但不是可购买的方案；供给未知与报价未知分别解释。它与
“实际轨迹能否评分”、分身的 C / E / M 无关：目录缺口不改写 E1 已发生轨迹的评分。
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any

from common.life_contracts import MAX_DURABLE_YEARS, LifeOpportunity
from common.product_contracts import (
    CatalogCompilation,
    CityInventory,
    CompiledOpportunity,
    InventoryItem,
    OpportunityCandidate,
    OptionInventoryCoverage,
    ProductIssue,
    SampleLabel,
    SourceFact,
    TaskDefinition,
    UncompiledCandidate,
    content_fingerprint,
    identity_content,
)
from product.answers import issue
from product.display import Code, Ent, Join, Num, Raw, Sub, label, say, t
from product.display.text import has_cjk
from product.pack import DataPack

COMPILER_VERSION = "e3a-catalog-compiler-v2"
#: 来源性质从弱到强：有一项合成，整条机会就标合成。
_KIND_ORDER = ("synthetic", "estimated", "derived", "observed")
_FIELD_LABEL = {
    "price": "报价",
    "lifetime": "寿命",
    "effect": "减负效果",
    "participation": "本人参与时间",
}


#: 报价来源单位写成“<币种>_<基年>_LOCAL/<计价对象>”。计价对象 → （计费周期，计价对象类别）。只认这里登记过的写法：
#: 没有登记的不猜它是按次、按月还是按台，按候选拒绝。
_SOURCE_DENOMINATORS: dict[str, tuple[str, str]] = {
    "月": ("per_month", "service_month"),
    "次": ("per_visit", "visit"),
    "套": ("one_time", "set"),
    "台": ("one_time", "machine"),
}
#: 候选的计价单位（E1 机会的 ``unit``）→ 计价对象类别：单件家电按“台”，成套设备按“套”，按月服务按“月”，上门按“次”。
_PER_UNIT_KIND: dict[str, str] = {
    "service-month": "service_month",
    "visit": "visit",
    "appliance-set": "set",
    "machine": "machine",
    "washer": "machine",
    "dryer": "machine",
    "robot": "machine",
}
_PERIOD_LABEL = {"per_month": "按月", "per_visit": "按次", "one_time": "一次性"}
_KIND_LABEL = {"service_month": "服务月", "visit": "次", "set": "套", "machine": "台"}

# ---------------------------------------------------------------------- 显示文字（显示附层）
#
# 编译原因都是有 key 的模板（``Sub``），生成问题时才变成文字：同一句原因在覆盖报告里还要再拼进一句话。字段、计费周期、计价对象
# 与候选种类的名称按实体绑定（``candidate_field`` / ``billing_period`` / ``price_object`` / ``candidate_kind``）；资料里的原样
# 文字（编号、单位、数值）不翻译。


def _say(s: Sub) -> str:
    return t(s.key, s.zh, **dict(s.params))


def _plain(value: object) -> Code | Raw:
    """资料里的原样文字（编号、单位、数值）：不含中文就各语言相同，含中文就是没有译文的原文片段。"""
    text = f"{value}"
    return Raw(text) if has_cjk(text) else Code(text)


def _field(name: str) -> Ent:
    return Ent("candidate_field", name, _FIELD_LABEL[name])


def _period(billing: object) -> Ent | Code | Raw:
    if isinstance(billing, str) and billing in _PERIOD_LABEL:
        return Ent("billing_period", billing, _PERIOD_LABEL[billing])
    return _plain(billing)


def _object(kind: str) -> Ent:
    return Ent("price_object", kind, _KIND_LABEL[kind])


_NOT_COMPILED = Sub("catalog.impact.not_compiled", "这项候选不能编入目录")
_NOT_COMPILED_INCOMPLETE = Sub(
    "catalog.impact.not_compiled_not_complete", "这项候选不能编入目录，也不标为资料完整"
)
_NOT_COMPILED_OTHERS_OK = Sub(
    "catalog.impact.not_compiled_others_ok", "这项候选不能编入目录（其他候选照常编译）"
)
_DEVICE_NOT_COMPILED = Sub("catalog.impact.device_not_compiled", "这项设备不能编入目录")
_SERVICE_NOT_COMPILED = Sub("catalog.impact.service_not_compiled", "这项服务不能编入目录")


@dataclass(frozen=True)
class _Problem:
    """一项候选不能编入的原因：机器代码、字段位置，原因 / 影响 / 下一步三句有 key 的模板。"""

    code: str
    field: str
    message: Sub
    impact: Sub
    action: Sub
    severity: str = "error"

    def issue(self) -> ProductIssue:
        return issue(
            self.code,
            self.field,
            _say(self.message),
            _say(self.impact),
            _say(self.action),
            severity=self.severity,
        )


def _finer_than(value: Fraction, digits: int) -> bool:
    """按实际数值判断是否细于 ``digits`` 位小数（乘以 10^digits 之后不是整数）；“1e-3”这种写法也照样识别。"""
    return (value * 10**digits).denominator != 1


def _number(text: str | None) -> Fraction | None:
    """精确解析一个十进制数文字；解析不了返回 ``None``（只捕获解析错误，不吞其他异常）。"""
    if text is None:
        return None
    try:
        d = Decimal(text.strip())
    except InvalidOperation:
        return None
    if not d.is_finite():
        return None
    return Fraction(d)


#: 家务时间的单位：结构里写死成常量（``TaskDefinition.unit`` 是 ``Literal``），绑定只能按位置（见 ``bind_json`` 的 ``constants``）
TASK_UNIT = Sub("catalog.task.unit", "小时/月")
#: ``CatalogCompilation`` 里写死成常量的显示字段
CONSTANT_TEXTS = {"/tasks/*/unit": TASK_UNIT}


def _task_definitions(
    base_protocol: dict[str, Any], tasks: tuple[str, ...]
) -> tuple[TaskDefinition, ...]:
    rules = base_protocol["resource_rules"]
    out = []
    for task in rules["household_tasks"]:
        if task["task"] in tasks:
            out.append(
                TaskDefinition(
                    task=task["task"],
                    baseline_hours_per_month=str(task["baseline_hours_per_month"]),
                    unit=say(TASK_UNIT),  # type: ignore[arg-type]
                    description=label("household_task", task["task"], task["description"]),
                )
            )
    return tuple(out)


def _unit_problem(
    pack: DataPack, c: OpportunityCandidate, field: str, f: SourceFact
) -> tuple[str, Sub] | None:
    """来源事实的单位能不能支持候选的这个字段；不能时返回（代码，原因）。不换算。

    报价要三样都对上：口径（资料包的币种、基年、当地价）、计费周期（按次 / 按月 / 一次性，与候选的 ``billing`` 相同）与计价对象
    （套 / 台 / 服务月，与候选的计价单位相同）。寿命用年，效果与参与用 小时/月。"""
    name = _field(field)
    if field == "lifetime":
        ok = f.unit == "年"
    elif field != "price":
        ok = f.unit.startswith("小时/月")
    else:
        mb = pack.body.money_basis
        basis, _, denom = f.unit.partition("/")
        if basis != f"{mb.currency}_{mb.base_year}_LOCAL" or not denom:
            return (
                "source_unit_mismatch",
                Sub(
                    "catalog.source_unit_mismatch.basis",
                    "{field}引用的事实 {fact} 单位是“{unit}”，币种、基年或当地价口径与资料包的 "
                    "{basis} 不同（不自动换算）",
                    (
                        ("field", name),
                        ("fact", _plain(f.fact_id)),
                        ("unit", _plain(f.unit)),
                        ("basis", _plain(f"{mb.currency}_{mb.base_year}_LOCAL")),
                    ),
                ),
            )
        if denom not in _SOURCE_DENOMINATORS:
            return (
                "source_unit_unregistered",
                Sub(
                    "catalog.source_unit_unregistered",
                    "{field}引用的事实 {fact} 按“{denom}”计价，这种计价对象没有登记：不能判断它是按次、按月还是一次性的价格（不猜）",
                    (("field", name), ("fact", _plain(f.fact_id)), ("denom", _plain(denom))),
                ),
            )
        period, kind = _SOURCE_DENOMINATORS[denom]
        if period != c.price.billing:
            return (
                "source_billing_mismatch",
                Sub(
                    "catalog.source_billing_mismatch",
                    "{field}的来源 {fact} 是{period}的价格（{unit}），候选是{billing}的价格：没有明确的换算依据（例如每月几次），"
                    "不把一种周期的价格当成另一种",
                    (
                        ("field", name),
                        ("fact", _plain(f.fact_id)),
                        ("period", _period(period)),
                        ("unit", _plain(f.unit)),
                        ("billing", _period(c.price.billing)),
                    ),
                ),
            )
        want = _PER_UNIT_KIND.get(c.price.per_unit or "")
        if want is not None and want != kind:
            return (
                "source_object_mismatch",
                Sub(
                    "catalog.source_object_mismatch",
                    "{field}的来源 {fact} 按“{kind}”计价，候选的计价单位 {per_unit} 按"
                    "“{want}”计价：不是同一个计价对象，不自动换算",
                    (
                        ("field", name),
                        ("fact", _plain(f.fact_id)),
                        ("kind", _object(kind)),
                        ("per_unit", _plain(c.price.per_unit)),
                        ("want", _object(want)),
                    ),
                ),
            )
        return None  # 计价单位没登记的情况在 _candidate_problems 里单独报一次
    if ok:
        return None
    return (
        "source_unit_mismatch",
        Sub(
            "catalog.source_unit_mismatch.unit",
            "{field}引用的事实 {fact} 单位是“{unit}”，与{field}要求的单位不一致（不自动换算）",
            (("field", name), ("fact", _plain(f.fact_id)), ("unit", _plain(f.unit))),
        ),
    )


def _source_problems(
    pack: DataPack,
    c: OpportunityCandidate,
    field: str,
    refs: tuple[str, ...],
    facts: dict[str, SourceFact],
    value_text: str | None = None,
) -> list[_Problem]:
    """一个实际用到的已知字段的来源：要有、要存在、要是这个字段、单位要对；单个数值还要有一条事实能支持它。"""
    where = f"candidates.{c.candidate_id}.{field}.facts"
    name = _field(field)
    if not refs:
        return [
            _Problem(
                "source_missing",
                where,
                Sub(
                    "catalog.source_missing.message",
                    "{field}有值，但没有引用任何来源事实：不能当作资料齐全，也不会自动生成来源或合成说明",
                    (("field", name),),
                ),
                _NOT_COMPILED,
                Sub(
                    "catalog.source_missing.next",
                    "为{field}补上来源事实（真实资料写定位；合成设定写明假设说明）",
                    (("field", name),),
                ),
            )
        ]
    out: list[_Problem] = []
    used: list[SourceFact] = []
    for ref in refs:
        f = facts.get(ref)
        if f is None:
            out.append(
                _Problem(
                    "source_unknown_fact",
                    where,
                    Sub(
                        "catalog.source_unknown_fact.message",
                        "{field}引用的来源事实 {fact} 在资料包里不存在",
                        (("field", name), ("fact", _plain(ref))),
                    ),
                    _NOT_COMPILED,
                    Sub("catalog.source_unknown_fact.next", "引用资料包里登记过的事实编号"),
                )
            )
        elif f.field != field:
            recorded = _field(f.field) if f.field in _FIELD_LABEL else _plain(f.field)
            out.append(
                _Problem(
                    "source_field_mismatch",
                    where,
                    Sub(
                        "catalog.source_field_mismatch.message",
                        "{field}引用的事实 {fact} 记录的是 {recorded}：不能直接支持{field}"
                        "（跨字段推导需要写明方法，本版没有登记任何跨字段推导）",
                        (("field", name), ("fact", _plain(ref)), ("recorded", recorded)),
                    ),
                    _NOT_COMPILED,
                    Sub(
                        "catalog.source_field_mismatch.next",
                        "引用记录{field}本身的事实",
                        (("field", name),),
                    ),
                )
            )
        elif (problem := _unit_problem(pack, c, field, f)) is not None:
            code, message = problem
            out.append(
                _Problem(
                    code,
                    where,
                    message,
                    _NOT_COMPILED_INCOMPLETE,
                    Sub(
                        "catalog.source_unit.next",
                        "引用计价口径、周期与对象都相同的事实，或先登记可审计的换算",
                    ),
                )
            )
        else:
            used.append(f)
    if out or value_text is None:
        return out
    want = _number(value_text)
    supported = any(
        f.kind in ("derived", "estimated") or (want is not None and _number(f.raw_value) == want)
        for f in used
    )
    if not supported:
        out.append(
            _Problem(
                "source_value_mismatch",
                where,
                Sub(
                    "catalog.source_value_mismatch.message",
                    "{field} {value} 与引用的来源事实的原始值都不一致，也没有写明换算方法的派生或估计事实",
                    (("field", name), ("value", _plain(value_text))),
                ),
                _NOT_COMPILED,
                Sub(
                    "catalog.source_value_mismatch.next",
                    "引用原始值就是这个数的事实，或登记写明换算方法的派生 / 估计事实",
                ),
            )
        )
    return out


def _candidate_problems(
    pack: DataPack,
    c: OpportunityCandidate,
    tasks: dict[str, Fraction],
    facts: dict[str, SourceFact],
) -> tuple[list[_Problem], bool]:
    """候选不能编入的原因；第二个值表示“报价未知、其余齐全”（可以编成资料未知）。确定不可得的候选不走这里。"""
    b = pack.body
    where = f"candidates.{c.candidate_id}"
    out: list[_Problem] = []
    price_unknown = False
    # 计价单位：报价已知、未知都需要（E1 机会的 unit），缺了不自动补
    p = c.price
    if not p.per_unit:
        out.append(
            _Problem(
                "price_unit_missing",
                f"{where}.price.per_unit",
                Sub(
                    "catalog.price_unit_missing.message",
                    "没有写计价单位（例如每套设备、每服务月）：系统不会自动补成 appliance-set 或 service-month",
                ),
                _NOT_COMPILED,
                Sub("catalog.price_unit_missing.next", "补上计价单位"),
            )
        )
    # 报价
    if p.status == "unknown":
        price_unknown = True
    else:
        missing = [
            name
            for name, value in (
                ("amount_text", p.amount_text),
                ("currency", p.currency),
                ("base_year", p.base_year),
                ("price_meaning", p.price_meaning),
                ("billing", p.billing),
            )
            if value is None
        ]
        mb = b.money_basis
        if missing:
            out.append(
                _Problem(
                    "price_field_missing",
                    f"{where}.price",
                    Sub(
                        "catalog.price_field_missing.message",
                        "报价标为已知，但缺少 {missing}：不能确定金额的口径",
                        (("missing", Join(tuple(Code(m) for m in missing))),),
                    ),
                    _NOT_COMPILED,
                    Sub(
                        "catalog.price_field_missing.next",
                        "补齐报价的金额、币种、基年、当地价口径与计费周期",
                    ),
                )
            )
        elif (p.currency, p.base_year, p.price_meaning) != (
            mb.currency,
            mb.base_year,
            "local_price_base_year",
        ):
            out.append(
                _Problem(
                    "price_basis_mismatch",
                    f"{where}.price",
                    Sub(
                        "catalog.price_basis_mismatch.message",
                        "报价口径是 {quote}，资料包的目录口径是 {pack}/当地基年价",
                        (
                            ("quote", _plain(f"{p.currency}/{p.base_year}/{p.price_meaning}")),
                            ("pack", _plain(f"{mb.currency}/{mb.base_year}")),
                        ),
                    ),
                    Sub("catalog.price_basis_mismatch.impact", "不同口径的钱不能进同一本账"),
                    Sub(
                        "catalog.price_basis_mismatch.next",
                        "提供同口径的报价，或先登记可审计的换算",
                    ),
                )
            )
        if not missing:
            want = "one_time" if c.kind == "device" else "per_month"
            if p.billing == "per_visit":
                out.append(
                    _Problem(
                        "billing_unit_unsupported",
                        f"{where}.price.billing",
                        Sub(
                            "catalog.billing_unit_unsupported.message",
                            "报价是按次上门，E1 的服务按月购买：没有“每月几次”的依据，不能把单次价格换成月套餐",
                        ),
                        _SERVICE_NOT_COMPILED,
                        Sub(
                            "catalog.billing_unit_unsupported.next",
                            "提供按月套餐的报价，或登记每月上门次数的资料与换算",
                        ),
                    )
                )
            elif p.billing != want:
                out.append(
                    _Problem(
                        "billing_kind_mismatch",
                        f"{where}.price.billing",
                        Sub(
                            "catalog.billing_kind_mismatch.message",
                            "{kind}的计费应是 {want}，得到 {billing}",
                            (
                                (
                                    "kind",
                                    Ent(
                                        "candidate_kind",
                                        "device" if c.kind == "device" else "service",
                                        "设备" if c.kind == "device" else "服务",
                                    ),
                                ),
                                ("want", Code(want)),
                                ("billing", _plain(p.billing)),
                            ),
                        ),
                        _NOT_COMPILED,
                        Sub("catalog.billing_kind_mismatch.next", "核对计费周期"),
                    )
                )
            if p.per_unit and p.per_unit not in _PER_UNIT_KIND:
                out.append(
                    _Problem(
                        "price_unit_unregistered",
                        f"{where}.price.per_unit",
                        Sub(
                            "catalog.price_unit_unregistered.message",
                            "计价单位“{per_unit}”没有登记它对应的计价对象（套 / 台 / 服务月 / 次）：不能核对报价来源是不是按同一对象计价",
                            (("per_unit", _plain(p.per_unit)),),
                        ),
                        _NOT_COMPILED_INCOMPLETE,
                        Sub(
                            "catalog.price_unit_unregistered.next",
                            "改用已登记的计价单位，或先登记这个计价单位对应的计价对象",
                        ),
                    )
                )
            amount = p.amount_text
            value = _number(amount)
            if value is None or value < 0:
                out.append(
                    _Problem(
                        "price_not_a_number",
                        f"{where}.price.amount_text",
                        Sub("catalog.price_not_a_number.message", "报价不是非负数"),
                        Sub("catalog.price_not_a_number.impact", "不能编入"),
                        Sub("catalog.price_not_a_number.next", "核对报价"),
                    )
                )
            elif _finer_than(value, mb.minor_unit_digits):
                out.append(
                    _Problem(
                        "price_too_precise",
                        f"{where}.price.amount_text",
                        Sub(
                            "catalog.price_too_precise.message",
                            "报价 {amount} 不能精确表示为最小记账单位（{digits} 位小数）：不四舍五入",
                            (
                                ("amount", _plain(amount)),
                                ("digits", Num(f"{mb.minor_unit_digits}")),
                            ),
                        ),
                        _NOT_COMPILED_OTHERS_OK,
                        Sub(
                            "catalog.price_too_precise.next",
                            "核对报价，写成最多两位小数的金额",
                        ),
                    )
                )
            else:
                out += _source_problems(pack, c, "price", p.facts, facts, amount)
    # 寿命
    if c.kind == "device":
        life = c.lifetime
        if life.status != "known" or life.years_text is None:
            out.append(
                _Problem(
                    "lifetime_unknown",
                    f"{where}.lifetime",
                    Sub(
                        "catalog.lifetime_unknown.message",
                        "设备能用几年的资料缺失：E1 可执行的耐用品要求明确的 1–30 整数年，不按“常见寿命”补值",
                    ),
                    Sub(
                        "catalog.lifetime_unknown.impact",
                        "这项设备不能编入目录；它的供给状态照实保留（有卖不等于确定买不到）",
                    ),
                    Sub(
                        "catalog.lifetime_unknown.next",
                        "补充寿命资料，或登记全组共用的明确假设",
                    ),
                )
            )
        else:
            y = _number(life.years_text)
            life_ok = True
            if y is None or y.denominator != 1:
                life_ok = False
                out.append(
                    _Problem(
                        "lifetime_not_integer",
                        f"{where}.lifetime.years_text",
                        Sub(
                            "catalog.lifetime_not_integer.message",
                            "寿命 {years} 年不是整数：E1 只接受整数年，不静默取整",
                            (("years", _plain(life.years_text)),),
                        ),
                        _DEVICE_NOT_COMPILED,
                        Sub(
                            "catalog.lifetime_not_integer.next",
                            "提供整数年的寿命资料，或登记明确的换算规则",
                        ),
                    )
                )
            elif not 1 <= y <= MAX_DURABLE_YEARS:
                life_ok = False
                out.append(
                    _Problem(
                        "lifetime_out_of_range",
                        f"{where}.lifetime.years_text",
                        Sub(
                            "catalog.lifetime_out_of_range.message",
                            "寿命 {years} 年不在 1–{max} 年之间",
                            (
                                ("years", _plain(life.years_text)),
                                ("max", Num(f"{MAX_DURABLE_YEARS}")),
                            ),
                        ),
                        _DEVICE_NOT_COMPILED,
                        Sub("catalog.lifetime_out_of_range.next", "核对单位（月写成年了？）"),
                    )
                )
            if life.effective_from is None:
                life_ok = False
                out.append(
                    _Problem(
                        "effective_from_unknown",
                        f"{where}.lifetime.effective_from",
                        Sub(
                            "catalog.effective_from_unknown.message",
                            "没有写明从何时开始起作用（当月 / 下一年）",
                        ),
                        _DEVICE_NOT_COMPILED,
                        Sub("catalog.effective_from_unknown.next", "写明生效时点"),
                    )
                )
            if life_ok:
                out += _source_problems(pack, c, "lifetime", life.facts, facts, life.years_text)
    # 效果
    e = c.effect
    if e.status != "known" or not e.residuals:
        out.append(
            _Problem(
                "effect_unknown",
                f"{where}.effect",
                Sub(
                    "catalog.effect_unknown.message",
                    "减负效果（本人每月还要做多少小时）资料缺失：不能删掉替代字段把它伪装成“确定没有效果”",
                ),
                _NOT_COMPILED,
                Sub(
                    "catalog.effect_unknown.next",
                    "补充减负资料，或登记全组共用的明确效果假设",
                ),
            )
        )
    else:
        effect_ok = True
        seen: dict[str, list[str]] = {}
        for r in e.residuals:
            seen.setdefault(r.task, []).append(r.residual_hours_per_month)
        for task, values in seen.items():
            if len(values) > 1:
                effect_ok = False
                out.append(
                    _Problem(
                        "residual_task_duplicated",
                        f"{where}.effect.{task}",
                        Sub(
                            "catalog.residual_task_duplicated.message",
                            "家务 {task} 在减负效果里出现了 {times} 次（{values} 小时/月）：同一项家务只能有一个残余小时，"
                            "系统不替你选，也不取最小值（内容完全相同的重复也拒绝）",
                            (
                                ("task", _plain(task)),
                                ("times", Num(f"{len(values)}")),
                                ("values", Join(tuple(_plain(v) for v in values))),
                            ),
                        ),
                        _NOT_COMPILED_OTHERS_OK,
                        Sub(
                            "catalog.residual_task_duplicated.next",
                            "每项家务只保留一条残余小时",
                        ),
                    )
                )
        for r in e.residuals:
            if len(seen[r.task]) > 1:
                continue
            base = tasks.get(r.task)
            v = _number(r.residual_hours_per_month)
            if base is None:
                effect_ok = False
                out.append(
                    _Problem(
                        "task_not_in_common_definition",
                        f"{where}.effect.{r.task}",
                        Sub(
                            "catalog.task_not_in_common_definition.message",
                            "家务 {task} 不在全组共用的任务定义里",
                            (("task", _plain(r.task)),),
                        ),
                        Sub(
                            "catalog.task_not_in_common_definition.impact",
                            "不能按城市另设任务或基准",
                        ),
                        Sub(
                            "catalog.task_not_in_common_definition.next",
                            "只用协议声明的代表性杂务",
                        ),
                    )
                )
            elif v is None or v < 0 or v > base or _finer_than(v, 2):
                effect_ok = False
                out.append(
                    _Problem(
                        "residual_invalid",
                        f"{where}.effect.{r.task}",
                        Sub(
                            "catalog.residual_invalid.message",
                            "残余 {hours} 小时/月不在 0 到基准 {base} 之间（最多两位小数）",
                            (
                                ("hours", _plain(r.residual_hours_per_month)),
                                ("base", Num(f"{base}")),
                            ),
                        ),
                        _NOT_COMPILED,
                        Sub(
                            "catalog.residual_invalid.next",
                            "核对残余小时：它是本人处理整项家务后仍需的月时间",
                        ),
                    )
                )
        if effect_ok:
            out += _source_problems(pack, c, "effect", e.facts, facts)
    # 服务的本人参与
    if c.kind == "service":
        part = c.participation
        if part.status != "known" or part.hours_text is None or part.burden_per_hour_text is None:
            out.append(
                _Problem(
                    "participation_unknown",
                    f"{where}.participation",
                    Sub(
                        "catalog.participation_unknown.message",
                        "服务需要本人每月参与多少小时的资料缺失（不当 0）",
                    ),
                    _SERVICE_NOT_COMPILED,
                    Sub("catalog.participation_unknown.next", "补充本人参与时间与负担"),
                )
            )
        else:
            bad = [
                Sub(
                    "catalog.participation_invalid.item",
                    "{name} “{text}”",
                    (("name", name), ("text", _plain(text))),
                )
                for name, text in (
                    (Sub("catalog.participation.hours", "每月小时"), part.hours_text),
                    (Sub("catalog.participation.burden", "每小时负担"), part.burden_per_hour_text),
                )
                if (n := _number(text)) is None or n < 0 or _finer_than(n, 2)
            ]
            if bad:
                out.append(
                    _Problem(
                        "participation_invalid",
                        f"{where}.participation",
                        Sub(
                            "catalog.participation_invalid.message",
                            "本人参与资料不是不小于 0、最多两位小数的数：{bad}",
                            (("bad", Join(tuple(bad))),),
                        ),
                        Sub(
                            "catalog.participation_invalid.impact",
                            "这项服务不能编入目录（其他候选照常编译）",
                        ),
                        Sub(
                            "catalog.participation_invalid.next",
                            "核对本人每月参与小时与每小时负担",
                        ),
                    )
                )
            else:
                out += _source_problems(pack, c, "participation", part.facts, facts)
    return out, price_unknown


def _weakest(kinds: list[str]) -> str:
    assert kinds, "编入的机会必须至少用到一条来源事实（来源检查已经保证）"
    return min(kinds, key=_KIND_ORDER.index)


def _used_fields(c: OpportunityCandidate, availability: str) -> dict[str, tuple[str, ...]]:
    """编出来的 E1 机会实际用到的字段 → 它们的来源事实（资料未知时报价不进 E1，也不进来源）。"""
    fields: dict[str, tuple[str, ...]] = {}
    if availability == "available":
        fields["price"] = c.price.facts
    if c.kind == "device":
        fields["lifetime"] = c.lifetime.facts
    fields["effect"] = c.effect.facts
    if c.kind == "service":
        fields["participation"] = c.participation.facts
    return fields


def _e1_opportunity(
    pack: DataPack, c: OpportunityCandidate, availability: str, facts: dict[str, SourceFact]
) -> tuple[dict[str, Any], dict[str, tuple[str, ...]], str]:
    """一项候选的 E1 机会（YAML 结构）、逐字段来源映射与最弱来源性质。"""
    b = pack.body
    field_sources = _used_fields(c, availability)
    used = [facts[f] for refs in field_sources.values() for f in refs]
    weakest = _weakest([f.kind for f in used])
    # 地理粒度取资料包为这次调查声明的那一个（与比较组一致，E1 的比较组检查逐项核对）；具体地点留在来源事实里
    source: dict[str, Any] = {
        "kind": weakest,
        "observed_on": b.observed_on.isoformat(),
        "geography_scope": b.catalog_scope.geography_scope,
        "mapping_version": COMPILER_VERSION,
    }
    if weakest == "synthetic":
        notes = sorted({f.assumption for f in used if f.kind == "synthetic" and f.assumption})
        assert notes, "合成事实必须写假设说明（SourceFact 的校验已经保证）"
        source["synthetic_note"] = (
            "E3-A 编译：逐字段来源见目录编译记录；含合成设定，整条按最弱来源标合成——"
            + "；".join(notes)
        )[:400]
    elif weakest in ("estimated", "derived"):
        source["inputs"] = [f.fact_id for f in used]
        source["rule"] = (
            "；".join(sorted({f.conversion for f in used if f.conversion})) or "见目录编译记录"
        )[:400]
    else:
        first = used[0]
        source["provenance"] = {
            "source_url": first.locator,
            "source_table": first.fact_id,
            "source_cell": first.field,
            "retrieved_date": b.observed_on.isoformat(),
        }
    substitutes = [
        {"task": r.task, "residual_hours_per_month": float(Decimal(r.residual_hours_per_month))}
        for r in c.effect.residuals
    ]
    residual_text = "、".join(
        f"{r.task} {r.residual_hours_per_month} 小时" for r in c.effect.residuals
    )
    opp: dict[str, Any] = {
        "opportunity_id": c.target_opportunity_id,
        "category": "durable" if c.kind == "device" else "service",
        "label": c.label,
        "unit": c.price.per_unit,
        "quality": c.quality,
        "availability": availability,
        "source": source,
    }
    if availability == "available":
        opp["price"] = {
            "amount": {
                "amount": float(Decimal(c.price.amount_text)),  # type: ignore[arg-type]
                "numeraire": f"{b.money_basis.currency}_{b.money_basis.base_year}_LOCAL",
            },
            "billing": c.price.billing,
            "per_unit": c.price.per_unit,
        }
    if c.kind == "device":
        opp["duration"] = {
            "kind": "durable_good",
            "lasts_years": int(Fraction(Decimal(c.lifetime.years_text))),  # type: ignore[arg-type]
            "effective_from": c.lifetime.effective_from,
        }
        opp["resource"] = {
            "basis": "passive",
            "substitutes": substitutes,
            "note": f"持有本身不占时间；在服务期内本人每月残余：{residual_text}"[:280],
        }
    else:
        opp["duration"] = {"kind": "ongoing_service"}
        opp["resource"] = {
            "basis": "per_service_month",
            "hours": float(Decimal(c.participation.hours_text)),  # type: ignore[arg-type]
            "burden_per_hour": float(Decimal(c.participation.burden_per_hour_text)),  # type: ignore[arg-type]
            "quantity_scales": False,
            "substitutes": substitutes,
            "note": f"只在实际付了钱的月份起作用；那些月份本人每月残余：{residual_text}"[:280],
        }
    LifeOpportunity.model_validate(
        opp
    )  # 编出来的必须是合法的 E1 机会；这里再失败是程序错误，原样抛出
    return opp, field_sources, weakest


#: 编成资料未知的原因 → 给人看的说明。
_UNKNOWN_NOTE = {
    "price_unknown": Sub(
        "catalog.unknown.price_unknown",
        "有卖但报价未知：编成“资料未知”，分身看得到但不能购买；不当免费",
    ),
    "supply_unknown": Sub(
        "catalog.unknown.supply_unknown",
        "不知道这座城有没有这项方案（供给未知）：编成“资料未知”，分身看得到但不能购买",
    ),
    "supply_and_price_unknown": Sub(
        "catalog.unknown.supply_and_price_unknown",
        "供给与报价都未知：编成“资料未知”，分身看得到但不能购买；不当免费",
    ),
}
#: 来源事实里给人看的文字字段：含中文时按 ``source_fact/<事实编号>/<字段>`` 绑定（编号、纯数值与网址不含中文，不用翻译）
_FACT_TEXT_FIELDS = (
    "raw_value",
    "unit",
    "period",
    "geography",
    "locator",
    "conversion",
    "assumption",
    "note",
)


def _fact_display(f: SourceFact) -> SourceFact:
    """同一条来源事实（内容不变），给人看的文字按事实编号绑定成实体。"""
    update = {
        name: label("source_fact", f.fact_id, value, name)
        for name in _FACT_TEXT_FIELDS
        if isinstance(value := getattr(f, name), str) and has_cjk(value)
    }
    return f.model_copy(update=update) if update else f


def compile_catalog(
    pack: DataPack,
    base_protocol: dict[str, Any],
    *,
    sample: SampleLabel | None = None,
) -> tuple[CatalogCompilation, dict[str, list[dict[str, Any]]]]:
    """编译资料包里的全部候选。返回（编译记录，每座城编入的 E1 机会）。调查范围之外的候选不编入（覆盖报告里记为排除）。"""
    b = pack.body
    scope = b.catalog_scope
    tasks = {
        d.task: Fraction(d.baseline_hours_per_month)
        for d in _task_definitions(base_protocol, scope.tasks)
    }
    facts = {f.fact_id: f for f in b.facts}
    compiled: list[CompiledOpportunity] = []
    uncompiled: list[UncompiledCandidate] = []
    by_city: dict[str, list[dict[str, Any]]] = {c.city_ref: [] for c in b.cities}
    for c in b.candidates:
        if not set(c.tasks) <= set(scope.tasks):
            continue  # 范围之外：覆盖报告记为排除，不编入
        if c.market_status == "not_offered":
            # 已证实不可得：确定的调查结果，不需要再核对报价、寿命或效果；不编成可购买方案
            uncompiled.append(
                UncompiledCandidate(
                    candidate_id=c.candidate_id,
                    city_ref=c.city_ref,
                    market_status=c.market_status,
                    reasons=(
                        _Problem(
                            "not_offered",
                            f"candidates.{c.candidate_id}.market_status",
                            Sub("catalog.not_offered.message", "资料显示这座城确定没有这项方案"),
                            Sub(
                                "catalog.not_offered.impact",
                                "这是已确定的调查结果；它不是可购买的方案，所以不编入目录",
                            ),
                            Sub("catalog.not_offered.next", "无需补资料；资料变了再更新"),
                            severity="info",
                        ).issue(),
                    ),
                )
            )
            continue
        problems, price_unknown = _candidate_problems(pack, c, tasks, facts)
        if problems:
            uncompiled.append(
                UncompiledCandidate(
                    candidate_id=c.candidate_id,
                    city_ref=c.city_ref,
                    market_status=c.market_status,
                    reasons=tuple(p.issue() for p in problems),
                )
            )
            continue
        supply_unknown = c.market_status == "unknown"
        unknown_reason = (
            "supply_and_price_unknown"
            if supply_unknown and price_unknown
            else "supply_unknown"
            if supply_unknown
            else "price_unknown"
            if price_unknown
            else None
        )
        availability = "available" if unknown_reason is None else "unknown"
        opp, field_sources, weakest = _e1_opportunity(pack, c, availability, facts)
        by_city[c.city_ref].append(opp)
        compiled.append(
            CompiledOpportunity(
                candidate_id=c.candidate_id,
                opportunity_id=c.target_opportunity_id,
                city_ref=c.city_ref,
                availability=availability,  # type: ignore[arg-type]
                unknown_reason=unknown_reason,  # type: ignore[arg-type]
                field_sources=field_sources,
                weakest_source_kind=weakest,  # type: ignore[arg-type]
                note_zh=_say(_UNKNOWN_NOTE[unknown_reason])
                if unknown_reason is not None
                else t(
                    "catalog.compiled.available",
                    "资料齐全：编成可购买的机会；E1 照旧按最小残余、寿命与实际服务月份结算",
                ),
            )
        )
    fields = dict(
        meta=pack.meta(sample),
        compilation_id=f"catalog.{b.pack_id}",
        compiler_version=COMPILER_VERSION,
        scope_zh=label("preflight_text", "catalog_scope", scope.declared_scope),
        cities=tuple(c.city_ref for c in b.cities),
        tasks=_task_definitions(base_protocol, scope.tasks),
        compiled=tuple(compiled),
        uncompiled=tuple(uncompiled),
        facts=tuple(_fact_display(f) for f in b.facts),
    )
    unsealed = CatalogCompilation.model_construct(**fields, compilation_fingerprint="0" * 64)
    fp = content_fingerprint(identity_content(unsealed, "compilation_fingerprint"))
    return CatalogCompilation(**fields, compilation_fingerprint=fp), by_city


def _reasons_text(
    pack: DataPack,
    c: OpportunityCandidate,
    unc: UncompiledCandidate,
    tasks: dict[str, Fraction],
    facts: dict[str, SourceFact],
) -> str:
    """覆盖报告里一项有缺口、没有编入的候选的原因（代码与原因逐条）。原因按编译时的同一套规则重新生成成有 key 的模板，与编译记录里
    的文字逐条核对：完全相同才用（可以给英文）；对不上（编译记录不是这份资料包编出来的）就照用记录里的原文（中文不变，英文缺失）。"""
    problems, _ = _candidate_problems(pack, c, tasks, facts)
    same = [(p.code, _say(p.message)) for p in problems] == [
        (i.code, i.message_zh) for i in unc.reasons
    ]
    messages: list[Sub | Raw] = (
        [p.message for p in problems] if same else [Raw(i.message_zh) for i in unc.reasons]
    )
    reasons = Join(
        tuple(
            Sub(
                "catalog.inventory.reason",
                "{code}（{message}）",
                (("code", Code(i.code)), ("message", m)),
            )
            for i, m in zip(unc.reasons, messages, strict=True)
        ),
        zh_sep="；",
        en_sep="; ",
    )
    if c.market_status == "unknown":
        return t(
            "catalog.inventory.incomplete_supply_unknown",
            "供给未知，且资料有缺口，没有编入：{reasons}",
            reasons=reasons,
        )
    return t("catalog.inventory.incomplete", "资料有缺口，没有编入：{reasons}", reasons=reasons)


def _item(
    pack: DataPack,
    c: OpportunityCandidate,
    scope_tasks: tuple[str, ...],
    compiled: dict[str, CompiledOpportunity],
    uncompiled: dict[str, UncompiledCandidate],
    tasks: dict[str, Fraction],
    facts: dict[str, SourceFact],
) -> InventoryItem:
    """一个候选在覆盖报告里的三种状态与分母归属。"""
    supply = c.market_status
    if not set(c.tasks) <= set(scope_tasks):
        return InventoryItem(
            candidate_id=c.candidate_id,
            outcome="excluded",
            supply_status=supply,
            data_status="not_assessed",
            compile_status="not_compiled",
            reason_zh=t(
                "catalog.inventory.excluded",
                "不在声明的调查范围（任务 {tasks} 不属于代表性杂务组合 {scope}）",
                tasks=_plain(list(c.tasks)),
                scope=_plain(list(scope_tasks)),
            ),
        )
    if c.candidate_id in compiled:
        rec = compiled[c.candidate_id]
        if rec.availability == "available":
            return InventoryItem(
                candidate_id=c.candidate_id,
                outcome="resolved",
                supply_status=supply,
                data_status="complete",
                compile_status="compiled_available",
                reason_zh=t("catalog.inventory.compiled_available", "资料齐全，已编入、可购买"),
            )
        return InventoryItem(
            candidate_id=c.candidate_id,
            outcome="unresolved",
            supply_status=supply,
            data_status="complete" if rec.unknown_reason == "supply_unknown" else "price_unknown",
            compile_status="compiled_unknown",
            reason_zh=_say(_UNKNOWN_NOTE[rec.unknown_reason or "price_unknown"]),
        )
    unc = uncompiled[c.candidate_id]
    if supply == "not_offered":
        return InventoryItem(
            candidate_id=c.candidate_id,
            outcome="resolved",
            supply_status="not_offered",
            data_status="not_needed",
            compile_status="not_compiled",
            reason_zh=t(
                "catalog.inventory.not_offered",
                "已证实这座城没有这项方案：调查结果已确定，但它不是可购买的方案",
            ),
        )
    return InventoryItem(
        candidate_id=c.candidate_id,
        outcome="unresolved",
        supply_status=supply,
        data_status="incomplete",
        compile_status="not_compiled",
        reason_zh=_reasons_text(pack, c, unc, tasks, facts)[:600],
    )


def inventory_coverage(
    pack: DataPack, compilation: CatalogCompilation, *, sample: SampleLabel | None = None
) -> OptionInventoryCoverage:
    """选项目录覆盖：声明范围内每座城调查了哪些候选；供给、资料与编译状态分开记；分母是调查过的全部候选（过滤前）。"""
    b = pack.body
    scope = b.catalog_scope
    compiled = {c.candidate_id: c for c in compilation.compiled}
    uncompiled = {u.candidate_id: u for u in compilation.uncompiled}
    tasks = {d.task: Fraction(d.baseline_hours_per_month) for d in compilation.tasks}
    facts = {f.fact_id: f for f in b.facts}
    cities: list[CityInventory] = []
    for city in b.cities:
        items = [
            _item(pack, c, scope.tasks, compiled, uncompiled, tasks, facts)
            for c in b.candidates
            if c.city_ref == city.city_ref
        ]
        n = len(items)
        resolved = sum(i.outcome == "resolved" for i in items)
        unresolved = sum(i.outcome == "unresolved" for i in items)
        excluded = sum(i.outcome == "excluded" for i in items)
        status = (
            "not_assessed"
            if n == 0
            else ("partial" if unresolved else "complete_for_declared_scope")
        )
        cities.append(
            CityInventory(
                city_ref=city.city_ref,
                status=status,  # type: ignore[arg-type]
                surveyed=n,
                resolved=resolved,
                unresolved=unresolved,
                excluded=excluded,
                items=tuple(items),
            )
        )
    return OptionInventoryCoverage(
        meta=pack.meta(sample),
        coverage_id=f"inventory.{b.pack_id}",
        scope_version=scope.scope_version,
        declared_scope_zh=label("preflight_text", "catalog_scope", scope.declared_scope),
        categories=scope.categories,
        cities=tuple(cities),
        not_exhaustive_zh=t(
            "catalog.inventory.not_exhaustive",
            "这只说明声明范围内调查过的候选资料齐不齐，不保证穷尽整个城市市场；没有记录不等于城市没有。供给状态（有卖 / 确定没有 / "
            "不知道）、资料状态与编译状态分开记：确定没有是已确定的调查结果，但不是可购买的方案。它与实际轨迹能否评分、分身的 "
            "C / E / M 是不同的判断：目录缺口不改写已发生轨迹的评分",
        ),
    )


def compiled_observed_on(pack: DataPack) -> _dt.date:
    return pack.body.observed_on
