"""E1-7A 运行配置的加载与跨对象检查（`DECISIONS/0030`）。不运行任何生活，也不调用规划器。

运行配置在开跑之前写好（YAML 顶层键 ``run``）：原计划名单、城市事实表、信息条件、每年的规划与修订上限、全局调用上限、运行顺序、
规划器身份（本轮只支持可控的 mock）、提示模板与记忆政策的版本。它与名单、事实表一起决定这次运行的身份；改了任何一项，都不能
冒充原来那次运行继续。

这里检查的是配置本身与名单之间的一致：规划器种类（真实模型在 E1-7B）、受控行为指向的分身确实在名单上、年份不超出协议的 N、
协议是按年交接的生活协议（``run_life_year`` 需要月度资源规则与反馈政策）。名单本身的检查（标准开局 v1、每个运行臂的跨城配置
检查）沿用 ``ledger.life_roster``；城市事实与评分条件的对应沿用 ``ledger.life_city_facts``。

E1-7B（``DECISIONS/0031``）：真实模型规划器只在运行配置版本 e1-7b-run-v1 出现；它的服务商、端点与模型快照必须与
``configs/models.yaml`` 里同一角色的声明一致（这里只读声明，不读密钥）；城市目录与城市事实里给分身看的文字不能有写给开发者的说明。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from common.config import ConfigError, ModelsConfig
from common.life_aggregation_contracts import RosterConfig
from common.life_city_facts import CityFactsConfig
from common.life_contracts import LifeContractError
from common.life_planning_contracts import DEVELOPER_NOTE_MARKERS, LifeRunConfig
from ledger.life_catalog import LifeBundle

#: 模型角色的声明文件（只读服务商、端点与模型快照；密钥只在调用时读取）。
MODELS_CONFIG = Path("configs/models.yaml")


def parse_run_config(data: dict[str, Any]) -> LifeRunConfig:
    try:
        return LifeRunConfig.model_validate(data)
    except ValidationError as exc:
        lines = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        raise LifeContractError("运行配置结构不合法：\n  " + "\n  ".join(lines)) from exc


def load_run_config(path: Path) -> LifeRunConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "run" not in raw:
        raise LifeContractError(f"{path}: 顶层必须是映射，并有 run 一节")
    return parse_run_config(raw["run"])


def _strings(obj: Any) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _strings(v)]
    if isinstance(obj, list | tuple):
        return [s for v in obj for s in _strings(v)]
    return []


def developer_note_hits(bundle: LifeBundle, facts: CityFactsConfig | None = None) -> list[str]:
    """城市目录（机会、治疗）与城市事实表里写给开发者的说明（E1-7B 第 4.4 节）。真实模型运行在开跑前拒绝；发送前另有一次
    对最终请求的同样扫描（``agentlayer.life_planning.developer_note_problems``）。"""
    out: list[str] = []
    parts: list[tuple[str, Any]] = []
    for c in bundle.catalogs:
        parts.append((f"{c.city} 的机会目录", [o.model_dump(mode="json") for o in c.opportunities]))
        parts.append((f"{c.city} 的治疗目录", [t.model_dump(mode="json") for t in c.treatments]))
    if facts is not None:
        parts.append(("城市事实表", [s.model_dump(mode="json") for s in facts.sheets]))
    for where, part in parts:
        for s in _strings(part):
            hits = [m for m in DEVELOPER_NOTE_MARKERS if m in s]
            if hits:
                out.append(f"{where}里有写给开发者的说明（{'、'.join(hits)}）：{s[:80]}")
    return out


def llm_settings_problems(config: LifeRunConfig, models_path: Path = MODELS_CONFIG) -> list[str]:
    """真实模型的设定与 ``configs/models.yaml`` 同一角色的声明是否一致（只读声明，不解析密钥）。"""
    llm = config.planner.llm
    if llm is None:
        return []
    try:
        spec = ModelsConfig.load(models_path).spec(llm.model_role)
    except (OSError, ConfigError, ValueError) as exc:
        return [
            f"读不了模型角色 {llm.model_role} 的声明 {models_path}（不回退到别的角色）：{type(exc).__name__}"
        ]
    out = []
    for name, got, want in (
        ("服务商", llm.provider, spec.provider),
        ("端点", llm.base_url, spec.base_url),
        ("模型快照", llm.model, spec.model),
    ):
        if got != want:
            out.append(
                f"运行配置的{name} {got} 与 {models_path} 里 {llm.model_role} 的声明 {want} 不同：不暗中换模型或端点"
            )
    # 版本策略（2026-10-13，DECISIONS/0032）：产品角色的声明写明 dated_snapshot 或 rolling，运行配置必须一致；研究角色只有固定版本
    want_policy = getattr(spec, "version_policy", "dated_snapshot")
    if llm.effective_version_policy != want_policy:
        out.append(
            f"运行配置的版本策略 {llm.effective_version_policy} 与 {models_path} 里 {llm.model_role} 的 {want_policy} 不同"
        )
    return out


def run_config_problems(
    config: LifeRunConfig,
    roster: RosterConfig,
    bundle: LifeBundle,
    facts: CityFactsConfig | None = None,
    *,
    models_path: Path = MODELS_CONFIG,
) -> list[str]:
    """运行配置与名单、生活输入之间的不一致（空列表 = 可以开跑）。"""
    out: list[str] = []
    ident = config.planner.identity
    if ident.kind == "llm":
        out += llm_settings_problems(config, models_path)
        out += developer_note_hits(bundle, facts)
    protocol = bundle.protocol
    clones = {e.clone_id for e in roster.entries}
    n = protocol.simulation_years
    for s in config.planner.scripts:
        if s.clone_id not in clones:
            out.append(f"受控行为 {s.behavior} 指向的 {s.clone_id} 不在原计划名单上")
        late = [y for y in s.years if y > n]
        if late:
            out.append(f"受控行为 {s.behavior}（{s.clone_id}）的年份 {late} 超出协议的 N = {n}")
    if not roster.entries:
        out.append("原计划名单为空：没有可以运行的分身")
    return out
