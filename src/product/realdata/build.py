"""资料构建的各阶段（业务函数，可以脱离 LangGraph 直接调用与测试）。

    配置 → 获取或读缓存 → 标准化 → A/B 映射 → 目录与菜单 → 检查 → 冻结

* 每个阶段的产物放在 ``<工作目录>/<阶段>/<身份前 16 位>/``，写完最后才写 ``done.json``。身份只由输入的身份、方法版本与配置决定；
  已有 ``done.json`` 的阶段直接复用（中断后恢复不重做已完成的获取与映射），只建了一半的目录删掉重做。资料或方法变化 → 新身份。
* 获取阶段默认离线（只读缓存，不联网）；联网只在显式的获取命令里（``scripts/intake_data.py fetch``）。
* 冻结之后查询不重新拟合、不重新抽样。
"""

from __future__ import annotations

import gzip
import json
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from common.intake_contracts import (
    INTAKE_SCHEMA_VERSION,
    AssumptionSpec,
    FetchRecord,
    Menu,
    MenuPolicy,
    MethodSpec,
    Opportunity,
    StdRecord,
    applies_to,
    canonical_json,
    content_identity,
)
from product.realdata import (
    acs,
    activities,
    background,
    e1_export,
    food,
    geo,
    goods,
    housing,
    jobs,
    mapping_a,
    mapping_b,
    menus,
    prices,
    sources,
    training,
)
from product.realdata.config import BuildConfig
from product.realdata.fetch import DEFAULT_CACHE, Mode, fetch

#: 改了获取阶段的记录方式（例如没取得的来源怎样说明原因）就升这个版本。
FETCH_VERSION = "2"
#: 改了标准化的语义（字段、过滤、文字清理）就升这个版本：否则同一身份会复用旧产物。
#: 4 = 官方顶码表、家电适用市场、活动年龄限制、标准化读取绑定获取阶段的原始指纹。
#: 5 = 住房记录加厨卫条件（KIT、PLM）、毛租金是否受水电燃料顶码影响、上网与通信设备字段（E3B 映射修正）。
#: 6 = BLS 就业预测的入职要求成为全国记录（``jobs.ep.<soc>``），不再只是一份旁路 JSON。
#: 7 = EP 表里写 "None"（不需要）的格子照原文保留，不再被 pandas 读成缺失、写成 "nan"。
STANDARDIZE_VERSION = "7"
#: 改了映射阶段的语义（机会怎样生成、编译缺口怎样分类、来源分类）就升这个版本。
#: 3 = 中位数与标准误成对、官方顶码下的可识别规则与说明、派生的食材机会、资料缺口与 E1 缺口分开。
#: 4 = B 住房候选换成完整加权经验支持表（housing_rent_support@1），不再压成三个价位；上网与通信设备分布（描述用）。
#: 5 = 不再生成旧的三档岗位候选（``job_opening_candidate``）；岗位候选池与菜单在 E3B 生活资料包里。
MAP_VERSION = "5"
STAGES = ("configure", "fetch", "standardize", "map", "catalog", "check", "freeze")


def pipeline_identity(config: BuildConfig) -> str:
    """配置 + 代码里会改变产物的版本号。任何一项变了，默认流程编号就变，不会把旧流程的冻结结果当成新结果。"""
    from product.realdata import reports

    return content_identity(
        {
            "config": config.identity,
            "schema": INTAKE_SCHEMA_VERSION,
            "fetch": FETCH_VERSION,
            "standardize": STANDARDIZE_VERSION,
            "map": MAP_VERSION,
            "menu_rules": menus.MENU_RULES_VERSION,
            "methods": [m.ref for m in all_methods()],
            "assumptions": [a.assumption_id for a in all_assumptions()],
            "reports": reports.REPORT_VERSION,
            "e1_export": e1_export.EXPORT_VERSION,
        }
    )


def all_methods() -> list[MethodSpec]:
    return [
        prices.METHOD,
        prices.ADJHSG_METHOD,
        housing.CLASSIFY,
        mapping_b.HOUSING_METHOD,
        mapping_b.SUPPORT_METHOD,
        mapping_b.INTERNET_METHOD,
        acs.TOPCODE_METHOD,
        mapping_b.COMMUTE_METHOD,
        mapping_b.JOBS_METHOD,
        mapping_a.HOUSING_GRID,
        food.AP_ANNUAL,
        food.RPP_METHOD,
        food.FOOD_PLAN,
        training.CIP_SOC,
        menus.ALGORITHM,
        activities.PARSE_NOTES,
        *background.HELPER_METHODS,
        e1_export.EXPORT_METHOD,
    ]


def all_assumptions() -> list[AssumptionSpec]:
    return [
        mapping_b.MARKET_PROXY,
        mapping_b.UTILITIES_IMPUTATION,
        mapping_b.ROUNDTRIP,
        food.BASKET,
        e1_export.BURDEN,
    ]


