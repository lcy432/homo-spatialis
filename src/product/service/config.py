"""服务配置（``configs/product/service.yaml``，不含任何秘密）。

* ``profile``：``production``（同源 HTTPS、安全 Cookie、不公开交互式文档；固定规模六城 × 每城 4 个分身 × 3 年）、
  ``local_mock``（显式开启的本地 HTTP，只绑定回环地址；规模同生产）、``test``（自动化测试用：临时数据目录，可以缩小规模加快测试）。
  生产与本地模式的规模由这里校验，浏览器不能改。
* ``live_calls_enabled`` 默认 ``false``：真实规划器的通路在，但开关不开就不发送任何付费请求，明确报告，不偷偷换成 mock。
* 数据目录（服务库、每次模拟的工作目录、账本、日志）必须在 git 忽略的位置（默认 ``runs/product-service/``）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

REPO = Path(__file__).resolve().parents[3]
SERVICE_CONFIG = REPO / "configs/product/service.yaml"
FIXED_REPEATS = (0, 1, 2, 3)
FIXED_YEARS = 3


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
    #: 运行的城市：空 = 资料包里的全部城市（生产与本地模式必须是全部）
    cities: tuple[str, ...] = ()
    repeat_indexes: tuple[int, ...] = FIXED_REPEATS
    years: int = FIXED_YEARS
    planner: Literal["llm", "mock"] = "llm"
    live_calls_enabled: bool = False
    flash_config: str = "configs/life/run_product_flash.yaml"
    models_config: str = "configs/models.yaml"
    max_cost_cny: str = Field(default="10", pattern=r"^[0-9]+(\.[0-9]{1,2})?$")
    workers: int = Field(default=16, ge=1, le=16)
    runs_total: int = Field(default=3, ge=1)
    session_days: int = Field(default=7, ge=1)
    result_retention_days: int = Field(default=30, ge=1)
    draft_retention_days: int = Field(default=30, ge=1)
    intent_retention_days: int = Field(default=30, ge=1)
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
        if self.profile in ("production", "local_mock"):
            if self.cities:
                raise ValueError("生产与本地模式固定运行资料包里的全部城市，不能在配置里删城")
            if tuple(self.repeat_indexes) != FIXED_REPEATS or self.years != FIXED_YEARS:
                raise ValueError("生产与本地模式的规模固定为每城 4 个分身、3 年")
        return self

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
