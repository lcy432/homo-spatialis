"""E3B 物品与职业一侧的编译与冻结：切片 + 两份配置 → 每城一份 JSON（机会、诊断、计数、内容身份）与一份清单。

每项机会都先过 ``LifeOpportunity`` 契约，再核对目录层会查的几条（金额精确到分、小时最多两位小数、词表、替代的家务、编号不重复）；
任何一条不过就停下报错，不写半成品。内容身份只由内容决定（规范 JSON 的 SHA-256），不含生成时间与本机路径。
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from common.life_contracts import (
    RESOURCE_INPUT_DECIMALS,
    LifeOpportunity,
    canonical_fingerprint,
    exact_minor_units,
    has_decimals,
)
from ledger.life_catalog import internal_leak_problems
from product.realdata.life_items import (
    ITEMS_VERSION,
    STATUSES,
    Ctx,
    Item,
    Report,
    chicago_programs,
    contexts,
    device_failure,
    devices,
    equipment,
    food_check,
    food_plans,
    services,
    template_activities,
)
from product.realdata.occupations import OCCUPATIONS_VERSION, Scope, jobs, start_jobs, training

FROZEN_SCHEMA = "e3b-life-items-frozen-v1"


class FreezeError(ValueError):
    pass


def item_problems(o: LifeOpportunity, vocab: dict[str, list[str]]) -> list[str]:
    """目录层会查、契约本身不查的几条：精度、词表、替代的家务、评分侧参数不进生活事实。"""
    oid = o.opportunity_id
    out: list[str] = internal_leak_problems(oid, o)
    for q in (o.price, o.compensation):
        if q is not None and exact_minor_units(q.amount.amount, 2) is None:
            out.append(f"{oid}: 金额 {q.amount.amount!r} 不是整数分")
    r = o.resource
    numbers = [r.hours, r.burden_per_hour, *(x.residual_hours_per_month for x in r.substitutes)]
    if r.job is not None:
        numbers += [r.job.work_hours_per_month, r.job.burden_per_work_hour]
    if o.food_plan is not None:
        numbers += [o.food_plan.prep_hours_per_month, o.food_plan.dining_hours_per_month]
    if o.activity is not None:
        numbers += [o.activity.participation_hours, o.activity.overhead_hours]
    for x in numbers:
        if x is not None and not has_decimals(x, RESOURCE_INPUT_DECIMALS):
            out.append(f"{oid}: {x!r} 细于 {RESOURCE_INPUT_DECIMALS} 位小数")

    def need(keys: list[str], name: str) -> None:
        for k in keys:
            if k not in vocab[name]:
                out.append(f"{oid}: {name} {k!r} 不在词表里")

    if o.food_plan is not None:
        need([p.cuisine for p in o.food_plan.parts if p.cuisine], "cuisines")
    if o.activity is not None:
        need([o.activity.primary_category, *o.activity.tags], "leisure_categories")
        need([*o.activity.requires, *o.activity.kit_provides], "capabilities")
    if o.equipment is not None:
        need(list(o.equipment.provides), "capabilities")
    need([e.function for e in o.enjoyment], "enjoyment_functions")
    need([x.task for x in r.substitutes], "household_tasks")
    return out


def compile_city(
    ctx: Ctx, occ: dict[str, Any], baskets: dict[str, Any], scope: Scope
) -> tuple[list[Item], Report, dict[str, Any]]:
    report = Report()
    job_items = jobs(ctx, occ, scope, report)
    items = [
        *food_plans(ctx, baskets, report),
        *services(ctx, report),
        *devices(ctx, report),
        *equipment(ctx),
        *template_activities(ctx, report),
        *chicago_programs(ctx, report),
        *job_items,
        *training(ctx, occ, scope, report),
    ]
    ids = Counter(i.opportunity.opportunity_id for i in items)
    dup = sorted(k for k, n in ids.items() if n > 1)
    if dup:
        raise FreezeError(f"{ctx.cbsa}: 机会编号重复 {dup[:5]}")
    problems = [p for i in items for p in item_problems(i.opportunity, ctx.cfg["vocabulary"])]
    bad = [i.status for i in items if i.status not in STATUSES]
    if problems or bad:
        raise FreezeError(f"{ctx.cbsa}: {(problems + bad)[:8]}")
    return items, report, start_jobs(ctx, occ, scope, job_items)


def _cheapest_food(items: list[Item]) -> dict[str, Any] | None:
    """最低食品方案的候选：可得的完整食品方案里月费最低的一个（E1 要求最低方案就是合格目录里最便宜的）。"""
    plans = [
        i.opportunity
        for i in items
        if i.opportunity.food_plan is not None and i.opportunity.price is not None
    ]
    if not plans:
        return None
    o = min(plans, key=lambda x: (x.price.amount.amount, x.opportunity_id))  # type: ignore[union-attr]
    assert o.price is not None
    return {"opportunity_id": o.opportunity_id, "monthly": o.price.amount.amount}


def counts(ctx: Ctx, items: list[Item], report: Report) -> dict[str, Any]:
    by_domain: dict[str, Counter[str]] = {}
    for i in items:
        by_domain.setdefault(i.domain, Counter())[i.status] += 1
    reported: Counter[str] = Counter()
    for e in report.entries:
        reported[e["kind"]] += e["count"]
    m = ctx.metro
    return {
        "raw_inputs": {
            "intake_records_referenced": len({r for i in items for r in i.records}),
            "food_item_prices": len(m["food"]["items"]),
            "osm_restaurants": m["restaurants"]["restaurant_total"],
            "osm_venues": sum(g["count"] for g in m["venues"]["groups"]),
            "oews_occupations": len(m["wages"]),
            "chicago_programs": len(m["programs"]),
            "ipeds_programs": len(m["training"]),
            "energy_star_us_models": len(ctx.slice["appliances"]),
        },
        "generated": {d: sum(c.values()) for d, c in sorted(by_domain.items())},
        "by_status": {s: sum(c[s] for c in by_domain.values()) for s in STATUSES},
        "by_domain_status": {d: dict(sorted(c.items())) for d, c in sorted(by_domain.items())},
        "not_generated": dict(sorted(reported.items())),
    }


def _assumption_registry(*cfgs: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for cfg in cfgs:
        for a in cfg["assumptions"]:
            out.append({**a, "config": cfg["version"]})
    return out


def freeze(
    sl: dict[str, Any],
    items_cfg: dict[str, Any],
    occ_cfg: dict[str, Any],
    baskets: dict[str, Any],
    out_dir: Path | None = None,
) -> dict[str, Any]:
    """编译六城并（给了 ``out_dir`` 时）写出冻结文件。返回清单。"""
    # YAML 把日期读成 date 对象：先规范成 JSON 能写的形状，内容身份与输出都按它算
    items_cfg, occ_cfg, baskets = (
        json.loads(json.dumps(x, default=str)) for x in (items_cfg, occ_cfg, baskets)
    )
    if sl["intake_build_id"] != items_cfg["intake_build_id"]:
        raise FreezeError("切片的 intake 构建身份与配置不同")
    scope = Scope(occ_cfg, sl)
    if scope.problems():
        raise FreezeError(f"职业配置与 EP 对不上：{scope.problems()}")
    used = {a for cfg in (items_cfg, occ_cfg) for a in (x["id"] for x in cfg["assumptions"])}
    used |= {"food_basket_quantities_v1", items_cfg["activities"]["burden_assumption"]}
    abilities = [
        a.model_dump(mode="json")
        for a in scope.abilities(occ_cfg["ep_observed_on"], items_cfg["mapping_version"])
    ]
    identities = {
        "items_config": canonical_fingerprint(items_cfg),
        "occupations_config": canonical_fingerprint(occ_cfg),
        "baskets": canonical_fingerprint(baskets),
        "slice": canonical_fingerprint(sl),
    }
    manifest: dict[str, Any] = {
        "schema": FROZEN_SCHEMA,
        "versions": {"items": ITEMS_VERSION, "occupations": OCCUPATIONS_VERSION},
        "intake_build_id": sl["intake_build_id"],
        "raw_sha256": sl["raw"],
        "identities": identities,
        "numeraire": items_cfg["numeraire"],
        "vocabulary": items_cfg["vocabulary"],
        "abilities": abilities,
        "device_failure": device_failure(items_cfg),
        "atus_magnitude_check": sl["atus"],
        "assumptions": _assumption_registry(items_cfg, occ_cfg),
        "cities": {},
    }
    files: dict[str, dict[str, Any]] = {}
    attribute_keys: set[str] = set()
    for ctx in contexts(items_cfg, sl):
        items, report, starts = compile_city(ctx, occ_cfg, baskets, scope)
        missing = sorted({a for i in items for a in i.assumptions} - used)
        if missing:
            raise FreezeError(f"{ctx.cbsa}: 机会引用了没有登记的设定 {missing}")
        main = [
            i.as_dict()
            for i in items
            if not i.opportunity.opportunity_id.startswith(f"activity.{ctx.cbsa}.cpd.")
        ]
        programs = [
            i.as_dict()
            for i in items
            if i.opportunity.opportunity_id.startswith(f"activity.{ctx.cbsa}.cpd.")
        ]
        body = {
            "schema": FROZEN_SCHEMA,
            "cbsa": ctx.cbsa,
            "label": ctx.label,
            "intake_build_id": sl["intake_build_id"],
            "identities": identities,
            "counts": counts(ctx, items, report),
            "food_check": food_check(ctx, baskets),
            "start_jobs": starts,
            "minimum_food_candidate": _cheapest_food(items),
            "not_generated": report.entries,
            "items": main,
        }
        content = canonical_fingerprint(
            {k: v for k, v in body.items() if k != "identities"} | {"programs": programs}
        )
        body = {"content_identity": content, **body}
        files[f"{ctx.cbsa}.json"] = body
        if programs:
            files[f"{ctx.cbsa}.programs.json"] = {
                "schema": FROZEN_SCHEMA,
                "cbsa": ctx.cbsa,
                "content_identity": content,
                "note_zh": "芝加哥公园管理局的真实活动：日期、年龄、季节与报名费原样保留；E1 有日期窗口、整期报名与年龄资格之前不可执行",
                "items": programs,
            }
        manifest["cities"][ctx.cbsa] = {"content_identity": content, "counts": body["counts"]}
        attribute_keys |= {k for i in items for k in i.opportunity.attributes}
    # 目录要求属性键在生活输入里声明（``LifeBundle.attribute_keys``）：这里列出用到的全部键，整合时一起声明
    manifest["vocabulary"] = {**manifest["vocabulary"], "attribute_keys": sorted(attribute_keys)}
    manifest["content_identity"] = canonical_fingerprint(
        {k: v for k, v in manifest.items() if k != "content_identity"}
    )
    if out_dir is not None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for name, body in {**files, "manifest.json": manifest}.items():
            (out_dir / name).write_text(_text(name, body) + "\n", encoding="utf-8", newline="\n")
    return manifest


def _text(name: str, body: dict[str, Any]) -> str:
    """芝加哥活动一千多条：紧凑写法、一条一行；其余缩进便于审阅。"""
    if not name.endswith(".programs.json"):
        return json.dumps(body, ensure_ascii=False, indent=1)
    head = json.dumps({k: v for k, v in body.items() if k != "items"}, ensure_ascii=False)
    lines = [json.dumps(i, ensure_ascii=False, separators=(",", ":")) for i in body["items"]]
    return head[:-1] + ', "items": [\n' + ",\n".join(lines) + "\n]}"
