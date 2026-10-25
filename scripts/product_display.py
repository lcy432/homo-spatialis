"""显示附层的资源工具（``product.display``，``e3-display-v1``）。

用法（仓库根目录）::

    uv run python scripts/product_display.py check
        核对代码里的显示文字与资源：字面写在代码里的 ``t("key", "中文模板", …)`` 与服务层的表格，每个 key 在
        ``configs/product/display/templates/`` 里都要有、中文副本要逐字相同（不同就是译文过时）；有问题退出码 1。
    uv run python scripts/product_display.py scaffold [--prefix <key 前缀>]
        列出还没有进资源的字面 key（中文照代码、英文留空），贴进模板文件后再写英文。
    uv run python scripts/product_display.py freeze
        按两个资料包（合成与 E3B）冻结当前资源、登记为当前版本，并导出给 E5 随包携带的固定双语错误目录
        （``contracts/product/display/v1/service-errors.json``）。冻结目录不可覆盖：内容没变就是同一个版本。
    uv run python scripts/product_display.py coverage <summary.json> [<summary.json> …] --out <报告.md>
        把离线联调（``scripts/product_service.py e2e``）记下的显示读取汇总成覆盖报告：每种对象应展示的文字位置、英文已提供、
        缺失与原因（按 JSON Pointer 模式）；用户原文不在分母里。
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

PACKS = ("configs/product/e3a_synthetic_pack.yaml", "configs/product/e3b_us_v1_pack.yaml")
EXPORT = REPO / "contracts/product/display/v1"


def literal_calls(root: Path = REPO / "src") -> dict[str, set[str]]:
    """代码里字面写着的 ``t("key", "中文模板")``：key → 中文模板集合（同一个 key 只能有一种中文）。"""
    out: dict[str, set[str]] = {}
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or len(node.args) < 2:
                continue
            name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else getattr(node.func, "attr", None)
            )
            if name not in ("t", "Sub"):
                continue
            key, zh = node.args[0], node.args[1]
            if (
                isinstance(key, ast.Constant)
                and isinstance(zh, ast.Constant)
                and isinstance(key.value, str)
            ):
                out.setdefault(key.value, set()).add(zh.value)
    return out


def table_calls() -> dict[str, set[str]]:
    """服务层按表格生成的 key（错误、说明、阶段、删除后保留的记录）。"""
    from product.service import explain as ex
    from product.service.actions import KEPT_AFTER_DELETE
    from product.service.errors import ERRORS, EXPIRED_SUBJECTS
    from product.service.views import STAGE_ZH

    out: dict[str, str] = {}
    for code, (_s, a, b, c) in ERRORS.items():
        out |= {f"error.{code}.message": a, f"error.{code}.impact": b, f"error.{code}.next": c}
    for sub, (a, b, c) in EXPIRED_SUBJECTS.items():
        out |= {
            f"error.expired.{sub}.message": a,
            f"error.expired.{sub}.impact": b,
            f"error.expired.{sub}.next": c,
        }
    for prefix, table in (
        ("explain.reason", ex.REASONS),
        ("explain.after_impact", ex._AFTER_IMPACT),
        ("explain.before_impact", ex._BEFORE_IMPACT),
        ("explain.before_next", ex._BEFORE_NEXT),
        ("explain.before_lead", ex._BEFORE_LEAD),
        ("explain.after_next", ex._AFTER_NEXT),
        ("stage", STAGE_ZH),
    ):
        out |= {f"{prefix}.{k}": v for k, v in table.items()}
    out |= dict(KEPT_AFTER_DELETE)
    return {k: {v} for k, v in out.items()}


def problems() -> tuple[list[str], list[tuple[str, str]]]:
    from product.display.catalog import load_templates

    templates = load_templates()
    used = literal_calls()
    for k, v in table_calls().items():
        used.setdefault(k, set()).update(v)
    out: list[str] = []
    missing: list[tuple[str, str]] = []
    for key, zhs in sorted(used.items()):
        if len(zhs) > 1:
            out.append(f"{key}：代码里有 {len(zhs)} 种不同的中文模板（一个 key 只能对应一种）")
            continue
        zh = next(iter(zhs))
        entry = templates.get(key)
        if entry is None:
            missing.append((key, zh))
        elif entry["zh"] != zh:
            out.append(f"{key}：资源里的中文副本与代码不同（译文过时）")
        elif entry["en"] is None:
            missing.append((key, zh))
    return out, missing


def cmd_check(_: argparse.Namespace) -> int:
    bad, missing = problems()
    for line in bad:
        print("✗", line)
    for key, _zh in missing:
        print("· 缺英文", key)
    print(f"显示文字核对：{len(bad)} 处不一致，{len(missing)} 个 key 还没有英文")
    return 1 if bad else 0


def cmd_scaffold(args: argparse.Namespace) -> int:
    import yaml

    _, missing = problems()
    rows = {k: {"zh": zh, "en": None} for k, zh in missing if k.startswith(args.prefix or "")}
    print(yaml.safe_dump({"templates": rows}, allow_unicode=True, sort_keys=True, width=1000))
    return 0


def cmd_freeze(_: argparse.Namespace) -> int:
    from product.display.catalog import freeze, register
    from product.pack import load_pack

    for rel in PACKS:
        pack = load_pack(REPO / rel)
        version = freeze(pack.fingerprint, pack.body.pack_id)
        register(pack.fingerprint, pack.body.pack_id, version)
        print(f"{pack.body.pack_id} {pack.fingerprint[:12]}… → {version}")
    export_errors()
    return 0


def export_errors() -> None:
    EXPORT.mkdir(parents=True, exist_ok=True)
    (EXPORT / "service-errors.json").write_text(
        error_catalog_text(), encoding="utf-8", newline="\n"
    )


def error_catalog_text() -> str:
    """固定、不含私人内容的错误说明（中英），E5 可以随包携带，在未登录或会话失效时显示。"""
    from product.display.catalog import load_templates
    from product.service.errors import ERRORS, EXPIRED_SUBJECTS

    templates = load_templates()

    def entry(base: str) -> dict[str, dict[str, str | None]]:
        parts = {p: templates.get(f"{base}.{p}") or {} for p in ("message", "impact", "next")}
        return {loc: {p: parts[p].get(loc) for p in parts} for loc in ("zh", "en")}

    body = {
        "display_schema_version": "e3-display-v1",
        "note": "固定双语错误目录（E3C 维护业务含义）。动态业务问题以服务回复里的显示附层为准，不要从中文反查",
        "errors": {
            code: {"status": ERRORS[code][0], **entry(f"error.{code}")} for code in sorted(ERRORS)
        },
        "expired_subjects": {s: entry(f"error.expired.{s}") for s in sorted(EXPIRED_SUBJECTS)},
    }
    return json.dumps(body, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def cmd_coverage(args: argparse.Namespace) -> int:
    lines = [
        "# 显示覆盖报告（e3-display-v1）",
        "",
        "由 `scripts/product_display.py coverage` 从离线联调的 `summary.json` 生成。应展示 = 回复里含中文、不是用户原文的文字位置"
        "（用户原文不翻译，不在分母里）；中文逐字照原文，全部提供。缺失原因见 `product.display.render`。",
    ]
    for path in args.summaries:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        disp = data.get("display")
        if disp is None:
            lines += ["", f"## {Path(path).parent.name}", "", "（这次联调没有走到显示读取）"]
            continue
        ident = data.get("data_identity", {})
        settings = data.get("settings", {})
        lines += [
            "",
            f"## {Path(path).parent.name}",
            "",
            f"资料包 `{ident.get('pack_id')}`（`{str(ident.get('pack_fingerprint'))[:12]}…`），"
            f"{len(settings.get('cities', []))} 城 × 每城 {settings.get('clones_per_city')} 个分身 × "
            f"{settings.get('years')} 年，规划器 {settings.get('planner_kind')}。",
            "",
            "| 对象 | 中文 | 英文 | 应展示 | 英文已提供 | 英文缺失（原因：个数） | source 两种语言相同 | source 就是业务回复 |",
            "|---|---|---|---:|---:|---|---|---|",
        ]
        total = provided = 0
        reasons: dict[str, int] = {}
        patterns: dict[str, dict[str, int]] = {}
        for kind, k in disp["kinds"].items():
            total += k["displayable"]
            provided += k["en_provided"]
            for r, n in k["en_missing_by_reason"].items():
                reasons[r] = reasons.get(r, 0) + n
            for pat, rs in k["en_missing_by_pattern"].items():
                slot = patterns.setdefault(f"{kind} {pat}", {})
                for r, n in rs.items():
                    slot[r] = slot.get(r, 0) + n
            missing = "、".join(f"{r}：{n}" for r, n in k["en_missing_by_reason"].items()) or "—"
            lines.append(
                f"| {kind} | {k['zh_status']} | {k['en_status']} | {k['displayable']} | "
                f"{k['en_provided']} | {missing} | {'是' if k['same_source_both_locales'] else '否'} | "
                f"{'是' if k['source_is_business_reply'] else '否'} |"
            )
        share = f"{provided / total:.1%}" if total else "—"
        lines += [
            "",
            f"合计：应展示 {total}，英文已提供 {provided}（{share}）；缺失 "
            + ("、".join(f"{r} {n}" for r, n in sorted(reasons.items())) or "0")
            + "。",
        ]
        if patterns:
            lines += ["", "| 缺英文的位置（对象 + JSON Pointer 模式） | 原因：个数 |", "|---|---|"]
            for pat, rs in sorted(patterns.items()):
                lines.append(
                    f"| `{pat}` | " + "、".join(f"{r}：{n}" for r, n in sorted(rs.items())) + " |"
                )
        geo = disp.get("geography", {})
        err = disp.get("unauthenticated_error", {})
        lines += [
            "",
            f"地理清单：HTTP {geo.get('http')}，status {geo.get('status')}，城市 {geo.get('cities')}，"
            f"锚点 {geo.get('anchor_status')}。未登录错误：中文 {err.get('zh_status')}，英文 {err.get('en_status')}。",
        ]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"已写 {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sc = sub.add_parser("scaffold")
    sc.add_argument("--prefix")
    sub.add_parser("freeze")
    cov = sub.add_parser("coverage")
    cov.add_argument("summaries", nargs="+")
    cov.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    return {
        "check": cmd_check,
        "scaffold": cmd_scaffold,
        "freeze": cmd_freeze,
        "coverage": cmd_coverage,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
