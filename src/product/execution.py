"""正式运行设置（E3C，DECISIONS/0043）：``ExecutionSpec`` 由服务端产生，不是网页能改的表单。

它写下这次模拟的规模（城市、每城分身、年数）、规划器（离线 mock 或真实模型 ``product_planner`` 角色）、有效调用设置、报价版本，
以及规划候选、服务尝试与费用的上限。编译（``product.compile.compile_inputs``）按它写出 E1 运行配置，**之后**才生成运行前检查——
用户确认的那份预检查就绑定了真实的规划器与上限，后台不能换成别的规划器。

运行选项（E3C 收尾）分三层：服务**默认值**、服务**允许范围**（``RunPolicy``，来自服务配置与资料包）、这次运行的**有效设置**
（``RunPolicy.derive`` 校验用户的选择后产生的 ``ExecutionSpec``，创建运行意图时就保存，之后一直用这一份）。上限都按这次的规模与
实际绑定的修订、重试规则算出：规划候选 = 城市数 × 每城分身数 × 年数 × (1 + 每年最多修订次数)；服务尝试 = 规划候选 × (1 + 每份
候选的服务重试次数)。默认（六城 × 4 × 3、每年最多 2 次修订、1 次服务重试）分别是 216 与 432——这两个数是算出来的，代码里不写死。
费用上限是全部城市、全部分身、全部服务尝试共用的这一次的上限；扩大规模不自动扩大预算。模型调用设定与报价从
``configs/life/run_product_flash.yaml`` 读，不在这里另写一套。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from agentlayer.life_llm import llm_identity
from agentlayer.life_mock import PRODUCT_MOCK_IDENTITY
from common.life_planning_contracts import LlmSettings, PriceTable
from common.product_contracts import PlannerAndCost, content_fingerprint
from product.pack import DataPack, load_pack

EXECUTION_VERSION = "e3c-execution-spec-v1"
OptionField = Literal["clones_per_city", "years", "max_cost_cny", "cities"]


class ExecutionSpec(BaseModel):
    """一次模拟的正式运行设置（服务端内部；内容身份进入运行请求、预检查与 E1 运行身份）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["e3c-execution-spec-v1"]
    pack_path: str
    pack_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    cities: tuple[str, ...] = Field(min_length=1)
    repeat_indexes: tuple[int, ...] = Field(min_length=1)
    years: int = Field(ge=1)
    max_revisions_per_year: int = Field(ge=0)
    planner_kind: Literal["mock", "llm"]
    planner_identity: dict[str, Any]
    llm: dict[str, Any] | None = None
    price: dict[str, Any] | None = None
    currency: Literal["CNY", "USD"] | None = None
    max_cost: str | None = None
    max_requests: int | None = None
    global_call_limit: int = Field(ge=1)
    transport_retries: int = Field(ge=0)

    @property
    def fingerprint(self) -> str:
        return content_fingerprint(self.model_dump(mode="json"))

    @property
    def clones(self) -> int:
        return len(self.cities) * len(self.repeat_indexes)

    @property
    def llm_settings(self) -> LlmSettings | None:
        return None if self.llm is None else LlmSettings.model_validate(self.llm)

    def planner_and_cost(self) -> PlannerAndCost:
        """给用户看的规划器与费用说明（预检查与结果同一份）。"""
        if self.planner_kind == "mock":
            return PlannerAndCost(
                planner_kind="mock",
                planner_id=str(self.planner_identity["planner_id"]),
                description_zh=(
                    "可控的确定性离线规划器（mock）：只检验接线、状态传递与信息隔离，不代表真实大语言模型的能力；"
                    "没有网络调用，费用为 0"
                ),
                call_limit=self.global_call_limit,
                paid=False,
                max_cost=None,
            )
        settings = self.llm_settings
        assert settings is not None
        return PlannerAndCost(
            planner_kind="llm",
            planner_id=str(self.planner_identity["planner_id"]),
            description_zh=(
                f"真实模型规划器：{settings.model}（{settings.model_role} 角色，滚动型号），每个分身每年一次正常规划、"
                f"最多 {self.max_revisions_per_year} 次修订，每份候选最多再试 {self.transport_retries} 次服务请求；"
                "只收到给分身看的安全投影，计划仍由环境检查与结算"
            ),
            call_limit=self.global_call_limit,
            paid=True,
            max_cost=f"{self.max_cost} {self.currency}（应用侧按报价 {self.price_version} 估算的上限，不是供应商账单上限）",
        )

    @property
    def price_version(self) -> str | None:
        return None if self.price is None else str(self.price.get("price_version"))

    def run_planner_block(self) -> dict[str, Any]:
        """E1 运行配置的 ``planner`` 一节。"""
        if self.planner_kind == "mock":
            return {"identity": dict(self.planner_identity), "scripts": []}
        return {"identity": dict(self.planner_identity), "llm": dict(self.llm or {})}

    def paid_budget_block(self) -> dict[str, Any] | None:
        """E1 运行配置的 ``paid_budget``：每次运行在自己的工作目录里新建一本账（``budget/``），不复用任何历史账本。"""
        if self.planner_kind == "mock":
            return None
        block: dict[str, Any] = {
            "ledger_dir": "budget",
            "max_requests": self.max_requests,
            "price": dict(self.price or {}),
        }
        block[f"max_cost_{(self.currency or 'CNY').lower()}"] = float(self.max_cost or 0)
        return block


