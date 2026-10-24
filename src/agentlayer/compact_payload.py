"""规划请求的可逆紧凑表示（E3B 映射修正 §7）：共同字段 + 行表。

目录变大以后，同一类对象（机会、片区、通勤线……）逐条重复写同样的键与同样的取值，提示里大部分是重复。这里把“由对象组成的
列表”改写成一张表，只省重复，不删字段、不改取值、不按任何评分筛选：

    {"$table": {"flat": 是否展开嵌套字段, "columns": [列名…], "common": {列名: 每行都相同的值},
                "rows": [[每行的值…], …]}}                       （列对齐的写法）
    或  "sparse_rows": [{"列号": 值, …}, …]                  （每行只写有的列：列号是 columns 里的位置，从 0 开始）

* 列名是字段路径：嵌套对象按“.”展开（``price.amount.amount``）；键里本来就有“.”或“$”的对象不展开，整块当一个值。
* 每一行都有、而且取值完全相同的字段只写一次，放在 ``common``。
* 一行缺某个字段（键不存在）：列对齐的写法写 ``{"$absent": 1}``，按行写的写法就是没有这个列号——都和 ``null``（键存在、
  值为空）是两回事，解码时原样还原。两种写法按编码后的长度取较短的一种（同样长取列对齐的），结果确定。
* 表里的值本身又是对象列表时，递归成嵌套的表。只有一项的列表、空列表、不全是对象的列表原样保留。

必须满足 ``decode(encode(payload)) == payload``。编码是规范的：列名排序，渲染时键也排序，所以同一份载荷（不论键的先后）
总是渲染出逐字相同的文字——恢复与重放靠这一点核对请求正文。载荷里本来就有以“$”开头的键时拒绝编码（保留给本表示用），不猜。
"""

from __future__ import annotations

import json
from typing import Any

FORMAT = "compact-tables-v1"
TABLE = "$table"
ABSENT = {"$absent": 1}


class CompactError(ValueError):
    pass


def _check_keys(obj: Any) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str):
                raise CompactError(f"键 {k!r} 不是字符串")
            if k.startswith("$"):
                raise CompactError(f"载荷里有保留键 {k!r}：不能用紧凑表示")
            _check_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            _check_keys(v)


def _canon(x: Any) -> str:
    """类型敏感的比较：1、1.0 与 true 是不同的值（JSON 文字不同），不能当成“每行相同”合并。"""
    return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _flatten(d: dict[str, Any], prefix: str = "") -> list[tuple[str, Any]]:
    """把对象展开成（路径，叶子值）；非空且键里没有“.”的子对象继续展开，空对象与其他值是叶子。"""
    out: list[tuple[str, Any]] = []
    for k, v in d.items():
        path = f"{prefix}{k}"
        if isinstance(v, dict) and v and all("." not in kk for kk in v):
            out.extend(_flatten(v, path + "."))
        else:
            out.append((path, v))
    return out


def _flattenable(rows: list[dict[str, Any]]) -> bool:
    return all("." not in k for r in rows for k in r)


def _encode_table(rows: list[dict[str, Any]]) -> dict[str, Any]:
    flat = [dict(_flatten(r)) if _flattenable(rows) else dict(r) for r in rows]
    # 列按名字排序：同一份载荷不论对象里键的先后，编码都一样（渲染时也按键排序），重新渲染能逐字复现
    columns = sorted({k for r in flat for k in r})
    common: dict[str, Any] = {}
    for c in columns:
        if all(c in r for r in flat):
            first = _canon(flat[0][c])
            if all(_canon(r[c]) == first for r in flat[1:]):
                common[c] = encode(flat[0][c])
    varying = [c for c in columns if c not in common]
    head = {"flat": _flattenable(rows), "columns": varying, "common": common}
    dense = {
        TABLE: {
            **head,
            "rows": [[encode(r[c]) if c in r else dict(ABSENT) for c in varying] for r in flat],
        }
    }
    sparse = {
        TABLE: {
            **head,
            "sparse_rows": [
                {str(i): encode(r[c]) for i, c in enumerate(varying) if c in r} for r in flat
            ],
        }
    }
    return sparse if len(_canon(sparse)) < len(_canon(dense)) else dense


def encode(obj: Any) -> Any:
    """紧凑表示（递归）。顶层调用前先检查保留键。"""
    if isinstance(obj, dict):
        return {k: encode(v) for k, v in obj.items()}
    if isinstance(obj, list):
        if len(obj) >= 2 and all(isinstance(x, dict) for x in obj):
            return _encode_table(obj)
        return [encode(x) for x in obj]
    return obj


def encode_payload(payload: dict[str, Any]) -> dict[str, Any]:
    _check_keys(payload)
    out = encode(payload)
    if _canon(decode(out)) != _canon(payload):  # 每次都核对一遍：编码有任何损失就停下，不发出去
        raise CompactError("紧凑表示不能原样还原：拒绝使用")
    return out


def _unflatten(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for path, v in pairs:
        parts = path.split(".")
        node = out
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = v
    return out


def decode(obj: Any) -> Any:
    if isinstance(obj, dict):
        if set(obj) == {TABLE}:
            t = obj[TABLE]
            common = {k: decode(v) for k, v in t["common"].items()}
            cols = t["columns"]
            rows = []
            if "sparse_rows" in t:
                cells = [[(cols[int(i)], v) for i, v in row.items()] for row in t["sparse_rows"]]
            else:
                cells = [
                    [(c, v) for c, v in zip(cols, row, strict=True) if v != ABSENT]
                    for row in t["rows"]
                ]
            for row in cells:
                pairs = list(common.items()) + [(c, decode(v)) for c, v in row]
                rows.append(_unflatten(pairs) if t["flat"] else dict(pairs))
            return rows
        return {k: decode(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [decode(x) for x in obj]
    return obj
