"""E1-7A 运行归档：同一次运行的全部记录，供审计、重放与年度边界恢复（`DECISIONS/0030` 第九节）。

一次运行一个目录（新运行时目录已存在就拒绝，不覆盖旧结果；恢复只在原目录里追加）：

    manifest.json           运行清单：全部内容身份（配置、名单、协议、目录与税、评分、评价、事实与对应、许可、提示模板、
                            记忆政策、规划器、事件来源、关键代码）
    support_scope.json/.md  运行前的支持范围说明
    frozen_roster.json      冻结的原计划名单
    alignment.json          城市事实 ↔ 评分条件的逐项核对
    archive.sqlite          逐条记录（``common.run_store``；2026-10-18 起取代原来的 jsonl），每种一张表：
                              calls           每次规划调用：请求编号、规划器身份、完整的可见请求（JSON）、提示指纹、
                                              原始响应或错误
                              attempts        每次调用的结构化结果：解析、执行前检查的阶段与问题代码、处置、安全诊断、
                                              需求摘要与理由
                              accepted        被接受并执行的年度行动与执行后的绑定指纹（重放与恢复只读这里，不重问规划器）
                              provider_calls  真实模型的服务请求明细（mock 没有）
    inputs/                 第一次规划调用之前冻结的输入文件
    progress.json           运行进度（每个年度边界、暂停、中断与结束时写）
    evaluations/<臂>.json   E1-6A 单分身评价；failures.json 没有评价结果的名单项；reports/<臂>.json E1-6B 城市汇总
    report.md               中文运行报告

mock 规划器的身份与用量记在 calls 表的 planner 一栏（没有 token、没有费用，不伪造），不写进真实模型的调用日志
（``common.runlog``）。归档里不写任何密钥或环境变量。全部文本用 UTF-8、LF 换行。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from common.life_contracts import LifeContractError
from common.run_store import RunStore

#: Windows 上刚写好的临时文件可能被杀毒或索引程序短暂占用，``os.replace`` 报"拒绝访问"（E1-7B 第一次跑 E1-7A 演示时遇到）。
#: 只对这种错误有限次重试，等待有上限；仍然失败就照常抛出，不吞掉。
REPLACE_RETRIES = 8
REPLACE_WAIT_SECONDS = 0.25


def atomic_replace(tmp: Path, dst: Path) -> None:
    for i in range(REPLACE_RETRIES):
        try:
            os.replace(tmp, dst)
            return
        except PermissionError:
            if i == REPLACE_RETRIES - 1:
                raise
            time.sleep(REPLACE_WAIT_SECONDS)


class RunArchive:
    """一次运行的归档目录。``create=True`` 新建（已存在就拒绝）；``create=False`` 打开已有的运行（必须有运行清单）。"""

    def __init__(self, path: Path, *, create: bool) -> None:
        self.path = Path(path)
        if create:
            if self.path.exists():
                raise LifeContractError(f"{self.path} 已存在：不覆盖旧的运行结果，请换一个新目录")
            self.path.mkdir(parents=True)
        elif not (self.path / "manifest.json").is_file():
            raise LifeContractError(f"{self.path} 不是一次 E1-7A 运行的归档（没有 manifest.json）")

    def _file(self, name: str) -> Path:
        p = self.path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def write_json(self, name: str, obj: Any) -> None:
        """整份写入（先写临时文件再替换，中途断电不会留下半个文件）。"""
        p = self._file(name)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(
            json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        atomic_replace(tmp, p)

    def write_text(self, name: str, text: str) -> None:
        p = self._file(name)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(text, encoding="utf-8", newline="\n")
        atomic_replace(tmp, p)

    @property
    def store(self) -> RunStore:
        return RunStore(self.path)

    def append(self, stream: str, obj: Any) -> None:
        """追加一条记录到 ``archive.sqlite`` 的 ``stream`` 表（calls / attempts / accepted / provider_calls）。"""
        self.store.append(stream, obj)

    def records(self, stream: str) -> list[Any]:
        return self.store.records(stream)

    def read_json(self, name: str) -> Any:
        return json.loads((self.path / name).read_text(encoding="utf-8"))

    def exists(self, name: str) -> bool:
        return (self.path / name).is_file()