# ---------------------------------------------------------------------- 资料包支持的年数

_PROTOCOL_YEARS: dict[tuple[str, int, int], int] = {}


def protocol_years(pack: DataPack, root: Path) -> int:
    """资料包基础样例的协议年数（``protocol.simulation_years``）。``root`` 是资料包里相对路径的根（仓库或工作目录）。"""
    path = (Path(root) / pack.body.e1_base.life_input).resolve()
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key not in _PROTOCOL_YEARS:
        text = path.read_text(encoding="utf-8")
        raw = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
        _PROTOCOL_YEARS[key] = int(raw["protocol"].get("simulation_years", 3))
    return _PROTOCOL_YEARS[key]


def pack_years(pack: DataPack, root: Path) -> tuple[int, ...]:
    """资料包支持（配套验证过）的年数：资料包在 ``run.years_supported`` 里显式声明的；没有声明就只有协议的那一个年数。
    不为了参数化生成更多年份的菜单——资料包没有声明的年数不开放。"""
    declared = pack.body.run.years_supported
    return tuple(sorted(declared)) if declared else (protocol_years(pack, root),)


# ---------------------------------------------------------------------- 有效设置


def build_execution_spec(
    *,
    pack: DataPack,
    pack_path: str,
    planner: Literal["mock", "llm"],
    cities: tuple[str, ...] = (),
    repeat_indexes: tuple[int, ...] | None = None,
    years: int | None = None,
    flash_config: Path | None = None,
    max_cost_cny: str = "20",
    root: Path | None = None,
) -> ExecutionSpec:
    """由规模、规划器与费用上限生成正式运行设置。城市为空 = 资料包里的全部城市；分身编号不写 = 资料包运行声明的编号；年数不写 =
    资料包协议的年数（要读基础样例：``root`` 是资料包相对路径的根，默认是资料包所在的仓库）。"""
    all_cities = tuple(c.city_ref for c in pack.body.cities)
    chosen = cities or all_cities
    unknown = [c for c in chosen if c not in all_cities]
    if unknown:
        raise ValueError(f"资料包里没有这些城市：{unknown}")
    if len(set(chosen)) != len(chosen):
        raise ValueError("城市不能重复")
    repeats = (
        tuple(repeat_indexes) if repeat_indexes is not None else tuple(pack.body.run.repeat_indexes)
    )
    if not repeats or len(set(repeats)) != len(repeats) or min(repeats) < 0:
        raise ValueError("分身编号必须是不重复的非负整数")
    if years is None:
        years = protocol_years(pack, root if root is not None else _pack_root(pack))
    revisions = pack.body.run.max_revisions_per_year
    calls = len(chosen) * len(repeats) * years * (1 + revisions)
    head: dict[str, Any] = dict(
        version=EXECUTION_VERSION,
        pack_path=pack_path,
        pack_fingerprint=pack.fingerprint,
        cities=chosen,
        repeat_indexes=repeats,
        years=years,
        max_revisions_per_year=revisions,
        global_call_limit=calls,
        max_cost=max_cost_cny,
    )
    if planner == "mock":
        # mock 不花钱：费用上限只是记下用户的选择（进入这次设置的身份），不进 E1 运行配置
        return ExecutionSpec(
            **head,
            planner_kind="mock",
            planner_identity=PRODUCT_MOCK_IDENTITY.model_dump(mode="json", exclude_none=True),
            currency="CNY",
            transport_retries=0,
        )
    if flash_config is None:
        raise ValueError("真实规划器要给出调用设定与报价的来源（run_product_flash.yaml）")
    flash = yaml.safe_load(Path(flash_config).read_text(encoding="utf-8"))["run"]
    settings = LlmSettings.model_validate(flash["planner"]["llm"])
    price = PriceTable.model_validate(flash["paid_budget"]["price"])
    retries = int(flash["planning"]["transport_retries"])
    return ExecutionSpec(
        **head,
        planner_kind="llm",
        planner_identity=llm_identity(settings).model_dump(mode="json"),
        llm=settings.model_dump(mode="json", exclude_none=True),
        price=price.model_dump(mode="json", exclude_none=True),
        currency=price.currency,  # type: ignore[arg-type]
        max_requests=calls * (1 + settings.service_retries),
        transport_retries=retries,
    )


