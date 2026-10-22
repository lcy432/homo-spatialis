"""A 映射：先设计有覆盖的候选组合，再按每座城的资料校准。

**住房（``housing_coverage_grid@1``）**：每座城都用同一张网格——卧室数（开间 / 一卧 / 两卧 / 三卧及以上）× 楼型（10 户以上 / 2–9 户 /
独栋或联排）× 通勤层（较短 / 中等 / 较长，都会区内三分位）× 价位（这一格租金分布三等分区间的中点：1/6、1/2、5/6 分位）。

* 网格是**候选设计**，保证每座城都有同样类型的选择可比较；它不是“现实里的平均城市”，网格里每一格的数量也不代表市场份额。
* 价格与通勤由这座城的 B 条件表校准（``mapping_b``），按层级回退并记录用了哪一层；连回退之后都没有支持的组合不生成，计入报告。
* 每个候选的目标权重 = 所在格（通勤层 × 卧室 × 楼型）的新租市场代理权重 ÷ 3；格里多生成几个候选，这一格的总权重不变。权重为 0
  的组合（例如这座城的这一层没有观测到这种楼型的新租房）照样保留在网格里供比较，但不会被抽中。
* 价位取三等分区间的中点，所以三个价位各代表这一格三分之一的目标人群——不是“便宜 / 中等 / 贵”三个随意的价钱。

**食品（``food_plan_from_regional_prices@1``）**：见 ``food`` 模块；这里只放网格结构。
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from common.intake_contracts import MethodSpec
from product.realdata.mapping_b import PRICE_POSITIONS, RentCell, RentModel

HOUSING_GRID = MethodSpec(
    method_id="housing_coverage_grid",
    version="1",
    family="mapping_a",
    summary_zh=(
        "每座城同一张住房网格：卧室数 × 楼型 × 通勤层 × 价位（三等分区间中点）；价格与通勤由本城 B 条件表校准、层级回退；目标权重 = "
        "所在格新租市场代理权重 ÷ 3，与候选生成密度分开。"
    ),
    inputs=("housing_rent_conditional@1", "commute_by_residence_puma@1"),
    known_biases_zh=(
        "网格是覆盖设计，不是现实分布；网格格数不代表市场份额",
        "回退到较粗层级时，这一格的价格反映的是更大范围的分布",
    ),
)


@dataclass(frozen=True)
class GridPoint:
    tier: str
    bed: str
    struct: str
    position: str
    rent: str | None
    cell: RentCell | None
    tried: tuple[str, ...]
    weight: Fraction

    @property
    def supported(self) -> bool:
        return self.cell is not None


def housing_grid(model: RentModel, cfg: dict) -> list[GridPoint]:
    if tuple(cfg["price_positions"]) != PRICE_POSITIONS:
        raise ValueError(
            f"配置的价位 {cfg['price_positions']} 与方法 {HOUSING_GRID.ref} 定义的 {PRICE_POSITIONS} 不同：改价位要升方法版本"
        )
    points: list[GridPoint] = []
    for tier in cfg["location_tiers"]:
        for bed in cfg["bedrooms"]:
            for struct in cfg["structures"]:
                own = model.get("tier_bed_struct", (tier, bed, struct))
                mass = Fraction(own.weight_sum) if own is not None else Fraction(0)
                cell, tried = model.resolve(
                    [
                        ("tier_bed_struct", (tier, bed, struct)),
                        ("tier_bed", (tier, bed)),
                        ("metro_bed", (bed,)),
                        ("metro_bed_stock", (bed,)),
                    ]
                )
                for pos in cfg["price_positions"]:
                    points.append(
                        GridPoint(
                            tier=tier,
                            bed=bed,
                            struct=struct,
                            position=pos,
                            rent=cell.quantiles.get(pos) if cell else None,
                            cell=cell,
                            tried=tuple(tried),
                            weight=mass / len(cfg["price_positions"]),
                        )
                    )
    return points