@dataclass(frozen=True)
class StageRef:
    """一个阶段的产物引用：阶段名、内容身份、目录（相对工作目录）、摘要。checkpoint 只存这些，不存整份资料。"""

    stage: str
    identity: str
    path: str
    summary: dict[str, Any] = field(default_factory=dict)

    def as_state(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "identity": self.identity,
            "path": self.path,
            "summary": self.summary,
        }

    @classmethod
    def from_state(cls, raw: dict[str, Any]) -> StageRef:
        return cls(
            stage=raw["stage"],
            identity=raw["identity"],
            path=raw["path"],
            summary=dict(raw.get("summary", {})),
        )


@dataclass
class BuildContext:
    config: BuildConfig
    work: Path
    cache: Path = DEFAULT_CACHE
    mode: Mode = "offline"
    out: Path | None = None
    #: 测试用的故障注入：在某个阶段写完一半时抛出，模拟构建中断。
    fail_after: dict[str, int] = field(default_factory=dict)


class InjectedInterruptError(RuntimeError):
    """测试注入的中断（模拟进程在阶段中途退出）。"""


def _stage_dir(ctx: BuildContext, stage: str, identity: str) -> Path:
    return ctx.work / stage / identity[:16]


def _done(ctx: BuildContext, stage: str, identity: str) -> StageRef | None:
    d = _stage_dir(ctx, stage, identity)
    f = d / "done.json"
    if f.exists():
        doc = json.loads(f.read_text(encoding="utf-8"))
        if doc.get("identity") == identity:
            return StageRef(
                stage, identity, d.relative_to(ctx.work).as_posix(), doc.get("summary", {})
            )
    return None


def load_ref(ctx: BuildContext, stage: str, identity: str) -> StageRef | None:
    """按身份找已完成的阶段产物（读 ``done.json``）；没有或没写完就返回 ``None``。"""
    return _done(ctx, stage, identity)


