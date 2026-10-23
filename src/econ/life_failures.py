"""持续物品的随机故障（DECISIONS/0042）。纯函数：无 I/O、无 LLM。

两类故障规则（``FailureSpec``）：``none_within_horizon``（模拟期内不随机坏，只按寿命到期）与 ``annual_hazard``（每年坏的
概率 p，是登记的类别假设，不是保修期）。月度风险 h = 1 − (1 − p)^(1/12)，十进制 30 位计算后取到 15 位小数（这是登记的数值
约定）；每件、每个服务月月末抽一次，u < h 就坏：记在这个月，下个月起不能用，剩余寿命作废（不再另扣一次）。

**随机数与疾病同一套做法**（``econ.life_health.uniform_of``）：键按规范 JSON 写出取 SHA-256 前 64 位 ÷ 2^64。键里有流版本、
配对身份、种子、人、重复分身序号、机会编号、取得年月、第几件、绝对月份与事件类别；没有城市、分身编号、遍历顺序与重试次数。
年初的无事件试算不抽签（与疾病一样），所以规划器看不到将来会不会坏。
"""

from __future__ import annotations

from decimal import Decimal, localcontext
from fractions import Fraction

from common.life_contracts import MONTHS_PER_YEAR, DeviceFailureSource, FailureSpec
from econ.life_health import uniform_of

FAILURE_CATEGORY = "device_failure"


def monthly_hazard(annual_probability: float) -> Fraction:
    """年概率 → 月度风险 h = 1 − (1 − p)^(1/12)，取到 15 位小数的精确分数。"""
    with localcontext() as ctx:
        ctx.prec = 30
        p = Decimal(repr(float(annual_probability)))
        h = Decimal(1) - (Decimal(1) - p) ** (Decimal(1) / Decimal(MONTHS_PER_YEAR))
        return Fraction(h.quantize(Decimal("1e-15")))


def failure_key(
    source: DeviceFailureSource,
    *,
    person_id: str,
    repeat_index: int,
    opportunity_id: str,
    acquired: tuple[int, int],
    unit: int,
    year: int,
    month: int,
) -> dict[str, object]:
    return {
        "stream": source.stream_version,
        "pairing_id": source.pairing_id,
        "seed": source.seed,
        "person_id": person_id,
        "repeat_index": repeat_index,
        "opportunity_id": opportunity_id,
        "acquired": list(acquired),
        "unit": unit,
        "absolute_month": (year - 1) * MONTHS_PER_YEAR + month,
        "category": FAILURE_CATEGORY,
    }


def draw_failure(
    spec: FailureSpec, source: DeviceFailureSource, **key: object
) -> tuple[bool, Fraction, Fraction, str]:
    """这一件这个月月末坏不坏：（坏了没有，u，h，键的 SHA-256）。模拟期内不随机坏的规则不抽签。"""
    if spec.kind != "annual_hazard":
        return False, Fraction(1), Fraction(0), ""
    assert spec.annual_probability is not None
    h = monthly_hazard(spec.annual_probability)
    u, digest = uniform_of(failure_key(source, **key))  # type: ignore[arg-type]
    return u < h, u, h, digest
