"""服务配置（``configs/product/service.yaml``，不含任何秘密）。

* ``profile``：``production``（同源 HTTPS、安全 Cookie、不公开交互式文档）、``local_mock``（显式开启的本地 HTTP，只绑定回环地址）、
  ``test``（自动化测试用：临时数据目录）。
* 运行选项分三层（E3C 收尾）：**默认值**（用户不选时用什么）、**允许范围**（用户能选什么，由维护者在这里定）、**本次有效设置**
  （创建运行意图时由服务端校验并为这次运行保存的 ``ExecutionSpec``，见 ``product.execution``）。城市来自用户确认的画像，必须
  是 ``allowed_cities`` 的子集；年数还要和资料包支持的年数取交集。改这里的默认值或范围不影响已经创建的运行。
* ``live_calls_enabled`` 默认 ``false``：真实规划器的通路在，但开关不开就不发送任何付费请求，明确报告，不偷偷换成 mock。
* 数据目录（服务库、每次模拟的工作目录、账本、日志）必须在 git 忽略的位置（默认 ``runs/product-service/``）。
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

REPO = Path(__file__).resolve().parents[3]
SERVICE_CONFIG = REPO / "configs/product/service.yaml"
#: 金额：元，最多到分
CENTS = r"^(0|[1-9][0-9]{0,6})(\.[0-9]{1,2})?$"


class ServiceConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["e3c-service-config-v1"]
    profile: Literal["production", "local_mock", "test"]
    data_dir: str = Field(min_length=1)
    #: 网页与 API 的同一个来源（scheme://host[:port]），修改请求的 Origin 必须等于它
    site_origin: str = Field(pattern=r"^https?://[A-Za-z0-9.\-]+(:[0-9]{1,5})?$")
    bind_host: str = "127.0.0.1"
    bind_port: int = Field(default=8765, ge=1, le=65535)
    #: 反向代理的地址：只信任它转发的客户端地址（uvicorn ``forwarded_allow_ips``；限流按来源地址）
    forwarded_allow_ips: str = "127.0.0.1"
    pack_path: str = "configs/product/e3b_us_v1_pack.yaml"
    #: 用户确认的城市允许取自哪些城市：空 = 资料包里的全部城市
    allowed_cities: tuple[str, ...] = ()
    #: 每城分身数：不选时的默认值与允许范围
    default_clones_per_city: int = Field(default=4, ge=1, le=1000)
    clones_per_city_min: int = Field(default=1, ge=1, le=1000)
    clones_per_city_max: int = Field(default=4, ge=1, le=1000)
    #: 年数：不写 = 资料包协议的年数；允许的年数不写 = 资料包支持的全部（最终取两者的交集）
    default_years: int | None = Field(default=None, ge=1, le=100)
    years_allowed: tuple[int, ...] = ()
    planner: Literal["llm", "mock"] = "llm"
    live_calls_enabled: bool = False
    flash_config: str = "configs/life/run_product_flash.yaml"
    models_config: str = "configs/models.yaml"
    #: 这次运行的费用上限（全部城市、全部分身、全部服务尝试共用；应用侧按报价预留，不是供应商账单上限）：默认值与允许范围。
    #: 范围上限不写 = 默认值（不选更高的预算）
    max_cost_cny: str = Field(default="20", pattern=CENTS)
    max_cost_cny_min: str = Field(default="0.01", pattern=CENTS)
    max_cost_cny_max: str | None = Field(default=None, pattern=CENTS)
    workers: int = Field(default=16, ge=1, le=16)
    runs_total: int = Field(default=3, ge=1)
    session_days: int = Field(default=7, ge=1)
    result_retention_days: int = Field(default=30, ge=1)
    draft_retention_days: int = Field(default=30, ge=1)
    intent_retention_days: int = Field(default=30, ge=1)
    #: 确认画像从首次确认起可以用来准备新模拟的天数（重复确认与查询不续期）
    profile_retention_days: int = Field(default=30, ge=1)
    max_body_bytes: int = Field(default=256 * 1024, ge=1024)
    invite_rate_per_minute: int = Field(default=10, ge=1)
    write_rate_per_minute: int = Field(default=60, ge=1)
    worker_poll_seconds: float = Field(default=2.0, gt=0)
    progress_poll_seconds: float = Field(default=5.0, gt=0)
    maintenance_seconds: float = Field(default=60.0, gt=0)
    #: 交互式 API 文档（/docs）。生产环境必须关闭
    expose_docs: bool = False

    @model_validator(mode="after")
    def _rules(self) -> ServiceConfig:
        if self.profile == "production":
            if not self.site_origin.startswith("https://"):
                raise ValueError("生产环境必须是同源 HTTPS")
            if self.expose_docs:
                raise ValueError("生产环境不公开交互式 API 文档")
            if self.planner != "llm":
                raise ValueError("生产环境使用真实规划器（product_planner 角色），不用 mock 冒充")
        if self.profile == "local_mock":
            if self.bind_host not in ("127.0.0.1", "::1", "localhost"):
                raise ValueError("本地 HTTP 模式只绑定回环地址")
            if self.planner != "mock" or self.live_calls_enabled:
                raise ValueError("本地 HTTP 模式只用离线 mock，不发付费请求")
        if len(set(self.allowed_cities)) != len(self.allowed_cities):
            raise ValueError("allowed_cities 不能重复")
        if not (
            self.clones_per_city_min <= self.default_clones_per_city <= self.clones_per_city_max
        ):
            raise ValueError("每城分身数必须 最小 ≤ 默认 ≤ 最大")
        if len(set(self.years_allowed)) != len(self.years_allowed) or any(
            y < 1 for y in self.years_allowed
        ):
            raise ValueError("years_allowed 不能重复，且都要 ≥ 1")
        if (
            self.default_years is not None
            and self.years_allowed
            and self.default_years not in self.years_allowed
        ):
            raise ValueError("默认年数必须在允许的年数里")
        low, default, high = self.cost_range
        if not (Decimal("0.01") <= low <= default <= high):
            raise ValueError("费用上限必须 0.01 ≤ 最小 ≤ 默认 ≤ 最大")
        return self

    @property
    def cost_range(self) -> tuple[Decimal, Decimal, Decimal]:
        """费用上限的（最小，默认，最大），元。最大不写就等于默认。"""
        high = self.max_cost_cny_max if self.max_cost_cny_max is not None else self.max_cost_cny
        return Decimal(self.max_cost_cny_min), Decimal(self.max_cost_cny), Decimal(high)

    @property
    def secure_cookies(self) -> bool:
        return self.profile == "production"

    def data_root(self, base: Path = REPO) -> Path:
        p = Path(self.data_dir)
        return (p if p.is_absolute() else base / p).resolve()

    def repo_path(self, rel: str) -> Path:
        return (REPO / rel).resolve()


def load_service_config(path: Path | str = SERVICE_CONFIG) -> ServiceConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return ServiceConfig.model_validate(raw["service"])
