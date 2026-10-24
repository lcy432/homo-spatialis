"""规划请求的可逆紧凑表示（E3B 映射修正 §7；E3B 收尾 §6：按板块分表、食品组合展开、文字模板与查表）。

目录变大以后，同一类对象（机会、片区、通勤线……）逐条重复写同样的键与同样的取值，提示里大部分是重复。这里只省重复，不删字段、
不改取值、不按任何评分筛选，也不发数据映射公式让模型代算。几种写法（模型直接按表读，系统提示第 7 条有同样的说明）：

* **行表** ``{"$table": {...}}``：一组对象写成一张表。
    - ``columns`` 列名、``rows`` 每行的值（与 columns 逐个对应）；或 ``sparse_rows``：每行 ``{列号: 值}``，只写有的列；或
      ``group`` + ``groups``：按一列分组，每组 ``[这一列的值, [各行]]``。三种按估计长度取最短的。
    - ``common``：每一行都有、取值也完全相同的字段，只写一次；``{"$absent": 1}``：这一行没有这个字段（不是 ``null``）。
    - ``flat`` 为真时列名是字段路径：嵌套对象按“.”展开（``price.amount.amount``）；键里本来就有“.”的对象不展开。
    - ``lookup``：几张查表 ``{"key": 键列, "columns": [...], "values": {键列的值: [这几列的值]}}``，这几列完全由键列决定
      （例如职业代码决定职业名称），每种取值只写一次；按列出的先后查，一张查表也可以带自己的 common、same、lookup。
    - ``same``：``{列: 另一列}``，两列每行都相同，只写后者。
    - ``format``：``{列: 模板}``，这一列的文字 = 模板里的 ``{0}``、``{1}``… 依次换成这一行 ``列#0``、``列#1``… 的值；对不上
      模板的行直接写原文。共同的文案只写一次。
    - ``nested``：``{列: 子表头}``，这一列的值是对象列表，所有对象合成一张子表（共用列名、common、lookup、same），每个对象写一行。
* **分板块** ``{"$sections": {"key": "category", "order": [[板块, 个数], …], "parts": {板块: 这一板块的对象}}}``：
  一列异质对象（住房、岗位、食品……）按板块各用自己的表；原来的顺序是按 order 依次从各板块取对应个数。
* **组合展开** ``{"$grid": {"axis", "values", "rows", "single", "order", "overrides"}}``（食品：36 个基础组合 × 9 个菜系 +
  3 个全自炊方案）：``rows`` 每一行按维度表 ``values`` 逐行展开，把 ``{维度名.列名}`` 换成维度表那一行的值（整段只有一个占位时
  换成原值，保留类型）；``single`` 是不展开的单项；``order`` 是两者的先后；``overrides`` 是逐项例外（某个组合有了自己的报价时
  按编号换掉这些顶层字段，``{"$absent": 1}`` 表示没有这个字段）。合法编号只有展开出来的这些；凑不齐完整菜系的基础组合整组写成
  单项，不补出不存在的选项。
* **编号引用** ``{"$ids": 板块}``：同一个观察对象里这一板块全部机会的编号，按字符排序的列表。

必须满足 ``decode(encode(payload)) == payload``（类型敏感：1、1.0 与 true 不同；缺键与 null 不同）。编码是规范的：列名排序、
候选写法按估计长度取最短、同样长按固定先后，渲染时键也排序，所以同一份载荷（不论键的先后）总是渲染出逐字相同的文字——恢复
与重放靠这一点核对请求正文。每张表与每个组合展开都当场核对能否原样还原，不能就退回原样列表；整份载荷最后再核对一次。载荷里
本来就有以“$”开头的键时拒绝编码（保留给本表示用），不猜。解码器只用于离线核对与 mock，不要求模型调用它。
"""

from __future__ import annotations

import copy
import difflib
import json
import os
import re
from collections import Counter
from typing import Any

FORMAT = "compact-tables-v2"
TABLE = "$table"
SECTIONS = "$sections"
GRID = "$grid"
IDS = "$ids"
ABSENT = {"$absent": 1}

#: 一列文字总长（估计 token）至少这么多才找共同模板，太短的列不值得
_MIN_FORMAT = 200
#: 不改写成模板的列：编号要原样出现在行里，模型照抄
_NO_FORMAT = frozenset({"opportunity_id", "area_id"})
#: 模板里两个空位之间的固定文字至少这么长（估计 token），太短的（一个空格、一个逗号）并进空位，免得模板碎成很多空位
_MIN_FIXED = 2
#: 找文字模板时的切词：连续的英文短语（字母与数字，中间可以有空格与常见标点，例如地名、职业名）各成一段；“.”与“_”
#: 单独成段（编号 chi.area.h.1703101 的共同开头可以提出来）；其余（汉字、中文标点、单独的空白）一字一段
_TOKEN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9 ,&()'/+-]*[A-Za-z0-9)])?|.", re.S)
_SLOT = re.compile(r"\{(\d+)\}")
#: 查表最多几张（每张由一列决定另外几列）
_MAX_LOOKUPS = 3
_ABSENT_COST = len('{"$absent":1}')


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


