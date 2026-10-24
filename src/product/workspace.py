"""E3 工作目录（生产与测试共用，E3C）：一个自包含的“小仓库”——资料包与它显式采用的产品协议基础样例按仓库里的相对路径复制
进来，编译产物与运行配置都用相对路径，结果不依赖目录放在哪里。

每次模拟一个独立的工作目录（E3C）。复制时记下每个文件的 SHA-256（``snapshot``）：已确认并排队的运行绑定这份快照，之后仓库里的
资料包更新不会替换它；快照缺失或被改坏时拒绝继续，不拿新资料冒充旧资料恢复。
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

#: 资料包、它显式采用的基础样例与构建报告（相对仓库根目录）。合成资料包与 E3B 真实资料包都在里面。
WORKSPACE_FILES = (
    "configs/product/e3a_synthetic_pack.yaml",
    "configs/life/synthetic_product.yaml",
    "configs/life/scoring_product.yaml",
    "configs/life/evaluation_product.yaml",
    "configs/life/city_facts_product.yaml",
    # E3B 六城真实资料包（DECISIONS/0042）
    "configs/product/e3b_us_v1_pack.yaml",
    "data/frozen/life/us-v1/pack/life_input.json",
    "data/frozen/life/us-v1/pack/scoring.json",
    "data/frozen/life/us-v1/pack/evaluation.json",
    "data/frozen/life/us-v1/pack/city_facts.json",
    # 构建报告：产品消息里的资料性质读它的计数（product.pack_facts）
    "data/frozen/life/us-v1/pack/report.json",
)
#: 真实模型规划器的运行前检查要按模型角色表核对服务商、端点与型号（不含密钥）
MODELS_FILE = "configs/models.yaml"


class SnapshotError(RuntimeError):
    """工作目录里的资料快照缺失或与记下的内容不同。"""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_workspace(
    work: Path, repo: Path, *, files: tuple[str, ...] = WORKSPACE_FILES, models: bool = False
) -> dict[str, str]:
    """把资料包与基础样例按原相对路径复制进 ``work``（只复制，不修改），返回快照（相对路径 → SHA-256）。"""
    work.mkdir(parents=True, exist_ok=True)
    wanted = (*files, MODELS_FILE) if models else files
    snapshot: dict[str, str] = {}
    for rel in wanted:
        dst = work / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo / rel, dst)
        snapshot[rel] = _sha(dst)
    return snapshot


def verify_snapshot(work: Path, snapshot: dict[str, str]) -> None:
    """快照里的每个文件都还在、内容没有变；否则拒绝（不拿仓库里的新资料补回去）。"""
    problems = []
    for rel, sha in sorted(snapshot.items()):
        path = work / rel
        if not path.is_file():
            problems.append(f"{rel} 不在了")
        elif _sha(path) != sha:
            problems.append(f"{rel} 的内容变了")
    if problems:
        raise SnapshotError("；".join(problems))