def _pack_root(pack: DataPack) -> Path:
    """资料包文件 ``<根>/configs/product/<名>.yaml`` 的根（资料包里的路径都相对它）。"""
    return Path(pack.path).resolve().parents[2]


class RunOptionsError(ValueError):
    """用户选的运行选项不在服务允许的范围内（不回显用户填的值）。"""

    def __init__(self, field: OptionField, reason: str) -> None:
        super().__init__(reason)
        self.field = field


class RunOptions(BaseModel):
    """一次运行解析后的选项（省略的已经换成当时的默认值；保存下来，之后服务默认值变了也不影响这次运行）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cities: tuple[str, ...] = Field(min_length=1)
    clones_per_city: int = Field(ge=1)
    years: int = Field(ge=1)
    max_cost_cny: str


@dataclass(frozen=True)
class RunPolicy:
    """服务允许用户选择什么（来自服务配置与资料包）。``derive`` 校验一次选择并产生这次运行的有效设置。"""

    pack: DataPack
    pack_path: str
    planner: Literal["mock", "llm"]
    flash_config: Path | None
    root: Path
    cities_allowed: tuple[str, ...]
    clones_default: int
    clones_min: int
    clones_max: int
    years_default: int
    years_allowed: tuple[int, ...]
    cost_default: Decimal
    cost_min: Decimal
    cost_max: Decimal

    @classmethod
    def from_config(cls, config: Any, pack: DataPack, root: Path) -> RunPolicy:
        """由服务配置（``product.service.config.ServiceConfig``）与资料包产生。配置与资料包对不上（默认值不在允许范围、城市不在资料包、
        默认年数资料包不支持）就拒绝启动服务。"""
        all_cities = tuple(c.city_ref for c in pack.body.cities)
        allowed = tuple(config.allowed_cities) or all_cities
        unknown = [c for c in allowed if c not in all_cities]
        if unknown:
            raise ValueError(f"服务配置允许的城市不在资料包里：{unknown}")
        supported = pack_years(pack, root)
        years = tuple(y for y in (config.years_allowed or supported) if y in supported)
        if not years:
            raise ValueError("服务允许的年数与资料包支持的年数没有交集")
        default_years = config.default_years if config.default_years is not None else None
        if default_years is None:
            protocol = protocol_years(pack, root)
            default_years = protocol if protocol in years else years[0]
        if default_years not in years:
            raise ValueError(
                f"默认年数 {default_years} 不在允许且资料包支持的年数 {list(years)} 里"
            )
        low, default, high = config.cost_range
        return cls(
            pack=pack,
            pack_path=config.pack_path,
            planner=config.planner,
            flash_config=(root / config.flash_config) if config.planner == "llm" else None,
            root=root,
            cities_allowed=allowed,
            clones_default=config.default_clones_per_city,
            clones_min=config.clones_per_city_min,
            clones_max=config.clones_per_city_max,
            years_default=default_years,
            years_allowed=tuple(sorted(years)),
            cost_default=default,
            cost_min=low,
            cost_max=high,
        )

    def resolve(
        self,
        cities: tuple[str, ...],
        *,
        clones_per_city: int | None = None,
        years: int | None = None,
        max_cost_cny: str | None = None,
    ) -> RunOptions:
        """校验一次选择，把省略的换成默认值。城市来自确认画像：不重复、非空、都在允许范围里；不替用户删城或补城。"""
        if not cities:
            raise RunOptionsError("cities", "没有选城市")
        if len(set(cities)) != len(cities):
            raise RunOptionsError("cities", "城市重复")
        if any(c not in self.cities_allowed for c in cities):
            raise RunOptionsError("cities", "有城市不在服务允许的范围里")
        clones = self.clones_default if clones_per_city is None else clones_per_city
        if not self.clones_min <= clones <= self.clones_max:
            raise RunOptionsError("clones_per_city", "每城分身数不在允许范围里")
        n_years = self.years_default if years is None else years
        if n_years not in self.years_allowed:
            raise RunOptionsError("years", "这个年数不开放（服务不允许或资料包没有配套验证）")
        cost = self.cost_default if max_cost_cny is None else _cents(max_cost_cny)
        if not self.cost_min <= cost <= self.cost_max:
            raise RunOptionsError("max_cost_cny", "费用上限不在允许范围里")
        return RunOptions(
            cities=tuple(cities),
            clones_per_city=clones,
            years=n_years,
            max_cost_cny=_money_text(cost),
        )

    def derive(self, options: RunOptions) -> ExecutionSpec:
        """一份（已经解析的）选择 → 这次运行的有效设置。分身编号 0…n−1 由服务端生成，菜单分配沿用编译器的规则（不重抽菜单）。
        重新检查时用同一份选择再算一遍：资料包、规划器设定或报价变了，有效设置就变（身份不同）。"""
        checked = self.resolve(
            options.cities,
            clones_per_city=options.clones_per_city,
            years=options.years,
            max_cost_cny=options.max_cost_cny,
        )
        return build_execution_spec(
            pack=self.pack,
            pack_path=self.pack_path,
            planner=self.planner,
            cities=checked.cities,
            repeat_indexes=tuple(range(checked.clones_per_city)),
            years=checked.years,
            flash_config=self.flash_config,
            max_cost_cny=checked.max_cost_cny,
            root=self.root,
        )

    def default_options(self) -> RunOptions:
        return self.resolve(self.cities_allowed)


def _cents(text: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise RunOptionsError("max_cost_cny", "费用上限不是合法金额") from exc
    if not value.is_finite() or value != value.quantize(Decimal("0.01")):
        raise RunOptionsError("max_cost_cny", "费用上限最多写到分")
    return value


def _money_text(value: Decimal) -> str:
    """按分规范写法：去掉多余的 0（``20``、``0.5``、``19.99``）。"""
    q = value.quantize(Decimal("0.01"))
    text = format(q, "f").rstrip("0").rstrip(".")
    return text or "0"


def service_pack(pack_file: Path, spec: ExecutionSpec) -> DataPack:
    """按运行设置读资料包：文件内容身份必须是设置记下的那一份（资料包更新不替换已确认的运行）。资料包原样读出、不改内存里的运行
    声明——每城分身数是这次运行的设置（编译与预检查从 ``spec`` 读），所以确认画像记下的资料包身份照样对得上。"""
    pack = load_pack(pack_file)
    if pack.fingerprint != spec.pack_fingerprint:
        raise ValueError("资料包的内容与运行设置记下的不同")
    return pack