def _run_stage(
    ctx: BuildContext, stage: str, identity: str, body: Callable[[Path], dict[str, Any]]
) -> StageRef:
    """已完成就复用；否则清掉半成品重做，最后写 done.json。"""
    hit = _done(ctx, stage, identity)
    if hit is not None:
        return StageRef(hit.stage, hit.identity, hit.path, {**hit.summary, "reused": True})
    d = _stage_dir(ctx, stage, identity)
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    summary = body(d)
    (d / "done.json").write_text(
        json.dumps(
            {"stage": stage, "identity": identity, "summary": summary},
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return StageRef(
        stage, identity, d.relative_to(ctx.work).as_posix(), {**summary, "reused": False}
    )


def _maybe_fail(ctx: BuildContext, stage: str, count: int) -> None:
    limit = ctx.fail_after.get(stage)
    if limit is not None and count >= limit:
        raise InjectedInterruptError(f"测试注入：{stage} 阶段在第 {count} 步中断")


def _write_jsonl(path: Path, items: Iterable[Any]) -> int:
    n = 0
    with gzip.open(path, "wt", encoding="utf-8", newline="\n") as f:
        for it in items:
            doc = it.model_dump(mode="json") if hasattr(it, "model_dump") else it
            f.write(canonical_json(doc) + "\n")
            n += 1
    return n


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def _write_json(path: Path, content: Any) -> None:
    path.write_text(
        json.dumps(content, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# =========================================================================== 1 配置


def stage_configure(ctx: BuildContext) -> StageRef:
    methods = [m.model_dump(mode="json") for m in all_methods()]
    assumptions = [a.model_dump(mode="json") for a in all_assumptions()]
    identity = content_identity(
        {
            "schema": INTAKE_SCHEMA_VERSION,
            "config": ctx.config.model_dump(mode="json"),
            "methods": methods,
            "assumptions": assumptions,
        }
    )

    def body(d: Path) -> dict[str, Any]:
        _write_json(d / "config.json", ctx.config.model_dump(mode="json"))
        _write_json(d / "methods.json", methods)
        _write_json(d / "assumptions.json", assumptions)
        return {"metros": [m.cbsa for m in ctx.config.metros], "sources": len(ctx.config.sources)}

    return _run_stage(ctx, "configure", identity, body)


# =========================================================================== 2 获取或读缓存


def _states(ctx: BuildContext) -> list[str]:
    d = fetch(geo.delineation_request(), ctx.cache, ctx.mode)
    if not d.ok:
        return []
    all_geo = geo.parse_delineation(d.content)  # type: ignore[arg-type]
    return sorted(
        {c.state for m in ctx.config.metros if m.cbsa in all_geo for c in all_geo[m.cbsa].counties}
    )


def _last_live_attempts(cache: Path) -> dict[str, dict[str, Any]]:
    """显式获取命令写下的最近一次联网结果（``_fetch_log.json``，运行元数据，不进仓库）。"""
    path = cache / "_fetch_log.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _explain(rec: FetchRecord, last: dict[str, Any] | None) -> FetchRecord:
    """离线读不到缓存时，附上最近一次联网获取为什么没有成功（HTTP 504、被拒……）。获取时间不写进说明：它是运行元数据。"""
    if rec.status == "ok" or not last or last.get("status") == "ok":
        return rec
    reason = " ".join(
        str(x) for x in (last.get("status"), last.get("http_status"), last.get("note")) if x
    )
    return rec.model_copy(update={"note": f"{rec.note}；最近一次联网获取没有成功：{reason}"[:400]})


def stage_fetch(ctx: BuildContext, configure: StageRef) -> StageRef:
    """读缓存（默认离线）。身份 = 每个请求的缓存键 → 状态、原始文件 SHA-256 与没取得的原因（获取时间、是否命中缓存不进身份）。"""
    reqs = sources.all_requests(ctx.config, _states(ctx))
    live = _last_live_attempts(ctx.cache)
    results: dict[str, FetchRecord] = {}
    for name, req in sorted(reqs.items()):
        results[name] = _explain(fetch(req, ctx.cache, ctx.mode).record, live.get(name))
    identity = content_identity(
        {
            "configure": configure.identity,
            "fetch": FETCH_VERSION,
            "raw": {
                k: [v.request_key, v.status, v.raw_sha256, None if v.status == "ok" else v.note]
                for k, v in sorted(results.items())
            },
        }
    )

    def body(d: Path) -> dict[str, Any]:
        _write_json(
            d / "fetch_log.json", {k: v.model_dump(mode="json") for k, v in results.items()}
        )
        ok = sorted(k for k, v in results.items() if v.status == "ok")
        bad = {k: f"{v.status} {v.note}".strip() for k, v in results.items() if v.status != "ok"}
        return {"ok": len(ok), "not_ok": bad}

    return _run_stage(ctx, "fetch", identity, body)


class RawChangedError(RuntimeError):
    """获取阶段记下的原始文件与现在缓存里的不同（缓存被刷新或改动）。不读新内容冒充旧来源：这个构建不能继续，新资料用新构建。"""


def _bound(ctx: BuildContext, name: str, req, log: dict[str, FetchRecord]) -> bytes:
    """按获取阶段记下的指纹读原始文件：请求键与 SHA-256 都要对上，否则拒绝（不悄悄读到另一份内容）。"""
    rec = log.get(name)
    if rec is None or rec.status != "ok" or rec.raw_sha256 is None:
        raise RuntimeError(f"{name} 在获取阶段没有取得，不能读")
    if req.key != rec.request_key:
        raise RawChangedError(f"{name}：请求与获取阶段记下的不同（配置或代码变了）：用新构建")
    r = fetch(req, ctx.cache, "offline")
    if not r.ok:
        raise RawChangedError(
            f"{name}：缓存里读不到获取阶段那份原始文件（{r.record.status}）：用新构建"
        )
    if r.record.raw_sha256 != rec.raw_sha256:
        raise RawChangedError(
            f"{name}：缓存里的内容（SHA-256 {r.record.raw_sha256[:12]}…）不是获取阶段记下的那一份"
            f"（{rec.raw_sha256[:12]}…）：不读新内容冒充旧来源，用新构建"
        )
    return r.content  # type: ignore[return-value]


def _states_from_log(log: dict[str, FetchRecord]) -> list[str]:
    """获取阶段请求过的州（PUMS 请求名里的州代码）：后续阶段不再重读划定文件来推算。"""
    return sorted({n.split(":", 1)[1] for n in log if n.startswith("pums_h:")})


# =========================================================================== 3 标准化


def stage_standardize(ctx: BuildContext, fetched: StageRef) -> StageRef:
    identity = content_identity({"fetch": fetched.identity, "standardize": STANDARDIZE_VERSION})

    def body(d: Path) -> dict[str, Any]:
        cfg = ctx.config
        log = {
            k: FetchRecord.model_validate(v)
            for k, v in _read_json(ctx.work / fetched.path / "fetch_log.json").items()
        }
        sha = {k: v.raw_sha256 for k, v in log.items()}
        summary: dict[str, Any] = {"records": {}, "skipped": {}}
        step = 0

        def ok(name: str) -> bool:
            return log.get(name) is not None and log[name].status == "ok"

        def content(name: str, req) -> bytes:
            return _bound(ctx, name, req, log)

        # ---- 地理（州来自获取阶段的请求，不重读划定文件）
        states = _states_from_log(log)
        reqs = sources.all_requests(cfg, states)
        if not all(ok(n) for n in ("cbsa_delineation", "tract_puma", "acs_geos")):
            raise RuntimeError("地理来源不完整（划定文件、普查区→PUMA、ACS 地理对照）：不能继续")
        geos = geo.parse_acs_geos(content("acs_geos", reqs["acs_geos"]))
        mg = geo.metro_geography(
            cfg.metros,
            content("cbsa_delineation", reqs["cbsa_delineation"]),
            content("tract_puma", reqs["tract_puma"]),
            geo.puma_names_from_geos(geos),
            content("gazetteer", reqs["gazetteer"]) if ok("gazetteer") else None,
        )
        _write_json(d / "geo.json", {c: geo.geo_summary(g) for c, g in mg.items()})
        cpi = prices.parse_cpi(content("cpi", reqs["cpi"]))
        _write_json(
            d / "cpi.json", {k: str(v) for k, v in sorted(cpi.values.items()) if k >= "2023"}
        )

        # ---- 住房与人员（PUMS）
        labels = (
            acs.dictionary_labels(content("pums_dictionary", reqs["pums_dictionary"]))
            if ok("pums_dictionary")
            else {}
        )
        official = (
            acs.parse_topcodes(content("pums_topcodes", reqs["pums_topcodes"]))
            if ok("pums_topcodes")
            else {}
        )
        utility_rules = (
            acs.parse_utility_topcodes(content("pums_topcodes", reqs["pums_topcodes"]))
            if ok("pums_topcodes")
            else {}
        )
        rules: dict[str, acs.TopcodeRule] = {}
        housing_records: list[StdRecord] = []
        h_cache: dict[str, bytes] = {}
        p_cache: dict[str, bytes] = {}
        persons_all = []
        for m in cfg.metros:
            g = mg[m.cbsa]
            parts_h, parts_p = [], []
            for st in g.states:
                if not ok(f"pums_h:{st}") or not ok(f"pums_p:{st}"):
                    summary["skipped"][f"pums:{st}"] = "PUMS 州文件没有取得"
                    continue
                pumas = {p.puma for p in g.pumas if p.state == st}
                h_cache.setdefault(st, content(f"pums_h:{st}", reqs[f"pums_h:{st}"]))
                if st not in rules:
                    rules[st] = official.get(st) or acs.TopcodeRule(
                        st, "missing", *(None, None), *acs.rent_top_values(h_cache[st])
                    )
                p_cache.setdefault(st, content(f"pums_p:{st}", reqs[f"pums_p:{st}"]))
                parts_h.append(acs.read_pums_housing(h_cache[st], pumas, cfg.pums.housing_vars))
                parts_p.append(acs.read_pums_persons(p_cache[st], pumas, cfg.pums.person_vars))
            if not parts_h:
                continue
            hdf = housing.prepare(pd.concat(parts_h, ignore_index=True), g, rules, utility_rules)
            pdf = pd.concat(parts_p, ignore_index=True).assign(cbsa=m.cbsa)
            hdf.to_parquet(d / f"housing_{m.cbsa}.parquet", index=False)
            pdf.to_parquet(d / f"persons_{m.cbsa}.parquet", index=False)
            persons_all.append(pdf)
            housing_records += list(
                housing.records(
                    hdf, g, labels, {st: sha.get(f"pums_h:{st}") for st in g.states}, rules
                )
            )
            step += 1
            _maybe_fail(ctx, "standardize", step)
        summary["records"]["housing"] = _write_jsonl(
            d / "records_housing.jsonl.gz", housing_records
        )
        _write_json(
            d / "topcodes.json",
            {
                "source": "pums_topcodes" if official else None,
                "source_sha256": sha.get("pums_topcodes") if official else None,
                "rules": {st: vars(r) for st, r in sorted(rules.items())},
            },
        )
        summary["topcodes"] = {st: r.status for st, r in sorted(rules.items())}
        del housing_records, h_cache, p_cache

        # ---- 职业
        cbsas = {m.cbsa for m in cfg.metros}
        titles = {m.cbsa: m.label for m in cfg.metros}
        job_records: list[StdRecord] = []
        if ok("oews"):
            factor = cpi.factor(["2024-05"])
            oews = jobs.parse_oews(content("oews", reqs["oews"]), cbsas)
            job_records += jobs.records(oews, factor, sha.get("oews"), titles)
        if ok("ep_education"):
            ep = jobs.parse_ep_education(content("ep_education", reqs["ep_education"]))
            job_records += jobs.ep_records(ep, sha.get("ep_education"))
        if job_records:
            summary["records"]["jobs"] = _write_jsonl(d / "records_jobs.jsonl.gz", job_records)

        # ---- 食品
        ap_reqs = {n.split(":", 1)[1]: r for n, r in reqs.items() if n.startswith("ap:")}
        food_records: list[StdRecord] = []
        if ok("ap:ap.series") and ok("ap:ap.data.0.Current"):
            ap = food.parse_ap(
                content("ap:ap.series", ap_reqs["ap.series"]),
                content("ap:ap.data.0.Current", ap_reqs["ap.data.0.Current"]),
            )
            food_records += list(food.ap_records(ap, sha.get("ap:ap.data.0.Current")))
        osm_counts: dict[str, Any] = {}
        for m in cfg.metros:
            elements, snap, raw = _osm_food_elements(ctx, reqs, log, m)
            osm_counts[m.cbsa] = {"elements": len(elements), "source": raw}
            food_records += list(food.restaurant_records(elements, m, snap, raw))
        summary["osm_food"] = osm_counts
        summary["records"]["food"] = _write_jsonl(d / "records_food.jsonl.gz", food_records)
        del food_records

        # ---- 工业品
        goods_records: list[StdRecord] = []
        for cat in sources.ENERGYSTAR:
            name = f"energystar:{cat}"
            if ok(name):
                goods_records += list(
                    goods.records(json.loads(content(name, reqs[name])), cat, sha.get(name))
                )
            else:
                summary["skipped"][name] = "没有取得"
        summary["records"]["goods"] = _write_jsonl(d / "records_goods.jsonl.gz", goods_records)

        # ---- 活动
        act_records: list[StdRecord] = []
        chi = next((m for m in cfg.metros if m.cbsa == "16980"), None)
        if chi is not None and ok("chicago_activities"):
            act_records += list(
                activities.chicago_records(
                    json.loads(content("chicago_activities", reqs["chicago_activities"])),
                    chi,
                    cpi,
                    sha.get("chicago_activities"),
                )
            )
        for m in cfg.metros:
            name = f"osm_leisure:{m.cbsa}"
            if ok(name):
                elements, snap = food.parse_osm(content(name, reqs[name]))
                act_records += list(activities.venue_records(elements, m, snap, sha.get(name)))
            else:
                summary["skipped"][name] = "没有取得"
        summary["records"]["activities"] = _write_jsonl(
            d / "records_activities.jsonl.gz", act_records
        )

        # ---- 培训
        if ok("ipeds_hd") and ok("ipeds_ic_py") and ok("cip_soc"):
            insts = training.parse_institutions(content("ipeds_hd", reqs["ipeds_hd"]), cbsas)
            progs = training.parse_programs(content("ipeds_ic_py", reqs["ipeds_ic_py"]))
            cip_soc = training.parse_cip_soc(content("cip_soc", reqs["cip_soc"]))
            summary["records"]["training"] = _write_jsonl(
                d / "records_training.jsonl.gz",
                training.records(
                    insts, progs, cpi, cip_soc, sha.get("ipeds_hd"), sha.get("ipeds_ic_py")
                ),
            )

        # ---- 城市背景
        bg: list[StdRecord] = []
        cbsa_geoid = {v["cbsa"]: k for k, v in geos.items() if v["sumlevel"] == "310"}
        tables = {}
        for t in sources.ACS_TABLES:
            if ok(f"acs_table:{t}"):
                tables[t] = acs.read_summary_table(
                    content(f"acs_table:{t}", reqs[f"acs_table:{t}"]), set(cbsa_geoid.values())
                )
        aqi = {
            year: (background.parse_aqi(content(name, reqs[name])), sha.get(name))
            for year, name in ((2025, "aqi"), (2024, "aqi_2024"))
            if ok(name)
        }
        energy = (
            background.parse_ap_energy(
                content("ap:ap.series", ap_reqs["ap.series"]),
                content("ap:ap.data.0.Current", ap_reqs["ap.data.0.Current"]),
                {m.ap_metro_area for m in cfg.metros},
            )
            if ok("ap:ap.series")
            else {}
        )
        rpp = food.parse_rpp(content("bea_rpp", reqs["bea_rpp"])) if ok("bea_rpp") else {}
        _write_json(d / "rpp.json", rpp)
        ce_by_file = {
            r: food.parse_ce(content(f"ce:{r}", reqs[f"ce:{r}"]))
            for r in {m.ce_region_file for m in cfg.metros}
            if ok(f"ce:{r}")
        }
        for m in cfg.metros:
            bg += background.acs_records(
                m,
                tables,
                cbsa_geoid.get(m.cbsa),
                {t: sha.get(f"acs_table:{t}") for t in sources.ACS_TABLES},
            )
            bg.append(background.aqi_record(m, aqi))
            bg.append(background.energy_record(m, energy, sha.get("ap:ap.data.0.Current")))
            bg.append(background.rpp_record(m, rpp, sha.get("bea_rpp")))
            bg.append(
                background.ce_record(
                    m, ce_by_file.get(m.ce_region_file, {}), sha.get(f"ce:{m.ce_region_file}")
                )
            )
            bg.append(background.geography_record(m, mg[m.cbsa], sha.get("cbsa_delineation")))
            bg += background.unavailable_records(m, mg[m.cbsa])
        summary["records"]["background"] = _write_jsonl(d / "records_background.jsonl.gz", bg)
        if not ok("hud_fmr"):
            summary["skipped"]["hud_fmr"] = (
                f"{log['hud_fmr'].status}：{log['hud_fmr'].note}"
                if "hud_fmr" in log
                else "没有请求"
            )
        return summary

    return _run_stage(ctx, "standardize", identity, body)


def _osm_food_elements(
    ctx: BuildContext, reqs: dict, log: dict[str, FetchRecord], m
) -> tuple[list[dict], str | None, str | None]:
    """合并查询优先；合并查询没取得而配置了分开查询的，就合并分开查询的结果（缺哪一类照实记在摘要里）。"""
    name = f"osm_food:{m.cbsa}"
    if log.get(name) is not None and log[name].status == "ok":
        elements, snap = food.parse_osm(_bound(ctx, name, reqs[name], log))
        return elements, snap, log[name].raw_sha256
    elements: list[dict] = []
    snap = None
    shas = []
    for amenity in sources.FOOD_AMENITIES:
        part = f"osm_food_split:{m.cbsa}:{amenity}"
        if log.get(part) is not None and log[part].status == "ok":
            e, s = food.parse_osm(_bound(ctx, part, reqs[part], log))
            elements += e
            snap = snap or s
            shas.append(log[part].raw_sha256)
    return elements, snap, shas[0] if len(shas) == 1 else (content_identity(shas) if shas else None)


# =========================================================================== 4 映射


def _records(path: Path) -> list[StdRecord]:
    return [StdRecord.model_validate(doc) for doc in read_jsonl(path)] if path.exists() else []


def stage_map(ctx: BuildContext, std: StageRef) -> StageRef:
    identity = content_identity(
        {
            "standardize": std.identity,
            "map": MAP_VERSION,
            "methods": [m.ref for m in all_methods()],
            "mapping": ctx.config.mapping,
        }
    )

    def body(d: Path) -> dict[str, Any]:
        cfg = ctx.config
        sd = ctx.work / std.path
        geo_doc = _read_json(sd / "geo.json")
        opps: list[Opportunity] = []
        tables: list[dict[str, Any]] = []
        summary: dict[str, Any] = {"grid_unsupported": {}, "b_levels": {}}
        persons_frames = {
            m.cbsa: pd.read_parquet(sd / f"persons_{m.cbsa}.parquet")
            for m in cfg.metros
            if (sd / f"persons_{m.cbsa}.parquet").exists()
        }
        pooled = (
            pd.concat(persons_frames.values(), ignore_index=True)
            if persons_frames
            else pd.DataFrame()
        )
        bcfg = cfg.mapping["b_housing"]
        step = 0
        for m in cfg.metros:
            hpath = sd / f"housing_{m.cbsa}.parquet"
            if not hpath.exists():
                continue
            mg = _geo_from_summary(geo_doc[m.cbsa], cfg)
            hdf = pd.read_parquet(hpath)
            pdf = persons_frames[m.cbsa]
            com = mapping_b.commute_by_puma(pdf, int(cfg.mapping["b_commute"]["min_unweighted_n"]))
            renter_w = hdf[hdf["full_puma"]].groupby("puma_code")["WGTP"].sum().to_dict()
            tiers = mapping_b.assign_tiers(com, renter_w)
            model = mapping_b.rent_model(hdf, m.cbsa, tiers, bcfg)
            tier_com = mapping_b.commute_by_tier(
                pdf, tiers, int(cfg.mapping["b_commute"]["min_unweighted_n"])
            )
            for cell in model.cells.values():
                tables.append(_cell_table(cell))
            for code, c in com.items():
                tables.append(_commute_table(m.cbsa, code, c, tiers.get(code), "puma"))
            for tier, c in tier_com.items():
                tables.append(_commute_table(m.cbsa, tier, c, tier, "tier"))
            b_opps, support = housing.support_cells(hdf, model, com, mg)
            tables += list(support.values())
            tables.append(mapping_b.internet_access(hdf, m.cbsa))
            points = mapping_a.housing_grid(model, cfg.mapping["a_housing"])
            a_opps = housing.a_opportunities(points, tier_com, mg)
            unsupported = sorted({(p.tier, p.bed, p.struct) for p in points if not p.supported})
            summary["grid_unsupported"][m.cbsa] = [list(u) for u in unsupported]
            levels: dict[str, int] = {}
            for o in b_opps:
                lv = str(o.attributes.get("price_level_used"))
                levels[lv] = levels.get(lv, 0) + 1
            summary["b_levels"][m.cbsa] = levels
            summary.setdefault("b_support_tables", {})[m.cbsa] = len(support)
            opps += b_opps + a_opps
            hours = mapping_b.job_hours(
                pdf, m.cbsa, pooled, int(cfg.mapping["b_jobs"]["min_unweighted_n"])
            )
            for major, h in hours.items():
                tables.append(
                    {
                        "table_id": f"b.jobs.{m.cbsa}.{major}",
                        "method": mapping_b.JOBS_METHOD.ref,
                        "cbsa": m.cbsa,
                        "soc_major": major,
                        "level": h.level,
                        "n": h.n,
                        "median_weekly_hours": h.median_weekly_hours,
                        "wfh_share": h.wfh_share,
                    }
                )
            step += 1
            _maybe_fail(ctx, "map", step)
        # 食品
        food_recs = _records(sd / "records_food.jsonl.gz")
        ap_recs = [r for r in food_recs if r.record_type == "food_item_avg_price"]
        rpp = _read_json(sd / "rpp.json")
        for m in cfg.metros:
            items = food.item_opportunities(ap_recs, m, food.goods_factor(rpp, m.cbsa))
            opps += items
            opps += food.plan_opportunities(items, m, cfg.mapping["a_food"]["baskets"])
        opps += [
            food.restaurant_opportunity(r) for r in food_recs if r.record_type == "restaurant_venue"
        ]
        del food_recs
        opps += [goods.opportunity(r) for r in _records(sd / "records_goods.jsonl.gz")]
        for r in _records(sd / "records_activities.jsonl.gz"):
            opps.append(
                activities.program_opportunity(r)
                if r.record_type == "activity_program"
                else activities.venue_opportunity(r)
            )
        opps += [training.opportunity(r) for r in _records(sd / "records_training.jsonl.gz")]
        opps += [background.opportunity(r) for r in _records(sd / "records_background.jsonl.gz")]
        opps.sort(key=lambda o: o.opportunity_id)
        ids = [o.opportunity_id for o in opps]
        if len(ids) != len(set(ids)):
            dup = sorted({i for i in ids if ids.count(i) > 1})[:5]
            raise RuntimeError(f"机会编号重复：{dup}")
        summary["opportunities"] = _write_jsonl(d / "opportunities.jsonl.gz", opps)
        tables.sort(key=lambda t: t["table_id"])
        summary["tables"] = _write_jsonl(d / "tables.jsonl.gz", tables)
        return summary

    return _run_stage(ctx, "map", identity, body)


def _geo_from_summary(doc: dict[str, Any], cfg: BuildConfig):
    """标准化阶段保存的地理摘要 → 映射要用的最小 MetroGeo（PUMA 名称与是否完整在内）。"""
    from product.realdata.geo import MetroGeo, PumaMembership

    g = MetroGeo(cbsa=doc["cbsa"], title=doc["title"], counties=[])
    g.pumas = [
        PumaMembership(
            state=p["code"][:2],
            puma=p["code"][2:],
            tracts_in_metro=1 if p["full"] else 0,
            tracts_total=1,
            counties=(),
            name=p.get("name"),
        )
        for p in doc.get("pumas", [])
    ]
    return g


def _cell_table(cell: mapping_b.RentCell) -> dict[str, Any]:
    return {
        "table_id": cell.table_id,
        "method": mapping_b.HOUSING_METHOD.ref,
        "cbsa": cell.cbsa,
        "level": cell.level,
        "key": list(cell.key),
        "population": cell.population,
        "n": cell.n,
        "weight_sum": cell.weight_sum,
        "n_eff": round(cell.n_eff, 2),
        "quantiles": cell.quantiles,
        "median_step": cell.median_step,
        "se_median_step": None if cell.se_median_step is None else round(cell.se_median_step, 2),
        "median_interpolated": cell.median_interpolated,
        "se_median_interpolated": None
        if cell.se_median_interpolated is None
        else round(cell.se_median_interpolated, 2),
        "censored_share": round(cell.censored_share, 4),
        "censor_bound_min": cell.censor_bound_min,
        "structure_mix": cell.structure_mix,
        "utilities_median": cell.utilities_median,
        "record_ids": list(cell.record_ids),
    }


def _commute_table(
    cbsa: str, key: str, c: mapping_b.PumaCommute, tier: str | None, level: str
) -> dict[str, Any]:
    prefix = "b.commute" if level == "puma" else "b.commute_tier"
    return {
        "table_id": f"{prefix}.{cbsa}.{key}",
        "method": mapping_b.COMMUTE_METHOD.ref,
        "cbsa": cbsa,
        "level": level,
        "key": key,
        "tier": tier,
        "n_workers": c.n_workers,
        "n_commuters": c.n_commuters,
        "wfh_share": c.wfh_share,
        "median_oneway": c.median_oneway,
        "p25": c.p25,
        "p75": c.p75,
        "supported": c.supported,
    }


# =========================================================================== 5 目录与菜单


def stage_catalog(ctx: BuildContext, mapped: StageRef) -> StageRef:
    identity = content_identity(
        {
            "map": mapped.identity,
            "rules": menus.MENU_RULES_VERSION,
            "menus": ctx.config.menus.model_dump(mode="json"),
            "seed": ctx.config.build.seed,
        }
    )

    def body(d: Path) -> dict[str, Any]:
        cfg = ctx.config
        opps = [
            Opportunity.model_validate(doc)
            for doc in read_jsonl(ctx.work / mapped.path / "opportunities.jsonl.gz")
        ]
        by_key: dict[tuple[str, str], list[Opportunity]] = {}
        for o in opps:
            for m in cfg.metros:
                if applies_to(o, m.cbsa, cfg.build.country):
                    by_key.setdefault((m.cbsa, o.sector), []).append(o)
        out: list[Menu] = []
        for m in cfg.metros:
            for sector, raw_policy in sorted(cfg.menus.policies.items()):
                policy = MenuPolicy(sector=sector, **raw_policy)
                pool = by_key.get((m.cbsa, sector), [])
                for year in cfg.menus.years:
                    for r in range(cfg.menus.env_repeats):
                        out.append(
                            menus.build_menu(
                                build_id=mapped.identity,
                                cbsa=m.cbsa,
                                year=year,
                                sector=sector,
                                env_repeat=r,
                                policy=policy,
                                opportunities=pool,
                                global_seed=cfg.build.seed,
                            )
                        )
        return {"menus": _write_jsonl(d / "menus.jsonl.gz", out)}

    return _run_stage(ctx, "catalog", identity, body)


# =========================================================================== 6 检查 与 7 冻结（在 build_checks / build_freeze 里）


def stage_check(ctx: BuildContext, std: StageRef, mapped: StageRef, catalog: StageRef) -> StageRef:
    from ledger import intake_data
    from product.realdata import reports

    identity = content_identity(
        {
            "catalog": catalog.identity,
            "checks": reports.REPORT_VERSION,
            "e1": e1_export.EXPORT_VERSION,
        }
    )

    def body(d: Path) -> dict[str, Any]:
        problems = intake_data.check_build(
            ctx.work / std.path,
            ctx.work / mapped.path,
            ctx.work / catalog.path,
            source_ids={s.source_id for s in ctx.config.sources},
            metro_cbsas=[m.cbsa for m in ctx.config.metros],
            methods={m.ref for m in all_methods()},
            assumptions={a.assumption_id for a in all_assumptions()},
            country=ctx.config.build.country,
        )
        _write_json(d / "problems.json", problems)
        if problems:
            raise RuntimeError(
                f"跨对象检查没有通过（{len(problems)} 条），见 problems.json：{problems[:3]}"
            )
        rep = reports.build_reports(
            ctx.config, ctx.work / std.path, ctx.work / mapped.path, ctx.work / catalog.path
        )
        for name, content in rep.items():
            _write_json(d / f"{name}.json", content)
        exp = e1_export.export(ctx.work / mapped.path)
        _write_json(d / "e1_export.json", exp)
        return {"problems": 0, "reports": sorted(rep), "e1_compilable": exp["summary"]["compiled"]}

    return _run_stage(ctx, "check", identity, body)


def stage_freeze(ctx: BuildContext, refs: dict[str, StageRef]) -> StageRef:
    from product.realdata import store

    build_id = content_identity({k: v.identity for k, v in sorted(refs.items())})

    def body(d: Path) -> dict[str, Any]:
        db = d / "intake.sqlite"
        conn = store.create(db)
        try:
            cfg = ctx.config
            store.put_meta(
                conn,
                {
                    "build_id": build_id,
                    "schema": INTAKE_SCHEMA_VERSION,
                    "country": cfg.build.country,
                    "config_identity": cfg.identity,
                    **{f"stage_{k}": v.identity for k, v in refs.items()},
                },
            )
            store.put_sources(conn, cfg.sources)
            store.put_fetches(
                conn,
                {
                    k: FetchRecord.model_validate(v)
                    for k, v in _read_json(ctx.work / refs["fetch"].path / "fetch_log.json").items()
                },
            )
            store.put_methods(conn, all_methods(), all_assumptions())
            sd = ctx.work / refs["standardize"].path
            n_rec = 0
            for p in sorted(sd.glob("records_*.jsonl.gz")):
                n_rec += store.put_records(
                    conn, (StdRecord.model_validate(doc) for doc in read_jsonl(p))
                )
            n_tab = store.put_tables(
                conn, read_jsonl(ctx.work / refs["map"].path / "tables.jsonl.gz")
            )
            n_opp = store.put_opportunities(
                conn,
                (
                    Opportunity.model_validate(doc)
                    for doc in read_jsonl(ctx.work / refs["map"].path / "opportunities.jsonl.gz")
                ),
            )
            n_menu = store.put_menus(
                conn,
                (
                    Menu.model_validate(doc)
                    for doc in read_jsonl(ctx.work / refs["catalog"].path / "menus.jsonl.gz")
                ),
            )
            cd = ctx.work / refs["check"].path
            for p in sorted(cd.glob("*.json")):
                if p.name != "done.json":
                    store.put_report(conn, p.stem, _read_json(p))
            conn.commit()
        finally:
            conn.close()
        return {
            "build_id": build_id,
            "records": n_rec,
            "tables": n_tab,
            "opportunities": n_opp,
            "menus": n_menu,
        }

    return _run_stage(ctx, "freeze", build_id, body)


def run_all(ctx: BuildContext, *, stop_after: str | None = None) -> dict[str, StageRef]:
    """不经过 LangGraph、直接按顺序跑全部阶段（测试与对照用）。"""
    refs: dict[str, StageRef] = {}
    refs["configure"] = stage_configure(ctx)
    if stop_after == "configure":
        return refs
    refs["fetch"] = stage_fetch(ctx, refs["configure"])
    if stop_after == "fetch":
        return refs
    refs["standardize"] = stage_standardize(ctx, refs["fetch"])
    if stop_after == "standardize":
        return refs
    refs["map"] = stage_map(ctx, refs["standardize"])
    if stop_after == "map":
        return refs
    refs["catalog"] = stage_catalog(ctx, refs["map"])
    if stop_after == "catalog":
        return refs
    refs["check"] = stage_check(ctx, refs["standardize"], refs["map"], refs["catalog"])
    if stop_after == "check":
        return refs
    refs["freeze"] = stage_freeze(ctx, refs)
    return refs
