"""生活评分用到的少量单位约定（E1-3 增补订正，`DECISIONS/0019` 第十二节）。

**不是通用单位换算系统。** 这里只登记本轮评分真正读到的几种数值字段，以及少数"量纲相同、
但不能直接当成同一单位"的近邻（立方英尺、小时），好让冲突能被叫出名字，而不是被当成拼写错误。

三种单位分开：

* **资料单位**：比较组单位表里，某个规格键的数值实际是什么单位（例如 ``storage_liters`` 是升）；
* **读取单位**：评分来源读这个键时期望的单位（``ComponentSource.unit``）；
* **分项单位**：z、b 与锚点 τ 的单位（``ScoringComponent.unit``）。

规则：资料单位必须与读取单位**是同一个规范单位**——评分器从不换算原始资料；资料若是别的单位，
要在资料整理时按可审计规则换算，并把来源标为 ``derived``（写明输入与规则）。读取单位到分项单位
可以有登记过的换算（平方英尺 → 百平方英尺，×0.01），换算系数写进来源表；η 仍是"换算 × 效率"，
这里不改它的含义。

别名是**明确列出**的写法，不做模糊匹配，也不按字段名猜单位。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UnitDef:
    """一个规范单位：量纲、相对本量纲基准单位的倍数、中文名。"""

    unit_id: str
    dimension: str
    scale: float
    label: str


_DEFS: tuple[tuple[UnitDef, tuple[str, ...]], ...] = (
    (UnitDef("sqft", "area", 1.0, "平方英尺"), ("平方英尺", "sqft", "sq ft", "square feet")),
    (UnitDef("hundred_sqft", "area", 100.0, "百平方英尺"), ("百平方英尺", "hundred sqft")),
    (UnitDef("liter", "volume", 1.0, "升"), ("升", "liter", "liters", "litre", "litres", "L")),
    (UnitDef("hundred_liter", "volume", 100.0, "百升"), ("百升", "hundred liters")),
    # 登记它是为了把"立方英尺 vs 升"报成单位冲突，不是为了让评分器替人换算
    (
        UnitDef("cubic_foot", "volume", 28.316846592, "立方英尺"),
        ("立方英尺", "cubic foot", "cubic feet", "ft3"),
    ),
    (
        UnitDef("minute", "duration", 1.0, "分钟"),
        ("分钟", "分钟/工作日往返", "minute", "minutes", "min"),
    ),
    (UnitDef("hour", "duration", 60.0, "小时"), ("小时", "hour", "hours", "h")),
    (
        UnitDef("meal_per_week", "meal_rate", 1.0, "餐/周"),
        ("餐/周", "新鲜餐/周", "新鲜餐/周（折算）", "meals per week"),
    ),
    (
        UnitDef("per_month", "monthly_count", 1.0, "次/月"),
        ("次/月", "次", "每月参与次数", "每月参与次数（折算）", "times per month"),
    ),
    (UnitDef("flag", "boolean", 1.0, "是/否"), ("是/否", "yes/no", "flag")),
    (UnitDef("presence", "boolean", 1.0, "有/无"), ("有/无", "presence")),
    (UnitDef("category", "category", 1.0, "类别"), ("类别", "category")),
    (UnitDef("index", "index", 1.0, "指数"), ("指数", "指数（合成）", "index")),
    # E1-5A 补充（`DECISIONS/0024`）：成长项读的是当月新增的能力进度之和（一项能力从没有到学会是 1）。
    # η_growth 把它换成发展维的合成指数——那是效果系数，不是固定单位换算。
    (
        UnitDef("ability_progress", "ability_progress", 1.0, "能力进度/月"),
        ("能力进度/月", "能力进度", "ability progress per month"),
    ),
    # E1 补充"日常生活便利"（`DECISIONS/0033`）：一个月里本人少做了多少小时家务。与资源账的小时同一单位、按月计；
    # 量纲单独登记（每月小时），不与"分钟/工作日往返"之类的时长混用
    (
        UnitDef("hour_per_month", "monthly_duration", 1.0, "小时/月"),
        ("小时/月", "hours per month"),
    ),
)

UNITS: dict[str, UnitDef] = {d.unit_id: d for d, _ in _DEFS}
#: 写法 → 规范单位。规范标识本身也是合法写法。
ALIASES: dict[str, str] = {}
for _d, _names in _DEFS:
    for _name in (_d.unit_id, *_names):
        if _name in ALIASES and ALIASES[_name] != _d.unit_id:
            raise RuntimeError(f"单位写法 {_name!r} 同时指向两个单位")
        ALIASES[_name] = _d.unit_id

#: 不同量纲之间没有换算；布尔、类别、指数也不参与数值换算之外的任何推断。
NUMERIC_DIMENSIONS: frozenset[str] = frozenset(
    {
        "area",
        "volume",
        "duration",
        "meal_rate",
        "monthly_count",
        "index",
        "ability_progress",
        "monthly_duration",
    }
)


def resolve_unit(text: str) -> UnitDef | None:
    """把一个单位写法解析成规范单位；没登记就返回 None（调用方报错，不猜）。"""
    if not isinstance(text, str):
        return None
    unit_id = ALIASES.get(text.strip())
    return UNITS[unit_id] if unit_id is not None else None


def conversion_factor(src: UnitDef, dst: UnitDef) -> float | None:
    """同量纲时，1 个 src 等于多少个 dst；不同量纲返回 None。"""
    if src.dimension != dst.dimension:
        return None
    return src.scale / dst.scale