def _cost(x: Any) -> int:
    """与请求估计器同一口径（ASCII 记 1、其他记 2）的长度，用来在候选写法之间取最短。"""
    s = _canon(x)
    return 2 * len(s) - len(s.encode("ascii", "ignore"))


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


def _unflatten(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for path, v in pairs:
        parts = path.split(".")
        node = out
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = v
    return out


def _category_ids(rows: Any) -> dict[str, list[str]] | None:
    """一列机会（每项都有字符串的 opportunity_id 与 category）→ {板块: 排序后的编号}；不是这种列表就是 None。"""
    if not isinstance(rows, list) or not rows:
        return None
    out: dict[str, list[str]] = {}
    for r in rows:
        if not (
            isinstance(r, dict)
            and isinstance(r.get("opportunity_id"), str)
            and isinstance(r.get("category"), str)
        ):
            return None
        out.setdefault(r["category"], []).append(r["opportunity_id"])
    return {k: sorted(v) for k, v in out.items()}


# ---------------------------------------------------------------------- 行表


def _common_affix(values: list[str]) -> tuple[str, str]:
    pre = os.path.commonprefix(values)
    rest = [v[len(pre) :] for v in values]
    suf = os.path.commonprefix([v[::-1] for v in rest])[::-1]
    return pre, suf


def _tokens(s: str) -> list[str]:
    return _TOKEN.findall(s)


def _merge(skel: list[str | None], toks: list[str]) -> list[str | None]:
    """把模板骨架与一行文字对齐：两边都有的片段留作固定文字，其余位置变成空位（None）。"""
    out: list[str | None] = []
    ai = bi = 0
    matcher = difflib.SequenceMatcher(None, skel, toks, autojunk=False)
    for a0, b0, size in matcher.get_matching_blocks():
        if (a0 > ai or b0 > bi) and (not out or out[-1] is not None):
            out.append(None)
        out.extend(skel[a0 : a0 + size])
        ai, bi = a0 + size, b0 + size
    return out


def _text_cost(s: str) -> int:
    """一段文字（不带引号）的估计长度：ASCII 记 1、其他记 2。"""
    return 2 * len(s) - len(s.encode("ascii", "ignore"))


def _fixed_cost(skel: list[str | None]) -> int:
    return sum(_text_cost(t) for t in skel if t is not None)


def _prune(skel: list[str | None]) -> list[str | None]:
    """两个空位之间太短的固定文字并进空位（开头与结尾的固定文字保留）。"""
    segs: list[list[str] | None] = []
    for t in skel:
        if t is None:
            if not segs or segs[-1] is not None:
                segs.append(None)
        elif segs and segs[-1] is not None:
            segs[-1].append(t)
        else:
            segs.append([t])
    out: list[str | None] = []
    for i, s in enumerate(segs):
        middle = 0 < i < len(segs) - 1
        if s is None or (middle and sum(_text_cost(x) for x in s) < _MIN_FIXED):
            if not out or out[-1] is not None:
                out.append(None)
        else:
            out.extend(s)
    return out


def _parts(skel: list[str | None]) -> list[str]:
    parts = [""]
    for t in skel:
        if t is None:
            parts.append("")
        else:
            parts[-1] += t
    return parts


def _parse(parts: list[str], s: str) -> list[str] | None:
    """按模板的固定文字切出各空位的值（从左到右找第一次出现）；对不上就是 None（这一行写原文）。"""
    first, last = parts[0], parts[-1]
    end = len(s) - len(last)
    if not (s.startswith(first) and s.endswith(last)) or end < len(first):
        return None
    pos, slots = len(first), []
    for f in parts[1:-1]:
        j = s.find(f, pos, end)
        if j < 0:
            return None
        slots.append(s[pos:j])
        pos = j + len(f)
    slots.append(s[pos:end])
    return slots


def _fill(template: str, slots: list[str]) -> str:
    return _SLOT.sub(lambda m: slots[int(m.group(1))], template)


def _text_format(values: list[str]) -> tuple[str, list[list[str] | None]] | None:
    """一列文字的共同模板：固定文字只写一次，空位写成 {0}、{1}…；返回模板与每行各空位的值（对不上模板的行是 None）。"""
    if any(_SLOT.search(v) for v in values):
        return None  # 原文里本来就有 {数字} 的写法时不用模板
    toks = [_tokens(v) for v in values]
    order = sorted(range(len(values)), key=lambda i: (len(toks[i]), i))
    best: tuple[int, str, list[list[str] | None]] | None = None
    # 候选骨架：全部行共有的固定文字；以及从第一行、长度居中的一行出发，跳过与骨架差太多的行（例如完全不同的全自炊
    # 方案，它们写原文）。三个候选按估计长度取最短，同样长取先算出的
    for seed, strict in ((0, False), (0, True), (order[len(order) // 2], True)):
        skel: list[str | None] = list(toks[seed])
        for i, t in enumerate(toks):
            if i != seed:
                merged = _merge(skel, t)
                if not strict or 2 * _fixed_cost(merged) >= _fixed_cost(skel):
                    skel = merged
        parts = _parts(_prune(skel))
        if len(parts) < 2:
            continue
        template = "".join(
            p + (f"{{{i}}}" if i < len(parts) - 1 else "") for i, p in enumerate(parts)
        )
        slots_of: list[list[str] | None] = []
        for v in values:
            slots = _parse(parts, v)
            slots_of.append(slots if slots is not None and _fill(template, slots) == v else None)
        cost = _cost(template) + sum(
            _cost(v) if s is None else sum(_cost(x) + 1 for x in s)
            for v, s in zip(values, slots_of, strict=True)
        )
        if best is None or cost < best[0]:
            best = (cost, template, slots_of)
    return None if best is None else (best[1], best[2])


def _depends(canon: list[dict[str, str]], key: str, col: str) -> bool:
    seen: dict[str, str] = {}
    for r in canon:
        sig = r.get(col, "$absent")
        if seen.setdefault(r[key], sig) != sig:
            return False
    return True


def _layout(cells: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    """一组行（列名 → 已编码的值）的共同部分：每行相同的字段、与另一列完全相同的列、由某一列决定的几列（查表）。
    返回表头里的这几项与仍逐行写的列。查表最多 ``_MAX_LOOKUPS`` 张，每次取估计省得最多的键列（一样多取列名靠前的）。"""
    columns = sorted({k for r in cells for k in r})
    canon = [{k: _canon(v) for k, v in r.items()} for r in cells]
    cost = [{k: 2 * len(s) - len(s.encode("ascii", "ignore")) for k, s in r.items()} for r in canon]
    common = {
        c: cells[0][c]
        for c in columns
        if all(c in r for r in canon) and len({r[c] for r in canon}) == 1
    }
    varying = [c for c in columns if c not in common]
    # 两列在每一行都同有同无、取值相同（例如 quality 模板里的职业名与 attributes.soc_title）：只写前一列
    same: dict[str, str] = {}
    first_of: dict[tuple[str, ...], str] = {}
    for c in varying:
        sig = tuple(r.get(c, "$absent") for r in canon)
        if sig in first_of:
            same[c] = first_of[sig]
        else:
            first_of[sig] = c
    varying = [c for c in varying if c not in same]

    def cc(i: int, c: str) -> int:
        return cost[i].get(c, _ABSENT_COST) + 1

    def rows_cost(cols: list[str]) -> int:
        """逐行写出这些列的估计长度（列对齐与按行写列号取较短的）。"""
        dense = sum(2 + sum(cc(i, c) for c in cols) for i in range(len(cells)))
        sparse = sum(
            2 + sum(len(str(j)) + 4 + cost[i][c] for j, c in enumerate(cols) if c in cost[i])
            for i in range(len(cells))
        )
        return min(dense, sparse)

    lookups: list[dict[str, Any]] = []
    for _ in range(_MAX_LOOKUPS):
        best: tuple[int, str, list[str]] | None = None
        now = rows_cost(varying)
        for key in varying:
            if not all(isinstance(r.get(key), str) for r in cells):
                continue
            first: dict[str, int] = {}
            for i, r in enumerate(cells):
                first.setdefault(r[key], i)
            if len(first) == len(cells):
                continue
            dep = [c for c in varying if c != key and _depends(canon, key, c)]
            if not dep:
                continue
            saved = now - rows_cost([c for c in varying if c not in dep])
            spent = _cost(dep) + _cost(key) + 40
            spent += sum(_cost(k) + 3 + sum(cc(i, c) for c in dep) for k, i in first.items())
            if saved > spent and (best is None or saved - spent > best[0]):
                best = (saved - spent, key, dep)
        if best is None:
            break
        _, key, dep = best
        firsts: dict[str, dict[str, Any]] = {}
        for r in cells:
            firsts.setdefault(r[key], {c: r[c] for c in dep if c in r})
        # 查表自己的各行也是一张小表：其中每行相同的、相同的列与再由某一列决定的几列照样只写一次（例如学历要求决定资格说明）
        sub, cols = _layout(list(firsts.values()))
        if not sub["common"]:
            del sub["common"]
        lk = {"key": key, **sub, "columns": cols}
        lk["values"] = {k: _row(v, cols) for k, v in firsts.items()}
        # 后选的查表放在前面：它的查出列里可能有先选的那张的键列，按列表顺序查就总能先拿到键
        lookups.insert(0, lk)
        varying = [c for c in varying if c not in dep]
    head: dict[str, Any] = {"common": common}
    if same:
        head["same"] = same
    if lookups:
        head["lookup"] = lookups
    return head, varying


def _row(r: dict[str, Any], cols: list[str]) -> list[Any]:
    return [r[c] if c in r else dict(ABSENT) for c in cols]


def _nested_rows(
    values: list[Any], ids: dict[str, list[str]] | None
) -> tuple[dict[str, Any], list[Any]] | None:
    """一列的值都是对象列表：所有对象合起来当一张子表（共用子列名、共同字段与查表），每个对象写成一行。"""
    if not all(isinstance(v, list) for v in values):
        return None
    items = [x for v in values for x in v]
    if not items or not all(isinstance(x, dict) for x in items) or not _flattenable(items):
        return None
    cells = [{k: encode(v, ids) for k, v in _flatten(x)} for x in items]
    head, cols = _layout(cells)
    head["columns"] = cols
    out: list[Any] = []
    pos = 0
    for v in values:
        out.append([_row(r, cols) for r in cells[pos : pos + len(v)]])
        pos += len(v)
    return head, out


def _shape_columns(
    raw: list[dict[str, Any]], cells: list[dict[str, Any]], ids: dict[str, list[str]] | None
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, Any]]:
    """逐列改写：文字列换成模板 + 空位列（``列名#0``、``列名#1``…），对象列表列换成子表的行。只在估计更短时改。"""
    names = {k for r in cells for k in r}
    out = [dict(r) for r in cells]
    formats: dict[str, str] = {}
    nested: dict[str, Any] = {}
    for c in sorted(names):
        idx = [i for i, r in enumerate(raw) if c in r]
        vals = [raw[i][c] for i in idx]
        if len(vals) < 3:
            continue
        plain = sum(_cost(cells[i][c]) for i in idx)
        if all(isinstance(v, str) for v in vals):
            if c in _NO_FORMAT or plain < _MIN_FORMAT:
                continue
            fmt = _text_format(vals)
            if fmt is None:
                continue
            template, slots_of = fmt
            slot_names = [f"{c}#{j}" for j in range(len(_SLOT.findall(template)))]
            if any(n in names for n in slot_names):
                continue
            cost = (
                _cost(template)
                + sum(_cost(n) for n in slot_names)
                + sum(
                    _cost(v) if s is None else sum(_cost(x) + 1 for x in s)
                    for v, s in zip(vals, slots_of, strict=True)
                )
            )
            if cost >= plain:
                continue
            formats[c] = template
            for i, s in zip(idx, slots_of, strict=True):
                if s is not None:
                    del out[i][c]
                    out[i].update(zip(slot_names, s, strict=True))
        else:
            nest = _nested_rows(vals, ids)
            if nest is None:
                continue
            sub, rows = nest
            if _cost(sub) + sum(_cost(r) for r in rows) >= plain:
                continue
            nested[c] = sub
            for i, r in zip(idx, rows, strict=True):
                out[i][c] = r
    return out, formats, nested


def _encode_table(rows: list[dict[str, Any]], ids: dict[str, list[str]] | None) -> dict[str, Any]:
    flat_ok = _flattenable(rows)
    raw = [dict(_flatten(r)) if flat_ok else dict(r) for r in rows]
    encoded = [{k: encode(v, ids) for k, v in r.items()} for r in raw]
    cells, formats, nested = _shape_columns(raw, encoded, ids)
    # 列按名字排序（_layout 里）：同一份载荷不论对象里键的先后，编码都一样（渲染时也按键排序），重新渲染能逐字复现
    head, rest = _layout(cells)
    head = {"flat": flat_ok, **head}
    if formats:
        head["format"] = formats
    if nested:
        head["nested"] = nested
    cost = [{c: _cost(r[c]) if c in r else _ABSENT_COST for c in rest} for r in cells]
    n, m = len(cells), len(rest)
    # 三种写法按估计长度取最短（一样长按 列对齐 → 按行写列号 → 按某一列分组 的先后）
    best: tuple[int, str, str | None] = (
        sum(2 + sum(row.values()) + max(m - 1, 0) for row in cost) + n,
        "rows",
        None,
    )
    sparse = (
        sum(
            2 + sum(len(str(j)) + 4 + row[c] for j, c in enumerate(rest) if c in r)
            for r, row in zip(cells, cost, strict=True)
        )
        + n
    )
    if sparse < best[0]:
        best = (sparse, "sparse_rows", None)
    # 按一列分组：这一列在相邻的行里取值相同（例如通勤线按居住片区排好），每组只写一次
    for g in rest:
        if not all(isinstance(r.get(g), str) for r in cells):
            continue
        runs: list[int] = []
        for i in range(n):
            if i == 0 or cells[i][g] != cells[i - 1][g]:
                runs.append(i)
        if 2 * len(runs) > n:
            continue
        grouped = (
            sum(cost[i][g] + 8 for i in runs)
            + _cost(g)
            + 10
            + sum(2 + sum(row.values()) - row[g] + max(m - 2, 0) for row in cost)
        )
        if grouped < best[0]:
            best = (grouped, "groups", g)
    _, kind, g = best
    if kind == "rows":
        return {TABLE: {**head, "columns": rest, "rows": [_row(r, rest) for r in cells]}}
    if kind == "sparse_rows":
        return {
            TABLE: {
                **head,
                "columns": rest,
                "sparse_rows": [
                    {str(j): r[c] for j, c in enumerate(rest) if c in r} for r in cells
                ],
            }
        }
    assert g is not None
    cols = [c for c in rest if c != g]
    groups: list[list[Any]] = []
    for r in cells:
        if not groups or groups[-1][0] != r[g]:
            groups.append([r[g], []])
        groups[-1][1].append(_row(r, cols))
    return {TABLE: {**head, "columns": cols, "group": g, "groups": groups}}


def _apply_head(head: dict[str, Any], stored: dict[str, Any]) -> dict[str, Any]:
    """一行写出的列补上表头的共同字段、查表（查表自己的表头递归同样补）与相同列。值仍是编码后的样子。"""
    cells = dict(head.get("common", {}))
    cells.update(stored)
    for lk in head.get("lookup", []):
        row = lk["values"][cells[lk["key"]]]
        got = {c: v for c, v in zip(lk["columns"], row, strict=True) if v != ABSENT}
        cells.update(_apply_head(lk, got))
    for c, src in head.get("same", {}).items():
        if src in cells:
            cells[c] = cells[src]
    return cells


def _resolve(
    head: dict[str, Any], stored: list[dict[str, Any]], flat: bool, ids: dict[str, list[str]] | None
) -> list[dict[str, Any]]:
    """表头 + 每行写出的列 → 原来的对象：共同字段、查表、相同列、文字模板、子表依次补回。"""
    formats = head.get("format", {})
    nested = head.get("nested", {})
    out = []
    for r in stored:
        cells = _apply_head(head, r)
        for c, template in formats.items():
            slot_names = [f"{c}#{j}" for j in range(len(_SLOT.findall(template)))]
            if c not in cells and slot_names[0] in cells:
                cells[c] = _fill(template, [cells.pop(s) for s in slot_names])
        values = {}
        for c, v in cells.items():
            if c in nested:
                sub = nested[c]
                rows = [
                    {k: x for k, x in zip(sub["columns"], row, strict=True) if x != ABSENT}
                    for row in v
                ]
                values[c] = _resolve(sub, rows, True, ids)
            else:
                values[c] = decode(v, ids)
        pairs = list(values.items())
        out.append(_unflatten(pairs) if flat else dict(pairs))
    return out


def _decode_table(t: dict[str, Any], ids: dict[str, list[str]] | None) -> list[dict[str, Any]]:
    cols = t["columns"]
    if "sparse_rows" in t:
        stored = [{cols[int(i)]: v for i, v in row.items()} for row in t["sparse_rows"]]
    elif "groups" in t:
        stored = [
            {t["group"]: value} | {c: v for c, v in zip(cols, row, strict=True) if v != ABSENT}
            for value, rows in t["groups"]
            for row in rows
        ]
    else:
        stored = [
            {c: v for c, v in zip(cols, row, strict=True) if v != ABSENT} for row in t["rows"]
        ]
    return _resolve(t, stored, t["flat"], ids)


# ---------------------------------------------------------------------- 组合展开（食品：基础组合 × 菜系）


def _strings(obj: Any) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for k, v in obj.items() for s in (k, *_strings(v))]
    if isinstance(obj, list):
        return [s for v in obj for s in _strings(v)]
    return []


def _keys_with_value(obj: Any, value: str) -> set[str]:
    out: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if v == value and isinstance(v, str):
                out.add(k)
            out |= _keys_with_value(v, value)
    elif isinstance(obj, list):
        for v in obj:
            out |= _keys_with_value(v, value)
    return out


def _set_path(obj: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    for p in parts[:-1]:
        obj = obj[p]
    obj[parts[-1]] = value


def _replace_strings(obj: Any, key: str, label: str | None, axis: str) -> Any:
    if isinstance(obj, str):
        if obj == key:
            return f"{{{axis}.key}}"
        if label is not None and label in obj:
            return obj.replace(label, f"{{{axis}.label}}")
        return obj
    if isinstance(obj, dict):
        return {k: _replace_strings(v, key, label, axis) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_replace_strings(v, key, label, axis) for v in obj]
    return obj


def _expand(obj: Any, axis: str, values: dict[str, Any]) -> Any:
    """把一行基础行里的 {axis.列名} 换成维度表这一行的值：整段就是一个占位时换成原值（保留类型），否则按文字替换。"""
    if isinstance(obj, str):
        pat = re.compile(r"\{" + re.escape(axis) + r"\.([A-Za-z0-9_.]+)\}")
        whole = pat.fullmatch(obj)
        if whole:
            return copy.deepcopy(values[whole.group(1)])

        def text(m: re.Match[str]) -> str:
            v = values[m.group(1)]
            if not isinstance(v, str):
                raise CompactError(f"占位 {m.group(0)} 在文字中间，但取值不是文字")
            return v

        return pat.sub(text, obj)
    if isinstance(obj, dict):
        return {k: _expand(v, axis, values) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand(v, axis, values) for v in obj]
    return obj


def _grid_parts(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """找出“前缀.维度键”的生成关系：连续若干行编号前缀相同、末段依次是同一组维度键。找不到就是 None（退回行表）。"""
    if len(rows) < 4 or not all(
        isinstance(r.get("opportunity_id"), str) and "." in r["opportunity_id"] for r in rows
    ):
        return None
    if len({r["opportunity_id"] for r in rows}) != len(rows):
        return None
    if any("{" in s or "}" in s for r in rows for s in _strings(r)):
        return None  # 原文里有花括号就不用占位，免得与原文混淆
    runs: list[tuple[str, list[int]]] = []
    for i, r in enumerate(rows):
        pre = r["opportunity_id"].rsplit(".", 1)[0]
        if runs and runs[-1][0] == pre:
            runs[-1][1].append(i)
        else:
            runs.append((pre, [i]))

    def suffixes(idx: list[int]) -> tuple[str, ...]:
        return tuple(rows[i]["opportunity_id"].rsplit(".", 1)[1] for i in idx)

    tuples = Counter(suffixes(idx) for _, idx in runs if len(idx) >= 2)
    if not tuples:
        return None
    keys, n = tuples.most_common(1)[0]
    if n < 2 or len(set(keys)) != len(keys):
        return None
    groups = [(pre, idx) for pre, idx in runs if suffixes(idx) == keys]
    grid_runs = {n for n, (_, idx) in enumerate(runs) if suffixes(idx) == keys}
    grid_rows = [(k, rows[i]) for _, idx in groups for k, i in zip(keys, idx, strict=True)]
    names = set.intersection(*(_keys_with_value(r, k) for k, r in grid_rows))
    axis = min(names) if names else "variant"
    if not re.fullmatch(r"[a-z_]+", axis) or axis in {"key", "label"}:
        axis = "variant"
    # 维度的中文名：同一组里各行 label 去掉共同开头与结尾剩下的部分；各组必须一致、互不相同才用
    label_of: dict[str, str] | None = {}
    if not all(isinstance(r.get("label"), str) for _, r in grid_rows):
        label_of = None
    else:
        for _, idx in groups:
            labels = [rows[i]["label"] for i in idx]
            pre, suf = _common_affix(labels)
            for k, lab in zip(keys, labels, strict=True):
                mid = lab[len(pre) : len(lab) - len(suf)]
                if label_of.setdefault(k, mid) != mid:
                    label_of = None
                    break
            if label_of is None:
                break
        if label_of is not None and (
            "" in label_of.values() or len(set(label_of.values())) != len(keys)
        ):
            label_of = None
    # 只随维度变化的字段（例如菜系的餐馆数与证据）：同一维度键在各组里取值相同，不同维度键之间不全相同
    flats = [(k, dict(_flatten(r))) for k, r in grid_rows]
    shared = set.intersection(*(set(f) for _, f in flats)) - {"opportunity_id"}
    axis_cols: list[str] = []
    for p in sorted(shared):
        per_key: dict[str, str] = {}
        if all(per_key.setdefault(k, _canon(f[p])) == _canon(f[p]) for k, f in flats) and (
            len(set(per_key.values())) > 1
        ):
            axis_cols.append(p)
    short = [p.rsplit(".", 1)[-1] for p in axis_cols]
    col_names = [
        s if short.count(s) == 1 and s not in {"key", "label"} else p
        for s, p in zip(short, axis_cols, strict=True)
    ]
    first = dict(flats[: len(keys)])
    values = []
    for k in keys:
        v: dict[str, Any] = {"key": k}
        if label_of is not None:
            v["label"] = label_of[k]
        for p, name in zip(axis_cols, col_names, strict=True):
            v[name] = first[k][p]
        values.append(v)
    return {
        "axis": axis,
        "keys": keys,
        "runs": runs,
        "grid_runs": grid_runs,
        "label_of": label_of,
        "axis_cols": list(zip(axis_cols, col_names, strict=True)),
        "values": values,
    }


def _encode_grid(rows: list[dict[str, Any]], ids: dict[str, list[str]] | None) -> Any:
    g = _grid_parts(rows)
    if g is None:
        return None
    axis, keys = g["axis"], g["keys"]
    vrow = {v["key"]: v for v in g["values"]}

    def template(pre: str, k: str, row: dict[str, Any]) -> dict[str, Any]:
        t = copy.deepcopy(row)
        for p, name in g["axis_cols"]:
            _set_path(t, p, f"{{{axis}.{name}}}")
        label = g["label_of"][k] if g["label_of"] is not None else None
        t = _replace_strings(t, k, label, axis)
        t["opportunity_id"] = f"{pre}.{{{axis}.key}}"
        return t

    base: list[dict[str, Any]] = []
    single: list[dict[str, Any]] = []
    order: list[list[Any]] = []
    overrides: dict[str, dict[str, Any]] = {}

    def note(kind: str, n: int) -> None:
        if order and order[-1][0] == kind:
            order[-1][1] += n
        else:
            order.append([kind, n])

    for n, (pre, idx) in enumerate(g["runs"]):
        if n not in g["grid_runs"]:
            single.extend(rows[i] for i in idx)
            note("single", len(idx))
            continue
        note("rows", 1)
        forms = [template(pre, k, rows[i]) for k, i in zip(keys, idx, strict=True)]
        # 取多数行共有的样子作基础行（一样多取先出现的）；单独更新过的组合作为例外逐项保存，不被吞掉
        top = Counter(_canon(f) for f in forms).most_common(1)[0][0]
        tpl = next(f for f in forms if _canon(f) == top)
        base.append(tpl)
        for k, i in zip(keys, idx, strict=True):
            got = _expand(tpl, axis, vrow[k])
            if _canon(got) == _canon(rows[i]):
                continue
            diff = {}
            for field in sorted(set(got) | set(rows[i])):
                if field not in rows[i]:
                    diff[field] = dict(ABSENT)
                elif field not in got or _canon(got[field]) != _canon(rows[i][field]):
                    diff[field] = encode(rows[i][field], ids)
            overrides[rows[i]["opportunity_id"]] = diff
    out: dict[str, Any] = {"axis": axis, "values": _encode_rows(g["values"], ids, grid=False)}
    out["rows"] = _encode_rows(base, ids, grid=False)
    if single:
        out["single"] = _encode_rows(single, ids, grid=False)
        out["order"] = order
    if overrides:
        out["overrides"] = overrides
    enc = {GRID: out}
    return enc if _round_trips(enc, rows, ids) else None


def _decode_grid(g: dict[str, Any], ids: dict[str, list[str]] | None) -> list[dict[str, Any]]:
    axis = g["axis"]
    values = decode(g["values"], ids)
    base = decode(g["rows"], ids)
    single = decode(g["single"], ids) if "single" in g else []
    order = g.get("order", [["rows", len(base)]])
    out: list[dict[str, Any]] = []
    pos = {"rows": 0, "single": 0}
    for kind, n in order:
        part = (base if kind == "rows" else single)[pos[kind] : pos[kind] + n]
        if len(part) != n:
            raise CompactError("组合展开的 order 与行数不一致")
        pos[kind] += n
        for r in part:
            if kind == "rows":
                out.extend(_expand(r, axis, v) for v in values)
            else:
                out.append(r)
    if pos != {"rows": len(base), "single": len(single)}:
        raise CompactError("组合展开的 order 与行数不一致")
    overrides = g.get("overrides", {})
    by_id = {r.get("opportunity_id"): r for r in out}
    for oid, diff in overrides.items():
        if oid not in by_id:
            raise CompactError(f"例外 {oid!r} 不是展开出来的编号")
        row = by_id[oid]
        for field, v in diff.items():
            if v == ABSENT:
                row.pop(field, None)
            else:
                row[field] = decode(v, ids)
    return out


# ---------------------------------------------------------------------- 分板块与递归


def _encode_rows(
    rows: list[dict[str, Any]], ids: dict[str, list[str]] | None, *, grid: bool = True
) -> Any:
    """一组对象的写法：有组合展开关系时用组合展开（若不比原样列表长），否则在原样列表与行表里取估计长度最短的
    （同样长取原样列表）。"""
    plain = [encode(x, ids) for x in rows]
    g = _encode_grid(rows, ids) if grid else None
    if g is not None:
        return min([plain, g], key=_cost)
    if len(rows) < 2:
        return plain
    table = _encode_table(rows, ids)
    # 每张表当场核对能否原样还原；万一不能，退回原样列表（只是长一些），不让一张表的问题挡住整份请求
    return min([plain, table], key=_cost) if _round_trips(table, rows, ids) else plain


def _encode_opportunities(rows: list[dict[str, Any]], ids: dict[str, list[str]] | None) -> Any:
    cats = [r["category"] for r in rows]
    if len(set(cats)) < 2:
        return _encode_rows(rows, ids)
    order: list[list[Any]] = []
    for c in cats:
        if order and order[-1][0] == c:
            order[-1][1] += 1
        else:
            order.append([c, 1])
    parts = {
        c: _encode_rows([r for r in rows if r["category"] == c], ids) for c in dict.fromkeys(cats)
    }
    # 异质机会一律按板块分表（E3B 收尾 §6），不和“摊成一张大表”比长短
    return {SECTIONS: {"key": "category", "order": order, "parts": parts}}


def encode(obj: Any, ids: dict[str, list[str]] | None = None) -> Any:
    """紧凑表示（递归）。顶层调用前先检查保留键。``ids`` 是外层观察对象里各板块的编号（用于编号引用）。"""
    if isinstance(obj, dict):
        inner = _category_ids(obj.get("opportunities"))
        if inner is None:
            return {k: encode(v, ids) for k, v in obj.items()}
        return {
            k: (_encode_opportunities(v, ids) if k == "opportunities" else encode(v, inner))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        if ids and len(obj) >= 2 and all(isinstance(x, str) for x in obj):
            for cat, lst in ids.items():
                if obj == lst:
                    return {IDS: cat}
        if len(obj) >= 2 and all(isinstance(x, dict) for x in obj):
            return _encode_rows(obj, ids)
        return [encode(x, ids) for x in obj]
    return obj


def encode_payload(payload: dict[str, Any]) -> dict[str, Any]:
    _check_keys(payload)
    out = encode(payload)
    if not _round_trips(out, payload):  # 每次都核对一遍：编码有任何损失就停下，不发出去
        raise CompactError("紧凑表示不能原样还原：拒绝使用")
    return out


def _round_trips(encoded: Any, original: Any, ids: dict[str, list[str]] | None = None) -> bool:
    try:
        return _canon(decode(encoded, ids)) == _canon(original)
    except (CompactError, KeyError, IndexError, TypeError, ValueError):
        return False


def decode(obj: Any, ids: dict[str, list[str]] | None = None) -> Any:
    if isinstance(obj, dict):
        if set(obj) == {TABLE}:
            return _decode_table(obj[TABLE], ids)
        if set(obj) == {GRID}:
            return _decode_grid(obj[GRID], ids)
        if set(obj) == {IDS}:
            if not ids or obj[IDS] not in ids:
                raise CompactError(f"编号引用 {obj[IDS]!r} 找不到对应板块")
            return list(ids[obj[IDS]])
        if set(obj) == {SECTIONS}:
            s = obj[SECTIONS]
            parts = {c: decode(v, ids) for c, v in s["parts"].items()}
            pos = dict.fromkeys(parts, 0)
            out = []
            for c, n in s["order"]:
                out.extend(parts[c][pos[c] : pos[c] + n])
                pos[c] += n
            if any(pos[c] != len(parts[c]) for c in parts):
                raise CompactError("分板块的个数与 order 不一致")
            return out
        if "opportunities" in obj:
            opps = decode(obj["opportunities"], ids)
            inner = _category_ids(opps)
            inner = ids if inner is None else inner
            return {k: (opps if k == "opportunities" else decode(v, inner)) for k, v in obj.items()}
        return {k: decode(v, ids) for k, v in obj.items()}
    if isinstance(obj, list):
        return [decode(x, ids) for x in obj]
    return obj
