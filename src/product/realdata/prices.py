"""时期换算：只在有明确依据时生成 ``USD_2024_LOCAL`` 估计值。

``USD_2024_LOCAL`` 是“当地价格水平、2024 年不变价”。来源金额的时期不是 2024 年时（例如 2023–24 学年学费），用全美 CPI-U
（城市平均、所有项目、未季节调整）的比值换到 2024 年年平均价格：``金额 × CPI(2024 年平均) ÷ CPI(来源时期)``。这是**估计**：
它假设这项价格与全美所有项目同步变化，也不区分地方通胀；原始金额与口径原样保留。时期本身就是 2024 年的（ACS 2024，ADJHSG =
1）只是确定的换算（派生），不是估计。

价格指数（CPI）在这里只用来换时期，**从不**当成付款单价。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from common.intake_contracts import MethodSpec

CPI_SERIES = "CUUR0000SA0"
METHOD = MethodSpec(
    method_id="cpi_u_period_conversion",
    version="1",
    family="conversion",
    summary_zh="来源时期 → 2024 年年平均价格：金额 × CPI-U(2024 年平均) ÷ CPI-U(来源时期)。全美、所有项目、未季节调整。",
    inputs=("bls_cpi_u",),
    known_biases_zh=(
        "假设这项价格与全美所有项目同步变化；地方通胀与具体商品的价格变化不同",
        "跨年度（例如学年）的来源时期取覆盖月份的 CPI 平均",
    ),
)
ADJHSG_METHOD = MethodSpec(
    method_id="acs_adjhsg_constant_dollars",
    version="1",
    family="conversion",
    summary_zh="ACS 住房金额 × ADJHSG（2024 年 1 年期 PUMS 的系数是 1.000000）→ 2024 年不变价；当地价格水平不变（没有空间平减）。",
    inputs=("census_acs1_pums_2024",),
    known_biases_zh=("ACS 金额是受访者报告的月租，四舍五入并在州内顶码",),
)


@dataclass(frozen=True)
class Cpi:
    """CPI-U 月度值（``2024-05`` → 指数）与年平均（``2024-annual``）。"""

    values: dict[str, Decimal]

    def index(self, period: str) -> Decimal:
        if period not in self.values:
            raise KeyError(f"CPI-U 没有时期 {period}")
        return self.values[period]

    def average(self, periods: list[str]) -> Fraction:
        return sum((Fraction(self.index(p)) for p in periods), Fraction(0)) / len(periods)

    def factor(self, from_periods: list[str], to_period: str = "2024-annual") -> Fraction:
        """把 ``from_periods``（一个或多个月份的平均）的金额换到 ``to_period`` 的价格水平的乘数。"""
        return Fraction(self.index(to_period)) / self.average(from_periods)


def parse_cpi(content: bytes) -> Cpi:
    values: dict[str, Decimal] = {}
    for ln in content.decode("utf-8", "replace").splitlines()[1:]:
        p = [x.strip() for x in ln.split("\t")]
        if len(p) < 4 or p[0] != CPI_SERIES:
            continue
        year, period, value = p[1], p[2], p[3]
        try:
            Decimal(value)
        except ArithmeticError:
            continue  # 没有发布的月份（文件里写成 “-”）：不补数
        if period == "M13":
            values[f"{year}-annual"] = Decimal(value)
        elif period.startswith("M"):
            values[f"{year}-{period[1:]}"] = Decimal(value)
    if "2024-annual" not in values:
        raise ValueError("CPI-U 文件里没有 2024 年年平均")
    return Cpi(values)


def months(start: str, end: str) -> list[str]:
    """``2023-07`` 到 ``2024-06``（含两端）的月份列表。"""
    y, m = int(start[:4]), int(start[5:])
    ey, em = int(end[:4]), int(end[5:])
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def to_cents_text(amount: Fraction) -> str:
    """精确分数 → 最小货币单位（分）四舍五入后的十进制文字。估计值本来就有误差，保留到分只是记账精度，不是精度声明。"""
    cents = (amount * 100).__round__()
    return str(Decimal(cents) / 100)
