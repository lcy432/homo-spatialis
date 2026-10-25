"""运行前检查、资料性质与目录编译的显示文字：``ProductPreflight``（问题、规划器说明、开局摘要、逐城原因、资料
缺口、范围说明、``scope_zh``）、``CityDataNature``（也嵌在 ``ProductResult.data_nature`` 里）、``CatalogCompilation`` 与
``OptionInventoryCoverage``。

* **中文不变。** 按生成公共样例的同一条代码路径重建 ``contracts/product/v3/samples`` 里这些消息，与提交的样例逐字相同（只去掉
  ``meta.sample`` 出处标签）。E3B 只重建一份预检查（旧金山 + 休斯敦，要两分钟左右）：同一个运行世界里六座城的资料性质都能算，
  其中休斯敦与西雅图的两份与 E3B 结果样例里嵌着的相同。
* **绑定。** 记录器只包住预检查本身（与服务的预检查适配器相同）。这些模块生成的每一段要展示的中文都在同一次生成里记下了 key 与
  参数；从别处转抄的文字（E1 的“数据与参数”说明、确认画像里的兴趣范围说明）按同一写法再生成、逐字核对相同才用，所以也有绑定。
* **英文。** 按当前源资源（不冻结）渲染：不过时、不对不上、不歧义；这里负责的模板（``preflight.`` / ``datanature.`` /
  ``catalog.``）与实体种类全部有英文。城市、职业、六维、机会与治疗的名称由输入侧、结果侧的资源提供，这里只要求它们不过时。
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from product import scenarios as sc
from product.answers import confirm_profile, review_draft
from product.catalog import CONSTANT_TEXTS, inventory_coverage
from product.display import bind_json, recording
from product.display.catalog import source_resources
from product.display.render import coverage
from product.display.text import has_cjk, iter_text
from product.fixtures import make_draft
from product.interests import confirmed_scope_lines
from product.pack import load_pack
from product.pack_facts import city_data_nature, scope_notes
from product.preflight import _blocked, _e1_scope, run_preflight

REPO = Path(__file__).resolve().parents[2]
SAMPLES = REPO / "contracts/product/v3/samples"
E3B = Path("configs/product/e3b_us_v1_pack.yaml")
HOU, SEA, SF = "US:CBSA2023:26420", "US:CBSA2023:42660", "US:CBSA2023:41860"
#: 这里负责的模板前缀与实体种类（其余实体种类由输入侧、结果侧负责）
MY_PREFIXES = ("preflight.", "datanature.", "catalog.")
MY_KINDS = {
    "assumption",
    "billing_period",
    "build_category",
    "candidate_field",
    "candidate_kind",
    "common_prior",
    "commute_mode",
    "commute_rule",
    "household_task",
    "menu_category",
    "preflight_text",
    "price_object",
    "safety_input",
    "source_fact",
    "tax_policy",
    "tax_rule",
    "tax_status",
}
#: 渲染时绝不该出现的缺失原因
BAD = {"translation_stale", "source_changed", "ambiguous"}


def _dump(message: Any) -> dict[str, Any]:
    return message.model_dump(mode="json")


def _strip(payload: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(payload))
    if isinstance(out.get("meta"), dict):
        out["meta"].pop("sample", None)
    return out


def _committed(name: str) -> dict[str, Any]:
    return json.loads((SAMPLES / f"{name}.json").read_text(encoding="utf-8"))["payload"]


def _same_as_sample(name: str, message: Any) -> None:
    assert _strip(_dump(message)) == _strip(_committed(name)), name


def _unbound(payload: dict[str, Any], rec: Any) -> set[str]:
    """要展示（含中文）却没有绑定的位置。"""
    bound = {b["pointer"] for b in bind_json(payload, rec, constants=CONSTANT_TEXTS)}
    return {p for p, text in iter_text(payload) if has_cjk(text) and p not in bound}


def _mine_missing(payload: dict[str, Any], rec: Any, res: Any) -> list[str]:
    """这里负责的模板与实体里缺英文、或资源里的中文副本与代码不同的那些。"""
    out: list[str] = []

    def template(key: str, sha: str, params: dict[str, Any]) -> None:
        if key.startswith(MY_PREFIXES):
            tpl = res.template(key)
            if tpl is None or tpl["en"] is None or tpl["zh_sha"] != sha:
                out.append(key)
        for p in params.values():
            param(p)

    def param(p: dict[str, Any]) -> None:
        if p["t"] == "ent" and p["kind"] in MY_KINDS:
            e = res.entity(p["kind"], p["id"], p["field"])
            if e is None or e["zh"] != p["zh"] or e.get("en") is None:
                out.append(f"{p['kind']}/{p['id']}/{p['field']}")
        elif p["t"] == "join":
            for x in p["items"]:
                param(x)
        elif p["t"] == "sub":
            template(p["key"], p["zh_sha"], p["params"])

    for b in bind_json(payload, rec, constants=CONSTANT_TEXTS):
        for cand in b["candidates"]:
            template(cand["key"], cand["zh_sha"], cand["params"])
    return sorted(set(out))


def _english_ok(payload: dict[str, Any], rec: Any, res: Any) -> None:
    cov = coverage(payload, rec, res, constants=CONSTANT_TEXTS)
    assert not BAD & set(cov["missing"]), cov["missing_patterns"]
    assert _mine_missing(payload, rec, res) == []


def _prepare(work: Path, pack_path: Path, name: str, *, closed_loop: bool, **draft_kwargs: Any):
    """与 ``product.scenarios`` 的 ``closed_loop`` / ``preflight_only`` 走到预检查为止的同一段路（同样的草稿编号与出处标签）；
    记录器只包住 ``run_preflight``。之后在同一个运行世界里算全部城市的资料性质与范围说明（另一个记录器，同结果投影）。"""
    work = sc.workspace(work)
    if closed_loop:
        sample = sc.label(
            "offline_run",
            f"product.scenarios.closed_loop({name})",
            "离线闭环实际执行的结果（mock 规划器，零网络）",
        )
        draft_id, draft_sample = "draft.u501", sample
    else:
        sample = sc.label(
            "offline_run",
            f"product.scenarios.preflight_only({name})",
            "预检查的实际输出（没有运行生活）",
        )
        draft_id, draft_sample = f"draft.u501.{name}", None
    with contextlib.chdir(work):
        pack = load_pack(pack_path)
        draft = make_draft(pack, draft_id=draft_id, sample=draft_sample, **draft_kwargs)
        review = review_draft(pack, draft, sample=draft_sample)
        confirmed = confirm_profile(
            pack,
            draft,
            person_ref="u501",
            acknowledged=review.pending_confirmations,
            sample=draft_sample,
        )
        with recording() as rec:
            run = run_preflight(
                pack,
                confirmed,
                Path(f"compiled/{name}"),
                draft=draft,
                repo_root=Path("."),
                sample=sample,
            )
        assert run.world is not None
        cities = tuple(str(c.city) for c in run.world.bundle.catalogs)
        with recording() as rec_nature:
            nature = {ref: city_data_nature(pack, run.world, ref) for ref in cities}
            notes = scope_notes(pack, run.world, cities)
        with recording() as rec_blocked:
            blocked = _blocked(pack, confirmed, [], None).preflight
    return SimpleNamespace(
        pack=pack,
        confirmed=confirmed,
        run=run,
        rec=rec,
        nature=nature,
        notes=notes,
        rec_nature=rec_nature,
        blocked=blocked,
        rec_blocked=rec_blocked,
        res=source_resources(pack.fingerprint, pack.body.pack_id),
    )


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory):
    return _prepare(
        tmp_path_factory.mktemp("display_pre_e3a"), sc.PACK_PATH, "normal", closed_loop=True
    )


# --------------------------------------------------------------------------- 合成资料包


def test_synthetic_preflight_catalog_and_data_nature_keep_their_chinese(synthetic):
    s = synthetic
    _same_as_sample("05_normal_preflight", s.run.preflight)
    _same_as_sample("18_catalog_compilation", s.run.compiled.catalog)
    _same_as_sample("19_option_inventory_coverage", s.run.inventory)
    # 产品结果里嵌着的资料性质是同一份投影
    embedded = _committed("10_normal_result")["data_nature"]
    assert [_dump(s.nature[d["city_ref"]]) for d in embedded] == embedded


def test_synthetic_texts_are_bound_and_have_english(synthetic):
    s = synthetic
    pre = _dump(s.run.preflight)
    assert _unbound(pre, s.rec) == set()
    _english_ok(pre, s.rec, s.res)
    for message in (s.run.compiled.catalog, s.run.inventory):
        payload = _dump(message)
        assert _unbound(payload, s.rec) == set()
        _english_ok(payload, s.rec, s.res)
    for dn in s.nature.values():
        payload = _dump(dn)
        assert _unbound(payload, s.rec_nature) == set()
        _english_ok(payload, s.rec_nature, s.res)
    blocked = _dump(s.blocked)
    assert _unbound(blocked, s.rec_blocked) == set()
    _english_ok(blocked, s.rec_blocked, s.res)


def test_synthetic_fixture_samples_keep_their_chinese(synthetic, tmp_path):
    def add_holding(data: dict[str, Any]) -> None:
        data["initial_states"][0]["holdings"] = [
            {
                "opportunity_id": "k51.durable.convenience_pack",
                "quantity": 1,
                "acquired_year": 0,
                "remaining_months": 24,
            }
        ]

    def recorded(make: Any, *args: Any, **kwargs: Any) -> tuple[Any, Any]:
        """每个场景各用一个记录器（同一段中文在别的场景里的绑定不混进来）。"""
        with recording() as rec:
            return make(*args, **kwargs), rec

    res = synthetic.res
    unknown, rec_unknown = recorded(sc.positive_weight_data_unknown, tmp_path / "unknown")
    rejected, _ = recorded(
        sc.preflight_only,
        tmp_path / "rejected",
        "start_rejected",
        origin="controlled_fixture",
        mutate_life_input=add_holding,
    )
    cashier, rec_cashier = recorded(
        sc.preflight_only,
        tmp_path / "cashier",
        "cashier",
        occupation_text="我在超市做收银",
        selected="occ.cashier",
    )
    (boundary, _), rec_boundary = recorded(sc.catalog_boundary, tmp_path / "boundary")
    (statuses, statuses_inv), rec_statuses = recorded(sc.catalog_statuses, tmp_path / "statuses")
    items, _ = recorded(sc.life_item_compilation, tmp_path / "life_items")
    for name, message in (
        ("34_positive_weight_data_unknown_preflight", unknown.pre.preflight),
        ("28_standard_start_rejected_preflight", rejected.pre.preflight),
        ("29_start_job_unmatched_preflight", cashier.pre.preflight),
        ("20_catalog_boundary_compilation", boundary),
        ("35_catalog_statuses_compilation", statuses),
        ("36_catalog_statuses_coverage", statuses_inv),
        ("48_life_item_compilation", items),
    ):
        _same_as_sample(name, message)
    # 资料缺口（正权重资料未知）与开局岗位对不上是这里生成的：都有绑定，这里负责的部分都有英文
    for s, rec in ((unknown, rec_unknown), (cashier, rec_cashier)):
        payload = _dump(s.pre.preflight)
        assert _unbound(payload, rec) == set()
        _english_ok(payload, rec, res)
    # 受控夹具在内存里改了资料：编译原因照样绑定；夹具改过或新加的来源事实不当作资料包原样（过时或缺失，不显示旧英文）
    for message, rec in (
        (boundary, rec_boundary),
        (statuses, rec_statuses),
        (statuses_inv, rec_statuses),
    ):
        payload = _dump(message)
        assert _unbound(payload, rec) == set()
        cov = coverage(payload, rec, res, constants=CONSTANT_TEXTS)
        assert not {"source_changed", "ambiguous"} & set(cov["missing"]), cov["missing_patterns"]
        assert all(m.startswith("source_fact/") for m in _mine_missing(payload, rec, res))


def test_copied_scope_lines_are_bound_only_when_regenerated_exactly(synthetic):
    """E1 的“数据与参数”说明与确认画像里的兴趣范围说明：按同一写法重新生成、逐条核对；原文的写法一变就照用原文（中文不变，
    英文缺失），不猜。"""
    world = synthetic.run.world
    with recording() as rec:
        same = _e1_scope(world)
    assert same == tuple(world.scope["数据与参数"])
    assert all(rec.get(line) for line in same)
    changed = SimpleNamespace(
        bundle=world.bundle,
        scoring=world.scoring,
        facts=world.facts,
        scope={"数据与参数": ["E1 改过的写法", *world.scope["数据与参数"][1:]]},
    )
    with recording() as rec2:
        fallback = _e1_scope(changed)  # type: ignore[arg-type]
    assert fallback == tuple(changed.scope["数据与参数"])
    assert all(
        rec2.get(line) is None for line in fallback
    )  # 照用的原文不是这次生成的文字：没有绑定

    confirmed = synthetic.confirmed
    with recording() as rec3:
        lines = confirmed_scope_lines(synthetic.pack, confirmed)
    assert lines == confirmed.interests.scope_zh and all(rec3.get(x) for x in lines)
    edited = confirmed.model_copy(
        update={
            "interests": confirmed.interests.model_copy(
                update={"scope_zh": ("确认时的另一种写法", *confirmed.interests.scope_zh[1:])}
            )
        }
    )
    with recording() as rec4:
        kept = confirmed_scope_lines(synthetic.pack, edited)
    assert kept == edited.interests.scope_zh
    assert all(rec4.get(x) is None for x in kept)


def test_inventory_reasons_fall_back_to_the_recorded_text_when_the_compilation_differs(synthetic):
    """覆盖报告的原因按编译规则重新生成后逐条核对：编译记录不是这份资料包编出来的，就照用记录里的原文（中文不变，英文缺失）。"""
    s = synthetic
    comp = s.run.compiled.catalog
    unc = next(u for u in comp.uncompiled if u.market_status != "not_offered")
    first = unc.reasons[0].model_copy(update={"message_zh": "编译记录里的另一段原文"})
    changed = unc.model_copy(update={"reasons": (first, *unc.reasons[1:])})
    other = comp.model_copy(
        update={"uncompiled": tuple(changed if u is unc else u for u in comp.uncompiled)}
    )
    with recording() as rec:
        inv = inventory_coverage(s.pack, other)
    item = next(i for c in inv.cities for i in c.items if i.candidate_id == unc.candidate_id)
    assert "编译记录里的另一段原文" in item.reason_zh
    assert coverage(_dump(inv), rec, s.res)["missing"] == {"untranslated_param": 1}


# --------------------------------------------------------------------------- E3B 真实资料包


def test_e3b_preflight_data_nature_and_scope_notes(tmp_path_factory):
    """一份 E3B 预检查（旧金山 + 休斯敦）：中文与样例相同；同一运行世界里六座城的资料性质与范围说明全部绑定、这里负责的部分都有
    英文。只建一次（E3B 的编译要两分钟左右），所以全部核对放在同一个测试里。"""
    s = _prepare(
        tmp_path_factory.mktemp("display_pre_e3b"),
        E3B,
        "e3b_sf",
        closed_loop=False,
        abilities=("edu_at_least_hs",),
        cities=(SF, HOU),
    )
    _same_as_sample("53_e3b_sf_transit_missing_preflight", s.run.preflight)
    embedded = _committed("52_e3b_result")["data_nature"]
    assert {d["city_ref"] for d in embedded} == {HOU, SEA}
    assert [_dump(s.nature[d["city_ref"]]) for d in embedded] == embedded
    assert len(s.nature) == 6

    pre = _dump(s.run.preflight)
    assert _unbound(pre, s.rec) == set()
    _english_ok(pre, s.rec, s.res)
    for dn in s.nature.values():
        payload = _dump(dn)
        assert _unbound(payload, s.rec_nature) == set()
        _english_ok(payload, s.rec_nature, s.res)
    for note in s.notes:
        payload = _dump(note)
        assert _unbound(payload, s.rec_nature) == set()
        _english_ok(payload, s.rec_nature, s.res)
    assert {n.topic for n in s.notes} == {
        "occupations",
        "tax",
        "commute",
        "data",
        "menus",
        "unsupported",
    }
    blocked = _dump(s.blocked)
    assert _unbound(blocked, s.rec_blocked) == set()
    _english_ok(blocked, s.rec_blocked, s.res)
