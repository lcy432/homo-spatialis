"""精确数：十进制解析、规范分数写法与显示舍入（E3-A，DECISIONS/0034）。

* 手工输入先按**精确十进制**解析（``"16.5"`` → 33/2），不经过二进制浮点；也接受规范分数写法（``"50/3"``）。
* 规范写法：约分后的整数（``"30"``）或 ``分子/分母``（``"50/3"``）。计算与比较只用它。
* 显示：四舍五入到给定小数位（半数向远离 0 的方向），只供阅读，**不回流计算**。
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from fractions import Fraction

_DECIMAL = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")
_FRACTION = re.compile(r"^(0|[1-9][0-9]*)/([1-9][0-9]*)$")
#: 金额的千分位写法：首组 1–3 位（不以 0 开头），之后每组恰好 3 位，小数点只能用 “.”（E3-A 收尾 F3）。
_MONEY_GROUPED = re.compile(r"^[1-9][0-9]{0,2}(,[0-9]{3})+(\.[0-9]+)?$")


class ExactParseError(ValueError):
    """数值文字不能精确解析（不是十进制或规范分数、小数位过多、为负）。``code`` 给机器读，``next_action`` 是给用户的纠正说明。"""

    def __init__(self, code: str, message: str, next_action: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.next_action = next_action


def parse_points(text: str, *, max_decimals: int) -> Fraction:
    """把一个点数文字精确解析成分数。十进制最多 ``max_decimals`` 位小数；分数必须已是规范写法（约分）。"""
    s = text.strip()
    if _DECIMAL.match(s):
        decimals = len(s.split(".")[1]) if "." in s else 0
        if decimals > max_decimals:
            raise ExactParseError(
                "points_too_many_decimals",
                f"“{text}”有 {decimals} 位小数，本版手工输入最多 {max_decimals} 位（步长不是测量精度）",
            )
        return Fraction(Decimal(s))
    m = _FRACTION.match(s)
    if m:
        q = Fraction(int(m.group(1)), int(m.group(2)))
        if canonical(q) != s:
            raise ExactParseError(
                "points_fraction_not_canonical", f"分数“{text}”不是规范写法，应写“{canonical(q)}”"
            )
        return q
    raise ExactParseError("points_not_a_number", f"“{text}”不是十进制数或规范分数")


def parse_money_text(text: str, *, minor_digits: int) -> int:
    """把金额文字精确换成整数最小单位（美元 2 位 = 分）。不舍入：精度细于最小单位、负数或不是十进制都报错。

    只接受两种写法（E3-A 收尾 F3）：不分组的非负十进制（``3000.50``），或严格每三位一组的千分位（``3,000.50``）；小数点只能用
    “.”。其他写法一律拒绝、不猜：不再无条件删掉逗号——``3000,50`` 删掉逗号会变成三十万，``3,,000``、``1,23.45`` 也不是合法分组。
    """
    raw = text.strip()
    if _DECIMAL.match(raw):
        s = raw
    elif _MONEY_GROUPED.match(raw):
        s = raw.replace(",", "")
    elif raw.startswith("-"):
        raise ExactParseError(
            "money_negative",
            f"金额“{text}”是负数：开始时可用的钱不能是负数（欠款不在本版支持范围）",
            "填写不小于 0 的金额",
        )
    elif "," in raw:
        raise ExactParseError(
            "money_separator_invalid",
            f"金额“{text}”的逗号用法不对：千分位必须每三位一组（例如 3,000.50），小数点只能用“.”。"
            "系统不猜你的意思——“3000,50”删掉逗号会变成 300,050",
            "按“3000.50”或“3,000.50”这样的写法重新填写",
        )
    else:
        raise ExactParseError(
            "money_not_a_number",
            f"金额“{text}”不是非负十进制数",
            "填写像“3000”或“3,000.50”这样的金额",
        )
    try:
        d = Decimal(s).scaleb(minor_digits)
    except InvalidOperation as exc:  # pragma: no cover - 正则已挡住
        raise ExactParseError("money_not_a_number", f"金额“{text}”不能解析") from exc
    if d != d.to_integral_value():
        raise ExactParseError(
            "money_too_many_decimals",
            f"金额“{text}”的精度细于最小记账单位（{minor_digits} 位小数）",
            f"最多写 {minor_digits} 位小数，不自动四舍五入",
        )
    return int(d)


def canonical(q: Fraction) -> str:
    """规范写法：约分后的整数或 ``分子/分母``。"""
    q = Fraction(q)
    return str(q.numerator) if q.denominator == 1 else f"{q.numerator}/{q.denominator}"


def display(q: Fraction, decimals: int) -> str:
    """显示文字：四舍五入到 ``decimals`` 位（只供阅读，不回流计算）。"""
    d = Decimal(q.numerator) / Decimal(q.denominator)
    quantum = Decimal(1).scaleb(-decimals)
    return str(d.quantize(quantum, rounding=ROUND_HALF_UP))


def money_display(minor: int, digits: int) -> str:
    """整数最小单位的显示文字（千分位、固定小数位）。"""
    q = Decimal(minor).scaleb(-digits)
    return f"{q:,.{digits}f}"
