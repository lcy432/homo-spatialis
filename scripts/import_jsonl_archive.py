"""一次性导入：把旧运行归档里的 calls / attempts / accepted / provider_calls 四个 jsonl 导进同一目录的 archive.sqlite。

2026-10-18 起运行归档的逐条记录存在 archive.sqlite（common.run_store），新代码不再读 jsonl。旧归档导入之后，就能用现在的代码
续跑与重放（resume / replay 照常核对运行清单里的内容身份）。

* 每一行原样写进对应的表（JSON 文本逐字不变，顺序不变）；
* 某张表在库里已经有记录就拒绝导入那张表（不重复导入、不覆盖）；
* 原来的 jsonl 文件保留不动，确认无误后可以自己删掉；
* 运行目录外的预算账本（ledger.jsonl）不在这里。

用法：uv run python scripts/import_jsonl_archive.py <运行目录> [<运行目录> ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import _bootstrap  # noqa: F401  (side effect: puts src/ on the path)
from common.run_store import STREAMS, RunStore


def import_run(run_dir: Path) -> dict[str, int | str]:
    if not (run_dir / "manifest.json").is_file():
        raise SystemExit(f"{run_dir} 不是运行归档（没有 manifest.json）")
    store = RunStore(run_dir)
    result: dict[str, int | str] = {}
    for stream in STREAMS:
        src = run_dir / f"{stream}.jsonl"
        if not src.is_file():
            result[stream] = "没有文件"
            continue
        if store.has(stream):
            result[stream] = "库里已有记录，跳过"
            continue
        lines = [ln for ln in src.read_text(encoding="utf-8").splitlines() if ln.strip()]
        for i, ln in enumerate(lines, start=1):
            try:
                json.loads(ln)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{src} 第 {i} 行不是合法 JSON：{exc}") from exc
            store.append(stream, ln)
        if store.bodies(stream) != lines:
            raise SystemExit(f"{src}：导入后读回的内容与原文件不同")
        result[stream] = len(lines)
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("runs", nargs="+", type=Path)
    for run_dir in ap.parse_args().runs:
        print(run_dir, json.dumps(import_run(run_dir), ensure_ascii=False))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
