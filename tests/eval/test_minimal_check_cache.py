"""`check_base_change.py` 的拟合缓存。

这份核查每跑一次要两次主规模拟合（各约 900s）。缓存把**拟合**与**核查**拆开，
于是改一行断言或一句注释不必再等半小时。测试盯住两件会造成实际损害的事：

1. **缺缓存时不许偷偷开拟合。** 一个"顺手帮你重算"的默认值会在无人看管时
   烧掉半小时，更糟的是它让"这次的数是从哪来的"变得不可知。
2. **缓存必须真的属于这批数据与这套规格。** 元数据对得上但参数其实来自别的
   数据，核查会照常全绿——那比不跑更坏。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from .synthetic import make_synthetic

SCRIPT = Path("scripts/diagnostics/check_base_change.py")


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("check_base_change", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # 必须先进 sys.modules 再 exec：`@dataclass` 解析 `-> FitParams` 这类
    # 延迟注解时会去 sys.modules 找模块，找不到就抛 AttributeError。
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


@pytest.fixture(scope="module")
def tiny():
    """小到能在测试里跑的数据。缓存逻辑与规模无关。"""
    return make_synthetic(n_personas=25, n_tasks=4, n_alts=6, sigma=0.1, seed=771)


def _params(mod, rng, ds, base):
    """造一组参数，并把它在这批数据上的**真实**似然存进去。"""
    chol = np.tril(rng.normal(scale=0.2, size=(6, 6))) + np.eye(6) * 0.4
    fp = mod.FitParams(
        rho=0.5,
        gamma_0=float(np.exp(0.5)),
        mu_alr=rng.normal(scale=0.1, size=6),
        chol=chol,
        loglik=0.0,
    )
    return mod.FitParams(
        rho=fp.rho,
        gamma_0=fp.gamma_0,
        mu_alr=fp.mu_alr,
        chol=fp.chol,
        loglik=mod.evaluate_loglik(ds, fp, base),
    )


@pytest.fixture(scope="module")
def cache_file(mod, tiny, tmp_path_factory):
    rng = np.random.default_rng(4)
    ds = tiny.dataset
    payload = {
        "seed": mod.SEED,
        "cache_key": mod.cache_key(ds),
        "fitted_parameters": {
            "material": _params(mod, rng, ds, mod.MATERIAL).to_dict(),
            "safety": _params(mod, rng, ds, mod.SAFETY).to_dict(),
        },
        "profile": mod.RESEARCH_PROFILE_ALR_FULL_MATERIAL.fingerprint(),
        "gap_3_2": {"refit_from_mapped_safety": {"loglik": -1.0, "gap_to_material_best": 0.5}},
    }
    path = tmp_path_factory.mktemp("cache") / "cache.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# 缺缓存不得自动拟合
# --------------------------------------------------------------------------


def test_a_missing_cache_refuses_instead_of_fitting(mod, tiny, tmp_path, monkeypatch, capsys):
    """缺缓存 → 报告并退出，**一次拟合都不许发生**。"""
    called = []
    monkeypatch.setattr(mod, "fit_research", lambda *a, **k: called.append(1))
    monkeypatch.setattr(mod, "build", lambda: (tiny.dataset, None))
    monkeypatch.setattr(
        "sys.argv", ["check", "--cache", str(tmp_path / "nope.json"), "--out", str(tmp_path / "o")]
    )

    assert mod.main() == 2
    assert called == [], "缺缓存时绝不能调用 fit_research"
    out = capsys.readouterr().out
    assert "缓存不存在" in out
    assert "--allow-refit" in out, "必须告诉使用者怎么显式授权"
    assert not (tmp_path / "o").exists(), "拒绝时不应写出结果"


def test_the_refusal_survives_a_corrupt_cache_too(mod, tiny, tmp_path, monkeypatch):
    """坏掉的 JSON 同样是"缺输入"，不是"那就重算吧"。"""
    called = []
    monkeypatch.setattr(mod, "fit_research", lambda *a, **k: called.append(1))
    monkeypatch.setattr(mod, "build", lambda: (tiny.dataset, None))
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["check", "--cache", str(bad), "--out", str(tmp_path / "o")])

    assert mod.main() == 2
    assert called == []


# --------------------------------------------------------------------------
# 缓存能完成检查
# --------------------------------------------------------------------------


def test_a_valid_cache_completes_the_check_without_fitting(
    mod, tiny, cache_file, tmp_path, monkeypatch
):
    """有缓存 → 两节核查都跑完，仍然一次拟合都没有。"""
    called = []
    monkeypatch.setattr(mod, "fit_research", lambda *a, **k: called.append(1))
    monkeypatch.setattr(mod, "build", lambda: (tiny.dataset, None))
    out = tmp_path / "report.json"
    monkeypatch.setattr("sys.argv", ["check", "--cache", str(cache_file), "--out", str(out)])

    mod.main()

    assert called == [], "走缓存时不该有任何拟合"
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["fit_source"]["source"] == "cache"
    assert len(report["identity_3_1"]["rows"]) == 13
    assert report["gap_3_2"]["refit_from_mapped_safety"]["source"] == "cache"
    assert "draws_200" in report["gap_3_2"] and "draws_800" in report["gap_3_2"]


def test_a_cache_without_the_reoptimisation_does_not_trigger_one(
    mod, tiny, cache_file, tmp_path, monkeypatch
):
    """重优化本身也是主规模拟合：缓存里没有就跳过并说明，不得顺手跑一次。"""
    payload = json.loads(cache_file.read_text(encoding="utf-8"))
    del payload["gap_3_2"]
    stripped = tmp_path / "no_refit.json"
    stripped.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    fitted = []
    monkeypatch.setattr(mod, "build", lambda: (tiny.dataset, None))
    monkeypatch.setattr(
        "eval.recovery.estimator._fit_recovery_alr", lambda *a, **k: fitted.append(1)
    )
    out = tmp_path / "r.json"
    monkeypatch.setattr("sys.argv", ["check", "--cache", str(stripped), "--out", str(out)])

    mod.main()

    assert fitted == []
    entry = json.loads(out.read_text(encoding="utf-8"))["gap_3_2"]["refit_from_mapped_safety"]
    assert entry["status"] == "unavailable_no_cache_and_refit_not_allowed"


# --------------------------------------------------------------------------
# 缓存必须真的匹配
# --------------------------------------------------------------------------


def test_parameters_that_do_not_belong_to_this_data_are_rejected(mod, tiny, cache_file, tmp_path):
    """存的似然复算不出来 → 拒绝。

    这是缓存校验的**主证据**：元数据可以对得上而参数其实来自另一批数据，
    那种缓存会让核查全绿地跑完，比不跑更危险。
    """
    payload = json.loads(cache_file.read_text(encoding="utf-8"))
    payload["fitted_parameters"]["material"]["loglik"] += 5.0
    bad = tmp_path / "wrong.json"
    bad.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(mod.CacheError, match="复算不出"):
        mod.load_cache(bad, tiny.dataset)


def test_a_cache_built_on_other_data_is_rejected_by_fingerprint(mod, tiny, cache_file, tmp_path):
    payload = json.loads(cache_file.read_text(encoding="utf-8"))
    payload["cache_key"]["dataset_digest"] = "0" * 16
    bad = tmp_path / "other.json"
    bad.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(mod.CacheError, match="dataset_digest"):
        mod.load_cache(bad, tiny.dataset)


def test_a_cache_built_under_another_spec_is_rejected(mod, tiny, cache_file, tmp_path):
    """换了 draws 数就是另一次拟合，哪怕数据一模一样。"""
    payload = json.loads(cache_file.read_text(encoding="utf-8"))
    payload["cache_key"]["profile_material"]["n_draws"] = 9999
    bad = tmp_path / "spec.json"
    bad.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(mod.CacheError, match="profile_material"):
        mod.load_cache(bad, tiny.dataset)


def test_the_digest_moves_when_the_data_moves(mod, tiny):
    """指纹得有牙：数据一变它就得变，否则上一条测的是个常数。"""
    other = make_synthetic(n_personas=25, n_tasks=4, n_alts=6, sigma=0.1, seed=772)
    assert mod.dataset_digest(tiny.dataset) != mod.dataset_digest(other.dataset)
    assert mod.dataset_digest(tiny.dataset) == mod.dataset_digest(tiny.dataset)


# --------------------------------------------------------------------------
# 改文档或断言不得让缓存失效
# --------------------------------------------------------------------------


def test_editing_prose_or_a_tolerance_does_not_invalidate_the_cache(mod, tiny, monkeypatch):
    """缓存键只认决定拟合的输入。

    这条是整个设计的要点：如果改一句注释或收紧一个核查容差就要重跑两次
    900s 的拟合，那没人会去改注释，也没人会去收紧容差。
    """
    before = mod.cache_key(tiny.dataset)

    monkeypatch.setattr(mod, "__doc__", "完全不同的模块文档")
    monkeypatch.setattr(mod, "TOL_POINTWISE", 1e-20)
    monkeypatch.setattr(mod, "TOL_LOGLIK_REL", 1e-20)
    monkeypatch.setattr(mod, "HIGH_PRECISION_DRAWS", 3200)

    assert mod.cache_key(tiny.dataset) == before


def test_the_cache_key_covers_everything_that_changes_a_fit(mod, tiny):
    """反过来：凡是会改变拟合落点的，都必须在键里。"""
    key = mod.cache_key(tiny.dataset)
    assert set(key) == {
        "dataset_digest",
        "generation",
        "profile_material",
        "profile_safety",
    }
    for part in ("profile_material", "profile_safety"):
        assert {
            "n_draws",
            "draw_method",
            "draw_seed",
            "maxiter",
            "tol",
            "start_sd0",
            "selection_rule",
            "base_dim",
            "covariance",
        } <= set(key[part])
    assert key["profile_material"]["base_dim"] != key["profile_safety"]["base_dim"]
