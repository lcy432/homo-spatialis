"""一次运行的记录库：运行目录里的 ``archive.sqlite``（2026-10-18 起取代 calls / attempts / accepted / provider_calls 四个 jsonl）。

每种记录一张表，结构相同：

    seq         自增序号（= 写入顺序；读取按它排序，语义与原来 jsonl 的行号相同）
    request_id  请求编号
    clone_id    分身
    year_index  年份
    body        整条记录的 JSON（紧凑写法，UTF-8，与原来 jsonl 的一行逐字相同）

前三列只是为了按分身、年份查询方便（``SELECT body FROM calls WHERE clone_id = ...``），读取时以 body 为准。表上不加唯一约束：
重复、缺失与对不上由运行器的归档核对报告（``orchestration.life_runner.load_archive_records``），数据库不替它悄悄去重。

每次读写各开一个连接、用完就关，事务提交即落盘（``synchronous=FULL``）；多个进程或线程同时写同一个库时由 SQLite 的锁排队
（等待上限 ``BUSY_TIMEOUT_SECONDS``）。不存密钥或环境变量。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

DB_NAME = "archive.sqlite"
#: 归档里的记录种类（表名）。新增种类要加在这里，不接受任意表名。
STREAMS: tuple[str, ...] = ("calls", "attempts", "accepted", "provider_calls")
BUSY_TIMEOUT_SECONDS = 30.0


def _check(stream: str) -> str:
    if stream not in STREAMS:
        raise ValueError(f"不认识的记录种类 {stream!r}（只有 {', '.join(STREAMS)}）")
    return stream


class RunStore:
    """``<运行目录>/archive.sqlite``。第一次写入时建库建表；只读不建库（没有库就是没有记录）。"""

    def __init__(self, run_dir: Path) -> None:
        self.path = Path(run_dir) / DB_NAME

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_SECONDS)) as con:
            con.execute("PRAGMA synchronous = FULL")
            yield con

    def _ensure(self, con: sqlite3.Connection) -> None:
        for s in STREAMS:
            con.execute(
                f"CREATE TABLE IF NOT EXISTS {s} (seq INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT,"
                " clone_id TEXT, year_index INTEGER, body TEXT NOT NULL)"
            )
            con.execute(f"CREATE INDEX IF NOT EXISTS {s}_clone_year ON {s} (clone_id, year_index)")

    def append(self, stream: str, obj: Any) -> None:
        """追加一条（dict，或已经序列化好的 JSON 字符串）。事务提交之后才返回。"""
        _check(stream)
        body = (
            obj
            if isinstance(obj, str)
            else json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        )
        row = json.loads(body) if isinstance(obj, str) else obj
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con, con:
            self._ensure(con)
            con.execute(
                f"INSERT INTO {stream} (request_id, clone_id, year_index, body) VALUES (?, ?, ?, ?)",
                (row.get("request_id"), row.get("clone_id"), row.get("year_index"), body),
            )

    def bodies(self, stream: str) -> list[str]:
        """按写入顺序返回原始 JSON 文本。"""
        _check(stream)
        if not self.path.is_file():
            return []
        with self._connect() as con:
            have = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (stream,)
            ).fetchone()
            if not have:
                return []
            return [b for (b,) in con.execute(f"SELECT body FROM {stream} ORDER BY seq")]

    def records(self, stream: str) -> list[Any]:
        return [json.loads(b) for b in self.bodies(stream)]

    def has(self, stream: str) -> bool:
        return bool(self.bodies(stream))

    def replace(self, stream: str, rows: list[Any]) -> None:
        """整表改写成 ``rows``（按顺序重新编号）。只供测试与演示模拟"归档被改动"；运行器从不改写已有记录。"""
        _check(stream)
        with self._connect() as con, con:
            self._ensure(con)
            con.execute(f"DELETE FROM {stream}")
            con.execute("DELETE FROM sqlite_sequence WHERE name = ?", (stream,))
        for r in rows:
            self.append(stream, r)

    def dump(self) -> dict[str, list[Any]]:
        """全部记录（按种类、按写入顺序），比较两份归档的内容用——不比较数据库文件的字节。"""
        return {s: self.records(s) for s in STREAMS}
