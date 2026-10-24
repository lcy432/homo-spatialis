"""正式运行设置（E3C，DECISIONS/0043）：``ExecutionSpec`` 由服务端配置产生，不是网页能改的表单。

它固定这次模拟的规模（城市、每城分身、年数）、规划器（离线 mock 或真实模型 ``product_planner`` 角色）、有效调用设置、报价版本，
以及规划候选、服务尝试与费用的上限。编译（``product.compile.compile_inputs``）按它写出 E1 运行配置，**之后**才生成运行前检查——
用户确认的那份预检查就绑定了真实的规划器与上限，后台不能换成别的规划器。

固定规模下：6 座城 × 4 个分身 × 3 年 = 72 个分身年度；每年一次正常规划、最多两次修订 → 最多 216 份规划候选；每份候选最多再试一次
服务请求 → 最多 432 次服务尝试；费用上限 10 元（应用侧按报价估算，不是供应商账单上限）。模型调用设定与报价从
``configs/life/run_product_flash.yaml`` 读，不在这里另写一套，也不自行放大输入或输出长度。
"""

from __future__ import annotations

import copy
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


def build_execution_spec(
    *,
    pack: DataPack,
    pack_path: str,
    planner: Literal["mock", "llm"],
    cities: tuple[str, ...] = (),
    repeat_indexes: tuple[int, ...] = (0, 1, 2, 3),
    years: int = 3,
    flash_config: Path | None = None,
    max_cost_cny: str = "10",
) -> ExecutionSpec:
    """由服务配置与资料包生成正式运行设置。城市为空 = 资料包里的全部城市。"""
    all_cities = tuple(c.city_ref for c in pack.body.cities)
    chosen = cities or all_cities
    unknown = [c for c in chosen if c not in all_cities]
    if unknown:
        raise ValueError(f"资料包里没有这些城市：{unknown}")
    revisions = pack.body.run.max_revisions_per_year
    calls = len(chosen) * len(repeat_indexes) * years * (1 + revisions)
    head: dict[str, Any] = dict(
        version=EXECUTION_VERSION,
        pack_path=pack_path,
        pack_fingerprint=pack.fingerprint,
        cities=chosen,
        repeat_indexes=repeat_indexes,
        years=years,
        max_revisions_per_year=revisions,
        global_call_limit=calls,
    )
    if planner == "mock":
        return ExecutionSpec(
            **head,
            planner_kind="mock",
            planner_identity=PRODUCT_MOCK_IDENTITY.model_dump(mode="json", exclude_none=True),
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
        max_cost=max_cost_cny,
        max_requests=calls * (1 + settings.service_retries),
        transport_retries=retries,
    )


def service_pack(pack_file: Path, spec: ExecutionSpec) -> DataPack:
    """按运行设置读资料包：文件内容身份必须是设置记下的那一份（资料包更新不替换已确认的运行）；每城分身数按设置写进内存里的
    运行声明（与 ``product.scenarios.closed_loop`` 同一种改法，磁盘上的资料包不动）。"""
    pack = load_pack(pack_file)
    if pack.fingerprint != spec.pack_fingerprint:
        raise ValueError("资料包的内容与运行设置记下的不同")
    raw = copy.deepcopy(pack.raw)
    raw["pack"]["run"]["repeat_indexes"] = list(spec.repeat_indexes)
    return DataPack(raw, pack.path)
